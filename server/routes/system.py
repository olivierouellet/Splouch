import datetime
import io
import os
import re
import subprocess
import tarfile
import time
import tomllib

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator
from starlette.concurrency import run_in_threadpool

import bus
import state
from web import ActionResult, require_login

router = APIRouter(tags=['System'])

# Single source of truth for the systemd unit (install/scripts/refresh-service.sh).
# Run before each in-app restart so a changed entrypoint/layout self-heals rather
# than crash-looping on a stale unit. Sibling of the repo's server/ dir.
_REFRESH_SCRIPT = os.path.join(state.REPO_DIR,
                               'install', 'scripts', 'refresh-service.sh')


class UpdateStart(BaseModel):
    target: str | None = None


class TimeSet(BaseModel):
    # The pattern is advertised in the schema for /docs; enforcement is done in
    # the validator so a bad value yields the app's friendly single-line error
    # rather than Pydantic's regex-echoing default.
    date: str = Field(json_schema_extra={'pattern': r'^\d{4}-\d{2}-\d{2}$'})
    time: str = Field(json_schema_extra={'pattern': r'^\d{2}:\d{2}(:\d{2})?$'})

    @field_validator('date')
    @classmethod
    def _check_date(cls, v):
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', v):
            raise ValueError('Invalid date or time format')
        return v

    @field_validator('time')
    @classmethod
    def _check_time(cls, v):
        if not re.match(r'^\d{2}:\d{2}(:\d{2})?$', v):
            raise ValueError('Invalid date or time format')
        return v


class TimeStatus(BaseModel):
    date: str
    time: str
    timezone: str
    ntp_active: bool
    synchronized: bool


class VersionList(BaseModel):
    # Success returns current/versions/branches; failure returns error. All
    # non-``ok`` fields are optional so both dict shapes validate.
    ok: bool
    current: str = ''
    versions: list[str] = Field(default_factory=list)
    branches: list[str] = Field(default_factory=list)
    error: str | None = None


class LogLine(BaseModel):
    text: str
    error: bool


class LogTail(BaseModel):
    lines: list[LogLine]
    done: bool | None = None
    # Only the app-update log sets this: the run stopped on a dirty checkout, so the
    # panel should offer "Repair checkout". Defaulted, so the OS-update and RTC logs
    # that share this model are unaffected.
    repair: bool = False


class RtcStatus(BaseModel):
    configured: bool
    active: bool


_VERSION_RE = re.compile(r'^v\d{4}\.\d{2}\.\d+$')


def _update_config():
    """Load the update-dropdown settings from `server/update_config.toml`.

    Beside the server package, not at the repo root — `state.REPO_DIR` is the root.

    Read fresh on each call so edits take effect without restarting the server.
    Returns (extra_branches, max_versions); max_versions == 0 means no limit.
    """
    try:
        with open(os.path.join(state.app_dir, 'update_config.toml'), 'rb') as f:
            cfg = tomllib.load(f)
    except Exception:
        cfg = {}
    return (cfg.get('extra_branches') or [], int(cfg.get('max_versions') or 0))


def _find_uv():
    import shutil
    uv = shutil.which('uv')
    if uv:
        return uv
    for p in [os.path.expanduser('~/.local/bin/uv'),
              os.path.expanduser('~/.cargo/bin/uv'),
              '/usr/local/bin/uv']:
        if os.path.isfile(p):
            return p
    return 'uv'


_UV = _find_uv()


# ── Time ───────────────────────────────────────────────────────────────────────

@router.get('/time_status', response_model=TimeStatus,
            dependencies=[Depends(require_login)])
def route_time_status():
    now          = datetime.datetime.now()
    ntp_active   = False
    synchronized = False
    timezone     = ''
    try:
        result = subprocess.run(['timedatectl'], capture_output=True, text=True, timeout=5)
        for line in result.stdout.splitlines():
            ll = line.lower()
            if 'ntp service' in ll:
                ntp_active = 'active' in ll
            if 'system clock synchronized' in ll:
                synchronized = 'yes' in ll
            if 'time zone' in ll:
                m = re.search(r'Time zone:\s+\S+\s+\((\w+)', line)
                if m:
                    timezone = m.group(1)
    except Exception:
        pass
    return {
        'date':         now.strftime('%Y-%m-%d'),
        'time':         now.strftime('%H:%M:%S'),
        'timezone':     timezone,
        'ntp_active':   ntp_active,
        'synchronized': synchronized,
    }


