# Daktronics Omnisport 2000

> **Not tested on real hardware.** Implemented from published protocol documentation. Adjustments may be needed once validated against a live console.

## Hardware

| Item | Purpose |
| --- | --- |
| DB9 cable | Connect to the J6 Results Port or J5 RTD Port on the Omnisport 2000 |
| USB-to-RS232 adapter | DB9 → Pi USB |

## Wiring

Connect the DB9 cable to the **J6 Results Port** (preferred) or **J5 RTD Port** on the back of the Omnisport 2000.

## Protocol

RS-232 — 19 200 baud, 8-N-1

Full protocol reference: [`console_decoders/omnisport_2000_serial.md`](../../server/console_decoders/omnisport_2000_serial.md)

## Settings

In the admin UI (**Settings → Timing**), set:

- **Console type:** `Daktronics Omnisport 2000`
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)
