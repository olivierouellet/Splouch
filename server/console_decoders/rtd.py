"""Daktronics Real-Time Data (RTD) — the swimming layout, shared by two consoles.

RTD is Daktronics' scoreboard feed, and the same wire format the AllSport 5000
open-source projects decode. Each packet writes a run of ASCII into a 4096-byte
buffer at the offset its header names:

    SYN 00000000 SOH <type:3><sub:2><offset:5> STX <data> EOT <checksum: 2 hex> ETB

Type 004 is a buffer write — the only one decoded. An OmniSport 2000 also sends
001, 003, 008 and 900 control messages, and an ARES "Venus ERTD" output raw ON/OFF
commands; all are dropped. The sub-code
is "21" from an OmniSport and "01" (or "00") from an ARES. The checksum is every byte after SYN up to and including EOT, summed mod 256. What
each offset *means* comes from a per-sport Input Template File (ITF). The decoder
keeps its own copy of the buffer and reads fields out of it, so a packet covering
several fields — or one character of one — lands where the console meant it.

The Omnisport 2000 speaks it natively; the ARES 21 speaks it to feed Daktronics
"Venus" display controllers. Both put ten 36-character result lines on the board:

    +0 name (15) | +15 team (5) | +20 lane (2) | +22 place (3) | +25 time (9) | +34 lengths (2)

Each console subclass names its sub-codes and where its lines start. The line base
is a preset of the console entry, not a constant: an OmniSport writes lines at 222
when its Results port is set to OMNI2000 and at 200 when set to OMNI6000 — the same
200 the ARES uses. See omnisport_2000_serial.md and swiss_timing_ares21_serial.md.
"""

import re

from .base import ConsoleDecoder, DecoderOption
from .utils import first_lane_of

SYN = 0x16
SOH = 0x01
STX = 0x02
EOT = 0x04

BUFFER_SIZE = 4096

# Offsets inside one result line.
LINE_STRIDE = 36
LINE_NAME = (0, 15)
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


# The OmniSport's "RTD Offset" menu: every offset it sends is shifted by 5000·k.
RTD_OFFSET_OPTION = DecoderOption(
    key="rtd_offset",
    label="RTD offset (console setting)",
    choices=(("0", "0K"), ("5000", "5K"), ("10000", "10K"), ("15000", "15K")),
    default="0",
    help="Must match the console's RTD Offset menu. 0K unless the venue changed it.",
)


# For a console whose RTD output is not documented (a Gen7 set to Daktronics RTD):
# which swimming layout, which header sub-code, which serial settings.
RTD_LINE_BASE_OPTION = DecoderOption(
    key="rtd_line_base",
    label="RTD layout",
    choices=(
        ("222", "Result lines at 222 (OMNI2000)"),
        ("200", "Result lines at 200 (OMNI6000)"),
    ),
    default="222",
    help="Where this console writes the result lines. Change it if no lane times appear.",
)
RTD_SUB_CODE_OPTION = DecoderOption(
    key="rtd_sub_code",
    label="RTD header sub-code",
    choices=(("any", "Any"), ("21", "21 (OmniSport)"), ("01", "01 (ARES)")),
    default="any",
    help="The two digits after 004 in each packet header.",
)
# What an OmniSport capture showed and a Gen7 has not: each one on site, defaults
# off for the Gen7 so an unknown console behaves as before.
RTD_SPLIT_HOLD_OPTION = DecoderOption(
    key="rtd_split_hold",
    label="Lane chrono at a split",
    choices=(
        ("on", "Stops until the console clears the split"),
        ("off", "Keeps running"),
    ),
    default="on",
    help="Stops: as an OmniSport does. If lanes stay stopped after a split, "
    "choose Keeps running.",
)
RTD_PREVIEW_SUB_CODE_OPTION = DecoderOption(
    key="rtd_preview_sub_code",
    label="Next-heat page",
    choices=(("none", "None"), ("01", "Sub-code 01 (OmniSport)")),
    default="01",
    help="A page the console fills with the next heat as soon as the operator "
    "moves to it. If the board shows the next heat's names during a race, "
    "choose 01.",
)
RTD_VALIDATES_OPTION = DecoderOption(
    key="rtd_validates",
    label="Podium colours",
    choices=(
        ("off", "At the finish"),
        ("on", "After \u201cOfficial Results\u201d"),
    ),
    default="off",
    help="After: the console sends \u201cOfficial Results\u201d when the heat is "
    "validated, as an OmniSport does. If it never does, colours appear "
    "a minute after the finish.",
)


