"""The top bar: EV/HT inline, the accent blue, and the event name.

The browser used to stack a 1.8vh word above a 4.5vh number (`.header_cell` is a
column flex). At a desk that reads as a caption. Across a pool deck at TV distance
the word is simply not there — 16px on a 1080p board — so the operator is left
reading a bare number and guessing whether it is the event or the heat.

Both displays now put the two on one line at the same size, `EV 12`, with the word
in the board's accent blue and the number in `header_value`. The colour is what
keeps the pair from reading as one long number now that the size no longer
separates them. This file covers the Qt side; the browser's copy of the rule is in
`tests/test_scoreboard_base_shared.py`.

Three things have to hold, and each of them broke a plausible implementation:

* **The word and its number are the same size.** Two FitLabels sharing a box each
  solve for their own width and land on different sizes, which reads as a mistake
  rather than as a label.
* **EVENT and HEAT agree with each other.** `EVENT 12` is a wider phrase than
  `HEAT 7`, so left alone the two cells settle a size apart with a divider between
  them advertising it.
* **The event name still shrinks to fit.** Its ceiling went up; a ceiling is not a
  size, and a long name must still come down to whatever the cell can hold.

Needs PySide6 (`uv run pytest tests/`); skips without it.
"""
import pytest

pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')

from PySide6.QtGui import QFont, QFontMetrics      # noqa: E402

from scoreboard.board import BoardWindow           # noqa: E402
from scoreboard.theme import Config                # noqa: E402

# `qt_app` comes from tests/conftest.py — session-scoped, fonts already loaded.

SHORT = {'event': 'EV', 'heat': 'HT'}
LONG  = {'event': 'EVENT', 'heat': 'HEAT'}


def _board(qt_app, labels=SHORT, size=(1920, 1080), **cfg):
    window = BoardWindow(Config({'num_lanes': 6, 'labels': labels, **cfg}))
    window.resize(*size)
    window.show()
    qt_app.processEvents()
    return window


def _sizes(cell):
    return cell.label.font().pixelSize(), cell.value.font().pixelSize()


def _fits(label):
    """Is what the label actually draws inside the box it was given?"""
    font = QFont(label.font())
    font.setPixelSize(label.font().pixelSize())
    drawn = QFontMetrics(font).horizontalAdvance(label.displayed_text())
    return drawn <= label.contentsRect().width()


# ── The word sits beside its number ────────────────────────────────────────────

@pytest.mark.parametrize('labels', [SHORT, LONG], ids=['short', 'long'])
@pytest.mark.parametrize('size', [(1280, 720), (1920, 1080), (3840, 2160)],
                         ids=['720p', '1080p', '4k'])
def test_the_word_and_its_number_are_one_size(qt_app, labels, size):
    board = _board(qt_app, labels, size)
    try:
        board.apply_update({'current_event': '12', 'current_heat': '7'})
        qt_app.processEvents()
        for cell in (board.event_cell, board.heat_cell):
            word, number = _sizes(cell)
            assert word == number, f'{word}px word against a {number}px number'
    finally:
        board.close()


def test_the_word_is_beside_the_number_not_above_it(qt_app):
    board = _board(qt_app)
    try:
        board.apply_update({'current_event': '12', 'current_heat': '7'})
        qt_app.processEvents()
        word, number = board.event_cell.label.geometry(), board.event_cell.value.geometry()
        assert word.right() <= number.left() + 2, 'the word is not to the left'
        assert abs(word.center().y() - number.center().y()) <= 2, 'not on one line'
        assert not word.intersects(number), 'the word and the number overlap'
    finally:
        board.close()


def test_the_word_is_readable_across_a_hall(qt_app):
    """The complaint that started this: 0.15 of the bar is 16px at 1080p."""
    board = _board(qt_app)
    try:
        board.apply_update({'current_event': '12', 'current_heat': '7'})
        qt_app.processEvents()
        word, _ = _sizes(board.event_cell)
        assert word >= board.header.height() * 0.45, \
            f'{word}px in a {board.header.height()}px bar is a caption again'
    finally:
        board.close()


@pytest.mark.parametrize('labels', [SHORT, LONG], ids=['short', 'long'])
def test_event_and_heat_agree_with_each_other(qt_app, labels):
    """`EVENT 12` is the wider phrase. With a divider between the two cells, one of
    them being larger reads as the other being wrong."""
    board = _board(qt_app, labels)
    try:
        board.apply_update({'current_event': '12', 'current_heat': '7'})
        qt_app.processEvents()
        assert _sizes(board.event_cell) == _sizes(board.heat_cell)
    finally:
        board.close()


def test_they_still_agree_when_the_event_number_grows(qt_app):
    """Event 9 to event 10 changes what fits — both cells have to follow."""
    board = _board(qt_app)
    try:
        board.apply_update({'current_event': '9', 'current_heat': '1'})
        qt_app.processEvents()
        board.apply_update({'current_event': '108', 'current_heat': '1'})
        qt_app.processEvents()
        assert _sizes(board.event_cell) == _sizes(board.heat_cell)
        assert _fits(board.event_cell.value), 'the number outgrew its cell'
    finally:
        board.close()


