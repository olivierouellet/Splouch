# Swiss Timing Omega Quantum — Serial Protocol Reference (OSM6)

> **Status: Documented** from the official Swiss Timing OSM6 specification
> and community implementation `hakostra/swimming-scoreboard` (`scoreboard/comms.py`, GPL-3.0).
> Protocol name: **OSM6** (the same wire format used by the Quantum's
> meet-management data output).

---

## Hardware Interface

Official Swiss Timing specification:

| Parameter | Value |
| --------- | ----- |
| Interface | **RS-485** |
| Baud rate | **9 600 – 115 200** (configurable on the console; default 9 600) |
| Data bits | **8** |
| Parity | **None** |
| Stop bits | 1 |
| Encoding | US-ASCII, no handshake, no XON/XOFF |

> **Note — community implementation discrepancy:** `hakostra/swimming-scoreboard`
> defaults to **7 data bits** and documents **RS-422** wiring. This may reflect
> a specific hardware variant or misconfiguration. The official spec (8 data
> bits, RS-485) is used here. If the decoder produces no output, try 7 data
> bits as a fallback.

### Wiring (Quantum RS-422/RS-485 connector → USB adapter)

```text
Quantum pin 3  →  USB-RS485 adapter RX-  (A-)
Quantum pin 4  →  USB-RS485 adapter RX+  (B+)
```

If you already have a cable that works with your meet-management software,
it will work here too — the Quantum uses the same port for both.

---

## Control Bytes

| Name | Value | Role |
| --- | --- | --- |
| SOH | 0x01 | Start of every frame |
| STX | 0x02 | Separator inside pt2 (before time string) |
| EOT | 0x04 | End of every frame |
| HOME | 0x08 | Third byte of the standard frame prefix |
| LF | 0x0A | Identifies pt2 (4th byte after prefix) |
| DC2 | 0x12 | Part of the alive-message pattern |
| DC4 | 0x14 | Part of the alive-message pattern |

---

## Frame Structure

Every frame starts with SOH and ends with EOT. Two frame types exist:

### Alive / keepalive (discard)

```text
SOH DC2 '9' DC4 'T' 'P' EOT     (7 bytes)
```

### Data frames (SOH STX HOME prefix)

```text
SOH STX HOME <payload> EOT
```

The 4th byte of the payload identifies the part:

- **4th byte ≠ LF** → Part 1 (pt1) — metadata
- **4th byte = LF** → Part 2 (pt2) — lane/time

---

## Two-Part Message Pairing

Each logical message from the Quantum is TWO consecutive physical frames.
pt1 carries metadata; pt2 carries the lane ID and time string.
They must be received and combined before a scoreboard update can be emitted.

### Part 1 payload — 16 bytes (`ABCDDEEFFFGG··HH`)

| Chars | Field | Description |
| --- | --- | --- |
| A | msg_type | Message type (see below) |
| B | time_kind | Kind of time (see below) |
| C | time_type | Time type / quality flag |
| DD | used_lanes | Lane bitmask — see Note 1 |
| EE | laps | Lap count — see Note 2 |
| FFF | event | Event number (3 digits, zero-padded) |
| GG | heat | Heat number (2 digits, zero-padded) |
| ·· | — | Two space characters (padding) |
| HH | rank | Finishing rank (2 digits, space-padded) |

### Part 2 payload — 17 bytes (`LF J KK STX Hh:Mm:Ss.dc ·`)

| Chars | Field | Description |
| --- | --- | --- |
| LF | — | pt2 identifier (0x0A) |
| J | lane | Lane number (1 ASCII digit — see Note 3) |
| KK | lap | Current lap number (2 chars, space-padded) |
| STX | — | Separator (0x02) |
| Hh:Mm:Ss.dc | time | Time string (11 chars — see Time Format) |
| · | — | Trailing space |

---

## Message Type Codes

### Field A — Message type

| Value | Meaning |
| ----- | ------- |
| `'0'` | Ready at start (new heat announced) |
| `'1'` | Official end (heat finished) |
| `'2'` | On-line time (split or finish for a lane) |
| `'3'` | Current race results |
| `'5'` | Previous race results |

### Field B — Kind of time

| Value | Meaning |
| ----- | ------- |
| `'S'` | Start signal |
| `'I'` | Intermediate / split time |
| `'A'` | Finish (final) time |
| `'D'` | Take-over time (relay) |
| `'R'` | Reaction time at start |
| `'B'` | Button-only finish |

---

## Time String Format

The 11-character time field is `Hh:Mm:Ss.dc` where unused leading
components are filled with spaces:

```text
"      21.89"   →  21.89        (seconds only — e.g. a split)
"    1:22.07"   →  1:22.07      (minutes + seconds — e.g. a finish)
"14:17:55.26"   →  clock time   (only in B='S' start messages — not race elapsed)
```

The decoder strips blank segments and converts to `M:SS.cc` or `SS.cc`.

> **Start message time:** when `B='S'`, the time field contains the **real-world
> clock time** at which the race started (e.g. `14:17:55.26`), not an elapsed
> race time. The decoder ignores this value — it is only useful for clock
> synchronisation, which is not implemented.

---

## Packet Examples (from official Swiss Timing spec)

### Enter Race — Event 3, Heat 2, 4 laps (200 m)

```text
pt1: [SOH][STX][HOME] 0 · ?? ·4 003 02 ·· ·· [EOT]
pt2: [SOH][STX][HOME][LF] ? ·0 [STX]           · [EOT]
```

A=`'0'` (ready at start), EE=`' 4'` (4 laps), FFF=`'003'`, GG=`'02'`, time blank.

### Race Started

