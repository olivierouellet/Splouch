"""Swiss Timing scoreboard protocol (UNT4 / GALACTICA) — line messages for a board.

What an ARES 21 sends on its SCB connector when the scoreboard kind is UNT4,
Calypso, Piccolo or GALACTICA (Swiss Timing doc 0100.064 §1), and what an OmniSport
2000 sends on its scoreboard port set to OMEGA. Every message is `SOH <text> EOT`:

    DC4 R02 STX BS '1 ' STX HH:MM:SS.D        running time (HH:MM:S may be blank)
    DC4 S02 STX BS LF*n A ' ' STX BB ' ' T    line n+1: A, BB and a net time T
    DC4 S02 STX BS LF*n A ' ' STX <blank>     line n+1 cleared

In *lane mode* A is the lane (one character, 0 = 10) and BB the rank; in *rank mode*
A is the rank and BB the lane. Nothing on the wire says which, hence the option.
Rank mode is the default: GALACTICA is rank mode, plus reaction times (`DC1 97 STX
RT|LL|t`, ignored).

What it does not carry: event and heat, and the lap. One undocumented message,
`ACK BS '8'` + 11 LF, holds an event at 20–22 and a heat at 23–25 when the 14th
character is not a LF; it is decoded here as a bonus, unconfirmed.

Start: the clock leaving zero, or a zeroed line before the race — the doc's "new
race", or any line ending ` 0.00`.

Splits: a time on a line is a touch. The ARES erases an intermediate time after a
few seconds; an erased line while the race runs means the swimmer is still going,
so the lane goes back to running. A time that stays is the finish.

See swiss_scoreboard_serial.md.
"""

import re

from .base import ConsoleDecoder, DecoderOption, SerialConfig, serial_from
from .utils import first_lane_of, lane_digit_row, split_step

SOH = 0x01
STX = "\x02"
EOT = 0x04

_RUNNING = "\x14R02\x02\x08"
_LINE = "\x14S02\x02\x08"
_EVENT_HEAT = "\x06\x088"

_TIME = re.compile(r"^(?:(\d+):)?(?:(\d+):)?(\d{1,2})[.,](\d{1,3})$")


def _clean_time(raw: str) -> str:
    """'  :01:02.3' / '00:00:58.21' / '0,0' → '1:02.3' / '58.21' / '0.0'."""
    t = raw.replace(" ", "").replace(",", ".")
    t = t.lstrip(":")
    m = _TIME.match(t)
    if not m:
        return t
    a, b, sec, frac = m.groups()
    hours, mins = (int(a), int(b)) if b is not None else (0, int(a or 0))
    if hours:
        return f"{hours}:{mins:02d}:{int(sec):02d}.{frac}"
    if mins:
        return f"{mins}:{int(sec):02d}.{frac}"
    return f"{int(sec)}.{frac}"


def _is_zero(t: str) -> bool:
    return not any(c in "123456789" for c in t)


def _rank_digit(c: str) -> int:
    """A one-character rank: `0` is 10th."""
    c = c.strip()
    if c == "0":
        return 10
    return int(c) if c.isdigit() else 0