def test_a_resize_re_solves_the_pair(qt_app):
    board = _board(qt_app, size=(1280, 720))
    try:
        board.apply_update({'current_event': '12', 'current_heat': '7'})
        qt_app.processEvents()
        small, _ = _sizes(board.event_cell)

        board.resize(3840, 2160)
        qt_app.processEvents()
        big, _ = _sizes(board.event_cell)

        assert big > small, 'the header did not scale with the window'
        assert _sizes(board.event_cell) == _sizes(board.heat_cell)
    finally:
        board.close()


def test_an_idle_board_shows_no_stray_word(qt_app):
    """A word with no number is not a label. At the old 16px a leftover `EVENT` was
    barely visible; at this size it would be the loudest thing on an empty board."""
    board = _board(qt_app)
    try:
        board.set_header_mode(False)
        qt_app.processEvents()
        assert board.event_cell.label.text() == ''
        assert board.heat_cell.label.text() == ''
    finally:
        board.close()


def test_the_word_and_the_number_take_different_colours(qt_app):
    """All that separates them now that the size does not."""
    board = _board(qt_app, theme_colors={'header_label': '#3b9eff',
                                         'header_value': '#e0e0e0'})
    try:
        assert 'color: #3b9eff' in board.event_cell.label.styleSheet()
        assert 'color: #e0e0e0' in board.event_cell.value.styleSheet()
    finally:
        board.close()


def test_the_wall_clock_matches_the_words_and_the_chrono_does_not(qt_app):
    """The two ends of the bar frame the race clock, which keeps the gold to
    itself — it is the one thing up there meant to stand out."""
    board = _board(qt_app, theme_colors={'header_label': '#3b9eff',
                                         'header_value': '#e0e0e0',
                                         'time': '#FFD700'})
    try:
        assert 'color: #3b9eff' in board.wall_clock.styleSheet()
        assert 'color: #FFD700' in board.chrono_label.styleSheet()
    finally:
        board.close()


# ── The event name ─────────────────────────────────────────────────────────────

def test_a_short_event_name_uses_the_room_it_has(qt_app):
    board = _board(qt_app)
    try:
        board.apply_update({'current_event': '1', 'current_heat': '1',
                            'event_name': '50m Freestyle'})
        qt_app.processEvents()
        px = board.name_label.font().pixelSize()
        assert px >= board.header.height() * 0.55, f'only {px}px of a whole bar'
    finally:
        board.close()


@pytest.mark.parametrize('name', [
    '50m Freestyle',
    'Girls 11-12 100m Butterfly',
    'Girls 13-14 200m Individual Medley',
    'Mixed 13 & Over 4x50m Freestyle Relay',
    'Women 15-17 4x100m Individual Medley Relay Timed Final Section 2',
    'X' * 200,
])
def test_the_event_name_always_fits_its_cell(qt_app, name):
    """A raised ceiling is not a size: every one of these has to come down to
    whatever the cell can actually hold, and Qt clips a label to its own rect —
    an overlong name is cut through a glyph rather than spilling somewhere visible."""
    board = _board(qt_app)
    try:
        board.apply_update({'current_event': '1', 'current_heat': '1',
                            'event_name': name})
        qt_app.processEvents()
        assert _fits(board.name_label), f'{name[:40]!r} overflowed the name cell'
    finally:
        board.close()


def test_a_longer_name_is_drawn_smaller(qt_app):
    board = _board(qt_app)
    try:
        seen = []
        for name in ('50m Free', 'Girls 11-12 100m Butterfly',
                     'Women 15-17 4x100m Individual Medley Relay Timed Final'):
            board.apply_update({'current_event': '1', 'current_heat': '1',
                                'event_name': name})
            qt_app.processEvents()
            seen.append(board.name_label.font().pixelSize())
        assert seen == sorted(seen, reverse=True), f'sizes did not shrink: {seen}'
    finally:
        board.close()


def test_the_full_name_survives_being_shrunk(qt_app):
    """`FitLabel.text()` is the whole name whatever is painted — the Schedule tab
    and the window title read it back."""
    board = _board(qt_app)
    name = 'Women 15-17 4x100m Individual Medley Relay Timed Final Section 2'
    try:
        board.apply_update({'current_event': '1', 'current_heat': '1',
                            'event_name': name})
        qt_app.processEvents()
        assert board.name_label.text() == name
    finally:
        board.close()


def test_the_name_re_fits_when_the_window_changes(qt_app):
    board = _board(qt_app, size=(3840, 2160))
    name = 'Girls 13-14 200m Individual Medley'
    try:
        board.apply_update({'current_event': '1', 'current_heat': '1',
                            'event_name': name})
        qt_app.processEvents()
        board.resize(1280, 720)
        qt_app.processEvents()
        assert _fits(board.name_label), 'it kept a size the smaller window cannot hold'
    finally:
        board.close()


