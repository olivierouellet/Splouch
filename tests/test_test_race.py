"""Event 0, the console's test race.

Every Gen7 event sequence has one, and the console saves nothing from it (F1034
p.24). It is a heat of its own on the live boards — lanes reset, the header reads
`EV 0 · HT 1 · Test race` — but never a result: Results keep the last real heat and
the Schedule saves no console times.
"""

import meet_data
import state
import worker
from console_decoders import make_decoder
from console_decoders.cts_gen6 import CTSGen6Decoder
from console_decoders.cts_gen7 import CTSGen7Decoder
from test_console_decoders import _gen7_digits, _gen7_encode, _gen7_feed
from test_console_outputs import _cts_line, _cts_packet


def test_gen7_event_0_is_a_heat():
    d = CTSGen7Decoder({"num_lanes": 4})
    lane = _gen7_digits(
        [(3, "1"), (5, "1"), (6 | 0x40, "0"), (7 | 0x40, "2"), (8, "3")]
    )
    _gen7_feed(d, _gen7_encode(0x82, lane))
    ev0 = _gen7_digits([(4, "0"), (9, "1")])
    updates = _gen7_feed(d, _gen7_encode(0x8C, ev0))
    assert updates["event_changed"] == (0, 1)
    assert updates["current_event"] == "0" and updates["lane_time2"] == ""


def test_gen7_a_blank_event_is_no_event():
    d = CTSGen7Decoder({"num_lanes": 4})
    updates = _gen7_feed(d, _gen7_encode(0x8C, _gen7_digits([(9, "1")])))
    assert "event_changed" not in updates and "current_event" not in updates


def test_gen6_event_0_is_a_heat():
    d = CTSGen6Decoder({"num_lanes": 4})
    d.feed(_cts_line(2, 0x0F ^ 2, 1, " 10400"))
    updates = d.feed(_cts_packet(12, "  0   1 "))
    assert updates["event_changed"] == (0, 1)
    assert d.get_lane_time(2) == ""


def test_gen6_show_definitions_sends_no_event():
    """`0C … 0C` decodes as event 0 with no heat: no event at all."""
    d = CTSGen6Decoder({"num_lanes": 4})
    assert d.feed(_cts_packet(12, "  0     ")) == {}


def test_the_test_race_is_named_in_the_meets_language(monkeypatch):
    monkeypatch.setitem(state.settings, "locale", "fr")
    assert meet_data.get_event_name_display(0) == "Course test"
    monkeypatch.setitem(state.settings, "locale", "en")
    assert meet_data.get_event_name_display(0) == "Test race"
    parts = meet_data.get_event_name_parts(0)
    assert parts is not None
    assert parts["kind"] == "test_race" and parts["raw"] == "Test race"
    assert not any(
        parts[k] for k in ("dist", "stroke", "gender", "age", "age_key", "round")
    )


def test_a_finished_test_race_is_not_published(monkeypatch):
    emitted = []
    monkeypatch.setattr(worker.bus, "emit", lambda *a, **k: emitted.append(a))
    monkeypatch.setattr(worker.bus, "run_bg", lambda fn: fn())
    monkeypatch.setattr(worker.relay, "relay_emit", lambda *a, **k: emitted.append(a))
    monkeypatch.setitem(state.settings, "finish_debounce", 0)
    d = make_decoder("cts_sys6", {**state.settings, "num_lanes": 4})
    d.last_event_sent = (0, 1)
    monkeypatch.setattr(state, "_decoder", d)
    monkeypatch.setattr(state, "_results_prev_race_finished", False)
    kept = {"event": "3", "heat": "2", "lanes": []}
    monkeypatch.setattr(state, "_last_results_snapshot", kept)
    worker._on_race_state_changed(True, {})
    assert state._last_results_snapshot is kept
    assert not any("results_snapshot" in a or "race_finished" in a for a in emitted)
    # A real heat still publishes.
    d.last_event_sent = (3, 2)
    monkeypatch.setattr(state, "_results_prev_race_finished", False)
    worker._on_race_state_changed(True, {})
    assert any("results_snapshot" in a for a in emitted)


def test_the_web_names_the_test_race_in_the_readers_language():
    from jsc import HAS_JS_ENGINE, run_page
    from test_lap_counts import _render

    if not HAS_JS_ENGINE:
        import pytest

        pytest.skip("needs a JavaScript engine")
    page = _render("server/templates", "results.html", t={})
    script = """
function assert(ok, msg) { if (!ok) throw new Error(msg); }
var parts = {kind: 'test_race', raw: 'Test race', dist: '', stroke: ''};
var got = composeEventName(parts, {test_race: 'Course test'});
assert(got === 'Course test', 'the reader\\'s word: ' + got);
got = composeEventName(parts, {freestyle: 'Libre'});
assert(got === 'Test race', 'no key: the meet\\'s name: ' + got);
"""
    run_page(page.replace("</body>", f"<script>{script}</script></body>"))
