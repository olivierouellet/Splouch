# CTS Gen6 (System 5 / System 6 / Gen7 Legacy) — Serial Protocol Reference

> **Status: Fully documented** from direct observation and community sources.
> This decoder also covers System 5 and Gen7 Legacy, which share the same wire protocol.
> Implemented in `console_decoders/cts_gen6.py` (`CTSGen6Decoder`).

---

## Hardware Interface

| Parameter | Value |
| --------- | ----- |
| Interface | **RS-232** |
| Baud rate | **9 600** |
| Data bits | 8 |
| Parity | **Even** |
| Stop bits | 1 |

---

## Packet Structure

Every packet starts with a **channel byte** whose high bit (bit 7) is set.
Bit 7 being set is the packet boundary marker — the worker calls `is_packet_start`
on every incoming byte and flushes the current buffer when it sees a high-bit byte.

Maximum packet length: **9 bytes** (1 channel byte + up to 8 data bytes).

### Channel byte layout

```text
Bit 7:   1           (always set — marks packet start)
Bit 6:   running     (1 = lane is actively racing / time is running)
Bits 5–1: channel    (5-bit encoded address — see decode below)
Bit 0:   format      (1 = format-display variant — ignored by parser)
```

### Channel address decoding

```python
channel = ((byte & 0x3E) >> 1) ^ 0x1F
```

### Data bytes — nibble encoding

Each data byte encodes one display digit in its high nibble and carries
its own slot index in the high nibble:

```python
slot = (byte >> 4) & 0x0F  # which digit slot (0–7)
value = byte & 0x0F  # raw nibble value
```

Converting a nibble to a display character:

```python
def hex_to_digit(c):
    c = (c & 0x0F) ^ 0x0F
    return " " if c > 9 else str(c)
```

### Time formatting (6 digits → string)

Digits at slots 2–7 encode a time in `MM:SS.HH` format:

```python
def format_time(a, b, c, d, e, f):  # slots 2,3,4,5,6,7
    m1, m2 = hex_to_digit(a), hex_to_digit(b)  # minutes
    s1, s2 = hex_to_digit(c), hex_to_digit(d)  # seconds
    h1, h2 = hex_to_digit(e), hex_to_digit(f)  # hundredths
    if s1 == s2 == h1 == h2 == " ":
        return ""  # blank = no time
    ...
```

---

## Channel Reference Table

The console transmits packets whose first byte encodes the channel address.
Decoded channel = `((byte & 0x3E) >> 1) ^ 0x1F`.
Bit 6 of the first byte = running/finish flag. Bit 0 = format-display flag.

| Channel Name | CH (hex) | Decoded addr | Value (running) | Format (running) | HEX | Value (stopped) | Format (stopped) | HEX |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Running Time | 00 | 0 | | | | 190 | | BE |
| Lane 1 * | 01 | 1 | 252 | 253 | FC | 188 | 189 | BC |
| Lane 2 | 02 | 2 | 250 | 251 | FA | 186 | 187 | BA |
| Lane 3 | 03 | 3 | 248 | 249 | F8 | 184 | 185 | B8 |
| Lane 4 | 04 | 4 | 246 | 247 | F6 | 182 | 183 | B6 |
| Lane 5 | 05 | 5 | 244 | 245 | F4 | 180 | 181 | B4 |
| Lane 6 | 06 | 6 | 242 | 243 | F2 | 178 | 179 | B2 |
| Lane 7 | 07 | 7 | 240 | 241 | F0 | 176 | 177 | B0 |
| Lane 8 | 08 | 8 | 238 | 239 | EE | 174 | 175 | AE |
| Lane 9 | 09 | 9 | 236 | 237 | EC | 172 | 173 | AC |
| Lane 10 | 0A | 10 | 234 | 235 | EA | 170 | 171 | AA |
| Length/Record | 0B | 11 | 232 | 233 | E8 | 168 | 169 | A8 |
| Event/Heat | 0C | 12 | 230 | 231 | E6 | 166 | 167 | A6 |
| Home/Guest/Guest * | 0D | 13 | 228 | 229 | E4 | 164 | 165 | A4 |
| Place | 0E | 14 | 226 | 227 | E2 | 162 | 163 | A2 |
| Scroll | 0F | 15 | 224 | 225 | E0 | 160 | 161 | A0 |
| (unused) | 10 | 16 | 222 | 223 | DE | 158 | 159 | 9E |
| Lane 1 Mirror | 11 | 17 | 220 | 221 | DC | 156 | 157 | 9C |
| TV / Broadcast | 12 | 18 | 218 | 219 | DA | 154 | 155 | 9A |
| Home/G1/G2/G3 | 13 | 19 | 216 | 217 | D8 | 152 | 153 | 98 |
| Home/Guest 1 | 14 | 20 | 214 | 215 | D6 | 150 | 151 | 96 |
| Guest 2/Guest 3 | 15 | 21 | 212 | 213 | D4 | 148 | 149 | 94 |
| Time | 16 | 22 | 210 | 211 | D2 | 146 | 147 | 92 |
| Lane 11 | 17 | 23 | 208 | 209 | D0 | 144 | 145 | 90 |
| Lane 12 | 18 | 24 | 206 | 207 | CE | 142 | 143 | 8E |

