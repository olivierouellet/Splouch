"""Wire-level checks for the decoders that have no recording to replay.

The CTS Gen6 is covered by real captures in `test_console_recordings.py`. The others
have never been near a deck here, so these pin them to their upstream references
instead: the Gen7 to `fabriziobertocci/coloradoScoreboard`, the Quantum to the OSM6
layout in `hakostra/swimming-scoreboard`. Each test is one thing the decoder used to
get wrong.
"""

from console_decoders.cts_gen7 import _MAPPINGS, CTSGen7Decoder, _rot_l, _rot_r
from console_decoders.quantum import QuantumDecoder
from console_decoders.swiss_timing_ares21 import Ares21Decoder

# ── CTS Gen7 ──────────────────────────────────────────────────────────────────


def _gen7_encode(header: int, payload: list[int]) -> list[int]:
    """One Gen7 packet as it is on the wire: the decoder's remap, run backwards.

    The cipher is an XOR keyed on the header and the decoded length, so encoding is
    the same walk with plain and cipher text swapped.
    """
    plain = [len(payload), *payload]
    plain.append((header + sum(plain)) & 0x7F)
    mapper = _MAPPINGS[header & 31]
    odd = header % 2 == 1
    out = [header, plain[0] ^ (mapper & 0x7F)]
    for i, p in enumerate(plain[1:], start=1):
        rot = (plain[0] * i) & 0xFFFFFFFF
        key = (_rot_r(mapper, rot) if odd else _rot_l(mapper, rot)) & 0x7F
        out.append(p ^ key)
    return out


def _gen7_digits(pairs: list[tuple[int, str]]) -> list[int]:
    """(descriptor, char) pairs → the descriptor/value byte stream of one module."""
    return [b for desc, ch in pairs for b in (desc, ord(ch))]


def _gen7_feed(decoder: CTSGen7Decoder, stream: list[int]) -> dict:
    updates: dict = {}
    # A trailing header flushes the last real packet, as the next one would live.
    for b in [*stream, 0x80]:
        if decoder.is_packet_start(b, []):
            updates.update(decoder.feed([]))
    return updates


def test_gen7_digits_land_in_the_module_the_header_names():
    """The header byte is the module number; skipping it put every lane in module 0."""
    d = CTSGen7Decoder({"num_lanes": 8})
    lane3 = _gen7_digits(
        [
            (2, " "),
            (3, "1"),
            (4, " "),
            (5, "1"),
            (6 | 0x40, "0"),
            (7 | 0x40, "2"),
            (8, "3"),
            (9, "4"),
        ]
    )
    updates = _gen7_feed(d, _gen7_encode(0x83, lane3))
    assert updates["lane_time3"] == "1:02.34"
    assert updates["lane_place3"] == "1"
    assert "running_time" not in updates


def test_gen7_place_keeps_both_digits():
    d = CTSGen7Decoder({"num_lanes": 10})
    updates = _gen7_feed(d, _gen7_encode(0x8A, _gen7_digits([(2, "1"), (3, "0")])))
    assert updates["lane_place10"] == "10"


def test_gen7_event_number_reads_digits_1_3_4():
    """Upstream GetDigits(12, 1, 3) steps over digit 2."""
    d = CTSGen7Decoder({"num_lanes": 8})
    module12 = _gen7_digits(
        [(1, "1"), (2, "9"), (3, "2"), (4, "3"), (7, " "), (8, " "), (9, "4")]
    )
    updates = _gen7_feed(d, _gen7_encode(0x8C, module12))
    assert updates["event_changed"] == (123, 4)


def test_gen7_running_time_under_a_minute_has_no_leading_colon():
    d = CTSGen7Decoder({"num_lanes": 8})
    module0 = _gen7_digits([(4, " "), (5, " "), (6, "1"), (7, "2"), (8, "3")])
    assert _gen7_feed(d, _gen7_encode(0x80, module0))["running_time"] == "12.3"


def test_gen7_zero_bytes_reach_the_decoder(monkeypatch):
    """0x00 is a legal cipher byte; the worker used to drop it and break the remap."""
    import state
    import worker

    d = CTSGen7Decoder({"num_lanes": 8})
    seen: list[dict] = []
    monkeypatch.setattr(state, "_decoder", d, raising=False)
    monkeypatch.setattr(worker, "_handle_packet", lambda buf: seen.append(d.feed(buf)))

    # Find a lane packet whose ciphertext carries a 0x00.
    candidates = (
        (lane, _gen7_encode(0x80 | lane, _gen7_digits([(dig, ch)])))
        for lane in range(1, 9)
        for dig in range(4, 10)
        for ch in "0123456789"
    )
    lane, stream = next((ln, s) for ln, s in candidates if 0 in s)

    buf: list[int] = []
    for b in [*stream, 0x80]:
        buf = worker._ingest_byte(b, buf)
    assert any(f"lane_time{lane}" in u for u in seen)


