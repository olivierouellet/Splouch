# Daktronics OmniSport 2000

> **Not tested on real hardware.** Implemented from the OmniSport 2000 manual (ED-13312), Daktronics' RTD field template (`OS2-Swimming.itf`) and a published capture of the RTD port. Adjustments may be needed once validated against a live console.

The OmniSport has several outputs. Pick the console, then the output you wired, in **Settings → Timing**.

| Output (second menu) | Port | Console menu | Decoder |
| --- | --- | --- | --- |
| **J5 RTD — Results set to OMNI2000** | J5 RTD | RTD Port → RTD, Results → OMNI2000 | Daktronics RTD, lines from offset 222 |
| **J5 RTD — Results set to OMNI6000** | J5 RTD | RTD Port → RTD, Results → OMNI6000 | Daktronics RTD, lines from offset 200 |
| **J5 RTD port set to CTS** | J5 RTD | RTD Port → CTS | Colorado legacy (unconfirmed) |
| **Ethernet — RTD over the network** | ETHERNET jack | Setup Network → Network Enable ON | Daktronics RTD over UDP broadcast (layout is an option) |
| **J6 Results port set to CTS** | J6 Results | Results Port → CTS | Colorado legacy (unconfirmed) |
| **Scoreboard port set to OMEGA** | Scoreboard | Scbrd Port → OMEGA | Swiss Timing scoreboard lines (no event/heat) |

The J5 RTD and Ethernet outputs are the recommended ones. J6 is the Results Port for meet management software: its default settings, OMNI 2000 and OMNI 6000, are bi-directional protocols Splouch does not decode; set to CTS it sends "the standard CTS scoreboard information" (ED-13312), which Splouch reads — Meet Manager must then read CTS on that port too.

## Ethernet

Consoles shipped from June 2013 have an ETHERNET jack. With *Setup Network → Network Enable* ON, the console broadcasts RTD as UDP packets. Its standard IP address is 192.168.0.55–58 (OMNI1–4) and its standard RTD port 21000–21003 (OMNI1–4); 3002 is used for Galaxy message centers. Put the Splouch server on the same network and subnet; no cable or adapter is needed.

## Hardware

| Item | Purpose |
| --- | --- |
| DB9 cable | Connect to the J5 RTD Port (or the scoreboard port for OMEGA) |
| USB-to-RS232 adapter | DB9 → Pi USB |

## Protocol

- RTD: RS-232 — 19 200 baud, 8-N-1, "Enhanced" protocol, 4096-byte buffer (ED-13312 appendix D), with the **Swimming** sport mode loaded.
- Ethernet: UDP broadcast on the console's RTD port number.
- CTS mode (J5 or J6): "the standard CTS scoreboard information". Serial settings aren't given in the manual; 9600 with even parity by default, parity is an option.
- OMEGA: serial settings undocumented — baud rate and format are options.

Full protocol reference: [`console_decoders/omnisport_2000_serial.md`](../../server/console_decoders/omnisport_2000_serial.md) and [`swiss_scoreboard_serial.md`](../../server/console_decoders/swiss_scoreboard_serial.md)

## Settings

In **Settings → Timing**:

- **Timing Equipment:** `OmniSport 2000 (Daktronics)`
- **Console output:** the output you wired (table above)
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)

Options shown for the RTD outputs:

- **RTD offset** — must match the console's *RTD Offset* menu (0K, 5K, 10K, 15K). Leave at 0K unless the venue changed it.
- Ethernet only: **RTD network port** (the console's *RTD Port #*, 21000 by default) and **RTD layout** (lines at 222 for Results set to OMNI2000, at 200 for OMNI6000).
