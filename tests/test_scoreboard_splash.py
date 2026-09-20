"""The splash / carousel overlay.

Raised by the carousel button on `/operator` (`display_overlay {active}`) and
dismissed by the same button *or* by a race starting. Sponsor images come from the
server over HTTP, so the fetch must not touch the GUI thread.

Needs PySide6 (`uv run pytest tests/`); skips without it.
"""
import http.server
import os
import socketserver
import tempfile
import threading
import time

import pytest

pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')

from PySide6.QtCore import QBuffer, QByteArray   # noqa: E402
from PySide6.QtGui import QColor, QPixmap        # noqa: E402

from scoreboard.board import BoardWindow         # noqa: E402
from scoreboard.theme import Config              # noqa: E402

# `qt_app` comes from tests/conftest.py — session-scoped, fonts already loaded.

_MAX_BLOCKING_SECONDS = 1.0


@pytest.fixture(scope='module')
def image_server(qt_app):
    """Serves three PNGs at /images/<name>, as the real server does."""
    directory = tempfile.mkdtemp()
    payloads = {}
    for name, colour in (('a.png', '#c00000'), ('b.png', '#00c000'),
                         ('c.png', '#0000c0')):
        pixmap = QPixmap(400, 300)
        pixmap.fill(QColor(colour))
        path = os.path.join(directory, name)
        pixmap.save(path, 'PNG')
        with open(path, 'rb') as f:
            payloads['/images/' + name] = f.read()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                    # noqa: N802
            body = payloads.get(self.path)
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = socketserver.TCPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f'http://127.0.0.1:{server.server_address[1]}'
    server.shutdown()
    server.server_close()


def _pump(qt_app, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qt_app.processEvents()
        time.sleep(0.01)


def _png(qt_app, colour='#808080'):
    """One encoded PNG, for feeding `_on_image` directly.

    The `image_server` fixture covers the real HTTP path; these tests need to
    control *when* an image lands, which a live download cannot.
    """
    pixmap = QPixmap(400, 300)
    pixmap.fill(QColor(colour))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.WriteOnly)
    pixmap.save(buffer, 'PNG')
    return data.data()


def _config(**overrides):
    base = {'num_lanes': 4, 'meet_title': 'Championnat provincial',
            'carousel_images': ['a.png', 'b.png', 'c.png'], 'carousel_interval': 1}
    base.update(overrides)
    return Config(base)


@pytest.fixture
def board(qt_app, image_server):
    window = BoardWindow(_config())
    window.resize(1920, 1080)
    window.show()
    window.splash.apply_config(window.cfg, image_server)
    _pump(qt_app, 1.0)                      # let the images arrive
    yield window
    window.hide_splash()
    window.stop_clock()
    window.close()


def test_images_are_fetched_without_blocking_the_gui_thread(qt_app, image_server):
    """The board must stay responsive while sponsor images download."""
    window = BoardWindow(_config())
    window.resize(1920, 1080)
    window.show()
    started = time.monotonic()
    window.splash.apply_config(window.cfg, image_server)
    assert time.monotonic() - started < _MAX_BLOCKING_SECONDS, \
        'apply_config blocked — images are being fetched inline'
    _pump(qt_app, 1.0)
    assert len(window.splash._pixmaps) == 3
    window.close()


def test_the_operator_button_shows_and_hides_it(board, qt_app):
    assert not board.splash_visible

    board.show_splash()
    _pump(qt_app, 1.0)                      # the overlay fades in over 0.8s
    assert board.splash_visible
    assert board.splash._fade.opacity() == 1.0

    board.hide_splash()
    _pump(qt_app, 1.0)
    assert not board.splash_visible


def test_the_meet_title_is_on_the_splash(board):
    """`live.html` omits it; the Qt splash carries it deliberately.

    The title comes from Settings → Display → Title, which reaches us as
    `meet_title` in /config.
    """
    assert board.splash.title.text() == 'Championnat provincial'
    assert board.splash.title.isVisible() or not board.splash.isVisible()


def test_the_background_image_is_loaded(board):
    """Sponsor logos are usually transparent PNGs and need something behind them."""
    assert not board.splash._background.isNull(), 'scoreboard_bg.png did not load'


def test_the_carousel_cross_fades_between_slides(board, qt_app):
    """Driven directly: a wall-clock window can span two ticks, and two flips
    land back on the layer you started from."""
    board.show_splash()
    _pump(qt_app, 0.9)
    before_layer = board.splash._front
    before_index = board.splash._index

    board.splash._advance()
    qt_app.processEvents()
    assert board.splash._index == (before_index + 1) % 3, 'did not advance one slide'
    assert board.splash._front != before_layer, 'layers did not swap'
    assert board.splash._crossfades, 'no cross-fade animation was started'


