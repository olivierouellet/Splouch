# Colorado Time Systems — Gen7

> **Not tested on real hardware.** Implemented from published protocol documentation and the CTS Gen7 user guides (F1034 Serial, F1058 Legacy). Adjustments may be needed once validated against a live console.

A Gen7 timer has several scoreboard outputs, and each RS-485 output can be set to another protocol in the Gen7 software (*Settings → Scoreboard → Input / Output Course Mappings*). Pick the console, then the output you wired, in **Settings → Timing**.

| Output (second menu) | Connector | Decoder | Status |
| --- | --- | --- | --- |
| **Legacy scoreboard output** | Gen7 Legacy timer, RS-232 legacy output | Colorado legacy | Tested (as System 6) |
| **Gen7 serial output** | RS-485 output, Gen7 Standard (default) | CTS Gen7 | Untested |
| **RS-485 output set to CTS Legacy** | RS-485 output, protocol CTS Legacy | Colorado legacy | Unconfirmed |
| **RS-485 output set to Daktronics RTD** | RS-485 output, protocol Daktronics RTD | Daktronics RTD | Unconfirmed — layout and serial settings are options |
| **RS-485 output set to Omega** | RS-485 output, protocol Omega | Swiss Timing scoreboard lines (rank mode) | Unconfirmed — no event/heat |

The alternative RS-485 protocols are licensed features of the Gen7 software. The Gen7 Legacy timer has one RS-232 legacy output and two RS-485 outputs (Black Diamond, Blue Square); the Gen7 Serial timer has three RS-485 outputs.

## Hardware

| Item | Purpose |
| --- | --- |
| USB-to-RS485 adapter | RS-485 output → Pi USB |
| USB-to-RS232 adapter | Legacy output → Pi USB |

## Protocol

- Gen7 Standard: RS-485 — 115 200 baud, 8-N-1
- RS-485 set to another protocol: serial settings are options (see below)

Full protocol reference: [`console_decoders/cts_gen7_serial.md`](../../server/console_decoders/cts_gen7_serial.md)

## Settings

In **Settings → Timing**:

- **Timing Equipment:** `Gen7 (Colorado Timing System)`
- **Console output:** the output you wired (table above)
- **Serial port:** `/dev/ttyUSB0` (or whichever device the adapter appears as)

Options, per output:

- RS-485 set to CTS Legacy — **Serial parity**
- RS-485 set to Daktronics RTD — **RTD layout** (lines at 222 or 200), **RTD header sub-code** (any by default), **Baud rate**, **Serial format**, **RTD offset**
- RS-485 set to Omega — **Scoreboard line order** (rank mode by default), **Baud rate**, **Serial format**

On the Gen7, set **Two Digit Lane and Place Display** to **A,B,C** or **Letters** for pools of more than 9 lanes; both are decoded.
