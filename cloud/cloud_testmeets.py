"""Test meets: fake Pis the control plane runs, so the apps can be tried on a live
cloud without a pool (docs/cloud.md, Test meets).

Each test meet is a relay client like any Pi: it is assigned a worker, opens
`/ws/relay`, registers with a ticket and sends the frames a console would
(docs/api.md §5.1–5.5) — so everything past the socket is the code real meets
run through, on whichever node the registry picks. They publish under one
organizer the database marks `test` (`cloud_auth.test_organizer`), which the
picker badges and a rollout does not wait for. A Pi's own tests never reach the
cloud at all (`server/relay._local_only`).

A meet swims a few events of two or three heats, eight lanes, in real time.
Meet Manager's validated results follow one or two heats behind the pool, so the
schedule always shows the three kinds of lane time side by side (docs/app.md
`S-22`): official for the older heats, the console's for the last one or two, seeds
for the heats still to swim. Then it starts over: the schedule's results are cleared and `test_loop` tells the worker
to forget the loop's console times and which heat notifications were sent, so a
follower is notified again on the next pass.

`build_meet` and `heat_frames` are pure — what a test drives without a socket;
`Runner` owns the tasks.
"""

import asyncio
import contextlib
import datetime
import json
import random

import cloud_i18n
from splouch_i18n import DEFAULT_THEME_COLORS, DEFAULT_THEME_FONTS, compose_event_name

MAX_MEETS = 10
DEFAULT_MEETS = 5
# The `settings` row that remembers how many run, so a restart resumes them.
SETTING = "test_meets"

LANES = 8
POOL_LENGTH = 25
# Seconds: on the blocks before the start, and the results held after the finish.
PRE_START = 10
RESULTS_HOLD = 15
# How many swum heats wait for validation: one or the other, drawn after each heat.
CONSOLE_ONLY = (1, 2)
# One validated lane in this many is a disqualification instead of a time.
DSQ_ONE_IN = 25
PING_SECS = 20

# (distance, stroke, gender, age, heats, the fastest seed in seconds)
_EVENTS = (
    ("50", "freestyle", "girls", "11-12", 2, 29.0),
    ("100", "backstroke", "boys", "13-14", 2, 63.0),
    ("200", "medley", "women", "", 2, 140.0),
    ("50", "breaststroke", "men", "", 3, 30.0),
    ("100", "butterfly", "mixed", "", 2, 62.0),
)
_FIRST = (
    "Emma", "Liam", "Olivia", "Noah", "Chloé", "Félix", "Léa", "Thomas", "Maya",
    "Samuel", "Rosalie", "Jacob", "Alice", "William", "Juliette", "Nathan",
)  # fmt: skip
_LAST = (
    "Tremblay", "Gagnon", "Roy", "Côté", "Bouchard", "Gauthier", "Morin", "Lavoie",
    "Fortin", "Gagné", "Ouellet", "Pelletier", "Bélanger", "Lévesque", "Bergeron",
)  # fmt: skip
_CLUBS = ("CAMO", "CNQ", "PPO", "CNHR", "NEO", "SAMAK")
_PLACES = ("Montréal", "Québec", "Sherbrooke", "Gatineau", "Laval", "Trois-Rivières")
_LANGS = ("en", "fr", "en", "es", "fr")


def clock(hundredths):
    """`m:ss.hh` or `ss.hh` — the one clock format (docs/api.md §5.1)."""
    h = max(int(round(hundredths)), 0)
    m, s, c = h // 6000, h // 100 % 60, h % 100
    return f"{m}:{s:02d}.{c:02d}" if m else f"{s}.{c:02d}"


def _lenex(hundredths):
    h = int(round(hundredths))
    return f"{h // 360000:02d}:{h // 6000 % 60:02d}:{h // 100 % 60:02d}.{h % 100:02d}"


def _delta(final, seed):
    """(html, seconds, better) as the Pi sends them (server/meet_data.delta_fields)."""
    d = int(round(final)) - int(round(seed))
    sign = "-" if d < 0 else "+"
    text = f"{sign}{clock(abs(d))}"
    cls = "delta-better" if d < 0 else "delta-worse"
    return f'<span class="{cls}">{text}</span>', d / 100, d < 0


