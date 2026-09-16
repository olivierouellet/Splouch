"""Running a meet with no timing console at all.

Every decoder learns the current event and heat off a wire, and `last_event_sent` is
what puts names, the event name, the heat time and the next-heats list on every
board. A club time trial with no console, or a console whose serial cable never
turned up, had no way to set it — so the boards stayed blank however good the Lenex
file was. The manual console is a decoder that opens no port and takes that one
number from the operator instead, over /manual.

Three things here are load-bearing beyond the new page:

* `heat_order()` is the first thing in the app that knows both meet formats. The two
  functions that wanted it had each grown half: `_get_next_heats` read `start_list`
  and returned nothing for a Hytek CSV, while `_worker_next_heat` read
  `event_info.events` and was blind to Lenex — the format most clubs actually use.
* A heat change goes through `_on_event_changed`, the same path a console's
  `event_changed` takes, not the smaller-looking `send_event_info()`.
* `state._decoder` is built at import, before `load_settings()` has read
  settings.json, so the saved console type never survived a restart. Harmless while
  every console was a serial one; fatal for a console whose whole point is that it
  has no port.

Qt-free and socket-free: the worker functions are driven directly, on the calling
thread, which is where they run in production anyway (the decoder has one owner).
"""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import meet_data                            # noqa: E402
import state                                # noqa: E402
import worker                               # noqa: E402
from console_decoders import (CONSOLE_OPTIONS, DECODERS,  # noqa: E402
                              console_info_for, make_decoder)
from console_decoders.base import ConsoleDecoder          # noqa: E402
from console_decoders.manual import ManualDecoder         # noqa: E402
from meet_parsers.hytek_parser import HytekParser         # noqa: E402
from meet_parsers.lenex_parser import load_lenex          # noqa: E402

LENEX = os.path.join(REPO, 'tests', 'fixtures', 'splash.lxf')


@pytest.fixture
def rig(monkeypatch):
    """A manual console, the sample Lenex meet, and every emit captured."""
    monkeypatch.setattr(state, 'meet', state._Meet(), raising=False)
    state.set_lenex(load_lenex(LENEX))
    monkeypatch.setattr(state, 'meet', state.meet, raising=False)

    decoder = make_decoder('manual', {**state.settings, 'num_lanes': 8})
    monkeypatch.setattr(state, '_decoder', decoder, raising=False)

    emitted = []
    monkeypatch.setattr(worker.bus, 'emit',
                        lambda ch, ev, d=None: emitted.append((ch, ev, d)))
    monkeypatch.setattr(meet_data.bus, 'emit',
                        lambda ch, ev, d=None: emitted.append((ch, ev, d)))
    monkeypatch.setattr(meet_data.relay, 'relay_emit', lambda *a, **k: None)
    monkeypatch.setattr(worker.relay, 'relay_emit', lambda *a, **k: None)
    monkeypatch.setattr(state, 'update', {}, raising=False)

    class Rig:
        pass
    rig = Rig()
    rig.decoder, rig.emitted = decoder, emitted
    return rig


def _boards(rig):
    """The scoreboard payloads, merged in order — what a board would end up showing."""
    out = {}
    for channel, event, data in rig.emitted:
        if channel == '/scoreboard' and event == 'update_scoreboard':
            out.update(data or {})
    return out


# ── The decoder contract every decoder must satisfy ────────────────────────────

@pytest.mark.parametrize('key', sorted(DECODERS))
def test_every_decoder_carries_what_the_app_layer_reads_off_it(key):
    """`last_event_sent` and `lane_seed_times` are required and declared by nothing.

    `send_event_info`, `_build_results_snapshot` and `_add_lane_deltas` all read them
    straight off `state._decoder`, but the ABC never mentions either, so a decoder can
    satisfy `ConsoleDecoder` in full and still crash the server on its first packet.
    Pinned here for every decoder, present and future, rather than only the new one.
    """
    decoder = DECODERS[key]({**state.settings, 'num_lanes': 8})
    assert decoder.last_event_sent == (0, 0)
    assert decoder.lane_seed_times == {}
    assert isinstance(DECODERS[key].requires_serial, bool)
    # Off the class, not the instance: Settings asks before one is ever built.
    assert isinstance(getattr(DECODERS[key], 'requires_serial'), bool)


