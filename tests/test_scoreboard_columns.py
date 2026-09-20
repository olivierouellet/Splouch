"""The column reveal, and the overflow rule that makes it safe.

Time / delta / place collapse when a heat loads and slide open when the race
starts. It is purely cosmetic — the contrast is the point: between heats the
board is a calm start list, then the race begins and the timing columns arrive.
The operator's toggle on /operator drives the same mechanism via `columns_state`.

Needs PySide6 (`uv run pytest tests/`); skips without it.
"""
import pytest

pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')

from PySide6.QtCore import QAbstractAnimation      # noqa: E402
from PySide6.QtGui import QFontMetrics             # noqa: E402

from scoreboard.board import BoardWindow         # noqa: E402
from scoreboard.theme import Config              # noqa: E402

# `qt_app` comes from tests/conftest.py — session-scoped, fonts already loaded.


@pytest.fixture
def board(qt_app):
    window = BoardWindow(Config({'num_lanes': 6}))
    window.resize(1920, 1080)
    window.show()
    qt_app.processEvents()
    yield window
    window.stop_clock()
    window.close()


def _timing_widths(board):
    row = board.rows[0]
    return (row.time_label.width(), row.delta_label.width(), row.place_label.width())


def _seek(board, qt_app, fraction):
    """Drive the animation to a point in time — deterministic, no sleeping."""
    anim = board._col_anim
    anim.setCurrentTime(int(anim.duration() * fraction))
    qt_app.processEvents()


def _run_transition(board, qt_app):
    """Drive the sequence deterministically instead of sleeping through 2s."""
    board._transition_collapse(board._transition_token)
    board._col_anim.setCurrentTime(board._col_anim.duration())
    board._transition_fade_out(board._transition_token)
    board._content_fade.setCurrentTime(board._content_fade.duration())
    qt_app.processEvents()


def test_an_idle_board_shows_every_column(board):
    """Before any heat arrives the board must look finished, not half-drawn."""
    assert board.columns_visible
    assert all(w > 0 for w in _timing_widths(board))


def test_a_new_heat_collapses_the_timing_columns(board, qt_app):
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'lane_name1': 'Roy, Zoé'})
    qt_app.processEvents()
    assert not board.columns_visible
    assert _timing_widths(board) == (0, 0, 0)
    # The freed width goes to the name, which is the visual point of collapsing.
    assert board.rows[0].name_cell.width() > 900


def test_the_race_starting_slides_them_open(board, qt_app):
    board.apply_update({'current_event': '3', 'current_heat': '1'})
    qt_app.processEvents()
    assert _timing_widths(board) == (0, 0, 0)

    board.apply_update({'running_time': '0.00', 'lane_running1': True})
    assert board._col_anim.state() == QAbstractAnimation.State.Running, 'reveal not animated'

    _seek(board, qt_app, 0.5)
    half = _timing_widths(board)
    assert all(0 < w for w in half), 'columns should be partly open mid-animation'

    _seek(board, qt_app, 1.0)
    assert board.columns_visible
    assert all(w > h for w, h in zip(_timing_widths(board), half, strict=True))


def test_the_next_heat_collapses_them_again(board, qt_app):
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'running_time': '0.00', 'lane_running1': True})
    _seek(board, qt_app, 1.0)
    assert board.columns_visible

    # A lane with a time on screen means there is something to dissolve, so the
    # collapse now waits behind the podium fade rather than snapping shut.
    board.apply_update({'lane_running1': False, 'current_heat': '2'})
    qt_app.processEvents()
    _run_transition(board, qt_app)
    assert not board.columns_visible


def test_an_event_change_collapses_them_too(board, qt_app):
    """The trigger is the (event, heat) pair, not the heat alone.

    Moving from event 3 heat 1 to event 4 heat 1 is a new race with a new start
    list, even though the heat number is unchanged. The browser treats it the same
    way — either field changing drops it into the intro state.
    """
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'running_time': '0.00', 'lane_running1': True})
    _seek(board, qt_app, 1.0)
    assert board.columns_visible

    board.apply_update({'lane_running1': False, 'current_event': '4'})
    qt_app.processEvents()
    _run_transition(board, qt_app)
    assert not board.columns_visible, 'an event change must collapse the columns'


