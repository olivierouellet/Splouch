import os
import re
import select
import signal
import struct
import subprocess
from typing import Literal

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import bus
import state
from meet_data import send_event_info
from meet_parsers.lenex_parser import load_lenex
from web import ActionResult, EnabledFlag, redirect, require_login, save_upload
from worker import (_list_sessions, _restart_worker, end_test_session,
                    forget_current_heat)

router = APIRouter(tags=['Debug'])


class NameBody(BaseModel):
    name: str = ''


class PlayBody(BaseModel):
    name: str = ''
    # Keep this session off the cloud. Defaults on: a replay is for the people in
    # the building, and the cost of getting it wrong is spectators watching a
    # recording as if it were the race in front of them. Forced on when a real meet
    # is loaded — see _test_play.
    local_only: bool = True


class SpeedBody(BaseModel):
    speed: float = 1.0


class Session(BaseModel):
    name: str
    source: str
    path: str


class TestStatus(BaseModel):
    playing: bool
    session: str
    recording: bool
    sessions: list[Session]
    speed: float
    has_meet: bool
    test_meet: bool
    test_meet_name: str
    local_only: bool        # what a session started now would do, or is doing
    local_only_forced: bool # a meet is loaded, so the choice is not the operator's
    meet_set_aside: str     # the meet being held for this session, '' if none


class SerialStatus(BaseModel):
    state: str
    msg: str


class SeedTimes(BaseModel):
    lane_seed_times: dict[int, str]
    last_event_sent: tuple[int, int]
    lenex_loaded: bool


class TerminalStart(BaseModel):
    # Keys must mirror _TERMINAL_ALLOWED_CMDS below; an unknown value is rejected
    # by validation before the handler runs.
    cmd: Literal['bash', 'raspi-config', 'logs', 'dmesg-tty', 'serial-ports'] = 'bash'

_TERMINAL_ALLOWED_CMDS = {
    'bash':         ['bash'],
    'raspi-config': ['sudo', 'raspi-config'],
    'logs':         ['journalctl', '-u', state.SERVICE_NAME, '-f'],
    'dmesg-tty':    ['bash', '-c', 'dmesg | grep -i tty'],
    'serial-ports': ['python3', '-m', 'serial.tools.list_ports', '-v'],
}


@router.get('/test_status', response_model=TestStatus,
            dependencies=[Depends(require_login)])
def route_test_status():
    return {
        'playing':        state._test_session is not None,
        'session':        os.path.basename(state._test_session) if state._test_session else '',
        'recording':      state._record_handle is not None,
        'sessions':       _list_sessions(),
        'speed':          state.in_speed,
        'has_meet':       bool(state._active_meet_file) and not state._test_meet_active,
        'test_meet':      state._test_meet_active,
        'test_meet_name': state._test_meet_name,
        'local_only':        (state._test_local_only if state._test_session
                              else _local_only_default()),
        'local_only_forced': bool(state._active_meet_file),
        'meet_set_aside':    state._active_meet_file if state._test_meet_active else '',
    }


def _local_only_default() -> bool:
    """What the checkbox shows for a session not yet started."""
    if state._active_meet_file:
        return True                 # not the operator's choice — see _test_play
    return bool(state.settings.get('test_local_only', True))


@router.post('/test_play', response_model=ActionResult,
             dependencies=[Depends(require_login)])
async def route_test_play(body: PlayBody):
    # Parsing the companion LENEX is blocking — run off the loop.
    return await run_in_threadpool(_test_play, body.name, body.local_only)


