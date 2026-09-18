from dataclasses import dataclass

from .base import ConsoleDecoder, SerialConfig

# ── Cipher ────────────────────────────────────────────────────────────────────
#
# Every raw byte from the RS-485 bus must be remapped through a stateful
# rotation-XOR cipher before interpretation.  The 32-entry mapping table is
# derived from the 256-char hex string embedded in ctsScoreboardasync.js.

_MAPPING_HEX = (
    "F37C65B454BD061AC3E2161EEBB26E8EEC95883E5CAB118EF3D7D3ACC6DA3754"
    "178C9A44414B16BC351AE48C30EA2D3839F009BCBC7F3AE4DECACED82AA0D794"
    "7A02E6B088BA6B4EA63D2E4463E1780A574169B4D0258F42023E04D0D0D19CF6"
    "FB0805F6E18DC550B61F577EC4FAEF9C9395C310EF23508067C46C28843F4A36"
)


def _build_mappings() -> list[int]:
    s = _MAPPING_HEX
    m = [0] * 32
    for i in range(16):
        m[i * 2 + 1] = int(s[i * 8 : i * 8 + 8], 16)
    for i in range(16):
        m[i * 2] = int(s[128 + i * 8 : 128 + i * 8 + 8], 16)
    return m


_MAPPINGS: list[int] = _build_mappings()


def _rot_l(x: int, n: int) -> int:
    n &= 31
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _rot_r(x: int, n: int) -> int:
    n &= 31
    return ((x >> n) | (x << (32 - n))) & 0xFFFFFFFF


@dataclass
class _RS:
    """Rotation-XOR remap state (resets on every high-bit byte)."""
    count:  int  = 0
    mapper: int  = 0
    is_odd: bool = False
    length: int  = 0


def _remap(raw: int, st: _RS) -> int:
    if raw > 127:
        st.count  = 0
        st.mapper = _MAPPINGS[raw & 31]
        st.is_odd = (raw % 2) == 1
        return raw
    if st.count == 0:
        st.length  = (raw ^ (st.mapper & 0x7F)) & 0xFFFFFFFF
        st.count  += 1
        return st.length & 0xFF
    rot = (st.length * st.count) & 0xFFFFFFFF
    xor = (_rot_r(st.mapper, rot) if st.is_odd else _rot_l(st.mapper, rot)) & 0x7F
    st.count += 1
    return (raw ^ xor) & 0xFF


# ── Scoreboard data model ─────────────────────────────────────────────────────

_BLANK_VALS = frozenset((15, 32))


class _Digit:
    __slots__ = ('value', 'dec_point')

    def __init__(self) -> None:
        self.value:     int  = 15    # 15 = blank/undefined
        self.dec_point: bool = False


class _Module:
    __slots__ = ('digits', 'univ', 'horn')

    def __init__(self) -> None:
        self.digits: list[_Digit] = [_Digit() for _ in range(31)]
        self.univ:   bool = False   # True → use module 0 as time source (lane is racing)
        self.horn:   bool = False


# ── Decoder ───────────────────────────────────────────────────────────────────

