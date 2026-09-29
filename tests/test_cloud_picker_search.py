"""Searching the meet list (`app.md` `P-17`).

A cloud serving a whole season's meets is a long page of cards, and a spectator who
knows the meet they want should not have to scroll for it. The picker already holds
every meet, so the search is a filter over the cards rather than an endpoint.

What this file guards: the box appears only once the list is long, a card carries
what the search reads, the matching is every word in any order through `S-09`'s
fold, and `GET /meets` leads with the meets that are live.
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

import cloud_server as cs
from jsc import HAS_JS_ENGINE, js_argv, run_page

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PICKER = os.path.join(REPO, "cloud", "templates", "picker.html")
FOLD_JS = os.path.join(REPO, "shared", "static", "js", "fold.js")


def _meet(i, **kw):
    return {
        "id": f"m{i}",
        "name": f"Meet {i}",
        "location": "",
        "sport": "",
        "organizer": "",
        "meet_date": "",
        "offline": False,
        "has_picker_image": False,
        **kw,
    }


def _render(meets):
    env = Environment(
        loader=FileSystemLoader(
            [
                os.path.join(REPO, "cloud", "templates"),
                os.path.join(REPO, "shared", "templates"),
            ]
        ),
        autoescape=True,
    )
    return env.get_template("picker.html").render(
        meets=meets,
        t={"meet_search": "Search meets", "no_meets_match": "No match"},
        locales=[("en", "English")],
    )


# ── The page ──────────────────────────────────────────────────────────────────


def test_a_short_list_has_no_search_box():
    """A small server's picker looks as it always has."""
    assert 'id="meet-search"' not in _render([_meet(i) for i in range(2)])


def test_three_meets_bring_the_search_box():
    html = _render([_meet(i) for i in range(3)])
    box = re.search(r'<input[^>]*id="meet-search"[^>]*>', html, re.DOTALL)
    assert box, "no search box at the threshold"
    assert "hidden" in box.group(0), (
        "the script reveals it: without JavaScript a box that filters nothing must not show"
    )
    assert 'type="search"' in box.group(0)


def test_a_card_carries_what_it_shows_and_its_organizer():
    """The organizer is not on the card, but a spectator may know a meet by its club."""
    html = _render(
        [
            _meet(
                1,
                name="Coupe du Québec",
                meet_date="2026-10-04",
                location="Montréal",
                sport="Swimming",
                organizer="CAMO",
            )
        ]
    )
    assert 'data-search="Coupe du Québec 2026-10-04 Montréal Swimming CAMO"' in html


def test_empty_fields_leave_no_gaps_in_the_card_text():
    assert 'data-search="Meet 1"' in _render([_meet(1)])


def test_the_page_runs_with_the_search_box():
    run_page(_render([_meet(i) for i in range(6)]))


# ── Matching ──────────────────────────────────────────────────────────────────


def _run_matches(cases):
    """Run the picker's own `meetMatches` over `(card text, query)` pairs."""
    src = Path(PICKER).read_text(encoding="utf-8")
    fn = re.search(r"^function meetMatches\(.*?^\}", src, re.DOTALL | re.MULTILINE)
    assert fn, "meetMatches is gone from the picker"
    js = Path(FOLD_JS).read_text(encoding="utf-8") + "\n" + fn.group(0)
    js += f"\nvar c = {json.dumps(cases)};"
    js += "\nJSON.stringify(c.map(function (p) { return meetMatches(foldName(p[0]), p[1]); }))\n"
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(js)
        path = fh.name
    try:
        res = subprocess.run(js_argv(path), capture_output=True, text=True, check=False)
    finally:
        os.unlink(path)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


needs_js = pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JS engine to run the picker's own matching"
)

_CARD = "Coupe du Québec 2026-10-04 Île-des-Sœurs Swimming CAMO"


@needs_js
def test_every_word_must_match_in_any_order():
    assert _run_matches(
        [
            (_CARD, "quebec 2026"),
            (_CARD, "2026 coupe"),
            (_CARD, "quebec 2025"),
        ]
    ) == [True, True, False]


@needs_js
def test_the_query_folds_like_the_typeahead():
    """`S-09`'s fold, table included: `œ` is not on a phone keyboard."""
    assert _run_matches(
        [
            (_CARD, "QUÉBEC"),
            (_CARD, "soeurs"),
            (_CARD, "camo"),
        ]
    ) == [True, True, True]


@needs_js
def test_an_empty_or_blank_query_shows_everything():
    assert _run_matches([(_CARD, ""), (_CARD, "   ")]) == [True, True]


# ── The order `GET /meets` serves ─────────────────────────────────────────────


def test_live_meets_come_before_retained_ones(monkeypatch):
    """Stable, so each group keeps the order it had."""
    store = {
        "old-a": {"name": "A", "location": "", "sport": "", "organizer": ""},
        "live-b": {"name": "B", "location": "", "sport": "", "organizer": ""},
        "old-c": {"name": "C", "location": "", "sport": "", "organizer": ""},
        "live-d": {"name": "D", "location": "", "sport": "", "organizer": ""},
    }
    monkeypatch.setattr(cs, "_sweep_expired", lambda: None)
    monkeypatch.setattr(cs, "_merged_meets", lambda: store)
    monkeypatch.setattr(cs, "_meets", {"live-b": {}, "live-d": {}})
    assert [m["id"] for m in cs._public_meet_list()] == [
        "live-b",
        "live-d",
        "old-a",
        "old-c",
    ]
