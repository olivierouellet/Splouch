import re

from .base import ConsoleDecoder, SerialConfig

_SYN = 0x16
_SOH = 0x01
_STX = 0x02
_EOT = 0x04

# The 10-digit header is "004210" + the offset, in characters, of the data into the
# console's RTD buffer. The buffer layout is Daktronics' own Input Template File for
# this sport mode (OS2-Swimming.itf) — every field below is an (offset, length) pair
# from it. Any other prefix is another sport mode, or Omni 6000 item numbers, whose
# offsets mean something else entirely.
_HEADER_PREFIX = "004210"
_BUFFER_SIZE = 4096

_RUNNING_TIME = (0, 9)
_EVENT_NUMBER = (99, 3)
_HEAT_NUMBER = (103, 2)
_MAX_LENGTHS = (126, 2)

# Ten result lines, 36 characters apart. A line is a row on the board, not a lane:
# the console may sort lines by place, so each one carries its own lane number.
_LINE_BASE = 222
_LINE_STRIDE = 36
_LINES = 10
_LINE_LANE = (20, 2)
_LINE_PLACE = (22, 3)
_LINE_TIME = (25, 9)
_LINE_LENGTHS = (34, 2)

# SS.f / M:SS.ff / M:SS.fff — up to thousandths, the template's 88:88.888
_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2})\.(\d{1,3})$")


def _parse_time(s: str) -> str:
    """Normalise an RTD time field to M:SS.CC, or SS.CC under a minute."""
    s = s.strip()
    m = _TIME_RE.match(s)
    if not m:
        return s
    mins = int(m.group(1) or 0)
    frac = m.group(3)[:2].ljust(2, "0")
    if mins:
        return f"{mins}:{int(m.group(2)):02d}.{frac}"
    return f"{m.group(2)}.{frac}"


def _is_zero_time(s: str) -> bool:
    """True for a blank clock or one that reads zero (0.0, 0:00.00 …)."""
    return not any(c in "123456789" for c in s)


def _int(s: str) -> int:
    s = s.strip()
    return int(s) if s.isdigit() else 0


