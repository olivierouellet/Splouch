"""The operator menu — F1 at the display itself.

There are already two ways to update a kiosk, and both need something other than
the display: Settings → Update → "Update displays" needs a browser on the server,
and `install.sh kiosk` needs an SSH session. This is the third, for the case where
neither is to hand: somebody standing at the TV with the keyboard that is already
plugged into it.

It also covers a case the other two cannot. A display too old to send `register`
(anything before v2026.09.0, when the Qt board replaced the Chromium kiosk) is
invisible to the server's button *and* too old to act on its `update` frame, so the
first hop has to happen at the display. After that the remote path works.

Three things shape the design:

* **It must be readable while the board is live.** A meet does not stop because
  somebody opened a menu, so this is a panel over the middle of the board, not a
  full-screen takeover — the lanes stay visible around it, the same reasoning that
  keeps the test badge a pill rather than a curtain.
* **It must work with no server.** The version it compares against comes from the
  cached config, so the menu still opens and still says what it knows when the
  link is down — which is when an operator is most likely to be standing here.
* **Keyboard only.** A kiosk has no mouse. Arrows and Enter, digits as shortcuts,
  Esc to leave.
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QWidget

from .theme import Config

# Panel size as a fraction of the window. Wide enough for a git describe string at
# a readable size, short enough to leave lanes showing above and below.
#
# It grows once an update starts talking: the output is the only progress an
# operator standing at the TV can see — the server's copy of the log is on the
# other Pi — and squeezing it into whatever the idle panel had left showed nothing
# at all. Still short of the full screen, so the board stays visible behind it.
_W, _H, _H_BUSY = 0.62, 0.52, 0.78

# Row heights and text sizes, as fractions of the panel height. Laid out by hand
# rather than by a QVBoxLayout: every child here has `QSizePolicy.Policy.Ignored` so its
# font can be derived from the panel's height, and a box layout then has no size
# hint to distribute and collapses most of the rows to nothing.
_ROW_TITLE  = 0.13
_ROW_STATUS = 0.22
_ROW_ITEM   = 0.13
_ROW_NOTE   = 0.09
_ROW_HINT   = 0.08
# Text size within its row.
_TEXT = 0.62


class MenuAction:
    """The actions an entry can carry. Plain strings — the board owns the doing."""
    UPDATE  = 'update'
    RESTART = 'restart'
    QUIT    = 'quit'


class OperatorMenu(QWidget):
    """Full-window overlay holding a centred panel. Hidden until F1.

    Emits :attr:`chosen` with a :class:`MenuAction`; it never acts on the board
    itself, so the policy (refuse mid-race, what a restart means) stays in one
    place — see :meth:`BoardWindow.menu_choose`.
    """

    chosen = Signal(str)

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._index = 0
        self._busy  = False        # an update is running; the menu stops taking input
        self._note  = ''           # one line under the entries: a refusal, or progress
        self._log   = []           # tail of the updater's output

        self.panel = QFrame(self)
        self.panel.setObjectName('menuPanel')
        self.panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self.title  = QLabel(self.panel)
        self.status = QLabel(self.panel)
        self.items  = [QLabel(self.panel) for _ in range(3)]
        self.note   = QLabel(self.panel)
        self.output = QLabel(self.panel)
        self.output.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.hint   = QLabel(self.panel)

        for widget in (self.title, self.status, *self.items,
                       self.note, self.output, self.hint):
            widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
            widget.setMinimumSize(0, 0)

        self.hide()
        self.apply_config(cfg)

    # ── Appearance ─────────────────────────────────────────────────────────────

    def apply_config(self, cfg: Config):
        self.cfg = cfg
        self._restyle()
        self.refresh()

    def _restyle(self):
        cfg = self.cfg
        # The overlay itself is a wash rather than a wall: the board stays readable
        # behind it, so an operator can see the race they are not interrupting.
        self.setStyleSheet('background-color: rgba(0,0,0,0.55);')
        # `QFrame#menuPanel`, not `QFrame`: QLabel *is* a QFrame subclass, so an
        # unscoped rule draws this border around every line of text in the panel.
        self.panel.setStyleSheet(
            f"QFrame#menuPanel {{ background-color: {cfg.color('header_bg')};"
            f" border: 2px solid {cfg.color('header_border')}; border-radius: 10px; }}")
        self.title.setStyleSheet(
            f"color: {cfg.color('header_label')}; background: transparent; border: none;")
        self.status.setStyleSheet(
            f"color: {cfg.color('th_text')}; background: transparent; border: none;")
        self.note.setStyleSheet(
            f"color: {cfg.color('time')}; background: transparent; border: none;")
        self.output.setStyleSheet(
            f"color: {cfg.color('th_text')}; background: transparent; border: none;")
        self.hint.setStyleSheet(
            f"color: {cfg.color('th_text')}; background: transparent; border: none;")
        for widget in (self.title, self.status, self.note, self.output, self.hint):
            widget.setFont(QFont(cfg.family))
        self._paint_items()

    def _paint_items(self):
        """Highlight the current row. A background, not a colour swap — at TV
        distance a tinted word is not obviously *selected*, a filled bar is."""
        cfg = self.cfg
        for i, label in enumerate(self.items):
            if i == self._index and not self._busy:
                label.setStyleSheet(
                    f"color: {cfg.color('header_bg')};"
                    f" background-color: {cfg.color('header_label')};"
                    f" border: none; border-radius: 6px;")
            else:
                label.setStyleSheet(
                    f"color: {cfg.color('header_value')};"
                    f" background: transparent; border: none;")
            label.setFont(QFont(cfg.family))

    # ── Content ────────────────────────────────────────────────────────────────

    def _string(self, key, fallback=''):
        return self.cfg.strings.get(key, fallback or key)

    def refresh(self, *, own_version='', link_up=False):
        """Redraw from the current config and the board's live state."""
        if own_version:
            self._own_version = own_version
        own    = getattr(self, '_own_version', '') or self._string('menu_unknown')
        server = self.cfg.server_version or self._string('menu_unknown')
        self._link_up = link_up or getattr(self, '_link_up', False)

        self.title.setText(self._string('menu_title'))
        state = ''
        if self.cfg.server_version and getattr(self, '_own_version', ''):
            state = ('  ·  ' + self._string(
                'menu_up_to_date' if self.cfg.server_version == self._own_version
                else 'menu_out_of_date'))
        self.status.setText(
            f"{self._string('menu_this')}: {own}\n"
            f"{self._string('menu_server')}: {server}{state}\n"
            f"{self._string('menu_link')}: "
            f"{self._string('menu_link_up' if self._link_up else 'menu_link_down')}")

        for label, key in zip(self.items, ('menu_update', 'menu_restart', 'menu_quit'),
                              strict=True):
            label.setText('  ' + self._string(key))
        self.note.setText(self._note)
        self.note.setVisible(bool(self._note))
        self.output.setText('\n'.join(self._log[-6:]))
        self.output.setVisible(bool(self._log))
        self.hint.setText(self._string('menu_close'))
        self.hint.setVisible(not self._busy)
        self._paint_items()
        self._layout()

    def set_note(self, text: str):
        """One line under the entries — a refusal, or what is happening now."""
        self._note = text or ''
        self.refresh()

    def add_output(self, text: str):
        self._log.append(text)
        self.refresh()

    def set_busy(self, busy: bool):
        """While an update runs the menu stops taking input and stays up: its own
        output is the only progress the operator has, and Esc during a `uv sync`
        would hide it without stopping anything."""
        self._busy = busy
        self.refresh()

    @property
    def busy(self) -> bool:
        return self._busy

    # ── Input ──────────────────────────────────────────────────────────────────

    def handle_key(self, key) -> bool:
        """Act on a key press. Returns whether it meant anything here.

        The caller swallows the key either way — the menu is modal — so the return
        value only says whether anything changed, not whether to pass it on.
        """
        if self._busy:
            return True                    # swallow everything mid-update
        if key in (Qt.Key.Key_Up, Qt.Key.Key_K):
            self._index = (self._index - 1) % len(self.items)
        elif key in (Qt.Key.Key_Down, Qt.Key.Key_J):
            self._index = (self._index + 1) % len(self.items)
        elif key in (Qt.Key.Key_1, Qt.Key.Key_2, Qt.Key.Key_3):
            self._index = key - Qt.Key.Key_1
            self._activate()
            return True
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self._activate()
            return True
        else:
            return False
        self._paint_items()
        return True

    def _activate(self):
        self.chosen.emit((MenuAction.UPDATE, MenuAction.RESTART,
                          MenuAction.QUIT)[self._index])

    def open(self):
        self._index = 0
        self._note  = ''
        self._log   = []
        self.show()
        self.raise_()
        self.refresh()

    def close_menu(self):
        self._note = ''
        self._log  = []
        self.hide()

    # ── Layout ─────────────────────────────────────────────────────────────────

    def resizeEvent(self, event):     # noqa: N802 — Qt naming
        super().resizeEvent(event)
        self._layout()

    def _layout(self):
        """Stack the rows down the panel, each sized from the panel's own height.

        The same rule the board uses everywhere: geometry first, then text sized
        from the geometry — never the other way round, or the panel sizes itself to
        its text and the text to the panel and the two settle somewhere tiny.
        """
        width  = max(1, int(self.width() * _W))
        height = max(1, int(self.height() * (_H_BUSY if self._log else _H)))
        self.panel.setGeometry((self.width() - width) // 2,
                               (self.height() - height) // 2, width, height)

        # Rows are sized off the *idle* height, so growing the panel for the output
        # does not also inflate the title and the entries — the extra room is the
        # output's, not everyone's.
        unit = max(1, int(self.height() * _H))
        pad  = max(8, int(unit * 0.07))
        x, inner = pad, max(1, width - 2 * pad)

        def place(widget, row_h, *, shown=True):
            """Give *widget* the next row, size its text to fit, return the next y."""
            nonlocal y
            widget.setVisible(shown)
            if not shown:
                return
            box = max(1, int(unit * row_h))
            widget.setGeometry(x, y, inner, box)
            font = widget.font()
            font.setPixelSize(max(9, int(box * _TEXT)))
            widget.setFont(font)
            y += box

        y = pad
        self.title.setFont(_bold(self.title.font()))
        place(self.title, _ROW_TITLE)
        # Three lines in one label, so it gets three rows' worth and a third of it
        # per line.
        box = max(1, int(unit * _ROW_STATUS))
        self.status.setGeometry(x, y, inner, box)
        font = self.status.font()
        font.setPixelSize(max(9, int(box / 3 * 0.72)))
        self.status.setFont(font)
        y += box + pad // 2

        for label in self.items:
            place(label, _ROW_ITEM)
        place(self.note, _ROW_NOTE, shown=bool(self._note))

        # Whatever is left over goes to the updater's output, which is the one row
        # with no natural size — it grows as the update talks.
        bottom = height - pad - max(1, int(unit * _ROW_HINT))
        if self._log and bottom - y > 8:
            self.output.setVisible(True)
            self.output.setGeometry(x, y, inner, bottom - y)
            font = self.output.font()
            font.setPixelSize(max(8, int(unit * 0.05)))
            self.output.setFont(font)
        else:
            self.output.setVisible(False)

        y = bottom
        place(self.hint, _ROW_HINT, shown=not self._busy)


def _bold(font: QFont) -> QFont:
    font = QFont(font)
    font.setBold(True)
    return font
