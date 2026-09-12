"""`shared/templates/` — one phone board, served by both servers.

`live-mobile.html` is a single file now: the Pi and the cloud render the same
template, differing only in whether `MEET_ID` is set. `results.html` (cloud) also
extends the same base, so a base change reaches three pages at once.

What the tests are for. The page reached its current shape by deleting things —
the carousel, the test banner, the operator column collapse, the timed revert on
implied results, the idle meet title, the synthesized row gradient — because this
view is for operators verifying a pool setup and for spectators falling back from
the native apps. Neither wants chrome, and both want the screen to match the last
frame received. Re-adding any of it, or letting the two servers drift apart again,
is what these guard against.
"""
import os
import re
import sys

import pytest
from jinja2 import Environment, FileSystemLoader

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import state   # noqa: E402

_LABELS = {'event': 'Event', 'heat': 'Heat', 'lane': 'Lane', 'name': 'Name',
           'club': 'Club', 'time': 'Time', 'delta': 'Δ', 'place': '#',
           'waiting_results': 'Waiting for results…', 'no_schedule': 'No schedule'}
_FLAGS = {f'show_{k}': True for k in
          ('lane_header', 'name_header', 'club_header', 'time_header',
           'delta_header', 'position_header', 'name', 'club', 'delta', 'position')}


def _render(own_dir, template, **extra):
    env = Environment(loader=FileSystemLoader(
        [os.path.join(REPO, own_dir), os.path.join(REPO, 'shared', 'templates')]))
    env.globals['url_for'] = lambda name, **kw: '/static/' + kw.get('filename', '')
    return env.get_template(template).render(
        num_lanes=6, labels=_LABELS,
        theme_colors=state.DEFAULT_THEME_COLORS,
        theme_fonts=state.DEFAULT_THEME_FONTS,
        **_FLAGS, **extra)


@pytest.fixture(scope='module')
def pi():
    """As the Pi serves it: one meet, so no room to join."""
    return _render('server/templates', 'live-mobile.html')


@pytest.fixture(scope='module')
def cloud():
    """As the cloud serves it: many meets, joined by id."""
    return _render('cloud/templates', 'live-mobile.html', meet_id='abc123')


_TABS = {'scoreboard': 'Scoreboard', 'results': 'Results', 'schedule': 'Schedule',
         'back_to_meets': 'Meets'}


@pytest.fixture(scope='module')
def shell_pi():
    return _render('server/templates', 'mobile.html', app_title='Coupe', t=_TABS)


@pytest.fixture(scope='module')
def shell_cloud():
    return _render('cloud/templates', 'mobile.html',
                   app_title='Coupe', t=_TABS, meet_id='abc123')


def _body(html):
    """Markup only — CSS mentions classes for elements that may not be rendered."""
    return re.sub(r'<style>.*?</style>', '', html, flags=re.S)


# ── One template, two servers ──────────────────────────────────────────────────

@pytest.mark.parametrize('name', ['live-mobile.html', 'mobile.html',
                                  'schedule.html', 'results.html'])
def test_pages_live_only_in_shared(name):
    """A copy reappearing under server/ or cloud/ would win over the shared one —
    the loaders are own-first — and drift silently."""
    assert os.path.isfile(os.path.join(REPO, 'shared/templates', name))
    for d in ('server/templates', 'cloud/templates'):
        assert not os.path.exists(os.path.join(REPO, d, name)), \
            f'{d}/{name} shadows the shared template'


def test_the_only_difference_is_the_room_join(pi, cloud):
    """Same file, so the rendered pages may differ only where MEET_ID does."""
    assert "emit('join_meet'" in pi and "emit('join_meet'" in cloud   # the call is in both
    assert 'var MEET_ID   = ""' in pi                                 # …guarded off here
    assert 'var MEET_ID   = "abc123"' in cloud
    assert 'if (MEET_ID) socket.emit' in pi


