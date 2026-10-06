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
import os
import re
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import cast

import pytest
from fastapi import Request
from jinja2 import Environment, FileSystemLoader

from conftest import stub_url_for
from jsc import HAS_JS_ENGINE, run_page

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── The start list cannot close the script it is embedded in ──────────────────

# What a Splash export can legitimately contain: the club name is free text typed
# by whoever entered the swimmer, and it reaches the page unchanged.
_XSS_NAME = "</script><img src=x onerror=alert(1)>"
_HEATS = [
    {
        "event": 1,
        "heat": 1,
        "event_name": "50 Free",
        "time": "10:00",
        "lanes": [
            {
                "lane": 4,
                "name": _XSS_NAME,
                "club": _XSS_NAME,
                "seed_time": "0:27.10",
                "swimmers": [],
            }
        ],
    }
]


def _render(own_dir, template, **extra):
    env = Environment(
        loader=FileSystemLoader(
            [os.path.join(REPO, own_dir), os.path.join(REPO, "shared", "templates")]
        )
    )
    stub_url_for(env)
    import state

    return env.get_template(template).render(
        num_lanes=6,
        labels={"event": "EVENT", "heat": "HEAT"},
        event_vocab={},
        has_meet=True,
        meet_name="Coupe",
        theme_colors=state.DEFAULT_THEME_COLORS,
        theme_fonts=state.DEFAULT_THEME_FONTS,
        t={},
        heats=_HEATS,
        **extra,
    )


# What each page needs beyond the shared context above.
_MANUAL_EXTRA = {
    "current_event": "1",
    "current_heat": "1",
    "manual_active": True,
    "console_label": "",
}


@pytest.mark.parametrize(
    "own_dir, template, extra",
    [
        ("server/templates", "schedule.html", {}),  # the Pi's public /schedule
        ("cloud/templates", "schedule.html", {}),  # and the cloud's, on the internet
        ("server/templates", "manual.html", _MANUAL_EXTRA),
    ],
)
def test_a_swimmer_name_cannot_break_out_of_the_script_tag(own_dir, template, extra):
    """`| tojson`, never `json.dumps` + `| safe`, for anything inside a <script>.

    The escaping the page does at render time (`esc()`) is irrelevant here: this
    payload never reaches it, because it ends the script block before the page's
    own code runs.
    """
    html = _render(own_dir, template, **extra)
    assert "</script><img" not in html, "start list closed its own <script> block"
    # The name is still there, just encoded — the page must not silently drop data.
    assert "\\u003c/script\\u003e" in html


def test_the_templates_do_not_reintroduce_safe_on_embedded_json():
    """A `| safe` inside a <script> is the exact shape of the bug — catch a repeat."""
    for path in ("shared/templates/schedule.html", "server/templates/manual.html"):
        body = Path(os.path.join(REPO, path)).read_text(encoding="utf-8")
        assert "heats | tojson" in body, f"{path} stopped using tojson"
        assert "| safe" not in body, f"{path} reintroduced | safe"


# ── The board writes names as text ───────────────────────────────────────────


@pytest.mark.parametrize(
    "path", ["shared/templates/scoreboard_base.html", "server/templates/live.html"]
)
def test_the_board_writes_meet_file_text_as_text_not_markup(path):
    """`lane_name`, `lane_club` and `event_name` are the Splash export's free text,
    and these pages are public on the Pi and, via `live-mobile.html`, on the cloud.
    Written as `innerHTML`, a name like `<img onerror=…>` ran on the viewer's origin
    — on the Pi, with the operator's session, which `/ws/terminal` turns into a
    shell."""
    body = Path(os.path.join(REPO, path)).read_text(encoding="utf-8")
    assert "getElementById(k).innerHTML" not in body, f"{path} writes s[k] as markup"
    assert "getElementById(k).textContent = s[k]" in body


# ── A relayed delta cannot carry markup ───────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "shared/templates/scoreboard_base.html",
        "shared/templates/results.html",
        "server/templates/live.html",
    ],
)
def test_the_delta_is_never_inserted_as_received(path):
    """The delta is the one board field that arrives as markup, and the cloud relays
    it exactly as a Pi sent it. Any holder of a relay key could put script on every
    spectator's phone — on the origin that also serves `/admin`."""
    body = Path(os.path.join(REPO, path)).read_text(encoding="utf-8")
    assert "innerHTML = delta;" not in body
    assert "= r.delta" not in body
    assert "safeDeltaHtml(" in body


@pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JavaScript engine (osascript or node)"
)
@pytest.mark.parametrize("page", ["phone", "kiosk"])
def test_a_relayed_delta_with_script_renders_as_text(page):
    from test_lap_counts import _render

    if page == "phone":
        html = _render("server/templates", "live-mobile.html")
    else:
        html = _render(
            "server/templates",
            "live.html",
            nosplash=True,
            test_background=False,
            carousel_images=[],
            carousel_interval=10,
        )
    script = r"""
    function assert(ok, msg) { if (!ok) throw new Error(msg); }
    var ok = safeDeltaHtml('<span class="delta-better">-1.20</span>');
    assert(ok === '<span class="delta-better">-1.20</span>', 'the real delta changed: ' + ok);
    var bad = safeDeltaHtml('<img src=x onerror=alert(1)>+1.20');
    assert(bad.indexOf('<') < 0, 'markup got through: ' + bad);
    var odd = safeDeltaHtml('<span class="x" onmouseover="alert(1)">1</span>');
    assert(odd.indexOf('<') < 0, 'a foreign span got through: ' + odd);
    """
    run_page(html.replace("</body>", f"<script>{script}</script></body>"))


