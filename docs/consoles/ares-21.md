# Swiss Timing Omega — ARES 21

> **Not tested on real hardware.** Implemented from Swiss Timing's protocol document (0100.064) and the ARES Swimming user manual. A recording of one heat (Settings → Test → Record, `.raw`) would confirm it.

The ARES 21 has three outputs on its IF-ARES interface. Pick the console, then the output you wired, in **Settings → Timing**.

| Output (second menu) | Connector | ARES setting | Decoder |
| --- | --- | --- | --- |
| **SCB — Venus ERTD** | SCB | Configuration → Scoreboard → Kind: ERTD | Daktronics RTD (lines from offset 200) |
| **SCB — UNT4 / Calypso / Piccolo / GALACTICA** | SCB | Kind: UNT4, Calypso, Piccolo or GALACTICA | Swiss Timing scoreboard lines (no event/heat) |
| **GP — OSM6 data handling** | GP | DH output in OSM6 mode | OSM6, same as the Quantum |

ERTD is the recommended output: it carries running time, event, heat and every result line.

## Hardware

| Item | Purpose |
| --- | --- |
| USB-to-RS485 adapter | RS-485 → Pi USB |

> Standard USB-to-RS232 adapters will not work — use a USB-to-RS485 adapter.

## Wiring

SCB connector (Sub-D 9 female on the IF-ARES):

```text
Pin 4 → RS-485 B+ (LD+)
Pin 3 → RS-485 A− (LD−)
Pin 7 → GND
```

GP connector (Sub-D 9 female on the IF-ARES):

```text
Pin 3 → RS-485 B+ (TxD+)
Pin 7 → RS-485 A− (TxD−)
Pin 8 → GND
```

## Protocol

- ERTD: RS-485 — 9600 baud, 8-N-1.
- UNT4 / GALACTICA: baud rate and format are options (9600, 8-N-1 by default).
- GP / OSM6: 9600, **7-E-1** (ARES Swimming manual §9.1.2).

Full protocol references: [`swiss_timing_ares21_serial.md`](../../server/console_decoders/swiss_timing_ares21_serial.md), [`swiss_scoreboard_serial.md`](../../server/console_decoders/swiss_scoreboard_serial.md), [`swiss_timing_quantum_serial.md`](../../server/console_decoders/swiss_timing_quantum_serial.md)

## Settings

In **Settings → Timing**:

- **Timing Equipment:** `ARES 21 (Swiss Timing Omega)`
- **Console output:** the output you wired (table above)
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)

Options shown, per output:

- ERTD — **Event number position**: change it if the event shown is wrong (e.g. 1 instead of 12).
- UNT4 / GALACTICA — **Scoreboard line order**: lane mode (default) or rank mode; must match the ARES scoreboard kind (`… La` = lane, `… Rk` and GALACTICA = rank). **Baud rate**, **Serial format**.
- GP / OSM6 — **Serial format** (7-E-1 by default), **Lane digit 0**.