def test_only_the_manual_console_declares_itself_portless():
    assert ManualDecoder.requires_serial is False
    assert [k for k, c in DECODERS.items() if not c.requires_serial] == ['manual']


def test_the_manual_console_is_offered_and_described():
    assert 'manual' in {key for key, _, _ in CONSOLE_OPTIONS}
    info = console_info_for('manual')
    assert info['requires_serial'] is False
    assert console_info_for('cts_gen6')['requires_serial'] is True


def test_a_wired_decoder_leaves_liveness_to_the_packet_clock():
    """None, not False. False would declare every console dead."""
    assert ConsoleDecoder.is_live.fget(object()) is None
    assert make_decoder('cts_gen6', state.settings).is_live is None


# ── The decoder itself ─────────────────────────────────────────────────────────

def test_it_decodes_nothing_because_nothing_arrives():
    d = ManualDecoder(state.settings)
    assert d.feed([0x80, 0x01, 0x02]) == {}
    assert d.is_packet_start(0x80, []) is False
    assert d.get_lane_time(1) == ''
    assert d.get_lane_place(1) == ' '
    assert d.adjust_splits(1, 2) == 0


def test_the_race_is_never_finished_even_once_a_heat_is_on():
    """A hard False. A True with no timed lanes would publish an empty
    `results_snapshot` and blank the Results page instead of leaving it waiting."""
    d = ManualDecoder(state.settings)
    d.last_event_sent = (3, 1)
    d.set_seed_times({1: '1:02.30'})
    assert d.race_finished() is False


def test_resetting_lanes_clears_the_structured_delta_too():
    """`send_event_info` sends `lane_delta`, `lane_delta_seconds` and
    `lane_delta_better` together, so clearing only the first leaves a native client
    showing the previous heat's delta against this heat's swimmer."""
    u = ManualDecoder(state.settings).reset_lanes()
    for lane in (1, 8, 12):
        assert u[f'lane_time{lane}'] == ''
        assert u[f'lane_place{lane}'] == ' '
        assert u[f'lane_running{lane}'] is False
        assert u[f'lane_splits{lane}'] == 0
        assert u[f'lane_delta{lane}'] == ''
        assert u[f'lane_delta_seconds{lane}'] is None
        assert u[f'lane_delta_better{lane}'] is None


def test_it_reads_live_only_once_a_heat_is_on_the_boards():
    """The packet clock would call this link dead 8s in and never revive it, which
    sends /results to its waiting state for the whole meet — `next_heats` included."""
    d = ManualDecoder(state.settings)
    assert d.is_live is False          # (0, 0): nothing on the boards yet
    d.last_event_sent = (3, 1)
    assert d.is_live is True


# ── One running order, both meet formats ───────────────────────────────────────

def _hytek_meet(monkeypatch, events, names=None):
    """Publish a Hytek meet without going through the 107-column CSV layout.

    `heat_order` cares only about which (event, heat) keys the parser ended up
    holding, so building the parser directly keeps this test about the branch under
    test rather than about Meet Manager's export format.
    """
    parser = HytekParser()
    parser.events.update(events)
    parser.event_names.update(names or {})
    monkeypatch.setattr(state, 'meet', state._Meet(event_info=parser), raising=False)
    return parser


def test_the_running_order_is_the_same_for_lenex_and_hytek(rig, monkeypatch):
    assert meet_data.heat_order() == [(1, 1), (2, 1), (3, 1), (3, 2), (4, 1)]

    _hytek_meet(monkeypatch, {
        (2, 2): {1: 'CNQ  Ana Costa'},
        (1, 1): {1: 'CNQ  Sophie Tremblay', 2: 'CAMO Louis Gagné'},
        (2, 1): {1: 'CNQ  Marie Roy'},
    })
    # Running order, not the order the parser happened to see them in.
    assert meet_data.heat_order() == [(1, 1), (2, 1), (2, 2)]


