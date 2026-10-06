"""Follow a swimmer: who to notify, about which heat, and when.

A spectator's phone registers the swimmers it follows at one meet (docs/app.md §10,
`PUT /meet/{id}/follow`, docs/api.md §5.13). The worker carrying the meet watches
its console through the frames it already relays and sends two notifications per
followed heat, through `cloud_push`:

* **upcoming** (`N-05`) — the heat is about *n* minutes away, by the estimate below,
  or *n* heats away, as the spectator chose;
* **selected** (`N-06`) — the operator has put the heat on the console. Forward
  only, and on a CTS console held 5 s first, because a CTS operator scrolls
  through heats to reach the one they want.

**The estimate** (`N-05` note). The heat's start, in the pool's time:

1. Meet Manager's scheduled time for the heat (Lenex `daytime`, dated by its
   session), moved by how late the meet runs now: the current heat's actual start
   (or the moment it was selected, while it has not started) minus its own
   scheduled time.
2. Without scheduled times: the current heat's start plus the length of every heat
   before the followed one — the heat's longest seed, else the event's, else an
   age-group table — and a changeover learned from this meet's own heats
   (45 s until it has seen some).

**Storage.** One SQLite file on the node's data volume, shared by its workers like
the meet store, so a worker restart keeps every follow. A row is a device token,
its platform, its language and the names and clubs it follows at one meet: kept
until the meet leaves the node (`keep_only`), dropped when the platform says the
token is dead, and carried to another node with the meet (`export_meet`,
`import_meet`). Nothing else about the spectator is kept.
"""

import asyncio
import datetime
import json
import math
import os
import re
import sqlite3
import statistics
import threading
import time
import unicodedata

from starlette.concurrency import run_in_threadpool

import cloud_i18n
import cloud_paths
import cloud_push
from splouch_i18n import compose_event_name
from splouch_times import hundredths

# How long a CTS operator's heat must stay on the console before it counts as the
# heat (`N-06`): scrolling past a heat on the way to another must not notify it.
CTS_HOLD_SECS = 5.0
DEFAULT_CHANGEOVER_SECS = 45.0
_CHANGEOVER_RANGE = (15.0, 180.0)
# A gap between two heats longer than this is a break, not a changeover.
_BREAK_SECS = 10 * 60
# What a spectator may ask for (`N-02`), and how much one registration may carry.
LEAD_MINUTES = (5, 10, 15)
LEAD_HEATS = (1, 2, 3)
MAX_SWIMMERS = 20
MAX_NAME = 120
MAX_TOKEN = 4096

_lock = threading.Lock()
_db = None
_db_path = None


# ── Store ─────────────────────────────────────────────────────────────────────


