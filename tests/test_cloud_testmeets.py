"""The cloud's own test meets (cloud/cloud_testmeets.py, docs/cloud.md).

Fake Pis the control plane runs so the apps can be tried without a pool. What is
worth pinning: the frames they send are the shapes a real Pi sends (docs/api.md
§5.1–5.5), so an app that works against them works against a meet; they loop and
say so (`test_loop`), so a follower is notified on every pass; and only they can
make a worker forget what a meet showed.
"""

import asyncio
import base64
import datetime
import os
import random
import re
import time

import pytest

import cloud_i18n
import cloud_paths
import cloud_server as cs
import cloud_testmeets as tm

CLOCK = re.compile(r"^(?:(\d+):)?(\d{1,2})\.(\d{2})$")
LANE_KEY = re.compile(
    r"^lane_(name|club|name_alt|time|place|running|delta|delta_seconds|"
    r"delta_better|splits)([1-9]|1[0-2])$"
)
HEAD_KEYS = {
    "current_event",
    "current_heat",
    "event_name",
    "event_name_parts",
    "heat_time",
    "expected_splits",
    "split_step",
    "running_time",
}


def _frames(meet, at=0, seed=1):
    return list(tm.heat_frames(meet, at, random.Random(seed)))


# ── What a meet sends ──────────────────────────────────────────────────────────


def test_a_meet_is_the_same_every_time():
    day = datetime.date(2026, 10, 6)
    assert tm.build_meet(3, day) == tm.build_meet(3, day)
    assert tm.build_meet(3, day)["uid"] != tm.build_meet(4, day)["uid"]


def test_meets_are_spread_over_three_days():
    day = datetime.date(2026, 10, 6)
    dates = [tm.build_meet(i, day)["date"] for i in range(1, 6)]
    assert dates == [
        "2026-10-06",
        "2026-10-07",
        "2026-10-08",
        "2026-10-06",
        "2026-10-07",
    ]


@pytest.mark.parametrize("index", range(1, tm.MAX_MEETS + 1))
def test_every_board_frame_is_one_a_pi_could_send(index):
    meet = tm.build_meet(index)
    for at in range(len(tm.heats(meet))):
        for wait, event, data in _frames(meet, at):
            assert wait >= 0
            if event != "update_scoreboard":
                continue
            for k, v in data.items():
                assert k in HEAD_KEYS or LANE_KEY.match(k), k
                if k == "running_time" or (k.startswith("lane_time") and v):
                    assert CLOCK.match(v), (k, v)
            assert isinstance(data.get("current_event", ""), str)


def test_a_heat_fills_the_board_starts_and_finishes_every_lane():
    meet = tm.build_meet(1)
    frames = _frames(meet)
    events = [e for _, e, _ in frames]
    assert events[:2] == ["next_heats", "update_scoreboard"]
    head = frames[1][2]
    assert head["current_event"] == "1" and head["current_heat"] == "1"
    assert head["expected_splits"] == 2  # 50 m in a 25 m pool

    entries = meet["events"][0]["heats"][1]
    start = frames[2][2]
    assert start["running_time"] == "0.00"
    assert {k for k in start if k.startswith("lane_running")} == {
        f"lane_running{lane}" for lane in entries
    }

    board = {}
    for _, event, data in frames:
        if event == "update_scoreboard":
            board.update(data)
    places = sorted(int(board[f"lane_place{lane}"]) for lane in entries)
    assert places == list(range(1, len(entries) + 1))
    assert not any(board[f"lane_running{lane}"] for lane in entries)
    assert all(board[f"lane_splits{lane}"] == 2 for lane in entries)
    assert frames[-1] == (tm.RESULTS_HOLD, None, None)


def test_the_last_results_snapshot_has_every_lane_by_place():
    frames = _frames(tm.build_meet(1))
    last = [d for _, e, d in frames if e == "results_snapshot"][-1]
    assert last["sort"] == "place"
    assert [r["place_int"] for r in last["lanes"]] == list(
        range(1, len(last["lanes"]) + 1)
    )
    for r in last["lanes"]:
        assert CLOCK.match(r["time"])
        assert isinstance(r["delta_seconds"], float)
        assert r["delta_better"] == (r["delta_seconds"] < 0)


