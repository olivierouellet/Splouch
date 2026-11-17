"""A prelim and its final under one event number, lane 0, and swimmers who will not swim.

Splash numbers a final like its prelim (the 2026 Union Pacifique invitational:
event 15 is the prelims on Saturday morning and the final that evening), and the
console only ever sends the number. The parser used to read the second EVENT
over the first and crashed on the first entry of a heat the second did not have.
"""

import io
import json
import zipfile

import pytest

import cloud_server as cs
import state
from meet_data import build_heats, results_summary
from meet_parsers.lenex_parser import load_lenex


def _lxf(xml):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("meet.lef", xml.encode("utf-8"))
    buf.seek(0)
    return buf


# Prelims in two heats on Saturday morning, the final in one heat that evening;
# its seeding is `{final}`. Lane 0 is a lane in this pool.
MEET = """<?xml version="1.0" encoding="UTF-8"?>
<LENEX xmlns="lenex/3.0" version="3.0">
  <MEETS><MEET name="Rounds Meet" course="SCM">
    <SESSIONS>
      <SESSION number="1" date="2026-11-07" daytime="08:30">
        <EVENTS>
          <EVENT eventid="1337" number="15" round="PRE" name="100 Free">
            <SWIMSTYLE distance="100" stroke="FREE"/>
            <HEATS>
              <HEAT heatid="1" number="1" daytime="09:00" status="OFFICIAL"/>
              <HEAT heatid="2" number="2" daytime="09:04" status="OFFICIAL"/>
            </HEATS>
          </EVENT>
          <EVENT eventid="1060" number="1" round="TIM" name="50 Free">
            <HEATS><HEAT heatid="3" number="1" status="SEEDED"/></HEATS>
          </EVENT>
        </EVENTS>
      </SESSION>
      <SESSION number="2" date="2026-11-07" daytime="16:00">
        <EVENTS>
          <EVENT eventid="1439" number="15" round="FIN" preveventid="1337" name="100 Free">
            <SWIMSTYLE distance="100" stroke="FREE"/>
            <HEATS><HEAT heatid="9" number="1" daytime="16:10" status="SEEDED"/></HEATS>
          </EVENT>
        </EVENTS>
      </SESSION>
    </SESSIONS>
    <CLUBS>
      <CLUB name="Aqua Club" shortname="AQUA">
        <ATHLETES>
          <ATHLETE athleteid="1" lastname="Smith" firstname="Jane">
            <ENTRIES>
              <ENTRY eventid="1337" heatid="1" lane="0" entrytime="00:01:01.00"/>
              {final}
            </ENTRIES>
            <RESULTS>
              <RESULT eventid="1337" heatid="1" lane="0" swimtime="00:01:00.50"/>
            </RESULTS>
          </ATHLETE>
          <ATHLETE athleteid="2" lastname="Doe" firstname="Mary">
            <ENTRIES>
              <ENTRY eventid="1337" heatid="2" lane="3" entrytime="00:01:02.00"/>
              <ENTRY eventid="1060" heatid="3" lane="4" status="WDR"/>
            </ENTRIES>
          </ATHLETE>
          <ATHLETE athleteid="3" lastname="Roy" firstname="Julie">
            <ENTRIES>
              <ENTRY eventid="1337" heatid="2" lane="4" status="DNS"/>
              <ENTRY eventid="1060" heatid="3" lane="5" status="EXH"/>
            </ENTRIES>
          </ATHLETE>
        </ATHLETES>
        <RELAYS>
          <RELAY number="1">
            <ENTRIES><ENTRY eventid="1060" heatid="3" lane="6" status="WDR"/></ENTRIES>
          </RELAY>
        </RELAYS>
      </CLUB>
    </CLUBS>
  </MEET></MEETS>
</LENEX>"""

FINAL_SEEDED = '<ENTRY eventid="1439" heatid="9" lane="4" entrytime="00:01:00.50"/>'


def _meet(final=""):
    return load_lenex(_lxf(MEET.replace("{final}", final)))


@pytest.fixture
def prelims():
    """Saturday morning: the final is not seeded yet."""
    return _meet()


@pytest.fixture
def finals():
    """Re-exported after the prelims: the final is seeded."""
    return _meet(FINAL_SEEDED)


# ── The parser ────────────────────────────────────────────────────────────────


def test_an_unseeded_final_leaves_the_prelims_live(prelims):
    assert prelims.live_rounds == {15: "prelims"}
    assert prelims.event_rounds[15] == "prelims"
    assert sorted(prelims.start_list[15]) == [1, 2]
    assert prelims.heat_times[15] == {1: "09:00", 2: "09:04"}
    assert prelims.results[15][1][0]["time"] == "00:01:00.50"


def test_a_seeded_final_takes_over_the_number(finals):
    assert finals.live_rounds == {15: "final"}
    assert finals.event_rounds[15] == "final"
    assert finals.start_list[15] == {
        1: {
            4: {
                "name": "Jane Smith",
                "club": "AQUA",
                "seed_time": "00:01:00.50",
                "swimmers": [],
            }
        }
    }
    assert finals.heat_times[15] == {1: "16:10"}
    assert 15 not in finals.results  # the prelims' results are not the final's


