# Daktronics OmniSport 2000

> **Not tested on real hardware.** Implemented from Daktronics' RTD field template (`OS2-Swimming.itf`) and a published capture of the RTD port. Adjustments may be needed once validated against a live console.

The OmniSport has several outputs. Pick the console, then the output you wired, in **Settings → Timing**.

| Output (second menu) | Port | Console menu | Decoder |
| --- | --- | --- | --- |
| **J5 RTD — Results set to OMNI2000** | J5 RTD | RTD Port → RTD, Results → OMNI2000 | Daktronics RTD, lines from offset 222 |
| **J5 RTD — Results set to OMNI6000** | J5 RTD | RTD Port → RTD, Results → OMNI6000 | Daktronics RTD, lines from offset 200 |
| **J5 RTD port set to CTS** | J5 RTD | RTD Port → CTS | Colorado legacy (unconfirmed) |
| **Scoreboard port set to OMEGA** | Scoreboard | Scbrd Port → OMEGA | Swiss Timing scoreboard lines (no event/heat) |

The J5 RTD outputs are the recommended ones. J6 is the Results Port, which speaks Meet Manager's protocol, not a scoreboard feed.

## Hardware

| Item | Purpose |
| --- | --- |
| DB9 cable | Connect to the J5 RTD Port (or the scoreboard port for OMEGA) |
| USB-to-RS232 adapter | DB9 → Pi USB |

## Protocol

- RTD: RS-232 — 19 200 baud, 8-N-1, with the **Swimming** sport mode loaded.
- CTS mode: the port runs at half the RTD speed (9600). Parity is an option (even by default).
- OMEGA: serial settings undocumented — baud rate and format are options.

Full protocol reference: [`console_decoders/omnisport_2000_serial.md`](../../server/console_decoders/omnisport_2000_serial.md) and [`swiss_scoreboard_serial.md`](../../server/console_decoders/swiss_scoreboard_serial.md)

## Settings

In **Settings → Timing**:

- **Timing Equipment:** `OmniSport 2000 (Daktronics)`
- **Console output:** the output you wired (table above)
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)

Options shown for the RTD outputs:

- **RTD offset** — must match the console's *RTD Offset* menu (0K, 5K, 10K, 15K). Leave at 0K unless the venue changed it.
