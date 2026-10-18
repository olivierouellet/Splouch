"""ALGE TM-SWIM video (TV-Online) output — one ASCII line per event, CR-terminated.

    R eee hhh HH:MM:SS.zhtc      running time, every 1/10 s, with event and heat
    START…                       start received (firmware 14xx and later)
    I LL TTT RR HH:MM:SS.zhtc    intermediate time: lane, touch count, rank
    F LL TTT RR HH:MM:SS.zhtc    finish time
    C LL TTT RR HH:MM:SS.zhtc    a lane's time deleted
    M LL TTT    HH:MM:SS.zhtc    reaction / relay take-off time       (SwimTime)
    ID<ev>/<heat>/<discipline>/<class>   event identification         (SwimTime)
    <lane>:<name>/<nation>/<club>        swimmer identification       (SwimTime)

Event and heat ride on every clock line, so the meet file's names and seed times load
on their own. The start is a line of its own; a clock leaving zero is taken as one
too, for firmware older than 14xx. A finish is `F`, an intermediate time `I`, and the
touch count gives the lengths. The rank (RR) is not sent by firmware later than 14xx:
a finish with none is ranked here from the finish times. Reaction times and the
swimmer lines are ignored — Lenex supplies the names.

See alge_tmswim_serial.md.
"""

import re

from .base import ConsoleDecoder, DecoderOption, SerialConfig
from .utils import first_lane_of, parse_time_hundredths, split_step

CR = 0x0D
LF = 0x0A

_TIME = re.compile(r"^(?:(\d{1,2}):)?(?:(\d{1,2}):)?(\d{1,2})\.(\d{1,4})$")
_CLOCK = re.compile(r"^R(\d{3})(\d{3})\s+(\S+)$")
_LANE = re.compile(r"^([IFC])(\d{2})(\d{3})([ \d]{2})\s*(\S+)$")
_ID = re.compile(r"^ID\s*(\d+)\s*/\s*(\d+)\s*/")


def _clean_time(raw: str, digits: int) -> str:
    """'00:01:02.3456' → '1:02.34' (digits=2) or '1:02.3' (digits=1); '' if not one.

    Truncated, not rounded: a swim time is cut to the precision shown.
    """
    m = _TIME.match(raw.strip())
    if not m:
        return ""
    a, b, sec, frac = m.groups()
    hours, mins = (int(a), int(b)) if b is not None else (0, int(a or 0))
    frac = frac[:digits].ljust(digits, "0")
    if hours:
        return f"{hours}:{mins:02d}:{int(sec):02d}.{frac}"
    if mins:
        return f"{mins}:{int(sec):02d}.{frac}"
    return f"{int(sec)}.{frac}"


def _is_zero(t: str) -> bool:
    return not any(c in "123456789" for c in t)


