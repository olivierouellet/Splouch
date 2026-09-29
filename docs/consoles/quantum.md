# Swiss Timing Omega — Quantum

> **Not tested on real hardware.** Implemented from published protocol documentation. Adjustments may be needed once validated against a live console.

## Hardware

| Item | Purpose |
| --- | --- |
| USB-to-RS485 adapter | RS-485 bus → Pi USB |

## Wiring

```text
Quantum pin 3  →  USB-RS485 adapter RX-  (A-)
Quantum pin 4  →  USB-RS485 adapter RX+  (B+)
```

If you already have a cable that works with your meet-management software, it will work here — the Quantum uses the same port for both.

## Protocol

RS-485 — 9600 baud, 8-N-1 (OSM6 format)

> If no data is received, try switching to 7 data bits in the Timing settings — the official spec says 8, but some hardware variants use 7.

Full protocol reference: [`console_decoders/swiss_timing_quantum_serial.md`](../../server/console_decoders/swiss_timing_quantum_serial.md)

## Settings

In the admin UI (**Settings → Timing**), set:

- **Console type:** `Swiss Timing Quantum`
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)
