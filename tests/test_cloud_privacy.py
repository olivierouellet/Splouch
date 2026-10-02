"""`GET /privacy` — the policy a store listing links to.

What it must hold:

* **It renders in every language the server ships**, with every placeholder
  filled — a `{days}` left in the text is a policy that does not say how long.
* **The retention it states is the one the code enforces**, read from
  `cloud_analytics` rather than typed into three locale files, and the prune that
  enforces it runs while the server is up, not only at startup.
* **None of it is on the wire to the apps.** `[privacy]` is its own section, so
  `GET /i18n/{lang}` is unchanged and no app string key moved.
* **The contact is configuration**, and unset leaves the section out.
"""

import asyncio
import os
import re
import tomllib
from pathlib import Path

import pytest
from starlette.requests import Request

import cloud_analytics
import cloud_server as cs

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(REPO, "cloud", "templates", "privacy.html")
LOCALES = os.path.join(REPO, "shared", "locales")


def get(query=""):
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/privacy",
        "raw_path": b"/privacy",
        "query_string": query.encode(),
        "root_path": "",
        "headers": [(b"host", b"splouch.ca")],
        "client": ("203.0.113.7", 41234),
        "server": ("splouch.ca", 443),
        "app": cs.app,
    }
    response = cs.route_privacy(Request(scope))
    assert response.status_code == 200
    return response.body.decode()


def section(code):
    path = os.path.join(LOCALES, f"{code}.toml")
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))["privacy"]


@pytest.fixture(autouse=True)
def no_contact(monkeypatch):
    monkeypatch.delenv("PRIVACY_CONTACT", raising=False)


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_every_language_renders_whole(code):
    html = get(f"lang={code}")
    assert f'<html lang="{code}">' in html
    assert section(code)["title"] in html
    assert "{host}" not in html and "{days}" not in html
    assert "splouch.ca" in html


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_the_stated_retention_is_the_enforced_one(code):
    days = cloud_analytics._ANALYTICS_RETENTION_DAYS
    assert "{days}" in section(code)["count_4"]
    assert f" {days} " in get(f"lang={code}")


def test_the_template_reads_every_key_and_only_those():
    used = set(re.findall(r"t\.get\('([a-z0-9_]+)'", Path(TEMPLATE).read_text()))
    assert used == set(section("en"))


def test_the_policy_is_not_in_the_app_bundle():
    """No app-side string key moved: `/i18n` carries none of `[privacy]`."""
    bundle = cs._i18n_bundle("en")
    assert "privacy" not in bundle
    assert not set(section("en")) & set(bundle["mobile"])


def test_the_page_is_public():
    for route in cs.app.routes:
        if getattr(route, "path", "") == "/privacy":
            assert not getattr(route, "dependencies", [])
            return
    pytest.fail("no /privacy route")


def test_contact_is_shown_only_when_configured(monkeypatch):
    assert "mailto:" not in get()
    monkeypatch.setenv("PRIVACY_CONTACT", "not an address")
    assert "mailto:" not in get()
    monkeypatch.setenv("PRIVACY_CONTACT", "privacy@example.org")
    assert 'href="mailto:privacy@example.org"' in get()


def test_the_prune_runs_while_the_server_is_up(monkeypatch):
    """Startup alone would let a long-running container keep rows past the promise."""

    class Stop(BaseException):
        pass

    def prune():
        raise Stop

    monkeypatch.setattr(cloud_analytics, "_ANALYTICS_FLUSH_SECS", 0)
    monkeypatch.setattr(cloud_analytics, "_ANALYTICS_PRUNE_SECS", 0)
    monkeypatch.setattr(cloud_analytics, "flush_analytics", lambda: None)
    monkeypatch.setattr(cloud_analytics, "analytics_prune", prune)
    with pytest.raises(Stop):
        asyncio.run(cloud_analytics.analytics_flush_loop())
