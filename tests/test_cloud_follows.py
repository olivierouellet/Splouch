"""Follow a swimmer — the cloud's heat notifications (docs/app.md §10).

What is pinned here: the registration a phone sends and what the node keeps of it;
which lane a followed name is in; the two triggers — *upcoming*, by minutes or by
heats (`N-05`), and *selected*, forward only and held on a CTS console (`N-06`);
the estimate behind the minutes; and the text, in the follower's language.
Nothing reaches Apple or Google: `cloud_push.send` is replaced.
"""

import asyncio
import datetime
import io
import zipfile
from typing import Any

import pytest

import cloud_follows as cf
import cloud_i18n
import cloud_push
import cloud_server as cs

OFFSET = -240  # the pool's UTC offset, minutes (Montréal in summer)
DAY = "2026-10-10"


def at(clock, day=DAY):
    """Pool-local `HH:MM` on *day* as a Unix time."""
    return cf._epoch(day, clock, OFFSET)


def lane(n, name, club="CNQ", seed="", swimmers=None):
    return str(n), {
        "name": name,
        "club": club,
        "seed_time": seed,
        "swimmers": swimmers or [],
    }


def schedule(heats, times=None, dates=None, parts=None):
    """A relay `schedule_snapshot`: `heats` is `{(ev, ht): [lane(...), ...]}`."""
    events, start_list = {}, {}
    for (ev, ht), lanes in heats.items():
        events.setdefault(ev, []).append(ht)
        start_list.setdefault(str(ev), {})[str(ht)] = dict(lanes)
    return {
        "events": [[ev, sorted(hts)] for ev, hts in sorted(events.items())],
        "names": {str(ev): f"Event {ev} name" for ev in events},
        "name_parts": parts or {},
        "times": times or {},
        "dates": dates if dates is not None else {str(ev): DAY for ev in events},
        "start_list": start_list,
    }


def sub(token="tok", swimmers=(("Emma Roy", "CNQ"),), lead=None, **kw):
    return cf.clean(
        {
            "token": token,
            "platform": "apns",
            "lang": kw.pop("lang", "en"),
            "swimmers": [{"name": n, "club": c} for n, c in swimmers],
            "lead": lead or {"minutes": 5},
            **kw,
        }
    )


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(cf.cloud_paths, "DATA_DIR", str(tmp_path))
    cf.close()
    cf._watches.clear()
    cf._followed.clear()
    yield
    cf.close()


def watch_on(sched, current=None, now=0.0, hold=0.0):
    w = cf.MeetWatch()
    w.load(sched, OFFSET)
    if current:
        w.frame(
            {"current_event": str(current[0]), "current_heat": str(current[1])}, now
        )
        w.settle(now, hold)
    return w


def due(sched, w, subs, now, fired=None, selected=None):
    return cf.due(
        "m1",
        sched,
        w,
        subs,
        fired if fired is not None else set(),
        now,
        cf.pool_today(OFFSET, now),
        cloud_i18n.strings,
        selected=selected,
    )


# ── Registration ──────────────────────────────────────────────────────────────


def test_a_registration_is_checked_and_trimmed():
    s = cf.clean(
        {
            "token": "abc",
            "platform": "fcm",
            "lang": "fr",
            "swimmers": [
                {"name": " Emma Roy ", "club": "CNQ"},
                {"name": "Emma Roy", "club": "CNQ"},
                {"name": ""},
            ],
            "lead": {"heats": 2},
            "selected": False,
        }
    )
    assert s["swimmers"] == [{"name": "Emma Roy", "club": "CNQ"}]
    assert (s["lead_kind"], s["lead"], s["selected"], s["lang"]) == (
        "heats",
        2,
        False,
        "fr",
    )


@pytest.mark.parametrize(
    "body",
    [
        {"platform": "apns", "swimmers": []},
        {"token": "t", "platform": "web", "swimmers": []},
        {"token": "t", "platform": "apns", "lead": {"minutes": 7}},
        {"token": "t", "platform": "apns", "lead": {"heats": 9}},
        {"token": "t", "platform": "apns", "swimmers": [{"club": "x"}]},
        {
            "token": "t",
            "platform": "apns",
            "swimmers": [{"name": str(i)} for i in range(cf.MAX_SWIMMERS + 1)],
        },
    ],
)
def test_a_bad_registration_is_refused(body):
    with pytest.raises(cf.Invalid):
        cf.clean(body)


