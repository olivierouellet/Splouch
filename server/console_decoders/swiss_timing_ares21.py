import re

from .base import ConsoleDecoder, SerialConfig

_SOH = 0x01
_STX = 0x02
_EOT = 0x04

_HEADER_TIME  = '0040100000'
_HEADER_EVENT = '0040100069'

# Lane name codes:   0040100200 + 36*(lane-1), lanes 1-10
# Lane result codes: 0040100220 + 36*(lane-1), lanes 1-10
_NAME_CODES   = {f'0040100{200 + 36 * (i - 1):03d}': i for i in range(1, 11)}
_RESULT_CODES = {f'0040100{220 + 36 * (i - 1):03d}': i for i in range(1, 11)}

_TIME_RE = re.compile(r'\d{1,2}:\d{2}\.\d{1,2}|\d{2}\.\d{1,2}')
_NORM_RE = re.compile(r'^(\d+):(\d{2})\.(\d{1,2})$|^(\d{2})\.(\d{1,2})$')


def _parse_time(s: str) -> str:
    s = s.strip()
    m = _NORM_RE.match(s)
    if not m:
        return s
    if m.group(1) is not None:
        mins = int(m.group(1))
        secs = m.group(2)
        frac = m.group(3).ljust(2, '0')
    else:
        mins = 0
        secs = m.group(4)
        frac = m.group(5).ljust(2, '0')
    prefix = '' if mins == 0 else f'{mins}:'
    return f'{prefix}{secs}.{frac}'


def _parse_event_heat(data: str) -> tuple[int, int]:
    ev_m = re.search(r'[Ee]vent\s+(\d+)', data)
    ht_m = re.search(r'[Hh]eat\s+(\d+)', data)
    ev = int(ev_m.group(1)) if ev_m else 0
    ht = int(ht_m.group(1)) if ht_m else 0
    return ev, ht


def _parse_result(data: str) -> tuple[str, str]:
    """Return (place, time) from a result payload like '1 00:54.32'."""
    m = _TIME_RE.search(data)
    if not m:
        return ' ', ''
    t = _parse_time(m.group())
    before = data[:m.start()]
    tokens = before.split()
    place = tokens[-1] if tokens and tokens[-1].isdigit() else ' '
    return place, t


class Ares21Decoder(ConsoleDecoder):
    """Decoder for Swiss Timing Omega Ares 21 timing consoles.

    Serial protocol: 9600 baud, 8-N-1, RS-485.
    Venus ERTD scoreboard format: SOH + 10-digit header + STX + ASCII data + EOT.

    See console_decoders/swiss_timing_ares21_serial.md for the full protocol reference.
    Source: fvishram/SRAYSScoreboard (AresDataHandler.cs, MIT).
    """

    def __init__(self, cfg: dict) -> None:
        self._serial_config = SerialConfig(baud=9600, bytesize=8, parity='N', stopbits=1)
        self.lane_times:   dict[int, str]  = {}
        self.lane_places:  dict[int, str]  = {}
        self.lane_running: dict[int, bool] = {}
        self.running_time  = ''
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
        return 128

    def configure(self, cfg: dict) -> None:
        self.num_lanes = int(cfg.get('num_lanes', 10))

    def set_seed_times(self, times: dict) -> None:
        self.lane_seed_times = dict(times)

    def get_lane_time(self, lane_idx: int) -> str:
        return self.lane_times.get(lane_idx, '')

    def get_lane_place(self, lane_idx: int) -> str:
        return self.lane_places.get(lane_idx, ' ')

    def reset_lanes(self) -> dict:
        updates: dict = {}
        for i in range(1, self.num_lanes + 1):
            self.lane_times[i]   = ''
            self.lane_places[i]  = ' '
            self.lane_running[i] = False
            updates[f'lane_time{i}']    = ''
            updates[f'lane_place{i}']   = ' '
            updates[f'lane_running{i}'] = False
            updates[f'lane_delta{i}']   = ''
            # This console reports no lap number, so nothing here ever raises the
            # count — but the key is part of the reset contract every other decoder
            # honours, and a board that carries a lap from a previous console (or a
            # custom decoder swapped in at runtime) must not keep it across a heat.
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
        """Decode one Ares 21 Venus ERTD packet (SOH header STX data EOT)."""
        updates: dict = {}
        if not packet or packet[0] != _SOH:
            return updates

        try:
            stx = packet.index(_STX, 1)
        except ValueError:
            return updates

        header = bytes(packet[1:stx]).decode('ascii', errors='ignore').strip()
        if len(header) != 10:
            return updates

        try:
            eot = packet.index(_EOT, stx + 1)
        except ValueError:
            eot = len(packet)

        data = bytes(packet[stx + 1:eot]).decode('ascii', errors='ignore').strip()

        if header == _HEADER_TIME:
            t = _parse_time(data)
            if t != self.running_time:
                self.running_time = t
                updates['running_time'] = t
            if not self._race_active and t:
                self._race_active = True
                for i in range(1, self.num_lanes + 1):
                    if not self.lane_times.get(i):
                        self.lane_running[i] = True
                        updates[f'lane_running{i}'] = True
                updates['dismiss_overlay'] = True

        elif header == _HEADER_EVENT:
            ev, ht = _parse_event_heat(data)
            if ev > 0 and ht > 0:
                updates['current_event'] = str(ev)
                updates['current_heat']  = str(ht)
                tup = (ev, ht)
                if tup != self.last_event_sent:
                    self.last_event_sent = tup
                    updates.update(self.reset_lanes())
                    updates['event_changed'] = tup

        elif header in _RESULT_CODES:
            lane = _RESULT_CODES[header]
            if lane <= self.num_lanes:
                place, t = _parse_result(data)
                if t:
                    self.lane_times[lane]   = t
                    self.lane_places[lane]  = place
                    self.lane_running[lane] = False
                    updates[f'lane_time{lane}']    = t
                    updates[f'lane_place{lane}']   = place
                    updates[f'lane_running{lane}'] = False

        # _NAME_CODES: swimmer names are supplied by Lenex; Ares names ignored.

        return updates
