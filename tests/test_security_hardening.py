"""The fixes from the September 2026 security audit, each pinned to a test.

Every case here is a defect that shipped, not a hypothetical. They are grouped by
what an attacker had to be able to do:

* **Nothing at all.** The session signing key was a constant in `server/app.py`,
  and this repo is public — so a cookie minted from it authenticated against every
  Splouch install ever made, password or no password. `/ws/terminal` then handed
  out a shell, since only `/terminal_start` was login-gated and the socket itself
  was not.
* **Get a page opened.** No WebSocket checked `Origin`, and the same-origin policy
  does not cover WebSockets — so any site could open one onto the pool-deck LAN.
  The destructive endpoints are GETs with a SameSite=Lax cookie, so a link was
  enough to wipe the meet files.
* **Get a name into the start list.** The schedule embedded `json.dumps(...)` with
  `| safe` inside a `<script>`, and `json.dumps` does not escape `</script>`. A
  swimmer's name is third-party text from a Splash export, and that page is public
  on the Pi *and* on the cloud, where every attendee's phone loads it.
* **Be signed in.** The backup restore extracted an uploaded tarball with no
  filter, following symlinks out of the home directory; the update endpoint fed an
  unvalidated string to `git checkout`.

Qt-free: templates are rendered as text, routes driven directly.
"""
import io
import json
import os
import sys
import tarfile
import tempfile

import pytest
from jinja2 import Environment, FileSystemLoader

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))


# ── The start list cannot close the script it is embedded in ──────────────────

# What a Splash export can legitimately contain: the club name is free text typed
# by whoever entered the swimmer, and it reaches the page unchanged.
_XSS_NAME = '</script><img src=x onerror=alert(1)>'
_HEATS = [{'event': 1, 'heat': 1, 'event_name': '50 Free', 'time': '10:00',
           'lanes': [{'lane': 4, 'name': _XSS_NAME, 'club': _XSS_NAME,
                      'seed_time': '0:27.10', 'swimmers': []}]}]


def _render(own_dir, template, **extra):
    env = Environment(loader=FileSystemLoader(
        [os.path.join(REPO, own_dir), os.path.join(REPO, 'shared', 'templates')]))
    env.globals['url_for'] = lambda name, **kw: '/static/' + kw.get('filename', '')
    import state
    return env.get_template(template).render(
        num_lanes=6, labels={'event': 'EVENT', 'heat': 'HEAT'}, event_vocab={},
        has_meet=True, meet_name='Coupe',
        theme_colors=state.DEFAULT_THEME_COLORS,
        theme_fonts=state.DEFAULT_THEME_FONTS,
        t={}, heats=_HEATS, **extra)


# What each page needs beyond the shared context above.
_MANUAL_EXTRA = {'current_event': '1', 'current_heat': '1', 'manual_active': True,
                 'console_label': ''}


@pytest.mark.parametrize('own_dir, template, extra', [
    ('server/templates', 'schedule.html', {}),   # the Pi's public /schedule
    ('cloud/templates',  'schedule.html', {}),   # and the cloud's, on the internet
    ('server/templates', 'manual.html',   _MANUAL_EXTRA),
])
def test_a_swimmer_name_cannot_break_out_of_the_script_tag(own_dir, template, extra):
    """`| tojson`, never `json.dumps` + `| safe`, for anything inside a <script>.

    The escaping the page does at render time (`esc()`) is irrelevant here: this
    payload never reaches it, because it ends the script block before the page's
    own code runs.
    """
    html = _render(own_dir, template, **extra)
    assert '</script><img' not in html, 'start list closed its own <script> block'
    # The name is still there, just encoded — the page must not silently drop data.
    assert '\\u003c/script\\u003e' in html


def test_the_templates_do_not_reintroduce_safe_on_embedded_json():
    """A `| safe` inside a <script> is the exact shape of the bug — catch a repeat."""
    for path in ('shared/templates/schedule.html', 'server/templates/manual.html'):
        body = open(os.path.join(REPO, path), encoding='utf-8').read()
        assert 'heats | tojson' in body, f'{path} stopped using tojson'
        assert '| safe' not in body, f'{path} reintroduced | safe'


# ── The session key is this install's, not the repo's ─────────────────────────

# The key that used to be committed in server/app.py. Any cookie signed with it
# authenticated against every install; it must never be accepted again.
_PUBLISHED_KEY = 'rimnqiuqnewiornhf7nfwenjmqvliwynhtmlfnlsklrmqwe'


