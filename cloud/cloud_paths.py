"""Where the relay keeps its data, and where it finds the shared assets.

Split out of ``cloud_server`` so the modules below it — the key store, the
analytics log, the locale reader — can find a file without importing the whole
application. Nothing here imports anything else of ours.

The in-container and from-source layouts differ: the Docker image copies
``shared/`` in next to this file, while a source checkout has it one level up.
Each constant resolves whichever exists, so the relay can be run and tested
without building an image.
"""
import os
import sys
import tempfile


DATA_DIR    = os.environ.get('DATA_DIR', '/data')
KEYS_FILE   = os.path.join(DATA_DIR, 'keys.json')
CREDS_FILE  = os.path.join(DATA_DIR, 'credentials.json')
RETAINED_DIR = os.path.join(DATA_DIR, 'retained')    # per-meet files: <id>.json + blobs
ANALYTICS_FILE = os.path.join(DATA_DIR, 'analytics.db')
_HERE       = os.path.dirname(__file__)
# Locales and static assets are the single canonical copies in the sibling
# shared/ dir (also used by the Pi server). The Docker image copies them next to
# cloud_server.py (COPY shared/locales/ locales/, COPY shared/static/ static/),
# so in-container they sit at _HERE/{locales,static}; running from source they
# live at ../shared/. Use whichever exists so cloud can be run/tested without Docker.
LOCALES_DIR = next(
    (p for p in (os.path.join(_HERE, 'locales'),
                 os.path.join(_HERE, os.pardir, 'shared', 'locales'))
     if os.path.isdir(p)),
    os.path.join(_HERE, 'locales'),
)
STATIC_DIR = next(
    (p for p in (os.path.join(_HERE, 'static'),
                 os.path.join(_HERE, os.pardir, 'shared', 'static'))
     if os.path.isdir(p)),
    os.path.join(_HERE, 'static'),
)
# scoreboard_base.html lives in shared/ because the Pi's live-mobile.html extends
# the same file — see notes/cloud_parity.md. Same in-container/from-source dance as
# above (COPY shared/templates/ templates_shared/).
# Python shared with the Pi server — `splouch_i18n`. The image copies shared/py/
# flat into /app beside this file; from source it is two levels up. Same
# in-container/from-source dance as the assets above, and put on the path here so
# `cloud_i18n` can just import it.
SHARED_PY_DIR = next(
    (p for p in (_HERE, os.path.join(_HERE, os.pardir, 'shared', 'py'))
     if os.path.isfile(os.path.join(p, 'splouch_i18n.py'))),
    _HERE,
)
if SHARED_PY_DIR not in sys.path:
    sys.path.insert(0, SHARED_PY_DIR)

SHARED_TEMPLATES_DIR = next(
    (p for p in (os.path.join(_HERE, 'templates_shared'),
                 os.path.join(_HERE, os.pardir, 'shared', 'templates'))
     if os.path.isdir(p)),
    os.path.join(_HERE, 'templates_shared'),
)


def atomic_write(path, text):
    """Write text to path atomically (temp file + os.replace). Blocking I/O.

    Every file this relay owns is rewritten in place while it is serving, so a
    torn write is a corrupt store on the next start rather than a lost update.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(text)
        os.replace(tmp, path)   # atomic — no torn file on crash
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

