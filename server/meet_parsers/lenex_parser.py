import contextlib
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import NamedTuple
from xml.parsers import expat

from splouch_times import wire_time


class LenexData(NamedTuple):
    """What `load_lenex` returns; the shapes are in its docstring."""

    event_names: dict
    start_list: dict
    heat_times: dict
    meet_info: dict
    event_distances: dict
    event_rounds: dict
    results: dict
    live_rounds: dict
    rounds: list


# HEAT (or EVENT) `status` values whose results Splouch shows as official.
# Splash leaves a heat whose results were typed in by hand `SEEDED`. Its
# `INOFFICIAL` (the spec's `UNOFFICIAL`) is left out on purpose: those are the
# console's own times, which Splouch already has, and by the time a meet file is
# uploaded they should all have been validated. The status is optional (Lenex
# 3.0): a heat without one goes by its EVENT's, and a file that writes no status
# on any heat or event — an exporter that does not track it — has its results
# kept as they are.
KEPT_HEAT_STATUSES = {"OFFICIAL", "SEEDED"}

# RESULT `status` values that are not a finish (Lenex 3.0). `EXH` (exhibition) is
# a finish with a valid time, so it is not here.
NON_FINISH_STATUSES = {"DSQ", "DNS", "DNF", "WDR", "SICK"}

# ENTRY `status` values of a swimmer who will not swim, whose lane stays empty:
# withdrawn, sick, rejected (Lenex 3.0) and Splash's declared non-start. `EXH`
# (exhibition) swims, so it is not here.
NOT_SWIMMING_ENTRY_STATUSES = {"WDR", "SICK", "RJC", "DNS"}


# EVENT `round` codes, as `[event_name]` vocabulary keys. Every code the Lenex
# spec defines; the three swim-offs share one word, since a spectator does not care
# which round forced it. Which of them a board or a phone actually shows is a
# setting (`round_names_board` / `round_names_phone`, meet_data.py).
ROUND_KEYS = {
    "TIM": "timed_final",
    "FHT": "fastest_heat",
    "PRE": "prelims",
    "QUA": "quarterfinal",
    "SEM": "semifinal",
    "FIN": "final",
    "SOP": "swimoff",
    "SOQ": "swimoff",
    "SOS": "swimoff",
    "TIMETRIAL": "time_trial",
}

# The distinct keys, in running order — the Settings checkboxes and their default.
ROUND_NAMES = tuple(dict.fromkeys(ROUND_KEYS.values()))


# Biggest inner XML we will read out of a .lxf. A real meet's start list is a few
# hundred KB; this is room for an unusually large one and a hard stop well before
# a crafted archive can exhaust the Pi's memory. `ZipFile.open` streams, so an
# archive that claims a small size and then expands (a zip bomb) is caught by
# reading through this cap rather than by trusting the header.
MAX_XML_BYTES = 64 * 1024 * 1024


def age_band(agemin, agemax):
    """An AGEGROUP's ages as `event_name_parts.age` writes a band, numbers only:
    `"13-14"`, `"≤7"` (7 and under), `"17+"`, `"8"`; `""` for no limit either way.
    Lenex writes no limit as `-1`."""

    def num(v):
        v = (v or "").strip()
        return int(v) if v.isdigit() and int(v) > 0 else None

    lo, hi = num(agemin), num(agemax)
    if lo and hi:
        return str(lo) if lo == hi else f"{lo}-{hi}"
    if hi:
        return f"≤{hi}"
    if lo:
        return f"{lo}+"
    return ""


# Pool length per `course` code (Lenex 3.0 §6.4): the short-course codes with a
# number carry the length in it (`SCM33`, `SCY20`); OPEN is open water.
_COURSE_LENGTHS = {"LCM": 50, "SCM": 25, "SCY": 25}


def course_length(course):
    """A `course` code's pool length, 0 for none or one Splouch cannot read."""
    course = (course or "").strip().upper()
    if course in _COURSE_LENGTHS:
        return _COURSE_LENGTHS[course]
    m = re.fullmatch(r"SC[MY](\d{2})", course)
    return int(m.group(1)) if m else 0