@pytest.mark.parametrize('probe', [
    'id="lane_num1"',        # the pulse target
    'id="row1"',
    'id="current_event"', 'id="current_heat"', 'id="event_name"',
    'id="meet_datetime"',
    'id="lane_name1"', 'id="lane_name_alt1"', 'id="lane_club1"',
    'id="lane_time1"', 'id="lane_delta1"', 'id="lane_place1"',
])
def test_all_three_pages_share_the_skeleton(pi, cloud, res_cloud, probe):
    for html in (pi, cloud, res_cloud):
        assert probe in html


def test_shared_symbols_are_defined_once(pi, cloud, res_cloud):
    """The merge loop and the state machine are the highest-risk shared code — the
    two surfaces had drifted here before (the cloud could not retrigger the
    time-locked animation). One definition each, on every page."""
    for html in (pi, cloud, res_cloud):
        for symbol in ('function applyScoreboardFrame(',
                       'function apply_state_transition(',
                       'function reset_state(',
                       'function updateAllLanePulses(',
                       'var VALID_FIELDS',
                       'var meet_live'):
            assert html.count(symbol) == 1, f'{symbol} defined {html.count(symbol)}x'


def test_lock_animation_retriggers(pi, cloud):
    """Re-adding a class that is still set does not restart its animation, so both
    classes come off before the reflow. Without this a lane that finishes twice
    shows the lock effect only once."""
    for html in (pi, cloud):
        i = html.index("} else if (was) {")
        block = html[i:html.index("add('time-locked')", i)]
        assert "remove('time-locked')" in block, 'stale class is never cleared'
        assert block.index("remove('time-locked')") < block.index('void tel.offsetWidth')


def test_intro_clears_the_previous_heat(pi, cloud):
    """The server refreshes names/clubs on an event change but not times, deltas or
    places. Without this the last heat's numbers sit under the new swimmers."""
    for html in (pi, cloud):
        intro = html[html.index('function mode_to_intro()'):html.index('function mode_to_running()')]
        for field in ('lane_time', 'lane_delta', 'lane_place'):
            assert f"'{field}'" in intro or f"'{field}'+" in intro or f"'{field}' +" in intro


# ── Deliberately absent ────────────────────────────────────────────────────────

@pytest.mark.parametrize('removed, why', [
    ('carousel-overlay',    'operator image overlay — Pi-local, never relayed'),
    ('test-overlay',        'test-session banner'),
    ('collapse_cols',       'operator column collapse'),
    ('expand_cols',         'duplicated the CSS column widths'),
    ('build_scoreboard_bg', 'synthesized row-stripe gradient'),
    ('set_header_mode',     'idle meet title'),
    ('results_implicit',    'timed revert after implied results'),
])
def test_removed_chrome_stays_removed(pi, cloud, removed, why):
    """Each of these was cut on purpose: this board shows the last frame received
    and nothing else. Re-adding one means re-opening that decision, not patching a
    regression."""
    for html in (pi, cloud):
        assert removed not in html, f'{removed} is back ({why})'


def test_columns_never_animate(pi, cloud, res_cloud):
    """Nothing on a phone collapses them and an orientation flip must not slide
    them. `timing_display.css` is shared with the kiosk, which does animate."""
    for html in (pi, cloud, res_cloud):
        style = re.search(r'<style>(.*?)</style>', html, re.S).group(1)
        rules = re.findall(r'\.delta-column\s*{\s*transition:\s*([^;]+)', style)
        assert rules and rules[-1].strip() == 'none'
        assert 'timing-anim' not in html


def test_results_page_reacts_to_meet_live_its_own_way(res_cloud):
    """It shares `meet_live` with the live board but not the response: no lane runs
    on a results screen, so it shows the waiting message instead of pulsing."""
    assert 'function bindLanePulse' in res_cloud   # defined by the base…
    assert 'bindLanePulse(' not in res_cloud.split('function bindLanePulse')[1]
    assert "sock.on('meet_live'" in res_cloud       # …but wired by hand here
    assert 'showWaiting()' in res_cloud


# ── The phone shell ────────────────────────────────────────────────────────────

