"""Per-lane running clocks.

A lane does not tick continuously. On the touch the console drops
`lane_running<i>` and sends the lap in `lane_time<i>`, then holds it there for a
fixed number of seconds — its own setting, not the length of the turn, and not
ended by the swimmer leaving the pad — before the flag returns and the lane
rejoins the race clock. Freezing the split is the entire point of the flag —
without it the lap time is overwritten before anyone sees it.

All running lanes show the *same* value, the console's race clock, exactly as the
browser does. There is no independent per-lane timer: a lane's own elapsed time
only becomes meaningful at its split, and that arrives as `lane_time<i>`.

Needs PySide6 (`uv run pytest tests/`); skips without it.
"""
import time

import pytest

pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')

from scoreboard.board import BoardWindow          # noqa: E402
from scoreboard.theme import Config               # noqa: E402

# `qt_app` comes from tests/conftest.py — session-scoped, fonts already loaded.


@pytest.fixture
def board(qt_app):
    window = BoardWindow(Config({'num_lanes': 4}))
    window.resize(1920, 1080)
    window.show()
    qt_app.processEvents()
    yield window
    window.stop_clock()
    window.close()


def _pump(qt_app, seconds):
    """Let the 50ms ticker run for real — this is a timing behaviour."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt_app.processEvents()
        time.sleep(0.01)


def test_running_lanes_mirror_the_race_clock(board, qt_app):
    board.apply_update({'running_time': '5.00',
                        'lane_running1': True, 'lane_running2': True})
    qt_app.processEvents()
    assert board.rows[0].time_label.text() == board.chrono_label.text()
    assert board.rows[1].time_label.text() == board.chrono_label.text()
    # Lanes with no swimmer running must stay untouched.
    assert board.rows[2].time_label.text() == ''
    assert board.rows[3].time_label.text() == ''


def test_clock_interpolates_between_console_frames(board, qt_app):
    """The console sends `running_time` a few times a second, not 20.

    Rendering only those frames makes the hundredths visibly step, so the board
    re-bases on each frame and interpolates locally in between.
    """
    board.apply_update({'running_time': '5.00', 'lane_running1': True})
    qt_app.processEvents()
    _pump(qt_app, 0.4)                       # no further console frames
    assert board.chrono_label.text() != '5.00', 'clock did not advance on its own'
    assert board.rows[0].time_label.text() == board.chrono_label.text()


def test_a_split_freezes_the_lane_until_the_hold_ends(board, qt_app):
    """The case this feature exists for."""
    board.apply_update({'running_time': '5.00',
                        'lane_running1': True, 'lane_running2': True})
    qt_app.processEvents()

    # Lane 1 touches the wall: flag drops, split arrives in the same frame.
    board.apply_update({'lane_running1': False, 'lane_time1': '28.41',
                        'running_time': '28.60'})
    qt_app.processEvents()
    assert board.rows[0].time_label.text() == '28.41'

    # It must hold there while lane 2 keeps swimming.
    _pump(qt_app, 0.3)
    assert board.rows[0].time_label.text() == '28.41', 'split was overwritten'
    assert board.rows[1].time_label.text() != '28.41', 'lane 2 should still tick'

    # The console's hold expires: the lane rejoins the clock.
    board.apply_update({'lane_running1': True, 'running_time': '30.00'})
    qt_app.processEvents()
    _pump(qt_app, 0.15)
    assert board.rows[0].time_label.text() != '28.41'


def test_later_frames_do_not_stamp_the_split_back_over_a_running_lane(board, qt_app):
    """`lane_time<i>` stays in the snapshot after the split.

    Every subsequent frame touching that lane redraws it, so a running lane has to
    ignore `lane_time<i>` or the stale split would flicker over the live clock.
    """
    board.apply_update({'running_time': '5.00', 'lane_running1': True,
                        'lane_time1': '28.41'})
    qt_app.processEvents()
    board.apply_update({'lane_club1': 'CAMO'})      # unrelated field, same lane
    qt_app.processEvents()
    assert board.rows[0].time_label.text() != '28.41'


def test_ticker_stops_when_the_last_lane_finishes(board, qt_app):
    board.apply_update({'running_time': '5.00',
                        'lane_running1': True, 'lane_running2': True})
    qt_app.processEvents()
    board.apply_update({'lane_running1': False, 'lane_running2': False,
                        'lane_time1': '58.12', 'lane_time2': '59.03'})
    qt_app.processEvents()

    assert not board._clock_timer.isActive(), 'ticker left running with no swimmers'
    finals = [row.time_label.text() for row in board.rows]
    _pump(qt_app, 0.25)
    assert [row.time_label.text() for row in board.rows] == finals, 'finals drifted'


def test_stop_clock_freezes_everything(board, qt_app):
    """`race_finished` calls this in case a `lane_running` frame was missed."""
    board.apply_update({'running_time': '5.00', 'lane_running1': True})
    qt_app.processEvents()
    assert board._clock_timer.isActive()

    board.stop_clock()
    assert not board._clock_timer.isActive()
    assert not any(row.running for row in board.rows)
    held = board.rows[0].time_label.text()
    _pump(qt_app, 0.2)
    assert board.rows[0].time_label.text() == held


def test_unparseable_running_time_is_shown_verbatim(board, qt_app):
    """Never blank the header because a console sent something unexpected."""
    board.apply_update({'running_time': '??:??'})
    qt_app.processEvents()
    assert board.chrono_label.text() == '??:??'


def test_reset_stops_the_clock(board, qt_app):
    board.apply_update({'running_time': '5.00', 'lane_running1': True})
    qt_app.processEvents()
    board.reset()
    assert not board._clock_timer.isActive()
    assert board.rows[0].time_label.text() == ''


def test_the_race_clock_clears_when_the_heat_ends(board, qt_app):
    """It has nothing to say once nobody is swimming — the browser hides the cell.

    We clear the *text* instead of hiding the widget, because a hidden widget
    leaves the layout and everything to its left slides across.
    """
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    assert board.chrono_label.text()

    board.apply_update({'lane_running1': False, 'lane_time1': '1:12.44'})
    qt_app.processEvents()
    assert board.chrono_label.text() == '', 'clock still showing after the heat'


def test_clearing_the_clock_does_not_move_the_header(board, qt_app):
    """Nothing beside the clock may shift as heats come and go."""
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    slot = board.chrono_label.geometry()
    name = board.name_label.geometry()

    board.apply_update({'lane_running1': False, 'lane_time1': '1:12.44'})
    qt_app.processEvents()
    assert board.chrono_label.geometry() == slot, 'the clock gave up its space'
    assert board.name_label.geometry() == name, 'the event name moved'


def test_stop_clock_also_clears_it(board, qt_app):
    """`race_finished` arrives even when a `lane_running` frame was missed."""
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    board.stop_clock()
    assert board.chrono_label.text() == ''


def test_the_next_race_brings_it_back(board, qt_app):
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    board.apply_update({'lane_running1': False})
    qt_app.processEvents()
    assert board.chrono_label.text() == ''

    board.apply_update({'running_time': '0.00', 'lane_running2': True})
    qt_app.processEvents()
    assert board.chrono_label.text(), 'the clock did not come back for the next race'


# ── Which owner has the cell ───────────────────────────────────────────────────
# A ticking clock and a frozen split are the same digits in the same place, so the
# colour is the only thing that says which. The browser greys a running lane
# (`.time-running`) and flashes the split from white down to the time colour when it
# locks (`time-lock-flash`); without that the two are indistinguishable.

def test_a_running_lane_is_greyed(board, qt_app):
    from scoreboard.board import _TIME_RUNNING
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    assert _TIME_RUNNING in board.rows[0].time_label.styleSheet().lower()
    # A lane that never started keeps the plain time colour.
    assert board.cfg.color('time') in board.rows[1].time_label.styleSheet()


def test_locking_a_split_flashes_it_back_to_the_time_colour(board, qt_app):
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    board.apply_update({'lane_running1': False, 'lane_time1': '28.41'})
    qt_app.processEvents()

    anim = board.rows[0]._time_anim
    assert anim is not None, 'the split locked without a flash'
    assert anim.duration() == 800
    anim.setCurrentTime(anim.duration())
    assert board.cfg.color('time').lower() in \
        board.rows[0].time_label.styleSheet().lower()


def test_rejoining_the_clock_cancels_a_flash_in_flight(board, qt_app):
    """A lane pauses at every wall, so the flash and the next length can overlap.

    Nothing coordinates the console's hold with the 800ms flash, so the lane can
    rejoin the clock with one still in flight.
    """
    from scoreboard.board import _TIME_RUNNING
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    board.apply_update({'lane_running1': False, 'lane_time1': '28.41'})
    qt_app.processEvents()
    assert board.rows[0]._time_anim is not None

    board.apply_update({'lane_running1': True})
    qt_app.processEvents()
    assert board.rows[0]._time_anim is None, 'the flash outlived the pause'
    assert _TIME_RUNNING in board.rows[0].time_label.styleSheet().lower()


def test_a_restyle_keeps_the_running_grey(board, qt_app):
    """`apply_theme` runs on every /config reload, mid-race included."""
    from scoreboard.board import _TIME_RUNNING
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    board.rows[0].apply_theme()
    assert _TIME_RUNNING in board.rows[0].time_label.styleSheet().lower()


def test_the_next_heat_clears_the_running_grey(board, qt_app):
    """The browser's `reset_times()`, called as the new start list is painted."""
    from scoreboard.board import _TIME_RUNNING
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    board.rows[0].clear()
    assert _TIME_RUNNING not in board.rows[0].time_label.styleSheet().lower()
    assert board.cfg.color('time') in board.rows[0].time_label.styleSheet()


