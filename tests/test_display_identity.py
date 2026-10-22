"""Settings → Displays → TVs: each TV by the ID it keeps, and Identify.

Hostnames repeat across Pis flashed from one image, so a TV makes up a UUID on
first launch (scoreboard/identity.py) and the server keys its settings by it
(server/routes/displays.py). Identify turns one TV's wall clock green for a while,
so the operator can tell which TV a row is.

Qt-free apart from the board tests at the end, which skip without PySide6.
"""

import asyncio
import os

import pytest

import state
from routes import displays
from scoreboard.identity import device_id, id_path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ID_A = "0b7f1c2e-4a5d-4e6f-8a9b-0c1d2e3f4a5b"
ID_B = "1c8a2d3f-5b6e-4f70-9bac-1d2e3f4a5b6c"


# ── The display's side ─────────────────────────────────────────────────────────


@pytest.fixture
def config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path


def test_the_id_is_made_once_and_kept(config_home):
    first = device_id()
    assert os.path.exists(id_path())
    assert device_id() == first, "a restart must not make the TV a new one"


def test_a_damaged_id_file_is_replaced(config_home):
    os.makedirs(os.path.dirname(id_path()))
    with open(id_path(), "w") as f:
        f.write("not a uuid")
    new = device_id()
    assert displays._ID.match(new)
    assert device_id() == new


def test_an_unwritable_config_still_gives_an_id(monkeypatch, tmp_path):
    """A read-only home must not stop the board — the TV just looks new next boot."""
    blocker = tmp_path / "file"
    blocker.write_text("")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(blocker))  # a file, not a directory
    assert displays._ID.match(device_id())


# ── The server's side ──────────────────────────────────────────────────────────


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, frame):
        self.sent.append(frame)


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setitem(state.settings, "displays", {})
    monkeypatch.setattr(state, "_scoreboard_clients", {})
    monkeypatch.setattr(state, "_display_sockets", {})
    monkeypatch.setattr(state, "_identify_until", {})
    saves = []
    monkeypatch.setattr(state, "save_settings", lambda: saves.append(1))
    return saves


def _connect(device=ID_A, hostname="splouch-tv"):
    ws = FakeSocket()
    client = {"ip": "10.10.10.20", "at": "09:41", "role": "kiosk", "hostname": hostname}
    state._scoreboard_clients[id(ws)] = client
    displays.register_display(ws, client, {"device_id": device})
    return ws, client


def _disconnect(ws):
    displays.unregister_display(ws, state._scoreboard_clients.pop(id(ws)))


def test_a_new_tv_is_remembered(server):
    _connect()
    assert state.settings["displays"] == {
        ID_A: {"name": "", "hostname": "splouch-tv", "view": "live"}
    }
    assert server == [1]


def test_a_reconnect_does_not_rewrite_settings(server):
    ws, _ = _connect()
    _disconnect(ws)
    _connect()
    assert server == [1], "settings.json is on an SD card; every reconnect is wear"


def test_a_new_hostname_is_noted(server):
    _connect()
    _connect(hostname="pool-deck")
    assert state.settings["displays"][ID_A]["hostname"] == "pool-deck"


@pytest.mark.parametrize("bad", ["", "../../etc", "x" * 400, ID_A + "0"])
def test_an_id_that_is_not_a_uuid_is_ignored(server, bad):
    """Unauthenticated LAN input, and it becomes a key in settings.json."""
    _, client = _connect(device=bad)
    assert state.settings["displays"] == {}
    assert "device_id" not in client


def test_a_tv_stays_listed_when_it_leaves(server):
    ws, _ = _connect()
    _disconnect(ws)
    (row,) = displays.display_rows()
    assert row["device_id"] == ID_A and not row["connected"]
    assert row["hostname"] == "splouch-tv", "kept from settings, to know which it was"
    assert ID_A not in state._display_sockets


def test_a_cloned_card_is_flagged(server):
    _connect()
    _connect()
    (row,) = displays.display_rows()
    assert row["copies"] == 2


def test_connected_tvs_come_first(server):
    state.settings["displays"][ID_B] = {"name": "Attic", "hostname": "old"}
    _connect()
    assert [r["device_id"] for r in displays.display_rows()] == [ID_A, ID_B]


def _post(route, **body):
    model = route.__annotations__["body"]
    return asyncio.run(route(model(**body)))


def test_identify_reaches_only_that_tv(server):
    a, _ = _connect()
    b, _ = _connect(device=ID_B, hostname="lobby")
    assert _post(displays.route_display_identify, device_id=ID_A) == {"ok": True}
    assert a.sent == [{"event": "identify", "data": {"active": True, "seconds": 10}}]
    assert b.sent == []
    left = {r["device_id"]: r["identify_left"] for r in displays.display_rows()}
    assert left == {ID_A: 10, ID_B: 0}


def test_identify_lights_every_copy(server):
    """Two Pis on one ID: lighting both is how you find them."""
    a, _ = _connect()
    b, _ = _connect()
    _post(displays.route_display_identify, device_id=ID_A)
    assert a.sent and b.sent


def test_cancel_ends_it_early(server):
    a, _ = _connect()
    _post(displays.route_display_identify, device_id=ID_A)
    _post(displays.route_display_identify, device_id=ID_A, active=False)
    assert a.sent[-1] == {"event": "identify", "data": {"active": False}}
    assert displays.display_rows()[0]["identify_left"] == 0


def test_identify_is_allowed_mid_race(server, monkeypatch):
    """Unlike an update: it changes only the colour of the clock."""
    monkeypatch.setattr(state, "_running_lanes", {1, 2}, raising=False)
    _connect()
    assert _post(displays.route_display_identify, device_id=ID_A) == {"ok": True}


