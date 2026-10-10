"""Daktronics Real-Time Data (RTD) — the swimming layout, shared by two consoles.

RTD is Daktronics' scoreboard feed, and the same wire format the AllSport 5000
open-source projects decode. Each packet writes a run of ASCII into a 4096-byte
buffer at the offset its header names:

    SYN 00000000 SOH <prefix><offset:4> STX <data> EOT <checksum: 2 hex> ETB

The checksum is every byte after SYN up to and including EOT, summed mod 256. What
each offset *means* comes from a per-sport Input Template File (ITF). The decoder
keeps its own copy of the buffer and reads fields out of it, so a packet covering
several fields — or one character of one — lands where the console meant it.

The Omnisport 2000 speaks it natively; the ARES 21 speaks it to feed Daktronics
"Venus" display controllers. Both put ten 36-character result lines on the board:

    +0 name (15) | +15 team (5) | +20 lane (2) | +22 place (3) | +25 time (9) | +34 lengths (2)

Each console subclass names its header prefix and where its lines start. See
omnisport_2000_serial.md for the template and the capture it was checked against.
"""

import re

from .base import ConsoleDecoder

SYN = 0x16
SOH = 0x01
STX = 0x02
EOT = 0x04

BUFFER_SIZE = 4096

# Offsets inside one result line.
LINE_STRIDE = 36
LINE_LANE = (20, 2)
LINE_PLACE = (22, 3)
LINE_TIME = (25, 9)
LINE_LENGTHS = (34, 2)
LINES = 10

# SS.f / M:SS.ff / M:SS.fff — up to thousandths, the template's 88:88.888
_TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2})\.(\d{1,3})$")
# A time anywhere in a run of text, for a line whose fields are not where the
# template puts them.
_TIME_IN_TEXT = re.compile(r"\d{1,2}:\d{2}\.\d{1,3}|\d{1,2}\.\d{1,3}")


def parse_time(s: str) -> str:
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


def is_zero_time(s: str) -> bool:
    """True for a blank clock or one that reads zero (0.0, 0:00.00 …)."""
    return not any(c in "123456789" for c in s)


def to_int(s: str) -> int:
    s = s.strip()
    return int(s) if s.isdigit() else 0


