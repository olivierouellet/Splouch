"""Telling the phones which console a meet is run on — and whether it times anything.

A meet with no timing console runs fine: the operator drives the boards by hand from
/manual, and the header, the swimmers, the upcoming heats and the whole schedule all
reach the phones. What never arrives is a time, a place or a `results_snapshot` — so
the Results screen on a spectator's phone sat on "waiting for results…" from the first
heat to the last, which is not a status, it is a lie that lasts a whole meet.

So the server publishes what it knows: `console = {key, timed}`, on the Pi's `/config`
and inside the relay's `settings` block, which the cloud already serves verbatim at
`GET /meet/{id}/config` (docs/api.md §5.4, §6). A client reads `timed` and drops the
tab (docs/app.md `A-11`).

Two things here are worth guarding beyond the field existing:

* `timed` comes off the **decoder**, never off `console_type == 'manual'`. A console
  added as a local plugin and driven by hand answers `requires_serial` False too, and
  a key comparison would publish it as timed.
* Changing the console in Settings has to *reach* a phone that is already connected —
  re-register, then `reload`, in that order — or the tab stays whatever it was when
  the spectator first loaded the page.
"""
import os
import sys
import tempfile

import pytest
from jinja2 import Environment, FileSystemLoader

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import relay                                   # noqa: E402
import state                                   # noqa: E402
import web                                     # noqa: E402
from console_decoders import make_decoder       # noqa: E402
from console_decoders.base import ConsoleDecoder, SerialConfig   # noqa: E402


@pytest.fixture
def console(monkeypatch):
    """Set the console the way Settings does: the key, and the decoder for it."""
    def _set(key):
        monkeypatch.setitem(state.settings, 'console_type', key)
        monkeypatch.setattr(state, '_decoder', make_decoder(key, state.settings))
    return _set


# ── What the server knows ──────────────────────────────────────────────────────

def test_a_wired_console_is_published_as_timed(console):
    console('cts_gen6')
    assert state.console_state() == {'key': 'cts_gen6', 'timed': True}


def test_the_manual_console_is_published_as_untimed(console):
    console('manual')
    assert state.console_state() == {'key': 'manual', 'timed': False}


def test_it_is_read_off_the_decoder_not_the_settings_key(monkeypatch):
    """The reason `timed` is a flag and not `key == 'manual'` on the client.

    A plugin console (`load_custom_decoders`) can be portless under any key it likes.
    Times will never arrive from it either, and a phone must be told so.
    """
    class HandDriven(ConsoleDecoder):
        requires_serial = False
        serial_config = SerialConfig()
        is_packet_start = feed = reset_lanes = race_finished = None
        set_seed_times = configure = get_lane_time = get_lane_place = None

    monkeypatch.setitem(state.settings, 'console_type', 'club_stopwatch')
    monkeypatch.setattr(state, '_decoder', HandDriven)
    assert state.console_state() == {'key': 'club_stopwatch', 'timed': False}


def test_a_decoder_written_before_the_flag_existed_counts_as_timed(monkeypatch):
    """`requires_serial` is a plain class attribute with a True default, and an old
    plugin that never heard of it is a wired console — the only kind there was."""
    class Old:
        pass

    monkeypatch.setattr(state, '_decoder', Old())
    assert state.console_state()['timed'] is True


# ── How it reaches a client ────────────────────────────────────────────────────

def test_the_pi_config_carries_it(console):
    """A phone pointed straight at a Pi gates on the same fact (docs/api.md §6)."""
    console('manual')
    assert web.display_config()['console'] == {'key': 'manual', 'timed': False}


def test_the_relay_metadata_carries_it(console):
    """Inside `settings`, which the cloud serves verbatim at `/meet/{id}/config`."""
    console('manual')
    assert relay._get_metadata()['settings']['console'] == {'key': 'manual',
                                                            'timed': False}


def test_the_two_servers_publish_the_same_block(console):
    """One fact, two transports. They must not be allowed to drift apart."""
    for key in ('cts_gen6', 'manual'):
        console(key)
        assert web.display_config()['console'] == \
            relay._get_metadata()['settings']['console']


def test_the_console_label_does_not_travel(console):
    """English-only in `CONSOLE_OPTIONS`, and nothing a spectator reads. `key` is for
    a support question; the label would be an untranslatable string on a phone."""
    console('manual')
    block = relay._get_metadata()['settings']['console']
    assert set(block) == {'key', 'timed'}


