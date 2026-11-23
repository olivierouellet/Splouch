"""Hy-Tek `.hy3` parser: a Meet Manager or Team Manager entries/results export.

Fixed-width lines, one record each, the code in columns 1-2. Columns below are
1-based, as Hy-Tek's own layout and SwimComm/hytek-parser (MIT) give them:

  B1  meet: name 3-47, facility 48-92, start date 93-100, end date 101-108
      (MMDDYYYY)
  B2  meet: course 99 (S/M/1 SCM, Y/2 SCY, L/3 LCM)
  C1  team: code 3-7, name 8-37 — the swimmers after it are its own
  D1  swimmer: meet ID 4-8, last name 9-28, first name 29-48
  E1  individual entry: swimmer ID 4-8, gender 14, gender/age 15, distance 16-21,
      stroke 22, age min 23-25 and max 26-28, event number 39-42, seed 52-59,
      seed course 60
  F1  relay entry: team 3-7, relay letter 8, then E1's columns from 14 on
  E2  one round of the entry above (F2 for a relay): round 3 (P prelims, F
      finals, S swim-off), time 4-11, time code 13, heat 21-23, lane 24-26
  F3  relay legs: up to 8 slots of 13 columns from 3, the swimmer ID at 4-8 and
      the leg at 15 of each

A seeded export carries the heat and lane on the E2/F2 line with no time; a
results export adds the time. The format has no heat times and no sessions.

Returns the same `LenexData` as `load_lenex`, so rounds, the Schedule and every
client work unchanged. Round keys: Hy-Tek writes `P` for the only round of a
timed-final meet too, so a number's `P` and `F` are named prelims and final only
when it has both; a swim-off is named always.
"""

import contextlib
import datetime
import re
from typing import Any

from meet_parsers.lenex_parser import LenexData
from splouch_times import wire_time

# The biggest file we will read. A season's merge export is a few MB.
MAX_HY3_BYTES = 64 * 1024 * 1024

# E2/F2 column 3, in running order: prelims, then any swim-off, then the final.
_ROUND_ORDER = {"P": 0, "S": 1, "F": 2}
_ROUND_KEYS = {"P": "prelims", "S": "swimoff", "F": "final"}

# E2/F2 column 13. `S` (scratch) empties the lane, as a Lenex withdrawal does.
_NON_FINISH = {"Q": "DSQ", "F": "DSQ", "R": "DNS", "D": "DNF"}
_SCRATCH = "S"

_STROKES = {
    "A": "Freestyle",
    "1": "Freestyle",
    "B": "Backstroke",
    "2": "Backstroke",
    "C": "Breaststroke",
    "3": "Breaststroke",
    "D": "Butterfly",
    "4": "Butterfly",
    "E": "Medley",
    "5": "Medley",
}

_COURSES = {
    "S": "SCM",
    "M": "SCM",
    "1": "SCM",
    "Y": "SCY",
    "2": "SCY",
    "L": "LCM",
    "3": "LCM",
}
_COURSE_METRES = {"LCM": 50, "SCM": 25, "SCY": 25}


def _col(line, start, length):
    """Columns *start*..*start*+*length*-1 of *line*, 1-based, stripped."""
    return line[start - 1 : start - 1 + length].strip()


def _int(text):
    text = text.strip()
    return int(text) if text.isdigit() else None


def _event_number(text):
    """`11` from `11` or `11A`: the console only ever sends the number."""
    m = re.match(r"\d+", text.strip())
    return int(m.group()) if m else None


def _time(text):
    """`HH:MM:SS.hh` from Hy-Tek's seconds (`54.15`, `125.34`), `""` for none."""
    m = re.fullmatch(r"(\d+)\.(\d{2})", text.strip())
    if not m:
        return ""
    h = int(m.group(1)) * 100 + int(m.group(2))
    if not h:
        return ""
    return wire_time(f"{h // 6000}:{h // 100 % 60:02d}.{h % 100:02d}")


def _seed_course(line, meet_course):
    """The seed's course (col 60) when it is not the meet's, else ""."""
    course = _COURSES.get(_col(line, 60, 1).upper(), "")
    return course if course and meet_course and course != meet_course else ""


def _date(text):
    """ISO date from MMDDYYYY, `""` when it is not one."""
    with contextlib.suppress(ValueError):
        return datetime.datetime.strptime(text.strip(), "%m%d%Y").date().isoformat()
    return ""