def test_results_by_lane_keep_lane_order():
    frames = list(tm.heat_frames(tm.build_meet(1), 0, random.Random(1), sort="lane"))
    snaps = [d for _, e, d in frames if e == "results_snapshot"]
    assert all(s["sort"] == "lane" for s in snaps)
    for s in snaps:
        lanes = [r["channel"] for r in s["lanes"]]
        assert lanes == sorted(lanes)


def test_a_short_first_heat_unless_every_lane_is_filled():
    day = datetime.date(2026, 10, 6)
    short = tm.build_meet(1, day)["events"][0]["heats"][1]
    full = tm.build_meet(1, day, full_lanes=True)
    assert 1 not in short and tm.LANES not in short
    for e in full["events"]:
        for lanes in e["heats"].values():
            assert sorted(lanes) == list(range(1, tm.LANES + 1))


def test_register_carries_the_lap_direction():
    meet = tm.build_meet(1)
    assert tm.register_meta(meet, "k", "t")["settings"]["lap_direction"] == "down"
    up = tm.register_meta(meet, "k", "t", "up")
    assert up["settings"]["lap_direction"] == "up"


def test_options_fall_back_to_their_defaults():
    assert tm.options() == tm.OPTIONS
    assert tm.options({"lap_direction": "sideways", "full_lanes": 1, "x": 2}) == (
        tm.OPTIONS
    )
    picked = {"full_lanes": True, "lap_direction": "up", "results_sort": "lane"}
    assert tm.options(picked) == picked


def test_next_heats_are_the_heats_after_this_one():
    meet = tm.build_meet(1)
    data = _frames(meet, at=0)[0][2]
    order = [(e["num"], h) for e, h in tm.heats(meet)]
    assert [(h["event"], h["heat"]) for h in data["heats"]] == order[1:4]
    assert all(isinstance(h["event"], int) for h in data["heats"])


def test_the_schedule_names_every_heat_and_its_start_list():
    meet = tm.build_meet(2)
    s = tm.schedule(meet)
    assert [ev for ev, _ in s["events"]] == [e["num"] for e in meet["events"]]
    for ev, heats in s["events"]:
        for h in heats:
            assert s["times"][str(ev)][str(h)]
            lanes = s["start_list"][str(ev)][str(h)]
            assert lanes and all(entry["seed_time"] for entry in lanes.values())
    assert s["results"] == {}


def test_validated_results_are_meet_manager_shaped():
    meet = tm.build_meet(1)
    finals = {}
    for _ in tm.heat_frames(meet, 0, random.Random(1), finals):
        pass
    entries = meet["events"][0]["heats"][1]
    assert set(finals) == set(entries)
    out = tm.official(finals, random.Random(2))
    assert set(out) == {str(lane) for lane in entries}
    for r in out.values():
        if r["status"]:
            assert r == {"time": "", "status": "DSQ"}
        else:
            assert re.match(r"^\d{2}:\d{2}:\d{2}\.\d{2}$", r["time"])


@pytest.mark.parametrize("lag", tm.CONSOLE_ONLY)
def test_validation_leaves_the_last_heats_to_the_console(lag):
    meet = tm.build_meet(1)
    order = tm.heats(meet)
    swum, results = [], {}
    rng = random.Random(3)
    assert not tm.validate(results, swum, lag, rng)
    for e, h in order[:4]:
        swum.append((e, h, {1: (1, 3000.0)}))
        tm.validate(results, swum, lag, rng)
    validated = [(int(ev), int(h)) for ev, hs in results.items() for h in hs]
    assert validated == [(e["num"], h) for e, h in order[: 4 - lag]]


def test_a_loop_is_timed_from_when_it_starts():
    meet = tm.build_meet(1)
    tm.retime(meet, datetime.datetime(2026, 10, 6, 14, 0))
    first = meet["events"][0]["times"]
    assert first[1] == "14:00" and first[2] > "14:00"