class RtdSwimmingDecoder(ConsoleDecoder):
    """A console that sends Daktronics RTD in the swimming template's line layout.

    Subclasses set:
      HEADER_PREFIX — the six header digits before the offset. Any other prefix is
                      another sport mode, whose offsets mean different fields.
      LINE_BASE     — offset of result line 1.
      LINE_IS_LANE  — when a line's own lane field is blank, take line n as lane n.
      RESULT_FALLBACK — when place/time are not at their template offsets, look for
                      a time anywhere in the line's result half (and the number
                      just before it as the place).
    """

    HEADER_PREFIX = ""
    LINE_BASE = 0
    LINE_IS_LANE = False
    RESULT_FALLBACK = False

    RUNNING_TIME = (0, 9)
    EVENT_NUMBER = (99, 3)
    HEAT_NUMBER = (103, 2)
    MAX_LENGTHS = (126, 2)

    def __init__(self, cfg: dict) -> None:
        self._buf = bytearray(b" " * BUFFER_SIZE)
        self._race_active = False
        self.lane_times: dict[int, str] = {}
        self.lane_places: dict[int, str] = {}
        self.lane_running: dict[int, bool] = {}
        self.lane_splits: dict[int, int] = {}
        self.running_time = ""
        self.last_event_sent: tuple[int, int] = (0, 0)
        self.lane_seed_times: dict[int, str] = {}
        self.configure(cfg)

    def is_packet_start(self, byte: int, buffer: list[int]) -> bool:
        # SYN starts every packet. SOH does too when nothing before it was a SYN:
        # a frame reaching us without its RTD prefix still decodes — it just has no
        # checksum to verify.
        if byte == SYN:
            return True
        return byte == SOH and not (buffer and buffer[0] == SYN)

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
            soh = packet.index(SOH)
            stx = packet.index(STX, soh + 1)
            eot = packet.index(EOT, stx + 1)
        except ValueError:
            return {}

        header = bytes(packet[soh + 1 : stx]).decode("ascii", errors="ignore")
        if len(header) != 10 or not header.startswith(self.HEADER_PREFIX):
            return {}
        if not header[6:].isdigit():
            return {}

        # Checksum: every byte after SYN up to and including EOT, summed mod 256,
        # sent as two hex digits. Only a packet that kept its SYN can be checked.
        if packet[0] == SYN:
            ck = bytes(packet[eot + 1 : eot + 3]).decode("ascii", errors="ignore")
            if len(ck) == 2:
                try:
                    expected = int(ck, 16)
                except ValueError:
                    return {}
                if sum(packet[1 : eot + 1]) & 0xFF != expected:
                    return {}

        offset = int(header[6:])
        data = bytes(packet[stx + 1 : eot])
        end = min(offset + len(data), BUFFER_SIZE)
        if offset >= end:
            return {}
        self._buf[offset:end] = data[: end - offset]
        return self._collect_updates(offset, end)

    # ── Buffer readout ────────────────────────────────────────────────────────

    def _field(self, start: int, length: int) -> str:
        return self._buf[start : start + length].decode("ascii", errors="replace")

    def _event_heat(self, touched) -> tuple[int, int] | None:
        """(event, heat) when the fields holding them were just written, else None."""
        if not (touched(*self.EVENT_NUMBER) or touched(*self.HEAT_NUMBER)):
            return None
        return to_int(self._field(*self.EVENT_NUMBER)), to_int(
            self._field(*self.HEAT_NUMBER)
        )

    def _collect_updates(self, lo: int, hi: int) -> dict:
        def touched(start: int, length: int) -> bool:
            return start < hi and lo < start + length

        updates: dict = {}

        ev_ht = self._event_heat(touched)
        if ev_ht and ev_ht[0] > 0 and ev_ht[1] > 0:
            updates["current_event"] = str(ev_ht[0])
            updates["current_heat"] = str(ev_ht[1])
            if ev_ht != self.last_event_sent:
                self.last_event_sent = ev_ht
                updates.update(self.reset_lanes())
                updates["event_changed"] = ev_ht

        if touched(*self.RUNNING_TIME):
            t = self._field(*self.RUNNING_TIME).strip()
            if t != self.running_time:
                self.running_time = t
                updates["running_time"] = t
            if is_zero_time(t):
                # Clock back at zero: the console was reset, so the next time it
                # moves is a new start — a restart of the same heat included.
                self._race_active = False
            elif not self._race_active:
                # No start message on this wire: the clock leaving zero is the start.
                self._race_active = True
                # Only lanes with a swimmer: an empty one never touches, and would
                # hold race_finished() False for good — see set_heat_lanes.
                for i in self.lanes_to_start():
                    if not self.lane_places.get(i, " ").strip():
                        self.lane_running[i] = True
                        updates[f"lane_running{i}"] = True
                updates["dismiss_overlay"] = True

        max_lengths = to_int(self._field(*self.MAX_LENGTHS))
        for n in range(1, LINES + 1):
            base = self.LINE_BASE + (n - 1) * LINE_STRIDE
            if touched(base, LINE_STRIDE):
                self._read_line(n, base, max_lengths, updates)

        return updates

    def _line_result(self, n: int, base: int) -> tuple[int, str, str, int] | None:
        """(lane, place, raw time, lengths) from a line, or None for no lane."""

        def sub(field: tuple[int, int]) -> str:
            return self._field(base + field[0], field[1])

        place = sub(LINE_PLACE).strip()
        raw_time = sub(LINE_TIME).strip()
        lane = to_int(sub(LINE_LANE))
        fixed_ok = (not place or place.isdigit()) and (
            not raw_time or bool(_TIME_RE.match(raw_time))
        )
        if fixed_ok:
            if not 1 <= lane <= self.num_lanes and self.LINE_IS_LANE:
                lane = n
            return lane, place, raw_time, to_int(sub(LINE_LENGTHS))

        if not self.RESULT_FALLBACK:
            return None
        # The fields are not where the template puts them. Take the first time in
        # the result half of the line, and the number just before it as the place;
        # the line is the lane, since the lane field cannot be trusted either.
        text = self._field(base + LINE_LANE[0], LINE_STRIDE - LINE_LANE[0])
        m = _TIME_IN_TEXT.search(text)
        if not m:
            return n, "", "", 0
        before = text[: m.start()].split()
        place = before[-1] if before and before[-1].isdigit() else ""
        return n, place, m.group(), 0

    def _read_line(self, n: int, base: int, max_lengths: int, updates: dict) -> None:
        result = self._line_result(n, base)
        if result is None:
            return
        lane, place, raw_time, lengths = result
        if not 1 <= lane <= self.num_lanes:
            return

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

        t = parse_time(raw_time)
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
