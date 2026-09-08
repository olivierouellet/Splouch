import collections
import glob
import hashlib
import json
import os
import os.path
import queue
import re
import subprocess
import sys

import tomllib

from meet_parsers.hytek_parser import HytekParser
from meet_parsers.lenex_parser import load_lenex
from console_decoders import make_decoder

try:
    import pty, fcntl, termios
    _PTY_AVAILABLE = True
except ImportError:
    _PTY_AVAILABLE = False

# ── Paths ──────────────────────────────────────────────────────────────────────

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
SCOREBOARD_DIR    = os.path.expanduser('~/SplouchData')
_LEGACY_DATA_DIR  = os.path.expanduser('~/TremplinData')  # pre-Splouch; migrated on first run
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
LOGS_DIR               = os.path.join(SCOREBOARD_DIR, 'logs')
CUSTOM_LOCALE_FOLDER          = os.path.join(SCOREBOARD_DIR, 'locale')
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

# The systemd unit is named `splouch` once a full (post-rename) reinstall has run;
# until then the legacy `tremplin` unit is still in place. Detect by unit-file
# existence so the app restarts the right service and matches the sudoers grant
# during the Tremplin→Splouch transition.
SERVICE_NAME = ('splouch' if os.path.exists('/etc/systemd/system/splouch.service')
                else 'tremplin')

# ── Theme / locale defaults ────────────────────────────────────────────────────

DEFAULT_THEME_COLORS = {
    'bg': '#0d0d0d', 'header_bg': '#1a1a1a', 'header_border': '#2e2e2e',
    'header_label': '#ffffff', 'header_value': '#e0e0e0',
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
    'event': 'EVENT', 'heat': 'HEAT', 'lane': 'LANE',
    'place': 'PLACE', 'time': 'TIME', 'name': 'NAME', 'club': 'CLUB',
    'chrono': 'CHRONO',
}

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

# ── Settings ───────────────────────────────────────────────────────────────────

settings = {
    'meet_title': '',
    'serial_port': 'COM1',
    'username': 'score',
    'password': 'swimming',
    'splash_url': '',
    'locale': 'en',
    'label_style': 'long',
    'num_lanes': 8,
    'show_lane_header': True,
    'show_name_header': True,
    'show_club_header': True,
    'show_time_header': True,
    'show_delta_header': True,
    'show_position_header': True,
    'show_name': True,
    'show_club': True,
    'show_delta': True,
    'show_position': True,
    'show_podium': True,
    'results_sort': 'lane',
    'active_theme': 'default',
    'theme_colors': {
        'bg': '#0d0d0d', 'header_bg': '#1a1a1a', 'header_border': '#2e2e2e',
        'header_label': '#ffffff', 'header_value': '#e0e0e0',
        'th_text': '#666666', 'th_bg': '#1a1a1a',
        'row_odd': '#141414', 'row_even': '#202020', 'row_text': '#e0e0e0',
        'time': '#FFD700', 'delta_better': '#4CAF50', 'delta_worse': '#808080',
        'podium_gold': '#545454', 'podium_silver': '#424242', 'podium_bronze': '#343434',
        'connection_lost': '#ef5350', 'connection_lost_text': '#0d0d0d',
    },
    'theme_fonts': {'family': 'Overpass Mono', 'digits': 'DSEG7Classic', 'timing': 'Overpass Mono'},
    'intro_timeout': 300,
    'results_timeout': 300,
    'server_update_timeout': 300,
    'finish_debounce': 3.0,
    'split_min_duration': 1.0,
    'pool_length': 25,
    'touchpad_sides': 1,
    'carousel_interval': 10,
    'console_type': 'cts_gen6',
    # Per-meet cloud appearance overrides, keyed by meet_uid(). See
    # CLOUD_PROFILE_FIELDS / apply_meet_profile().
    'meet_profiles': {},
}

