"""`GET /add` — where a scanned code lands when the app is not installed (`P-16`).

This page exists for one reader: someone standing in front of a poster who just
pointed a camera at it and does **not** have the app. A phone that has it never
arrives — the OS matched the link against `/.well-known/` and opened the app long
before a request was made — so every choice here follows from who is left.

Three of those choices are what the tests below hold:

* **It always answers.** A 404 is the one outcome worse than any way the link
  itself can be wrong: the reader did the thing the poster asked and got nothing.
  A missing, malformed or cleartext-to-nowhere `server` still renders a page.
* **It shows what was scanned**, held to the same rule the client applies, so the
  reader can check it against the poster and so the page never quotes back an
  address no app would accept.
* **It does not pretend to be the app.** No scheme attempt, no redirect, no
  script at all — each would run only on the phone where it cannot work.

The store links are `P-10`'s hand-off and are *data* (`/picker/config`), so a
listing that moves is a config change rather than a deploy, and their absence
hides the affordance instead of showing a dead button.
"""
import json
import os
import re
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='splouch-cloud-test-'))
sys.path.insert(0, os.path.join(REPO, 'cloud'))

import cloud_server as cs                    # noqa: E402
from starlette.requests import Request       # noqa: E402

from conftest import matched                 # noqa: E402

ADD_TEMPLATE = os.path.join(REPO, 'cloud', 'templates', 'add.html')

PLAY = 'https://play.google.com/store/apps/details?id=app.splouch.android'
APPSTORE = 'https://apps.apple.com/ca/app/splouch/id1234567890'


@pytest.fixture
def stores(tmp_path, monkeypatch):
    """Control both config sources for the store URLs, and start from neither."""
    monkeypatch.setattr(cs, 'APPLINKS_FILE', str(tmp_path / 'applinks.json'))
    monkeypatch.delenv('STORE_URL_ANDROID', raising=False)
    monkeypatch.delenv('STORE_URL_IOS', raising=False)

    def write(**data):
        (tmp_path / 'applinks.json').write_text(json.dumps(data))
    return write


IPHONE = ('Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 '
          '(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1')
ANDROID = ('Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) '
           'Chrome/125.0.0.0 Mobile Safari/537.36')
# iPadOS asks for desktop sites by default and is indistinguishable from macOS here.
IPAD_DESKTOP = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 '
                '(KHTML, like Gecko) Version/17.5 Safari/605.1.15')


def respond(query='', agent=None):
    """Render `/add` through the real route, and hand back the response."""
    headers = [(b'host', b'splouch.ca')]
    if agent:
        headers.append((b'user-agent', agent.encode()))
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'method': 'GET', 'scheme': 'https', 'path': '/add', 'raw_path': b'/add',
             'query_string': query.encode(), 'root_path': '',
             'headers': headers, 'client': ('203.0.113.7', 41234),
             'server': ('splouch.ca', 443), 'app': cs.app}
    response = cs.route_add(Request(scope))
    assert response.status_code == 200
    return response


def get(query='', agent=None):
    return respond(query, agent).body.decode()


def scanned(html):
    """The origin the page quoted back, or None when it drew no server at all."""
    found = re.search(r'<div class="scanned-origin">(.*?)</div>', html, re.S)
    return found.group(1).strip() if found else None


# ── it always answers ──────────────────────────────────────────────────────────

@pytest.mark.parametrize('query', [
    '',                                                  # the bare URL
    'server=',                                           # a code that carried nothing
    'server=%3Cscript%3Ealert(1)%3C%2Fscript%3E',        # junk in the parameter
    'server=http%3A%2F%2F192.168.1.10%3A5000',           # cleartext to a raw IP
    'other=1',
])
def test_the_page_answers_whatever_the_code_carried(stores, query):
    """The only page whose absence a spectator meets as a 404 after scanning.

    They have already done the one thing the poster asked. Every way the link can
    be wrong is still better answered with a page than with an error.
    """
    html = get(query)
    assert '<html' in html


@pytest.mark.parametrize('query', [
    '', 'server=', 'server=http%3A%2F%2F192.168.1.10%3A5000',
])
def test_a_link_with_no_usable_server_says_so_instead_of_inventing_one(stores, query):
    html = get(query)
    assert scanned(html) is None
    assert 'did not name a server' in html


def test_the_page_shows_what_was_scanned(stores):
    """The one thing the reader can check against the poster in front of them."""
    assert scanned(get('server=http%3A%2F%2Fpoolpi.local%3A5000')) == \
        'http://poolpi.local:5000'