def test_repeated_heat_frames_do_not_re_collapse(board, qt_app):
    """`current_heat` is resent constantly; only a *change* may collapse."""
    board.apply_update({'current_event': '3', 'current_heat': '1'})
    board.apply_update({'running_time': '0.00', 'lane_running1': True})
    _seek(board, qt_app, 1.0)

    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'running_time': '12.00'})
    qt_app.processEvents()
    assert board.columns_visible, 'a repeated heat frame collapsed the board mid-race'


def test_operator_toggle_uses_the_same_mechanism(board, qt_app):
    board.set_columns_visible(False, animate=False)
    assert not board.columns_visible

    board.set_columns_visible(True)
    _seek(board, qt_app, 1.0)
    assert board.columns_visible
    assert all(w > 0 for w in _timing_widths(board))


def test_re_asserting_the_current_state_does_not_re_animate(board):
    """Repeated `columns_state` frames must not make the board stutter."""
    board.set_columns_visible(True, animate=False)
    board.set_columns_visible(True)
    assert board._col_anim.state() != QAbstractAnimation.State.Running


def test_reset_restores_the_idle_look(board, qt_app):
    board.apply_update({'current_event': '3', 'current_heat': '1'})
    qt_app.processEvents()
    assert not board.columns_visible

    board.reset()
    assert board.columns_visible


# ── Overflow ───────────────────────────────────────────────────────────────────

def test_no_cell_paints_outside_its_column(board, qt_app):
    """Every cell shrinks to fit, and elides rather than being cut mid-glyph.

    Qt clips a label to its own rect, so an oversized value never reaches the
    neighbouring cell — it is truncated through a character instead, which reads
    as corruption: `1:12.44` next to a clipped delta rendered as `1:12.44).06`.
    Below the font floor the text is elided, so it ends in `…` rather than a
    half-drawn glyph.
    """
    board.apply_update({
        'current_event': '3', 'current_heat': '1',
        **{f'lane_name{i}': 'Vandenbroucke-Mortensen, Alexandra' for i in range(1, 7)},
        **{f'lane_club{i}': 'CN Saint-Jean-sur-Richelieu' for i in range(1, 7)},
        **{f'lane_time{i}': '1:12.44' for i in range(1, 7)},
        **{f'lane_place{i}': str(i) for i in range(1, 7)},
        **{f'lane_delta_seconds{i}': -12.34 for i in range(1, 7)},
    })
    board.set_columns_visible(True, animate=False)
    qt_app.processEvents()

    overflowing = []
    for row in board.rows:
        cells = ((row.lane_label, 'lane'), (row.name_label, 'name'),
                 (row.club_label, 'club'), (row.time_label, 'time'),
                 (row.delta_label, 'delta'), (row.place_label, 'place'))
        for label, name in cells:
            if not label.isVisible() or not label.text():
                continue
            drawn  = label.displayed_text()
            needed = QFontMetrics(label.font()).horizontalAdvance(drawn)
            if needed > label.width():
                overflowing.append(f'lane {row.lane} {name}: '
                                   f'{needed}px of text in {label.width()}px')
    assert not overflowing, 'cells painting outside their column: ' + '; '.join(overflowing)


def test_headers_shrink_to_fit_too(board, qt_app):
    """Translated headers are long — `COULOIR` in a lane column six units wide."""
    board.header_row.cells['lane'].setText('COULOIR')
    board.header_row.cells['place'].setText('CLASSEMENT')
    qt_app.processEvents()
    for key in ('lane', 'place'):
        label = board.header_row.cells[key]
        needed = QFontMetrics(label.font()).horizontalAdvance(label.displayed_text())
        assert needed <= label.width(), f'{key} header overflows its column'