def build_meet(index, today=None):
    """Test meet `index` (1-based): the same swimmers and seeds every time."""
    today = today or datetime.date.today()
    rng = random.Random(index)
    lang = _LANGS[(index - 1) % len(_LANGS)]
    vocab = cloud_i18n.strings(lang, "event_name")
    events = []
    for n, (dist, stroke, gender, age, heats, best) in enumerate(_EVENTS, 1):
        parts = {
            "raw": f"{dist} {stroke.title()} {gender.title()} {age}".strip(),
            "dist": dist,
            "stroke": stroke,
            "relay": False,
            "gender": gender,
            "age": age,
            "age_key": "" if age else "open",
            "round": "",
        }
        start_list = {}
        for h in range(1, heats + 1):
            lanes = {}
            # The last heat is the fastest, as a seeded meet swims them.
            slow = (heats - h) * 3.0
            for lane in range(1, LANES + 1):
                if h == 1 and heats > 1 and lane in (1, LANES):
                    continue  # a short first heat leaves the outside lanes empty
                seed = (best + slow + abs(lane - 4.5) * 0.6 + rng.uniform(0, 1.5)) * 100
                lanes[lane] = {
                    "name": f"{rng.choice(_LAST)}, {rng.choice(_FIRST)}",
                    "club": rng.choice(_CLUBS),
                    "seed": seed,
                }
            start_list[h] = lanes
        events.append(
            {
                "num": n,
                "parts": parts,
                "name": compose_event_name(parts, vocab),
                "lengths": int(dist) // POOL_LENGTH,
                "heats": start_list,
                "times": {},
            }
        )
    meet = {
        "index": index,
        "uid": f"test-{index}",
        "name": f"Test meet {index}",
        "location": _PLACES[(index - 1) % len(_PLACES)],
        "lang": lang,
        "date": today.isoformat(),
        "events": events,
    }
    retime(meet, datetime.datetime.combine(today, datetime.time(9, 0)))
    return meet


def retime(meet, start, speed=1.0):
    """Schedule every heat from `start`, at the pace the meet swims them: what a
    follower's "upcoming" notification is estimated from (cloud_follows)."""
    at = start
    for event, heat in heats(meet):
        event["times"][heat] = at.strftime("%H:%M")
        slowest = max(s["seed"] for s in event["heats"][heat].values()) * 1.03 / 100
        at += datetime.timedelta(seconds=(PRE_START + slowest + RESULTS_HOLD) / speed)


