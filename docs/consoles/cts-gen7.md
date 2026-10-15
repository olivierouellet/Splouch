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

- Gen7 serial output — **Pool** (1 by default): the pool this Splouch shows, as numbered in the Gen7's course setup. A Gen7 running several pools sends them all on this output. Until a two-pool capture confirms it, a second pool is safest on its own RS-485 or legacy output, mapped to that pool in *Input / Output Course Mappings*.
- Gen7 serial output, Legacy scoreboard output, RS-485 set to CTS Legacy — **Event / heat on scoreboard**: match *Quick Options → EVT/HT*. Swapped puts event and heat on module 0D and the team scores on 0C.
- RS-485 set to CTS Legacy — **Serial parity**
- RS-485 set to Daktronics RTD — **RTD layout** (lines at 222 or 200), **RTD header sub-code** (any by default), **Baud rate**, **Serial format**, **RTD offset**
- RS-485 set to Omega — **Scoreboard line order** (rank mode by default), **Baud rate**, **Serial format**

Results posted by place (*Lane Module Order* set to Place, or a result posted by place) land on each swimmer's lane: Splouch reads the lane number each scoreboard line shows. In a pool of 11 or 12 lanes, keep **Two Digit Lane and Place Display** on A,B,C or Letters — a `1` could be lane 1 or 11, and reads as lane 1.

Event 0, the test race in every Gen7 event sequence, shows live as `EV 0 · HT 1 · Test race` on every board and phone, and is never published as a result.

Leave the Gen7's **Scoreboard Definitions** at their defaults (*Settings → Scoreboard*): Splouch reads each lane from its default module (lanes 11 and 12 included).

On the Gen7, set **Two Digit Lane and Place Display** to **A,B,C** or **Letters** for pools of more than 9 lanes; both are decoded.

## Capturing at the pool

Follow the [general procedure](../admin.md#capturing-at-the-pool). The output's protocol is chosen in the Gen7 software (*Settings → Scoreboard → Input / Output Course Mappings*); write down which connector (Blue Square, Black Diamond) and protocol you used.

### Gen7 serial output (Gen7 Standard)

1. **Console:** the RS-485 output left at Gen7 Standard (the default).
2. **Wire:** USB-to-RS485 adapter on that output (Data+ / Data− / GND).
3. **Splouch:** `Gen7` → `Gen7 serial output`.
4. **Check:** *Connected*. Splouch sends the 4-byte start request on opening; the Serial Monitor then shows packets that start with a byte from `80` to `FF`. The payload is scrambled, so it is unreadable by eye — that is expected.
5. **Record:** `.raw` (untested), then a `.serial` if the board shows times.
6. **Also record:** 30 s with *Quick Options → Scoreboard State → Blank (with Time of Day)*. Splouch reads the clock it puts on lane 3's module as no time; the capture confirms the two modules match.
7. **Also record:** 30 s with *Quick Options → EVT/HT* swapped and team scores entered, with **Event / heat on scoreboard** set to Swapped. Set both back after.
8. **Also record:** a finished heat with *Lane Module Order* set to Place. Splouch should show the same times on the same lanes as in lane order.
9. **Also record:** 10 s each of the scoreboard self test and diagnostics. Splouch should show neither: no `88:88.88`, no letters in a time, no lane moved.
10. **Also record:** a 200 with every lane swimming, through to its results, as `.raw`. The lap count on the board should reach 8 (4 in a 50 m pool) on every lane, one touch at a time. Write down each lane's split times from the Gen7 screen: this capture is what decodes the split times the RS-485 stream carries after each lane's time (not decoded yet).
11. **Also record** (a facility with two pools on one Gen7): 30 s with both pools racing, **Pool** set to 2. Splouch should show pool 2 only; write down which pool each output was mapped to.
12. **Also record** (a meet whose start lists the meet software sends to the Gen7): 30 s around an event change, as `.raw`, with no meet file loaded in Splouch. Write down the event title and each lane's name and team as the Gen7 shows them: this capture is what decodes the swimmer names the RS-485 stream carries (command 18, not decoded yet). If you can, capture the meet PC's UDP traffic to the Gen7 (port 60287) as well.
13. **Also copy** the `.gen` files the Gen7 wrote during the session (*Meet Management File Export*, written at each Save and Reset — F1034 pp.35, 88), with a photo of the Gen7's results screen for one heat. Splouch does not read them yet: a real file is what decides its format, and whether Splouch takes it by upload (like the Lenex file on `/mm`) or from a shared folder.

### RS-485 output set to CTS Legacy

1. **Console:** that output's protocol → CTS Legacy.
2. **Wire:** USB-to-RS485 adapter on that output.
3. **Splouch:** `Gen7` → `RS-485 output set to CTS Legacy`; **Serial parity** even by default.
4. **Check:** the same short packets as the legacy output (a byte `80`–`FF`, then a few below `80`). Garbage → try **Serial parity** None.
5. **Record:** `.raw`.

### RS-485 output set to Daktronics RTD

1. **Console:** that output's protocol → Daktronics RTD.
2. **Wire:** USB-to-RS485 adapter on that output.
3. **Splouch:** `Gen7` → `RS-485 output set to Daktronics RTD`; **Baud rate** 19200 first.
4. **Check:** packets start `16 30 30 30 30 30 30 30 30 01 30 30 34` (SYN, `00000000`, SOH, `004…`). Garbage → try another **Baud rate**, then **Serial format**. Running time but no lane times → switch **RTD layout**.
5. **Record:** `.raw`. Note the baud rate and layout that worked.

### RS-485 output set to Omega

1. **Console:** that output's protocol → Omega.
2. **Wire:** USB-to-RS485 adapter on that output.
3. **Splouch:** `Gen7` → `RS-485 output set to Omega`; **Scoreboard line order** rank mode by default.
4. **Check:** packets start `01 14 52 30 32 02 08` (running time) or `01 14 53 30 32 02 08` (a line) and end with `04`. Garbage → try **Baud rate** / **Serial format**. Lanes and places swapped → switch **Scoreboard line order**.
5. **Record:** `.raw`.