# ── Meet data ──────────────────────────────────────────────────────────────────
# The loaded meet (the LENEX dicts + the Hytek parser) lives in a single immutable
# snapshot, `meet`. Publishing a new meet swaps that one reference — atomic in
# CPython — so a reader on another thread (the worker mid-race, the relay) never
# sees a half-updated dict. Read it as `state.meet.start_list`,
# `state.meet.event_info`, …; a function that touches several fields should pin the
# snapshot once (`m = state.meet`) so it sees a consistent view even if a swap lands
# mid-read. Writers must go through set_lenex / load_event_info / clear_meet — the
# published snapshot is never mutated in place.

class _Meet:
    __slots__ = ('event_names', 'start_list', 'heat_times', 'meet_info',
                 'event_distances', 'event_info')

    def __init__(self, event_names=None, start_list=None, heat_times=None,
                 meet_info=None, event_distances=None, event_info=None):
        self.event_names     = event_names     or {}
        self.start_list      = start_list      or {}
        self.heat_times      = heat_times      or {}
        self.meet_info       = meet_info       or {}
        self.event_distances = event_distances or {}
        self.event_info      = event_info if event_info is not None else HytekParser()


meet = _Meet()


def set_lenex(data):
    """Publish a LENEX meet atomically (from a parsers.lenex_parser result)."""
    global meet
    meet = _Meet(event_names=data.event_names, start_list=data.start_list,
                 heat_times=data.heat_times, meet_info=data.meet_info,
                 event_distances=data.event_distances)


def load_event_info(path):
    """Load a Hytek CSV into a fresh parser and publish it atomically."""
    global meet
    p = HytekParser()
    p.load(path)
    meet = _Meet(event_info=p)


def clear_meet():
    """Drop the loaded meet atomically."""
    global meet
    meet = _Meet()

# Cloud-appearance overrides that travel per meet (not Pi-global). Keyed by
# meet_uid() in settings['meet_profiles']; the active values are mirrored into
# settings so the relay metadata and the Meet tab read them directly.
CLOUD_PROFILE_FIELDS = ('cloud_meet_title', 'meet_location', 'meet_sport',
                        'app_window_title', 'cloud_label_style',
                        'active_picker_image', 'active_home_icon')
_PROFILE_DEFAULTS = {'cloud_label_style': 'short'}


def _profile_field(f):
    return settings.get(f, _PROFILE_DEFAULTS.get(f, ''))


def uid_from_meet_info(meet_info, fallback_file=''):
    """meet_uid() for an arbitrary parsed meet, without touching global state.

    Lets callers (e.g. the "update file" guard) compute a candidate file's uid
    and compare it to the loaded meet before committing the swap.
    """
    name  = meet_info.get('name', '')
    dates = sorted(d for d in (s.get('date', '') for s in meet_info.get('sessions', [])) if d)
    basis = (name + '|' + '|'.join(dates)).strip('|')
    if not basis:
        basis = fallback_file
    if not basis:
        return ''
    return hashlib.sha1(basis.encode('utf-8')).hexdigest()[:12]


def meet_uid():
    """Stable identifier for the currently loaded meet.

    LENEX: a hash of the meet name plus its session dates — stable across
    re-exports and distinct for each day of a meet split into separate files.
    Otherwise (Hytek CSV, no sessions) falls back to the loaded file name.
    Returns '' when nothing is loaded.
    """
    return uid_from_meet_info(meet.meet_info, _active_meet_file)


def apply_meet_profile(uid):
    """Load a meet's saved cloud-appearance overrides into the active settings.

    A meet with no profile yet is seeded from the current values, so a freshly
    loaded meet starts from what's already on screen (usually only the title
    needs changing). Returns the active_home_icon so the caller can re-render it.
    """
    profiles = settings.setdefault('meet_profiles', {})
    if uid and uid in profiles:
        for f in CLOUD_PROFILE_FIELDS:
            settings[f] = profiles[uid].get(f, _PROFILE_DEFAULTS.get(f, ''))
    elif uid:
        profiles[uid] = {f: _profile_field(f) for f in CLOUD_PROFILE_FIELDS}
    return settings.get('active_home_icon', '')


