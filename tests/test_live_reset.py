"""`/live` after a test session: the `reset` must leave an empty board, not a ghost.

A replay ends with `worker.end_test_session`: the operator's own heat is put back
(`send_event_info`), the board takes a `reset`, then the same heat once more. When
the console has no heat of its own that frame names none — and the page used to
read "nobody running" as a heat over, open the result columns again and show the
replay's times and places under an empty start list. Driven through the real page
scripts under `tests/jsc.py`, with the socket replaced by a recorder.
"""

import pytest

from jsc import HAS_JS_ENGINE, run_page
from test_lap_counts import _render

_SOCKET = r"""<script>
var __h = {};
splouchSocket = function () {
    return { on: function (ev, fn) { __h[ev] = fn; }, emit: function () {} };
};
</script>
"""

_DRIVE = r"""<script>
function assert(ok, msg) { if (!ok) throw new Error(msg); }
function text(id) { return document.getElementById(id).textContent; }
var frame = __h['update_scoreboard'];
frame({current_event: '58', current_heat: '2', lane_name1: 'Test Nageur21',
       lane_club1: 'CLBD'});
frame({lane_running1: true, running_time: '0.10'});
frame({lane_time1: '1:08.05', lane_place1: '1', lane_running1: false});
assert(text('lane_time1') === '1:08.05', 'the heat never finished');

var none = {current_event: '', current_heat: '', current_round: '', event_name: '',
            event_name_parts: null, lane_name1: '', lane_club1: '',
            lane_name_alt1: '', lane_delta1: ''};
frame(none);
__h['test_mode']({active: false});
__h['reset']({});
frame(none);
assert(text('lane_time1') === '', 'a time outlived the reset: ' + text('lane_time1'));
assert(text('lane_place1') === '', 'a place outlived the reset: ' + text('lane_place1'));
assert(current_state !== 3, 'an empty board was taken for a finished heat');
assert(_tc.style.width === '0', 'the result columns opened over nothing');
</script></body>"""


@pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JavaScript engine (osascript or node)"
)
def test_a_reset_with_no_heat_to_put_back_leaves_the_board_empty():
    html = _render(
        "server/templates",
        "live.html",
        show_laps=False,
        nosplash=True,
        test_background=False,
        carousel_images=[],
        carousel_interval=10,
    )
    ws = '<script src="/static/js/ws.js"></script>'
    assert ws in html
    run_page(html.replace(ws, ws + _SOCKET, 1).replace("</body>", _DRIVE))


_PODIUM = r"""<script>
function assert(ok, msg) { if (!ok) throw new Error(msg); }
var reveals = 0;
highlight_podium = function () { reveals++; };
var frame = __h['update_scoreboard'];
frame({current_event: '58', current_heat: '1', lane_name1: 'A', lane_name2: 'B'});
frame({lane_running1: true, lane_running2: true, running_time: '0.10'});
frame({lane_time1: '1:30.57', lane_place1: '1', lane_running1: false});
frame({lane_time2: '1:38.48', lane_place2: '2', lane_running2: false,
       results_pending: true});
assert(current_state === 3, 'the heat should read as over');
assert(reveals === 0, 'the podium came before the validation');
frame({lane_time2: '1:23.24', lane_place2: '1', lane_place1: '2'});
assert(reveals === 0, 'a correction revealed the podium');
frame({results_pending: false});
__h['race_finished']({});
assert(reveals === 1, 'the validation never revealed the podium: ' + reveals);
</script></body>"""


@pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JavaScript engine (osascript or node)"
)
def test_the_podium_waits_for_a_validating_console():
    html = _render(
        "server/templates",
        "live.html",
        show_laps=False,
        nosplash=True,
        test_background=False,
        carousel_images=[],
        carousel_interval=10,
    )
    ws = '<script src="/static/js/ws.js"></script>'
    run_page(html.replace(ws, ws + _SOCKET, 1).replace("</body>", _PODIUM))


_SPLIT_PLACES = r"""<script>
function assert(ok, msg) { if (!ok) throw new Error(msg); }
var frame = __h['update_scoreboard'];
frame({current_event: '58', current_heat: '1', lane_name1: 'A', lane_name2: 'B'});
frame({lane_running1: true, lane_running2: true, running_time: '0.10'});
frame({lane_time1: '28.41', lane_place1: '1', lane_running1: false,
       lane_at_split1: true});
frame({lane_time2: '28.90', lane_place2: '2', lane_running2: false,
       lane_at_split2: true, running_time: '29.00'});
assert(current_state !== 3, 'a turn with every lane placed ended the heat');
frame({lane_time1: '1:01.23', lane_place1: '1', lane_at_split1: false});
frame({lane_time2: '1:02.05', lane_place2: '2', lane_at_split2: false});
assert(current_state === 3, 'the finish should end the heat');
</script></body>"""


@pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JavaScript engine (osascript or node)"
)
def test_a_place_at_a_split_does_not_end_the_heat():
    html = _render(
        "server/templates",
        "live.html",
        show_laps=False,
        nosplash=True,
        test_background=False,
        carousel_images=[],
        carousel_interval=10,
    )
    ws = '<script src="/static/js/ws.js"></script>'
    run_page(html.replace(ws, ws + _SOCKET, 1).replace("</body>", _SPLIT_PLACES))
