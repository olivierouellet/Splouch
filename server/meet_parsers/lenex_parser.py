import contextlib
import re
import xml.etree.ElementTree as ET
import zipfile
from typing import NamedTuple
from xml.parsers import expat

from splouch_i18n import FINAL_LETTERS
from splouch_times import wire_time


class MeetFileError(ValueError):
    """A meet file Splouch refuses to read, with a reason written for the operator:
    shown on the page, unlike an unexpected error's, which goes to the log."""


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
    # What the parser left out or read another way, for Settings to show; see
    # `load_lenex`. A `.hy3` has none.
    warnings: tuple = ()


# HEAT (or EVENT) `status` values whose results Splouch shows as official.
# Splash leaves a heat whose results were typed in by hand `SEEDED`. Its
# `INOFFICIAL` (the spec's `UNOFFICIAL`) is left out on purpose: those are the
# console's own times, which Splouch already has, and by the time a meet file is
# uploaded they should all have been validated. The status is optional (Lenex
# 3.0): a heat without one goes by its EVENT's, and a file that writes no status
# on any heat or event — an exporter that does not track it — has its results
# kept as they are.
KEPT_HEAT_STATUSES = {"OFFICIAL", "SEEDED"}

# HEAT (or EVENT) `status` values of a heat that has been swum: its results are
# in, official or not (Lenex 3.0 `UNOFFICIAL`, Splash's `INOFFICIAL`). What a
# swim-off waits for before its final takes the number (`load_lenex`).
SWUM_HEAT_STATUSES = {"OFFICIAL", "UNOFFICIAL", "INOFFICIAL"}

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


# A para swimmer's sport class typed into the last name, as a federation's
# registration does (`Rivard_S10SB9SM10`), sometimes cut short by a length limit
# (`Hunsicker_S10SB9SM1`). The class is the ATHLETE's HANDICAP element's to say
# (Lenex 3.0), and a scoreboard name is no place for it. No name has an
# underscore, so one followed by `S` and a digit starts nothing else.
_SPORT_CLASS_SUFFIX = re.compile(r"_S\d[\dSBM]*$")


def _last_name(athlete):
    """An ATHLETE's last name as a board shows it: with its `nameprefix` ("van den
    Hoogenband"), without a sport class typed into it."""
    last = _SPORT_CLASS_SUFFIX.sub("", (athlete.get("lastname") or "").strip())
    return f"{(athlete.get('nameprefix') or '').strip()} {last}".strip()