def save_meet_profile(uid):
    """Persist the active cloud-appearance overrides into the meet's profile."""
    if not uid:
        return
    profiles = settings.setdefault('meet_profiles', {})
    profiles[uid] = {f: _profile_field(f) for f in CLOUD_PROFILE_FIELDS}

# ── Console log capture ──────────────────────────────────────────────────────
# Keep the most recent console output (the same lines that go to the journal) in
# a RAM ring buffer so the operator can download it or save it to flash on demand
# — without writing to flash continuously, which matters on SD-card Pis.

_log_ring = collections.deque(maxlen=10000)


class _LogTee:
    """Wrap a stream so complete lines are also captured into _log_ring."""

    def __init__(self, stream):
        self._stream = stream
        self._buf    = ''

    def write(self, s):
        self._stream.write(s)
        self._buf += s
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            _log_ring.append(line)
        return len(s)

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


def install_log_capture():
    """Tee stdout/stderr into the ring buffer. Idempotent; call once at startup."""
    if not isinstance(sys.stdout, _LogTee):
        sys.stdout = _LogTee(sys.stdout)
    if not isinstance(sys.stderr, _LogTee):
        sys.stderr = _LogTee(sys.stderr)


# ── Runtime state ──────────────────────────────────────────────────────────────

update      = {}

# The last results payload, published by the worker and read by the results-WS
# connect handler and the relay. Always reassigned as a whole dict (never mutated
# in place) so the rebind is an atomic swap — a reader gets the old or new dict
# whole, never half-built. Keep it that way: build a new dict, don't mutate this.
_last_results_snapshot      = {}
_results_prev_race_finished = False

# The worker's stop signal. Each worker captures my_gen = _worker_gen at start and
# runs while _worker_gen == my_gen; _restart_worker bumps it to stop the current
# worker. It must be a monotonic token, NOT a resettable bool: a superseded worker
# blocked in a long playback sleep could miss a bool that the new worker flips back,
# then keep feeding the decoder — two owners at once. A gen it captured can never be
# un-seen. Bumped with `+= 1` from more than one thread (concurrent _restart_worker
# calls), a non-atomic read-modify-write whose lost updates are harmless: only a
# *change* matters (it stops workers holding an older gen), never the exact value,
# and it only moves forward. Don't "fix" that with a lock.
_worker_gen         = 0
_test_session       = None
_record_handle      = None
_debug_serial       = False
_serial_status      = {'state': 'idle', 'msg': ''}
# Invalidation token for the finish/reset debounce. Bumped by the worker on every
# finish/un-finish transition and by the meet-load handler to cancel pending tasks
# across a meet change — so `+= 1` runs from two threads and a bump can be lost.
# Benign for the same reason as _worker_gen: a debounced task only fires if the gen
# it captured still matches, so any advance invalidates stale tasks; exactness is
# irrelevant. No lock needed.
_finish_timer_gen   = 0
_scoreboard_clients = {}
# Cap on update-log lines kept per display — see app.ws_scoreboard.
UPDATE_LOG_MAX = 40
_test_meet_active   = False
_overlay_active     = False
_cols_hidden        = False
# Is the timing console actually feeding this display? Published to clients as the
# `meet_live` event (docs/api.md §2), the local twin of the cloud's relay-connected
# flag. Keyed off packet arrival rather than the serial port's state: a cable can sit
# open against a powered-off console for hours, and `_serial_status` would still say
# 'open'. Test-session playback counts as live — it goes through _handle_packet too.
# The window matches the Qt display's own `_STALE` (scoreboard/client.py) so the two
# give up on the link at the same moment rather than contradicting each other.
MEET_LIVE_STALE     = 8      # seconds of silence before the link reads as dead
_last_packet_at     = 0.0    # time.monotonic() of the last decoded packet
_meet_live          = False  # last value broadcast — only transitions are emitted
_pty_fd             = None
_pty_pid            = None
main_thread         = None

