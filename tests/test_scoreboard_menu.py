"""The operator menu — F1 at the display itself.

Two ways to update a kiosk already exist and both need something other than the
display: Settings → Update → "Update displays" wants a browser on the server,
`install.sh kiosk` wants an SSH session. This is the one an operator can reach
while standing at the TV with the keyboard that is already plugged into it.

It also covers what neither of the others can. A display too old to send
`register` — anything before v2026.09.0, when the Qt board replaced the Chromium
kiosk — is invisible to the server's button *and* too old to act on its `update`
frame, so the first hop has to happen here. That is not a hypothetical: it is why
"Update displays" reports no displays while the clients list shows one.

The rules that matter are all refusals. An update restarts the app, so a board
mid-race must not take one; a display whose server never answered has no version
to aim at; and two updates must never run at once, whichever surface asked.

Needs PySide6 (`uv run pytest tests/`); skips without it.
"""
import pytest

pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')

from PySide6.QtCore import Qt                      # noqa: E402

from scoreboard.board import BoardWindow           # noqa: E402
from scoreboard.menu import MenuAction             # noqa: E402
from scoreboard.theme import Config                # noqa: E402

# `qt_app` comes from tests/conftest.py — session-scoped, fonts already loaded.

SERVER_REF = 'v2026.09.0'


@pytest.fixture
def board(qt_app):
    window = BoardWindow(Config({'num_lanes': 6, 'server_version': SERVER_REF}))
    window.resize(1920, 1080)
    window.show()
    window.own_version = 'v2026.08.1-4-gb3d21af'
    qt_app.processEvents()
    yield window
    window.stop_clock()
    window.close()


def _press(board, key, ctrl=False):
    from PySide6.QtGui import QKeyEvent
    mod = Qt.ControlModifier if ctrl else Qt.NoModifier
    board.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, key, mod))


@pytest.fixture
def chosen(board):
    """Watch what the menu emits without letting the board act on it.

    `menu_choose` really does restart (`os._exit`) and really does quit, so a test
    that drives the keys with the board still listening takes pytest with it.
    """
    board.menu.chosen.disconnect(board.menu_choose)
    picked = []
    board.menu.chosen.connect(picked.append)
    return picked


# ── Opening and closing ────────────────────────────────────────────────────────

def test_f1_opens_it_and_esc_closes_it(board, qt_app):
    assert not board.menu.isVisible()
    _press(board, Qt.Key_F1)
    qt_app.processEvents()
    assert board.menu.isVisible()

    _press(board, Qt.Key_Escape)
    qt_app.processEvents()
    assert not board.menu.isVisible()


def test_esc_closes_the_menu_before_it_leaves_fullscreen(board, qt_app):
    """With a panel in front of you, Esc means "close this" — not "resize the
    window behind it"."""
    board.set_fullscreen(True)
    board.open_menu()
    qt_app.processEvents()

    _press(board, Qt.Key_Escape)
    qt_app.processEvents()
    assert not board.menu.isVisible()
    assert board.isFullScreen(), 'Esc fell through to the fullscreen toggle'


def test_it_does_not_cover_the_board(board, qt_app):
    """A meet does not stop because somebody opened a menu. Same reasoning as the
    test badge being a pill rather than a curtain."""
    board.apply_update({'lane_name1': 'Roy, Zoé', 'lane_time1': '1:12.44'})
    board.open_menu()
    qt_app.processEvents()

    panel = board.menu.panel.geometry()
    assert panel.height() < board.height() * 0.85
    assert panel.width() < board.width() * 0.8
    assert board.rows[0].name_label.text() == 'Roy, Zoé'


# ── Choosing ───────────────────────────────────────────────────────────────────

def test_arrows_move_the_selection_and_it_wraps(board, qt_app, chosen):
    board.open_menu()

    _press(board, Qt.Key_Down)
    _press(board, Qt.Key_Return)
    qt_app.processEvents()
    assert chosen == [MenuAction.RESTART]

    board.open_menu()                       # index resets each time it opens
    _press(board, Qt.Key_Up)                # wraps to the last entry
    _press(board, Qt.Key_Return)
    qt_app.processEvents()
    assert chosen[-1] == MenuAction.QUIT


def test_digits_pick_an_entry_directly(board, qt_app, chosen):
    board.open_menu()
    _press(board, Qt.Key_2)
    qt_app.processEvents()
    assert chosen == [MenuAction.RESTART]


def test_keys_do_not_reach_the_board_while_it_is_open(board, qt_app):
    """F11 under a menu would resize the window out from under the panel."""
    board.set_fullscreen(True)
    board.open_menu()
    _press(board, Qt.Key_F11)
    qt_app.processEvents()
    assert board.isFullScreen(), 'the board acted on a key the menu was holding'


# ── The update, and what it refuses ────────────────────────────────────────────

def test_choosing_update_asks_for_the_server_version(board, qt_app):
    """The board never updates itself — it asks, and `app.py` owns the updater, so
    the menu and the server's button cannot start two at once."""
    asked = []
    board.update_requested.connect(asked.append)
    board.open_menu()
    board.menu_choose(MenuAction.UPDATE)
    qt_app.processEvents()

    assert asked == [SERVER_REF]
    assert board.menu.busy, 'the menu kept taking input during an update'


