"""`/help`, `/aide`, `/ayuda` — the Pi's list of its own pages.

What it must hold:

* **The URL picks the language**, so a link read out at the pool opens the same
  way on every phone.
* **Every page it lists is a page this server has**, and every lock it shows is the
  role the route requires — a list that drifts from the routes is worse than none.
* **It is open**, like the board: it is how someone without a login finds the
  sign-in page.
"""

import re
import tomllib
from pathlib import Path

import pytest
from fastapi import Request

import app
import web
from routes import help as help_routes

PANEL = Path(__file__).resolve().parents[1] / "shared" / "locales" / "panel"


def get(path, query=""):
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": query.encode(),
        "headers": [],
        "app": app.app,
    }
    handler = {"/help": help_routes.route_help, "/aide": help_routes.route_aide}.get(
        path, help_routes.route_ayuda
    )
    response = handler(Request(scope))
    assert response.status_code == 200
    return response.body.decode()


def section(code):
    return tomllib.loads((PANEL / f"{code}.toml").read_text(encoding="utf-8"))["help"]


@pytest.mark.parametrize("path, code", sorted(help_routes.HELP_PATHS.items()))
def test_the_url_picks_the_language(path, code):
    html = get(path)
    assert f'<html lang="{code}">' in html
    assert section(code)["title"] in html


def test_a_language_without_its_own_path_is_a_parameter():
    assert '<html lang="fr">' in get("/help", "lang=fr")
    assert '<html lang="en">' in get("/aide", "lang=xx")


def _routes():
    """Every GET path on the app, through the routers it includes."""
    out = {}

    def walk(routes):
        for r in routes:
            if hasattr(r, "original_router"):
                walk(r.original_router.routes)
            elif "GET" in (getattr(r, "methods", None) or ()):
                out[r.path] = r

    walk(app.app.routes)
    return out


def test_every_listed_page_exists():
    routes = _routes()
    for _, path, _ in help_routes.PAGES:
        assert path in routes, path


def test_every_lock_is_the_route_s_own():
    """A role shown here is the role the route's dependency requires."""
    routes = _routes()
    for _, path, role in help_routes.PAGES:
        roles = [
            d.call.role
            for d in routes[path].dependant.dependencies
            if isinstance(d.call, web.require_role)
        ]
        assert roles == ([role] if role else []), path


def test_the_pages_themselves_are_open():
    routes = _routes()
    for path in help_routes.HELP_PATHS:
        assert not routes[path].dependant.dependencies


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_every_page_has_its_words(code):
    words = section(code)
    for group, path, role in help_routes.PAGES:
        assert words[f"group_{group}"]
        assert words[f"page_{path.strip('/')}"]
        if role:
            assert words[f"role_{role}"]


def test_the_template_reads_only_keys_english_has():
    template = (Path(app.__file__).parent / "templates" / "help.html").read_text()
    used = set(re.findall(r"t\.([a-z_]+)", template))
    assert used <= set(section("en"))
