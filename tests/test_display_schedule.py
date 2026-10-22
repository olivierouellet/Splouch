"""The TV's Schedule view: the heats still to swim, as many as fit whole.

It starts at the heat after the one on the console — the first of `next_heats` —
and adds each following heat only if the whole of it fits on screen: a heat cut
off at the bottom reads as a heat with fewer swimmers. Nothing scrolls. The pure
half (scoreboard/schedule.py) is Qt-free; the widget and the app skip without
PySide6.
"""

import pytest

from scoreboard import schedule


def _heat(event, heat, swimmers, time=""):
    lanes = [
        {"lane": i, "name": f"S{event}.{heat}.{i}", "club": "CNQ"} for i in swimmers
    ]
    lanes.append({"lane": 9, "name": "", "club": ""})  # an empty lane in the list
    return {
        "event": event,
        "heat": heat,
        "event_name": "100 Free",
        "time": time,
        "lanes": lanes,
    }


MEET = [_heat(1, 1, range(1, 9)), _heat(1, 2, range(1, 9)), _heat(2, 1, range(1, 4))]
MEET += [_heat(3, h, range(1, 9)) for h in (1, 2, 3)]


def test_a_heat_is_its_title_and_its_swimmers():
    assert schedule.heat_lines(MEET[0]) == 9, "empty lanes take no line"


def test_it_starts_at_the_next_heat():
    """Heats already swum, and the one in the water, are not shown."""
    assert schedule.upcoming(MEET, (1, 2))[0] is MEET[1]
    assert schedule.upcoming(MEET, ("2", "1"))[0] is MEET[2], "keys compare as text"
    assert schedule.upcoming(MEET, (9, 9)) == []
    assert schedule.upcoming(MEET, None) == []


def test_only_whole_heats_are_shown():
    # 9 + 9 = 18 of 20 lines; the next 8-lane heat would need 9 more.
    assert schedule.fit(MEET[:2] + MEET[3:]) == MEET[:2]


def test_a_short_heat_after_still_fits_if_whole():
    # 9 + 9 + 4 = 22 > 20: the 3-swimmer heat does not fit after two full ones…
    assert schedule.fit(MEET) == MEET[:2]
    # …but after one it does, and the full heat after it does not.
    assert schedule.fit(MEET[1:]) == MEET[1:3]


def test_it_never_skips_ahead_to_a_shorter_heat():
    """Heat order is the point of a schedule: no heat out of its turn."""
    shown = schedule.fit([MEET[3], MEET[4], MEET[2]], lines=20)
    assert shown == [MEET[3], MEET[4]]


def test_the_next_heat_is_shown_even_if_too_long():
    assert schedule.fit([_heat(1, 1, range(1, 30))]) != []


def test_the_last_lane_on_top_reverses_each_heat():
    lines = schedule.layout([MEET[2]], "down")
    assert [item["lane"] for kind, item, _ in lines if kind == "lane"] == [3, 2, 1]
    assert lines[0][0] == "title"


def test_seed_times_read_as_on_the_schedule_page():
    assert schedule.display_time("00:01:02.40") == "01:02.40"
    assert schedule.display_time("01:02:03.40") == "01:02:03.40"
    assert schedule.display_time("") == ""


# ── On the TV ──────────────────────────────────────────────────────────────────


@pytest.fixture
def board(qt_app):
    from scoreboard.board import BoardWindow
    from scoreboard.theme import Config

    window = BoardWindow(Config({"num_lanes": 8}))
    window.resize(1920, 1080)
    window.show()
    qt_app.processEvents()
    yield window
    window.stop_clock()
    window.close()


def test_the_view_says_schedule(board, qt_app):
    board.set_view_mode("schedule")
    assert board.waiting.isVisibleTo(board)
    board.show_schedule(MEET[1:])
    qt_app.processEvents()
    assert board.schedule.isVisibleTo(board) and not board.content.isVisibleTo(board)
    assert board.chrono_label.text() == "SCHEDULE"
    assert board.schedule.heats == MEET[1:3]
    board.schedule.grab()  # paints without raising


