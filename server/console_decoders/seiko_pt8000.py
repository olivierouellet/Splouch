"""Seiko PT-8000 serial output — one fixed 16-byte record per event.

    STX 'A' c3 lane order ' ' <time: 8> kind ETX

    c3      'R' on a running-clock record; otherwise unused
    lane    one character (byte 4)
    order   one character (byte 5): the finish order of a lap or a goal
    time    bytes 7-14, right-aligned: `m:ss.t` on the clock, `m:ss.hh` on a touch
    kind    byte 15: 'L' lap, 'G' goal (finish), 'J' reaction, 'K' take-over,
            blank on a running-clock record

The clock says when the race starts and resets: a clock record showing hundredths
(`0.00`) is the reset, and the first one after it — tenths, `0.0` — the start. No
event or heat travels on this output, so names and seed times only load when the
heat is set some other way.

A lap is a touch on the way; a goal is the finish, with its place. Reaction and
take-over times are not shown on the board and are ignored.

Unconfirmed until a capture: how a lane or a place above 9 is written in its one
character, and whether a lap's order is a running rank. See seiko_pt8000_serial.md.
"""

import re

from .base import ConsoleDecoder, DecoderOption, SerialConfig, serial_from
from .utils import first_lane_of, lane_digit_row, split_step

_STX = 0x02
_ETX = 0x03
_RECORD = 16

# A clock that reads zero to the hundredth: the console was reset.
_RESET = re.compile(r"^0?:?0*0\.00$")
_TIME = re.compile(r"^(?:(\d+):)?(\d{1,2})\.(\d{1,3})$")


def _clean_time(raw: str) -> str:
    """' 1:29.30' / '   29.30' / '0:05.1' → '1:29.30' / '29.30' / '5.1'."""
    t = raw.strip()
    m = _TIME.match(t)
    if not m:
        return t
    mins, sec, frac = m.groups()
    if mins and int(mins):
        return f"{int(mins)}:{int(sec):02d}.{frac}"
    return f"{int(sec)}.{frac}"


def _place(c: str) -> str:
    """A one-character order: '1'-'9', '0' as 10th, anything else no place."""
    c = c.strip()
    if c == "0":
        return "10"
    return c if c.isdigit() else " "


