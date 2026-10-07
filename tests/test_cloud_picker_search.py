"""Searching the meet list (`app.md` `P-17`).

A cloud serving a whole season's meets is a long page of cards, and a spectator who
knows the meet they want should not have to scroll for it. The picker already holds
every meet, so the search is a filter over the cards rather than an endpoint.

What this file guards: the box appears only once the list is long, a card carries
what the search reads, the matching is every word in any order through `S-09`'s
fold, and `GET /meets` comes by date, then city, without the meets already over.
"""

import datetime
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

import cloud_control as cs
from jsc import HAS_JS_ENGINE, js_argv, run_page

# The picker reads its branding and the meet list from the control plane's
# store (cloud/cloud_db.py).
pytestmark = pytest.mark.usefixtures("pg")

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
        "country": "",
        "province": "",
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
        days=cs._picker_days(meets),
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
    """The organizer, day and sport are not on the card, but a spectator may know a
    meet by them; and a province by any of its names."""
    html = _render(
        [
            _meet(
                1,
                name="Coupe du Québec",
                meet_date="2026-10-04",
                location="Montréal",
                sport="Swimming",
                organizer="CAMO",
                country="CA",
                province="QC",
            )
        ]
    )
    assert (
        'data-search="Coupe du Québec 2026-10-04 Montréal Swimming CAMO QC Québec PQ CA"'
        in html
    )


def test_the_card_shows_the_city_and_the_codes_not_the_day_or_sport():
    html = _render(
        [
            _meet(
                1,
                meet_date="2026-10-04",
                location="Montréal",
                sport="Swimming",
                country="CA",
                province="QC",
            )
        ]
    )
    meta = re.search(r'<div class="card-meta">(.*?)</div>', html, re.DOTALL)
    assert meta, "no card-meta line"
    assert re.findall(r"<span[^>]*>([^<]*)</span>", meta.group(1)) == [
        "Montréal",
        "QC",
        "CA",
    ]
    assert "Swimming" not in meta.group(1) and "2026-10-04" not in meta.group(1)


def test_cards_sit_under_their_day():
    html = _render(
        [
            _meet(1, meet_date="2026-10-04"),
            _meet(2, meet_date="2026-10-04"),
            _meet(3, meet_date="2026-10-05"),
            _meet(4),
        ]
    )
    heads = re.findall(r'<h2 class="day-head"([^>]*)>([^<]*)</h2>', html)
    assert heads == [
        (' data-date="2026-10-04"', "2026-10-04"),
        (' data-date="2026-10-05"', "2026-10-05"),
        ("", "Date to be announced"),
    ]
    first = html[
        html.index('data-date="2026-10-04"') : html.index('data-date="2026-10-05"')
    ]
    assert first.count('class="card"') == 2


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


def test_meets_come_by_date_then_city_and_a_past_one_not_at_all():
    """By day, a day's meets by city, an undated meet last; a live meet whose Pi
    still holds a meet over for more than a day is not listed."""
    import cloud_auth
    import cloud_registry

    today = datetime.date.today()
    day = [(today + datetime.timedelta(days=n)).isoformat() for n in (-2, 0, 1)]
    key = cloud_auth.add_organizer("Club")
    cloud_registry.heartbeat("ca1", 1, [], host="https://ca1.example")
    cloud_registry.restore(
        {"old-z": {"name": "A", "location": "Laval", "meet_date": day[1]}}, node="ca1"
    )
    ids = {
        name: cloud_registry.register(key, name, meta, "ca1", 1)["meet_id"]
        for name, meta in (
            ("past", {"name": "Past", "meet_date": day[0]}),
            ("later", {"name": "Later", "location": "Alma", "meet_date": day[2]}),
            ("undated", {"name": "Undated"}),
            ("today", {"name": "Z", "location": "Gatineau", "meet_date": day[1]}),
        )
    }
    assert [m["id"] for m in cs._public_meet_list()] == [
        ids["today"],
        "old-z",
        ids["later"],
        ids["undated"],
    ]


def test_a_past_meet_its_operator_keeps_stays_listed_until_its_pi_leaves():
    """`app.md` `P-01`: held *Keep listing* on the Pi — on the picker while it is
    connected, until the keep runs out; gone the moment the Pi disconnects."""
    import cloud_auth
    import cloud_registry

    now = datetime.datetime.now(datetime.UTC)
    past = (now - datetime.timedelta(days=2)).date().isoformat()
    keep = now + datetime.timedelta(hours=12)
    key = cloud_auth.add_organizer("Club")
    cloud_registry.heartbeat("ca1", 1, [], host="https://ca1.example")
    meta = {"name": "Kept", "meet_date": past, "utc_offset_minutes": 0}
    kept = cloud_registry.register(
        key, "kept", {**meta, "keep_listed_until": keep.isoformat()}, "ca1", 1
    )["meet_id"]
    cloud_registry.register(key, "gone", {**meta, "name": "Gone"}, "ca1", 1)
    assert [m["id"] for m in cs._public_meet_list()] == [kept]
    cloud_registry.retire(kept, "ca1", 1)
    assert cs._public_meet_list() == []


def test_a_live_meet_stays_listed_the_day_after_its_end():
    """The results come in late: 24 hours past its end, no one asking."""
    import cloud_auth
    import cloud_registry

    now = datetime.datetime.now(datetime.UTC)
    yesterday = (now - datetime.timedelta(days=1)).date().isoformat()
    key = cloud_auth.add_organizer("Club")
    cloud_registry.heartbeat("ca1", 1, [], host="https://ca1.example")
    meta = {"name": "Late", "meet_date": yesterday, "utc_offset_minutes": 0}
    ids = {
        name: cloud_registry.register(
            key, name, {**meta, "meet_end": end.isoformat()}, "ca1", 1
        )["meet_id"]
        for name, end in (
            ("recent", now - datetime.timedelta(hours=23)),
            ("old", now - datetime.timedelta(hours=25)),
        )
    }
    assert [m["id"] for m in cs._public_meet_list()] == [ids["recent"]]
