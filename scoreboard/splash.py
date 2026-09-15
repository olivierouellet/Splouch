"""The splash / carousel overlay.

Raised over the board when the operator presses the carousel button on
`/operator`, which reaches us as ``display_overlay {active: true}``. It covers the
whole window: the meet title along the top, sponsor images cross-fading beneath.

Three behaviours worth knowing:

* **A race dismisses it.** The operator does not have to remember. `app.py` hides
  the overlay when a lane starts running and tells the server, so the button on
  `/operator` and every browser client follow along — one source of truth.
* **The background is `scoreboard_bg.png`.** Sponsor logos are usually PNGs with
  transparency, and they need something behind them; a bare black rectangle wastes
  the one moment the display is not busy.
* **Images are fetched off the GUI thread.** They come from the server over HTTP
  (`GET /images/{filename}`), and the board must never block on the network — see
  `scoreboard/README.md`.

The meet title goes across the top, which `live.html` omits: a splash with no idea
whose meet it is helps nobody, and this screen is what a hall stares at between
heats.
"""
import os
import threading
import urllib.parse
import urllib.request

from PySide6.QtCore import (QObject, QPropertyAnimation, Qt, QTimer,
                          Signal)
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget

from .theme import Config
from .widgets import FitLabel

# Matching live.html: the overlay itself fades over 0.8s, images cross-fade over 1s.
FADE_MS       = 800
CROSSFADE_MS  = 1000
# Images sit inside a 4% margin, as `inset: 4%` does in the template.
_INSET = 0.04

_BACKGROUND = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'shared', 'static', 'img', 'scoreboard_bg.png')


