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
from meet_data import (
    _build_results_snapshot,
    _get_next_heats,
    build_heats,
    heat_order,
    heat_step,
    results_summary,
    send_event_info,
)
from meet_parsers.lenex_parser import age_band, load_lenex


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
                "seed_course": "",
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


def test_every_round_is_kept_in_running_order(finals):
    assert [
        (r["event"], r["round"], r["live"], r["date"], sorted(r["start_list"]))
        for r in finals.rounds
    ] == [
        (15, "prelims", False, "2026-11-07", [1, 2]),
        (1, "", True, "2026-11-07", [1]),
        (15, "final", True, "2026-11-07", [1]),
    ]
    assert finals.rounds[0]["results"][1][0]["time"] == "00:01:00.50"


def test_two_rounds_of_one_kind_still_differ():
    """An odd file: one number, two timed finals. Each keeps its own identity."""
    xml = MEET.replace("{final}", FINAL_SEEDED).replace('"FIN"', '"PRE"')
    data = load_lenex(_lxf(xml))
    assert [r["round"] for r in data.rounds if r["event"] == 15] == [
        "prelims",
        "prelims-2",
    ]


# ── Console times ─────────────────────────────────────────────────────────────


def _heat(heats, ev, ht, rnd=None):
    return next(
        h
        for h in heats
        if (h["event"], h["heat"]) == (ev, ht) and rnd in (None, h["round"])
    )


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
    # Kept, and shown on the prelims' own card (`S-24`).
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


# ── The Schedule (docs/app.md `S-24`) ─────────────────────────────────────────


def test_a_client_that_did_not_ask_gets_the_live_round_only(pi, finals):
    state.set_lenex(finals)
    assert [(h["event"], h["heat"], h["round"]) for h in build_heats()] == [
        (1, 1, ""),
        (15, 1, "final"),
    ]


def test_every_round_in_running_order_when_asked(pi, prelims, finals):
    state.set_lenex(prelims)
    state.record_console_heat(15, 1, {0: "00:01:00.48"})
    state.set_lenex(finals)
    heats = build_heats(all_rounds=True)
    assert [(h["event"], h["heat"], h["round"]) for h in heats] == [
        (15, 1, "prelims"),
        (15, 2, "prelims"),
        (1, 1, ""),
        (15, 1, "final"),
    ]
    prelim = _heat(heats, 15, 1, "prelims")["lanes"][0]
    assert prelim["console_time"] == "00:01:00.48"
    assert prelim["result_time"] == "00:01:00.50"
    assert _heat(heats, 15, 1, "final")["time"] == "16:10"


def test_a_shared_number_always_names_its_round_to_a_phone(pi, monkeypatch, finals):
    """Two cards must not read the same, whatever Settings hides (`S-24`)."""
    monkeypatch.setitem(state.settings, "round_names_phone", [])
    state.set_lenex(finals)
    heats = build_heats(all_rounds=True)
    assert _heat(heats, 15, 1, "prelims")["event_name_parts"]["round"] == "prelims"
    assert _heat(heats, 15, 1, "final")["event_name_parts"]["round"] == "final"
    assert _heat(heats, 1, 1)["event_name_parts"]["round"] == ""


def test_the_live_frames_name_the_round(pi, monkeypatch, finals):
    state.set_lenex(finals)
    monkeypatch.setattr(state._decoder, "last_event_sent", (15, 1))
    sent = []
    monkeypatch.setattr("meet_data.bus.emit", lambda ns, ev, d: sent.append(d))
    monkeypatch.setattr("meet_data.relay.relay_emit", lambda ev, d: None)
    send_event_info()
    assert sent[0]["current_round"] == "final"
    assert _build_results_snapshot()["round"] == "final"
    assert [h["round"] for h in _get_next_heats(1, 1)] == ["final"]
    monkeypatch.setattr(state._decoder, "last_event_sent", (1, 1))
    assert _build_results_snapshot()["round"] == ""


