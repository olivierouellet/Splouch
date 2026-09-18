"""Lap counts: from the wire, through the relay, onto both boards.

A lap count is the one number on the board that no console is obliged to send. Two
of the five report it natively (Quantum, Omnisport), the CTS Gen6 *infers* it from
touchpad stops, and the Gen7 and ARES 21 have nothing to say about it at all. That
spread is the whole reason these tests exist:

* Every decoder has to answer `adjust_splits`, because
  `worker._worker_adjust_splits` calls it blind. It used to exist on two decoders out
  of six, and the other four surfaced only as an `AttributeError` traceback on the
  worker thread — the operator's ± buttons just did nothing.
* Every decoder has to blank `lane_splits{n}` in `reset_lanes()`, whether or not it
  ever raises the count, or a number survives a heat change.
* `split_step` belongs to the **pool**, not the console: a swimmer who does not
  touch a pad is invisible to every console on the market, so pads at one end mean
  four observations in a 200m whatever brand is on the deck. Everything that asks
  "is this the last length?" is `splits + step >= expected`, so a board that assumed
  1 would never pulse on the very setup that needs it most.
* The delta column has two tenants and one writer. Both boards render it the same
  way — see `notes/scoreboard_parity.md` — and the Qt board is a third
  implementation of the same rule.
"""
import os
import re
import sys

import pytest
from jinja2 import Environment, FileSystemLoader

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import state                                          # noqa: E402
from console_decoders import DECODERS, make_decoder    # noqa: E402
from console_decoders.utils import split_step          # noqa: E402
from jsc import HAS_JSC, run_page                      # noqa: E402

_ALL = sorted(DECODERS)


# ── The decoder contract ───────────────────────────────────────────────────────

@pytest.mark.parametrize('key', _ALL)
def test_every_decoder_answers_adjust_splits(key):
    """`_worker_adjust_splits` calls this on whatever console is configured."""
    dec = make_decoder(key, {'num_lanes': 8})
    value = dec.adjust_splits(1, 2)
    assert isinstance(value, int)
    assert value >= 0


@pytest.mark.parametrize('key', _ALL)
def test_reset_lanes_blanks_the_lap_count(key):
    """A heat change takes the previous heat's lengths off the board."""
    dec = make_decoder(key, {'num_lanes': 8})
    updates = dec.reset_lanes()
    for lane in range(1, 9):
        assert updates.get(f'lane_splits{lane}') == 0, f'lane {lane} keeps its laps'


@pytest.mark.parametrize('key', ['omega_quantum', 'dak_2000', 'cts_gen6'])
def test_counting_decoders_really_move_the_count(key):
    """Where there is a count to move, ± has to move it — and never below zero.

    Not the whole registry: a decoder that reports no lap number has nothing to
    correct, and the base class's no-op is the honest answer there.
    """
    dec = make_decoder(key, {'num_lanes': 8})
    dec.reset_lanes()
    assert dec.adjust_splits(3, 2) == 2
    assert dec.adjust_splits(3, 1) == 3
    assert dec.adjust_splits(3, -1) == 2
    assert dec.adjust_splits(3, -99) == 0, 'a lap count cannot go negative'
    assert dec.adjust_splits(4, 1) == 1, 'lanes are counted separately'


def test_one_sided_touchpads_count_in_twos():
    """Pads at one end mean the swimmer is only *seen* every second length.

    This is what `splits + split_step >= expected_splits` is for: with a step of 2
    the count goes 2, 4, 6 and never equals `expected - 1`, so a board testing
    `+ 1` would never find the last length.
    """
    assert split_step(1) == 2
    assert split_step(2) == 1


def test_the_step_is_the_pool_not_the_console():
    """A property of the venue, so it cannot vary by brand.

    A swimmer who does not touch a pad is invisible to every console on the market;
    there are four observations in a 200m in a one-sided pool whatever is on the
    deck. When this lived on the decoder, a Quantum in that pool published a step of
    1 and its last length never pulsed.
    """
    assert not any(hasattr(make_decoder(key, {'num_lanes': 8}), 'split_step')
                   for key in _ALL), 'split_step must not be a decoder property'


