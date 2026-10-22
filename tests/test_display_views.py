"""Settings → Displays → TVs: which view each TV shows — Live or Results.

The server keeps a view per TV (server/routes/displays.py) and sends it as
`display_view` on every connect and on every change. Results needs a console that
times — with none, no result ever arrives (the phones' `A-11`) — so it is not
offered then, and a TV on it goes back to Live.

On the TV the results view is the board's own rows fed from `/ws/results`
(scoreboard/board.py `show_results`), with the waiting line in place of the table
until there is a heat to show (docs/app.md `R-01`). Qt tests skip without PySide6.
"""

import asyncio

import pytest

import bus
import state
from routes import displays

ID_A = "0b7f1c2e-4a5d-4e6f-8a9b-0c1d2e3f4a5b"


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, frame):
        self.sent.append(frame)

    def events(self):
        return [f["event"] for f in self.sent]


class Decoder:
    def __init__(self, timed=True):
        self.requires_serial = timed


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setitem(state.settings, "displays", {})
    monkeypatch.setattr(state, "_scoreboard_clients", {})
    monkeypatch.setattr(state, "_display_sockets", {})
    monkeypatch.setattr(state, "_identify_until", {})
    monkeypatch.setattr(state, "_decoder", Decoder(timed=True))
    monkeypatch.setattr(state, "_test_saved_decoder", None)
    monkeypatch.setattr(state, "board", {"current_event": "3", "lane_name1": "Roy"})
    monkeypatch.setattr(state, "save_settings", lambda: None)
    monkeypatch.setitem(state.settings, "board_lane_direction", "up")
    monkeypatch.setitem(state.settings, "results_sort", "lane")
    submitted = []
    monkeypatch.setattr(bus, "submit", lambda coro: submitted.append(coro))
    yield submitted
    for coro in submitted:
        coro.close()


def _connect():
    ws = FakeSocket()
    client = {"ip": "10.10.10.20", "role": "kiosk", "hostname": "splouch-tv"}
    state._scoreboard_clients[id(ws)] = client
    assert displays.register_display(ws, client, {"device_id": ID_A}) == ID_A
    asyncio.run(displays.send_view({ws}, ID_A))
    return ws


def _set_view(view):
    return asyncio.run(
        displays.route_display_view(displays.ViewBody(device_id=ID_A, view=view))
    )


def test_a_new_tv_starts_live(server):
    ws = _connect()
    assert state.settings["displays"][ID_A]["view"] == "live"
    assert ws.sent[0] == {
        "event": "display_view",
        "data": {"view": "live", "lane_direction": "up", "results_sort": "lane"},
    }


def test_live_comes_with_the_board(server):
    """The TV dropped the board while on another view; frames are partial, so it
    needs the whole of it back, not the next change."""
    ws = _connect()
    assert ws.events() == ["display_view", "update_scoreboard"]
    assert ws.sent[1]["data"] == {"current_event": "3", "lane_name1": "Roy"}


def test_switching_reaches_the_tv(server):
    ws = _connect()
    ws.sent.clear()
    assert _set_view("results") == {"ok": True}
    assert ws.events() == ["display_view"]
    assert ws.sent[0]["data"]["view"] == "results"
    assert state.settings["displays"][ID_A]["view"] == "results"
    ws.sent.clear()
    _set_view("live")
    assert ws.events() == ["display_view", "update_scoreboard"]


def test_a_tv_not_connected_takes_it_on_connect(server):
    _connect()
    state._display_sockets.clear()
    assert _set_view("results") == {"ok": True}
    ws = _connect()
    assert ws.sent[0]["data"]["view"] == "results"


def test_an_unknown_view_is_refused(server):
    _connect()
    assert _set_view("cinema").status_code == 409


def test_switching_is_allowed_mid_race(server, monkeypatch):
    monkeypatch.setattr(state, "_running_lanes", {1}, raising=False)
    _connect()
    assert _set_view("results") == {"ok": True}


# ── Each TV's lane order and results order ─────────────────────────────────────


def _set_option(key, value):
    return asyncio.run(
        displays.route_display_option(
            displays.OptionBody(device_id=ID_A, key=key, value=value)
        )
    )


def test_a_new_tv_copies_the_pools_choices(server):
    state.settings["board_lane_direction"] = "down"
    state.settings["results_sort"] = "place"
    ws = _connect()
    entry = state.settings["displays"][ID_A]
    assert (entry["lane_direction"], entry["results_sort"]) == ("down", "place")
    assert ws.sent[0]["data"]["lane_direction"] == "down"
    assert ws.sent[0]["data"]["results_sort"] == "place"