def test_the_cloud_serves_every_round_as_the_pi_does(pi, monkeypatch, prelims, finals):
    import relay

    state.set_lenex(prelims)
    state.record_console_heat(15, 1, {0: "00:01:00.48"})
    state.set_lenex(finals)
    sent = {}
    monkeypatch.setattr(relay, "_local_only", lambda: False)
    monkeypatch.setattr(relay, "_send_schedule_data", lambda c, d: sent.update(d))
    relay.send_schedule()
    sched = json.loads(json.dumps(sent))  # through the wire
    # The cloud heard the prelims' heat while they were live.
    console = cs._merge_console_times(
        {},
        {"event": "15", "heat": "1", "lanes": [{"channel": 0, "time": "1:00.48"}]},
        {"15": "prelims"},
    )

    def shape(heats):
        return [
            (
                h["event"],
                h["heat"],
                h["round"],
                h["event_name"],
                h["event_name_parts"],
                h["time"],
                h["age"],
                h["seeded"],
                h["official"],
                [
                    (
                        lane["lane"],
                        lane["name"],
                        lane["console_time"],
                        lane["result_time"],
                    )
                    for lane in h["lanes"]
                ],
            )
            for h in heats
        ]

    for all_rounds in (False, True):
        assert shape(cs._build_heats_json(sched, console, all_rounds)) == shape(
            build_heats(all_rounds)
        )


def test_an_older_pis_schedule_still_serves(pi):
    """No `order` or `other_rounds`: every round asked for is the live one."""
    sched = {
        "events": [[3, [1]]],
        "names": {"3": "50 Free"},
        "start_list": {"3": {"1": {"4": {"name": "A", "club": "C"}}}},
    }
    heats = cs._build_heats_json(sched, {}, all_rounds=True)
    assert [(h["event"], h["heat"], h["round"]) for h in heats] == [(3, 1, "")]


# ── Running order beyond the Schedule ─────────────────────────────────────────


def test_next_heat_follows_the_sessions(pi, finals):
    """The Pi's Next heat walks the file's order: the final swims after the
    morning's events, not between 14 and 16."""
    state.set_lenex(finals)
    assert heat_order() == [(1, 1), (15, 1)]
    assert heat_step(1, 1) == (15, 1)
    assert heat_step(15, 1) is None


def test_notifications_count_heats_in_session_order(pi, monkeypatch, finals):
    """The cloud's heats-before count (`N-05`) walks `events`, which the relay
    sends in running order, each number dated by its live round's session."""
    import cloud_follows
    import relay

    state.set_lenex(finals)
    sent = {}
    monkeypatch.setattr(relay, "_local_only", lambda: False)
    monkeypatch.setattr(relay, "_send_schedule_data", lambda c, d: sent.update(d))
    relay.send_schedule()
    sched = json.loads(json.dumps(sent))
    heats = cloud_follows.build_heats(sched, 0)
    assert [(h.event, h.heat, h.index) for h in heats] == [(1, 1, 0), (15, 1, 1)]
    assert sched["dates"]["15"] == "2026-11-07"


# ── Heat labels (docs/app.md `S-25`) ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("agemin", "agemax", "band"),
    [
        ("13", "14", "13-14"),
        ("-1", "7", "≤7"),
        ("17", "-1", "17+"),
        ("8", "8", "8"),
        ("-1", "-1", ""),
        (None, None, ""),
    ],
)
def test_an_age_group_reads_as_a_band(agemin, agemax, band):
    assert age_band(agemin, agemax) == band


def _finals_by_age():
    """The final split by age, one heat per group, as Splash seeds it."""
    xml = MEET.replace(
        '<HEATS><HEAT heatid="9" number="1" daytime="16:10" status="SEEDED"/></HEATS>',
        '<AGEGROUPS><AGEGROUP agegroupid="91" agemin="11" agemax="12"/>'
        '<AGEGROUP agegroupid="92" agemin="17" agemax="-1"/></AGEGROUPS>'
        '<HEATS><HEAT heatid="9" number="1" agegroupid="91" status="SEEDED"/>'
        '<HEAT heatid="10" number="2" agegroupid="92" status="SEEDED"/></HEATS>',
    )
    return load_lenex(_lxf(xml.replace("{final}", FINAL_SEEDED)))


