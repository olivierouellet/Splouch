"""Touch targets, announced choices and silent decoration (docs/app.md `X-05`–`X-07`).

Sizes were measured in Chrome, not guessed: every control a finger is expected to
hit is 44px in at least the direction it is crowded in. Where growing the drawn
control would change the page — a chip, a pill, the filter row — the target grows
instead, as an invisible band centred on it. These tests pin the rules that do it;
the measurement itself needs a real layout engine and is in `docs/web-parity.md`.
"""

import re

from test_cloud_picker_stores import picker as _picker
from test_scoreboard_base_shared import _render, _sched


def test_the_picker_controls_are_44px():
    html = _picker()
    assert re.search(r"#prefs-btn \{[^}]*width: 44px; height: 44px", html)
    assert re.search(r"\.notice-close \{[^}]*width: 44px; height: 44px", html)
    assert re.search(r"\.prefs-opt \{[^}]*min-height: 44px", html)
    assert (
        ".notice-pill::after { content: ''; position: absolute; inset: -8px 0; }"
        in html
    )


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