def test_the_store_replaces_and_forgets():
    cf.put("m1", sub("a"))
    cf.put("m1", sub("a", swimmers=(("Léa Roy", ""),)))
    cf.put("m1", sub("b"))
    subs, _ = cf.for_meet("m1")
    assert {s["token"]: s["swimmers"][0]["name"] for s in subs} == {
        "a": "Léa Roy",
        "b": "Emma Roy",
    }
    cf.put("m1", sub("b", swimmers=()))
    assert [s["token"] for s in cf.for_meet("m1")[0]] == ["a"]
    cf.drop_token("a")
    assert cf.for_meet("m1") == ([], set())


def test_follows_leave_with_their_meet():
    cf.put("m1", sub())
    cf.put("m2", sub())
    cf.keep_only(["m2"])
    assert cf.meets_followed() == {"m2"}


def test_a_move_carries_follows_and_what_was_sent():
    cf.put("m1", sub("a", lead={"heats": 2}, selected=False))
    cf.mark_fired("m1", [("a", 1, 1, "upcoming")])
    data = cf.export_meet("m1")
    cf.drop_meet("m1")
    cf.import_meet("m1", data)
    subs, fired = cf.for_meet("m1")
    assert subs[0]["lead_kind"] == "heats" and subs[0]["selected"] is False
    assert fired == {("a", 1, 1, "upcoming")}


# ── Names ─────────────────────────────────────────────────────────────────────


def test_names_fold_as_the_phones_fold_them():
    """`S-09`: the 17 letters with no decomposition are expanded, not dropped."""
    assert cf.fold("Île-des-Sœurs") == "ile-des-soeurs"
    assert cf.fold("  Łukasz   Øster ") == "lukasz oster"


def test_a_name_matches_its_lane_and_its_relay_legs_but_not_another_club():
    _, relay = lane(4, "CNQ A", swimmers=[{"name": "Émma Roy"}, {"name": "Léa Roy"}])
    assert cf._matches(relay, {"name": "emma roy", "club": "cnq"}) == ["Émma Roy"]
    assert cf._matches(relay, {"name": "CNQ A", "club": ""}) == ["CNQ A"]
    assert cf._matches(relay, {"name": "Emma Roy", "club": "MEGO"}) == []


# ── Dates and the schedule ────────────────────────────────────────────────────


def test_a_heat_is_dated_by_its_session_in_the_pools_time():
    sched = schedule(
        {(1, 1): [lane(4, "Emma Roy")], (2, 1): [lane(4, "Léa Roy")]},
        times={"1": {"1": "09:30"}, "2": {"1": "15:05"}},
        dates={"1": "2026-10-10", "2": "2026-10-11"},
    )
    h1, h2 = cf.build_heats(sched, OFFSET)
    utc = datetime.datetime.fromtimestamp(h1.scheduled, datetime.UTC)
    assert utc.strftime("%Y-%m-%d %H:%M") == "2026-10-10 13:30"
    assert h2.date == "2026-10-11"


