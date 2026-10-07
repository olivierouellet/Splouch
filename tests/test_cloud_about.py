"""`GET /about` — what Splouch is, the apps, and where the source lives.

What it must hold:

* **It renders in every language the server ships**, and links every repository.
* **Store buttons only once an app is listed**, from the same `_store_links()` the
  picker uses; until then the page says the apps are coming.
* **None of it is on the wire to the apps**: `[about]` is its own section, like
  `[privacy]`.
"""

import re
import tomllib
from pathlib import Path

import pytest
from starlette.requests import Request

import cloud_control as cs

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "cloud" / "templates" / "about.html"


def get(query=""):
    scope = {
        "type": "http",
        "method": "GET",
        "scheme": "https",
        "path": "/about",
        "query_string": query.encode(),
        "headers": [(b"host", b"splouch.org")],
        "server": ("splouch.org", 443),
        "app": cs.app,
    }
    response = cs.route_about(Request(scope))
    assert response.status_code == 200
    return response.body.decode()


def section(code):
    path = REPO / "shared" / "locales" / f"{code}.toml"
    return tomllib.loads(path.read_text(encoding="utf-8"))["about"]


@pytest.fixture(autouse=True)
def no_stores(monkeypatch):
    monkeypatch.setattr(cs, "_store_links", dict)


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_every_language_renders_with_every_repository(code):
    html = get(f"lang={code}")
    assert f'<html lang="{code}">' in html
    assert section(code)["title"] in html
    for _, url in cs.REPOS:
        assert f'href="{url}"' in html


def test_store_buttons_only_once_listed(monkeypatch):
    html = get("lang=en")
    assert section("en")["apps_soon"] in html
    url = "https://apps.apple.com/app/id1"
    monkeypatch.setattr(cs, "_store_links", lambda: {"ios": url})
    html = get("lang=en")
    assert f'href="{url}"' in html
    assert section("en")["apps_soon"] not in html


def test_the_template_reads_every_key_and_only_those():
    used = set(re.findall(r"t\.get\('([a-z0-9_]+)'", TEMPLATE.read_text())) - {"repo_"}
    used |= {f"repo_{key}" for key, _ in cs.REPOS}
    assert used == set(section("en"))


def test_the_page_is_not_in_the_app_bundle():
    assert "about" not in cs._i18n_bundle("en")


def test_the_page_is_public():
    route = next(r for r in cs.app.routes if getattr(r, "path", "") == "/about")
    assert not getattr(route, "dependencies", [])