def test_shell_tabs_point_at_each_server_own_routes(shell_pi, shell_cloud):
    """`meet_id` is the whole difference: the cloud routes by room, the Pi serves
    one meet from local paths."""
    for page in ('live', 'results', 'schedule'):
        assert f'src="/mobile/{page}?meet=abc123&' in shell_cloud
    for path in ('/live-mobile', '/results', '/schedule'):
        assert f'src="{path}?' in shell_pi
    assert 'meet=' not in _body(shell_pi)


def test_the_tabs_inherit_the_shell_resolved_language_and_style(shell_pi, shell_cloud):
    """One control, three tabs. The shell has already reconciled the visitor's
    choice with the meet's defaults, so a tab reads its answer off the URL rather
    than re-deriving it (docs/app.md `T-06`, `T-09`)."""
    for shell in (shell_pi, shell_cloud):
        for frame in ('frame0', 'frame1', 'frame2'):
            src = re.search(rf'id="{frame}"[^>]*src="([^"]+)"', shell).group(1)
            assert 'lang=' in src and 'style=' in src, src
    # And the shell asks for itself again when a link arrives without the choice.
    assert 'splouch_lang' in shell_cloud and 'location.replace' in shell_cloud


def test_back_to_meets_only_where_there_are_meets(shell_pi, shell_cloud):
    """One meet on the Pi, so a list to go back to would be a dead end."""
    assert 'class="nav-back"' in _body(shell_cloud)
    assert 'class="nav-back"' not in _body(shell_pi)


def test_manifest_and_icon_follow_the_server(shell_pi, shell_cloud):
    assert '/manifest/abc123' in shell_cloud and '/icon/abc123' in shell_cloud
    assert '/manifest.json' in shell_pi and '/home_icon' in shell_pi
    assert '/manifest/' not in shell_pi


@pytest.mark.parametrize('feature', [
    'sessionStorage',      # the tab you were on survives a reload
    'on_tab_shown',        # the revealed page re-joins / re-lays out
    'bindPtr',             # pull to refresh
    'edgeL',               # swipe between tabs
])
def test_shell_features_are_on_both(shell_pi, shell_cloud, feature):
    """These were cloud-only before the merge; the Pi gains all of them."""
    for html in (shell_pi, shell_cloud):
        assert feature in html


def test_pull_to_refresh_binds_inside_the_iframe(shell_pi, shell_cloud):
    """Not under an overlay in the shell. The old `#edgeT` div covered the top 65px
    of each tab, which meant the gesture only worked on the header and forced
    schedule.html to keep its controls clear of a hard-coded dead zone."""
    for html in (shell_pi, shell_cloud):
        assert 'edgeT' not in html
        assert 'contentDocument' in html
        assert 'atTop(' in html                    # only fires at scrollTop 0
        assert "addEventListener('load'" in html   # re-bound when a tab reloads


def test_a2hs_hint_is_gone_from_the_shell(shell_pi, shell_cloud):
    """The picker steers people to the native apps; teaching them to install the
    web page works against that, and operators do not need it at all."""
    for html in (shell_pi, shell_cloud):
        assert 'a2hs' not in html.lower()


def test_shell_and_pages_agree_on_the_header_height(shell_pi, pi):
    """The shell reserves 65px for the tab bar and the pages draw a 65px header;
    if they disagree the tabs jump as you swipe."""
    assert '65px' in shell_pi
    assert 'max(65px' in pi or '65px' in pi


# ── The schedule tab ───────────────────────────────────────────────────────────

_SCHED_T = {'schedule': 'Horaire', 'search_placeholder': 'Add…',
            'upcoming_only': 'Upcoming', 'show_all_heats': 'All heats',
            'reset_filters': 'Reset', 'reset_confirm': 'Clear?', 'no_meet': 'No meet'}
_HEATS = ('[{"event":3,"heat":1,"event_name":"50 Libre","time":"10:42",'
          '"lanes":[{"lane":4,"name":"T, A","club":"CNL","seed_time":"0:27.10","swimmers":[]}]}]')