def test_the_carousel_timer_runs_while_it_is_up(board, qt_app):
    board.show_splash()
    _pump(qt_app, 0.9)
    assert board.splash._timer.isActive()
    assert board.splash._timer.interval() == 1000    # carousel_interval, in ms

    board.hide_splash()
    _pump(qt_app, 1.0)
    assert not board.splash._timer.isActive(), 'timer left running behind the board'


def test_a_single_image_does_not_rotate(qt_app, image_server):
    """Nothing to cross-fade to; the timer would just burn cycles."""
    window = BoardWindow(_config(carousel_images=['a.png']))
    window.resize(1920, 1080)
    window.show()
    window.splash.apply_config(window.cfg, image_server)
    _pump(qt_app, 0.8)
    window.show_splash()
    _pump(qt_app, 0.9)
    assert not window.splash._timer.isActive()
    window.hide_splash()
    window.close()


def test_images_arriving_after_it_opens_still_rotate(qt_app, image_server):
    """The operator can press the button before the downloads finish.

    `show_splash` can only start the timer for the images it can see at the time,
    and a lone image has nothing to rotate to — so the overlay used to stick on
    slide one for the rest of the meet, recovering only if it was dismissed and
    raised again. The loader emits one signal per image precisely so the first
    slide can go up while the rest are still coming in; the carousel has to pick
    them up when they land.
    """
    window = BoardWindow(_config())
    window.resize(1920, 1080)
    window.show()
    try:
        # Up with a single image, as it would be a moment into the downloads.
        window.splash._on_image('a.png', _png(qt_app))
        window.show_splash()
        _pump(qt_app, 0.9)
        assert not window.splash._timer.isActive(), 'one image has nothing to rotate to'

        window.splash._on_image('b.png', _png(qt_app))
        _pump(qt_app, 0.1)
        assert window.splash._timer.isActive(), 'the carousel never started'

        first = window.splash._index
        _pump(qt_app, 1.3)                  # carousel_interval is 1s
        assert window.splash._index != first, 'it is running but not advancing'
    finally:
        window.hide_splash()
        window.close()


def test_a_late_image_does_not_restart_a_dismissal(qt_app, image_server):
    """An image landing during the 800ms fade-out must not revive the timer."""
    window = BoardWindow(_config())
    window.resize(1920, 1080)
    window.show()
    try:
        window.splash._on_image('a.png', _png(qt_app))
        window.show_splash()
        _pump(qt_app, 0.9)
        window.hide_splash()                # still visible, fading

        window.splash._on_image('b.png', _png(qt_app))
        assert not window.splash._timer.isActive()
        _pump(qt_app, 1.0)
        assert not window.splash_visible
    finally:
        window.close()


def test_the_background_is_scaled_once_per_size(qt_app):
    """It is a 3840x2160 PNG, and the overlay repaints on every step of its fade.

    A fresh smooth downscale per paint is the only per-frame work of that size on
    this display, and the kiosk it would hurt is a Pi.
    """
    window = BoardWindow(_config(carousel_images=[]))
    window.resize(1920, 1080)
    window.show()
    try:
        window.show_splash()
        _pump(qt_app, 0.9)
        if window.splash._background.isNull():
            pytest.skip('scoreboard_bg.png is not in this checkout')
        assert window.splash._background_for == window.splash.size()
        cached = window.splash._background_scaled
        assert not cached.isNull()

        _pump(qt_app, 0.2)                  # more paints, same size
        assert window.splash._background_scaled is cached, 'rescaled mid-fade'

        window.resize(3840, 2160)
        _pump(qt_app, 0.2)
        assert window.splash._background_for == window.splash.size(), \
            'the cache outlived the size it was built for'
    finally:
        window.hide_splash()
        window.close()


def test_no_images_still_shows_title_and_background(qt_app):
    """A meet with no sponsor images should still get a tidy splash."""
    window = BoardWindow(_config(carousel_images=[]))
    window.resize(1920, 1080)
    window.show()
    window.show_splash()
    _pump(qt_app, 0.9)
    assert window.splash_visible
    assert window.splash.title.text() == 'Championnat provincial'
    window.hide_splash()
    window.close()


def test_a_race_starting_is_what_dismisses_it(board, qt_app):
    """The predicate app.py uses — the operator should not have to remember."""
    board.show_splash()
    _pump(qt_app, 0.9)
    assert board.splash_visible
    assert not board.any_lane_running

    board.apply_update({'running_time': '0.00', 'lane_running1': True})
    qt_app.processEvents()
    assert board.any_lane_running, 'the dismissal predicate never fires'