def test_identify_needs_the_tv_connected(server):
    ws, _ = _connect()
    _disconnect(ws)
    assert _post(displays.route_display_identify, device_id=ID_A).status_code == 409


def test_a_name_is_kept_tidy(server):
    _connect()
    _post(
        displays.route_display_name, device_id=ID_A, name="  Pool \n deck " + "x" * 80
    )
    name = state.settings["displays"][ID_A]["name"]
    assert name.startswith("Pool deck x") and len(name) == displays.NAME_MAX


def test_only_a_gone_tv_can_be_forgotten(server):
    ws, _ = _connect()
    assert _post(displays.route_display_forget, device_id=ID_A).status_code == 409
    _disconnect(ws)
    assert _post(displays.route_display_forget, device_id=ID_A) == {"ok": True}
    assert state.settings["displays"] == {}


# ── What the operator sees ─────────────────────────────────────────────────────


def _render(rows, unlisted=0, code="en"):
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(os.path.join(REPO, "server", "templates"))
    )
    return env.get_template("settings/fetched/displays.html").render(
        displays=rows, unlisted=unlisted, t=state.settings_strings(code)
    )


def test_the_list_offers_the_right_button(server):
    _connect()
    state.settings["displays"][ID_B] = {"name": "Attic", "hostname": "old"}
    html = _render(displays.display_rows())
    assert html.count('data-hold-fn="identifyDisplay"') == 1
    assert html.count('data-hold-fn="forgetDisplay"') == 1


def test_cancel_replaces_identify_while_it_runs(server):
    _connect()
    _post(displays.route_display_identify, device_id=ID_A)
    html = _render(displays.display_rows())
    assert "identifyDisplay(this, false)" in html
    assert 'data-hold-fn="identifyDisplay"' not in html


def test_counts_render(server):
    _connect()
    _connect()
    html = _render(displays.display_rows(), unlisted=2)
    assert "2 Pis, one ID" in html
    assert "2 display(s) connected without an ID" in html


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_the_tvs_are_translated(code):
    strings = state.settings_strings(code)
    for key in (
        "nav_displays",
        "nav_appearance",
        "nav_tvs",
        "tvs_identify",
        "hold_identify",
    ):
        assert strings.get(key), f"{code} missing {key}"


# ── The TV's clock ─────────────────────────────────────────────────────────────


@pytest.fixture
def board(qt_app):
    from scoreboard.board import BoardWindow
    from scoreboard.theme import Config

    window = BoardWindow(Config({"num_lanes": 6}))
    window.resize(1920, 1080)
    window.show()
    qt_app.processEvents()
    yield window
    window.stop_clock()
    window.close()


def test_identify_fills_the_wall_clock_green(board):
    from scoreboard.board import _IDENTIFY, _IDENTIFY_INK

    before = board.wall_clock.styleSheet()
    board.set_identify(True)
    style = board.wall_clock.styleSheet()
    assert f"background: {_IDENTIFY}" in style and f"color: {_IDENTIFY_INK}" in style
    assert _IDENTIFY not in board.chrono_label.styleSheet(), "the clock, not the chrono"
    board.set_identify(False)
    assert board.wall_clock.styleSheet() == before


def test_a_green_clock_theme_still_changes(board):
    """A theme may draw the clock green already; green text would change nothing."""
    from scoreboard.board import _IDENTIFY

    board.cfg.colors["header_label"] = _IDENTIFY
    board.apply_theme()
    before = board.wall_clock.styleSheet()
    board.set_identify(True)
    assert board.wall_clock.styleSheet() != before
    assert f"background: {_IDENTIFY}" in board.wall_clock.styleSheet()


def test_a_green_bar_swaps_the_colours(qt_app):
    """On a bar that is green already, a green fill would vanish into it."""
    from scoreboard.board import _IDENTIFY, _IDENTIFY_INK, BoardWindow
    from scoreboard.theme import Config

    window = BoardWindow(
        Config({"num_lanes": 6, "theme_colors": {"header_bg": "#22ee22"}})
    )
    try:
        window.set_identify(True)
        style = window.wall_clock.styleSheet()
        assert f"background: {_IDENTIFY_INK}" in style
        assert f"color: {_IDENTIFY}" in style
    finally:
        window.close()


def test_a_theme_reload_keeps_it_green(board):
    from scoreboard.board import _IDENTIFY

    board.set_identify(True)
    board.apply_theme()
    assert _IDENTIFY in board.wall_clock.styleSheet()


def test_the_bar_shows_over_the_splash(board, qt_app):
    board.show_splash()
    qt_app.processEvents()
    board.set_identify(True)
    children = board.children()
    assert children.index(board.header) > children.index(board.splash)
    board.set_identify(False)
    children = board.children()
    assert children.index(board.header) < children.index(board.splash)


def test_the_tv_times_it_itself(qt_app):
    """So a link that drops mid-identify still leaves the clock back to normal."""
    from scoreboard.app import ScoreboardApp

    app = ScoreboardApp("http://127.0.0.1:1", fullscreen=False)
    try:
        app._on_frame("identify", {"active": True, "seconds": 10})
        assert app.window.identifying
        assert app._identify_timer.isActive()
        assert app._identify_timer.interval() == 10_000
        app._identify_timer.timeout.emit()
        assert not app.window.identifying

        app._on_frame("identify", {"active": True, "seconds": 9999})
        assert app._identify_timer.interval() == 60_000, "a bad frame is capped"
        app._on_frame("identify", {"active": False})
        assert not app.window.identifying and not app._identify_timer.isActive()
    finally:
        app.link.stop()
