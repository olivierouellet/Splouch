"""`GET /qr` — the code an operator prints for the meet (`app.md` `P-16`).

**The code names the cloud, not this Pi**, and that is the single property worth
defending here, because the other answer is the tempting one. The useful server at
a pool *is* the Pi in the building — no internet dependency, an unthrottled race
clock (`P-11`) — so a code carrying `http://splouch.local:5000` looks obviously
right until you ask who reads a poster:

* a spectator on cellular, for whom a `.local` name resolves to nothing;
* a spectator on a guest network with client isolation, where mDNS is blocked even
  though they did join the wifi;
* and a spectator whose phone reaches it fine — the only one of the three it works
  for.

A poster cannot ask which of those it is being read by, so it names the address
that works from all three. The Pi is offered afterwards, by `P-12`'s browse, once
the reader is on the picker and their phone is on the venue's network.

The second thing that falls out of naming a cloud: a cloud session's launch screen
is the **meet list**, so the reader lands there rather than on a board — which is
where `P-06`'s unofficial-results disclaimer is. A Pi session skips the picker
(§0.2), so a code naming a Pi would have walked a first-time spectator straight
past a **must**. The tests below pin both halves of that.
"""
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (REPO, os.path.join(REPO, 'server'), os.path.join(REPO, 'shared', 'py')):
    sys.path.insert(0, path)

import splouch_links                       # noqa: E402
import state                               # noqa: E402
from routes.qr import invite, route_qr     # noqa: E402
from starlette.requests import Request     # noqa: E402

from conftest import matched               # noqa: E402

QR_TEMPLATE = os.path.join(REPO, 'server', 'templates', 'qr.html')
QR_ROUTE = os.path.join(REPO, 'server', 'routes', 'qr.py')


@pytest.fixture
def cloud(monkeypatch):
    """Point this Pi at a cloud, restoring whatever the settings dict held."""
    def set_url(url):
        monkeypatch.setitem(state.settings, 'cloud_relay_url', url)
    set_url('https://splouch.ca')
    return set_url


def request_from(host='splouch.local:5000'):
    """A request as the operator's browser made it — by name, or by raw IP."""
    name, _, port = host.partition(':')
    return Request({'type': 'http', 'http_version': '1.1', 'method': 'GET',
                    'path': '/qr', 'raw_path': b'/qr', 'query_string': b'',
                    'root_path': '', 'scheme': 'http',
                    'headers': [(b'host', host.encode())],
                    'client': ('192.168.1.22', 51110),
                    'server': (name, int(port) if port else 80), 'app': None})


def page(host='splouch.local:5000'):
    response = route_qr(request_from(host))
    assert response.status_code == 200
    return response.body.decode()


# ── what the code carries ──────────────────────────────────────────────────────

def test_the_code_names_the_cloud_this_pi_publishes_to(cloud):
    assert invite()['link'] == \
        'https://splouch.ca/add?server=https%3A%2F%2Fsplouch.ca'


def test_no_local_address_is_ever_minted(cloud, monkeypatch):
    """The property this whole page turns on.

    A `.local` name resolves only for a device already on the venue's wifi, and a
    poster cannot ask. Whatever the Pi's own hostname is, and however the operator
    reached this page, none of it may reach the code.
    """
    monkeypatch.setattr('socket.gethostname', lambda: 'splouch')
    for host in ('192.168.1.22:5000', 'splouch.local:5000', 'localhost:5000'):
        html = page(host)
        assert '.local' not in html
        assert '192.168.1.22' not in html


def test_the_code_does_not_depend_on_how_the_page_was_reached(cloud):
    """A poster minted from the address bar carries whatever the operator typed.

    `invite()` takes no request at all, which is the structural version of this
    test: there is no way for the browsing address to leak into the link.
    """
    assert page('192.168.1.22:5000') == page('splouch.local')


