"""The scoreboard window — header bar plus one row per lane.

Layout mirrors ``server/templates/live.html`` — the page the Chromium kiosk
actually rendered, since ``/`` redirects there, and now the only board the Pi
serves.

Four things depart from the browser, deliberately:

* Names shrink to fit instead of being ellipsised (see :mod:`scoreboard.widgets`).
* Deltas come from the structured ``lane_delta_seconds<i>`` / ``lane_delta_better<i>``
  fields rather than the HTML ``lane_delta<i>`` blob, as ``docs/api.md`` §5.1
  instructs native clients to do.
* Heats dissolve into one another instead of cutting. ``/live`` collapses the
  columns instantly; a fade reads better across a hall at TV distance, where an
  instant cut looks like a glitch. See the README.
* The EVENT/HEAT word sits *inline* with its number at the same size — ``EV 12`` —
  rather than as a small caption above it. The browser's 1.8vh word is unreadable
  across a pool deck, which is the only distance this display is ever read at.
* The running clock shows tenths (``1:05.20``), not hundredths. The console reports
  its own clock to tenths while a race is on, so the last digit was ours alone and
  it changed twenty times a second under the number everyone is watching.

``notes/scoreboard_parity.md`` is the full ledger of what matches and what does not.
"""
import os
import re
import time

from PySide6.QtCore import (QEasingCurve, QPropertyAnimation, Qt, QTimer,
                          QVariantAnimation, Signal)
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect,
                               QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout,
                               QWidget)

# Qt's "no maximum" sentinel. PyQt5 exported it from QtWidgets; PySide6 does not,
# so it is spelled out here — it is a fixed part of the Qt API, not a guess.
QWIDGETSIZE_MAX = 16777215

# E402 below: the sentinel above is kept with the Qt imports it belongs to,
# which leaves this group past the top of the file. Nothing depends on the
# order — it reads better this way.
from .format import fmt_clock, fmt_delta, parse_clock  # noqa: E402
from .menu import OperatorMenu  # noqa: E402
from .splash import SplashOverlay  # noqa: E402
from .version import cached_version  # noqa: E402
from .theme import Config  # noqa: E402
from .widgets import FitLabel  # noqa: E402

# Clock repaint cadence. 50ms matches the browser: fast enough that hundredths
# look continuous, slow enough to stay cheap on a Pi.
_CLOCK_TICK_MS = 50
_WALL_CLOCK_TICK_MS = 10_000        # HH:MM only — no need to tick every second

# Header bar height, as a fraction of the window. The browser's 85px at 1080p is
# the floor of what is readable across a pool deck, so this is deliberately larger.
_H_BAR = 0.105

# Text sizes as fractions of the BAR, which is safe because the bar's own height
# is a fixed fraction of the window (above) rather than derived from its content.
# Sizing them off a content-derived bar is what made the whole header collapse.
# Ratios keep the browser's proportions: 12px label under 48px digits in an 85px
# bar. Raising _H_BAR alone now scales the entire header.
# The EV/HT word sits *inline* with its number at the same size (see HeaderCell),
# so there is no separate label ratio any more: `EV 12` is one phrase at _R_DIGITS.
# It used to be 0.15 of the bar — 16px at 1080p — which is legible on a desk and not
# at all across a pool deck, which is the only place this display is ever read.
_R_VALUE  = 0.62    # event name
_R_DIGITS = 0.57    # event/heat cells, both clocks

# Header cell widths, as percentages of the bar. Fixed rather than content-derived,
# so nothing shifts when the event number gains a digit or the race clock blanks
# between heats. `.header_cell` in timing_display.css now carries the same five
# numbers — this is the one place the browser followed the display rather than the
# other way round. They sum to 100, so the weights are the percentages directly.
# EVENT and HEAT are wider than the browser's because their word now sits beside
# the number rather than above it; the wall clock gives up what they take, being the
# one cell nobody reads a race off. Measured, not guessed: at these weights `EV 12`
# and `HT 7` both reach the full _R_DIGITS size, and the race clock gets *more* room
# than before (62px against 53px at 1080p).
_HW_EVENT, _HW_HEAT, _HW_NAME, _HW_CHRONO, _HW_CLOCK = 13, 13, 48, 16, 10

# `.header_cell`'s `6px 1vw` padding, as fractions of the bar height and the window
# width. 6px in the browser's 85px bar is 7% of it.
_HDR_PAD_Y = 0.07
# `.header_cell`'s `1vw` — the same fraction on both displays. It was `2vw` in the
# browser and half that here: that padding is a fraction of the *window*, so it costs
# the same on every cell however narrow, and five cells at 2vw a side spend a fifth of
# the bar on whitespace, which is what squeezed the header's text in the first place.
# The dividers already separate the cells; the whitespace was doing nothing. The
# browser has since followed this value down.
_HDR_PAD_X = 0.01
# Space between the EV/HT word and its number, as a fraction of their shared size.
_HDR_INLINE_GAP = 0.45

# Column stretch weights = the vw widths of `/live`, the page the Chromium kiosk
# actually rendered: `.lane-column` 5vw and `.club-column` 8vw from
# timing_display.css, 6/17/15vw for place/time/delta from live.html's expand_cols().
# Name takes the remainder, as it does in CSS. They sum to 100, so the weights are
# the percentages directly.
#
# Taken from `/live`, which is the reference for this display; see the README.
# A retired second template used to give delta 9vw, so a stray 9 here is a sign
# someone measured the wrong page.
_W_LANE, _W_NAME, _W_CLUB, _W_TIME, _W_DELTA, _W_PLACE = 5, 49, 8, 17, 15, 6

# Per-column padding, as fractions of the row width: `.lane-name-cell`'s `0 2vw`
# and `.club-column`'s `padding-right: 1vw`. The delta column takes none any more —
# it is centred now that it shares the cell with the lap count, and a right padding
# on a centred label just shifts it off centre. Rows themselves have no margins and
# no spacing, so the weights above apply to the full width exactly as the vw widths
# do in the browser.
_PAD_NAME  = 0.02
_PAD_CLUB  = 0.01

# The three columns that slide in when a race starts, and how long that takes.
# 500ms matches `.timing-anim { transition: … 0.5s ease }` in timing_display.css.
_COL_ANIM_MS = 500

# Heat transition: the podium tints
# fade, then the columns close, then the table fades out, is swapped while
# invisible, and fades back in. Each step is 500ms in the browser.
_PODIUM_FADE_MS  = 500
_CONTENT_FADE_MS = 500
_COL_TOTAL_WEIGHT = _W_LANE + _W_NAME + _W_CLUB + _W_TIME + _W_DELTA + _W_PLACE

# Podium reveal, mirroring highlight_podium() in live.html: gold, silver and bronze
# arrive 400ms apart, each easing in over the 0.5s the browser's `background-color`
# transition takes.
_PODIUM_STEP_MS    = 400
_PODIUM_FADE_IN_MS = 500

# Lane time colours while a race is on, from `.time-running` and the
# `time-lock-flash` keyframes. Hardcoded in timing_display.css too — they are not
# theme keys there, and inventing settings that exist on only one of the two
# displays would be worse than matching the browser exactly.
_TIME_RUNNING   = '#a0a0a0'
_TIME_LOCK_FROM = '#ffffff'
_TIME_LOCK_MS   = 800

# The link-lost badge and the frozen clock share one colour, so the two obviously
# belong to each other. It comes from the theme (`connection_lost`, Settings →
# Theme → Status), like every other colour on the board — the stock value is a red
# that stands clear of the gold `time` it replaces.

_LANE_SUFFIX = re.compile(r'(\d+)$')

# Fractions of a row's height used as the font ceiling for each kind of cell.
_FONT_MAIN = 0.52
_FONT_ALT  = 0.7 * _FONT_MAIN   # `.name-sub` is `0.7em` of the row's own text

# Column titles are 3vh against the rows' 5vh — 60% of the row text. The header row
# is half a lane row (stretch 1 against 2), so 62% of its own height lands there.
_FONT_HEADER = 0.62

# How the name cell splits between the swimmer and the relay line under it: 5vh of
# name over 3.5vh of sub-name, the browser's own proportion.
_NAME_STRETCH, _ALT_STRETCH = 50, 35

# Largest pixel size that sits comfortably in a cell of a given height, allowing for
# the ~1.25 line spacing a font needs above and below its em box. Only the name cell
# needs this: it is the one cell whose height is not the whole row.
_FONT_OF_CELL = 0.8


def _animate_color(owner, start, end, duration_ms, curve, paint):
    """Ease a colour from *start* to *end*, handing each step to *paint*.

    Qt stylesheets do not animate, so every colour transition the browser gets free
    from a CSS `transition` or `@keyframes` is interpolated here and re-applied.
    """
    anim = QVariantAnimation(owner)
    anim.setDuration(duration_ms)
    anim.setStartValue(QColor(start))
    anim.setEndValue(QColor(end))
    anim.setEasingCurve(curve)
    anim.valueChanged.connect(paint)
    anim.start()
    return anim


def _restyle(label, font):
    """Give a plain ``QLabel`` a new face without losing its computed pixel size.

    A ``QFont`` carries a size as well as a family, so assigning one throws away
    whatever the last resize worked out — about 13px, which on a pool-deck TV is
    unreadable. :class:`~scoreboard.widgets.FitLabel` re-fits itself for exactly this
    reason; the status overlay and the test badge are plain labels sized from the
    window, so they need the size carried across by hand.

    It matters because ``apply_theme`` runs on every ``/config`` reload — including
    the first, which lands seconds after the kiosk window opens while the waiting
    message is the only thing on screen.
    """
    size = label.font().pixelSize()
    if size > 0:
        font.setPixelSize(size)
    label.setFont(font)


def _pad_columns(width, name, club):
    """Apply `.lane-name-cell`'s `0 2vw` and `.club-column`'s `1vw` to a row.

    Shared by the lane rows and the header row so their padding cannot drift apart —
    the two must line up character for character.
    """
    pad = int(width * _PAD_NAME)
    name.setContentsMargins(pad, 0, pad, 0)
    club.setContentsMargins(0, 0, int(width * _PAD_CLUB), 0)


