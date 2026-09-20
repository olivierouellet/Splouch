"""Updating the kiosks from the server's Settings page.

The server broadcasts `update {target}` on /ws/scoreboard; each display checks out
that ref, runs `uv sync`, reports progress as `update_log`, and
exits **non-zero** so `start-scoreboard.sh` relaunches it on the new code.

The dangerous outcomes are a display that restarts mid-race and a display that
exits into a broken checkout. Both are tested here.
"""
import asyncio
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import bus                                          # noqa: E402
import state                                        # noqa: E402
from routes.update import route_displays_update     # noqa: E402


@pytest.fixture
def clean_state(monkeypatch):
    monkeypatch.setattr(state, '_scoreboard_clients', {}, raising=False)
    monkeypatch.setattr(state, '_running_lanes', set(), raising=False)
    emitted = []
    monkeypatch.setattr(bus, 'emit', lambda ch, ev, data=None: emitted.append((ch, ev, data)))
    return emitted


def _register_kiosk(hostname='splouch-tv'):
    state._scoreboard_clients[len(state._scoreboard_clients) + 1] = {
        'ip': '10.10.10.20', 'at': '09:41', 'role': 'kiosk',
        'hostname': hostname, 'version': 'v2026.07.9', 'commit': 'aaa1111',
        'dirty': False}


def _call():
    return asyncio.run(route_displays_update())


# ── The endpoint ───────────────────────────────────────────────────────────────

def test_it_broadcasts_the_servers_own_ref(clean_state, monkeypatch):
    """No target is accepted from the caller — lockstep is the entire point."""
    monkeypatch.setattr(state, 'git_describe', lambda: {'version': 'v2026.08.1',
                                                        'commit': 'dddbc6f'})
    _register_kiosk()
    result = _call()
    assert result == {'ok': True}
    assert ('/scoreboard', 'update', {'target': 'v2026.08.1'}) in clean_state


def test_it_refuses_while_a_race_is_running(clean_state, monkeypatch):
    """A display restarts to finish updating. Not mid-race."""
    monkeypatch.setattr(state, 'git_describe', lambda: {'version': 'v2026.08.1',
                                                        'commit': 'd'})
    _register_kiosk()
    state._running_lanes.add(3)
    response = _call()
    assert getattr(response, 'status_code', None) == 409
    assert not clean_state, 'nothing should have been broadcast'


def test_it_refuses_when_no_display_is_registered(clean_state, monkeypatch):
    monkeypatch.setattr(state, 'git_describe', lambda: {'version': 'v2026.08.1',
                                                        'commit': 'd'})
    state._scoreboard_clients[1] = {'ip': '10.10.10.55', 'at': '09:44'}   # browser tab
    assert getattr(_call(), 'status_code', None) == 404


def test_it_refuses_from_a_dirty_server(clean_state, monkeypatch):
    """`--dirty` appends a suffix that is not a real object; every kiosk would
    fail to check it out."""
    monkeypatch.setattr(state, 'git_describe',
                        lambda: {'version': 'v2026.08.1-2-gabc-dirty', 'commit': 'a'})
    _register_kiosk()
    assert getattr(_call(), 'status_code', None) == 409


def test_it_resets_each_displays_previous_log(clean_state, monkeypatch):
    """A failed attempt must not leave stale red lines under a fresh run."""
    monkeypatch.setattr(state, 'git_describe', lambda: {'version': 'v2026.08.1',
                                                        'commit': 'd'})
    _register_kiosk()
    client = next(iter(state._scoreboard_clients.values()))
    client['update_lines'] = [{'text': 'old failure', 'error': True}]
    client['update_state'] = 'failed'
    _call()
    assert client['update_lines'] == []
    assert client['update_state'] == 'updating'


# ── The log frames the server collects ─────────────────────────────────────────

def _apply_update_log(client, payload):
    """The normalisation `app.ws_scoreboard` performs on an update_log frame."""
    lines = client.setdefault('update_lines', [])
    lines.append({'text': str(payload.get('text', ''))[:400],
                  'error': bool(payload.get('error', False))})
    del lines[:-state.UPDATE_LOG_MAX]
    done = payload.get('done')
    client['update_state'] = ('updating' if done is None
                              else 'ok' if done else 'failed')
    return client


