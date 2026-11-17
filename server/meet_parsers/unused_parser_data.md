# Unused Parser Data

Fields available in the parsed data that are not currently used by the scoreboard.

---

## Lenex XML (.lxf)

### `ATHLETE` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `birthdate` | `"2015-03-22"` | Use with meet date to compute age at time of competition. |
| `gender` | `"M"` / `"F"` | Per-swimmer gender, distinct from the event-level gender. |
| `nation` | `"CAN"` | IOC 3-letter country code. |
| `license` | `"123456"` | Club/federation registration number. |

### `ENTRY` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `entrycourse` | `"SCM"` / `"LCM"` / `"SCY"` | Pool type the seed time was swum in. A SCY seed vs a SCM finish makes the delta less meaningful. |

### `EVENT` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `timing` | `"AUTOMATIC"` / `"MANUAL"` | Timing system type for the event. |

### `SWIMSTYLE` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `relaycount` | `"4"` | Number of relay legs. `1` for individual events. Could be used to detect relay without checking for missing `athleteid`. |
| `technique` | `"BREASTSTROKE"` | Used in some masters meets to specify a non-standard stroke variation. |

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
| `name` | `"Club de natation Avantage"` | Full club name. Currently only `shortname` (e.g. `"AVAN"`) is used. |
| `nation` | `"CAN"` | Country of the club. |

### `EVENT` / `HEAT` links between rounds

| Attribute | Example | Notes |
| --- | --- | --- |
| `EVENT preveventid` | `"1337"` | The prelim a final is seeded from. Rounds sharing a number are read apart and listed side by side (`load_lenex`), but nothing pairs a final with its prelim yet. |

### `SESSION` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `name` | `"Samedi pm"` | Only `date`, `daytime`, `endtime` and the warm-up times are read. |

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
