"""What a phone shows before it has seen anything (docs/app.md `L-13`, `R-01`).

Two first moments, one on each board tab — and, below, what a crowded board gives up (`L-24`):

* **The Scoreboard's first heat is a baseline, not a change.** The join replay (cloud)
  or the push on connect (Pi) carries the current heat's times beside its number.
  The board used to start its "last event" at `0`, read that replay as a heat change
  and blank the only state a late joiner had.
* **Results before the first snapshot is a line, not a grid.** Blank rows mean a heat
  under way on the live board; here there is no heat to fill them.
"""

import os
import re

import pytest
from jinja2 import Environment, FileSystemLoader

import state
from conftest import stub_url_for
from jsc import HAS_JS_ENGINE, run_page

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _render(own_dir, template, **extra):
    env = Environment(
        loader=FileSystemLoader(
            [os.path.join(REPO, own_dir), os.path.join(REPO, "shared", "templates")]
        )
    )
    stub_url_for(env)
    return env.get_template(template).render(
        num_lanes=6,
        labels={},
        theme_colors=state.DEFAULT_THEME_COLORS,
        theme_fonts=state.DEFAULT_THEME_FONTS,
        t={"waiting_results": "Waiting…"},
        **extra,
    )


@pytest.fixture(scope="module", params=["server/templates", "cloud/templates"])
def live(request):
    return _render(request.param, "live-mobile.html")


@pytest.fixture(scope="module", params=["server/templates", "cloud/templates"])
def results(request):
    return _render(request.param, "results.html")


_DRIVE = r"""
function assert(ok, msg) { if (!ok) throw new Error(msg); }
function time(i) { var e = document.getElementById('lane_time' + i);
                   return e.textContent || e.innerHTML; }
"""


def _drive(html, script):
    return run_page(
        html.replace("</body>", f"<script>{_DRIVE}\n{script}</script></body>")
    )


needs_js = pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JavaScript engine (osascript or node)"
)


# ── L-13 ──────────────────────────────────────────────────────────────────────


@needs_js
def test_the_first_heat_after_a_connect_keeps_its_times(live):
    """A late joiner's replay: the heat and its finals in one frame."""
    _drive(
        live,
        r"""
    applyScoreboardFrame({current_event: '5', current_heat: '2',
                          lane_time1: '1:02.31', lane_place1: '1'});
    assert(time(1) === '1:02.31', 'the baseline was blanked: ' + JSON.stringify(time(1)));
    """,
    )


@needs_js
def test_a_real_heat_change_still_blanks(live):
    """The exception is the first heat only — the next one is a change."""
    _drive(
        live,
        r"""
    applyScoreboardFrame({current_event: '5', current_heat: '2',
                          lane_time1: '1:02.31', lane_place1: '1'});
    applyScoreboardFrame({current_event: '5', current_heat: '3'});
    assert(time(1) === '', 'a new heat kept the last one\'s time: ' + JSON.stringify(time(1)));
    """,
    )


@needs_js
def test_a_reconnect_starts_a_new_baseline(live):
    """`reset_state` is what the socket's connect handler runs, so a reconnect —
    which replays the heat again — must not read that replay as a change either."""
    _drive(
        live,
        r"""
    applyScoreboardFrame({current_event: '5', current_heat: '2'});
    reset_state();
    applyScoreboardFrame({current_event: '6', current_heat: '1',
                          lane_time1: '0:58.10', lane_place1: '2'});
    assert(time(1) === '0:58.10', 'the replay after a reconnect was blanked: '
                                  + JSON.stringify(time(1)));
    """,
    )


# ── R-01 ──────────────────────────────────────────────────────────────────────


def _css(html):
    style = re.search(r"<style>(.*?)</style>", html, flags=re.DOTALL)
    assert style
    return re.sub(r"/\*.*?\*/", "", style.group(1), flags=re.DOTALL)


def test_the_waiting_line_replaces_the_table(results):
    """Not under it: until a snapshot the table is not drawn at all, in any
    orientation, and once one arrives the line goes."""
    css = _css(results)
    assert re.search(
        r"body:not\(\.has-results\)\s+\.timing-content\s*\{\s*display:\s*none", css
    )
    assert re.search(r"body\.has-results\s+#waiting\s*\{\s*display:\s*none", css)
    assert "@media (orientation" not in css


def test_a_snapshot_shows_the_table_and_a_wipe_brings_the_line_back(results):
    """`R-02`'s wipe returns the tab to exactly the waiting state."""
    assert "document.body.classList.add('has-results')" in results
    assert (
        "function showWaiting() { document.body.classList.remove('has-results'); }"
        in results
    )


@needs_js
def test_the_results_page_still_loads(results):
    run_page(results)


# ── L-24 ──────────────────────────────────────────────────────────────────────
#
# The stub DOM has no layout, so the sizes are set by hand: a board 600px tall,
# and a table whose height follows the row scale. `classList` is a no-op there, so the relay step is
# read off the CSS and the type step off the `--row-scale` it writes.

_CROWDED = r"""
window.matchMedia = function () { return { matches: true }; };
document.getElementById('scoreboard').clientHeight = 600;
/* As real layout would: the table's height follows the row scale. */
Object.defineProperty(document.getElementById('timing-board'), 'scrollHeight', {
  get: function () {
    return %d * parseFloat(document.documentElement.style
                             .getPropertyValue('--row-scale') || '1');
  }
});
fitNameFontSize();
var scale = document.documentElement.style.getPropertyValue('--row-scale');
assert(scale === '%s', 'row scale ' + JSON.stringify(scale));
"""


@needs_js
@pytest.mark.parametrize(
    "wants, scale",
    [(500, "1"), (700, "0.857"), (2000, "0.720")],
    ids=["fits", "shrinks", "floor-then-scroll"],
)
def test_a_crowded_board_shrinks_its_rows_to_a_floor(live, wants, scale):
    """A board that fits is left alone; one that does not shrinks in proportion,
    and never below 0.72 — past that it scrolls rather than become unreadable."""
    _drive(live, _CROWDED % (wants, scale))


def test_the_relay_line_goes_before_the_type_shrinks(live):
    """The first thing a crowded board gives up, and only on a compact one."""
    css = _css(live)
    compact = css[css.index("@media (max-width: 599px)") :]
    assert re.search(r"body\.crowded\s+\.name-sub\s*\{\s*display:\s*none", compact)
    assert "var(--row-scale, 1)" in compact
