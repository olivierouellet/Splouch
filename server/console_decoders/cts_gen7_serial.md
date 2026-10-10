# CTS Gen7 Serial (WA-2) — Protocol Reference

> **Status: Fully documented** from community reverse-engineering of
> `fabriziobertocci/coloradoScoreboard` (`src/ctsScoreboardasync.js`).
> Official documentation: Colorado Time Systems F1034 manual (Rev 202405, not publicly accessible).

---

## Hardware Interface

| Parameter | Value |
|-----------|-------|
| Interface | **RS-485** (differential, multi-node bus) — not RS-232 |
| Baud rate | **115 200** |
| Data bits | 8 |
| Parity | **None** |
| Stop bits | 1 |
| Connector | CONXALL/SWITCHCRAFT 3280-4PG-315 (4-pin male) |
| Adapter    | FTDI USB-RS485-WE-1800-BT |

### Wiring (Male plug → FTDI adapter)

```text
CTS pin 2 (Orange, Data+) → FTDI TXD+/RXD+ (orange/yellow wire)
CTS pin 3 (Yellow, Data−) → FTDI TXD−/RXD− 
CTS pin 4 (Black,  GND)   → FTDI GND
```

Socket pinout (female socket on console, rear view):

```text
TOP
.---v---.
/         \
| o 1   o 4 |
|           |
| o 2   o 3 |
\         /
  '-----'
```

---

## Initialization

Immediately after opening the port, the host must send 4 bytes to request data:

```python
port.write(bytes([0x80, 0x1F, 0x0F, 0x02]))
```

---

## Two-Layer Decoding

### Layer 1 — Stream deobfuscation (rotation-XOR cipher)

Every raw byte from the serial port must be remapped before interpretation.
The cipher uses a 32-entry lookup table of 32-bit words initialized from a
256-character hex string. The state is per-stream and resets on any high-bit byte.

**Mapping table** (hex string, 256 chars):

```text
F37C65B454BD061AC3E2161EEBB26E8EEC95883E5CAB118EF3D7D3ACC6DA3754
178C9A44414B16BC351AE48C30EA2D3839F009BCBC7F3AE4DECACED82AA0D794
7A02E6B088BA6B4EA63D2E4463E1780A574169B4D0258F42023E04D0D0D19CF6
FB0805F6E18DC550B61F577EC4FAEF9C9395C310EF23508067C46C28843F4A36
```

Table initialization: first 128 hex chars → `mappings[1,3,5,…,31]` (odd);
last 128 hex chars → `mappings[0,2,4,…,30]` (even).

**Per-byte remap algorithm:**

```python
def remap_byte(src, state):
    if src > 127:  # high-bit byte: reset state
        state.count = 0
        state.mapper = mappings[src & 31]
        state.is_odd = (src % 2) == 1
        return src  # high-bit bytes pass through unchanged
    elif state.count == 0:  # first data byte after high-bit
        state.map_len = (src ^ (state.mapper & 0x7F)) & 0xFFFFFFFF
        state.count += 1
        return state.map_len & 0xFF
    else:  # subsequent data bytes
        rot = (state.map_len * state.count) & 0xFFFFFFFF
        if state.is_odd:
            xor_val = rotate_right32(state.mapper, rot) & 0x7F
        else:
            xor_val = rotate_left32(state.mapper, rot) & 0x7F
        state.count += 1
        return (src ^ xor_val) & 0xFF
```

### Layer 2 — Packet framing (after remap)

State machine operating on remapped bytes:

```text
IDLE
  byte & 0x80 != 0  →  store in buffer[0], checksum = raw_src & 0xFF
                     →  WAITING_LENGTH

WAITING_LENGTH
  next byte = expected_length
  checksum += byte; dataCount = 0  →  IN_PACKET

IN_PACKET
  each byte: buffer[writePtr++] = byte; checksum += byte; dataCount++
  when dataCount > expected_length:     # this byte is the checksum
    if (checksum & 0x7F) == byte  →  packet valid
    else                          →  discard  →  IDLE
```

**Special multi-pool header:** if `buffer[0] == 0x9F` and `buffer[1] in (0x11, 0x13)`,
the payload (starting at buffer[3]) undergoes a second independent remap pass.
`buffer[2] - 1` is the pool number (0-based). Up to 4 pools supported.

---

## Layer 3 — ParseEnhancedByte: module/digit stream

After both remap passes, bytes describe a scoreboard as **modules** of **digits**.
The stream starts at the packet's own header byte (`buffer[0]`): it is the module
header for the digits that follow. Data bytes never carry bit 7 after the remap, so
one packet is one module. A `0x9F` multi-pool packet is the exception — its
second-pass output carries its own module header, and the outer one is not parsed.

### Module header byte (bit 7 = 1)

```text
Bit 7:    1   (marks module header)
Bits 4-0: module number (0–30); 31 = non-module command
Bit 6:    Universal flag (use module 0 as time display fallback)
Bit 5:    Horn active
```

### Digit descriptor + value pair (bit 7 = 0)

Two bytes per digit, alternating:

**Descriptor byte:**

```text
Bits 4-0: digit index (0–30); 31 = enter command mode (ignore following value byte)
Bit 6:    decimal point lit
Bit 5:    segment-mapped flag
```

**Value byte:**

```text
Bits 6-0: digit value; 0 is stored as 32 (space)
```