# ── Column titles line up with the data ────────────────────────────────────────

def _column_lefts(board):
    row = board.rows[0]
    cells = board.header_row.cells
    return [(cells[k].x(), w.x()) for k, w in
            (('lane', row.lane_label), ('name', row.name_cell),
             ('club', row.club_label), ('time', row.time_label),
             ('delta', row.delta_label), ('place', row.place_label))]


def test_hiding_a_column_title_does_not_move_the_column(qt_app):
    """The browser uses `visibility: hidden` here — the width stays put.

    Hiding the widget instead takes it out of the layout and Qt hands its stretch to
    the neighbours, so every title after it slides left and stops naming the column
    underneath. `show_lane_header` and `show_time_header` have no column flag at all,
    so their columns always remain and the misalignment is guaranteed.
    """
    plain = BoardWindow(Config({'num_lanes': 6}))
    plain.resize(1920, 1080)
    plain.show()
    qt_app.processEvents()
    expected = _column_lefts(plain)

    hidden = BoardWindow(Config({'num_lanes': 6, 'show_lane_header': False,
                                 'show_time_header': False}))
    hidden.resize(1920, 1080)
    hidden.show()
    qt_app.processEvents()
    try:
        assert _column_lefts(hidden) == expected, 'the column titles drifted'
        assert hidden.header_row.cells['lane'].text() == '', 'title should be blank'
        assert hidden.header_row.cells['lane'].isVisibleTo(hidden), \
            'blanked, not removed — the column below it is still there'
    finally:
        plain.close()
        hidden.close()


def test_hiding_a_whole_column_does_remove_its_title(qt_app):
    """`display: none` is the other case: the width goes with the column."""
    window = BoardWindow(Config({'num_lanes': 6, 'show_club': False}))
    window.resize(1920, 1080)
    window.show()
    qt_app.processEvents()
    try:
        assert not window.header_row.cells['club'].isVisibleTo(window)
        assert not window.rows[0].club_label.isVisibleTo(window)
    finally:
        window.close()


def test_the_time_title_blanks_while_the_column_is_shut(board, qt_app):
    """`collapse_cols()` clears it outright with `_tc.innerHTML = ''`."""
    board.set_columns_visible(False, animate=False)
    qt_app.processEvents()
    assert board.header_row.cells['time'].text() == ''

    board.set_columns_visible(True, animate=False)
    qt_app.processEvents()
    assert board.header_row.cells['time'].text() == board.cfg.labels['time']


# ── Header bar geometry ────────────────────────────────────────────────────────

def test_the_wall_clock_stays_hard_right_on_an_idle_board(board, qt_app):
    """Nothing in the bar may move between an idle board and a loaded heat.

    The cells used to be hidden rather than blanked when no heat was loaded, and Qt
    redistributes a hidden widget's stretch — which left the wall clock about
    three-quarters of the way across instead of at the edge.
    """
    idle = board.wall_clock.geometry()
    assert idle.right() >= board.width() - 2, 'the clock is not at the right edge'

    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'event_name': '100 m Papillon'})
    qt_app.processEvents()
    assert board.wall_clock.geometry() == idle, 'loading a heat moved the clock'


def test_a_wider_event_number_does_not_reflow_the_bar(board, qt_app):
    board.apply_update({'current_event': '3', 'current_heat': '1'})
    qt_app.processEvents()
    before = [w.geometry() for w in (board.event_cell, board.heat_cell,
                                     board.name_label, board.chrono_label,
                                     board.wall_clock)]
    board.apply_update({'current_event': '188', 'current_heat': '12'})
    qt_app.processEvents()
    after = [w.geometry() for w in (board.event_cell, board.heat_cell,
                                    board.name_label, board.chrono_label,
                                    board.wall_clock)]
    assert after == before, 'the header reflowed around a longer number'


