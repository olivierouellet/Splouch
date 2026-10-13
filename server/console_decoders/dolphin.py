"""CTS Dolphin wireless stopwatches — over the Dolphin software's TCP/IP streaming API.

The Dolphin base talks to the Dolphin PC software over USB; that software serves a
documented meet-management API on TCP (Dolphin user instructions F912, "TCP/IP
Streaming"). Splouch connects as a client, the way a meet manager would. Lines are
ASCII, comma-separated, CRLF-terminated:

    → autoSendUpdates,ON / includeSplitData,ON / getCurrentEventAndHeat
    ← CurrentEventAndHeat,<version>,<event index>,<event number>,<heat>,<race>
    ← EventChanged,<index>,<number>,<name>,<heats>,<splits>,<round>
    ← Event <index>  Heat <heat>  Race <race>
    ← <lane>,<time>,<dq True|False>,<empty True|False>[,1,A,B,C,final, … ,10,A,B,C,final]

A race's lines arrive once, after the race is over and reset — there is no start
signal and no running clock. Dolphin sends no places; they are ranked here from the
times. Right after saving a race Dolphin may move to the next heat on its own, which
would wipe the results off the board at once, so a heat change that follows a race
is held for `dolphin_results_hold` seconds (`tick`).

See dolphin_tcp.md.
"""

import re
import time

from .base import ConsoleDecoder, DecoderOption, SerialConfig
from .utils import parse_time_hundredths

LF = 0x0A
PORTS = ("13382", "13441", "14559", "14917", "15828")

_RACE = re.compile(r"^Event\s+(\d+)\s+Heat\s+(\d+)\s+Race\s+(\d+)")
_LANE = re.compile(r"^(\d+),([^,]*),(True|False),(True|False)(?:,(.*))?$", re.IGNORECASE)


def _clean_time(raw: str) -> str:
    """'01:02.34' → '1:02.34'; '58.21' stays; blank stays blank."""
    t = raw.strip()
    parts = t.split(":")
    while len(parts) > 1 and parts[0].strip("0") == "":
        parts.pop(0)
    if parts and parts[0]:
        parts[0] = parts[0].lstrip("0") or "0"
    return ":".join(parts)


def _splits(rest: str | None) -> int:
    """Number of splits with a final time in the `1,A,B,C,final,…` tail."""
    if not rest:
        return 0
    fields = rest.split(",")
    return sum(1 for i in range(0, len(fields) - 4, 5) if fields[i + 4].strip())