def _event_name(line, relay):
    """A name `i18n.parse_event_name` reads back into the same parts:
    `Girls 6 & Under 25 Freestyle`, `Mixed 100 Medley Relay`."""
    gender = _col(line, 14, 1).upper()
    gender_age = _col(line, 15, 1).upper()
    dist = _int(_col(line, 16, 6)) or 0
    stroke = _STROKES.get(_col(line, 22, 1).upper(), "")
    lo = _int(_col(line, 23, 3)) or 0
    hi = _int(_col(line, 26, 3)) or 0
    if hi >= 99:
        hi = 0
    if lo and hi:
        age = f"{lo}-{hi}"
    elif hi:
        age = f"{hi} & Under"
    elif lo:
        age = f"{lo} & Over"
    else:
        age = ""
    # Hy-Tek's own word for a junior event is Girls/Boys; Team Manager writes
    # only F/M, so an age limit of 18 or under is what says junior.
    youth = bool(hi and hi <= 18)
    if gender == "X" or gender_age == "X":
        word = "Mixed"
    elif gender_age in ("G", "B", "W"):
        word = {"G": "Girls", "B": "Boys", "W": "Women"}[gender_age]
    elif gender == "F":
        word = "Girls" if youth else "Women"
    elif gender == "M":
        word = "Boys" if youth else "Men"
    else:
        word = ""
    if stroke == "Medley" and not relay:
        stroke = "Individual Medley"
    words = [word, age, str(dist) if dist else "", stroke, "Relay" if relay else ""]
    return " ".join(w for w in words if w), dist


def _read_lines(path):
    with open(path, "rb") as f:
        data = f.read(MAX_HY3_BYTES + 1)
    if len(data) > MAX_HY3_BYTES:
        raise ValueError(
            f"{path} is larger than {MAX_HY3_BYTES // (1024 * 1024)} MB; "
            f"refusing to parse it."
        )
    # Hy-Tek writes Windows-1252; every byte decodes, so a stray one cannot stop
    # the meet from loading.
    return data.decode("cp1252", errors="replace").splitlines()