# ── The session key is this install's, not the repo's ─────────────────────────

# The key that used to be committed in server/app.py. Any cookie signed with it
# authenticated against every install; it must never be accepted again.
_PUBLISHED_KEY = "rimnqiuqnewiornhf7nfwenjmqvliwynhtmlfnlsklrmqwe"


def test_no_session_key_is_committed_in_the_source():
    body = Path(os.path.join(REPO, "server", "app.py")).read_text(encoding="utf-8")
    assert _PUBLISHED_KEY not in body
    assert "SECRET_KEY = state.session_secret()" in body


def test_the_session_key_is_per_install_and_unreadable_by_others(tmp_path, monkeypatch):
    import paths

    key_file = tmp_path / ".session_key"
    # `paths`, not `state`: `session_secret()` lives there now and reads its own
    # module global, so a name rebound on `state` alone would be read by nobody.
    monkeypatch.setattr(paths, "SESSION_KEY_FILE", str(key_file))

    first = paths.session_secret()
    assert len(first) >= 32
    assert first != _PUBLISHED_KEY
    # Stable across restarts, or every restart would sign everyone out.
    assert paths.session_secret() == first
    assert oct(key_file.stat().st_mode & 0o777) == "0o600"

    # A second install gets a different key, which is the whole point.
    monkeypatch.setattr(paths, "SESSION_KEY_FILE", str(tmp_path / "other"))
    assert paths.session_secret() != first


# ── WebSockets: origin checked, terminal gated ────────────────────────────────


def _session(user):
    """A session as `/login` leaves it: the user, and the login it was issued under."""
    import web

    return {"user": user, "cred": web.credentials_stamp()} if user else {}


class _FakeWS:
    """Enough of a WebSocket for the guard: headers, session, and a close record."""

    def __init__(self, origin=None, host="splouch.local", user=None):
        self.headers = {"host": host}
        if origin is not None:
            self.headers["origin"] = origin
        self.session = _session(user)
        self.closed = None

    async def close(self, code=1000):
        self.closed = code


async def _guard(ws, **kw):
    import web

    return await web.ws_guard(ws, **kw)


@pytest.mark.parametrize(
    "origin, allowed",
    [
        (None, True),  # Qt display / native app: no Origin
        ("http://splouch.local", True),  # the page on this server
        ("https://evil.example", False),  # any other site
        ("http://splouch.local.evil.com", False),  # suffix that only looks like ours
    ],
)
def test_a_websocket_from_another_site_is_refused(origin, allowed):
    import asyncio

    ws = _FakeWS(origin=origin)
    assert asyncio.run(_guard(ws)) is allowed
    assert ws.closed == (None if allowed else 1008)


# ── DNS rebinding: only LAN names reach the Pi ────────────────────────────────


@pytest.mark.parametrize(
    "host, allowed",
    [
        ("splouch.local", True),
        ("tableau.local:80", True),  # a translated mDNS alias
        ("splouch", True),  # the bare hostname
        ("10.10.10.10:5000", True),
        ("[fe80::1]:5000", True),
        ("splouch.lan", True),
        ("localhost", True),
        ("testserver", True),
        ("evil.example", False),  # a public name, rebound to this Pi's address
        ("evil.example:5000", False),
        ("splouch.local.evil.com", False),
        ("", False),
    ],
)
def test_only_a_name_the_lan_resolves_is_accepted(host, allowed):
    import web

    assert web.local_host(host) is allowed


def test_the_operator_can_allow_a_dns_name_of_their_own():
    import web

    assert web.local_host("scores.club.example", ["scores.club.example"])
    assert not web.local_host("other.example", ["scores.club.example"])


def _through_host_guard(scope_type, host):
    """Drive the Pi's host middleware around an app that records what reached it."""
    import asyncio

    import app as pi_app

    reached, sent = [], []

    async def inner(scope, receive, send):
        reached.append(scope["type"])

    async def receive():
        return {"type": "http.request", "body": b""}

    async def send(message):
        sent.append(message)

    scope = {
        "type": scope_type,
        "path": "/ws/scoreboard" if scope_type == "websocket" else "/schedule.json",
        "headers": [(b"host", host.encode())],
    }
    if scope_type == "http":
        scope.update(method="GET", query_string=b"")
    asyncio.run(pi_app._LanHostsOnly(inner)(scope, receive, send))
    return reached, sent


@pytest.mark.parametrize("scope_type", ["http", "websocket"])
def test_a_rebound_page_reaches_neither_the_routes_nor_the_sockets(scope_type):
    """Origin is checked against Host, and under DNS rebinding the attacker picks
    both: any page a phone at the meet opened could drive `/ws/scoreboard` and
    read the start lists out to the internet."""
    reached, sent = _through_host_guard(scope_type, "evil.example")
    assert reached == []
    if scope_type == "websocket":
        assert sent == [{"type": "websocket.close", "code": 1008}]
    else:
        assert sent[0]["status"] == 421

    reached, _ = _through_host_guard(scope_type, "splouch.local")
    assert reached == [scope_type]


