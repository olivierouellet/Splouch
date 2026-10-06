"""Languages, labels and the shipped palette — for both servers, from one file.

The Pi and the cloud relay each used to carry a copy of this. They agreed, but only
because someone kept them agreeing by hand: the same label-style rule written twice,
the same `GET /i18n/{lang}` body built twice, two palettes a test had to compare.
The XSS in the start list was the same shape of problem — one fact, several homes.

Nothing here knows which server it is running on. The file readers take the locales
directory, and `i18n_bundle` takes the reader itself, because the two sides
deliberately read differently: the Pi re-reads on every call so an operator editing
a locale file sees it immediately, while the relay caches — it serves one fixed set
of files to a lot of phones. That difference is the reason this is a parameter
rather than a copy.

Imported as `splouch_i18n` by `server/i18n.py` and `cloud/cloud_i18n.py`, each of
which keeps its own module as the place its server's habits live.
"""

import glob
import os
import tomllib

DEFAULT_THEME_COLORS = {
    "bg": "#0d0d0d",
    "header_bg": "#1a1a1a",
    "header_border": "#2e2e2e",
    # The accent blue: the top bar's labels and the wall clock. The same value as
    # `schedule_event` below by intent rather than accident — one accent colour
    # across the board reads as a system. `scoreboard/theme.py` carries it too, as
    # the fallback for the seconds before `/config` answers.
    "header_label": "#3b9eff",
    "header_value": "#e0e0e0",
    "th_text": "#666666",
    "th_bg": "#1a1a1a",
    "row_odd": "#141414",
    "row_even": "#202020",
    "row_text": "#e0e0e0",
    "time": "#FFD700",
    "delta_better": "#4CAF50",
    "delta_worse": "#808080",
    "podium_gold": "#545454",
    "podium_silver": "#424242",
    "podium_bronze": "#343434",
    # The board's one warning colour: the link-lost badge and the frozen race
    # clock behind it. Not used by any browser page — only the Qt display can
    # tell that the console has stopped talking to it.
    "connection_lost": "#ef5350",
    # Text on that badge. Defaults to the board background, which is what makes
    # a pill read as punched out of the board — but the pill behind it is a
    # warning colour, not a board colour, so it gets its own swatch.
    "connection_lost_text": "#0d0d0d",
    "schedule_event": "#3b9eff",
    "schedule_time": "#FFD700",
    "schedule_name": "#e0e0e0",
    "schedule_club": "#666666",
    # A lane's time on the Schedule, by how far it has come (docs/app.md `S-22`):
    # the seed from the meet file, the console's provisional finish, and the result
    # Meet Manager validated. Read by the Schedule pages only, never by the board.
    "schedule_seed": "#e0e0e0",
    "schedule_console": "#FFD700",
    "schedule_official": "#4ade80",
}
DEFAULT_THEME_FONTS = {
    "family": "Overpass Mono",
    "digits": "DSEG7Classic",
    "timing": "Overpass Mono",
}

# `server/themes/white.toml`, the server's own light board. Here as well as there
# because the cloud image ships `shared/` and not `server/`; a test keeps the two
# equal. The phone pages' Light (docs/app.md `P-15`) — the same palette the iOS and
# Android apps draw.
LIGHT_THEME_COLORS = {
    "bg": "#f8f8f8",
    "header_bg": "#ffffff",
    "header_border": "#dddddd",
    "header_label": "#333333",
    "header_value": "#111111",
    "th_text": "#888888",
    "th_bg": "#f0f0f0",
    "row_odd": "#f5f5f5",
    "row_even": "#ffffff",
    "row_text": "#111111",
    "time": "#0055aa",
    "delta_better": "#2e7d32",
    "delta_worse": "#757575",
    "podium_gold": "#d0d0d0",
    "podium_silver": "#dcdcdc",
    "podium_bronze": "#e8e8e8",
    "connection_lost": "#c62828",
    "connection_lost_text": "#f8f8f8",
    "schedule_event": "#0055cc",
    "schedule_time": "#0055aa",
    "schedule_name": "#111111",
    "schedule_club": "#888888",
    "schedule_seed": "#111111",
    "schedule_console": "#0055aa",
    "schedule_official": "#2e7d32",
}

# The reader's Appearance (docs/app.md `P-15`), first the default: the pages were
# dark before the choice existed, and a spectator who never opens the menu should
# see the pages they saw yesterday.
READER_THEMES = ("dark", "light", "auto")


def reader_palette(theme):
    """Template context for a phone page drawn in the reader's Appearance.

    The meet's own `theme_colors` are not consulted, as in the apps: a preference
    the next meet could overrule is not a preference. `auto` draws dark and hands
    the template the light palette too, for a `prefers-color-scheme` block — the
    server cannot see the device's setting, the page can.
    """
    theme = theme if theme in READER_THEMES else READER_THEMES[0]
    return {
        "reader_theme": theme,
        "theme_colors": LIGHT_THEME_COLORS
        if theme == "light"
        else DEFAULT_THEME_COLORS,
        "theme_colors_light": LIGHT_THEME_COLORS if theme == "auto" else None,
    }


_FALLBACK_LABELS = {
    "event": "EVENT",
    "heat": "HEAT",
    "lane": "LN",
    "place": "PL",
    "time": "TIME",
    "name": "NAME",
    "club": "CLUB",
    "chrono": "CHRONO",
}

# Only these columns have a long form worth showing. The lane and place columns are
# the two narrow ones on every board we ship: a long word there either clips or
# shrinks the whole row to fit it, so they resolve short whatever `label_style` says
# (docs/app.md `T-09`). Keep this list and the cloud's copy in step.
STYLED_LABEL_KEYS = frozenset({"event", "heat"})


