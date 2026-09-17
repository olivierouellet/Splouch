"""Updating this Pi's own code, and the displays that mirror it.

Split out of ``routes/system`` because it is the one thing in there that is not
about the machine. The rest of that module reads a clock, installs an RTC, saves a
log, reboots — operations on hardware that happen to be reachable over HTTP. This
is the app rewriting itself: a `git fetch`, a checkout confined to a release tag or
an allowlisted branch, `uv sync`, a systemd unit refreshed to match the code that
just landed, and the same instruction broadcast to every Qt display so the board
and the server do not end up on different refs.

Its long jobs report through ``state._update_log_lines`` and friends, polled by the
Update panel — which is why the runners here return nothing and write there instead.

`run_cmd_blocking` comes from ``routes/system``: the RTC installer drives its own
long job the same way, so the runner sits above both rather than travelling with
either.
"""
import os
import re
import subprocess
import time
import tomllib

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import bus
import state
from routes.system import run_cmd_blocking
from web import ActionResult, LogTail, require_login

router = APIRouter(tags=['Update'])

# Single source of truth for the systemd unit (install/scripts/refresh-service.sh).
# Run before each in-app restart so a changed entrypoint/layout self-heals rather
# than crash-looping on a stale unit. Sibling of the repo's server/ dir.
_REFRESH_SCRIPT = os.path.join(state.REPO_DIR,
                               'install', 'scripts', 'refresh-service.sh')


class UpdateStart(BaseModel):
    target: str | None = None


class VersionList(BaseModel):
    # Success returns current/versions/branches; failure returns error. All
    # non-``ok`` fields are optional so both dict shapes validate.
    ok: bool
    current: str = ''
    versions: list[str] = Field(default_factory=list)
    branches: list[str] = Field(default_factory=list)
    error: str | None = None


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


def _dirty_files():
    """Paths git reports as locally modified, staged or untracked.

    ``git status --porcelain`` lines are `XY <path>`; the two status columns are
    fixed-width, so the path starts at offset 3.
    """
    out, rc = run_cmd_blocking(['git', 'status', '--porcelain'], cwd=state.REPO_DIR)
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
        out, rc = run_cmd_blocking(['git', 'fetch', '--tags'], cwd=state.REPO_DIR)
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
        out, rc = run_cmd_blocking(['git', 'checkout', 'HEAD', '--', 'uv.lock'],
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
        elif _VERSION_RE.match(target):
            cmds  = [['git', 'checkout', target], [_UV, 'sync']]
            label = f'Version {target} installed'
        else:
            # Anything not a release tag or an allowlisted branch stops here. The
            # dropdown only ever offers those two, so a value that reaches this
            # branch was hand-sent — and `git checkout <ref>` would take any commit
            # in the repo, including one whose code has never been reviewed.
            emit(f'\nUnknown version "{target}".\n', error=True)
            state._update_log_done = False
            return

        for cmd in cmds:
            emit('$ ' + ' '.join(cmd) + '\n')
            out, rc = run_cmd_blocking(cmd, cwd=state.REPO_DIR)
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
        # restart. Non-interactive (sudo -n): an install predating the sudoers grant
        # skips this and is told to re-run the installer.
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
        out, rc = run_cmd_blocking(['git', 'reset', '--hard', 'HEAD'],
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
            out, rc = run_cmd_blocking(cmd)
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

