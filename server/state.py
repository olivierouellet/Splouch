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

# ── Re-exports ─────────────────────────────────────────────────────────────────
# Where things live now: `paths` for the filesystem layout, `i18n` for anything
# that needs only a language code. Both used to be sections of this module, and
# almost every caller reaches them as `state.X`, so the names stay available here.
#
# Patch the owning module, not this one, when a test needs to redirect a
# directory: `i18n` reads `paths.LOCALES_DIR` at call time, so a name rebound on
# `state` alone would be read by nobody.
import i18n
import paths
from paths import (app_dir, REPO_DIR, SHARED_DIR, STATIC_DIR, LOCALES_DIR,
                   PANEL_LOCALES_DIR, SCOREBOARD_DIR, settings_file, _settings_default,
                   SESSIONS_FOLDER, CUSTOM_SESSIONS_FOLDER, IMAGES_DIR, ICONS_DIR,
                   HOME_ICON_PATH, HOME_ICON_512_PATH, PICKER_DIR, MEET_FOLDER,
                   TEST_MEET_FOLDER, LOGS_DIR, THEME_FOLDER, CUSTOM_THEME_FOLDER,
                   CUSTOM_DECODERS_FOLDER, PROVISION_VERSION_FILE,
                   PROVISIONED_MARKER, SESSION_KEY_FILE, SERVICE_NAME,
                   session_secret)
from i18n import (DEFAULT_THEME_COLORS, DEFAULT_THEME_FONTS,
                  STYLED_LABEL_KEYS, resolve_labels, available_locales,
                  list_locales, list_builtin_themes, list_custom_themes,
                  load_theme, parse_event_name, compose_event_name,
                  translate_event_name)
# Underscored, but reached from outside — keep them resolving off `state`.
_locale_section = i18n.locale_section
_panel_section  = i18n.panel_section
_FALLBACK_LABELS = i18n._FALLBACK_LABELS


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
    # Lap counts in the delta column while a lane is swimming. Off by default:
    # only a console that reports a lap number on the wire is reliable enough to
    # put on a public board, and the CTS Gen6 count is inferred from touchpad
    # stops. Turn it on once you know which console the venue has.
    'show_laps': False,
    'results_sort': 'lane',
    'active_theme': 'default',
    'theme_colors': {
        'bg': '#0d0d0d', 'header_bg': '#1a1a1a', 'header_border': '#2e2e2e',
        'header_label': '#3b9eff', 'header_value': '#e0e0e0',
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

# Everything ever published on `/scoreboard` as `update_scoreboard`, merged. The
# frames are partial (docs/api.md §5.1), so a client that connects mid-heat has no
# way to learn what it missed — the console only resends a lane when that lane
# changes. The cloud has always solved this with `meet['last_scoreboard']` and its
# join replay; this is the same cache on the Pi, so a kiosk that reconnects between
# two touches gets the board back instead of sitting blank until the next frame.
#
# Written from the worker thread and read from the event loop. No lock: every writer
# goes through `record_board`, and a reader takes `dict(board)` — CPython's GIL makes
# both atomic against each other, and a frame that lands mid-copy is one the client
# is about to be sent anyway.
board = {}


def record_board(data):
    """Merge a published `update_scoreboard` frame into the replay cache.

    `running_time` is deliberately dropped. The replay carries no indication of how
    old it is, and a stale clock is worse than no clock: a client joining mid-heat
    would paint a frozen figure and believe it. It waits for the next re-base
    instead, which is at most one tick away. The cloud drops it for the same reason
    (cloud_server._forward).
    """
    if not data:
        return
    board.update(data)
    board.pop('running_time', None)

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


# ── This server's identity, and the language it reads in ─────────────────────────────

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


# The language this server reads in unless a caller names another one. Every
# wrapper below is the same shape: fill in the meet's language, then hand off to
# `i18n`, which knows nothing about settings.

def _locale():
    return settings.get('locale', 'en')


def load_locale(style=None):
    """Column headers in the meet's language and this server's label style."""
    return i18n.labels_for(_locale(), style or settings.get('label_style', 'long'))

def i18n_bundle(code=None):
    """`GET /i18n/{lang}` (api.md §5.9), defaulting to the meet's language."""
    return i18n.i18n_bundle(code or _locale())

def load_event_translations():
    """The `[event_name]` vocabulary, in the meet's language."""
    return i18n.event_translations(_locale())

def load_preview_strings():
    return i18n.panel_section(_locale(), 'preview')

def manual_strings(code=None):
    """Words on /manual — an operator page, so `panel/`, not the served bundle.

    Follows the scoreboard `locale` rather than the per-device `ui_lang` cookie: the
    operator is standing at the pool reading the same event names the boards show,
    and `labels` and `event_vocab` on that page already come from the meet's language.
    """
    return i18n.panel_section(code or _locale(), 'manual')

def _mobile_strings():
    return i18n.locale_section(_locale(), 'mobile')

def display_strings(code=None):
    """Status strings for the native TV display, English-merged.

    An untranslated key falls back to English rather than rendering blank on the
    TV. Driven by the ``locale`` setting (Settings → Display → Scoreboard
    language), so the display follows the same language as the board it replaces.
    """
    return i18n.display_strings(code or _locale())

def settings_strings(code=None):
    """UI strings for the operator Settings panel, English-merged so any
    untranslated key falls back to English — templates can safely use
    ``{{ t.key }}`` without risking a blank label.

    ``[chrome]`` underneath: the sidebar and theme switcher are the same markup here
    and in the cloud's ``/admin``, so their words live in one section both pages read.
    ``[settings]`` wins on a clash, so a page-specific override stays possible.
    """
    return i18n.panel_strings(code or _locale(), 'chrome', 'settings')

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


def _shipped_credentials():
    """The username/password `settings.default.json` ships with.

    Read from that file rather than restated here, so this stays true if the
    shipped defaults ever change. Read once — it is a file in the repo, and
    `using_default_credentials()` is consulted on every settings render.
    """
    global _SHIPPED_CREDS
    if _SHIPPED_CREDS is None:
        try:
            with open(_settings_default) as f:
                d = json.load(f)
            _SHIPPED_CREDS = (d.get('username', ''), d.get('password', ''))
        except (OSError, ValueError):
            _SHIPPED_CREDS = ('', '')
    return _SHIPPED_CREDS


_SHIPPED_CREDS = None


def using_default_credentials():
    """True while the admin login is still the one every install ships with.

    These are in the README and in docs/admin.md, so until they are changed the
    password protects nothing — the settings panel says so rather than leaving it
    to a line in the documentation that an operator reads once.

    Empty shipped values read as False: that means the defaults file could not be
    read, and a banner shown on a guess would be worse than none.
    """
    user, password = _shipped_credentials()
    if not user and not password:
        return False
    return (settings.get('username') == user and
            settings.get('password') == password)


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