def _conn():
    global _db, _db_path
    path = os.path.join(cloud_paths.DATA_DIR, "follows.db")
    if _db is None or _db_path != path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        db = sqlite3.connect(path, check_same_thread=False, timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS follows ("
            "meet_id TEXT NOT NULL, token TEXT NOT NULL, platform TEXT NOT NULL, "
            "sandbox INTEGER NOT NULL, lang TEXT NOT NULL, swimmers TEXT NOT NULL, "
            "lead_kind TEXT NOT NULL, lead INTEGER NOT NULL, "
            "selected INTEGER NOT NULL, updated REAL NOT NULL, "
            "PRIMARY KEY (meet_id, token))"
        )
        db.execute(
            "CREATE TABLE IF NOT EXISTS fired ("
            "meet_id TEXT NOT NULL, token TEXT NOT NULL, event INTEGER NOT NULL, "
            "heat INTEGER NOT NULL, kind TEXT NOT NULL, "
            "PRIMARY KEY (meet_id, token, event, heat, kind))"
        )
        db.commit()
        _db, _db_path = db, path
    return _db


def close():
    global _db, _db_path
    with _lock:
        if _db is not None:
            _db.close()
        _db = _db_path = None


class Invalid(ValueError):
    """A registration the server will not store; the message says why."""


def clean(body):
    """A `PUT /meet/{id}/follow` body, checked and trimmed, or Invalid.

    Returns `{token, platform, sandbox, lang, swimmers, lead_kind, lead,
    selected}`; `swimmers` empty means stop following this meet.
    """
    if not isinstance(body, dict):
        raise Invalid("body must be an object")
    token = body.get("token")
    if not isinstance(token, str) or not 0 < len(token) <= MAX_TOKEN:
        raise Invalid("token")
    platform = body.get("platform")
    if platform not in ("apns", "fcm"):
        raise Invalid("platform")
    swimmers = []
    for s in body.get("swimmers") or []:
        if not isinstance(s, dict) or not isinstance(s.get("name"), str):
            raise Invalid("swimmers")
        name = s["name"].strip()[:MAX_NAME]
        club = s.get("club") if isinstance(s.get("club"), str) else ""
        if name and {"name": name, "club": club.strip()[:MAX_NAME]} not in swimmers:
            swimmers.append({"name": name, "club": club.strip()[:MAX_NAME]})
    if len(swimmers) > MAX_SWIMMERS:
        raise Invalid("too many swimmers")
    lead = body.get("lead") or {}
    if "heats" in lead:
        lead_kind, value, allowed = "heats", lead.get("heats"), LEAD_HEATS
    else:
        lead_kind, value, allowed = "minutes", lead.get("minutes", 5), LEAD_MINUTES
    if value not in allowed:
        raise Invalid("lead")
    lang = body.get("lang") if isinstance(body.get("lang"), str) else "en"
    return {
        "token": token,
        "platform": platform,
        "sandbox": bool(body.get("sandbox")),
        "lang": lang[:16] or "en",
        "swimmers": swimmers,
        "lead_kind": lead_kind,
        "lead": int(value),
        "selected": body.get("selected", True) is not False,
    }


def put(meet_id, sub):
    """Store one device's follows at one meet, replacing what it had there. No
    swimmers removes it. What it was already notified of stays notified."""
    with _lock:
        db = _conn()
        if not sub["swimmers"]:
            db.execute(
                "DELETE FROM follows WHERE meet_id = ? AND token = ?",
                (meet_id, sub["token"]),
            )
            db.execute(
                "DELETE FROM fired WHERE meet_id = ? AND token = ?",
                (meet_id, sub["token"]),
            )
        else:
            db.execute(
                "INSERT INTO follows VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (meet_id, token) DO UPDATE SET "
                "platform = excluded.platform, sandbox = excluded.sandbox, "
                "lang = excluded.lang, swimmers = excluded.swimmers, "
                "lead_kind = excluded.lead_kind, lead = excluded.lead, "
                "selected = excluded.selected, updated = excluded.updated",
                (
                    meet_id,
                    sub["token"],
                    sub["platform"],
                    int(sub["sandbox"]),
                    sub["lang"],
                    json.dumps(sub["swimmers"], ensure_ascii=False),
                    sub["lead_kind"],
                    sub["lead"],
                    int(sub["selected"]),
                    time.time(),
                ),
            )
        db.commit()


def _row(r):
    return {
        "token": r[0],
        "platform": r[1],
        "sandbox": bool(r[2]),
        "lang": r[3],
        "swimmers": json.loads(r[4]),
        "lead_kind": r[5],
        "lead": r[6],
        "selected": bool(r[7]),
    }


def for_meet(meet_id):
    """Every device following someone at this meet, and what each was already
    sent: `(subs, {(token, event, heat, kind)})`."""
    with _lock:
        db = _conn()
        rows = db.execute(
            "SELECT token, platform, sandbox, lang, swimmers, lead_kind, lead, "
            "selected FROM follows WHERE meet_id = ?",
            (meet_id,),
        ).fetchall()
        fired = db.execute(
            "SELECT token, event, heat, kind FROM fired WHERE meet_id = ?", (meet_id,)
        ).fetchall()
    return [_row(r) for r in rows], {tuple(f) for f in fired}


def meets_followed():
    with _lock:
        return {r[0] for r in _conn().execute("SELECT DISTINCT meet_id FROM follows")}


def mark_fired(meet_id, keys):
    """Record notifications as sent — before sending, so a crash between the two
    loses one rather than repeating it."""
    with _lock:
        db = _conn()
        db.executemany(
            "INSERT OR IGNORE INTO fired VALUES (?, ?, ?, ?, ?)",
            [(meet_id, *k) for k in keys],
        )
        db.commit()


def drop_token(token):
    """The platform says this token is dead: forget it everywhere on the node."""
    with _lock:
        db = _conn()
        db.execute("DELETE FROM follows WHERE token = ?", (token,))
        db.execute("DELETE FROM fired WHERE token = ?", (token,))
        db.commit()


def drop_meet(meet_id):
    with _lock:
        db = _conn()
        db.execute("DELETE FROM follows WHERE meet_id = ?", (meet_id,))
        db.execute("DELETE FROM fired WHERE meet_id = ?", (meet_id,))
        db.commit()


def keep_only(meet_ids):
    """Drop the follows of every meet this node no longer has (it expired, was
    deleted, or moved to another node and was carried there)."""
    keep = set(meet_ids)
    for meet_id in meets_followed() - keep:
        drop_meet(meet_id)


def export_meet(meet_id):
    """A meet's follows, for the node it is moving to (`import_meet`)."""
    subs, fired = for_meet(meet_id)
    return {"follows": subs, "fired": [list(f) for f in fired]}


def import_meet(meet_id, data):
    """Store follows carried from another node. Idempotent: a repeated hand-over
    replaces each device's row with the same one."""
    for sub in (data or {}).get("follows") or []:
        try:
            clean_sub = clean(
                {
                    **sub,
                    "lead": {sub.get("lead_kind", "minutes"): sub.get("lead")},
                }
            )
        except Invalid:
            continue
        if clean_sub["swimmers"]:
            put(meet_id, clean_sub)
    keys = [
        tuple(f)
        for f in (data or {}).get("fired") or []
        if isinstance(f, list) and len(f) == 4
    ]
    if keys:
        mark_fired(meet_id, keys)


# ── Names ─────────────────────────────────────────────────────────────────────

# docs/app.md `S-09`: the 17 letters with no canonical decomposition.
_EXPAND = {
    "ß": "ss", "æ": "ae", "ð": "d", "ø": "o", "þ": "th", "đ": "d", "ħ": "h",
    "ı": "i", "ĳ": "ij", "ĸ": "k", "ŀ": "l", "ł": "l", "ŉ": "n", "ŋ": "n",
    "œ": "oe", "ŧ": "t", "ſ": "s",
}  # fmt: skip


def fold(s):
    """`S-09`'s fold, so a name followed on a phone matches the same name here:
    lowercase, NFD, expand the 17, drop anything past ASCII. Spaces collapsed."""
    s = unicodedata.normalize("NFD", (s or "").lower())
    s = "".join(_EXPAND.get(c, c) for c in s)
    s = "".join(c for c in s if ord(c) <= 0x7F)
    return " ".join(s.split())


def _matches(lane, swimmer):
    """The display names in *lane* that *swimmer* (`{name, club}`) is."""
    name, club = fold(swimmer["name"]), fold(swimmer.get("club", ""))
    if club and fold(lane.get("club", "")) != club:
        return []
    found = []
    if fold(lane.get("name", "")) == name:
        found.append(lane.get("name", ""))
    found.extend(
        m.get("name", "")
        for m in lane.get("swimmers") or []
        if fold(m.get("name", "")) == name
    )
    return found


# ── The meet's heats ──────────────────────────────────────────────────────────


class Heat:
    __slots__ = ("event", "heat", "index", "date", "scheduled", "swim", "lanes")

    def __init__(self, event, heat, index, date, scheduled, swim, lanes):
        self.event, self.heat, self.index = event, heat, index
        self.date, self.scheduled, self.swim, self.lanes = date, scheduled, swim, lanes


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _clock(daytime):
    """Lenex `daytime` (`HH:MM` or `HH:MM:SS`) as seconds after midnight."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})(?::(\d{2}))?", daytime or "")
    if not m:
        return None
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3) or 0)


def _epoch(date, daytime, offset_minutes):
    """A pool-local date and time of day as a Unix time, or None."""
    secs = _clock(daytime)
    if secs is None or not date or offset_minutes is None:
        return None
    try:
        day = datetime.date.fromisoformat(date)
    except ValueError:
        return None
    tz = datetime.timezone(datetime.timedelta(minutes=int(offset_minutes)))
    midnight = datetime.datetime.combine(day, datetime.time(), tz)
    return midnight.timestamp() + secs


def pool_today(offset_minutes, now):
    if offset_minutes is None:
        return None
    tz = datetime.timezone(datetime.timedelta(minutes=int(offset_minutes)))
    return datetime.datetime.fromtimestamp(now, tz).date().isoformat()


# ── Age-group table (the last fallback) ───────────────────────────────────────

# Freestyle seconds per 50 m for a slow-ish swimmer of each band — the one that
# sets a heat's length — then a factor per stroke and per distance. Rough on
# purpose: it only fills in where a meet file gives neither a schedule nor seeds.
_PACE_50 = {  # upper age bound → (girls, boys)
    10: (48.0, 48.0),
    12: (41.0, 40.0),
    14: (37.0, 35.0),
    17: (35.0, 32.0),
    99: (34.0, 31.0),
}
_STROKE = {
    "freestyle": 1.0,
    "backstroke": 1.12,
    "butterfly": 1.14,
    "breaststroke": 1.26,
    "medley": 1.16,
}


def _age_bound(parts):
    if parts.get("age_key") in ("open", "senior"):
        return 99
    nums = [int(n) for n in re.findall(r"\d+", parts.get("age") or "")]
    if not nums:
        return 99
    if (parts.get("age") or "").startswith("<"):
        return nums[0] - 1
    return max(nums)


def table_secs(parts):
    """An age-group estimate of a heat's slowest swim, in seconds, or None."""
    if not parts:
        return None
    dist = parts.get("dist") or ""
    m = re.match(r"^(\d+)[xX](\d+)$", dist)
    legs, leg = (int(m.group(1)), int(m.group(2))) if m else (1, _int(dist))
    if not leg:
        return None
    bound = _age_bound(parts)
    pace = next(v for b, v in sorted(_PACE_50.items()) if bound <= b)
    gender = parts.get("gender") or ""
    if gender in ("girls", "women"):
        per50 = pace[0]
    elif gender in ("boys", "men"):
        per50 = pace[1]
    else:
        per50 = sum(pace) / 2
    per50 *= _STROKE.get(parts.get("stroke") or "", 1.1)
    fatigue = 1 + 0.06 * math.log2(max(leg, 50) / 50)
    return legs * per50 * (leg / 50) * fatigue


def build_heats(sched, offset_minutes):
    """The meet's heats in running order, each with its date, scheduled start (Unix
    time, or None) and estimated swim (seconds, or None for an empty heat)."""
    if not sched or not sched.get("events"):
        return []
    times = sched.get("times", {})
    dates = sched.get("dates", {})
    parts = sched.get("name_parts", {})
    start_list = sched.get("start_list", {})
    session_dates = sorted(set(dates.values()))
    only_date = session_dates[0] if len(session_dates) == 1 else None

    def longest(lanes):
        seeds = [
            h
            for e in lanes.values()
            if e.get("name") and (h := hundredths(e.get("seed_time", "")))
        ]
        return max(seeds) / 100.0 if seeds else None

    heats, index = [], 0
    for ev, heat_nums in sched["events"]:
        ev_key = str(ev)
        event_heats = start_list.get(ev_key, {})
        event_seed = max(
            (s for s in (longest(lanes) for lanes in event_heats.values()) if s),
            default=None,
        )
        for ht in heat_nums:
            lanes = event_heats.get(str(ht), {})
            named = [
                {"lane": _int(k), **e}
                for k, e in sorted(lanes.items(), key=lambda kv: _int(kv[0]) or 0)
                if e.get("name")
            ]
            swim = None
            if named:
                swim = longest(lanes) or event_seed or table_secs(parts.get(ev_key))
            date = dates.get(ev_key) or only_date
            heats.append(
                Heat(
                    int(ev),
                    int(ht),
                    index,
                    date,
                    _epoch(
                        date, times.get(ev_key, {}).get(str(ht), ""), offset_minutes
                    ),
                    swim,
                    named,
                )
            )
            index += 1
    return heats


# ── A live meet, as the worker sees it ────────────────────────────────────────


class MeetWatch:
    """One live meet's console, as far as notifications care: which heat is on it,
    when each heat was selected and started, and what the next heat change is
    waiting to become."""

    def __init__(self):
        self.sched_id = None
        self.heats = []
        self.by_key = {}
        self.current = None  # index of the heat on the console, once held
        self.pending = None  # (event, heat, at): a change still inside its hold
        self.selected_at = {}  # index -> Unix time
        self.started_at = {}  # index -> Unix time

    def load(self, sched, offset_minutes):
        if self.sched_id == id(sched):
            return
        self.sched_id = id(sched)
        self.heats = build_heats(sched, offset_minutes)
        self.by_key = {(h.event, h.heat): h for h in self.heats}

    def frame(self, data, now):
        """Read one `update_scoreboard` frame."""
        ev, ht = _int(data.get("current_event")), _int(data.get("current_heat"))
        if ev and ht is not None and "current_event" in data:
            cur = self.heats[self.current] if self.current is not None else None
            if cur is not None and (cur.event, cur.heat) == (ev, ht):
                self.pending = None
            elif not (self.pending and self.pending[:2] == (ev, ht)):
                self.pending = (ev, ht, now)
        if (
            self.current is not None
            and self.current not in self.started_at
            and any(k.startswith("lane_running") and v for k, v in data.items())
        ):
            self.started_at[self.current] = now

    def settle(self, now, hold):
        """Promote a held change to the current heat. Returns the newly current
        Heat when the console moved *forward* onto it, else None."""
        if not self.pending or now - self.pending[2] < hold:
            return None
        ev, ht, at = self.pending
        self.pending = None
        heat = self.by_key.get((ev, ht))
        if heat is None:
            return None
        previous, self.current = self.current, heat.index
        self.selected_at.setdefault(heat.index, at)
        if previous is not None and heat.index <= previous:
            return None
        return heat

    # The estimate (`N-05`).

    def changeover(self):
        gaps = []
        for i, started in sorted(self.started_at.items()):
            nxt = next(
                (j for j in range(i + 1, len(self.heats)) if self.heats[j].swim),
                None,
            )
            if nxt is None or nxt not in self.started_at:
                continue
            gap = self.started_at[nxt] - started
            if self.heats[i].swim and 0 < gap < _BREAK_SECS:
                gaps.append(gap - self.heats[i].swim)
        if not gaps:
            return DEFAULT_CHANGEOVER_SECS
        lo, hi = _CHANGEOVER_RANGE
        return min(max(statistics.median(gaps[-8:]), lo), hi)

    def heats_ahead(self, heat):
        """Heats with swimmers from the current one to *heat*: 1 is next."""
        if self.current is None or heat.index <= self.current:
            return None
        return sum(1 for h in self.heats[self.current + 1 : heat.index + 1] if h.lanes)

    def eta(self, heat, now, today):
        """When *heat* should start (Unix time), or None when it cannot be said."""
        cur = self.heats[self.current] if self.current is not None else None
        if cur is None or (cur.date and today and cur.date != today):
            # Nothing on the console today yet: the schedule as written.
            if heat.scheduled and heat.date == today:
                return heat.scheduled
            return None
        if heat.index <= cur.index:
            return None
        if heat.date and cur.date and heat.date != cur.date:
            return heat.scheduled  # another day: no lateness carries over
        begun = self.started_at.get(cur.index)
        base = (
            begun
            if begun is not None
            else max(now, self.selected_at.get(cur.index, now))
        )
        if heat.scheduled and cur.scheduled:
            return heat.scheduled + (base - cur.scheduled)
        change = self.changeover()
        total = base
        for h in self.heats[cur.index : heat.index]:
            if h.swim:
                total += h.swim + change
        return max(total, now)


# ── What to send ──────────────────────────────────────────────────────────────


def _push_strings(lang, read):
    return {**read("en", "push"), **(read(lang, "push") if lang != "en" else {})}


def _event_name(sched, heat, lang, read):
    vocab = {
        **read("en", "event_name"),
        **(read(lang, "event_name") if lang != "en" else {}),
    }
    parts = (sched.get("name_parts") or {}).get(str(heat.event))
    return compose_event_name(parts, vocab) or (sched.get("names") or {}).get(
        str(heat.event), ""
    )


def _note(meet_id, sched, sub, heat, kind, names, lanes, when, read):
    words = _push_strings(sub["lang"], read)
    line = words.get("heat", "Event {event}, heat {heat}").format(
        event=heat.event, heat=heat.heat
    )
    if len(set(lanes)) == 1 and lanes[0] is not None:
        line += ", " + words.get("lane", "lane {lane}").format(lane=lanes[0])
    head = words.get("selected", "Heat on the console") if kind == "selected" else when
    event_name = _event_name(sched, heat, sub["lang"], read)
    return {
        "token": sub["token"],
        "platform": sub["platform"],
        "sandbox": sub["sandbox"],
        "meet_id": meet_id,
        "event": heat.event,
        "heat": heat.heat,
        "kind": kind,
        "title": ", ".join(dict.fromkeys(names)),
        "body": f"{head} · {line}" + (f"\n{event_name}" if event_name else ""),
        "collapse": f"{meet_id}:{heat.event}:{heat.heat}",
    }


def _when(sub, watch, heat, now, today, words):
    """The upcoming phrase if *heat* is inside *sub*'s lead now, else None."""
    if sub["lead_kind"] == "heats":
        ahead = watch.heats_ahead(heat)
        if ahead is None or ahead > sub["lead"]:
            return None
        if ahead == 1:
            return words.get("next_heat", "Next heat")
        return words.get("in_heats", "In {heats} heats").format(heats=ahead)
    if watch.current is not None and heat.index <= watch.current:
        return None
    eta = watch.eta(heat, now, today)
    if eta is None or eta - now > sub["lead"] * 60:
        return None
    minutes = max(1, math.ceil((eta - now) / 60))
    return words.get("in_minutes", "In about {minutes} min").format(minutes=minutes)


def due(meet_id, sched, watch, subs, fired, now, today, read, selected=None):
    """The notifications to send now. *selected* is a heat the console just moved
    forward onto (`MeetWatch.settle`). Pure: storage and sending are the caller's."""
    notes = []
    for sub in subs:
        for heat in watch.heats:
            found = [
                (name, lane.get("lane"))
                for lane in heat.lanes
                for s in sub["swimmers"]
                for name in _matches(lane, s)
            ]
            if not found:
                continue
            names = [n for n, _ in found]
            lanes = [ln for _, ln in found]
            if selected is not None and heat.index == selected.index:
                kind, when = ("selected", None) if sub["selected"] else (None, None)
            else:
                # Past or current heats answer None here: once a heat is on the
                # console, an "upcoming" for it would only arrive late.
                words = _push_strings(sub["lang"], read)
                when = _when(sub, watch, heat, now, today, words)
                kind = "upcoming" if when else None
            if kind is None:
                continue
            key = (sub["token"], heat.event, heat.heat, kind)
            if key in fired:
                continue
            fired.add(key)
            notes.append(
                _note(meet_id, sched, sub, heat, kind, names, lanes, when, read)
            )
    return notes


def hold_for(settings):
    """The console's hold (`N-06`): a CTS operator scrolls through heats."""
    key = ((settings or {}).get("console") or {}).get("key", "")
    return CTS_HOLD_SECS if str(key).startswith("cts") else 0.0


# ── The worker's side ─────────────────────────────────────────────────────────

# How often a held heat change is looked at, and how often the minutes estimate is
# re-read against the clock. Settling is in memory; the estimate reads the store.
TICK_SECS = 1.0
ESTIMATE_EVERY = 15

_watches = {}  # meet_id -> MeetWatch, for the meets this worker carries
_followed = set()  # meet ids with at least one follow on this node, refreshed


def watch_for(meet_id, meet):
    w = _watches.get(meet_id)
    if w is None:
        w = _watches[meet_id] = MeetWatch()
    w.load(meet.get("schedule_data") or {}, meet.get("utc_offset_minutes"))
    return w


def forget(meet_id):
    _watches.pop(meet_id, None)


def note_followed(meet_id, following):
    (_followed.add if following else _followed.discard)(meet_id)


async def evaluate(meet_id, meet, now=None, selected=None, read=None):
    """Work out and send what is due for one meet. Returns what was sent."""
    if meet_id not in _followed:
        return []
    now = time.time() if now is None else now
    watch = watch_for(meet_id, meet)
    subs, fired = await run_in_threadpool(for_meet, meet_id)
    if not subs:
        return []
    notes = due(
        meet_id,
        meet.get("schedule_data") or {},
        watch,
        subs,
        fired,
        now,
        pool_today(meet.get("utc_offset_minutes"), now),
        read or cloud_i18n.strings,
        selected=selected,
    )
    if not notes:
        return []
    await run_in_threadpool(
        mark_fired,
        meet_id,
        [(n["token"], n["event"], n["heat"], n["kind"]) for n in notes],
    )
    results = await asyncio.gather(*(cloud_push.send(n) for n in notes))
    for note, result in zip(notes, results, strict=True):
        if result == cloud_push.GONE:
            await run_in_threadpool(drop_token, note["token"])
    return notes


async def observe(meet_id, meet, data):
    """Read one relayed `update_scoreboard` frame; send at once when it settles a
    heat (a console with no hold)."""
    if "current_event" not in data and not any(
        k.startswith("lane_running") for k in data
    ):
        return
    now = time.time()
    watch = watch_for(meet_id, meet)
    watch.frame(data, now)
    heat = watch.settle(now, hold_for(meet.get("settings")))
    if heat is not None:
        await evaluate(meet_id, meet, now, selected=heat)


async def run(live_meets):
    """Forever: settle held heat changes, and every ESTIMATE_EVERY ticks check the
    minutes. `live_meets()` returns `{meet_id: meet}` for this worker."""
    n = 0
    while True:
        try:
            if n % ESTIMATE_EVERY == 0:
                _followed.clear()
                _followed.update(await run_in_threadpool(meets_followed))
            now = time.time()
            for meet_id, meet in live_meets().items():
                watch = watch_for(meet_id, meet)
                heat = watch.settle(now, hold_for(meet.get("settings")))
                if heat is not None or n % ESTIMATE_EVERY == 0:
                    await evaluate(meet_id, meet, now, selected=heat)
        except Exception as e:
            # One bad meet must not end notifications for every other one.
            print(f"[follows] {e!r}", flush=True)
        n += 1
        await asyncio.sleep(TICK_SECS)
