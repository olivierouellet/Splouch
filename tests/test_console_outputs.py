"""One console, several outputs: the two-level registry, per-console decoder options,
and each decoder's handling of what its `*_serial.md` documents.
"""

import contextlib
import os
from typing import cast

import pytest
from starlette.requests import Request

import state
from console_decoders import (
    CONSOLES,
    DECODERS,
    console_models,
    decoder_config,
    make_decoder,
    option_specs,
    option_values,
    same_decoding,
)
from console_decoders.cts_gen6 import CTSGen6Decoder
from console_decoders.omnisport_2000 import Omnisport2000Decoder
from console_decoders.quantum import QuantumDecoder
from console_decoders.swiss_scoreboard import SwissScoreboardDecoder
from console_decoders.swiss_timing_ares21 import Ares21Decoder

# ── Registry ───────────────────────────────────────────────────────────────────


def test_every_console_output_builds_its_decoder():
    for e in CONSOLES:
        dec = make_decoder(e.key, {"num_lanes": 8})
        assert type(dec) is DECODERS[e.decoder], e.key


def test_menu_groups_outputs_under_their_console():
    menu = dict(console_models())
    assert [k for k, _ in menu["OmniSport 2000 (Daktronics)"]] == [
        "dak_os2000_rtd",
        "dak_os2000_rtd_omni6000",
        "dak_os2000_cts",
        "dak_os2000_udp",
        "dak_os2000_j6_cts",
        "dak_os2000_omega",
    ]
    assert [k for k, _ in menu["ARES 21 (Swiss Timing Omega)"]] == [
        "ares21_ertd",
        "ares21_unt4",
        "ares21_gp_osm6",
    ]
    assert menu["System 6 (Colorado Timing System)"] == [("cts_sys6", "")]


def test_exposed_options_exist_on_the_decoder():
    for e in CONSOLES:
        keys = {o.key for o in DECODERS[e.decoder].OPTIONS}
        assert set(e.options) <= keys, e.key
        for opt, default in option_specs(e.key):
            assert default in {c for c, _ in opt.choices}, (e.key, opt.key)


def test_each_console_keeps_its_own_option_values():
    """The same OSM6 decoder: 7-E-1 on an ARES GP port, 8-N-1 on a Quantum."""
    settings = {"console_options": {"quantum_osm6": {"serial_format": "7E1"}}}
    assert option_values("ares21_gp_osm6", {})["serial_format"] == "7E1"
    assert option_values("quantum_osm6", {})["serial_format"] == "8N1"
    assert option_values("quantum_osm6", settings)["serial_format"] == "7E1"
    assert option_values("ares21_gp_osm6", settings)["serial_format"] == "7E1"


def test_a_value_outside_the_choices_falls_back_to_the_default():
    settings = {"console_options": {"ares21_unt4": {"scb_line_mode": "sideways"}}}
    assert option_values("ares21_unt4", settings)["scb_line_mode"] == "lane"


def test_presets_and_options_reach_the_decoder():
    omni2000 = cast(Omnisport2000Decoder, make_decoder("dak_os2000_rtd", {}))
    omni6000 = cast(Omnisport2000Decoder, make_decoder("dak_os2000_rtd_omni6000", {}))
    assert (omni2000.line_base, omni6000.line_base) == (222, 200)
    cfg = decoder_config(
        "dak_os2000_rtd",
        {"console_options": {"dak_os2000_rtd": {"rtd_offset": "5000"}}},
    )
    shifted = cast(Omnisport2000Decoder, make_decoder("dak_os2000_rtd", cfg))
    assert shifted.offset_shift == 5000
    gp = make_decoder("ares21_gp_osm6", {}).serial_config
    assert (gp.bytesize, gp.parity) == (7, "E")


def test_two_outputs_of_one_decoder_do_not_decode_alike():
    assert not same_decoding("dak_os2000_rtd", "dak_os2000_rtd_omni6000", {})
    assert same_decoding("cts_sys5", "cts_gen7_legacy", {})


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


