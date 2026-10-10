# Swiss Timing Omega Ares 21 — Serial Protocol Reference

> **Status: Documented** from community reverse-engineering of
> `fvishram/SRAYSScoreboard` (`AresDataHandler.cs`, MIT).
> Protocol name: **Venus ERTD** (Extended Real-Time Data).

---

## Hardware Interface

| Parameter | Value |
| --------- | ----- |
| Interface | **RS-485** (not RS-232) |
| Baud rate | **9 600** |
| Data bits | 8 |
| Parity | **None** |
| Stop bits | 1 |

> Note: the Ares 21 also supports older OSM 6 mode (RS-232, 9600, 7-E-1) but Venus ERTD over RS-485 is the scoreboard format to use.

### Wiring (DB9 cable — custom pinout)

```text
PC side (DB9 female)         ARES side (DB9 male)
  Pin 1 → T(+) / RS-485 B+    Pin 4 → T(+) / RS-485 B+
  Pin 2 → T(-) / RS-485 A-    Pin 3 → T(-) / RS-485 A-
  Pin 5 → Ground               Pin 7 → Ground
```

Standard USB-to-RS-232 adapters will not work — use a USB-to-RS-485 adapter.

---

## Message Structure

```text
SOH (0x01) | Header (10 ASCII digits) | STX (0x02) | Data (ASCII) | EOT (0x04)
```

Control characters:

- **SOH** `0x01` — start of header (packet boundary marker)
- **STX** `0x02` — start of data payload
- **EOT** `0x04` — end of message

The header is always exactly 10 ASCII digit characters.

---

## Header Codes

### Running time

| Header | Content |
| ------ | ------- |
| `0040100000` | Current running time |

Data format: `MM:SS.cc` (e.g. `01:23.45`).

### Event information

| Header | Content |
| ------ | ------- |
| `0040100069` | Event name and heat |

Data format: free text containing `Event N` and `Heat N` substrings
(e.g. `Event 12 Heat 3` or `Event 1: Men's 100m Freestyle Heat 2`).

### Lane swimmer names

| Header | Lane |
| ------ | ---- |
| `0040100200` | Lane 1 |
| `0040100236` | Lane 2 |
| `0040100272` | Lane 3 |
| `0040100308` | Lane 4 |
| `0040100344` | Lane 5 |
| `0040100380` | Lane 6 |
| `0040100416` | Lane 7 |
| `0040100452` | Lane 8 |
| `0040100488` | Lane 9 |
| `0040100524` | Lane 10 |

Stride between lanes: **36**. Formula: `0040100` + `(200 + 36 × (lane − 1))`.

Data format: swimmer name string (trimmed).

### Lane results (place + time)

| Header | Lane |
| ------ | ---- |
| `0040100220` | Lane 1 |
| `0040100256` | Lane 2 |
| `0040100292` | Lane 3 |
| `0040100328` | Lane 4 |
| `0040100364` | Lane 5 |
| `0040100400` | Lane 6 |
| `0040100436` | Lane 7 |
| `0040100472` | Lane 8 |
| `0040100508` | Lane 9 |
| `0040100544` | Lane 10 |

Stride between lanes: **36**. Formula: `0040100` + `(220 + 36 × (lane − 1))`.

Data format: place number followed by time (e.g. `1 00:54.32`).
The time is always the last `MM:SS.cc`-shaped token; the place is the integer token immediately before it.

---

## Data Examples

```text
SOH + 0040100000 + STX + 01:23.45            + EOT   running time
SOH + 0040100069 + STX + Event 1 Heat 2      + EOT   event / heat
SOH + 0040100200 + STX + John Smith          + EOT   lane 1 name
SOH + 0040100220 + STX + 1 00:54.32          + EOT   lane 1 result
```

---

## Running State Detection

The Ares 21 has no explicit "race started" or "lane running" message. The decoder infers state as follows:

- **Race started**: first running-time packet after a reset/event-change. Lanes without a result are marked `lane_running=True` — only lanes with a swimmer in the start lists (`set_heat_lanes`; every lane when no meet is loaded) — and the overlay is dismissed.
- **Lane finished**: a result packet arrives for that lane → `lane_running=False`.
- **Race reset**: new event/heat detected via the event header → all lanes cleared.

---

## Swimmer Names

The Ares 21 transmits swimmer names natively via the lane name header codes. In this application the names are **not used** — they are already supplied by the Lenex/HyTek meet-management file. The Ares names would only be needed for a standalone deployment with no Lenex integration.

---

## Split Times

Not transmitted in the Venus ERTD scoreboard format. The Ares 21 stores split data internally and transmits it to meet-management software via a separate interface.

---

## Known Uncertainties (needs hardware validation)

The framing and header dispatch are derived directly from a working open-source
implementation and should be reliable. The three areas below are inferred and will
likely need tuning once tested against a real Ares 21 or a serial capture.

### Result data format

The documented example is `1 00:54.32` (place space time). The reference C#
implementation has a likely bug where it extracts both place and time from the
second token, discarding the first. The actual wire format may have extra leading
tokens (e.g. a lane-confirmation prefix), which would shift the token positions.
If places are not showing correctly, capture a raw result packet and compare.

### Event / heat text format

The event header payload is free text. The decoder searches for `Event N` and
`Heat N` substrings (case-insensitive). If the Ares formats the string differently
(e.g. `Ev. 1 Ht. 2`, or just an event number without the word "Heat"), the
`event_changed` signal will never fire, lanes will not reset between heats, and
seed times will not reload. A capture of the event header payload from real
hardware is needed to confirm the exact format.

### Running state inference

The Ares 21 has no explicit "race started" message. The decoder marks lanes
`running=True` — only lanes with a swimmer in the start lists (`set_heat_lanes`; every lane when no meet is loaded) — on the first running-time packet that has left zero. Two known
edge cases:

- **Pre-start clock**: if the console runs the clock before the starter's gun (e.g.
  during a false-start hold), the overlay will be dismissed prematurely.
- **Intra-event restart**: if a heat is abandoned and restarted without the Ares
  sending a new event-header packet, `_race_active` is never cleared and the
  running-state transition is missed for the restart.

---

## Implementation Status

Implemented in `console_decoders/ares21.py` (`Ares21Decoder`).

| Feature | Status |
| ------- | ------ |
| Packet framing (SOH / STX / EOT) | Done |
| Running time | Done |
| Event / heat detection and lane reset | Done |
| Lane finish times and places | Done |
| Lane running state inference | Done |
| Swimmer names (Ares native) | Not implemented — Lenex integration covers this |
| Split times | Not available in Venus ERTD |

---

## Sources

- `fvishram/SRAYSScoreboard` — `AresDataHandler.cs` and `docs/PROTOCOL.md`: [github.com/fvishram/SRAYSScoreboard](https://github.com/fvishram/SRAYSScoreboard)
- Hy-Tek OSM 6 interface reference: [hytek.active.com](https://hytek.active.com/user_guides_html/swmm8/omegaosm6.htm)
- Ares 21 User Manual: [hertsssa.org.uk](http://www.hertsssa.org.uk/uploads/5/2/5/3/5253152/ares_swimming_user_manual.pdf)