class DolphinDecoder(ConsoleDecoder):
    """Decoder for the CTS Dolphin software's TCP/IP streaming API."""

    transport = "tcp"

    OPTIONS = (
        DecoderOption(
            key="tcp_port",
            label="Dolphin TCP/IP port",
            choices=tuple((p, p) for p in PORTS),
            default="13382",
            help="The port shown in Dolphin's Settings → TCP/IP Streaming.",
        ),
        DecoderOption(
            key="dolphin_results_hold",
            label="Results hold before the next heat",
            choices=(
                ("0", "None"),
                ("5", "5 s"),
                ("10", "10 s"),
                ("20", "20 s"),
                ("30", "30 s"),
            ),
            default="10",
            help="Dolphin can move to the next heat as soon as a race is saved. "
            "Keep the race's times on the board this long first.",
        ),
        DecoderOption(
            key="dolphin_lane_base",
            label="Dolphin lane numbering",
            choices=(("1", "Lanes start at 1"), ("0", "Lanes start at 0")),
            default="1",
            help="Must match Dolphin's General setting. Lanes starting at 0 are "
            "shown one higher (lane 0 is lane 1).",
        ),
    )

    def __init__(self, cfg: dict) -> None:
        self.lane_times: dict[int, str] = {}
        self.lane_places: dict[int, str] = {}
        self.lane_splits: dict[int, int] = {}
        self.last_event_sent: tuple[int, int] = (0, 0)
        self.lane_seed_times: dict[int, str] = {}
        self._finished = False
        self._hold_until = 0.0
        self._pending: tuple[int, int] | None = None
        self._race_hundredths: dict[int, int] = {}
        self.configure(cfg)

    # ── ConsoleDecoder interface ──────────────────────────────────────────────

    @property
    def serial_config(self) -> SerialConfig:
        return SerialConfig()  # not used: transport is TCP

    @property
    def tcp_endpoint(self) -> tuple[str, int]:
        return self._host, self._port

    @property
    def post_open_bytes(self) -> bytes:
        return (
            b"autoSendUpdates,ON\r\nincludeSplitData,ON\r\ngetCurrentEventAndHeat\r\n"
        )

    def is_packet_start(self, byte: int, buffer: list[int]) -> bool:
        return bool(buffer) and buffer[-1] == LF

    @property
    def max_packet_bytes(self) -> int:
        return 1024

    def configure(self, cfg: dict) -> None:
        self.num_lanes = int(cfg.get("num_lanes", 10))
        self._host = str(cfg.get("console_host", "") or "").strip()
        port = str(cfg.get("tcp_port", "13382"))
        self._port = int(port) if port.isdigit() else 13382
        self._hold = float(cfg.get("dolphin_results_hold", "10") or 0)
        self._lane_shift = 1 if str(cfg.get("dolphin_lane_base", "1")) == "0" else 0

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
            self.lane_splits[i] = 0
            updates[f"lane_time{i}"] = ""
            updates[f"lane_place{i}"] = " "
            updates[f"lane_running{i}"] = False
            updates[f"lane_delta{i}"] = ""
            updates[f"lane_splits{i}"] = 0
        self._race_hundredths.clear()
        self._finished = False
        return updates

    def race_finished(self) -> bool:
        return self._finished

    def tick(self) -> dict:
        if self._pending is not None and time.monotonic() >= self._hold_until:
            pair, self._pending = self._pending, None
            return self._change_heat(pair)
        return {}

    # ── Decoding ──────────────────────────────────────────────────────────────

    def feed(self, packet: list[int]) -> dict:
        text = bytes(packet).decode("ascii", errors="replace").strip()
        if text.startswith("CurrentEventAndHeat,"):
            return self._current(text.split(","))
        m = _RACE.match(text)
        if m:
            # A race block begins: its lane lines follow.
            self._race_hundredths.clear()
            return {}
        m = _LANE.match(text)
        if m:
            return self._lane(m)
        return {}

    def _current(self, parts: list[str]) -> dict:
        if len(parts) < 5:
            return {}
        ev_m = re.match(r"\d+", parts[3].strip())
        heat = parts[4].strip()
        if not ev_m or not heat.isdigit():
            return {}
        pair = (int(ev_m.group()), int(heat))
        if pair[0] <= 0 or pair[1] <= 0 or pair == self.last_event_sent:
            self._pending = None
            return {}
        if self._finished and time.monotonic() < self._hold_until:
            self._pending = pair  # results stay up until the hold runs out
            return {}
        return self._change_heat(pair)

    def _change_heat(self, pair: tuple[int, int]) -> dict:
        self.last_event_sent = pair
        self.lane_seed_times.clear()
        updates = self.reset_lanes()
        updates["current_event"] = str(pair[0])
        updates["current_heat"] = str(pair[1])
        updates["event_changed"] = pair
        return updates

    def _lane(self, m: re.Match) -> dict:
        lane = int(m.group(1)) + self._lane_shift
        if not 1 <= lane <= self.num_lanes:
            return {}
        raw, dq, empty = m.group(2), m.group(3).lower(), m.group(4).lower()
        updates: dict = {}
        t = "" if empty == "true" else _clean_time(raw)
        if t and dq == "true":
            t = "DQ"
        if t != self.lane_times.get(lane, ""):
            self.lane_times[lane] = t
            updates[f"lane_time{lane}"] = t
        hund = parse_time_hundredths(t) if t and t != "DQ" else None
        if hund:
            self._race_hundredths[lane] = hund
        else:
            self._race_hundredths.pop(lane, None)
        splits = _splits(m.group(5))
        if splits != self.lane_splits.get(lane, 0):
            self.lane_splits[lane] = splits
            updates[f"lane_splits{lane}"] = splits
        updates[f"lane_running{lane}"] = False
        updates.update(self._rank())
        self._finished = True
        self._hold_until = time.monotonic() + self._hold
        return updates

    def _rank(self) -> dict:
        """Places from the race's times: ties share a place."""
        updates: dict = {}
        ordered = sorted(self._race_hundredths.items(), key=lambda kv: kv[1])
        places: dict[int, str] = {}
        for i, (lane, hund) in enumerate(ordered):
            prev = ordered[i - 1] if i else None
            places[lane] = places[prev[0]] if prev and prev[1] == hund else str(i + 1)
        for lane in range(1, self.num_lanes + 1):
            place = places.get(lane, " ")
            if place != self.lane_places.get(lane, " "):
                self.lane_places[lane] = place
                updates[f"lane_place{lane}"] = place
        return updates
