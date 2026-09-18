"""CTS Gen6 (System 5 / System 6 / Gen7 Legacy) decoder.

Written from the protocol specification in cts_gen6_serial.md.
Serial: 9 600 baud, 8 data bits, even parity, 1 stop bit.
"""
import time
import traceback

from .base import ConsoleDecoder, SerialConfig

# Decoded channel address → 1-based lane index.
# Channels 1-10 map directly; channels 23 and 24 extend to lanes 11-12.
_LANE_MAP: dict[int, int] = {**{ch: ch for ch in range(1, 11)}, 23: 11, 24: 12}


def _digit(nibble: int) -> str:
    """Decode one wire nibble to a display character ('0'-'9' or ' ')."""
    v = (nibble & 0x0F) ^ 0x0F
    return ' ' if v > 9 else str(v)


def _time_str(raw: list[int]) -> str:
    """Convert six raw byte values (slots 2-7) to a MM:SS.HH display string.

    Returns an empty string when seconds and hundredths are all blank,
    which means no time has been received yet.
    """
    m0, m1 = _digit(raw[0]), _digit(raw[1])
    s0, s1 = _digit(raw[2]), _digit(raw[3])
    h0, h1 = _digit(raw[4]), _digit(raw[5])
    if s0 == s1 == h0 == h1 == ' ':
        return ''
    mins = ('' if m0 == ' ' else m0) + ('0' if m1 == ' ' else m1)
    secs = ('0' if s0 == ' ' else s0) + ('0' if s1 == ' ' else s1)
    hund = ('0' if h0 == ' ' else h0) + ('0' if h1 == ' ' else h1)
    return f'{mins}:{secs}.{hund}'


