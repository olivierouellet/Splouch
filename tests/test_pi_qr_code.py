"""`GET /qr` — the code a Pi draws of itself, for an operator to print (`P-16`).

The useful server at a pool is the Pi in the building (`P-11`), and typing
`splouch.local:5000` into a phone is the step that loses a spectator. A poster on
the wall is the answer, and the operator is the only person who can put one there.

Everything worth testing here is about what goes *into* the link, because the code
itself is printed once and read by strangers with no way to report a problem:

* **The host is the cloud, never this Pi.** A link is verified per host and a Pi
  has no certificate, so the Pi goes in the query. With no cloud configured the
  page says which field to fill instead of minting something that cannot work.
* **The Pi names itself by mDNS**, `http://<host>.local[:port]`, whatever address
  the operator happens to be browsing by. A code minted from an address bar
  showing `192.168.1.22` scans into "cannot add this server" on the deck.
* **The address is printed in words under the code**, because a code nobody can
  read is a code nobody can check, and because a camera that will not focus on a
  wall leaves typing as the only way in.
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


@pytest.fixture
def cloud(monkeypatch):
    """Point this Pi at a cloud, restoring whatever the settings dict held."""
    def set_url(url):
        monkeypatch.setitem(state.settings, 'cloud_relay_url', url)
    set_url('https://splouch.ca')
    return set_url


@pytest.fixture
def hostname(monkeypatch):
    """The Pi's own name, which `install.sh` sets to `splouch` by default."""
    def set_name(name):
        monkeypatch.setattr('routes.qr.socket.gethostname', lambda: name)
    set_name('splouch')
    return set_name


def request_from(host):
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


def test_the_link_is_the_cloud_s_with_this_pi_in_the_query(cloud, hostname):
    """`P-16`: the authority is the app's default server, never the Pi.

    A verified link is per host, and no app can verify a Pi — no `https`, no
    certificate. Putting the Pi in the query is also what stops a server minting
    a code that adds a *different* server.
    """
    assert invite(request_from('splouch.local:5000'))['link'] == \
        'https://splouch.ca/add?server=http%3A%2F%2Fsplouch.local%3A5000'


def test_the_pi_names_itself_by_mdns_even_when_browsed_by_ip(cloud, hostname):
    """The failure this prevents can only be discovered standing at a pool.

    A client refuses cleartext to anything but a `.local` name and the loopbacks
    (`P-12`), so a code carrying `192.168.1.22` is a printed poster that fails on
    the deck, where nobody can fix it.
    """
    for host in ('192.168.1.22:5000', 'splouch.local:5000', 'localhost:5000'):
        assert invite(request_from(host))['origin'] == 'http://splouch.local:5000'


def test_no_raw_ip_reaches_the_printed_page(cloud, hostname):
    html = page('192.168.1.22:5000')
    assert '192.168.1.22' not in html
    assert 'splouch.local' in html


def test_the_port_follows_the_way_the_server_is_reached(cloud, hostname):
    """An installed Pi answers on 80 (the iptables redirect in `install.sh`).

    Dropping the default port is not cosmetic: a shorter URL is a sparser code,
    and this one is read across a pool deck.
    """
    assert invite(request_from('splouch.local'))['origin'] == 'http://splouch.local'
    assert invite(request_from('splouch.local:5000'))['origin'] == \
        'http://splouch.local:5000'


def test_a_hostname_already_ending_in_local_is_not_doubled(cloud, hostname):
    """`socket.gethostname()` answers `<name>.local` on some platforms."""
    hostname('splouch.local')
    assert invite(request_from('splouch.local:5000'))['origin'] == \
        'http://splouch.local:5000'


def test_the_page_prints_the_address_in_words_under_the_code(cloud, hostname):
    """A code nobody can read is a code nobody can check against the poster."""
    html = page()
    assert matched(r'<div class="origin">(.*?)</div>', html, flags=re.S).strip() == \
        'http://splouch.local:5000'
    assert matched(r'<div class="link">(.*?)</div>', html, flags=re.S).strip() == \
        invite(request_from('splouch.local:5000'))['link']


def test_the_page_draws_a_scannable_code(cloud, hostname):
    """Inline SVG, black on white, scaling to whatever the paper gives it."""
    html = page()
    assert '<svg' in html
    assert 'viewBox' in matched(r'(<svg[^>]*>)', html)
    assert '#000' in html and '#fff' in html


def test_the_code_is_of_the_link_and_not_of_something_near_it(cloud, hostname):
    """Decoding is not testable here, so pin the input segno was handed."""
    import segno
    link = invite(request_from('splouch.local:5000'))['link']
    expected = segno.make(link, error='m').svg_inline(
        svgclass=None, lineclass=None, omitsize=True, dark='#000000', light='#ffffff')
    assert expected in page()


def test_no_cloud_configured_says_which_field_to_fill(cloud, hostname):
    """Rather than a code built on a host no app will accept.

    The host has to be the app's default server, and the only thing on a Pi that
    names a cloud is the URL in Settings → Cloud.
    """
    cloud('')
    data = invite(request_from('splouch.local:5000'))
    assert data['link'] is None and data['reason'] == 'no_cloud'
    html = page()
    assert '<svg' not in html
    assert 'Cloud tab' in matched(r'<div class="missing">(.*?)</div>', html, flags=re.S)


def test_a_cloud_url_that_cannot_be_a_link_host_is_the_same_answer(cloud, hostname):
    """Cleartext to a public name: refused as a server, refused again as a host.

    Same words as a missing one, because the fix is the same field.
    """
    cloud('http://scores.example.com')
    assert invite(request_from('splouch.local:5000'))['reason'] == 'no_cloud'


def test_an_unusable_hostname_is_reported_rather_than_guessed(cloud, hostname):
    hostname('')
    data = invite(request_from('splouch.local:5000'))
    assert data['link'] is None and data['reason'] == 'no_hostname'
    assert 'missing' in page()


def test_the_page_is_reachable_from_the_admin_panel():
    """`P-16` asks for it to be reachable, and an operator will not guess `/qr`."""
    panel = open(os.path.join(REPO, 'server', 'templates', 'settings.html'),
                 encoding='utf-8').read()
    assert 'href="/qr"' in panel


def test_the_link_shape_is_the_shared_one():
    """The Pi and the cloud's `GET /add` must not drift, so neither owns the shape."""
    source = open(os.path.join(REPO, 'server', 'routes', 'qr.py'), encoding='utf-8').read()
    assert 'splouch_links' in source
    assert '/add?' not in source, 'the link is built by the shared helper, not here'
    assert splouch_links.INVITE_PATH == '/add'


def test_the_printed_sheet_drops_the_screen_furniture():
    """It is opened to be printed; the button and the hints are not the poster."""
    css = matched(r'@media print \{(.*?)\n        \}', open(QR_TEMPLATE, encoding='utf-8').read(),
                  flags=re.S)
    assert '.print' in css and 'display: none' in css