# The decoder is owned by a single thread — the serial/playback worker. Other
# threads that need a decoder operation (WS adjust_splits/next_heat, the reset
# debounce) post a callable here instead of calling the decoder directly; the
# worker drains this queue between packets. So the decoder is never touched
# concurrently and needs no lock. See worker._drain_cmds and its command handlers.
_worker_cmds = queue.Queue()

# Lanes currently emitting lane_running=True — worker-thread state. Consoles only
# send the running flag on transitions (start/split/finish), streaming just the
# clock in between, so the debounced board-wipe must consult this rather than the
# momentary packet to avoid clearing a heat that is mid-race.
_running_lanes = set()

in_speed = 1.0

_update_in_progress    = False
_active_meet_file      = ''   # basename of the currently loaded meet file
_active_meet_uid       = ''   # meet_uid() of the currently loaded meet
_os_update_in_progress = False
_update_log_lines      = []
_update_log_done       = None
# True when an update stopped because the checkout has local edits. Drives the
# "Repair checkout" button on the Update panel — see routes/system._run_update.
_update_repair_needed  = False
_os_update_log_lines   = []
_os_update_log_done    = None

_rtc_in_progress       = False
_rtc_log_lines         = []
_rtc_log_done          = None

# ── Init ───────────────────────────────────────────────────────────────────────

def _ensure_data_dirs():
    for d in (SCOREBOARD_DIR, MEET_FOLDER, IMAGES_DIR, ICONS_DIR, PICKER_DIR,
              CUSTOM_SESSIONS_FOLDER, CUSTOM_LOCALE_FOLDER, CUSTOM_THEME_FOLDER,
              CUSTOM_DECODERS_FOLDER):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(settings_file) and os.path.exists(_settings_default):
        import shutil
        shutil.copy2(_settings_default, settings_file)

def _migrate_data_dir():
    """One-time rename of the pre-Splouch data dir (~/TremplinData → ~/SplouchData).

    Zero-privilege (the user owns it) and content-preserving — keeps settings,
    meets, recordings, custom themes/decoders across the rebrand. Runs before the
    dirs are (re)created so the destination doesn't yet exist.
    """
    if not os.path.exists(SCOREBOARD_DIR) and os.path.isdir(_LEGACY_DATA_DIR):
        try:
            os.rename(_LEGACY_DATA_DIR, SCOREBOARD_DIR)
        except OSError:
            pass

_migrate_data_dir()
_ensure_data_dirs()

# ── Locale / theme utilities ───────────────────────────────────────────────────

_git_describe_cache = None


def git_describe():
    """This checkout's ref, for comparing against a registered display.

    Mirrors ``scoreboard/version.py`` on the display side. They are deliberately
    separate: ``docs/api.md`` states there is no shared client library, and the
    display must not import from ``server/``.

    Cached, because the caller is an async route that HTMX polls: two `git`
    subprocesses per poll would run on the event loop and stall every WebSocket
    on the box. The value only changes on update, which restarts the service.
    """
    global _git_describe_cache
    if _git_describe_cache is not None:
        return _git_describe_cache

    def run(*args):
        try:
            r = subprocess.run(('git',) + args, cwd=app_dir, capture_output=True,
                               text=True, timeout=8)
        except (OSError, subprocess.SubprocessError):
            return ''
        return r.stdout.strip() if r.returncode == 0 else ''

    _git_describe_cache = {'version': run('describe', '--tags', '--always', '--dirty'),
                           'commit':  run('rev-parse', '--short', 'HEAD')}
    return _git_describe_cache


def _locale_path(code):
    """The file a language resolves to. Prefer :func:`_locale_section` for reading:
    it layers a custom file over the shipped one instead of replacing it."""
    custom = os.path.join(CUSTOM_LOCALE_FOLDER, code + '.toml')
    return custom if os.path.exists(custom) else os.path.join(LOCALES_DIR, code + '.toml')

