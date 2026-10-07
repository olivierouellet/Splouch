"""The picker's notices (`app.md` `P-06`, `P-07`) and the counting choice (`C-10`).

The two folding banners above the list stood in the way. Now the unofficial-results
disclaimer is one line that cannot be closed — a `<details>`, so the full text is a
tap away with or without the script — and the attendance note lives in the settings
sheet, beside the toggle that refuses counting.

What this file guards: the line is above the list and in the page without
JavaScript; the privacy section follows `analytics_enabled`; and `count.js` — the one
place the picker and every meet page get the attendance id from — keeps its
promises: off deletes the id, back on makes a new one, Global Privacy Control starts
off, an id is replaced after 13 months, and the hand-off fragment carries a refusal.
"""

import json
import os
import re
from urllib.parse import unquote

import pytest
from jinja2 import Environment, FileSystemLoader

from jsc import HAS_JS_ENGINE, run_page

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DISCLAIMER = "Live, unofficial results."
PRIVACY = "Visitors are counted with a random identifier."


def _render(analytics_enabled=False, meets=3):
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
        meets=[
            {"id": f"m{i}", "name": f"Meet {i}", "offline": False} for i in range(meets)
        ],
        t={
            "results_disclaimer": DISCLAIMER,
            "results_disclaimer_short": "Unofficial results",
            "privacy_note": PRIVACY,
        },
        locales=[("en", "English")],
        analytics_enabled=analytics_enabled,
    )


def _body(html):
    return html[html.index("<body") :]


def _dialog(html):
    start = html.index('<dialog id="settings"')
    return html[start : html.index("</dialog>", start)]


# ── The page ──────────────────────────────────────────────────────────────────


def test_the_disclaimer_comes_before_the_search_box_and_the_cards():
    body = _body(_render(analytics_enabled=True))
    line = body.index('<details class="disclaimer"')
    assert line < body.index('id="meet-search"')
    assert line < body.index('class="days"')


def test_the_disclaimer_is_there_even_with_no_meets():
    assert '<details class="disclaimer"' in _render(meets=0)


def test_the_disclaimer_is_one_line_with_its_full_text_a_tap_away():
    """Closed by default; the full text is in the page, so no script is needed."""
    html = _render()
    details = html[html.index('<details class="disclaimer"') :]
    details = details[: details.index("</details>")]
    assert "open" not in details.split(">", 1)[0]
    assert "Unofficial results" in details and DISCLAIMER in details


def test_nothing_on_the_picker_can_close_it():
    body = _body(_render(analytics_enabled=True))
    for gone in ("notice-close", "notice-pill", 'class="notices"'):
        assert gone not in body


def test_the_privacy_note_lives_in_settings_only_while_counting():
    html = _render(analytics_enabled=True)
    assert PRIVACY in _dialog(html)
    assert html.count(PRIVACY) == 1
    assert 'id="count-toggle"' in _dialog(html)
    off = _render(analytics_enabled=False)
    assert PRIVACY not in off and 'id="count-toggle"' not in off


def test_settings_carry_the_disclaimer_and_the_policy():
    dialog = _dialog(_render())
    assert DISCLAIMER in dialog and 'href="/privacy"' in dialog


def test_the_page_runs():
    run_page(_render(analytics_enabled=True))
    run_page(_render(analytics_enabled=False, meets=0))


# ── count.js ──────────────────────────────────────────────────────────────────

needs_js = pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JS engine to run count.js"
)

# Each fresh id differs, so "a new one, never the old" can be seen.
_SETUP = r"""
var __n = 0;
window.crypto.randomUUID = function () { __n += 1; return 'id-' + __n; };
var __store = %(store)s;
Object.keys(__store).forEach(function (k) { localStorage.setItem(k, __store[k]); });
navigator.globalPrivacyControl = %(gpc)s;
location.hash = %(hash)s;
"""


