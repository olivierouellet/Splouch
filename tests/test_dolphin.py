"""CTS Dolphin over the Dolphin software's TCP/IP streaming API, and the worker's TCP
transport that carries it. Also the Gen7 RS-485 outputs that reuse other decoders.
"""

import contextlib
import socket
import threading
import time
from typing import cast

import pytest
from starlette.requests import Request

import state
import worker
from console_decoders import console_info_for, make_decoder, option_values
from console_decoders.dolphin import DolphinDecoder
from console_decoders.omnisport_2000 import Omnisport2000Decoder
from console_decoders.swiss_scoreboard import SwissScoreboardDecoder


def _line(text: str) -> list[int]:
    return [*f"{text}\r\n".encode()]


def _feed(d: DolphinDecoder, *lines: str) -> dict:
    updates: dict = {}
    for text in lines:
        updates.update(d.feed(_line(text)))
    return updates


def _dolphin(**cfg) -> DolphinDecoder:
    return DolphinDecoder({"num_lanes": 6, **cfg})


# ── Parsing ────────────────────────────────────────────────────────────────────


def test_current_event_and_heat_announces_the_heat():
    d = _dolphin()
    updates = _feed(d, "CurrentEventAndHeat,5.0.19,3,12,2,7")
    assert updates["event_changed"] == (12, 2)
    assert (updates["current_event"], updates["current_heat"]) == ("12", "2")
    assert _feed(d, "CurrentEventAndHeat,5.0.19,3,12,2,7") == {}, "same heat again"


def test_a_race_block_gives_times_places_and_splits():
    d = _dolphin()
    _feed(d, "CurrentEventAndHeat,5.0.19,1,1,1,1")
    split = ",1,15.10,15.12,15.11,15.11,2,,,,"
    updates = _feed(
        d,
        "Event 1  Heat 1  Race 1",
        "1,01:02.34,False,False" + split,
        "2,58.21,False,False",
        "3,58.21,False,False",
        "4,1:05.00,True,False",
        "5,,False,True",
    )
    assert updates["lane_time1"] == "1:02.34"
    assert (updates["lane_place2"], updates["lane_place3"]) == ("1", "1"), "a tie"
    assert updates["lane_place1"] == "3"
    assert updates["lane_time4"] == "DQ"
    assert d.get_lane_place(4) == " "
    assert d.get_lane_time(5) == ""
    assert updates["lane_splits1"] == 1
    assert d.race_finished()


def test_lanes_numbered_from_zero_are_shifted():
    d = _dolphin(dolphin_lane_base="0")
    updates = _feed(d, "Event 1  Heat 1  Race 1", "0,58.21,False,False")
    assert updates["lane_time1"] == "58.21"


def test_other_lines_are_ignored():
    d = _dolphin()
    assert _feed(d, "autoSendUpdates set to ON", "EventChanged,1,1,Free,2,0,F") == {}


# ── Results hold ───────────────────────────────────────────────────────────────


