"""Every phone page's load path actually executes (see `tests/jsc.py`).

One test per page per server. They render the real template, run its scripts in
document order under JavaScriptCore against a stub DOM, and fail if anything is
thrown before the page is idle.

This is the test the Schedule tab needed and did not have: `ws.js` was loaded after
the block that called `eventNameOf()`, so the page threw on load and rendered an empty
list, while every substring assertion in `test_scoreboard_base_shared.py` still passed.
"""
import json
import os
import sys

import pytest
from jinja2 import Environment, FileSystemLoader

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import state          # noqa: E402
from jsc import HAS_JSC, run_page   # noqa: E402

pytestmark = pytest.mark.skipif(
    not HAS_JSC, reason='needs JavaScriptCore via osascript (macOS)')

_LABELS = {'event': 'Event', 'heat': 'Heat', 'lane': 'Lane', 'name': 'Name',
           'club': 'Club', 'time': 'Time', 'delta': 'Δ', 'place': '#',
           'waiting_results': 'Waiting…', 'no_schedule': 'No schedule'}
_FLAGS = {f'show_{k}': True for k in
          ('lane_header', 'name_header', 'club_header', 'time_header',
           'delta_header', 'position_header', 'name', 'club', 'delta', 'position')}
_TABS = {'scoreboard': 'Scoreboard', 'results': 'Results', 'schedule': 'Schedule',
         'back_to_meets': 'Meets'}
_SCHED_T = {'schedule': 'Horaire', 'search_placeholder': 'Ajouter…',
            'upcoming_only': 'À venir', 'show_all_heats': 'Toutes',
            'reset_filters': 'Réinitialiser', 'reset_confirm': 'Effacer ?',
            'no_meet': 'Aucune rencontre', 'swimmer': 'Nageur', 'club': 'Club'}

# Real-looking data, accents included: the index folds every name at build time, so a
# page that renders with ASCII only would not exercise it.
# The routes hand the template the list itself and let Jinja's `tojson` encode it
# (that filter escapes `<`, so a name cannot close the <script> it sits in).
_HEATS = json.loads(
    '[{"event":3,"heat":1,"event_name":"50 Libre",'
    '"event_name_parts":{"raw":"50 Free","dist":"50","stroke":"freestyle",'
    '"relay":false,"gender":"","age":"","age_key":""},"time":"10:42",'
    '"lanes":[{"lane":4,"name":"Sørensen, Åse","club":"Île-des-Sœurs",'
    '"seed_time":"0:27.10","swimmers":[]},'
    '{"lane":5,"name":"Relais A","club":"CAMO","seed_time":"",'
    '"swimmers":[{"pos":1,"name":"Élise Roy","first":"Élise"}]}]}]')
_VOCAB = {'unit': 'm', 'freestyle': 'Libre', 'separator': ' — '}
_MANUAL_T = {'title': 'Console manuelle', 'prev': 'Précédente', 'next': 'Suivante',
             'commit': 'Afficher cette série', 'locate': 'Aller à la série en cours',
             'hold': 'Maintenir pour changer', 'no_meet': 'Aucun fichier',
             'not_active': 'Une console est sélectionnée.',
             'open_settings': 'Réglages', 'reconnecting': 'Reconnexion…'}


def _render(own_dir, template, **extra):
    env = Environment(loader=FileSystemLoader(
        [os.path.join(REPO, own_dir), os.path.join(REPO, 'shared', 'templates')]))
    env.globals['url_for'] = lambda name, **kw: '/static/' + kw.get('filename', '')
    return env.get_template(template).render(
        num_lanes=6, labels=_LABELS, event_vocab=_VOCAB,
        theme_colors=state.DEFAULT_THEME_COLORS,
        theme_fonts=state.DEFAULT_THEME_FONTS,
        **_FLAGS, **extra)