def _club_short(club):
    """A CLUB's short name: the first of `shortname`, `code` and `name` that is
    not empty. An UNATTACHED club may have none of them."""
    return next(
        (v.strip() for k in ("shortname", "code", "name") if (v := club.get(k, ""))),
        "",
    )


def _pool_lanes(pool_el, uses_lane_0=False):
    """``[first, last]`` lane of a POOL, ``None`` without a usable `lanemax`.

    Without a `lanemin`, the pool starts at lane 1 unless the file seeds or times
    a swim in lane 0: SPLASH writes only `lanemax` for a pool numbered 0–9.
    """
    if pool_el is None:
        return None
    try:
        last = int(pool_el.get("lanemax", ""))
        first = int(pool_el.get("lanemin") or (0 if uses_lane_0 else 1))
    except ValueError:
        return None
    if first not in (0, 1) or not first < last <= 12:
        return None
    return [first, last]


def _session_number(session):
    """A SESSION's `number`, 0 for none or one that is not a number."""
    num = (session.get("number", "") if session is not None else "").strip()
    return int(num) if num.isdigit() else 0


class _PrologRead(Exception):
    """The root element began: the prolog held no DOCTYPE."""


def _refuse_doctype(*_):
    raise ValueError("Lenex file contains a DOCTYPE declaration; refusing to parse it.")


def _check_no_doctype(data):
    """Refuse a document type declaration before handing bytes to the parser.

    ElementTree expands entities defined in an internal DTD, so a file carrying
    the classic nested definitions ("billion laughs") expands to gigabytes during
    the parse and takes the server down with it — from an upload, on the machine
    running the meet. No Lenex exporter emits a DOCTYPE, so refusing one removes
    the whole class of attack rather than reasoning about which entity forms are
    safe.

    Asked of expat itself, stopped at the root element, rather than looked for in
    the bytes: a scan for `<!DOCTYPE` read a UTF-16 file — or any encoding the
    parser decodes and a byte search does not — as a clean prolog, and the parse
    behind it expanded the entities anyway. Expat reports the declaration in
    whatever encoding the file is in, before it expands anything.
    """
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = _refuse_doctype
    parser.EntityDeclHandler = _refuse_doctype

    def root_reached(*_):
        raise _PrologRead

    parser.StartElementHandler = root_reached
    try:
        parser.Parse(data, True)
    except _PrologRead:
        return
    except expat.ExpatError:
        return  # malformed before the root: the real parse reports it


def _open_lenex_xml(path):
    """Return the parsed XML tree for a Lenex file at *path*.

    A .lxf file is a zip that, per the spec, holds a single .lef XML. Real-world
    exporters vary, so be forgiving: match the inner file case-insensitively, and
    if none ends in .lef fall back to a .xml member or the only file present. A
    plain (unzipped) .lef XML file is also accepted.
    """
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            xml_name = (
                next((n for n in names if n.lower().endswith(".lef")), None)
                or next((n for n in names if n.lower().endswith(".xml")), None)
                or (names[0] if len(names) == 1 else None)
            )
            if xml_name is None:
                raise ValueError(
                    f"No Lenex XML (.lef) found inside {path}; "
                    f"archive contains: {', '.join(names) or '(empty)'}"
                )
            with z.open(xml_name) as fh:
                data = fh.read(MAX_XML_BYTES + 1)
            if len(data) > MAX_XML_BYTES:
                raise ValueError(
                    f"Lenex XML inside {path} is larger than "
                    f"{MAX_XML_BYTES // (1024 * 1024)} MB; refusing to parse it."
                )
            _check_no_doctype(data)
            return ET.ElementTree(ET.fromstring(data))
    # Not a zip — assume a raw .lef/.xml Lenex document.
    with open(path, "rb") as fh:
        data = fh.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise ValueError(
            f"{path} is larger than {MAX_XML_BYTES // (1024 * 1024)} MB; "
            f"refusing to parse it."
        )
    _check_no_doctype(data)
    return ET.ElementTree(ET.fromstring(data))


