"""Languages, labels, themes and event names — the parts that only need a code.

Split out of ``state`` because none of it depends on this server's runtime: give a
function a language code and it answers the same way on any machine. That is also
why the signatures take a code rather than reading ``settings`` — ``state`` keeps
the settings-aware wrappers (``load_locale()``, ``display_strings()`` …) that the
routes and templates already call, and supplies the default from ``settings``
there.

What both servers agree on — the label-style rule, the locale file readers, the
`GET /i18n/{lang}` body, the shipped palette — now lives once in
``shared/py/splouch_i18n.py`` and is re-exported below. What stays here is what
only a Pi does: reading themes off this machine, and decomposing an event name
into the parts a client renders (the relay forwards those parts, it never parses
them). See notes/cloud_parity.md.

Not to be confused with ``routes/i18n.py``, which is the HTTP endpoint that serves
:func:`i18n_bundle` to clients.
"""
import glob
import os
import re
import tomllib

import paths          # noqa: F401  — its import puts shared/py on sys.path
import splouch_i18n

# The half both servers share, re-exported so `state` and the routes keep reaching
# for `i18n.X` as they always have. The readers below bind this server's locales
# directory; the relay's copy binds its own.
from splouch_i18n import (HEADER_LABEL_BLUE, _HEADER_LABEL_WAS, DEFAULT_THEME_COLORS,
                          DEFAULT_THEME_FONTS, _FALLBACK_LABELS, STYLED_LABEL_KEYS,
                          resolve_labels)


def available_locales():
    """``(code, display name)`` for every language this server serves."""
    return splouch_i18n.available_locales(paths.LOCALES_DIR)


def locale_section(code, section):
    """One section of a served language file, as shipped."""
    return splouch_i18n.locale_section(paths.LOCALES_DIR, code, section)


def panel_section(code, section):
    """One section of a language's operator-panel file, English-merged per key."""
    return splouch_i18n.panel_section(paths.PANEL_LOCALES_DIR, code, section)


def i18n_bundle(code):
    """`GET /i18n/{lang}` (api.md §5.9) for this server's locale files.

    Read fresh, not cached: an operator editing a locale file on this Pi sees the
    change on the next request. The relay caches instead — it serves one fixed set
    of files to many phones — which is why the reader is the caller's to supply.
    """
    return splouch_i18n.i18n_bundle(
        code,
        read_section=locale_section,
        available=dict(available_locales()),
    )


_STROKE_ALIASES = [
    ('individual medley', 'medley'),
    ('breaststroke',      'breaststroke'),
    ('backstroke',        'backstroke'),
    ('butterfly',         'butterfly'),
    ('freestyle',         'freestyle'),
    ('medley',            'medley'),
    ('breast',            'breaststroke'),
    ('back',              'backstroke'),
    ('free',              'freestyle'),
    ('fly',               'butterfly'),
    ('im',                'medley'),
]

_GENDER_PATTERNS = [
    (r"\bwomen(?:'s)?\b", 'women'),
    (r"\bgirls?(?:'s)?\b", 'girls'),
    (r"\bmen(?:'s)?\b", 'men'),
    (r"\bboys?(?:'s)?\b", 'boys'),
    (r"\bmixed\b", 'mixed'),
]






def labels_for(code, style):
    """Column headers for one language, in one label style.

    Falls back to the built-in English table when the language ships no `[labels]`
    section at all — a board with blank headers is worse than an untranslated one.
    """
    labels = locale_section(code, 'labels')
    if not labels:
        return dict(_FALLBACK_LABELS)
    return resolve_labels(labels, style)


def display_strings(code):
    """Status strings for a native display, English-merged.

    An untranslated key falls back to English rather than rendering blank on a TV
    at the far end of the pool.
    """
    base = locale_section('en', 'display')
    return base if code == 'en' else {**base, **locale_section(code, 'display')}


def panel_strings(code, *sections):
    """Operator-panel strings, later sections winning on a clash.

    `[chrome]` underneath `[settings]` is the case this exists for: the sidebar is
    the same markup here and in the cloud's /admin, so its words live in one
    section both pages read, while a page-specific override stays possible.
    """
    out = {}
    for section in sections:
        out.update(panel_section(code, section))
    return out


def event_translations(code):
    """The `[event_name]` vocabulary a parsed event name is rendered against."""
    return locale_section(code, 'event_name')


def _read_locale_name(path, fallback):
    try:
        with open(path, 'rb') as f:
            return tomllib.load(f).get('meta', {}).get('name', fallback)
    except Exception:
        return fallback

def list_locales():
    result = []
    for path in sorted(glob.glob(os.path.join(paths.LOCALES_DIR, '*.toml'))):
        code = os.path.splitext(os.path.basename(path))[0]
        result.append((code, _read_locale_name(path, code)))
    return result


def _read_theme_name(path, fallback):
    try:
        with open(path, 'rb') as f:
            return tomllib.load(f).get('name', fallback)
    except Exception:
        return fallback

def list_builtin_themes():
    return [(os.path.splitext(os.path.basename(p))[0],
             _read_theme_name(p, os.path.splitext(os.path.basename(p))[0]))
            for p in sorted(glob.glob(os.path.join(paths.THEME_FOLDER, '*.toml')))]

def list_custom_themes():
    return [(os.path.splitext(os.path.basename(p))[0],
             _read_theme_name(p, os.path.splitext(os.path.basename(p))[0]))
            for p in sorted(glob.glob(os.path.join(paths.CUSTOM_THEME_FOLDER, '*.toml')))]

