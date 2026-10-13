# Swiss Timing Omega ARES 21 — "Venus ERTD" Serial Protocol Reference

> **Status: Documented** from the ARES Swimming 2.24e scoreboard set-up and
> `fvishram/SRAYSScoreboard`. Not yet run against a live console.
>
> ERTD is Daktronics RTD — the same wire format as the OmniSport 2000 (`rtd.py`,
> `omnisport_2000_serial.md`). Implemented in `swiss_timing_ares21.py`
> (`Ares21Decoder`, decoder key `ares_ertd`, console entry `ares21_ertd`).
>
> The ARES has two other outputs, decoded elsewhere: the SCB connector in
> UNT4/GALACTICA mode (`swiss_scoreboard_serial.md`) and the GP connector in OSM6
> mode (`swiss_timing_quantum_serial.md`).

---

## Hardware Interface

ARES Swimming manual 3330.560.02 §11: *"The ERTD type is designed to drive DAKTRONICS
scoreboards running with the VENUS software."* Selected in *Configuration →
Scoreboard → Kind of scoreboard*; output on the IF-ARES **SCB** connector.

| Parameter | Value |
| --------- | ----- |
| Connector | IF-ARES SCB, Sub-D 9 female: pin 4 LD+, pin 3 LD−, pin 7 GND (also 20 mA loop on 9/8) |
| Interface | **RS-485** (when *RS485* is checked in the scoreboard set-up) |
| Baud rate | 9 600 (set in the IF-ARES) |
| Format | 8-N-1 |

Standard USB-to-RS-232 adapters will not work — use a USB-to-RS-485 adapter.

---

## Message Structure

```text
SYN 00000000 SOH 004 01 ooooo STX <data> EOT <checksum: 2 hex> ETB
```

| Part | Meaning |
| ---- | ------- |
| `SYN 00000000 SOH` | as on the OmniSport |
| `004` | message type: buffer write |
| `01` / `00` | sub-code (`01` usual) |
| `ooooo` | 5-digit offset: `VOFFSET[k] + HOFFSET[k] × line` |
| checksum | Σ bytes after SYN through EOT, mod 256, 2 uppercase hex, then ETB |

Test packet: `SYN 00000000 SOH 0040100000 STX "    x.y  " EOT`. Raw ON/OFF commands
(`SYN 70000000 SOH 003010002 EOT`, …) carry no checksum and another type; they are
dropped.

---

## Buffer Layout

ARES Swimming ERTD set-up:

```text
VOFFSET=200;225;225;108;200;69     HOFFSET=36;36;36;23;36;30     NAMELEN=20F14n5
```

plus a 7th table, `VOFFSET 100`, `HOFFSET 5`.

| k | Offset | Content |
| - | ------ | ------- |
| — | 0 | Running time, 9 chars |
| 5 | 69 | Title (≤30, left) · **event (3) at 99** · 2-char field at 102 |
| 6 | 100 (+5·line) | event (3) + heat (2) — **event at 100–102, heat at 103–104** |
| 3 | 108 + 23·n | Record line: name 12, code 2, time |
| 4 | **200 + 36·n** | Result / start-list line |
| 1, 2 | 225 + 36·(lane − 1) | 8-char time into a line's time field (reaction / running time per lane) |

This is the OmniSport OMNI2000 template shifted by −22 from offset 105 on — the
OmniSport's own **OMNI6000** layout.

### Result line (36 characters)

| Line | Layout |
| ---- | ------ |
| Result | name block 20 · lane (2) at +20 · rank (3) at +22 · space · time from **+26** |
| Start list | name 15 · club 5 · 5 chars · time (9) right-aligned at +25 |

No lengths are sent. The decoder reads lane (20, 2), place (22, 3) and time (25, 9)
stripped — the time starting at +26 reads the same.

---

## Decoder Behaviour

Shared with the OmniSport (`RtdSwimmingDecoder`): event/heat → `event_changed`;
running time leaving zero → start; a line with place + time → `lane_time`,
`lane_place`; a placed time is a finish (no lengths, so no `max_lengths` test).

Fallbacks: a line with no lane number is lane
n; a line whose place/time are not at the template offsets is searched for a time.

## Options

| Option | Values | Default | Why |
| --- | --- | --- | --- |
| `ertd_event_field` | `99` (after the title), `100` (event/heat block), `title` (text only) | `99` | The event can be written at 99 or at 100; which one a scoreboard set-up uses is unknown. A blank number falls back to `Event N … Heat N` in the title |

## Known Uncertainties

- When the sub-code is `00` instead of `01`.
- Whether table 6 (100 + 5·line) is always written at line 0.
- The SCB baud rate is an IF-ARES setting.

---

## Sources

- Swiss Timing, *Transmission protocols — ARES 21 Swimming* (doc 0100.064 v1.10, Nov 2009) and *ARES Swimming* user manual 3330.560.02, on the ARES 21 software CD
- ARES Swimming 2.24e scoreboard set-up (Venus ERTD)
- `fvishram/SRAYSScoreboard` — `AresDataHandler.cs` and `docs/PROTOCOL.md`: [github.com/fvishram/SRAYSScoreboard](https://github.com/fvishram/SRAYSScoreboard)
- Daktronics Input Template File `OS2-Swimming.itf`: <http://dakfiles.daktronics.com/downloads/Data/ITF/OS2-Swimming.itf>