def _test_play(name, local_only=True):
    """Start a recorded session.

    The recording's own event and heat numbers only line up with the start lists
    in the companion `.lxf` beside it, so that is what gets loaded — even when the
    operator has a real meet open. The real meet's files stay in MEET_FOLDER
    untouched and `_active_meet_file` still names it; only `state.meet` is swapped,
    and `worker.end_test_session` reads it back when the session ends. Deleting the
    meet by hand and re-uploading it afterwards used to be the operator's job.
    """
    for s in _list_sessions():
        if s['name'] != name:
            continue
        # A replay must never publish under a live meet's identity: the times are
        # invented and the cloud would show them to spectators as the real race.
        local_only = bool(local_only) or bool(state._active_meet_file)
        _begin_local_only(local_only)
        bus.emit('/scoreboard', 'test_mode', {'active': True})
        bus.run_bg(_restart_worker, s['path'])

        companion = os.path.splitext(s['path'])[0] + '.lxf'
        if os.path.exists(companion):
            try:
                # In memory only. Not through routes/settings._load_meet_file:
                # that would rewrite `last_meet_file`, seed a `meet_profiles`
                # entry for the recording and overwrite the real meet's cloud
                # title and images — see state.apply_meet_profile.
                state.set_lenex(load_lenex(companion))
                state._test_meet_active = True
                state._test_meet_name   = os.path.basename(companion)
                # The decoder still holds the previous session's event and heat.
                # Broadcasting that against this recording's start lists shows its
                # number over eight empty lanes until the replay announces its own
                # — see worker.forget_current_heat.
                forget_current_heat()
                send_event_info()
            except Exception as e:
                print(f'[test] Failed to load companion LXF: {e}', flush=True)
        return {'ok': True}
    return JSONResponse({'error': 'Session not found'}, status_code=404)


def _begin_local_only(local_only: bool):
    """Take the cloud out of the picture for the duration of a test session.

    The relay is stopped rather than filtered. The cloud already derives its
    `meet_live` flag from relay connect/disconnect, so dropping the link gives
    spectators the offline state it already knows how to show — no new event, and
    no cloud deploy. It also means there is no socket for a replay frame to escape
    on, which no amount of filtering can promise.
    """
    import relay
    state._test_local_only = bool(local_only)
    if not local_only or state._test_saved_results is not None:
        # Already holding a session's worth of state: a second `_test_play` (the
        # Play buttons are disabled while one runs, but not from the API) must not
        # overwrite the *real* snapshot with the first test's, or record the relay
        # as already-stopped and so never restart it.
        return
    # Restored when the session ends: the relay re-sends this snapshot on every
    # reconnect, so a replay's results would otherwise reach the cloud on the next
    # connect, long after the test was over.
    state._test_saved_results = state._last_results_snapshot or {}
    state._test_relay_was_running = relay.status()['running']
    if state._test_relay_was_running:
        relay.stop()