def _club_short(club):
    """What a lane shows for a CLUB: the first of `code`, `shortname` and `name`
    that is not blank. `code` is the federation's club code (`CNSH`), what a
    narrow club column is made for; `shortname` is a short *name*, up to 20
    characters (Lenex 3.0), which Splash fills with `Mustang Boucherville` or
    `C.A.Y.O. De Dolbeau-Mistassini`. An UNATTACHED club may have none of them."""
    return next(
        (v for k in ("code", "shortname", "name") if (v := club.get(k, "").strip())),
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


def _whole(value):
    """*value* as a whole number, None when it is not one: a Lenex `n` attribute
    that is missing or that an exporter wrote wrong. The element is then left
    out rather than the file refused, so one bad event or heat cannot keep a
    whole meet off the board."""
    value = (value or "").strip()
    return int(value) if value.isdigit() else None


def _session_number(session):
    """A SESSION's `number`, 0 for none or one that is not a number."""
    num = (session.get("number", "") if session is not None else "").strip()
    return int(num) if num.isdigit() else 0


class _PrologRead(Exception):
    """The root element began: the prolog held no DOCTYPE."""


def _refuse_doctype(*_):
    raise MeetFileError("the file contains a DOCTYPE declaration; refusing to parse it")


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


def _parse_xml(data):
    """*data* as an XML tree, once `_check_no_doctype` has let it through. A file
    that is not well-formed XML says where it breaks, for the operator to show
    whoever exported it."""
    _check_no_doctype(data)
    try:
        return ET.ElementTree(ET.fromstring(data))
    except ET.ParseError as e:
        line, column = e.position
        raise MeetFileError(
            f"not well-formed XML at line {line}, column {column}"
        ) from e


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
                raise MeetFileError(
                    "No Lenex XML (.lef) found inside the .lxf; "
                    f"it contains: {', '.join(names) or '(nothing)'}"
                )
            with z.open(xml_name) as fh:
                data = fh.read(MAX_XML_BYTES + 1)
            if len(data) > MAX_XML_BYTES:
                raise MeetFileError(
                    "the Lenex XML inside is larger than "
                    f"{MAX_XML_BYTES // (1024 * 1024)} MB; refusing to parse it"
                )
            return _parse_xml(data)
    # Not a zip — assume a raw .lef/.xml Lenex document.
    with open(path, "rb") as fh:
        data = fh.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise MeetFileError(
            f"the file is larger than {MAX_XML_BYTES // (1024 * 1024)} MB; "
            "refusing to parse it"
        )
    return _parse_xml(data)


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
                       group), `heat_finals` (a final's heat's HEAT `final`, one
                       of `FINAL_LETTERS`) and `results`, keyed by heat as above.
        warnings     — what was left out, or read another way, in file order:
                       each a dict with `key` and the values that place it.
                       `event_no_number` (`eventid`), `heat_no_number` (`event`,
                       `round`), `entry_no_heat` (`event`, `round`, `name`: it
                       names a heat the round does not have), `entry_no_lane`
                       (`event`, `round`, `heat`, `name`), `lane_twice` (the
                       same and `lane`, `other`: the swimmer it replaced) and
                       `extra_heats_alone` (`event`: Splash extra heats whose
                       timed final is not in the file, read as a round of
                       their own). `round` is a `ROUND_KEYS` value or `""`.
                       Unknown round codes, statuses and final letters are
                       not warned about: they are what exporters write.

    One event number can hold several rounds: Splash numbers a prelim and its
    final the same, and the console only ever sends the number. Each round is read
    on its own, then every number-keyed field holds the number's *live* round: the
    last one in the file with a swimmer seeded in it (fastest heats wait for
    their timed final to be swum, and a final for a swim-off before it). A final has no swimmers until
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

    # Athlete lookup: athleteid → (lastname, firstname); see `_last_name`. An
    # ATHLETE without an id is a record holder in RECORDLISTS, not a swimmer at
    # this meet.
    athletes = {}
    for a in find(root, "ATHLETE"):
        aid = a.get("athleteid")
        if not aid:
            continue
        athletes[aid] = (_last_name(a), a.get("firstname", ""))

    # Club lookup: athleteid → what its lane shows for the club (`_club_short`)
    clubs = {}
    # Relay lookup: relayid → the same
    relay_clubs = {}
    for c in find(root, "CLUB"):
        short = _club_short(c)
        for a in find(c, "ATHLETE"):
            clubs[a.get("athleteid")] = short
        for r in find(c, "RELAY"):
            rid = r.get("relayid", "")
            if rid:
                relay_clubs[rid] = short

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

    def ages_str(event, heats):
        """The ages the event's AGEGROUPs span, written the way
        `parse_event_name` reads a band back: `10 & Under`, `11 & Over`,
        `11-12`, `8-8` for one age, `""` when any group is open both ways or
        there is none. A timed final scored by age groups (`≤7`, `8`, `9`, `10`)
        is the whole span, `≤10`. None when one of the round's *heats* has a
        group of its own (a final seeded one heat per age group): each heat says
        which (`heat_ages`), and the span beside it would read twice,
        `11+ · 13-14`."""
        groups = find(event, "AGEGROUP")
        ids = {g.get("agegroupid") for g in groups if g.get("agegroupid")}
        if any(h.get("agegroupid") in ids for h in heats):
            return ""
        los, his = [], []
        for g in groups:
            band = age_band(g.get("agemin"), g.get("agemax"))
            if not band:
                return ""
            lo = (g.get("agemin") or "").strip()
            hi = (g.get("agemax") or "").strip()
            los.append(int(lo) if lo.isdigit() and int(lo) > 0 else None)
            his.append(int(hi) if hi.isdigit() and int(hi) > 0 else None)
        if not los:
            return ""
        lo = None if None in los else min(los)
        hi = None if None in his else max(his)
        if lo and hi:
            return f"{lo}-{hi}"
        if hi:
            return f"{hi} & Under"
        if lo:
            return f"{lo} & Over"
        return ""

    def event_name_str(event, heats):
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
            ages_str(event, heats),
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

    # What was left out or read another way, for Settings to show the operator
    # (`warnings` in the docstring).
    warnings = []

    def warn(key, **values):
        warnings.append({"key": key, **values})

    # An EVENT without a number is one the console cannot call: left out.
    events = []
    for e in find(root, "EVENT"):
        if _whole(e.get("number")) is None:
            warn("event_no_number", eventid=(e.get("eventid") or "").strip())
        else:
            events.append(e)
    by_eventid = {e.get("eventid"): e for e in events if e.get("eventid")}

    def _previous(event):
        """The EVENT its `preveventid` names, None for none (-1) or one not in
        the file."""
        return by_eventid.get((event.get("preveventid") or "").strip())

    def _primary(event):
        """The EVENT whose round *event* is part of: itself, unless it is one of
        Splash's extra heats. Splash swims the heats of a long timed final
        (1500, 800) alternating with other events by giving each heat an EVENT
        of its own, `round="EXTRAHEATS"` (not a Lenex 3.0 code), with the same
        number and a `preveventid` naming the timed final, which holds the age
        groups but no heats. The console sends the number and the heat, so the
        heats are one round, the timed final's."""
        if (event.get("round") or "").strip().upper() != "EXTRAHEATS":
            return event
        prev = _previous(event)
        if prev is None or prev.get("number") != event.get("number"):
            return event
        return prev

    def _age_groups(event):
        """{agegroupid: band} of the AGEGROUPs a HEAT of *event* can name: its
        own, then its previous event's. A fastest-heats EVENT may have no
        AGEGROUPS (Lenex 3.0: "If round=FHT, then no AGEGROUPS element is
        allowed"); its heats name its timed final's, as Splash's extra heats do."""
        groups = {}
        seen = set()
        while event is not None and id(event) not in seen:
            seen.add(id(event))
            for g in find(event, "AGEGROUP"):
                if g.get("agegroupid"):
                    groups.setdefault(
                        g.get("agegroupid"), age_band(g.get("agemin"), g.get("agemax"))
                    )
            event = _previous(event)
        return groups

    round_of = {}  # id(EVENT element) → round
    primaries = []  # round → its EVENT element
    members = []  # round → every EVENT element it is read from, in file order
    for event in events:
        primary = _primary(event)
        if (
            primary is event
            and (event.get("round") or "").strip().upper() == "EXTRAHEATS"
        ):
            warn("extra_heats_alone", event=_whole(event.get("number")))
        if id(primary) not in round_of:
            round_of[id(primary)] = len(primaries)
            primaries.append(primary)
            members.append([])
        round_of[id(event)] = round_of[id(primary)]
        members[round_of[id(event)]].append(event)
    if any(len(m) > 1 for m in members):
        # A merged round runs where its first heat is, not where its timed final
        # (which has none) stands in the file.
        position = {id(e): i for i, e in enumerate(events)}

        def _runs_at(r):
            swum = [e for e in members[r] if find(e, "HEAT")] or [primaries[r]]
            return position[id(swum[0])]

        order = sorted(range(len(primaries)), key=_runs_at)
        primaries = [primaries[r] for r in order]
        members = [members[r] for r in order]
        round_of = {id(e): r for r, m in enumerate(members) for e in m}

    round_number = []  # round → event number
    round_key = []  # round → `ROUND_KEYS` value, or ""
    round_session = {}  # round → its SESSION: where its first heat is swum
    names = {}  # round → event name
    starts = {}  # round → {heat_number: {lane: entry}}
    times = {}  # round → {heat_number: daytime_str}
    ages = {}  # round → {heat_number: age band}, for a heat with its own age group
    finals = {}  # round → {heat_number: "A"…"D"}, for a heat of an A, B… final
    distances = {}  # round → distance in metres
    relay_legs = {}  # round → its SWIMSTYLE's `relaycount`, when above 1
    eventid_map = {}  # eventid → round (Splash-style)
    heatid_map = {}  # heatid → (round, heat_number)
    round_course = {}  # round → its course: its SESSION's, else the meet's
    round_status = {}  # round → its EVENT's `status`
    heat_status = {}  # (round, heat_number) → its HEAT's `status`

    for rnd, event in enumerate(primaries):
        round_number.append(_whole(event.get("number")))
        round_key.append(ROUND_KEYS.get(event.get("round", "").upper(), ""))
        heats = []
        for m in members[rnd]:
            for h in find(m, "HEAT"):
                if _whole(h.get("number")) is None:
                    warn(
                        "heat_no_number", event=round_number[rnd], round=round_key[rnd]
                    )
                else:
                    heats.append(h)
        names[rnd] = event_name_str(event, heats)
        swum_in = [m for m in members[rnd] if find(m, "HEAT")] or [event]
        session = session_of.get(id(swum_in[0]))
        if session is not None:
            round_session[rnd] = session
        round_course[rnd] = (
            session.get("course", "") if session is not None else ""
        ).upper() or meet_course
        round_status[rnd] = event.get("status", "").strip().upper()
        starts[rnd] = {}
        times[rnd] = {}
        ages[rnd] = {}
        finals[rnd] = {}
        age_groups = {}
        for m in members[rnd]:
            for gid, band in _age_groups(m).items():
                age_groups.setdefault(gid, band)
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
        for m in members[rnd]:
            if m.get("eventid"):
                eventid_map[m.get("eventid")] = rnd
        for heat in heats:
            h_num = _whole(heat.get("number"))
            starts[rnd][h_num] = {}
            daytime = heat.get("daytime", "")
            if daytime:
                times[rnd][h_num] = daytime
            # A finals heat per age group (Splash): the heat says which.
            if age_groups.get(heat.get("agegroupid", "")):
                ages[rnd][h_num] = age_groups[heat.get("agegroupid")]
            # An A or B final (Lenex 3.0: A to D). Anything else — Splash's `F`
            # for an age group's only final, a letter no spec names — is the
            # round's plain final.
            letter = (heat.get("final") or "").strip().upper()
            if letter in FINAL_LETTERS:
                finals[rnd][h_num] = letter
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

    def _athlete_name(aid):
        last, first = athletes.get(aid, ("", ""))
        return f"{first} {last}".strip()

    def _place(entry, rnd, h_num, name, club, swimmers):
        """Seat an entry in its lane: nothing without one, nobody who will not
        swim. A lane two swimmers claim keeps the later, as it always has, and
        says so; the same entry written twice (Splash does) is no error."""
        if entry.get("status", "").upper() in NOT_SWIMMING_ENTRY_STATUSES:
            return
        lane = _lane(entry)
        where = {"event": round_number[rnd], "round": round_key[rnd], "heat": h_num}
        if lane is None:
            warn("entry_no_lane", **where, name=name)
            return
        before = starts[rnd][h_num].get(lane)
        if before is not None and (before["name"], before["club"]) != (name, club):
            warn("lane_twice", **where, lane=lane, name=name, other=before["name"])
        starts[rnd][h_num][lane] = {
            "name": name,
            "club": club,
            "seed_time": entry.get("entrytime", ""),
            "seed_course": _seed_course(entry, rnd),
            "swimmers": swimmers,
        }

    def _add_entry(entry, rnd, h_num, aid=None):
        if aid is None:
            aid = entry.get("athleteid", "")
        if aid:
            name = _athlete_name(aid)
            club = clubs.get(aid, "")
            swimmers = []
        else:
            relay_el = find_first(entry, "RELAY")
            name = relay_el.get("name", "") if relay_el is not None else ""
            rid = relay_el.get("relayid", "") if relay_el is not None else ""
            club = relay_clubs.get(rid, "")
            swimmers = _relay_swimmers(entry, rnd)
        _place(entry, rnd, h_num, name, club, swimmers)

    def _heat_missing(entry, rnd, name):
        """An entry that names a heat the round does not have: left out. One
        that names none is entered but not seeded yet, which is no error."""
        if (entry.get("heatid") or "").strip() or (entry.get("heat") or "").strip():
            warn(
                "entry_no_heat",
                event=round_number[rnd],
                round=round_key[rnd],
                name=name,
            )

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
        for event in events:
            rnd = round_of[id(event)]
            for heat in find(event, "HEAT"):
                h_num = _whole(heat.get("number"))
                if h_num is None:
                    continue
                for entry in find(heat, "ENTRY"):
                    _add_entry(entry, rnd, h_num)

    # Structure B — ENTRIES directly under EVENT, linked to a heat by heatid.
    # Real Lenex 3.0 files leave the HEATs empty and list every entry here, so
    # this runs independently of Structure A (not only alongside it).
    if any_entry_in_event:
        for event in events:
            rnd = round_of[id(event)]
            # Any heat of the round: an extra heat's entry is listed under the
            # timed final.
            heatid_to_num = {
                hid: h_num for hid, (r, h_num) in heatid_map.items() if r == rnd
            }
            for entries_el in find_direct(event, "ENTRIES"):
                for entry in find_direct(entries_el, "ENTRY"):
                    hid = entry.get("heatid", "")
                    h_num = heatid_to_num.get(hid) or _whole(entry.get("heat"))
                    if h_num in starts[rnd]:
                        _add_entry(entry, rnd, h_num)
                    else:
                        _heat_missing(
                            entry, rnd, _athlete_name(entry.get("athleteid", ""))
                        )

    # Structure C (Splash) — entries under ATHLETE / RELAY, keyed by eventid+heatid.
    # Only when entries aren't already at the heat or event level.
    if not any_entry_in_heat and not any_entry_in_event:

        def _resolve_heat(entry):
            hid = entry.get("heatid", "")
            if hid and hid in heatid_map:
                return heatid_map[hid]
            eid = entry.get("eventid", "")
            if eid and eid in eventid_map:
                h_num = _whole(entry.get("heat"))
                rnd = eventid_map[eid]
                if h_num in starts[rnd]:
                    return rnd, h_num
            return None, None

        def _unresolved(entry, name):
            """An entry `_resolve_heat` could not seat, when its event is one
            the file has: an event left out has said so already."""
            rnd = eventid_map.get(entry.get("eventid", ""))
            if rnd is not None:
                _heat_missing(entry, rnd, name)

        # Individual swimmer entries
        for athlete in find(root, "ATHLETE"):
            aid = athlete.get("athleteid", "")
            for entry in find(athlete, "ENTRY"):
                rnd, h_num = _resolve_heat(entry)
                if rnd is not None:
                    _add_entry(entry, rnd, h_num, aid=aid)
                else:
                    _unresolved(entry, _athlete_name(aid))

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
                    if rnd is None:
                        _unresolved(entry, team_name)
                        continue
                    _place(
                        entry,
                        rnd,
                        h_num,
                        team_name,
                        club_short,
                        swum.get((rnd, h_num)) or _relay_swimmers(entry, rnd),
                    )

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
    for event in events:
        rnd = round_of[id(event)]
        for heat in find(event, "HEAT"):
            h_num = _whole(heat.get("number"))
            if h_num is None:
                continue
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

    def _heat_swum(r, h_num):
        status = heat_status.get((r, h_num)) or round_status[r]
        return status in SWUM_HEAT_STATUSES or h_num in round_results.get(r, {})

    def _live(rnds):
        seeded = [r for r in rnds if any(starts[r].values())]
        if not seeded:
            return rnds[0]
        last = seeded[-1]
        # A swim-off stays live until it is swum, even once the final after it is
        # seeded: the console calls it by the same number and heat 1, and the
        # final would put its own heat 1 on the board in place of the tied
        # swimmers. Swum is the heat's status, not its results, which a Splash
        # file often carries none of; a file that tracks no status cannot say,
        # and the last seeded round is live as before.
        if tracks_status:
            pending = [
                r
                for r in seeded
                if r < last
                and round_key[r] == "swimoff"
                and not all(_heat_swum(r, h) for h, lanes in starts[r].items() if lanes)
            ]
            if pending:
                return pending[0]
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
                # A number shared by rounds is listed by its live round's
                # session; a round by the session of its first heat.
                "events": list(
                    dict.fromkeys(
                        _whole(e.get("number"))
                        for e in find(s, "EVENT")
                        if id(e) in round_of
                        and live.get(_whole(e.get("number"))) == round_of[id(e)]
                        and round_session.get(round_of[id(e)]) is s
                    )
                ),
            }
            for s in sessions
        ]

    rounds = [
        {
            "event": ev_num,
            "round": ident[r],
            "kind": round_key[r],
            "live": live[ev_num] == r,
            "name": names[r],
            "date": round_session[r].get("date", "") if r in round_session else "",
            # A heading on the Schedule where the session changes (`S-26`).
            "session": _session_number(round_session.get(r)),
            "session_name": (
                round_session[r].get("name", "").strip() if r in round_session else ""
            ),
            "start_list": starts[r],
            "heat_times": times[r],
            "heat_ages": ages[r],
            "heat_finals": finals[r] if round_key[r] == "final" else {},
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
        warnings=tuple(warnings),
    )