class SeikoPT8000Decoder(ConsoleDecoder):
    """Decoder for the Seiko PT-8000 serial output (RS-422)."""

    OPTIONS = (
        DecoderOption(
            key="serial_baud",
            label="Baud rate",
            choices=(("9600", "9600"), ("19200", "19200")),
            default="9600",
            help="Not documented for this port. 9600 is the usual setting.",
        ),
        DecoderOption(
            key="serial_format",
            label="Serial format",
            choices=(("7E1", "7-E-1"), ("8N1", "8-N-1")),
            default="7E1",
            help="If nothing decodes, try the other format.",
        ),
        DecoderOption(
            key="lane_zero",
            label="Lane digit 0",
            choices=(("10", "Lane 10"), ("ignore", "Ignore")),
            default="10",
            help="The lane is one character; 0 is usually lane 10. Unconfirmed "
            "for this console.",
        ),
    )

    def __init__(self, cfg: dict) -> None:
        self._serial_config = SerialConfig(
            baud=9600, bytesize=7, parity="E", stopbits=1
        )
        self._lane_zero = "10"
        self.lane_times: dict[int, str] = {}
        self.lane_places: dict[int, str] = {}
        self.lane_running: dict[int, bool] = {}
        self.lane_splits: dict[int, int] = {}
        self.running_time = ""
        self._race_active = False
        self.last_event_sent: tuple[int, int] = (0, 0)
        self.lane_seed_times: dict[int, str] = {}
        self.configure(cfg)

    # ── ConsoleDecoder interface ──────────────────────────────────────────────

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_config

    def is_packet_start(self, byte: int, buffer: list[int]) -> bool:
        return byte == _STX

    @property
    def max_packet_bytes(self) -> int:
        return 32

    def configure(self, cfg: dict) -> None:
        self.num_lanes = int(cfg.get("num_lanes", 10))
        self.first_lane = first_lane_of(cfg)
        self._lane_zero = str(cfg.get("lane_zero", "10"))
        self._pad_sides = int(cfg.get("touchpad_sides", 1) or 1)
        self._serial_config = serial_from(
            cfg.get("serial_baud", 9600), str(cfg.get("serial_format", "7E1")), "7E1"
        )

    def set_seed_times(self, times: dict) -> None:
        self.lane_seed_times = dict(times)

    def get_lane_time(self, lane_idx: int) -> str:
        return self.lane_times.get(lane_idx, "")

    def get_lane_place(self, lane_idx: int) -> str:
        return self.lane_places.get(lane_idx, " ")

    def adjust_splits(self, lane: int, delta: int) -> int:
        val = max(0, self.lane_splits.get(lane, 0) + delta)
        self.lane_splits[lane] = val
        return val

    def reset_lanes(self) -> dict:
        updates: dict = {}
        for i in range(1, self.num_lanes + 1):
            self.lane_times[i] = ""
            self.lane_places[i] = " "
            self.lane_running[i] = False
            self.lane_splits[i] = 0
            updates[f"lane_time{i}"] = ""
            updates[f"lane_place{i}"] = " "
            updates[f"lane_running{i}"] = False
            updates[f"lane_delta{i}"] = ""
            updates[f"lane_splits{i}"] = 0
        self._race_active = False
        return updates

    def race_finished(self) -> bool:
        any_placed = False
        for i in range(1, self.num_lanes + 1):
            if self.lane_running.get(i):
                return False
            if self.lane_times.get(i):
                if self.lane_places.get(i, " ") == " ":
                    return False
                any_placed = True
        return any_placed

    # ── Decoding ──────────────────────────────────────────────────────────────

    def feed(self, packet: list[int]) -> dict:
        # A record ends at its ETX, not at the next STX the worker splits on.
        if _ETX in packet:
            packet = packet[: packet.index(_ETX) + 1]
        if len(packet) != _RECORD or packet[0] != _STX or packet[-1] != _ETX:
            return {}
        text = bytes(packet[1:-1]).decode("latin-1")  # bytes 2-15
        if text[0] != "A":
            return {}
        kind = text[13]
        time = text[5:13]
        if text[1] == "R" and kind == " ":
            return self._clock(time)
        if kind in "LG":
            return self._touch(text[2], text[3], _clean_time(time), kind == "G")
        return {}  # 'J' reaction, 'K' take-over

    def _clock(self, raw: str) -> dict:
        updates: dict = {}
        t = raw.strip()
        if _RESET.match(t):
            # Reset: the next clock record is a start. The results stay up until it.
            self._race_active = False
            if self.running_time:
                self.running_time = ""
                updates["running_time"] = ""
            return updates
        t = _clean_time(t)
        if t != self.running_time:
            self.running_time = t
            updates["running_time"] = t
        if not self._race_active:
            # The first clock after a reset: a new race. Clear the last one's times,
            # but keep the seeds — no event_changed comes on this wire to reload them.
            seeds = dict(self.lane_seed_times)
            updates.update(self.reset_lanes())
            self.lane_seed_times = seeds
            self._race_active = True
            for i in self.lanes_to_start():
                self.lane_running[i] = True
                updates[f"lane_running{i}"] = True
            updates["dismiss_overlay"] = True
        return updates

    def _touch(self, lane_c: str, order_c: str, t: str, goal: bool) -> dict:
        updates: dict = {}
        lane = lane_digit_row(lane_c, self.first_lane, self._lane_zero == "10")
        if not 1 <= lane <= self.num_lanes or not _TIME.match(t):
            return updates
        self.lane_times[lane] = t
        updates[f"lane_time{lane}"] = t
        place = _place(order_c)
        if place != " ":
            self.lane_places[lane] = place
            updates[f"lane_place{lane}"] = place
        self.lane_splits[lane] = self.lane_splits.get(lane, 0) + split_step(
            self._pad_sides
        )
        updates[f"lane_splits{lane}"] = self.lane_splits[lane]
        running = not goal
        if self.lane_running.get(lane, False) != running:
            self.lane_running[lane] = running
            updates[f"lane_running{lane}"] = running
        return updates
