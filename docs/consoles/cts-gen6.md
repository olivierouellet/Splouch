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

- **Console type:** `CTS Gen6`
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)
