# Colorado Time Systems — System 5 / System 6 / Gen7 Legacy

> **Tested on real hardware.**

All three models share the same wire protocol. The Gen6 decoder handles all three.

## Hardware

| Item | Purpose |
| --- | --- |
| 1/4" Y-cable (stereo audio splitter) | Tap on the CTS serial line without interrupting it |
| DB9 female connector | Solder tap wires to RS-232 pinout |
| USB-to-RS232 adapter | DB9 → Pi USB |

## Wiring

The CTS Gen6 outputs serial data on the 1/4" headphone jack. Tap it passively with a Y-cable — one side goes back to the CTS display, the other to the DB9 connector.

```text
1/4" Y-cable center conductor (tip) → DB9 pin 2 (RX)
1/4" Y-cable shield (sleeve)        → DB9 pin 5 (GND)
```

## Protocol

RS-232 — 9600 baud, 8-E-1 (8 data bits, even parity, 1 stop bit)

Full protocol reference: [`console_decoders/cts_gen6_serial.md`](../../server/console_decoders/cts_gen6_serial.md)

## Settings

In the admin UI (**Settings → Timing**), set:

- **Timing Equipment:** `System 5 (Colorado Timing System)`, `System 6 (Colorado Timing System)`, or `Gen7 (Colorado Timing System)` with **Console output** `Legacy scoreboard output`
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)

Results posted by place (*Post Race by Place*, or the scoreboard set to place order) land on each swimmer's lane: Splouch reads the lane number each scoreboard line shows. In a pool of 11 or 12 lanes, show lanes 10-12 as letters (A, B, C), not emulated as 0, 1, 2 — a `1` then could be lane 1 or 11, and reads as lane 1.

Event 0 shows live as a test race (`EV 0 · Test race`) and is never published as a result.

Options:

- **Event / heat on scoreboard** (System 6 and Gen7 only) — match the timer's *Quick Options → EVT/HT*. Swapped puts event and heat on module 0D and the team scores on 0C. A System 5 has no such toggle.

## Capturing at the pool

Follow the [general procedure](../admin.md#capturing-at-the-pool). For System 5, System 6, and a Gen7's legacy output:

1. **Console:** nothing to set — the legacy scoreboard output always sends.
2. **Wire:** the 1/4" scoreboard Y-cable to a USB-to-RS232 adapter (tip → DB9 pin 2, sleeve → pin 5).
3. **Splouch:** Timing Equipment `System 5`, `System 6`, or `Gen7` → `Legacy scoreboard output`; the serial port of the adapter.
4. **Check:** *Connected: /dev/ttyUSB0*. The Serial Monitor scrolls constantly, even between races: short packets, each a byte from `80` to `FF` followed by a few bytes below `80`.
5. **Record:** `.serial` (this decoder is tested). For a pool with more than 9 lanes, make sure 10th, 11th and 12th places happen in the heat.
6. **Also record** (System 6, Gen7): 30 s with *Quick Options → EVT/HT* swapped and team scores entered, then set it back. Splouch, set to **Swapped**, reads event and heat from 0D; the capture confirms 0D carries them digit for digit like 0C.
7. **Also record:** a finished heat reposted by place (System 6: *Quick Options → Post Race by Place*; System 5: Scoreboard setup 0). Splouch should show the same times on the same lanes.
8. **Also record:** 10 s each of *Scoreboard Setup → Self Test* and *Show definitions on scoreboard*. Splouch should show neither: no `88:88.88`, no lane moved.