def _sched(own_dir, **extra):
    return _render(own_dir, 'schedule.html', heats_json=_HEATS, has_meet=True,
                   meet_name='Coupe', t=_SCHED_T, **extra)


@pytest.fixture(scope='module')
def sched_pi():
    return _sched('server/templates')


@pytest.fixture(scope='module')
def sched_cloud():
    return _sched('cloud/templates', meet_id='abc123')


def test_schedule_lives_only_in_shared():
    assert os.path.isfile(os.path.join(REPO, 'shared/templates/schedule.html'))
    for d in ('server/templates', 'cloud/templates'):
        assert not os.path.exists(os.path.join(REPO, d, 'schedule.html'))


def test_schedule_joins_a_room_only_on_the_cloud(sched_pi, sched_cloud):
    assert "const MEET_ID = ''" in sched_pi
    assert "const MEET_ID = 'abc123'" in sched_cloud
    for html in (sched_pi, sched_cloud):
        assert 'if (MEET_ID) sock.emit' in html
        assert "if (MEET_ID) url += '&meet_id='" in html


def test_both_listen_for_schedule_update(sched_pi, sched_cloud):
    """A loaded meet file invalidates the start list this page is holding. The Pi
    had no path for this at all — its Schedule tab showed the previous meet until
    someone refreshed by hand."""
    for html in (sched_pi, sched_cloud):
        assert "splouchSocket('/ws/schedule')" in html
        assert "sockSchedule.on('schedule_update'" in html


def test_filter_sheet_is_no_longer_boxed_out_of_its_own_header(sched_pi, sched_cloud):
    """The 65px spacer row and the `#filter-title` that filled it existed only to
    dodge the shell's pull-to-refresh overlay, which is gone: the gesture now binds
    inside this document. The sheet gets its full height back."""
    for html in (sched_pi, sched_cloud):
        assert 'edgeT' not in html
        assert 'filter-title' not in html
        assert 'grid-template-rows' not in html


def test_schedule_palette_comes_from_the_server(sched_pi, sched_cloud):
    """The cloud template used to hard-code fallback hexes because its
    `_DEFAULT_COLORS` lacked the schedule_* keys. They live in the palette now, so
    a partial theme still themes the page and the values exist in one place."""
    src = open(os.path.join(REPO, 'cloud', 'cloud_server.py')).read()
    for key in ('schedule_event', 'schedule_time', 'schedule_name', 'schedule_club'):
        assert key in state.DEFAULT_THEME_COLORS, f'{key} missing from the Pi palette'
        assert f"'{key}'" in src,                 f'{key} missing from the cloud palette'
    for html in (sched_pi, sched_cloud):
        assert "theme_colors.get('schedule" not in html


# ── Page language ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('template, extra', [
    ('mobile.html',      {'app_title': 'Coupe', 't': _TABS}),
    ('live-mobile.html', {}),
    ('schedule.html',    {'heats_json': _HEATS, 'has_meet': True,
                          'meet_name': 'Coupe', 't': _SCHED_T}),
])
@pytest.mark.parametrize('code', ['fr', 'es', 'en'])
def test_html_lang_follows_the_meet_locale(template, extra, code):
    """Screen readers and hyphenation key off <html lang>. The content is in the
    meet's language, not the device's, so the routes pass it explicitly."""
    html = _render('server/templates', template, lang=code, **extra)
    assert f'<html lang="{code}">' in html


def test_html_lang_falls_back_when_unset():
    """A caller that forgets it should get a valid document, not lang=""."""
    assert '<html lang="en">' in _render('server/templates', 'live-mobile.html')


def test_every_shared_template_declares_a_language():
    """A missing lang leaves assistive tech guessing from the browser locale."""
    import glob
    for path in glob.glob(os.path.join(REPO, 'shared/templates/*.html')):
        src = open(path).read()
        if '<html' not in src:
            continue                       # a fragment, not a document
        assert '<html lang="{{ lang' in src, f'{os.path.basename(path)} hard-codes or omits lang'