def test_no_session_key_is_committed_in_the_source():
    body = open(os.path.join(REPO, 'server', 'app.py'), encoding='utf-8').read()
    assert _PUBLISHED_KEY not in body
    assert 'SECRET_KEY = state.session_secret()' in body


def test_the_session_key_is_per_install_and_unreadable_by_others(tmp_path, monkeypatch):
    import state
    key_file = tmp_path / '.session_key'
    monkeypatch.setattr(state, 'SESSION_KEY_FILE', str(key_file))

    first = state.session_secret()
    assert len(first) >= 32
    assert first != _PUBLISHED_KEY
    # Stable across restarts, or every restart would sign everyone out.
    assert state.session_secret() == first
    assert oct(key_file.stat().st_mode & 0o777) == '0o600'

    # A second install gets a different key, which is the whole point.
    monkeypatch.setattr(state, 'SESSION_KEY_FILE', str(tmp_path / 'other'))
    assert state.session_secret() != first


# ── WebSockets: origin checked, terminal gated ────────────────────────────────

class _FakeWS:
    """Enough of a WebSocket for the guard: headers, session, and a close record."""

    def __init__(self, origin=None, host='splouch.local', user=None):
        self.headers = {'host': host}
        if origin is not None:
            self.headers['origin'] = origin
        self.session = {'user': user} if user else {}
        self.closed = None

    async def close(self, code=1000):
        self.closed = code


async def _guard(ws, **kw):
    import web
    return await web.ws_guard(ws, **kw)


@pytest.mark.parametrize('origin, allowed', [
    (None,                            True),   # Qt display / native app: no Origin
    ('http://splouch.local',          True),   # the page on this server
    ('https://evil.example',          False),  # any other site
    ('http://splouch.local.evil.com', False),  # suffix that only looks like ours
])
def test_a_websocket_from_another_site_is_refused(origin, allowed):
    import asyncio
    ws = _FakeWS(origin=origin)
    assert asyncio.run(_guard(ws)) is allowed
    assert ws.closed == (None if allowed else 1008)


def test_the_terminal_socket_needs_a_session_not_just_a_local_address():
    """`/terminal_start` was gated and the socket was not, so anyone on the LAN
    could read the operator's shell output and type into it."""
    import asyncio
    anon = _FakeWS(origin='http://splouch.local')
    assert asyncio.run(_guard(anon, login_required=True)) is False
    assert anon.closed == 1008

    signed_in = _FakeWS(origin='http://splouch.local', user='score')
    assert asyncio.run(_guard(signed_in, login_required=True)) is True


def test_the_terminal_route_actually_calls_the_guard():
    body = open(os.path.join(REPO, 'server', 'routes', 'debug.py'), encoding='utf-8').read()
    ws_terminal = body.split("@router.websocket('/ws/terminal')")[1]
    assert 'ws_guard(ws, login_required=True)' in ws_terminal.split('async def')[1]


# ── Cross-site requests to authenticated endpoints ────────────────────────────

class _FakeRequest:
    def __init__(self, site=None, user='score'):
        self.headers = {} if site is None else {'sec-fetch-site': site}
        self.session = {'user': user} if user else {}


@pytest.mark.parametrize('site', ['same-origin', 'same-site', 'none', None])
def test_the_operators_own_navigation_still_works(site):
    import web
    web.require_login(_FakeRequest(site))            # must not raise


def test_a_link_from_another_site_cannot_trigger_a_destructive_get():
    """`/meet_clear` and friends are GETs, and a Lax cookie rides a top-level
    navigation — so the referring site is the only thing left to check."""
    import web
    with pytest.raises(web.CrossSiteRequest):
        web.require_login(_FakeRequest('cross-site'))


# ── Backup restore stays inside the home directory ────────────────────────────

def test_a_backup_cannot_write_outside_the_home_directory(tmp_path, monkeypatch):
    """The old check read `m.name` only, so a symlink member plus a file "inside"
    it wrote anywhere the service user could — including the checkout it runs."""
    from routes import system as system_routes

    home = tmp_path / 'home'; home.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    monkeypatch.setattr(os.path, 'expanduser', lambda p: str(home) if p == '~' else p)

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        link = tarfile.TarInfo('Splouch/escape')
        link.type, link.linkname = tarfile.SYMTYPE, '../../outside'
        tar.addfile(link)
        payload = b'pwned'
        member = tarfile.TarInfo('Splouch/escape/owned.txt')
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))

    result = system_routes._restore_backup(buf.getvalue())

    assert list(outside.iterdir()) == [], 'backup restore escaped the home directory'
    assert getattr(result, 'status_code', 200) == 500  # refused, reported as an error