class TMSwimDecoder(ConsoleDecoder):
    """Decoder for the ALGE TM-SWIM video (TV-Online) RS-232 output."""

    OPTIONS = (
        DecoderOption(
            key="serial_baud",
            label="Baud rate",
            choices=(
                ("19200", "19200"),
                ("9600", "9600"),
                ("4800", "4800"),
                ("38400", "38400"),
            ),
            default="19200",
            help="Must match the console's port setting. If nothing decodes, try "
            "another.",
        ),
    )

    def __init__(self, cfg: dict) -> None:
        self._serial_config = SerialConfig(
            baud=19200, bytesize=8, parity="N", stopbits=1
        )
        self.lane_times: dict[int, str] = {}
        self.lane_places: dict[int, str] = {}
        self.lane_running: dict[int, bool] = {}
        self.lane_splits: dict[int, int] = {}
        self._finish: dict[int, int] = {}  # lane → finish, hundredths, for ranking
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
        return bool(buffer) and buffer[-1] in (CR, LF)

    @property
    def max_packet_bytes(self) -> int:
        return 128

    def configure(self, cfg: dict) -> None:
        self.num_lanes = int(cfg.get("num_lanes", 10))
        self.first_lane = first_lane_of(cfg)
        self._pad_sides = int(cfg.get("touchpad_sides", 1) or 1)
        try:
            baud = int(cfg.get("serial_baud", 19200))
        except (TypeError, ValueError):
            baud = 19200
        self._serial_config = SerialConfig(
            baud=baud, bytesize=8, parity="N", stopbits=1
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
        self._finish.clear()
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
        text = bytes(packet).decode("latin-1").strip("\r\n\x00 ")
        if not text:
            return {}
        if text.startswith("START"):
            return self._start()
        if m := _ID.match(text):
            return self._event_heat(int(m.group(1)), int(m.group(2)))
        if m := _CLOCK.match(text):
            return self._clock(int(m.group(1)), int(m.group(2)), m.group(3))
        if m := _LANE.match(text):
            return self._lane(*m.groups())
        return {}  # 'M' reaction, swimmer lines

    def _event_heat(self, ev: int, ht: int) -> dict:
        updates: dict = {}
        if ev <= 0 or ht <= 0:
            return updates
        updates["current_event"] = str(ev)
        updates["current_heat"] = str(ht)
        if (ev, ht) != self.last_event_sent:
            self.last_event_sent = (ev, ht)
            self.lane_seed_times.clear()
            updates.update(self.reset_lanes())
            updates["event_changed"] = (ev, ht)
        return updates

    def _start(self) -> dict:
        if self._race_active:
            return {}
        updates: dict = {"dismiss_overlay": True}
        if any(self.lane_times.values()):
            # The same heat started again: clear it, but keep the seeds — no
            # event_changed follows to load them a second time.
            seeds = dict(self.lane_seed_times)
            updates.update(self.reset_lanes())
            self.lane_seed_times = seeds
        self._race_active = True
        for i in self.lanes_to_start():
            self.lane_running[i] = True
            updates[f"lane_running{i}"] = True
        return updates

    def _clock(self, ev: int, ht: int, raw: str) -> dict:
        updates = self._event_heat(ev, ht)
        t = _clean_time(raw, 1)
        if not t:
            return updates
        if _is_zero(t):
            # Reset: the next clock that moves, or the START line, is a new race.
            self._race_active = False
            if self.running_time:
                self.running_time = ""
                updates["running_time"] = ""
            return updates
        if t != self.running_time:
            self.running_time = t
            updates["running_time"] = t
        if not self._race_active:
            updates.update(self._start())
        return updates

    def _lane(self, kind: str, ll: str, ttt: str, rr: str, raw: str) -> dict:
        updates: dict = {}
        lane = int(ll) - self.first_lane + 1
        if not 1 <= lane <= self.num_lanes:
            return updates
        if kind == "C":
            # A deleted time. While the race runs, the swimmer is still going.
            self._finish.pop(lane, None)
            self.lane_times[lane] = ""
            self.lane_places[lane] = " "
            updates[f"lane_time{lane}"] = ""
            updates[f"lane_place{lane}"] = " "
            if self._race_active and not self.lane_running.get(lane):
                self.lane_running[lane] = True
                updates[f"lane_running{lane}"] = True
            return updates
        t = _clean_time(raw, 2)
        if not t:
            return updates
        self.lane_times[lane] = t
        updates[f"lane_time{lane}"] = t
        lengths = int(ttt) * split_step(self._pad_sides)
        if lengths != self.lane_splits.get(lane, 0):
            self.lane_splits[lane] = lengths
            updates[f"lane_splits{lane}"] = lengths
        rank = rr.strip()
        if kind == "I":
            if rank and int(rank):
                self.lane_places[lane] = rank.lstrip("0")
                updates[f"lane_place{lane}"] = self.lane_places[lane]
            if self._race_active and not self.lane_running.get(lane):
                self.lane_running[lane] = True
                updates[f"lane_running{lane}"] = True
            return updates
        # Finish.
        if self.lane_running.get(lane):
            self.lane_running[lane] = False
            updates[f"lane_running{lane}"] = False
        hundredths = parse_time_hundredths(t)
        if hundredths:
            self._finish[lane] = hundredths
        if rank and int(rank):
            self.lane_places[lane] = rank.lstrip("0")
            updates[f"lane_place{lane}"] = self.lane_places[lane]
        else:
            updates.update(self._rank())
        return updates

    def _rank(self) -> dict:
        """Places from the finish times so far; a tie shares the place."""
        updates: dict = {}
        ordered = sorted(self._finish.values())
        for lane, h in self._finish.items():
            place = str(ordered.index(h) + 1)
            if place != self.lane_places.get(lane, " "):
                self.lane_places[lane] = place
                updates[f"lane_place{lane}"] = place
        return updates