def test_app_tells_the_server_when_a_race_dismisses_the_splash(qt_app, image_server, monkeypatch):
    """Hiding silently would leave the /operator button lit with nothing behind it.

    The next press would then appear to do nothing, because the server still
    believes the overlay is on.
    """
    from scoreboard.app import ScoreboardApp
    app = ScoreboardApp(image_server, fullscreen=False)
    sent = []
    monkeypatch.setattr(app.link, 'send', lambda event, data=None: sent.append((event, data)))
    try:
        app.window.splash.apply_config(app.window.cfg, image_server)
        app.window.show_splash()
        _pump(qt_app, 0.9)
        assert app.window.splash_visible

        app._on_frame('update_scoreboard', {'running_time': '0.00',
                                            'lane_running1': True})
        _pump(qt_app, 1.0)
        assert not app.window.splash_visible, 'a race must dismiss the splash'
        assert ('set_overlay', {'active': False}) in sent, \
            'the server was not told, so /operator would be out of sync'
    finally:
        app.link.stop()


def test_display_overlay_frames_drive_it(qt_app, image_server):
    from scoreboard.app import ScoreboardApp
    app = ScoreboardApp(image_server, fullscreen=False)
    try:
        app._on_frame('display_overlay', {'active': True})
        _pump(qt_app, 0.9)
        assert app.window.splash_visible

        app._on_frame('display_overlay', {'active': False})
        _pump(qt_app, 1.0)
        assert not app.window.splash_visible
    finally:
        app.link.stop()


# ── Several kiosks on one server ───────────────────────────────────────────────
# The overlay is server-global state (`state._overlay_active`), so it has always
# been all-displays-or-none — the /operator button worked that way for browser
# clients too. What matters is that each kiosk asks *once*.

def test_a_dismissal_sends_exactly_one_frame(qt_app, image_server, monkeypatch):
    """`update_scoreboard` arrives ~10x/second during a race.

    The splash stays visible for the 800ms of its fade-out, so a naive
    `isVisible()` check re-fires on every frame — about ten `set_overlay` frames
    per dismissal, per kiosk, each of which the server then broadcasts to all the
    others.
    """
    from scoreboard.app import ScoreboardApp
    app = ScoreboardApp(image_server, fullscreen=False)
    sent = []
    monkeypatch.setattr(app.link, 'send', lambda event, data=None: sent.append((event, data)))
    try:
        app.window.show_splash()
        _pump(qt_app, 0.9)
        for tick in range(10):
            app._on_frame('update_scoreboard',
                          {'running_time': f'{tick}.00', 'lane_running1': True})
            _pump(qt_app, 0.05)
        assert sent == [('set_overlay', {'active': False})], \
            f'expected one frame, got {len(sent)}'
    finally:
        app.link.stop()


def test_redundant_dismissals_from_other_kiosks_are_harmless(qt_app, image_server, monkeypatch):
    """Every kiosk sends one, so each receives N rebroadcasts of the same state.

    `set_overlay` is an absolute set, not a toggle, so they cannot flip-flop; the
    extra frames must simply do nothing.
    """
    from scoreboard.app import ScoreboardApp
    app = ScoreboardApp(image_server, fullscreen=False)
    sent = []
    monkeypatch.setattr(app.link, 'send', lambda event, data=None: sent.append((event, data)))
    try:
        app._on_frame('display_overlay', {'active': True})
        _pump(qt_app, 0.9)
        assert app.window.splash_visible

        # This kiosk dismisses, then three others' rebroadcasts arrive.
        app._on_frame('update_scoreboard', {'lane_running1': True})
        for _ in range(3):
            app._on_frame('display_overlay', {'active': False})
        _pump(qt_app, 1.0)

        assert not app.window.splash_visible
        assert len(sent) == 1, 'rebroadcasts should not each trigger another send'
    finally:
        app.link.stop()


def test_the_operator_can_re_raise_it_during_the_fade_out(qt_app, image_server):
    """Pressing the button again mid-dismissal turns it straight around."""
    board_cfg = _config()
    window = BoardWindow(board_cfg)
    window.resize(1920, 1080)
    window.show()
    window.splash.apply_config(board_cfg, image_server)
    _pump(qt_app, 0.8)
    try:
        window.show_splash()
        _pump(qt_app, 0.9)
        window.hide_splash()
        _pump(qt_app, 0.2)                  # mid-fade
        assert not window.splash_visible

        window.show_splash()
        _pump(qt_app, 1.0)
        assert window.splash_visible, 'should have come back up'
        assert window.splash._fade.opacity() == 1.0
    finally:
        window.hide_splash()
        window.close()