def test_the_pool_changing_later_moves_no_tv(server):
    """A copy, not a link: a TV the operator set up stays as it was."""
    _connect()
    state.settings["board_lane_direction"] = "down"
    state.settings["results_sort"] = "place"
    ws = _connect()
    assert ws.sent[0]["data"]["lane_direction"] == "up"
    assert ws.sent[0]["data"]["results_sort"] == "lane"


def test_an_option_change_reaches_the_tv(server):
    ws = _connect()
    ws.sent.clear()
    assert _set_option("lane_direction", "down") == {"ok": True}
    assert state.settings["displays"][ID_A]["lane_direction"] == "down"
    assert ws.sent[0]["event"] == "display_view"
    assert ws.sent[0]["data"]["lane_direction"] == "down"


@pytest.mark.parametrize(
    ("key", "value"),
    [("lane_direction", "sideways"), ("results_sort", "x"), ("name", "up")],
)
def test_an_unknown_option_is_refused(server, key, value):
    _connect()
    assert _set_option(key, value).status_code == 409


# ── A meet with no timing console ──────────────────────────────────────────────


def test_no_console_no_results(server, monkeypatch):
    monkeypatch.setattr(state, "_decoder", Decoder(timed=False))
    assert displays.available_views() == ["live"]
    _connect()
    assert _set_view("results").status_code == 409


def test_a_test_replay_does_not_offer_results(server, monkeypatch):
    """A replay stands a timing decoder in for the manual console while it runs;
    the meet still has none."""
    monkeypatch.setattr(state, "_test_saved_decoder", Decoder(timed=False))
    assert displays.available_views() == ["live"]


def test_tvs_on_results_go_back_live(server, monkeypatch):
    ws = _connect()
    _set_view("results")
    ws.sent.clear()
    monkeypatch.setattr(state, "_decoder", Decoder(timed=False))
    displays.fall_back_unavailable()
    assert state.settings["displays"][ID_A]["view"] == "live", "saved, not overridden"
    (coro,) = server
    asyncio.run(coro)
    server.clear()
    assert ws.events() == ["display_view", "update_scoreboard"]
    assert ws.sent[0]["data"]["view"] == "live"


def test_a_tv_offline_through_the_change_comes_back_live(server, monkeypatch):
    _connect()
    _set_view("results")
    state._display_sockets.clear()
    monkeypatch.setattr(state, "_decoder", Decoder(timed=False))
    ws = _connect()
    assert ws.sent[0]["data"]["view"] == "live"


def test_the_settings_list_offers_only_what_the_meet_feeds(server, monkeypatch):
    import os

    from jinja2 import Environment, FileSystemLoader

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = Environment(
        loader=FileSystemLoader(os.path.join(repo, "server", "templates"))
    )
    _connect()

    def render():
        return env.get_template("settings/fetched/displays.html").render(
            displays=displays.display_rows(),
            views=displays.available_views(),
            unlisted=0,
            t=state.settings_strings("en"),
        )

    assert 'data-view="results"' in render()
    monkeypatch.setattr(state, "_decoder", Decoder(timed=False))
    assert 'data-view="results"' not in render()


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_the_views_are_translated(code):
    strings = state.settings_strings(code)
    for view in displays.VIEWS:
        assert strings.get(f"tvs_view_{view}"), f"{code} missing tvs_view_{view}"
    assert state.display_strings(code).get("waiting_results")


# ── The TV ─────────────────────────────────────────────────────────────────────

SNAPSHOT = {
    "event": "3",
    "heat": "2",
    "event_name": "100 Free",
    "sort": "lane",
    "lanes": [
        {"channel": 4, "place": "1", "place_int": 1, "time": "58.10", "name": "Roy"},
        {"channel": 2, "place": "2", "place_int": 2, "time": "59.30", "name": "Lee"},
        {"channel": 5, "place": "", "place_int": 99, "time": "", "name": "Tran"},
    ],
}


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


def test_the_waiting_line_is_the_screen(board):
    """`R-01`: no grid of blank rows before the first heat."""
    board.set_results_mode(True)
    assert board.waiting.isVisibleTo(board)
    assert not board.content.isVisibleTo(board)
    assert board.waiting.text() == "Waiting for results…"


def test_results_by_lane(board):
    board.set_results_mode(True)
    board.show_results(SNAPSHOT)
    assert board.content.isVisibleTo(board) and not board.waiting.isVisibleTo(board)
    assert board.event_cell.value.text() == "3" and board.heat_cell.value.text() == "2"
    assert board.name_label.text() == "100 Free"
    rows = {row.lane: row for row in board.rows}
    assert rows[4].name_label.text() == "Roy" and rows[4].place_label.text() == "1"
    assert rows[2].name_label.text() == "Lee"
    assert rows[1].name_label.text() == "" and rows[1].lane_label.text() == "1"
    assert rows[5].time_label.text() == "—", "a result missing its time (`R-07`)"


