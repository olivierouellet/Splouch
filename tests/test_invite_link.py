"""The QR link's shape (`app.md` `P-16`), which three repos have to agree on.

    https://<the app's default host>/add?server=<origin, percent-encoded>

The Pi mints one, the cloud's `GET /add` parses one, and an app in another repo
parses one too — with `java.net.URI`, against rules written before either server
half existed. Nothing here can check the app; what it can do is hold this side to
the same rules, because every one of them is a way the link can be wrong in a
place no test on this side would otherwise look: on a pool deck, from a camera.

The rules, and why each is not a preference:

* **`https`, on the cloud's host, path `/add`.** A verified link is per host, and
  a Pi has no certificate, so no link can be hosted on one. A link on any other
  authority is not ours — a server cannot mint a code that adds a *different*
  server — and the Android manifest matches `/add` exactly rather than by prefix.
* **Cleartext only to a `.local` name or a loopback** (`P-12`). The same floor a
  typed address gets (`P-13`), applied to a string a stranger printed.
* **Percent-encoded, fully.** The `:` and `/` of the origin are escaped so nothing
  between the camera and the app can read the value as a path of its own.

What a server *mints* is narrower than what this module will parse, and that rule
is tested where it is enforced — see `test_pi_qr_code.py`. `parse_origin` mirrors
the client, which accepts a `.local` address however it arrives.
"""
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'shared', 'py'))

import splouch_links as links   # noqa: E402

CLOUD = 'https://splouch.ca'


def test_the_link_is_the_shape_the_app_was_built_against():
    """The one example in `parity.md` `P-16`, byte for byte."""
    assert links.invite_link(CLOUD, 'http://poolpi.local:5000') == \
        'https://splouch.ca/add?server=http%3A%2F%2Fpoolpi.local%3A5000'


def test_the_origin_is_escaped_and_not_merely_quoted():
    """`safe=''`: a bare `/` in the value would be a second path to a naive reader."""
    link = links.invite_link(CLOUD, 'http://poolpi.local:5000')
    assert '://' not in link.split('?', 1)[1]
    assert link.count('/') == 3, 'the only slashes left are the cloud origin and /add'


def test_the_path_is_exactly_add():
    """`pathPrefix` in the manifest would also swallow `/address` and friends."""
    assert links.INVITE_PATH == '/add'
    assert links.INVITE_PARAM == 'server'


@pytest.mark.parametrize('origin', [
    'http://poolpi.local:5000',      # the pool's Pi, by the name mDNS publishes
    'http://splouch.local',          # an installed Pi answers on 80, port dropped
    'https://scores.example.com',    # another cloud
    'http://localhost:5055',         # the developer loopbacks
    'http://10.0.2.2:5056',
])
def test_an_address_a_client_would_accept_round_trips(origin):
    assert links.parse_origin(origin) == origin
    assert links.invite_link(CLOUD, origin).endswith(
        links.quote(origin, safe=''))


@pytest.mark.parametrize('origin', [
    'http://192.168.1.10:5000',   # the whole point of P-12: a raw IP is refused
    'http://10.0.0.5',
    'http://splouch.ca',          # cleartext to a public name
])
def test_cleartext_off_the_local_network_mints_nothing(origin):
    """A code that scans into "cannot add this server" is worse than no code.

    The client's rule cannot express IP ranges, so cleartext is by name only.
    This is the floor a *typed* address gets (`P-13`) applied to a printed one,
    and it holds wherever the string came from — a hand-written poster included.
    """
    assert links.parse_origin(origin) is None
    assert links.invite_link(CLOUD, origin) is None


@pytest.mark.parametrize('text', [
    '', '   ', 'not a url',
    'https://<script>alert(1)</script>',   # urlsplit hands this back as a "host"
    'http://a b.local',
    'http://-nope-.local',
    'http://user:pw@splouch.local',        # userinfo, which the client refuses
    'http://splouch.local:notaport',
    'http://splouch.local:99999',
])
def test_a_host_a_stricter_parser_would_reject_is_rejected_here(text):
    """`urlsplit` is far laxer than the `java.net.URI` on the other side.

    It answers `<script>alert(1)<` for the host of the third case above. Escaped
    on the page, so never an injection — but an address quoted back to a reader
    as though it were a server, and one no app would ever accept.
    """
    assert links.parse_origin(text) is None


def test_two_spellings_of_one_server_are_one_string():
    """The key a `vid` is stored under (`C-10`), so normalisation is not cosmetic."""
    same = {links.parse_origin(t) for t in (
        'https://splouch.ca', 'https://SPLOUCH.CA/', 'https://splouch.ca:443')}
    assert same == {'https://splouch.ca'}


def test_the_cloud_host_is_normalised_too():
    """An operator's trailing slash must not change the code from one boot to the next."""
    assert links.invite_link('https://splouch.ca/', 'http://poolpi.local') == \
           links.invite_link('https://splouch.ca:443', 'http://poolpi.local')


def test_a_half_configured_server_mints_nothing():
    assert links.invite_link('', 'http://poolpi.local') is None
    assert links.invite_link(CLOUD, '') is None


def test_the_apps_default_server_is_the_only_authority_a_link_may_carry():
    """A property of the published app, not of any server here.

    An App Link is verified per host and the Android manifest names this one, so a
    Pi cannot be asked what the app on a stranger's phone was built against — which
    is why it is a constant rather than a setting.
    """
    assert links.DEFAULT_APP_SERVER == CLOUD
    assert links.parse_origin(links.DEFAULT_APP_SERVER) == links.DEFAULT_APP_SERVER
