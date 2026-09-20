"""The test-session badge.

A recorded session replays real console traffic, so the board looks exactly like a
live race — the operator is watching it to check the board. The badge therefore
has to be unmissable *without* covering anything: the browser draws a small pill
at the bottom centre (`.test-overlay` in timing_display.css) and so do we.

This started as a bug: `test_mode` reused the full-screen status overlay, which is
opaque, so starting a test hid the entire scoreboard behind the words TEST SESSION.
"""
import os
import sys
from typing import cast

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import state                                  # noqa: E402
from scoreboard.theme import DEFAULT_STRINGS, Config   # noqa: E402


# ── Qt-free ────────────────────────────────────────────────────────────────────

def test_the_badge_text_is_a_display_string():
    assert 'test_session' in DEFAULT_STRINGS


@pytest.mark.parametrize('code', ['en', 'fr', 'es'])
def test_every_locale_translates_it(code):
    strings = state.display_strings(code)
    assert strings.get('test_session'), f'{code} has no test_session'


def test_the_server_ships_it_in_config(monkeypatch):
    import web
    monkeypatch.setitem(state.settings, 'locale', 'fr')
    cfg = Config(web.display_config())
    assert cfg.strings['test_session'].startswith('⚠')
    assert 'TEST' in cfg.strings['test_session'].upper()


# ── Qt ─────────────────────────────────────────────────────────────────────────

pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')

from scoreboard.board import BoardWindow      # noqa: E402


@pytest.fixture
def board(qt_app):
    window = BoardWindow(Config({
        'num_lanes': 6,
        'display_strings': {'test_session': '⚠ SESSION DE TEST'}}))
    window.resize(1920, 1080)
    window.show()
    qt_app.processEvents()
    yield window
    window.stop_clock()
    window.close()


def test_the_badge_appears_and_disappears(board, qt_app):
    assert not board.test_badge.isVisible()

    board.set_test_mode(True)
    qt_app.processEvents()
    assert board.test_badge.isVisible()
    assert board.test_badge.text() == '⚠ SESSION DE TEST', 'should use the locale string'

    board.set_test_mode(False)
    qt_app.processEvents()
    assert not board.test_badge.isVisible()


def test_it_does_not_cover_the_board(board, qt_app):
    """The whole point. The status overlay is opaque and full-screen; the badge
    must not be that, or a test session hides the thing being tested."""
    board.apply_update({'lane_name1': 'Roy, Zoé', 'lane_time1': '1:12.44'})
    board.set_test_mode(True)
    qt_app.processEvents()

    assert not board.status_box.isVisible(), 'the full-screen overlay must stay down'
    assert board.rows[0].name_label.isVisible()
    assert board.rows[0].name_label.text() == 'Roy, Zoé'

    badge = board.test_badge.geometry()
    assert badge.height() < board.height() * 0.1, 'badge is a pill, not a curtain'
    assert badge.width() < board.width() * 0.5