def test_a_missing_or_odd_setting_reads_as_one_sided():
    """`touchpad_sides` ships as 1, and anything unset must not read as 2-sided:
    over-counting the last length is a pulse one length early, under-counting is no
    pulse at all."""
    assert split_step(None) == 2
    assert split_step(0) == 2
    assert split_step('1') == 2
    assert split_step('2') == 1


def test_the_gen6_counts_in_the_same_step_it_publishes():
    """The inference and the published value are one function, so the count the
    board receives and the test it applies to it cannot disagree."""
    dec = make_decoder('cts_gen6', {'num_lanes': 8, 'touchpad_sides': 1})
    dec.reset_lanes()
    assert dec.adjust_splits(1, split_step(1)) == 2


# ── The replay cache ───────────────────────────────────────────────────────────

def test_record_board_merges_partial_frames():
    """Frames are partial, so the cache is the only full picture of the board."""
    state.board.clear()
    try:
        state.record_board({'lane_time1': '31.44', 'lane_splits1': 2})
        state.record_board({'lane_splits1': 4})
        assert state.board['lane_time1'] == '31.44', 'an untouched key was dropped'
        assert state.board['lane_splits1'] == 4
    finally:
        state.board.clear()


def test_record_board_drops_the_clock():
    """A replayed clock is a frozen one, and a frozen clock is worse than none.

    The client re-bases on the next `running_time` it is sent, at most a tick away;
    until then the header is empty rather than confidently wrong. The cloud drops it
    from `last_scoreboard` for the same reason.
    """
    state.board.clear()
    try:
        state.record_board({'running_time': '1:02.31', 'lane_place1': '1'})
        assert 'running_time' not in state.board
        assert state.board['lane_place1'] == '1'
    finally:
        state.board.clear()


# ── The two browser boards ─────────────────────────────────────────────────────

_LABELS = {'event': 'Event', 'heat': 'Heat', 'lane': 'Lane', 'name': 'Name',
           'club': 'Club', 'time': 'Time', 'delta': 'Δ', 'place': '#'}
_FLAGS = {f'show_{k}': True for k in
          ('lane_header', 'name_header', 'club_header', 'time_header',
           'delta_header', 'position_header', 'name', 'club', 'delta', 'position',
           'podium')}


def _render(own_dir, template, **extra):
    env = Environment(loader=FileSystemLoader(
        [os.path.join(REPO, own_dir), os.path.join(REPO, 'shared', 'templates')]))
    env.globals['url_for'] = lambda name, **kw: '/static/' + kw.get('filename', '')
    return env.get_template(template).render(
        num_lanes=6, labels=_LABELS,
        theme_colors=state.DEFAULT_THEME_COLORS,
        theme_fonts=state.DEFAULT_THEME_FONTS,
        **_FLAGS, **extra)


@pytest.fixture(scope='module')
def phone():
    return _render('server/templates', 'live-mobile.html', show_laps=True)


@pytest.fixture(scope='module')
def kiosk():
    return _render('server/templates', 'live.html', meet_title='Coupe',
                   show_laps=True, nosplash=True, test_background=False,
                   carousel_images=[], carousel_interval=10)


@pytest.fixture(scope='module')
def boards(phone, kiosk):
    return {'phone': phone, 'kiosk': kiosk}


@pytest.mark.parametrize('fn', ['renderDelta', 'lapVisible', 'lapIsLast'])
def test_both_boards_carry_the_same_renderer(boards, fn):
    """The kiosk predates the shared base and keeps its own frame handler, so the
    lap rule exists twice. Neither copy may quietly lose a piece of it."""
    for name, html in boards.items():
        assert f'function {fn}(' in html, f'{name} has no {fn}'