def test_the_pi_sends_each_events_date(monkeypatch):
    import relay
    from meet_parsers.lenex_parser import load_lenex

    xml = """<?xml version="1.0" encoding="UTF-8"?>
<LENEX version="3.0"><MEETS><MEET name="M"><SESSIONS>
  <SESSION date="2026-10-10" daytime="09:00"><EVENTS>
    <EVENT number="1"><HEATS><HEAT number="1" daytime="09:30"/></HEATS></EVENT>
  </EVENTS></SESSION>
  <SESSION date="2026-10-11"><EVENTS>
    <EVENT number="2"><HEATS><HEAT number="1"/></HEATS></EVENT>
  </EVENTS></SESSION>
</SESSIONS></MEET></MEETS></LENEX>"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("meet.lef", xml.encode())
    buf.seek(0)
    meet_info = load_lenex(buf).meet_info
    assert relay._event_dates(meet_info) == {"1": "2026-10-10", "2": "2026-10-11"}


# ── The console ───────────────────────────────────────────────────────────────


SCHED = schedule(
    {
        (1, 1): [lane(3, "Ana Lee", seed="00:01:10.00")],
        (1, 2): [lane(3, "Bo Li", seed="00:01:05.00")],
        (1, 3): [lane(4, "Emma Roy", seed="00:01:02.00")],
        (2, 1): [lane(5, "Emma Roy")],
    },
    times={"1": {"1": "10:00", "2": "10:03", "3": "10:06"}, "2": {"1": "10:20"}},
)


def test_a_cts_operator_scrolling_past_a_heat_does_not_select_it():
    w = watch_on(SCHED, current=(1, 1))
    hold = cf.hold_for({"console": {"key": "cts_gen6"}})
    assert hold == 5.0
    w.frame({"current_event": "1", "current_heat": "3"}, 100.0)
    w.frame({"current_event": "1", "current_heat": "2"}, 102.0)  # one too far
    assert w.settle(106.0, hold) is None  # 1-2 held for only 4 s
    heat = w.settle(107.0, hold)
    assert (heat.event, heat.heat) == (1, 2)


def test_only_a_forward_move_counts_as_selecting_a_heat():
    w = watch_on(SCHED, current=(1, 3))
    w.frame({"current_event": "1", "current_heat": "2"}, 10.0)
    assert w.settle(10.0, 0.0) is None
    assert w.current == 1


def test_other_consoles_are_not_held():
    assert cf.hold_for({"console": {"key": "quantum"}}) == 0.0
    assert cf.hold_for({}) == 0.0


# ── The estimate ──────────────────────────────────────────────────────────────


def test_the_schedule_moves_by_how_late_the_meet_runs():
    w = watch_on(SCHED, current=(1, 1), now=at("10:04"))
    w.frame({"lane_running3": True}, at("10:04"))  # started 4 min late
    assert w.eta(w.by_key[(1, 3)], at("10:05"), DAY) == at("10:10")


def test_without_a_schedule_the_seeds_and_changeover_add_up():
    sched = {**SCHED, "times": {}}
    w = watch_on(sched, current=(1, 1), now=1000.0)
    w.frame({"lane_running3": True}, 1000.0)
    # 1-1 swims 70 s, 1-2 swims 65 s, each with the 45 s default changeover.
    assert w.eta(w.by_key[(1, 3)], 1000.0, None) == 1000.0 + 70 + 45 + 65 + 45


def test_the_changeover_is_learned_from_the_meet():
    sched = {**SCHED, "times": {}}
    w = watch_on(sched, current=(1, 1), now=0.0)
    w.frame({"lane_running3": True}, 0.0)
    w.frame({"current_event": "1", "current_heat": "2"}, 80.0)
    w.settle(80.0, 0.0)
    w.frame({"lane_running3": True}, 100.0)  # 1-1's 70 s swim + 30 s
    assert w.changeover() == 30.0


def test_the_age_table_fills_in_when_there_is_no_seed():
    parts = {"dist": "100", "stroke": "breaststroke", "gender": "girls", "age": "11-12"}
    secs = cf.table_secs(parts)
    assert 100 < secs < 120
    relay = cf.table_secs({"dist": "4x50", "stroke": "freestyle", "age_key": "open"})
    assert 120 < relay < 160
    assert cf.table_secs({"dist": "", "stroke": "freestyle"}) is None


def test_the_events_longest_seed_stands_in_for_an_unseeded_heat():
    sched = schedule(
        {
            (1, 1): [lane(3, "Ana Lee", seed="00:02:30.00")],
            (1, 2): [lane(3, "Bo Li")],
        }
    )
    assert [h.swim for h in cf.build_heats(sched, OFFSET)] == [150.0, 150.0]


# ── What is sent ──────────────────────────────────────────────────────────────


def test_upcoming_by_minutes_fires_once_inside_the_lead():
    w = watch_on(SCHED, current=(1, 1), now=at("10:00"))
    w.frame({"lane_running3": True}, at("10:00"))
    subs, fired = [sub()], set()
    assert due(SCHED, w, subs, at("10:00"), fired) == []  # 1-3 is 6 min off
    notes = due(SCHED, w, subs, at("10:01") + 30, fired)
    assert [(n["event"], n["heat"], n["kind"]) for n in notes] == [(1, 3, "upcoming")]
    assert notes[0]["title"] == "Emma Roy"
    assert notes[0]["body"].startswith("In about 5 min · Event 1, heat 3, lane 4")
    assert due(SCHED, w, subs, at("10:02"), fired) == []


def test_upcoming_by_heats_counts_heats_with_swimmers():
    w = watch_on(SCHED, current=(1, 1))
    notes = due(SCHED, w, [sub(lead={"heats": 2})], 0.0)
    assert [(n["event"], n["heat"]) for n in notes] == [(1, 3)]
    assert notes[0]["body"].startswith("In 2 heats")


def test_selected_fires_when_the_console_reaches_the_heat():
    w = watch_on(SCHED, current=(1, 2))
    w.frame({"current_event": "1", "current_heat": "3"}, 50.0)
    heat = w.settle(50.0, 0.0)
    notes = due(SCHED, w, [sub(lead={"heats": 1})], 50.0, selected=heat)
    # Emma swims 2-1 too, which the same move made the next heat.
    assert [(n["event"], n["heat"], n["kind"]) for n in notes] == [
        (1, 3, "selected"),
        (2, 1, "upcoming"),
    ]
    assert notes[0]["collapse"] == "m1:1:3"


def test_selected_can_be_turned_off():
    w = watch_on(SCHED, current=(1, 2))
    w.frame({"current_event": "1", "current_heat": "3"}, 50.0)
    heat = w.settle(50.0, 0.0)
    assert due(SCHED, w, [sub(selected=False)], 50.0, selected=heat) == []


def test_the_text_is_in_the_followers_language():
    sched = {
        **SCHED,
        "name_parts": {
            "1": {
                "raw": "100 Free",
                "dist": "100",
                "stroke": "freestyle",
                "relay": False,
                "gender": "girls",
                "age": "",
                "age_key": "",
                "round": "",
            }
        },
    }
    w = watch_on(sched, current=(1, 1))
    notes = due(sched, w, [sub(lang="fr", lead={"heats": 2})], 0.0)
    assert (
        notes[0]["body"]
        == "Dans 2 séries · Épreuve 1, série 3, couloir 4\n100 m libre  —  Filles"
    )


# ── The worker ────────────────────────────────────────────────────────────────


def test_a_dead_token_is_forgotten(monkeypatch):
    sent = []

    async def send(note):
        sent.append(note)
        return cloud_push.GONE

    monkeypatch.setattr(cloud_push, "send", send)
    cf.put("m1", sub(lead={"heats": 2}))
    cf.note_followed("m1", True)
    meet = {"schedule_data": SCHED, "utc_offset_minutes": OFFSET, "settings": {}}
    asyncio.run(cf.observe("m1", meet, {"current_event": "1", "current_heat": "1"}))
    assert [(n["event"], n["heat"]) for n in sent] == [(1, 3)]
    assert cf.for_meet("m1")[0] == []


def test_the_relay_watches_the_console_through_forwarded_frames(monkeypatch):
    sent = []

    async def send(note):
        sent.append(note)
        return cloud_push.OK

    async def broadcast(*a, **k):
        return None

    monkeypatch.setattr(cloud_push, "send", send)
    monkeypatch.setattr(cs.manager, "broadcast", broadcast)
    meet = {
        "last_scoreboard": {},
        "clock_at": 0.0,
        "schedule_data": SCHED,
        "utc_offset_minutes": OFFSET,
        "settings": {"console": {"key": "quantum"}},
    }
    monkeypatch.setattr(cs, "_meets", {"m1": meet})
    monkeypatch.setattr(cs, "_relay_sids", {"sid": "m1"})
    cf.put("m1", sub(lead={"heats": 1}))
    cf.note_followed("m1", True)
    frame = {"current_event": "1", "current_heat": "2"}
    asyncio.run(cs._forward("sid", "update_scoreboard", frame))
    assert [(n["heat"], n["kind"]) for n in sent] == [(3, "upcoming")]
    asyncio.run(
        cs._forward(
            "sid", "update_scoreboard", {"current_event": "1", "current_heat": "3"}
        )
    )
    assert [(n["event"], n["heat"], n["kind"]) for n in sent][1:] == [
        (1, 3, "selected"),
        (2, 1, "upcoming"),
    ]


class _Body:
    """What the two routes read of a request: its JSON body and its headers."""

    def __init__(self, body, headers=None):
        self.body, self.headers = body, headers or {}

    async def json(self):
        return self.body


def make_request(body, headers=None) -> Any:
    return _Body(body, headers)


def status(call):
    """A route's status code, whether it returned a response or raised."""
    try:
        return asyncio.run(call).status_code
    except cs.HTTPException as e:
        return e.status_code


