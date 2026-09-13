"""Typeahead suggestions (`app.md` `S-09`) — built on the client, from the start list.

There used to be a `GET /search_suggestions` on both servers. It never needed to
exist: it read only `lane.name`, `lane.club` and `lane.swimmers[].name`, every one of
which `GET /meet/{id}/schedule` and the Pi's `GET /schedule.json` already carry. The
fetch bought a round-trip per keystroke and a window where the server's start list was
ahead of the client's and could offer a swimmer the client could not then match. Both
routes are gone (`api.md` §7); `S-09` specifies a local index instead.

Two things keep that honest, and they are what this file guards. The payload has to
keep carrying the index's three inputs — trim one and the typeahead quietly stops
finding people, with no error anywhere — and the page has to keep building the index
rather than reaching for a server.
"""
import os
import re
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

# Before the import, as `test_cloud_forward` does: the module derives every path
# from DATA_DIR at import time.
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='splouch-cloud-test-'))
sys.path.insert(0, os.path.join(REPO, 'cloud'))

import cloud_server as cs                       # noqa: E402
import state                                    # noqa: E402
from routes import meet as meet_routes          # noqa: E402

# A relay and an individual, which between them carry every field the index reads.
_START_LIST = {
    3: {1: {4: {'name': 'Sørensen, Åse', 'club': 'CAMO', 'seed_time': '1:02.40',
                'swimmers': []},
            5: {'name': 'Relay A', 'club': '1900 Aquatique', 'seed_time': '',
                'swimmers': [{'pos': 1, 'name': 'Élise Roy', 'first': 'Élise'},
                             {'pos': 2, 'name': "O'Brien, Pat", 'first': 'Pat'}]}}},
}


def _as_str(start_list):
    """The cloud keys its start list by string, the Pi by int — same data."""
    return {str(ev): {str(ht): {str(ln): entry for ln, entry in lanes.items()}
                      for ht, lanes in heats.items()}
            for ev, heats in start_list.items()}


@pytest.fixture
def pi_lanes(monkeypatch):
    monkeypatch.setattr(state, 'meet', state._Meet(
        event_names={3: '200 Backstroke'}, start_list=_START_LIST,
        heat_times={3: {1: '10:42'}}, meet_info={'name': 'Invitation'}))
    return meet_routes.route_schedule_json()['heats'][0]['lanes']


@pytest.fixture
def cloud_lanes():
    sched = {'events': [(3, [1])], 'names': {'3': '200 Backstroke'},
             'times': {'3': {'1': '10:42'}}, 'start_list': _as_str(_START_LIST)}
    return cs._build_heats_json(sched)[0]['lanes']


@pytest.mark.parametrize('lanes', ['pi_lanes', 'cloud_lanes'])
def test_the_payload_carries_everything_the_index_reads(lanes, request):
    """`S-09`'s three inputs, on both servers. Drop one and the typeahead goes quiet.

    There is no request left to fall back on, so a lane that loses its `club` or a
    relay that loses `swimmers[]` is simply unfindable — no error, no empty state,
    just a swimmer who cannot be searched for.
    """
    individual, relay = request.getfixturevalue(lanes)
    assert individual['name'] == 'Sørensen, Åse'
    assert individual['club'] == 'CAMO'
    # The team name is indexed like any other entry: a spectator may know the team
    # and not one swimmer on it.
    assert relay['name'] == 'Relay A'
    assert relay['club'] == '1900 Aquatique'
    # And its members, which is also what `S-14` filters on.
    assert [s['name'] for s in relay['swimmers']] == ['Élise Roy', "O'Brien, Pat"]


def test_neither_server_still_exposes_the_endpoint():
    """Removed, not deprecated (`api.md` §7).

    It was also the one route the two servers wrote out separately instead of
    sharing a helper, and they had drifted: the cloud offered relay team names and
    the Pi did not, so the same query answered differently depending on which server
    an app happened to be pointed at. Re-adding it re-opens that.
    """
    for mod in (meet_routes, cs):
        assert not hasattr(mod, 'route_search_suggestions')
    for name in ('server/routes/meet.py', 'cloud/cloud_server.py'):
        src = open(os.path.join(REPO, name), encoding='utf-8').read()
        assert 'search_suggestions' not in src


def test_the_schedule_page_builds_its_own_suggestions():
    """`S-09`'s 220ms debounce went with the fetch.

    It existed to spare the server; over a local index it would only lag the sheet
    (`app.md` §0.4).
    """
    src = open(os.path.join(REPO, 'shared', 'templates', 'schedule.html'),
               encoding='utf-8').read()
    body = src[src.index('<script>'):]
    assert 'buildSuggestIndex' in body
    assert not re.search(r'fetch\(\s*url', body)
    assert '_searchTimer' not in body