def test_the_header_cells_fill_the_bar_exactly(board):
    """The weights are percentages, so they have to sum to 100."""
    from scoreboard.board import (_HW_CHRONO, _HW_CLOCK, _HW_EVENT, _HW_HEAT,
                                  _HW_NAME)
    assert _HW_EVENT + _HW_HEAT + _HW_NAME + _HW_CHRONO + _HW_CLOCK == 100


# ── Relay sub-names ────────────────────────────────────────────────────────────

def test_a_relay_name_fits_the_half_cell_it_shares(board, qt_app):
    """The name and the relay line split one cell, so the name cannot be sized
    from the whole row — FitLabel only ever solves for width, and the overflow
    would be vertical, clipping the team name top and bottom."""
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'lane_name1': 'CN Rosemère',
                        'lane_name_alt1': 'Roy / Côté / Nguyen / Tremblay'})
    qt_app.processEvents()

    row = board.rows[0]
    assert row.alt_label.isVisibleTo(board)
    name_px = row.name_label.font().pixelSize()
    assert name_px <= row.name_label.height(), 'the team name overflows its half-cell'
    # And the relay line stays the browser's 0.7em under it.
    assert row.alt_label.font().pixelSize() < name_px


def test_a_solo_name_keeps_the_full_row_ceiling(board, qt_app):
    """Sharing the cell is what shrinks the name; on its own it must not."""
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'lane_name1': 'Roy, Zoé'})
    qt_app.processEvents()
    row = board.rows[0]
    assert not row.alt_label.isVisibleTo(board)
    assert row.name_label.font().pixelSize() == row.lane_label.font().pixelSize()


# ── Heat transition ────────────────────────────────────────────────────────────
# Results → next heat is a five-step dissolve: podium tints fade, columns close, the
# table fades out, the new heat is painted while invisible, the table fades back in.
# 500ms per step. `/live` cuts instead; the fade is deliberate here, because an
# instant cut reads as a glitch at TV distance.

def _finish_a_heat(board, qt_app, event='3', heat='1'):
    board.apply_update({'current_event': event, 'current_heat': heat,
                        'lane_name1': 'Roy, Zoé', 'lane_name2': 'Côté, Léa'})
    board.apply_update({'running_time': '0.00',
                        'lane_running1': True, 'lane_running2': True})
    qt_app.processEvents()
    board.apply_update({'lane_running1': False, 'lane_running2': False,
                        'lane_time1': '58.12', 'lane_place1': '1',
                        'lane_time2': '59.03', 'lane_place2': '2'})
    board.cancel_heat_transition()
    board.set_columns_visible(True, animate=False)
    qt_app.processEvents()


def test_the_table_pauses_so_the_new_heat_does_not_leak(board, qt_app):
    """The outgoing results stay on screen until the fade hides them."""
    _finish_a_heat(board, qt_app)
    board.apply_update({'current_heat': '2', 'lane_name1': 'Nguyen, An'})
    qt_app.processEvents()

    assert board.paused
    assert board.rows[0].name_label.text() == 'Roy, Zoé', 'next heat leaked early'
    assert board.rows[0].time_label.text() == '58.12', 'results cleared too early'


def test_the_podium_tint_fades_before_anything_moves(board, qt_app, settle_podium):
    _finish_a_heat(board, qt_app)
    settle_podium(board)
    gold = board.cfg.color('podium_gold')
    assert board.rows[0]._current_bg.lower() == gold.lower()

    board.apply_update({'current_heat': '2'})
    qt_app.processEvents()
    anim = board.rows[0]._podium_anim
    assert anim is not None, 'podium tint did not start fading'

    anim.setCurrentTime(anim.duration())
    assert board.rows[0]._current_bg.lower() == board.rows[0]._base_bg.lower()
    # Nothing else has moved yet.
    assert board._content_opacity.opacity() == 1.0
    assert board.columns_visible


