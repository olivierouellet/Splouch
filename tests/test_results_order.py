"""The reader's own results order (docs/app.md `P-23`).

The pool sets a default (Settings → Display, the snapshot's `sort`); a reader who
chose otherwise in the picker's settings sees their order instead. These render the
real Results page and drive `renderResults` against the stub DOM (`tests/jsc.py`).
"""

import os

import pytest

from jsc import HAS_JS_ENGINE, run_page
from test_lap_counts import _render

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JavaScript engine (osascript or node)"
)

_SNAPSHOT = """{event: '4', heat: '1', sort: %s, lanes: [
    {channel: 2, place: '2', place_int: 2, time: '36.38', name: 'Gauthier'},
    {channel: 5, place: '1', place_int: 1, time: '36.14', name: 'Lévesque'}]}"""


@pytest.fixture(scope="module")
def results():
    return _render("server/templates", "results.html", t={})


def _drive(html, cookie, sort, check):
    script = f"""
function assert(ok, msg) {{ if (!ok) throw new Error(msg); }}
document.cookie = {cookie!r};
renderResults({_SNAPSHOT % sort});
var top = document.getElementById('lane_num1').textContent;
{check}
"""
    return run_page(html.replace("</body>", f"<script>{script}</script></body>"))


def test_no_choice_follows_the_pool(results):
    _drive(results, "", "'place'", "assert(top == '5', 'the winner first: ' + top);")
    _drive(results, "", "'lane'", "assert(top == '1', 'lane 1 first: ' + top);")


def test_a_reader_who_wants_places_gets_them_from_a_lane_pool(results):
    _drive(
        results,
        "splouch_results=place",
        "'lane'",
        "assert(top == '5', 'ranked here, not by the server: ' + top);",
    )


def test_a_reader_who_wants_lanes_gets_them_from_a_place_pool(results):
    _drive(
        results,
        "splouch_results=lane",
        "'place'",
        """assert(top == '1', 'lane 1 keeps its row: ' + top);
assert(document.getElementById('lane_name5').textContent == 'Lévesque', 'lane 5');""",
    )


def test_the_picker_offers_the_choice():
    with open(os.path.join(REPO, "cloud", "templates", "picker.html")) as f:
        picker = f.read()
    assert 'data-pref="results"' in picker
    assert "results: 'splouch_results'" in picker
