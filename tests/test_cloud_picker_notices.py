"""The picker's notices (`app.md` `P-06`, `P-07`).

Under the list, a season of meets pushed the unofficial-results disclaimer and the
attendance note out of sight. They now sit above it, and each folds to a pill with
an X — never further, so the disclaimer a **must** asks for is always on screen.

What this file guards: both notices are above the list and in full without
JavaScript, the attendance note follows `analytics_enabled`, and a fold is
remembered against the words folded, so new wording is shown in full again.
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
PRIVACY = "Visitors are counted anonymously."


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
            "privacy_note_short": "Attendance counting",
            "notice_collapse": "Collapse",
        },
        locales=[("en", "English")],
        analytics_enabled=analytics_enabled,
    )


def _body(html):
    return html[html.index("<body") :]


# ── The page ──────────────────────────────────────────────────────────────────


def test_the_notices_come_before_the_search_box_and_the_cards():
    body = _body(_render(analytics_enabled=True))
    notices = body.index('class="notices"')
    assert notices < body.index('id="meet-search"')
    assert notices < body.index('class="meets"')


def test_the_disclaimer_is_there_even_with_no_meets():
    assert DISCLAIMER in _render(meets=0)


def test_the_attendance_note_follows_counting():
    assert '<div class="notice" data-notice="privacy"' not in _render(False)
    html = _render(analytics_enabled=True)
    assert '<div class="notice" data-notice="privacy"' in html and PRIVACY in html


@pytest.mark.parametrize("control", ["notice-pill", "notice-close"])
def test_without_javascript_only_the_full_text_shows(control):
    """The pill and the X do nothing until the script is up, so they start hidden."""
    for tag in re.findall(rf'<button[^>]*class="{control}"[^>]*>', _render(True)):
        assert "hidden" in tag


def test_the_full_text_is_never_rendered_hidden():
    for tag in re.findall(r'<p[^>]*class="notice-full"[^>]*>', _render(True)):
        assert "hidden" not in tag


def test_the_page_runs_with_both_notices():
    run_page(_render(analytics_enabled=True))
    run_page(_render(analytics_enabled=False, meets=0))


# ── Folding ───────────────────────────────────────────────────────────────────

# The stub DOM finds nothing by selector, so the notices are handed to the page
# here, each recording whether its full text or its pill ends up showing. Adding
# `ready` to the box is the script's last act: that is when the state is reported
# and, when a test asks, one control clicked and the state reported again. A report
# rides `__calls`, encoded so its commas survive the join.
_FAKE_NOTICES = r"""
var __store = %(store)s, __click = %(click)s, __notices = {};
Object.keys(__store).forEach(function (k) { localStorage.setItem(k, __store[k]); });
function __fakeNotice(name, text) {
  function ctl() { var c = __node('button'); c.on = {};
    c.addEventListener = function (t, fn) { c.on[t] = fn; }; return c; }
  var el = __node('div'), full = __node('p'), span = __node('span');
  var parts = { '.notice-text': span, '.notice-pill': ctl(), '.notice-full': full,
                '.notice-close': ctl() };
  el.dataset.notice = name; span.textContent = text;
  el.querySelector = function (s) { return parts[s] || null; };
  __notices[name] = parts;
  return el;
}
function __report() {
  var o = { stored: {} };
  Object.keys(__notices).forEach(function (n) {
    o[n] = __notices[n]['.notice-full'].hidden ? 'pill' : 'full'; });
  ['results', 'privacy'].forEach(function (n) {
    o.stored[n] = localStorage.getItem('splouch_notice_' + n); });
  __calls.push('@' + encodeURIComponent(JSON.stringify(o)));
}
var __box = __node('div'), __items = __list(%(notices)s.map(function (p) {
  return __fakeNotice(p[0], p[1]); }));
__box.querySelectorAll = function () { return __items; };
__box.querySelector = function (s) {
  return s === '[data-notice="privacy"]' && __notices.privacy ? __node() : null; };
__box.classList = { add: function () {
  __report();
  if (__click) { __notices[__click[0]][__click[1]].on.click(); __report(); }
}, remove: function () {}, toggle: function () {}, contains: function () { return false; } };
var __qs = document.querySelector;
document.querySelector = function (s) { return s === '.notices' ? __box : __qs(s); };
"""

needs_js = pytest.mark.skipif(
    not HAS_JS_ENGINE, reason="needs a JS engine to run the picker's own script"
)


def _fold(store=None, click=None, privacy=True):
    """The notices' state as the page leaves it, then after `click` if given."""
    notices = [["results", DISCLAIMER]] + ([["privacy", PRIVACY]] if privacy else [])
    extra = _FAKE_NOTICES % {
        "store": json.dumps(store or {}),
        "click": json.dumps(click),
        "notices": json.dumps(notices),
    }
    scheduled = run_page(_render(analytics_enabled=privacy), extra)
    return [
        json.loads(unquote(c[1:])) for c in scheduled.split(",") if c.startswith("@")
    ]


@needs_js
def test_a_first_visit_shows_both_in_full():
    (state,) = _fold()
    assert (state["results"], state["privacy"]) == ("full", "full")


@needs_js
def test_a_fold_of_these_very_words_is_kept():
    (state,) = _fold({"splouch_notice_results": DISCLAIMER})
    assert (state["results"], state["privacy"]) == ("pill", "full")


@needs_js
def test_new_words_are_shown_in_full_again():
    """A reworded notice, or the same one in another language."""
    (state,) = _fold({"splouch_notice_results": "The old wording."})
    assert state["results"] == "full"


@needs_js
def test_the_x_folds_and_remembers_the_words():
    _, after = _fold(click=["results", ".notice-close"])
    assert after["results"] == "pill"
    assert after["stored"]["results"] == DISCLAIMER


@needs_js
def test_the_pill_opens_and_forgets():
    _, after = _fold(
        {"splouch_notice_results": DISCLAIMER}, click=["results", ".notice-pill"]
    )
    assert after["results"] == "full"
    assert after["stored"]["results"] is None


@needs_js
def test_counting_off_forgets_the_attendance_fold():
    """So a server that turns counting back on says so in full."""
    (state,) = _fold(
        {"splouch_notice_results": DISCLAIMER, "splouch_notice_privacy": PRIVACY},
        privacy=False,
    )
    assert state["stored"] == {"results": DISCLAIMER, "privacy": None}
