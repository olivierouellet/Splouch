from .base import ConsoleDecoder, SerialConfig

# OSM6 control bytes (all < 0x80, compatible with 7-bit framing)
_SOH  = 0x01   # Start of Heading — every frame begins here
_STX  = 0x02   # Start of Text — separates header from time in pt2
_EOT  = 0x04   # End of Transmission — frame terminator
_HOME = 0x08   # Home — third byte of the standard frame prefix
_LF   = 0x0A   # Line Feed — identifies pt2 (4th byte after prefix)
_DC2  = 0x12   # Device Control 2 — alive message marker
_DC4  = 0x14   # Device Control 4 — alive message marker

_ALIVE = [_SOH, _DC2, ord('9'), _DC4, ord('T'), ord('P'), _EOT]
_PREFIX = [_SOH, _STX, _HOME]


def _parse_time(s: str) -> str:
    """Normalize OSM6 time 'HH:MM:SS.cc' (blanked leading parts) to M:SS.cc."""
    parts = [p.strip() for p in s.strip().split(':')]
    parts = [p for p in parts if p]   # drop blank segments (spaces = absent)
    if not parts:
        return ''
    try:
        if len(parts) == 3:
            total_min = int(parts[0]) * 60 + int(parts[1])
            sec = parts[2]
            return f'{total_min}:{sec}' if total_min else sec
        if len(parts) == 2:
            m_val = int(parts[0])
            sec = parts[1]
            return f'{m_val}:{sec}' if m_val else sec
        return parts[0]
    except ValueError:
        return ''


class QuantumDecoder(ConsoleDecoder):
    """Decoder for Swiss Timing Quantum timing consoles (OSM6 serial protocol).

    Serial protocol: 9600 baud, 7-N-1, RS-422.
    Messages arrive as paired frames (pt1 + pt2), each framed by
    SOH+STX+HOME … EOT. pt2 is identified by LF as the 4th byte.

    See console_decoders/swiss_timing_quantum_serial.md for the full
    protocol reference.
    Source: hakostra/swimming-scoreboard (comms.py, GPL-3.0).
    """

    def __init__(self, cfg: dict) -> None:
        self._serial_config = SerialConfig(baud=9600, bytesize=8, parity='N', stopbits=1)
        self._pending_pt1: list[int] | None = None

        self.lane_times:   dict[int, str]  = {}
        self.lane_places:  dict[int, str]  = {}
        self.lane_running: dict[int, bool] = {}
        self.lane_splits:  dict[int, int]  = {}
        self.last_event_sent:  tuple[int, int] = (0, 0)
        self._race_active  = False
        self.lane_seed_times: dict[int, str] = {}
        self.configure(cfg)

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_config

    def is_packet_start(self, byte: int, buffer: list[int]) -> bool:
        return byte == _SOH

    @property
    def max_packet_bytes(self) -> int:
        return 32

    def configure(self, cfg: dict) -> None:
        self.num_lanes = int(cfg.get('num_lanes', 10))

    def set_seed_times(self, times: dict) -> None:
        self.lane_seed_times = dict(times)

    def get_lane_time(self, lane_idx: int) -> str:
        return self.lane_times.get(lane_idx, '')

    def get_lane_place(self, lane_idx: int) -> str:
        return self.lane_places.get(lane_idx, ' ')

    def adjust_splits(self, lane: int, delta: int) -> int:
        """Hand correction to a lap this console reports itself.

        Worth having even though the count is native: a console started on the wrong
        length, or a heat joined late, leaves every lane off by the same amount, and
        the operator has no other way to say so. The next split message overwrites
        this with the console's own number, which is the right outcome — the console
        is the authority whenever it speaks.
        """
        val = max(0, self.lane_splits.get(lane, 0) + delta)
        self.lane_splits[lane] = val
        return val

    def reset_lanes(self) -> dict:
        updates: dict = {}
        for i in range(1, self.num_lanes + 1):
            self.lane_times[i]   = ''
            self.lane_places[i]  = ' '
            self.lane_running[i] = False
            self.lane_splits[i]  = 0
            updates[f'lane_time{i}']    = ''
            updates[f'lane_place{i}']   = ' '
            updates[f'lane_running{i}'] = False
            updates[f'lane_delta{i}']   = ''
            updates[f'lane_splits{i}']  = 0
        self.lane_seed_times.clear()
        self._race_active = False
        return updates

    def race_finished(self) -> bool:
        any_placed = False
        for i in range(1, self.num_lanes + 1):
            if self.lane_running.get(i):
                return False
            if self.lane_times.get(i):
                if self.lane_places.get(i, ' ') == ' ':
                    return False
                any_placed = True
        return any_placed

    def feed(self, packet: list[int]) -> dict:
        """Decode one OSM6 frame. pt1 is buffered until pt2 arrives."""
        if packet == _ALIVE:
            return {}

        if len(packet) < 5 or packet[:3] != _PREFIX or packet[-1] != _EOT:
            return {}

        payload = packet[3:-1]   # strip SOH STX HOME … EOT

        if payload and payload[0] == _LF:
            # pt2: combine with buffered pt1
            if self._pending_pt1 is None:
                return {}
            result = self._process_pair(self._pending_pt1, payload)
            self._pending_pt1 = None
            return result

        # pt1: buffer it, wait for pt2
        self._pending_pt1 = payload
        return {}

    def _process_pair(self, pt1: list[int], pt2: list[int]) -> dict:
        updates: dict = {}
        try:
            A = chr(pt1[0])
            B = chr(pt1[1])
            FFF = bytes(pt1[7:10]).decode('ascii')
            GG  = bytes(pt1[10:12]).decode('ascii')
            HH  = bytes(pt1[14:16]).decode('ascii')

            J        = chr(pt2[1])
            KK       = bytes(pt2[2:4]).decode('ascii')
            time_str = bytes(pt2[5:16]).decode('ascii')
        except (IndexError, ValueError, UnicodeDecodeError):
            return {}

        if A == '0':
            # Ready at start — new heat announced
            try:
                ev, ht = int(FFF), int(GG)
            except ValueError:
                return {}
            if ev > 0 and ht > 0:
                tup = (ev, ht)
                if tup != self.last_event_sent:
                    self.last_event_sent = tup
                    updates['current_event'] = str(ev)
                    updates['current_heat']  = str(ht)
                    updates.update(self.reset_lanes())
                    updates['event_changed'] = tup

        elif A == '2' and B == 'S':
            # Start signal — mark all unfished lanes as running
            if not self._race_active:
                self._race_active = True
                for i in range(1, self.num_lanes + 1):
                    if not self.lane_times.get(i):
                        self.lane_running[i] = True
                        updates[f'lane_running{i}'] = True
                updates['dismiss_overlay'] = True

        elif A == '2' and B in ('I', 'A'):
            # Intermediate split (I) or finish (A)
            try:
                lane = int(J)
                lap  = int(KK)
                rank = int(HH.strip()) if HH.strip() else 0
            except ValueError:
                return {}
            t = _parse_time(time_str)
            if not t or lane < 1 or lane > self.num_lanes:
                return {}

            self.lane_times[lane] = t
            updates[f'lane_time{lane}'] = t

            if rank > 0:
                place = str(rank)
                self.lane_places[lane] = place
                updates[f'lane_place{lane}'] = place

            if B == 'I':
                self.lane_splits[lane] = lap
                updates[f'lane_splits{lane}'] = lap
            else:
                # Finish
                self.lane_running[lane] = False
                updates[f'lane_running{lane}'] = False

        elif A == '1':
            # Official end of heat
            self._race_active = False

        return updates
