"""`GET /schedule.json` — the Pi's start list as data.

app.md §0.2 promises that nothing a phone needs is HTML-only, and the cloud keeps
that promise with `GET /meet/{id}/schedule`. The Pi rendered its start list into
`/schedule` and nowhere else, so an app pointed at a Pi (`P-11`) had no Schedule
tab. This endpoint is the twin, and it is built by the same function as the page so
the two cannot drift.
"""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import state                                   # noqa: E402
from routes import meet as meet_routes         # noqa: E402

_START_LIST = {
    3: {1: {4: {'name': 'A. Swimmer', 'club': 'CLUB', 'seed_time': '1:02.40',
                'swimmers': []},
            5: {'name': 'Relay A', 'club': 'CLUB', 'seed_time': '',
                'swimmers': [{'pos': 1, 'name': 'B. One', 'first': 'B'},
                             {'pos': 2, 'name': 'C. Two', 'first': 'C'}]}},
        2: {}},
}


@pytest.fixture
def loaded_meet(monkeypatch):
    monkeypatch.setattr(state, 'meet', state._Meet(
        event_names={3: '200 Backstroke Girls 12 & Under'},
        start_list=_START_LIST,
        heat_times={3: {1: '10:42'}},
        meet_info={'name': 'Invitation'}))


def test_empty_heats_when_no_meet_is_loaded(monkeypatch):
    """Not an error: the client shows `S-07` and waits for `schedule_update`."""
    monkeypatch.setattr(state, 'meet', state._Meet())
    assert meet_routes.route_schedule_json() == {'heats': []}


def test_every_heat_in_running_order_with_its_lanes(loaded_meet):
    heats = meet_routes.route_schedule_json()['heats']
    assert [(h['event'], h['heat']) for h in heats] == [(3, 1), (3, 2)]
    first = heats[0]
    # Composed in the meet's locale by the server, as `update_scoreboard.event_name` is.
    assert 'Backstroke' in first['event_name']
    assert first['time'] == '10:42'
    assert [l['lane'] for l in first['lanes']] == [4, 5]
    assert first['lanes'][1]['swimmers'][0]['first'] == 'B'
    # A heat with no entries still appears, with an empty `lanes` (api.md §5.8).
    assert heats[1]['lanes'] == []


def test_carries_event_name_parts_like_the_cloud(loaded_meet):
    """`T-11` composes the name in the reader's language from these; the cloud's
    `GET /meet/{id}/schedule` has them, so the Pi's must too."""
    parts = meet_routes.route_schedule_json()['heats'][0]['event_name_parts']
    assert parts['dist'] == '200'
    assert parts['stroke'] == 'backstroke'


def test_json_and_page_are_the_same_list(loaded_meet, monkeypatch):
    """One builder for both, so an app and a browser see the same start list."""
    captured = {}

    def fake_render(request, template, **ctx):
        captured.update(ctx)
        return None
    monkeypatch.setattr(meet_routes, 'render', fake_render)
    monkeypatch.setattr(meet_routes, 'client_strings', lambda request: {})
    meet_routes.route_schedule(request=None)
    assert json.loads(captured['heats_json']) == \
        json.loads(json.dumps(meet_routes.route_schedule_json()['heats']))
    assert captured['has_meet'] is True
