# Unused Parser Data

Fields available in the parsed data that are not currently used by the scoreboard.

## Next candidates

Weighed and left for later, most useful first. Each changes the apps' contract
(docs/api.md, docs/app.md) unless it says otherwise.

| What | From | What it would give | Why not yet |
| --- | --- | --- | --- |
| **PB / season best** | Lenex `splash.pbest` / `splash.sbest` | A "new PB" mark on an official result | Splash files only; parser, contract and both apps |
| **Official splits, reaction times, DSQ reason** | Lenex `RESULT` (`SPLITS`, `reactiontime`, `comment`); `.hy3` `G1`, `H1`/`H2` | What the officials record and the console does not | Large, and only useful once results are official |
| **A final linked to its prelim** | Lenex `EVENT preveventid` | "Seeded from the prelims" on a final | Little gain: both rounds are already listed, each named with its round (`S-24`) |
| **`.hy3` rounds, checked** | `.hy3` `E2`/`F2` column 3 | Confidence in the prelims/final mapping | The only sample is a Team Manager dual meet, all `P`; needs a recent Meet Manager prelims/finals export. No contract change |
| **Relay swimmers and rounds in a Hy-Tek CSV** | CSV rows after a relay entry; the `Heat N of M Finals` label | What a `.hy3` already gives | Superseded by `.hy3`; would need a real CSV export to place the fields. No contract change |
| **SDIF `.cl2` / `.sd3`** | USA Swimming's interchange format | Results from SwimTopia Meet Maestro or TeamUnify TouchPad | Carries no heats before the meet (checked on a sample), so it cannot drive the board; only if a club has no seeded `.hy3` or Lenex. No contract change |

---

## Lenex XML (.lxf)

### `ATHLETE` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `birthdate` | `"2015-03-22"` | Use with meet date to compute age at time of competition. |
| `gender` | `"M"` / `"F"` | Per-swimmer gender, distinct from the event-level gender. |
| `nation` | `"CAN"` | IOC 3-letter country code. |
| `license` | `"123456"` | Club/federation registration number. |

### `EVENT` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `timing` | `"AUTOMATIC"` / `"MANUAL"` | Timing system type for the event. |

### `SWIMSTYLE` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `code` | `"KICK25"` | A special event's short code; its `name` is used instead. |

### `RESULT` element

Parsed since Meet Manager results reach the Schedule (`swimtime`, `status`, `lane`,
`heatid`; docs/architecture/meet-manager-results.md). Still unused:

| Attribute / child | Example | Notes |
| --- | --- | --- |
| `reactiontime` | `"+72"` | Start reaction in hundredths. |
| `points` | `"512"` | FINA/World Aquatics points. |
| `SPLITS > SPLIT` | `distance="50" swimtime="00:00:29.10"` | Official splits — the console's are provisional. |
| `comment` | `"G-4"` | Why a swimmer was disqualified, in the judges' code. |

### `CLUB` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `name` | `"Club de natation Avantage"` | Full club name. Used only for a relay team's name, and as a lane's club when there is no `shortname` or `code`. |
| `nation` | `"CAN"` | Country of the club. |

### `EVENT` / `HEAT` links between rounds

| Attribute | Example | Notes |
| --- | --- | --- |
| `EVENT preveventid` | `"1337"` | The prelim a final is seeded from. Rounds sharing a number are read apart and listed side by side (`load_lenex`), but nothing pairs a final with its prelim yet. Read only to join Splash's extra heats (`round="EXTRAHEATS"`) to their timed final and to find a fastest heat's age groups. |

### `SESSION` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `maxentriesathlete`, `officialmeeting`, `teamleadermeeting` | `"3"` | Session rules and meeting times; `number`, `name`, `date`, `daytime`, `endtime` and the warm-up times are read. |

### `ENTRY` (Splash)

| Attribute | Example | Notes |
| --- | --- | --- |
| `splash.pbest` / `splash.sbest` | `"00:01:02.34"` | Personal and season best, Splash only. |

---

## HyTek CSV

The CSV column layout is indexed relative to the `Lane` header row (≈ row 92).
All value rows are at `header_position + column_offset` where `column_offset` is
computed per-entry (typically 6).

| Field | How to find it | Notes |
| --- | --- | --- |
| **Age** | `row.index("Age", lane_header, 107) + column_offset` | Individual events only. Integer string, e.g. `"10"`. |
| **Relay member names** | Rows 105–108 after the relay entry | Each row is `"Lastname, Firstname Age"`. Up to 4 swimmers per relay team. |
| **Qualifying standard** | Two rows after seed time value | A label like `"DIST"`, `"AAA"`, `"BB"`. Not always present. |

---

## Hy-Tek HY3

Read by `hy3_parser.load_hy3`; columns 1-based. Not read yet:

| Record | Field | Notes |
| --- | --- | --- |
| `D1` | nickname 49-68, birth date 89-96, age 97-99 | The preferred name, and the swimmer's age. |
| `E1` / `F1` | converted seed 43-50, exhibition 84 (`X`) | An exhibition swimmer is shown like any other. |
| `E2` / `F2` | course 12, places 27-33, reaction times | |
| `G1` | splits | Official splits. |
| `H1` / `H2` | disqualification text | The reason for a `DSQ`. |
| `C2` / `C3` | team address and contacts | |