class LaneRow(QFrame):
    """One lane. Column widths come from the shared stretch weights, so every
    row (and the header) lines up without a grid."""

    def __init__(self, lane: int, cfg: Config, parent=None):
        super().__init__(parent)
        self.lane = lane
        self.cfg  = cfg
        # While True the time cell belongs to the board's clock ticker, not to
        # `lane_time<i>` — see BoardWindow._tick_clock.
        self.running = False
        # Last `lane_delta_better<i>` seen, so a restyle keeps the right colour.
        self._delta_better = None
        # Last `lane_place<i>` seen. Recorded rather than acted on: the podium tint
        # arrives at the end of the heat, not the moment a place lands — see
        # BoardWindow.highlight_podium.
        self._place = ''
        # Whether the delta cell is currently showing a lap count rather than a
        # delta, and whether that lap is the final stretch — `apply_theme` needs both
        # to know which of the three colours the cell owes after a reload.
        self._lap_shown = False
        self._lap_final = False
        self._podium_anim = None
        self._time_anim   = None
        self.setAutoFillBackground(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        # No margins and no spacing: the stretch weights must apply to the full row
        # width, exactly as the vw column widths do in CSS. What padding there is
        # belongs to individual cells — see _pad_columns.
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.lane_label = FitLabel(str(lane))
        self.lane_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Name and relay members share one cell, stacked — matching the browser's
        # `.lane-name-cell` with its `.name-sub` second line.
        name_box = QVBoxLayout()
        name_box.setContentsMargins(0, 0, 0, 0)
        name_box.setSpacing(0)
        self.name_label = FitLabel()
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.alt_label = FitLabel()
        self.alt_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.alt_label.hide()
        # Split in the browser's own proportion — 5vh of name over 3.5vh of relay
        # line — rather than evenly. The name's font ceiling is then taken from the
        # height it actually gets: FitLabel only ever solves for *width*, so a
        # ceiling derived from the whole row would size a relay team name to roughly
        # twice its own cell and clip it top and bottom.
        name_box.addWidget(self.name_label, _NAME_STRETCH)
        name_box.addWidget(self.alt_label, _ALT_STRETCH)
        # `.name-sub` is drawn at `opacity: 0.7`. An effect rather than a dimmed
        # colour, so it composites against whatever the row background happens to be
        # — including a podium tint.
        self._alt_opacity = QGraphicsOpacityEffect(self.alt_label)
        self._alt_opacity.setOpacity(0.7)
        self.alt_label.setGraphicsEffect(self._alt_opacity)
        self.name_cell = QWidget()
        self.name_cell.setLayout(name_box)

        self.club_label = FitLabel()
        self.club_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # All shrink-to-fit. Qt clips a label to its own rect, so an oversized
        # value is not lost into the neighbour — it is cut through a glyph, which
        # is worse: `1:12.44` beside a clipped delta read as `1:12.44).06`.
        self.time_label = FitLabel()
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.delta_label = FitLabel()
        self.delta_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.place_label = FitLabel()
        self.place_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Size policy Ignored in BOTH directions. Cell fonts are derived from the
        # row height, so a font-driven minimum height would feed straight back into
        # the layout and inflate the window — a 1080p board came out 1460px tall,
        # which on a fullscreen TV means the bottom lane is cut off. Rows are sized
        # purely by their stretch weights.
        for widget, weight in ((self.lane_label,  _W_LANE),
                               (self.name_cell,   _W_NAME),
                               (self.club_label,  _W_CLUB),
                               (self.time_label,  _W_TIME),
                               (self.delta_label, _W_DELTA),
                               (self.place_label, _W_PLACE)):
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            widget.setMinimumSize(0, 0)
            row.addWidget(widget, weight)
        for label in (self.name_label, self.alt_label):
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            label.setMinimumSize(0, 0)
        self.setMinimumSize(0, 0)

        self.apply_theme()
        self.clear()

    # ── Appearance ─────────────────────────────────────────────────────────────

    def apply_theme(self):
        cfg  = self.cfg
        self._base_bg = cfg.color('row_odd' if self.lane % 2 else 'row_even')
        # Re-assert whatever tint is correct *now*: a `/config` reload runs this on a
        # board that may already be showing a podium, and repainting it to the plain
        # stripe would drop the tint until the next heat. Same reasoning as
        # `_style_delta` remembering `lane_delta_better<i>`.
        key = self.podium_key()
        self._set_bg(cfg.color(key) if key else self._base_bg)

        text_color = cfg.color('row_text')
        for label in (self.lane_label, self.name_label, self.alt_label,
                      self.club_label, self.place_label):
            label.setStyleSheet(f'color: {text_color}; background: transparent;')
            label.setFont(QFont(cfg.family))
        # The lane number and the place take the *digits* font, matching
        # `tbody td:first-child, [id^="lane_place"]` in timing_display.css — on the
        # stock theme that is a seven-segment face, and it is the single most
        # visible thing about the board.
        for label in (self.lane_label, self.place_label):
            label.setFont(QFont(cfg.digits_family))
        self.time_label.setFont(QFont(cfg.timing_family))
        self.delta_label.setFont(QFont(cfg.timing_family))
        # Whichever tenant the delta cell currently has, in the colours the reloaded
        # theme says. Restated here for the same reason as the podium tint above.
        if self._lap_shown:
            self._style_lap(self._lap_final)
        else:
            self._style_delta(self._delta_better)
        # Grey while the clock owns the cell, otherwise the time colour. Restated
        # here for the same reason as the tint above.
        self._stop_time_flash()
        self._style_time(_TIME_RUNNING if self.running else cfg.color('time'))

        self.name_cell.setVisible(cfg.show_name)
        self.club_label.setVisible(cfg.show_club)
        self.delta_label.setVisible(cfg.show_delta)
        self.place_label.setVisible(cfg.show_position)

    def animated_cells(self):
        """(widget, weight) for the columns that slide in at race start."""
        return ((self.time_label,  _W_TIME),
                (self.delta_label, _W_DELTA),
                (self.place_label, _W_PLACE))

    def resizeEvent(self, event):     # noqa: N802 — Qt naming
        super().resizeEvent(event)
        _pad_columns(self.width(), self.name_cell, self.club_label)
        self.set_row_height(self.height())

    def set_row_height(self, height: int):
        """Rescale fonts to the row height.

        Driven by this row's own resizeEvent, not the window's. Reading a child's
        height from the parent's resizeEvent gets you the geometry from *before*
        the layout ran — that returned 480 for a row that ended up 161px tall, so
        every cell was sized about three times too large.
        """
        main = max(8, int(height * _FONT_MAIN))
        for label in (self.lane_label, self.club_label, self.time_label,
                      self.delta_label, self.place_label):
            label.set_max_px(main)

        # The name shares its cell with the relay line, so cap it from the slice it
        # actually gets rather than from the row. Computed from the stretch weights
        # instead of read back off the widgets: the layout has not necessarily run
        # by the time this fires, which is the same trap set_row_height itself
        # exists to avoid.
        if self.alt_label.isVisibleTo(self):
            share = _NAME_STRETCH / (_NAME_STRETCH + _ALT_STRETCH)
        else:
            share = 1.0
        self.name_label.set_max_px(
            max(8, min(main, int(height * share * _FONT_OF_CELL))))
        self.alt_label.set_max_px(
            max(8, min(int(height * _FONT_ALT),
                       int(height * (1 - share) * _FONT_OF_CELL))))

    def _style_delta(self, better):
        """Colour the delta from `lane_delta_better<i>` (Settings → Theme).

        Remembers the value: `apply_theme` re-runs on every `/config` reload, and
        passing None there would repaint a faster swim in the *slower* colour.
        It happens to recover today because `set_config` calls `refresh()` straight
        after — an ordering accident, not a guarantee.

        A lap count in the cell outranks all of that: it is not a delta and must not
        take a delta's colour. `.td_delta.lap-count` wins the same way in CSS.
        """
        self._delta_better = better
        if self._lap_shown:
            return
        color = self.cfg.color('delta_better' if better else 'delta_worse')
        self.delta_label.setStyleSheet(f'color: {color}; background: transparent;')

    # ── Lap count ──────────────────────────────────────────────────────────────
    # The delta cell has two tenants: this lane's lengths while it is swimming, then
    # the time difference once it has finished. See the `renderDelta` pair in
    # scoreboard_base.html and live.html — this is the same rule, and
    # `notes/scoreboard_parity.md` records what the three owe each other.

    def _style_lap(self, final: bool):
        """Paint the cell as a lap: the header's accent blue, gold on the last one.

        `header_label`, the same key the EVENT/HEAT words take, because a lap is a
        label rather than a result — and `time`, the colour a stopped chrono takes,
        once this is the final stretch. `.td_delta.lap-count` and its `.lap-final`
        resolve to the same two variables in CSS.
        """
        colour = self.cfg.color('time' if final else 'header_label')
        self.delta_label.setStyleSheet(f'color: {colour}; background: transparent;')

    def set_lap(self, text, final: bool = False):
        """Show *text* in the delta cell, or hand the cell back when it is None."""
        if text is None:
            if self._lap_shown:
                self._lap_shown = False
                self._lap_final = False
                self._style_delta(self._delta_better)
            return
        was, was_final = self._lap_shown, self._lap_final
        self._lap_shown, self._lap_final = True, final
        self.delta_label.setText(str(text))
        # Only restyle on an edge: the stylesheet is re-parsed on every set, and this
        # runs on every frame a swimming lane produces.
        if not was or final != was_final:
            self._style_lap(final)

    # ── Podium tint ────────────────────────────────────────────────────────────

    def podium_place(self):
        """1, 2 or 3 when this row belongs on the podium, else ``None``."""
        if not self.cfg.show_podium:
            return None
        place = (self._place or '').strip()
        return int(place) if place in ('1', '2', '3') else None

    def podium_key(self):
        """The theme key for this row's tint, or ``None`` for the plain stripe."""
        place = self.podium_place()
        return {1: 'podium_gold', 2: 'podium_silver',
                3: 'podium_bronze'}.get(place)

    def _set_bg(self, colour: str):
        """Paint the row background at once, cancelling any fade in flight."""
        self._stop_podium_fade()
        self._current_bg = colour
        self.setStyleSheet(f'background-color: {colour};')

    def _paint_bg(self, colour: QColor):
        self._current_bg = colour.name()
        self.setStyleSheet(f'background-color: {colour.name()};')

    def _fade_bg(self, target: str, duration_ms: int):
        start = QColor(getattr(self, '_current_bg', self._base_bg))
        end   = QColor(target)
        if start == end:
            return
        self._stop_podium_fade()
        self._podium_anim = _animate_color(self, start, end, duration_ms,
                                           QEasingCurve.Type.InOutQuad, self._paint_bg)
        self._podium_anim.finished.connect(self._stop_podium_fade)

    def _stop_podium_fade(self):
        anim = getattr(self, '_podium_anim', None)
        if anim is not None:
            anim.stop()
            self._podium_anim = None

    def fade_podium_in(self, duration_ms: int):
        """Ease this row up to its podium colour — the browser's 0.5s transition.

        Only ever called from :meth:`BoardWindow.highlight_podium`, which is what
        holds the tint back until the heat is actually over.
        """
        key = self.podium_key()
        if key is not None:
            self._fade_bg(self.cfg.color(key), duration_ms)

    def fade_podium_out(self, duration_ms: int):
        """Ease the podium tint back to the row's own stripe.

        Step 1 of the heat transition, and the browser's `clear_podium()`. The
        browser gets this free from a CSS `background-color` transition — every
        background change on a `td` is transitioned, whatever removed the class —
        so Qt interpolates the colour and re-applies it.
        """
        self._fade_bg(self._base_bg, duration_ms)

    def drop_stale_podium(self, duration_ms: int):
        """Fade off a tint this row is no longer entitled to.

        A tint outlives its place: the console's board reset (`reset_lanes()`)
        blanks `lane_place<i>` without the heat changing, and the reveal is a
        one-shot, so nothing else would take the colour off. Removing a tint is
        not the same as adding one — `highlight_podium` still holds the *reveal*
        back until the heat is over, which is the rule this must not break.

        Skipped while a fade is already in flight: this runs from `update_from`,
        so restarting the animation on every frame would leave it converging on
        the stripe without ever arriving.
        """
        if self.podium_key() is not None or self._podium_anim is not None:
            return
        # Case-insensitively: `_current_bg` comes back from a fade as QColor.name()
        # (lower case) while `_base_bg` is whatever the operator typed into the
        # theme form, so `#202020` and `#202020` could compare unequal.
        current = getattr(self, '_current_bg', self._base_bg)
        if current.lower() != self._base_bg.lower():
            self._fade_bg(self._base_bg, duration_ms)

    # ── Lane time ──────────────────────────────────────────────────────────────

    def _style_time(self, colour: str):
        self.time_label.setStyleSheet(f'color: {colour}; background: transparent;')

    def _stop_time_flash(self):
        anim = getattr(self, '_time_anim', None)
        if anim is not None:
            anim.stop()
            self._time_anim = None

    def set_running(self, running: bool):
        """Take the time cell into or out of the race clock's hands.

        The colour says which of the two owners is in charge, as `.time-running` and
        the `time-lock-flash` keyframes do in the browser: grey while the clock is
        ticking, then a flash from white down to the time colour at the moment the
        split locks. Without it a live clock and a frozen lap look identical.
        """
        was, self.running = self.running, bool(running)
        if self.running:
            self._stop_time_flash()
            self._style_time(_TIME_RUNNING)
        elif was:
            self._flash_time()

    def _flash_time(self):
        self._stop_time_flash()
        self._time_anim = _animate_color(
            self, _TIME_LOCK_FROM, self.cfg.color('time'), _TIME_LOCK_MS,
            QEasingCurve.Type.OutQuad, lambda colour: self._style_time(colour.name()))
        self._time_anim.finished.connect(self._stop_time_flash)

    # ── Data ───────────────────────────────────────────────────────────────────

    def clear(self):
        self.name_label.setText('')
        self.alt_label.setText('')
        self.alt_label.hide()
        self.club_label.setText('')
        self.time_label.setText('')
        self.delta_label.setText('')
        self.place_label.setText('')
        self._place = ''
        # Hands the delta cell back before the next heat writes into it — otherwise
        # the pulse would run on under a blank cell, and the first delta of the new
        # heat would be painted in the lap's colour.
        self.set_lap(None)
        self._set_bg(self._base_bg)
        # The browser's `reset_times()`, called from mode_to_intro(): drop any
        # running grey or half-finished lock flash before the next heat is painted.
        self._stop_time_flash()
        self._style_time(self.cfg.color('time'))
        self.set_row_height(self.height())

    def lap_for(self, snapshot: dict):
        """(text, final) for this lane's lap, or ``(None, False)`` for no lap.

        The browser's `lapVisible` + `lapText`, in one call because the Qt side has
        one writer for the cell either way. No lap once the lane has a place — that
        is the finish, whatever the delta is doing. A swimmer with no seed time never
        gets a delta at all, so waiting for one would leave the lap sitting under a
        finished swim for the rest of the heat.

        Counting down is `expected - done`, clamped at 0 so an over-count (a stray
        touch on an inferred count) shows the last length rather than a negative one.
        With no expected total there is nothing to count down from, so it counts up —
        silently, for the reason `state.settings['lap_direction']` gives.
        """
        i = self.lane
        if not self.cfg.show_laps:
            return None, False
        if (snapshot.get(f'lane_place{i}', '') or '').strip():
            return None, False
        if fmt_delta(snapshot.get(f'lane_delta_seconds{i}')):
            return None, False

        done     = int(snapshot.get(f'lane_splits{i}') or 0)
        expected = int(snapshot.get('expected_splits') or 0)
        step     = int(snapshot.get('split_step') or 1)
        counting_down = self.cfg.lap_direction == 'down' and expected > 0
        if done <= 0:
            # Nothing swum yet. Counting up has nothing to say — a column of noughts
            # under a start list is noise — but counting down has the whole race to
            # report, so it shows from the moment the heat loads. It needs a swimmer
            # in the lane to say it about: an empty lane in a short heat must not
            # advertise eight lengths nobody is swimming.
            if not (counting_down and (snapshot.get(f'lane_name{i}', '') or '').strip()):
                return None, False
        # The final stretch, exactly as the browser's `lapIsFinal` puts it: the next
        # thing the console reports is the finish, so this cannot be taken back.
        final = expected > 0 and (done + step) >= expected
        if counting_down:
            return str(max(0, expected - done)), final
        return str(done), final

    def update_from(self, snapshot: dict):
        """Re-render from the merged scoreboard state (only this lane's keys)."""
        i = self.lane
        self.name_label.setText(snapshot.get(f'lane_name{i}', ''))

        alt = snapshot.get(f'lane_name_alt{i}', '')
        was_showing = self.alt_label.isVisibleTo(self)
        self.alt_label.setText(alt)
        self.alt_label.setVisible(bool(alt))
        if bool(alt) != was_showing:
            # The name's ceiling depends on whether it is sharing the cell.
            self.set_row_height(self.height())

        self.club_label.setText(snapshot.get(f'lane_club{i}', ''))
        # A running lane's time is driven by the ticker; writing `lane_time<i>`
        # here would stamp the last split back over the live clock on every frame.
        if not self.running:
            self.time_label.setText(snapshot.get(f'lane_time{i}', ''))

        # The delta cell, then the lap that may be standing in for it. `set_lap`
        # overwrites the text it just wrote when there is a lap to show, and hands
        # the cell back — text and colour both — the moment there is not.
        self.delta_label.setText(fmt_delta(snapshot.get(f'lane_delta_seconds{i}')))
        self._style_delta(snapshot.get(f'lane_delta_better{i}'))
        self.set_lap(*self.lap_for(snapshot))

        # Recorded, not acted on. The browser tints only once the heat is over —
        # `highlight_podium()` runs from `mode_to_results()` and `race_finished`, not
        # from the update handler — so tinting here would send the first finisher's
        # row gold while everyone else is still swimming.
        place = (snapshot.get(f'lane_place{i}', '') or '').strip()
        self._place = place
        self.place_label.setText(place)
        self.drop_stale_podium(_PODIUM_FADE_MS)


class Badge(QLabel):
    """A small pill floating over the board, sized as a fraction of the window.

    Deliberately *not* the status overlay: that one is opaque and full screen, so
    using it for anything the operator needs to see through hides the very board it
    is reporting on. The browser draws its test-session notice this way
    (`.test-overlay` in timing_display.css) and so do we.

    Two live at once — the test session along the bottom, the link state along the
    top — so they can never collide.
    """

    def __init__(self, parent=None, *, at_top=False):
        super().__init__('', parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.at_top = at_top
        self._tint  = '#ffffff'
        self.hide()

    def apply_theme(self, cfg: Config, tint: str, text: str | None = None):
        """Inverted pill: *tint* at 75% behind, *text* on top.

        *text* defaults to the board's own background, which is what makes a pill
        read as punched out of the board rather than laid on it.
        """
        self._tint = tint
        colour = QColor(tint)
        self.setStyleSheet(
            f"color: {text or cfg.color('bg')};"
            f"background-color: rgba({colour.red()},{colour.green()},{colour.blue()},0.75);"
            f"border-radius: 6px;")
        font = QFont(cfg.family)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 115)   # `.test-overlay`'s 0.15em
        _restyle(self, font)

    def place(self, width: int, height: int, top_offset: int = 0):
        """Centre horizontally, 2.5% in from whichever edge this badge hugs.

        *top_offset* is where the board's content starts — the header bar's height —
        so a top badge sits under the header rather than over the event name, which
        is exactly what an official still wants to read while the link is down.
        """
        font = self.font()
        font.setPixelSize(max(10, int(height * 0.022)))
        self.setFont(font)
        self.adjustSize()
        pad_x, pad_y = int(width * 0.025), int(height * 0.006)
        w = self.sizeHint().width() + 2 * pad_x
        h = self.sizeHint().height() + 2 * pad_y
        margin = int(height * 0.025)
        y = top_offset + margin if self.at_top else height - h - margin
        self.setGeometry((width - w) // 2, y, w, h)

    def show_text(self, text: str, width: int, height: int, top_offset: int = 0):
        """Set the text and place it, or hide the badge when *text* is empty."""
        self.setText(text)
        self.setVisible(bool(text))
        if text:
            self.place(width, height, top_offset)
            self.raise_()


class HeaderBar(QFrame):
    """The top bar. Scales its own children for the same reason the rows do."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scale_children = None      # set by BoardWindow once its cells exist

    def resizeEvent(self, event):     # noqa: N802 — Qt naming
        super().resizeEvent(event)
        if self.scale_children is not None:
            self.scale_children(self.height())


class HeaderCell(QWidget):
    """An EVENT/HEAT cell: the word and the number side by side, at one size.

    `.header_cell` in the browser is a *column* — a 1.8vh word stacked on a 4.5vh
    number. At a desk that reads as a caption; across a pool deck the word simply is
    not there, which is the whole reason this cell diverges. Inline and equal-sized,
    `EV 12` reads as one phrase at a glance, and the accent colour on the word is
    what keeps it from reading as one long number.

    The two halves are placed by hand rather than by a layout because they must end
    up the *same* size. Two FitLabels in a box each solve for their own share and
    land on different sizes — which is precisely what makes a label look like a
    mistake instead of a label. So the size is solved once, for the pair.
    """

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        # `.header_cell` draws a left divider against its neighbour; the first cell
        # in the bar does not (`:first-child { border-left: none }`).
        self.divider = False
        self._ceiling = 10
        self._shared_px = None
        # A bare QWidget ignores stylesheet borders unless it is told to paint
        # itself through the style.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.label = FitLabel(parent=self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.value = FitLabel(parent=self)
        self.value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for part in (self.label, self.value):
            part.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            part.setMinimumSize(0, 0)
        self.apply_theme()

    def apply_theme(self):
        # The word takes `header_label` — the board's accent blue — and the number
        # keeps `header_value`. Two colours on one line is what separates the two
        # halves now that they are the same size; the browser's stacked cell got
        # that separation from the size difference instead.
        #
        # Scoped to the type, so the divider does not propagate down to the two
        # labels — a Qt stylesheet applies to a widget *and* its descendants.
        edge = (f"border-left: 1px solid {self.cfg.color('header_border')};"
                if self.divider else 'border: none;')
        self.setStyleSheet(f"HeaderCell {{ background: transparent; {edge} }}")
        self.label.setStyleSheet(
            f"color: {self.cfg.color('header_label')}; background: transparent; border: none;")
        self.value.setStyleSheet(
            f"color: {self.cfg.color('header_value')}; background: transparent; border: none;")
        # `.header_label`'s `letter-spacing: 0.08em`, which is most of what makes the
        # word read as a label rather than as text that happens to be there.
        word = QFont(self.cfg.family)
        word.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        self.label.setFont(word)
        self.value.setFont(QFont(self.cfg.digits_family))
        self._relayout()

    def set_pixel_size(self, px: int):
        """Ceiling for the pair. Both halves get whatever size actually fits."""
        self._ceiling = max(10, int(px))
        self._relayout()

    def set_shared_px(self, px):
        """Take *px* instead of this cell's own answer, or ``None`` to go back to it.

        EVENT and HEAT sit side by side, so what matters is not that each is as
        large as it can be but that the two agree: `EVENT 12` needs more room than
        `HEAT 7`, and left to themselves they settle two sizes apart and read as a
        mistake. BoardWindow hands both the smaller answer — see _sync_header_cells.
        """
        self._shared_px = px
        self._relayout()

    def solve_px(self) -> int:
        """The largest size at which this cell's word and number both fit."""
        avail = max(1, self.contentsRect().width())
        lo, hi, best = 10, self._ceiling, 10
        while lo <= hi:
            mid = (lo + hi) // 2
            if sum(self._widths(mid)) <= avail:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        return best

    def set_text(self, label: str, value: str):
        # A word with no number is not a label, it is a stray `EV` — and at this size
        # it would be the loudest thing on an idle board. The cell blanks whole.
        self.label.setText(label if value else '')
        self.value.setText(value)
        self._relayout()

    def resizeEvent(self, event):     # noqa: N802 — Qt naming
        super().resizeEvent(event)
        self._relayout()

    def _widths(self, px: int):
        """(word, gap, number) advances at *px*, in this cell's two faces."""
        word = QFont(self.label.font())
        word.setPixelSize(px)
        number = QFont(self.value.font())
        number.setPixelSize(px)
        return (QFontMetrics(word).horizontalAdvance(self.label.text()),
                int(px * _HDR_INLINE_GAP) if self.label.text() else 0,
                QFontMetrics(number).horizontalAdvance(self.value.text()))

    def _relayout(self):
        """Place the word and the number at whatever size this cell is to use.

        Its own answer normally, the pair's shared one when BoardWindow has set it.
        The search in `solve_px` is widest-first, the same shape as
        `FitLabel._refit` — the advance is monotonic in the size, so it is exact.
        """
        rect = self.contentsRect()
        avail = max(1, rect.width())
        best = self._shared_px if self._shared_px is not None else self.solve_px()
        word_w, gap, number_w = self._widths(best)
        # +2px each: FitLabel re-fits against its *contents* rect and would drop a
        # size on a rounding difference between the advance and the widget width.
        self.label.set_max_px(best)
        self.value.set_max_px(best)
        self.label.setGeometry(rect.x(), rect.y(), word_w + 2, rect.height())
        self.value.setGeometry(rect.x() + word_w + gap, rect.y(),
                               max(1, avail - word_w - gap) + 2, rect.height())


class HeaderRow(QFrame):
    """Column titles. Each title is independently hideable (``show_*_header``)."""

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        # False while the timing columns are shut, as `collapse_cols()` blanks the
        # time title with `_tc.innerHTML = ''`.
        self._time_title_shown = True
        self.setAutoFillBackground(True)
        self.setFrameShape(QFrame.Shape.NoFrame)

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        # The delta title is centred over right-aligned values. That is the
        # browser's own inconsistency (`.td_delta` is right, the `th` is centre) and
        # it is kept, so the two boards read identically.
        self.cells = {}
        spec = (('lane',  _W_LANE,  Qt.AlignmentFlag.AlignCenter),
                ('name',  _W_NAME,  Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                ('club',  _W_CLUB,  Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                ('time',  _W_TIME,  Qt.AlignmentFlag.AlignCenter),
                ('delta', _W_DELTA, Qt.AlignmentFlag.AlignCenter),
                ('place', _W_PLACE, Qt.AlignmentFlag.AlignCenter))
        for key, weight, align in spec:
            label = FitLabel()
            label.setAlignment(align)
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            label.setMinimumSize(0, 0)
            row.addWidget(label, weight)
            self.cells[key] = label
        self.setMinimumSize(0, 0)

        self.apply_theme()

    def apply_theme(self):
        cfg = self.cfg
        self.setStyleSheet(f"background-color: {cfg.color('th_bg')};")
        for label in self.cells.values():
            label.setStyleSheet(f"color: {cfg.color('th_text')}; background: transparent;")
            label.setFont(QFont(cfg.family))
        # `#time-column` shares a CSS rule with `.td_time`, so the time title is the
        # only column heading that is not `th_text` — it takes the time colour and
        # the timing font, sitting directly above the values it names.
        self.cells['time'].setStyleSheet(
            f"color: {cfg.color('time')}; background: transparent;")
        self.cells['time'].setFont(QFont(cfg.timing_family))

        # A column can be gone, or merely its title. The browser draws the first with
        # `display: none` — the width goes with it — and the second with
        # `visibility: hidden`, which keeps the space. Hiding the widget for the
        # second case takes it out of the layout and hands its stretch to the
        # neighbours, so the titles stop lining up with the data underneath; blank
        # the text instead. `lane` and `time` have no column flag at all, so their
        # columns always stay.
        shown  = {'lane': True, 'name': cfg.show_name, 'club': cfg.show_club,
                  'time': True, 'delta': cfg.show_delta, 'place': cfg.show_position}
        # The delta title goes with the lap count: for most of a heat that column
        # holds lengths, not a time difference, and a `DELTA` over a column of small
        # integers reads as a claim about them. The column itself stays — only its
        # title is dropped, the same `visibility: hidden` the browser applies, so
        # nothing below it shifts. It stays dropped through the results too: a title
        # that appeared at the finish would be the moving header `L-23` refuses.
        titled = {'lane': cfg.show_lane_header,  'name':  cfg.show_name_header,
                  'club': cfg.show_club_header,  'time':  cfg.show_time_header,
                  'delta': cfg.show_delta_header and not cfg.show_laps,
                  'place': cfg.show_position_header}
        for key, label in self.cells.items():
            label.setVisible(shown[key])
            label.setText(cfg.labels.get(key, key.upper()) if titled[key] else '')
        self._refresh_time_title()

    def set_time_title(self, shown: bool):
        """Blank the time title while the column is shut, as `collapse_cols()` does."""
        self._time_title_shown = shown
        self._refresh_time_title()

    def _refresh_time_title(self):
        cfg = self.cfg
        wanted = self._time_title_shown and cfg.show_time_header
        self.cells['time'].setText(cfg.labels.get('time', 'TIME') if wanted else '')

    def animated_cells(self):
        """(widget, weight) for the columns that slide in at race start."""
        return ((self.cells['time'],  _W_TIME),
                (self.cells['delta'], _W_DELTA),
                (self.cells['place'], _W_PLACE))

    def resizeEvent(self, event):     # noqa: N802 — Qt naming
        super().resizeEvent(event)
        _pad_columns(self.width(), self.cells['name'], self.cells['club'])
        self.set_row_height(self.height())

    def set_row_height(self, height: int):
        # `_FONT_HEADER`, not `_FONT_MAIN`: column titles are 3vh against the rows'
        # 5vh, and this row is half a lane row's height.
        for label in self.cells.values():
            label.set_max_px(max(8, int(height * _FONT_HEADER)))


class BoardWindow(QWidget):
    """Top-level display: title/event/heat/chrono bar above the lane rows.

    Holds the merged scoreboard state. ``update_scoreboard`` frames are *partial*
    (only changed keys), so they are merged into ``self.snapshot`` and the
    affected rows redrawn — never replace the dict wholesale.
    """

    #: The operator asked for an update from the menu, with the ref to move to.
    #: A signal rather than a direct call because the updater belongs to
    #: :class:`~scoreboard.app.ScoreboardApp`, which also owns the server-driven
    #: path — both must go through one place or two updates could run at once.
    update_requested = Signal(str)

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg      = cfg
        self.snapshot = {}
        self._windowed_size = None    # size to restore when leaving fullscreen

        # ── Live clock ─────────────────────────────────────────────────────────
        # The console streams `running_time` a few times a second. Rendering only
        # those frames would make the clock visibly step, so we re-base on each
        # one and interpolate locally in between.
        self._clock_base = None       # hundredths at the last console update
        self._clock_at: float = 0.0   # monotonic() when that update arrived
        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(_CLOCK_TICK_MS)
        self._clock_timer.timeout.connect(self._tick_clock)

        # ── Column reveal ──────────────────────────────────────────────────────
        # Time / delta / place slide open when a race starts. Purely cosmetic, and
        # the reason it reads well is the contrast: between heats the board is a
        # calm start list, then the race begins and the timing columns arrive.
        # Starts expanded so an idle board looks finished rather than half-drawn.
        self._col_fraction = 1.0
        self._heat_key = None        # (event, heat) — a change starts the transition
        # Podium reveal. Held back until the heat is over, then staggered, so the
        # timers have to be cancellable — see highlight_podium.
        self._podium_timers = []
        self._podium_shown  = False
        # While True, frames merge into the snapshot but are not painted: the
        # outgoing heat stays on screen until the fade hides it.
        self.paused = False
        self._transition_token = 0
        self._col_anim = QVariantAnimation(self)
        self._col_anim.setDuration(_COL_ANIM_MS)
        self._col_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._col_anim.valueChanged.connect(self._apply_col_fraction)
        self.setWindowTitle(cfg.meet_title or 'Splouch')

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Header bar ─────────────────────────────────────────────────────────
        self.header = HeaderBar()
        self.header.setAutoFillBackground(True)
        # No margins, no spacing: the cell weights are percentages of the whole bar,
        # and `.header_cell`'s own `6px 1vw` padding is applied per cell in
        # _scale_header so it stays proportional at 4K.
        bar = QHBoxLayout(self.header)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(0)

        # No meet title here. It lives on the splash overlay: `live.html` only
        # ever calls set_header_mode(true), which hides its title cell, so the
        # header the kiosk actually showed never carried one.
        self.event_cell = HeaderCell(cfg)
        self.heat_cell  = HeaderCell(cfg)
        self.name_label = FitLabel()
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.chrono_label = FitLabel()
        self.chrono_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Wall clock, far right — `#meet_datetime` in the browser. Always on, so
        # the board says something useful even between sessions.
        self.wall_clock = FitLabel()
        self.wall_clock.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # EVENT and HEAT lead, hard against the left edge — they are what an
        # official glances at first. The browser leads with them too now that its
        # meet-title cell is gone.
        #
        # The widths are fixed percentages rather than content-derived, so the bar
        # does not reflow when the event number gains a digit. `.header_cells_fixed`
        # in timing_display.css carries the same five numbers.
        self.heat_cell.divider = True
        for widget, weight in ((self.event_cell, _HW_EVENT),
                               (self.heat_cell, _HW_HEAT),
                               (self.name_label, _HW_NAME),
                               (self.chrono_label, _HW_CHRONO),
                               (self.wall_clock, _HW_CLOCK)):
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            widget.setMinimumSize(0, 0)
            bar.addWidget(widget, weight)
        root.addWidget(self.header)

        # Idle until a heat arrives: event/heat/name blank, both clocks in place.
        self.set_header_mode(False)

        self._wall_timer = QTimer(self)
        self._wall_timer.setInterval(_WALL_CLOCK_TICK_MS)
        self._wall_timer.timeout.connect(self._tick_wall_clock)
        self._wall_timer.start()
        self._tick_wall_clock()

        # ── Board ──────────────────────────────────────────────────────────────
        # The column titles and lane rows live in one `content` widget so the heat
        # transition can fade the whole table as a unit while the header bar and
        # background stay put — the same split the browser has between `#scoreboard`
        # and the `.timing-content` table it fades.
        self.content = QWidget()
        content = QVBoxLayout(self.content)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)

        self.header_row = HeaderRow(cfg)
        content.addWidget(self.header_row, 1)

        self.rows = []
        for lane in range(1, cfg.num_lanes + 1):
            row = LaneRow(lane, cfg)
            self.rows.append(row)
            content.addWidget(row, 2)

        self._content_opacity = QGraphicsOpacityEffect(self.content)
        self._content_opacity.setOpacity(1.0)
        self.content.setGraphicsEffect(self._content_opacity)
        self._content_fade = QPropertyAnimation(self._content_opacity, b'opacity', self)
        self._content_fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        # One permanent connection dispatching to a stored callback, rather than
        # connect/disconnect per fade. Blanket `disconnect()` raised TypeError with
        # nothing connected under PyQt5 but only warns under PySide6, so the
        # try/except around it quietly stopped guarding anything.
        self._content_fade_done = None
        self._content_fade.finished.connect(self._on_content_fade_finished)
        root.addWidget(self.content, 1)
        self.header.scale_children = self._scale_header

        # ── Status overlay ─────────────────────────────────────────────────────
        # Shown until the first connection; also covers a mid-meet drop so the TV
        # says why it is frozen instead of silently showing stale times.
        #
        # Two lines: a headline anyone in the stands can read, and a dimmer detail
        # line for whoever is fixing it. The detail carries a live elapsed count,
        # which is the only thing on screen that distinguishes "still trying" from
        # "crashed" — the question a black screen always raises.
        self.status_box = QWidget(self)
        status_layout = QVBoxLayout(self.status_box)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(0)
        self.status = QLabel('')
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_detail = QLabel('')
        self.status_detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_layout.addStretch(1)
        status_layout.addWidget(self.status)
        status_layout.addWidget(self.status_detail)
        status_layout.addStretch(1)
        self.status_box.hide()

        # Test-session badge, bottom centre, matching `.test-overlay`.
        self.test_badge = Badge(self)
        # Link-lost badge, top centre. A dropped connection mid-meet must NOT raise
        # the status overlay: the board is holding a real start list or a real set of
        # results, and covering those for a two-second blip is worse than the blip.
        # The full-screen message is for a cold boot, where there is nothing to hide.
        self.link_badge = Badge(self, at_top=True)
        # True from the moment the link drops until it comes back. While set, the
        # clock is frozen rather than interpolating — see set_link_lost.
        self.link_lost = False

        # The carousel overlay, above the board and above the status message.
        self.splash = SplashOverlay(cfg, self)

        # This display's own git ref, for the menu. Read from `version`'s cache,
        # which a startup thread warms — shelling out to git on the GUI thread for a
        # string that cannot change while we run is exactly the blocking call this
        # app is built to avoid.
        self.own_version = ''
        # Last of all, so it is above everything including the splash: it is the
        # one overlay the operator opened deliberately.
        self.menu = OperatorMenu(cfg, self)
        self.menu.chosen.connect(self.menu_choose)

        self.apply_theme()

    # ── Appearance ─────────────────────────────────────────────────────────────

    def apply_theme(self):
        cfg = self.cfg
        self.setStyleSheet(f"background-color: {cfg.color('bg')};")
        # Scoped to the type: an unscoped rule propagates to every descendant, and
        # the bottom border would then be drawn under each header cell as well.
        self.header.setStyleSheet(
            f"HeaderBar {{ background-color: {cfg.color('header_bg')};"
            f" border-bottom: 1px solid {cfg.color('header_border')}; }}")
        # `.header_cell`'s left divider, on every cell but the first.
        divider = f"border-left: 1px solid {cfg.color('header_border')};"
        for label in (self.name_label,):
            label.setStyleSheet(
                f"color: {cfg.color('header_value')}; background: transparent;"
                f" border: none; {divider}")
            label.setFont(QFont(cfg.family))
        for cell in (self.event_cell, self.heat_cell):
            cell.cfg = cfg
            cell.apply_theme()
        # The running clock uses the *digits* font (Settings → Display → Theme →
        # Digit Font), not the timing font — same split as the browser, where the
        # chrono is typically a seven-segment face and lane times are not.
        self.chrono_label.setStyleSheet(
            f"color: {cfg.color('time')}; background: transparent;"
            f" border: none; {divider}")
        self.chrono_label.setFont(QFont(cfg.digits_family))
        # The accent blue, like the EV/HT words — the two ends of the bar then frame
        # the race clock, which is the one element up here meant to stand out and
        # keeps the gold `time` to itself. The wall clock is the header's least
        # urgent text; matching the labels is what stops it competing with the race.
        self.wall_clock.setStyleSheet(
            f"color: {cfg.color('header_label')}; background: transparent;"
            f" border: none; {divider}")
        self.wall_clock.setFont(QFont(cfg.digits_family))
        self.test_badge.apply_theme(cfg, cfg.color('row_text'))
        # The link badge is the one an operator may want to tune: its pill is a
        # warning colour rather than a board colour, so the text on it does not
        # necessarily read against the background the other pill borrows.
        self.link_badge.apply_theme(cfg, cfg.color('connection_lost'),
                                    cfg.color('connection_lost_text'))

        self.status_box.setStyleSheet(f"background-color: {cfg.color('bg')};")
        self.status.setStyleSheet(
            f"color: {cfg.color('header_value')}; background: transparent;")
        _restyle(self.status, QFont(cfg.family))
        self.status_detail.setStyleSheet(
            f"color: {cfg.color('th_text')}; background: transparent;")
        _restyle(self.status_detail, QFont(cfg.family))
        self.header_row.apply_theme()
        for row in self.rows:
            row.apply_theme()
        if hasattr(self, 'menu'):
            self.menu.apply_config(cfg)
        # Last, so it wins: both of the loops above repaint the clock and the lane
        # times for a healthy link.
        self._apply_link_tint()
        if hasattr(self, 'splash'):
            self.splash.apply_config(cfg)

    def keyPressEvent(self, event):   # noqa: N802 — Qt naming
        """Operator keys: leave the board without an SSH session.

        The kiosk has no window decorations and no menu, so these are the only way
        back to the desktop from the TV itself.

        * **Ctrl+Q** quits. Deliberately two-handed: a stray keypress must not take
          the board down mid-meet. It exits with status 0, which
          ``start-scoreboard.sh`` reads as "the operator meant it" and does not
          relaunch — the desktop icon does that.
        * **F11** / **Ctrl+F** toggle fullscreen, for a quick look at the desktop.
          F11 is the Linux-wide convention and the one to reach for; Ctrl+F is a
          second binding for hands used to it. It normally means Find, but this
          app has nothing to search, so the key is free.
        * **Esc** leaves fullscreen (never quits), the conventional escape hatch —
          but closes the operator menu first when it is open, which is what an
          operator means by Esc with a panel in front of them.
        * **F1** opens the operator menu: update, restart, quit, and what version
          this display and its server are on. It is the only one of these that can
          *change* anything, so every entry on it refuses a board mid-race — see
          `menu_choose`.
        """
        key  = event.key()
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        # Ctrl+Q is the one key the menu never holds: a wedged update must not be
        # able to trap the board with no way out.
        if self.menu.isVisible() and not (ctrl and key == Qt.Key.Key_Q):
            if key == Qt.Key.Key_Escape and not self.menu.busy:
                self.menu.close_menu()
            else:
                self.menu.handle_key(key)
            # Modal: nothing else sees a key while the panel is up. Letting the
            # unhandled ones through looks harmless until F11 resizes the window out
            # from under the panel an operator is reading.
            return
        if ctrl and key == Qt.Key.Key_Q:
            app = QApplication.instance()
            if app is not None:
                app.quit()
        elif key == Qt.Key.Key_F1:
            self.open_menu()
        elif key == Qt.Key.Key_F11 or (ctrl and key == Qt.Key.Key_F):
            self.set_fullscreen(not self.isFullScreen())
        elif key == Qt.Key.Key_Escape and self.isFullScreen():
            self.set_fullscreen(False)
        else:
            super().keyPressEvent(event)

    # ── Operator menu ──────────────────────────────────────────────────────────

    def open_menu(self):
        """Raise the operator menu over the board."""
        # Read the ref now rather than holding one from startup: the warm-up thread
        # may still have been running when this window was built.
        self.own_version = self.own_version or cached_version()
        self.menu.setGeometry(self.rect())
        self.menu.open()
        self.menu.refresh(own_version=self.own_version, link_up=not self.link_lost)

    def menu_choose(self, action: str):
        """Act on a menu entry, or refuse and say why.

        The refusals live here rather than in the menu so the board's own state —
        is a lane running, did the server say what version to use — is read in one
        place. `update` is handed on to whoever owns the updater (app.py); the menu
        only names the action.
        """
        from .menu import MenuAction
        # Nothing on this menu may take the board down mid-race. Every entry here
        # blanks the TV for a few seconds at least, and two keys — F1 then a digit —
        # is nowhere near deliberate enough for that with somebody in the water.
        # Ctrl+Q is still the unconditional way out: it is two-handed, which is what
        # earns it the right to ignore this.
        if self.any_lane_running:
            self.menu.set_note(self.cfg.strings.get(
                'menu_race_on', 'A race is running — not updating now.'))
            return
        if action == MenuAction.QUIT:
            app = QApplication.instance()
            if app is not None:
                app.quit()
        elif action == MenuAction.RESTART:
            # Non-zero, so start-scoreboard.sh brings it straight back — the same
            # contract a finished update uses. Status 0 would leave the TV dark.
            os._exit(1)
        elif action == MenuAction.UPDATE:
            if not self.cfg.server_version:
                self.menu.set_note(self.cfg.strings.get(
                    'menu_no_target',
                    'The server has not said which version to use.'))
                return
            self.menu.set_busy(True)
            self.menu.set_note(self.cfg.strings.get('menu_updating', 'Updating…'))
            self.update_requested.emit(self.cfg.server_version)

    def set_fullscreen(self, fullscreen: bool):
        """Enter or leave fullscreen, remembering the windowed size ourselves.

        ``showNormal()`` is supposed to restore the pre-fullscreen geometry, but
        the kiosk goes fullscreen before the window is ever shown normally, so Qt
        has nothing recorded and falls back to a default box. Tracking the size
        here makes Esc/F11 land at a usable size on the first press.
        """
        if fullscreen:
            if not self.isFullScreen():
                self._windowed_size = self.size()
            self.showFullScreen()
        else:
            self.showNormal()
            if self._windowed_size is not None:
                self.resize(self._windowed_size)

    def set_header_mode(self, active: bool):
        """Fill or blank the EVENT / HEAT / event-name group.

        Mirrors `set_header_mode()` in the browser: they appear once a heat is
        loaded, and an idle board shows just the two clocks. Filling is the caller's
        job — `apply_update` writes the values as they arrive — so only the blanking
        happens here.

        Blanked, not hidden. A hidden widget leaves the layout entirely and Qt hands
        its stretch to the neighbours, which is precisely what the bar's fixed cell
        widths exist to prevent: hiding these three used to leave the wall clock
        sitting around three-quarters of the way across an idle board instead of
        hard right. Same reasoning as the race clock keeping its slot.
        """
        self._header_mode = active
        if not active:
            self.event_cell.set_text('', '')
            self.heat_cell.set_text('', '')
            self.name_label.setText('')
            self._sync_header_cells()

    def _tick_wall_clock(self):
        self.wall_clock.setText(time.strftime('%H:%M'))

    def _scale_header(self, height: int):
        """Size the top bar's text from the bar's height.

        Only safe because `resizeEvent` gives the bar a *fixed* height first. It
        used to be content-derived, which made this circular: the bar shrank to fit
        its labels and the labels shrank to fit the bar, settling at 34px.
        """
        bar = max(1, height)
        self.name_label.set_max_px(max(10, int(bar * _R_VALUE)))
        self.chrono_label.set_max_px(max(10, int(bar * _R_DIGITS)))
        self.wall_clock.set_max_px(max(10, int(bar * _R_DIGITS)))
        for cell in (self.event_cell, self.heat_cell):
            cell.set_pixel_size(int(bar * _R_DIGITS))
        self._sync_header_cells()
        # `.header_cell`'s `6px 1vw`, kept proportional so it does not shrink to
        # nothing on a 4K panel.
        pad_x, pad_y = int(self.width() * _HDR_PAD_X), int(bar * _HDR_PAD_Y)
        for widget in (self.event_cell, self.heat_cell, self.name_label,
                       self.chrono_label, self.wall_clock):
            widget.setContentsMargins(pad_x, pad_y, pad_x, pad_y)

    def _sync_header_cells(self):
        """Give EVENT and HEAT one size — the smaller of what the two can take.

        They sit side by side with a divider between them, so a size difference is
        read as one of them being wrong rather than as either being as large as it
        fits. `EVENT 12` is the wider phrase, so it is usually the one setting the
        size; with the short labels (`EV`, `HT`) both reach the ceiling and this
        costs nothing.
        """
        cells = (self.event_cell, self.heat_cell)
        for cell in cells:
            cell.set_shared_px(None)       # ask each for its own answer first
        shared = min(cell.solve_px() for cell in cells)
        for cell in cells:
            cell.set_shared_px(shared)

    def resizeEvent(self, event):     # noqa: N802 — Qt naming
        super().resizeEvent(event)
        # Rows and the header bar scale themselves from their own resizeEvents —
        # see LaneRow.set_row_height for why reading their height from here does
        # not work. This handles only what belongs to the window.
        # A fixed fraction of the window, like `.timing-header-bar`'s 85px at
        # 1080p. Without this the bar shrinks to whatever its labels need, and the
        # labels shrink to fit the bar.
        #
        # Scale from the height we just asked for, not from `header.height()`:
        # `setFixedHeight` only constrains the widget, and the geometry does not
        # change until the layout next runs — so reading it straight back returns
        # the *previous* height. At the first show that is the pre-layout default,
        # which sized the whole header off a bar that never existed.
        bar_height = max(48, int(self.height() * _H_BAR))
        self.header.setFixedHeight(bar_height)
        self._scale_header(bar_height)
        if self._col_fraction < 1.0:
            self._apply_col_fraction(self._col_fraction)   # widths are width-relative
        self.splash.setGeometry(self.rect())
        if hasattr(self, 'menu'):
            self.menu.setGeometry(self.rect())
        for badge in (self.test_badge, self.link_badge):
            if badge.isVisible():
                badge.place(self.width(), self.height(), self.header.height())
        self.status_box.setGeometry(self.rect())
        font = self.status.font()
        font.setPixelSize(max(16, int(self.height() * 0.055)))
        self.status.setFont(font)
        font = self.status_detail.font()
        font.setPixelSize(max(11, int(self.height() * 0.028)))
        self.status_detail.setFont(font)

    # ── State ──────────────────────────────────────────────────────────────────

    def set_config(self, cfg: Config):
        """Adopt a freshly fetched config (after a ``reload`` event).

        Lane count changes need the rows rebuilt, which the app handles by
        recreating the window; everything else is a restyle in place.
        """
        self.cfg = cfg
        self.header_row.cfg = cfg
        for row in self.rows:
            row.cfg = cfg
        self.setWindowTitle(cfg.meet_title or 'Splouch')
        self.apply_theme()
        self.refresh()

    def set_test_mode(self, active: bool):
        """Show or hide the test-session badge.

        A recorded session looks exactly like a real race on screen, so the board
        has to stay fully visible — the badge only has to be impossible to miss.
        """
        self.test_badge.show_text(
            self.cfg.strings.get('test_session', '⚠ TEST SESSION') if active else '',
            self.width(), self.height())

    def set_link_lost(self, active: bool, detail: str | None = None):
        """Report a dropped connection without covering the board.

        Everything on screen is still the last thing the console actually said, so it
        stays exactly where it is; the badge says not to trust it as *live*. That is
        the whole reason this is not `set_status`, which is opaque and full screen.

        The clock is frozen at the same moment, and this is the part that matters
        beyond looks. The ticker interpolates between `running_time` frames, so left
        alone it keeps counting up smoothly off a base that stopped arriving — the
        board would show a confident, fabricated race time. Stopping it and tinting
        it the badge's colour says "this is the last figure the console gave me".

        The lanes' `running` flags are deliberately left alone: they are what the
        console said, the race is presumably still going, and clearing them here
        would fire the split-lock flash on every lane and lose the state we need to
        pick up again on reconnect.

        *detail* of ``None`` leaves the badge's text as it is — the reconnect loop
        reports each failed attempt, and re-blanking the badge on every one of them
        would make it strobe once it is up.
        """
        active = bool(active)
        changed, self.link_lost = active != self.link_lost, active

        if changed:
            if active:
                self._clock_timer.stop()
            # Coming back it is left stopped on purpose: `_clock_base` is stale, so
            # resuming now would jump. The next `running_time` frame re-bases it and
            # `_sync_clock` starts it again — see apply_update.
            self._apply_link_tint()

        if not active:
            self.link_badge.show_text('', self.width(), self.height())
        elif detail is not None:
            self.link_badge.show_text(detail, self.width(), self.height(),
                                      self.header.height())

    def _apply_link_tint(self):
        """Colour the clock, and any lane still holding it, for the current link state.

        Re-applied from `apply_theme` as well as from `set_link_lost`: `/config` is
        fetched over plain HTTP and can succeed while the WebSocket is still down, so
        a reload during an outage would otherwise repaint the clock as though the
        console were talking again.
        """
        cfg = self.cfg
        colour = cfg.color('connection_lost') if self.link_lost else cfg.color('time')
        self.chrono_label.setStyleSheet(
            f"color: {colour}; background: transparent; border: none;"
            f"border-left: 1px solid {cfg.color('header_border')};")
        for row in self.rows:
            if row.running:
                row._style_time(cfg.color('connection_lost') if self.link_lost
                                else _TIME_RUNNING)

    def set_status(self, text: str, detail: str = ''):
        """Show (or clear, with ``''``) the full-screen status message.

        *text* is the headline, sized to be read from the stands. *detail* is a
        dimmer second line for whoever is troubleshooting — the server address, a
        retry count. Pass a single space as *text* to blank the board with no
        message (the operator's display-overlay toggle).
        """
        self.status.setText(text)
        self.status_detail.setText(detail)
        self.status_detail.setVisible(bool(detail))
        self.status_box.setVisible(bool(text))
        if text:
            self.status_box.setGeometry(self.rect())
            self.status_box.raise_()

    def status_text(self) -> str:
        """The current headline, or ``''`` when no status is showing."""
        return self.status.text() if self.status_box.isVisible() else ''

    # ── Splash overlay ─────────────────────────────────────────────────────────

    def show_splash(self):
        self.splash.setGeometry(self.rect())
        self.splash.show_splash()
        self.splash.raise_()

    def hide_splash(self):
        self.splash.hide_splash()

    @property
    def splash_visible(self) -> bool:
        """Up or coming up — False the instant a dismissal begins."""
        return self.splash.is_up

    @property
    def any_lane_running(self) -> bool:
        return any(row.running for row in self.rows)

    # ── Heat transition ────────────────────────────────────────────────────────

    def _drop_stale_timing(self, keep=()):
        """Forget the previous heat's times, places and deltas.

        A new heat has none yet, and they are not merely invisible: the columns
        may be collapsed now, but the operator can reopen them, and `refresh()`
        repaints from the snapshot. Without this the last heat's results would
        reappear under the next heat's names.

        *keep* is the frame that triggered the heat change. Anything it carries
        belongs to the **new** heat and must survive — a console is free to send
        the heat number and a lane time in one packet, and wiping those would
        discard data we had just been given.
        """
        stale = [key for key in self.snapshot
                 if key.startswith(('lane_time', 'lane_place', 'lane_delta',
                                    'lane_running', 'lane_splits'))
                 and key not in keep]
        for key in stale:
            del self.snapshot[key]

    def _has_results(self) -> bool:
        """True when finished times are on screen — something worth fading out."""
        return any(row.place_label.text() or row.time_label.text()
                   for row in self.rows)

    # ── Podium ─────────────────────────────────────────────────────────────────

    def heat_is_done(self) -> bool:
        """The browser's `all_done`: nobody swimming, nobody timed but unplaced.

        An empty board satisfies it trivially, which is why `highlight_podium` also
        checks that there is something to reveal.
        """
        for row in self.rows:
            if row.running:
                return False
            if row.time_label.text() and not row.place_label.text():
                return False
        return True

    def highlight_podium(self):
        """Reveal the top three — gold, then silver, then bronze.

        The browser runs this only on entering the results screen and on
        `race_finished`, never from the update handler, and staggers the three rows
        400ms apart over a 0.5s `background-color` transition. Tinting as each place
        lands instead would send the first finisher's row gold while the rest of the
        heat is still in the water.

        Does nothing until there is actually a podium to show, so an empty board
        cannot consume the one reveal this heat gets.
        """
        if self._podium_shown:
            return
        placed = [(row.podium_place(), row) for row in self.rows]
        placed = [(place, row) for place, row in placed if place is not None]
        if not placed:
            return
        self._podium_shown = True
        self._clear_podium_timers()
        for place, row in placed:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.setInterval((place - 1) * _PODIUM_STEP_MS)
            timer.timeout.connect(
                lambda r=row: r.fade_podium_in(_PODIUM_FADE_IN_MS))
            self._podium_timers.append(timer)
            timer.start()

    def _clear_podium_timers(self):
        for timer in self._podium_timers:
            timer.stop()
        self._podium_timers = []

    def clear_podium(self):
        """Take the tints off and re-arm the reveal — the browser's `clear_podium()`.

        `_podium_shown` is a latch, because `highlight_podium` is evaluated at the
        end of every `apply_update` and without it the 400ms stagger would restart
        on each frame. The browser needs no latch: it runs the reveal only on a
        state change, and it is idempotent by construction.

        The cost of the latch is that it outlives the heat it was set for unless
        something clears it, and a heat-key change is not the only way a heat ends.
        A false start re-swum under the same number, a console board reset, a
        recording replayed from the top — all reuse the key, and the previous
        attempt's colours stayed on the rows with the reveal refusing to repaint
        them, so the new winner could sit in silver while the runner-up held gold.

        Called where the browser calls it: when a race starts (`mode_to_running()`).
        """
        self._clear_podium_timers()
        self._podium_shown = False
        for row in self.rows:
            row.fade_podium_out(_PODIUM_FADE_MS)

    def begin_heat_transition(self):
        """Results → next heat, in the browser's five steps.

        1. podium tints fade back to the row stripes
        2. the timing columns close
        3. the table fades out
        4. the new heat is painted while it is invisible
        5. the table fades back in

        Each step is 500ms, so the whole thing is about two seconds — which is why
        it only runs when there are results to clear. Loading a heat onto an empty
        board collapses the columns outright; there is nothing to dissolve.

        While the transition runs the table is *paused*: frames still merge into
        the snapshot, they just are not painted until step 4. Otherwise the next
        heat's names would appear on the outgoing results.
        """
        if not self._has_results():
            self.set_columns_visible(False, animate=False)
            return

        token = self._transition_token = self._transition_token + 1
        self.paused = True
        self.stop_clock()

        for row in self.rows:                       # step 1
            row.fade_podium_out(_PODIUM_FADE_MS)
        QTimer.singleShot(_PODIUM_FADE_MS, lambda: self._transition_collapse(token))

    def _transition_collapse(self, token):
        if token != self._transition_token:
            return
        self.set_columns_visible(False)              # step 2
        QTimer.singleShot(_COL_ANIM_MS, lambda: self._transition_fade_out(token))

    def _transition_fade_out(self, token):
        if token != self._transition_token:
            return
        self._fade_content(0.0, lambda: self._transition_swap(token))   # step 3

    def _transition_swap(self, token):
        if token != self._transition_token:
            return
        self.paused = False
        for row in self.rows:                        # step 4, while invisible
            row.clear()
        self.refresh()
        self._fade_content(1.0, None)                # step 5

    def _on_content_fade_finished(self):
        callback, self._content_fade_done = self._content_fade_done, None
        if callback is not None:
            callback()

    def _fade_content(self, target: float, done):
        self._content_fade.stop()
        self._content_fade_done = done
        self._content_fade.setDuration(_CONTENT_FADE_MS)
        self._content_fade.setStartValue(self._content_opacity.opacity())
        self._content_fade.setEndValue(target)
        self._content_fade.start()

    def cancel_heat_transition(self):
        """Abandon a transition mid-flight and show the current state at once.

        Called when a race starts during the sequence — a swimmer on the blocks
        outranks an animation.
        """
        self._transition_token += 1
        self._content_fade.stop()
        self._content_opacity.setOpacity(1.0)
        if self.paused:
            self.paused = False
            self.refresh()

    # ── Column reveal ──────────────────────────────────────────────────────────

    def _animated_cells(self):
        for container in [self.header_row, *self.rows]:
            yield from container.animated_cells()

    def _apply_col_fraction(self, fraction):
        """Squeeze the timing columns to *fraction* of their natural width.

        Driven by ``maximumWidth`` rather than layout stretch: these cells use
        ``QSizePolicy.Policy.Ignored``, which carries the Expand flag, so a stretch of 0
        would not reliably close them.
        """
        self._col_fraction = float(fraction)
        width = max(1, self.width())
        for cell, weight in self._animated_cells():
            if self._col_fraction >= 1.0:
                cell.setMaximumWidth(QWIDGETSIZE_MAX)   # hand control back to the layout
            else:
                natural = width * weight / _COL_TOTAL_WEIGHT
                cell.setMaximumWidth(int(natural * self._col_fraction))

    def set_columns_visible(self, visible: bool, animate: bool = True):
        """Slide the timing columns open or shut.

        Idempotent: re-asserting the current state does not restart the animation,
        so repeated ``columns_state`` frames cannot make the board stutter.
        """
        target = 1.0 if visible else 0.0
        # `collapse_cols()` blanks the time title outright and `expand_cols()` puts
        # it back; the browser does not animate the text, only the width.
        self.header_row.set_time_title(visible)
        self._col_anim.stop()
        if not animate or self._col_fraction == target:
            self._apply_col_fraction(target)
            return
        self._col_anim.setStartValue(self._col_fraction)
        self._col_anim.setEndValue(target)
        self._col_anim.start()

    @property
    def columns_visible(self) -> bool:
        return self._col_fraction >= 1.0

    # ── Live clock ─────────────────────────────────────────────────────────────

    def _tick_clock(self):
        """Repaint the header chrono and every running lane from the local clock.

        All running lanes show the *same* value — the console's race clock — which
        is what the browser does too. There is no independent per-lane timer: a
        lane's own elapsed time only becomes meaningful at its split, and that
        arrives as ``lane_time<i>``.
        """
        if self._clock_base is None:
            return
        elapsed = int((time.monotonic() - self._clock_at) * 100)
        # Interpolate in hundredths, show tenths: the extra precision is what keeps
        # the tenth turning over at the right moment, and showing it is what made
        # the last digit strobe. See fmt_clock.
        text = fmt_clock(self._clock_base + elapsed, tenths=True)
        self.chrono_label.setText(text)
        for row in self.rows:
            if row.running:
                row.time_label.setText(text)

    def _sync_clock(self):
        """Run the ticker only while at least one lane is actually swimming.

        And only while the console is still talking to us: with the link down the
        ticker has nothing to interpolate *towards*, so it would invent a time.
        """
        if self.link_lost:
            self._clock_timer.stop()
            return
        if any(row.running for row in self.rows):
            if not self._clock_timer.isActive():
                self._clock_timer.start()
        else:
            self._clock_timer.stop()

    def stop_clock(self):
        """Freeze every lane at its last time — race over, or board reset.

        Through `set_running`, so a lane the console never explicitly stopped still
        gets its lock flash. This is the backstop for exactly that missed frame.
        """
        self._clock_timer.stop()
        self._clock_base = None
        self._clear_chrono()
        for row in self.rows:
            row.set_running(False)

    def _clear_chrono(self):
        """Blank the race clock without giving up its place in the header.

        Clearing the *text* rather than hiding the widget: a hidden widget drops
        out of the layout and every cell to its left slides right, so the meet
        title would jump each time a heat ended.
        """
        self.chrono_label.setText('')

    def apply_update(self, data: dict):
        """Merge a partial ``update_scoreboard`` frame and redraw what changed."""
        if not isinstance(data, dict):
            return
        self.snapshot.update(data)

        # Running flags first: they decide whether the rows redrawn below take
        # their time from `lane_time<i>` or leave it to the ticker.
        #
        # A lane pauses at every wall. On the touch the console drops
        # `lane_running<i>` and sends the split in `lane_time<i>`, then holds it
        # there for a fixed number of seconds — its own setting, not the length of
        # the turn — before the flag comes back and the lane rejoins the clock.
        # Freezing the split is the whole point of the flag — without it the lap
        # time would be overwritten before anyone saw it.
        was_racing = any(row.running for row in self.rows)
        for key, value in data.items():
            if not key.startswith('lane_running'):
                continue
            match = _LANE_SUFFIX.search(key)
            if not match:
                continue
            lane = int(match.group(1))
            if 1 <= lane <= len(self.rows):
                # set_running, not the bare attribute: the running → not-running
                # edge is what fires the lock flash on the split.
                self.rows[lane - 1].set_running(bool(value))

        # A new heat empties the board back to a start list; the race starting
        # brings the timing columns back. Collapsing is instant because it happens
        # while the previous heat's numbers are being cleared anyway — only the
        # reveal is worth animating.
        if 'current_event' in data or 'current_heat' in data:
            heat = (data.get('current_event', self.snapshot.get('current_event')),
                    data.get('current_heat',  self.snapshot.get('current_heat')))
            if heat != self._heat_key:
                first_heat = self._heat_key is None
                self._heat_key = heat
                # A heat is loaded: fill in EVENT/HEAT/name.
                self.set_header_mode(True)
                # Re-arm the podium for the heat now starting.
                self._clear_podium_timers()
                self._podium_shown = False
                # Retire the previous heat's numbers now; the rows keep showing
                # them until step 4 repaints, which is what fades out.
                self._drop_stale_timing(keep=data)
                if first_heat:
                    self.set_columns_visible(False, animate=False)
                else:
                    self.begin_heat_transition()
        if not was_racing and any(row.running for row in self.rows):
            # A race outranks any animation still in flight.
            self.cancel_heat_transition()
            # The podium on screen belongs to the heat that just ended, not to the
            # one now in the water — `mode_to_running()` drops it here too.
            self.clear_podium()
            self.set_columns_visible(True)

        if 'current_event' in data or 'current_heat' in data:
            if 'current_event' in data:
                self.event_cell.set_text(self.cfg.labels.get('event', 'EVENT'),
                                         str(data['current_event']))
            if 'current_heat' in data:
                self.heat_cell.set_text(self.cfg.labels.get('heat', 'HEAT'),
                                        str(data['current_heat']))
            # An event number gaining a digit changes what fits, and both cells
            # follow so the pair stays level.
            self._sync_header_cells()
        if 'event_name' in data:
            self.name_label.setText(data['event_name'])
        # `running_time` only means something while the heat is unfinished. The
        # console keeps streaming its clock long after the last lane touches — every
        # recording in `server/console_recordings/` carries hundreds of such frames,
        # counting on past the winning time — so without this the header blanks at
        # the end of the heat and the very next frame starts it running again.
        #
        # `/live` has always guarded this: its `if (any_running)` wraps the whole
        # block, over the same condition `heat_is_done` expresses here.
        if 'running_time' in data and not self.heat_is_done():
            # Re-base the local clock on the console's authority. Between these
            # frames the ticker interpolates; it never free-runs for long.
            hundredths = parse_clock(data['running_time'])
            if hundredths is None:
                # Unrecognised format — show whatever the console said rather than
                # blanking the header.
                self.chrono_label.setText(data['running_time'])
            else:
                self._clock_base = hundredths
                self._clock_at   = time.monotonic()
                # Paint it now. The ticker only runs while a lane is swimming, so
                # relying on it alone would freeze the header during the seconds
                # when every lane is paused at a wall.
                #
                # Tenths here too, and for the same reason the ticker uses them: a
                # console that does report hundredths while running would otherwise
                # make the figure jump between its value and the ticker's zero
                # twenty times a second, which is the flicker with an extra step.
                self.chrono_label.setText(fmt_clock(hundredths, tenths=True))

        # Lane keys are `lane_<field><n>` — the lane is the trailing digits, which
        # is the only part of the name that is stable across fields
        # (`lane_time3`, `lane_delta_seconds3`, `lane_name_alt3`).
        touched = set()
        for key in data:
            if not key.startswith('lane_'):
                continue
            match = _LANE_SUFFIX.search(key)
            if match:
                touched.add(int(match.group(1)))
        # Neither of these names a lane, but both decide what every lane's delta cell
        # shows — `expected_splits` arrives with the heat, and `split_step` can move
        # on its own when the operator changes the touchpad setting mid-meet.
        if 'expected_splits' in data or 'split_step' in data:
            touched.update(row.lane for row in self.rows)
        if not self.paused:
            for row in self.rows:
                if row.lane in touched:
                    row.update_from(self.snapshot)

        if was_racing and not any(row.running for row in self.rows):
            self._clear_chrono()    # heat over — the clock has nothing to say

        # The heat being over is what releases the podium, mirroring the browser's
        # move into the results screen. `highlight_podium` is a no-op until there is
        # a podium to show, so an empty board between heats does not consume it.
        if not self.paused and self.heat_is_done():
            self.highlight_podium()

        self._sync_clock()
        if self._clock_timer.isActive():
            self._tick_clock()      # paint now rather than up to 50ms from now

    def refresh(self):
        for row in self.rows:
            row.update_from(self.snapshot)

    def reset(self):
        self.stop_clock()
        self.cancel_heat_transition()
        self._clear_podium_timers()
        self._podium_shown = False
        self.set_header_mode(False)
        self._heat_key = None
        self.set_columns_visible(True, animate=False)
        self.snapshot.clear()
        self.event_cell.set_text('', '')
        self.heat_cell.set_text('', '')
        self.name_label.setText('')
        self.chrono_label.setText('')
        for row in self.rows:
            row.clear()
