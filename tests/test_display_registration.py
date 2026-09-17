"""Displays identify themselves, so the server can show which ref each is on.

The single-repo decision rests on the kiosk and the server running the *same* git
ref — that is what guarantees they agree about the WebSocket contract. Until now
the only way to check was an SSH session per kiosk.

Qt-free apart from one `ServerLink` test, which stubs the socket rather than
opening one.
"""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import state                                        # noqa: E402
from scoreboard.version import describe, registration   # noqa: E402

TEMPLATE_DIR = os.path.join(REPO, 'server', 'templates')


# ── What the display reports ───────────────────────────────────────────────────

def test_describe_reports_this_checkout():
    info = describe()
    assert info['version'], 'no version — is this a git checkout?'
    assert info['commit']
    assert isinstance(info['dirty'], bool)


def test_registration_carries_role_and_hostname():
    payload = registration()
    assert payload['role'] == 'kiosk'
    assert set(payload) == {'role', 'hostname', 'version', 'commit', 'dirty'}


def test_describe_survives_a_checkout_without_git(monkeypatch):
    """An unpacked tarball must still run — degrade, never raise."""
    import scoreboard.version as version
    monkeypatch.setattr(version, '_git', lambda *args: None)
    assert describe() == {'version': '', 'commit': '', 'dirty': False}


# ── What the server does with it ───────────────────────────────────────────────

def _apply_register(client, payload):
    """The same normalisation `app.ws_scoreboard` performs on a register frame."""
    client.update({
        'role':     str(payload.get('role', ''))[:20],
        'hostname': str(payload.get('hostname', ''))[:64],
        'version':  str(payload.get('version', ''))[:64],
        'commit':   str(payload.get('commit', ''))[:16],
        'dirty':    bool(payload.get('dirty', False)),
    })
    return client


def test_the_handler_normalises_a_register_frame():
    client = _apply_register({'ip': '10.10.10.20', 'at': '09:41'}, registration())
    assert client['role'] == 'kiosk'
    assert client['ip'] == '10.10.10.20', 'connection details must survive'


def test_oversized_fields_are_truncated():
    """The frame is unauthenticated LAN input; it should not be able to bloat state."""
    client = _apply_register({}, {'role': 'k' * 500, 'hostname': 'h' * 500,
                                  'version': 'v' * 500, 'commit': 'c' * 500,
                                  'dirty': 'yes'})
    assert len(client['role']) == 20
    assert len(client['hostname']) == 64
    assert len(client['version']) == 64
    assert len(client['commit']) == 16
    assert client['dirty'] is True


def test_the_server_knows_its_own_ref():
    info = state.git_describe()
    assert info['version'] and info['commit']


def test_git_describe_is_cached(monkeypatch):
    """`/clients_fragment` is an async route HTMX polls — two git subprocesses per
    poll would run on the event loop and stall every WebSocket on the box."""
    state.git_describe()                       # prime
    monkeypatch.setattr(state, 'subprocess', None)   # blow up if it shells out again
    assert state.git_describe()['version']


# ── What the operator sees ─────────────────────────────────────────────────────

def _render(clients, server_version='v2026.08.1'):
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR))
    return env.get_template('settings/fetched/clients.html').render(
        clients=clients, server_version=server_version,
        t=state.settings_strings('en'))


def test_a_matching_kiosk_gets_no_warning_badge():
    html = _render([{'ip': '10.10.10.20', 'at': '09:41', 'role': 'kiosk',
                     'hostname': 'splouch-tv', 'version': 'v2026.08.1',
                     'commit': 'dddbc6f', 'dirty': False}])
    assert 'splouch-tv' in html and 'kiosk' in html
    assert 'different version' not in html
    assert 'modified' not in html


def test_a_kiosk_on_another_ref_is_flagged():
    """The whole point: a version split is invisible today until something breaks."""
    html = _render([{'ip': '10.10.10.21', 'at': '09:40', 'role': 'kiosk',
                     'hostname': 'splouch-tv2', 'version': 'v2026.07.9',
                     'commit': 'aaaa111', 'dirty': False}])
    assert 'different version' in html


def test_a_kiosk_with_local_edits_is_flagged():
    """Usually a forgotten dev change, which then resists updating."""
    html = _render([{'ip': '10.10.10.22', 'at': '09:42', 'role': 'kiosk',
                     'hostname': 'splouch-tv3', 'version': 'v2026.08.1-dirty',
                     'commit': 'dddbc6f', 'dirty': True}])
    assert 'modified' in html
    assert 'different version' not in html, 'dirty already explains the difference'


def test_a_browser_tab_still_renders():
    """Browser tabs never send `register` — they must not look broken."""
    html = _render([{'ip': '10.10.10.55', 'at': '09:44'}])
    assert '10.10.10.55' in html
    assert 'kiosk' not in html
    assert 'different version' not in html


@pytest.mark.parametrize('code', ['en', 'fr', 'es'])
def test_the_badges_are_translated(code):
    for key in ('clients_mismatch', 'clients_mismatch_hint',
                'clients_dirty', 'clients_dirty_hint'):
        assert state.settings_strings(code).get(key), f'{code} missing {key}'


# ── The display actually sends it ──────────────────────────────────────────────

def test_the_link_registers_on_every_connect(monkeypatch):
    """Including reconnects — the server's list must be right again after a drop."""
    pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')
    from scoreboard.client import ServerLink

    sent = []

    class FakeSocket:
        enable_multithreading = False

        def settimeout(self, _):
            pass

        def send(self, raw):
            sent.append(json.loads(raw))

        def recv(self):
            raise KeyboardInterrupt          # break out of the receive loop

        def close(self):
            pass

    import websocket
    monkeypatch.setattr(websocket, 'create_connection', lambda *a, **k: FakeSocket())

    # recv() raises KeyboardInterrupt, which is not an Exception subclass, so it
    # escapes the loop's handler and ends _run after exactly one connect.
    link = ServerLink('http://127.0.0.1:1', register=lambda: {'role': 'kiosk'})
    try:
        link._run()
    except KeyboardInterrupt:
        pass
    assert sent and sent[0] == {'event': 'register', 'data': {'role': 'kiosk'}}


def test_a_failing_register_does_not_stop_the_board(monkeypatch):
    """Registration is diagnostics; it must never cost us the live times."""
    pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')
    from scoreboard.client import ServerLink

    connected = []

    class FakeSocket:
        enable_multithreading = False

        def settimeout(self, _):
            pass

        def send(self, raw):
            raise OSError('network went away')

        def recv(self):
            raise KeyboardInterrupt

        def close(self):
            pass

    import websocket
    monkeypatch.setattr(websocket, 'create_connection', lambda *a, **k: FakeSocket())

    link = ServerLink('http://127.0.0.1:1', register=lambda: {'role': 'kiosk'})
    link.connected.connect(lambda ok: connected.append(ok))
    try:
        link._run()
    except KeyboardInterrupt:
        pass
    assert True in connected, 'the link should still report itself connected'
