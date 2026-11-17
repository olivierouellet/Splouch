"""Hy-Tek `.hy3` exports: the layout checked against a real Team Manager file,
the records here written by hand (no real swimmer in the repo)."""

import state
from meet_data import build_heats, get_event_name_display, heat_order
from meet_parsers.hy3_parser import load_hy3
from meet_parsers.meet_file import is_meet_file, load_structured, meet_files


def _line(code, *fields):
    """A record: *fields* are (1-based column, text) pairs."""
    chars = list(code.ljust(130))
    for col, text in fields:
        chars[col - 1 : col - 1 + len(text)] = text
    return "".join(chars)


def _swimmer(sid, last, first, gender="F"):
    return _line("D1", (3, gender), (4, f"{sid:>5}"), (9, last), (29, first))


def _event(code, ev, dist, stroke="A", gender="F", lo=0, hi=0, seed="0.00", **kw):
    """An E1 (individual, `sid`) or F1 (relay, `team` and `letter`)."""
    head = (
        [(4, f"{kw['sid']:>5}")]
        if code == "E1"
        else [(3, kw["team"].ljust(5)), (8, kw["letter"])]
    )
    return _line(
        code,
        *head,
        (14, gender),
        (15, gender),
        (16, f"{dist:>6}"),
        (22, stroke),
        (23, f"{lo:>3}"),
        (26, f"{hi:>3}"),
        (39, f"{ev:>3} "),
        (52, f"{seed:>8}"),
    )


def _round(code, rnd, heat, lane, time="", time_code=" "):
    return _line(
        code, (3, rnd), (4, f"{time:>8}"), (13, time_code), (21, f"{heat:>3}"),
        (24, f"{lane:>3}"),
    )  # fmt: skip


def _legs(*sids):
    fields = []
    for i, sid in enumerate(sids):
        fields += [
            (3 + 13 * i, "F"),
            (4 + 13 * i, f"{sid:>5}"),
            (15 + 13 * i, str(i + 1)),
        ]
    return _line("F3", *fields)


HEADER = [
    _line("A1", (3, "02Meet Entries")),
    _line(
        "B1",
        (3, "Summer Invitational"),
        (48, "Home Pool"),
        (93, "06012026"),
        (101, "06012026"),
    ),  # fmt: skip
    _line("B2", (99, "Y")),
    _line("C1", (3, "AQUA"), (8, "Aqua Club")),
    _swimmer(1, "Smith", "Jane"),
    _swimmer(2, "Doe", "Mary"),
    _swimmer(3, "Roy", "Julie"),
    _swimmer(4, "Lee", "Ann"),
]


def _load(tmp_path, *lines, name="HFILE001.HY3"):
    path = tmp_path / name
    path.write_bytes(("\r\n".join([*HEADER, *lines, "Z0"]) + "\r\n").encode("cp1252"))
    return load_hy3(str(path))


def test_a_seeded_export_gives_the_start_list(tmp_path):
    d = _load(
        tmp_path,
        _event("E1", 11, 25, hi=6, seed="26.29", sid=1),
        _round("E2", "P", 1, 3),
        _event("E1", 11, 25, hi=6, seed="125.34", sid=2),
        _round("E2", "P", 2, 0),
        _event("F1", 1, 100, hi=6, team="AQUA", letter="A"),
        _round("F2", "P", 1, 4),
        _legs(4, 3, 2, 1),
    )
    assert d.event_names == {
        1: "Girls 6 & Under 100 Freestyle Relay",
        11: "Girls 6 & Under 25 Freestyle",
    }
    assert d.start_list[11] == {
        1: {
            3: {
                "name": "Jane Smith",
                "club": "AQUA",
                "seed_time": "00:00:26.29",
                "swimmers": [],
            }
        },
        # Lane 0 is a lane; seconds past 60 are minutes.
        2: {
            0: {
                "name": "Mary Doe",
                "club": "AQUA",
                "seed_time": "00:02:05.34",
                "swimmers": [],
            }
        },
    }
    relay = d.start_list[1][1][4]
    assert (relay["name"], relay["club"], relay["seed_time"]) == (
        "Aqua Club A",
        "AQUA",
        "",
    )
    assert [(s["pos"], s["first"]) for s in relay["swimmers"]] == [
        (1, "Ann"),
        (2, "Julie"),
        (3, "Mary"),
        (4, "Jane"),
    ]
    assert d.event_distances == {1: 100, 11: 25}
    # `P` is also the only round of a timed-final meet: no round word.
    assert d.event_rounds == {} and d.live_rounds == {}
    assert d.meet_info["name"] == "Summer Invitational"
    assert d.meet_info["course"] == "SCY" and d.meet_info["pool_length_lenex"] == 25
    # One day: every event is that day's, as a Lenex session's would be.
    assert d.meet_info["sessions"][0]["date"] == "2026-06-01"
    assert d.meet_info["sessions"][0]["events"] == [1, 11]