def test_the_delta_cell_has_exactly_one_writer(boards):
    """`lane_delta<i>` must not be in the direct-to-DOM set.

    Both boards write every key in `VALID_FIELDS` straight to the element of the
    same id. The delta cell is shared with the lap count, so a direct write would
    race `renderDelta` — and leave the lap's colour class on a cell now showing a
    time difference.
    """
    for name, html in boards.items():
        start = html.index('var VALID_FIELDS')
        valid = html[start:html.index(']);', start)]   # the set literal alone
        assert 'lane_place' in valid, f'{name}: wrong block matched'
        assert 'lane_delta' not in valid, f'{name}: the delta cell has two writers'


def test_the_pulse_test_uses_the_step_not_one(boards):
    """`splits + split_step >= expected_splits`, never `splits + 1`.

    The one-sided-touchpad count moves in twos, so a `+ 1` test never fires on the
    setup where the last length is hardest to judge from the deck.
    """
    for name, html in boards.items():
        body = html[html.index('function lapIsLast('):]
        body = body[:body.index('\n}')]
        assert 'split_step' in body, f'{name} ignores the step'
        assert 'expected_splits' in body, f'{name} ignores the expected count'


def test_a_place_ends_the_lap(boards):
    """Not the delta: a swimmer with no seed time never gets one, and the lap would
    sit under their finished swim for the rest of the heat."""
    for name, html in boards.items():
        body = html[html.index('function lapVisible('):]
        body = body[:body.index('\n}')]
        assert 'lane_place_blank' in body, f'{name} does not end the lap on a place'
        assert 'SHOW_LAPS' in body, f'{name} ignores the setting'


def test_laps_are_off_unless_the_setting_says_otherwise():
    """The Gen6 count is inferred, so nothing goes on a public board by default."""
    assert state.settings['show_laps'] is False
    for template, own in (('live-mobile.html', 'server/templates'),
                          ('live.html', 'server/templates')):
        extra = {} if template == 'live-mobile.html' else dict(
            meet_title='Coupe', nosplash=True, test_background=False,
            carousel_images=[], carousel_interval=10)
        html = _render(own, template, **extra)   # no show_laps passed at all
        assert re.search(r'var SHOW_LAPS\s*=\s*false', html), template


# ── The rule, actually executed ────────────────────────────────────────────────
# Everything above this line matches source. These render the real page, run its
# scripts under JavaScriptCore against the stub DOM (`tests/jsc.py`) and drive the
# real frame handler, so what is asserted is what the cell ends up holding.
#
# Classes are not asserted: the stub's `classList` is a no-op by design. The text in
# the cell is the substance — which of the two tenants has it.

_DRIVE = r'''
function assert(ok, msg) { if (!ok) throw new Error(msg); }
function cell(i) { return document.getElementById('lane_delta' + i).textContent
                       || document.getElementById('lane_delta' + i).innerHTML; }
'''


def _drive(html, script):
    """Append a script to the rendered page and run the whole thing."""
    return run_page(html.replace('</body>', '<script>%s\n%s</script></body>'
                                 % (_DRIVE, script)))


@pytest.mark.skipif(not HAS_JSC, reason='needs JavaScriptCore via osascript (macOS)')
def test_the_lap_appears_while_the_lane_swims(phone):
    """Two lengths down, none of them finished: the cell carries the count."""
    _drive(phone, r'''
    applyScoreboardFrame({current_event: '5', current_heat: '1',
                          expected_splits: 8, split_step: 1});
    applyScoreboardFrame({lane_splits1: 2, lane_running1: true});
    assert(cell(1) === '2', 'expected the lap count, got ' + JSON.stringify(cell(1)));
    ''')