def test_a_club_cloud_is_carried_under_the_apps_default_host(cloud):
    """The two halves come from different places, and only one is configurable.

    The authority must be the host the app verified; the `server=` value is the
    cloud this Pi publishes to. They are the same string in the canonical
    deployment and differ for a club running its own relay.
    """
    cloud('https://scores.myclub.ca')
    link = invite()['link']
    assert link.startswith(f'{splouch_links.DEFAULT_APP_SERVER}/add?')
    assert link.endswith('server=https%3A%2F%2Fscores.myclub.ca')


def test_the_default_host_is_not_taken_from_the_pi_s_own_setting(cloud):
    """A Pi cannot be asked what the app on a stranger's phone was built against."""
    cloud('https://scores.myclub.ca')
    assert 'scores.myclub.ca/add' not in invite()['link']


# ── where the reader ends up ───────────────────────────────────────────────────

def test_the_code_leads_to_a_meet_list_and_not_to_a_board(cloud):
    """`P-06`'s disclaimer lives on the picker, and a Pi session skips the picker.

    A cloud origin is what keeps the notice in the path of a spectator who arrived
    by camera — the reader who has most likely never seen it. This asserts the
    property that makes that true: the code names a `cloud` server, which every
    client implementing `P-11` resolves to the meet list.
    """
    origin = invite()['origin']
    assert origin.startswith('https://')
    assert not splouch_links.is_local_name(origin.split('://', 1)[1].split(':')[0])


# ── the page itself ────────────────────────────────────────────────────────────

def test_the_page_prints_the_address_in_words_under_the_code(cloud):
    """A code nobody can read is a code nobody can check against the poster."""
    html = page()
    assert matched(r'<div class="origin">(.*?)</div>', html, flags=re.S).strip() == \
        'https://splouch.ca'
    assert matched(r'<div class="link">(.*?)</div>', html, flags=re.S).strip() == \
        invite()['link']


def test_the_page_draws_a_scannable_code(cloud):
    """Inline SVG, black on white, scaling to whatever the paper gives it."""
    html = page()
    assert '<svg' in html
    assert 'viewBox' in matched(r'(<svg[^>]*>)', html)
    assert '#000' in html and '#fff' in html


def test_the_code_is_of_the_link_and_not_of_something_near_it(cloud):
    """Decoding is not testable here, so pin the input segno was handed."""
    import segno
    expected = segno.make(invite()['link'], error='m').svg_inline(
        svgclass=None, lineclass=None, omitsize=True, dark='#000000', light='#ffffff')
    assert expected in page()


def test_no_cloud_configured_says_which_field_to_fill(cloud):
    """With no cloud there is nowhere to send anyone, so there is no code.

    One reason and one message, because there is one field: a URL that will not
    parse is the same answer as a missing one.
    """
    cloud('')
    assert invite()['link'] is None and invite()['reason'] == 'no_cloud'
    html = page()
    assert '<svg' not in html
    assert 'Cloud tab' in matched(r'<div class="missing">(.*?)</div>', html, flags=re.S)


def test_a_cloud_url_the_app_would_refuse_is_the_same_answer(cloud):
    """Cleartext to a public name: refused as a server, so refused as a code."""
    cloud('http://scores.example.com')
    assert invite()['reason'] == 'no_cloud'


def test_the_page_is_reachable_from_the_admin_panel():
    """`P-16` asks for it to be reachable, and an operator will not guess `/qr`."""
    panel = open(os.path.join(REPO, 'server', 'templates', 'settings.html'),
                 encoding='utf-8').read()
    assert 'href="/qr"' in panel


def test_the_link_shape_is_the_shared_one():
    """The Pi and the cloud's `GET /add` must not drift, so neither owns the shape."""
    source = open(QR_ROUTE, encoding='utf-8').read()
    assert 'splouch_links' in source
    assert '/add?' not in source, 'the link is built by the shared helper, not here'
    assert splouch_links.INVITE_PATH == '/add'


def test_the_printed_sheet_drops_the_screen_furniture():
    """It is opened to be printed; the button and the hints are not the poster."""
    css = matched(r'@media print \{(.*?)\n        \}',
                  open(QR_TEMPLATE, encoding='utf-8').read(), flags=re.S)
    assert '.print' in css and 'display: none' in css
