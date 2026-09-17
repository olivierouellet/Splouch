import collections
import glob
import hashlib
import json
import os
import os.path
import queue
import re
import secrets
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
# Operator-facing strings, one optional file per language, English-merged per key.
PANEL_LOCALES_DIR = os.path.join(LOCALES_DIR, 'panel')
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

# The systemd unit is named `splouch` once a full (post-rename) reinstall has run;
# until then the legacy `tremplin` unit is still in place. Detect by unit-file
# existence so the app restarts the right service and matches the sudoers grant
# during the Tremplin→Splouch transition.
SERVICE_NAME = ('splouch' if os.path.exists('/etc/systemd/system/splouch.service')
                else 'tremplin')

# ── Theme / locale defaults ────────────────────────────────────────────────────

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

# ── Settings ───────────────────────────────────────────────────────────────────

# How long the server waits, after the console reports every lane finished, before
# publishing the results snapshot (worker.py). The Timing pane warns when the running
# value is off this and offers it back, so it is named rather than repeated.
FINISH_DEBOUNCE_DEFAULT = 3.0

# How long a lane's clock must stay stopped before the decoder counts a completed
# length. The Timing pane warns when the running value is off this and offers it
# back, so it is named rather than repeated.
SPLIT_MIN_DEFAULT = 1.0

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
        'header_label': HEADER_LABEL_BLUE, 'header_value': '#e0e0e0',
        'th_text': '#666666', 'th_bg': '#1a1a1a',
        'row_odd': '#141414', 'row_even': '#202020', 'row_text': '#e0e0e0',
        'time': '#FFD700', 'delta_better': '#4CAF50', 'delta_worse': '#808080',
        'podium_gold': '#545454', 'podium_silver': '#424242', 'podium_bronze': '#343434',
        'connection_lost': '#ef5350', 'connection_lost_text': '#0d0d0d',
    },
    'theme_fonts': {'family': 'Overpass Mono', 'digits': 'DSEG7Classic', 'timing': 'Overpass Mono'},
    'finish_debounce': FINISH_DEBOUNCE_DEFAULT,
    'split_min_duration': SPLIT_MIN_DEFAULT,
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
# A recording's start lists are loaded into `meet` while this is set. The real
# meet's files, `_active_meet_file` and its cloud profile are untouched throughout,
# so ending the test only has to reload from disk — see worker._cleanup_test_meet.
_test_meet_active   = False
_test_meet_name     = ''   # basename of the start lists the test is using
# Keep this test session off the cloud: LAN browsers and the Qt display see it,
# the relay does not. Forced on whenever a real meet is loaded, so a replay can
# never publish under a live meet's identity — see routes/debug._test_play.
_test_local_only        = False
# Whether the relay was running when we stopped it for a local-only test. An
# operator who had the cloud switched off must not find it switched on afterwards.
_test_relay_was_running = False
# The results snapshot from before the test, restored when it ends. The relay
# re-sends this on every reconnect, so without it a replay's results would reach
# the cloud on the next connect — long after the test was over.
_test_saved_results     = None
# The event and heat the console was on before a test session started, put back when
# it ends. Not cleared: a Quantum announces its heat once, when it is readied, so a
# board told to forget would have nothing to show until the next one — see
# worker.restore_current_heat.
_test_saved_heat        = None
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
    for d in (SCOREBOARD_DIR, MEET_FOLDER, TEST_MEET_FOLDER, IMAGES_DIR,
              ICONS_DIR, PICKER_DIR, CUSTOM_SESSIONS_FOLDER, CUSTOM_THEME_FOLDER,
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


def available_locales():
    """``(code, display name)`` for every language this server serves.

    One file in ``shared/locales/`` is one language (docs/admin.md "Localisation").
    ``panel/`` is not a language list: it holds the operator-facing strings, and a
    language may omit its panel file and read English there.
    """
    found = {}
    for path in sorted(glob.glob(os.path.join(LOCALES_DIR, '*.toml'))):
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
    """One section of a served language file, as shipped."""
    return _toml_section(os.path.join(LOCALES_DIR, code + '.toml'), section)


def _panel_section(code, section):
    """One section of a language's operator-panel file, English-merged per key.

    The panel is the operator's console, not what a spectator reads, so a language
    may ship without one: every key then renders in English, and a partial file
    degrades word by word (docs/admin.md "Localisation").
    """
    base = _toml_section(os.path.join(PANEL_LOCALES_DIR, 'en.toml'), section)
    if code == 'en':
        return dict(base)
    return {**base, **_toml_section(os.path.join(PANEL_LOCALES_DIR, code + '.toml'), section)}


def i18n_bundle(code=None):
    """Client-facing strings for one language — ``GET /i18n/{lang}``, api.md §5.9.

    Everything a client renders itself: its own chrome (``[mobile]``, ``[display]``)
    and both label styles, so language and short/long are one fetch rather than two
    axes the client has to reassemble. English-merged per key, the rule
    :func:`display_strings` already follows — a half-translated locale falls back
    word by word instead of rendering blank.

    The shipped table only: there is no per-Pi wording, so the Pi and the cloud
    serve the same body for the same language and a client may cache either.
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
        # The vocabulary an event name is composed from, so a client that took
        # `event_name_parts` can render it in this language (api.md §5.1, §5.9).
        'event_name': merged('event_name'),
        # Both styles, so language and short/long are one fetch. `long` differs from
        # `short` only for STYLED_LABEL_KEYS; the narrow columns are short in both.
        'labels': {style: resolve_labels(labels, style)
                   for style in ('short', 'long')},
    }


def load_locale(style=None):
    code  = settings.get('locale', 'en')
    style = style or settings.get('label_style', 'long')
    labels = _locale_section(code, 'labels')
    if not labels:
        return dict(_FALLBACK_LABELS)
    return resolve_labels(labels, style)

def load_preview_strings():
    return _panel_section(settings.get('locale', 'en'), 'preview')

def manual_strings(code=None):
    """Words on /manual — an operator page, so `panel/`, not the served bundle.

    Follows the scoreboard `locale` rather than the per-device `ui_lang` cookie: the
    operator is standing at the pool reading the same event names the boards show,
    and `labels` and `event_vocab` on that page already come from the meet's language.
    """
    return _panel_section(code or settings.get('locale', 'en'), 'manual')

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

def settings_strings(code=None):
    """UI strings for the operator Settings panel, English-merged so any
    untranslated key falls back to English — templates can safely use
    ``{{ t.key }}`` without risking a blank label.

    ``[chrome]`` underneath: the sidebar and theme switcher are the same markup here
    and in the cloud's ``/admin``, so their words live in one section both pages read.
    ``[settings]`` wins on a clash, so a page-specific override stays possible.
    """
    code = code or settings.get('locale', 'en')
    return {**_panel_section(code, 'chrome'), **_panel_section(code, 'settings')}

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
    installed = {c for c, _ in list_locales()}
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
    _migrate_header_label()


def _migrate_header_label():
    """Move `header_label` off the white it used to default to.

    Every install has this key stored — `merge_theme_defaults` has been writing the
    whole palette back since it was added — so a new default alone would reach only
    a fresh install, and every existing board would keep a colour nobody chose.

    Storing the old default is the only evidence available that it was never
    customised, and it is good evidence: the swatch sits in Settings → Theme with a
    reset button beside it, so an operator who actually wants white is one click from
    it, while an operator who never opened the tab gets the new look.
    """
    if settings['theme_colors'].get('header_label', '').lower() == _HEADER_LABEL_WAS:
        settings['theme_colors']['header_label'] = HEADER_LABEL_BLUE


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
    _apply_console_type()


def _apply_console_type():
    """Rebuild `_decoder` if the saved console is not the one that got built at import.

    `_decoder` is created at module scope, which runs *before* `load_settings()` has
    read settings.json — so it is always built from the in-module default, and every
    boot came up as a CTS whatever the operator had chosen. It went unnoticed because
    the Settings form rebuilds the decoder itself on save, so the right decoder
    appeared the moment anyone touched the page and survived until the next restart.

    A manual console is where that stops being survivable: the saved choice decides
    whether a serial port is opened at all, so a Pi rebooting into `cts_gen6` would
    sit retrying a port that is not there while /manual's buttons did nothing —
    `_run_live_serial` only drains the command queue once a port is actually open.
    """
    global _decoder, _decoder_console_type
    want = settings.get('console_type', 'cts_gen6')
    if want != _decoder_console_type:
        _decoder = make_decoder(want, settings)
        _decoder_console_type = want
    else:
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
    # 0600: this file holds the admin password and the cloud relay key in clear
    # text, and the default umask would leave both readable by every account on
    # the Pi. Set on the temp file, before the rename, so the finished file is
    # never briefly world-readable.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wt') as f:
        f.write(data)
    os.replace(tmp, settings_file)

# ── Decoder (initialized after settings dict is defined) ──────────────────────

_decoder_console_type = settings.get('console_type', 'cts_gen6')
_decoder = make_decoder(_decoder_console_type, settings)


def console_state():
    """Which console is driving this meet, for the clients that must gate on it.

    Rides on `/config` and on the relay's `settings` block (docs/api.md §5.4, §6),
    so a native attendee learns it the same way from either server.

    `timed` is the load-bearing half: false means no time and no place will ever
    arrive for this meet, so a Results screen there is a promise the meet cannot
    keep — a phone disables it rather than sitting on "waiting for results" from
    the first heat to the last. It is read off the decoder, never off
    `console_type == 'manual'`, for the reason `worker` gives at its own check: a
    local plugin console driven by hand answers `requires_serial` False too, and
    would otherwise be published as timed.

    `key` is the console the operator chose. No client behaviour hangs on it —
    it is there so a support screen or a log can say which console a meet ran on
    without the operator reading it off the Pi. The human label stays behind:
    `CONSOLE_OPTIONS` carries it in English only, and no spectator reads it.
    """
    return {
        'key':   settings.get('console_type', 'cts_gen6'),
        'timed': bool(getattr(_decoder, 'requires_serial', True)),
    }