def test_an_empty_meet_has_no_running_order(monkeypatch):
    monkeypatch.setattr(state, 'meet', state._Meet(), raising=False)
    assert meet_data.heat_order() == []
    assert meet_data.heat_step(0, 0, 1) is None
    assert meet_data.has_heat(1, 1) is False


def test_stepping_stops_at_both_ends_and_starts_from_nothing(rig):
    order = meet_data.heat_order()
    assert meet_data.heat_step(*order[0], 1) == order[1]
    assert meet_data.heat_step(*order[1], -1) == order[0]
    assert meet_data.heat_step(*order[0], -1) is None
    assert meet_data.heat_step(*order[-1], 1) is None
    # The (0, 0) sentinel, and a heat left over from another meet file: Next must
    # still do something useful rather than nothing at all.
    assert meet_data.heat_step(0, 0, 1) == order[0]
    assert meet_data.heat_step(999, 9, 1) == order[0]
    assert meet_data.heat_step(999, 9, -1) == order[-1]


def test_the_upcoming_heats_list_now_works_on_a_hytek_meet(monkeypatch):
    """It used to return [] for a CSV meet, so `/next_heats`, the Results page's
    upcoming list and the relay's `next_heats` were all empty on one."""
    _hytek_meet(monkeypatch,
                {(1, 1): {1: 'CNQ  Sophie Tremblay'},
                 (2, 1): {1: 'CAMO Louis Gagné'}},
                names={2: '100 Free'})

    upcoming = meet_data._get_next_heats(1, 1, n=3, num_lanes=1)
    assert [(h['event'], h['heat']) for h in upcoming] == [(2, 1)]
    # Through `get_lane_parts`, which splits the 4-char club prefix Hytek packs in.
    assert upcoming[0]['swimmers'][0]['name'] == 'Louis Gagné'
    assert upcoming[0]['swimmers'][0]['club'] == 'CAMO'


# ── Committing a heat ──────────────────────────────────────────────────────────

def test_setting_a_heat_loads_it_the_way_a_console_would(rig):
    """Not `send_event_info()`, which carries no heat time and no split count and
    leaves the previous heat's times on the board."""
    worker._worker_set_heat(1, 1)

    board = _boards(rig)
    assert board['current_event'] == '1'
    assert board['current_heat'] == '1'
    assert board['event_name']
    assert 'heat_time' in board
    assert 'expected_splits' in board
    assert board['lane_name1'] == 'Sophie Tremblay'
    assert board['lane_club1'] == 'CNQ'
    # The lanes are blanked, so nothing of the previous heat survives the change.
    assert board['lane_time1'] == ''
    assert board['lane_place1'] == ' '
    # And the upcoming list moved with it.
    assert any(ev == 'next_heats' for _, ev, _ in rig.emitted)


def test_setting_a_heat_cancels_a_debounce_left_by_the_console_before_it(rig):
    """`race_finished()` is always False here, so a True left behind by the console
    we just switched away from would never be cleared — and its pending board reset
    would land on top of the wipe this just did."""
    state._results_prev_race_finished = True
    state._running_lanes.add(4)
    gen = state._finish_timer_gen

    worker._worker_set_heat(2, 1)

    assert state._results_prev_race_finished is False
    assert state._running_lanes == set()
    assert state._finish_timer_gen > gen


def test_next_and_previous_walk_a_lenex_meet(rig):
    """The regression. `_worker_next_heat` read `meet.event_info.events` — the Hytek
    parser — so on any `.lxf` meet it fell through to the (0, 0) sentinel and blanked
    the board instead of advancing."""
    worker._worker_next_heat()
    assert rig.decoder.last_event_sent == (1, 1)
    worker._worker_next_heat()
    assert rig.decoder.last_event_sent == (2, 1)
    worker._worker_prev_heat()
    assert rig.decoder.last_event_sent == (1, 1)


def test_stepping_past_the_last_heat_changes_nothing(rig):
    worker._worker_goto_heat(4, 1)          # the last heat
    rig.emitted.clear()
    worker._worker_next_heat()
    assert rig.decoder.last_event_sent == (4, 1)
    assert rig.emitted == []