class SwissScoreboardDecoder(ConsoleDecoder):
    """Decoder for the Swiss Timing UNT4 / GALACTICA scoreboard line protocol."""

    OPTIONS = (
        DecoderOption(
            key="scb_line_mode",
            label="Scoreboard line order",
            choices=(("lane", "Lane mode"), ("rank", "Rank mode")),
            default="rank",
            help="Must match the console's scoreboard kind: 'La' entries are lane "
            "mode, 'Rk' entries and GALACTICA are rank mode.",
        ),
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
            choices=(("8N1", "8-N-1"), ("7E1", "7-E-1")),
            default="7E1",
            help="7-E-1 is the usual format. If nothing decodes, try the other.",
        ),
    )

    def __init__(self, cfg: dict) -> None:
        self._serial_config = SerialConfig(
            baud=9600, bytesize=7, parity="E", stopbits=1
        )
        self.lane_times: dict[int, str] = {}
        self.lane_places: dict[int, str] = {}
        self.lane_running: dict[int, bool] = {}
        self.lane_splits: dict[int, int] = {}
        self._line_lane: dict[int, int] = {}
        self._race_active = False
        self.running_time = ""
        self.last_event_sent: tuple[int, int] = (0, 0)
        self.lane_seed_times: dict[int, str] = {}
        self.configure(cfg)

    # ── ConsoleDecoder interface ──────────────────────────────────────────────

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_config

    def is_packet_start(self, byte: int, buffer: list[int]) -> bool:
        return byte == SOH

    @property
    def max_packet_bytes(self) -> int:
        return 128

    def configure(self, cfg: dict) -> None:
        self.num_lanes = int(cfg.get("num_lanes", 10))
        self.rank_mode = str(cfg.get("scb_line_mode", "rank")) == "rank"
        self._pad_sides = int(cfg.get("touchpad_sides", 1) or 1)
        self.first_lane = first_lane_of(cfg)
        self._serial_config = serial_from(
            cfg.get("serial_baud", 9600),
            str(cfg.get("serial_format", "7E1")),
            "7E1",
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
        self._line_lane.clear()
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
        if not packet or packet[0] != SOH or EOT not in packet:
            return {}
        msg = bytes(packet[1 : packet.index(EOT)]).decode("latin-1")
        if msg.startswith(_RUNNING):
            return self._running(msg)
        if msg.startswith(_LINE):
            return self._line(msg[len(_LINE) :])
        if msg.startswith(_EVENT_HEAT):
            return self._event_heat(msg)
        return {}

    def _running(self, msg: str) -> dict:
        updates: dict = {}
        t = _clean_time(msg.rsplit(STX, 1)[-1])
        if t != self.running_time:
            self.running_time = t
            updates["running_time"] = t
        if _is_zero(t):
            if self._race_active:
                # Clock back to zero: a new race.
                updates.update(self.reset_lanes())
        elif not self._race_active:
            updates.update(self._start())
        return updates

    def _start(self) -> dict:
        updates: dict = {"dismiss_overlay": True}
        self._race_active = True
        for i in self.lanes_to_start():
            if not self.lane_times.get(i):
                self.lane_running[i] = True
                updates[f"lane_running{i}"] = True
        return updates

    def _line(self, body: str) -> dict:
        updates: dict = {}
        n = len(body) - len(body.lstrip("\n"))
        body = body[n:]
        if STX not in body:
            return updates
        head, tail = body.split(STX, 1)
        a = head[:1]
        b, raw_time = tail[:2], tail[2:]
        time = _clean_time(raw_time.strip()) if raw_time.strip() else ""

        new_race = n == 0 and not b.strip()
        if time and _is_zero(time) and (new_race or not self._race_active):
            # "New race" (doc 0100.064): line 1, no second field, a zero time. Any
            # line ending ` 0.00` before the race is the start too; during a race
            # such a line is an erased split instead, below. A console that also sends
            # the clock puts it back at zero, which resets this start until the gun.
            self.running_time = ""
            return {**self.reset_lanes(), "running_time": "", **self._start()}

        # BB is two characters, so a lane there is its full number; A is one.
        if self.rank_mode:
            rank = _rank_digit(a)
            lane = int(b) - self.first_lane + 1 if b.strip().isdigit() else 0
        else:
            lane = lane_digit_row(a, self.first_lane)
            rank = (
                _rank_digit(b)
                if len(b.strip()) == 1
                else (int(b) if b.strip().isdigit() else 0)
            )
        if not lane:
            lane = self._line_lane.get(n, 0)
        if not 1 <= lane <= self.num_lanes:
            return updates

        if not time or _is_zero(time):
            # Line erased. While the race runs that was an intermediate time: the
            # swimmer is still going.
            self._line_lane.pop(n, None)
            if self.lane_times.get(lane):
                self.lane_times[lane] = ""
                self.lane_places[lane] = " "
                updates[f"lane_time{lane}"] = ""
                updates[f"lane_place{lane}"] = " "
            if self._race_active and not self.lane_running.get(lane):
                self.lane_running[lane] = True
                updates[f"lane_running{lane}"] = True
            return updates

        self._line_lane[n] = lane
        if time != self.lane_times.get(lane):
            self.lane_times[lane] = time
            updates[f"lane_time{lane}"] = time
            if self._race_active:
                self.lane_splits[lane] = self.lane_splits.get(lane, 0) + split_step(
                    self._pad_sides
                )
                updates[f"lane_splits{lane}"] = self.lane_splits[lane]
        place = str(rank) if rank else " "
        if place != self.lane_places.get(lane, " "):
            self.lane_places[lane] = place
            updates[f"lane_place{lane}"] = place
        if self.lane_running.get(lane):
            self.lane_running[lane] = False
            updates[f"lane_running{lane}"] = False
        return updates

    def _event_heat(self, msg: str) -> dict:
        updates: dict = {}
        if len(msg) < 26 or msg[14] == "\n":
            return updates
        ev, ht = msg[20:23].strip(), msg[23:26].strip()
        if not (ev.isdigit() and ht.isdigit()):
            return updates
        pair = (int(ev), int(ht))
        if pair[0] <= 0 or pair[1] <= 0:
            return updates
        updates["current_event"] = str(pair[0])
        updates["current_heat"] = str(pair[1])
        if pair != self.last_event_sent:
            self.last_event_sent = pair
            self.lane_seed_times.clear()
            updates.update(self.reset_lanes())
            updates["event_changed"] = pair
        return updates
