"""Self-update, triggered by the server.

The operator presses one button in Settings → Update and every connected kiosk
moves to the server's ref. Without this, keeping displays in step means an SSH
session per Pi — and the single-repo design assumes they *are* in step.

The restart needs nothing new. `install/scripts/start-scoreboard.sh` already
relaunches the app whenever it exits non-zero, so a successful update just exits
1 and comes back on the new code. No systemd unit, no sudo, no extra privilege.

Two rules keep a failed update from taking the TV down:

* **Only exit on success.** A failed `uv sync` leaves the old code running and
  reports the failure. Exiting into a broken checkout means a black screen and a
  walk to the kiosk, which is the one outcome worse than being out of date.
* **Never touch a dirty checkout.** Local edits mean `git checkout` would either
  fail or discard someone's work. Report and stop.
* **Sync the same extras the installer does, then prove Qt still imports.** The
  kiosk's Qt comes from an optional extra, and `uv sync` removes whatever it was
  not asked for — so the step that installs the new version is also the step that
  can uninstall the one thing the app needs to start.
"""
import os
import shutil
import subprocess
import threading

from PySide6.QtCore import QObject, Signal

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Each git/uv step gets its own ceiling: a fetch over venue Wi-Fi is slow, and a
# `uv sync` that has to build a wheel on a Pi is slower still.
_FETCH_TIMEOUT = 120
_SYNC_TIMEOUT  = 900


def _find_uv():
    """Same search the server uses — uv is often outside a service PATH."""
    found = shutil.which('uv')
    if found:
        return found
    for candidate in (os.path.expanduser('~/.local/bin/uv'),
                      os.path.expanduser('~/.cargo/bin/uv'),
                      '/usr/local/bin/uv'):
        if os.path.isfile(candidate):
            return candidate
    return 'uv'


class Updater(QObject):
    """Runs the update in a worker thread, reporting progress as it goes."""

    line = Signal(str, bool)     # text, is_error
    done = Signal(bool)          # True when the app should restart

    def __init__(self, parent=None):
        super().__init__(parent)
        self._busy = threading.Lock()

    @property
    def running(self) -> bool:
        return self._busy.locked()

    def start(self, target: str):
        """Move this checkout to *target*. No-op if an update is already running."""
        if not self._busy.acquire(blocking=False):
            return False
        threading.Thread(target=self._run, args=(target,), daemon=True).start()
        return True

    # ── Worker thread ──────────────────────────────────────────────────────────

    def _run(self, target):
        ok = False
        try:
            ok = self._update(target)
        except Exception as e:                       # never let the thread die silently
            self.line.emit(f'Error: {e}', True)
        finally:
            self._busy.release()
            self.done.emit(ok)

    def _cmd(self, args, timeout):
        self.line.emit('$ ' + ' '.join(args), False)
        try:
            result = subprocess.run(args, cwd=_REPO, capture_output=True, text=True,
                                    timeout=timeout)
        except subprocess.TimeoutExpired:
            self.line.emit(f'timed out after {timeout}s', True)
            return False
        except OSError as e:
            self.line.emit(str(e), True)
            return False
        output = (result.stdout or '') + (result.stderr or '')
        if output.strip():
            self.line.emit(output.strip()[-2000:], False)
        if result.returncode != 0:
            self.line.emit(f'exit code {result.returncode}', True)
            return False
        return True

    def _update(self, target):
        if not os.path.isdir(os.path.join(_REPO, '.git')):
            self.line.emit('Not a git checkout — update from the installer instead.',
                           True)
            return False

        # `uv sync` rewrites uv.lock; that expected drift must not read as a local
        # edit, so discard it before deciding whether the tree is dirty. `HEAD --`,
        # not a bare `--`: the latter restores from the index, so a *staged* uv.lock
        # would stay different from HEAD and the dirty-tree check below would refuse
        # the update over a file we are deliberately discarding.
        subprocess.run(['git', 'checkout', 'HEAD', '--', 'uv.lock'], cwd=_REPO,
                       capture_output=True, timeout=30)
        status = subprocess.run(['git', 'status', '--porcelain'], cwd=_REPO,
                                capture_output=True, text=True, timeout=30)
        if status.stdout.strip():
            self.line.emit('This checkout has local changes — refusing to update, '
                           'since checking out would discard them.', True)
            return False

        if not self._cmd(['git', 'fetch', '--tags'], _FETCH_TIMEOUT):
            return False
        # -B so a branch target follows origin rather than staying on a stale local
        # tip; harmless for a tag, which resolves to a detached head either way.
        if not self._cmd(['git', 'checkout', '-B', 'display', target],
                         _FETCH_TIMEOUT):
            if not self._cmd(['git', 'checkout', target], _FETCH_TIMEOUT):
                return False
        # `--extra scoreboard`, exactly as `install.sh kiosk` does. `uv sync` is
        # declarative: it makes the environment match the lockfile for the extras it
        # was *given*, and removes everything else. A bare sync here therefore
        # uninstalled PySide6 — the one dependency this app cannot start without —
        # on every remote update, and the display came back to a stack trace.
        if not self._cmd([_find_uv(), 'sync', '--extra', 'scoreboard'],
                         _SYNC_TIMEOUT):
            return False

        # Prove the display can still start before telling anyone this worked. The
        # update restarts the app, and `start-scoreboard.sh` only relaunches on a
        # non-zero exit — so a checkout that cannot import Qt becomes a black TV
        # and a five-second restart loop, with the failure two steps back.
        if not self._cmd([os.path.join(_REPO, '.venv', 'bin', 'python'),
                          '-c', 'import PySide6.QtWidgets'], _FETCH_TIMEOUT):
            self.line.emit('The new version cannot load Qt — staying on the old one.',
                           True)
            return False

        self.line.emit(f'Updated to {target}. Restarting…', False)
        return True