def test_a_heat_that_is_not_in_the_meet_is_ignored(rig):
    """`goto_heat` arrives over an unauthenticated LAN socket, so an unknown heat is
    dropped rather than published as an event number over eight blank lanes."""
    worker._worker_goto_heat(3, 2)
    rig.emitted.clear()
    worker._worker_goto_heat(999, 9)
    assert rig.decoder.last_event_sent == (3, 2)
    assert rig.emitted == []


# ── The worker opens no port ───────────────────────────────────────────────────

def test_the_manual_worker_never_opens_a_serial_port(rig, monkeypatch):
    """`_run_live_serial` would open `settings['serial_port']` and retry every 5s —
    and, worse, only drains the command queue once a port is actually open, so every
    button on /manual would do nothing."""
    def _boom(*a, **k):
        raise AssertionError('manual mode opened a serial port')
    monkeypatch.setattr(worker.serial, 'Serial', _boom)
    monkeypatch.setattr(state, '_test_session', None, raising=False)
    monkeypatch.setattr(state, '_serial_status', {}, raising=False)

    ran = []
    state._worker_cmds.put(lambda: ran.append('drained'))

    # One pass of the loop, then supersede it the way `_restart_worker` does.
    gen = state._worker_gen
    monkeypatch.setattr(worker.time, 'sleep',
                        lambda _s: monkeypatch.setattr(state, '_worker_gen', gen + 1,
                                                       raising=False))
    worker._run_manual(gen)

    assert ran == ['drained'], 'the manual worker did not drain queued commands'
    assert state._serial_status['state'] == 'idle'


def test_the_worker_picks_its_mode_off_the_decoder_not_the_settings_key(monkeypatch):
    """So a portless local-only plugin in ~/SplouchData/console_decoders/ gets the
    same treatment without this file knowing its name."""
    import inspect
    body = '\n'.join(line.split('#')[0] for line in
                     inspect.getsource(worker.main_thread_worker).splitlines())
    assert 'requires_serial' in body
    assert "'manual'" not in body, 'the worker is matching on the settings key'


# ── The saved console survives a restart ───────────────────────────────────────

def test_the_saved_console_is_the_one_that_gets_built(monkeypatch):
    """`state._decoder` is created at import, before `load_settings()` reads
    settings.json, so every boot came up as a CTS whatever was saved. Invisible while
    all consoles were serial ones — the Settings form rebuilds on save — and fatal for
    a console whose whole point is having no port."""
    monkeypatch.setitem(state.settings, 'console_type', 'manual')
    monkeypatch.setattr(state, '_decoder_console_type', 'cts_gen6', raising=False)
    monkeypatch.setattr(state, '_decoder', make_decoder('cts_gen6', state.settings),
                        raising=False)

    state._apply_console_type()

    assert isinstance(state._decoder, ManualDecoder)
    assert state._decoder.requires_serial is False


# ── Settings → Timing drops what a portless console cannot have ────────────────

def _timing_pane(requires_serial):
    """Render settings.html far enough to inspect the Timing pane's markup.

    `ChainableUndefined` so the dozens of context values this pane does not care
    about render empty instead of raising — the alternative is a fixture that has to
    be extended every time any other tab gains a variable.
    """
    from jinja2 import ChainableUndefined, Environment, FileSystemLoader
    env = Environment(undefined=ChainableUndefined, loader=FileSystemLoader(
        [os.path.join(REPO, 'server', 'templates'),
         os.path.join(REPO, 'shared', 'templates')]))
    env.globals['url_for'] = lambda name, **kw: '/static/' + kw.get('filename', '')
    console = 'cts_gen6' if requires_serial else 'manual'
    return env.get_template('settings.html').render(
        t=state.settings_strings('en'),
        serial_port='COM1', serial_port_list=[('COM1', 'COM1')],
        console_type=console,
        console_options=[(k, label) for k, label, _ in CONSOLE_OPTIONS],
        console_info=console_info_for(console),
        console_requires_serial=requires_serial,
        theme_colors=state.DEFAULT_THEME_COLORS,
        theme_color_defaults=state.DEFAULT_THEME_COLORS,
        theme_fonts=state.DEFAULT_THEME_FONTS)