def test_the_new_heat_appears_only_once_invisible(board, qt_app):
    _finish_a_heat(board, qt_app)
    board.apply_update({'current_heat': '2', 'lane_name1': 'Nguyen, An'})
    qt_app.processEvents()
    _run_transition(board, qt_app)

    assert not board.paused
    assert board.rows[0].name_label.text() == 'Nguyen, An'
    # The previous heat's times must be gone, not merely hidden behind the
    # collapsed columns — the operator can reopen them at any moment.
    assert board.rows[0].time_label.text() == ''
    assert board.rows[0].place_label.text() == ''
    assert not any(k.startswith('lane_time') for k in board.snapshot)
    assert not board.columns_visible, 'a start list keeps the timing columns shut'


def test_the_table_fades_back_in(board, qt_app):
    _finish_a_heat(board, qt_app)
    board.apply_update({'current_heat': '2'})
    qt_app.processEvents()
    _run_transition(board, qt_app)

    board._content_fade.setCurrentTime(board._content_fade.duration())
    qt_app.processEvents()
    assert board._content_opacity.opacity() == 1.0


def test_a_race_starting_cuts_the_transition_short(board, qt_app):
    """A swimmer on the blocks outranks an animation."""
    _finish_a_heat(board, qt_app)
    board.apply_update({'current_heat': '2', 'lane_name1': 'Nguyen, An'})
    qt_app.processEvents()
    assert board.paused

    board.apply_update({'running_time': '0.00', 'lane_running1': True})
    qt_app.processEvents()
    assert not board.paused, 'transition was not cancelled'
    assert board._content_opacity.opacity() == 1.0, 'board left half-faded'
    assert board.rows[0].name_label.text() == 'Nguyen, An'


def test_loading_a_heat_onto_an_empty_board_skips_the_dissolve(board, qt_app):
    """Two seconds of dissolve to clear nothing would just look slow."""
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'lane_name1': 'Roy, Zoé'})
    qt_app.processEvents()
    assert not board.paused
    assert not board.columns_visible
    assert board._content_opacity.opacity() == 1.0


def test_text_below_the_font_floor_is_elided_not_cut(board, qt_app):
    """A 27-character club in an 8vw column cannot fit even at `min_px`.

    Qt would cut it through a glyph, which reads as corruption. Eliding says
    "there is more" instead. `text()` still returns the full string, so nothing
    downstream sees the shortened version.
    """
    long_club = 'CN Saint-Jean-sur-Richelieu'
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'lane_club1': long_club})
    board.set_columns_visible(True, animate=False)
    qt_app.processEvents()

    label = board.rows[0].club_label
    assert label.text() == long_club, 'the full value must still be readable in code'
    assert label.displayed_text() != long_club, 'should have been elided'
    assert label.displayed_text().endswith('…')
    assert (QFontMetrics(label.font()).horizontalAdvance(label.displayed_text())
            <= label.width())


def test_a_name_that_fits_is_never_elided(board, qt_app):
    """Shrink-to-fit is the point; eliding is only the last resort."""
    name = 'Vandenbroucke-Mortensen, Alexandra'
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'lane_name1': name})
    qt_app.processEvents()
    label = board.rows[0].name_label
    assert label.displayed_text() == name, 'a name that fits must stay whole'


# ── Podium reveal ──────────────────────────────────────────────────────────────
# The browser tints only on entering the results screen, and steps gold → silver →
# bronze 400ms apart. Tinting as each place lands instead sends the first finisher's
# row gold while the rest of the heat is still in the water.

def test_no_podium_tint_while_the_heat_is_still_running(board, qt_app):
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'lane_name1': 'Roy, Zoé', 'lane_name2': 'Côté, Léa'})
    board.apply_update({'running_time': '0.00',
                        'lane_running1': True, 'lane_running2': True})
    qt_app.processEvents()
    # Lane 1 touches first; lane 2 is still swimming.
    board.apply_update({'lane_running1': False,
                        'lane_time1': '58.12', 'lane_place1': '1'})
    qt_app.processEvents()

    assert board.rows[0].podium_place() == 1, 'the place itself must be recorded'
    assert board.rows[0]._current_bg.lower() == board.rows[0]._base_bg.lower(), \
        'gold arrived while lane 2 was still in the water'
    assert not board._podium_shown


