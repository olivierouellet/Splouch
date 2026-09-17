"""Recovering an update that stopped on a dirty checkout.

`uv sync` rewrites `uv.lock` on every deploy, so a Pi's checkout is essentially
always dirty. The updater discards that one file, but nothing else — so any other
stray edit blocks every future update with git's "local changes would be
overwritten", which tells a meet operator nothing at all.

Two halves are tested here: the update log explains *which* files are in the way,
and `Repair checkout` on the Update panel clears them.

Every test runs against a throwaway repo in tmp_path. `_run_repair` runs
`git reset --hard`, so pointing it at the real checkout would delete whatever the
developer was working on.
"""
import os
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import state                            # noqa: E402
import routes.update as system          # noqa: E402


def _git(repo, *args):
    subprocess.run(['git', *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A one-commit git repo, standing in for the installed copy on the Pi."""
    repo = tmp_path / 'Splouch'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.email', 'test@example.com')
    _git(repo, 'config', 'user.name', 'Test')
    (repo / 'uv.lock').write_text('locked\n')
    (repo / 'app.py').write_text('original\n')
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', 'initial')
    monkeypatch.setattr(state, 'REPO_DIR', str(repo))
    return repo


@pytest.fixture(autouse=True)
def _reset_log():
    state._update_log_lines = []
    state._update_log_done = None
    state._update_repair_needed = False
    yield
    state._update_in_progress = False


def _log():
    return ''.join(line['text'] for line in state._update_log_lines)


# ── Spotting the problem ───────────────────────────────────────────────────────

def test_dirty_files_sees_all_three_kinds(checkout):
    (checkout / 'app.py').write_text('edited\n')
    (checkout / 'uv.lock').write_text('resynced\n')
    _git(checkout, 'add', 'uv.lock')            # staged
    (checkout / 'notes.txt').write_text('hi\n')  # untracked

    assert sorted(system._dirty_files()) == ['app.py', 'notes.txt', 'uv.lock']


def test_a_clean_checkout_reports_nothing(checkout):
    assert system._dirty_files() == []


def test_the_failure_names_the_files_and_offers_the_button(checkout):
    """Git's own message lists nothing an operator can act on."""
    (checkout / 'app.py').write_text('edited\n')
    system._explain_dirty_failure(lambda text, error=False:
                                  state._update_log_lines.append(
                                      {'text': text, 'error': error}))

    assert state._update_repair_needed, 'the Repair button must be offered'
    assert 'app.py' in _log()
    assert 'Repair checkout' in _log()


def test_a_clean_checkout_does_not_offer_the_button(checkout):
    """An update can fail for reasons repair cannot fix — a lost network, say."""
    system._explain_dirty_failure(lambda text, error=False: None)
    assert not state._update_repair_needed


# ── Fixing it ──────────────────────────────────────────────────────────────────

def test_repair_discards_tracked_edits_staged_or_not(checkout):
    (checkout / 'app.py').write_text('edited\n')
    (checkout / 'uv.lock').write_text('resynced\n')
    _git(checkout, 'add', 'uv.lock')

    system._run_repair()

    assert state._update_log_done is True
    assert (checkout / 'app.py').read_text() == 'original\n'
    assert (checkout / 'uv.lock').read_text() == 'locked\n'
    assert system._dirty_files() == [], 'the pull would still be blocked'


def test_repair_lists_what_it_is_about_to_throw_away(checkout):
    (checkout / 'app.py').write_text('edited\n')
    system._run_repair()
    assert 'app.py' in _log()


def test_repair_leaves_untracked_files_alone(checkout):
    """Someone's notes or a hand-copied recording are not this button's business."""
    (checkout / 'app.py').write_text('edited\n')
    (checkout / 'notes.txt').write_text('do not delete me\n')

    system._run_repair()

    assert (checkout / 'notes.txt').read_text() == 'do not delete me\n'
    assert (checkout / 'app.py').read_text() == 'original\n'
    assert state._update_log_done is True
    assert 'not removed' in _log(), 'the leftover must be called out, not hidden'


def test_repair_does_not_change_the_installed_version(checkout):
    """It resets *to* HEAD; it must never move HEAD."""
    before = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=checkout,
                            capture_output=True, text=True).stdout
    (checkout / 'app.py').write_text('edited\n')
    system._run_repair()
    after = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=checkout,
                           capture_output=True, text=True).stdout
    assert before == after


def test_repair_on_a_clean_checkout_is_a_no_op(checkout):
    system._run_repair()
    assert state._update_log_done is True
    assert 'Nothing to repair' in _log()


def test_repair_clears_the_flag_that_summoned_it(checkout):
    state._update_repair_needed = True
    (checkout / 'app.py').write_text('edited\n')
    system._run_repair()
    assert not state._update_repair_needed, 'the button would linger after the fix'


def test_a_failed_repair_keeps_the_button_on_screen(checkout, monkeypatch):
    """The tree is still dirty, so repair is still the answer — clearing the flag
    would hide the only button that can retry it."""
    (checkout / 'app.py').write_text('edited\n')
    real = system.run_cmd_blocking

    def only_reset_fails(cmd, cwd=None):
        if cmd[:2] == ['git', 'reset']:
            return ('error: unable to unlink app.py: Permission denied\n', 1)
        return real(cmd, cwd=cwd)

    monkeypatch.setattr(system, 'run_cmd_blocking', only_reset_fails)
    system._run_repair()

    assert state._update_log_done is False
    assert state._update_repair_needed, 'no way left to retry'


def test_repair_releases_the_update_lock(checkout):
    """It shares `_update_in_progress` with the updater, so pressing Install right
    after must not 409."""
    state._update_in_progress = True
    system._run_repair()
    assert not state._update_in_progress
