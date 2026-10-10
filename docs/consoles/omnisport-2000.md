# Daktronics Omnisport 2000

> **Not tested on real hardware.** Implemented from Daktronics' own RTD field template (`OS2-Swimming.itf`) and checked against a published capture of the RTD port. Adjustments may be needed once validated against a live console.

## Hardware

| Item | Purpose |
| --- | --- |
| DB9 cable | Connect to the J5 RTD Port on the Omnisport 2000 |
| USB-to-RS232 adapter | DB9 → Pi USB |

## Wiring

Connect the DB9 cable to the **J5 RTD Port** on the back of the Omnisport 2000. Not J6: that is the Results Port, which speaks Meet Manager's protocol, not the scoreboard feed.

On the console, **RTD Port** must be set to **RTD** (not CTS), with the **Swimming** sport mode loaded and the RTD output set to Omni 2000 item numbers (not Omni 6000).

## Protocol

RS-232 — 19 200 baud, 8-N-1

Full protocol reference: [`console_decoders/omnisport_2000_serial.md`](../../server/console_decoders/omnisport_2000_serial.md)

## Settings

In the admin UI (**Settings → Timing**), set:

- **Console type:** `Daktronics Omnisport 2000`
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)
