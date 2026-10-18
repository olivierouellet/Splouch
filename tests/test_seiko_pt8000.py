"""Seiko PT-8000 serial output: fixed 16-byte STX … ETX records."""

import pytest

from console_decoders import make_decoder, option_values
from console_decoders.seiko_pt8000 import SeikoPT8000Decoder

RESET = b"\x02AR       0.00 \x03"
START = b"\x02AR       0.0  \x03"
RUNNING = b"\x02AR    1:46.3  \x03"


def _touch(lane: str, order: str, time: str, kind: str) -> bytes:
    return b"\x02A" + f"{order}{lane}{order} {time:>8}{kind}".encode() + b"\x03"


def _feed(d: SeikoPT8000Decoder, *records: bytes) -> dict:
    updates: dict = {}
    for r in records:
        updates.update(d.feed(list(r)))
    return updates


def _decoder(**cfg) -> SeikoPT8000Decoder:
    return SeikoPT8000Decoder({"num_lanes": 8, **cfg})


def test_the_records_are_sixteen_bytes():
    for r in (RESET, START, RUNNING, _touch("5", "4", "1:29.30", "L")):
        assert len(r) == 16


def test_the_first_clock_after_a_reset_starts_the_race():
    d = _decoder()
    assert "dismiss_overlay" not in _feed(d, RESET)
    updates = _feed(d, START)
    assert updates["dismiss_overlay"] is True
    assert updates["running_time"] == "0.0"
    assert all(updates[f"lane_running{n}"] for n in range(1, 9))
    assert "dismiss_overlay" not in _feed(d, RUNNING)
    assert d.running_time == "1:46.3"


def test_a_start_runs_only_the_start_list_lanes():
    d = _decoder()
    d.set_heat_lanes({2, 5})
    updates = _feed(d, RESET, START)
    assert {n for n in range(1, 9) if updates.get(f"lane_running{n}")} == {2, 5}


def test_a_lap_is_a_split_and_a_goal_the_finish():
    d = _decoder(touchpad_sides=2)
    d.set_heat_lanes({5})
    _feed(d, RESET, START)
    lap = _feed(d, _touch("5", "1", "29.30", "L"))
    assert (lap["lane_time5"], lap["lane_place5"], lap["lane_splits5"]) == (
        "29.30",
        "1",
        1,
    )
    assert "lane_running5" not in lap
    assert not d.race_finished()
    goal = _feed(d, _touch("5", "1", "1:01.05", "G"))
    assert (goal["lane_time5"], goal["lane_splits5"]) == ("1:01.05", 2)
    assert goal["lane_running5"] is False
    assert d.race_finished()


def test_touches_count_two_lengths_with_pads_at_one_end():
    d = _decoder()
    _feed(d, RESET, START)
    assert _feed(d, _touch("3", "2", "1:02.00", "L"))["lane_splits3"] == 2


def test_reset_keeps_the_results_until_the_next_start():
    d = _decoder()
    _feed(d, RESET, START, _touch("4", "1", "58.21", "G"))
    reset = _feed(d, RESET)
    assert "lane_time4" not in reset
    assert d.get_lane_time(4) == "58.21"
    assert _feed(d, START)["lane_time4"] == ""


def test_a_new_start_keeps_the_seeds():
    d = _decoder()
    d.set_seed_times({4: "1:00.00"})
    _feed(d, RESET, START)
    assert d.lane_seed_times == {4: "1:00.00"}


def test_reaction_and_takeover_records_are_ignored():
    d = _decoder()
    _feed(d, RESET, START)
    assert _feed(d, b"\x02A 4  S0S0.69 J\x03") == {}
    assert _feed(d, b"\x02A0502 0+0.63 K\x03") == {}


@pytest.mark.parametrize(
    ("option", "first", "row"),
    [("10", 1, 10), ("ignore", 1, None), ("10", 0, 1)],
)
def test_lane_zero(option, first, row):
    d = SeikoPT8000Decoder({"num_lanes": 10, "lane_zero": option, "first_lane": first})
    _feed(d, RESET, START)
    updates = _feed(d, _touch("0", "3", "58.21", "G"))
    if row:
        assert updates[f"lane_time{row}"] == "58.21"
    else:
        assert not any(k.startswith("lane_time") for k in updates)


def test_order_zero_is_tenth():
    d = SeikoPT8000Decoder({"num_lanes": 10})
    _feed(d, RESET, START)
    assert _feed(d, _touch("7", "0", "1:05.00", "G"))["lane_place7"] == "10"


def test_a_short_or_unframed_record_is_dropped():
    d = _decoder()
    assert d.feed(list(b"\x02AR  0.0\x03")) == {}
    assert d.feed(list(b"\x02BR       0.0  \x03")) == {}


def test_a_record_with_bytes_after_its_etx_still_decodes():
    d = _decoder()
    _feed(d, RESET)
    assert d.feed([*START, 0x0D, 0x0A])["dismiss_overlay"] is True


def test_the_console_entry_defaults_to_9600_7e1():
    sc = make_decoder("seiko_pt8000", {}).serial_config
    assert (sc.baud, sc.bytesize, sc.parity, sc.stopbits) == (9600, 7, "E", 1)
    assert option_values("seiko_pt8000", {}) == {
        "serial_baud": "9600",
        "serial_format": "7E1",
        "lane_zero": "10",
    }


def test_the_serial_options_reach_the_port():
    cfg = {
        "console_options": {
            "seiko_pt8000": {"serial_baud": "19200", "serial_format": "8N1"}
        }
    }
    sc = make_decoder("seiko_pt8000", cfg).serial_config
    assert (sc.baud, sc.bytesize, sc.parity) == (19200, 8, "N")