# ── Tenths, not hundredths ─────────────────────────────────────────────────────
# The console reports its own clock to tenths while a race is on — a CTS blanks the
# hundredths digit and `_time_str` reads the blank back as a zero — so the last
# digit on a *running* clock was the board's interpolation alone, changing twenty
# times a second under the one number the whole hall is watching. The browser never
# had the problem: `/live` writes the console's own string into the lane cells, and
# the phone board formats to tenths outright (`formatTenths` in scoreboard_base).

def test_the_running_clock_shows_a_still_last_digit(board, qt_app):
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    seen = set()
    for _ in range(12):
        _pump(qt_app, 0.05)
        seen.add(board.chrono_label.text())
    assert len(seen) > 1, 'the clock did not run at all'
    for text in seen:
        assert text.endswith('0'), f'{text!r} — the hundredths digit is still moving'


def test_every_running_lane_shows_the_same_still_digit(board, qt_app):
    board.apply_update({'running_time': '12.30',
                        'lane_running1': True, 'lane_running2': True})
    _pump(qt_app, 0.2)
    assert board.rows[0].time_label.text() == board.chrono_label.text()
    assert board.rows[1].time_label.text() == board.chrono_label.text()
    assert board.chrono_label.text().endswith('0')


def test_the_console_frame_is_rounded_too(board, qt_app):
    """A console that does report hundredths while running would otherwise make the
    figure jump between its value and the ticker's zero — the flicker, with a step."""
    board.apply_update({'running_time': '12.37', 'lane_running1': True})
    qt_app.processEvents()
    assert board.chrono_label.text() == '12.30'


def test_a_finished_time_keeps_its_hundredths(board, qt_app):
    """Only the *running* clock is rounded. A split or a final time is the console's
    own figure and is written straight through — that precision is the whole point
    of the sport."""
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    board.apply_update({'lane_running1': False, 'lane_time1': '1:02.47'})
    qt_app.processEvents()
    assert board.rows[0].time_label.text() == '1:02.47'


def test_the_width_never_changes_as_it_runs(board, qt_app):
    """Two digits either way. A figure that narrows by a character when it stops
    would shift everything around it — worse than the flicker it replaced."""
    board.apply_update({'running_time': '9.30', 'lane_running1': True})
    _pump(qt_app, 0.15)
    running = board.chrono_label.text()
    assert len(running.split('.')[1]) == 2, f'{running!r} dropped a digit'