def test_it_sits_at_the_bottom_centre(board, qt_app):
    """Matching `.test-overlay`'s `bottom: 2.5vh; left: 50%` in the browser."""
    board.set_test_mode(True)
    qt_app.processEvents()
    badge = board.test_badge.geometry()

    assert abs(badge.center().x() - board.width() // 2) <= 2, 'not centred'
    assert badge.bottom() < board.height(), 'runs off the bottom of the screen'
    assert badge.top() > board.height() * 0.8, 'not near the bottom'


def test_it_follows_a_resize(board, qt_app):
    board.set_test_mode(True)
    qt_app.processEvents()
    board.resize(3840, 2160)
    qt_app.processEvents()

    badge = board.test_badge.geometry()
    assert abs(badge.center().x() - board.width() // 2) <= 2
    assert badge.top() > board.height() * 0.8


def test_it_falls_back_when_the_server_sends_no_string(qt_app):
    """A server older than this key must not produce a blank badge."""
    window = BoardWindow(Config({'num_lanes': 4}))
    window.resize(1920, 1080)
    window.show()
    try:
        window.set_test_mode(True)
        qt_app.processEvents()
        assert window.test_badge.text() == DEFAULT_STRINGS['test_session']
    finally:
        window.close()


def test_a_config_reload_does_not_shrink_the_badge(board, qt_app):
    """`apply_theme` assigns a fresh QFont, and a QFont carries a size.

    The badge is a plain QLabel sized from the window in `_place_test_badge`, so
    unlike a FitLabel it cannot re-fit itself — the size has to survive the restyle.
    A reload while a test session is running used to drop it to ~13px.
    """
    board.set_test_mode(True)
    qt_app.processEvents()
    before = board.test_badge.font().pixelSize()
    assert before > 0

    board.set_config(Config({
        'num_lanes': 6,
        'display_strings': {'test_session': '⚠ SESSION DE TEST'}}))
    qt_app.processEvents()

    assert board.test_badge.font().pixelSize() == before, 'the badge shrank'
    assert board.test_badge.font().bold(), 'and it must stay bold'


# ── Link-lost badge ────────────────────────────────────────────────────────────
# A drop mid-meet must not raise the full-screen status overlay: the board is
# holding a real start list or real results, and covering those for a two-second
# blip is worse than the blip. The badge says "do not trust this as live" while
# leaving the information on screen.

def test_a_drop_does_not_cover_the_board(board, qt_app):
    board.set_link_lost(True, '⚠ CONNEXION PERDUE · 5 s')
    qt_app.processEvents()
    assert board.link_badge.isVisible()
    assert not board.status_box.isVisible(), 'the board was covered'


def test_the_two_badges_never_collide(board, qt_app):
    """A recorded session can drop its link like any other."""
    board.set_test_mode(True)
    board.set_link_lost(True, '⚠ CONNEXION PERDUE · 5 s')
    qt_app.processEvents()
    assert board.test_badge.isVisible() and board.link_badge.isVisible()
    assert not board.link_badge.geometry().intersects(board.test_badge.geometry())


def test_the_link_badge_clears_the_header(board, qt_app):
    """Top centre, but under the bar — event, heat and the clocks are exactly what
    an official still wants to read while the link is down."""
    board.set_link_lost(True, '⚠ CONNEXION PERDUE · 5 s')
    qt_app.processEvents()
    assert board.link_badge.geometry().top() >= board.header.height()


def test_the_clock_freezes_instead_of_inventing_a_time(board, qt_app):
    """The ticker interpolates between console frames, so left running it counts up
    off a base that stopped arriving — a confident, fabricated race time."""
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    assert board._clock_timer.isActive()
    frozen = board.chrono_label.text()

    board.set_link_lost(True)
    qt_app.processEvents()
    assert not board._clock_timer.isActive(), 'the clock kept running on a dead link'
    assert board.chrono_label.text() == frozen, 'the last figure must stay on screen'
    assert board.rows[0].running, 'the console said this lane is swimming; keep it'


def test_the_frozen_clock_is_tinted(board, qt_app):
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    board.set_link_lost(True)
    assert board.cfg.color('connection_lost') in board.chrono_label.styleSheet()
    assert board.cfg.color('connection_lost') in board.rows[0].time_label.styleSheet()

    board.set_link_lost(False)
    assert board.cfg.color('connection_lost') not in board.chrono_label.styleSheet()


def test_a_restyle_during_an_outage_keeps_the_tint(board, qt_app):
    """`/config` is plain HTTP and can answer while the WebSocket is still down."""
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    board.set_link_lost(True)
    qt_app.processEvents()

    board.set_config(Config({'num_lanes': 6}))
    qt_app.processEvents()
    assert board.cfg.color('connection_lost') in board.chrono_label.styleSheet(), 'reload repainted it as live'


def test_repeated_drop_reports_do_not_strobe_the_badge(board, qt_app):
    """The reconnect loop emits `connected(False)` on every failed attempt."""
    board.set_link_lost(True, '⚠ CONNEXION PERDUE · 5 s')
    qt_app.processEvents()
    board.set_link_lost(True)            # no detail — must leave the badge alone
    qt_app.processEvents()
    assert board.link_badge.isVisible()
    assert board.link_badge.text() == '⚠ CONNEXION PERDUE · 5 s'


def test_reconnecting_clears_the_badge_and_lets_the_clock_resume(board, qt_app):
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    board.set_link_lost(True, '⚠ CONNEXION PERDUE · 5 s')
    qt_app.processEvents()

    board.set_link_lost(False)
    qt_app.processEvents()
    assert not board.link_badge.isVisible()
    # Still stopped: `_clock_base` is stale, so resuming before the next frame
    # would make the clock jump. The frame below re-bases it.
    assert not board._clock_timer.isActive()

    board.apply_update({'running_time': '19.80'})
    qt_app.processEvents()
    assert board._clock_timer.isActive(), 'the clock never picked up again'


def test_the_badge_text_has_its_own_swatch(qt_app):
    """The pill is a warning colour, not a board colour, so whatever reads well on
    it is not necessarily the background the test-session pill borrows."""
    window = BoardWindow(Config({
        'num_lanes': 6,
        'theme_colors': {'bg': '#0d0d0d',
                         'connection_lost': '#ef5350',
                         'connection_lost_text': '#00ffcc'},
        'display_strings': {'test_session': '⚠ SESSION DE TEST'}}))
    window.resize(1920, 1080)
    window.show()
    qt_app.processEvents()
    try:
        window.set_link_lost(True, '⚠ CONNEXION PERDUE · 5 s')
        qt_app.processEvents()
        css = window.link_badge.styleSheet()
        assert 'color: #00ffcc' in css, 'the text swatch never reached the badge'
        assert 'rgba(239,83,80,0.75)' in css, 'the pill must still be the warning colour'

        # The test-session pill is unaffected: it keeps punching out of the board.
        window.set_test_mode(True)
        qt_app.processEvents()
        assert 'color: #0d0d0d' in window.test_badge.styleSheet()
    finally:
        window.close()


def test_the_badge_text_defaults_to_the_board_background(qt_app):
    """Out of the box nothing changes — the swatch is there to be tuned, not to
    make a fresh install look different."""
    import state
    assert (state.DEFAULT_THEME_COLORS['connection_lost_text']
            == state.DEFAULT_THEME_COLORS['bg'])


# ── Starting a session wipes the board ─────────────────────────────────────────
# `live.html` does `reset_state(); mode_to_intro()` on `test_mode {active: true}`,
# and it matters more here than there: a recording replays the same event and heat
# on every run, so on a re-run the heat key never changes, the dissolve never
# fires, and nothing else would clear what the last run left behind.

class _FakeApp:
    """Just enough of ScoreboardApp for `_on_frame` — no socket, no config fetch.

    Building a real one needs a listening server (see test_scoreboard_splash.py);
    this path only touches `self.window`.
    """

    def __init__(self, window):
        self.window = window


def _finish_a_heat(board, qt_app, event='1', heat='1'):
    board.apply_update({'current_event': event, 'current_heat': heat,
                        'lane_name1': 'Roy, Zoé', 'lane_name2': 'Côté, Léa'})
    board.apply_update({'running_time': '0.00',
                        'lane_running1': True, 'lane_running2': True})
    qt_app.processEvents()
    board.apply_update({'lane_running1': False, 'lane_running2': False,
                        'lane_time1': '58.12', 'lane_place1': '1',
                        'lane_time2': '59.03', 'lane_place2': '2'})
    board.cancel_heat_transition()
    qt_app.processEvents()


def test_the_board_is_wiped_when_a_session_starts(board, qt_app, settle_podium):
    from scoreboard.app import ScoreboardApp

    _finish_a_heat(board, qt_app)
    settle_podium(board)
    assert board.rows[0].time_label.text() == '58.12'

    ScoreboardApp._on_frame(cast(ScoreboardApp, _FakeApp(board)), 'test_mode', {'active': True})
    qt_app.processEvents()

    assert board.test_badge.isVisible(), 'the badge must still go up'
    assert board.snapshot == {}, 'the previous run is still in the merged state'
    for row in board.rows:
        assert row.name_label.text() == ''
        assert row.time_label.text() == ''
        assert row.place_label.text() == ''
        assert row._current_bg.lower() == row._base_bg.lower(), 'a tint survived'
    assert board._heat_key is None, 'the replay re-runs this heat — it must look new'
    assert not board._podium_shown


def test_stopping_a_session_leaves_the_board_alone(board, qt_app):
    """Only `active: true` resets, as in the browser. The last heat of a recording
    is the operator's evidence that the replay worked; wiping it on stop would
    take that away."""
    from scoreboard.app import ScoreboardApp

    _finish_a_heat(board, qt_app)
    ScoreboardApp._on_frame(cast(ScoreboardApp, _FakeApp(board)), 'test_mode', {'active': False})
    qt_app.processEvents()

    assert not board.test_badge.isVisible()
    assert board.rows[0].time_label.text() == '58.12', 'the results were wiped on stop'