The two columns labelled "running" carry bit 6 set (0x40); the two "stopped" columns have bit 6 clear.

### Scoreboard Blank mode

In **Scoreboard → Scoreboard Blank** the console keeps the clock on the board: a real
capture (`tests/fixtures/cts_gen6_blank.raw`) carries the same digits on lane 3,
Scroll (15) and Time (22), with lane 3's lane number and place blank. Read naively
that is a `0:39.00` in lane 3. The decoder treats a lane with no lane number, no place
and a time equal to channel 22's as showing the time of day, and reports no time for
it (`_shows_time_of_day`). A place is required to be a result, so a real finish that
happens to match the clock is unaffected. **Total Blank** (pressed twice) sends every
channel empty and needs nothing special.
The "Format" variant has bit 0 set (format-display flag) and is ignored by the parser.

---

## Implementation Status

### Console-sourced keys

Keys derived directly from bytes received on the serial port.

| Decoded addr | Channel name | `update` key(s) emitted | Status |
| --- | --- | --- | --- |
| 0 | Running Time | `running_time` | Implemented |
| 1–10 | Lane 1–10 | `lane_running#`, `lane_place#`, `lane_time#` | Implemented |
| 11 | Length/Record | `length_record` | Implemented |
| 12 | Event/Heat | `current_event`, `current_heat` | Implemented |
| 13 | Home/Guest/Guest* | — | Not parsed |
| 14 | Place | `global_place` | Implemented |
| 15 | Scroll | — | Not parsed |
| 16 | (unused) | — | Not parsed |
| 17 | Lane 1 Mirror | — | Not parsed |
| 18 | TV / Broadcast | — | Not parsed |
| 19 | Home/G1/G2/G3 | — | Not parsed |
| 20 | Home/Guest 1 | — | Not parsed |
| 21 | Guest 2/Guest 3 | — | Not parsed |
| 22 | Time | `channel_22_time` | Implemented |
| 23 | Lane 11 | `lane_running11`, `lane_place11`, `lane_time11` | Implemented |
| 24 | Lane 12 | `lane_running12`, `lane_place12`, `lane_time12` | Implemented |

### Server-computed keys

Keys not received from the console but computed by the server from other data sources.

| `update` key | Source | Description |
| --- | --- | --- |
| `event_name` | Lenex / HyTek file | Human-readable event name looked up from the start list |
| `lane_name#` | Lenex / HyTek file | Swimmer name for lane #, refreshed on each new event/heat |
| `lane_club#` | Lenex / HyTek file | Club name for lane #, refreshed on each new event/heat |
| `lane_delta#` | Lenex / HyTek file + finish time | Difference between finish time and seed time; HTML coloured green (faster) or grey (slower) |

---

## Notes on Unimplemented Channels

- **Home/Guest (0x0D, 0x13, 0x14, 0x15)** — team score segments used in certain swim meets.
- **Scroll (0x0F)** — drives a scrolling text segment on the physical board; no equivalent in the software scoreboard.
- **Lane 1 Mirror / Team Scores (0x11)** — mirrors lane 1 time and place during a race; reverts to team scores (0x0D) after reset.
- **TV / Broadcast (0x12)** — shows running time, lead split, and winning finish time only (does not cycle through all finishers). Intended for televised broadcasts; not needed for local display.
- **Time (0x16)** — separate time display segment. Displayed in the console.html header. Data format to be confirmed from observation.

---

## Key Differences from Gen7

| Aspect | Gen6 | Gen7 |
| --- | --- | --- |
| Baud rate | 9 600 | 115 200 |
| Bus | RS-232 | RS-485 |
| Parity | Even | None |
| Framing | Continuous byte stream (high bit = boundary) | Packetized (length + checksum) |
| Obfuscation | None | Rotation-XOR cipher |
| Structure | 25 channels × 8 digit slots | 31 modules × 31 digits |
| Swimmer names | Not present | Native (via command packets) |
| Max lanes | 12 (channels 23, 24) | 10+ (modules 1–10+) |

---

## Sources

- CTS Gen6 serial protocol: direct observation and community documentation
- Protocol interpretation: [hwbrill/vsCTS](https://github.com/hwbrill/vsCTS/blob/master/README.md)
- Protocol interpretation: [Marco's Corner](https://marcoscorner.walther-family.org/2015/07/colorado-timing-console-scoreboard-protocol/)
- Colorado Time Systems product page: [coloradotime.com](https://coloradotime.com)
