"""The picker's install hand-off (`P-10`): the reader's store, else the home screen.

The same narrowing `/add` applies (see `test_cloud_add_page.py`), on the page every
spectator passes through: one button for a phone we recognise, every listing for an
agent we cannot place, and nothing for a platform with no listing yet — that reader
still gets Add to Home Screen.
"""

import re

import pytest
from starlette.requests import Request

import cloud_control as cs
from test_cloud_add_page import ANDROID, APPSTORE, IPAD_DESKTOP, IPHONE, PLAY, stores

# The picker reads its branding and the meet list from the control plane's
# store (cloud/cloud_db.py).
pytestmark = pytest.mark.usefixtures("pg")

__all__ = ["stores"]  # a fixture, imported so pytest finds it here


def picker(agent=None):
    headers = [(b"host", b"splouch.org")]
    if agent:
        headers.append((b"user-agent", agent.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("203.0.113.7", 41234),
        "server": ("splouch.org", 443),
        "app": cs.app,
    }
    return cs.route_index(Request(scope)).body.decode()


def store_links(html):
    return re.findall(r'class="a2hs-store" href="([^"]+)"', html)


def test_no_listing_offers_no_store(stores):
    """Today's state: nothing configured, so the page is the home-screen hint."""
    for agent in (IPHONE, ANDROID, None):
        assert store_links(picker(agent)) == []


@pytest.mark.parametrize(
    "agent, want", [(IPHONE, APPSTORE), (ANDROID, PLAY)], ids=["iphone", "android"]
)
def test_a_phone_gets_its_own_store(stores, agent, want):
    stores(store_ios=APPSTORE, store_android=PLAY)
    assert store_links(picker(agent)) == [want]


def test_a_platform_without_a_listing_is_not_sent_to_the_other_one(stores):
    """An App Store link is not an answer to an Android phone."""
    stores(store_ios=APPSTORE)
    assert store_links(picker(ANDROID)) == []


def test_an_agent_we_cannot_place_gets_every_listing(stores):
    """As on `/add`: mostly iPads asking for the desktop site, which no header gives
    away, so sniffing narrows only when it is sure and never guesses."""
    stores(store_ios=APPSTORE, store_android=PLAY)
    assert sorted(store_links(picker(IPAD_DESKTOP))) == sorted([APPSTORE, PLAY])


def test_the_button_says_the_store_in_the_readers_words(stores):
    stores(store_android=PLAY)
    html = picker(ANDROID)
    assert "Get it on Google Play" in html.split('class="a2hs-store"', 1)[1][:200]