def test_the_end_of_the_meet_is_the_waiting_line(board):
    board.set_view_mode("schedule")
    board.show_schedule(MEET)
    board.show_schedule([])
    assert board.waiting.isVisibleTo(board)
    assert board.waiting.text() == "No upcoming heats"


def test_back_to_live_hides_it(board):
    board.set_view_mode("schedule")
    board.show_schedule(MEET)
    board.set_view_mode("live")
    assert board.content.isVisibleTo(board) and not board.schedule.isVisibleTo(board)


def _next(event, heat):
    return {"heats": [{"event": event, "heat": heat, "swimmers": []}]}


def test_the_app_follows_the_next_heat(qt_app, monkeypatch):
    from scoreboard.app import ScoreboardApp
    from scoreboard.client import ScheduleLoader, ServerLink

    monkeypatch.setattr(ServerLink, "start", lambda self: None)
    fetches = []
    monkeypatch.setattr(ScheduleLoader, "request", lambda self: fetches.append(1))
    app = ScoreboardApp("http://127.0.0.1:1", fullscreen=False)
    try:
        app._on_frame("display_view", {"view": "schedule", "ready": True})
        app._on_results_frame("next_heats", _next(1, 2))
        assert fetches, "a heat change fetches the start list again"
        assert app.window.waiting.isVisibleTo(app.window), "waits for the list"
        app._on_schedule_loaded(MEET)
        assert app.window.schedule.heats[0] is MEET[1]

        app._on_results_frame("next_heats", _next(9, 9))  # the list predates it
        assert app.window.schedule.heats[0] is MEET[1], "not repainted from it"
        app._on_results_frame("next_heats", {"heats": []})
        assert app.window.waiting.text() == "No upcoming heats"

        app._on_frame("display_view", {"view": "schedule", "ready": False})
        assert app.window.waiting.text() == "No meet file loaded"
        app._on_results_frame("next_heats", _next(2, 1))
        app._on_schedule_loaded(MEET)
        assert app.window.waiting.isVisibleTo(app.window), "not while not ready"
        app._on_frame("display_view", {"view": "schedule", "ready": True})
        assert app.window.schedule.heats[0] is MEET[2]
    finally:
        app.link.stop()
        app._stop_results_link()


def test_words_in_the_clock_cell_use_the_text_face(board):
    """The digit font is usually seven-segment: NEXT HEAT came out `nEHt hEAt`."""
    digits = board.chrono_label.font().family()
    board.set_view_mode("schedule")
    board.show_schedule(MEET)
    assert board.chrono_label.font().family() != digits
    board.apply_theme()
    assert board.chrono_label.font().family() != digits, "kept through a reload"
    board.set_view_mode("live")
    assert board.chrono_label.font().family() == digits


@pytest.mark.parametrize(
    ("lanes", "lines", "full_heats"),
    [(4, 20, 4), (6, 21, 3), (8, 20, 2), (10, 22, 2), (12, 26, 2)],
)
def test_the_lines_follow_the_pool(lanes, lines, full_heats):
    """Three full heats up to 6 lanes, two above, never fewer than 20 lines."""
    assert schedule.lines_for(lanes) == lines
    full = [_heat(1, h, range(1, lanes + 1)) for h in range(1, 6)]
    assert len(schedule.fit(full, lines)) == full_heats


def test_the_widget_divides_by_its_pool(qt_app):
    from scoreboard.schedule_view import ScheduleView
    from scoreboard.theme import Config

    view = ScheduleView(Config({"num_lanes": 12}))
    assert view.line_count() == 26
    view.set_heats([_heat(1, h, range(1, 13)) for h in range(1, 4)])
    assert len(view.heats) == 2