# ── Update target is a release tag or an allowlisted branch ───────────────────

def test_an_arbitrary_ref_is_not_checked_out(monkeypatch):
    """`git checkout <target>` took any string, so any commit in the repo — code
    that no release ever went through — could be put on the Pi."""
    import state
    from routes import system as system_routes

    ran = []
    monkeypatch.setattr(system_routes, '_run_cmd_blocking',
                        lambda cmd, cwd=None: (ran.append(cmd), ('', 0))[1])
    monkeypatch.setattr(system_routes, '_update_config', lambda: ([], 0))
    monkeypatch.setattr(state, '_update_log_lines', [])

    system_routes._run_update('some-attacker-branch')

    assert not any('checkout' in c and 'some-attacker-branch' in c
                   for c in (' '.join(x) for x in ran))
    assert state._update_log_done is False


def test_a_real_release_tag_is_still_accepted(monkeypatch):
    import state
    from routes import system as system_routes

    ran = []
    monkeypatch.setattr(system_routes, '_run_cmd_blocking',
                        lambda cmd, cwd=None: (ran.append(cmd), ('', 0))[1])
    monkeypatch.setattr(system_routes, '_update_config', lambda: ([], 0))
    monkeypatch.setattr(system_routes, 'time', type('T', (), {'sleep': staticmethod(lambda n: None)}))
    monkeypatch.setattr(system_routes.subprocess, 'run', lambda *a, **k: None)
    monkeypatch.setattr(state, '_update_log_lines', [])

    system_routes._run_update('v2026.09.1')

    assert ['git', 'checkout', 'v2026.09.1'] in ran


# ── The Lenex parser refuses an entity bomb ───────────────────────────────────

def test_a_lenex_file_with_a_doctype_is_refused():
    """ElementTree expands internal entities, so nested definitions expand to
    gigabytes mid-parse — an upload that takes down the server running the meet."""
    import zipfile
    from meet_parsers.lenex_parser import _open_lenex_xml

    bomb = ('<?xml version="1.0"?>\n<!DOCTYPE LENEX [\n'
            ' <!ENTITY a "aaaaaaaaaa">\n'
            ' <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
            ' <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n]>\n'
            '<LENEX><MEET name="&c;"/></LENEX>')
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'bomb.lxf')
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('meet.lef', bomb)
        with pytest.raises(ValueError, match='DOCTYPE'):
            _open_lenex_xml(path)


def test_an_ordinary_lenex_file_still_parses():
    """The guard above must not cost a real export — including one with a comment
    or processing instruction ahead of the root element."""
    import zipfile
    from meet_parsers.lenex_parser import _open_lenex_xml

    good = ('<?xml version="1.0" encoding="UTF-8"?>\n<!-- Splash export -->\n'
            '<LENEX version="3.0"><MEET name="Coupe"/></LENEX>')
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'good.lxf')
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('meet.lef', good)
        tree = _open_lenex_xml(path)
    assert tree.getroot().find('MEET').get('name') == 'Coupe'


# ── The cloud admin panel throttles guesses ───────────────────────────────────

def test_the_admin_panel_locks_out_a_password_guesser(monkeypatch, tmp_path):
    """One password on the open internet, and fail2ban here only watches sshd.
    The lock also caps the PBKDF2 work an unauthenticated flood can demand."""
    import base64
    sys.path.insert(0, os.path.join(REPO, 'cloud'))
    monkeypatch.setenv('DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ADMIN_USER', 'admin')
    monkeypatch.setenv('ADMIN_PASSWORD', 'correct-horse')
    import cloud_server as cs
    from fastapi import HTTPException

    cs.CREDS_FILE = str(tmp_path / 'credentials.json')
    cs._admin_fails.clear()

    class Req:
        def __init__(self, pw, ip='203.0.113.9'):
            tok = base64.b64encode(f'admin:{pw}'.encode()).decode()
            self.headers = {'Authorization': 'Basic ' + tok}
            self.client = type('C', (), {'host': ip})()

    def status(pw, ip='203.0.113.9'):
        try:
            cs.require_admin(Req(pw, ip))
            return 200
        except HTTPException as e:
            return e.status_code

    assert status('correct-horse') == 200            # the real password works
    for _ in range(cs._ADMIN_FAIL_MAX):
        assert status('wrong') == 401
    assert status('wrong') == 429                    # locked out
    assert status('correct-horse') == 429            # and the lock holds
    assert status('correct-horse', ip='198.51.100.4') == 200   # per-address


