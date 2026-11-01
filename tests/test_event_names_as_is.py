"""A meet whose event names are not swimming's shows them as the file has them.

Artistic swimming names (`Duet Free Routine`, `Team Free 12 & Under`) parse into
a stroke and an age and lose the rest. Settings → Meet turns the parse off for one
meet: every part but `raw` and `round` is empty, so the board and every composer
(docs/app.md `T-11`) fall back to `raw`.
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
from splouch_i18n import compose_event_name
from test_settings_display_form import _FakeRequest

UID = "abc123def456"


@pytest.fixture(autouse=True)
def _restore_settings():
    saved = dict(state.settings)
    yield
    state.settings.clear()
    state.settings.update(saved)


@pytest.fixture
def duet_meet(monkeypatch):
    monkeypatch.setattr(
        state,
        "meet",
        state._Meet(
            event_names={3: "Duet Free Routine 12 & Under"},
            start_list={3: {1: {}}},
            event_rounds={3: "final"},
        ),
    )
    monkeypatch.setattr(state, "_active_meet_uid", UID)
    monkeypatch.setitem(state.settings, "locale", "en")
    monkeypatch.setitem(state.settings, "round_names_board", list(ROUND_NAMES))
    monkeypatch.setitem(state.settings, "round_names_phone", list(ROUND_NAMES))
    monkeypatch.setitem(state.settings, "event_names_as_is", [])


def test_off_parses_as_swimming(duet_meet):
    assert get_event_name_display(3).startswith("Freestyle")


def test_on_shows_the_name_as_written_with_its_round(duet_meet):
    state.settings["event_names_as_is"] = [UID]
    assert get_event_name_display(3) == "Duet Free Routine 12 & Under  —  Final"
    parts = get_event_name_parts(3)
    assert parts["raw"] == "Duet Free Routine 12 & Under"
    assert parts["round"] == "final"
    assert not any(parts[k] for k in ("dist", "stroke", "gender", "age", "age_key"))
    assert not parts["relay"]
    heat = build_heats()[0]
    assert heat["event_name"] == "Duet Free Routine 12 & Under  —  Final"
    assert heat["event_name_parts"] == parts


def test_a_phone_composes_the_raw_name_in_any_language(duet_meet):
    state.settings["event_names_as_is"] = [UID]
    vocab = state.i18n_bundle("fr")["event_name"]
    assert compose_event_name(get_event_name_parts(3), vocab) == (
        "Duet Free Routine 12 & Under  —  Finale"
    )


def test_another_meet_is_unaffected(duet_meet):
    state.settings["event_names_as_is"] = ["someothermeet"]
    assert get_event_name_display(3).startswith("Freestyle")


def _post(form, monkeypatch):
    emitted = []
    monkeypatch.setattr(
        bus, "emit", lambda channel, event, data=None: emitted.append((channel, event))
    )
    monkeypatch.setattr(state, "save_settings", lambda: None)
    with contextlib.suppress(Exception):
        asyncio.run(route_settings(cast(Request, _FakeRequest(form))))
    return emitted


def test_the_meet_tab_stores_it_per_meet_and_re_announces(duet_meet, monkeypatch):
    state.settings["event_names_as_is"] = ["someothermeet"]
    emitted = _post({"names_as_is_submit": "1", "names_as_is": "1"}, monkeypatch)
    assert state.settings["event_names_as_is"] == ["someothermeet", UID]
    assert ("/scoreboard", "update_scoreboard") in emitted
    assert ("/schedule", "schedule_update") in emitted

    _post({"names_as_is_submit": "1"}, monkeypatch)
    assert state.settings["event_names_as_is"] == ["someothermeet"]


def test_no_meet_loaded_stores_nothing(duet_meet, monkeypatch):
    monkeypatch.setattr(state, "_active_meet_uid", "")
    emitted = _post({"names_as_is_submit": "1", "names_as_is": "1"}, monkeypatch)
    assert state.settings["event_names_as_is"] == []
    assert ("/schedule", "schedule_update") not in emitted