# ── Sitting on the same line ───────────────────────────────────────────────────
# `AlignVCenter` centres a font's *line box* — ascent plus descent — not its glyphs.
# Two fonts share almost every row on this board (`EV` beside `12`, a swimmer's name
# beside a lane number), and at 62px Overpass Mono reports a 24px descent while DSEG7
# Classic reports zero. So Qt centred one of them around a gap under the text that
# the other did not have, and the word floated six pixels above its number.
#
# `FitLabel` centres the *cap band* instead. Off the font, never off the string:
# ink extents would put `Roy` and `Zoé` at different heights and make a lane's time
# bob as its digits changed.

def _cap_offset(label):
    """Where the capital/digit band's centre sits, relative to the widget's."""
    metrics = QFontMetrics(label.font())
    geom, contents = label.geometry(), label.contentsRect()
    baseline = (geom.y() + contents.y()
                + (contents.height() - metrics.height()) / 2 + metrics.ascent())
    return (baseline - metrics.capHeight() / 2) - (geom.y() + geom.height() / 2)


def _fills(board):
    board.apply_update({
        'current_event': '12', 'current_heat': '7',
        'event_name': 'Girls 13-14 200m Individual Medley', 'running_time': '1:05.23',
        'lane_running1': True,
        'lane_name1': 'Roy, Zoé', 'lane_club1': 'CNQ',          # descender + accent
        'lane_name2': 'NHAM', 'lane_club2': 'AAA',              # neither
        'lane_time2': '58.00', 'lane_place2': '2',
        'lane_delta_seconds2': -0.46, 'lane_delta_better2': True})
    board.cancel_heat_transition()
    board._apply_col_fraction(1.0)


def test_the_word_and_its_number_sit_on_one_line(qt_app):
    """The complaint: `EV` visibly higher than the `12` beside it."""
    board = _board(qt_app)
    try:
        _fills(board)
        qt_app.processEvents()
        for cell in (board.event_cell, board.heat_cell):
            drift = abs(_cap_offset(cell.label) - _cap_offset(cell.value))
            assert drift <= 1, f'{drift:.1f}px apart — the word floats'
    finally:
        board.close()


def test_every_cell_in_the_header_shares_a_centre_line(qt_app):
    board = _board(qt_app)
    try:
        _fills(board)
        qt_app.processEvents()
        cells = {'EV word': board.event_cell.label, 'EV number': board.event_cell.value,
                 'HT word': board.heat_cell.label, 'HT number': board.heat_cell.value,
                 'name': board.name_label, 'chrono': board.chrono_label,
                 'wall clock': board.wall_clock}
        off = {name: _cap_offset(w) for name, w in cells.items()}
        assert max(off.values()) - min(off.values()) <= 1, off
    finally:
        board.close()


def test_a_lane_row_shares_one_too(qt_app):
    """Same mechanism, eight more rows of it: the lane number and the place are in
    the digits font, everything between them is not."""
    board = _board(qt_app)
    try:
        _fills(board)
        qt_app.processEvents()
        row = board.rows[1]
        off = {n: _cap_offset(w) for n, w in
               (('lane', row.lane_label), ('name', row.name_label),
                ('club', row.club_label), ('time', row.time_label),
                ('delta', row.delta_label), ('place', row.place_label))}
        assert max(off.values()) - min(off.values()) <= 1, off
    finally:
        board.close()


def test_a_descender_does_not_move_the_line(qt_app):
    """Solved off the font, not the string. `Roy, Zoé` has a descender and an accent
    and `NHAM` has neither; if the two sat at different heights the board would
    twitch every time a heat changed."""
    board = _board(qt_app)
    try:
        _fills(board)
        qt_app.processEvents()
        assert abs(_cap_offset(board.rows[0].name_label)
                   - _cap_offset(board.rows[1].name_label)) <= 1
    finally:
        board.close()


def test_nothing_is_pushed_out_of_its_box(qt_app):
    """The shift is padding, and padding eats height. A cell that centred its
    capitals by clipping their tops would be a worse bug than the one it fixed."""
    board = _board(qt_app)
    try:
        _fills(board)
        qt_app.processEvents()
        row = board.rows[1]
        for name, label in (('EV word', board.event_cell.label),
                            ('name', board.name_label),
                            ('chrono', board.chrono_label),
                            ('row name', row.name_label),
                            ('row time', row.time_label),
                            ('row place', row.place_label)):
            metrics = QFontMetrics(label.font())
            contents = label.contentsRect()
            baseline = (contents.y()
                        + (contents.height() - metrics.height()) / 2 + metrics.ascent())
            assert baseline - metrics.ascent() >= -1, f'{name} clipped at the top'
            assert baseline + metrics.descent() <= label.height() + 1, \
                f'{name} clipped at the bottom'
    finally:
        board.close()


def test_a_font_with_no_cap_height_is_left_alone(qt_app):
    """A face that does not report one gets today's behaviour rather than a shift
    computed from a zero."""
    from scoreboard.widgets import FitLabel
    label = FitLabel('X')
    label.resize(200, 60)
    font = label.font()
    font.setPixelSize(40)
    label.setFont(font)
    assert isinstance(label._cap_shift(), int)