# ── Quantum ───────────────────────────────────────────────────────────────────

_SOH, _STX, _EOT, _HOME, _LF = 0x01, 0x02, 0x04, 0x08, 0x0A


def _osm6(a: str, b: str, lane: str, lap: str, rank: str, time: str) -> list[int]:
    """One pt1 + pt2 pair for event 3, heat 2."""
    pt1 = f"{a}{b} ?? 400302  {rank:>2}".encode()
    pt2 = bytes([_LF]) + f"{lane}{lap:>2}".encode() + bytes([_STX])
    pt2 += f"{time:>11} ".encode()
    head = [_SOH, _STX, _HOME]
    return [*head, *pt1, _EOT, *head, *pt2, _EOT]


def _quantum_feed(d: QuantumDecoder, stream: list[int]) -> dict:
    updates: dict = {}
    buf: list[int] = []
    for b in [*stream, _SOH]:
        if d.is_packet_start(b, buf) and buf:
            updates.update(d.feed(buf))
            buf = []
        buf.append(b)
    return updates


def test_quantum_button_only_finish_stops_the_lane():
    d = QuantumDecoder({"num_lanes": 8})
    _quantum_feed(d, _osm6("0", " ", "?", "0", "", ""))
    _quantum_feed(d, _osm6("2", "S", "1", "0", "0", "14:17:55.26"))
    updates = _quantum_feed(d, _osm6("2", "B", "4", "4", "2", "1:22.07"))
    assert updates["lane_time4"] == "1:22.07"
    assert updates["lane_running4"] is False


def test_quantum_finish_without_a_time_stops_the_lane():
    d = QuantumDecoder({"num_lanes": 8})
    _quantum_feed(d, _osm6("2", "S", "1", "0", "0", "14:17:55.26"))
    updates = _quantum_feed(d, _osm6("2", "A", "5", "4", "", ""))
    assert updates == {"lane_running5": False}


def test_quantum_frame_with_bytes_after_eot_still_decodes():
    d = QuantumDecoder({"num_lanes": 8})
    _quantum_feed(d, _osm6("2", "S", "1", "0", "0", "14:17:55.26"))
    stream = _osm6("2", "A", "3", "4", "1", "58.21")
    stream = [*stream, 0x0D, 0x0A]
    assert _quantum_feed(d, stream)["lane_time3"] == "58.21"


def test_quantum_same_heat_readied_again_clears_lanes_for_the_reswim():
    d = QuantumDecoder({"num_lanes": 8})
    _quantum_feed(d, _osm6("0", " ", "?", "0", "", ""))
    d.set_seed_times({1: "1:00.00"})
    _quantum_feed(d, _osm6("2", "S", "1", "0", "0", "14:17:55.26"))
    _quantum_feed(d, _osm6("2", "A", "3", "4", "1", "58.21"))
    updates = _quantum_feed(d, _osm6("0", " ", "?", "0", "", ""))
    assert updates["lane_time3"] == ""
    assert "event_changed" not in updates
    assert d.lane_seed_times == {1: "1:00.00"}
    restart = _quantum_feed(d, _osm6("2", "S", "1", "0", "0", "14:20:01.00"))
    assert restart["dismiss_overlay"] is True


# ── Ares 21 ───────────────────────────────────────────────────────────────────


def _ares(header: str, data: str) -> list[int]:
    return [0x01, *header.encode(), 0x02, *data.encode(), 0x04]


def test_ares_zero_clock_is_not_a_start():
    d = Ares21Decoder({"num_lanes": 8})
    assert "dismiss_overlay" not in d.feed(_ares("0040100000", "00:00.0"))
    assert d.feed(_ares("0040100000", "00:00.1"))["dismiss_overlay"] is True


def test_ares_single_digit_seconds_parse():
    d = Ares21Decoder({"num_lanes": 8})
    updates = d.feed(_ares("0040100220", "1 5.23"))
    assert (updates["lane_place1"], updates["lane_time1"]) == ("1", "5.23")
