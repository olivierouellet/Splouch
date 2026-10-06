"""Seed, console and official times on the Schedule (docs/app.md `S-22`, `S-23`).

A lane carries up to three times: the seed from the meet file, the console's
provisional finish, and the result Splash Meet Manager validated, which arrives
when the operator uploads the meet file again on `/mm`
(docs/architecture/meet-manager-results.md). These tests guard each leg:

* the Lenex parser keeps results only from heats Meet Manager calls `OFFICIAL`
  or `SEEDED` — an `INOFFICIAL` heat's results are the console's own times;
* the Pi remembers console times across a restart, one appended line per heat;
* both servers serve the same lane fields, and agree on a fully official heat;
* a same-meet upload refreshes results without touching the heat that is live.
"""

import asyncio
import io
import json
import os
import zipfile

import pytest
from fastapi import Depends

import cloud_server as cs
import state
from meet_data import build_heats, results_summary
from meet_parsers.lenex_parser import load_lenex
from routes import meet as meet_routes, settings as settings_routes
from splouch_times import heat_official, lane_times, wire_time
from web import require_login


def _lxf(xml):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("meet.lef", xml.encode("utf-8"))
    buf.seek(0)
    return buf


# Splash's layout (Lenex 3.0): heats carry a status and a heatid; each result sits
# under the athlete or relay that swam it.
SPLASH = """<?xml version="1.0" encoding="UTF-8"?>
<LENEX xmlns="lenex/3.0" version="3.0">
  <MEETS><MEET name="Results Meet" course="SCM">
    <SESSIONS><SESSION date="2026-10-06">
      <EVENTS>
        <EVENT eventid="10" number="1" name="50 Free">
          <HEATS>
            <HEAT heatid="101" number="1" status="OFFICIAL"/>
            <HEAT heatid="102" number="2" status="INOFFICIAL"/>
            <HEAT heatid="103" number="3"/>
            <HEAT heatid="104" number="4" status="SEEDED"/>
          </HEATS>
        </EVENT>
        <EVENT eventid="20" number="2" name="4x50 Free Relay">
          <HEATS><HEAT heatid="201" number="1" status="OFFICIAL"/></HEATS>
        </EVENT>
      </EVENTS>
    </SESSION></SESSIONS>
    <CLUBS>
      <CLUB name="Aqua Club" shortname="AQUA">
        <ATHLETES>
          <ATHLETE athleteid="1" lastname="Smith" firstname="Jane">
            <ENTRIES><ENTRY eventid="10" heatid="101" lane="3" entrytime="00:00:31.00"/></ENTRIES>
            <RESULTS><RESULT eventid="10" heatid="101" lane="3" swimtime="00:00:30.12"/></RESULTS>
          </ATHLETE>
          <ATHLETE athleteid="2" lastname="Doe" firstname="Mary">
            <ENTRIES><ENTRY eventid="10" heatid="101" lane="4" entrytime="00:00:29.50"/></ENTRIES>
            <RESULTS><RESULT eventid="10" heatid="101" lane="4" swimtime="00:00:30.80" status="DSQ"/></RESULTS>
          </ATHLETE>
          <ATHLETE athleteid="3" lastname="Roy" firstname="Julie">
            <ENTRIES><ENTRY eventid="10" heatid="101" lane="5" entrytime="NT"/></ENTRIES>
            <RESULTS><RESULT eventid="10" heatid="101" lane="5" swimtime="NT" status="DNS"/></RESULTS>
          </ATHLETE>
          <ATHLETE athleteid="4" lastname="Côté" firstname="Marie">
            <ENTRIES><ENTRY eventid="10" heatid="102" lane="3" entrytime="00:00:33.00"/></ENTRIES>
            <RESULTS><RESULT eventid="10" heatid="102" lane="3" swimtime="00:00:32.00"/></RESULTS>
          </ATHLETE>
          <ATHLETE athleteid="5" lastname="Lavoie" firstname="Léa">
            <ENTRIES><ENTRY eventid="10" heatid="103" lane="3" entrytime="00:00:34.00"/></ENTRIES>
            <RESULTS><RESULT eventid="10" heatid="103" lane="3" swimtime="00:00:33.50"/></RESULTS>
          </ATHLETE>
          <ATHLETE athleteid="6" lastname="Gagné" firstname="Lou">
            <ENTRIES><ENTRY eventid="10" heatid="104" lane="3" entrytime="00:00:35.00"/></ENTRIES>
            <RESULTS><RESULT eventid="10" heatid="104" lane="3" swimtime="00:00:34.40"/></RESULTS>
          </ATHLETE>
        </ATHLETES>
        <RELAYS>
          <RELAY number="1">
            <ENTRIES><ENTRY eventid="20" heatid="201" lane="2" entrytime="00:02:00.00"/></ENTRIES>
            <RESULTS><RESULT eventid="20" heatid="201" lane="2" swimtime="00:01:58.70"/></RESULTS>
          </RELAY>
        </RELAYS>
      </CLUB>
    </CLUBS>
  </MEET></MEETS>
</LENEX>"""