def load_lenex(path):
    """
    Parse a Lenex .lxf file (zip containing a .lef XML).

    Returns a LenexData:
        event_names  — {event_number: str}
        start_list   — {event_number: {heat_number: {lane: {'name': str, 'club': str,
                       'seed_time': str, 'seed_course': str, 'swimmers': list}}}};
                       `seed_course` is the ENTRY's `entrycourse` when it is not
                       the meet's (`LCM` for a long-course seed at a short-course
                       meet), else "": a gap to such a seed means nothing.
        event_rounds — {event_number: round key}; see `ROUND_KEYS`
        results      — {event_number: {heat_number: {lane: {'time': str, 'status': str}}}},
                       only from heats kept by `KEPT_HEAT_STATUSES`.
                       `time` is `HH:MM:SS.hh` or `""`; `status` is one of
                       `NON_FINISH_STATUSES` or `""`.
        live_rounds  — {event_number: round}, only for a number the file uses for
                       more than one round: the live one's `round` in `rounds`.
        rounds       — every round, live or not, in the file's running order: a
                       dict with `event`, `round` (`""` for a number with one
                       round, else its `ROUND_KEYS` value, made unique within the
                       number), `kind` (its `ROUND_KEYS` value or `""`), `live`,
                       `name`, `date`, `session` and `session_name` (its
                       SESSION's `number` and `name`; 0 and "" outside one),
                       and its own `start_list`,
                       `heat_times`, `heat_ages` (`age_band` of a heat's own age
                       group) and `results`, keyed by heat as above.

    One event number can hold several rounds: Splash numbers a prelim and its
    final the same, and the console only ever sends the number. Each round is read
    on its own, then every number-keyed field holds the number's *live* round: the
    last one in the file with a swimmer seeded in it (fastest heats wait for
    their timed final to be swum). A final has no swimmers until
    its prelims are swum and the file re-exported, so the board follows the meet
    from the file alone — not from a clock, which a Pi with no network may not
    have right. A session lists only the numbers whose live round it holds.
    """
    tree = _open_lenex_xml(path)

    root = tree.getroot()

    # Detect namespace (Lenex 3.0 uses one, 2.0 does not)
    ns_raw = root.tag.split("}")[0].lstrip("{") if "}" in root.tag else ""
    ns = {"l": ns_raw} if ns_raw else {}
    prefix = "l:" if ns_raw else ""

    def find(node, tag):
        if ns:
            return node.findall(f".//{prefix}{tag}", ns)
        return node.findall(f".//{tag}")

    def find_first(node, tag):
        if ns:
            return node.find(f".//{prefix}{tag}", ns)
        return node.find(f".//{tag}")

    def find_direct(node, tag):
        """Non-recursive: direct children only."""
        if ns:
            return node.findall(f"{prefix}{tag}", ns)
        return node.findall(tag)

    # Athlete lookup: athleteid → (lastname, firstname). The last name carries
    # its `nameprefix` ("van den Hoogenband"). An ATHLETE without an id is a
    # record holder in RECORDLISTS, not a swimmer at this meet.
    athletes = {}
    for a in find(root, "ATHLETE"):
        aid = a.get("athleteid")
        if not aid:
            continue
        last = f"{a.get('nameprefix', '').strip()} {a.get('lastname', '')}".strip()
        athletes[aid] = (last, a.get("firstname", ""))

    # Club lookup: athleteid → club shortname
    clubs = {}
    # Relay lookup: relayid → club shortname
    relay_clubs = {}
    for c in find(root, "CLUB"):
        shortname = _club_short(c)
        for a in find(c, "ATHLETE"):
            clubs[a.get("athleteid")] = shortname
        for r in find(c, "RELAY"):
            rid = r.get("relayid", "")
            if rid:
                relay_clubs[rid] = shortname

    _gender_map = {"M": "Men's", "F": "Women's", "X": "Mixed"}
    _stroke_map = {
        "FREESTYLE": "Freestyle",
        "FREE": "Freestyle",
        "BACKSTROKE": "Backstroke",
        "BACK": "Backstroke",
        "BREASTSTROKE": "Breaststroke",
        "BREAST": "Breaststroke",
        "BUTTERFLY": "Butterfly",
        "FLY": "Butterfly",
        "MEDLEY": "Medley",
        "IMRELAY": "Individual Medley",
    }
    _technique_map = {
        "DIVE": "Dive",
        "GLIDE": "Glide",
        "KICK": "Kick",
        "PULL": "Pull",
        "START": "Start",
        "TURN": "Turn",
    }

    def event_name_str(event):
        prename = event.get("prename", "")
        name = event.get("name", "")
        if prename or name:
            return f"{prename} {name}".strip()
        # Built from the SWIMSTYLE, as `parse_event_name` reads a name back: a
        # relay's distance is one leg's, so a 4x50 is written `4x50`.
        style = find_first(event, "SWIMSTYLE")
        if style is None:
            return f"Event {event.get('number', '')}"
        gender = _gender_map.get(event.get("gender", ""), "")
        stroke = style.get("stroke", "").upper()
        # UNKNOWN: a special event, which the spec names in SWIMSTYLE `name`
        # ("5 x 75m Breast with one Arm only"), distance included.
        if stroke == "UNKNOWN" or (stroke not in _stroke_map and style.get("name")):
            special = (style.get("name") or style.get("code") or "").strip()
            if special:
                return f"{gender} {special}".strip()
        try:
            legs = int(style.get("relaycount") or 1)
        except ValueError:
            legs = 1
        distance = style.get("distance", "")
        if legs > 1 and distance:
            distance = f"{legs}x{distance}"
        parts = [
            gender,
            distance,
            _stroke_map.get(
                stroke, stroke.replace("_", " ").capitalize() if stroke else ""
            ),
            _technique_map.get(style.get("technique", "").upper(), ""),
            "Relay" if legs > 1 else "",
        ]
        return " ".join(p for p in parts if p) or f"Event {event.get('number', '')}"

    # Pass 1: events and heats. Everything is keyed by *round*, the EVENT
    # element's position in the file, until the live round of each number is
    # picked at the end: two rounds can share a number (see the docstring).
    meet_el = find_first(root, "MEET")
    meet_course = (meet_el.get("course", "") if meet_el is not None else "").upper()
    sessions = find(meet_el, "SESSION") if meet_el is not None else []
    session_of = {id(e): s for s in sessions for e in find(s, "EVENT")}

    events = find(root, "EVENT")
    round_number = []  # round → event number
    round_key = []  # round → `ROUND_KEYS` value, or ""
    round_of = {}  # id(EVENT element) → round
    names = {}  # round → event name
    starts = {}  # round → {heat_number: {lane: entry}}
    times = {}  # round → {heat_number: daytime_str}
    ages = {}  # round → {heat_number: age band}, for a heat with its own age group
    distances = {}  # round → distance in metres
    relay_legs = {}  # round → its SWIMSTYLE's `relaycount`, when above 1
    eventid_map = {}  # eventid → round (Splash-style)
    heatid_map = {}  # heatid → (round, heat_number)
    round_course = {}  # round → its course: its SESSION's, else the meet's
    round_status = {}  # round → its EVENT's `status`
    heat_status = {}  # (round, heat_number) → its HEAT's `status`

    for rnd, event in enumerate(events):
        round_of[id(event)] = rnd
        round_number.append(int(event.get("number")))
        round_key.append(ROUND_KEYS.get(event.get("round", "").upper(), ""))
        names[rnd] = event_name_str(event)
        session = session_of.get(id(event))
        round_course[rnd] = (
            session.get("course", "") if session is not None else ""
        ).upper() or meet_course
        round_status[rnd] = event.get("status", "").strip().upper()
        starts[rnd] = {}
        times[rnd] = {}
        ages[rnd] = {}
        age_groups = {
            g.get("agegroupid"): age_band(g.get("agemin"), g.get("agemax"))
            for g in find(event, "AGEGROUP")
            if g.get("agegroupid")
        }
        style = find_first(event, "SWIMSTYLE")
        if style is not None:
            # A relay's `distance` is one leg's (Lenex 3.0): a 4x50 is 50 with
            # `relaycount` 4, and its lap count ended after the first swimmer.
            with contextlib.suppress(ValueError, TypeError):
                distances[rnd] = int(style.get("distance", 0)) * max(
                    int(style.get("relaycount") or 1), 1
                )
            with contextlib.suppress(ValueError, TypeError):
                if int(style.get("relaycount") or 1) > 1:
                    relay_legs[rnd] = int(style.get("relaycount"))
        eid = event.get("eventid", "")
        if eid:
            eventid_map[eid] = rnd
        for heat in find(event, "HEAT"):
            h_num = int(heat.get("number"))
            starts[rnd][h_num] = {}
            daytime = heat.get("daytime", "")
            if daytime:
                times[rnd][h_num] = daytime
            # A finals heat per age group (Splash): the heat says which.
            if age_groups.get(heat.get("agegroupid", "")):
                ages[rnd][h_num] = age_groups[heat.get("agegroupid")]
            hid = heat.get("heatid", "")
            if hid:
                heatid_map[hid] = (rnd, h_num)
            heat_status[(rnd, h_num)] = heat.get("status", "").strip().upper()

    # Heats whose results are shown: see `KEPT_HEAT_STATUSES`.
    tracks_status = any(heat_status.values()) or any(round_status.values())
    kept_heats = {
        (rnd, h_num)
        for (rnd, h_num), status in heat_status.items()
        if (status or round_status[rnd]) in KEPT_HEAT_STATUSES or not tracks_status
    }

    def _seed_course(entry, rnd):
        """The ENTRY's `entrycourse` when it is not the course its round is swum
        in, else "". A meet can swim prelims short course and finals long course,
        so the round's SESSION course comes first."""
        course = (entry.get("entrycourse") or "").upper()
        swum = round_course.get(rnd, "")
        return course if course and swum and course != swum else ""

    def _lane(el):
        """The element's `lane`, or None without one. Lane 0 is a lane: pools
        numbered 0–9 seed it."""
        lane = (el.get("lane") or "").strip()
        return int(lane) if lane.isdigit() else None

    def _relay_swimmers(entry, rnd):
        """Return sorted list of {'pos': int, 'name': str} for RELAYPOSITION children.

        Only the legs: a RELAYPOSITION numbered outside 1 to the event's
        `relaycount` is a reserve, who does not swim. Without a `relaycount`
        above 1 every position from 1 up is kept.
        """
        legs = relay_legs.get(rnd)
        swimmers = []
        for rp in find(entry, "RELAYPOSITION"):
            r_aid = rp.get("athleteid", "")
            if not r_aid:
                continue
            try:
                pos = int(rp.get("number", ""))
            except ValueError:
                continue
            if pos < 1 or (legs and pos > legs):
                continue
            last, first = athletes.get(r_aid, ("", ""))
            swimmer_name = f"{first} {last}".strip()
            if swimmer_name:
                swimmers.append(
                    {
                        "pos": pos,
                        "name": swimmer_name,
                        "first": first,
                    }
                )
        swimmers.sort(key=lambda x: x["pos"])
        return swimmers

    def _add_entry(entry, rnd, h_num, aid=None):
        lane = _lane(entry)
        if lane is None:
            return
        if entry.get("status", "").upper() in NOT_SWIMMING_ENTRY_STATUSES:
            return
        if aid is None:
            aid = entry.get("athleteid", "")
        if aid:
            last, first = athletes.get(aid, ("", ""))
            name = f"{first} {last}".strip()
            club = clubs.get(aid, "")
            swimmers = []
        else:
            relay_el = find_first(entry, "RELAY")
            name = relay_el.get("name", "") if relay_el is not None else ""
            rid = relay_el.get("relayid", "") if relay_el is not None else ""
            club = relay_clubs.get(rid, "")
            swimmers = _relay_swimmers(entry, rnd)
        starts[rnd][h_num][lane] = {
            "name": name,
            "club": club,
            "seed_time": entry.get("entrytime", ""),
            "seed_course": _seed_course(entry, rnd),
            "swimmers": swimmers,
        }

    # Pass 2: populate entries — detect which layout the file uses.
    #
    # Structure A — ENTRY inside HEAT (hand-crafted / simple files):
    #   EVENT > HEATS > HEAT > ENTRIES > ENTRY (has athleteid, lane)
    #
    # Structure B — ENTRY at EVENT level (standard Lenex 3.0):
    #   EVENT > ENTRIES > ENTRY (has heatid, athleteid, lane)
    #
    # Structure C — ENTRY under ATHLETE (Splash Meet Manager):
    #   CLUB > ATHLETES > ATHLETE (athleteid) > ENTRIES > ENTRY (has eventid/heatid, lane)

    any_entry_in_heat = any(
        find(heat, "ENTRY") for event in events for heat in find(event, "HEAT")
    )
    any_entry_in_event = any(
        find_direct(entries_el, "ENTRY")
        for event in events
        for entries_el in find_direct(event, "ENTRIES")
    )

    # Structure A — ENTRY nested inside HEAT.
    if any_entry_in_heat:
        for rnd, event in enumerate(events):
            for heat in find(event, "HEAT"):
                h_num = int(heat.get("number"))
                for entry in find(heat, "ENTRY"):
                    _add_entry(entry, rnd, h_num)

    # Structure B — ENTRIES directly under EVENT, linked to a heat by heatid.
    # Real Lenex 3.0 files leave the HEATs empty and list every entry here, so
    # this runs independently of Structure A (not only alongside it).
    if any_entry_in_event:
        for rnd, event in enumerate(events):
            heatid_to_num = {
                h.get("heatid", ""): int(h.get("number"))
                for h in find(event, "HEAT")
                if h.get("heatid")
            }
            for entries_el in find_direct(event, "ENTRIES"):
                for entry in find_direct(entries_el, "ENTRY"):
                    hid = entry.get("heatid", "")
                    h_num = heatid_to_num.get(hid) or int(entry.get("heat", 0) or 0)
                    if h_num:
                        _add_entry(entry, rnd, h_num)

    # Structure C (Splash) — entries under ATHLETE / RELAY, keyed by eventid+heatid.
    # Only when entries aren't already at the heat or event level.
    if not any_entry_in_heat and not any_entry_in_event:

        def _resolve_heat(entry):
            hid = entry.get("heatid", "")
            if hid and hid in heatid_map:
                return heatid_map[hid]
            eid = entry.get("eventid", "")
            if eid and eid in eventid_map:
                h_num = int(entry.get("heat", 0) or 0)
                rnd = eventid_map[eid]
                if h_num in starts[rnd]:
                    return rnd, h_num
            return None, None

        # Individual swimmer entries
        for athlete in find(root, "ATHLETE"):
            aid = athlete.get("athleteid", "")
            for entry in find(athlete, "ENTRY"):
                rnd, h_num = _resolve_heat(entry)
                if rnd is not None:
                    _add_entry(entry, rnd, h_num, aid=aid)

        # Relay entries (CLUB > RELAY > ENTRIES > ENTRY)
        for club in find(root, "CLUB"):
            club_short = _club_short(club)
            club_full = club.get("name", "").strip() or club_short
            for relay in find(club, "RELAY"):
                relay_num = relay.get("number", "")
                team_name = relay.get("name", "").strip() or (
                    f"{club_full} {relay_num}".strip() if relay_num else club_full
                )
                # The swimmers who swam, when the relay has a result for the heat:
                # a coach can change the order, or bring in a reserve, after entries.
                swum = {}
                for res in find(relay, "RESULT"):
                    rnd, h_num = _resolve_heat(res)
                    if rnd is not None:
                        swum[(rnd, h_num)] = _relay_swimmers(res, rnd)
                for entry in find(relay, "ENTRY"):
                    rnd, h_num = _resolve_heat(entry)
                    lane = _lane(entry)
                    if rnd is None or lane is None:
                        continue
                    if entry.get("status", "").upper() in NOT_SWIMMING_ENTRY_STATUSES:
                        continue
                    starts[rnd][h_num][lane] = {
                        "name": team_name,
                        "club": club_short,
                        "seed_time": entry.get("entrytime", ""),
                        "seed_course": _seed_course(entry, rnd),
                        "swimmers": swum.get((rnd, h_num))
                        or _relay_swimmers(entry, rnd),
                    }

    # Results — RESULT elements, wherever the exporter put them: inside a HEAT
    # (hand-written files), or under the ATHLETE / RELAY that swam it, linked to
    # its heat by `heatid` (Lenex 3.0, Splash). The heat is the one carrying the
    # status, so a result is kept only when its heat is.
    round_results = {}  # round → {heat_number: {lane: result}}

    def _add_result(res, rnd, h_num):
        if (rnd, h_num) not in kept_heats:
            return
        lane = _lane(res)
        if lane is None:
            return
        status = res.get("status", "").upper()
        round_results.setdefault(rnd, {}).setdefault(h_num, {})[lane] = {
            "time": wire_time(res.get("swimtime", "")),
            "status": status if status in NON_FINISH_STATUSES else "",
        }

    seen_results = set()
    for rnd, event in enumerate(events):
        for heat in find(event, "HEAT"):
            h_num = int(heat.get("number"))
            for res in find(heat, "RESULT"):
                seen_results.add(id(res))
                _add_result(res, rnd, h_num)
    for res in find(root, "RESULT"):
        if id(res) in seen_results:
            continue
        ev_h = heatid_map.get(res.get("heatid", ""))
        if ev_h:
            _add_result(res, *ev_h)

    # Each number's live round: the last with a swimmer seeded, else its first.
    # Fastest heats (FHT) are the exception: they are seeded with their timed
    # final, not after it, so they take over only once the rest of the event is
    # swum — every seeded heat of the rounds before them official.
    def _swum(r):
        return all(
            (r, h_num) in kept_heats and h_num in round_results.get(r, {})
            for h_num, lanes in starts[r].items()
            if lanes
        )

    def _live(rnds):
        seeded = [r for r in rnds if any(starts[r].values())]
        if not seeded:
            return rnds[0]
        last = seeded[-1]
        if round_key[last] == "fastest_heat":
            before = [r for r in seeded if r < last and not _swum(r)]
            if before:
                return before[0]
        return last

    rounds_of_number = {}
    for rnd, ev_num in enumerate(round_number):
        rounds_of_number.setdefault(ev_num, []).append(rnd)
    live = {ev_num: _live(rnds) for ev_num, rnds in rounds_of_number.items()}
    # A round's identity within its number, what console times are kept under:
    # "" when the number has one round, else its key — suffixed in the odd file
    # that gives one number two rounds of the same kind.
    ident = [""] * len(round_number)
    for rnds in rounds_of_number.values():
        if len(rnds) > 1:
            seen = {}
            for r in rnds:
                key = round_key[r] or "round"
                seen[key] = seen.get(key, 0) + 1
                ident[r] = key if seen[key] == 1 else f"{key}-{seen[key]}"
    live_rounds = {
        ev_num: ident[live[ev_num]]
        for ev_num, rnds in rounds_of_number.items()
        if len(rnds) > 1
    }
    event_names = {n: names[r] for n, r in live.items()}
    start_list = {n: starts[r] for n, r in live.items()}
    heat_times = {n: times[r] for n, r in live.items()}
    event_distances = {n: distances[r] for n, r in live.items() if r in distances}
    event_rounds = {n: round_key[r] for n, r in live.items() if round_key[r]}
    results = {n: round_results[r] for n, r in live.items() if r in round_results}

    # Meet / pool / session metadata
    meet_info = {}
    if meet_el is not None:
        meet_info["name"] = meet_el.get("name", "")
        meet_info["city"] = meet_el.get("city", "")
        meet_info["hostclub"] = meet_el.get("hostclub", "")
        # Without a meet `course`, every SESSION has one (Lenex 3.0); the meet's
        # is then theirs when they agree.
        course = meet_course
        if not course:
            session_courses = {s.get("course", "").strip().upper() for s in sessions}
            if len(session_courses) == 1:
                course = session_courses.pop()
        meet_info["pool_length_lenex"] = course_length(course)
        meet_info["course"] = course
        # The meet's own POOL, else the first session's: a recursive search
        # also reaches the POOL of an ENTRY's MEETINFO, the qualifying meet's.
        pool_el = next(
            (p for el in (meet_el, *sessions) for p in find_direct(el, "POOL")),
            None,
        )
        # The pool's name is FACILITY's since 2016; POOL `name` from older files.
        facility = next(iter(find_direct(meet_el, "FACILITY")), None)
        meet_info["pool"] = (
            (facility.get("name", "") if facility is not None else "")
            or (pool_el.get("name", "") if pool_el is not None else "")
        ).strip()
        # The pool's lanes as the file has them, for Settings to check its own
        # against: `lanemin` is 1 unless the pool is numbered from lane 0.
        uses_lane_0 = any(
            (el.get("lane") or "").strip() == "0"
            for tag in ("ENTRY", "RESULT")
            for el in find(meet_el, tag)
        )
        lanes = _pool_lanes(pool_el, uses_lane_0)
        if lanes:
            meet_info["lanes_lenex"] = lanes
        meet_info["sessions"] = [
            {
                "number": _session_number(s),
                "name": s.get("name", ""),
                "date": s.get("date", ""),
                "daytime": s.get("daytime", ""),
                "endtime": s.get("endtime", ""),
                "warmupfrom": s.get("warmupfrom", ""),
                "warmupuntil": s.get("warmupuntil", ""),
                # Which events the session holds: a heat's `daytime` is a time of
                # day, and this is what puts a date on it (docs/app.md `N-05`).
                # A number shared by rounds is listed by its live round's session.
                "events": [
                    int(e.get("number"))
                    for e in find(s, "EVENT")
                    if (e.get("number") or "").isdigit()
                    and live.get(int(e.get("number"))) == round_of.get(id(e))
                ],
            }
            for s in sessions
        ]

    round_sessions = {  # round → its SESSION
        round_of[id(e)]: s
        for s in sessions
        for e in find(s, "EVENT")
        if id(e) in round_of
    }
    rounds = [
        {
            "event": ev_num,
            "round": ident[r],
            "kind": round_key[r],
            "live": live[ev_num] == r,
            "name": names[r],
            "date": round_sessions[r].get("date", "") if r in round_sessions else "",
            # A heading on the Schedule where the session changes (`S-26`).
            "session": _session_number(round_sessions.get(r)),
            "session_name": (
                round_sessions[r].get("name", "").strip() if r in round_sessions else ""
            ),
            "start_list": starts[r],
            "heat_times": times[r],
            "heat_ages": ages[r],
            "results": round_results.get(r, {}),
        }
        for r, ev_num in enumerate(round_number)
    ]

    return LenexData(
        event_names=event_names,
        start_list=start_list,
        heat_times=heat_times,
        meet_info=meet_info,
        event_distances=event_distances,
        event_rounds=event_rounds,
        results=results,
        live_rounds=live_rounds,
        rounds=rounds,
    )
