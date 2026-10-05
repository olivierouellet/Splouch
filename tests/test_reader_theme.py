"""The reader's Appearance on the web (`app.md` `P-15`).

Dark, Light or Automatic, chosen in the cloud picker's menu and holding on every
page of every meet the cloud serves, as it does in the apps. What this file guards:
the light palette is the server's own `white.toml`, a phone page draws the reader's
palette and never the meet's, Automatic hands the page both, the picker offers the
choice where the label control used to be, and the Pi — which has no picker — keeps
the operator's colours.
"""

import os
import re
import tomllib
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

import cloud_server as cs
import splouch_i18n
import state
import web

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Q:
    """A request with cookies and query params, which is all these read."""

    def __init__(self, cookies=None, **params):
        self.cookies = cookies or {}
        self.query_params = params


def _env(own_dir):
    """The loader both servers use: their own templates, then the shared ones."""
    return Environment(
        loader=FileSystemLoader(
            [os.path.join(REPO, own_dir), os.path.join(REPO, "shared", "templates")]
        )
    )


# ── The palettes ──────────────────────────────────────────────────────────────


def test_light_is_the_servers_white_theme():
    """A copy, because the cloud image ships `shared/` and not `server/` — so this
    is what keeps the copy honest."""
    path = os.path.join(REPO, "server", "themes", "white.toml")
    with open(path, "rb") as f:
        white = tomllib.load(f)["colors"]
    assert white == splouch_i18n.LIGHT_THEME_COLORS


def test_both_palettes_carry_every_key_a_page_reads():
    assert set(splouch_i18n.LIGHT_THEME_COLORS) == set(
        splouch_i18n.DEFAULT_THEME_COLORS
    )


def test_the_published_palettes_are_the_served_ones():
    """`api.md` §6.1 is what the apps copy their two palettes from. A hand-copied
    `header_label` once drifted to white in both of them, so the table is held to
    the code key for key, the Qt display's own two keys aside."""
    text = Path(os.path.join(REPO, "docs", "api.md")).read_text(encoding="utf-8")
    section = text.split("### 6.1 Default palettes and faces", 1)[1].split("\n## ", 1)[
        0
    ]
    rows = re.findall(
        r"^\| `(\w+)` \| `(#[0-9A-Fa-f]{6})` \| `(#[0-9A-Fa-f]{6})` \|$",
        section,
        re.MULTILINE,
    )
    assert rows, "api.md §6.1 lost its palette table"
    qt_only = {"connection_lost", "connection_lost_text"}
    for name, table in (
        ("dark", splouch_i18n.DEFAULT_THEME_COLORS),
        ("light", splouch_i18n.LIGHT_THEME_COLORS),
    ):
        want = {k: v for k, v in table.items() if k not in qt_only}
        got = {k: (dark if name == "dark" else light) for k, dark, light in rows}
        assert got == want, name


@pytest.mark.parametrize(
    "cookie, colors, light",
    [
        ("", "dark", None),
        ("dark", "dark", None),
        ("light", "light", None),
        ("auto", "dark", "light"),
        # A value this build does not know reads as no choice, which is Dark.
        ("sepia", "dark", None),
    ],
)
def test_the_cookie_picks_the_palette(cookie, colors, light):
    palettes = {
        "dark": splouch_i18n.DEFAULT_THEME_COLORS,
        "light": splouch_i18n.LIGHT_THEME_COLORS,
        None: None,
    }
    ctx = cs._client_palette(_Q({"splouch_theme": cookie} if cookie else None))
    assert ctx["theme_colors"] is palettes[colors]
    assert ctx["theme_colors_light"] is palettes[light]
    assert ctx["reader_theme"] == (
        cookie if cookie in splouch_i18n.READER_THEMES else "dark"
    )


def test_the_meet_palette_is_not_what_a_phone_draws():
    """The routes pass the reader's palette, never `settings.theme_colors` — a
    preference the next meet could overrule is not a preference."""
    # The picker is the control plane's; the shell and the three tabs, a worker's.
    src = "".join(
        Path(REPO, "cloud", name).read_text(encoding="utf-8")
        for name in ("cloud_control.py", "cloud_server.py")
    )
    assert 's.get("theme_colors"' not in src
    assert src.count("**_client_palette(request)") == 5, (
        "the picker, the shell and the three tabs"
    )


# ── The phone pages ───────────────────────────────────────────────────────────