def load_theme(code):
    path = os.path.join(paths.CUSTOM_THEME_FOLDER, code + '.toml')
    if not os.path.exists(path):
        path = os.path.join(paths.THEME_FOLDER, code + '.toml')
    try:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
        colors = {**DEFAULT_THEME_COLORS, **data.get('colors', {})}
        fonts  = {**DEFAULT_THEME_FONTS,  **data.get('fonts',  {})}
        return colors, fonts
    except Exception:
        return dict(DEFAULT_THEME_COLORS), dict(DEFAULT_THEME_FONTS)


# Distances, with and without a unit. Metric only: nothing in this project renders
# yards, and matching `50y` here would print it as "50 m" — a wrong distance reads
# worse than a missing one.
_UNITS = r'(?:metres|meters|metre|meter|m)'
_DIST_WITH_UNIT = re.compile(r'\b(\d+\s*[xX]\s*\d+|\d+)\s*' + _UNITS + r'\b',
                             re.IGNORECASE)
_DIST_BARE      = re.compile(r'\b(\d+\s*[xX]\s*\d+|\d+)\b')


def parse_event_name(raw):
    """Decompose a raw event name into language-neutral parts.

    Keys, not words: ``stroke``, ``gender`` and ``age_key`` name entries in a
    locale's ``[event_name]`` table, so one parse renders in every language the
    server ships. That is what lets a spectator reading in Spanish at a French meet
    get a Spanish event name (docs/app.md `T-04`, `T-06`) — the alternative is three
    client repos re-implementing the regexes below and drifting.

    ``age`` carries a numeric band verbatim (``< 12``, ``12-13``) because a number
    needs no translation; ``age_key`` carries ``open`` / ``senior``, which do.
    """
    if not raw:
        return None
    s = raw.strip()

    gender = ''
    for pat, key in _GENDER_PATTERNS:
        if re.search(pat, s, re.IGNORECASE):
            gender = key
            break

    age, age_key = '', ''
    s_rest = s
    age_m = re.search(
        r'\b(\d+)\s*(?:[Uu](?:nder)?|&\s*[Uu]nder|[Aa]nd\s+[Uu]nder)\b'
        r'|\b[Uu](\d+)\b', s)
    if age_m:
        num    = age_m.group(1) or age_m.group(2)
        age    = '< ' + num
        s_rest = s[:age_m.start()] + s[age_m.end():]
    else:
        range_m = re.search(r'\b(\d{1,2}-\d{1,2})\b', s)
        if range_m:
            age    = range_m.group(1)
            s_rest = s[:range_m.start()] + s[range_m.end():]
        elif re.search(r'\bopen\b', s, re.IGNORECASE):
            age_key = 'open'
            s_rest  = re.sub(r'\bopen\b', '', s, flags=re.IGNORECASE)
        elif re.search(r'\bsenior\b', s, re.IGNORECASE):
            age_key = 'senior'
            s_rest  = re.sub(r'\bsenior\b', '', s, flags=re.IGNORECASE)

    is_relay = bool(re.search(r'\brelay\b', s_rest, re.IGNORECASE))

    # A distance with its unit attached first — `100m`, `4x50 m`, `200 metres` —
    # then a bare number as the fallback.
    #
    # The unit pass is not a nicety. `\b(\d+)\b` cannot match `100` in `100m`:
    # there is no word boundary between a digit and a letter, so the whole distance
    # vanished and `100m Freestyle` rendered as just "Freestyle" ("libre" in
    # French). Splash and Hy-Tek both export the glued form, so this was every
    # event at a real meet, not an edge case.
    #
    # Trying the unit first also settles which number is the distance when a name
    # carries more than one: `Mixed 13 & Over 4x50m Freestyle Relay` used to take
    # the `13` from the age band and call it the distance.
    dist   = ''
    dist_m = _DIST_WITH_UNIT.search(s_rest) or _DIST_BARE.search(s_rest)
    if dist_m:
        dist = re.sub(r'\s+', '', dist_m.group(1))

    stroke = ''
    for alias, key in _STROKE_ALIASES:
        if re.search(r'\b' + re.escape(alias) + r'\b', s_rest, re.IGNORECASE):
            stroke = key
            break

    return {'raw': raw, 'dist': dist, 'stroke': stroke, 'relay': is_relay,
            'gender': gender, 'age': age, 'age_key': age_key}


def compose_event_name(parts, ev):
    """Render parsed parts with one locale's ``[event_name]`` vocabulary.

    The other half of :func:`parse_event_name`, and the only half a client needs: a
    lookup and a join, no parsing. An unknown key renders as itself rather than
    blank, the same floor `T-10` sets for every other string.
    """
    if not parts:
        return ''
    if not ev:
        return parts.get('raw', '')
    unit = ev.get('unit', 'm')
    sep  = ev.get('separator', '  \u2014  ')

    left_parts = []
    if parts.get('dist'):
        left_parts.append(parts['dist'] + ' ' + unit)
    if parts.get('stroke'):
        left_parts.append(ev.get(parts['stroke'], parts['stroke']))
    if parts.get('relay') and ev.get('relay'):
        left_parts.append(ev['relay'])
    left = ' '.join(left_parts)

    age = parts.get('age') or (ev.get(parts['age_key'], parts['age_key'])
                               if parts.get('age_key') else '')
    gender = ev.get(parts['gender'], parts['gender']) if parts.get('gender') else ''
    right = ' '.join(p for p in [gender, age] if p)

    if left and right:
        return left + sep + right
    return left or right or parts.get('raw', '')


def translate_event_name(raw, ev):
    """One raw name rendered in one locale — ``compose(parse(raw))``."""
    if not ev or not raw:
        return raw
    return compose_event_name(parse_event_name(raw), ev)