def available_locales():
    """``(code, display name)`` for every language this Pi can serve.

    Custom files in ``scoreboard/locale/`` count: a club that adds one adds a
    language, and ``_locale_path`` already prefers it over the bundled file.
    """
    found = {}
    for folder in (LOCALES_DIR, CUSTOM_LOCALE_FOLDER):
        for path in sorted(glob.glob(os.path.join(folder, '*.toml'))):
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


def _locale_section(code, section):
    """One section of a language: the shipped file with this Pi's custom file over it.

    ``_locale_path`` picks a custom file *instead of* the shipped one, which meant a
    club overriding a single label silently dropped every other key in that
    language and fell back to English. Layering is a strict superset — a complete
    custom file resolves identically — and it is the model the cloud is sent, since
    :func:`label_overrides` ships a diff for the client to layer the same way. The
    two have to agree.
    """
    base = _toml_section(os.path.join(LOCALES_DIR, code + '.toml'), section)
    custom = os.path.join(CUSTOM_LOCALE_FOLDER, code + '.toml')
    return {**base, **_toml_section(custom, section)} if os.path.exists(custom) else base


def i18n_bundle(code=None):
    """Client-facing strings for one language — ``GET /i18n/{lang}``, api.md §5.9.

    Everything a client renders itself: its own chrome (``[mobile]``, ``[display]``)
    and both label styles, so language and short/long are one fetch rather than two
    axes the client has to reassemble. English-merged per key, the rule
    :func:`display_strings` already follows — a half-translated locale falls back
    word by word instead of rendering blank.

    Read through ``_locale_path``, so a Pi's custom wording is *served*, not diffed.
    The cloud cannot see those files, which is why the relay also ships
    :func:`label_overrides`.
    """
    code = code or settings.get('locale', 'en')
    if code not in dict(available_locales()):
        code = 'en'

    def merged(section):
        base = _locale_section('en', section)
        return dict(base) if code == 'en' else {**base, **_locale_section(code, section)}

    labels = merged('labels')
    return {
        'lang':    code,
        'mobile':  merged('mobile'),
        'display': merged('display'),
        # A custom file may give one style only; fall back to the other rather
        # than serving an empty header.
        'labels': {style: {k: v.get(style) or v.get('long') or v.get('short') or ''
                           for k, v in labels.items() if isinstance(v, dict)}
                   for style in ('short', 'long')},
    }


def label_overrides():
    """What this Pi's custom locale files change in ``[labels]``, lang → style → key.

    Only the difference. The bundled table is the same for every meet on a cloud and
    travels as ``GET /i18n/{lang}``; what cannot travel that way is a file sitting in
    one pool's ``scoreboard/locale/``, so that — and only that — rides in the relay
    payload (api.md §5.4). Normally empty.
    """
    out = {}
    for path in sorted(glob.glob(os.path.join(CUSTOM_LOCALE_FOLDER, '*.toml'))):
        code = os.path.splitext(os.path.basename(path))[0]
        try:
            with open(path, 'rb') as f:
                custom = tomllib.load(f).get('labels', {})
        except Exception:
            continue
        bundled = {}
        shipped = os.path.join(LOCALES_DIR, code + '.toml')
        if os.path.exists(shipped):
            try:
                with open(shipped, 'rb') as f:
                    bundled = tomllib.load(f).get('labels', {})
            except Exception:
                pass
        for key, val in custom.items():
            if not isinstance(val, dict):
                continue
            for style in ('short', 'long'):
                if style in val and val[style] != bundled.get(key, {}).get(style):
                    out.setdefault(code, {}).setdefault(style, {})[key] = val[style]
    return out


def load_locale(style=None):
    code  = settings.get('locale', 'en')
    style = style or settings.get('label_style', 'long')
    labels = _locale_section(code, 'labels')
    if not labels:
        return dict(_FALLBACK_LABELS)
    return {k: (v.get(style) or v.get('long') or v.get('short') or '')
            for k, v in labels.items() if isinstance(v, dict)}

def load_preview_strings():
    return _locale_section(settings.get('locale', 'en'), 'preview')

def _mobile_strings():
    return _locale_section(settings.get('locale', 'en'), 'mobile')

