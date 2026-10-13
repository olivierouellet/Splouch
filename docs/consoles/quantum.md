# Swiss Timing Omega — Quantum

> **Not tested on real hardware.** Implemented from published protocol documentation; the message format is the one Swiss Timing documents for the ARES 21 GP output (OSM6). Adjustments may be needed once validated against a live console.

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

RS-485 — 9600 baud, OSM6 format. 8-N-1 by default; the ARES 21 sends the same format at 7-E-1, and some Quantum set-ups may too.

Full protocol reference: [`console_decoders/swiss_timing_quantum_serial.md`](../../server/console_decoders/swiss_timing_quantum_serial.md)

## Settings

In **Settings → Timing**:

- **Timing Equipment:** `Quantum (Swiss Timing Omega)`
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)

Options:

- **Serial format** — 8-N-1 (default) or 7-E-1. If no data decodes, try the other.
- **Lane digit 0** — lane 10 (default) or ignored. The lane is one character; other Swiss Timing outputs send lane 10 as `0`.

## Capturing at the pool

Follow the [general procedure](../admin.md#capturing-at-the-pool).

1. **Console:** the Quantum's OSM6 data output enabled on the serial port you wire.
2. **Wire:** pins 3 / 4 to a USB-to-RS485 adapter (the meet-management cable works).
3. **Splouch:** `Quantum` → serial port; **Serial format** 8-N-1 first.
4. **Check:** pairs of packets `01 02 08 …04` and `01 02 08 0a …04`, only when something happens (ready, start, touches). Garbage → **Serial format** 7-E-1.
5. **Record:** `.raw`, then `.serial`. In a 10-lane pool, make sure lane 10 swims; note which **Lane digit 0** setting gives the right lane.