def test_a_timed_but_unplaced_lane_holds_the_podium_back(board, qt_app):
    """The browser's `all_done` counts a lane with a time and no place as running."""
    board.apply_update({'current_event': '3', 'current_heat': '1'})
    board.apply_update({'lane_time1': '58.12', 'lane_place1': '1',
                        'lane_time2': '59.03'})
    qt_app.processEvents()
    assert not board.heat_is_done()
    assert not board._podium_shown


def test_the_podium_steps_down_the_three_rows(board, qt_app):
    board.apply_update({'current_event': '3', 'current_heat': '1'})
    board.apply_update({'running_time': '0.00', 'lane_running1': True})
    qt_app.processEvents()
    board.apply_update({
        'lane_running1': False,
        **{f'lane_time{i}': f'5{i}.00' for i in range(1, 7)},
        **{f'lane_place{i}': str(i) for i in range(1, 7)}})
    qt_app.processEvents()

    delays = {t.interval() for t in board._podium_timers}
    assert delays == {0, 400, 800}, 'gold, silver and bronze must be staggered'
    assert len(board._podium_timers) == 3, 'only the top three are tinted'


def test_the_podium_eases_in_rather_than_snapping(board, qt_app):
    _finish_a_heat(board, qt_app)
    qt_app.processEvents()          # lets the zero-delay gold timer fire
    anim = board.rows[0]._podium_anim
    assert anim is not None, 'gold snapped straight on instead of fading'
    assert anim.duration() == 500

    anim.setCurrentTime(anim.duration())
    assert board.rows[0]._current_bg.lower() == board.cfg.color('podium_gold').lower()


def test_the_next_heat_re_arms_the_podium(board, qt_app, settle_podium):
    """One reveal per heat — but the following heat must get its own."""
    _finish_a_heat(board, qt_app)
    settle_podium(board)
    assert board._podium_shown

    board.apply_update({'current_heat': '2'})
    qt_app.processEvents()
    assert not board._podium_shown, 'the next heat inherited a spent podium'


def test_an_empty_board_does_not_consume_the_reveal(board, qt_app):
    """`heat_is_done()` is trivially true with nothing on screen."""
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'lane_name1': 'Roy, Zoé'})
    qt_app.processEvents()
    assert board.heat_is_done()
    assert not board._podium_shown, 'the reveal was spent on an empty start list'


def test_podium_off_leaves_the_rows_alone(qt_app, settle_podium):
    window = BoardWindow(Config({'num_lanes': 6, 'show_podium': False}))
    window.resize(1920, 1080)
    window.show()
    qt_app.processEvents()
    try:
        window.apply_update({'current_event': '3', 'current_heat': '1',
                             'lane_time1': '58.12', 'lane_place1': '1'})
        settle_podium(window)
        assert window.rows[0].podium_place() is None
        assert window.rows[0]._current_bg.lower() == window.rows[0]._base_bg.lower()
    finally:
        window.close()


# ── Delta colour ───────────────────────────────────────────────────────────────

def test_a_faster_swim_takes_the_delta_better_colour(board, qt_app):
    """`lane_delta_better<i>` picks the colour; the value comes from
    `lane_delta_seconds<i>`. Both are sent together by the server."""
    board.apply_update({'lane_place1': '1', 'lane_time1': '1:12.44',
                        'lane_delta_seconds1': -0.46, 'lane_delta_better1': True,
                        'lane_place2': '2', 'lane_time2': '1:13.01',
                        'lane_delta_seconds2': 0.34, 'lane_delta_better2': False})
    qt_app.processEvents()
    assert board.cfg.color('delta_better') in board.rows[0].delta_label.styleSheet()
    assert board.cfg.color('delta_worse') in board.rows[1].delta_label.styleSheet()


