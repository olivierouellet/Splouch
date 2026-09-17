"""Where everything on this Pi lives, and the one-time setup that makes it exist.

Split out of ``state`` so the modules that only need to find a file — the i18n
loader, the route handlers — do not have to import the meet, the decoder and the
worker's runtime flags with it. Nothing here reads ``settings``: these paths are
what ``settings`` is *loaded from*, so the dependency only runs one way.

Importing this module creates the data directory (migrating the pre-Splouch one
if it is still there). That happens on import because every other module assumes
the directories are already present, and doing it here means it happens exactly
once however the app is started.
"""
import os
import secrets
import sys

app_dir           = os.path.dirname(os.path.abspath(__file__))
# The git checkout itself, one level up from server/. Every git command must run
# from here, not from app_dir: a git *pathspec* resolves relative to the working
# directory, so `git checkout -- uv.lock` from server/ silently fails with
# "pathspec did not match any file(s)" while non-pathspec commands like `git pull`
# quietly succeed by walking up to the repo root. That mismatch let the update's
# uv.lock guard fail unnoticed until a release actually changed uv.lock.
REPO_DIR          = os.path.dirname(app_dir)
# Cross-component assets (static/, locales/) live in the sibling `shared/` dir,
# one level up from server/ — they are also consumed by the cloud relay's build.
SHARED_DIR        = os.path.join(REPO_DIR, 'shared')
STATIC_DIR        = os.path.join(SHARED_DIR, 'static')
LOCALES_DIR       = os.path.join(SHARED_DIR, 'locales')
# Python shared with the cloud relay — `splouch_i18n`, the one copy of the label,
# locale and palette rules both servers answer from. Put on the path here, in the
# module whose job is knowing where things are, so neither server has to do
# sys.path surgery of its own at import time.
SHARED_PY_DIR     = os.path.join(SHARED_DIR, 'py')
if SHARED_PY_DIR not in sys.path:
    sys.path.insert(0, SHARED_PY_DIR)
# Operator-facing strings, one optional file per language, English-merged per key.
PANEL_LOCALES_DIR = os.path.join(LOCALES_DIR, 'panel')
SCOREBOARD_DIR    = os.path.expanduser('~/SplouchData')
settings_file     = os.path.join(SCOREBOARD_DIR, 'settings.json')
_settings_default = os.path.join(app_dir, 'settings.default.json')

SESSIONS_FOLDER        = os.path.join(app_dir, 'console_recordings')
CUSTOM_SESSIONS_FOLDER = os.path.join(SCOREBOARD_DIR, 'recorded')
IMAGES_DIR             = os.path.join(SCOREBOARD_DIR, 'images')
ICONS_DIR              = os.path.join(SCOREBOARD_DIR, 'icons')
HOME_ICON_PATH         = os.path.join(ICONS_DIR, 'home_icon.png')
HOME_ICON_512_PATH     = os.path.join(ICONS_DIR, 'home_icon_512.png')
PICKER_DIR             = os.path.join(SCOREBOARD_DIR, 'picker')
MEET_FOLDER            = os.path.join(SCOREBOARD_DIR, 'meet')
# A test session's start lists live here, never in MEET_FOLDER. Keeping the two
# apart is what lets a test run with the operator's meet still loaded: the real
# files are never touched, so nothing has to be put back, and a power cut mid-test
# leaves load_settings() finding the real meet exactly where it always was.
TEST_MEET_FOLDER       = os.path.join(SCOREBOARD_DIR, 'test_meet')
LOGS_DIR               = os.path.join(SCOREBOARD_DIR, 'logs')
THEME_FOLDER           = os.path.join(app_dir, 'themes')
CUSTOM_THEME_FOLDER    = os.path.join(SCOREBOARD_DIR, 'themes')
CUSTOM_DECODERS_FOLDER = os.path.join(SCOREBOARD_DIR, 'console_decoders')

# Provisioning version. install.sh records the version it fully provisioned into
# PROVISIONED_MARKER; the app compares it with PROVISION_VERSION_FILE (shipped in
# the repo) to nudge for a reinstall when an update pulled a change needing
# privileges/steps the in-app update can't self-apply. See
# install/scripts/refresh-service.sh.
PROVISION_VERSION_FILE = os.path.join(os.path.dirname(app_dir), 'install', 'PROVISION_VERSION')
PROVISIONED_MARKER     = os.path.join(SCOREBOARD_DIR, '.provisioned_version')

# Signing key for the admin session cookie. In the data dir, never in the repo:
# this file is what makes one Pi's cookies worthless on another, so a key living
# in a tracked source file would be the same key on every install ever made — and
# published, since the repo is public. The update path (`git pull`, and the
# cloud's `git reset --hard`) also wipes the working tree, so the repo could not
# hold it across an update even if it were secret.
SESSION_KEY_FILE       = os.path.join(SCOREBOARD_DIR, '.session_key')

# The systemd unit this server runs under. Written by
# install/scripts/refresh-service.sh, and named in the sudoers grant that lets the
# app restart itself.
SERVICE_NAME = 'splouch'


# ── First-run setup ───────────────────────────────────────────────────────────

def _ensure_data_dirs():
    for d in (SCOREBOARD_DIR, MEET_FOLDER, TEST_MEET_FOLDER, IMAGES_DIR,
              ICONS_DIR, PICKER_DIR, CUSTOM_SESSIONS_FOLDER, CUSTOM_THEME_FOLDER,
              CUSTOM_DECODERS_FOLDER):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(settings_file) and os.path.exists(_settings_default):
        import shutil
        shutil.copy2(_settings_default, settings_file)

_ensure_data_dirs()


def session_secret():
    """This install's own session-cookie signing key, created on first run.

    Read (or generated) once per process at startup. Anyone holding the key can
    mint a cookie that `require_login` accepts without ever seeing the password,
    so the value has to be unique per Pi and unreadable by other local accounts —
    hence 0600, and a fresh `token_hex` rather than anything derived from the
    settings, which are world-readable and change.

    Losing the file is harmless: the next start writes a new one and every open
    session simply has to sign in again.
    """
    try:
        with open(SESSION_KEY_FILE) as f:
            key = f.read().strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    # Create with the mode already set — writing first and chmod'ing after leaves
    # the key world-readable for the moment in between.
    fd = os.open(SESSION_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(key)
    return key
