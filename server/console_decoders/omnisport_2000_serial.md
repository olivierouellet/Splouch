# Daktronics Omnisport 2000 — Serial Protocol Reference

## Hardware Interface

| Parameter | Value |
|-----------|-------|
| Connector | DB-9 (J6 Results Port / J5 RTD Port) |
| Interface | RS-232 |
| Baud rate | 19 200 |
| Data bits | 8 |
| Parity | None |
| Stop bits | 1 |
| Flow control | None |

---

## Packet Framing

The Omnisport 2000 uses ASCII text packets delimited by control characters:

| Byte | Hex | Role |
|------|-----|------|
| SYN  | 0x16 | Frame start — marks the beginning of a new packet |
| STX  | 0x02 | Payload start — content between SYN and STX is discarded |
| EOT  | 0x04 | Payload end |
| CR   | 0x0D | Packet terminator (follows EOT) |

Packet structure:

```text
SYN ... STX <payload> EOT CR
```

Bytes between SYN and STX are keepalive/sync fill and should be ignored.
The decodable content is everything between STX and EOT.

**Packet start detection:** `byte == 0x16` (SYN)

---

## Payload Format

All payload data is ASCII text. The first character is a type prefix.

### Running Time — prefix `t`

```text
t<MM:SS.T>
```

| Field | Description |
|-------|-------------|
| `t`   | Type prefix |
| MM    | Minutes (zero-padded, omitted if 0) |
| SS    | Seconds (zero-padded) |
| T     | Tenths of a second |

Example: `t1:02.4` → 1 minute, 2.4 seconds

### Lane Finish — prefix `l` (lowercase L)

```text
l<lane> <place> <MM:SS.CC>
```

| Field  | Description |
|--------|-------------|
| `l`    | Type prefix |
| lane   | Lane number (1-based) |
| place  | Finish place |
| MM:SS.CC | Finish time (hundredths precision) |

Example: `l3 1 1:11.63` → Lane 3, 1st place, 1:11.63

### Split Time — prefix `s`

Split times are transmitted **natively** in the serial stream (unlike CTS Gen6, which infers them server-side).

```text
s<lane> <place> <MM:SS.CC> <laps>
```

| Field  | Description |
|--------|-------------|
| `s`    | Type prefix |
| lane   | Lane number (1-based) |
| place  | Current place at split |
| MM:SS.CC | Split time (hundredths precision) |
| laps   | Number of lengths completed |

Example: `s3 1 1:11.63 2` → Lane 3, 1st at split, 1:11.63, 2 lengths completed

### Other Observed Prefixes

| Prefix | Meaning | Notes |
|--------|---------|-------|
| `r`    | Race reset / clear | Signals a new race start |
| `b`    | Backup time | Secondary touchpad time |

---

## Split Handling

Because split times arrive as explicit `s` packets, the decoder should:

- Emit `lane_splits{n}` = `laps` value from each `s` packet
- Update `lane_time{n}` with the split time string
- Not rely on touchpad-transition inference (as Gen6 does)

---

## Event / Heat Detection

The Omnisport 2000 **does not transmit event or heat numbers** over the RTD serial port. Event/heat must be tracked externally (e.g., from the meet management software or manual entry).

---

## Implementation Status

| Packet type | Key(s) emitted | Status |
|-------------|----------------|--------|
| Running time (`t`) | `running_time` | Planned |
| Lane finish (`l`) | `lane_time{n}`, `lane_place{n}`, `lane_running{n}` | Planned |
| Split (`s`) | `lane_splits{n}`, `lane_time{n}` | Planned |
| Reset (`r`) | *(reset all lanes)* | Planned |

---

## Sources

- XY Kao — *Decoding the Daktronics Omnisport 2000*: <https://xy-kao.com/projects/decoding-daktronics-omnisport-2000/>
- GitHub reverse-engineering project: <https://github.com/xyk2/daktronics>
- Hy-Tek interface documentation: <https://hytek.active.com/user_guides_html/swmm8/dak2000.htm>
- ManualsLib Omnisport 2000: <https://www.manualslib.com/manual/1798873/Daktronics-Omnisport-2000.html>