def test_the_next_heat_waits_for_the_hold(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("console_decoders.dolphin.time.monotonic", lambda: now[0])
    d = _dolphin(dolphin_results_hold="10")
    _feed(d, "CurrentEventAndHeat,v,1,1,1,1", "Event 1  Heat 1  Race 1")
    _feed(d, "1,58.21,False,False")
    assert _feed(d, "CurrentEventAndHeat,v,1,1,2,2") == {}, "held: results stay"
    assert d.get_lane_time(1) == "58.21"
    now[0] += 5
    assert d.tick() == {}
    now[0] += 6
    updates = d.tick()
    assert updates["event_changed"] == (1, 2)
    assert updates["lane_time1"] == ""
    assert d.tick() == {}


def test_no_hold_moves_on_at_once():
    d = _dolphin(dolphin_results_hold="0")
    _feed(d, "CurrentEventAndHeat,v,1,1,1,1", "Event 1  Heat 1  Race 1")
    _feed(d, "1,58.21,False,False")
    assert _feed(d, "CurrentEventAndHeat,v,1,1,2,2")["event_changed"] == (1, 2)


# ── Transport ──────────────────────────────────────────────────────────────────


def test_dolphin_is_a_tcp_console():
    info = console_info_for("cts_dolphin")
    assert info and info["transport"] == "tcp" and info["requires_serial"] is True
    d = make_decoder("cts_dolphin", {"console_host": "10.0.0.5"})
    assert d.tcp_endpoint == ("10.0.0.5", 13382)
    cfg = {"console_options": {"cts_dolphin": {"tcp_port": "14917"}}}
    assert option_values("cts_dolphin", cfg)["tcp_port"] == "14917"


def test_the_worker_reads_a_tcp_console(monkeypatch):
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]
    received: list[bytes] = []

    def serve():
        conn, _ = server.accept()
        with conn:
            received.append(conn.recv(4096))
            conn.sendall(
                b"CurrentEventAndHeat,v,1,4,2,1\r\nEvent 1  Heat 1  Race 1\r\n"
            )
            time.sleep(0.5)

    threading.Thread(target=serve, daemon=True).start()
    packets: list[str] = []
    monkeypatch.setattr(
        worker, "_handle_packet", lambda b: packets.append(bytes(b).decode())
    )
    monkeypatch.setattr(worker, "_set_serial_status", lambda *a: None)
    decoder = DolphinDecoder({"console_host": "127.0.0.1", "tcp_port": str(port)})
    monkeypatch.setattr(state, "_decoder", decoder)
    monkeypatch.setattr(state, "_worker_gen", 1)
    t = threading.Thread(target=worker._run_live_tcp, args=(1,), daemon=True)
    t.start()
    deadline = time.time() + 3
    while len(packets) < 2 and time.time() < deadline:
        time.sleep(0.02)
    state._worker_gen = 2
    t.join(2)
    server.close()
    assert received and received[0].startswith(b"autoSendUpdates,ON\r\n")
    assert packets[:2] == [
        "CurrentEventAndHeat,v,1,4,2,1\r\n",
        "Event 1  Heat 1  Race 1\r\n",
    ]


# ── Settings → Timing ──────────────────────────────────────────────────────────


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


def test_the_host_is_saved(monkeypatch):
    import asyncio

    from routes import settings as settings_route

    monkeypatch.setattr(settings_route, "_restart_worker", lambda: None)
    monkeypatch.setattr(state, "save_settings", lambda: None)
    monkeypatch.setitem(state.settings, "console_type", "cts_dolphin")
    monkeypatch.setitem(state.settings, "console_host", "")
    monkeypatch.setattr(state, "_decoder_console_type", "cts_dolphin")
    monkeypatch.setattr(state, "_decoder", make_decoder("cts_dolphin", state.settings))
    form = {
        "timing_settings_submit": "1",
        "console_model": "Dolphin (Colorado Timing System)",
        "console_type": "cts_dolphin",
        "console_host": " 192.168.1.20 ",
    }
    with contextlib.suppress(Exception):
        asyncio.run(settings_route.route_settings(cast(Request, _FakeRequest(form))))
    assert state.settings["console_host"] == "192.168.1.20"
    assert state._decoder.tcp_endpoint == ("192.168.1.20", 13382)


# ── Gen7 RS-485 outputs ────────────────────────────────────────────────────────


def _rtd(header: str, data: str) -> list[int]:
    body = [*b"00000000", 0x01, *header.encode(), 0x02, *data.encode(), 0x04]
    return [0x16, *body, *f"{sum(body) & 0xFF:02X}".encode(), 0x17]


@pytest.mark.parametrize("sub", ["21", "01", "37"])
def test_gen7_rtd_accepts_any_sub_code_by_default(sub):
    d = cast(Omnisport2000Decoder, make_decoder("cts_gen7_rs485_rtd", {}))
    assert d.feed(_rtd(f"004{sub}00000", "    0.1  "))["running_time"] == "0.1"


def test_gen7_omega_defaults_to_rank_mode():
    d = cast(SwissScoreboardDecoder, make_decoder("cts_gen7_rs485_omega", {}))
    assert d.rank_mode is True
