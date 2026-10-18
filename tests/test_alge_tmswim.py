"""ALGE TM-SWIM video (TV-Online) output: CR-terminated ASCII lines."""

import pytest

from console_decoders import make_decoder, option_values
from console_decoders.alge_tmswim import TMSwimDecoder


def _line(text: str) -> list[int]:
    return [*f"{text}\r".encode()]


def _feed(d: TMSwimDecoder, *lines: str) -> dict:
    updates: dict = {}
    for text in lines:
        updates.update(d.feed(_line(text)))
    return updates


def _clock(ev: int, ht: int, t: str) -> str:
    return f"R{ev:03d}{ht:03d} {t}"


def _touch(kind: str, lane: int, touches: int, rank: str, t: str) -> str:
    return f"{kind}{lane:02d}{touches:03d}{rank:>2}{t}"


def _decoder(**cfg) -> TMSwimDecoder:
    return TMSwimDecoder({"num_lanes": 8, **cfg})


def test_lines_split_on_carriage_return():
    d = _decoder()
    assert d.is_packet_start(ord("R"), [*b"R003001 00:00:00.0000\r"])
    assert not d.is_packet_start(ord("0"), [*b"R003"])


def test_the_clock_carries_event_and_heat():
    d = _decoder()
    updates = _feed(d, _clock(3, 2, "00:00:00.0000"))
    assert updates["event_changed"] == (3, 2)
    assert (updates["current_event"], updates["current_heat"]) == ("3", "2")
    assert "event_changed" not in _feed(d, _clock(3, 2, "00:00:00.0000"))


def test_event_zero_is_no_event():
    d = _decoder()
    assert "event_changed" not in _feed(d, _clock(0, 0, "00:00:00.0000"))


def test_start_line_starts_the_race():
    d = _decoder()
    d.set_heat_lanes({2, 5})
    _feed(d, _clock(3, 1, "00:00:00.0000"))
    updates = _feed(d, "START INT ")
    assert updates["dismiss_overlay"] is True
    assert {n for n in range(1, 9) if updates.get(f"lane_running{n}")} == {2, 5}
    assert _feed(d, _clock(3, 1, "00:00:01.2345"))["running_time"] == "1.2"
    assert "dismiss_overlay" not in _feed(d, _clock(3, 1, "00:00:01.3000"))


def test_a_moving_clock_starts_the_race_without_a_start_line():
    d = _decoder()
    _feed(d, _clock(3, 1, "00:00:00.0000"))
    updates = _feed(d, _clock(3, 1, "00:00:00.1000"))
    assert updates["dismiss_overlay"] is True
    assert all(updates[f"lane_running{n}"] for n in range(1, 9))


def test_running_time_formats():
    d = _decoder()
    _feed(d, _clock(1, 1, "00:00:00.0000"), "START")
    assert _feed(d, _clock(1, 1, "00:01:02.3456"))["running_time"] == "1:02.3"


def test_intermediate_then_finish_with_rank():
    d = _decoder(touchpad_sides=2)
    d.set_heat_lanes({4})
    _feed(d, _clock(5, 1, "00:00:00.0000"), "START")
    split = _feed(d, _touch("I", 4, 1, " 1", "00:00:28.1099"))
    assert (split["lane_time4"], split["lane_place4"], split["lane_splits4"]) == (
        "28.10",
        "1",
        1,
    )
    assert not d.race_finished()
    fin = _feed(d, _touch("F", 4, 2, " 1", "00:00:58.2187"))
    assert (fin["lane_time4"], fin["lane_place4"], fin["lane_splits4"]) == (
        "58.21",
        "1",
        2,
    )
    assert fin["lane_running4"] is False
    assert d.race_finished()


def test_touches_count_two_lengths_with_pads_at_one_end():
    d = _decoder()
    _feed(d, _clock(5, 1, "00:00:00.0000"), "START")
    assert _feed(d, _touch("I", 3, 1, "", "00:01:02.0000"))["lane_splits3"] == 2


def test_a_finish_without_rank_is_ranked_by_time():
    d = _decoder()
    d.set_heat_lanes({1, 2, 3})
    _feed(d, _clock(5, 1, "00:00:00.0000"), "START")
    _feed(d, _touch("F", 2, 2, "", "00:00:59.0000"))
    updates = _feed(d, _touch("F", 1, 2, "", "00:00:58.5000"))
    assert (updates["lane_place1"], updates["lane_place2"]) == ("1", "2")
    tie = _feed(d, _touch("F", 3, 2, "", "00:00:59.0099"))
    assert tie["lane_place3"] == "2"


def test_a_deleted_time_puts_the_lane_back_in_the_water():
    d = _decoder()
    d.set_heat_lanes({6})
    _feed(d, _clock(5, 1, "00:00:00.0000"), "START")
    _feed(d, _touch("F", 6, 2, " 1", "00:00:31.0000"))
    updates = _feed(d, _touch("C", 6, 2, " 1", "00:00:31.0000"))
    assert (updates["lane_time6"], updates["lane_place6"]) == ("", " ")
    assert updates["lane_running6"] is True
    assert not d.race_finished()


@pytest.mark.parametrize(("first", "row"), [(1, 10), (0, 1)])
def test_lane_numbers_follow_the_pools_first_lane(first, row):
    d = TMSwimDecoder({"num_lanes": 10, "first_lane": first})
    lane = 10 if first == 1 else 0
    _feed(d, _clock(5, 1, "00:00:00.0000"), "START")
    assert (
        _feed(d, _touch("F", lane, 2, " 1", "00:00:58.2100"))[f"lane_time{row}"]
        == "58.21"
    )


def test_a_lane_outside_the_pool_is_dropped():
    d = _decoder()
    assert _feed(d, _touch("F", 12, 2, " 1", "00:00:58.2100")) == {}


def test_a_second_start_of_the_same_heat_clears_it_but_keeps_seeds():
    d = _decoder()
    _feed(d, _clock(5, 1, "00:00:00.0000"))
    d.set_seed_times({4: "1:00.00"})
    _feed(d, "START", _touch("F", 4, 2, " 1", "00:00:58.2100"))
    _feed(d, _clock(5, 1, "00:00:00.0000"))
    assert d.get_lane_time(4) == "58.21"  # results stay up through the reset
    assert _feed(d, "START")["lane_time4"] == ""
    assert d.lane_seed_times == {4: "1:00.00"}


def test_event_identification_line():
    d = _decoder()
    assert _feed(d, "ID12/3/100m Freestyle/Men")["event_changed"] == (12, 3)


def test_reaction_and_swimmer_lines_are_ignored():
    d = _decoder()
    _feed(d, _clock(5, 1, "00:00:00.0000"), "START")
    assert _feed(d, "M04000  00:00:00.6500") == {}
    assert _feed(d, "4:DOE John/CAN/Club") == {}


def test_the_console_entry_defaults_to_19200_8n1():
    sc = make_decoder("alge_tmswim_video", {}).serial_config
    assert (sc.baud, sc.bytesize, sc.parity, sc.stopbits) == (19200, 8, "N", 1)
    assert option_values("alge_tmswim_video", {}) == {"serial_baud": "19200"}
    cfg = {"console_options": {"alge_tmswim_video": {"serial_baud": "9600"}}}
    assert make_decoder("alge_tmswim_video", cfg).serial_config.baud == 9600