def test_results_by_place(board):
    board.set_results_mode(True)
    board.show_results(SNAPSHOT, sort="place")
    names = [row.name_label.text() for row in board.rows]
    assert names[:3] == ["Roy", "Lee", "Tran"]
    lanes = [row.lane_label.text() for row in board.rows]
    assert lanes[:3] == ["4", "2", "5"]
    assert lanes[3:] == ["", "", ""], "an unfilled rank shows no lane (`R-06`)"


def test_an_empty_snapshot_changes_nothing(board):
    board.set_results_mode(True)
    board.show_results(SNAPSHOT)
    board.show_results({"event": "4", "lanes": []})
    assert board.event_cell.value.text() == "3"


def test_clearing_goes_back_to_waiting(board):
    """`R-02`: a results screen holds still, so stale times read as current."""
    board.set_results_mode(True)
    board.show_results(SNAPSHOT)
    board.clear_results()
    assert board.waiting.isVisibleTo(board)
    assert all(row.name_label.text() == "" for row in board.rows)
    assert board.event_cell.value.text() == ""


def test_back_to_live_restores_the_lane_numbers(board):
    board.set_results_mode(True)
    board.show_results(SNAPSHOT, sort="place")
    board.set_results_mode(False)
    assert [row.lane_label.text() for row in board.rows] == [
        str(i) for i in range(1, 7)
    ]
    assert board.content.isVisibleTo(board) and not board.waiting.isVisibleTo(board)


def test_the_app_switches_and_ignores_the_live_board(qt_app, monkeypatch):
    from scoreboard.app import ScoreboardApp
    from scoreboard.client import ServerLink

    started = []
    monkeypatch.setattr(ServerLink, "start", lambda self: started.append(self._path))
    app = ScoreboardApp("http://127.0.0.1:1", fullscreen=False)
    try:
        started.clear()
        app._on_frame("display_view", {"view": "results"})
        assert app.view == "results" and started == ["/ws/results"]
        assert app.window.results_mode

        app._on_frame("update_scoreboard", {"current_event": "9", "lane_name1": "X"})
        assert app.window.event_cell.value.text() == ""
        assert app.window.waiting.isVisibleTo(app.window)

        app._on_results_frame("results_snapshot", SNAPSHOT)
        assert app.window.rows[3].name_label.text() == "Roy"
        app._on_results_frame("meet_live", {"live": False})
        assert app.window.waiting.isVisibleTo(app.window)

        app._on_frame("display_view", {"view": "live"})
        assert app.results_link is None and not app.window.results_mode
        app._on_frame("update_scoreboard", {"current_event": "9", "lane_name1": "X"})
        assert app.window.rows[0].name_label.text() == "X"
    finally:
        app.link.stop()
        app._stop_results_link()


def test_last_lane_on_top(board):
    board.set_lane_direction("down")
    layout = board.content.layout()
    on_screen = [layout.itemAt(i).widget() for i in range(1, layout.count())]
    assert [row.lane for row in on_screen] == [6, 5, 4, 3, 2, 1]
    board.apply_update({"lane_name6": "Roy"})
    assert board.rows[5].name_label.text() == "Roy", "a lane keeps its own row"
    board.set_lane_direction("up")
    on_screen = [layout.itemAt(i).widget() for i in range(1, layout.count())]
    assert [row.lane for row in on_screen] == [1, 2, 3, 4, 5, 6]


def test_a_ranking_still_starts_at_the_top(board):
    """With the last lane on top, first place is the top row, not lane 1's."""
    board.set_lane_direction("down")
    board.set_results_mode(True)
    board.show_results(SNAPSHOT, sort="place")
    top_down = board.rows_top_down()
    assert [row.name_label.text() for row in top_down[:3]] == ["Roy", "Lee", "Tran"]
    assert [row.lane_label.text() for row in top_down[:3]] == ["4", "2", "5"]


def test_the_app_takes_the_tvs_own_order(qt_app, monkeypatch):
    from scoreboard.app import ScoreboardApp
    from scoreboard.client import ServerLink

    monkeypatch.setattr(ServerLink, "start", lambda self: None)
    app = ScoreboardApp("http://127.0.0.1:1", fullscreen=False)
    try:
        frame = {"view": "results", "lane_direction": "down", "results_sort": "lane"}
        app._on_frame("display_view", frame)
        assert app.window.lane_direction == "down"
        app._on_results_frame("results_snapshot", SNAPSHOT)
        assert app.window.rows[3].name_label.text() == "Roy"
        app._on_frame("display_view", {**frame, "results_sort": "place"})
        assert app.window.rows_top_down()[0].name_label.text() == "Roy", "re-ranked"
    finally:
        app.link.stop()
        app._stop_results_link()