# ── Changing it mid-meet ───────────────────────────────────────────────────────

def _switch_console(monkeypatch, to, **also):
    """Post Settings → Timing with a new console, capturing what goes out."""
    import asyncio

    import bus
    from routes import settings as settings_route

    sent = []
    monkeypatch.setattr(relay, 'update_metadata', lambda: sent.append('register'))
    monkeypatch.setattr(relay, 'relay_emit', lambda ev, d: sent.append(('relay', ev)))
    monkeypatch.setattr(bus, 'emit',
                        lambda ch, ev, data=None: sent.append((ch, ev)))
    monkeypatch.setattr(settings_route, '_restart_worker', lambda: None)
    monkeypatch.setattr(state, 'save_settings', lambda: None)
    monkeypatch.setitem(state.settings, 'console_type', 'cts_gen6')
    monkeypatch.setitem(state.settings, 'serial_port', '/dev/ttyUSB0')

    form = {'timing_settings_submit': '1', **also}
    if to:
        form['console_type'] = to
    try:
        asyncio.run(settings_route.route_settings(_FakeRequest(form)))
    except Exception:
        # Rendering the page back needs a real Request; the half under test — the
        # saving half — has already run. Same bargain as test_settings_display_form.
        pass
    return sent


class _FakeRequest:
    """Enough of a Starlette Request for the settings POST path."""

    def __init__(self, form):
        self._form = form
        self.method = 'POST'
        self.session = {'user': 'test'}
        self.url = type('U', (), {'path': '/settings'})()
        self.cookies = {}
        self.headers = {}

    async def form(self):
        return self._form


def test_switching_console_re_registers_and_tells_the_clients(monkeypatch):
    """A phone only re-reads the meet config on `reload` (docs/app.md `C-08`), so
    without this an operator who switches to the manual console mid-meet leaves every
    connected spectator with a Results tab that has stopped being able to fill."""
    sent = _switch_console(monkeypatch, 'manual')
    assert 'register' in sent
    assert ('relay', 'reload') in sent
    assert ('/scoreboard', 'reload') in sent and ('/results', 'reload') in sent


def test_the_new_metadata_goes_out_before_the_reload(monkeypatch):
    """Order matters: `reload` sends clients back for their config, so the cloud must
    already hold the new one when they ask."""
    sent = _switch_console(monkeypatch, 'manual')
    assert sent.index('register') < sent.index(('relay', 'reload'))


def test_changing_only_the_serial_port_tells_nobody(monkeypatch):
    """The phones do not know what a serial port is, and this pane saves on every
    keystroke of the port field — a reload of every board for each one is noise."""
    assert _switch_console(monkeypatch, None, serial_port='/dev/ttyUSB1') == []


# ── The web client (the reference implementation of `A-11`) ────────────────────

_LABELS = {'event': 'Event', 'heat': 'Heat', 'lane': 'Lane', 'name': 'Name',
           'club': 'Club', 'time': 'Time', 'delta': 'Δ', 'place': '#'}
_TABS = {'scoreboard': 'Scoreboard', 'results': 'Results', 'schedule': 'Schedule',
         'back_to_meets': 'Meets'}


def _shell(own_dir, **extra):
    env = Environment(loader=FileSystemLoader(
        [os.path.join(REPO, own_dir), os.path.join(REPO, 'shared', 'templates')]))
    env.globals['url_for'] = lambda name, **kw: '/static/' + kw.get('filename', '')
    return env.get_template('mobile.html').render(
        app_title='Coupe', t=_TABS, labels=_LABELS, num_lanes=6,
        theme_colors=state.DEFAULT_THEME_COLORS,
        theme_fonts=state.DEFAULT_THEME_FONTS, **extra)


@pytest.mark.parametrize('own_dir', ['server/templates', 'cloud/templates'])
def test_the_shell_drops_the_results_tab_for_an_untimed_meet(own_dir):
    html = _shell(own_dir, show_results=False, meet_id='abc123')
    assert 'id="frame1"' not in html and 'id="tab1"' not in html
    assert 'id="frame0"' in html and 'id="frame2"' in html    # the other two stay
    assert f">{_TABS['results']}<" not in html   # the nav button's label