@router.post('/test_stop', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_test_stop():
    # Restores the meet, wipes the boards and puts the cloud back — the same
    # ending a recording that runs to its end gets (worker._run_test_session).
    end_test_session()
    bus.run_bg(_restart_worker, None)
    return {'ok': True}


@router.post('/test_meet_upload', dependencies=[Depends(require_login)])
async def route_test_meet_upload(request: Request):
    file = (await request.form()).get('meet_file')
    # Saving + LENEX parsing is blocking — run off the loop.
    return await run_in_threadpool(_test_meet_upload, file)


def _test_meet_upload(file):
    """Start lists for a recording that has no companion `.lxf` beside it.

    Saved to TEST_MEET_FOLDER, never MEET_FOLDER: the operator's own meet files
    live there and a test must leave them exactly as it found them. The
    "a real meet file is already loaded" refusal that used to guard this is gone
    with the rest of the delete-your-meet-first workflow.
    """
    if state._test_session is None:
        return {'ok': False, 'error': 'No test session is running'}
    if state._test_meet_active:
        return {'ok': False, 'error': 'Test meet already loaded'}
    if not file or not file.filename:
        return {'ok': False, 'error': 'No file provided'}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.csv', '.lxf'):
        return {'ok': False, 'error': 'File must be .lxf or .csv'}
    dest = os.path.join(state.TEST_MEET_FOLDER, os.path.basename(file.filename))
    save_upload(file, dest)
    try:
        if ext == '.csv':
            state.load_event_info(dest)
        else:
            state.set_lenex(load_lenex(dest))
        send_event_info()
        state._test_meet_active = True
        state._test_meet_name   = os.path.basename(file.filename)
        return {'ok': True, 'name': os.path.basename(file.filename)}
    except Exception as e:
        try:
            os.remove(dest)
        except Exception:
            pass
        return {'ok': False, 'error': str(e)}


class LocalOnlyBody(BaseModel):
    local_only: bool = True


@router.post('/test_set_local_only', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_test_set_local_only(body: LocalOnlyBody):
    """Remember the checkbox between sessions.

    Only the preference for the *next* session — a session already running keeps
    whatever it started with, since the relay was stopped (or not) at that point
    and flipping it mid-replay would publish half a test.
    """
    state.settings['test_local_only'] = bool(body.local_only)
    state.save_settings()
    return {'ok': True}


@router.post('/test_set_speed', dependencies=[Depends(require_login)])
def route_test_set_speed(body: SpeedBody):
    state.in_speed = max(0.1, min(body.speed, 100.0))
    return {'speed': state.in_speed}


@router.post('/test_record_start', dependencies=[Depends(require_login)])
async def route_test_record_start(body: NameBody):
    if state._record_handle:
        state._record_handle.close()
    code = re.sub(r'[^a-z0-9_-]', '_', body.name.strip().lower()) or 'recording'
    path = os.path.join(state.CUSTOM_SESSIONS_FOLDER, code + '.cts')
    state._record_handle = open(path, 'wt')
    return {'ok': True, 'file': code + '.cts'}


@router.post('/test_record_stop', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_test_record_stop():
    if state._record_handle:
        state._record_handle.close()
        state._record_handle = None
    return {'ok': True}


@router.post('/test_session_delete', dependencies=[Depends(require_login)])
async def route_test_session_delete(body: NameBody):
    path = os.path.join(state.CUSTOM_SESSIONS_FOLDER, body.name)
    if os.path.isfile(path) and path.endswith(SESSION_UPLOAD_EXTS):
        if state._test_session == path:
            bus.run_bg(_restart_worker, None)
        os.remove(path)
    return redirect('/settings')


# The recording formats a session upload takes. `settings.html` puts the same list in
# the file dialog's `accept`, and a test pins the two together: they disagreed, so the
# dialog offered only .cts while the server stored more than that.
#
# `.cap` was a third, and is gone: it was the same bytes as a `.raw`, in binary rather
# than hex, so every capture appeared twice in the operator's session list as two rows
# that played identically. Convert one with
# `server/console_recordings/cap-to-raw.py` — see that folder's README.
SESSION_UPLOAD_EXTS = ('.cts', '.raw')


@router.post('/test_session_upload', response_model=ActionResult,
             response_model_exclude_none=True, dependencies=[Depends(require_login)])
async def route_test_session_upload(request: Request):
    """Store an uploaded console recording, and say so.

    This used to answer a redirect to /settings whether it had saved the file or
    silently dropped it, and the page reloaded its session list either way — so a
    wrong extension looked exactly like a successful upload, minus the new row.
    """
    file = (await request.form()).get('session_file')
    if not file or not file.filename:
        return {'ok': False, 'error': 'No file provided'}
    if not file.filename.lower().endswith(SESSION_UPLOAD_EXTS):
        named = '%s or %s' % (', '.join(SESSION_UPLOAD_EXTS[:-1]), SESSION_UPLOAD_EXTS[-1])
        return {'ok': False, 'error': 'File must be ' + named}
    # Writing the upload is blocking — run it off the event loop so live
    # scoreboard broadcasts keep flowing (a long capture is not small).
    await run_in_threadpool(
        save_upload, file,
        os.path.join(state.CUSTOM_SESSIONS_FOLDER,
                     os.path.basename(file.filename)))
    return {'ok': True}


@router.get('/serial_status', response_model=SerialStatus,
            dependencies=[Depends(require_login)])
def route_serial_status():
    return state._serial_status


@router.get('/debug_status', response_model=EnabledFlag,
            dependencies=[Depends(require_login)])
def route_debug_status():
    return {'enabled': state._debug_serial}


@router.post('/debug_toggle', response_model=EnabledFlag,
             dependencies=[Depends(require_login)])
def route_debug_toggle():
    state._debug_serial = not state._debug_serial
    return {'enabled': state._debug_serial}


@router.get('/debug/seed_times', response_model=SeedTimes)
def route_debug_seed_times():
    return {
        'lane_seed_times': state._decoder.lane_seed_times,
        'last_event_sent': state._decoder.last_event_sent,
        'lenex_loaded':    bool(state.meet.start_list),
    }


# ── Terminal (PTY) ─────────────────────────────────────────────────────────────

def _pty_reader():
    while state._pty_fd is not None:
        try:
            r, _, _ = select.select([state._pty_fd], [], [], 0.05)
            if r:
                data = os.read(state._pty_fd, 4096)
                if data:
                    bus.emit('/terminal', 'output', data.decode('utf-8', errors='replace'))
                else:
                    break
        except OSError:
            break
        except Exception:
            break
    state._pty_fd = state._pty_pid = None
    bus.emit('/terminal', 'exit', {})


@router.post('/terminal_start', response_model=ActionResult,
             dependencies=[Depends(require_login)])
async def route_terminal_start(body: TerminalStart):
    if not state._PTY_AVAILABLE:
        return {'ok': False, 'error': 'PTY not available on this platform'}
    if state._pty_fd is not None:
        return {'ok': True}
    try:
        import fcntl
        import pty
        import termios
        # body.cmd is constrained to _TERMINAL_ALLOWED_CMDS keys by the model.
        cmd = _TERMINAL_ALLOWED_CMDS[body.cmd]
        master_fd, slave_fd = pty.openpty()
        winsize = struct.pack('HHHH', 24, 80, 0, 0)
        fcntl.ioctl(slave_fd, termios.TIOCSWINSZ, winsize)
        TIOCSCTTY = getattr(termios, 'TIOCSCTTY', 0x540E)

        def _preexec():
            os.setsid()
            fcntl.ioctl(0, TIOCSCTTY, 0)

        env  = {**os.environ, 'TERM': 'xterm-256color'}
        proc = subprocess.Popen(
            cmd,
            stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
            close_fds=True, preexec_fn=_preexec, env=env
        )
        os.close(slave_fd)
        state._pty_fd  = master_fd
        state._pty_pid = proc.pid
        bus.run_bg(_pty_reader)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@router.post('/terminal_stop', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_terminal_stop():
    if state._pty_pid:
        try:
            os.kill(state._pty_pid, signal.SIGTERM)
        except Exception:
            pass
    if state._pty_fd:
        try:
            os.close(state._pty_fd)
        except Exception:
            pass
    state._pty_fd = state._pty_pid = None
    return {'ok': True}


def _terminal_input(data):
    if state._pty_fd is not None:
        try:
            os.write(state._pty_fd, data.encode('utf-8'))
        except OSError:
            pass


def _terminal_resize(data):
    if state._pty_fd is not None:
        try:
            import fcntl
            import termios
            winsize = struct.pack('HHHH', data['rows'], data['cols'], 0, 0)
            fcntl.ioctl(state._pty_fd, termios.TIOCSWINSZ, winsize)
        except Exception:
            pass


@router.websocket('/ws/terminal')
async def ws_terminal(ws: WebSocket):
    await bus.manager.connect(ws, '/terminal')
    try:
        while True:
            msg = await ws.receive_json()
            ev, d = msg.get('event'), msg.get('data')
            if ev == 'input':
                _terminal_input(d)
            elif ev == 'resize':
                _terminal_resize(d)
            elif ev == 'ping':
                await bus.manager.send(ws, 'pong')
    except WebSocketDisconnect:
        pass
    finally:
        bus.manager.disconnect(ws, '/terminal')