def load_hy3(path):
    """Parse a Hy-Tek `.hy3` file into a `LenexData` (see `load_lenex`)."""
    meet_info = {}
    swimmers = {}  # meet ID → (last, first, team code)
    team, team_name = "", ""
    names = {}  # event → name
    distances = {}  # event → distance
    # event → round letter → heat → lane → entry
    starts = {}
    results = {}  # (event, letter) → heat → lane → result
    # The E1/F1 being read: its event and lane entry.
    entry: tuple[int, dict[str, Any]] | None = None
    placed: list[dict[str, Any]] = []  # where it was seeded, for F3

    for line in _read_lines(path):
        code = line[:2]
        if code == "Z0":
            break
        if code == "B1":
            meet_info["name"] = _col(line, 3, 45)
            meet_info["pool"] = _col(line, 48, 45)
            start, end = _date(_col(line, 93, 8)), _date(_col(line, 101, 8))
            meet_info["dates"] = [d for d in dict.fromkeys((start, end)) if d]
        elif code == "B2":
            course = _COURSES.get(_col(line, 99, 1).upper(), "")
            meet_info["course"] = course
            meet_info["pool_length_lenex"] = _COURSE_METRES.get(course, 0)
        elif code == "C1":
            team = _col(line, 3, 5)
            team_name = _col(line, 8, 30) or team
        elif code == "D1":
            sid = _int(_col(line, 4, 5))
            if sid is not None:
                swimmers[sid] = (_col(line, 9, 20), _col(line, 29, 20), team)
        elif code in ("E1", "F1"):
            relay = code == "F1"
            ev = _event_number(_col(line, 39, 4))
            entry, placed = None, []
            if ev is None:
                continue
            if ev not in names:
                names[ev], dist = _event_name(line, relay)
                if dist:
                    distances[ev] = dist
            if relay:
                club = _col(line, 3, 5) or team
                letter = _col(line, 8, 1)
                name = f"{team_name if club == team else club} {letter}".strip()
            else:
                last, first, club = swimmers.get(
                    _int(_col(line, 4, 5)) or -1, ("", "", team)
                )
                name = f"{first} {last}".strip()
            entry = (
                ev,
                {
                    "name": name,
                    "club": club,
                    "seed_time": _time(_col(line, 52, 8)),
                    "seed_course": _seed_course(line, meet_info.get("course", "")),
                    "swimmers": [],
                },
            )
        elif code in ("E2", "F2") and entry is not None:
            ev, lane_entry = entry
            letter = _col(line, 3, 1).upper()
            heat, lane = _int(_col(line, 21, 3)), _int(_col(line, 24, 3))
            if letter not in _ROUND_ORDER or not heat or lane is None:
                continue
            time_code = _col(line, 13, 1).upper()
            if time_code == _SCRATCH:
                continue
            rnd = starts.setdefault(ev, {}).setdefault(letter, {})
            # A new dict per round: F3 fills the legs of every round at once.
            seeded = dict(lane_entry)
            rnd.setdefault(heat, {})[lane] = seeded
            placed.append(seeded)
            swim = _time(_col(line, 4, 8))
            if swim or time_code in _NON_FINISH:
                results.setdefault((ev, letter), {}).setdefault(heat, {})[lane] = {
                    "time": swim,
                    "status": _NON_FINISH.get(time_code, ""),
                }
        elif code == "F3" and entry is not None:
            legs: list[dict[str, Any]] = list(entry[1]["swimmers"])
            for slot in range(8):
                off = slot * 13
                sid = _int(_col(line, 4 + off, 5))
                leg = _int(_col(line, 15 + off, 1))
                if sid is None or leg is None:
                    break
                last, first, _club = swimmers.get(sid, ("", "", ""))
                if first or last:
                    legs.append(
                        {"pos": leg, "name": f"{first} {last}".strip(), "first": first}
                    )
            legs.sort(key=lambda s: s["pos"])
            entry[1]["swimmers"] = legs
            for seeded in placed:
                seeded["swimmers"] = legs

    # Every round of every number, then the running order: prelims of every
    # event, then swim-offs, then finals — the format has no sessions to say more.
    ordered: list[tuple[tuple[int, int], dict[str, Any]]] = []
    live = {}
    live_rounds = {}
    for ev in sorted(starts):
        letters = sorted(starts[ev], key=_ROUND_ORDER.__getitem__)
        shared = len(letters) > 1
        seeded = [k for k in letters if any(starts[ev][k].values())]
        live[ev] = seeded[-1] if seeded else letters[0]
        if shared:
            live_rounds[ev] = _ROUND_KEYS[live[ev]]
        for letter in letters:
            kind = _ROUND_KEYS[letter] if shared or letter == "S" else ""
            ordered.append(
                (
                    (_ROUND_ORDER[letter], ev),
                    {
                        "event": ev,
                        "round": _ROUND_KEYS[letter] if shared else "",
                        "kind": kind,
                        "live": live[ev] == letter,
                        "name": names.get(ev, f"Event {ev}"),
                        "date": "",
                        "session": 0,
                        "session_name": "",
                        "start_list": starts[ev][letter],
                        "heat_times": {},
                        "heat_ages": {},
                        "heat_finals": {},
                        "results": results.get((ev, letter), {}),
                    },
                )
            )
    rounds = [r for _, r in sorted(ordered, key=lambda o: o[0])]

    # A one-day meet's date is every event's, as a Lenex session's would be
    # (docs/app.md `N-05`). Over several days nothing says which event is when.
    dates = meet_info.pop("dates", [])
    if len(dates) == 1:
        meet_info["sessions"] = [
            {
                "number": 0,
                "name": "",
                "date": dates[0],
                "daytime": "",
                "endtime": "",
                "warmupfrom": "",
                "warmupuntil": "",
                "events": sorted(live),
            }
        ]
        for r in rounds:
            r["date"] = dates[0]
    else:
        meet_info["sessions"] = []
        if dates:
            meet_info["dates"] = dates

    return LenexData(
        event_names={ev: names.get(ev, f"Event {ev}") for ev in live},
        start_list={ev: starts[ev][live[ev]] for ev in live},
        heat_times={ev: {} for ev in live},
        meet_info=meet_info,
        event_distances={ev: d for ev, d in distances.items() if ev in live},
        event_rounds={
            ev: _ROUND_KEYS[live[ev]]
            for ev in live
            if ev in live_rounds or live[ev] == "S"
        },
        results={
            ev: results[(ev, live[ev])] for ev in live if (ev, live[ev]) in results
        },
        live_rounds=live_rounds,
        rounds=rounds,
    )