def test_register_says_the_console_times():
    meta = tm.register_meta(tm.build_meet(1), "k", "t")
    assert meta["meet_uid"] == "test-1" and meta["ticket"] == "t"
    assert meta["settings"]["console"] == {"key": "test", "timed": True}
    assert meta["settings"]["labels"]
    assert meta["session_dates"] == [meta["meet_date"]]
    png = base64.b64decode(meta["settings"]["picker_image_b64"])
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    assert "home_icon_b64" not in meta["settings"]  # the picker card only


def test_each_meet_is_a_team_with_its_own_logo():
    meets = [tm.build_meet(i) for i in range(1, tm.MAX_MEETS + 1)]
    assert len({m["team"] for m in meets}) == tm.MAX_MEETS
    assert len({m["name"] for m in meets}) == tm.MAX_MEETS
    images = {
        tm.register_meta(m, "k", "t")["settings"]["picker_image_b64"] for m in meets
    }
    assert len(images) == tm.MAX_MEETS


@pytest.mark.parametrize("lang", ["en", "fr", "es"])
def test_every_team_is_named_and_drawn(lang):
    names = cloud_i18n.strings(lang, "test_meets")
    assert set(names) == set(tm._TEAMS)
    for team in tm._TEAMS:
        logo = os.path.join(cloud_paths.STATIC_DIR, "img", "test_meet", f"{team}.png")
        with open(logo, "rb") as f:
            assert f.read(8) == b"\x89PNG\r\n\x1a\n", team
        assert os.path.exists(logo[: -len("png")] + "svg"), team


def test_a_meet_is_named_in_its_own_language():
    meets = [tm.build_meet(i) for i in range(1, len(tm._LANGS) + 1)]
    assert {m["lang"] for m in meets} == {"en", "fr", "es"}
    for m in meets:
        assert m["name"] == cloud_i18n.strings(m["lang"], "test_meets")[m["team"]]


# ── Running them ───────────────────────────────────────────────────────────────


class _Socket:
    """A relay socket that takes everything and never answers."""

    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, raw):
        import json

        self.log.append(json.loads(raw))

    def __aiter__(self):
        return self

    async def __anext__(self):
        await asyncio.Event().wait()
        raise StopAsyncIteration


def test_a_meet_registers_then_loops_with_a_clean_start_each_pass():
    sent = []

    def assign(key, uid):
        return {"meet_id": "m-" + uid, "relay_url": "ws://x", "ticket": "tk"}

    async def go():
        runner = tm.Runner("key", assign, connect=lambda url: _Socket(sent), speed=1e9)
        runner.start(1)
        deadline = time.monotonic() + 20
        while sum(m["event"] == "test_loop" for m in sent) < 2:
            assert time.monotonic() < deadline, "the meet never looped"
            await asyncio.sleep(0.01)
        status = runner.status()
        await runner.stop()
        return status

    status = asyncio.run(go())
    names = [m["event"] for m in sent]
    assert names[:3] == ["register", "test_loop", "schedule_snapshot"]
    assert sent[0]["data"]["key"] == "key" and sent[0]["data"]["ticket"] == "tk"
    first_loop = names.index("test_loop", 1)
    second = names.index("test_loop", first_loop + 1)
    heats = [
        (m["data"]["current_event"], m["data"]["current_heat"])
        for m in sent[first_loop:second]
        if m["event"] == "update_scoreboard" and "current_heat" in m["data"]
    ]
    meet = tm.build_meet(1)
    assert heats == [(str(e["num"]), str(h)) for e, h in tm.heats(meet)]
    assert status[0]["connected"] and status[0]["meet_id"] == "m-test-1"

    # Every schedule sent mid-pass: official for the older heats, the console's
    # alone for the last one or two swum, seeds for the rest.
    started, checked = 0, 0
    for m in sent[first_loop:second]:
        if m["event"] == "update_scoreboard" and "current_heat" in m["data"]:
            started += 1
        elif m["event"] == "schedule_snapshot" and started:
            results = m["data"]["results"]
            official = sum(len(hs) for hs in results.values())
            assert started - official in tm.CONSOLE_ONLY
            checked += 1
    # A heat that adds nothing to validate sends nothing; by the end of the pass
    # all but the last one or two are official.
    assert checked and official >= len(heats) - max(tm.CONSOLE_ONLY)