def test_the_terminal_socket_needs_a_session_not_just_a_local_address():
    """`/terminal_start` was gated and the socket was not, so anyone on the LAN
    could read the operator's shell output and type into it."""
    import asyncio

    anon = _FakeWS(origin="http://splouch.local")
    assert asyncio.run(_guard(anon, login_required=True)) is False
    assert anon.closed == 1008

    signed_in = _FakeWS(origin="http://splouch.local", user="score")
    assert asyncio.run(_guard(signed_in, login_required=True)) is True


def test_the_terminal_route_actually_calls_the_guard():
    body = Path(os.path.join(REPO, "server", "routes", "debug.py")).read_text(
        encoding="utf-8"
    )
    ws_terminal = body.split('@router.websocket("/ws/terminal")')[1]
    assert "ws_guard(ws, login_required=True)" in ws_terminal.split("async def")[1]


# ── Cross-site requests to authenticated endpoints ────────────────────────────


class _FakeRequest:
    def __init__(self, site=None, user="score"):
        self.headers = {} if site is None else {"sec-fetch-site": site}
        self.session = _session(user)


@pytest.mark.parametrize("site", ["same-origin", "same-site", "none", None])
def test_the_operators_own_navigation_still_works(site):
    import web

    web.require_login(cast(Request, _FakeRequest(site)))  # must not raise


def test_a_link_from_another_site_cannot_trigger_a_destructive_get():
    """`/meet_clear` and friends are GETs, and a Lax cookie rides a top-level
    navigation — so the referring site is the only thing left to check."""
    import web

    with pytest.raises(web.CrossSiteRequest):
        web.require_login(cast(Request, _FakeRequest("cross-site")))


# ── Backup restore stays inside the data folder ───────────────────────────────


def _backup_dirs(tmp_path, monkeypatch):
    """A home with a data folder and a checkout beside it, the restart stubbed."""
    import bus
    import state

    home = tmp_path / "home"
    data, checkout = home / "SplouchData", home / "Splouch"
    data.mkdir(parents=True)
    checkout.mkdir()
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(home) if p == "~" else p)
    monkeypatch.setattr(state, "SCOREBOARD_DIR", str(data))
    monkeypatch.setattr(bus, "run_bg", lambda *a, **k: None)  # no service restart
    return home, data, checkout


def _tar(*members):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


