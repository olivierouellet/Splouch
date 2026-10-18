# ALGE — TM-SWIM

> **Not tested on real hardware.** Implemented from ALGE's *Swimming Timing System* manual (§5.1.1.2). A recording of one heat (Settings → Test → Record, `.raw`) would confirm it.

Splouch reads the TM-SWIM's **Video (TV-Online)** port, the output ALGE provides for TV graphics. It carries:

- the running clock, with the **event and heat** — names and seed times load from the meet file on their own;
- the start;
- every touch per lane: intermediate times and the finish, with the touch count;
- deleted times.

The TM-SWIM keeps sending to SwimTime over USB as usual; the Video port is a separate output.

## Hardware

| Item | Purpose |
| --- | --- |
| USB-to-RS232 adapter | Video port → Pi USB |
| 8-pin DIN plug (or the ALGE video cable) | Video port connector |

## Wiring

```text
DIN pin 1 (TX)  → adapter RX (DB9 pin 2)
DIN pin 2 (GND) → adapter GND (DB9 pin 5)
```

The port is galvanically isolated and transmit-only.

## Protocol

RS-232 — 19200 baud, 8-N-1, ASCII lines. The baud rate is set in SwimTime (*Options → Settings → Advanced → Video settings*).

Full protocol reference: [`alge_tmswim_serial.md`](../../server/console_decoders/alge_tmswim_serial.md)

## Settings

In **Settings → Timing**:

- **Timing Equipment:** `TM-SWIM (ALGE)`
- **Console output:** `Video (TV-Online) output`
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)

Options:

- **Baud rate** — must match SwimTime's video baud rate (19200 by default).

Set **Settings → Meet → Touchpads** to match the pool: the touch count is turned into lengths with it.

## Capturing at the pool

Follow the [general procedure](../admin.md#capturing-at-the-pool).

1. **SwimTime:** note the video baud rate (*Advanced settings → Video settings*).
2. **Wire:** the Video port to a USB-to-RS232 adapter (pin 1 → RX, pin 2 → GND).
3. **Splouch:** `TM-SWIM` → `Video (TV-Online) output`; **Baud rate** as in SwimTime.
4. **Check:** lines starting `R` ten times a second while the clock runs, and `I…` / `F…` lines at touches. Garbage → **Baud rate**.
5. **Record:** `.raw` — an event change, a start and a whole heat to its last finish, ideally a 200 in a 25 m pool. Note the SwimTime and TM-SWIM firmware versions, and whether ranks appear on the board.