def test_a_session_lists_a_shared_number_by_its_live_round(prelims, finals):
    assert [s["events"] for s in prelims.meet_info["sessions"]] == [[15, 1], []]
    assert [s["events"] for s in finals.meet_info["sessions"]] == [[1], [15]]


def test_a_number_with_one_round_is_not_in_live_rounds(finals):
    assert 1 not in finals.live_rounds
    assert finals.event_rounds[1] == "timed_final"


def test_lane_0_is_a_lane(prelims):
    assert prelims.start_list[15][1][0]["name"] == "Jane Smith"
    assert 0 in prelims.results[15][1]


def test_a_swimmer_who_will_not_swim_leaves_the_lane_empty(prelims):
    """Withdrawn (`WDR`) and declared non-starters (`DNS`) are left out, swimmer
    or relay; an exhibition swim (`EXH`) is still a swim."""
    assert sorted(prelims.start_list[15][2]) == [3]
    assert sorted(prelims.start_list[1][1]) == [5]


def test_the_crash_this_was(finals):
    """The first entry of the prelims' heat 2, which the final does not have, used
    to raise KeyError: the final had replaced the event's heats."""
    assert finals.start_list[15][1][4]["name"] == "Jane Smith"


# ── Console times ─────────────────────────────────────────────────────────────


def _heat(heats, ev, ht):
    return next(h for h in heats if (h["event"], h["heat"]) == (ev, ht))


@pytest.fixture
def pi(monkeypatch, tmp_path):
    monkeypatch.setattr(state, "CONSOLE_TIMES_DIR", str(tmp_path))
    monkeypatch.setattr(state, "_test_session", None)
    monkeypatch.setattr(state, "_test_meet_active", False)
    yield tmp_path
    state.clear_meet()


def test_a_final_does_not_show_its_prelim_heats_times(pi, prelims, finals):
    state.set_lenex(prelims)
    state.record_console_heat(15, 1, {0: "00:01:00.48"})
    assert _heat(build_heats(), 15, 1)["lanes"][0]["console_time"] == "00:01:00.48"

    state.set_lenex(finals)  # the re-export, between sessions
    assert _heat(build_heats(), 15, 1)["lanes"][0]["console_time"] == ""
    # Kept, for the rounds side by side on the Schedule later.
    assert state.console_times == {(15, 1, "prelims"): {0: "00:01:00.48"}}


def test_a_rounds_console_times_survive_a_restart(pi, finals):
    state.set_lenex(finals)
    state.record_console_heat(15, 1, {4: "00:00:59.90"})
    state.record_console_heat(1, 1, {5: "00:00:30.00"})
    lines = [
        json.loads(line)
        for line in (pi / (state.meet_uid() + ".jsonl")).read_text().splitlines()
    ]
    assert [line.get("round") for line in lines] == ["final", None]
    state.console_times = {}
    state.set_lenex(finals)
    assert state.console_times == {
        (15, 1, "final"): {4: "00:00:59.90"},
        (1, 1, ""): {5: "00:00:30.00"},
    }


def test_a_summary_does_not_compare_a_final_with_its_prelims(pi, prelims):
    """The prelims' heat 1 was official already; the final's heat 1 just became."""
    state.set_lenex(prelims)
    before = build_heats()
    swum = MEET.replace('daytime="16:10" status="SEEDED"', 'status="OFFICIAL"')
    result = '<RESULT eventid="1439" heatid="9" lane="4" swimtime="00:00:59.95"/>'
    state.set_lenex(load_lenex(_lxf(swum.replace("{final}", FINAL_SEEDED + result))))
    after = build_heats()
    assert results_summary(before, after)["official_heats"] == []
    assert results_summary(before, after, moved={15})["official_heats"] == [
        {"event": 15, "heat": 1}
    ]


# ── The cloud ─────────────────────────────────────────────────────────────────


def test_the_cloud_keeps_a_rounds_console_times_apart(pi, monkeypatch, finals):
    import relay

    sent = {}
    monkeypatch.setattr(relay, "_local_only", lambda: False)
    monkeypatch.setattr(relay, "_send_schedule_data", lambda c, d: sent.update(d))
    state.set_lenex(finals)
    relay.send_schedule()
    sched = json.loads(json.dumps(sent))  # through the wire
    assert sched["rounds"] == {"15": "final"}

    snap = {"event": "15", "heat": "1", "lanes": [{"channel": 4, "time": "59.90"}]}
    console = cs._merge_console_times(
        {"15": {"1": {"4": "00:01:00.48"}}}, snap, sched["rounds"]
    )
    assert console["15/final"] == {"1": {"4": "00:00:59.90"}}
    lane = _heat(cs._build_heats_json(sched, console), 15, 1)["lanes"][0]
    assert lane["console_time"] == "00:00:59.90"
    # An older Pi's meet, with no `rounds`: keyed by the number, as before.
    assert cs._merge_console_times({}, snap) == {"15": {"1": {"4": "00:00:59.90"}}}