def display_strings(code=None):
    """Status strings for the native TV display, English-merged.

    Same fallback rule as :func:`settings_strings`: an untranslated key falls back
    to English rather than rendering blank on the TV. Driven by the ``locale``
    setting (Settings → Display → Scoreboard language), so the display follows the
    same language as the board it replaces.
    """
    code = code or settings.get('locale', 'en')

    base = _locale_section('en', 'display')
    return base if code == 'en' else {**base, **_locale_section(code, 'display')}

def _settings_section(code):
    return _locale_section(code, 'settings')

def settings_strings(code=None):
    """UI strings for the operator Settings panel, English-merged so any
    untranslated key falls back to English — templates can safely use
    ``{{ t.key }}`` without risking a blank label."""
    code = code or settings.get('locale', 'en')
    base = _settings_section('en')
    return base if code == 'en' else {**base, **_settings_section(code)}

def ui_locale(request):
    """Resolve the Settings-panel UI language.

    The ``ui_lang`` cookie — the user's explicit per-device override, set by the
    sidebar Panel-language selector — wins. Otherwise the panel follows the
    scoreboard ``locale`` setting, so everyone opening Settings on this server sees
    it in the meet's language by default, and a new install lands on ``en`` because
    that is what ``settings['locale']`` defaults to.

    Deliberately *not* Accept-Language: the panel is one operator's console for one
    meet, and a browser happening to prefer another language is a worse default than
    the language the meet is actually run in. Someone who wants otherwise sets the
    override, which is what it is for.
    """
    installed = {c for c, _ in list_locales()} | {c for c, _ in list_custom_locales()}
    cookie = request.cookies.get('ui_lang')
    if cookie in installed:
        return cookie
    code = settings.get('locale', 'en')
    return code if code in installed else 'en'

def _read_locale_name(path, fallback):
    try:
        with open(path, 'rb') as f:
            return tomllib.load(f).get('meta', {}).get('name', fallback)
    except Exception:
        return fallback

def list_locales():
    result = []
    for path in sorted(glob.glob(os.path.join(LOCALES_DIR, '*.toml'))):
        code = os.path.splitext(os.path.basename(path))[0]
        result.append((code, _read_locale_name(path, code)))
    return result


def provisioning_stale():
    """True when the repo needs a reinstall that the in-app update can't apply.

    Compares PROVISION_VERSION_FILE (shipped in the repo) against what install.sh
    last recorded in PROVISIONED_MARKER. A missing marker reads as 0, so the first
    upgrade to a provisioning-aware build correctly flags itself. Gated on
    ``INVOCATION_ID`` so it only fires for a real systemd-managed service, never a
    plain dev run.
    """
    if not os.environ.get('INVOCATION_ID'):
        return False

    def _read(path):
        try:
            with open(path) as f:
                return int(f.read().strip() or '0')
        except (OSError, ValueError):
            return 0

    want = _read(PROVISION_VERSION_FILE)
    return want > 0 and want > _read(PROVISIONED_MARKER)

def list_custom_locales():
    result = []
    for path in sorted(glob.glob(os.path.join(CUSTOM_LOCALE_FOLDER, '*.toml'))):
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
            for p in sorted(glob.glob(os.path.join(THEME_FOLDER, '*.toml')))]

def list_custom_themes():
    return [(os.path.splitext(os.path.basename(p))[0],
             _read_theme_name(p, os.path.splitext(os.path.basename(p))[0]))
            for p in sorted(glob.glob(os.path.join(CUSTOM_THEME_FOLDER, '*.toml')))]

def load_theme(code):
    path = os.path.join(CUSTOM_THEME_FOLDER, code + '.toml')
    if not os.path.exists(path):
        path = os.path.join(THEME_FOLDER, code + '.toml')
    try:
        with open(path, 'rb') as f:
            data = tomllib.load(f)
        colors = {**DEFAULT_THEME_COLORS, **data.get('colors', {})}
        fonts  = {**DEFAULT_THEME_FONTS,  **data.get('fonts',  {})}
        return colors, fonts
    except Exception:
        return dict(DEFAULT_THEME_COLORS), dict(DEFAULT_THEME_FONTS)