@router.post('/time_sync', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_time_sync():
    try:
        subprocess.run(['sudo', 'timedatectl', 'set-ntp', 'true'], timeout=5, check=True)
        subprocess.run(['sudo', 'systemctl', 'restart', 'systemd-timesyncd'], timeout=5)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@router.post('/time_set', response_model=ActionResult,
             dependencies=[Depends(require_login)])
async def route_time_set(body: TimeSet):
    return await run_in_threadpool(_time_set, body.date, body.time)


def _time_set(date_str, time_str):
    # date/time formats are already validated by the TimeSet model.
    if len(time_str) == 5:
        time_str += ':00'
    try:
        subprocess.run(['sudo', 'timedatectl', 'set-ntp', 'false'], timeout=5, check=True)
        subprocess.run(['sudo', 'timedatectl', 'set-time', f'{date_str} {time_str}'],
                       timeout=5, check=True)
        return {'ok': True}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


# ── App / OS update ────────────────────────────────────────────────────────────

def _run_cmd_blocking(cmd, cwd=None):
    proc = subprocess.run(cmd, cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)
    return proc.stdout, proc.returncode


def _dirty_files():
    """Paths git reports as locally modified, staged or untracked.

    ``git status --porcelain`` lines are `XY <path>`; the two status columns are
    fixed-width, so the path starts at offset 3.
    """
    out, rc = _run_cmd_blocking(['git', 'status', '--porcelain'], cwd=state.REPO_DIR)
    if rc != 0:
        return []
    return [line[3:].strip() for line in out.splitlines() if line.strip()]


def _explain_dirty_failure(emit):
    """Turn git's "local changes would be overwritten" into something actionable.

    Raw git output tells a meet operator nothing, and the fix — reset the checkout
    — is not something to guess at from a phone on a pool deck. Naming the files and
    pointing at the button is the whole difference between a five-second fix and a
    call to whoever set the system up.

    Sets the flag that reveals *Repair checkout* on the Update panel.
    """
    dirty = _dirty_files()
    if not dirty:
        return
    state._update_repair_needed = True
    emit('\nThis checkout has local changes, so the update cannot be applied:\n',
         error=True)
    for path in dirty[:20]:
        emit(f'    {path}\n', error=True)
    if len(dirty) > 20:
        emit(f'    … and {len(dirty) - 20} more\n', error=True)
    emit('\nPress "Repair checkout" below to discard them and try again.\n',
         error=True)


def _run_update(target=None):
    state._update_log_lines = []
    state._update_log_done  = None
    state._update_repair_needed = False

    def emit(text, error=False):
        state._update_log_lines.append({'text': text, 'error': error})

    try:
        emit('$ git fetch --tags\n')
        out, rc = _run_cmd_blocking(['git', 'fetch', '--tags'], cwd=state.REPO_DIR)
        if out:
            emit(out)
        if rc != 0:
            emit(f'\nCommand failed (exit code {rc})\n', error=True)
            state._update_log_done = False
            return

        # `uv sync` rewrites uv.lock on every deploy, so the file is essentially
        # always dirty on a Pi; discard that expected drift or the pull below aborts
        # with "local changes would be overwritten" the first time a release changes
        # uv.lock. `HEAD --` rather than a bare `--`, so a *staged* change is reset
        # too — `git checkout -- <path>` restores from the index, which would leave a
        # staged uv.lock still differing from HEAD and still blocking the merge.
        #
        # Report a failure instead of swallowing it. This ran from `server/` for a
        # long time, where the pathspec cannot resolve, and the silent non-zero exit
        # is exactly why the guard was never noticed to be doing nothing.
        out, rc = _run_cmd_blocking(['git', 'checkout', 'HEAD', '--', 'uv.lock'],
                                    cwd=state.REPO_DIR)
        if rc != 0:
            emit('$ git checkout HEAD -- uv.lock\n')
            if out:
                emit(out)
            emit('\nCould not reset uv.lock; the pull may fail on local changes.\n',
                 error=True)

        extra_refs, _ = _update_config()
        if not target or target == 'master':
            cmds  = [['git', 'checkout', 'master'], ['git', 'pull'], [_UV, 'sync']]
            label = 'Development (master) installed'
        elif target in extra_refs:
            # Branch: force the local branch to match origin so repeated deploys
            # pick up new commits (a plain checkout of an existing branch would
            # stay on the stale local tip). origin/<branch> was refreshed by the
            # fetch above.
            cmds  = [['git', 'checkout', '-B', target, f'origin/{target}'], [_UV, 'sync']]
            label = f'Branch {target} installed'
        else:
            cmds  = [['git', 'checkout', target], [_UV, 'sync']]
            label = f'Version {target} installed'

        for cmd in cmds:
            emit('$ ' + ' '.join(cmd) + '\n')
            out, rc = _run_cmd_blocking(cmd, cwd=state.REPO_DIR)
            if out:
                emit(out)
            if rc != 0:
                emit(f'\nCommand failed (exit code {rc})\n', error=True)
                # A git step that fails on a dirty tree is the common case and the
                # one git explains worst; say which files and offer the way out.
                if cmd[0] == 'git':
                    _explain_dirty_failure(emit)
                state._update_log_done = False
                return

        emit(f'\n{label}. Restarting service…\n')
        state._update_log_done = True
        time.sleep(2)
        # Self-heal the systemd unit to match the just-updated code before the
        # restart. Non-interactive (sudo -n): installs that predate the sudoers
        # grant simply skip this and fall back to the Tremplin.py compat shim.
        if os.path.isfile(_REFRESH_SCRIPT):
            emit('$ sudo -n install/scripts/refresh-service.sh\n')
            r = subprocess.run(['sudo', '-n', _REFRESH_SCRIPT],
                               capture_output=True, text=True)
            if r.stdout:
                emit(r.stdout)
            if r.returncode != 0:
                emit('(could not refresh the service unit automatically — re-run '
                     'install.sh on this device to finish updating)\n')
                if r.stderr:
                    emit(r.stderr)
        subprocess.run(['sudo', 'systemctl', 'restart', state.SERVICE_NAME])
    except Exception as e:
        emit(f'\nError: {e}\n', error=True)
        state._update_log_done = False
    finally:
        state._update_in_progress = False


def _run_repair():
    """Discard local changes so the next update can apply.

    Streams into the *same* log the update writes, so the operator watches it in the
    Update panel rather than being sent to a shell — the commands and their output
    are shown exactly as the update's are.

    ``git reset --hard HEAD`` and nothing more:

    * It clears staged *and* unstaged edits to tracked files, which is what blocks a
      pull. A bare ``git checkout -- .`` would restore from the index and leave a
      staged file still differing from HEAD.
    * It leaves **untracked** files alone. Deleting those would need ``git clean``,
      and someone's notes or a hand-copied recording are not this button's business.
      They only block a pull in the rare case a release adds a file of the same name,
      and the log says so if any remain.
    * HEAD does not move, so this cannot change which version is installed. The
      operator still presses Install afterwards, and sees what they are getting.
    """
    state._update_log_lines = []
    state._update_log_done  = None
    state._update_repair_needed = False

    def emit(text, error=False):
        state._update_log_lines.append({'text': text, 'error': error})

    try:
        before = _dirty_files()
        if not before:
            emit('Nothing to repair — the checkout has no local changes.\n')
            state._update_log_done = True
            return

        emit('Discarding local changes to:\n')
        for path in before:
            emit(f'    {path}\n')

        emit('\n$ git reset --hard HEAD\n')
        out, rc = _run_cmd_blocking(['git', 'reset', '--hard', 'HEAD'],
                                    cwd=state.REPO_DIR)
        if out:
            emit(out)
        if rc != 0:
            emit(f'\nCommand failed (exit code {rc})\n', error=True)
            # Still dirty, so repair is still the answer — keep the button on screen
            # rather than clearing the flag and leaving no way to retry.
            state._update_repair_needed = True
            state._update_log_done = False
            return

        left = _dirty_files()
        if left:
            # Untracked leftovers. Harmless unless the incoming release adds the
            # same path, so name them rather than deleting them behind the operator.
            emit('\nStill present (untracked — not removed):\n')
            for path in left:
                emit(f'    {path}\n')
            emit('\nThese do not usually block an update. If one does, remove it by '
                 'hand from Settings → Terminal.\n')
        else:
            emit('\nCheckout is clean.\n')
        emit('\nRepaired. Press Install to update.\n')
        state._update_log_done = True
    except Exception as e:
        emit(f'\nError: {e}\n', error=True)
        state._update_repair_needed = True   # as above — leave the retry available
        state._update_log_done = False
    finally:
        state._update_in_progress = False


def _run_os_update():
    state._os_update_log_lines = []
    state._os_update_log_done  = None

    def emit(text, error=False):
        state._os_update_log_lines.append({'text': text, 'error': error})

    try:
        for cmd in [['sudo', 'apt-get', 'update'],
                    ['sudo', 'apt-get', 'upgrade', '-y']]:
            emit('$ ' + ' '.join(cmd) + '\n')
            out, rc = _run_cmd_blocking(cmd)
            if out:
                emit(out)
            if rc != 0:
                emit(f'\nCommand failed (exit code {rc})\n', error=True)
                state._os_update_log_done = False
                return
        emit('\nOS update complete.\n')
        state._os_update_log_done = True
    except Exception as e:
        emit(f'\nError: {e}\n', error=True)
        state._os_update_log_done = False
    finally:
        state._os_update_in_progress = False


@router.get('/version_list', response_model=VersionList,
            dependencies=[Depends(require_login)])
def route_version_list():
    try:
        r = subprocess.run(['git', 'describe', '--tags', '--exact-match', 'HEAD'],
                           capture_output=True, text=True, cwd=state.REPO_DIR, timeout=8)
        current = r.stdout.strip() if r.returncode == 0 else ''
        subprocess.run(['git', 'fetch', '--tags'], capture_output=True,
                       cwd=state.REPO_DIR, timeout=20)
        r = subprocess.run(['git', 'tag', '-l', '--sort=-version:refname'],
                           capture_output=True, text=True, cwd=state.REPO_DIR, timeout=8)
        extra_refs, max_versions = _update_config()
        tags = [t.strip() for t in r.stdout.splitlines()
                if t.strip() and _VERSION_RE.match(t.strip())]
        if max_versions > 0:
            tags = tags[:max_versions]
        branches = []
        for ref in extra_refs:
            chk = subprocess.run(['git', 'rev-parse', '--verify', '--quiet',
                                  f'refs/remotes/origin/{ref}'],
                                 capture_output=True, cwd=state.REPO_DIR, timeout=8)
            if chk.returncode == 0:
                branches.append(ref)
        return {'ok': True, 'current': current, 'versions': tags, 'branches': branches}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@router.post('/update_start', response_model=ActionResult,
             dependencies=[Depends(require_login)])
async def route_update_start(body: UpdateStart | None = None):
    if state._update_in_progress:
        return JSONResponse({'error': 'Update already in progress'}, status_code=409)
    state._update_in_progress = True
    target = (body.target if body else None) or None
    bus.run_bg(_run_update, target)
    return {'ok': True}


@router.post('/repair_checkout', response_model=ActionResult,
             dependencies=[Depends(require_login)])
async def route_repair_checkout():
    """Discard local edits that are blocking an update.

    Shares `_update_in_progress` and the update log with `/update_start`, so the two
    cannot run at once and the panel needs only one output pane.
    """
    if state._update_in_progress:
        return JSONResponse({'error': 'Update already in progress'}, status_code=409)
    state._update_in_progress = True
    bus.run_bg(_run_repair)
    return {'ok': True}


@router.post('/displays_update', response_model=ActionResult,
             dependencies=[Depends(require_login)])
async def route_displays_update():
    """Tell every registered display to move to the ref this server is on.

    No target is accepted from the caller: the whole point is lockstep, and
    letting the operator aim a display at some other ref reintroduces exactly the
    split this is meant to close. Update the server first, then press this.

    Refused while any lane is running — a display restarts to finish updating.
    """
    if state._running_lanes:
        return JSONResponse({'error': 'A race is running.'}, status_code=409)

    # Only clients that sent `register` (docs/api.md §2). A browser tab never does,
    # and that includes a Chromium kiosk showing /live — which is exactly what an
    # operator mid-upgrade is looking at while the list in front of them shows a
    # connected client. Say so, rather than reporting nothing is there.
    displays = [c for c in state._scoreboard_clients.values() if c.get('role')]
    if not displays:
        others = len(state._scoreboard_clients)
        if not others:
            return JSONResponse(
                {'error': 'No displays are connected.'}, status_code=404)
        return JSONResponse({'error': (
            f'{others} client(s) are connected, but none of them is a native '
            'display. Browser tabs do not count — a Chromium kiosk showing /live '
            'is a browser tab. A display that identifies itself shows a "kiosk" '
            'badge and a version in the list; one that does not is either a '
            'browser or a display too old to announce itself, and has to be '
            'updated on the Pi itself with install.sh once.')}, status_code=404)

    # `git describe --tags --always` — a *commit*, not a branch: `v2026.09.0-8-g3ecaa80`
    # off master, or the tag itself on a release. Displays are pinned to the commit
    # this server is running rather than pointed at a branch, which is what makes
    # lockstep hold: a branch would drift the moment anything landed on it, and the
    # two ends would disagree about the WebSocket contract with nothing to show for it
    # (notes/native_app_strategy.md).
    target = state.git_describe()['version']
    # A dirty server has no ref a display could check out. `--dirty` appends a suffix
    # that is not a real object, so this would fail on every kiosk. Being off a tag is
    # fine — master works, as long as the commit is clean and pushed.
    if not target:
        return JSONResponse(
            {'error': 'Cannot tell what version this server is running.'},
            status_code=409)
    if target.endswith('-dirty'):
        return JSONResponse({'error': (
            'This server has uncommitted changes, so there is no commit a display '
            'could check out. Any clean commit works — it does not have to be a '
            'release tag.')}, status_code=409)

    for client in displays:
        client['update_state'] = 'updating'
        client['update_lines'] = []
    bus.emit('/scoreboard', 'update', {'target': target})
    return {'ok': True}


@router.post('/os_update_start', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_os_update_start():
    if state._os_update_in_progress:
        return JSONResponse({'error': 'OS update already in progress'}, status_code=409)
    state._os_update_in_progress = True
    bus.run_bg(_run_os_update)
    return {'ok': True}


@router.get('/update_log', response_model=LogTail,
            dependencies=[Depends(require_login)])
def route_update_log():
    return {'lines': state._update_log_lines, 'done': state._update_log_done,
            'repair': state._update_repair_needed}


@router.get('/os_update_log', response_model=LogTail,
            dependencies=[Depends(require_login)])
def route_os_update_log():
    return {'lines': state._os_update_log_lines, 'done': state._os_update_log_done}


# ── RTC (Adafruit PiRTC DS3231) ──────────────────────────────────────────────────

_RTC_SCRIPT = os.path.join(os.path.dirname(state.app_dir), 'install', 'scripts', 'rtc_setup.sh')


@router.get('/rtc_status', response_model=RtcStatus,
            dependencies=[Depends(require_login)])
def route_rtc_status():
    configured = False
    active     = False

    for config_txt in ('/boot/firmware/config.txt', '/boot/config.txt'):
        try:
            with open(config_txt) as f:
                if any(line.strip() == 'dtoverlay=i2c-rtc,ds3231' for line in f):
                    configured = True
                    break
        except OSError:
            continue

    try:
        with open('/sys/class/rtc/rtc0/name') as f:
            name = f.read().lower()
        if 'ds3231' in name or 'rtc-ds1307' in name:
            active = True
    except OSError:
        pass

    return {'configured': configured, 'active': active}


def _run_rtc(action):
    state._rtc_log_lines = []
    state._rtc_log_done  = None

    def emit(text, error=False):
        state._rtc_log_lines.append({'text': text, 'error': error})

    try:
        emit(f'$ sudo bash install/scripts/rtc_setup.sh {action}\n')
        out, rc = _run_cmd_blocking(['sudo', 'bash', _RTC_SCRIPT, action])
        if out:
            emit(out)
        if rc != 0:
            emit(f'\nCommand failed (exit code {rc})\n', error=True)
            state._rtc_log_done = False
            return
        state._rtc_log_done = True
    except Exception as e:
        emit(f'\nError: {e}\n', error=True)
        state._rtc_log_done = False
    finally:
        state._rtc_in_progress = False


@router.post('/rtc_install_start', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_rtc_install_start():
    if state._rtc_in_progress:
        return JSONResponse({'error': 'RTC setup already in progress'}, status_code=409)
    state._rtc_in_progress = True
    bus.run_bg(_run_rtc, 'enable')
    return {'ok': True}


@router.post('/rtc_remove_start', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_rtc_remove_start():
    if state._rtc_in_progress:
        return JSONResponse({'error': 'RTC setup already in progress'}, status_code=409)
    state._rtc_in_progress = True
    bus.run_bg(_run_rtc, 'disable')
    return {'ok': True}


@router.get('/rtc_log', response_model=LogTail,
            dependencies=[Depends(require_login)])
def route_rtc_log():
    return {'lines': state._rtc_log_lines, 'done': state._rtc_log_done}


@router.get('/logs_download', dependencies=[Depends(require_login)])
def route_logs_download():
    text = '\n'.join(state._log_ring) + '\n'
    ts   = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
    return Response(
        content=text, media_type='text/plain',
        headers={'Content-Disposition': f'attachment; filename="splouch-log-{ts}.log"'})


@router.post('/logs_save', dependencies=[Depends(require_login)])
def route_logs_save():
    try:
        os.makedirs(state.LOGS_DIR, exist_ok=True)
        name = 'splouch-log-' + datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S') + '.log'
        path = os.path.join(state.LOGS_DIR, name)
        with open(path, 'w') as f:
            f.write('\n'.join(state._log_ring) + '\n')
        return {'ok': True, 'path': path}
    except Exception as e:
        return JSONResponse({'ok': False, 'error': str(e)}, status_code=500)


@router.post('/system_reboot', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_system_reboot():
    def _reboot():
        time.sleep(1)
        subprocess.run(['sudo', 'reboot'])
    bus.run_bg(_reboot)
    return {'ok': True}


@router.post('/system_shutdown', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_system_shutdown():
    def _shutdown():
        time.sleep(1)
        subprocess.run(['sudo', 'poweroff'])
    bus.run_bg(_shutdown)
    return {'ok': True}


@router.post('/system_service_restart', response_model=ActionResult,
             dependencies=[Depends(require_login)])
def route_service_restart():
    # Restart just the app (splouch.service) — far faster than a full reboot,
    # and the same command the updater runs. Deferred so this response returns
    # before systemd kills the process serving it.
    def _restart():
        time.sleep(1)
        subprocess.run(['sudo', 'systemctl', 'restart', state.SERVICE_NAME])
    bus.run_bg(_restart)
    return {'ok': True}


@router.get('/backup_download', dependencies=[Depends(require_login)])
def route_backup_download():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        tar.add(state.SCOREBOARD_DIR, arcname='Splouch')
    date_str = datetime.datetime.now().strftime('%Y-%m-%d')
    return Response(
        content=buf.getvalue(), media_type='application/gzip',
        headers={'Content-Disposition':
                 f'attachment; filename="splouch_backup_{date_str}.tar.gz"'})


@router.post('/backup_restore', response_model=ActionResult,
             dependencies=[Depends(require_login)])
async def route_backup_restore(request: Request):
    form = await request.form()
    f = form.get('backup_file')
    if not f or not getattr(f, 'filename', '').endswith('.tar.gz'):
        return JSONResponse({'ok': False, 'error': 'Please upload a .tar.gz backup file'},
                            status_code=400)
    data = await f.read()
    # Extracting the tarball is blocking; run it off the event loop.
    return await run_in_threadpool(_restore_backup, data)


def _restore_backup(data):
    try:
        buf = io.BytesIO(data)
        with tarfile.open(fileobj=buf, mode='r:gz') as tar:
            members = tar.getmembers()
            # Validate all paths stay within home dir (no path traversal)
            home = os.path.expanduser('~')
            for m in members:
                dest = os.path.normpath(os.path.join(home, m.name))
                if not dest.startswith(home):
                    return JSONResponse({'ok': False, 'error': 'Invalid archive path'},
                                        status_code=400)
            tar.extractall(path=home)
    except Exception as e:
        return JSONResponse({'ok': False, 'error': str(e)}, status_code=500)
    # Restart after a short delay so the response can be sent first
    def _restart():
        time.sleep(1)
        subprocess.run(['sudo', 'systemctl', 'restart', state.SERVICE_NAME])
    bus.run_bg(_restart)
    return {'ok': True}