@pytest.mark.parametrize('template', [
    'live.html', 'scoreboard.html', 'next_heats.html', 'full_schedule.html',
])
def test_pi_display_pages_declare_the_scoreboard_language(template):
    """Every page that renders `labels` shows text in the Settings → Display →
    Scoreboard language. Declaring lang="en" while painting French headers is the
    mismatch these guard against; the admin pages are excluded on purpose, since
    they follow the per-device `ui_lang` cookie instead."""
    src = open(os.path.join(REPO, 'server/templates', template)).read()
    assert '<html lang="{{ lang' in src


def test_locale_fallbacks_agree_with_the_settings_default():
    """`load_locale()` used to default to 'fr' while DEFAULT_SETTINGS said 'en', so
    a config with no locale would paint French labels under lang="en"."""
    state_src = open(os.path.join(REPO, 'server/state.py')).read()
    assert "'locale': 'en'," in state_src, 'the settings default moved or changed'
    for path in ('server/state.py', 'server/web.py', 'server/routes/settings.py'):
        src = open(os.path.join(REPO, path)).read()
        assert "get('locale', 'fr')" not in src, f'{path} still falls back to fr'


# ── Admin-panel language ───────────────────────────────────────────────────────

class _Req:
    """Minimal stand-in for the parts of Request that ui_locale() reads."""
    def __init__(self, cookie=None, accept=''):
        self.cookies = {'ui_lang': cookie} if cookie else {}
        self.headers = {'Accept-Language': accept}


@pytest.fixture
def scoreboard_locale(monkeypatch):
    def _set(code):
        monkeypatch.setitem(state.settings, 'locale', code)
    return _set


def test_panel_defaults_to_the_scoreboard_language(scoreboard_locale):
    """Everyone opening Settings sees it in the language the meet is run in."""
    scoreboard_locale('fr')
    assert state.ui_locale(_Req()) == 'fr'


def test_panel_ignores_the_browser_preference(scoreboard_locale):
    """A laptop that happens to prefer Spanish is a worse default than the meet's
    own language — the per-device override exists for that case."""
    scoreboard_locale('fr')
    assert state.ui_locale(_Req(accept='es-ES,es;q=0.9')) == 'fr'


def test_per_device_override_still_wins(scoreboard_locale):
    scoreboard_locale('fr')
    assert state.ui_locale(_Req(cookie='es')) == 'es'


def test_stale_override_falls_back_rather_than_breaking(scoreboard_locale):
    """A cookie naming a locale that has since been removed must not stick."""
    scoreboard_locale('fr')
    assert state.ui_locale(_Req(cookie='zz')) == 'fr'


def test_new_install_lands_on_english(scoreboard_locale):
    scoreboard_locale('en')
    assert state.ui_locale(_Req(accept='fr-CA')) == 'en'


def test_settings_page_declares_the_panel_language_not_the_scoreboard_one():
    """It is the one page whose text comes from ui_locale rather than `labels`."""
    src = open(os.path.join(REPO, 'server/templates/settings.html')).read()
    assert '<html lang="{{ ui_locale' in src


@pytest.mark.parametrize('template', ['meet.html', 'console.html',
                                      'operator.html', 'login.html'])
def test_other_admin_pages_declare_the_scoreboard_language(template):
    src = open(os.path.join(REPO, 'server/templates', template)).read()
    assert '<html lang="{{ lang' in src


def test_login_is_rendered_with_the_globals():
    """A bare TemplateResponse would leave `lang` undefined, silently pinning the
    login page to the fallback whatever the server is set to."""
    src = open(os.path.join(REPO, 'server/app.py')).read()
    assert "templates.TemplateResponse(request, 'login.html'" not in src
    assert src.count("render(request, 'login.html'") == 2


# ── The results tab ────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def res_pi():
    return _render('server/templates', 'results.html', t={'waiting_results': 'Waiting…'},
                   show_podium=True)


@pytest.fixture(scope='module')
def res_cloud():
    return _render('cloud/templates', 'results.html', meet_id='abc123',
                   t={'waiting_results': 'Waiting…'}, show_podium=True)