def resolve_labels(labels, style):
    """Flatten a `[labels]` table to one string per key, in `style`.

    `style` reaches only STYLED_LABEL_KEYS; every other key resolves short. A custom
    file may define one form and not the other, so each key falls back to whatever it
    does have rather than serving an empty header.
    """
    out = {}
    for key, val in labels.items():
        if not isinstance(val, dict):
            continue
        want = style if key in STYLED_LABEL_KEYS else "short"
        out[key] = val.get(want) or val.get("long") or val.get("short") or ""
    return out


def toml_file(path):
    """A whole parsed TOML file, or ``{}`` if it cannot be read.

    Tolerant on purpose: a locale file that is missing, unreadable or malformed
    must degrade to English, never take down the page that asked for a word.

    Separate from :func:`toml_section` because the relay caches whole files — it
    reads one fixed set many times — while the Pi reads a section at a time so an
    edited file takes effect on the next request.
    """
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def toml_section(path, section):
    """One section of a TOML file, or ``{}`` if it cannot be read."""
    return toml_file(path).get(section, {})


def available_locales(locales_dir):
    """``(code, display name)`` for every language served from *locales_dir*.

    One file in ``shared/locales/`` is one language (docs/admin.md "Localisation").
    ``panel/`` is not a language list: it holds the operator-facing strings, and a
    language may omit its panel file and read English there.
    """
    found = {}
    for path in sorted(glob.glob(os.path.join(locales_dir, "*.toml"))):
        code = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError):
            continue
        found[code] = data.get("meta", {}).get("name", code)
    return sorted(found.items())


def locale_section(locales_dir, code, section):
    """One section of a served language file, as shipped."""
    return toml_section(os.path.join(locales_dir, code + ".toml"), section)


def panel_section(panel_dir, code, section):
    """One section of a language's operator-panel file, English-merged per key.

    The panel is the operator's console, not what a spectator reads, so a language
    may ship without one: every key then renders in English, and a partial file
    degrades word by word (docs/admin.md "Localisation").

    Takes the panel directory rather than deriving it from the locales one: the Pi
    names it separately (`paths.PANEL_LOCALES_DIR`), and collapsing the two would
    silently remove a seam that exists today.
    """
    base = toml_section(os.path.join(panel_dir, "en.toml"), section)
    if code == "en":
        return dict(base)
    return {**base, **toml_section(os.path.join(panel_dir, code + ".toml"), section)}


def i18n_bundle(code, read_section, available):
    """Client-facing strings for one language — ``GET /i18n/{lang}``, api.md §5.9.

    Everything a phone client renders itself: its chrome (``[mobile]``) and both
    label styles, so language and short/long are one fetch rather than two
    axes the client has to reassemble. English-merged per key — a half-translated
    locale falls back word by word instead of rendering blank.

    The shipped table only: there is no per-server wording, so the Pi and the cloud
    return the same body for the same language and a client may cache either. That
    promise is why this lives here rather than being written twice.

    *read_section* is ``(code, section) -> dict`` and *available* the codes this
    server has — supplied by the caller so each side keeps its own caching.
    """
    if code not in set(available):
        code = "en"

    def merged(section):
        base = read_section("en", section)
        return dict(base) if code == "en" else {**base, **read_section(code, section)}

    labels = merged("labels")
    return {
        "lang": code,
        "mobile": merged("mobile"),
        # Not `[display]`: those are the Qt TV's words, and it reads them from
        # `GET /config` → `display_strings` in the meet's language. No phone
        # renders one, so serving them here only padded every app's snapshot.
        # The vocabulary an event name is composed from, so a client that took
        # `event_name_parts` can render it in this language (api.md §5.1, §5.9).
        "event_name": merged("event_name"),
        # Both styles, so language and short/long are one fetch. `long` differs from
        # `short` only for STYLED_LABEL_KEYS; the narrow columns are short in both.
        "labels": {style: resolve_labels(labels, style) for style in ("short", "long")},
    }


def compose_event_name(parts, ev):
    """Render parsed parts with one locale's ``[event_name]`` vocabulary.

    The other half of the Pi's ``i18n.parse_event_name``, and the only half a client
    needs: a lookup and a join, no parsing. An unknown key renders as itself rather
    than blank, the same floor `T-10` sets for every other string. Shared because
    the relay composes too: a heat notification reads in the follower's language,
    not the meet's (docs/app.md `N-06`).
    """
    if not parts:
        return ""
    if not ev:
        return parts.get("raw", "")
    unit = ev.get("unit", "m")
    sep = ev.get("separator", "  \u2014  ")

    left_parts = []
    if parts.get("dist"):
        left_parts.append(parts["dist"] + " " + unit)
    if parts.get("stroke"):
        left_parts.append(ev.get(parts["stroke"], parts["stroke"]))
    if parts.get("relay") and ev.get("relay"):
        left_parts.append(ev["relay"])
    left = " ".join(left_parts)

    age = parts.get("age") or (
        ev.get(parts["age_key"], parts["age_key"]) if parts.get("age_key") else ""
    )
    gender = ev.get(parts["gender"], parts["gender"]) if parts.get("gender") else ""
    right = " ".join(p for p in [gender, age] if p)

    name = (
        left + sep + right if left and right else left or right or parts.get("raw", "")
    )
    if parts.get("round"):
        name += sep + ev.get(parts["round"], parts["round"])
    return name