def _post(monkeypatch, form, console="cts_sys6"):
    import asyncio

    from routes import settings as settings_route

    monkeypatch.setattr(settings_route, "_restart_worker", lambda: None)
    monkeypatch.setattr(state, "save_settings", lambda: None)
    monkeypatch.setitem(state.settings, "console_type", console)
    monkeypatch.setitem(state.settings, "console_options", {})
    monkeypatch.setattr(state, "_decoder_console_type", console)
    monkeypatch.setattr(state, "_decoder", make_decoder(console, state.settings))
    with contextlib.suppress(Exception):
        asyncio.run(
            settings_route.route_settings(
                cast(Request, _FakeRequest({"timing_settings_submit": "1", **form}))
            )
        )


def test_picking_a_console_model_selects_its_first_output(monkeypatch):
    _post(
        monkeypatch,
        {"console_model": "ARES 21 (Swiss Timing Omega)", "console_type": "cts_sys6"},
    )
    assert state.settings["console_type"] == "ares21_ertd"
    assert isinstance(state._decoder, Ares21Decoder)


def test_picking_an_output_keeps_it(monkeypatch):
    _post(
        monkeypatch,
        {
            "console_model": "ARES 21 (Swiss Timing Omega)",
            "console_type": "ares21_unt4",
        },
        console="ares21_ertd",
    )
    assert state.settings["console_type"] == "ares21_unt4"
    assert isinstance(state._decoder, SwissScoreboardDecoder)


def test_an_option_is_saved_for_its_console_and_rebuilds_the_decoder(monkeypatch):
    _post(
        monkeypatch,
        {
            "console_model": "ARES 21 (Swiss Timing Omega)",
            "console_type": "ares21_unt4",
            "console_opt_scb_line_mode": "rank",
        },
        console="ares21_unt4",
    )
    assert state.settings["console_options"] == {
        "ares21_unt4": {"scb_line_mode": "rank"}
    }
    assert cast(SwissScoreboardDecoder, state._decoder).rank_mode is True


def test_option_fields_of_the_previous_console_are_ignored(monkeypatch):
    _post(
        monkeypatch,
        {
            "console_model": "Quantum (Swiss Timing Omega)",
            "console_type": "ares21_unt4",
            "console_opt_scb_line_mode": "rank",
        },
        console="ares21_unt4",
    )
    assert state.settings["console_type"] == "quantum_osm6"
    assert state.settings["console_options"] == {}


def test_the_timing_tab_shows_only_the_selected_consoles_options():
    from jinja2 import Environment, FileSystemLoader

    from routes.settings import _console_menu, _console_option_fields

    t = state.settings_strings("en")
    env = Environment(loader=FileSystemLoader(os.path.join("server", "templates")))
    tpl = env.get_template("settings/debug.html")

    def render(console):
        return tpl.render(
            t=t,
            console_type=console,
            console_menu=_console_menu(t),
            console_option_fields=_console_option_fields(t, console),
            console_info=None,
            console_requires_serial=True,
            serial_port_list=[],
            serial_port="",
            finish_debounce=3.0,
            finish_debounce_default=3.0,
            split_min_duration=1.0,
            split_min_duration_default=1.0,
        )

    unt4 = render("ares21_unt4")
    assert 'name="console_opt_scb_line_mode"' in unt4
    assert 'name="console_type"' in unt4 and "SCB — Venus ERTD" in unt4
    cts = render("cts_sys6")
    assert "console_opt_" not in cts
    assert '<input type="hidden" name="console_type" value="cts_sys6">' in cts


# ── CTS legacy: the character table ────────────────────────────────────────────


def _cts_header(channel: int, running: bool = False) -> int:
    return 0x80 | (0x40 if running else 0) | (((channel ^ 0x1F) & 0x1F) << 1)


def _cts_lane(channel: int, place_nibble: int, digits: str) -> list[int]:
    """Lane packet: place in slot 1, time digits (6, 'MMSSHH') in slots 2-7."""
    data = [(1 << 4) | place_nibble]
    for slot, ch in enumerate(digits, start=2):
        data.append((slot << 4) | (0 if ch == " " else 0x0F ^ int(ch)))
    return [_cts_header(channel), *data]