@pytest.mark.skipif(not HAS_JSC, reason='needs JavaScriptCore via osascript (macOS)')
def test_the_delta_takes_the_cell_back_at_the_finish(phone):
    """The swap this whole design is built around, in the order a console sends it:
    the place lands on the touch, the delta a frame or two later."""
    _drive(phone, r'''
    applyScoreboardFrame({current_event: '5', current_heat: '1',
                          expected_splits: 8, split_step: 1});
    applyScoreboardFrame({lane_splits1: 6, lane_running1: true});
    assert(cell(1) === '6', 'lap should be showing, got ' + JSON.stringify(cell(1)));

    applyScoreboardFrame({lane_place1: '1', lane_time1: '1:02.31',
                          lane_running1: false});
    assert(cell(1) === '', 'a placed lane must not keep its lap: ' + JSON.stringify(cell(1)));

    applyScoreboardFrame({lane_delta1: '<span class="delta-better">-1.20</span>'});
    assert(cell(1).indexOf('-1.20') >= 0, 'the delta never arrived: ' + JSON.stringify(cell(1)));
    ''')


@pytest.mark.skipif(not HAS_JSC, reason='needs JavaScriptCore via osascript (macOS)')
def test_zero_lengths_shows_nothing(phone):
    """`lane_splits{n} = 0` is every lane at the top of every heat. A board that
    rendered it would put a column of noughts under a start list."""
    _drive(phone, r'''
    applyScoreboardFrame({current_event: '5', current_heat: '1',
                          expected_splits: 8, split_step: 1, lane_splits1: 0});
    assert(cell(1) === '', 'a zero lap count was rendered: ' + JSON.stringify(cell(1)));
    ''')


@pytest.mark.skipif(not HAS_JSC, reason='needs JavaScriptCore via osascript (macOS)')
def test_the_setting_off_means_no_lap_at_all():
    """The default. The frame still carries the count — /operator and /console use
    it — so the board has to be the thing that declines to draw it."""
    off = _render('server/templates', 'live-mobile.html', show_laps=False)
    _drive(off, r'''
    applyScoreboardFrame({current_event: '5', current_heat: '1',
                          expected_splits: 8, split_step: 1});
    applyScoreboardFrame({lane_splits1: 3, lane_running1: true});
    assert(cell(1) === '', 'laps drawn with the setting off: ' + JSON.stringify(cell(1)));
    ''')


@pytest.mark.skipif(not HAS_JSC, reason='needs JavaScriptCore via osascript (macOS)')
def test_a_heat_change_takes_the_lap_off_the_board(phone):
    """`reset_lanes()` sends `lane_splits{n} = 0` with the new heat, and the cell has
    to follow it back to empty — the previous heat's lengths are not this one's."""
    _drive(phone, r'''
    applyScoreboardFrame({current_event: '5', current_heat: '1',
                          expected_splits: 8, split_step: 1});
    applyScoreboardFrame({lane_splits1: 4, lane_running1: true});
    assert(cell(1) === '4', 'setup failed: ' + JSON.stringify(cell(1)));

    applyScoreboardFrame({current_event: '5', current_heat: '2',
                          expected_splits: 8, split_step: 1,
                          lane_splits1: 0, lane_time1: '', lane_place1: ' ',
                          lane_running1: false, lane_delta1: ''});
    assert(cell(1) === '', 'the previous heat kept its lap: ' + JSON.stringify(cell(1)));
    ''')


def test_the_lap_colour_is_shared_between_the_boards():
    """One CSS file, so the kiosk and the phone cannot drift on what a lap looks
    like — the row's own colour, and the timing colour on the last length."""
    css = open(os.path.join(REPO, 'shared', 'static', 'css',
                            'timing_display.css')).read()
    assert '.td_delta.lap-count' in css
    assert '@keyframes lap-last-pulse' in css
    assert 'var(--color-row-text)' in css[css.index('.td_delta.lap-count'):]


# ── The Qt board ───────────────────────────────────────────────────────────────
# A third implementation of the same rule, on the display that actually hangs over
# the pool. `notes/scoreboard_parity.md` is the contract between the three; these
# assert the Qt half of it against real widgets.

