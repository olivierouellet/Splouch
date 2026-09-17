"""The relay's language layer: the shared rules, plus a cache in front of them.

Everything both servers agree on — the label-style rule, the locale readers, the
`GET /i18n/{lang}` body, the shipped palette — is `shared/py/splouch_i18n.py`.
This module is the relay's half: it binds that server's locales directory and
caches the parsed TOML.

The cache is the one real difference between the two sides, and it is why the
shared code takes a reader instead of doing the reading. This process serves one
fixed set of locale files, baked into the image, to a lot of phones; the Pi
re-reads on every call because an operator can edit a locale file on the machine
and expects the next page to show it.
"""
import os

import cloud_paths
import splouch_i18n

# The shared rules, re-exported so `cloud_server` keeps reaching for them here.
from splouch_i18n import (STYLED_LABEL_KEYS, resolve_labels,
                          DEFAULT_THEME_COLORS as _DEFAULT_COLORS,
                          DEFAULT_THEME_FONTS as _DEFAULT_FONTS)

_locale_cache = {}
_panel_cache = {}


def available_locales():
    """`(code, display name)` for every language this relay serves."""
    return splouch_i18n.available_locales(cloud_paths.LOCALES_DIR)


def strings(lang, section):
    """One section of a served language file, cached after the first read.

    Falls back to English for a language this relay does not ship, so a stale
    bookmark or a hand-typed `?lang=` cannot produce an empty page.
    """
    if lang not in {code for code, _ in available_locales()}:
        lang = 'en'
    if lang not in _locale_cache:
        path = os.path.join(cloud_paths.LOCALES_DIR, f'{lang}.toml')
        _locale_cache[lang] = splouch_i18n.toml_file(path)
    return _locale_cache[lang].get(section, {})


def panel_strings(lang, section):
    """One section of a language's operator-panel file, English-merged per key.

    `panel/{lang}.toml` is optional: the admin page is not what a spectator reads,
    so a language shipped without one renders it in English (docs/admin.md).
    """
    def load(code):
        if code not in _panel_cache:
            path = os.path.join(cloud_paths.LOCALES_DIR, 'panel', f'{code}.toml')
            _panel_cache[code] = splouch_i18n.toml_file(path)
        return _panel_cache[code].get(section, {})

    base = load('en')
    return dict(base) if lang == 'en' else {**base, **load(lang)}


def i18n_bundle(lang):
    """`GET /i18n/{lang}` (api.md §5.9) — the same body the Pi serves.

    Same body, from the same code: `read_section` is this module's cached reader,
    and that is the whole of the difference between the two servers here.
    """
    return splouch_i18n.i18n_bundle(
        lang,
        read_section=strings,
        available={code for code, _ in available_locales()},
    )


def locale_name(code):
    for c, name in available_locales():
        if c == code:
            return name
    return code