def test_start_caps_the_count_and_stop_ends_every_meet():
    async def go():
        runner = tm.Runner("key", lambda k, u: None)
        assert runner.start(99) == tm.MAX_MEETS
        assert len(runner.status()) == tm.MAX_MEETS
        runner.start(2)
        assert [m["index"] for m in runner.status()] == [1, 2]
        before = dict(runner.tasks)
        runner.start(2)
        assert runner.tasks == before  # same options: left running
        runner.start(2, {"results_sort": "lane"})
        assert runner.options["results_sort"] == "lane"
        assert all(runner.tasks[i] is not before[i] for i in before)
        await runner.stop()
        return runner.status()

    assert asyncio.run(go()) == []


# ── The worker's side ──────────────────────────────────────────────────────────


@pytest.fixture
def worker(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cs.cloud_meetstore, "update", lambda mid, **f: calls.append(("store", mid))
    )
    monkeypatch.setattr(
        cs.cloud_follows, "clear_fired", lambda mid: calls.append(("fired", mid))
    )

    async def _broadcast(channel, event, data=None):
        calls.append(("broadcast", channel))

    monkeypatch.setattr(cs.manager, "broadcast", _broadcast)
    return calls


@pytest.mark.parametrize("test", [True, False])
def test_only_a_test_meet_can_start_over(monkeypatch, worker, test):
    meet = {"test": test, "console_times": {"1": {}}, "last_results": {"x": 1}}
    monkeypatch.setattr(cs, "_meets", {"m1": meet})
    monkeypatch.setattr(cs, "_relay_sids", {"sid": "m1"})
    asyncio.run(cs._on_test_loop("sid"))
    if test:
        assert ("fired", "m1") in worker and meet["console_times"] == {}
    else:
        assert worker == [] and meet["console_times"] == {"1": {}}


# ── The control plane's side (Postgres) ────────────────────────────────────────


@pytest.fixture
def test_key(pg):
    import cloud_auth

    return cloud_auth.test_organizer_key()


def test_there_is_one_test_organizer_and_it_is_never_listed(test_key):
    import cloud_auth

    assert cloud_auth.test_organizer_key() == test_key
    assert test_key not in cloud_auth.load_keys()


def test_a_test_meet_is_badged_and_holds_no_rollout(test_key):
    import cloud_registry as reg

    reg.heartbeat("ca1", 1, [], host="https://ca1.example", region="ca", version="v1")
    found = reg.assign(test_key, "test-1")
    out = reg.register(test_key, "test-1", {"name": "Test meet 1"}, "ca1", 1)
    assert out["test"] and out["meet_id"] == found["meet_id"]
    (row,) = reg.list_meets()
    assert row["test"]

    reg.heartbeat(
        "ca1", 1, [out["meet_id"]], version="v1", frames={out["meet_id"]: time.time()}
    )
    reg.start_rollout("v2")
    assert next(n for n in reg.nodes() if n["name"] == "ca1")["target_version"] == "v2"


def test_stopping_leaves_no_card_behind(test_key):
    import cloud_auth
    import cloud_registry as reg

    other = cloud_auth.add_organizer("Club")
    reg.register(test_key, "test-1", {"name": "Test meet 1"}, "ca1", 1)
    reg.register(other, "real", {"name": "Real"}, "ca1", 1)
    reg.forget_test_meets()
    assert [m["name"] for m in reg.list_meets()] == ["Real"]


def test_the_wanted_count_survives(test_key):
    import cloud_registry as reg

    assert reg.test_meets_wanted() == 0
    reg.set_test_meets_wanted(5)
    assert reg.test_meets_wanted() == 5
    assert reg.test_meets_options() == {}
    reg.set_test_meets_options({"full_lanes": True})
    assert reg.test_meets_options() == {"full_lanes": True}


def test_the_admin_form_defaults_are_the_options():
    import cloud_control

    assert cloud_control.TestMeetsIn().model_dump(exclude={"count"}) == tm.OPTIONS
