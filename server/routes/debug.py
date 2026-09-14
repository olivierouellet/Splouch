import glob
import os
import re
import select
import shutil
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
from worker import _cleanup_test_meet, _list_sessions, _restart_worker

router = APIRouter(tags=['Debug'])


class NameBody(BaseModel):
    name: str = ''


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
        'test_meet_name': state._active_meet_file if state._test_meet_active else '',
    }


@router.post('/test_play', response_model=ActionResult,
             dependencies=[Depends(require_login)])
async def route_test_play(body: NameBody):
    # Copying + parsing the companion LENEX is blocking — run off the loop.
    return await run_in_threadpool(_test_play, body.name)


def _test_play(name):
    for s in _list_sessions():
        if s['name'] == name:
            bus.emit('/scoreboard', 'test_mode', {'active': True})
            bus.run_bg(_restart_worker, s['path'])
            companion = os.path.splitext(s['path'])[0] + '.lxf'
            if os.path.exists(companion):
                all_companions = {
                    os.path.basename(os.path.splitext(sess['path'])[0] + '.lxf')
                    for sess in _list_sessions()
                }
                for f in glob.glob(os.path.join(state.MEET_FOLDER, '*.lxf')):
                    if os.path.basename(f) in all_companions:
                        try:
                            os.remove(f)
                        except Exception:
                            pass
                if state._test_meet_active:
                    state.clear_meet()
                    state._test_meet_active = False
                if not state._active_meet_file:
                    try:
                        dest = os.path.join(state.MEET_FOLDER, os.path.basename(companion))
                        shutil.copy2(companion, dest)
                        state.set_lenex(load_lenex(dest))
                        send_event_info()
                        state._test_meet_active = True
                    except Exception as e:
                        print(f'[test] Failed to load companion LXF: {e}')
            return {'ok': True}
    return JSONResponse({'error': 'Session not found'}, status_code=404)


@router.post('/test_stop', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_test_stop():
    _cleanup_test_meet()
    bus.emit('/scoreboard', 'test_mode', {'active': False})
    bus.run_bg(_restart_worker, None)
    return {'ok': True}


@router.post('/test_meet_upload', dependencies=[Depends(require_login)])
async def route_test_meet_upload(request: Request):
    file = (await request.form()).get('meet_file')
    # Saving + LENEX parsing is blocking — run off the loop.
    return await run_in_threadpool(_test_meet_upload, file)


def _test_meet_upload(file):
    if state._test_session is None:
        return {'ok': False, 'error': 'No test session is running'}
    if state._test_meet_active:
        return {'ok': False, 'error': 'Test meet already loaded'}
    meet_files = glob.glob(os.path.join(state.MEET_FOLDER, '*.lxf')) + \
                 glob.glob(os.path.join(state.MEET_FOLDER, '*.csv'))
    if meet_files:
        return {'ok': False, 'error': 'A real meet file is already loaded'}
    if not file or not file.filename:
        return {'ok': False, 'error': 'No file provided'}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ('.csv', '.lxf'):
        return {'ok': False, 'error': 'File must be .lxf or .csv'}
    dest = os.path.join(state.MEET_FOLDER, os.path.basename(file.filename))
    save_upload(file, dest)
    try:
        if ext == '.csv':
            state.load_event_info(dest)
        else:
            state.set_lenex(load_lenex(dest))
        send_event_info()
        state._test_meet_active = True
        return {'ok': True, 'name': os.path.basename(file.filename)}
    except Exception as e:
        try:
            os.remove(dest)
        except Exception:
            pass
        return {'ok': False, 'error': str(e)}


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
    if os.path.isfile(path) and (path.endswith('.cts') or
                                  path.endswith('.raw') or
                                  path.endswith('.cap')):
        if state._test_session == path:
            bus.run_bg(_restart_worker, None)
        os.remove(path)
    return redirect('/settings')


# The recording formats a session upload takes. `settings.html` puts the same list in
# the file dialog's `accept`, and a test pins the two together: they disagreed, so the
# dialog offered only .cts while the server had always stored all three.
SESSION_UPLOAD_EXTS = ('.cts', '.raw', '.cap')


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
    # scoreboard broadcasts keep flowing (a .cap can be large).
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