# ── The shipped login announces itself until it is changed ────────────────────

def test_the_panel_warns_while_the_shipped_login_is_still_in_use(monkeypatch):
    """`score`/`swimming` are printed in the README and docs/admin.md, so until
    they are changed the password protects nothing. Now that the session key is
    per-install, that password is what is actually holding the door."""
    import state

    monkeypatch.setitem(state.settings, 'username', 'score')
    monkeypatch.setitem(state.settings, 'password', 'swimming')
    assert state.using_default_credentials() is True

    # Changing either half is enough to clear it.
    monkeypatch.setitem(state.settings, 'password', 'a-real-password')
    assert state.using_default_credentials() is False
    monkeypatch.setitem(state.settings, 'password', 'swimming')
    monkeypatch.setitem(state.settings, 'username', 'timing')
    assert state.using_default_credentials() is False


def test_the_warning_is_read_from_the_defaults_file_not_restated(monkeypatch):
    """Hard-coding 'score'/'swimming' in the check would leave it silently wrong
    the day the shipped defaults change."""
    import json as _json
    import state

    monkeypatch.setattr(state, '_SHIPPED_CREDS', None)
    shipped = _json.load(open(os.path.join(REPO, 'server', 'settings.default.json')))
    assert state._shipped_credentials() == (shipped['username'], shipped['password'])


def test_the_banner_is_rendered_only_while_the_login_is_the_default(monkeypatch):
    import state
    import web

    monkeypatch.setitem(state.settings, 'username', 'score')
    monkeypatch.setitem(state.settings, 'password', 'swimming')
    assert web._globals()['default_credentials'] is True

    monkeypatch.setitem(state.settings, 'password', 'a-real-password')
    assert web._globals()['default_credentials'] is False

    body = open(os.path.join(REPO, 'server', 'templates', 'settings.html'),
                encoding='utf-8').read()
    assert '{% if default_credentials %}' in body
    assert 't.default_password_banner' in body


@pytest.mark.parametrize('code', ['en', 'fr', 'es'])
def test_every_panel_language_has_the_warning(code):
    """A half-translated banner would fall back to English mid-sentence."""
    import state
    t = state.settings_strings(code)
    assert t['default_password_banner'].strip()
    assert t['default_password_banner_action'].strip()
    if code != 'en':
        en = state.settings_strings('en')
        assert t['default_password_banner'] != en['default_password_banner'], \
            f'{code} banner is still the English text'


# ── The installer removes the sudo rule it says it removes ────────────────────

def test_the_temporary_sudo_rule_is_removed_under_the_name_it_was_written():
    """It was written to /etc/sudoers.d/splouch and the cleanup deleted
    /etc/sudoers.d/tremplin — the pre-rename name — so every cloud VM kept
    `NOPASSWD:ALL` while the installer printed that it had been removed."""
    body = open(os.path.join(REPO, 'install', 'install.sh'), encoding='utf-8').read()

    assert 'TEMP_SUDOERS_FILE=' in body
    write = [ln for ln in body.splitlines() if 'NOPASSWD:ALL' in ln and 'echo' in ln]
    assert write and all('"$TEMP_SUDOERS_FILE"' in ln for ln in write), \
        'the blanket rule is written somewhere the cleanup does not look'
    assert 'rm -f "$1" /etc/sudoers.d/tremplin' in body, \
        'the cleanup no longer removes the temporary file'
    # It verifies rather than announcing: the success message is on the true branch
    # of a check that no blanket grant is left anywhere in /etc/sudoers.d.
    assert '! grep -rqs "NOPASSWD:ALL" /etc/sudoers.d/' in body
    assert 'Could not remove the temporary NOPASSWD sudo rule' in body
    # One sudo for the whole cleanup — the grant being removed is what makes it
    # passwordless, so a second call would stall an unattended install.
    cleanup = body.split('# Remove the temporary NOPASSWD rule')[1]
    assert cleanup.count('sudo ') == 1 or cleanup.count('\n    sudo ') <= 1