_LABELS = dict.fromkeys(
    (
        "event",
        "heat",
        "lane",
        "name",
        "club",
        "time",
        "delta",
        "place",
        "waiting_results",
        "no_schedule",
    ),
    "x",
)


def _board(template, theme):
    return (
        _env("cloud/templates")
        .get_template(template)
        .render(
            meet_id="abc",
            num_lanes=6,
            labels=_LABELS,
            theme_fonts=state.DEFAULT_THEME_FONTS,
            heats=[],
            t={},
            **splouch_i18n.reader_palette(theme),
        )
    )


def _root_bg(html):
    match = re.search(r"--color-bg:\s*(#[0-9a-fA-F]+)", html)
    assert match, "no --color-bg in the page"
    return match.group(1)


@pytest.mark.parametrize(
    "template", ["live-mobile.html", "results.html", "schedule.html"]
)
def test_each_tab_draws_the_choice(template):
    assert _root_bg(_board(template, "dark")) == "#0d0d0d"
    assert _root_bg(_board(template, "light")) == "#f8f8f8"
    for theme in ("dark", "light"):
        assert "prefers-color-scheme" not in _board(template, theme)


@pytest.mark.parametrize(
    "template", ["live-mobile.html", "results.html", "schedule.html"]
)
def test_automatic_lets_the_device_pick(template):
    """The server cannot see the device's setting, so it draws dark and carries the
    light palette behind the media query."""
    html = _board(template, "auto")
    assert _root_bg(html) == "#0d0d0d"
    block = html[html.index("@media (prefers-color-scheme: light)") :]
    assert _root_bg(block) == "#f8f8f8"


def _shell(own_dir, **ctx):
    return (
        _env(own_dir)
        .get_template("mobile.html")
        .render(app_title="Coupe", t={}, lang="en", **ctx)
    )


def test_the_shell_follows_the_choice():
    dark = _shell("cloud/templates", meet_id="abc", reader_theme="dark")
    light = _shell("cloud/templates", meet_id="abc", reader_theme="light")
    auto = _shell("cloud/templates", meet_id="abc", reader_theme="auto")
    assert "--shell-bg: #000000" in dark and "--shell-icon: invert(1)" in dark
    assert "--shell-bg: #f8f8f8" in light and "--shell-icon: none" in light
    assert "prefers-color-scheme: light" in auto
    assert auto.count('name="theme-color"') == 2


def test_the_pi_shell_is_the_shell_it_always_was():
    """No picker, no choice: without a `reader_theme` the shell is Dark."""
    html = _shell("server/templates")
    assert "--shell-bg: #000000" in html
    assert "prefers-color-scheme" not in html


def test_the_pi_keeps_the_operators_palette(monkeypatch):
    """Nothing sets `splouch_theme` on a Pi, and a stray one is not read: its phone
    pages keep drawing `theme_colors` from the operator's settings."""
    ctx = web.client_strings(_Q({"splouch_theme": "light"}))
    assert "theme_colors" not in ctx and "reader_theme" not in ctx


# ── The picker ────────────────────────────────────────────────────────────────


def _picker(**ctx):
    return (
        _env("cloud/templates")
        .get_template("picker.html")
        .render(
            meets=[],
            t={"appearance": "Apparence", "appearance_light": "Clair"},
            locales=[("en", "English")],
            reader_themes=splouch_i18n.READER_THEMES,
            **ctx,
        )
    )


def test_the_picker_offers_the_choice_under_the_language():
    """In settings' Display section (`P-19`), where the column-labels control was."""
    html = _picker(reader_theme="dark")
    panel = html[html.index('<dialog id="settings"') : html.index("</dialog>")]
    assert panel.index('data-pref="lang"') < panel.index("Apparence")
    themes = re.findall(r'data-pref="theme" data-value="(\w+)">([^<]+)<', panel)
    assert [v for v, _ in themes] == ["dark", "light", "auto"]
    # The served word where there is one, the English floor where there is not.
    assert dict(themes)["light"] == "Clair" and dict(themes)["auto"] == "Automatic"


@pytest.mark.parametrize("theme", ["dark", "light", "auto"])
def test_the_picker_is_drawn_in_the_choice(theme):
    assert f'data-theme="{theme}"' in _picker(reader_theme=theme)


def test_a_picker_without_a_choice_is_dark():
    assert 'data-theme="dark"' in _picker()
