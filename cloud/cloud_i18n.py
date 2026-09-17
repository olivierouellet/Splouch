"""Languages, labels and theme defaults for the relay.

The same job `server/i18n.py` does on the Pi, and — for the parts that matter —
the same code. The two deployables still carry a copy each: this one reads the
locale files out of the image, that one out of the checkout, and neither imports
the other. `STYLED_LABEL_KEYS` below says so, and so does notes/cloud_parity.md.

What is *not* here is the deliberate half of that split. Deciding which language a
given visitor gets — `_browser_lang`, `_picker_lang`, `_admin_lang`, the `lang`
cookie — is relay policy that needs a `Request` and the admin's stored preference,
so it stays in `cloud_server`. Everything in this module answers from a language
code alone, which is what would let it be replaced by a shared module rather than
maintained twice.
"""
import glob
import os
import tomllib

import cloud_paths


_locale_cache = {}

def available_locales():
    locales = []
    for path in sorted(glob.glob(os.path.join(cloud_paths.LOCALES_DIR, '*.toml'))):
        code = os.path.splitext(os.path.basename(path))[0]
        with open(path, 'rb') as f:
            name = tomllib.load(f).get('meta', {}).get('name', code)
        locales.append((code, name))
    return locales

def strings(lang, section):
    """One section of a served language file (`shared/locales/{lang}.toml`)."""
    available = {code for code, _ in available_locales()}
    if lang not in available:
        lang = 'en'
    if lang not in _locale_cache:
        with open(os.path.join(cloud_paths.LOCALES_DIR, f'{lang}.toml'), 'rb') as f:
            _locale_cache[lang] = tomllib.load(f)
    return _locale_cache[lang].get(section, {})


_panel_cache = {}

def panel_strings(lang, section):
    """One section of a language's operator-panel file, English-merged per key.

    `panel/{lang}.toml` is optional: the admin page is not what a spectator reads,
    so a language shipped without one renders it in English (docs/admin.md).
    """
    def load(code):
        if code not in _panel_cache:
            path = os.path.join(cloud_paths.LOCALES_DIR, 'panel', f'{code}.toml')
            try:
                with open(path, 'rb') as f:
                    _panel_cache[code] = tomllib.load(f)
            except OSError:
                _panel_cache[code] = {}
        return _panel_cache[code].get(section, {})
    base = load('en')
    return dict(base) if lang == 'en' else {**base, **load(lang)}

# Only these columns have a long form worth showing — the lane and place columns are
# too narrow for one on every board we ship (docs/app.md `T-09`). The Pi says the same
# thing in `state.STYLED_LABEL_KEYS`; the two deployables share no code, so both carry
# it and both must move together.
STYLED_LABEL_KEYS = frozenset({'event', 'heat'})


def resolve_labels(labels, style):
    """Flatten a `[labels]` table to one string per key, in `style`.

    `style` reaches only STYLED_LABEL_KEYS; every other key resolves short.
    """
    out = {}
    for key, val in labels.items():
        if not isinstance(val, dict):
            continue
        want = style if key in STYLED_LABEL_KEYS else 'short'
        out[key] = val.get(want) or val.get('long') or val.get('short') or ''
    return out


def i18n_bundle(lang):
    """Client-facing strings for one language — ``GET /i18n/{lang}``, api.md §5.9.

    The same body the Pi serves for the same language — there is no per-Pi wording
    — English-merged per key so a half-translated locale degrades word by word.
    """
    if lang not in {code for code, _ in available_locales()}:
        lang = 'en'

    def merged(section):
        base = strings('en', section)
        return dict(base) if lang == 'en' else {**base, **strings(lang, section)}

    labels = merged('labels')
    return {
        'lang':    lang,
        'mobile':  merged('mobile'),
        'display': merged('display'),
        # The vocabulary an event name is composed from, so a client that took
        # `event_name_parts` can render it in this language (api.md §5.1, §5.9).
        'event_name': merged('event_name'),
        'labels': {style: resolve_labels(labels, style)
                   for style in ('short', 'long')},
    }


def locale_name(code):
    for c, name in available_locales():
        if c == code:
            return name
    return code

# ── Theme defaults (fallback when Pi hasn't sent settings yet) ─────────────────

_DEFAULT_COLORS = {
    'bg': '#0d0d0d', 'header_bg': '#1a1a1a', 'header_border': '#2e2e2e',
    # The accent blue, same value as `schedule_event` below and for the same reason
    # — one accent across the board. Must match server/state.py, as the note there.
    'header_label': '#3b9eff', 'header_value': '#e0e0e0',
    'th_text': '#666666', 'th_bg': '#1a1a1a',
    'row_odd': '#141414', 'row_even': '#202020', 'row_text': '#e0e0e0',
    'time': '#FFD700', 'delta_better': '#4CAF50', 'delta_worse': '#808080',
    'podium_gold': '#545454', 'podium_silver': '#424242', 'podium_bronze': '#343434',
    # Schedule tab. Must match server/state.py's DEFAULT_THEME_COLORS: the page is
    # shared, so a key missing here would render an empty CSS variable on the cloud
    # for any relay that sends a partial palette.
    'schedule_event': '#3b9eff', 'schedule_time': '#FFD700',
    'schedule_name': '#e0e0e0', 'schedule_club': '#666666',
}
_DEFAULT_FONTS = {
    'family': 'Overpass Mono', 'digits': 'DSEG7Classic', 'timing': 'Overpass Mono',
}
