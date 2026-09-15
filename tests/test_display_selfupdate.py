"""What a display runs when it updates itself, and what it must not.

A remote update is the one operation that can leave a kiosk unable to start. It
replaces the code and then restarts the app, and `start-scoreboard.sh` only
relaunches on a non-zero exit — so a checkout that cannot import Qt becomes a black
TV in a five-second restart loop, with the cause two steps back and no operator
anywhere near a terminal.

That is not hypothetical. `uv sync` is declarative: it makes the environment match
the lockfile for the extras it was *given* and removes everything else. The kiosk's
Qt lives in the optional `scoreboard` extra, which `install.sh kiosk` asks for and
the updater did not — so every remote update quietly uninstalled PySide6, and the
display came back to a stack trace. `uv sync --dry-run` in this repo still reports
"Would uninstall pyside6-essentials, shiboken6", which is exactly what happened on
the Pi.

Needs PySide6 (`scoreboard.updater` imports QtCore); skips without it.
"""
import os
import re
import sys

import pytest

pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from scoreboard.updater import Updater        # noqa: E402

INSTALLER = os.path.join(REPO, 'install', 'install.sh')


@pytest.fixture
def ran(monkeypatch):
    """Record every command `_update` would run, and let them all succeed."""
    calls = []

    def fake_cmd(self, args, timeout):
        calls.append(list(args))
        return True

    monkeypatch.setattr(Updater, '_cmd', fake_cmd)
    # The guards before the git work: a real checkout, and a clean tree.
    monkeypatch.setattr(os.path, 'isdir', lambda p: True)

    import subprocess
    def fake_run(args, **kw):
        calls.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout='', stderr='')
    monkeypatch.setattr(subprocess, 'run', fake_run)
    return calls


def _sync(calls):
    return next((c for c in calls if len(c) > 1 and c[1] == 'sync'), None)


# ── The step that broke displays ───────────────────────────────────────────────

def test_the_sync_keeps_the_qt_extra(ran):
    Updater()._update('v2026.09.0')
    sync = _sync(ran)
    assert sync is not None, 'no sync ran at all'
    assert '--extra' in sync and 'scoreboard' in sync, (
        f'{sync} — a bare `uv sync` uninstalls PySide6 and the display will not start')


def _installer_kiosk_extras():
    """The `--extra` flags `install.sh kiosk` passes to `uv sync`."""
    source = open(INSTALLER, encoding='utf-8').read()
    kiosk = source[source.index('ROLE" == "kiosk"'):]
    line = re.search(r'uv sync ([^\n;|&]*)', kiosk)
    assert line, 'the kiosk role no longer runs uv sync'
    return re.findall(r'--extra\s+(\S+)', line.group(1))


def test_it_syncs_the_same_extras_the_installer_does(ran):
    """The invariant behind the bug: these two drifted apart, and a display could
    then only be repaired by hand, at the Pi."""
    Updater()._update('v2026.09.0')
    sync = _sync(ran)
    wanted = _installer_kiosk_extras()
    assert wanted, 'the installer stopped asking for an extra — check this still holds'
    for flag in wanted:
        assert flag in sync, (
            f'install.sh asks for --extra {flag}; the updater runs {sync}')


def test_it_checks_qt_still_imports_before_calling_it_a_success(ran):
    """Whatever else changes, the last word must come from actually loading Qt."""
    Updater()._update('v2026.09.0')
    probe = [c for c in ran if '-c' in c and any('PySide6' in str(a) for a in c)]
    assert probe, 'nothing verified the new checkout can start'
    sync_at = ran.index(_sync(ran))
    assert ran.index(probe[0]) > sync_at, 'checked Qt before installing it'


def test_a_checkout_that_cannot_load_qt_is_not_reported_as_updated(monkeypatch):
    """It must stay on the old version rather than restart into a broken one."""
    import subprocess
    monkeypatch.setattr(os.path, 'isdir', lambda p: True)
    monkeypatch.setattr(subprocess, 'run',
                        lambda args, **kw: subprocess.CompletedProcess(args, 0, '', ''))

    def fake_cmd(self, args, timeout):
        return not (len(args) > 1 and args[1] == '-c')   # the Qt probe fails
    monkeypatch.setattr(Updater, '_cmd', fake_cmd)

    updater = Updater()
    said = []
    updater.line.connect(lambda text, error: said.append((text, error)))
    assert updater._update('v2026.09.0') is False
    assert any(error and 'Qt' in text for text, error in said), said


def test_the_new_code_is_in_place_before_it_is_installed(ran):
    """Checkout, then sync, then the Qt probe. Syncing before the checkout would
    resolve the *old* lockfile and report a success that installed nothing new."""
    Updater()._update('v2026.09.0')
    target_checkout = next(i for i, c in enumerate(ran)
                           if c[:2] == ['git', 'checkout'] and 'v2026.09.0' in c)
    sync_at = ran.index(_sync(ran))
    probe_at = next(i for i, c in enumerate(ran)
                    if '-c' in c and any('PySide6' in str(a) for a in c))
    assert target_checkout < sync_at < probe_at, ran


def test_a_fetch_precedes_the_checkout_of_the_target(ran):
    """The ref comes from the server and may be newer than anything this Pi has."""
    Updater()._update('v2026.09.0')
    fetch_at = next(i for i, c in enumerate(ran) if c[:2] == ['git', 'fetch'])
    target_at = next(i for i, c in enumerate(ran)
                     if c[:2] == ['git', 'checkout'] and 'v2026.09.0' in c)
    assert fetch_at < target_at, ran


# ── The installer has to be able to repair one ─────────────────────────────────

def test_the_installer_never_runs_a_bare_git_pull():
    """A display that has taken a remote update sits on `display`, a branch pinned
    to a commit and therefore with no upstream. `git pull` there fails with "no
    tracking information", and `install.sh` runs under `set -e` — so the one command
    an operator reaches for when a display is broken aborted before repairing it."""
    source = open(INSTALLER, encoding='utf-8').read()
    offenders = [line.strip() for line in source.splitlines()
                 if re.search(r'^\s*git .*\bpull\b', line)]
    assert not offenders, offenders


def test_the_installer_fetches_without_assuming_a_branch_is_tracked():
    source = open(INSTALLER, encoding='utf-8').read()
    assert 'fetch_and_ff()' in source, 'the safe fetch helper is gone'
    assert source.count('fetch_and_ff "$INSTALL_DIR"') >= 3, (
        'a code path still fetches its own way')
    assert '@{u}' in source, 'nothing checks for an upstream before merging'


def test_choosing_master_lands_on_the_remote_branch():
    """A display arriving here has just skipped its pull, so its *local* master is
    as old as the last install. Checking that out and calling it "master" is how a
    repaired display ends up months behind the server it has to match."""
    source = open(INSTALLER, encoding='utf-8').read()
    body = source[source.index('checkout_version() {'):]
    body = body[:body.index('\n}')]
    assert 'origin/$branch' in body, body
