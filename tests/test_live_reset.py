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