def test_prelims_and_finals_under_one_number(tmp_path):
    prelims_only = _load(
        tmp_path,
        _event("E1", 5, 50, lo=13, hi=14, sid=1),
        _round("E2", "P", 1, 4),
        name="a.hy3",
    )
    assert prelims_only.live_rounds == {}

    d = _load(
        tmp_path,
        _event("E1", 5, 50, lo=13, hi=14, sid=1),
        _round("E2", "P", 1, 4, time="31.20"),
        _round("E2", "F", 1, 5),
        _event("E1", 5, 50, lo=13, hi=14, sid=2),
        _round("E2", "P", 2, 4, time="30.10", time_code="Q"),
        _event("E1", 6, 50, lo=13, hi=14, sid=3),
        _round("E2", "P", 1, 3),
        _round("E2", "F", 1, 3),
        name="b.hy3",
    )
    assert d.live_rounds == {5: "final", 6: "final"}
    assert d.event_rounds == {5: "final", 6: "final"}
    assert d.start_list[5] == {1: {5: d.start_list[5][1][5]}}
    # Every prelim before every final: the format has no sessions.
    assert [(r["event"], r["round"], r["live"]) for r in d.rounds] == [
        (5, "prelims", False),
        (6, "prelims", False),
        (5, "final", True),
        (6, "final", True),
    ]
    assert d.rounds[0]["results"] == {
        1: {4: {"time": "00:00:31.20", "status": ""}},
        2: {4: {"time": "00:00:30.10", "status": "DSQ"}},
    }
    assert d.event_names[5] == "Girls 13-14 50 Freestyle"


def test_a_scratch_leaves_the_lane_empty(tmp_path):
    d = _load(
        tmp_path,
        _event("E1", 3, 100, stroke="E", lo=15, sid=1),
        _round("E2", "P", 1, 4, time_code="S"),
        _event("E1", 3, 100, stroke="E", lo=15, sid=2),
        _round("E2", "P", 1, 5, time_code="R"),
    )
    assert list(d.start_list[3][1]) == [5]
    assert d.results[3] == {1: {5: {"time": "", "status": "DNS"}}}
    assert d.event_names[3] == "Women 15 & Over 100 Individual Medley"


def test_an_unseeded_entry_is_not_on_the_start_list(tmp_path):
    """A roster-only or entries-only export: no heat, nothing to show."""
    d = _load(tmp_path, _event("E1", 3, 100, sid=1), _round("E2", "P", 0, 0))
    assert d.start_list == {} and d.rounds == []


def test_the_board_and_the_schedule_read_a_hy3_meet(tmp_path, monkeypatch):
    monkeypatch.setitem(state.settings, "locale", "en")
    state.set_lenex(
        _load(
            tmp_path,
            _event("E1", 11, 25, lo=9, hi=10, sid=1),
            _round("E2", "P", 1, 3),
        )
    )
    try:
        assert heat_order() == [(11, 1)]
        assert "9-10" in get_event_name_display(11, 1)
        heat = build_heats()[0]
        assert heat["lanes"][0]["name"] == "Jane Smith"
        assert heat["event_name_parts"]["dist"] == "25"
        assert heat["event_name_parts"]["age"] == "9-10"
    finally:
        state.clear_meet()


def test_a_hy3_is_a_meet_file_in_any_case(tmp_path):
    for name in ("HFILE001.HY3", "b.hy3", "c.lxf", "d.CSV", "e.zip", "f.cl2"):
        (tmp_path / name).write_text("")
    assert sorted(p.split("/")[-1] for p in meet_files(str(tmp_path))) == [
        "HFILE001.HY3",
        "b.hy3",
        "c.lxf",
        "d.CSV",
    ]
    assert is_meet_file("x.Hy3") and not is_meet_file("x.cl2")
    path = tmp_path / "HFILE001.HY3"
    path.write_bytes(("\r\n".join(HEADER) + "\r\n").encode("cp1252"))
    assert load_structured(str(path)).meet_info["name"] == "Summer Invitational"