def test_an_update_is_refused_mid_race(board, qt_app):
    """It restarts the app to finish. A black TV while somebody is swimming is
    worse than being a version behind."""
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    asked = []
    board.update_requested.connect(asked.append)

    board.open_menu()
    board.menu_choose(MenuAction.UPDATE)
    qt_app.processEvents()

    assert asked == [], 'started an update over a running race'
    assert not board.menu.busy
    assert board.menu.note.isVisible()
    assert board.menu.note.text() == board.cfg.strings['menu_race_on']


def test_an_update_is_refused_with_no_target(qt_app):
    """A server too old to send `server_version`, or one that never answered."""
    window = BoardWindow(Config({'num_lanes': 4}))     # no server_version
    window.resize(1920, 1080)
    window.show()
    asked = []
    window.update_requested.connect(asked.append)
    try:
        window.open_menu()
        window.menu_choose(MenuAction.UPDATE)
        qt_app.processEvents()
        assert asked == []
        assert window.menu.note.text() == window.cfg.strings['menu_no_target']
    finally:
        window.close()


def test_the_menu_stops_taking_input_while_updating(board, qt_app, chosen):
    board.open_menu()
    board.menu.set_busy(True)

    for key in (Qt.Key_Down, Qt.Key_Return, Qt.Key_1, Qt.Key_Escape):
        _press(board, key)
    qt_app.processEvents()

    assert chosen == [], 'a second update could be started from the same panel'
    assert board.menu.isVisible(), 'Esc hid the only progress on screen'


def test_ctrl_q_still_works_during_an_update(board, qt_app):
    """A wedged `uv sync` must not be able to trap the board with no way out."""
    board.open_menu()
    board.menu.set_busy(True)
    quit_calls = []
    from PySide6.QtWidgets import QApplication
    original = QApplication.instance().quit
    QApplication.instance().quit = lambda: quit_calls.append(True)
    try:
        _press(board, Qt.Key_Q, ctrl=True)
        qt_app.processEvents()
    finally:
        QApplication.instance().quit = original
    assert quit_calls == [True]


# ── What it says ───────────────────────────────────────────────────────────────

def test_it_names_both_versions_and_the_link(board, qt_app):
    board.open_menu()
    qt_app.processEvents()
    text = board.menu.status.text()
    assert 'v2026.08.1-4-gb3d21af' in text, 'this display is not named'
    assert SERVER_REF in text, 'the server version is not named'
    assert board.cfg.strings['menu_out_of_date'] in text


def test_it_says_up_to_date_when_it_is(qt_app):
    window = BoardWindow(Config({'num_lanes': 4, 'server_version': SERVER_REF}))
    window.resize(1920, 1080)
    window.show()
    window.own_version = SERVER_REF
    try:
        window.open_menu()
        qt_app.processEvents()
        assert window.cfg.strings['menu_up_to_date'] in window.menu.status.text()
    finally:
        window.close()


def test_it_opens_with_the_link_down(board, qt_app):
    """The case it exists for: no server, so no browser to press a button in."""
    board.set_link_lost(True, 'lost')
    board.open_menu()
    qt_app.processEvents()
    assert board.menu.isVisible()
    assert board.cfg.strings['menu_link_down'] in board.menu.status.text()


def test_update_output_is_shown_on_the_tv(board, qt_app):
    """The server's copy of the log is on the other Pi. This panel is the only
    progress an operator standing at the display can see."""
    board.open_menu()
    board.menu.set_busy(True)
    for line in ('$ git fetch --tags', '$ uv sync', 'Installed 3 packages'):
        board.menu.add_output(line)
    qt_app.processEvents()

    assert board.menu.output.isVisible(), 'the update ran with nothing on screen'
    assert 'Installed 3 packages' in board.menu.output.text()
    assert board.menu.output.height() > 0
    assert board.menu.panel.height() > int(board.height() * 0.6), \
        'the panel did not make room for the output'


def test_a_restyle_keeps_the_menu_readable(board, qt_app):
    """`/config` lands seconds after boot and on every reload."""
    board.open_menu()
    board.set_config(Config({'num_lanes': 6, 'server_version': SERVER_REF,
                             'theme_colors': {'header_label': '#ff00ff'}}))
    qt_app.processEvents()
    assert board.menu.isVisible()
    assert 'color: #ff00ff' in board.menu.title.styleSheet()


@pytest.mark.parametrize('action', [MenuAction.RESTART, MenuAction.QUIT])
def test_nothing_on_the_menu_takes_the_board_down_mid_race(board, qt_app, action):
    """F1 then a digit is two keystrokes. Ctrl+Q was made two-handed precisely so a
    stray press could not blank the TV with somebody in the water, and a menu entry
    that restarts or quits has to meet the same bar.

    If this ever regresses it does so silently — `os._exit` leaves no traceback and
    the board simply comes back, looking like a crash.
    """
    board.apply_update({'running_time': '12.30', 'lane_running1': True})
    qt_app.processEvents()
    assert board.any_lane_running

    quit_calls = []
    exits = []
    from PySide6.QtWidgets import QApplication
    import scoreboard.board as board_mod
    original_quit, original_exit = QApplication.instance().quit, board_mod.os._exit
    QApplication.instance().quit = lambda: quit_calls.append(True)
    board_mod.os._exit = lambda code: exits.append(code)
    try:
        board.open_menu()
        board.menu_choose(action)
        qt_app.processEvents()
    finally:
        QApplication.instance().quit = original_quit
        board_mod.os._exit = original_exit

    assert quit_calls == [] and exits == [], f'{action} went through mid-race'
    assert board.menu.note.text() == board.cfg.strings['menu_race_on']