def _count(steps, store=None, gpc=False, fragment=""):
    """Run `steps` (JS) after count.js; each `__out(x)` is returned in order."""
    page = (
        '<script src="/static/js/count.js"></script>'
        "<script>function __out(x) { __calls.push('@' + encodeURIComponent("
        "JSON.stringify(x === undefined ? null : x))); }\n" + steps + "</script>"
    )
    extra = _SETUP % {
        "store": json.dumps(store or {}),
        "gpc": json.dumps(gpc),
        "hash": json.dumps(fragment),
    }
    return [
        json.loads(unquote(c[1:]))
        for c in run_page(page, extra).split(",")
        if c.startswith("@")
    ]


_STATE = (
    "__out([SplouchCount.allowed(), localStorage.getItem('splouch_vid'), "
    "localStorage.getItem('splouch_count')]);"
)


@needs_js
def test_counting_is_on_by_default_and_makes_one_id():
    assert _count("__out(SplouchCount.vid()); __out(SplouchCount.vid());") == [
        "id-1",
        "id-1",
    ]


@needs_js
def test_global_privacy_control_starts_off_until_the_spectator_says_otherwise():
    assert _count("__out(SplouchCount.vid());" + _STATE, gpc=True) == [
        "",
        [False, None, None],
    ]
    assert _count("SplouchCount.set(true); __out(SplouchCount.vid());", gpc=True) == [
        "id-1"
    ]


@needs_js
def test_off_deletes_the_id_and_back_on_makes_a_new_one():
    out = _count(
        "__out(SplouchCount.vid()); SplouchCount.set(false);"
        + _STATE
        + "__out(SplouchCount.vid()); SplouchCount.set(true); __out(SplouchCount.vid());"
    )
    assert out == ["id-1", [False, None, "off"], "", "id-2"]


@needs_js
def test_an_id_from_before_ages_were_kept_is_dated_not_replaced():
    out = _count(
        "__out(SplouchCount.vid()); __out(!!localStorage.getItem('splouch_vid_at'));",
        store={"splouch_vid": "old"},
    )
    assert out == ["old", True]


@needs_js
def test_an_id_older_than_13_months_is_replaced():
    stale = "String(Date.now() - 396 * 24 * 3600 * 1000)"
    young = "String(Date.now() - 300 * 24 * 3600 * 1000)"
    for at, expect in ((stale, "id-1"), (young, "old")):
        out = _count(
            f"localStorage.setItem('splouch_vid_at', {at}); __out(SplouchCount.vid());",
            store={"splouch_vid": "old"},
        )
        assert out == [expect]


@needs_js
def test_the_hand_off_carries_a_refusal():
    out = _count(
        "SplouchCount.accept();" + _STATE,
        store={"splouch_vid": "mine"},
        fragment="#vid=0",
    )
    assert out == [[False, None, "off"]]


@needs_js
def test_the_hand_off_carries_the_pickers_id_and_its_consent():
    out = _count(
        "SplouchCount.accept();" + _STATE,
        store={"splouch_count": "off"},
        fragment="#vid=abc",
    )
    assert out == [[True, "abc", "on"]]


@needs_js
def test_the_picker_forgets_the_old_folds():
    page = _render(analytics_enabled=True) + (
        "<script>__calls.push('@' + [localStorage.getItem('splouch_notice_results'),"
        " localStorage.getItem('splouch_notice_privacy')].join('|'));</script>"
    )
    extra = (
        "localStorage.setItem('splouch_notice_results', 'x');"
        "localStorage.setItem('splouch_notice_privacy', 'y');"
    )
    marks = [c for c in run_page(page, extra).split(",") if c.startswith("@")]
    assert marks == ["@|"]  # both gone: `[null, null].join` is "|"


def test_the_hand_off_sends_zero_for_a_refusal():
    script = _render()
    assert re.search(
        r"'vid=' \+ encodeURIComponent\(SplouchCount\.vid\(\) \|\| '0'\)", script
    )
