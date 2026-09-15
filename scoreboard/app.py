"""Application entry point — wires the server link to the board window.

Startup order is deliberate: the window opens *before* the server is reachable.
At a meet the TV Pi and the server Pi power up together, and the kiosk usually
wins — it has no service to start. So the board draws immediately with fallback
theming and a status message, then adopts the real config once ``GET /config``
answers.

That only holds because **nothing blocking runs on the GUI thread**. Both server
calls are asynchronous: ``ConfigLoader`` fetches ``/config`` in a worker thread,
``ServerLink`` runs the WebSocket in another. An inline ``fetch_config()`` here
would defeat the whole design — a server that is reachable but not yet answering
blocks for the full timeout, and before the first paint that means a blank TV,
not merely a stale one.
"""
import argparse
import os
import sys
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication

from .board import BoardWindow
from .cache import load_cached_config, save_cached_config
from .client import ConfigLoader, ServerLink
from .fonts import load_app_fonts
from .theme import Config
from .updater import Updater
from .version import registration

DEFAULT_SERVER = os.environ.get('SPLOUCH_SERVER', 'http://splouch.local')

# Re-fetch /config this often after a failure, until it succeeds.
_CONFIG_RETRY_MS = 5000

# How often to refresh the "waiting" line's elapsed count.
_WAIT_TICK_MS = 1000

# How long a dropped link has to stay dropped before the board says anything.
# Reconnects are usually quicker than this — a switch renegotiating, the server
# restarting after an update — and flashing a warning across the board for two
# seconds is worse than the two seconds. The browser hedges the same way with
# `LEAVE_RESULTS_DEBOUNCE` in live.html.
#
# Nothing is being hidden by waiting: the clock freezes the moment the link goes,
# so the board stops inventing a race time whether or not the badge is up yet.
_DROP_GRACE_MS = 4000


def _host_of(url: str) -> str:
    """`http://splouch.local` -> `splouch.local` — the scheme is noise on a TV."""
    return url.split('://', 1)[-1].rstrip('/')


def _elapsed(seconds: float) -> str:
    """`47 s`, then `3 min 05 s`. The only thing on screen that tells an operator
    the display is still trying rather than hung."""
    seconds = int(seconds)
    if seconds < 60:
        return f'{seconds} s'
    return f'{seconds // 60} min {seconds % 60:02d} s'