class CTSGen7Decoder(ConsoleDecoder):
    """Decoder for CTS Gen7 / WA-2 timing consoles.

    Serial protocol: 115 200 baud, 8-N-1, RS-485.
    The stream uses a rotation-XOR obfuscation cipher; packets are
    length-prefixed and verified with a running-sum checksum.

    See console_decoders/cts_gen7_serial.md for the full protocol reference.
    Sources: fabriziobertocci/coloradoScoreboard (ctsScoreboardasync.js, MIT).
    """

    def __init__(self, cfg: dict) -> None:
        self._serial_config = SerialConfig(baud=115200, bytesize=8, parity='N', stopbits=1)

        # Outer remap state (maintained across all bytes in the live stream)
        self._rmap = _RS()

        # Remapped bytes accumulating for the current in-progress packet
        self._rmap_buf: list[int] = []
        # Complete remapped packet snapshot waiting for the next feed() call
        self._pending:  list[int] = []

        # Scoreboard: 31 modules (0-30), each with 31 digits
        self._mod: list[_Module] = [_Module() for _ in range(31)]

        # ParseEnhancedByte parse state (persistent across packets)
        self._cur_mod:    int  = 0
        self._cur_dig:    int  = 0
        self._byte1:      bool = True
        self._in_cmd:     bool = False
        self._in_mod_cmd: bool = False
        self._mod_cmd:    list[int] = []

        # Which modules received digit updates in the current feed() call
        self._dirty: set[int] = set()

        # Cached scoreboard output (to emit only on change)
        self._lane_times:   dict[int, str]  = {}
        self._lane_places:  dict[int, str]  = {}
        self._lane_running: dict[int, bool] = {}
        self._running_time  = ''
        self.last_event_sent: tuple[int, int] = (0, 0)
        self.lane_seed_times: dict[int, str] = {}

        self.configure(cfg)

    # ── ConsoleDecoder interface ───────────────────────────────────────────────

    @property
    def serial_config(self) -> SerialConfig:
        return self._serial_config

    @property
    def post_open_bytes(self) -> bytes:
        """Gen7 requires this 4-byte handshake immediately after port open."""
        return bytes([0x80, 0x1F, 0x0F, 0x02])

    def is_packet_start(self, raw: int, buffer: list[int]) -> bool:
        """Remap raw byte; flush accumulated buffer when a new high-bit byte arrives."""
        remapped = _remap(raw, self._rmap)
        if remapped & 0x80:
            # High-bit byte = new packet start; save current buffer for feed()
            self._pending = self._rmap_buf[:]
            self._rmap_buf = [remapped]
            return bool(self._pending)
        self._rmap_buf.append(remapped)
        return False

    @property
    def max_packet_bytes(self) -> int:
        return 260

    def configure(self, cfg: dict) -> None:
        self.num_lanes = int(cfg.get('num_lanes', 10))

    def set_seed_times(self, times: dict) -> None:
        self.lane_seed_times = dict(times)

    def get_lane_time(self, lane_idx: int) -> str:
        return self._lane_times.get(lane_idx, '')

    def get_lane_place(self, lane_idx: int) -> str:
        return self._lane_places.get(lane_idx, ' ')

    def reset_lanes(self) -> dict:
        updates: dict = {}
        for i in range(1, self.num_lanes + 1):
            self._lane_times[i]   = ''
            self._lane_places[i]  = ' '
            self._lane_running[i] = False
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
        return updates

    def race_finished(self) -> bool:
        any_placed = False
        for i in range(1, self.num_lanes + 1):
            if self._lane_running.get(i):
                return False
            t = self._lane_times.get(i, '')
            if t:
                if self._lane_places.get(i, ' ') == ' ':
                    return False
                any_placed = True
        return any_placed

    # ── feed ──────────────────────────────────────────────────────────────────

    def feed(self, raw_packet: list[int]) -> dict:
        """Decode one Gen7 packet (outer-remapped, length-framed).

        raw_packet is ignored — the decoder uses the remapped bytes captured
        during is_packet_start() calls instead.
        """
        packet = self._pending
        self._pending = []
        if len(packet) < 3 or not (packet[0] & 0x80):
            return {}

        expected_len = packet[1]
        if len(packet) < expected_len + 3:
            return {}

        # Verify outer running-sum checksum (masked to 7 bits)
        cs = 0
        for b in packet[:expected_len + 2]:
            cs = (cs + b) & 0xFF
        if (cs & 0x7F) != (packet[expected_len + 2] & 0x7F):
            return {}

        payload = packet[2 : expected_len + 2]

        # Special multi-pool header: 0x9F + 0x11 or 0x13
        # Payload (from byte index 1 onward) undergoes a second independent remap.
        if packet[0] == 0x9F and len(payload) >= 2 and payload[0] in (0x11, 0x13):
            payload = self._secondary_remap(payload)
            if payload is None:
                return {}

        self._dirty.clear()
        for b in payload:
            self._parse_byte(b)

        return self._collect_updates()

    # ── Secondary remap (special pool header) ────────────────────────────────

    def _secondary_remap(self, payload: list[int]) -> list[int] | None:
        """Apply the second remap pass used for multi-pool 0x9F packets."""
        if len(payload) < 3:
            return None
        st = _RS()
        # payload[0]=subtype (0x11/0x13), payload[1]=pool byte, payload[2]=first data byte.
        # Force high bit on payload[2] to reset cipher state, matching JS behaviour.
        src0 = (payload[2] | 0x80) & 0xFF
        out: list[int] = []
        first = _remap(src0, st)
        out.append(first)
        cs = first
        for i in range(3, len(payload) - 1):
            b = _remap(payload[i], st)
            if i != 3:          # skip the second remapped byte (mirrors JS idx2!=1 skip)
                out.append(b)
            cs = (cs + b) & 0xFF
        ckb = _remap(payload[-1], st)
        if (cs & 0x7F) != ckb:
            return None
        return out

    # ── ParseEnhancedByte ─────────────────────────────────────────────────────

    def _parse_byte(self, inc: int) -> None:
        """Update module/digit state from one decoded byte."""
        if inc & 0x80:
            # Module header byte: end of previous module, start of new one
            if self._in_mod_cmd and self._mod_cmd:
                self._parse_command(self._mod_cmd)

            self._cur_mod = inc & 31
            if self._cur_mod < 31:
                m = self._mod[self._cur_mod]
                m.univ = bool(inc & 0x40)
                m.horn = bool(inc & 0x20)
            self._byte1      = True
            self._in_cmd     = False
            self._in_mod_cmd = (self._cur_mod == 31)
            if self._in_mod_cmd:
                self._mod_cmd = []

        elif self._in_mod_cmd:
            self._mod_cmd.append(inc)

        elif not self._in_cmd:
            if self._byte1:
                # Descriptor byte: digit index + flags
                self._cur_dig = inc & 31
                if self._cur_mod < 31 and self._cur_dig < 31:
                    self._mod[self._cur_mod].digits[self._cur_dig].dec_point = bool(inc & 0x40)
                self._byte1 = False
                if self._cur_dig == 31:
                    self._in_cmd = True
            else:
                # Value byte
                val = inc & 0x7F
                if val == 0:
                    val = 32
                if self._cur_mod < 31 and self._cur_dig < 31:
                    self._mod[self._cur_mod].digits[self._cur_dig].value = val
                    self._dirty.add(self._cur_mod)
                self._byte1 = True

    # ── Non-module command parsing (module 31) ────────────────────────────────

    def _parse_command(self, cmd: list[int]) -> None:
        """Command 18 carries meet/event/swimmer metadata sent natively by Gen7."""
        if len(cmd) < 3 or cmd[0] != 18:
            return
        # pool = cmd[1] - 1   # 0-based pool; we only support pool 0 for now
        # sub-case dispatch left as a future extension (swimmer names, meet title)

    # ── Scoreboard readout helpers ────────────────────────────────────────────

    def _char(self, mod_idx: int, dig_idx: int) -> str:
        if mod_idx >= 31 or dig_idx >= 31:
            return ' '
        v = self._mod[mod_idx].digits[dig_idx].value
        return ' ' if v in _BLANK_VALS else chr(v)

    def _get_time(self, mod_idx: int, start: int = 4, count: int = 6) -> str:
        """Build a formatted time string from module digits.

        Mirrors GetTime() in ctsScoreboardasync.js:
          digit at start+2: insert ':' before it when dec_point lit (or module 0)
          digit at start+3: insert '.' after it when dec_point lit (or module 0)
          digit at start+1: insert '.' after it when dec_point lit
        """
        m   = self._mod[mod_idx]
        src = 0 if m.univ else mod_idx
        out = ''
        for i in range(count):
            dig = start + i
            if dig >= 31:
                break
            ch = self._char(src, dig)
            dp = m.digits[dig].dec_point
            if i == 2:
                if mod_idx == 0 or (dp and self._char(src, dig - 1) != ' '):
                    out += ':'
                out += ch
            elif i == 3 and (dp or mod_idx == 0):
                out += ch + '.'
            elif i == 1 and dp:
                out += ch + '.'
            else:
                out += ch
        return out.strip()

    def _get_digits_int(self, mod_idx: int, start: int, count: int) -> int:
        s = ''.join(self._char(mod_idx, start + i) for i in range(count)).strip()
        return int(s) if s.isdigit() else 0

    # ── Update collection ─────────────────────────────────────────────────────

    def _collect_updates(self) -> dict:
        updates: dict = {}

        # Running time — module 0
        if 0 in self._dirty:
            t = self._get_time(0, start=4, count=6)
            if t != self._running_time:
                self._running_time = t
                updates['running_time'] = t
            # Lanes with univ=True show module 0's time; mark them dirty too
            # so their displayed time is always in sync with the running clock.
            for lane in range(1, min(self.num_lanes + 1, 11)):
                if self._mod[lane].univ:
                    self._dirty.add(lane)

        # Lanes 1-10 — modules 1-10
        for lane in range(1, min(self.num_lanes + 1, 11)):
            if lane not in self._dirty:
                continue
            m       = self._mod[lane]
            t       = self._get_time(lane, start=4, count=6)
            place   = self._char(lane, 3)
            # Univ flag = True means the lane is actively racing (showing running time)
            running = m.univ

            if t != self._lane_times.get(lane, ''):
                self._lane_times[lane] = t
                updates[f'lane_time{lane}'] = t
            if place != self._lane_places.get(lane, ' '):
                self._lane_places[lane] = place
                updates[f'lane_place{lane}'] = place
            if running != self._lane_running.get(lane, False):
                prev = self._lane_running.get(lane, False)
                self._lane_running[lane] = running
                updates[f'lane_running{lane}'] = running
                if running and not prev:
                    updates['dismiss_overlay'] = True

        # Event / heat — module 12
        if 12 in self._dirty:
            ev = self._get_digits_int(12, 1, 3)
            ht = self._get_digits_int(12, 7, 3)
            if ev > 0 and ht > 0:
                updates['current_event'] = str(ev)
                updates['current_heat']  = str(ht)
                tup = (ev, ht)
                if tup != self.last_event_sent:
                    self.last_event_sent = tup
                    lane_resets = self.reset_lanes()
                    self.lane_seed_times.clear()
                    updates.update(lane_resets)
                    updates['event_changed'] = tup

        return updates