def test_results_shares_the_scoreboard_skeleton(res_pi, res_cloud, pi):
    """Same base as the live board, so swiping between the two tabs does not shift
    the layout — the reason this page adopted the base's DOM rather than keeping
    its own `res-*` / positional-cell markup."""
    for html in (res_pi, res_cloud):
        for probe in ('id="row1"', 'id="lane_num1"', 'id="lane_name1"',
                      'id="lane_place1"', 'id="current_event"'):
            assert probe in html and probe in pi


def test_results_keeps_podium_and_shrink_to_fit(res_pi, res_cloud):
    """The two things a results board earns that the live board does without: it is
    read carefully and it holds still. Both were Pi-only before the merge."""
    for html in (res_pi, res_cloud):
        assert 'podium-gold' in html and 'podium-silver' in html and 'podium-bronze' in html
        assert '--color-podium-gold' in html
        assert 'fitNameFontSize' in html


def test_podium_respects_the_setting(res_pi, res_cloud):
    """`show_podium` is an operator setting and is relayed (docs/api.md §5.4), so
    both servers must forward it rather than assuming."""
    for html in (res_pi, res_cloud):
        assert 'var SHOW_PODIUM = true' in html
        assert 'if (tr && SHOW_PODIUM)' in html
    src = open(os.path.join(REPO, 'cloud', 'cloud_server.py')).read()
    assert "show_podium=s.get('show_podium', True)," in src


def test_live_board_has_no_podium_tinting(pi, cloud):
    """Places move while a heat is settling; tinting them mid-race would flicker.
    The variables are declared on both (they cost nothing), the logic is not."""
    for html in (pi, cloud):
        assert 'podium-gold' not in html.replace('--color-podium-gold', '')


def test_every_board_shrinks_names_to_fit(pi, cloud, res_pi, res_cloud):
    """Clipping was never a decision — `/live` in the browser was simply the one
    display that had not grown it, while the Qt board shrank via FitLabel all
    along. Row heights are floored by min-height, so this changes type size only."""
    for html in (pi, cloud, res_pi, res_cloud):
        assert 'class="name-primary"' in html
        assert 'function fitNameFontSize()' in html


def test_name_primary_is_styled_once_in_the_stylesheet(pi, res_pi):
    """Shared by four displays including the standalone kiosk page, so it lives in
    timing_display.css rather than being re-declared per template."""
    css = open(os.path.join(REPO, 'shared/static/css/timing_display.css')).read()
    assert '.name-primary {' in css
    for html in (pi, res_pi):
        assert '.name-primary {' not in html


def test_refit_is_not_on_the_per_frame_path(pi, cloud):
    """fitNameFontSize forces synchronous layout per lane. Names arrive on a heat
    change, so the call is gated — running it on every update_scoreboard would put
    a layout pass in the middle of a race."""
    for html in (pi, cloud):
        assert 'if (names_changed) requestAnimationFrame(fitNameFontSize)' in html


def test_kiosk_page_shrinks_names_too():
    """server/templates/live.html is standalone — it does not extend the base, so
    it carries its own copy and can drift. notes/scoreboard_parity.md tracks it
    against the Qt board, which has always used FitLabel here."""
    src = open(os.path.join(REPO, 'server/templates/live.html')).read()
    assert 'class="name-primary"' in src
    assert 'function fitNameFontSize()' in src
    assert 'if (names_changed) requestAnimationFrame(fitNameFontSize)' in src


def test_results_falls_back_to_waiting_when_nothing_feeds_it(res_pi, res_cloud):
    """Previously Pi-only pages had no such state: a dead console left the last
    heat on screen indefinitely, with nothing saying it was stale."""
    for html in (res_pi, res_cloud):
        assert "sock.on('meet_live'" in html
        assert "sock.on('disconnect'" in html
        assert 'showWaiting()' in html