def register_meta(meet, key, ticket):
    """The `register` payload (docs/api.md §5.4)."""
    lang = meet["lang"]
    labels = cloud_i18n.resolve_labels(cloud_i18n.strings(lang, "labels"), "short")
    mobile = cloud_i18n.strings(lang, "mobile")
    for k in ("waiting_results", "no_upcoming", "no_schedule"):
        if mobile.get(k):
            labels[k] = mobile[k]
    offset = datetime.datetime.now().astimezone().utcoffset()
    return {
        "key": key,
        "ticket": ticket,
        "organizer_location": None,
        "meet_uid": meet["uid"],
        "name": meet["name"],
        "location": meet["location"],
        "sport": "Swimming",
        "app_window_title": meet["name"],
        "meet_date": meet["date"],
        "session_dates": [meet["date"]],
        "utc_offset_minutes": int(offset.total_seconds() // 60) if offset else 0,
        "settings": {
            "num_lanes": LANES,
            "show_podium": True,
            "show_name": True,
            "show_club": True,
            "show_delta": True,
            "show_position": True,
            "show_laps": True,
            "lap_direction": "up",
            "theme_colors": dict(DEFAULT_THEME_COLORS),
            "theme_fonts": dict(DEFAULT_THEME_FONTS),
            "locale": lang,
            "labels": labels,
            "label_style": "short",
            "console": {"key": "test", "timed": True},
        },
    }


def schedule(meet, results=None):
    """The `schedule_snapshot` (docs/api.md §5.5), with the official `results` so
    far (`validate`)."""
    return {
        "events": [[e["num"], sorted(e["heats"])] for e in meet["events"]],
        "names": {str(e["num"]): e["name"] for e in meet["events"]},
        "name_parts": {str(e["num"]): e["parts"] for e in meet["events"]},
        "times": {
            str(e["num"]): {str(h): t for h, t in e["times"].items()}
            for e in meet["events"]
        },
        "dates": {str(e["num"]): meet["date"] for e in meet["events"]},
        "start_list": {
            str(e["num"]): {
                str(h): {
                    str(lane): {
                        "name": s["name"],
                        "club": s["club"],
                        "seed_time": _lenex(s["seed"]),
                        "swimmers": [],
                    }
                    for lane, s in lanes.items()
                }
                for h, lanes in e["heats"].items()
            }
            for e in meet["events"]
        },
        "results": results or {},
    }


def official(finals, rng):
    """A heat's validated results from its console finals: `{lane: {"time",
    "status"}}`, as Meet Manager would publish them. Mostly the console's time, at
    times a hundredth or three off (a backup time, a judge's call), now and then a
    disqualification."""
    out = {}
    for lane, (_, t) in finals.items():
        if rng.randrange(DSQ_ONE_IN) == 0:
            out[str(lane)] = {"time": "", "status": "DSQ"}
            continue
        adjust = 0 if rng.random() < 0.7 else rng.choice((-3, -2, -1, 1, 2, 3))
        out[str(lane)] = {"time": _lenex(t + adjust), "status": ""}
    return out


def validate(results, swum, lag, rng):
    """Validate every swum heat but the last `lag`, into `results` (the schedule's
    `{event: {heat: {lane: …}}}`). `swum` is `[(event, heat, finals)]` in swim
    order. True when a heat was added."""
    added = False
    for event, heat, finals in swum[: max(len(swum) - lag, 0)]:
        heats_done = results.setdefault(str(event["num"]), {})
        if str(heat) not in heats_done:
            heats_done[str(heat)] = official(finals, rng)
            added = True
    return added


def heats(meet):
    """Every (event, heat) in swim order."""
    return [(e, h) for e in meet["events"] for h in sorted(e["heats"])]


def _next_heats(meet, at):
    order = heats(meet)
    upcoming = order[at + 1 : at + 4]
    return {
        "heats": [
            {
                "event": e["num"],
                "heat": h,
                "event_name": e["name"],
                "time": e["times"][h],
                "swimmers": [
                    {"lane": lane, "name": s["name"], "club": s["club"], "alt": ""}
                    for lane, s in sorted(e["heats"][h].items())
                ],
            }
            for e, h in upcoming
        ]
    }


def _results(event, heat, finals):
    """`results_snapshot` for the lanes finished so far (docs/api.md §5.2)."""
    lanes = []
    for lane, (place, final) in sorted(finals.items(), key=lambda kv: kv[1][0]):
        s = event["heats"][heat][lane]
        html, secs, better = _delta(final, s["seed"])
        lanes.append(
            {
                "channel": lane,
                "place": str(place),
                "place_int": place,
                "time": clock(final),
                "name": s["name"],
                "club": s["club"],
                "alt": "",
                "delta": html,
                "delta_seconds": secs,
                "delta_better": better,
            }
        )
    return {
        "event": str(event["num"]),
        "heat": str(heat),
        "event_name": event["name"],
        "event_name_parts": event["parts"],
        "sort": "place",
        "lanes": lanes,
    }


def heat_frames(meet, at, rng, finals=None):
    """One heat, as `(seconds to wait first, event, data)`: the heat's frames, from
    the board filling to the results held. `at` indexes `heats(meet)`. `finals`,
    when given, is filled with each lane's `(place, hundredths)` as it touches."""
    event, heat = heats(meet)[at]
    entries = event["heats"][heat]
    n = event["lengths"]
    head = {
        "current_event": str(event["num"]),
        "current_heat": str(heat),
        "event_name": event["name"],
        "event_name_parts": event["parts"],
        "heat_time": event["times"][heat],
        "expected_splits": n,
        "split_step": 1,
    }
    for lane in range(1, LANES + 1):
        s = entries.get(lane)
        head |= {
            f"lane_name{lane}": s["name"] if s else "",
            f"lane_club{lane}": s["club"] if s else "",
            f"lane_name_alt{lane}": "",
            f"lane_time{lane}": "",
            f"lane_place{lane}": " ",
            f"lane_running{lane}": False,
            f"lane_delta{lane}": "",
            f"lane_delta_seconds{lane}": None,
            f"lane_delta_better{lane}": None,
            f"lane_splits{lane}": 0,
        }
    yield 0, "next_heats", _next_heats(meet, at)
    yield 0, "update_scoreboard", head
    yield (
        PRE_START,
        "update_scoreboard",
        {"running_time": clock(0)} | {f"lane_running{lane}": True for lane in entries},
    )

    # Every touch, in race time: each length a little slower than the first.
    touches = []
    finals = {} if finals is None else finals
    for lane, s in entries.items():
        final = s["seed"] * rng.uniform(0.97, 1.03)
        weights = [1.0] + [1.06] * (n - 1)
        at_t = 0.0
        for k in range(1, n + 1):
            at_t += final * weights[k - 1] / sum(weights)
            touches.append((final if k == n else at_t, lane, k))
    touches.sort()
    finish = max(t for t, _, _ in touches)
    ticks = [(t * 100.0, None, 0) for t in range(1, int(finish // 100) + 1)]
    now = 0.0
    place = 0
    for t, lane, k in sorted(touches + ticks, key=lambda x: (x[0], x[1] is None)):
        wait, now = (t - now) / 100, t
        if lane is None:
            yield wait, "update_scoreboard", {"running_time": clock(t)}
            continue
        frame = {
            "running_time": clock(t),
            f"lane_time{lane}": clock(t),
            f"lane_splits{lane}": k,
        }
        if k == n:
            place += 1
            finals[lane] = (place, t)
            html, secs, better = _delta(t, entries[lane]["seed"])
            frame |= {
                f"lane_running{lane}": False,
                f"lane_place{lane}": str(place),
                f"lane_delta{lane}": html,
                f"lane_delta_seconds{lane}": secs,
                f"lane_delta_better{lane}": better,
            }
        yield wait, "update_scoreboard", frame
        if k == n:
            yield 0, "results_snapshot", _results(event, heat, finals)
    yield RESULTS_HOLD, None, None


# ── Running them ───────────────────────────────────────────────────────────────


def _send(ws, event, data):
    return ws.send(json.dumps({"event": event, "data": data}))


class Runner:
    """The test meets this control plane runs, one task each.

    `assign(key, meet_uid)` is the control plane's `/api/assign` answer (a dict
    with `relay_url` and `ticket`, or None), and `connect(url)` opens a socket —
    `websockets.connect` unless a test hands in its own.
    """

    def __init__(self, key, assign, connect=None, speed=1.0):
        self.key = key
        self.assign = assign
        self.connect = connect or _connect
        self.speed = speed
        self.tasks = {}
        self.state = {}

    def status(self):
        return [{"index": i, **self.state.get(i, {})} for i in sorted(self.tasks)]

    def start(self, count):
        """Run meets 1…`count` (at most MAX_MEETS); stop any above it."""
        count = max(0, min(int(count), MAX_MEETS))
        for i in sorted(self.tasks):
            if i > count:
                self.tasks.pop(i).cancel()
                self.state.pop(i, None)
        for i in range(1, count + 1):
            if i not in self.tasks or self.tasks[i].done():
                self.state[i] = {"connected": False, "event": None, "heat": None}
                self.tasks[i] = asyncio.create_task(self._run(i))
        return count

    async def stop(self):
        tasks = list(self.tasks.values())
        self.tasks.clear()
        self.state.clear()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _run(self, i):
        """Keep meet `i` connected, reconnecting with a backoff, until cancelled."""
        backoff = 1
        while True:
            try:
                await self._session(i)
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                print(f"[test] meet {i}: {e!r}", flush=True)
            self.state[i]["connected"] = False
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _session(self, i):
        meet = build_meet(i)
        found = await asyncio.to_thread(self.assign, self.key, meet["uid"])
        if not found:
            raise RuntimeError("not assigned")
        async with self.connect(found["relay_url"]) as ws:
            await _send(ws, "register", register_meta(meet, self.key, found["ticket"]))
            self.state[i] |= {"connected": True, "meet_id": found["meet_id"]}
            reader = asyncio.create_task(self._read(ws))
            try:
                await self._swim(ws, i, meet, reader)
            finally:
                reader.cancel()

    async def _read(self, ws):
        """Answer nothing, but end on a close or a refusal (the meet re-assigns)."""
        with contextlib.suppress(Exception):
            async for raw in ws:
                with contextlib.suppress(ValueError):
                    if json.loads(raw).get("event") == "rejected":
                        return

    async def _swim(self, ws, i, meet, reader):
        rng = random.Random()
        last_send = asyncio.get_running_loop().time()
        while True:
            # A new day is a new meet date: register again so the card follows it.
            if build_meet(i)["date"] != meet["date"]:
                return
            # Each pass starts clean: no console times or sent notifications from
            # the last one, and a schedule with no results, timed from now.
            retime(meet, datetime.datetime.now(), self.speed)
            await _send(ws, "test_loop", {})
            await _send(ws, "schedule_snapshot", schedule(meet))
            swum, results = [], {}
            for at in range(len(heats(meet))):
                event, heat = heats(meet)[at]
                self.state[i] |= {"event": event["num"], "heat": heat}
                finals = {}
                swum.append((event, heat, finals))
                for wait, name, data in heat_frames(meet, at, rng, finals):
                    # Sleep in slices so an idle hold still pings the worker.
                    remaining = wait / self.speed
                    while remaining > 0:
                        step = min(remaining, PING_SECS)
                        done, _ = await asyncio.wait({reader}, timeout=step)
                        if done:
                            return
                        remaining -= step
                        loop_now = asyncio.get_running_loop().time()
                        if loop_now - last_send >= PING_SECS:
                            await _send(ws, "ping", {})
                            last_send = loop_now
                    if name:
                        await _send(ws, name, data)
                        last_send = asyncio.get_running_loop().time()
                # The results held, the next heat to the blocks: Meet Manager
                # catches up to within one or two heats of the pool.
                if validate(results, swum, rng.choice(CONSOLE_ONLY), rng):
                    await _send(ws, "schedule_snapshot", schedule(meet, results))


def _connect(url):
    import websockets

    return websockets.connect(url, open_timeout=15, max_size=2**22)
