"""Touch targets, announced choices, silent decoration, motion and focus
(docs/app.md `X-05`–`X-07`, `X-09`, `X-10`).

Sizes were measured in Chrome, not guessed: every control a finger is expected to
hit is 44px in at least the direction it is crowded in. Where growing the drawn
control would change the page — a chip, a pill, the filter row — the target grows
instead, as an invisible band centred on it. These tests pin the rules that do it;
the measurement itself needs a real layout engine and is in `docs/web-parity.md`.
"""

import re

import pytest

from test_cloud_picker_stores import picker as _picker
from test_scoreboard_base_shared import _render, _sched


@pytest.mark.usefixtures("pg")  # the picker reads the store
def test_the_picker_controls_are_44px():
    html = _picker()
    assert re.search(r"#prefs-btn \{[^}]*width: 44px; height: 44px", html)
    assert re.search(r"\.notice-close \{[^}]*width: 44px; height: 44px", html)
    assert re.search(r"\.prefs-opt \{[^}]*min-height: 44px", html)
    assert (
        ".notice-pill::after { content: ''; position: absolute; inset: -8px 0; }"
        in html
    )


@pytest.mark.usefixtures("pg")  # the picker reads the store
def test_the_menu_says_whether_it_is_open():
    html = _picker()
    assert 'aria-expanded="false" aria-controls="prefs-panel"' in html
    assert "setAttribute('aria-expanded', open ? 'true' : 'false')" in html


def test_the_shell_tabs_are_tabs_and_say_which_is_selected():
    html = _render("cloud/templates", "mobile.html", app_title="C", meet_id="m", t={})
    assert '<nav role="tablist">' in html
    assert 'id="tab0" role="tab" aria-selected="true"' in html
    assert "tabs[current].setAttribute('aria-selected', 'true');" in html


def test_the_schedule_controls_grow_their_target_not_their_size():
    html = _sched("server/templates")
    assert re.search(
        r"#filter-btn::after, \.filter-chip::after,\s*#btn-upcoming::after, "
        r"#btn-all-heats::after, #btn-reset::after \{[^}]*height: 44px",
        html,
    )
    assert re.search(r"#close-filter-btn \{[^}]*width: 44px; height: 44px", html)


def test_the_schedule_says_its_toggles_and_names_its_buttons():
    html = _sched("server/templates")
    assert 'id="btn-upcoming" onclick="toggleUpcoming()" aria-pressed="false"' in html
    assert "setAttribute('aria-pressed', String(upcomingOnly))" in html
    assert 'id="close-filter-btn" onclick="closeFilter()" aria-label="Done"' in html
    assert "Filtrer" not in html, "the filter button's word was hard-coded in French"


@pytest.mark.usefixtures("pg")  # the picker reads the store
def test_decorative_glyphs_are_silent():
    sched = _sched("server/templates")
    for glyph in ("☰", "↺"):
        assert f'<span aria-hidden="true">{glyph}</span>' in sched
    assert 'class="chip-remove" aria-hidden="true"' in sched
    assert 'class="sug-check" aria-hidden="true"' in sched
    assert '<div id="ptr-wrap" aria-hidden="true">' in _picker()


def test_an_added_suggestion_is_inert():
    """`S-10`: marked and inert — to a finger, a keyboard and a screen reader."""
    sched = _sched("server/templates")
    assert "(already ? ' aria-disabled=\"true\"' : '')" in sched
    assert "item.getAttribute('aria-disabled') === 'true'" in sched


# ── X-09, X-10 ────────────────────────────────────────────────────────────────


@pytest.mark.usefixtures("pg")  # the picker reads the store
def test_decorative_motion_stands_still_on_request():
    """The live dot is decoration; the board's lane pulse and lock flash are
    information (`L-11`, `L-12`) and are deliberately left running."""
    assert (
        "@media (prefers-reduced-motion: reduce) { .status-dot { animation: none; } }"
        in _picker()
    )
    sched = _sched("server/templates")
    assert "behavior: still ? 'auto' : 'smooth'" in sched


@pytest.mark.usefixtures("pg")  # the picker reads the store
def test_focus_follows_a_fold():
    """`X-10`: the control that replaced itself hands focus to its replacement."""
    html = _picker()
    close = html.index("close.addEventListener('click'")
    assert "pill.focus();" in html[close : close + 200]
    reopen = html.index("pill.addEventListener('click'")
    assert "close.focus();" in html[reopen : reopen + 200]


# ── X-08: 200% zoom ───────────────────────────────────────────────────────────
#
# At 200% a 390px phone is a 195px page. Measured in Chrome: the picker scrolled
# sideways, its title ran under the menu button, the schedule cut names to "E…",
# and the shell's tabs lost their names when a short window hid the labels.


@pytest.mark.usefixtures("pg")  # the picker reads the store
def test_the_picker_never_scrolls_sideways_or_hides_under_its_button():
    html = _picker()
    assert "min-width: min(260px, 100%)" in html
    assert re.search(r"h1 \{[^}]*padding: 0 36px; text-align: center;", html)
    assert re.search(
        r"@media \(max-width: 359px\) \{\s*body \{ padding-top: 64px; \}", html
    )


def test_a_zoomed_schedule_wraps_rather_than_cuts():
    narrow = _sched("server/templates").split("@media (max-width: 359px) {", 1)[1]
    narrow = narrow[: narrow.index("\n        }\n")]
    assert ".heat-header { flex-wrap: wrap; }" in narrow
    assert ".swimmer-name, .sug-name { white-space: normal;" in narrow


def test_a_hidden_tab_label_is_still_the_tabs_name():
    html = _render("cloud/templates", "mobile.html", app_title="C", meet_id="m", t={})
    short = html.split("@media (max-height: 480px) {", 1)[1]
    label = short[
        short.index(".tab-label {") : short.index("}", short.index(".tab-label {"))
    ]
    assert "display: none" not in label
    assert "clip-path: inset(50%)" in label