def test_restyling_keeps_the_faster_colour(board, qt_app):
    """`apply_theme` runs on every `/config` reload — including the one triggered
    by editing the colours in Settings → Theme. Forgetting the delta state there
    repaints a faster swim in the slower colour."""
    board.apply_update({'lane_place1': '1', 'lane_time1': '1:12.44',
                        'lane_delta_seconds1': -0.46, 'lane_delta_better1': True})
    qt_app.processEvents()

    board.rows[0].apply_theme()          # no refresh() afterwards, on purpose
    qt_app.processEvents()
    assert board.cfg.color('delta_better') in board.rows[0].delta_label.styleSheet(), \
        'a restyle lost the faster colour'


# ── Restyling must not undo the fit ────────────────────────────────────────────
# `apply_theme` runs on every /config reload — including the very first one, which
# lands seconds after the kiosk window opens. It assigns a fresh QFont per label,
# and a QFont carries a size: whatever the family's default is, not the size the
# label had fitted. Anything that is not re-set afterwards is then drawn at ~13px
# until something happens to refit it, which on a kiosk means never.
#
# The lane number and the EVENT/HEAT cells are exactly the labels whose text is not
# re-set by `refresh()`, which is why they were the ones that came up tiny.

def _sizes(board):
    row = board.rows[0]
    return {
        'lane':     row.lane_label.font().pixelSize(),
        'place':    row.place_label.font().pixelSize(),
        'name':     row.name_label.font().pixelSize(),
        'club':     row.club_label.font().pixelSize(),
        'time':     row.time_label.font().pixelSize(),
        'ev_word':  board.event_cell.label.font().pixelSize(),
        'ev_num':   board.event_cell.value.font().pixelSize(),
        'hdr_lane': board.header_row.cells['lane'].font().pixelSize(),
    }


def test_a_config_reload_does_not_shrink_the_text(board, qt_app):
    board.apply_update({'current_event': '3', 'current_heat': '1',
                        'lane_name1': 'Roy, Zoé', 'lane_club1': 'CNR'})
    qt_app.processEvents()
    before = _sizes(board)
    assert all(px > 0 for px in before.values()), 'nothing was fitted to begin with'

    board.set_config(Config({'num_lanes': 6}))
    qt_app.processEvents()

    assert _sizes(board) == before, 'a restyle shrank the text'


def test_restyling_a_lane_row_keeps_its_fitted_size(board, qt_app):
    """The lane number is the clearest case: its text is set once, in __init__."""
    board.apply_update({'current_event': '3', 'current_heat': '1'})
    qt_app.processEvents()
    before = board.rows[0].lane_label.font().pixelSize()

    board.rows[0].apply_theme()          # no refresh() afterwards, on purpose
    qt_app.processEvents()
    assert board.rows[0].lane_label.font().pixelSize() == before


def test_a_new_font_still_takes_effect(board, qt_app):
    """Keeping the size must not mean ignoring the family the operator chose."""
    board.apply_update({'current_event': '3', 'current_heat': '1'})
    qt_app.processEvents()
    board.set_config(Config({'num_lanes': 6,
                             'theme_fonts': {'family': 'Roboto Mono',
                                             'digits': 'DSEG7Classic'}}))
    qt_app.processEvents()
    assert board.rows[0].lane_label.font().family() == 'DSEG7 Classic'
    assert board.rows[0].club_label.font().family() == 'Roboto Mono'
    assert board.rows[0].lane_label.font().pixelSize() > 0


def test_a_new_theme_colour_reaches_an_existing_delta(board, qt_app):
    """Changing the colour in Settings → Theme must repaint what is on screen."""
    board.apply_update({'lane_place1': '1', 'lane_time1': '1:12.44',
                        'lane_delta_seconds1': -0.46, 'lane_delta_better1': True})
    qt_app.processEvents()

    board.set_config(Config({'num_lanes': 6,
                             'theme_colors': {'delta_better': '#00ff00'}}))
    qt_app.processEvents()
    assert '#00ff00' in board.rows[0].delta_label.styleSheet()
    assert board.rows[0].delta_label.text() == '-0.46', 'the value must survive too'