def test_a_phone_registers_at_the_meets_worker(monkeypatch):
    # The store's own dict: `meet_for` reads it there, not through `cs`.
    monkeypatch.setitem(cs._meets, "m1", {"name": "M", "settings": {}})
    monkeypatch.setattr(cloud_push, "platforms", lambda: ["apns"])
    body = {
        "token": "t",
        "platform": "apns",
        "lang": "en",
        "swimmers": [{"name": "Emma Roy", "club": "CNQ"}],
        "lead": {"minutes": 10},
        "selected": True,
    }

    def put(meet_id, b):
        return status(cs.route_follow(meet_id, make_request(b)))

    assert put("m1", body) == 204
    assert cf.for_meet("m1")[0][0]["lead"] == 10
    assert put("m1", {**body, "platform": "fcm"}) == 503
    assert put("m1", {**body, "lead": {"minutes": 3}}) == 400
    assert put("nope", body) == 404
    assert cs.route_meet_config("m1")["push"] == ["apns"]


def test_the_import_route_wants_the_node_secret(monkeypatch):
    monkeypatch.setenv("NODE_SECRET", "s3cret")
    data = {"follows": [{**sub(), "lead": 5}], "fired": []}
    assert status(cs.route_follows_import("m1", make_request(data))) == 404
    signed = make_request(data, {"authorization": "Bearer s3cret"})
    assert status(cs.route_follows_import("m1", signed)) == 204
    assert cf.for_meet("m1")[0][0]["swimmers"] == [{"name": "Emma Roy", "club": "CNQ"}]


