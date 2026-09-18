"""The panel reloads itself when the app it is served from restarts.

Three actions in Settings restart the service under the page that is watching — the
server update, the service restart on the Power pane, and a backup restore — so the
panel has to bring itself back when the app returns.

All three used to guess how long that takes: 6 seconds after an update, 5 after a
service restart, and the restore polled but believed the first `ok` it got. The guess
measured the wrong thing. `routes/update._run_update` publishes `done`, *then* sleeps
two seconds, refreshes the systemd unit and only then runs `systemctl restart` — so
the browser's timer was already half spent before the server began going down, and on
a Pi the Python cold start finished well after it. The reload landed on a dead port
and the operator saw a browser error page instead of their panel.

The fix is not a bigger number. `reloadWhenServerReturns()` probes until the app
answers again, however long that takes, and only believes a success once a probe has
failed — the old process is still answering for the first couple of seconds, and
reloading against it is the same bug wearing a different hat.

These are source assertions, like the rest of the settings tests: what the restart
actually costs on a given Pi is not something the suite can measure.
"""
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

SETTINGS_JS = os.path.join(REPO, 'shared', 'static', 'js', 'settings.js')


@pytest.fixture(scope='module')
def js():
    return open(SETTINGS_JS, encoding='utf-8').read()


def _body(js, name):
    """One function's source, comments stripped.

    Stripped because these tests read as "the old approach is gone", and the comment
    recording what the old approach was is exactly the string they look for.
    """
    start = js.index('function %s(' % name)
    end = js.index('\n}\n', start) + 2
    return re.sub(r'/\*.*?\*/', '', js[start:end], flags=re.S)


def test_nothing_guesses_how_long_a_restart_takes(js):
    """No `setTimeout(… location.reload …)` in any of the three restart paths.

    The meet-file upload keeps its short timer on purpose — it reloads the page
    against a server that never went anywhere.
    """
    for name in ('startUpdate', 'serviceRestart', 'restoreBackup'):
        try:
            body = _body(js, name)
        except ValueError:
            pytest.skip(f'{name} has been renamed; update this test')
        assert 'reloadWhenServerReturns' in body, f'{name} does not wait for the app'
        assert not re.search(r'setTimeout\([^)]*reload', body), \
            f'{name} is guessing how long the restart takes again'


def test_the_probe_waits_for_the_app_to_go_down_first(js):
    """The old process answers for a second or two after the restart is ordered, so a
    success is only believed once a probe has failed."""
    body = _body(js, 'reloadWhenServerReturns')
    assert 'sawDown' in body, 'a success from the dying process would be believed'
    assert 'graceMs' in body, 'probing starts before the restart is even scheduled'
    assert 'assumeAfterMs' in body, \
        'a restart quick enough to fall between two probes would never reload'
    assert 'timeoutMs' in body, 'a server that never returns would poll forever'


def test_the_probe_outlasts_a_slow_pi(js):
    """The whole point is that it no longer expires before the app is back. The old
    numbers were 5 and 6 seconds; a Pi cold start can take far longer than that."""
    body = _body(js, 'reloadWhenServerReturns')
    timeout = int(re.search(r'opts\.timeoutMs\s*\|\|\s*(\d+)', body).group(1))
    assert timeout >= 120000, f'{timeout}ms is not long enough for a slow boot'
    grace = int(re.search(r'opts\.graceMs\s*\|\|\s*(\d+)', body).group(1))
    assert grace >= 2000, 'update.py sleeps 2s after `done` before it restarts at all'


def test_the_update_publishes_done_before_it_restarts():
    """The fact the whole fix turns on, asserted against the server rather than
    assumed: `done` goes out first, and the restart follows it."""
    src = open(os.path.join(REPO, 'server', 'routes', 'update.py'),
               encoding='utf-8').read()
    done = src.index('_update_log_done = True')
    restart = src.index("'systemctl', 'restart'")
    assert done < restart, 'the restart now precedes `done`; the grace period is moot'
