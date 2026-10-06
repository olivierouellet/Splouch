"""What a screen reader hears on the phone pages (docs/app.md `X-01`–`X-04`).

The drawn board is a table of six cells per lane, which a screen reader walks as
six unrelated stops — `1`, a name, `CNQ`, `2:01.41`, `-1.10`, `1` — with nothing
saying which number is a time and which a place. So the drawn table is hidden from
assistive technology, and a list of sentences stands in for it, rebuilt from the
cells so it can never say something the screen does not.
"""

import os
import re

import pytest
from jinja2 import Environment, FileSystemLoader

import state
from conftest import stub_url_for
from jsc import HAS_JS_ENGINE, run_page
from test_scoreboard_base_shared import _SCHED_T, _render as _render_shared, _sched

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

needs_js = pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JavaScript engine (osascript or node)"
)

_LABELS = {
    "event": "EVENT",
    "heat": "HEAT",
    "lane": "LN",
    "club": "CLUB",
    "time": "TIME",
    "delta": "DIFF",
    "place": "PL",
}


def _live(own_dir):
    env = Environment(
        loader=FileSystemLoader(
            [os.path.join(REPO, own_dir), os.path.join(REPO, "shared", "templates")]
        )
    )
    stub_url_for(env)
    return env.get_template("live-mobile.html").render(
        num_lanes=4,
        labels=_LABELS,
        theme_colors=state.DEFAULT_THEME_COLORS,
        theme_fonts=state.DEFAULT_THEME_FONTS,
        t={"spoken_laps": "Laps"},
        show_laps=True,
    )


@pytest.fixture(scope="module", params=["server/templates", "cloud/templates"])
def board(request):
    return _live(request.param)


_DRIVE = r"""
function assert(ok, msg) { if (!ok) throw new Error(msg); }
function said(id) { return document.getElementById(id).textContent; }
"""


# The stub DOM keeps `innerHTML` and `textContent` apart; a browser does not, and
# the board writes most cells through `innerHTML`. Keep them in step, as a browser
# would, rather than teach the page about the stub.
_SYNC = r"""
var __bare = __node;
__node = function (tag) {
  var n = __bare(tag), html = '';
  Object.defineProperty(n, 'innerHTML', {
    get: function () { return html; },
    set: function (v) { html = String(v); n.textContent = html.replace(/<[^>]*>/g, ''); }
  });
  return n;
};
"""


def _drive(html, script):
    run_page(
        html.replace("</body>", f"<script>{_DRIVE}\n{script}</script></body>"),
        extra=_SYNC,
    )


def test_the_drawn_board_is_hidden_and_the_sentences_stand_in(board):
    assert re.search(r'<table id="timing-board" aria-hidden="true"', board)
    assert 'id="header_event_cell" aria-hidden="true"' in board
    assert 'id="header_heat_cell" aria-hidden="true"' in board
    assert '<ul class="sr-only" id="spoken_lanes" role="list">' in board
    # Before any frame, an empty lane says its number and nothing else.
    assert '<li id="spoken_lane3">LN 3</li>' in board


@needs_js
def test_a_lane_is_one_sentence_in_the_servers_words(board):
    _drive(
        board,
        r"""
    applyScoreboardFrame({current_event: '12', current_heat: '3',
                          lane_name1: 'Relay Team A', lane_name_alt1: 'Roy · Gagnon',
                          lane_club1: 'CAMO', lane_time1: '2:01.41', lane_place1: '1'});
    var got = said('spoken_lane1');
    assert(got === 'LN 1, Relay Team A, Roy · Gagnon, CLUB CAMO, TIME 2:01.41, PL 1',
           'lane 1 said ' + JSON.stringify(got));
    assert(said('spoken_lane3') === 'LN 3', 'an empty lane said more than its number');
    """,
    )


@needs_js
def test_the_heat_is_one_utterance_and_silent_before_a_number(board):
    _drive(
        board,
        r"""
    assert(said('spoken_heat') === '', 'spoke before a heat arrived');
    applyScoreboardFrame({current_event: '12', current_heat: '3'});
    assert(said('spoken_heat') === 'EVENT 12, HEAT 3',
           'heat said ' + JSON.stringify(said('spoken_heat')));
    """,
    )


def test_the_lap_count_is_named_not_read_as_a_difference(board):
    """No column names a lap count; read bare after a time it is a second time.
    The stub's classList cannot hold `lap-count`, so the choice is read off the
    source, and the word off the server's `[mobile]` table."""
    assert "contains('lap-count')" in board
    assert '"laps": "Laps"' in board


# ── The start list (`X-03`) and its empty states (`X-04`) ──────────────────────


def test_a_start_list_lane_is_one_sentence():
    html = _sched("server/templates")
    assert (
        "var parts = [SPOKEN_LABELS.lane + ' ' + lane.lane, laneDisplayName(lane)];"
        in html
    )
    # The time is named by kind (`S-22`): `NT` or a bare number tells a listener
    # nothing, and neither does a colour.
    assert "if (lt) parts.push(lt.spoken);" in html
    assert '<span class="sr-only">' in html
    assert '<div class="lane-num" aria-hidden="true">' in html
    assert 'role="list"' in html


def test_empty_states_are_headings():
    empty = _render_shared(
        "server/templates",
        "schedule.html",
        heats=[],
        has_meet=False,
        meet_name="",
        t=_SCHED_T,
    )
    assert '<div id="waiting" role="heading" aria-level="2">' in empty
    html = _sched("server/templates")
    assert '<div id="no-matches" class="hidden" role="heading" aria-level="2">' in html