@pytest.mark.parametrize(
    ("nibble", "place"),
    [(5, "10"), (0, " "), (4, "11"), (3, "12"), (0xF, "10"), (14, "1")],
)
def test_cts_places_ten_to_twelve(nibble, place):
    d = CTSGen6Decoder({"num_lanes": 10})
    updates = d.feed(_cts_lane(3, nibble, " 10234"))
    assert updates["lane_place3"] == place
    assert updates["lane_time3"] == "1:02.34"


def test_a_tenth_place_no_longer_holds_the_heat_open():
    d = CTSGen6Decoder({"num_lanes": 10})
    for lane in range(1, 11):
        place_nibble = 5 if lane == 10 else 0x0F ^ lane
        d.feed(_cts_lane(lane, place_nibble, " 10234"))
    assert d.race_finished()


def test_cts_parity_option():
    assert make_decoder("cts_sys6", {}).serial_config.parity == "E"
    cfg = {"console_options": {"dak_os2000_cts": {"serial_parity": "N"}}}
    assert make_decoder("dak_os2000_cts", cfg).serial_config.parity == "N"


# ── Daktronics RTD: header, presets, offset ────────────────────────────────────


def _rtd(header: str, data: str) -> list[int]:
    body = [*b"00000000", 0x01, *header.encode(), 0x02, *data.encode(), 0x04]
    return [0x16, *body, *f"{sum(body) & 0xFF:02X}".encode(), 0x17]


def _line(lane: int, place: str, time: str) -> str:
    return f"{'':20}{lane:>2}{place:>3}{time:<9}  "


def test_omni6000_lines_start_at_200():
    d = make_decoder("dak_os2000_rtd_omni6000", {"num_lanes": 8})
    updates = d.feed(_rtd("0042100200", _line(4, "1", "58.21")))
    assert updates["lane_time4"] == "58.21"
    assert (
        make_decoder("dak_os2000_rtd", {"num_lanes": 8})
        .feed(_rtd("0042100200", _line(4, "1", "58.21")))
        .get("lane_time4")
        is None
    )


def test_rtd_offset_is_subtracted():
    cfg = {
        "num_lanes": 8,
        "console_options": {"dak_os2000_rtd": {"rtd_offset": "5000"}},
    }
    d = make_decoder("dak_os2000_rtd", cfg)
    assert d.feed(_rtd("0042105000", "    0.1  "))["running_time"] == "0.1"
    assert d.feed(_rtd("0042100000", "    0.2  ")) == {}, "below the offset"


def test_other_message_types_are_dropped():
    d = make_decoder("dak_os2000_rtd", {"num_lanes": 8})
    assert d.feed(_rtd("0030100002", "")) == {}
    assert d.feed(_rtd("0040100000", "    0.1  ")) == {}, "ARES sub-code"


def test_ares_accepts_sub_code_00():
    d = Ares21Decoder({"num_lanes": 8})
    assert d.feed(_rtd("0040000000", "    0.1  "))["running_time"] == "0.1"


def test_ares_ertd_line_time_after_a_space():
    """The ARES puts rank (3) at +22, a space, then the time from +26."""
    d = Ares21Decoder({"num_lanes": 8})
    line = f"{'':20}{4:>2}{1:>3} 1:02.34   "
    assert d.feed(_rtd("0040100200", line))["lane_time4"] == "1:02.34"


@pytest.mark.parametrize(
    ("field", "writes", "pair"),
    [
        ("99", [(99, " 12"), (103, " 3")], (12, 3)),
        ("100", [(100, " 12"), (103, " 3")], (12, 3)),
        ("title", [(69, f"{'Event 12 Heat 3':<30}")], (12, 3)),
    ],
)
def test_ares_event_field_option(field, writes, pair):
    d = Ares21Decoder({"num_lanes": 8, "ertd_event_field": field})
    updates: dict = {}
    for offset, text in writes:
        updates.update(d.feed(_rtd(f"00401{offset:05d}", text)))
    assert updates["event_changed"] == pair


# ── OSM6 ───────────────────────────────────────────────────────────────────────


def _osm6(lane: str, time: str = "58.21") -> list[list[int]]:
    pt1 = [0x01, 0x02, 0x08, *b"2A ?? 400302   1", 0x04]
    pt2 = [
        0x01,
        0x02,
        0x08,
        0x0A,
        *f"{lane} 4".encode(),
        0x02,
        *f"{time:>11} ".encode(),
        0x04,
    ]
    return [pt1, pt2]