def test_a_portless_console_hides_the_port_picker_and_the_connection_badge():
    """A serial-port dropdown, a Connection badge stuck on "—" and a Serial Monitor
    with nothing to monitor are three pieces of furniture that can only mislead when
    there is no wire. What replaces them is the link to the page that does the work."""
    wired = _timing_pane(True)
    assert 'id="serial_port"' in wired
    assert 'id="serial-status-badge"' in wired
    assert 'id="debug-output"' in wired

    manual = _timing_pane(False)
    assert 'id="serial_port"' not in manual
    assert 'id="serial-status-badge"' not in manual
    assert 'id="debug-output"' not in manual
    assert 'href="/manual"' in manual


def test_the_manual_page_is_always_reachable_from_the_quick_links():
    """Offered even under a real console: an Omnisport transmits no event or heat at
    all (omnisport_2000_serial.md), so its operator needs this page too."""
    assert _timing_pane(True).count('href="/manual"') >= 1


# ── The hold that guards Prev / Next ───────────────────────────────────────────

def test_the_hold_duration_is_declared_once_and_read_by_both_halves():
    """The JS timer and the CSS fill animation have to agree, or the bar finishes at
    a different moment than the action it is a progress bar for. hold.js writes the
    duration into `--hold-ms`; the CSS reads it. Hardcoding 1.5s in only one of them
    is the bug this pins."""
    hold_js = open(os.path.join(REPO, 'shared', 'static', 'js', 'hold.js'),
                   encoding='utf-8').read()
    assert 'data-hold-ms' in hold_js
    assert "setProperty('--hold-ms'" in hold_js

    for css in ('shared/static/css/panel.css', 'server/templates/manual.html'):
        src = open(os.path.join(REPO, css), encoding='utf-8').read()
        assert 'animation: holdfill var(--hold-ms' in src, f'{css} hardcodes the duration'


def test_everything_that_reaches_the_boards_is_held_not_tapped():
    """One rule for the whole page rather than two: Previous, Next, Clear and every
    row's ▸ all take the same 1.5s press-and-hold the Power tab uses. A tap that
    changed the heat mid-race is the accident this page exists to make hard."""
    page = _manual_page()
    for fn in ('manualPrev', 'manualNext', 'manualClear', 'manualCommit'):
        assert f'data-hold-fn="{fn}"' in page, f'{fn} is not behind a hold'
    # Three in the header plus the one on every row of the running order.
    assert page.count('data-hold-ms="1500"') == 4


def test_a_short_press_on_a_row_arrow_does_not_fall_through_to_the_preview():
    """hold.js swallows the click on a [data-hold] but does not stop it bubbling, so
    without this guard an abandoned hold on ▸ would silently toggle the row instead —
    the page quietly doing something other than what was asked of it."""
    page = _manual_page()
    assert "if (e.target.closest('[data-hold]')) return;" in page


def test_the_row_arrows_read_their_heat_off_the_button_that_was_held():
    """One handler for every row. hold.js passes the element for exactly this — a
    per-row closure would mean re-binding on every render."""
    page = _manual_page()
    assert "window.manualCommit = function (el)" in page
    assert "el.getAttribute('data-commit')" in page

    hold_js = open(os.path.join(REPO, 'shared', 'static', 'js', 'hold.js'),
                   encoding='utf-8').read()
    assert 'window[fn](el)' in hold_js, 'hold.js must hand the element to the handler'


def test_the_preview_shows_swimmers_but_never_seed_times():
    """The page carries no times at all, so a seed time beside a name has nothing on
    this screen to be compared against."""
    page = open(os.path.join(REPO, 'server', 'templates', 'manual.html'),
                encoding='utf-8').read()
    assert 'swimmer-name' in page and 'swimmer-club' in page
    assert 'seed_time' not in page and 'seed-time' not in page


# ── The page's layout rules ────────────────────────────────────────────────────

def _manual_page():
    return open(os.path.join(REPO, 'server', 'templates', 'manual.html'),
                encoding='utf-8').read()