**DataToChar:** value 0 or 15 → `' '`; otherwise `chr(value)`.

---

## Module Map

| Module | Content |
|--------|---------|
| 0      | Universal / fallback display (used when Univ flag is set on another module) |
| 1–10   | Lane timing displays (lanes 1–10) |
| 12     | Event and heat numbers |
| 15     | State detection (reset-dots sentinel) |
| 22     | Time-of-day display |
| 31     | Non-module command block |

### Module 12 — Event / Heat

```text
Event number: digits 1, 3, 4  (3 digits → integer; GetDigits steps over digit 2)
Heat  number: digits 7, 8, 9  (3 digits → integer)
```

Changes trigger `EventChange` / `HeatChange` events.

### Modules 1–10 — Lane data

| Digit offset (from 0) | Content |
|-----------------------|---------|
| 0–1 | Lane number |
| 1 | Decimal point = reset indicator |
| 2–3 | Place (two-character field) |
| 4–9 | Time: `M M : S S . H H` (6 digits) |

Time format produced by `GetTime(pool, module, startDigit=4, count=6)`:

- `startDigit+2`: insert `':'` if decimal point lit at that digit
- `startDigit+3`: insert `'.'` after digit if decimal point lit
- Result: `MM:SS.HH` or `M:SS.HH`

Beyond 6 digits (`count > 6`): additional digits encode **split time** data.

### Module 0 — Running time

`GetTime(pool, 0, startDigit=4, count=30)` — reads up to 30 digits;
decimal points determine colon/dot insertion. Running time is in
digits 4–9 as `MM:SS.d` (tenths).

---

## Scoreboard State Detection

| State | Condition |
|-------|-----------|
| Reset | Any of modules 1–9 has `digit[1].decPoint == True`, or module 15 `digit[1].decPoint == True` |
| Running | Sport is swimming and previous state was not None |
| BlankWithTime | All lanes blank AND module 22 digits 5–6 are non-blank |
| TotalBlank | All lanes blank AND no time-of-day showing |

---

## Non-Module Command Packets (Module 31)

Command `id=18` carries swimmer metadata transmitted natively by the Gen7:

| Sub-case | Content | Byte layout |
|----------|---------|-------------|
| 1 | Meet title | `cmd[3]` = length, `cmd[4…]` = UTF-8 |
| 2 | Start list header | `cmd[3]<<8|cmd[4]` = event#, `cmd[5]<<8|cmd[6]` = heat#, `cmd[7]` = title length, `cmd[8…]` = event title |
| 3 | Swimmer | `cmd[3]` = lane index (0-based), `cmd[4]` = last name length, `cmd[5]` = team length, `cmd[7…]` = last name, then team |
| 4 | Event finalize | marks heat as having Gen7 data |

**UTF-8 escape:** byte `0x7F` in string payloads means the next byte should be OR'd with `0x80` to recover the original value.

**Implementation note:** Command 18 is intentionally not implemented. The application already receives swimmer names, event titles, and seed times from the Lenex/HyTek file via the meet-management integration. Command 18 exists for standalone scoreboards that have no such integration.

---

## Split Times

Each split is stored as an additional group of 6 digits immediately following the finish-time digits in the lane module. The format is the same `M:SS.cc` as the finish time (centisecond precision). For a 200 m race in a 50 m pool, four split groups would occupy digit positions 10–15, 16–21, 22–27, and 28–33.

Not yet implemented — a live multi-length capture is needed to verify the exact digit positions and confirm how many valid groups the console transmits before the entry is finalized.

---

## Key Differences from Gen6

| Aspect | Gen6 | Gen7 |
|--------|------|------|
| Baud rate | 9 600 | 115 200 |
| Bus | RS-232 | RS-485 |
| Parity | Even | None |
| Framing | Continuous byte stream | Packetized (length + checksum) |
| Obfuscation | None | Rotation-XOR cipher |
| Structure | 32 channels × 8 positions | 31 modules × 31 digits |
| Swimmer names | Not present | Native (via command packets) |
| Meet / event title | Not present | Native |
| Max lanes | 12 (channels 17, 18) | 10+ (modules 1–10+) |
| Pool support | 1 | Up to 4 |

---

## Implementation Status

Implemented in `console_decoders/cts_gen7.py` (`CTSGen7Decoder`).

| Feature | Status |
| ------- | ------ |
| Stream deobfuscation (rotation-XOR cipher) | Done |
| Packet framing (length + running-sum checksum) | Done |
| Secondary remap for multi-pool 0x9F packets | Done |
| Module/digit parsing | Done |
| Running time (module 0) | Done |
| Lane times, places, running flag (modules 1–10) | Done |
| Event / heat detection (module 12) | Done |
| Swimmer names / meet title (command 18) | Not implemented — Lenex integration covers this |
| Split times (digits 10+ per lane module) | Not implemented — needs a live multi-length capture |
| Multi-pool (pools 1–3) | Not implemented |

---

## Sources

- `fabriziobertocci/coloradoScoreboard` — `ctsScoreboardasync.js`: <https://github.com/fabriziobertocci/coloradoScoreboard>
- Colorado Time Systems Gen7 product page: <https://coloradotime.com/products/gen7-swim-timing-serial>
- F1034 manual (Rev 202405) — not publicly accessible in text form
