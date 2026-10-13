"""A pool numbered from lane 0 (Settings → Meet → Pool → First lane).

Rows stay 1..num_lanes everywhere (`lane_time<i>` is row i); row i is lane
i + first_lane - 1. The meet file and results' `channel` speak the pool's lane
numbers; decoders that read a lane *number* off the wire convert it to a row.
"""

import contextlib
from typing import cast

import pytest
from starlette.requests import Request

import meet_data
import state
import worker
from console_decoders import make_decoder
from console_decoders.quantum import QuantumDecoder
from console_decoders.swiss_scoreboard import SwissScoreboardDecoder


@pytest.fixture
def lane_zero(monkeypatch):
    monkeypatch.setitem(state.settings, "first_lane", 0)


def test_rows_and_lanes(lane_zero):
    assert (state.first_lane(), state.lane_of(1), state.row_of(0)) == (0, 0, 1)


def test_rows_and_lanes_default(monkeypatch):
    monkeypatch.setitem(state.settings, "first_lane", 1)
    assert (state.lane_of(1), state.row_of(1)) == (1, 1)


def test_the_meet_file_is_read_by_lane(lane_zero, monkeypatch):
    asked = []
    monkeypatch.setattr(
        worker, "get_lane_parts", lambda ev, ht, lane: asked.append(lane) or ("", "")
    )
    monkeypatch.setattr(worker, "get_lane_alt", lambda ev, ht, lane: "")
    monkeypatch.setattr(worker, "get_lane_seed_time", lambda ev, ht, lane: "")
    monkeypatch.setattr(worker, "_get_next_heats", lambda *a, **k: [])
    monkeypatch.setattr(state, "_decoder", make_decoder("cts_sys6", state.settings))
    worker._on_event_changed({}, 1, 1)
    assert asked[:3] == [0, 1, 2], "row 1 is lane 0"


def test_results_channel_is_the_lane(lane_zero, monkeypatch):
    d = make_decoder("manual", {**state.settings, "num_lanes": 8})
    monkeypatch.setattr(d, "get_lane_time", lambda row: "58.21" if row == 1 else "")
    monkeypatch.setattr(d, "get_lane_place", lambda row: "1")
    monkeypatch.setattr(state, "_decoder", d)
    snap = meet_data._build_results_snapshot()
    assert [r["channel"] for r in snap["lanes"]] == [0]


# ── Decoders that read a lane number ───────────────────────────────────────────


def _rtd(header: str, data: str) -> list[int]:
    body = [*b"00000000", 0x01, *header.encode(), 0x02, *data.encode(), 0x04]
    return [0x16, *body, *f"{sum(body) & 0xFF:02X}".encode(), 0x17]


@pytest.mark.parametrize(("first", "row"), [(0, 1), (1, None)])
def test_rtd_lane_zero(first, row):
    d = make_decoder("dak_os2000_rtd", {"num_lanes": 8, "first_lane": first})
    line = f"{'':20}{0:>2}{1:>3}{'58.21':<9}  "
    updates = d.feed(_rtd("0042100222", line))
    if row:
        assert updates[f"lane_time{row}"] == "58.21"
    else:
        assert not any(k.startswith("lane_time") for k in updates)


@pytest.mark.parametrize(("first", "row"), [(0, 1), (1, 10)])
def test_swiss_lines_lane_zero(first, row):
    d = SwissScoreboardDecoder({"num_lanes": 10, "first_lane": first})
    d.feed([0x01, *b"\x14R02\x02\x081 \x02         1.0", 0x04])
    msg = "\x14S02\x02\x08" + "0 \x02" + " 2" + "     58.21"
    assert d.feed([0x01, *msg.encode(), 0x04])[f"lane_time{row}"] == "58.21"


@pytest.mark.parametrize(("first", "row"), [(0, 1), (1, 10)])
def test_osm6_lane_zero(first, row):
    d = QuantumDecoder({"num_lanes": 10, "first_lane": first})
    pt1 = [0x01, 0x02, 0x08, *b"2A ?? 400302   1", 0x04]
    pt2 = [0x01, 0x02, 0x08, 0x0A, *b"0 4", 0x02, *f"{'58.21':>11} ".encode(), 0x04]
    d.feed(pt1)
    assert d.feed(pt2)[f"lane_time{row}"] == "58.21"


def test_position_decoders_ignore_it(lane_zero):
    """CTS channels and Dolphin's lane field are positions: row 1 stays row 1."""
    d = make_decoder("cts_dolphin", {**state.settings, "num_lanes": 4})
    d.feed([*b"Event 1  Heat 1  Race 1\r\n"])
    assert d.feed([*b"1,58.21,False,False\r\n"])["lane_time1"] == "58.21"


# ── Settings ───────────────────────────────────────────────────────────────────


class _FakeRequest:
    def __init__(self, form):
        self._form = form
        self.method = "POST"
        self.session = {"user": "score"}
        self.url = type("U", (), {"path": "/settings"})()
        self.cookies = {}
        self.query_params = {}
        self.headers = {}

    async def form(self):
        return _Form(self._form)


class _Form(dict):
    def getlist(self, key):
        v = self.get(key)
        return [] if v is None else [v]


def test_the_setting_is_saved_and_reaches_the_decoder(monkeypatch):
    import asyncio

    from routes import settings as settings_route

    monkeypatch.setattr(state, "save_settings", lambda: None)
    monkeypatch.setattr(settings_route, "send_event_info", lambda: None)
    monkeypatch.setitem(state.settings, "first_lane", 1)
    monkeypatch.setitem(state.settings, "console_type", "quantum_osm6")
    monkeypatch.setattr(state, "_decoder", make_decoder("quantum_osm6", state.settings))
    form = {
        "pool_setup_submit": "1",
        "num_lanes": str(state.settings.get("num_lanes", 8)),
        "first_lane": "0",
        "pool_length": str(state.settings.get("pool_length", 25)),
        "touchpad_sides": str(state.settings.get("touchpad_sides", 1)),
    }
    with contextlib.suppress(Exception):
        asyncio.run(settings_route.route_settings(cast(Request, _FakeRequest(form))))
    assert state.settings["first_lane"] == 0
    assert cast(QuantumDecoder, state._decoder).first_lane == 0


def test_boards_label_rows_with_the_lane():
    with open("shared/templates/scoreboard_base.html", encoding="utf-8") as f:
        src = f.read()
    assert "{{ i + (first_lane | default(1)) - 1 }}" in src
    assert "var FIRST_LANE = {{ first_lane | default(1) }};" in src