def test_the_steppers_live_in_the_sticky_header():
    """Above the list they scrolled away. The operator is usually well down the
    running order when the next heat is called, so a stepper that had scrolled off
    the top meant hunting for it between every heat."""
    page = _manual_page()
    header = page[page.index('<div id="now">'):page.index('{% if not manual_active %}')]
    assert 'id="stepper"' in header, 'the steppers are not inside the sticky header'
    assert 'data-hold-fn="manualPrev"' in header
    assert 'data-hold-fn="manualNext"' in header
    # And nothing sticky is left behind above the list to compete with it.
    assert page.count('position: sticky') == 1


def test_tapping_the_numbers_returns_to_the_heat_that_is_on():
    page = _manual_page()
    assert 'id="now-jump"' in page
    assert "closest('#now-jump')" in page
    assert 'scrollToCurrent(true)' in page, 'the jump should be smooth, not a cut'


def test_the_current_heat_lands_at_the_top_of_the_list_not_its_middle():
    """What the operator wants next is the heats *after* this one, so the current
    heat sits under the header with the rest of the meet running down below it."""
    page = _manual_page()
    assert "block: 'start'" in page
    assert "block: 'center'" not in page


def test_the_scroll_offset_is_measured_rather_than_hardcoded():
    """The header is taller with the wrong-console banner up, and taller again when a
    long event name wraps — a fixed offset either tucks the current heat under the
    header or leaves a gap above it."""
    page = _manual_page()
    assert 'scroll-margin-top: var(--header-h' in page
    assert "setProperty('--header-h'" in page
    assert 'syncHeaderHeight' in page


def test_the_current_heat_is_always_expanded():
    """It is the one the operator is looking up from to check who is behind the
    blocks; re-opening it after every change is a tap too many. A previewed heat
    opens alongside it rather than instead of it."""
    page = _manual_page()
    assert 'key === openKey || isCurrent(h)' in page


def test_the_steppers_are_wide_enough_for_a_thumb_and_set_apart():
    """They were 52px squares tucked beside the numbers. A stepper the operator hits
    between every heat, on a wet pool deck, gets the width of the screen."""
    page = _manual_page()
    stepper = page[page.index('#stepper {'):page.index('.step-btn[disabled]')]
    assert 'flex: 1' in stepper, 'the steppers should split the row, not sit at a fixed width'
    assert 'min-width: 110px' in stepper
    assert 'gap: 14px' in stepper


def test_the_next_heat_is_expanded_alongside_the_current_one():
    """The two an operator actually looks at: who is swimming, and who to call up."""
    page = _manual_page()
    assert 'key === openKey || isCurrent(h) || isNext' in page
    assert 'i === cur + 1' in page
    # And tellable apart, since both are now open.
    assert '.heat-card.heat-next' in page


def test_clearing_the_board_puts_the_decoder_back_at_its_sentinel(rig):
    """Not a new state — (0, 0) is the "nothing announced yet" value a cold boot has,
    which `send_event_info` already renders as empty strings rather than event 0."""
    worker._worker_set_heat(3, 1)
    rig.emitted.clear()

    worker._worker_clear_heat()

    assert rig.decoder.last_event_sent == (0, 0)
    assert rig.decoder.lane_seed_times == {}
    board = _boards(rig)
    assert board['current_event'] == ''
    assert board['current_heat'] == ''
    assert board['event_name'] == ''
    assert board['lane_name1'] == ''
    # `send_event_info` blanks names and deltas but not these — hence reset_lanes too.
    assert board['lane_time1'] == ''
    assert board['lane_place1'] == ' '
    assert board['lane_splits1'] == 0


def test_clearing_the_board_sends_the_upcoming_list_back_to_the_top(rig):
    """An empty board means nothing has run yet, so the next-heats list says so."""
    worker._worker_set_heat(3, 2)
    rig.emitted.clear()
    worker._worker_clear_heat()

    frames = [d for _, ev, d in rig.emitted if ev == 'next_heats']
    assert frames, 'clearing did not republish the upcoming heats'
    first = frames[-1]['heats'][0]
    assert (first['event'], first['heat']) == meet_data.heat_order()[0]


def test_clearing_the_board_reads_as_not_live(rig):
    """`is_live` is keyed off the sentinel, so an empty board correctly reports the
    link as idle — there is genuinely nothing on it."""
    worker._worker_set_heat(1, 1)
    assert rig.decoder.is_live is True
    worker._worker_clear_heat()
    assert rig.decoder.is_live is False