def load_event_translations():
    return _locale_section(settings.get('locale', 'en'), 'event_name')

def translate_event_name(raw, ev):
    if not ev or not raw:
        return raw
    s    = raw.strip()
    unit = ev.get('unit', 'm')
    sep  = ev.get('separator', '  —  ')

    gender = ''
    for pat, key in _GENDER_PATTERNS:
        if re.search(pat, s, re.IGNORECASE):
            gender = ev.get(key, key)
            break

    age   = ''
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
            age    = ev.get('open', 'Open')
            s_rest = re.sub(r'\bopen\b', '', s, flags=re.IGNORECASE)
        elif re.search(r'\bsenior\b', s, re.IGNORECASE):
            age    = ev.get('senior', 'Senior')
            s_rest = re.sub(r'\bsenior\b', '', s, flags=re.IGNORECASE)

    is_relay = bool(re.search(r'\brelay\b', s_rest, re.IGNORECASE))

    dist   = ''
    dist_m = re.search(r'\b(\d+[xX]\d+|\d+)\b', s_rest)
    if dist_m:
        dist = dist_m.group(1)

    stroke = ''
    for alias, key in _STROKE_ALIASES:
        if re.search(r'\b' + re.escape(alias) + r'\b', s_rest, re.IGNORECASE):
            stroke = ev.get(key, alias)
            break

    left_parts = []
    if dist:
        left_parts.append(dist + ' ' + unit)
    if stroke:
        left_parts.append(stroke)
    if is_relay and ev.get('relay'):
        left_parts.append(ev['relay'])
    left = ' '.join(left_parts)

    right = ' '.join(p for p in [gender, age] if p)

    if left and right:
        return left + sep + right
    return left or right or raw

# ── Settings loader ────────────────────────────────────────────────────────────

def merge_theme_defaults():
    """Put the built-in theme back underneath whatever was stored.

    ``settings.update()`` is shallow, so a saved ``settings.json`` replaces these
    dicts outright — and a colour added to the theme *after* that file was written
    would then be missing rather than defaulted. The Settings picker renders a
    missing colour as an empty value, which the browser shows as black and saves as
    black. `connection_lost` was the first key to arrive after installs existed in
    wild; this makes every future one safe too.
    """
    settings['theme_colors'] = {**DEFAULT_THEME_COLORS,
                                **(settings.get('theme_colors') or {})}
    settings['theme_fonts']  = {**DEFAULT_THEME_FONTS,
                                **(settings.get('theme_fonts') or {})}


def load_settings():
    try:
        with open(settings_file, 'rt') as f:
            settings.update(json.load(f))
    except Exception:
        pass
    merge_theme_defaults()
    csv_files = glob.glob(os.path.join(MEET_FOLDER, '*.csv'))
    if csv_files:
        try:
            load_event_info(max(csv_files, key=os.path.getmtime))
        except Exception:
            pass
    lxf_files = glob.glob(os.path.join(MEET_FOLDER, '*.lxf'))
    if lxf_files:
        try:
            set_lenex(load_lenex(max(lxf_files, key=os.path.getmtime)))
        except Exception:
            pass
    _decoder.configure(settings)


def save_settings():
    """Persist the settings dict to disk atomically.

    Serialize a top-level snapshot to a string first (so a concurrent write
    from another handler thread can't corrupt the output mid-dump), then write
    a temp file and os.replace it into place — a crash or power loss mid-write
    (a real risk on the Pi) can't leave a truncated settings.json.
    """
    data = json.dumps(dict(settings), sort_keys=True, indent=4)
    tmp = settings_file + '.tmp'
    with open(tmp, 'wt') as f:
        f.write(data)
    os.replace(tmp, settings_file)

# ── Decoder (initialized after settings dict is defined) ──────────────────────

_decoder = make_decoder(settings.get('console_type', 'cts_gen6'), settings)