@pytest.mark.parametrize('own_dir', ['server/templates', 'cloud/templates'])
def test_a_timed_meet_keeps_all_three(own_dir):
    html = _shell(own_dir, show_results=True, meet_id='abc123')
    for probe in ('id="frame0"', 'id="frame1"', 'id="frame2"',
                  'id="tab0"', 'id="tab1"', 'id="tab2"'):
        assert probe in html


def test_a_server_that_says_nothing_gets_all_three():
    """The template is rendered by two servers and by neither's tests alone; an
    undefined flag must mean *there is a console*, as it did before it existed."""
    assert 'id="frame1"' in _shell('server/templates')


def test_the_tab_bar_is_not_numbered_three(monkeypatch):
    """The swipe, the restore and the buttons all read how many tabs there are."""
    html = _shell('server/templates', show_results=False)
    assert 'frames.length - 1' in html
    assert 'Math.min(current + 1, last)' in html


def test_the_selected_tab_is_remembered_by_name(monkeypatch):
    """`A-04` stores a choice, not a position: the console can change between two
    visits, and index 2 would then be a different tab — or no tab at all."""
    html = _shell('server/templates')
    assert "sessionStorage.setItem('tab', names[idx])" in html
    assert 'names.indexOf(saved)' in html


def test_a_config_change_reloads_the_shell_not_just_the_tab():
    """The tab bar is server-rendered and the shell holds no socket of its own, so a
    `reload` that only reached the iframes would leave a dead Results tab in the bar
    until the spectator reloaded the page by hand."""
    env = Environment(loader=FileSystemLoader(
        [os.path.join(REPO, 'server', 'templates'),
         os.path.join(REPO, 'shared', 'templates')]))
    env.globals['url_for'] = lambda name, **kw: '/static/' + kw.get('filename', '')
    board = env.get_template('live-mobile.html').render(
        num_lanes=6, labels=_LABELS, theme_colors=state.DEFAULT_THEME_COLORS,
        theme_fonts=state.DEFAULT_THEME_FONTS,
        **{f'show_{k}': True for k in
           ('lane_header', 'name_header', 'club_header', 'time_header',
            'delta_header', 'position_header', 'name', 'club', 'delta', 'position')})
    assert 'window.parent !== window ? window.parent : window' in board


# ── End to end, through the routes both servers actually serve ─────────────────

def _fake_request(app, query=b''):
    from starlette.requests import Request
    return Request({'type': 'http', 'method': 'GET', 'path': '/mobile',
                    'query_string': query, 'headers': [], 'session': {},
                    'app': app, 'router': app.router})


def test_the_pi_serves_a_shell_without_the_tab(console):
    console('manual')
    import app as pi_app
    from routes.scoreboard import route_mobile

    html = route_mobile(_fake_request(pi_app.app)).body.decode()
    assert 'id="frame1"' not in html
    assert 'id="frame2"' in html


@pytest.fixture(scope='module')
def cloud():
    os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='splouch-cloud-test-'))
    sys.path.insert(0, os.path.join(REPO, 'cloud'))
    import cloud_server as cs
    return cs


def test_the_cloud_passes_the_block_through_untouched(cloud):
    """`/meet/{id}/config` serves `settings` verbatim, so the cloud needs no knowledge
    of consoles at all — which is why this field cost it nothing."""
    block = {'key': 'manual', 'timed': False}
    cloud._meets['m-console'] = {'name': 'Coupe', 'settings': {'console': block}}
    try:
        assert cloud.route_meet_config('m-console')['settings']['console'] == block
    finally:
        cloud._meets.pop('m-console', None)


@pytest.mark.parametrize('settings,expected', [
    ({'console': {'key': 'manual',   'timed': False}}, False),
    ({'console': {'key': 'cts_gen6', 'timed': True}},  True),
    ({},                                               True),   # a relay before this
])
def test_the_cloud_shell_follows_the_meet(cloud, settings, expected):
    cloud._meets['m-shell'] = {'name': 'Coupe', 'settings': settings}
    try:
        resp = cloud.route_mobile(_fake_request(cloud.app, b'meet=m-shell'))
        html = resp.body.decode()
    finally:
        cloud._meets.pop('m-shell', None)
    assert ('id="frame1"' in html) is expected
