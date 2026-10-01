# Unused Parser Data

Fields available in the parsed data that are not currently used by the scoreboard.

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

**Already parsed but worth noting:**

- Relay letter (`A`, `B`, …) — added in this session.
- Seed time — added in this session.

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
| `round` | `"HEATS"` / `"FINAL"` / `"SEMIFINAL"` | Could replace or augment the heat number in the header (e.g. show "Final" instead of "Heat 1 of 1"). |
| `timing` | `"AUTOMATIC"` / `"MANUAL"` | Timing system type for the event. |

### `HEAT` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `daytime` | `"10:30"` | Scheduled start time of the heat. Could be shown in the header. |

### `SWIMSTYLE` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `relaycount` | `"4"` | Number of relay legs. `1` for individual events. Could be used to detect relay without checking for missing `athleteid`. |
| `technique` | `"BREASTSTROKE"` | Used in some masters meets to specify a non-standard stroke variation. |

### `CLUB` element

| Attribute | Example | Notes |
| --- | --- | --- |
| `name` | `"Club de natation Avantage"` | Full club name. Currently only `shortname` (e.g. `"AVAN"`) is used. |
| `nation` | `"CAN"` | Country of the club. |

### `RELAY` / `RELAYPOSITIONS`

The `RELAY` element under `CLUB` can contain `RELAYPOSITION` child elements,
each with an `athleteid`, a `number` (leg 1–4), and optionally a `reactiontime`
and split. The full relay team roster is therefore available but not parsed.