@pytest.fixture
def splash():
    return load_lenex(_lxf(SPLASH))


# ── The parser ────────────────────────────────────────────────────────────────


def test_an_official_heat_keeps_its_results(splash):
    heat = splash.results[1][1]
    assert heat[3] == {"time": "00:00:30.12", "status": ""}
    # A status rides with the time the officials kept, if any.
    assert heat[4] == {"time": "00:00:30.80", "status": "DSQ"}
    assert heat[5] == {"time": "", "status": "DNS"}


def test_an_unofficial_heat_and_a_heat_without_status_keep_none(splash):
    """`INOFFICIAL` results are the console's own times, which the Pi already has;
    a heat with no status at all is not trusted either."""
    assert 2 not in splash.results[1]
    assert 3 not in splash.results[1]


def test_a_seeded_heat_keeps_its_results(splash):
    assert splash.results[1][4][3]["time"] == "00:00:34.40"


def test_a_relay_result_is_read(splash):
    assert splash.results[2][1][2]["time"] == "00:01:58.70"


def test_a_start_list_without_results_has_none():
    xml = SPLASH.replace("<RESULTS>", "<!--").replace("</RESULTS>", "-->")
    assert load_lenex(_lxf(xml)).results == {}


# ── The shared lane fields ────────────────────────────────────────────────────


def test_every_time_travels_in_one_form():
    assert wire_time("58.21") == "00:00:58.21"
    assert wire_time("1:02.34") == "00:01:02.34"
    assert wire_time("00:01:02.34") == "00:01:02.34"
    assert wire_time("NT") == ""


def test_the_delta_is_official_minus_seed():
    t = lane_times("00:00:31.00", "00:00:30.15", {"time": "00:00:30.12", "status": ""})
    assert t["console_time"] == "00:00:30.15"
    assert t["result_delta_seconds"] == -0.88
    assert t["result_delta_better"] is True


@pytest.mark.parametrize(
    ("seed", "result"),
    [
        ("NT", {"time": "00:00:30.12", "status": ""}),  # no seed
        ("00:00:31.00", {"time": "00:00:30.80", "status": "DSQ"}),  # not a finish
        ("00:00:31.00", None),  # not official yet
    ],
)
def test_no_delta_without_a_seed_a_finish_and_a_result(seed, result):
    assert lane_times(seed, "", result)["result_delta_seconds"] is None


def test_a_heat_is_official_once_every_swum_lane_is():
    done = {"name": "A", "result_time": "00:00:30.00", "result_status": ""}
    dsq = {"name": "B", "result_time": "", "result_status": "DSQ"}
    empty = {"name": "", "result_time": "", "result_status": ""}
    pending = {"name": "C", "result_time": "", "result_status": ""}
    assert heat_official([done, dsq, empty])
    assert not heat_official([done, pending])
    assert not heat_official([empty])


# ── The Pi ────────────────────────────────────────────────────────────────────


@pytest.fixture
def pi_meet(monkeypatch, tmp_path, splash):
    monkeypatch.setattr(state, "CONSOLE_TIMES_DIR", str(tmp_path))
    monkeypatch.setattr(state, "_test_session", None)
    monkeypatch.setattr(state, "_test_meet_active", False)
    state.set_lenex(splash)
    yield
    state.clear_meet()


def test_the_pi_serves_all_three_times(pi_meet):
    state.record_console_heat(1, 1, {3: "00:00:30.15"})
    heat = build_heats()[0]
    lane3 = next(lane for lane in heat["lanes"] if lane["lane"] == 3)
    assert lane3["seed_time"] == "00:00:31.00"
    assert lane3["console_time"] == "00:00:30.15"
    assert lane3["result_time"] == "00:00:30.12"
    assert heat["official"] is True
    unofficial = build_heats()[1]
    assert unofficial["official"] is False
    assert unofficial["lanes"][0]["result_time"] == ""


def test_console_times_survive_a_restart(pi_meet, splash):
    state.record_console_heat(1, 2, {3: "00:00:32.10"})
    state.record_console_heat(1, 2, {3: "00:00:32.05"})  # the heat swum again
    state.console_times = {}
    state.set_lenex(splash)  # what a boot does
    assert state.console_times == {(1, 2): {3: "00:00:32.05"}}


def test_a_torn_line_is_skipped(pi_meet, splash, tmp_path):
    state.record_console_heat(1, 2, {3: "00:00:32.10"})
    with open(tmp_path / (state.meet_uid() + ".jsonl"), "a") as f:
        f.write('{"event": 1, "he')
    state.set_lenex(splash)
    assert state.console_times == {(1, 2): {3: "00:00:32.10"}}


def test_a_test_replay_is_not_written_to_the_meet(pi_meet, monkeypatch, tmp_path):
    monkeypatch.setattr(state, "_test_session", "replay.serial")
    state.record_console_heat(1, 2, {3: "00:00:32.10"})
    assert state.console_times[(1, 2)] == {3: "00:00:32.10"}
    assert os.listdir(tmp_path) == []