@pytest.mark.parametrize(
    ("option", "lane_key"), [("10", "lane_time10"), ("ignore", None)]
)
def test_osm6_lane_zero(option, lane_key):
    d = QuantumDecoder({"num_lanes": 10, "lane_zero": option})
    updates: dict = {}
    for p in _osm6("0"):
        updates.update(d.feed(p))
    if lane_key:
        assert updates[lane_key] == "58.21"
    else:
        assert not any(k.startswith("lane_time") for k in updates)


# ── Swiss Timing scoreboard (UNT4 / GALACTICA / OmniSport OMEGA) ───────────────


def _scb(text: str) -> list[int]:
    return [0x01, *text.encode("latin-1"), 0x04]


def _running(t: str) -> list[int]:
    return _scb(f"\x14R02\x02\x081 \x02{t:>12}")


def _net(line: int, a: str, b: str, t: str) -> list[int]:
    return _scb(f"\x14S02\x02\x08{chr(10) * (line - 1)}{a} \x02{b:>2} {t:>9}")


def _clear(line: int, a: str) -> list[int]:
    return _scb(f"\x14S02\x02\x08{chr(10) * (line - 1)}{a} \x02{'':12}")


def test_scb_running_time_starts_the_race():
    d = SwissScoreboardDecoder({"num_lanes": 4})
    assert "dismiss_overlay" not in d.feed(_running("  :  :0.0"))
    updates = d.feed(_running("  :  :0.1"))
    assert updates["dismiss_overlay"] is True
    assert updates["running_time"] == "0.1"
    assert all(updates[f"lane_running{i}"] for i in range(1, 5))
    assert d.feed(_running("00:01:02.3"))["running_time"] == "1:02.3"


def test_scb_lane_mode_line():
    d = SwissScoreboardDecoder({"num_lanes": 8})
    d.feed(_running("1.0"))
    updates = d.feed(_net(1, "3", " 2", "58.21"))
    assert (updates["lane_time3"], updates["lane_place3"]) == ("58.21", "2")
    assert updates["lane_running3"] is False


def test_scb_rank_mode_line_and_lane_zero_is_ten():
    d = SwissScoreboardDecoder({"num_lanes": 10, "scb_line_mode": "rank"})
    d.feed(_running("1.0"))
    updates = d.feed(_net(1, "0", "4", "1:02.34"))
    assert (updates["lane_time4"], updates["lane_place4"]) == ("1:02.34", "10")


def test_scb_an_erased_line_was_a_split():
    d = SwissScoreboardDecoder({"num_lanes": 8, "touchpad_sides": 2})
    d.set_heat_lanes({3})
    d.feed(_running("1.0"))
    d.feed(_net(1, "3", " 1", "28.10"))
    assert d.lane_splits[3] == 1
    updates = d.feed(_clear(1, "3"))
    assert updates["lane_running3"] is True
    assert not d.race_finished()
    d.feed(_net(1, "3", " 1", "58.21"))
    assert d.lane_splits[3] == 2
    assert d.race_finished()


def test_scb_new_race_and_zero_clock_reset():
    d = SwissScoreboardDecoder({"num_lanes": 4})
    d.feed(_running("1.0"))
    d.feed(_net(1, "2", " 1", "58.21"))
    assert d.feed(_scb("\x14S02\x02\x081 \x02       0,0"))["lane_time2"] == ""
    d.feed(_running("1.0"))
    d.feed(_net(1, "2", " 1", "58.21"))
    assert d.feed(_running("0.0"))["lane_time2"] == ""


def test_scb_event_heat_message():
    d = SwissScoreboardDecoder({"num_lanes": 4})
    msg = "\x06\x088" + "\n" * 11 + "X" + "     " + " 12" + "  3"
    assert d.feed(_scb(msg))["event_changed"] == (12, 3)


def test_scb_serial_options():
    cfg = {
        "console_options": {
            "ares21_unt4": {"serial_baud": "19200", "serial_format": "7E1"}
        }
    }
    sc = make_decoder("ares21_unt4", cfg).serial_config
    assert (sc.baud, sc.bytesize, sc.parity) == (19200, 7, "E")
