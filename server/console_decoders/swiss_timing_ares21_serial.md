# Swiss Timing Omega Ares 21 — Serial Protocol Reference

> **Status: Partly documented, partly inferred.** Offsets from community
> reverse-engineering of `fvishram/SRAYSScoreboard` (`AresDataHandler.cs`, MIT);
> the result-line layout inferred from the Daktronics swimming template. Not yet
> run against a live console.
>
> Protocol name: **Venus ERTD**. Venus is Daktronics' display-control software, so
> this is **Daktronics RTD** — the same wire format as the Omnisport 2000
> (`rtd.py`, `omnisport_2000_serial.md`).

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

Daktronics RTD (see `omnisport_2000_serial.md` for the framing, checksum and how the
buffer works):

```text
SYN 00000000 SOH 004010oooo STX <data> EOT <checksum: 2 hex> ETB
```

`004010` is the Ares's header prefix; `oooo` is the offset into a 4096-byte buffer
where `<data>` is written. SRAYS shows frames starting at SOH with no SYN or
checksum; the decoder accepts both, and verifies the checksum only when the SYN is
there.

---

## Buffer Layout

| Offset | Len | Field | Source |
| -----: | --: | ----- | ------ |
| 0 | 9 | Running time | SRAYS (`0040100000`) |
| 69 | 30 | Event title — free text with `Event N` / `Heat N` | SRAYS (`0040100069`) |
| 99 | 3 | Event number | inferred (template) |
| 103 | 2 | Heat number | inferred (template) |
| 126 | 2 | Maximum lengths | inferred (template) |
| 200 + 36·(n−1) | 36 | Result line n, n = 1…10 | SRAYS (names at +0, results at +20) |

### Result line (inferred)

SRAYS gives only where a line starts and that its result half starts 20 characters
in. That matches the Daktronics swimming template's line exactly — same 36-character
stride, name and team in the first 20 — so the decoder reads it with that layout:

| Offset in line | Len | Field |
| -------------: | --: | ----- |
| 0 | 15 | Swimmer name (ignored — Lenex supplies names) |
| 15 | 5 | Team |
| 20 | 2 | Lane number |
| 22 | 3 | Place |
| 25 | 9 | Split / finish time |
| 34 | 2 | Lengths completed |

Every other known Ares offset matches the template too: running time at 0, event
title at 69. Only the start of line 1 differs (200 here, 222 on the Omnisport) —
presumably fewer record fields ahead of the lanes.

**Fallbacks**, for while this is unconfirmed:

- No lane number in the line → line n is lane n, as SRAYS assumes.
- Place or time not where the template puts them → the first time anywhere in the
  line's result half, and the number just before it as the place. This reads
  SRAYS's own example, `1 00:54.32` at `0040100220`, as lane 1 place 1.
- Event/heat: the number fields (99, 103) when both hold digits, else `Event N` and
  `Heat N` in the title text.

---

## Decoder Behaviour

Shared with the Omnisport (`RtdSwimmingDecoder`):

| Field change | Emitted |
| ------------ | ------- |
| Event / heat | `current_event`, `current_heat`; on a new pair, `event_changed` and a lane reset |
| Running time | `running_time`. Leaving zero is the start: untimed lanes go `lane_running=True` — only lanes with a swimmer in the start lists (`set_heat_lanes`) — and `dismiss_overlay`. Returning to zero re-arms the start |
| Result line with place + time | `lane_time`, `lane_place`, `lane_splits` (lengths, when the line has them) |
| …and lengths ≥ maximum lengths (or either unknown) | `lane_running=False` — the finish |
| Result line blank in place and time | lane cleared |

---

## Known Uncertainties (needs hardware validation)

- **Result-line layout.** Inferred, as above. A capture of one split and one finish
  settles it; if the fallback is what decodes it, the template guess was wrong.
- **Event / heat.** If the Ares fills neither the number fields nor an `Event N …
  Heat N` title, `event_changed` never fires, lanes are not reset between heats and
  seed times do not reload.
- **Pre-start clock.** A clock running before the start (a false-start hold) is
  taken as the start.

---

## Sources

- `fvishram/SRAYSScoreboard` — `AresDataHandler.cs` and `docs/PROTOCOL.md`: [github.com/fvishram/SRAYSScoreboard](https://github.com/fvishram/SRAYSScoreboard)
- Daktronics Input Template File `OS2-Swimming.itf` (the line layout): <http://dakfiles.daktronics.com/downloads/Data/ITF/OS2-Swimming.itf>
- Daktronics RTD framing as decoded by the AllSport 5000 projects, e.g. `scorebox-consoles`: <https://pypi.org/project/scorebox-consoles/>
- Ares 21 User Manual: [hertsssa.org.uk](http://www.hertsssa.org.uk/uploads/5/2/5/3/5253152/ares_swimming_user_manual.pdf)
