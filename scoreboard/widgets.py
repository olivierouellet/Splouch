"""Custom widgets — chiefly the shrink-to-fit label.

This is the reason the display stopped being a web page. CSS can only *truncate*
an overlong swimmer name (`text-overflow: ellipsis`); it cannot shrink it. Qt
measures text before drawing it, so :class:`FitLabel` picks the largest pixel size
at which the whole name fits and nobody named "Vandenbroucke-Mortensen" loses
their surname on the TV.
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QLabel


class FitLabel(QLabel):
    """A QLabel whose font shrinks (never grows past *max_px*) to fit its width.

    The height drives the natural size — a lane row is a fixed fraction of the
    screen — and the width is the constraint we solve against. ``min_px`` stops
    the search before the text becomes unreadable; past that we let it clip
    rather than render a 4px name.
    """

    def __init__(self, text='', *, max_px=48, min_px=10, parent=None):
        super().__init__(text, parent)
        self._max_px = max_px
        self._min_px = min_px
        self._full   = text or ''
        # What the caller asked for. The vertical pair is ours to adjust — see
        # _cap_shift — so the two are kept apart rather than read back off the widget.
        self._margins = (0, 0, 0, 0)
        self.setTextFormat(Qt.PlainText)

    def set_max_px(self, px: int):
        """Set the ceiling font size (called on resize, from the row height)."""
        px = max(self._min_px, int(px))
        if px != self._max_px:
            self._max_px = px
            self._refit()

    def setFont(self, font):          # noqa: N802 — Qt naming
        """Adopt a new family or style, then fit it again.

        A ``QFont`` carries a size as well as a face, so the plain ``QLabel``
        behaviour is to throw away whatever size the fit had arrived at and fall
        back to the family's default — around 13px on a 4K TV. ``apply_theme`` runs
        on every ``/config`` reload, including the first one seconds after the kiosk
        window opens, so on a real board this happened at every boot.

        It only *looked* intermittent because most cells are re-fitted moments later
        by ``refresh()`` writing their text back. The ones with no text to re-set —
        the lane number, and the EVENT/HEAT cells before a heat is loaded — simply
        stayed tiny until someone resized the window.
        """
        super().setFont(font)
        self._refit()

    def setText(self, text):          # noqa: N802 — Qt naming
        self._full = text if text is not None else ''
        self._refit()

    def text(self):                   # noqa: N802 — Qt naming
        """The full text, even when what is drawn has been elided."""
        return self._full

    def displayed_text(self) -> str:
        """What is actually painted — elided if it would not fit at *min_px*."""
        return super().text()

    def resizeEvent(self, event):     # noqa: N802 — Qt naming
        super().resizeEvent(event)
        self._refit()

    def setContentsMargins(self, *args):   # noqa: N802 — Qt naming
        """Remember the caller's padding; the vertical half is ours to adjust."""
        if len(args) == 1:                 # a QMargins
            box = args[0]
            self._margins = (box.left(), box.top(), box.right(), box.bottom())
        else:
            self._margins = tuple(args)
        self._apply_margins()

    def _cap_shift(self) -> int:
        """How far to move this font's text down so its *capitals* sit centred.

        `AlignVCenter` centres the font's line box — ascent plus descent — not the
        glyphs. That is fine until two fonts share a row, which on this board is
        everywhere: `EV` beside `12`, a swimmer's name beside a lane number. Overpass
        Mono reports a 24px descent at 62px and DSEG7 Classic reports **zero**, so
        Qt centres one of them around a gap under the text that the other does not
        have, and the word floats six pixels above its number.

        Solved off the font, never off the current string. Ink extents would centre
        `Roy` and `Zoé` at different heights and make a lane's time bob as the digits
        changed; the cap band is a property of the face and holds still.
        """
        metrics = QFontMetrics(self.font())
        cap = metrics.capHeight()
        if cap <= 0:                       # a face that does not report one
            return 0
        return round(cap / 2 + metrics.height() / 2 - metrics.ascent())

    def _apply_margins(self):
        left, top, right, bottom = self._margins
        # Only for a vertically centred label: with AlignTop or AlignBottom the
        # caller has asked for an edge, and an edge is not ours to move.
        if self.alignment() & Qt.AlignVCenter:
            shift = self._cap_shift()
            # Padding one side moves the contents rect's centre by half of it.
            if shift > 0:
                top += 2 * shift
            elif shift < 0:
                bottom += -2 * shift
        super().setContentsMargins(left, top, right, bottom)

    def _refit(self):
        # Every font assignment below goes through `super().setFont`, never
        # `self.setFont` — the override calls straight back in here.
        text = self._full
        font = self.font()
        if not text:
            font.setPixelSize(self._max_px)
            super().setFont(font)
            self._apply_margins()
            super().setText('')
            return

        # The *contents* rect, not the widget: several cells carry the column
        # padding `timing_display.css` gives them (`.lane-name-cell`'s `0 2vw`, for
        # one) as contents margins, and fitting to the full width would let the text
        # run straight through that padding into its neighbour.
        avail = max(1, self.contentsRect().width() - 2)   # 1px breathing room each side

        # Widest-first: the common case is a name that already fits, so check the
        # ceiling before paying for a search.
        font.setPixelSize(self._max_px)
        if QFontMetrics(font).horizontalAdvance(text) <= avail:
            super().setFont(font)
            self._apply_margins()
            super().setText(text)
            return

        # Binary search the largest pixel size that fits. Text advance is monotonic
        # in font size, so this is exact, and ~6 iterations for any realistic range.
        lo, hi, best = self._min_px, self._max_px, self._min_px
        while lo <= hi:
            mid = (lo + hi) // 2
            font.setPixelSize(mid)
            if QFontMetrics(font).horizontalAdvance(text) <= avail:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        font.setPixelSize(best)
        super().setFont(font)
        self._apply_margins()

        # Even the floor may not fit — a 27-character club in an 8vw column. Qt
        # clips a label to its own rect, so the text would simply be cut through a
        # glyph; an ellipsis says "there is more" instead of looking like a bug.
        metrics = QFontMetrics(font)
        if metrics.horizontalAdvance(text) <= avail:
            super().setText(text)
            return
        elided = metrics.elidedText(text, Qt.ElideRight, avail)
        # With almost no room even the ellipsis does not fit and elidedText returns
        # "". Showing the full text clipped is better than showing nothing — and in
        # a layout that sizes from sizeHint, an empty label collapses to zero width
        # and never grows back, because the next refit then has even less room.
        super().setText(elided or text)