class Omnisport2000Decoder(ConsoleDecoder):
    """Decoder for Daktronics Omnisport 2000 timing consoles (RTD port, J5).

    Serial protocol: 19200 baud, 8-N-1. Each packet writes a run of ASCII into the
    console's RTD buffer at the offset its header names:

        SYN 00000000 SOH 004210<offset> STX <data> EOT <checksum: 2 hex> ETB

    The decoder keeps its own copy of that buffer and reads the swimming fields out
    of it, so a packet that spans several fields — or only part of one — lands
    exactly where the console meant it to.

    See console_decoders/omnisport_2000_serial.md for the full protocol reference.
    """

    def __init__(self, cfg: dict):
        self._serial_config = SerialConfig(
            baud=19200, bytesize=8, parity="N", stopbits=1
        )
        self._buf = bytearray(b" " * _BUFFER_SIZE)
        self._race_active = False
        self.lane_times: dict[int, str] = {}
        self.lane_places: dict[int, str] = {}
        self.lane_running: dict[int, bool] = {}
        self.lane_splits: dict[int, int] = {}
        self.running_time = ""
        self.last_event_sent: tuple = (0, 0)
        self.lane_seed_times: dict[int, str] = {}
        self.configure(cfg)

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_config

    def is_packet_start(self, byte: int, buffer: list[int]) -> bool:
        return byte == _SYN

    @property
    def max_packet_bytes(self) -> int:
        # Framing is 27 bytes; the longest single field is a 30-character title, and
        # a packet can run across several fields.
        return 512

    def configure(self, cfg: dict) -> None:
        self.num_lanes = int(cfg.get("num_lanes", 10))

    def set_seed_times(self, times: dict) -> None:
        self.lane_seed_times = dict(times)

    def get_lane_time(self, lane_idx: int) -> str:
        return self.lane_times.get(lane_idx, "")

    def get_lane_place(self, lane_idx: int) -> str:
        return self.lane_places.get(lane_idx, " ")

    def adjust_splits(self, lane: int, delta: int) -> int:
        """Hand correction to a lap this console reports itself.

        Worth having even though the count is native: a console started on the wrong
        length, or a heat joined late, leaves every lane off by the same amount, and
        the operator has no other way to say so. The next result line overwrites
        this with the console's own number, which is the right outcome — the console
        is the authority whenever it speaks.
        """
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
        self.lane_seed_times.clear()
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

    # ── feed ──────────────────────────────────────────────────────────────────

    def feed(self, packet: list[int]) -> dict:
        """Decode one RTD packet and apply it to the buffer copy."""
        try:
            syn = packet.index(_SYN)
            soh = packet.index(_SOH, syn + 1)
            stx = packet.index(_STX, soh + 1)
            eot = packet.index(_EOT, stx + 1)
        except ValueError:
            return {}

        header = bytes(packet[soh + 1 : stx]).decode("ascii", errors="ignore")
        if len(header) != 10 or not header.startswith(_HEADER_PREFIX):
            return {}
        if not header[6:].isdigit():
            return {}

        # Checksum: every byte after SYN up to and including EOT, summed mod 256,
        # sent as two hex digits. Checked only when it is there to check.
        ck = bytes(packet[eot + 1 : eot + 3]).decode("ascii", errors="ignore")
        if len(ck) == 2:
            try:
                expected = int(ck, 16)
            except ValueError:
                return {}
            if sum(packet[syn + 1 : eot + 1]) & 0xFF != expected:
                return {}

        offset = int(header[6:])
        data = bytes(packet[stx + 1 : eot])
        end = min(offset + len(data), _BUFFER_SIZE)
        if offset >= end:
            return {}
        self._buf[offset:end] = data[: end - offset]
        return self._collect_updates(offset, end)

    # ── Buffer readout ────────────────────────────────────────────────────────

    def _field(self, start: int, length: int) -> str:
        return self._buf[start : start + length].decode("ascii", errors="replace")

    def _collect_updates(self, lo: int, hi: int) -> dict:
        def touched(start: int, length: int) -> bool:
            return start < hi and lo < start + length

        updates: dict = {}

        if touched(*_EVENT_NUMBER) or touched(*_HEAT_NUMBER):
            ev = _int(self._field(*_EVENT_NUMBER))
            ht = _int(self._field(*_HEAT_NUMBER))
            if ev > 0 and ht > 0:
                updates["current_event"] = str(ev)
                updates["current_heat"] = str(ht)
                tup = (ev, ht)
                if tup != self.last_event_sent:
                    self.last_event_sent = tup
                    updates.update(self.reset_lanes())
                    updates["event_changed"] = tup

        if touched(*_RUNNING_TIME):
            t = self._field(*_RUNNING_TIME).strip()
            if t != self.running_time:
                self.running_time = t
                updates["running_time"] = t
            if _is_zero_time(t):
                # Clock back at zero: the console was reset, so the next time it
                # moves is a new start — a restart of the same heat included.
                self._race_active = False
            elif not self._race_active:
                # No start message on this wire: the clock leaving zero is the start.
                self._race_active = True
                for i in range(1, self.num_lanes + 1):
                    if not self.lane_places.get(i, " ").strip():
                        self.lane_running[i] = True
                        updates[f"lane_running{i}"] = True
                updates["dismiss_overlay"] = True

        max_lengths = _int(self._field(*_MAX_LENGTHS))
        for n in range(_LINES):
            base = _LINE_BASE + n * _LINE_STRIDE
            if touched(base, _LINE_STRIDE):
                self._read_line(base, max_lengths, updates)

        return updates

    def _read_line(self, base: int, max_lengths: int, updates: dict) -> None:
        def sub(field: tuple[int, int]) -> str:
            return self._field(base + field[0], field[1])

        lane = _int(sub(_LINE_LANE))
        if not 1 <= lane <= self.num_lanes:
            return
        place = sub(_LINE_PLACE).strip()
        raw_time = sub(_LINE_TIME).strip()
        lengths = _int(sub(_LINE_LENGTHS))

        if not place:
            # No place, no result. A line with a time but no place may be showing
            # the running clock; a line blank in both has been cleared.
            if not raw_time and self.lane_times.get(lane):
                self.lane_times[lane] = ""
                self.lane_places[lane] = " "
                updates[f"lane_time{lane}"] = ""
                updates[f"lane_place{lane}"] = " "
            return
        if not raw_time:
            return

        t = _parse_time(raw_time)
        if t != self.lane_times.get(lane, ""):
            self.lane_times[lane] = t
            updates[f"lane_time{lane}"] = t
        if place != self.lane_places.get(lane, " "):
            self.lane_places[lane] = place
            updates[f"lane_place{lane}"] = place
        if lengths and lengths != self.lane_splits.get(lane, 0):
            self.lane_splits[lane] = lengths
            updates[f"lane_splits{lane}"] = lengths

        # The race distance is known once the heat is loaded; until then — or in a
        # one-length race, where nothing counts — a placed time is a finish.
        finished = not max_lengths or not lengths or lengths >= max_lengths
        if finished and self.lane_running.get(lane, False):
            self.lane_running[lane] = False
            updates[f"lane_running{lane}"] = False