def test_a_backup_cannot_write_outside_the_data_folder(tmp_path, monkeypatch):
    """The old check read `m.name` only, so a symlink member plus a file "inside"
    it wrote anywhere the service user could — including the checkout it runs."""
    from routes import system as system_routes

    _, _, _ = _backup_dirs(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        link = tarfile.TarInfo("SplouchData/escape")
        link.type, link.linkname = tarfile.SYMTYPE, "../../../outside"
        tar.addfile(link)
        payload = b"pwned"
        member = tarfile.TarInfo("SplouchData/escape/owned.txt")
        member.size = len(payload)
        tar.addfile(member, io.BytesIO(payload))

    result = system_routes._restore_backup(buf.getvalue())

    assert list(outside.iterdir()) == [], "backup restore escaped the data folder"
    assert getattr(result, "status_code", 200) == 400  # refused as a bad archive


def test_a_backup_cannot_replace_the_servers_code(tmp_path, monkeypatch):
    """Restore extracted into `~` as whatever paths the archive named, and the
    download's own root was `Splouch/` — the checkout. A "backup" handed to an
    operator could therefore swap `server/app.py` for its own."""
    from routes import system as system_routes

    home, data, checkout = _backup_dirs(tmp_path, monkeypatch)
    archive = _tar(
        ("Splouch/server/app.py", b"pwned"),
        (".bashrc", b"pwned"),
        ("SplouchData/settings.json", b"{}"),
    )

    system_routes._restore_backup(archive)

    assert not (checkout / "server").exists(), "restore wrote into the checkout"
    assert not (home / ".bashrc").exists(), "restore wrote outside the data folder"
    assert (data / "settings.json").read_bytes() == b"{}"
    # The legacy root was the data folder under its old name: it lands there too.
    assert (data / "server" / "app.py").read_bytes() == b"pwned"


def test_a_downloaded_backup_restores_into_the_data_folder(tmp_path, monkeypatch):
    """The download and the restore must agree on where the files go: the archive
    was rooted at `Splouch/`, so restoring one put the data in the checkout and
    left `~/SplouchData` exactly as it was."""
    from routes import system as system_routes

    _, data, checkout = _backup_dirs(tmp_path, monkeypatch)
    (data / "meet").mkdir()
    (data / "meet" / "gala.lxf").write_bytes(b"meet")
    (data / ".session_key").write_text("this-pi-only")

    archive = system_routes.route_backup_download().body
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        names = tar.getnames()
    assert "SplouchData/meet/gala.lxf" in names
    assert not any(n.endswith(".session_key") for n in names), "session key shipped"

    (data / "meet" / "gala.lxf").unlink()
    result = system_routes._restore_backup(archive)

    assert result == {"ok": True}
    assert (data / "meet" / "gala.lxf").read_bytes() == b"meet"
    assert list(checkout.iterdir()) == []


# ── Update target is a release tag or an allowlisted branch ───────────────────


def test_an_arbitrary_ref_is_not_checked_out(monkeypatch):
    """`git checkout <target>` took any string, so any commit in the repo — code
    that no release ever went through — could be put on the Pi."""
    import state
    from routes import update as update_routes

    ran = []
    monkeypatch.setattr(
        update_routes,
        "run_cmd_blocking",
        lambda cmd, cwd=None: (ran.append(cmd), ("", 0))[1],
    )
    monkeypatch.setattr(update_routes, "_update_config", lambda: ([], 0))
    monkeypatch.setattr(state, "_update_log_lines", [])

    update_routes._run_update("some-attacker-branch")

    assert not any(
        "checkout" in c and "some-attacker-branch" in c
        for c in (" ".join(x) for x in ran)
    )
    assert state._update_log_done is False


def test_a_real_release_tag_is_still_accepted(monkeypatch):
    import state
    from routes import update as update_routes

    ran = []
    monkeypatch.setattr(
        update_routes,
        "run_cmd_blocking",
        lambda cmd, cwd=None: (ran.append(cmd), ("", 0))[1],
    )
    monkeypatch.setattr(update_routes, "_update_config", lambda: ([], 0))
    monkeypatch.setattr(
        update_routes, "time", type("T", (), {"sleep": staticmethod(lambda n: None)})
    )
    monkeypatch.setattr(update_routes.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(state, "_update_log_lines", [])

    update_routes._run_update("v2026.09.1")

    assert ["git", "checkout", "v2026.09.1"] in ran


# ── The Lenex parser refuses an entity bomb ───────────────────────────────────


def test_a_lenex_file_with_a_doctype_is_refused():
    """ElementTree expands internal entities, so nested definitions expand to
    gigabytes mid-parse — an upload that takes down the server running the meet."""
    import zipfile

    from meet_parsers.lenex_parser import _open_lenex_xml

    bomb = (
        '<?xml version="1.0"?>\n<!DOCTYPE LENEX [\n'
        ' <!ENTITY a "aaaaaaaaaa">\n'
        ' <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">\n'
        ' <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">\n]>\n'
        '<LENEX><MEET name="&c;"/></LENEX>'
    )
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "bomb.lxf")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("meet.lef", bomb)
        with pytest.raises(ValueError, match="DOCTYPE"):
            _open_lenex_xml(path)


@pytest.mark.parametrize("encoding", ["utf-16", "utf-16-be", "utf-16-le"])
def test_a_doctype_in_another_encoding_is_refused_too(tmp_path, encoding):
    """The check scanned the bytes for `<!DOCTYPE`, which a UTF-16 file does not
    contain — and the parser behind it, which reads UTF-16, expanded the entities."""
    from meet_parsers.lenex_parser import _open_lenex_xml

    bom = "﻿" if encoding != "utf-16" else ""
    doc = (
        bom + '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE LENEX [<!ENTITY a "aaaaaaaaaa">]><LENEX><MEET name="&a;"/></LENEX>'
    )
    path = tmp_path / "bomb.lef"
    path.write_bytes(doc.encode(encoding))
    with pytest.raises(ValueError, match="DOCTYPE"):
        _open_lenex_xml(str(path))


def test_an_ordinary_lenex_file_still_parses():
    """The guard above must not cost a real export — including one with a comment
    or processing instruction ahead of the root element."""
    import zipfile

    from meet_parsers.lenex_parser import _open_lenex_xml

    good = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<!-- Splash export -->\n'
        '<LENEX version="3.0"><MEET name="Coupe"/></LENEX>'
    )
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "good.lxf")
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("meet.lef", good)
        tree = _open_lenex_xml(path)
    assert tree.getroot().find("MEET").get("name") == "Coupe"


# ── The cloud admin panel throttles guesses ───────────────────────────────────


