# Colorado Time Systems — Gen7 Serial

> **Not tested on real hardware.** Implemented from published protocol documentation. Adjustments may be needed once validated against a live console.

## Hardware

| Item | Purpose |
| --- | --- |
| USB-to-RS485 adapter | RS-485 bus → Pi USB |

## Wiring

Connect to the RS-485 port on the Gen7 console.

## Protocol

RS-485 — 115 200 baud, 8-N-1

Full protocol reference: [`console_decoders/cts_gen7_serial.md`](../../server/console_decoders/cts_gen7_serial.md)

## Settings

In the admin UI (**Settings → Timing**), set:

- **Console type:** `CTS Gen7`
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)
