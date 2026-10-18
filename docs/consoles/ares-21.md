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
- UNT4 / GALACTICA: baud rate and format are options (9600, 7-E-1 by default).
- GP / OSM6: 9600, **7-E-1** (ARES Swimming manual §9.1.2).

Full protocol references: [`swiss_timing_ares21_serial.md`](../../server/console_decoders/swiss_timing_ares21_serial.md), [`swiss_scoreboard_serial.md`](../../server/console_decoders/swiss_scoreboard_serial.md), [`swiss_timing_quantum_serial.md`](../../server/console_decoders/swiss_timing_quantum_serial.md)

## Settings

In **Settings → Timing**:

- **Timing Equipment:** `ARES 21 (Swiss Timing Omega)`
- **Console output:** the output you wired (table above)
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)

Options shown, per output:

- ERTD — **Event number position**: change it if the event shown is wrong (e.g. 1 instead of 12).
- UNT4 / GALACTICA — **Scoreboard line order**: rank mode (default) or lane mode; must match the ARES scoreboard kind (`… La` = lane, `… Rk` and GALACTICA = rank). **Baud rate**, **Serial format**.
- GP / OSM6 — **Serial format** (7-E-1 by default), **Lane digit 0**.

## Capturing at the pool

Follow the [general procedure](../admin.md#capturing-at-the-pool). Photograph the ARES Swimming *Configuration → Scoreboard* and *DH* settings.

### SCB — Venus ERTD

1. **Console:** Configuration → Scoreboard → Kind of scoreboard: ERTD; **RS485** checked.
2. **Wire:** IF-ARES SCB connector, pins 4 / 3 / 7, to a USB-to-RS485 adapter.
3. **Splouch:** `ARES 21` → `SCB — Venus ERTD`.
4. **Check:** packets start `16 30 30 30 30 30 30 30 30 01 30 30 34 30 31` (`00401`). Event number wrong (e.g. 1 instead of 12) → switch **Event number position**.
5. **Record:** `.raw`, then `.serial` once lane times show. Start recording before opening the Race dialog (or pressing Next Heat): that is when ARES sends the start list.

### SCB — UNT4 / Calypso / Piccolo / GALACTICA

1. **Console:** Kind of scoreboard: the UNT4 / Calypso / Piccolo or GALACTICA entry; note whether it is `Rk` (rank) or `La` (lane).
2. **Wire:** as for ERTD.
3. **Splouch:** `ARES 21` → `SCB — UNT4 / …`; **Scoreboard line order** = rank for `Rk` and GALACTICA, lane for `La`.
4. **Check:** packets `01 14 52 30 32 02 08 …` (running time) and `01 14 53 30 32 02 08 …` (lines), ending `04`. Garbage → **Baud rate** / **Serial format**.
5. **Record:** `.raw`. Let intermediate times be erased on the board during the race, as usual.

### GP — OSM6 data handling

1. **Console:** DH output in OSM6 mode.
2. **Wire:** IF-ARES GP connector, pins 3 / 7 / 8, to a USB-to-RS485 adapter.
3. **Splouch:** `ARES 21` → `GP — OSM6 data handling`; **Serial format** 7-E-1.
4. **Check:** pairs of packets `01 02 08 …04` and `01 02 08 0a …04`, only when something happens (ready, start, touches) — quiet otherwise. Garbage → **Serial format** 8-N-1.
5. **Record:** `.raw`. In a 10-lane pool, make sure lane 10 swims.