def test_the_admin_panel_locks_out_a_password_guesser(monkeypatch, tmp_path, pg):
    """One password on the open internet, and fail2ban here only watches sshd.
    The lock also caps the PBKDF2 work an unauthenticated flood can demand."""
    import base64

    sys.path.insert(0, os.path.join(REPO, "cloud"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    from fastapi import HTTPException

    import cloud_auth
    import cloud_control as cs

    # The `pg` fixture empties the store, so the login is seeded from the
    # environment above on first read, as on a new install.
    monkeypatch.setattr(cloud_auth, "_admin_fails", {})
    cs._admin_fails = cloud_auth._admin_fails

    class Req:
        method = "GET"

        def __init__(self, pw, ip="203.0.113.9"):
            tok = base64.b64encode(f"admin:{pw}".encode()).decode()
            self.headers = {"Authorization": "Basic " + tok}
            self.client = type("C", (), {"host": ip})()

    def status(pw, ip="203.0.113.9"):
        try:
            cs.require_admin(cast(Request, Req(pw, ip)))
            return 200
        except HTTPException as e:
            return e.status_code

    assert status("correct-horse") == 200  # the real password works
    for _ in range(cs._ADMIN_FAIL_MAX):
        assert status("wrong") == 401
    assert status("wrong") == 429  # locked out
    assert status("correct-horse") == 429  # and the lock holds
    assert status("correct-horse", ip="198.51.100.4") == 200  # per-address


def test_a_cross_site_post_to_the_admin_panel_is_refused(monkeypatch, tmp_path, pg):
    """Basic credentials ride a cross-site form POST, so any page the admin visited
    could switch analytics on in their name or delete a retained meet. A plain link
    to /admin from elsewhere still has to open."""
    import base64

    sys.path.insert(0, os.path.join(REPO, "cloud"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    from fastapi import HTTPException

    import cloud_auth

    monkeypatch.setattr(cloud_auth, "_admin_fails", {})

    class Req:
        def __init__(self, method, site):
            tok = base64.b64encode(b"admin:correct-horse").decode()
            self.method = method
            self.headers = {"Authorization": "Basic " + tok}
            if site is not None:
                self.headers["sec-fetch-site"] = site
            self.client = type("C", (), {"host": "203.0.113.9"})()

    def status(method, site):
        try:
            cloud_auth.require_admin(cast(Request, Req(method, site)))
            return 200
        except HTTPException as e:
            return e.status_code

    assert status("POST", "cross-site") == 403
    assert status("POST", "same-origin") == 200
    assert status("POST", None) == 200  # curl, scripts: no header at all
    assert status("GET", "cross-site") == 200  # a link to /admin still opens


# ── The cloud admin login is never seeded empty ───────────────────────────────


def test_an_empty_admin_password_does_not_open_the_panel(monkeypatch, tmp_path, pg):
    """docker compose expands `$…` in an unquoted .env value, so a password like
    `$tr0ng` reached the control plane as '' — and was seeded as the login, kept
    in the database from then on."""
    import base64

    sys.path.insert(0, os.path.join(REPO, "cloud"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "")
    import cloud_auth

    class Req:
        headers = {"Authorization": "Basic " + base64.b64encode(b"admin:").decode()}

    assert not cloud_auth.check_admin(Req())
    with cloud_auth.cloud_db.conn() as c:
        assert c.execute("SELECT 1 FROM admin").fetchone() is None, "seeded empty"

    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    Req.headers = {
        "Authorization": "Basic " + base64.b64encode(b"admin:correct-horse").decode()
    }
    assert cloud_auth.check_admin(Req()), "fixing .env must seed the login"


def test_the_installer_writes_the_typed_login_quoted():
    body = Path(os.path.join(REPO, "install", "install.sh")).read_text(encoding="utf-8")
    assert "_set_env ADMIN_PASSWORD \"'$_ap1'\"" in body
    assert "_set_env ADMIN_USER \"'$_au'\"" in body
    assert "cannot contain a single quote" in body


# ── The shipped login announces itself until it is changed ────────────────────


def test_the_panel_warns_while_the_shipped_login_is_still_in_use(monkeypatch):
    """`score`/`swimming` are printed in the README and docs/admin.md, so until
    they are changed the password protects nothing. Now that the session key is
    per-install, that password is what is actually holding the door."""
    import state

    monkeypatch.setitem(state.settings, "username", "score")
    monkeypatch.setitem(state.settings, "password", "swimming")
    assert state.using_default_credentials() is True

    # Changing either half is enough to clear it.
    monkeypatch.setitem(state.settings, "password", "a-real-password")
    assert state.using_default_credentials() is False
    monkeypatch.setitem(state.settings, "password", "swimming")
    monkeypatch.setitem(state.settings, "username", "timing")
    assert state.using_default_credentials() is False


def test_the_warning_is_read_from_the_defaults_file_not_restated(monkeypatch):
    """Hard-coding 'score'/'swimming' in the check would leave it silently wrong
    the day the shipped defaults change."""
    import json as _json

    import state

    monkeypatch.setattr(state, "_SHIPPED_CREDS", None)
    shipped = _json.loads(
        Path(REPO, "server", "settings.default.json").read_text(encoding="utf-8")
    )
    assert state._shipped_credentials() == (shipped["username"], shipped["password"])


def test_the_banner_is_rendered_only_while_the_login_is_the_default(monkeypatch):
    import state
    import web

    monkeypatch.setitem(state.settings, "username", "score")
    monkeypatch.setitem(state.settings, "password", "swimming")
    assert web._globals()["default_credentials"] is True

    monkeypatch.setitem(state.settings, "password", "a-real-password")
    assert web._globals()["default_credentials"] is False

    body = Path(os.path.join(REPO, "server", "templates", "settings.html")).read_text(
        encoding="utf-8"
    )
    assert "{% if default_credentials %}" in body
    assert "t.default_password_banner" in body


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_every_panel_language_has_the_warning(code):
    """A half-translated banner would fall back to English mid-sentence."""
    import state

    t = state.settings_strings(code)
    assert t["default_password_banner"].strip()
    assert t["default_password_banner_action"].strip()
    if code != "en":
        en = state.settings_strings("en")
        assert t["default_password_banner"] != en["default_password_banner"], (
            f"{code} banner is still the English text"
        )


# ── The installer removes the sudo rule it says it removes ────────────────────


def test_the_temporary_sudo_rule_is_removed_under_the_name_it_was_written():
    """It was written under one filename and the cleanup deleted another, so every
    cloud VM kept `NOPASSWD:ALL` while the installer printed that it had been
    removed. Hence the single constant, and the check on the result."""
    body = Path(os.path.join(REPO, "install", "install.sh")).read_text(encoding="utf-8")

    assert "TEMP_SUDOERS_FILE=" in body
    write = [ln for ln in body.splitlines() if "NOPASSWD:ALL" in ln and "echo" in ln]
    assert write and all('"$TEMP_SUDOERS_FILE"' in ln for ln in write), (
        "the blanket rule is written somewhere the cleanup does not look"
    )
    assert 'rm -f "$1"' in body, "the cleanup no longer removes the temporary file"
    # It verifies rather than announcing: the success message is on the true branch
    # of a check that no blanket grant is left anywhere in /etc/sudoers.d.
    assert '! grep -rqs "NOPASSWD:ALL" /etc/sudoers.d/' in body
    assert "Could not remove the temporary NOPASSWD sudo rule" in body
    # One sudo for the whole cleanup — the grant being removed is what makes it
    # passwordless, so a second call would stall an unattended install.
    cleanup = body.split("# Remove the temporary NOPASSWD rule")[1]
    assert cleanup.count("sudo ") == 1 or cleanup.count("\n    sudo ") <= 1


# ── The Pi's sudo rule grants commands, not root ──────────────────────────────


def _pi_sudo_rule():
    """Every command the Pi's sudo rule grants, `$PRIV_DIR` spelled out."""
    body = Path(os.path.join(REPO, "install", "install.sh")).read_text(encoding="utf-8")
    found = re.search(r'^\s*PRIV_DIR="([^"]+)"', body, re.MULTILINE)
    assert found, "install.sh no longer sets PRIV_DIR — update this test"
    priv = found.group(1)
    lines = [
        ln
        for ln in body.splitlines()
        if ln.startswith("$TARGET_USER ALL=(ALL) NOPASSWD:")
    ]
    assert lines, "the Pi's sudo rule is gone — update this test"
    return (
        body,
        priv,
        [
            cmd.strip().replace("$PRIV_DIR", priv)
            for ln in lines
            for cmd in ln.split("NOPASSWD:", 1)[1].split(",")
        ],
    )


def test_every_script_the_sudo_rule_grants_is_a_root_owned_copy():
    """The rule named `$INSTALL_DIR/install/scripts/…` — files in a checkout the
    service user owns. It could edit `refresh-service.sh` and then run it as root, so
    anything running as that user was root. Before that it named
    `web-reinstall.sh`, which the repo never shipped: root for whoever created it."""
    body, priv, cmds = _pi_sudo_rule()
    assert not any("$INSTALL_DIR" in c or "$TARGET_HOME" in c for c in cmds)
    scripts = {c.split()[0] for c in cmds if c.startswith(priv + "/")}
    assert scripts, "the rule no longer names any script — update this test"
    loop = re.search(r"for _script in ([^;]+); do", body)
    assert loop, "install.sh no longer copies the scripts — update this test"
    copied = loop.group(1).split()
    for script in scripts:
        name = os.path.basename(script)
        assert name in copied, f"{name} is granted but never installed root-owned"
        assert os.path.isfile(os.path.join(REPO, "install", "scripts", name))
    assert 'sudo install -d -o root -g root -m 0755 "$PRIV_DIR"' in body


def test_no_command_is_granted_with_arguments_of_the_callers_choosing():
    """`/usr/bin/apt-get` and `/usr/bin/nmcli` were granted bare, which in sudoers
    means with any arguments — and `apt-get -o …::Pre-Invoke=` runs a command."""
    _, _, cmds = _pi_sudo_rule()
    bare = [c for c in cmds if len(c.split()) == 1]
    assert not bare, f"granted with any arguments: {bare}"


def test_the_root_run_mdns_service_does_not_run_from_the_checkout():
    body = Path(os.path.join(REPO, "install", "install.sh")).read_text(encoding="utf-8")
    exec_line = next(ln for ln in body.splitlines() if "mdns-aliases.sh $MDNS" in ln)
    assert exec_line.startswith("ExecStart=$PRIV_DIR/"), exec_line


@pytest.mark.parametrize(
    "script, module",
    [("rtc_setup.sh", "system.py"), ("refresh-service.sh", "update.py")],
)
def test_the_app_runs_the_root_owned_copy_when_there_is_one(
    tmp_path, monkeypatch, script, module
):
    import paths

    (tmp_path / script).write_text("#!/bin/sh\n")
    monkeypatch.setattr(paths, "PRIVILEGED_SCRIPTS_DIR", str(tmp_path))
    assert paths.privileged_script(script) == str(tmp_path / script)
    source = Path(REPO, "server", "routes", module).read_text(encoding="utf-8")
    assert "privileged_script(" in source
    assert '"sudo", "bash"' not in source, "`sudo bash <script>` matches no rule"


# ── A public meet id cannot steer or flood the control plane ─────────────────


@pytest.fixture
def node(monkeypatch):
    sys.path.insert(0, os.path.join(REPO, "cloud"))
    import cloud_node

    calls = []
    monkeypatch.setattr(cloud_node, "_records", {})
    monkeypatch.setattr(
        cloud_node, "_call", lambda method, path, *a, **k: calls.append(path)
    )
    return cloud_node, calls


@pytest.mark.parametrize(
    "meet_id", ["a/../../meets", "x?y=1", "../admin", "a b", "", "a" * 65]
)
def test_a_meet_id_from_a_query_string_never_reaches_the_internal_api(node, meet_id):
    """`/mobile?meet=` went into `/internal/meets/{meet_id}` as typed, on a call
    that carries NODE_SECRET."""
    cloud_node, calls = node
    assert cloud_node.fetch_meet(meet_id) is None
    assert calls == []


def test_unknown_meet_ids_cannot_grow_the_card_cache_without_end(node, monkeypatch):
    """Entries were only dropped once older than the TTL, so fresh ids arriving
    faster than that grew the cache for as long as the flood lasted."""
    cloud_node, calls = node
    monkeypatch.setattr(cloud_node, "_RECORDS_MAX", 50)
    for i in range(500):
        cloud_node.fetch_meet(f"m{i}")
    assert len(calls) == 500
    assert len(cloud_node._records) <= 50


def test_lookups_in_flight_are_capped(node, monkeypatch):
    """Each lookup holds a pool thread for up to its timeout, and the pool serves
    every page on the worker: past the cap, an unknown id is simply not found."""
    cloud_node, calls = node
    monkeypatch.setattr(
        cloud_node, "_fetch_slots", __import__("threading").Semaphore(0)
    )
    assert cloud_node.fetch_meet("abc123") is None
    assert calls == []


# ── The login's `next` stays on this Pi ───────────────────────────────────────


@pytest.mark.parametrize(
    "target, kept",
    [
        ("/settings#tab-meet", True),
        ("/settings?x=1", True),
        ("//evil.example", False),
        ("/\\evil.example", False),  # browsers read `\` as `/`
        ("/\t/evil.example", False),  # and drop tabs before parsing
        ("https://evil.example", False),
    ],
)
def test_the_login_never_redirects_off_this_pi(target, kept):
    import app as pi_app

    assert (pi_app._safe_next(target) == target) is kept


# ── Changing the password ends every other session ───────────────────────────


def test_a_session_does_not_outlive_the_password_it_was_issued_under(monkeypatch):
    """The session is a signed cookie: a copy taken off a laptop stayed good for
    its whole lifetime, through logout and through the password change meant to
    lock it out."""
    import state
    import web

    monkeypatch.setitem(state.settings, "username", "admin")
    monkeypatch.setitem(state.settings, "password", "before")
    session = _session("admin")
    assert web.signed_in(session)

    monkeypatch.setitem(state.settings, "password", "after")
    assert not web.signed_in(session), "the old password's session still works"
    assert not web.signed_in({"user": "admin"}), "a session with no stamp is accepted"


def test_the_pi_login_locks_out_a_password_guesser(monkeypatch):
    """Anyone on the pool deck can reach the form, and it guards a shell."""
    import asyncio

    import app as pi_app
    import state

    monkeypatch.setattr(pi_app, "_login_fails", {})
    monkeypatch.setitem(state.settings, "username", "admin")
    monkeypatch.setitem(state.settings, "password", "correct-horse")

    def attempt(pw):
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/login",
                "query_string": b"",
                "headers": [(b"host", b"splouch.local")],
                "client": ("10.10.10.50", 50000),
                "session": {},
                "app": pi_app.app,
            }
        )
        resp = asyncio.run(pi_app.route_login(request, "admin", pw))
        return resp.status_code

    for _ in range(pi_app._LOGIN_FAIL_MAX):
        assert attempt("guess") == 401
    assert attempt("correct-horse") == 429, "the right password got through the lock"

    pi_app._login_fails.clear()
    assert attempt("correct-horse") == 303


# ── A saved theme is valid TOML whatever it is called ─────────────────────────


def test_a_quote_in_a_theme_name_or_font_cannot_break_the_file(tmp_path, monkeypatch):
    """Values were written between bare quotes, so `"` ended the string early: the
    file stopped parsing, or a crafted name added keys of its own."""
    import contextlib
    import tomllib

    import state
    from routes import settings as settings_routes
    from test_settings_display_form import _FakeRequest

    monkeypatch.setattr(state, "CUSTOM_THEME_FOLDER", str(tmp_path))
    monkeypatch.setattr(state, "save_settings", lambda: None)
    monkeypatch.setitem(
        state.settings,
        "theme_fonts",
        {**state.DEFAULT_THEME_FONTS, "family": '"Fira Sans", sans-serif'},
    )
    name = 'Club "Red"\nx = 1'
    with contextlib.suppress(Exception):  # rendering the page afterwards may not
        settings_routes._settings_view(
            _FakeRequest({}), {"theme_save_submit": "1", "theme_name": name}
        )
    (saved,) = tmp_path.glob("*.toml")
    data = tomllib.loads(saved.read_text(encoding="utf-8"))
    assert data["name"] == name
    assert "x" not in data
    assert data["fonts"]["family"] == '"Fira Sans", sans-serif'


def test_images_are_served_from_the_images_folder_only(tmp_path, monkeypatch):
    from fastapi import HTTPException

    import state
    from routes.appearance import serve_image

    images = tmp_path / "images"
    images.mkdir()
    (images / "logo.png").write_bytes(b"png")
    sibling = tmp_path / "images_private"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("no")
    monkeypatch.setattr(state, "IMAGES_DIR", str(images))

    assert serve_image("logo.png").path == str((images / "logo.png").resolve())
    for escape in (
        "../images_private/secret.txt",
        "../images/../images_private/secret.txt",
    ):
        with pytest.raises(HTTPException):
            serve_image(escape)


@pytest.mark.parametrize(
    "field, key",
    [
        ("selected_picker_image", "active_picker_image"),
        ("active_picker_image", "active_picker_image"),  # via the generic sweep
        ("splash_url", "splash_url"),
        ("active_theme", "active_theme"),
    ],
)
def test_a_settings_field_cannot_name_a_file_outside_its_folder(
    monkeypatch, field, key
):
    """`/picker_image` serves `PICKER_DIR/<active_picker_image>` to anyone, and
    `/splash_delete` removes `IMAGES_DIR/<splash_url>`: a `../` stored in either
    reached any file the service user can read or delete."""
    import contextlib

    import state
    from routes import settings as settings_routes
    from test_settings_display_form import _FakeRequest

    monkeypatch.setattr(state, "save_settings", lambda: None)
    monkeypatch.setattr(state, "save_meet_profile", lambda *_: None)
    monkeypatch.setattr(
        settings_routes,
        "relay",
        type(
            "R",
            (),
            {
                "update_metadata": staticmethod(lambda: None),
                "relay_emit": staticmethod(lambda *a: None),
            },
        ),
    )
    monkeypatch.setitem(state.settings, key, "safe.png")
    form = {
        "meet_appearance_submit": "1",
        "splash_settings_submit": "1",
        field: "../../.session_key",
    }
    with contextlib.suppress(Exception):
        settings_routes._settings_view(_FakeRequest({}), form)
    assert "/" not in str(state.settings.get(key, "")), state.settings.get(key)


# ── The Pi's admin password is stored hashed ──────────────────────────────────


def test_a_clear_password_is_hashed_on_load_and_still_signs_in(tmp_path, monkeypatch):
    """settings.json goes into every backup, and backups get passed around."""
    import json

    import state

    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"username": "admin", "password": "correct-horse"}))
    monkeypatch.setattr(state, "settings_file", str(path))
    monkeypatch.setattr(state, "settings", dict(state.settings))
    monkeypatch.setattr(state, "set_lenex", lambda *_: None)
    monkeypatch.setattr(state, "load_event_info", lambda *_: None)
    state.load_settings()

    saved = json.loads(path.read_text())
    assert "correct-horse" not in path.read_text(), "the password is still in clear"
    assert saved["password_hash"].startswith("pbkdf2_sha256$")
    assert state.check_password("correct-horse")
    assert not state.check_password("wrong")