def test_a_finals_heat_names_its_age_group(pi):
    state.set_lenex(_finals_by_age())
    heats = build_heats(all_rounds=True)
    assert [h["age"] for h in heats if h["round"] == "final"] == ["11-12", "17+"]
    assert _heat(heats, 15, 1, "prelims")["age"] == ""


def test_an_unseeded_round_says_so(pi, prelims, finals):
    state.set_lenex(prelims)
    heats = build_heats(all_rounds=True)
    assert _heat(heats, 15, 1, "final")["seeded"] is False
    assert _heat(heats, 15, 1, "prelims")["seeded"] is True
    state.set_lenex(finals)
    assert _heat(build_heats(all_rounds=True), 15, 1, "final")["seeded"] is True


def test_the_cloud_serves_the_heat_labels(pi, monkeypatch):
    import relay

    state.set_lenex(_finals_by_age())
    sent = {}
    monkeypatch.setattr(relay, "_local_only", lambda: False)
    monkeypatch.setattr(relay, "_send_schedule_data", lambda c, d: sent.update(d))
    relay.send_schedule()
    sched = json.loads(json.dumps(sent))
    for all_rounds in (False, True):
        cloud = cs._build_heats_json(sched, {}, all_rounds)
        pi_heats = build_heats(all_rounds)
        assert [(h["age"], h["seeded"]) for h in cloud] == [
            (h["age"], h["seeded"]) for h in pi_heats
        ]


def test_the_live_frames_name_the_heats_age_group(pi, monkeypatch):
    """On the board, in Results and in Next heats the name is the heat's: heat 2
    of an open final is the 17+ (docs/api.md §5.1)."""
    state.set_lenex(_finals_by_age())
    monkeypatch.setattr(state._decoder, "last_event_sent", (15, 2))
    sent = []
    monkeypatch.setattr("meet_data.bus.emit", lambda ns, ev, d: sent.append(d))
    monkeypatch.setattr("meet_data.relay.relay_emit", lambda ev, d: None)
    send_event_info()
    assert sent[0]["event_name_parts"]["age"] == "17+"
    assert "17+" in sent[0]["event_name"]
    assert _build_results_snapshot()["event_name_parts"]["age"] == "17+"
    nxt = _get_next_heats(1, 1)
    assert [h["event_name_parts"]["age"] for h in nxt] == ["11-12", "17+"]
    assert "11-12" in nxt[0]["event_name"]
    # A heat with no age group of its own keeps the event's name.
    monkeypatch.setattr(state._decoder, "last_event_sent", (1, 1))
    assert _build_results_snapshot()["event_name_parts"]["age"] == ""


def test_the_schedule_keeps_the_events_name(pi):
    """The Schedule shows the age beside the name (`S-25`), not in it."""
    state.set_lenex(_finals_by_age())
    heat = _heat(build_heats(), 15, 2)
    assert heat["age"] == "17+"
    assert heat["event_name_parts"]["age"] == ""


@pytest.mark.parametrize(
    ("pool", "lanes"),
    [
        ('<POOL name="Centre" lanemin="0" lanemax="9"/>', [0, 9]),
        ('<POOL lanemax="9"/>', [0, 9]),
        ('<POOL lanemin="1" lanemax="9"/>', [1, 9]),
        ('<POOL name="Centre"/>', None),
        ('<POOL lanemax="x"/>', None),
        ('<POOL lanemin="3" lanemax="8"/>', None),
        ("", None),
    ],
)
def test_the_files_pool_lanes(pool, lanes):
    """Settings checks its lanes against these (Settings → Meet). The meet seeds
    lane 0, which a POOL without `lanemin` counts as its first lane."""
    xml = MEET.replace('course="SCM">', f'course="SCM">{pool}').replace("{final}", "")
    assert load_lenex(_lxf(xml)).meet_info.get("lanes_lenex") == lanes