class CTSGen6Decoder(ConsoleDecoder):
    """Decoder for CTS System 5, System 6, and Gen7 Legacy timing consoles.

    Protocol reference: console_decoders/cts_gen6_serial.md
    """

    def __init__(self, cfg: dict) -> None:
        self._serial_cfg = SerialConfig(baud=9600, bytesize=8, parity='E', stopbits=1)

        # Digit-slot buffers, one 8-slot array per lane (index 0 unused).
        self._slots: list[list[int]] = [[]] + [[0] * 8 for _ in range(12)]

        # Auxiliary channel buffers.
        self._rt_buf:    list[int] = [0] * 8  # ch 0  — running time
        self._eh_buf:    list[str] = [' '] * 8  # ch 12 — event / heat digits
        self._rec_buf:   list[int] = [0] * 8  # ch 11 — length / record
        self._place_buf: list[int] = [0] * 8  # ch 14 — global place
        self._t22_buf:   list[int] = [0] * 8  # ch 22 — time display

        self._running_time = ''
        self._lane_active: list[bool] = [False] * 12

        self.last_event_sent: tuple[int, int] = (0, 0)
        self.lane_seed_times: dict[int, str] = {}

        # Split-counting state.
        self._split_stop:    dict[int, float] = {}
        self._split_counted: dict[int, bool]  = {}
        self._splits:        dict[int, int]   = {}

        self.configure(cfg)

    # ── ConsoleDecoder interface ──────────────────────────────────────────────

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_cfg

    def is_packet_start(self, byte: int, _buffer: list[int]) -> bool:
        return bool(byte & 0x80)

    @property
    def max_packet_bytes(self) -> int:
        return 9

    def configure(self, cfg: dict) -> None:
        self._num_lanes = int(cfg.get('num_lanes', 10))
        self._split_min = float(cfg.get('split_min_duration', 1.0))
        self._pad_sides = int(cfg.get('touchpad_sides', 1))

    @property
    def split_step(self) -> int:
        """Two lengths per counted split when only one end has touchpads.

        Nothing on this wire carries a lap number, so the count here is the *stops*
        this decoder has seen (see `feed`). With pads at one end the swimmer is only
        seen every second length, so each stop is worth two.
        """
        return 2 if self._pad_sides == 1 else 1

    def set_seed_times(self, times: dict) -> None:
        self.lane_seed_times = dict(times)

    def get_lane_time(self, lane: int) -> str:
        return _time_str(self._slots[lane][2:8])

    def get_lane_place(self, lane: int) -> str:
        return _digit(self._slots[lane][1])

    def adjust_splits(self, lane: int, delta: int) -> int:
        val = max(0, self._splits.get(lane, 0) + delta)
        self._splits[lane] = val
        return val

    def reset_lanes(self) -> dict:
        updates: dict = {}
        for ln in range(1, 13):
            self._slots[ln]          = [0] * 8
            self._lane_active[ln-1]  = False
            updates[f'lane_time{ln}']    = ''
            updates[f'lane_place{ln}']   = ' '
            updates[f'lane_running{ln}'] = False
            updates[f'lane_delta{ln}']   = ''
            updates[f'lane_splits{ln}']  = 0
        self._splits.clear()
        self._split_stop.clear()
        self._split_counted.clear()
        return updates

    def race_finished(self) -> bool:
        any_placed = False
        for ln in range(1, self._num_lanes + 1):
            if self._lane_active[ln - 1]:
                return False
            t = _time_str(self._slots[ln][2:8])
            if t:
                if _digit(self._slots[ln][1]) == ' ':
                    return False
                any_placed = True
        return any_placed

    # ── Packet decoding ───────────────────────────────────────────────────────

    def feed(self, packet: list[int]) -> dict:
        """Decode one assembled packet; return a dict of display updates.

        Special keys consumed by the app layer (not forwarded to clients):
          'event_changed'   → (event_num, heat_num)
          'dismiss_overlay' → True
        """
        updates: dict = {}
        data = list(packet)
        try:
            header   = data.pop(0)
            running  = bool(header & 0x40)   # bit 6: lane actively racing
            fmt_only = bool(header & 0x01)   # bit 0: format-display packet
            channel  = ((header & 0x3E) >> 1) ^ 0x1F

            if fmt_only:
                return updates

            # ── Lanes 1-12 ────────────────────────────────────────────────────
            if channel in _LANE_MAP:
                ln   = _LANE_MAP[channel]
                prev = self._lane_active[ln - 1]
                self._lane_active[ln - 1] = running

                for byte in data:
                    self._slots[ln][(byte >> 4) & 0x0F] = byte

                updates[f'lane_place{ln}']   = _digit(self._slots[ln][1])
                updates[f'lane_running{ln}'] = running

                if running and not prev:
                    updates['dismiss_overlay'] = True

                if not running:
                    updates[f'lane_time{ln}'] = _time_str(self._slots[ln][2:8])

                # Split counting: count each stop after an active run.
                if prev and not running:
                    self._split_stop[ln]    = time.time()
                    self._split_counted[ln] = False
                elif running:
                    self._split_stop.pop(ln, None)
                elif not self._split_counted.get(ln, True):
                    elapsed = time.time() - self._split_stop.get(ln, time.time())
                    if elapsed >= self._split_min:
                        self._splits[ln] = self._splits.get(ln, 0) + self.split_step
                        self._split_counted[ln] = True
                        updates[f'lane_splits{ln}'] = self._splits[ln]

            # ── Ch 0 — Running time ───────────────────────────────────────────
            elif channel == 0:
                for byte in data:
                    self._rt_buf[(byte >> 4) & 0x0F] = byte
                self._running_time = _time_str(self._rt_buf[2:8])
                updates['running_time'] = self._running_time

            # ── Ch 11 — Length / record ───────────────────────────────────────
            elif channel == 11:
                self._rec_buf = [0] * 8
                for byte in data:
                    self._rec_buf[(byte >> 4) & 0x0F] = byte
                updates['length_record'] = ''.join(
                    _digit(self._rec_buf[i]) for i in range(8)
                ).strip()

            # ── Ch 12 — Event / heat ──────────────────────────────────────────
            elif channel == 12:
                # Reset before each packet: the console omits blank slots, so
                # accumulating across packets would leave stale digits when the
                # event number shrinks (e.g. event 31 → event 1 never blanks slot 1).
                self._eh_buf = [' '] * 8
                for byte in data:
                    self._eh_buf[(byte >> 4) & 0x0F] = _digit(byte)

                event_str = ''.join(self._eh_buf[:3])
                heat_str  = ''.join(self._eh_buf[-3:])
                # Only emit non-blank values: split packets (e.g. event-only or
                # heat-only) should not overwrite the other field in the browser.
                # Strip the console's blank left-pad so the value matches the
                # unpadded str(int) form the other decoders and send_event_info
                # emit — otherwise "  3" vs "3" reads as a heat change and flips
                # the board to intro every time send_event_info broadcasts.
                if event_str.strip():
                    updates['current_event'] = event_str.strip()
                if heat_str.strip():
                    updates['current_heat']  = heat_str.strip()

                try:
                    ev_heat = (int(event_str), int(heat_str))
                except ValueError:
                    return updates

                if self.last_event_sent != ev_heat:
                    self.last_event_sent = ev_heat
                    self.lane_seed_times.clear()
                    updates.update(self.reset_lanes())
                    updates['event_changed'] = ev_heat

            # ── Ch 14 — Global place ──────────────────────────────────────────
            elif channel == 14:
                self._place_buf = [0] * 8
                for byte in data:
                    self._place_buf[(byte >> 4) & 0x0F] = byte
                updates['global_place'] = ''.join(
                    _digit(self._place_buf[i]) for i in range(8)
                ).strip()

            # ── Ch 22 — Time display ──────────────────────────────────────────
            elif channel == 22:
                for byte in data:
                    self._t22_buf[(byte >> 4) & 0x0F] = byte
                updates['channel_22_time'] = _time_str(self._t22_buf[2:8])

        except IndexError:
            traceback.print_exc()

        return updates