# ── A podium that outlives its heat ────────────────────────────────────────────
# `_podium_shown` is a latch, because `highlight_podium` is evaluated at the end of
# every frame and without one the 400ms stagger would restart on each. The browser
# needs no latch — it reveals on a state change and is idempotent — so the latch is
# the one place the two can drift, and it drifts whenever a heat ends without the
# heat *key* changing. `clear_podium()` is where the two meet again.

def test_a_race_starting_takes_the_previous_podium_off(board, qt_app, settle_podium):
    """`mode_to_running()` calls `clear_podium()`; so must we.

    A false start re-swum under the same number used to swim under the last
    attempt's gold and silver — the key never changed, so nothing cleared them.
    """
    _finish_a_heat(board, qt_app)
    settle_podium(board)
    assert board.rows[0]._current_bg.lower() == board.cfg.color('podium_gold').lower()

    board.apply_update({'lane_running1': True, 'lane_running2': True,
                        'running_time': '0.00'})
    settle_podium(board)
    assert not board._podium_shown, 'the reveal stayed spent into the new race'
    for row in board.rows[:2]:
        assert row._current_bg.lower() == row._base_bg.lower(), 'swimming under a stale tint'


def test_the_same_heat_re_swum_gets_its_own_podium(board, qt_app, settle_podium):
    """The sharpest form of it: the places come back in a different order.

    With the reveal latched and the tints never dropped, the new winner inherited
    silver from whoever finished second last time, and the runner-up held gold.
    """
    _finish_a_heat(board, qt_app)
    settle_podium(board)

    board.apply_update({'lane_running1': True, 'lane_running2': True,
                        'running_time': '0.00'})
    qt_app.processEvents()
    board.apply_update({'lane_running1': False, 'lane_running2': False,
                        'lane_time1': '57.40', 'lane_place1': '2',
                        'lane_time2': '56.90', 'lane_place2': '1'})
    settle_podium(board)

    assert board.rows[0]._current_bg.lower() == board.cfg.color('podium_silver').lower()
    assert board.rows[1]._current_bg.lower() == board.cfg.color('podium_gold').lower()


def test_a_console_board_reset_clears_the_tints(board, qt_app, settle_podium):
    """The `r` packet — `reset_lanes()` blanks every time and place in one frame.

    No heat change, so the dissolve never runs and `LaneRow.clear()` never fires.
    The tint has to come off with the place that earned it.
    """
    _finish_a_heat(board, qt_app)
    settle_podium(board)

    reset = {}
    for lane in range(1, 7):
        reset[f'lane_time{lane}']    = ''
        reset[f'lane_place{lane}']   = ' '     # the decoders' blank, not ''
        reset[f'lane_running{lane}'] = False
    board.apply_update(reset)
    settle_podium(board)

    for row in board.rows:
        assert row._current_bg.lower() == row._base_bg.lower(), 'tint survived the reset'


def test_the_reveal_still_waits_for_the_heat_to_be_over(board, qt_app):
    """Dropping a stale tint must not become tinting from the update handler.

    That was the original bug the reveal was held back to fix: the first finisher's
    row going gold while the rest of the heat is still in the water.
    """
    board.apply_update({'current_event': '4', 'current_heat': '1',
                        'lane_name1': 'Roy, Zoé', 'lane_name2': 'Côté, Léa'})
    board.apply_update({'lane_running1': True, 'lane_running2': True,
                        'running_time': '0.00'})
    qt_app.processEvents()
    board.apply_update({'lane_running1': False, 'lane_time1': '58.12',
                        'lane_place1': '1'})
    qt_app.processEvents()

    assert not board._podium_shown, 'revealed while lane 2 was still swimming'
    assert board.rows[0]._current_bg.lower() == board.rows[0]._base_bg.lower()