def test_a_pool_without_lane_0_starts_at_lane_1():
    """No `lanemin` and no swim in lane 0: lanes 1 to `lanemax`."""
    xml = MEET.replace('course="SCM">', 'course="SCM"><POOL lanemax="8"/>')
    xml = xml.replace('lane="0"', 'lane="1"').replace("{final}", "")
    assert load_lenex(_lxf(xml)).meet_info["lanes_lenex"] == [1, 8]


@pytest.mark.parametrize("audience", ["board", "phone"])
def test_settings_can_keep_the_heats_age_out_of_the_name(pi, monkeypatch, audience):
    """Settings → Displays and Settings → Phones, one box each: the board decides
    `event_name`, the phones `event_name_parts` (docs/api.md §5.1)."""
    from meet_data import get_event_name_display, get_event_name_parts

    state.set_lenex(_finals_by_age())
    monkeypatch.setitem(state.settings, f"heat_age_{audience}", False)
    board, phone = get_event_name_display(15, 2), get_event_name_parts(15, 2)
    assert ("17+" in board) is (audience == "phone")
    assert (phone["age"] == "17+") is (audience == "board")
    # The Schedule's `age` (`S-25`) is the phones': their box hides it too, on
    # the Pi and through the cloud.
    assert (_heat(build_heats(), 15, 2)["age"] == "17+") is (audience == "board")
    import relay

    sent = {}
    monkeypatch.setattr(relay, "_local_only", lambda: False)
    monkeypatch.setattr(relay, "_send_schedule_data", lambda c, d: sent.update(d))
    relay.send_schedule()
    cloud = cs._build_heats_json(json.loads(json.dumps(sent)), {}, True)
    assert [h["age"] for h in cloud] == [h["age"] for h in build_heats(True)]


@pytest.mark.parametrize("audience", ["board", "phone"])
def test_settings_can_keep_the_gender_out_of_the_name(pi, monkeypatch, audience):
    """Everywhere the audience reads a name: live, and on the Schedule."""
    from meet_data import get_event_name_display, get_event_name_parts

    xml = MEET.replace('name="100 Free"', 'name="100 Free Girls"').replace(
        "{final}", FINAL_SEEDED
    )
    state.set_lenex(load_lenex(_lxf(xml)))
    monkeypatch.setitem(state.settings, "locale", "en")
    monkeypatch.setitem(state.settings, f"name_gender_{audience}", False)
    board, phone = get_event_name_display(15, 1), get_event_name_parts(15, 1)
    heat = _heat(build_heats(), 15, 1)
    assert ("Girls" in board) is (audience == "phone")
    assert ("Girls" in heat["event_name"]) is (audience == "phone")
    assert (phone["gender"] == "girls") is (audience == "board")
    assert (heat["event_name_parts"]["gender"] == "girls") is (audience == "board")
    assert phone["stroke"] == "freestyle"


@pytest.mark.parametrize(
    ("style", "distance"),
    [
        ('distance="50" relaycount="4"', 200),
        ('distance="100" relaycount="1"', 100),
        ('distance="100"', 100),
        ('distance="50" relaycount="x"', None),
    ],
)
def test_a_relay_swims_every_leg(style, distance):
    """Lenex gives a relay's distance per leg: a 4x50 is 200 m of lengths to
    count (`expected_splits`), not the first swimmer's 50."""
    xml = MEET.replace(
        '<SWIMSTYLE distance="100" stroke="FREE"/>',
        f'<SWIMSTYLE {style} stroke="FREE"/>',
    ).replace("{final}", "")
    assert load_lenex(_lxf(xml)).event_distances.get(15) == distance


# ── Session headings (docs/app.md `S-26`) and a seed's course (`S-27`) ─────────


def _named_sessions(final=FINAL_SEEDED):
    xml = MEET.replace(
        '<SESSION number="1" date="2026-11-07"',
        '<SESSION number="1" name="Samedi am" date="2026-11-07"',
    )
    return load_lenex(_lxf(xml.replace("{final}", final)))