@pytest.fixture
def qt_board(qt_app):
    pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')
    from scoreboard.board import BoardWindow
    from scoreboard.theme import Config
    window = BoardWindow(Config({'num_lanes': 6, 'show_laps': True}))
    window.resize(1920, 1080)
    window.show()
    qt_app.processEvents()
    yield window
    window.stop_clock()
    window.close()


def _delta_text(board, lane=1):
    return board.rows[lane - 1].delta_label.text()


def test_qt_shows_the_lap_while_the_lane_swims(qt_board, qt_app):
    qt_board.apply_update({'current_event': '5', 'current_heat': '1',
                           'expected_splits': 8, 'split_step': 1})
    qt_board.apply_update({'lane_splits1': 2, 'lane_running1': True})
    qt_app.processEvents()
    assert _delta_text(qt_board) == '2'
    assert qt_board.rows[0]._lap_shown


def test_qt_hands_the_cell_back_at_the_finish(qt_board, qt_app):
    """The place ends the lap, and the delta then owns both the text and the colour
    — `_style_delta` must not still be short-circuited by a stale `_lap_shown`."""
    qt_board.apply_update({'current_event': '5', 'current_heat': '1',
                           'expected_splits': 8, 'split_step': 1})
    qt_board.apply_update({'lane_splits1': 6, 'lane_running1': True})
    qt_app.processEvents()
    assert _delta_text(qt_board) == '6'

    qt_board.apply_update({'lane_place1': '1', 'lane_time1': '1:02.31',
                           'lane_running1': False,
                           'lane_delta_seconds1': -1.2, 'lane_delta_better1': True})
    qt_app.processEvents()
    assert not qt_board.rows[0]._lap_shown
    assert '1.20' in _delta_text(qt_board)
    better = qt_board.cfg.color('delta_better')
    assert better in qt_board.rows[0].delta_label.styleSheet()


def test_qt_pulses_only_on_the_last_length(qt_board, qt_app):
    """`+ split_step`, not `+ 1`: with two-a-side counting the pulse belongs on 6
    of 8, because the next touch is the finish."""
    qt_board.apply_update({'current_event': '5', 'current_heat': '1',
                           'expected_splits': 8, 'split_step': 2})
    qt_board.apply_update({'lane_splits1': 4, 'lane_running1': True})
    qt_app.processEvents()
    assert qt_board.rows[0].lap_for(qt_board.snapshot) == (4, False)

    qt_board.apply_update({'lane_splits1': 6})
    qt_app.processEvents()
    assert qt_board.rows[0].lap_for(qt_board.snapshot) == (6, True)
    assert qt_board.rows[0]._lap_anim is not None, 'the last length does not pulse'


def test_qt_draws_no_lap_with_the_setting_off(qt_app):
    from scoreboard.board import BoardWindow
    from scoreboard.theme import Config
    window = BoardWindow(Config({'num_lanes': 6}))       # show_laps defaults off
    try:
        window.apply_update({'current_event': '5', 'current_heat': '1',
                             'expected_splits': 8, 'split_step': 1})
        window.apply_update({'lane_splits1': 3, 'lane_running1': True})
        qt_app.processEvents()
        assert window.rows[0].delta_label.text() == ''
    finally:
        window.stop_clock()
        window.close()


def test_qt_drops_the_lap_on_a_heat_change(qt_board, qt_app):
    """`_drop_stale_timing` has to forget `lane_splits` too, or the next heat's
    start list carries the previous heat's lengths until a lane moves."""
    qt_board.apply_update({'current_event': '5', 'current_heat': '1',
                           'expected_splits': 8, 'split_step': 1})
    qt_board.apply_update({'lane_splits1': 4, 'lane_running1': True})
    qt_app.processEvents()
    assert _delta_text(qt_board) == '4'

    qt_board.apply_update({'current_event': '5', 'current_heat': '2'})
    qt_app.processEvents()
    assert 'lane_splits1' not in qt_board.snapshot
