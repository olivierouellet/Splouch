"""The picker's install hand-off (`P-10`): the reader's store, else the home screen.

The same narrowing `/add` applies (see `test_cloud_add_page.py`), on the page every
spectator passes through: one button, the reader's own platform, and nothing for a
platform with no listing yet — that reader still gets Add to Home Screen.
"""

import pytest
from starlette.requests import Request

import cloud_server as cs
from test_cloud_add_page import ANDROID, APPSTORE, IPAD_DESKTOP, IPHONE, PLAY, stores

__all__ = ["stores"]  # a fixture, imported so pytest finds it here


def picker(agent=None):
    headers = [(b"host", b"splouch.ca")]
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
        "server": ("splouch.ca", 443),
        "app": cs.app,
    }
    return cs.route_index(Request(scope)).body.decode()


def store_link(html):
    marker = 'id="a2hs-store" href="'
    if marker not in html:
        return None
    return html.split(marker, 1)[1].split('"', 1)[0]


def test_no_listing_offers_no_store(stores):
    """Today's state: nothing configured, so the page is the home-screen hint."""
    for agent in (IPHONE, ANDROID, None):
        assert store_link(picker(agent)) is None


@pytest.mark.parametrize(
    "agent, want", [(IPHONE, APPSTORE), (ANDROID, PLAY)], ids=["iphone", "android"]
)
def test_a_phone_gets_its_own_store(stores, agent, want):
    stores(store_ios=APPSTORE, store_android=PLAY)
    assert store_link(picker(agent)) == want


def test_a_platform_without_a_listing_is_not_sent_to_the_other_one(stores):
    """An App Store link is not an answer to an Android phone."""
    stores(store_ios=APPSTORE)
    assert store_link(picker(ANDROID)) is None


def test_an_agent_we_cannot_place_gets_no_store_button(stores):
    """Unlike `/add`, which lists every store for it: the picker is not the page a
    poster sent someone to, and a desktop reading it needs no phone app pitched."""
    stores(store_ios=APPSTORE, store_android=PLAY)
    assert store_link(picker(IPAD_DESKTOP)) is None


def test_the_button_says_the_store_in_the_readers_words(stores):
    stores(store_android=PLAY)
    html = picker(ANDROID)
    assert "Get it on Google Play" in html.split('id="a2hs-store"', 1)[1][:200]