def test_clearing_cancels_a_pending_debounce(rig):
    worker._worker_set_heat(1, 1)
    state._results_prev_race_finished = True
    state._running_lanes.add(2)
    gen = state._finish_timer_gen

    worker._worker_clear_heat()

    assert state._results_prev_race_finished is False
    assert state._running_lanes == set()
    assert state._finish_timer_gen > gen


def test_the_next_heat_is_marked_with_the_same_rectangle_as_the_current_one():
    """It was a 3px bar down the left edge, which read as a different kind of thing
    rather than the other half of a pair. Same outline, different colour: the board's
    yellow for what is on, the schedule's blue for what is up next."""
    page = _manual_page()
    for cls, colour in (('heat-current', '--color-time'),
                        ('heat-next', '--color-schedule-event')):
        rule = page[page.index('.heat-card.' + cls + ' {'):]
        rule = rule[:rule.index('}')]
        assert 'outline: 2px solid var(' + colour + ')' in rule, cls
        assert 'outline-offset: -2px' in rule, cls
    assert 'box-shadow: inset' not in page


def test_the_clear_button_does_not_reset_the_gradient_the_hold_fills():
    """`background: none` on an id beat `.btn-holding`'s gradient, so holding Clear
    changed its colour but showed no progress — the one control with no visible
    feedback looking like the one control that does nothing."""
    import re
    page = _manual_page()
    rule = page[page.index('#btn-clear {'):]
    rule = rule[:rule.index('}')]
    # Declarations only — the comment above them names the shorthand it warns against.
    decls = re.sub(r'/\*.*?\*/', '', rule, flags=re.S)
    assert 'background-color: transparent' in decls
    assert 'background:' not in decls, 'the shorthand resets background-image'


def test_the_hold_fill_survives_a_higher_specificity_background_rule():
    """Belt to the braces above: the gradient and its repeat carry !important in both
    copies of `.btn-holding`, so no page rule can silently blank the progress bar."""
    for path in ('shared/static/css/panel.css', 'server/templates/manual.html'):
        src = open(os.path.join(REPO, path), encoding='utf-8').read()
        rule = src[src.index('.btn-holding {'):]
        rule = rule[:rule.index('}')]
        assert 'background-image: linear-gradient' in rule and rule.count('!important') >= 4, path


def test_a_long_press_cannot_raise_the_phones_text_menu():
    """The hold *is* a long press, which is also how iOS raises the selection callout
    and Writing Tools — the menu came up over the row arrow and took the press with
    it. Being a real <button> is not enough; the callout keys off selectable content."""
    for path in ('shared/static/css/panel.css', 'server/templates/manual.html'):
        src = open(os.path.join(REPO, path), encoding='utf-8').read()
        rule = src[src.index('[data-hold] {'):]
        rule = rule[:rule.index('}')]
        for prop in ('touch-action: none', '-webkit-touch-callout: none',
                     '-webkit-user-select: none', 'user-select: none'):
            assert prop in rule, f'{path} is missing {prop}'

    hold_js = open(os.path.join(REPO, 'shared', 'static', 'js', 'hold.js'),
                   encoding='utf-8').read()
    assert "addEventListener('contextmenu'" in hold_js


def test_the_event_and_heat_readout_is_centred_and_the_biggest_thing_on_the_page():
    """It is the one number checked against the board across the pool, at a glance and
    at arm's length. The name and caption centre with it so the header reads as one
    block rather than a left column with a centred number dropped into it."""
    page = _manual_page()
    jump = page[page.index('#now-jump {'):]
    jump = jump[:jump.index('}')]
    assert 'justify-content: center' in jump
    assert 'text-align: center' in jump

    for block in ('#now-name {', '#now-caption {'):
        rule = page[page.index(block):]
        rule = rule[:rule.index('}')]
        assert 'text-align: center' in rule, block

    value = page[page.index('.now-value {'):]
    value = value[:value.index('}')]
    size = float(value.split('font-size:')[1].split('em')[0].strip())
    assert size >= 2.2, f'the readout is only {size}em'