def test_a_downgraded_release_finds_neither_the_password_nor_the_default(monkeypatch):
    """A release from before hashing reads only `password`, and fell back to the
    shipped default — printed in the README — when it was missing."""
    import state

    monkeypatch.setattr(state, "settings", {"username": "score"})
    state.set_password("correct-horse")
    shipped = state._shipped_credentials()[1]
    assert state.settings["password"] not in ("correct-horse", shipped, "")


def test_the_default_login_banner_still_knows_the_shipped_password(monkeypatch):
    import state

    user, shipped = state._shipped_credentials()
    monkeypatch.setattr(state, "settings", {"username": user})
    state.set_password(shipped)
    assert state.using_default_credentials()
    state.set_password("something-else")
    assert not state.using_default_credentials()


def test_changing_the_password_in_settings_stores_only_a_hash(monkeypatch):
    import contextlib

    import state
    from routes import settings as settings_routes
    from test_settings_display_form import _FakeRequest

    monkeypatch.setattr(state, "settings", {**state.settings, "username": "score"})
    state.set_password("old-one")
    monkeypatch.setattr(state, "save_settings", lambda: None)
    request = _FakeRequest({})
    with contextlib.suppress(Exception):
        settings_routes._settings_view(request, {"password": "new-one"})
    assert state.check_password("new-one") and not state.check_password("old-one")
    assert "new-one" not in str(state.settings)