def test_the_page_says_so_when_the_code_named_this_very_server(stores):
    """The ordinary case now: a Pi prints a code for the cloud it publishes to.

    For most deployments that is this cloud, and the app answers such a scan with
    "you're already on this server" before opening the meet list. Promising that
    it "will offer to add this server" would be a small lie told to the majority
    of readers.
    """
    here = get('server=https%3A%2F%2Fsplouch.ca')
    assert 'right place' in here
    assert 'offer to add this server' not in here

    elsewhere = get('server=https%3A%2F%2Fscores.myclub.ca')
    assert 'offer to add this server' in elsewhere
    assert 'right place' not in elsewhere


def test_the_origin_is_held_to_the_rule_the_client_applies(stores):
    """`P-12`/`P-13`, via the same helper the Pi mints with.

    A page that displayed an address the app would refuse would be sending the
    reader to a dead end with a store link under it.
    """
    assert scanned(get('server=http%3A%2F%2F192.168.1.10%3A5000')) is None
    assert scanned(get('server=https%3A%2F%2Fscores.example.com')) == \
        'https://scores.example.com'


def test_a_scanned_origin_is_escaped_where_it_is_drawn(stores):
    """Autoescaping plus a validated host, and the test says so in one place."""
    html = get('server=http%3A%2F%2Fpool%22onload%3D%22x.local')
    assert '<script' not in html
    assert 'onload="x' not in html


def test_a_store_url_can_be_changed_without_touching_the_environment(stores):
    """The data file is on the persisted volume, so this needs no compose edit."""
    stores(store_android=PLAY)
    assert cs._store_links() == {'android': PLAY}


def test_only_https_store_urls_are_offered(stores, monkeypatch):
    """These are links this server hands a phone; a store's address is never http."""
    monkeypatch.setenv('STORE_URL_ANDROID', 'http://play.google.com/store/apps')
    monkeypatch.setenv('STORE_URL_IOS', 'javascript:alert(1)')
    assert cs._store_links() == {}


def test_picker_config_carries_the_same_dict(stores, monkeypatch):
    """`api.md` §5.7. The native picker reads it; this page renders from it."""
    monkeypatch.setenv('STORE_URL_IOS', APPSTORE)
    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'method': 'GET', 'scheme': 'https', 'path': '/picker/config',
             'raw_path': b'/picker/config', 'query_string': b'', 'root_path': '',
             'headers': [(b'host', b'splouch.ca')], 'client': ('203.0.113.7', 41234),
             'server': ('splouch.ca', 443), 'app': cs.app}
    config = cs.route_picker_config(Request(scope))
    assert config['stores'] == {'ios': APPSTORE}
    # The rest of the contract is untouched — this is an added key, not a reshape.
    assert {'title', 'window_title', 'has_logo', 'logo_above', 'lang',
            'analytics_enabled', 'strings'} <= set(config)


# ── what the page offers, and to whom ──────────────────────────────────────────

def offers(html):
    """The page's links, as labels, in the order they are drawn."""
    return ['web' if kind == 'web' else ('android' if 'play.google' in href else 'ios')
            for kind, href in re.findall(r'<a class="(store|web)" href="([^"]*)"', html)]


def configure(monkeypatch, *platforms):
    for platform, url in (('android', PLAY), ('ios', APPSTORE)):
        if platform in platforms:
            monkeypatch.setenv(f'STORE_URL_{platform.upper()}', url)


# The whole rule in one table, because it is two independent questions — what the
# agent says, and what has actually been listed — and reading them separately is
# how the earlier version of this page ended up offering an iPhone a Play link.
@pytest.mark.parametrize('agent,listed,expected', [
    # A recognised phone whose app is listed: the one button, and nothing else.
    (ANDROID, ('android',),      ['android']),
    (ANDROID, ('android', 'ios'), ['android']),
    (IPHONE,  ('ios',),          ['ios']),
    (IPHONE,  ('android', 'ios'), ['ios']),
    # A recognised phone whose app is not listed: the browser, not the other store.
    (ANDROID, ('ios',),          ['web']),
    (IPHONE,  ('android',),      ['web']),
    # Nothing listed at all — today's state — is the browser for everyone.
    (ANDROID, (),                ['web']),
    (IPHONE,  (),                ['web']),
    (IPAD_DESKTOP, (),           ['web']),
    # An agent we could not place is read as an iPad, and always keeps the browser.
    (IPAD_DESKTOP, ('ios',),          ['ios', 'web']),
    (IPAD_DESKTOP, ('android', 'ios'), ['ios', 'web']),
    (IPAD_DESKTOP, ('android',),      ['web']),
    (None,    ('android', 'ios'), ['ios', 'web']),
])
def test_what_each_reader_is_offered(stores, monkeypatch, agent, listed, expected):
    configure(monkeypatch, *listed)
    assert offers(get('server=https%3A%2F%2Fsplouch.ca', agent)) == expected