def test_another_meet_starts_with_no_console_times(pi_meet):
    state.record_console_heat(1, 2, {3: "00:00:32.10"})
    state.set_lenex(load_lenex(_lxf(SPLASH.replace("Results Meet", "Other Meet"))))
    assert state.console_times == {}


class _Upload:
    """What `save_upload` reads of a Starlette UploadFile."""

    def __init__(self, data, filename):
        self.file = io.BytesIO(data)
        self.filename = filename


def test_a_same_meet_upload_keeps_the_live_heat(pi_meet, monkeypatch, tmp_path):
    """The file lands while a heat may be swimming: results refresh, the live
    board's snapshot and the console times stay."""
    meet_dir = tmp_path / "meet"
    meet_dir.mkdir()
    start_list = SPLASH.replace('status="OFFICIAL"', 'status="INOFFICIAL"')
    (meet_dir / "m.lxf").write_bytes(_lxf(start_list).getvalue())
    monkeypatch.setattr(state, "MEET_FOLDER", str(meet_dir))
    monkeypatch.setattr(state, "_active_meet_file", "m.lxf")
    state.set_lenex(load_lenex(str(meet_dir / "m.lxf")))
    monkeypatch.setattr(state, "_active_meet_uid", state.meet_uid())
    state.record_console_heat(1, 1, {3: "00:00:30.15"})
    live = {"event": "1", "heat": "2", "lanes": []}
    monkeypatch.setattr(state, "_last_results_snapshot", live)
    emitted = []
    monkeypatch.setattr(settings_routes, "send_event_info", lambda: None)
    monkeypatch.setattr(
        settings_routes, "announce_schedule", lambda: emitted.append("schedule")
    )

    upload = _Upload(_lxf(SPLASH).getvalue(), "m.lxf")
    out = settings_routes._meet_update_file(upload)

    assert out["ok"] is True
    assert state._last_results_snapshot is live
    assert state.console_times[(1, 1)] == {3: "00:00:30.15"}
    assert emitted == ["schedule"]
    summary = out["summary"]
    assert {"event": 1, "heat": 1} in summary["official_heats"]
    assert [s["status"] for s in summary["statuses"]] == ["DSQ", "DNS"]
    assert summary["corrected"] == [
        {
            "event": 1,
            "heat": 1,
            "lane": 3,
            "name": "Jane Smith",
            "console_time": "00:00:30.15",
            "result_time": "00:00:30.12",
        }
    ]


def test_a_summary_reports_only_what_changed(pi_meet):
    heats = build_heats()
    assert results_summary(heats, heats) == {
        "official_heats": [],
        "statuses": [],
        "corrected": [],
    }


def test_the_mm_page_needs_the_login():
    route = next(
        r for r in meet_routes.router.routes if getattr(r, "path", "") == "/mm"
    )
    assert Depends(require_login).dependency in [
        d.dependency for d in getattr(route, "dependencies", [])
    ]


# ── The cloud ─────────────────────────────────────────────────────────────────


def _snapshot(ev, ht, lanes):
    return {
        "event": str(ev),
        "heat": str(ht),
        "lanes": [{"channel": ch, "time": t} for ch, t in lanes.items()],
    }


def test_the_cloud_keeps_console_times_from_results_frames(monkeypatch):
    meet = {"last_results": {}, "console_times": {}}
    stored = {}
    monkeypatch.setattr(cs, "_meets", {"m1": meet})
    monkeypatch.setattr(cs, "_relay_sids", {"sid": "m1"})

    async def broadcast(*a, **k):
        return None

    monkeypatch.setattr(cs.manager, "broadcast", broadcast)
    monkeypatch.setattr(
        cs.cloud_meetstore, "update", lambda mid, **f: stored.update({mid: f})
    )
    asyncio.run(cs._forward("sid", "results_snapshot", _snapshot(1, 1, {3: "30.15"})))
    assert meet["console_times"] == {"1": {"1": {"3": "00:00:30.15"}}}
    assert stored["m1"] == {"console_times": meet["console_times"]}


def test_the_cloud_serves_what_the_pi_serves(pi_meet, monkeypatch):
    """The relay's `schedule_snapshot` plus the cloud's console times give the
    same lanes the Pi's `/schedule.json` does."""
    import relay

    sent = {}
    monkeypatch.setattr(relay, "_local_only", lambda: False)
    monkeypatch.setattr(relay, "_send_schedule_data", lambda c, d: sent.update(d))
    relay.send_schedule()
    state.record_console_heat(1, 1, {3: "00:00:30.15"})
    console = cs._merge_console_times({}, _snapshot(1, 1, {3: "30.15"}))
    sched = json.loads(json.dumps(sent))  # through the wire

    pi = build_heats()
    cloud = cs._build_heats_json(sched, console)
    keys = (
        "console_time",
        "result_time",
        "result_status",
        "result_delta_seconds",
        "result_delta_better",
    )
    assert [h["official"] for h in cloud] == [h["official"] for h in pi]
    for c, p in zip(cloud, pi, strict=True):
        for cl, pl in zip(c["lanes"], p["lanes"], strict=True):
            assert {k: cl[k] for k in keys} == {k: pl[k] for k in keys}