# ── The cloud relay does not run as root ──────────────────────────────────────


def test_the_cloud_image_drops_root_before_the_app_starts():
    """The relay is the internet-facing half; a bug in it should land in an
    account that owns /data and nothing else."""
    dockerfile = Path(os.path.join(REPO, "cloud", "Dockerfile")).read_text()
    assert "groupadd --system --gid 10001" in dockerfile
    assert "useradd --system --uid 10001 --gid 10001" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]' in dockerfile
    entry = Path(os.path.join(REPO, "cloud", "docker-entrypoint.sh")).read_text()
    assert "APP_UID=10001" in entry, "must match the Dockerfile's useradd --uid"
    assert 'exec setpriv --reuid="$APP_UID" --regid="$APP_UID" --clear-groups' in entry
    # Volumes from before this are root-owned: handed over, not left unwritable.
    assert 'find /data ! -user "$APP_UID" -exec chown "$APP_UID:$APP_UID" {} +' in entry


@pytest.mark.skipif(
    os.geteuid() == 0, reason="as root it drops to the image's user, which is not here"
)
def test_the_entrypoint_runs_the_command_when_already_unprivileged(tmp_path):
    import subprocess

    entry = os.path.join(REPO, "cloud", "docker-entrypoint.sh")
    out = subprocess.run(
        ["sh", entry, "echo", "started"], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "started"


# ── The Wi-Fi password is not on a command line ───────────────────────────────


def test_the_wifi_password_goes_to_nmcli_on_stdin(monkeypatch):
    """As an argument it was readable from `ps` by every process on the Pi."""
    import subprocess

    from routes import network

    runs = []

    def fake_run(args, **kw):
        runs.append((list(args), kw.get("input")))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert network._wifi_connect("Pool", "s3cret-psk") == {"ok": True}
    assert all("s3cret-psk" not in a for args, _ in runs for a in args)
    connect = [(a, i) for a, i in runs if "connect" in a]
    assert connect and connect[0][1] == "s3cret-psk\n"
    assert "--ask" in connect[0][0]
