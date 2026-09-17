"""Languages, labels, themes and event names — the parts that only need a code.

Split out of ``state`` because none of it depends on this server's runtime: give a
function a language code and it answers the same way on any machine. That is also
why the signatures take a code rather than reading ``settings`` — ``state`` keeps
the settings-aware wrappers (``load_locale()``, ``display_strings()`` …) that the
routes and templates already call, and supplies the default from ``settings``
there.

The cloud relay carries its own copy of roughly this file (``cloud_server.py``
says so at its ``STYLED_LABEL_KEYS``). Nothing here reads ``settings``, a serial
port or a meet, so this is the module to move into ``shared/`` the day the two
deployables are made to share code — see notes/cloud_parity.md.

Not to be confused with ``routes/i18n.py``, which is the HTTP endpoint that serves
:func:`i18n_bundle` to clients.
"""
import glob
import os
import re
import tomllib

import paths

# The blue the top bar's labels and wall clock take. Shared with `schedule_event`
# by intent rather than accident: one accent colour across the board reads as a
# system, and this is the same blue the schedule already uses for event numbers.
HEADER_LABEL_BLUE = '#3b9eff'
# What `header_label` was before it became that blue. An install that still stores
# this never chose it — it is the old default — so `merge_theme_defaults` moves it
# on. See _migrate_header_label.
_HEADER_LABEL_WAS = '#ffffff'

DEFAULT_THEME_COLORS = {
    'bg': '#0d0d0d', 'header_bg': '#1a1a1a', 'header_border': '#2e2e2e',
    'header_label': HEADER_LABEL_BLUE, 'header_value': '#e0e0e0',
    'th_text': '#666666', 'th_bg': '#1a1a1a',
    'row_odd': '#141414', 'row_even': '#202020', 'row_text': '#e0e0e0',
    'time': '#FFD700', 'delta_better': '#4CAF50', 'delta_worse': '#808080',
    'podium_gold': '#545454', 'podium_silver': '#424242', 'podium_bronze': '#343434',
    # The board's one warning colour: the link-lost badge and the frozen race
    # clock behind it. Not used by any browser page — only the Qt display can
    # tell that the console has stopped talking to it.
    'connection_lost': '#ef5350',
    # Text on that badge. Defaults to the board background, which is what makes
    # a pill read as punched out of the board — but the pill behind it is a
    # warning colour, not a board colour, so it gets its own swatch.
    'connection_lost_text': '#0d0d0d',
    'schedule_event': '#3b9eff', 'schedule_time': '#FFD700',
    'schedule_name': '#e0e0e0', 'schedule_club': '#666666',
}
DEFAULT_THEME_FONTS = {'family': 'Overpass Mono', 'digits': 'DSEG7Classic', 'timing': 'Overpass Mono'}

_FALLBACK_LABELS = {
    'event': 'EVENT', 'heat': 'HEAT', 'lane': 'LN',
    'place': 'PL', 'time': 'TIME', 'name': 'NAME', 'club': 'CLUB',
    'chrono': 'CHRONO',
}

# Only these columns have a long form worth showing. The lane and place columns are
# the two narrow ones on every board we ship: a long word there either clips or
# shrinks the whole row to fit it, so they resolve short whatever `label_style` says
# (docs/app.md `T-09`). Keep this list and the cloud's copy in step.
STYLED_LABEL_KEYS = frozenset({'event', 'heat'})


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
        want = style if key in STYLED_LABEL_KEYS else 'short'
        out[key] = val.get(want) or val.get('long') or val.get('short') or ''
    return out

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


def available_locales():
    """``(code, display name)`` for every language this server serves.

    One file in ``shared/locales/`` is one language (docs/admin.md "Localisation").
    ``panel/`` is not a language list: it holds the operator-facing strings, and a
    language may omit its panel file and read English there.
    """
    found = {}
    for path in sorted(glob.glob(os.path.join(paths.LOCALES_DIR, '*.toml'))):
        code = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, 'rb') as f:
                data = tomllib.load(f)
        except Exception:
            continue
        found[code] = data.get('meta', {}).get('name', code)
    return sorted(found.items())


def _toml_section(path, section):
    try:
        with open(path, 'rb') as f:
            return tomllib.load(f).get(section, {})
    except Exception:
        return {}


def locale_section(code, section):
    """One section of a served language file, as shipped."""
    return _toml_section(os.path.join(paths.LOCALES_DIR, code + '.toml'), section)


def panel_section(code, section):
    """One section of a language's operator-panel file, English-merged per key.

    The panel is the operator's console, not what a spectator reads, so a language
    may ship without one: every key then renders in English, and a partial file
    degrades word by word (docs/admin.md "Localisation").
    """
    base = _toml_section(os.path.join(paths.PANEL_LOCALES_DIR, 'en.toml'), section)
    if code == 'en':
        return dict(base)
    return {**base, **_toml_section(os.path.join(paths.PANEL_LOCALES_DIR, code + '.toml'), section)}


def i18n_bundle(code):
    """Client-facing strings for one language — ``GET /i18n/{lang}``, api.md §5.9.

    Everything a client renders itself: its own chrome (``[mobile]``, ``[display]``)
    and both label styles, so language and short/long are one fetch rather than two
    axes the client has to reassemble. English-merged per key, the rule
    :func:`display_strings` already follows — a half-translated locale falls back
    word by word instead of rendering blank.

    The shipped table only: there is no per-Pi wording, so the Pi and the cloud
    serve the same body for the same language and a client may cache either.
    """
    if code not in dict(available_locales()):
        code = 'en'

    def merged(section):
        base = locale_section('en', section)
        return dict(base) if code == 'en' else {**base, **locale_section(code, section)}

    labels = merged('labels')
    return {
        'lang':    code,
        'mobile':  merged('mobile'),
        'display': merged('display'),
        # The vocabulary an event name is composed from, so a client that took
        # `event_name_parts` can render it in this language (api.md §5.1, §5.9).
        'event_name': merged('event_name'),
        # Both styles, so language and short/long are one fetch. `long` differs from
        # `short` only for STYLED_LABEL_KEYS; the narrow columns are short in both.
        'labels': {style: resolve_labels(labels, style)
                   for style in ('short', 'long')},
    }


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
