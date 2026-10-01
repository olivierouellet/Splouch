"""Which Lenex rounds follow the event name — one choice for boards, one for phones.

Boards render `event_name` as sent; phones compose from `event_name_parts`
(docs/app.md `T-11`). So the board choice decides the string and the phone choice
decides `parts.round`, and one frame serves both.
"""

import asyncio
import contextlib
from typing import cast

import pytest
from fastapi import Request

import bus
import state
from meet_data import build_heats, get_event_name_display, get_event_name_parts
from meet_parsers.lenex_parser import ROUND_NAMES
from routes.settings import route_settings
from test_settings_display_form import _FakeRequest


@pytest.fixture
def final_meet(monkeypatch):
    monkeypatch.setattr(
        state,
        "meet",
        state._Meet(
            event_names={3: "100 Free Women"},
            start_list={3: {1: {}}},
            event_rounds={3: "final"},
        ),
    )
    monkeypatch.setitem(state.settings, "locale", "en")
    monkeypatch.setitem(state.settings, "round_names_board", list(ROUND_NAMES))
    monkeypatch.setitem(state.settings, "round_names_phone", list(ROUND_NAMES))


def test_board_off_keeps_the_round_for_phones(final_meet):
    state.settings["round_names_board"] = ["semifinal"]
    assert not get_event_name_display(3).endswith("Final")
    assert get_event_name_parts(3)["round"] == "final"
    heat = build_heats()[0]
    assert not heat["event_name"].endswith("Final")
    assert heat["event_name_parts"]["round"] == "final"


def test_phone_off_keeps_the_round_on_the_board(final_meet):
    state.settings["round_names_phone"] = []
    assert get_event_name_display(3).endswith("Final")
    assert get_event_name_parts(3)["round"] == ""
    assert build_heats()[0]["event_name_parts"]["round"] == ""


def test_a_missing_setting_shows_every_round(final_meet, monkeypatch):
    """An install from before the setting existed behaves as it did."""
    monkeypatch.delitem(state.settings, "round_names_board")
    monkeypatch.delitem(state.settings, "round_names_phone")
    assert get_event_name_display(3).endswith("Final")
    assert get_event_name_parts(3)["round"] == "final"


def _post(form, monkeypatch):
    emitted = []
    monkeypatch.setattr(
        bus, "emit", lambda channel, event, data=None: emitted.append((channel, event))
    )
    monkeypatch.setattr(state, "save_settings", lambda: None)
    with contextlib.suppress(Exception):
        asyncio.run(route_settings(cast(Request, _FakeRequest(form))))
    return emitted


def test_saving_stores_each_audience_and_re_announces(final_meet, monkeypatch):
    """A board keeps the composed name until told; a Schedule tab caches the list."""
    emitted = _post(
        {
            "display_settings_submit": "1",
            "round_board_final": "1",
            "round_board_bogus": "1",
            "round_phone_semifinal": "1",
            "round_phone_final": "1",
        },
        monkeypatch,
    )
    assert state.settings["round_names_board"] == ["final"]
    assert state.settings["round_names_phone"] == ["semifinal", "final"]
    assert ("/scoreboard", "update_scoreboard") in emitted
    assert ("/schedule", "schedule_update") in emitted


def test_saving_unchanged_rounds_announces_nothing(final_meet, monkeypatch):
    form = {"display_settings_submit": "1"}
    for audience in ("board", "phone"):
        form.update({f"round_{audience}_{k}": "1" for k in ROUND_NAMES})
    emitted = _post(form, monkeypatch)
    assert ("/schedule", "schedule_update") not in emitted


def test_both_sections_are_in_the_auto_saving_form():
    from conftest import settings_source

    form = settings_source().split('id="display-settings-form"')[1].split("</form>")[0]
    assert "round_checks('board'" in form
    assert "round_checks('phone'" in form
