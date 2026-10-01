"""The shell's swipe between tabs (docs/app.md `A-03`, `A-10`).

A drag from an edge strip moves the current tab with the finger and brings its
neighbour in beside it; on release it settles on the neighbour or springs back.
These drive the gesture through the shell's own script. The harness never fires a
`setTimeout`, so they run with reduced motion on, which settles at once — the same
decision, without the slide.
"""

import pytest

from jsc import HAS_JS_ENGINE, run_page
from test_page_scripts_run import _TABS, _render

pytestmark = pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JavaScript engine (osascript or node)"
)

_REDUCED = r"""
window.matchMedia = function (q) {
  return { matches: /reduce/.test(q), addListener: function () {},
           addEventListener: function () {} };
};
"""

_DRIVE = r"""
function assert(ok, msg) { if (!ok) throw new Error(msg); }
/* The shell's `current` is a `let`, private to its own script; the tab it settles
   on is what `show()` writes for `A-04`, so read that. */
function tab() { return sessionStorage.getItem('tab') || 'scoreboard'; }
function swipe(from, to, ms) {
  onTouchStart({ touches: [{ clientX: from, clientY: 300 }], timeStamp: 0 });
  onTouchMove({ touches: [{ clientX: (from + to) / 2, clientY: 302 }] });
  onTouchMove({ touches: [{ clientX: to, clientY: 304 }] });
  onTouchEnd({ timeStamp: ms });
}
"""


@pytest.fixture(scope="module")
def shell():
    """The page, and the tab a swipe left of the Scoreboard lands on.

    Three tabs only: the stub's `getElementById` invents any element it is asked
    for, so a shell rendered without its Results tab still looks like three to the
    script. That shell's load path is `test_page_scripts_run.py`'s.
    """
    html = _render(
        "cloud/templates", "mobile.html", app_title="Coupe", t=_TABS, meet_id="abc123"
    )
    return html, "results"


def _drive(shell, script):
    html, second = shell
    script = script.replace("SECOND", repr(second))
    run_page(
        html.replace("</body>", f"<script>{_DRIVE}\n{script}</script></body>"),
        extra=_REDUCED,
    )


def test_a_long_drag_settles_on_the_next_tab(shell):
    """Half the width, slowly: past the quarter that commits a slow drag."""
    _drive(shell, "swipe(380, 180, 900); assert(tab() === SECOND, 'on ' + tab());")


def test_a_short_slow_drag_springs_back(shell):
    """Less than a quarter of the width, and too slow to be a flick."""
    _drive(
        shell, "swipe(380, 320, 900); assert(tab() === 'scoreboard', 'on ' + tab());"
    )


def test_a_flick_commits_from_the_minimum(shell):
    """60px in 80ms is a flick, though it is far short of a quarter."""
    _drive(shell, "swipe(380, 320, 80); assert(tab() === SECOND, 'on ' + tab());")


def test_under_the_minimum_is_never_a_switch(shell):
    """However fast: 30px is a twitch at the edge, not a swipe."""
    _drive(shell, "swipe(380, 350, 10); assert(tab() === 'scoreboard', 'on ' + tab());")


def test_there_is_nothing_before_the_first_tab(shell):
    """Dragging right on the first tab has no neighbour to bring in."""
    _drive(shell, "swipe(10, 300, 900); assert(tab() === 'scoreboard', 'on ' + tab());")


def test_a_mostly_vertical_drag_is_not_a_swipe(shell):
    _drive(
        shell,
        r"""
    onTouchStart({ touches: [{ clientX: 380, clientY: 100 }], timeStamp: 0 });
    onTouchMove({ touches: [{ clientX: 340, clientY: 400 }] });
    onTouchEnd({ timeStamp: 900 });
    assert(tab() === 'scoreboard', 'on ' + tab());
    """,
    )


def test_the_last_tab_goes_back_but_not_on(shell):
    """From Schedule, the last tab: a swipe on finds nothing, a swipe back works."""
    _drive(
        shell,
        r"""
    show(2);
    swipe(380, 100, 900);
    assert(tab() === 'schedule', 'went past the last tab: ' + tab());
    swipe(10, 300, 900);
    assert(tab() === 'results', 'did not go back: ' + tab());
    """,
    )
