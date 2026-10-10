# Daktronics Omnisport 2000 — RTD Serial Protocol Reference

> **Status: Documented from Daktronics' own field template, checked against a
> published capture.** The field layout comes from Daktronics' Input Template File
> `OS2-Swimming.itf`; the framing and checksum were worked out from XY Kao's raw RTD
> dump, and every address in that dump lands on a field boundary of the template.
> Not yet run against a live console.

---

## Hardware Interface

From the OmniSport 2000 manual (ED-13312), appendix D, *RTD Output Specifications*:

| Parameter | Value |
| --------- | ----- |
| Connector | DB-9, **J5 RTD Port** |
| Interface | RS-232 (TX only is needed) |
| Baud rate | 19 200 |
| Data bits | 8 |
| Parity | None |
| Stop bits | 1 |
| Protocol | "Enhanced" |
| Buffer size | 4096 |

**J6 is the wrong port.** It is the Results Port, which talks to Meet Manager in its
own protocol. The scoreboard feed is J5.

Console settings: **RTD Port → RTD** (not CTS), the **Swimming** sport mode, and RTD
output in Omni 2000 item numbers (selecting *Omni 6000* changes every address).

---

## Packet Framing

```text
SYN  00000000  SOH  004210oooo  STX  <data>  EOT  cc  ETB
0x16 8 × '0'   0x01 10 digits   0x02 ASCII   0x04 2 hex 0x17
```

| Part | Meaning |
| ---- | ------- |
| `00000000` | Always zeros in the capture; counted in the checksum |
| `004210` | Fixed prefix for this sport mode |
| `oooo` | Offset, in characters, into the 4096-byte RTD buffer |
| `<data>` | ASCII written at that offset — may cover part of one field or run across several |
| `cc` | Checksum: sum of every byte after SYN up to and including EOT, mod 256, as two uppercase hex digits |

Checksum worked example (from the capture):

```text
SYN 00000000 SOH 0042100000 STX "    0.1  " EOT "BD" ETB
sum('00000000' + SOH + '0042100000' + STX + '    0.1  ' + EOT) & 0xFF = 0xBD
```

The decoder (`rtd.py`, shared with the ARES 21) keeps a copy of the buffer, writes each packet's data at its offset,
then reads the fields it needs from the copy. This handles partial and multi-field
writes without caring how the console chooses to chunk them.

**Packet start detection:** `byte == 0x16` (SYN).

---

## Field Map (from `OS2-Swimming.itf`)

Offsets are the running total of the template's field lengths.

| Offset | Len | Field |
| -----: | --: | ----- |
| 0 | 9 | Running time (`88:88.888`, left-justified) |
| 9 | 30 | Event title line 1 |
| 39 | 30 | Event title line 2 |
| 69 | 30 | Event title lines 1 & 2 |
| 99 | 3 | **Event number** |
| 102 | 1 | Event number — alpha |
| 103 | 2 | **Heat number** |
| 105 | 20 | Heat number — alpha |
| 125 | 1 | Round |
| 126 | 2 | **Maximum lengths to complete** |
| 128 | 2 | Lengths completed |
| 130–221 | | Records 1–4 (name 12, code 2, time 9) |
| **222 + 36·(n−1)** | 36 | **Result line n**, n = 1…10 (below) |
| 582 | 386 | Reserved |
| 968–999 | | Home / guest abbreviations and scores |
| 1000 | 36 | Single-line display (same layout as a result line) |
| 1036 | 31 | Single-line logo |
| 1100 + 9·(n−1) | 9 | Line n subtractive time |
| 1484+ | | Official results flag, team scores (women / men / combined) |

### Result line (36 characters)

| Offset in line | Len | Field |
| -------------: | --: | ----- |
| 0 | 15 | Swimmer name |
| 15 | 5 | Team |
| 20 | 2 | **Lane number** (right-justified) |
| 22 | 3 | **Place** (right-justified) |
| 25 | 9 | **Split / finish time** (left-justified) |
| 34 | 2 | **Lengths completed** (right-justified) |

A *line* is a row on the board, not a lane: the console may sort lines by place, so
the decoder always uses the line's own lane number.

### Cross-check against the published capture

| Header in dump | Template field |
| -------------- | -------------- |
| `0042100000` + `4.0` (padded to 9) | Running time |
| `0042100009` `Mixed 10&U 100 Fly` | Event title line 1 |
| `0042100039` `Timed Finals` | Event title line 2 |
| `0042100069` `X 10&U 100 Fly Timed Finals` | Event title lines 1 & 2 |
| `0042100099` + `5`, `1`, `F`, `4` (padded fields) | Event 5, heat 1, round F, 4 lengths |
| `0042100258` `Matsuyama, Neo BUCS  2` | Line 2: name, team, lane 2 |
| `0042101025` + `0.1` (padded to 9) | Single-line time (mirrors the clock) |
| `0042101100` … `0042101181` | Line 1–10 subtractive time |

---

## Decoder Behaviour

| Field change | Emitted |
| ------------ | ------- |
| Event / heat | `current_event`, `current_heat`; on a new pair, `event_changed` and a lane reset |
| Running time | `running_time` (as sent, stripped). Leaving zero is the start: lanes without a place go `lane_running=True`, only lanes with a swimmer in the start lists (`set_heat_lanes`; every lane when no meet is loaded), `dismiss_overlay`. Returning to zero re-arms the start for a restart |
| Result line with place + time | `lane_time`, `lane_place`, `lane_splits` (= lengths completed) |
| …and lengths ≥ maximum lengths (or either is blank) | `lane_running=False` — the finish |
| Result line blank in place and time | lane time and place cleared |

A line with a time but no place is ignored: it may be showing the running clock.
Times are normalised to `M:SS.CC` / `SS.CC`; thousandths are truncated.

Names, team scores and records are ignored — Lenex supplies the names.

---

## Known Uncertainties (needs hardware validation)

- **Lines during a race.** Whether a line's time field shows the running clock
  before a touch is not known; the decoder only accepts a time that has a place.
- **The `00000000` field** was zeros throughout the capture. If it ever carries
  something else, it is still covered by the checksum as implemented.
- **Header prefix.** Only `004210` is accepted. Other sport modes or Omni 6000
  item numbers are dropped on purpose — their offsets mean different fields.

---

## Sources

- Daktronics, *OmniSport 2000 Timing Console and Pro Software Operations Manual* (ED-13312), appendix D: <https://www.daktronics.com/web-documents/customer-service-manuals/ed13312.pdf>
- Daktronics Input Template File `OS2-Swimming.itf`: <http://dakfiles.daktronics.com/downloads/Data/ITF/OS2-Swimming.itf>
- XY Kao — *Decoding the Daktronics Omnisport 2000* (raw RTD capture): <https://xy-kao.com/projects/decoding-daktronics-omnisport-2000/>
