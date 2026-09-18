import re

from .base import ConsoleDecoder, SerialConfig

_SYN = 0x16
_STX = 0x02
_EOT = 0x04

# MM:SS.CC or SS.CC or SS.T — accept varying precision
_TIME_RE = re.compile(r'^(\d+):(\d{2})\.(\d{1,2})$|^(\d{2})\.(\d{1,2})$')


def _parse_time(s: str) -> str:
    """Normalise a raw Omnisport time string to M:SS.CC format."""
    s = s.strip()
    m = _TIME_RE.match(s)
    if not m:
        return s
    if m.group(1) is not None:
        mins = m.group(1)
        secs = m.group(2)
        frac = m.group(3).ljust(2, '0')
    else:
        mins = '0'
        secs = m.group(4)
        frac = m.group(5).ljust(2, '0')
    prefix = '' if mins == '0' else mins + ':'
    return f'{prefix}{secs}.{frac}'


class Omnisport2000Decoder(ConsoleDecoder):
    """Decoder for Daktronics Omnisport 2000 timing consoles.

    Serial protocol: 19200 baud, 8-N-1.
    Packets are ASCII text framed by SYN (0x16) / STX (0x02) / EOT (0x04).
    Split times are transmitted natively in the serial stream.

    See info/omnisport_2000_serial.md for the full protocol reference.
    """

    def __init__(self, cfg: dict):
        self._serial_config = SerialConfig(baud=19200, bytesize=8, parity='N', stopbits=1)
        self._in_payload   = False   # True once STX seen, False after EOT
        self.lane_times:   dict[int, str]  = {}
        self.lane_places:  dict[int, str]  = {}
        self.lane_running: dict[int, bool] = {}
        self.lane_splits:  dict[int, int]  = {}
        self.running_time  = ''
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
        # SYN + up to ~32 ASCII chars + EOT + CR
        return 64

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
        the operator has no other way to say so. The next `s` packet overwrites this
        with the console's own number, which is the right outcome — the console is
        the authority whenever it speaks.
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
        """Decode one Omnisport 2000 packet (SYN … STX payload EOT CR)."""
        updates: dict = {}

        # Extract payload between STX and EOT
        try:
            stx = packet.index(_STX)
        except ValueError:
            return updates
        try:
            eot = packet.index(_EOT, stx + 1)
        except ValueError:
            eot = len(packet)

        payload = bytes(packet[stx + 1:eot]).decode('ascii', errors='ignore').strip()
        if not payload:
            return updates

        prefix = payload[0]
        body   = payload[1:].strip()

        if prefix == 't':
            # Running time: t<MM:SS.T>
            self.running_time = _parse_time(body)
            updates['running_time'] = self.running_time

        elif prefix == 'l':
            # Lane finish: l<lane> <place> <MM:SS.CC>
            parts = body.split()
            if len(parts) >= 3:
                lane  = int(parts[0])
                place = parts[1]
                t     = _parse_time(parts[2])
                self.lane_times[lane]   = t
                self.lane_places[lane]  = place
                self.lane_running[lane] = False
                updates[f'lane_time{lane}']    = t
                updates[f'lane_place{lane}']   = place
                updates[f'lane_running{lane}'] = False

        elif prefix == 's':
            # Split: s<lane> <place> <MM:SS.CC> <laps>
            parts = body.split()
            if len(parts) >= 4:
                lane  = int(parts[0])
                place = parts[1]
                t     = _parse_time(parts[2])
                laps  = int(parts[3])
                self.lane_times[lane]  = t
                self.lane_places[lane] = place
                self.lane_splits[lane] = laps
                updates[f'lane_time{lane}']   = t
                updates[f'lane_place{lane}']  = place
                updates[f'lane_splits{lane}'] = laps

        elif prefix == 'r':
            # Race reset
            updates.update(self.reset_lanes())

        return updates