class ScoreboardApp:
    """Owns the window, the link, and the config-refresh policy."""

    def __init__(self, server: str, fullscreen: bool = True):
        self.server = server
        # Start from the last config the server gave us. The board then comes up
        # already themed and in the right language instead of showing an English
        # fallback for however long the server takes to boot.
        self.config  = Config(load_cached_config() or {})
        self.window  = BoardWindow(self.config)
        self.link    = ServerLink(server, register=registration)
        self._have_config = False

        self.link.frame.connect(self._on_frame)
        self.link.connected.connect(self._on_connected)

        self._waiting_since = time.monotonic()
        self._waiting_key   = 'waiting_server'
        # Never reached the server at all, as opposed to having lost it. The two
        # want completely different treatment — see _on_connected.
        self._ever_connected = False
        self._show(self.window, fullscreen)

        # Holds the link-lost badge back until a drop looks real rather than
        # momentary. Single-shot: `_on_connected` cancels it on reconnect.
        self._drop_timer = QTimer()
        self._drop_timer.setSingleShot(True)
        self._drop_timer.setInterval(_DROP_GRACE_MS)
        self._drop_timer.timeout.connect(self._announce_drop)

        # Ticks the elapsed count on the waiting screen. Without it the TV looks
        # identical whether the app is retrying or has hung, which is exactly the
        # ambiguity a black screen creates at a meet.
        self._wait_timer = QTimer()
        self._wait_timer.setInterval(_WAIT_TICK_MS)
        self._wait_timer.timeout.connect(self._tick_waiting)
        self._start_waiting('waiting_server')

        # Self-update, triggered by the server (see scoreboard/updater.py).
        self.updater = Updater()
        self.updater.line.connect(self._on_update_line)
        self.updater.done.connect(self._on_update_done)

        self.config_loader = ConfigLoader(server)
        self.config_loader.loaded.connect(self._on_config)
        self.config_loader.failed.connect(self._on_config_failed)

        self._config_timer = QTimer()
        self._config_timer.setInterval(_CONFIG_RETRY_MS)
        self._config_timer.timeout.connect(self.config_loader.request)
        self.config_loader.request()
        self.link.start()

    @staticmethod
    def _show(window, fullscreen: bool):
        """Show *window*, sizing it first so F11/Esc has a sane windowed size.

        ``showNormal()`` restores the geometry the window had before it went
        fullscreen — with no prior resize that is a useless 100px box, so set one
        even on the kiosk path where it is never seen directly.
        """
        window.resize(1280, 720)
        window.set_fullscreen(fullscreen)
        if not fullscreen:
            window.show()

    # ── Waiting screen ─────────────────────────────────────────────────────────

    def _tick_waiting(self):
        """Refresh whichever indicator is up with a fresh elapsed count.

        Reads the strings from the *current* config every tick rather than caching
        the rendered text, so a config arriving mid-wait switches the language on
        screen immediately.

        One timer drives both indicators: the full-screen message on a cold boot, and
        the badge once a mid-meet drop has outlasted its grace period.
        """
        if self.window.link_lost:
            self.window.set_link_lost(True, self._drop_text())
            return
        strings = self.config.strings
        self.window.set_status(
            strings.get(self._waiting_key, self._waiting_key),
            f"{_host_of(self.server)} · {strings.get('retrying', 'retrying')} · "
            f"{_elapsed(time.monotonic() - self._waiting_since)}")

    def _start_waiting(self, key: str):
        self._waiting_key   = key
        self._waiting_since = time.monotonic()
        self._tick_waiting()
        self._wait_timer.start()

    def _stop_waiting(self):
        self._wait_timer.stop()
        self.window.set_status('')

    @property
    def _waiting(self) -> bool:
        return self._wait_timer.isActive()

    # ── Config ─────────────────────────────────────────────────────────────────

    def _on_config_failed(self, reason: str):
        """A /config fetch failed — keep retrying until the server appears.

        Only noisy about it before the first success; after that the board already
        has usable config and a failed refresh is not worth a log line per retry.
        """
        if not self._have_config:
            print(f'[scoreboard] config fetch failed ({reason}) — retrying', flush=True)
            self._config_timer.start()

    def _on_config(self, raw: dict):
        """Apply a freshly fetched config.

        A lane-count change alters the number of rows, which means rebuilding the
        window rather than restyling it — so it is handled separately from a
        pure theme change.
        """
        self._config_timer.stop()
        self._have_config = True
        save_cached_config(raw)
        new_config = Config(raw)

        if new_config.num_lanes != self.config.num_lanes:
            was_full = self.window.isFullScreen()
            snapshot = dict(self.window.snapshot)
            status   = self.window.status_text()
            detail   = self.window.status_detail.text()

            # Build and show the replacement BEFORE closing the old window.
            # Closing the only visible window emits QApplication::lastWindowClosed,
            # which quits the app under the default quitOnLastWindowClosed — and
            # because that is a *clean* exit (status 0), start-scoreboard.sh would
            # treat it as a deliberate quit and not restart. Overlapping the two
            # windows keeps the count above zero and the signal unfired.
            old_window  = self.window
            self.window = BoardWindow(new_config)
            self.window.snapshot.update(snapshot)
            self.window.refresh()
            self.window.set_status(status, detail)
            self._show(self.window, was_full)
            old_window.close()
            old_window.deleteLater()
        else:
            self.window.set_config(new_config)

        # After any rebuild, so the surviving window is the one that gets it.
        self.window.splash.apply_config(new_config, self.server)

        self.config = new_config
        # A language change lands here too — repaint the waiting screen so it
        # switches immediately instead of at the next tick.
        if self._waiting:
            self._tick_waiting()

    # ── Self-update ────────────────────────────────────────────────────────────

    def _on_update(self, data):
        """Server asked us to move to its ref.

        Refused mid-race: a successful update restarts the app, and a black TV
        while somebody is swimming is worse than being a version behind.
        """
        target = (data or {}).get('target', '')
        if not target:
            self.link.send('update_log',
                           {'text': 'No target ref given.', 'error': True,
                            'done': False})
            return
        if self.window.any_lane_running:
            self.link.send('update_log',
                           {'text': 'A race is running — not updating now.',
                            'error': True, 'done': False})
            return
        if not self.updater.start(target):
            return                      # already updating; ignore the repeat
        self.link.send('update_log', {'text': f'Updating to {target}…',
                                      'error': False, 'done': None})

    def _on_update_line(self, text: str, error: bool):
        print(f'[scoreboard] update: {text}', flush=True)
        self.link.send('update_log', {'text': text, 'error': error, 'done': None})

    def _on_update_done(self, ok: bool):
        self.link.send('update_log',
                       {'text': 'Restarting on the new version.' if ok
                                else 'Update failed — still on the old version.',
                        'error': not ok, 'done': ok})
        if not ok:
            return
        # Exit non-zero so start-scoreboard.sh relaunches us on the new code.
        # Give the frame above a moment to reach the server first, or the operator
        # never learns how it ended.
        QTimer.singleShot(1000, lambda: os._exit(1))

    # ── Splash ─────────────────────────────────────────────────────────────────

    def _dismiss_splash_if_racing(self):
        """A race starting takes the splash down, without the operator acting.

        We tell the server rather than just hiding locally, so the toggle on
        /operator and every browser client agree with the TV. Hiding silently
        would leave the button lit with nothing behind it, and the next press
        would appear to do nothing.
        """
        if not (self.window.splash_visible and self.window.any_lane_running):
            return
        self.window.hide_splash()
        self.link.send('set_overlay', {'active': False})

    # ── Server events (docs/api.md §2 `/ws/scoreboard`) ────────────────────────

    def _on_connected(self, ok: bool):
        """Cold boot and a mid-meet drop are different problems, so they look different.

        * **Never connected.** The board is empty. The full-screen message is right:
          there is nothing to cover, and whoever is setting up needs to know why the
          TV is blank.
        * **Dropped mid-meet.** The board is holding a real start list or a real set
          of results. Covering those is worse than the outage, so it gets a badge —
          and only once the drop has outlasted `_DROP_GRACE_MS`, since most are over
          in a second or two.

        The clock freezes immediately either way. That is not cosmetic: the ticker
        interpolates between console frames, so left running it fabricates a race
        time for as long as the link is down.
        """
        if ok:
            self._ever_connected = True
            self._drop_timer.stop()
            self._stop_waiting()
            self.window.set_link_lost(False)
            # The server's per-connection state (theme, lane count) may have moved
            # while we were away; a reconnect is the cheapest place to resync.
            self.config_loader.request()
            return

        if not self._ever_connected:
            self._start_waiting('waiting_server')
            return
        if self.window.link_lost:
            # Already handling this outage. The receive loop reports a failure on
            # every reconnect attempt — every few seconds — and re-stamping the
            # start time here made the badge's counter restart on each one, so it
            # never showed how long the link had actually been down.
            return
        # Detecting a dead link costs `_STALE` seconds of silence, so by the time we
        # hear about it the outage is already that old. Backdate to when the server
        # actually went quiet: the badge then counts from the truth rather than from
        # whenever the receive loop happened to give up.
        silent = self.link.silent_seconds()
        self._waiting_since = time.monotonic() - silent
        self.window.set_link_lost(True)     # freeze now, announce later

        # And spend only the grace period that is left. A pulled cable has already
        # outlasted it by the time it is noticed, so the badge goes up at once;
        # what the grace still buys is the case detected instantly — the server
        # closing the socket cleanly on a restart, which is usually back in seconds.
        remaining = _DROP_GRACE_MS - int(silent * 1000)
        if remaining > 0:
            self._drop_timer.start(remaining)
        else:
            self._announce_drop()

    def _announce_drop(self):
        """The drop outlasted the grace period — put the badge up."""
        if not self.window.link_lost:
            return                        # reconnected while the timer was pending
        self.window.set_link_lost(True, self._drop_text())
        self._wait_timer.start()          # ticks the elapsed count on the badge

    def _drop_text(self) -> str:
        headline = self.config.strings.get('connection_lost', '⚠ CONNECTION LOST')
        return f'{headline} · {_elapsed(time.monotonic() - self._waiting_since)}'

    def _on_frame(self, event: str, data):
        if event == 'update_scoreboard':
            self.window.apply_update(data or {})
            self._dismiss_splash_if_racing()
        elif event == 'reload':
            # Settings or theme changed — re-fetch config and redraw.
            self.config_loader.request()
        elif event == 'test_mode':
            active = bool((data or {}).get('active'))
            # Wipe the board before the badge goes up, as `live.html` does
            # (`reset_state(); mode_to_intro()`). A recording replays the same
            # event and heat every run, so on a re-run the heat key never changes
            # and nothing else would clear the previous run: its times, places and
            # podium tints would sit under the new start list with the badge on
            # top saying the whole thing is a test.
            if active:
                self.window.reset()
            self.window.set_test_mode(active)
        elif event == 'display_overlay':
            # The carousel button on /operator. Shows the splash over the board.
            if (data or {}).get('active'):
                self.window.show_splash()
            else:
                self.window.hide_splash()
        elif event == 'race_finished':
            # Results are confirmed. Belt-and-braces: the console normally clears
            # every `lane_running` flag first, but if a frame were missed the
            # ticker would keep counting up over the final times.
            self.window.stop_clock()
            # And the same backstop for the podium, which the browser also reveals
            # from this event. It is a no-op once the reveal has already run.
            self.window.highlight_podium()
        elif event == 'update':
            self._on_update(data)
        elif event == 'columns_state':
            # The operator's manual toggle on /operator, same mechanism as the
            # automatic reveal at race start.
            self.window.set_columns_visible(not (data or {}).get('hidden', False))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog='scoreboard', description='Splouch Qt scoreboard (TV display)')
    parser.add_argument('--server', default=DEFAULT_SERVER,
                        help=f'Splouch server base URL (default: {DEFAULT_SERVER})')
    parser.add_argument('--windowed', action='store_true',
                        help='run in a window instead of fullscreen (development)')
    args = parser.parse_args(argv)

    # Crisp text on a 4K TV: scale by the display's real DPI rather than
    # rendering at 1080p and upscaling, which is what the browser kiosk did.
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    qt_app = QApplication(sys.argv[:1])
    qt_app.setApplicationName('Splouch Scoreboard')
    load_app_fonts()   # needs the QApplication — Qt has no font database before it

    app = ScoreboardApp(args.server, fullscreen=not args.windowed)
    try:
        return qt_app.exec()
    finally:
        app.link.stop()