# ── The platforms' messages ───────────────────────────────────────────────────

NOTE = {
    "token": "abcd",
    "platform": "apns",
    "sandbox": True,
    "meet_id": "m1",
    "event": 1,
    "heat": 3,
    "kind": "upcoming",
    "title": "Emma Roy",
    "body": "In about 5 min · Event 1, heat 3, lane 4",
    "collapse": "m1:1:3",
}


def test_an_apns_alert_is_time_sensitive_and_collapses_per_heat(monkeypatch):
    monkeypatch.setattr(cloud_push, "_apns_jwt", lambda now: "jwt")
    monkeypatch.setenv("APNS_TOPIC", "app.splouch.ios")
    url, headers, body = cloud_push.apns_request(NOTE, sandbox=True, now=1000)
    assert url == "https://api.sandbox.push.apple.com/3/device/abcd"
    assert headers["apns-collapse-id"] == "m1:1:3"
    assert headers["apns-topic"] == "app.splouch.ios"
    assert headers["apns-expiration"] == str(1000 + cloud_push.EXPIRY_SECS)
    import json

    aps = json.loads(body)["aps"]
    assert aps["interruption-level"] == "time-sensitive"
    assert aps["thread-id"] == "m1"


def test_an_fcm_message_is_data_only_so_the_app_picks_the_channel():
    msg = cloud_push.fcm_message({**NOTE, "platform": "fcm"})["message"]
    assert "notification" not in msg
    assert msg["android"]["priority"] == "HIGH"
    assert msg["data"]["kind"] == "upcoming"
    assert all(isinstance(v, str) for v in msg["data"].values())


def test_no_key_no_platform(monkeypatch):
    for name in ("APNS_KEY_ID", "APNS_TEAM_ID", "APNS_TOPIC", "APNS_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("FCM_SERVICE_ACCOUNT_FILE", raising=False)
    assert cloud_push.platforms() == []
