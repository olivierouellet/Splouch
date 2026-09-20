"""Font registration and family resolution.

``shared/static/fonts/`` holds each face twice: **woff2** for the browser clients
(smaller, and all CSS can load) and **TTF** for Qt, which cannot read woff2 at
all. The TTFs are the upstream originals, unmodified — see that directory's
README for provenance and licences.

Family resolution runs in three steps:

1. Register every TTF/OTF in ``shared/static/fonts/`` (add a face there and it is
   picked up automatically; woff2 files are skipped).
2. Match the requested family — exactly if possible, otherwise ignoring spaces
   and case, because the browser's CSS name for a font and the font's own
   internal name do not always agree. See :func:`resolve_family`.
3. Fall back to the platform's monospace face, so a missing font degrades to a
   readable board rather than Qt's proportional default.

Resolution is cached: it is called once per label restyle, and querying the font
database is not free.
"""
import os

_APP_FONTS_LOADED = False
_RESOLVED: dict[str, str] = {}

# Where the browser clients keep their faces; shared with the Qt display.
_FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'shared', 'static', 'fonts')


def load_app_fonts() -> list[str]:
    """Register bundled TTF/OTF faces with Qt. Returns the families added.

    Safe to call more than once; only the first call touches the font database.
    Call it after the QApplication exists — Qt has no font database before that.
    """
    global _APP_FONTS_LOADED
    if _APP_FONTS_LOADED:
        return []
    _APP_FONTS_LOADED = True

    from PySide6.QtGui import QFontDatabase

    added = []
    try:
        names = sorted(os.listdir(_FONT_DIR))
    except OSError:
        return added

    for name in names:
        if not name.lower().endswith(('.ttf', '.otf')):
            continue
        font_id = QFontDatabase.addApplicationFont(os.path.join(_FONT_DIR, name))
        if font_id != -1:
            added.extend(QFontDatabase.applicationFontFamilies(font_id))
    if added:
        # Registering families changes what resolves, so any answer given before
        # now is stale — a lookup made first would have cached the monospace
        # fallback and kept returning it for the life of the process.
        _RESOLVED.clear()
        print(f'[scoreboard] loaded bundled fonts: {", ".join(sorted(set(added)))}',
              flush=True)
    return added


def _squash(name: str) -> str:
    """Normalise a family name for matching: no spaces, no case."""
    return name.replace(' ', '').replace('-', '').lower()


def resolve_family(family: str) -> str:
    """Return the Qt family name to use for *family*, else a monospace fallback.

    The name the server sends is not always the name Qt knows the font by. The
    browser declares its own via ``@font-face { font-family: 'DSEG7Classic' }``,
    which is an arbitrary CSS label with no obligation to match anything inside
    the file — while Qt reads the family from the font's ``name`` table, where
    that same font calls itself ``DSEG7 Classic``. An exact-match-only lookup
    silently drops both DSEG faces to the fallback.

    So: exact match first, then a space/case-insensitive match, then fall back.
    The loose match is general rather than a lookup table of known aliases, so a
    font added to the settings dropdown later needs no change here.

    The fallback is deliberately monospace — a scoreboard in a proportional face
    is unreadable at a distance, because the digits jitter as times tick.
    """
    if not family:
        family = 'monospace'
    if family in _RESOLVED:
        return _RESOLVED[family]

    resolved = family
    try:
        from PySide6.QtGui import QFontDatabase
        # Static in Qt 6 — PyQt5 required an instance, PySide6 does not.
        families = QFontDatabase.families()
        if family not in families:
            wanted = _squash(family)
            match = next((f for f in families if _squash(f) == wanted), None)
            if match:
                resolved = match
                print(f'[scoreboard] font "{family}" matched as "{match}"', flush=True)
            else:
                from PySide6.QtGui import QFont
                fallback = QFont()
                fallback.setStyleHint(QFont.StyleHint.Monospace)
                fallback.setFamily('monospace')
                resolved = QFont(fallback.defaultFamily()).family() or 'monospace'
                print(f'[scoreboard] font "{family}" unavailable — using {resolved}',
                      flush=True)
    except Exception:
        pass

    _RESOLVED[family] = resolved
    return resolved