class ImageLoader(QObject):
    """Downloads carousel images in a daemon thread, one signal per image.

    Emits as each arrives rather than batching, so the first slide can appear
    while the rest are still coming in — a meet with a dozen sponsor logos should
    not show a blank overlay until the last one lands.
    """

    loaded = Signal(str, bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._generation = 0

    def fetch(self, base_url: str, names):
        """Start fetching *names*. Supersedes any fetch already running."""
        self._generation += 1
        generation = self._generation
        base = base_url.strip().rstrip('/')

        def run():
            for name in names:
                if generation != self._generation:
                    return                      # config changed under us
                try:
                    url = f'{base}/images/{urllib.parse.quote(name)}'
                    with urllib.request.urlopen(url, timeout=10) as response:
                        data = response.read()
                except Exception as e:
                    print(f'[scoreboard] carousel image {name!r} failed: {e}',
                          flush=True)
                    continue
                if generation == self._generation:
                    self.loaded.emit(name, data)

        threading.Thread(target=run, daemon=True).start()


class SplashOverlay(QWidget):
    """Full-window splash: meet title over a cross-fading image carousel."""

    def __init__(self, cfg: Config, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._pixmaps = []          # QPixmap, in carousel order
        self._index = 0
        self._front = 0             # which of the two layers is showing
        # True from the moment a dismissal starts until the fade finishes. The
        # widget stays visible through those 800ms, so without this `is_up` would
        # keep reporting True and callers would act on it again — see `is_up`.
        self._dismissing = False

        self._background = QPixmap(_BACKGROUND) if os.path.exists(_BACKGROUND) \
            else QPixmap()
        # `_background` cropped to the window, kept between paints. The source is
        # 3840x2160, and a 1080p kiosk would otherwise pay a full smooth downscale
        # of an 8-megapixel image on every paint — which, with the opacity effect
        # below repainting the whole overlay on each step of an 800ms fade, is the
        # one place on this display where a Pi has real work to do per frame.
        self._background_scaled = QPixmap()
        self._background_for    = None      # the size `_background_scaled` fits

        # Two stacked image layers, cross-faded by swapping their opacities.
        self._layers = []
        self._effects = []
        for _ in range(2):
            label = QLabel(self)
            label.setAlignment(Qt.AlignCenter)
            label.setAttribute(Qt.WA_TranslucentBackground)
            effect = QGraphicsOpacityEffect(label)
            effect.setOpacity(0.0)
            label.setGraphicsEffect(effect)
            self._layers.append(label)
            self._effects.append(effect)

        self.title = FitLabel(cfg.meet_title, parent=self)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setAttribute(Qt.WA_TranslucentBackground)

        self._loader = ImageLoader(self)
        self._loader.loaded.connect(self._on_image)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

        self._fade = QGraphicsOpacityEffect(self)
        self._fade.setOpacity(1.0)
        self.setGraphicsEffect(self._fade)
        self._fade_anim = QPropertyAnimation(self._fade, b'opacity', self)
        self._fade_anim.setDuration(FADE_MS)
        # See BoardWindow._on_content_fade_finished for why this is a permanent
        # connection rather than connect/disconnect per fade.
        self._fade_done = None
        self._fade_anim.finished.connect(self._on_fade_finished)
        self._crossfades = []

        self.apply_config(cfg)
        self.hide()

    # ── Config ─────────────────────────────────────────────────────────────────

    def apply_config(self, cfg: Config, server: str = ''):
        """Adopt new theme/carousel settings; re-fetch if the image list changed."""
        names_changed = cfg.carousel_images != self.cfg.carousel_images
        self.cfg = cfg
        self.title.setText(cfg.meet_title)
        self.title.setStyleSheet(
            f"color: {cfg.color('header_value')}; background: transparent;")
        self.title.setFont(self.title.font())
        self._timer.setInterval(cfg.carousel_interval * 1000)

        if names_changed or not self._pixmaps:
            self._pixmaps = []
            self._index = 0
            if server and cfg.carousel_images:
                self._loader.fetch(server, cfg.carousel_images)
        self._layout_children()

    def _on_image(self, name: str, data: bytes):
        pixmap = QPixmap()
        if not pixmap.loadFromData(data):
            print(f'[scoreboard] carousel image {name!r} is not a readable image',
                  flush=True)
            return
        self._pixmaps.append(pixmap)
        if not self.isVisible():
            return
        if len(self._pixmaps) == 1:
            self._show_pixmap(0, animate=False)
        elif not self._timer.isActive() and not self._dismissing:
            # The rest of the carousel arrived after the operator opened it.
            # `show_splash` can only start the timer for the images it could see
            # at the time, and a single image has nothing to rotate to — so
            # without this the overlay sticks on slide one for the whole meet,
            # recovering only if it is dismissed and raised again.
            self._timer.start()

    # ── Show / hide ────────────────────────────────────────────────────────────

    @property
    def is_up(self) -> bool:
        """Showing, or on its way up — but False as soon as it starts going down.

        `isVisible()` alone is not enough. A dismissal fades over 800ms and the
        widget is visible the whole time, so a caller polling this on every
        `update_scoreboard` frame would re-trigger about ten times per dismissal;
        multiplied across kiosks, each one asking the server to broadcast to all
        the others.
        """
        return self.isVisible() and not self._dismissing

    def show_splash(self):
        if self._dismissing:
            # Caught mid-dismissal — turn straight around rather than waiting for
            # the fade to finish and hide the widget.
            self._dismissing = False
            self._run_fade(self._fade.opacity(), 1.0)
            if len(self._pixmaps) > 1:
                self._timer.start()
            return
        if self.isVisible():
            return
        self._index = 0
        if self.parent() is not None:
            self.setGeometry(self.parent().rect())
        self._layout_children()
        self.show()
        self.raise_()
        if self._pixmaps:
            self._show_pixmap(0, animate=False)
        # A single image has nothing to rotate to.
        if len(self._pixmaps) > 1:
            self._timer.start()
        self._dismissing = False
        self._run_fade(0.0, 1.0)

    def hide_splash(self):
        """Fade out, then hide once invisible — a cut back to the board is jarring."""
        self._timer.stop()
        if self._dismissing or not self.isVisible():
            return
        self._dismissing = True
        self._run_fade(self._fade.opacity(), 0.0, on_done=self._finish_hide)

    def _finish_hide(self):
        self._dismissing = False
        self.hide()

    def _on_fade_finished(self):
        callback, self._fade_done = self._fade_done, None
        if callback is not None:
            callback()

    def _run_fade(self, start: float, end: float, on_done=None):
        self._fade_anim.stop()
        self._fade_done = on_done
        self._fade.setOpacity(start)
        self._fade_anim.setStartValue(start)
        self._fade_anim.setEndValue(end)
        self._fade_anim.start()

    # ── Carousel ───────────────────────────────────────────────────────────────

    def _advance(self):
        if len(self._pixmaps) < 2:
            return
        self._index = (self._index + 1) % len(self._pixmaps)
        self._show_pixmap(self._index, animate=True)

    def _show_pixmap(self, index: int, animate: bool):
        if not self._pixmaps:
            return
        back = 1 - self._front
        self._layers[back].setPixmap(self._scaled(self._pixmaps[index]))
        self._layers[back].raise_()
        self.title.raise_()

        if not animate:
            self._crossfades.clear()
            self._effects[back].setOpacity(1.0)
            self._effects[self._front].setOpacity(0.0)
        else:
            # Hold references: a QPropertyAnimation that goes out of scope is
            # garbage-collected mid-flight and the slide never appears.
            self._crossfades = []
            for effect, target in ((self._effects[back], 1.0),
                                   (self._effects[self._front], 0.0)):
                anim = QPropertyAnimation(effect, b'opacity', self)
                anim.setDuration(CROSSFADE_MS)
                anim.setStartValue(effect.opacity())
                anim.setEndValue(target)
                anim.start()
                self._crossfades.append(anim)
        self._front = back

    def _scaled(self, pixmap: QPixmap) -> QPixmap:
        box = self._image_rect()
        if box[2] <= 0 or box[3] <= 0 or pixmap.isNull():
            return pixmap
        return pixmap.scaled(box[2], box[3], Qt.KeepAspectRatio,
                             Qt.SmoothTransformation)

    # ── Layout ─────────────────────────────────────────────────────────────────

    def _image_rect(self):
        inset_x = int(self.width() * _INSET)
        inset_y = int(self.height() * _INSET)
        top = inset_y + self._title_height()
        return (inset_x, top,
                self.width() - 2 * inset_x,
                max(0, self.height() - top - inset_y))

    def _title_height(self) -> int:
        return int(self.height() * 0.12) if self.cfg.meet_title else 0

    def _layout_children(self):
        height = self._title_height()
        self.title.setVisible(bool(self.cfg.meet_title))
        self.title.setGeometry(int(self.width() * _INSET), int(self.height() * _INSET),
                               self.width() - 2 * int(self.width() * _INSET),
                               height)
        self.title.set_max_px(max(12, int(height * 0.7)))
        x, y, w, h = self._image_rect()
        for layer in self._layers:
            layer.setGeometry(x, y, w, h)
        if self._pixmaps:
            self._layers[self._front].setPixmap(
                self._scaled(self._pixmaps[self._index % len(self._pixmaps)]))

    def resizeEvent(self, event):     # noqa: N802 — Qt naming
        super().resizeEvent(event)
        self._layout_children()

    def paintEvent(self, event):      # noqa: N802 — Qt naming
        """Draw `scoreboard_bg.png` behind the images, cropped to cover.

        Sponsor logos are usually transparent PNGs, so what sits behind them is
        what the audience actually sees for most of the overlay's life.
        """
        # No super().paintEvent() call: this widget carries a QGraphicsOpacityEffect,
        # and letting QWidget open a second QPainter on the same device while ours
        # is live trips "a paint device can only be painted by one painter at a
        # time". We draw the whole background ourselves, so there is nothing the
        # base implementation needs to add.
        painter = QPainter(self)
        if self._background.isNull():
            painter.fillRect(self.rect(), QColor(self.cfg.color('bg')))
            return
        if self._background_for != self.size():
            self._background_scaled = self._background.scaled(
                self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self._background_for = self.size()
        scaled = self._background_scaled
        x = (scaled.width() - self.width()) // 2
        y = (scaled.height() - self.height()) // 2
        painter.drawPixmap(0, 0, scaled, x, y, self.width(), self.height())