def test_each_heat_names_its_session(pi, monkeypatch):
    import relay

    state.set_lenex(_named_sessions())
    heats = build_heats(all_rounds=True)
    assert [
        (h["event"], h["round"], h["session"], h["session_name"]) for h in heats
    ] == [
        (15, "prelims", 1, "Samedi am"),
        (15, "prelims", 1, "Samedi am"),
        (1, "", 1, "Samedi am"),
        (15, "final", 2, ""),
    ]
    # The cloud serves the same, live rounds and the others alike.
    sent = {}
    monkeypatch.setattr(relay, "_local_only", lambda: False)
    monkeypatch.setattr(relay, "_send_schedule_data", lambda c, d: sent.update(d))
    relay.send_schedule()
    sched = json.loads(json.dumps(sent))
    for all_rounds in (False, True):
        cloud = cs._build_heats_json(sched, {}, all_rounds)
        assert [(h["session"], h["session_name"]) for h in cloud] == [
            (h["session"], h["session_name"]) for h in build_heats(all_rounds)
        ]


def test_the_meet_preview_heads_each_session(pi):
    """/meet draws the Schedule's headings (`S-26`), and names each session in its
    header: the file's name, else the numbered word."""
    import re

    from starlette.requests import Request

    import app as pi_app
    from routes.meet import route_meet

    state.set_lenex(_named_sessions())
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/meet",
            "query_string": b"",
            "headers": [],
            "session": {},
            "app": pi_app.app,
            "router": pi_app.app.router,
        }
    )
    html = route_meet(request).body.decode()
    headings = re.findall(r'<h2 class="session-heading">([^<]*)</h2>', html)
    assert headings == ["Samedi am", "Session 2"]
    assert "Samedi am: 2026-11-07" in html


def test_an_older_pi_sends_no_session():
    """A snapshot from before `sessions`: no heading, rather than an error."""
    sched = {
        "events": [[3, [1]]],
        "names": {"3": "50 Free"},
        "start_list": {"3": {"1": {"4": {"name": "A", "seed_time": "00:00:30.00"}}}},
    }
    heat = cs._build_heats_json(sched)[0]
    assert (heat["session"], heat["session_name"]) == (0, "")
    assert heat["lanes"][0]["seed_course"] == ""


def test_a_seed_from_another_course_has_no_gap(pi, monkeypatch):
    """A long-course seed at a short-course meet: shown, but nothing measured
    against it — on the Schedule, through the cloud, or on the board."""
    import relay
    from meet_data import get_lane_seed_time

    xml = MEET.replace(
        'lane="0" entrytime="00:01:01.00"/>',
        'lane="0" entrytime="00:01:01.00" entrycourse="LCM"/>',
    ).replace(
        'lane="3" entrytime="00:01:02.00"/>',
        'lane="3" entrytime="00:01:02.00" entrycourse="SCM"/>',
    )
    state.set_lenex(load_lenex(_lxf(xml.replace("{final}", ""))))
    long_course = _heat(build_heats(), 15, 1)["lanes"][0]
    assert long_course["seed_course"] == "LCM"
    assert long_course["result_time"] == "00:01:00.50"
    assert long_course["result_delta_seconds"] is None
    # The meet's own course is no course to mention.
    assert _heat(build_heats(), 15, 2)["lanes"][0]["seed_course"] == ""
    assert get_lane_seed_time(15, 1, 0) == ""
    assert get_lane_seed_time(15, 2, 3) == "00:01:02.00"

    sent = {}
    monkeypatch.setattr(relay, "_local_only", lambda: False)
    monkeypatch.setattr(relay, "_send_schedule_data", lambda c, d: sent.update(d))
    relay.send_schedule()
    cloud = cs._build_heats_json(json.loads(json.dumps(sent)))
    lane = next(h for h in cloud if (h["event"], h["heat"]) == (15, 1))["lanes"][0]
    assert (lane["seed_course"], lane["result_delta_seconds"]) == ("LCM", None)
