"""Empty lanes as still water (docs/app.md `L-25`), on the phone pages.

A lane with nobody in it draws a faint, slowly drifting wave instead of a blank row
or a `—`. Which rows get it is decided from the cells after every change, so these
render the real pages, run their scripts against the stub DOM (`tests/jsc.py`) and
drive the real handlers. The stub's `classList` is a no-op by design, so the rows
here get a working one before anything runs — the class is the whole answer.
"""

import pytest

from jsc import HAS_JS_ENGINE, run_page
from test_lap_counts import _render

pytestmark = pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JavaScript engine (osascript or node)"
)

_DRIVE = r"""
function assert(ok, msg) { if (!ok) throw new Error(msg); }
for (var __i = 1; __i <= NUM_LANES; __i++) {
    document.getElementById('row' + __i).classList = { _s: {},
        add: function (c) { this._s[c] = true; },
        remove: function () { for (var k = 0; k < arguments.length; k++) delete this._s[arguments[k]]; },
        toggle: function (c, on) { if (on === undefined) on = !this._s[c];
                                   if (on) this._s[c] = true; else delete this._s[c]; return on; },
        contains: function (c) { return !!this._s[c]; } };
}
function still() {
    var out = [];
    for (var i = 1; i <= NUM_LANES; i++)
        out.push(document.getElementById('row' + i).classList.contains('still-water') ? 1 : 0);
    return out.join('');
}
"""


def _drive(html, script):
    return run_page(
        html.replace("</body>", f"<script>{_DRIVE}\n{script}</script></body>")
    )


@pytest.fixture(scope="module")
def phone():
    return _render("server/templates", "live-mobile.html")


@pytest.fixture(scope="module")
def results():
    return _render("server/templates", "results.html", t={})


def test_an_empty_lane_among_named_ones_is_still_water(phone):
    _drive(
        phone,
        r"""
    applyScoreboardFrame({current_event: '4', current_heat: '1',
                          lane_name2: 'Roy', lane_name3: 'Côté', lane_name4: 'Morin',
                          lane_name5: 'Lavoie'});
    assert(still() === '100001', 'lanes 1 and 6 are empty: ' + still());
    """,
    )


def test_a_board_with_no_names_claims_nothing(phone):
    """A console that sends no names leaves every lane bare — not a pool of empty ones."""
    _drive(
        phone,
        r"""
    applyScoreboardFrame({current_event: '4', current_heat: '1', lane_time2: '20.0'});
    assert(still() === '000000', 'no names, no still water: ' + still());
    """,
    )


def test_a_running_lane_with_no_name_is_a_swimmer(phone):
    _drive(
        phone,
        r"""
    applyScoreboardFrame({current_event: '4', current_heat: '1', lane_name2: 'Roy',
                          lane_running1: true});
    assert(still()[0] === '0', 'lane 1 is swimming: ' + still());
    assert(still()[2] === '1', 'lane 3 is empty: ' + still());
    """,
    )


def test_a_lane_that_fills_stops_being_still_water(phone):
    _drive(
        phone,
        r"""
    applyScoreboardFrame({current_event: '4', current_heat: '1', lane_name2: 'Roy'});
    assert(still()[0] === '1', 'lane 1 starts empty: ' + still());
    applyScoreboardFrame({lane_name1: 'Gagné'});
    assert(still()[0] === '0', 'lane 1 has a swimmer now: ' + still());
    """,
    )


def test_an_unfilled_rank_has_no_lane_number_and_is_still_water(results):
    _drive(
        results,
        r"""
    renderResults({event: '4', heat: '1', sort: 'place', lanes: [
        {channel: 6, place: '1', time: '36.14', name: 'Lévesque'},
        {channel: 5, place: '2', time: '36.38', name: 'Gauthier'}]});
    assert(document.getElementById('lane_num3').textContent === '',
           'an unfilled rank has no lane: '
           + JSON.stringify(document.getElementById('lane_num3').textContent));
    assert(still() === '001111', 'unfilled ranks are still water: ' + still());
    assert(document.getElementById('spoken_lane3').textContent === '',
           'an unfilled rank says nothing');
    """,
    )


def test_a_lane_without_a_result_keeps_its_number(results):
    _drive(
        results,
        r"""
    renderResults({event: '4', heat: '1', sort: 'lane', lanes: [
        {channel: 2, place: '1', time: '36.14', name: 'Lévesque'}]});
    assert(document.getElementById('lane_num1').textContent === '1', 'lane 1 keeps its number');
    assert(still() === '101111', 'every lane but 2 is empty: ' + still());
    """,
    )
