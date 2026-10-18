# Seiko PT-8000 — Serial Output Reference

> **Status: Documented** from a published community decoding of the PT-8000's serial
> output (see Sources). Not yet run against a live console.
> Implemented in `seiko_pt8000.py` (`SeikoPT8000Decoder`, decoder key `seiko_pt`,
> console entry `seiko_pt8000`).

---

## Hardware Interface

| Parameter | Value |
| --------- | ----- |
| Interface | **RS-422** — a USB-to-RS485/422 adapter on the transmit pair |
| Baud rate | 9 600 (option: 19 200) |
| Format | 7-E-1 (option: 8-N-1) |
| Flow control | none |

The PT-8000 may have other outputs in other formats (a PC link, a scoreboard link);
only this one is decoded.

---

## Record

Every record is 16 bytes:

| Byte | Content |
| ---: | --- |
| 1 | STX (`0x02`) |
| 2 | `A` |
| 3 | `R` on a running-clock record; otherwise unused |
| 4 | Lane (one character) |
| 5 | Finish order of a lap or goal (one character) |
| 6 | unused |
| 7–14 | Time, right-aligned |
| 15 | Kind: `L` lap, `G` goal, `J` reaction, `K` take-over, blank for the clock |
| 16 | ETX (`0x03`) |

### Running clock (`R`, kind blank)

The time is `m:ss.t` in bytes 7–13, the minutes blank under a minute.

| Record | Meaning |
| --- | --- |
| `STX A R ······ 0.00 · ETX` | Reset — the clock shows hundredths |
| `STX A R ······ 0.0 ·· ETX` | Start — the first clock record after a reset |
| `STX A R ··· 1:46.3 ·· ETX` | Running |

### Lap and goal (`L`, `G`)

`STX A 4 5 4 ·· 1:29.30 L ETX` — lane 5, 4th, 1:29.30, a lap. A goal is the finish.

### Reaction and take-over (`J`, `K`)

`STX A · 4 ·· S0S0.69 · J ETX` — lane 4, reaction 0.69 s (time in bytes 10–13).
`STX A 0 5 0 2 · 0+0.63 · K ETX` — lane 5, take-over +0.63 s (sign in byte 9). Outside a
relay a take-over record follows the turn's lap and reads `-0.00`.

---

## Decoder Behaviour

| Record | Emitted |
| --- | --- |
| Clock, reset (`0.00`) | `running_time` cleared; the next clock record is a start. The last race's times stay on the board |
| Clock, first after a reset | lanes cleared (seeds kept), `lane_running=True` for the lanes in the start list (every lane when none), `dismiss_overlay` |
| Clock, running | `running_time` |
| Lap | `lane_time`, `lane_place` (its order), `lane_splits` += split step; the lane keeps running |
| Goal | `lane_time`, `lane_place`, `lane_splits` += split step, `lane_running=False` |
| Reaction, take-over | ignored |

The split step follows Settings → Meet → Touchpads (two lengths per touch with pads at
one end).

**Not carried:** event and heat. Names and seed times load only when the heat is set
another way.

## Options

| Option | Values | Default |
| --- | --- | --- |
| `serial_baud` | `9600`, `19200` | `9600` |
| `serial_format` | `7E1`, `8N1` | `7E1` |
| `lane_zero` | `10`, `ignore` | `10` — not used when the pool's first lane is 0: `0` is then lane 0 |

## Known Uncertainties (needs hardware validation)

- A lane or a place above 9 in its one character: `0` is read as lane 10 and 10th.
- Whether a lap's order is a running rank, and what byte 3 holds on a touch (it
  repeats the order in the example).
- Reaction times of 10 s or more, and negative ones.

---

## Sources

- charliekato, *PT8000 serial data format* (Zenn): <https://zenn.dev/charliekato/articles/pt_serial_data_format>