```text
pt1: [SOH][STX][HOME] 2S · ?? ·4 003 02 ·· ·0 [EOT]
pt2: [SOH][STX][HOME][LF] 1 ·0 [STX] 14:17:55.26 · [EOT]
```

A=`'2'`, B=`'S'`. pt2 time is clock time (14:17:55.26), not race elapsed time.

### Split — Rank 3, Lane 2, Lap 1

```text
pt1: [SOH][STX][HOME] 2I · ?? ·4 003 02 ·· ·3 [EOT]
pt2: [SOH][STX][HOME][LF] 2 ·1 [STX]       21.89 · [EOT]
```

A=`'2'`, B=`'I'`, lane=2, lap=1, time=21.89 s, rank=3.

### Finish — Rank 2, Lane 4

```text
pt1: [SOH][STX][HOME] 2A · ?? ·4 003 02 ·· ·2 [EOT]
pt2: [SOH][STX][HOME][LF] 4 ·4 [STX]    1:22.07 · [EOT]
```

A=`'2'`, B=`'A'`, lane=4, lap=4, time=1:22.07, rank=2.

Note: `··` = space character 0x20; `??` = lane bitmask bytes, content not relevant to decoder.

---

## Dispatch Logic

| A | B | Action |
| --- | --- | --- |
| `'0'` | any | New heat: emit `event_changed`, reset lanes |
| `'2'` | `'S'` | Race started: mark untimed lanes `running=True` — only lanes with a swimmer in the start lists (`set_heat_lanes`; every lane when no meet is loaded) — dismiss overlay |
| `'2'` | `'I'` | Split: emit `lane_time`, `lane_place` (if ranked), `lane_splits` |
| `'2'` | `'A'`, `'B'` | Finish (B = backup buttons only): emit `lane_time`, `lane_place`, `lane_running=False`; a blank time still stops the lane |
| `'1'` | any | Heat officially ended |

---

## Running Time

The OSM6 protocol does **not** broadcast a running clock over the serial wire.
The start signal (`A='2'`, `B='S'`) marks when the race began, but subsequent
time packets only arrive on touchpad hits. There is no `running_time` value
available from this decoder — the scoreboard displays no clock until the first
split or finish time arrives.

---

## Split Times

Split times are natively transmitted. Each intermediate touchpad hit produces
an `A='2'`, `B='I'` message with the **cumulative** race time for that lane and
the current lap number. The decoder emits `lane_splits{n}` (lap count) on each
intermediate message.

---

## Notes

### Note 1 — DD field: used-lane bitmask

DD is two ASCII bytes. The first encodes lanes 1–5, the second encodes lanes 6–10.
Each byte is a bitmask: bits 7/6/5 are fixed as `0/0/1` (so the byte is always
in the range 0x20–0x3F), and bits 4–0 represent lane activity one bit per lane
(LSB = lane 1 or lane 6).

The decoder does not use DD — lane identity is taken from the `J` field in pt2.

### Note 2 — EE field: lap count

The official spec labels EE "Lap number". In `A='0'` (ready at start) messages
it appears to carry the **total laps** for the race distance. In `A='2'`
(on-line time) messages it may carry the **current lap**. The decoder does not
use EE — current lap is taken from the `KK` field in pt2.

### Note 3 — J field: lane 10

The `J` field is one ASCII character. For lanes 1–9 this is `'1'`–`'9'`. For
lane 10 the encoding is **not confirmed** — it may be `'0'` (wrap-around),
`'A'` (hex), or something else. The DD bitmask confirms the console supports
10 lanes. A capture from a 10-lane pool is needed to resolve this.

---

## Known Uncertainties (needs hardware validation)

### Serial parameters (7 vs 8 data bits)

The official Swiss Timing spec says **8 data bits**. The community implementation
`hakostra/swimming-scoreboard` defaults to **7 data bits**. Both sources agree on
9600 baud, no parity, 1 stop bit. The decoder is configured for 8 data bits
(trusting the official spec). **If no data is received, change to 7 in the
Settings and try again.**

### Lane 10 encoding in J field

As described in Note 3, the byte value for lane 10 is unknown. If `int(J)`
raises ValueError the message is silently dropped. A capture from a 10-lane
pool is the only way to confirm.

### Rank at split time

The official spec does not state whether HH is populated during split messages
(`B='I'`) or only at finish (`B='A'`). If HH is blank at split time,
`lane_place` will not be updated until the finish arrives.

### Running-state inference

The start signal triggers `lane_running=True` — only lanes with a swimmer in the start lists (`set_heat_lanes`; every lane when no meet is loaded) — for lanes that have not yet
received a finish time. There is no per-lane confirmation from the console.
Pre-start clock runs or intra-heat restarts will not be handled correctly.

---

## Implementation Status

Implemented in `console_decoders/quantum.py` (`QuantumDecoder`).

| Feature | Status |
| ------- | ------ |
| Frame framing (SOH STX HOME / EOT) | Done |
| Two-part message pairing (pt1 + pt2) | Done |
| Alive message filtering | Done |
| New heat / event-changed signal | Done |
| Start signal → lane running state | Done |
| Split times with lap count | Done |
| Finish times with rank/place | Done |
| Running time (clock) | Not available in OSM6 |
| Lane 10 | Unverified — see Note 3 |
| 7 vs 8 data bits | Configured for 8 (official spec); try 7 if no data |

---

## Sources

- Official Swiss Timing OSM6 specification (Quantum Swimming, OSM6 Type)
- `hakostra/swimming-scoreboard` — `scoreboard/comms.py`: [github.com/hakostra/swimming-scoreboard](https://github.com/hakostra/swimming-scoreboard)
- Swiss Timing Quantum datasheet: [swisstiming.com](https://www.swisstiming.com/fileadmin/Resources/Data/Datasheets/DOCM_AQ_Quantum_1015_EN.pdf)
