"""The cloud's own test meets (cloud/cloud_testmeets.py, docs/cloud.md).

Fake Pis the control plane runs so the apps can be tried without a pool. What is
worth pinning: the frames they send are the shapes a real Pi sends (docs/api.md
§5.1–5.5), so an app that works against them works against a meet; they loop and
say so (`test_loop`), so a follower is notified on every pass; and only they can
make a worker forget what a meet showed.
"""

import asyncio
import datetime
import random
import re
import time

import pytest

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


def test_start_caps_the_count_and_stop_ends_every_meet():
    async def go():
        runner = tm.Runner("key", lambda k, u: None)
        assert runner.start(99) == tm.MAX_MEETS
        assert len(runner.status()) == tm.MAX_MEETS
        runner.start(2)
        assert [m["index"] for m in runner.status()] == [1, 2]
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
