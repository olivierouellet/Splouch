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


@pytest.fixture(autouse=True)
def _restore_settings():
    """A Display-form post clears every `show_*` box it does not carry — on the
    shared `state.settings`. Put them back, or `show_podium` stays off for every
    test after this file (the Qt podium colour test, in CI's order)."""
    saved = dict(state.settings)
    yield
    state.settings.clear()
    state.settings.update(saved)


def _post(form, monkeypatch):
    emitted = []
    monkeypatch.setattr(
        bus, "emit", lambda channel, event, data=None: emitted.append((channel, event))
    )
    monkeypatch.setattr(state, "save_settings", lambda: None)
    with contextlib.suppress(Exception):
        asyncio.run(route_settings(cast(Request, _FakeRequest(form))))
    return emitted


@pytest.mark.parametrize(
    "submit,audience,other",
    [
        ("display_settings_submit", "board", "phone"),
        ("phones_settings_submit", "phone", "board"),
    ],
)
def test_each_form_stores_its_audience_and_re_announces(
    final_meet, monkeypatch, submit, audience, other
):
    """A board keeps the composed name until told; a Schedule tab caches the list.

    Each audience has its own form now, so a save from one must leave the other's
    rounds alone rather than reading its absent checkboxes as all unticked.
    """
    monkeypatch.setitem(state.settings, f"round_names_{other}", ["final"])
    emitted = _post(
        {
            submit: "1",
            f"round_{audience}_semifinal": "1",
            f"round_{audience}_final": "1",
            f"round_{audience}_bogus": "1",
        },
        monkeypatch,
    )
    assert state.settings[f"round_names_{audience}"] == ["semifinal", "final"]
    assert state.settings[f"round_names_{other}"] == ["final"]
    assert ("/scoreboard", "update_scoreboard") in emitted
    assert ("/schedule", "schedule_update") in emitted


def test_saving_unchanged_rounds_announces_nothing(final_meet, monkeypatch):
    form = {"display_settings_submit": "1", "phones_settings_submit": "1"}
    for audience in ("board", "phone"):
        form.update({f"round_{audience}_{k}": "1" for k in ROUND_NAMES})
    emitted = _post(form, monkeypatch)
    assert ("/schedule", "schedule_update") not in emitted


@pytest.mark.parametrize(
    "form_id,audience",
    [("display-settings-form", "board"), ("phones-settings-form", "phone")],
)
def test_each_audience_is_in_its_own_auto_saving_form(form_id, audience):
    """The board's rounds under Scoreboard → Display, the phones' under Phones."""
    from conftest import settings_source

    form = settings_source().split(f'id="{form_id}"')[1].split("</form>")[0]
    assert f"round_checks('{audience}'" in form
    other = "phone" if audience == "board" else "board"
    assert f"round_checks('{other}'" not in form


def test_the_lane_order_reaches_the_phones(monkeypatch):
    """docs/app.md `L-26`: the pool's starting lane order, on both config paths."""
    import relay
    import web

    monkeypatch.setitem(state.settings, "lane_direction", "up")
    _post({"phones_settings_submit": "1", "lane_direction": "down"}, monkeypatch)
    assert state.settings["lane_direction"] == "down"
    assert web._globals()["lane_direction"] == "down"
    assert relay._get_metadata()["settings"]["lane_direction"] == "down"
    _post({"phones_settings_submit": "1", "lane_direction": "sideways"}, monkeypatch)
    assert state.settings["lane_direction"] == "down"


def test_the_lane_order_is_in_the_phones_form():
    from conftest import settings_source

    form = settings_source().split('id="phones-settings-form"')[1].split("</form>")[0]
    assert 'name="lane_direction"' in form


def test_the_phones_group_holds_display_and_cloud():
    """Phones are not the pool's scoreboard: their card sits under its own group,
    beside Cloud, not under Scoreboard."""
    from conftest import settings_source

    src = settings_source()
    group = src.split('id="grp-phones"')[1].split("</div>")[0]
    assert 'data-target="#tab-phones"' in group
    assert 'data-target="#tab-cloud"' in group
    scoreboard = src.split('id="grp-appearance"')[1].split("</div>")[0]
    assert "#tab-phones" not in scoreboard
