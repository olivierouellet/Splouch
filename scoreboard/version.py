"""What git ref this display is running.

The single-repo decision rests on the kiosk and the server sitting on the *same*
ref — that is what guarantees they agree about the WebSocket contract (see
`notes/native_app_strategy.md`). Until now there was no way to check it short of
an SSH session, so the display reports its version when it registers and the
server shows it in Settings → Network.

Deliberately not shared with the server's own copy of this: `docs/api.md` says
there is no shared client library, and a five-line subprocess call is not worth
breaking that for. Qt-free, so it can be tested without PySide6.
"""
import os
import socket
import subprocess
import threading

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# git on a cold page cache can be slow; short enough not to delay a reconnect,
# long enough for a Pi's SD card.
_TIMEOUT = 8


def _git(*args):
    try:
        result = subprocess.run(('git',) + args, cwd=_REPO, capture_output=True,
                                text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def describe() -> dict:
    """Identify this checkout, as the display reports it on `register`.

    Every field degrades to a usable value: an unpacked tarball with no `.git`
    reports empty strings rather than raising, and the display still runs. Call
    it off the GUI thread — it shells out to git.
    """
    version = _git('describe', '--tags', '--always', '--dirty')
    commit  = _git('rev-parse', '--short', 'HEAD')
    status  = _git('status', '--porcelain')
    return {
        'version': version or '',
        'commit':  commit or '',
        # Local edits mean the ref alone no longer describes what is running —
        # worth flagging in the UI, because it is usually a forgotten dev change
        # on a kiosk that then resists updating.
        'dirty':   bool(status),
    }


# describe() memoised, for the operator menu. A checkout's ref cannot change while
# the app runs — an update restarts it — so this is computed once and read freely.
_CACHE = {}
_CACHE_LOCK = threading.Lock()


def warm_cache():
    """Compute :func:`describe` once, for :func:`cached_version` to serve.

    Meant to be run in a daemon thread at startup: three git subprocesses, up to
    eight seconds each on a cold SD card, and the GUI thread must not wait for them.

    Deliberately takes no arguments and touches nothing outside this module. A
    worker that captures a Qt object can end up holding the last reference to it,
    and freeing a QWidget from a non-GUI thread is a crash — see the note in
    `tests/conftest.py`.
    """
    data = describe()
    with _CACHE_LOCK:
        _CACHE.update(data)


def cached_version() -> str:
    """This checkout's ref, or ``''`` until :func:`warm_cache` has finished."""
    with _CACHE_LOCK:
        return _CACHE.get('version', '')


def registration(role: str = 'kiosk') -> dict:
    """The full `register` payload (docs/api.md §2)."""
    try:
        hostname = socket.gethostname()
    except OSError:
        hostname = ''
    return {'role': role, 'hostname': hostname, **describe()}