def test_waiting_message_is_translated_on_both(res_pi, res_cloud):
    """It is a [mobile] string. The cloud read it off `labels`, where it does not
    exist, so its waiting screen was English whatever language the meet ran in."""
    for html in (res_pi, res_cloud):
        assert 'Waiting…' in html            # the fixture's [mobile] string won
    src = open(os.path.join(REPO, 'cloud', 'cloud_server.py')).read()
    assert src.count("t=_strings(_client_lang(request, meet), 'mobile')") == 3, \
        'every per-meet cloud page must pass the [mobile] strings'


def _css(html):
    style = re.search(r'<style>(.*?)</style>', html, re.S).group(1)
    return re.sub(r'/\*.*?\*/', '', style, flags=re.S)   # comments mention z-index too


def _rule(css, selector, after=0):
    start = css.index(selector, after)
    return css[start:css.index('}', start)]


def _page_css(html):
    """Only this page's own block. The base declares its own portrait/landscape
    media queries earlier in the same <style>, so searching the whole thing finds
    those instead of the overrides under test."""
    css = _css(html)
    return css[css.index('#waiting {'):]


def test_waiting_message_never_covers_the_board(res_pi, res_cloud):
    """It used to be a fixed panel over the table, which needed a z-index above the
    table's own and leaked its background out below the last lane. Now the rows are
    cleared when the feed drops, so there is nothing to hide and nothing to stack."""
    for html in (res_pi, res_cloud):
        rule = _rule(_css(html), '#waiting {')
        assert 'position: fixed' not in rule
        assert 'z-index' not in rule
        assert 'background' not in rule
        assert 'flex: 1' in rule           # takes leftover room, not the whole area


def test_waiting_message_fills_the_gap_in_portrait(res_pi, res_cloud):
    """Portrait rows take their natural height, so the space below the last lane is
    free and the message goes there, under a visible empty grid."""
    for html in (res_pi, res_cloud):
        portrait = _page_css(html)
        portrait = portrait[portrait.index('@media (orientation: portrait)'):]
        assert 'flex: 0 0 auto' in _rule(portrait, '.timing-content')
        assert 'height: auto' in _rule(portrait, '.timing-table'), \
            'the table must give up its 100% height or there is no gap'


def test_no_message_in_landscape(res_pi, res_cloud):
    """The rows share out the full height there, so there is no room for it. An
    empty grid reads the same way the live board's does, and that shows no message
    either — better than covering the board to say so."""
    for html in (res_pi, res_cloud):
        landscape = _page_css(html)
        landscape = landscape[landscape.index('@media (orientation: landscape)'):]
        assert 'display: none' in _rule(landscape, '#waiting {')


def test_stale_results_are_cleared_not_covered(res_pi, res_cloud):
    """A results board holds still by design, which is what makes an old heat read
    as the current one. When the feed goes away the rows are wiped, so the message
    never has anything to hide."""
    for html in (res_pi, res_cloud):
        assert 'function clearResults()' in html
        assert 'function goIdle() { clearResults(); showWaiting(); }' in html
        assert "sock.on('disconnect', goIdle)" in html
        assert 'if (!(d && d.live)) goIdle()' in html


# ── Event names follow the reader (`T-11`) ────────────────────────────────────

def test_the_board_composes_the_event_name_it_is_given_parts_for(pi, cloud):
    """A frame is one broadcast, so `event_name` is in the meet's language. A page
    rendered for a visitor reading another one composes from `event_name_parts`
    against the vocabulary the server embedded — see `T-11`."""
    for page in (pi, cloud):
        assert 'window.EVENT_VOCAB' in page
        assert 'composeEventName(s["event_name_parts"], window.EVENT_VOCAB)' in page


def test_the_vocabulary_is_the_page_language_not_the_meets():
    """Embedded per render, so the tab the visitor opened carries their words."""
    page = _render('server/templates', 'live-mobile.html',
                   event_vocab={'unit': 'm', 'freestyle': 'libre'})
    assert '"freestyle": "libre"' in page or '"freestyle":"libre"' in page


def test_a_page_without_a_vocabulary_still_renders(pi):
    """`event_vocab` is optional: an older render path leaves it out and the board
    falls back to `event_name`, which is already right for most viewers."""
    assert 'window.EVENT_VOCAB = {}' in pi