def test_a_recognised_phone_with_its_app_listed_gets_no_browser_link(stores, monkeypatch):
    """`P-10` is a hand-off, not a second front door.

    Where the app is the whole answer, a browser link beside the store button is
    the easier tap and the one that ends the hand-off.
    """
    configure(monkeypatch, 'android', 'ios')
    assert 'class="web"' not in get('server=https%3A%2F%2Fsplouch.ca', ANDROID)
    assert 'class="web"' not in get('server=https%3A%2F%2Fsplouch.ca', IPHONE)


def test_nobody_is_ever_left_with_nothing(stores, monkeypatch):
    """What makes reading an unknown agent as an iPad safe.

    The guess can be wrong — a computer, or an Android tablet in some browser that
    asks for desktop sites — and the browser link is there in every one of those
    cases, so being wrong costs a wasted button rather than a dead end.
    """
    for listed in ((), ('android',), ('ios',), ('android', 'ios')):
        for agent in (ANDROID, IPHONE, IPAD_DESKTOP, None, 'curl/8.4.0'):
            monkeypatch.delenv('STORE_URL_ANDROID', raising=False)
            monkeypatch.delenv('STORE_URL_IOS', raising=False)
            configure(monkeypatch, *listed)
            assert offers(get('server=https%3A%2F%2Fsplouch.ca', agent)), (agent, listed)


def test_the_browser_link_goes_to_the_picker_and_never_to_a_meet(stores):
    """`P-06`'s disclaimer is on the meet list, and a reader arriving by camera
    is the one who has never seen it. `/mobile` would walk them straight past it.
    """
    html = get('server=https%3A%2F%2Fsplouch.ca', IPHONE)
    web = matched(r'<a class="web" href="([^"]*)"', html)
    assert web == '/'
    assert '/mobile' not in html


def test_the_response_says_it_varies_by_agent(stores, monkeypatch):
    """Nothing caches this today; a proxy that one day does must not mix them up."""
    configure(monkeypatch, 'android')
    assert respond('server=https%3A%2F%2Fsplouch.ca', ANDROID).headers['Vary'] == 'User-Agent'


# ── it does not pretend to be the app ──────────────────────────────────────────

def test_the_page_never_tries_to_reach_the_app(stores):
    """Only a phone that *cannot* open the app ever reads this page.

    A `splouch://` attempt or a timed redirect would therefore fire exclusively
    where it fails, and a page that flickers through a dead scheme before showing
    a store link reads as broken rather than as an offer.
    """
    # The template's own comments say all of this in prose and are stripped from
    # the output, so the check is against what is actually served.
    source = re.sub(r'\{#.*?#\}', '', open(ADD_TEMPLATE, encoding='utf-8').read(), flags=re.S)
    html = get('server=http%3A%2F%2Fpoolpi.local%3A5000')
    for text in (source, html):
        assert '<script' not in text
        assert 'splouch://' not in text
        assert 'http-equiv="refresh"' not in text.lower()
        assert 'window.location' not in text


def test_the_page_carries_the_disclaimer_the_picker_does(stores):
    """`P-06` is not decoration, and this is a page a spectator reads before a board."""
    html = get('server=http%3A%2F%2Fpoolpi.local%3A5000')
    assert 'unofficial' in matched(r'<p class="note">(.*?)</p>', html, flags=re.S)


def test_the_page_follows_the_reader_s_language(stores):
    """Picker language, not a meet's — the same resolution `GET /` uses.

    The server in the query has a language of its own and this page is not it: the
    reader is holding a phone, not standing in front of a board yet.
    """
    html = get('server=http%3A%2F%2Fpoolpi.local%3A5000&lang=fr')
    assert 'Ce code pointe vers' in html
    assert matched(r'<html lang="([a-z]+)"', html) == 'fr'
