# Swiss Timing Omega — Ares 21

> **Not tested on real hardware.** Implemented from published protocol documentation. Adjustments may be needed once validated against a live console.

## Hardware

| Item | Purpose |
| --- | --- |
| USB-to-RS485 adapter | RS-485 bus → Pi USB |

> Standard USB-to-RS232 adapters will not work — use a USB-to-RS485 adapter.

## Wiring

The Ares 21 uses a non-standard DB9 pinout.

```text
PC side (DB9 female)          Ares 21 side (DB9 male)
  Pin 1 → T(+) / RS-485 B+     Pin 4 → T(+) / RS-485 B+
  Pin 2 → T(-) / RS-485 A-     Pin 3 → T(-) / RS-485 A-
  Pin 5 → Ground                Pin 7 → Ground
```

## Protocol

RS-485 — 9600 baud, 8-N-1 (Venus ERTD scoreboard format)

Full protocol reference: [`console_decoders/swiss_timing_ares21_serial.md`](../../server/console_decoders/swiss_timing_ares21_serial.md)

## Settings

In the admin UI (**Settings → Timing**), set:

- **Console type:** `Swiss Timing Ares 21`
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)