class RtdSwimmingDecoder(ConsoleDecoder):
    """A console that sends Daktronics RTD in the swimming template's line layout.

    Subclasses set:
      SUB_CODES     — the two header digits after the type that this console sends.
      PREVIEW_SUB_CODES — sub-codes of a second page the console fills with the
                      *next* heat (an OmniSport's `01`): read for its event, heat and
                      names only, never written over the live page.
      SPLIT_HOLD    — the console blanks a line's place and time a while after a
                      split, so the lane stops on the split until then. Off where
                      that clearing is not known: the lane would stay stopped.
      LINE_BASE     — offset of result line 1, unless the console entry presets
                      `rtd_line_base`.
      LINE_IS_LANE  — when a line's own lane field is blank, take line n as lane n.
      RESULT_FALLBACK — when place/time are not at their template offsets, look for
                      a time anywhere in the line's result half (and the number
                      just before it as the place).
    """

    MESSAGE_TYPE = "004"
    SUB_CODES: tuple[str, ...] = ()
    PREVIEW_SUB_CODES: tuple[str, ...] = ()
    SPLIT_HOLD = False
    LINE_BASE = 0
    LINE_IS_LANE = False
    RESULT_FALLBACK = False

    RUNNING_TIME = (0, 9)
    # "Official Results" once the operator validates the heat, blank otherwise.
    OFFICIAL = (1484, 16)
    EVENT_NUMBER = (99, 3)
    HEAT_NUMBER = (103, 2)
    MAX_LENGTHS = (126, 2)

    def __init__(self, cfg: dict) -> None:
        self._buf = bytearray(b" " * BUFFER_SIZE)
        self._preview = bytearray(b" " * BUFFER_SIZE)
        self._race_active = False
        # Lanes stopped at an intermediate time, waiting for the console to clear
        # the line — the end of its split hold — to swim on.
        self._held: set[int] = set()
        # Whether the console's own line for a lane carries a swimmer's name, from
        # the last write of that line on either page.
        self._named: dict[int, bool] = {}
        # The lane each line last carried, so an erased line is known to be that
        # lane's, and not a line that was blank all along.
        self._line_lane: dict[int, int] = {}
        # Rows whose line the console erased whole: nobody swims there. Kept across
        # a heat change and lifted only when a line carries the lane again, as the
        # next heat's lines do — the console may name the heat after it erased one.
        self._scratched: set[int] = set()
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
        # A packet can run across several fields — an OmniSport fills its whole
        # next-heat page in one, near 600 bytes — so the cap is the buffer plus its
        # framing. Cut any shorter, such a packet loses its end and its checksum.
        return BUFFER_SIZE + 32

    def configure(self, cfg: dict) -> None:
        self.num_lanes = int(cfg.get("num_lanes", 10))
        self.line_base = int(cfg.get("rtd_line_base", self.LINE_BASE))
        sub = str(cfg.get("rtd_sub_code", "") or "")
        self.sub_codes = None if sub == "any" else ((sub,) if sub else self.SUB_CODES)
        self.offset_shift = int(cfg.get("rtd_offset", 0) or 0)
        self.first_lane = first_lane_of(cfg)
        # Only where the console is known to write the official flag — waiting for
        # one that never comes would hold every heat's results until the fallback.
        validates = cfg.get("rtd_validates", False)
        self._validates = validates is True or validates == "on"
        hold = cfg.get("rtd_split_hold")
        self.split_hold = self.SPLIT_HOLD if hold in (None, "") else hold == "on"
        preview = str(cfg.get("rtd_preview_sub_code", "") or "")
        self.preview_sub_codes = (
            self.PREVIEW_SUB_CODES
            if not preview
            else ()
            if preview == "none"
            else (preview,)
        )

    @property
    def validates_results(self) -> bool:
        return self._validates

    def scratched_lanes(self) -> frozenset[int]:
        return frozenset(self._scratched)

    def _unscratch(self, lane: int, updates: dict) -> None:
        if lane in self._scratched:
            self._scratched.discard(lane)
            updates["scratch_changed"] = True

    def lanes_to_start(self) -> list[int]:
        """The start lists' lanes — and, when the console names swimmers, only those.

        With no start list for the heat (a final not yet seeded in the meet file)
        the console's own lines are the only word on which lanes are empty: a line
        with a lane number and no name has nobody in it. A console that sends no
        names at all says nothing either way, and every lane still starts.
        """
        lanes = super().lanes_to_start()
        if any(self._named.values()):
            lanes = [n for n in lanes if self._named.get(n, False)]
        return lanes

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
        self._held.clear()
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
        if len(header) != 10 or header[:3] != self.MESSAGE_TYPE:
            return {}
        if not header[5:].isdigit() or not header[3:5].isdigit():
            return {}
        # A sub-code that is not the live page may be the console's next-heat page.
        # With any sub-code accepted, a known next-heat page is still that page.
        sub = header[3:5]
        preview = sub in self.preview_sub_codes and (
            self.sub_codes is None or sub not in self.sub_codes
        )
        if not preview and self.sub_codes is not None and sub not in self.sub_codes:
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

        offset = int(header[5:]) - self.offset_shift
        if offset < 0:
            return {}
        data = bytes(packet[stx + 1 : eot])
        end = min(offset + len(data), BUFFER_SIZE)
        if offset >= end:
            return {}
        if preview:
            self._preview[offset:end] = data[: end - offset]
            return self._collect_preview(offset, end)
        self._buf[offset:end] = data[: end - offset]
        return self._collect_updates(offset, end)

    # ── Buffer readout ────────────────────────────────────────────────────────

    def _field(self, start: int, length: int, buf: bytearray | None = None) -> str:
        buf = self._buf if buf is None else buf
        return buf[start : start + length].decode("ascii", errors="replace")

    def _event_heat(self, touched) -> tuple[int, int] | None:
        """(event, heat) when the fields holding them were just written, else None."""
        if not (touched(*self.EVENT_NUMBER) or touched(*self.HEAT_NUMBER)):
            return None
        return to_int(self._field(*self.EVENT_NUMBER)), to_int(
            self._field(*self.HEAT_NUMBER)
        )

    def _announce_heat(self, ev_ht: tuple[int, int] | None, updates: dict) -> None:
        if not (ev_ht and ev_ht[0] > 0 and ev_ht[1] > 0):
            return
        updates["current_event"] = str(ev_ht[0])
        updates["current_heat"] = str(ev_ht[1])
        if ev_ht != self.last_event_sent:
            self.last_event_sent = ev_ht
            updates.update(self.reset_lanes())
            updates["event_changed"] = ev_ht

    def _collect_preview(self, lo: int, hi: int) -> dict:
        """The next-heat page: the heat the operator moved to, and who is in it.

        An OmniSport writes its live page's event and heat only at the start, so
        without this the board would show the last heat until the gun. Ignored while
        a race runs: a heat change there would wipe the one in the water.
        """
        if self._race_active:
            return {}

        def touched(start: int, length: int) -> bool:
            return start < hi and lo < start + length

        updates: dict = {}
        if touched(*self.EVENT_NUMBER) or touched(*self.HEAT_NUMBER):
            ev = to_int(self._field(*self.EVENT_NUMBER, self._preview))
            ht = to_int(self._field(*self.HEAT_NUMBER, self._preview))
            self._announce_heat((ev, ht), updates)
        for n in range(1, LINES + 1):
            base = self.line_base + (n - 1) * LINE_STRIDE
            if not touched(base, LINE_STRIDE):
                continue
            lane_text = self._field(base + LINE_LANE[0], LINE_LANE[1], self._preview)
            if lane_text.strip().isdigit():
                lane = int(lane_text) - self.first_lane + 1
                name = self._field(base + LINE_NAME[0], LINE_NAME[1], self._preview)
                self._named[lane] = bool(name.strip())
                self._unscratch(lane, updates)
        return updates

    def _collect_updates(self, lo: int, hi: int) -> dict:
        def touched(start: int, length: int) -> bool:
            return start < hi and lo < start + length

        updates: dict = {}

        self._announce_heat(self._event_heat(touched), updates)

        if touched(*self.RUNNING_TIME):
            raw = self._field(*self.RUNNING_TIME).strip()
            # The console counts in tenths ("41.6"); every client reads M:SS.CC.
            t = parse_time(raw) if raw else ""
            if t != self.running_time:
                self.running_time = t
                updates["running_time"] = t
            if is_zero_time(raw):
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

        if self._validates and touched(*self.OFFICIAL):
            # Reported on every write, not on a change: a console that never blanks
            # it between heats would otherwise validate only its first heat.
            updates["results_official"] = bool(self._field(*self.OFFICIAL).strip())

        max_lengths = to_int(self._field(*self.MAX_LENGTHS))
        for n in range(1, LINES + 1):
            base = self.line_base + (n - 1) * LINE_STRIDE
            if touched(base, LINE_STRIDE):
                self._read_line(n, base, max_lengths, updates)

        return updates

    def _line_result(self, n: int, base: int) -> tuple[int, str, str, int] | None:
        """(lane, place, raw time, lengths) from a line, or None for no lane."""

        def sub(field: tuple[int, int]) -> str:
            return self._field(base + field[0], field[1])

        place = sub(LINE_PLACE).strip()
        raw_time = sub(LINE_TIME).strip()
        # The line carries the pool's lane number; the board works in rows.
        lane_text = sub(LINE_LANE).strip()
        lane = int(lane_text) - self.first_lane + 1 if lane_text.isdigit() else 0
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

    def _lines_in_lane_order(self) -> bool:
        """True when every line that names a lane names the one its position gives.

        The board is in lane order during a race unless the console sorts by place,
        and only then does a line's position say whose it is.
        """
        seen = False
        for m in range(1, LINES + 1):
            base = self.line_base + (m - 1) * LINE_STRIDE
            text = self._field(base + LINE_LANE[0], LINE_LANE[1]).strip()
            if not text:
                continue
            if not text.isdigit() or int(text) - self.first_lane + 1 != m:
                return False
            seen = True
        return seen

    def _erased_line(self, n: int, base: int, updates: dict) -> None:
        """A line blanked whole, lane number included: a scratch.

        Seen on an OmniSport a few seconds after the gun, for a lane whose swimmer
        did not start; the operator can do it before the gun as well. Its lane comes
        from its position, so every guard is about being sure of that:

        * the line carried that very lane just before — a line that was never a
          lane's (a spare row in a short heat) says nothing;
        * every other line is in lane order — sorted by place, a position is no lane;
        * the lane holds no time and is not at a split — the blank line is then a
          re-sort, not a scratch.

        The row goes empty on the board: chrono stopped and blank, and the name and
        club blanked by the app layer (`scratched_lanes`).
        """
        was = self._line_lane.pop(n, 0)
        if self._field(base, LINE_STRIDE).strip():
            return
        if was != n or not 1 <= n <= self.num_lanes:
            return
        if self.lane_times.get(n) or n in self._held:
            return
        if not self._lines_in_lane_order():
            return
        self._named[n] = False
        if n not in self._scratched:
            self._scratched.add(n)
            updates["scratch_changed"] = True
        if self.lane_running.get(n):
            self._empty_lane(n, updates)

    def _empty_lane(self, lane: int, updates: dict) -> None:
        """Stop a lane nobody swims in, and blank its cell.

        `lane_running=False` alone leaves the clock's last frame in the cell — the
        browser keeps what it painted, the Qt board flashes it as a locked time —
        so the time and place are sent blank with it: an empty lane shows nothing.
        """
        self.lane_running[lane] = False
        self.lane_times[lane] = ""
        self.lane_places[lane] = " "
        updates[f"lane_running{lane}"] = False
        updates[f"lane_time{lane}"] = ""
        updates[f"lane_place{lane}"] = " "

    def _read_line(self, n: int, base: int, max_lengths: int, updates: dict) -> None:
        result = self._line_result(n, base)
        if result is None:
            return
        lane, place, raw_time, lengths = result
        if not 1 <= lane <= self.num_lanes:
            self._erased_line(n, base, updates)
            return
        self._line_lane[n] = lane
        self._unscratch(lane, updates)
        named = bool(self._field(base + LINE_NAME[0], LINE_NAME[1]).strip())
        self._named[lane] = named

        if not place:
            # No place, no result. A line with a time but no place may be showing
            # the running clock; a line blank in both has been cleared.
            if raw_time:
                return
            if self.lane_times.get(lane):
                self.lane_times[lane] = ""
                self.lane_places[lane] = " "
                updates[f"lane_time{lane}"] = ""
                updates[f"lane_place{lane}"] = " "
            if lane in self._held:
                # The console's split hold is over: the swimmer goes on.
                self._held.discard(lane)
                if self._race_active and not self.lane_running.get(lane):
                    self.lane_running[lane] = True
                    updates[f"lane_running{lane}"] = True
            elif (
                not named and self.lane_running.get(lane) and any(self._named.values())
            ):
                # The console names its swimmers and this lane has none: nobody
                # will ever touch here, so it must not hold the heat open.
                self._empty_lane(lane, updates)
            return
        if not raw_time:
            return

        t = parse_time(raw_time)
        if t != self.lane_times.get(lane, ""):
            self.lane_times[lane] = t
            updates[f"lane_time{lane}"] = t
        if lengths and lengths != self.lane_splits.get(lane, 0):
            self.lane_splits[lane] = lengths
            updates[f"lane_splits{lane}"] = lengths

        # The race distance is known once the heat is loaded; until then — or in a
        # one-length race, where nothing counts — a placed time is a finish.
        finished = not max_lengths or not lengths or lengths >= max_lengths
        if not finished:
            # An intermediate time. The lane stops on it, as a CTS lane does, until
            # the console clears the line; the console's place here is only the
            # order at the wall, and the board shows a place for a finish alone.
            if self.lane_places.get(lane, " ").strip():
                self.lane_places[lane] = " "
                updates[f"lane_place{lane}"] = " "
            if self.split_hold and self._race_active:
                self._held.add(lane)
                if self.lane_running.get(lane, False):
                    self.lane_running[lane] = False
                    updates[f"lane_running{lane}"] = False
            return

        self._held.discard(lane)
        if place != self.lane_places.get(lane, " "):
            self.lane_places[lane] = place
            updates[f"lane_place{lane}"] = place
        if self.lane_running.get(lane, False):
            self.lane_running[lane] = False
            updates[f"lane_running{lane}"] = False
