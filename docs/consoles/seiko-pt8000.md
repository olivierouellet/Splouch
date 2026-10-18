# Seiko — PT-8000

> **Not tested on real hardware.** Implemented from a published decoding of the PT-8000's serial output. A recording of one heat (Settings → Test → Record, `.raw`) would confirm it.

What the board gets:

- the running clock, from the start to the finish;
- each touch: a lap (the lane keeps going) or the goal (the finish), with its place.

What it does not get: event and heat. Names and seed times from the meet file load only when the heat is set some other way.

## Hardware

| Item | Purpose |
| --- | --- |
| USB-to-RS485/422 adapter | The PT-8000's RS-422 output → Pi USB |

## Wiring

The PT-8000's RS-422 transmit pair to the adapter's receive pair (RX+ / RX−), and ground. The connector's pinout is not confirmed: check it on site or in the PT-8000 manual. If a meet-management PC already listens on that output, tap it in parallel with termination off.

## Protocol

RS-422 — 9600 baud, 7-E-1, fixed 16-byte records (STX … ETX). Baud rate and format are options until a capture confirms them.

Full protocol reference: [`seiko_pt8000_serial.md`](../../server/console_decoders/seiko_pt8000_serial.md)

## Settings

In **Settings → Timing**:

- **Timing Equipment:** `PT-8000 (Seiko)`
- **Console output:** `RS-422 serial output`
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)

Options:

- **Baud rate** — 9600 (default) or 19200.
- **Serial format** — 7-E-1 (default) or 8-N-1. If no data decodes, try the other.
- **Lane digit 0** — lane 10 (default) or ignored. The lane is one character.

Set **Settings → Meet → Touchpads** to match the pool: with pads at one end only, each touch is two lengths.

## Capturing at the pool

Follow the [general procedure](../admin.md#capturing-at-the-pool).

1. **Console:** nothing to set — the output sends while the PT-8000 runs.
2. **Wire:** the RS-422 output to a USB-to-RS485/422 adapter (transmit pair → RX+ / RX−, ground).
3. **Splouch:** `PT-8000` → `RS-422 serial output`; **Baud rate** 9600, **Serial format** 7-E-1.
4. **Check:** 16-byte packets that start `02 41` and end `03`; while the clock runs, `02 41 52 …` several times a second. Garbage → try **Serial format** 8-N-1, then **Baud rate** 19200.
5. **Record:** `.raw` — a reset, a start and a whole heat to its last finish. In a 10-lane pool make sure lane 10 swims and a 10th place happens; note which lane and place Splouch shows for them.