# (id, own_dir, template, extra) — every page both servers put on a phone.
PAGES = [
    ('scoreboard-pi',    'server/templates', 'live-mobile.html', {}),
    ('scoreboard-cloud', 'cloud/templates',  'live-mobile.html', {'meet_id': 'abc123'}),
    ('results-pi',       'server/templates', 'results.html',  {'t': _LABELS}),
    ('results-cloud',    'cloud/templates',  'results.html',  {'t': _LABELS, 'meet_id': 'abc123'}),
    ('schedule-pi',      'server/templates', 'schedule.html',
     {'heats': _HEATS, 'has_meet': True, 'meet_name': 'Coupe', 't': _SCHED_T}),
    ('schedule-cloud',   'cloud/templates',  'schedule.html',
     {'heats': _HEATS, 'has_meet': True, 'meet_name': 'Coupe', 't': _SCHED_T,
      'meet_id': 'abc123'}),
    ('manual-pi',        'server/templates', 'manual.html',
     {'heats': _HEATS, 'has_meet': True, 'meet_name': 'Coupe', 't': _MANUAL_T,
      'current_event': '3', 'current_heat': '1', 'manual_active': True,
      'console_label': 'Manual — no timing console'}),
    ('shell-pi',         'server/templates', 'mobile.html', {'app_title': 'Coupe', 't': _TABS}),
    ('shell-cloud',      'cloud/templates',  'mobile.html',
     {'app_title': 'Coupe', 't': _TABS, 'meet_id': 'abc123'}),
    # The shell a meet with no timing console gets: two tabs, not three (app.md
    # `A-11`). Its tab bar is built from what was rendered, so it is a different
    # path through the same script.
    ('shell-untimed',    'cloud/templates',  'mobile.html',
     {'app_title': 'Coupe', 't': _TABS, 'meet_id': 'abc123', 'show_results': False}),
]


@pytest.mark.parametrize('page_id,own_dir,template,extra',
                         PAGES, ids=[p[0] for p in PAGES])
def test_the_page_runs_without_throwing(page_id, own_dir, template, extra):
    run_page(_render(own_dir, template, **extra))


def test_the_schedule_page_with_no_meet_also_runs():
    """The `S-07` empty state is a different branch of the template."""
    run_page(_render('server/templates', 'schedule.html',
                     heats=[], has_meet=False, meet_name='', t=_SCHED_T))


def test_the_manual_page_with_no_meet_also_runs():
    """No meet loaded means no stepper and no list — a different branch, and the one
    an operator hits first, before they have uploaded anything."""
    run_page(_render('server/templates', 'manual.html',
                     heats=[], has_meet=False, meet_name='', t=_MANUAL_T,
                     current_event='', current_heat='', manual_active=True,
                     console_label=''))


def test_the_manual_page_warns_when_a_real_console_is_configured():
    """The banner branch: the page still works, but a console will overwrite it."""
    html = _render('server/templates', 'manual.html',
                   heats=_HEATS, has_meet=True, meet_name='Coupe', t=_MANUAL_T,
                   current_event='3', current_heat='1', manual_active=False,
                   console_label='System 6 (Colorado Timing System)')
    run_page(html)
    assert 'System 6 (Colorado Timing System)' in html
    assert _MANUAL_T['not_active'] in html


def test_the_harness_notices_a_page_that_throws():
    """Otherwise a stub gap could make every page above pass by accident."""
    from jsc import PageScriptError
    broken = '<script>var x = 1;</script><script>nope.missing();</script>'
    with pytest.raises(PageScriptError) as e:
        run_page(broken)
    assert 'inline #1' in str(e.value)


def test_a_helper_defined_in_a_later_script_is_not_visible_earlier():
    """The harness's whole point, and the easiest thing to get wrong in it.

    A browser compiles every `<script>` separately, so a page cannot call into one it
    has not loaded yet. Run the scripts as one concatenated block instead and function
    declarations hoist backwards over the ones before them — which passes precisely the
    bug this file exists to catch. `run_page` uses a separate indirect `eval` per
    script to keep the two apart; this is what would notice if that changed.
    """
    from jsc import PageScriptError
    page = '<script>later();</script><script>function later() {}</script>'
    with pytest.raises(PageScriptError, match="Can't find variable: later"):
        run_page(page)


def test_the_harness_notices_a_missing_static_file():
    from jsc import PageScriptError
    with pytest.raises(PageScriptError, match='not in shared/static'):
        run_page('<script src="/static/js/no_such_file.js"></script>')
