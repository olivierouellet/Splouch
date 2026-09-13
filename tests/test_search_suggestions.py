"""Typeahead suggestions — now built on the client, and the servers agree meanwhile.

`app.md` `S-09` used to be the one filter the phone could not answer alone: every
other part of the filter sheet runs against the start list the client already holds,
but the suggestion list round-tripped to `GET /search_suggestions`. It never needed
to. `_build_heats_json()` hands every lane its `name`, `club` and `swimmers[]`, which
is the whole of what the endpoint read, so the fetch bought nothing but latency and a
window where the server could offer a swimmer the client's list did not have yet.

The endpoint outlived its purpose the moment the Pi grew `GET /schedule.json`. It
still serves, because `Splouch-ios` and `Splouch-android` are still calling it, and
until they stop the two implementations have to agree — they had already drifted,
which is the second thing here.
"""
import os
import re
import sys
import tempfile

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

# Built to hit every case the two implementations disagreed on: a relay (team name
# *and* members), a name whose accents the fold cannot transliterate, one swimmer
# entered twice under different clubs, and a numeric-looking club.
_START_LIST = {
    3: {1: {4: {'name': 'Sørensen, Åse', 'club': 'CAMO', 'seed_time': '1:02.40',
                'swimmers': []},
            5: {'name': 'Relay A', 'club': '1900 Aquatique', 'seed_time': '',
                'swimmers': [{'pos': 1, 'name': 'Élise Roy', 'first': 'Élise'},
                             {'pos': 2, 'name': "O'Brien, Pat", 'first': 'Pat'}]},
            6: {'name': 'Tremblay, Luc', 'club': 'CAMO', 'seed_time': '58.10',
                'swimmers': []}},
        2: {1: {'name': 'Tremblay, Luc', 'club': 'NATATION SUD', 'seed_time': '57.90',
                'swimmers': []}}},
}


class _Req:
    def __init__(self, **kw):
        self.query_params = kw


def _as_str(start_list):
    """The cloud keys its start list by string, the Pi by int — same data."""
    return {str(ev): {str(ht): {str(ln): entry for ln, entry in lanes.items()}
                      for ht, lanes in heats.items()}
            for ev, heats in start_list.items()}


def _pi(monkeypatch, q):
    monkeypatch.setattr(state, 'meet', state._Meet(start_list=_START_LIST))
    return meet_routes.route_search_suggestions(_Req(q=q))


def _cloud(monkeypatch, q):
    meet = {'schedule_data': {'start_list': _as_str(_START_LIST)}}
    monkeypatch.setattr(cs, '_get_meet', lambda meet_id: meet if meet_id == 'M1' else None)
    return cs.route_search_suggestions(_Req(q=q, meet_id='M1'))


def test_pi_and_cloud_answer_identically(monkeypatch):
    """The drift this file exists for: the Pi skipped relay *team* names.

    It guarded the lane name with `not entry.get('swimmers')`, so a relay's team
    name was indexed on the cloud and invisible on a Pi — the same swimmer typing
    the same query got different suggestions depending on which server the app
    happened to be pointed at, against `app.md` §0.2.
    """
    for q in ('tre', 'srensen', 'elise', 'relay', 'camo', '1900', 'roy', "o'brien", 'z'):
        assert _pi(monkeypatch, q) == _cloud(monkeypatch, q), q


def test_a_relay_is_findable_by_its_team_name(monkeypatch):
    """A spectator may know the team and not one member on it."""
    assert _pi(monkeypatch, 'relay a') == [
        {'type': 'swimmer', 'name': 'Relay A', 'club': '1900 Aquatique'}]


def test_a_relay_is_findable_by_its_members(monkeypatch):
    """`S-14`: the members are indexed under the lane's club, not the team name."""
    assert _pi(monkeypatch, 'elise') == [
        {'type': 'swimmer', 'name': 'Élise Roy', 'club': '1900 Aquatique'}]


def test_clubs_come_after_swimmers(monkeypatch):
    """Both lists are sorted, and swimmers lead — the common search."""
    assert [(r['type'], r['name']) for r in _pi(monkeypatch, 'a')] == [
        ('swimmer', "O'Brien, Pat"),
        ('swimmer', 'Relay A'),
        ('swimmer', 'Sørensen, Åse'),
        ('swimmer', 'Tremblay, Luc'),
        ('club', '1900 Aquatique'),
        ('club', 'CAMO'),
        ('club', 'NATATION SUD'),
    ]


def test_the_fold_strips_what_it_cannot_decompose(monkeypatch):
    """`ø` has no NFD decomposition, so it is dropped rather than folded to `o`.

    Lossy, and deliberately kept: the client's `foldName()` reproduces it exactly,
    so a query that finds a swimmer on the server finds them on the phone.
    """
    assert _pi(monkeypatch, 'srensen')
    assert _pi(monkeypatch, 'sorensen') == []


def test_an_empty_query_returns_nothing(monkeypatch):
    for q in ('', '   '):
        assert _pi(monkeypatch, q) == []
        assert _cloud(monkeypatch, q) == []


def test_the_schedule_page_builds_its_own_suggestions():
    """The web page holds the same start list, so it must not fetch them.

    `S-09`'s 220ms debounce went with the fetch: it existed to spare the server, and
    over a local index it would only lag the sheet (`app.md` §0.4).
    """
    src = open(os.path.join(REPO, 'shared', 'templates', 'schedule.html'),
               encoding='utf-8').read()
    body = src[src.index('<script>'):]
    assert 'search_suggestions' not in body.replace('/search_suggestions endpoint', '')
    assert not re.search(r'fetch\(\s*url', body)
    assert 'buildSuggestIndex' in body