def test_log_frames_build_a_capped_tail():
    """Unauthenticated LAN input — a chatty client must not grow state forever."""
    client = {}
    for i in range(state.UPDATE_LOG_MAX * 3):
        _apply_update_log(client, {'text': f'line {i}', 'done': None})
    assert len(client['update_lines']) == state.UPDATE_LOG_MAX
    assert client['update_lines'][-1]['text'].endswith(str(state.UPDATE_LOG_MAX * 3 - 1))


@pytest.mark.parametrize('done, expected', [(None, 'updating'), (True, 'ok'),
                                            (False, 'failed')])
def test_done_maps_to_a_state(done, expected):
    client = _apply_update_log({}, {'text': 'x', 'done': done})
    assert client['update_state'] == expected


def test_an_overlong_line_is_truncated():
    client = _apply_update_log({}, {'text': 'x' * 5000, 'done': None})
    assert len(client['update_lines'][0]['text']) == 400


# ── The display side ───────────────────────────────────────────────────────────

class _FakeSubprocess:
    """Stands in for the `subprocess` module inside updater only.

    Patching `subprocess.run` itself would be global — every other test, and Qt's
    own font machinery, share that module.
    """

    TimeoutExpired = TimeoutError

    # Set by the tests that need a result after the scripted ones run out.
    _results_default: object

    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    def run(self, args, **kwargs):
        self.calls.append(args)
        return self._results.pop(0) if self._results else self._results_default


class _Res:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_the_updater_refuses_a_dirty_checkout(monkeypatch):
    """`git checkout` would discard someone's uncommitted work."""
    pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')
    import scoreboard.updater as updater

    fake = _FakeSubprocess([_Res(), _Res(stdout=' M scoreboard/board.py\n')])
    monkeypatch.setattr(updater, 'subprocess', fake)
    up = updater.Updater()
    lines = []
    up.line.connect(lambda text, error: lines.append((text, error)))
    assert up._update('v2026.08.1') is False
    assert any('local changes' in text for text, _ in lines)


def test_a_failed_step_stops_the_update(monkeypatch):
    """And crucially reports failure, so the caller does not restart into it."""
    pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')
    import scoreboard.updater as updater

    # uv.lock reset, clean dirty-check, then a fetch that fails.
    fake = _FakeSubprocess([_Res(), _Res(),
                            _Res(128, stderr='fatal: could not read from remote')])
    fake._results_default = _Res(128)
    monkeypatch.setattr(updater, 'subprocess', fake)
    up = updater.Updater()
    done = []
    up.done.connect(done.append)
    up._busy.acquire()          # start() normally does this; _run releases it
    up._run('v2026.08.1')
    assert done == [False], 'a failed fetch must not report success'
    assert not up.running, '_run must release the lock even on failure'


def test_a_second_request_while_updating_is_ignored(monkeypatch):
    pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')
    import scoreboard.updater as updater

    up = updater.Updater()
    up._busy.acquire()
    try:
        assert up.running is True
        assert up.start('v2026.08.1') is False
    finally:
        up._busy.release()


def test_the_app_refuses_to_update_mid_race(qt_app, monkeypatch):
    """Belt and braces — the server guards too, but a display may miss a frame.

    Takes the session-scoped `qt_app` fixture: creating a QApplication here and
    not holding a reference gets it garbage-collected, which aborts the process.
    """
    pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')
    from scoreboard.app import ScoreboardApp

    app = ScoreboardApp('http://127.0.0.1:1', fullscreen=False)
    sent = []
    monkeypatch.setattr(app.link, 'send', lambda event, data=None: sent.append((event, data)))
    started = []
    monkeypatch.setattr(app.updater, 'start', lambda target: started.append(target) or True)
    try:
        app.window.apply_update({'lane_running1': True})
        app._on_frame('update', {'target': 'v2026.08.1'})
        assert not started, 'started an update with a swimmer in the water'
        assert any('race is running' in (d or {}).get('text', '') for _, d in sent)
    finally:
        app.link.stop()
