"""Shared test setup.

Nothing here imports PySide6 at module level — most of the suite is deliberately
Qt-free so it runs in CI without the `scoreboard` extra, and importing Qt here
would break that. The `qt_app` fixture skips instead.
"""
import gc
import os
import sys
import tempfile

import pytest

# Offscreen rendering, and a throwaway cache dir. ScoreboardApp reads the cached
# config at startup and writes it after every successful fetch, so without this a
# test run leaves a stale lane count in the developer's real ~/.cache that the
# next run silently picks up.
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('XDG_CACHE_HOME', tempfile.mkdtemp(prefix='splouch-test-'))

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (REPO, os.path.join(REPO, 'server')):
    if path not in sys.path:
        sys.path.insert(0, path)

# Module-level so the QApplication outlives every fixture scope. A fixture that
# only yields it holds the sole Python reference, so PySide destroys the C++ object
# at teardown — and Qt discards all fonts registered via addApplicationFont along
# with it. The next module then gets a fresh, font-less application while
# `fonts._APP_FONTS_LOADED` still reports them as loaded, and every family
# silently resolves to the monospace fallback.
_QT_APP = None


@pytest.fixture(scope='session')
def qt_app():
    """A single QApplication for the whole session, with the bundled fonts loaded."""
    global _QT_APP
    pytest.importorskip('PySide6', reason='needs the `scoreboard` extra (PySide6)')
    from PySide6.QtWidgets import QApplication

    from scoreboard.fonts import load_app_fonts
    if _QT_APP is None:
        _QT_APP = QApplication.instance() or QApplication([])
        # Mirror main(): register fonts before anything resolves a family, since
        # resolve_family() memoises its answers.
        load_app_fonts()
    return _QT_APP


@pytest.fixture(autouse=True)
def _reclaim_qt_garbage():
    """Collect leaked Qt objects here, on the main thread, after every test.

    Several tests build a `BoardWindow` or a whole `ScoreboardApp` and never destroy
    it — `close()` hides a window, it does not delete it — so the C++ widget survives
    as Python garbage until the collector happens to run.

    That is a deadlock waiting to happen, and it duly did. If the collector fires on
    a **non-GUI** thread, `~QWidget` calls `QWindow::close()`, which blocks in
    `QWindowSystemInterface::flushWindowSystemEvents()` until the GUI thread
    processes the event. In this suite the GUI thread is pytest's main thread, and
    the tests that exercise an async route park it in `asyncio.run()` →
    `selectors.select()` with no Qt event loop running. It never flushes, the worker
    never returns, and the run hangs with the process at 0% CPU.

    Reclaiming here means the collector never has that garbage to find later, and
    what it does find, it frees on the thread Qt expects. Only worth doing once Qt is
    actually in play, so the Qt-free CI run pays nothing.
    """
    yield
    if _QT_APP is not None:
        gc.collect()


@pytest.fixture
def settle_podium(qt_app):
    """Run the staggered podium reveal to its end state, without sleeping.

    The board reveals gold, silver and bronze 400ms apart and eases each one in over
    500ms, so a test that only calls `processEvents()` catches the rows mid-fade at
    some colour between the stripe and the tint. Fire the pending timers and seek
    every fade to its end instead — 1.3s of animation, deterministically.
    """
    def run(board):
        qt_app.processEvents()
        for timer in list(board._podium_timers):
            if timer.isActive():
                timer.stop()
                timer.timeout.emit()
        for row in board.rows:
            if row._podium_anim is not None:
                row._podium_anim.setCurrentTime(row._podium_anim.duration())
        qt_app.processEvents()
    return run


def settings_markup():
    """The Settings page's markup: `settings.html` plus every tab partial.

    The page used to be one 2594-line file, and a dozen tests grep it for a class
    name, a form field or a hold-to-confirm attribute. Its tab panes now live in
    `templates/settings/`, so "the settings markup" is a template and the
    files it includes — this joins them so those greps keep asking the question
    they were written to ask.

    Reading the source rather than a rendered page is deliberate in those tests:
    they check what the template *says*, including the branches a single render
    would not take.
    """
    import glob
    base = os.path.join(REPO, 'server', 'templates')
    paths = [os.path.join(base, 'settings.html')]
    paths += sorted(glob.glob(os.path.join(base, 'settings', '*.html')))
    return '\n'.join(open(p, encoding='utf-8').read() for p in paths)
