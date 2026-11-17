from typing import Any

import bus
import relay
import state
from console_decoders.utils import parse_time_hundredths
from meet_parsers.lenex_parser import ROUND_NAMES
from splouch_times import heat_official, lane_times


def _delta_hundredths(finish_str, seed_str):
    """Signed (finish − seed) in hundredths of a second, or None if unparseable."""
    finish = parse_time_hundredths(finish_str)
    seed = parse_time_hundredths(seed_str)
    if finish is None or seed is None:
        return None
    return finish - seed


def _delta_html(delta):
    abs_d = abs(delta)
    h = abs_d % 100
    s = (abs_d // 100) % 60
    m_part = abs_d // 6000
    sign = "-" if delta < 0 else "+"
    text = (f"{sign}{m_part}:{s:02d}.{h:02d}") if m_part else (f"{sign}{s}.{h:02d}")
    cls = "delta-better" if delta < 0 else "delta-worse"
    return f'<span class="{cls}">{text}</span>'


def delta_fields(finish_str, seed_str):
    """Return (html, seconds, better) for a finish vs its seed time.

    `seconds` is the signed difference as a float (negative = faster than seed) and
    `better` is a bool — structured values for native clients that can't parse the
    HTML `html` the browser scoreboard renders. Returns ('', None, None) if either
    time is missing/unparseable.
    """
    delta = _delta_hundredths(finish_str, seed_str)
    if delta is None:
        return "", None, None
    return _delta_html(delta), round(delta / 100.0, 2), delta < 0


def get_lane_seed_time(event_num, heat_num, lane):
    """The seed the board measures a finish against (`lane_delta<i>`): none for a
    seed swum in another course than the meet's (docs/api.md §5.1)."""
    m = state.meet
    try:
        entry = m.start_list[event_num][heat_num][lane]
        return "" if entry.get("seed_course") else entry.get("seed_time", "")
    except (KeyError, TypeError):
        return m.event_info.get_seed_time(event_num, heat_num, lane)


def _raw_event_name(event_num):
    m = state.meet
    return m.event_names.get(event_num) or m.event_info.get_event_name(event_num)


def event_distance(event_num, m=None):
    """The distance *event_num* swims, in the pool's unit; 0 when unknown.

    The meet file's when it says (Lenex SWIMSTYLE), else read from the event's
    name: a Hy-Tek CSV has no distance column, only `100 Free`, and without one
    `expected_splits` stayed 0 and no board counted laps. A relay named `4x50`
    swims 200.
    """
    m = m or state.meet
    if event_num in m.event_distances:
        return m.event_distances[event_num]
    parts = state.parse_event_name(_raw_event_name(event_num)) or {}
    legs, x, leg = parts.get("dist", "").lower().partition("x")
    if x:
        return int(legs) * int(leg) if legs.isdigit() and leg.isdigit() else 0
    return int(legs) if legs.isdigit() else 0


def _round_shown(kind, shared, audience):
    """*kind* (a round key) if *audience* is shown it, else ``""``. A phone is
    always shown the round of a number several rounds share: its Schedule lists
    them side by side, and two cards may not read the same (docs/app.md `S-24`)."""
    if shared and audience == "phone":
        return kind
    shown = state.settings.get(f"round_names_{audience}", ROUND_NAMES)
    return kind if kind in shown else ""


def shown_round(event_num, audience, m=None):
    """The event's round key if Settings shows it to *audience*, else ``""``.

    Two audiences, one per wire field (docs/api.md §5.1): ``board`` decides the
    composed `event_name`, which every board renders as sent; ``phone`` decides
    `event_name_parts.round`, which every phone composes from instead.
    """
    m = m or state.meet
    return _round_shown(
        m.event_rounds.get(event_num, ""), event_num in m.live_rounds, audience
    )


def current_round(event_num, m=None):
    """The round the live heat of *event_num* belongs to: its `round` in
    `build_heats`, ``""`` for a number with one round (docs/api.md §5.1)."""
    return (m or state.meet).live_rounds.get(event_num, "")


# Event 0 is the console's test race (Gen7 F1034 p.24; any CTS can be set to it): in
# no meet file, so it is named rather than looked up, in the meet's language.
TEST_EVENT = 0


def heat_age_shown(audience):
    """Does Settings put a heat's own age group in *audience*'s event name — and,
    for phones, beside it on the Schedule (`heat_age_<audience>`)?"""
    return bool(state.settings.get(f"heat_age_{audience}", True))


def heat_age(event_num, heat_num, m=None):
    """The age group of heat *heat_num* of *event_num*'s live round, as a band
    (`"13-14"`), ``""`` when the heat has none of its own: a final seeded one heat
    per age group (docs/api.md §5.8 `age`)."""
    m = m or state.meet
    for r in m.rounds:
        if r["live"] and r["event"] == event_num:
            return r["heat_ages"].get(heat_num, "")
    return ""


def _shown_parts(parts, audience, age=""):
    """*parts* as Settings shows them to *audience* (docs/api.md §5.1): the heat's
    own age group *age* in place of the event's, and no gender if it is hidden
    (`name_gender_<audience>`). Not for a name that parsed to nothing, which every
    composer shows as `raw`, nor one shown as the file has it."""
    if not parts or not (parts.get("dist") or parts.get("stroke")):
        return parts
    if age and heat_age_shown(audience):
        parts = {**parts, "age": age, "age_key": ""}
    if not state.settings.get(f"name_gender_{audience}", True):
        parts = {**parts, "gender": ""}
    return parts


def board_name(raw, round_key, age=""):
    """*raw* composed for the boards, in the meet's language: `event_name`."""
    parts = _shown_parts(
        state.parse_event_name(raw, round_key, state.event_names_as_is()),
        "board",
        age,
    )
    return state.compose_event_name(parts, state.load_event_translations()) or raw


def phone_parts(raw, round_key, age=""):
    """*raw* as parts for the phones to compose: `event_name_parts`."""
    return _shown_parts(
        state.parse_event_name(raw, round_key, state.event_names_as_is()),
        "phone",
        age,
    )


def get_event_name_display(event_num, heat_num=0):
    if event_num == TEST_EVENT:
        return state.load_event_translations().get("test_race", "Test race")
    return board_name(
        _raw_event_name(event_num),
        shown_round(event_num, "board"),
        heat_age(event_num, heat_num),
    )


def get_event_name_parts(event_num, heat_num=0):
    """The same name, language-neutral, for a client rendering in its own language.

    Travels beside `event_name` rather than replacing it (api.md §5.1): a client
    that does not compose keeps rendering the string, which is already correct for
    anyone who has not chosen a language (docs/app.md `T-04`).

    The test race is a `kind`, not a name to parse: a client that knows the key
    shows the vocabulary's word for it, in its reader's language. One that does not
    finds nothing else to compose and shows `raw`, the meet's language — exactly
    what it showed before the key existed (api.md §5.1).
    """
    if event_num == TEST_EVENT:
        return {
            "raw": get_event_name_display(TEST_EVENT),
            "dist": "",
            "stroke": "",
            "relay": False,
            "gender": "",
            "age": "",
            "age_key": "",
            "round": "",
            "kind": "test_race",
        }
    return phone_parts(
        _raw_event_name(event_num),
        shown_round(event_num, "phone"),
        heat_age(event_num, heat_num),
    )


def get_lane_parts(event_num, heat_num, lane):
    """Return (name, club) tuple for display."""
    m = state.meet
    try:
        entry = m.start_list[event_num][heat_num][lane]
        return entry["name"], (entry["club"] or "")
    except (KeyError, TypeError):
        s = m.event_info.get_display_string(event_num, heat_num, lane)
        if len(s) > 5 and s[4] == " ":
            return s[5:], s[:4].strip()
        return "", s.strip()


def get_lane_alt(event_num, heat_num, lane):
    """Return alternate display string for relay lanes (first names), else ''."""
    try:
        entry = state.meet.start_list[event_num][heat_num][lane]
        swimmers = entry.get("swimmers", [])
        if not swimmers:
            return ""
        return " · ".join(
            sw.get("first", "") or sw["name"].split()[-1] for sw in swimmers
        )
    except (KeyError, TypeError):
        return ""


def heat_order() -> list[tuple[int, int]]:
    """Every (event, heat) in the loaded meet, in running order.

    The one place that knows the difference between a Lenex meet (`start_list`) and a
    Hytek one (`event_info.events`). Three callers wanted this list and two of them
    grew their own half of it: `_get_next_heats` read `start_list` and returned []
    for a CSV meet, while `worker._worker_next_heat` read `event_info.events` and was
    blind to a Lenex one — so on any `.lxf` meet, the only meet most clubs have, the
    Next Heat button silently jumped to the (0, 0) sentinel and blanked the board.

    Running order is the file's sessions', the Schedule's (docs/api.md §5.8): a
    final numbered like its prelim swims in the evening, not between events 14
    and 16. Each number's live round only — the one the console can be on.
    """
    m = state.meet
    if m.rounds:
        return [
            (r["event"], ht)
            for r in m.rounds
            if r["live"]
            for ht in sorted(r["start_list"])
        ]
    if m.start_list:
        return [
            (ev, ht) for ev in sorted(m.start_list) for ht in sorted(m.start_list[ev])
        ]
    return sorted(m.event_info.events.keys())


def heat_step(event_num, heat_num, delta=1):
    """The heat `delta` positions along from (event_num, heat_num), or None.

    None means *do not move*: either no meet is loaded, or the operator is already at
    the first or last heat. A current heat the loaded meet does not contain — the
    (0, 0) sentinel on a cold start, or a heat left over from the previous meet file
    — lands on the first heat going forward and the last going back, so Next always
    does something useful rather than nothing.
    """
    order = heat_order()
    if not order:
        return None
    try:
        i = order.index((event_num, heat_num))
    except ValueError:
        return order[0] if delta > 0 else order[-1]
    j = i + delta
    return order[j] if 0 <= j < len(order) else None


def has_heat(event_num, heat_num):
    """Is this heat in the loaded meet? The validation gate for a hand-picked heat."""
    return (event_num, heat_num) in heat_order()


def _get_next_heats(after_event=0, after_heat=0, n=3, num_lanes=8):
    m = state.meet
    ordered = heat_order()
    start = 0
    if after_event:
        for i, (ev, ht) in enumerate(ordered):
            if ev == after_event and ht == after_heat:
                start = i + 1
                break
    result = []
    for ev, ht in ordered[start : start + n]:
        swimmers = []
        first = state.first_lane()
        for ln in range(first, first + num_lanes):
            # Via the accessors rather than `start_list[ev][ht]` directly: they fall
            # back to `event_info`, which is what makes this list appear at all on a
            # Hytek CSV meet. It never used to — the function returned [] for one.
            name, club = get_lane_parts(ev, ht, ln)
            swimmers.append(
                {
                    "lane": ln,
                    "name": name,
                    "club": club,
                    "alt": get_lane_alt(ev, ht, ln),
                }
            )
        result.append(
            {
                "event": ev,
                "heat": ht,
                "round": current_round(ev, m),
                "event_name": get_event_name_display(ev, ht),
                "event_name_parts": get_event_name_parts(ev, ht),
                "time": m.heat_times.get(ev, {}).get(ht, ""),
                "swimmers": swimmers,
            }
        )
    return result


def _build_results_snapshot():
    ev, ht = (
        state._decoder.last_event_sent
        if state._decoder.last_event_sent != (0, 0)
        else (0, 0)
    )
    lanes: list[dict[str, Any]] = []
    # Every row of the pool, 11 and 12 included: a fixed 1-10 left them out.
    rows = min(int(state.settings.get("num_lanes", 8) or 8), 12)
    for ch in range(1, rows + 1):
        time_str = state._decoder.get_lane_time(ch)
        if not time_str:
            continue
        place_str = state._decoder.get_lane_place(ch)
        place_int = int(place_str) if place_str.strip().isdigit() else 99
        # `ch` is the board row the decoder reports; the meet file and `channel`
        # carry the pool's lane number (row - 1 in a pool numbered from lane 0).
        lane = state.lane_of(ch)
        name, club = get_lane_parts(ev, ht, lane) if ev else ("", "")
        alt = get_lane_alt(ev, ht, lane) if ev else ""
        delta, delta_seconds, delta_better = "", None, None
        if time_str and ch in state._decoder.lane_seed_times:
            delta, delta_seconds, delta_better = delta_fields(
                time_str, state._decoder.lane_seed_times[ch]
            )
        lanes.append(
            {
                "channel": lane,
                "place": place_str,
                "place_int": place_int,
                "time": time_str,
                "name": name,
                "club": club,
                "alt": alt,
                "delta": delta,
                "delta_seconds": delta_seconds,
                "delta_better": delta_better,
            }
        )
    sort = state.settings.get("results_sort", "lane")
    if sort == "place":
        lanes.sort(key=lambda r: r["place_int"])
    else:
        lanes.sort(key=lambda r: r["channel"])
    return {
        "event": str(ev) if ev else "",
        "heat": str(ht) if ht else "",
        "round": current_round(ev),
        "event_name": get_event_name_display(ev, ht) if ev else "",
        "event_name_parts": get_event_name_parts(ev, ht) if ev else None,
        # Lanes without a final time are omitted above; 'sort' lets the client
        # place each result in the row matching its lane (lane mode) so a missing
        # lane leaves a blank row instead of shifting the lanes below it up.
        "sort": sort,
        "lanes": lanes,
    }


def _build_meet_data():
    """Normalize Lenex or Hytek data into a unified structure for meet/schedule views."""
    m = state.meet
    if m.start_list:
        event_names = {
            num: board_name(name, shown_round(num, "board", m))
            for num, name in m.event_names.items()
        }
        event_name_parts = {
            num: phone_parts(name, shown_round(num, "phone", m))
            for num, name in m.event_names.items()
        }
        # Running order: the file's, which is its sessions' (docs/api.md §5.8).
        events_grouped = [
            (r["event"], sorted(r["start_list"])) for r in m.rounds if r["live"]
        ] or [(ev, sorted(m.start_list[ev])) for ev in sorted(m.start_list)]
        return {
            "events_grouped": events_grouped,
            "event_names": event_names,
            "event_name_parts": event_name_parts,
            "start_list": m.start_list,
            "heat_times": m.heat_times,
            "meet_info": m.meet_info,
            "results": m.results,
        }
    info = m.event_info
    by_ev = {}
    for ev, ht in sorted(info.events.keys()):
        by_ev.setdefault(ev, []).append(ht)
    events_grouped = sorted(by_ev.items())
    start_list = {}
    for (ev, ht), lane_data in info.events.items():
        sl_ht = start_list.setdefault(ev, {}).setdefault(ht, {})
        for lane, display in lane_data.items():
            if len(display) > 5 and display[4] == " ":
                name, club = display[5:], display[:4].strip()
            else:
                name, club = "", display.strip()
            seed = info.seed_times.get((ev, ht), {}).get(lane, "")
            sl_ht[lane] = {
                "name": name,
                "club": club,
                "seed_time": seed,
                "swimmers": [],
            }
    # Composed and parsed like a Lenex meet's: the file's own names never reached
    # the Schedule translated, nor followed Settings → Meet's or the name boxes.
    return {
        "events_grouped": events_grouped,
        "event_names": {
            num: board_name(name, shown_round(num, "board", m))
            for num, name in info.event_names.items()
        },
        "event_name_parts": {
            num: phone_parts(name, shown_round(num, "phone", m))
            for num, name in info.event_names.items()
        },
        "start_list": start_list,
        "heat_times": {},
        "meet_info": {},
        "results": {},
    }


def schedule_rounds(all_rounds=False):
    """Each round of the loaded meet as the Schedule lists it, in running order:
    `event`, `round`, its composed `event_name` and `event_name_parts`, and its
    own `start_list`, `heat_times` and `results` keyed by heat.

    Only the live round of each number unless *all_rounds*: a client that has not
    asked for them keys its cards by event and heat alone (docs/api.md §5.8).
    """
    m = state.meet
    if m.rounds:
        # The Schedule's `age` is for phones; their box hides it (`S-25`).
        ages_shown = heat_age_shown("phone")
        return [
            {
                "event": r["event"],
                "round": r["round"],
                "live": r["live"],
                "date": r["date"],
                "session": r.get("session", 0),
                "session_name": r.get("session_name", ""),
                "event_name": board_name(
                    r["name"], _round_shown(r["kind"], bool(r["round"]), "board")
                ),
                "event_name_parts": phone_parts(
                    r["name"], _round_shown(r["kind"], bool(r["round"]), "phone")
                ),
                "start_list": r["start_list"],
                "heat_times": r["heat_times"],
                "heat_ages": r["heat_ages"] if ages_shown else {},
                "results": r["results"],
            }
            for r in m.rounds
            if all_rounds or r["live"]
        ]
    data = _build_meet_data()
    return [
        {
            "event": ev,
            "round": "",
            "live": True,
            "date": "",
            "session": 0,
            "session_name": "",
            "event_name": data["event_names"].get(ev, ""),
            "event_name_parts": data.get("event_name_parts", {}).get(ev),
            "start_list": {
                ht: data["start_list"].get(ev, {}).get(ht, {}) for ht in heats
            },
            "heat_times": data["heat_times"].get(ev, {}),
            "heat_ages": {},
            "results": data["results"].get(ev, {}),
        }
        for ev, heats in data["events_grouped"]
    ]


def build_heats(all_rounds=False):
    """The start list as the phone Schedule tab consumes it — every heat in running
    order, each with its lanes. One builder for the HTML page and the JSON endpoint,
    so the two cannot drift (docs/app.md §0.2); the shape is the cloud's
    `GET /meet/{id}/schedule` (docs/api.md §5.8). *all_rounds* lists every round of
    a number several share, not only the live one (`schedule_rounds`).

    Lives here beside `_build_meet_data`, which it is a thin shaping layer over,
    rather than in `routes/meet.py` where it started: `/manual` needs the same list,
    and a route module importing another route module to get at meet logic is the
    wrong direction.
    """
    console = state.console_times
    heats_out = []
    for r in schedule_rounds(all_rounds):
        ev, rnd = r["event"], r["round"]
        # A round with no swimmer yet: a final before its prelims are swum (S-25).
        seeded = any(r["start_list"].values())
        for ht in sorted(r["start_list"]):
            heat_results = r["results"].get(ht, {})
            heat_console = console.get((ev, ht, rnd), {})
            lanes_out = []
            for lane in sorted(r["start_list"][ht]):
                entry = r["start_list"][ht][lane]
                seed = entry.get("seed_time", "")
                seed_course = entry.get("seed_course", "")
                lanes_out.append(
                    {
                        "lane": lane,
                        "name": entry.get("name", ""),
                        "club": entry.get("club", ""),
                        "seed_time": seed,
                        "seed_course": seed_course,
                        "swimmers": [
                            {
                                "pos": s.get("pos", 0),
                                "name": s.get("name", ""),
                                "first": s.get("first", ""),
                            }
                            for s in entry.get("swimmers", [])
                        ],
                        **lane_times(
                            seed,
                            heat_console.get(lane),
                            heat_results.get(lane),
                            seed_course,
                        ),
                    }
                )
            heats_out.append(
                {
                    "event": ev,
                    "heat": ht,
                    "round": rnd,
                    "event_name": r["event_name"],
                    "event_name_parts": r["event_name_parts"],
                    "time": r["heat_times"].get(ht, ""),
                    "age": r["heat_ages"].get(ht, ""),
                    "seeded": seeded,
                    # Where a heading goes on the Schedule (`S-26`).
                    "session": r["session"],
                    "session_name": r["session_name"],
                    "official": heat_official(lanes_out),
                    "lanes": lanes_out,
                }
            )
    return heats_out


def results_summary(before, after, moved=()):
    """What a re-uploaded meet file changed in the official results, for `/mm`.

    *before* and *after* are `build_heats()` taken either side of the reload.
    *moved* are the events whose number now holds another round (a final seeded
    since): their heats are new, not changed. Each list holds
    `{"event", "heat", "lane", "name", ...}` items; heats that became official
    carry no lane.
    """
    old = {(h["event"], h["heat"]): h for h in before if h["event"] not in moved}
    official, statuses, corrected = [], [], []
    for h in after:
        key = (h["event"], h["heat"])
        prev = old.get(key, {})
        if h["official"] and not prev.get("official"):
            official.append({"event": key[0], "heat": key[1]})
        prev_lanes = {lane["lane"]: lane for lane in prev.get("lanes", [])}
        for lane in h["lanes"]:
            was = prev_lanes.get(lane["lane"], {})
            item = {
                "event": key[0],
                "heat": key[1],
                "lane": lane["lane"],
                "name": lane["name"],
            }
            status = lane["result_status"]
            if status and status != was.get("result_status"):
                statuses.append({**item, "status": status})
            elif (
                not status
                and lane["result_time"]
                and lane["console_time"]
                and lane["result_time"] != lane["console_time"]
                and lane["result_time"] != was.get("result_time")
            ):
                corrected.append(
                    {
                        **item,
                        "console_time": lane["console_time"],
                        "result_time": lane["result_time"],
                    }
                )
    return {
        "official_heats": official,
        "statuses": statuses,
        "corrected": corrected,
    }


# Called by `meet_changed`. Registered by modules `meet_data` must not import —
# routes/displays moves TVs off a view the new meet cannot feed.
meet_change_hooks: list = []


def meet_changed():
    """The loaded meet changed and has settled — a new file, a re-export, a test's
    start lists in or out, or none at all.

    Only once settled: loading clears the meet before reading the new one, and
    a hook seeing that empty moment would move TVs off Next heat for good. Also
    re-sends `next_heats`, which otherwise waits for the next heat change and
    so showed the previous meet's heats until then.
    """
    ev, ht = state._decoder.last_event_sent
    bus.emit(
        "/results",
        "next_heats",
        {
            "heats": _get_next_heats(
                ev, ht, num_lanes=int(state.settings.get("num_lanes", 8))
            )
        },
    )
    for hook in meet_change_hooks:
        hook()


def announce_schedule():
    """The start list changed: local Schedule tabs re-fetch, and the cloud gets it.

    Loading a meet file announces its own (routes/settings._load_meet_file); this
    is for the start lists a test session swaps in and out, which used to leave a
    phone open before the session on "No schedule available yet." until
    pull-to-refresh, and for a meet being unloaded. No start list goes to the cloud
    as an empty one, so it stops serving the last. The relay drops the snapshot
    while a test is local-only.
    """
    bus.emit("/schedule", "schedule_update")
    relay.send_schedule(clear=True)
    meet_changed()


def send_event_info():
    ev, ht = state._decoder.last_event_sent
    # (0, 0) is the decoder's "nothing yet" sentinel, not event 0 of heat 0. Send it
    # as blank: a client writes these straight into the header, so str(0) painted a
    # literal "0" under EVENT and HEAT before the console had reported anything.
    started = (ev, ht) != (0, 0)
    u = {
        "current_event": str(ev) if started else "",
        "current_heat": str(ht) if started else "",
        "current_round": current_round(ev) if started else "",
        "event_name": get_event_name_display(ev, ht) if started else "",
        "event_name_parts": get_event_name_parts(ev, ht) if started else None,
    }
    # 12, not 10: Settings offers a 12-lane pool and every other producer of these
    # keys covers 1-12 (`worker._load_heat_names`, each decoder's `reset_lanes`).
    # Stopping at 10 left lanes 11 and 12 un-named on a reconnect or a `next_heat`,
    # still showing the previous heat's swimmers while the rest of the board moved on.
    for i in range(1, 13):
        name, club = get_lane_parts(ev, ht, state.lane_of(i))
        u[f"lane_name{i}"] = name
        u[f"lane_club{i}"] = club
        u[f"lane_delta{i}"] = ""
        u[f"lane_delta_seconds{i}"] = None
        u[f"lane_delta_better{i}"] = None
        u[f"lane_name_alt{i}"] = get_lane_alt(ev, ht, state.lane_of(i))
    state.record_board(u)
    bus.emit("/scoreboard", "update_scoreboard", u)
    relay.relay_emit("update_scoreboard", u)
