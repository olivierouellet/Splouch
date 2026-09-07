"""Splouch cloud relay server.

Receives scoreboard events from Pi relays and forwards them to attendees.
One instance handles all active meets; each meet is a broadcast channel.

FastAPI + plain WebSockets. Each WebSocket path
(``/ws/relay`` ``/ws/scoreboard`` ``/ws/results`` ``/ws/schedule``) and each
per-meet room is a channel keyed ``<namespace>:<meet_id>``. Messages are JSON
frames ``{"event", "data"}``.
"""
import asyncio
import base64
import datetime
import glob
import hashlib
import hmac
import json
import os
import queue
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import tomllib
import urllib.request
from contextlib import asynccontextmanager

from fastapi import (Depends, FastAPI, HTTPException, Request, WebSocket,
                     WebSocketDisconnect)
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool


class ActionResult(BaseModel):
    """Success/failure body for admin actions (error only present on failure)."""
    ok: bool
    error: str | None = None


class RestoreResult(BaseModel):
    """Result of a backup restore: how many records were merged in."""
    ok: bool
    count: int = 0
    error: str | None = None


class StatsResult(BaseModel):
    """Attendee count for a meet/window; count is null when analytics are off."""
    enabled: bool
    count: int | None = None

DATA_DIR    = os.environ.get('DATA_DIR', '/data')
KEYS_FILE   = os.path.join(DATA_DIR, 'keys.json')
CREDS_FILE  = os.path.join(DATA_DIR, 'credentials.json')
MEETS_FILE  = os.path.join(DATA_DIR, 'meets.json')   # legacy single-file store (migrated on load)
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
SHARED_TEMPLATES_DIR = next(
    (p for p in (os.path.join(_HERE, 'templates_shared'),
                 os.path.join(_HERE, os.pardir, 'shared', 'templates'))
     if os.path.isdir(p)),
    os.path.join(_HERE, 'templates_shared'),
)

_locale_cache = {}

def _available_locales():
    locales = []
    for path in sorted(glob.glob(os.path.join(LOCALES_DIR, '*.toml'))):
        code = os.path.splitext(os.path.basename(path))[0]
        with open(path, 'rb') as f:
            name = tomllib.load(f).get('meta', {}).get('name', code)
        locales.append((code, name))
    return locales

def _strings(lang, section):
    available = {code for code, _ in _available_locales()}
    if lang not in available:
        lang = 'en'
    if lang not in _locale_cache:
        with open(os.path.join(LOCALES_DIR, f'{lang}.toml'), 'rb') as f:
            _locale_cache[lang] = tomllib.load(f)
    return _locale_cache[lang].get(section, {})

def _browser_lang(request):
    """First Accept-Language entry matching an available locale, or None."""
    available = {code for code, _ in _available_locales()}
    accept = request.headers.get('Accept-Language', '')
    for part in accept.replace('-', '_').split(','):
        code = part.split(';')[0].strip().split('_')[0].lower()
        if code in available:
            return code
    return None

def _server_lang(request):
    # Admin's pinned locale wins; otherwise follow the browser.
    available = {code for code, _ in _available_locales()}
    stored = _load_creds().get('locale', '')
    if stored and stored in available:
        return stored
    return _browser_lang(request) or 'en'

def _picker_lang(request):
    # Public picker: each visitor's browser language wins; the server-wide
    # default (creds['locale']) is only a fallback when the browser language
    # isn't available. Set from the Appearance tab — see change_locale.
    return _browser_lang(request) or _server_lang(request)

def _admin_lang(request):
    # The admin panel language is per-device, independent of the server-wide
    # public-page locale above: the ui_lang cookie wins, else the browser, else
    # English. Same contract as the operator Settings panel.
    available = {code for code, _ in _available_locales()}
    cookie = request.cookies.get('ui_lang', '')
    if cookie in available:
        return cookie
    return _browser_lang(request) or 'en'

def _ui_lang_cookie(request):
    # The explicitly-pinned panel language, or '' for "Auto" — so a stale cookie
    # for a removed locale reads as Auto, matching _admin_lang() resolution.
    available = {code for code, _ in _available_locales()}
    cookie = request.cookies.get('ui_lang', '')
    return cookie if cookie in available else ''

def _load_cloud_strings(request):
    return _strings(_admin_lang(request), 'cloud')

def _meet_lang(meet):
    return meet.get('settings', {}).get('locale') or 'en'

def _locale_name(code):
    for c, name in _available_locales():
        if c == code:
            return name
    return code


@asynccontextmanager
async def lifespan(app):
    os.makedirs(DATA_DIR, exist_ok=True)
    _analytics_prune()
    flush_task = asyncio.create_task(_analytics_flush_loop())
    try:
        yield
    finally:
        flush_task.cancel()
        try:
            await flush_task
        except asyncio.CancelledError:
            pass
        try:
            await run_in_threadpool(_flush_analytics)   # persist anything still queued
        except Exception:
            pass                                        # don't let a failed drain error shutdown


# Built-in docs are disabled here and re-served below behind `require_admin`, so
# the OpenAPI schema and Swagger/ReDoc UIs require admin Basic-auth credentials.
app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=STATIC_DIR, check_dir=False),
          name='static')
templates = Jinja2Templates(directory=[os.path.join(_HERE, 'templates'),
                                       SHARED_TEMPLATES_DIR])


def render(request, name, **ctx):
    return templates.TemplateResponse(request, name, ctx)


# ── Realtime (plain WebSocket) ─────────────────────────────────────────────────

class ConnectionManager:
    """Attendee WebSockets grouped into per-meet channels."""

    def __init__(self):
        self.channels: dict[str, set] = {}

    def join(self, ws, channel):
        self.channels.setdefault(channel, set()).add(ws)

    def leave_all(self, ws):
        for conns in self.channels.values():
            conns.discard(ws)

    async def send(self, ws, event, data=None):
        try:
            await ws.send_json({'event': event, 'data': data})
        except Exception:
            pass

    async def broadcast(self, channel, event, data=None):
        targets = list(self.channels.get(channel, ()))
        if not targets:
            return
        frame = {'event': event, 'data': data}
        # Send to every attendee concurrently so one slow/backed-up client can't
        # delay delivery to the rest (still one loop — this overlaps the I/O waits,
        # it is not parallelism). return_exceptions keeps one failure from
        # cancelling the others; failed sockets are dropped.
        results = await asyncio.gather(*(ws.send_json(frame) for ws in targets),
                                       return_exceptions=True)
        for ws, result in zip(targets, results):
            if isinstance(result, Exception):
                self.leave_all(ws)


manager = ConnectionManager()


def _ch(ns, meet_id):
    return f'{ns}:{meet_id}'


# ── Per-meet state ─────────────────────────────────────────────────────────────
# _meets: meet_id -> {
#   relay_key, relay_sid, organizer, name, location, sport, meet_date,
#   settings, connected_at, clock_at,
#   last_scoreboard, last_results, last_next_heats, schedule_data
# }
# _retained: meet_id -> persisted snapshot that outlives the relay connection, so
#   a meet keeps showing (schedule, picker image, icon) after the console
#   disconnects, until it expires. Persisted to MEETS_FILE. Fields:
#   organizer, relay_key, name, location, sport, app_window_title, meet_date,
#   settings, schedule_data, last_seen (iso), expires_at (iso or None while live).
_meets      = {}
_relay_sids = {}   # relay connection id -> meet_id
_lock       = threading.Lock()

# Fields copied from a live meet into its retained snapshot.
_RETAINED_FIELDS = ('organizer', 'relay_key', 'name', 'location', 'sport',
                    'app_window_title', 'meet_date', 'settings', 'schedule_data')

# The retained store is persisted as one small metadata file per meet plus
# separate files for the big fields — so a persist writes only the meet that
# changed (not all 30), and the base64 logo/background and the full start list
# never sit in the metadata JSON that gets rewritten on every register. In memory
# `_retained[meet_id]` still holds the whole record (images + schedule),
# reassembled on load, so every read/serve route is unchanged. Files per meet:
#   <id>.json            metadata + settings MINUS the two *_b64 images
#   <id>.schedule.json   schedule_data (start list)
#   <id>.icon / .picker  the home-icon / picker-image base64 strings
# See info/async_architecture.md ("Scaling the cloud persistence").
_ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,64}$')   # meet id -> safe filename

# How often a running race clock is re-based on the attendees' devices. They tick
# it themselves in between, so this is a correction rate, not a frame rate: it
# bounds drift and how long a phone joining mid-heat waits for a clock.
_CLOCK_SYNC_SECS = 2.0


# ── Retained meets (persistence) ───────────────────────────────────────────────

def _meet_file(meet_id, suffix):
    return os.path.join(RETAINED_DIR, meet_id + suffix)


def _atomic_write(path, text):
    """Write text to path atomically (temp file + os.replace). Blocking I/O."""
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


def _write_blob(path, text):
    """Write a blob file, or remove it when the value is empty."""
    if text:
        _atomic_write(path, text)
    elif os.path.exists(path):
        os.remove(path)


def _split_record(rec):
    """Split a full retained record into (meta, schedule, icon_b64, picker_b64).

    `meta` is a shallow copy safe to serialize — the big fields are pulled out of
    it, not out of the shared record."""
    meta     = dict(rec)
    schedule = meta.pop('schedule_data', None)
    settings = dict(meta.get('settings') or {})
    icon     = settings.pop('home_icon_b64', '')
    picker   = settings.pop('picker_image_b64', '')
    meta['settings'] = settings
    return meta, schedule, icon, picker


def _write_meet_files(meet_id, rec, write_schedule, write_images):
    """Persist one meet's per-file record. Blocking (disk I/O) — call off the
    loop. The metadata file is always written; the big blobs only when the event
    that changed them asks (register -> images, schedule_snapshot -> schedule), so
    an unchanged blob isn't rewritten on every reconnect."""
    if not _ID_RE.match(meet_id or ''):
        return
    meta, schedule, icon, picker = _split_record(rec)
    _atomic_write(_meet_file(meet_id, '.json'), json.dumps(meta, indent=2))
    if write_schedule:
        _write_blob(_meet_file(meet_id, '.schedule.json'),
                    json.dumps(schedule) if schedule else '')
    if write_images:
        _write_blob(_meet_file(meet_id, '.icon'),   icon)
        _write_blob(_meet_file(meet_id, '.picker'), picker)


def _delete_meet_files(meet_id):
    if not _ID_RE.match(meet_id or ''):
        return
    for suffix in ('.json', '.schedule.json', '.icon', '.picker'):
        try:
            os.remove(_meet_file(meet_id, suffix))
        except OSError:
            pass


def _load_retained():
    """Load every per-meet file back into one in-memory dict of full records."""
    os.makedirs(RETAINED_DIR, exist_ok=True)
    # One-time migration from the legacy single-file meets.json.
    if os.path.exists(MEETS_FILE):
        try:
            with open(MEETS_FILE) as f:
                legacy = json.load(f)
            for mid, rec in legacy.items():
                _write_meet_files(mid, rec, True, True)
            os.replace(MEETS_FILE, MEETS_FILE + '.migrated')
        except (json.JSONDecodeError, OSError):
            pass
    store = {}
    for path in glob.glob(os.path.join(RETAINED_DIR, '*.json')):
        if path.endswith('.schedule.json'):
            continue
        mid = os.path.basename(path)[:-len('.json')]
        try:
            with open(path) as f:
                rec = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        sp = _meet_file(mid, '.schedule.json')
        if os.path.exists(sp):
            try:
                with open(sp) as f:
                    rec['schedule_data'] = json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        settings = rec.setdefault('settings', {})
        for suffix, field in (('.icon', 'home_icon_b64'), ('.picker', 'picker_image_b64')):
            bp = _meet_file(mid, suffix)
            if os.path.exists(bp):
                try:
                    with open(bp) as f:
                        settings[field] = f.read()
                except OSError:
                    pass
        store[mid] = rec
    return store


_retained = _load_retained()


def _persist_meet_mem(meet_id, meet):
    """In-memory write-through of a live meet into the retained store — no disk.
    Caller holds _lock."""
    snap = _retained.get(meet_id, {})
    snap.update({k: meet.get(k) for k in _RETAINED_FIELDS})
    snap['last_seen']   = datetime.datetime.now().isoformat(timespec='seconds')
    snap['expires_at']  = None   # live — never expires while connected
    _retained[meet_id] = snap


def _record_copy_locked(meet_id):
    """Shallow copy of a retained record, to serialize off the lock. Its big
    fields (settings, schedule_data) are rebound wholesale, never mutated in
    place, so sharing their references with the writer thread is safe."""
    return dict(_retained[meet_id])


def _compute_expiry(meet_date, when=None):
    """Midnight after the final session date, or after `when` if no meet date."""
    when = when or datetime.datetime.now()
    base = None
    if meet_date:
        try:
            base = datetime.date.fromisoformat(meet_date)
        except ValueError:
            base = None
    if base is None:
        base = when.date()
    return datetime.datetime.combine(base, datetime.time.min) + datetime.timedelta(days=1)


def _meet_id_for(key, meet_uid):
    """Deterministic cloud meet id for a (relay key, meet_uid) pair.

    Lets one relay key publish several meets — e.g. a meet split across days into
    separate LENEX files — each landing on its own stable picker card and
    reattaching on reload.
    """
    return hashlib.sha256(f'{key}:{meet_uid}'.encode()).hexdigest()[:11]


def _retire_mem(meet_id):
    """Move a live meet into the retained store with an expiry, in memory only —
    no disk. Caller holds _lock."""
    meet = _meets.pop(meet_id, None)
    if not meet:
        return
    snap = _retained.get(meet_id, {})
    snap.update({k: meet.get(k) for k in _RETAINED_FIELDS})
    snap['last_seen']  = datetime.datetime.now().isoformat(timespec='seconds')
    snap['expires_at'] = _compute_expiry(meet.get('meet_date', '')).isoformat(timespec='seconds')
    _retained[meet_id] = snap


def _sweep_expired():
    """Drop retained meets past their expiry. Live meets are never swept."""
    now = datetime.datetime.now()
    with _lock:
        expired = []
        for meet_id in list(_retained):
            if meet_id in _meets:
                continue  # still connected — keep visible regardless of expiry
            exp = _retained[meet_id].get('expires_at')
            if exp and datetime.datetime.fromisoformat(exp) <= now:
                del _retained[meet_id]
                expired.append(meet_id)
    for meet_id in expired:
        _delete_meet_files(meet_id)


def _get_meet(meet_id):
    """Live meet if connected, else its retained snapshot, else None.

    Caller holds _lock.
    """
    return _meets.get(meet_id) or _retained.get(meet_id)


def _merged_meets():
    """meet_id -> meet dict for all live + retained meets, live preferred.

    Caller holds _lock.
    """
    merged = dict(_retained)
    merged.update(_meets)
    return merged


# ── Key management ─────────────────────────────────────────────────────────────

def _load_keys():
    try:
        with open(KEYS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_keys(keys):
    _atomic_write(KEYS_FILE, json.dumps(keys, indent=2))


# ── Admin credentials ──────────────────────────────────────────────────────────

def _hash_password(password, salt=None):
    if salt is None:
        salt = os.urandom(16).hex()
    dk = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100_000)
    return base64.b64encode(dk).decode(), salt


def _load_creds():
    try:
        with open(CREDS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # First run — migrate from env vars and persist
    user     = os.environ.get('ADMIN_USER', 'admin')
    password = os.environ.get('ADMIN_PASSWORD', '')
    pw_hash, salt = _hash_password(password)
    creds = {'user': user, 'password_hash': pw_hash, 'salt': salt}
    _save_creds(creds)
    return creds


def _save_creds(creds):
    _atomic_write(CREDS_FILE, json.dumps(creds, indent=2))


def _picker_appearance():
    creds = _load_creds()
    raw = creds.get('picker_title')
    raw_wt = creds.get('picker_window_title')
    return {
        'picker_title_form':        'Splouch' if raw is None else raw,
        'picker_window_title_form': 'Splouch' if raw_wt is None else raw_wt,
        'has_picker_logo':          bool(creds.get('picker_logo_b64', '')),
        'has_picker_icon':          bool(creds.get('picker_icon_b64', '')),
        'picker_logo_above':        creds.get('picker_logo_above', False),
    }


def _admin_meet_list():
    """Merged live + retained meets for the admin table, live first."""
    out = []
    with _lock:
        for mid, m in _merged_meets().items():
            live = mid in _meets
            exp  = None if live else _retained.get(mid, {}).get('expires_at')
            disp = ''
            if exp:
                try:
                    disp = datetime.datetime.fromisoformat(exp).strftime('%Y-%m-%d %H:%M')
                except ValueError:
                    disp = exp
            out.append({
                'id':              mid,
                'name':            m.get('name', ''),
                'location':        m.get('location', ''),
                'sport':           m.get('sport', ''),
                'organizer':       m.get('organizer', ''),
                'connected_at':    m.get('connected_at', ''),
                'language':        _locale_name(_meet_lang(m)),
                'live':            live,
                'expires_at':      exp,
                'expires_display': disp,
                'expires_input':   (exp or '')[:16],   # for <input type=datetime-local>
            })
    out.sort(key=lambda x: (not x['live'], (x['name'] or '').lower()))
    return out


def _check_admin(request):
    hdr = request.headers.get('Authorization', '')
    if not hdr.startswith('Basic '):
        return False
    try:
        user, _, pw = base64.b64decode(hdr[6:]).decode().partition(':')
    except Exception:
        return False
    creds = _load_creds()
    if user != creds['user']:
        return False
    pw_hash, _ = _hash_password(pw, creds['salt'])
    return hmac.compare_digest(pw_hash, creds['password_hash'])


def require_admin(request: Request):
    if not _check_admin(request):
        raise HTTPException(status_code=401, detail='Authentication required',
                            headers={'WWW-Authenticate': 'Basic realm="Splouch Admin"'})


# ── API docs (admin-gated) ─────────────────────────────────────────────────────
# 401 → the browser prompts for admin Basic-auth credentials, which it then also
# resends when Swagger UI fetches /openapi.json from the same origin.

@app.get('/openapi.json', include_in_schema=False,
         dependencies=[Depends(require_admin)])
async def route_openapi():
    return app.openapi()


@app.get('/docs', include_in_schema=False, dependencies=[Depends(require_admin)])
async def route_docs():
    return get_swagger_ui_html(openapi_url='/openapi.json', title='Splouch Cloud API docs')


@app.get('/redoc', include_in_schema=False, dependencies=[Depends(require_admin)])
async def route_redoc():
    return get_redoc_html(openapi_url='/openapi.json', title='Splouch Cloud API docs')


# ── Attendee analytics (opt-in) ────────────────────────────────────────────────
# When the admin enables it, each attendee `join_meet` is logged as one row keyed
# by a random per-device id the mobile page stores in localStorage. "How many
# people in the last X" is then COUNT(DISTINCT visitor_id) over that window. No IP
# or personal data is ever stored. Off by default — see the legal note in the
# admin panel. Lives in its own SQLite file inside the existing /data volume.

_ANALYTICS_RETENTION_DAYS = 120
_ANALYTICS_FLUSH_SECS     = 5      # how often the background task drains the queue
_analytics_lock  = threading.Lock()
_analytics_db    = None
_analytics_queue = queue.Queue()   # pending joins, flushed to the DB off the loop

# window key -> timedelta; 'all' means since the beginning of time.
_ANALYTICS_WINDOWS = {
    '1h':  datetime.timedelta(hours=1),
    '3h':  datetime.timedelta(hours=3),
    '12h': datetime.timedelta(hours=12),
    '24h': datetime.timedelta(hours=24),
    '7d':  datetime.timedelta(days=7),
}


def _get_analytics_db():
    """Lazily open the analytics DB. Caller holds _analytics_lock."""
    global _analytics_db
    if _analytics_db is None:
        os.makedirs(DATA_DIR, exist_ok=True)
        db = sqlite3.connect(ANALYTICS_FILE, check_same_thread=False)
        db.execute('CREATE TABLE IF NOT EXISTS connections ('
                   'meet_id TEXT, visitor_id TEXT, ts INTEGER, namespace TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_conn_meet_ts '
                   'ON connections (meet_id, ts)')
        db.commit()
        _analytics_db = db
    return _analytics_db


def _analytics_enabled():
    return bool(_load_creds().get('analytics_enabled'))


def _log_connection(meet_id, visitor_id, namespace):
    """Queue one attendee join. Called from the WS connect handlers on the event
    loop, so it does no I/O — just a non-blocking in-memory enqueue. The
    background flush task batches these off the loop and drops them if analytics
    is disabled (so a reconnect storm can't stall the loop with per-join commits)."""
    if not meet_id or not visitor_id:
        return
    _analytics_queue.put((meet_id, str(visitor_id)[:64],
                          int(datetime.datetime.now().timestamp()), namespace))


def _flush_analytics():
    """Drain queued joins into the DB in one transaction. Blocking (disk I/O) —
    run off the loop. Rows are discarded when analytics is disabled."""
    rows = []
    try:
        while True:
            rows.append(_analytics_queue.get_nowait())
    except queue.Empty:
        pass
    if not rows or not _analytics_enabled():
        return
    with _analytics_lock:
        db = _get_analytics_db()
        db.executemany('INSERT INTO connections VALUES (?, ?, ?, ?)', rows)
        db.commit()


async def _analytics_flush_loop():
    """Periodically flush queued analytics joins to the DB, off the event loop."""
    while True:
        await asyncio.sleep(_ANALYTICS_FLUSH_SECS)
        try:
            await run_in_threadpool(_flush_analytics)
        except Exception as e:
            # A transient DB error (locked, disk full) must not kill the loop —
            # that would stop all future flushes and grow the queue unbounded.
            print(f'[cloud] analytics flush failed: {e}', flush=True)


def _attendee_count(meet_id, since_ts):
    """Distinct visitors of a meet since `since_ts` (unix seconds)."""
    with _analytics_lock:
        db = _get_analytics_db()
        row = db.execute('SELECT COUNT(DISTINCT visitor_id) FROM connections '
                         'WHERE meet_id = ? AND ts >= ?', (meet_id, since_ts)).fetchone()
    return row[0] if row else 0


def _attendee_counts(meet_id):
    """Distinct-visitor counts for a meet across every analytics window plus
    all-time, computed under a single lock so the relay gets one consistent
    snapshot. Blocking (disk I/O) — run off the loop."""
    now     = datetime.datetime.now()
    windows = {k: int((now - d).timestamp()) for k, d in _ANALYTICS_WINDOWS.items()}
    windows['all'] = 0
    out = {}
    with _analytics_lock:
        db = _get_analytics_db()
        for key, since in windows.items():
            row = db.execute('SELECT COUNT(DISTINCT visitor_id) FROM connections '
                             'WHERE meet_id = ? AND ts >= ?', (meet_id, since)).fetchone()
            out[key] = row[0] if row else 0
    return out


def _analytics_prune():
    """Drop rows past the retention window so the DB stays small."""
    cutoff = int((datetime.datetime.now()
                  - datetime.timedelta(days=_ANALYTICS_RETENTION_DAYS)).timestamp())
    with _analytics_lock:
        db = _get_analytics_db()
        db.execute('DELETE FROM connections WHERE ts < ?', (cutoff,))
        db.commit()


# ── Routes ─────────────────────────────────────────────────────────────────────

def _public_meet_list():
    """Meets for the picker — live and retained alike, newest state first.

    Shared by the HTML picker and ``GET /meets`` so a native client's list can
    never drift from the web one. Deliberately excludes anything an attendee has
    no business seeing (relay keys, expiry, connection times); the admin table
    has its own builder, ``_admin_meet_list``.
    """
    _sweep_expired()
    with _lock:
        return [{'id': mid, 'name': m['name'], 'location': m['location'],
                 'sport': m['sport'], 'organizer': m['organizer'],
                 'meet_date': m.get('meet_date', ''),
                 'offline': mid not in _meets,
                 'has_picker_image': bool(m.get('settings', {}).get('picker_image_b64', ''))}
                for mid, m in _merged_meets().items()]


def _picker_branding():
    """Operator-set look of the meet list, in the shape public clients consume.

    ``None`` and ``''`` mean different things for the titles: unset falls back to
    'Splouch', while an explicitly blank title hides it. Preserve that — see
    route_picker_appearance.
    """
    creds  = _load_creds()
    raw    = creds.get('picker_title')
    raw_wt = creds.get('picker_window_title')
    return {
        'title':        'Splouch' if raw is None else raw,
        'window_title': 'Splouch' if raw_wt is None else raw_wt,
        'has_logo':     bool(creds.get('picker_logo_b64', '')),
        'logo_above':   creds.get('picker_logo_above', False),
    }


# The picker chrome a native client renders itself. Kept server-side rather than
# shipped in the app because results_disclaimer and privacy_note are compliance
# text: they must be correctable without waiting on an App Store review.
_PICKER_STRING_KEYS = ('page_title', 'no_meets', 'unnamed_meet',
                       'results_disclaimer', 'privacy_note')


@app.get('/', tags=['Public'])
def route_index(request: Request):
    meets = _public_meet_list()
    brand = _picker_branding()
    # The list spans meets that may each run in a different language, so this page
    # follows the visitor, not a meet. Per-meet language starts at /mobile.
    lang = _picker_lang(request)
    return render(request, 'picker.html', meets=meets, t=_strings(lang, 'cloud'),
        lang=lang,
        picker_title=brand['title'],
        picker_window_title=brand['window_title'],
        picker_logo=brand['has_logo'],
        picker_logo_above=brand['logo_above'],
        analytics_enabled=_analytics_enabled())


@app.get('/meets', tags=['Public'])
def route_meets():
    """The meet list as JSON — the native picker's equivalent of ``GET /``.

    ``offline`` meets are retained ones with no relay currently connected; they
    stay listed on purpose so a spectator can still read the last state.
    ``has_picker_image`` says whether ``GET /picker_image/{id}`` will return an
    image for that meet."""
    return {'meets': _public_meet_list()}


@app.get('/picker/config', tags=['Public'])
def route_picker_config(request: Request):
    """Branding, localised chrome, and the analytics flag for a native picker.

    Language resolves from ``?lang=`` when it names an available locale, else the
    client's Accept-Language, else the server default — the same order the HTML
    picker uses. The meet list has no locale of its own; per-meet language only
    starts at ``GET /meet/{id}/config``.

    ``privacy_note`` is present regardless, but is only to be shown when
    ``analytics_enabled`` is true, matching the web picker."""
    lang = request.query_params.get('lang', '')
    if lang not in {code for code, _ in _available_locales()}:
        lang = _picker_lang(request)
    strings = _strings(lang, 'cloud')
    return {
        **_picker_branding(),
        'lang':              lang,
        'analytics_enabled': _analytics_enabled(),
        'strings':           {k: strings[k] for k in _PICKER_STRING_KEYS if k in strings},
    }


@app.get('/mobile', tags=['Public'])
def route_mobile(request: Request):
    meet_id = request.query_params.get('meet', '')
    with _lock:
        meet = _get_meet(meet_id)
    if not meet:
        return RedirectResponse('/', status_code=303)
    return render(request, 'mobile.html',
                  meet_id=meet_id,
                  app_title=(meet.get('app_window_title') or meet['name'] or 'Splouch'),
                  t=_strings(_meet_lang(meet), 'mobile'),
                  lang=_meet_lang(meet))


@app.get('/mobile/live', tags=['Public'])
def route_live(request: Request):
    meet_id = request.query_params.get('meet', '')
    with _lock:
        meet = _get_meet(meet_id)
    if not meet:
        return render(request, 'offline.html')
    s = meet.get('settings', {})
    return render(request, 'live-mobile.html',
        meet_id=meet_id,
        num_lanes=s.get('num_lanes', 8),
        show_lane_header=s.get('show_lane_header', True),
        show_name_header=s.get('show_name_header', True),
        show_club_header=s.get('show_club_header', True),
        show_time_header=s.get('show_time_header', True),
        show_delta_header=s.get('show_delta_header', True),
        show_position_header=s.get('show_position_header', True),
        show_name=s.get('show_name', True),
        show_club=s.get('show_club', True),
        show_delta=s.get('show_delta', True),
        show_position=s.get('show_position', True),
        # Merge over the defaults rather than falling back wholesale: a relay that
        # sends a partial theme_colors would otherwise leave every unlisted CSS
        # variable empty. Matches route_results and route_schedule.
        theme_colors={**_DEFAULT_COLORS, **s.get('theme_colors', {})},
        theme_fonts={**_DEFAULT_FONTS,  **s.get('theme_fonts',  {})},
        labels=s.get('labels', {}),
        lang=_meet_lang(meet),
    )


@app.get('/mobile/results', tags=['Public'])
def route_results(request: Request):
    meet_id = request.query_params.get('meet', '')
    with _lock:
        meet = _get_meet(meet_id)
    if not meet:
        return render(request, 'offline.html')
    s = meet.get('settings', {})
    return render(request, 'results.html',
        meet_id=meet_id,
        num_lanes=s.get('num_lanes', 8),
        show_lane_header=s.get('show_lane_header', True),
        show_name_header=s.get('show_name_header', True),
        show_club_header=s.get('show_club_header', True),
        show_time_header=s.get('show_time_header', True),
        show_delta_header=s.get('show_delta_header', True),
        show_position_header=s.get('show_position_header', True),
        show_name=s.get('show_name', True),
        show_club=s.get('show_club', True),
        show_delta=s.get('show_delta', True),
        show_position=s.get('show_position', True),
        show_podium=s.get('show_podium', True),
        t=_strings(_meet_lang(meet), 'mobile'),
        theme_colors={**_DEFAULT_COLORS, **s.get('theme_colors', {})},
        # Merged, not a wholesale fallback: a relay sending only one font would
        # otherwise leave the other two CSS variables empty. Same as route_live.
        theme_fonts={**_DEFAULT_FONTS,  **s.get('theme_fonts',  {})},
        labels=s.get('labels', {}),
        lang=_meet_lang(meet),
    )


def _build_heats_json(sched):
    if not sched or not sched.get('events'):
        return []
    names      = sched.get('names', {})
    times      = sched.get('times', {})
    start_list = sched.get('start_list', {})
    heats = []
    for ev, sorted_heats in sched['events']:
        ev_str = str(ev)
        for ht in sorted_heats:
            ht_str = str(ht)
            lanes_data = start_list.get(ev_str, {}).get(ht_str, {})
            lanes = []
            for lane_str in sorted(lanes_data, key=lambda x: int(x) if x.lstrip('-').isdigit() else 0):
                entry = lanes_data[lane_str]
                lanes.append({
                    'lane':      int(lane_str) if lane_str.lstrip('-').isdigit() else lane_str,
                    'name':      entry.get('name', ''),
                    'club':      entry.get('club', ''),
                    'seed_time': entry.get('seed_time', ''),
                    'swimmers':  entry.get('swimmers', []),
                })
            heats.append({
                'event':      ev,
                'heat':       ht,
                'event_name': names.get(ev_str, ''),
                'time':       times.get(ev_str, {}).get(ht_str, ''),
                'lanes':      lanes,
            })
    return heats


@app.get('/mobile/schedule', tags=['Public'])
def route_schedule(request: Request):
    meet_id = request.query_params.get('meet', '')
    with _lock:
        meet = _get_meet(meet_id)
    if not meet:
        return render(request, 'offline.html')
    s     = meet.get('settings', {})
    sched = meet.get('schedule_data', {})
    heats = _build_heats_json(sched)
    return render(request, 'schedule.html',
        meet_id=meet_id,
        heats_json=json.dumps(heats),
        has_meet=bool(heats),
        meet_name=meet['name'],
        t=_strings(_meet_lang(meet), 'mobile'),
        labels=s.get('labels', {}),
        theme_colors={**_DEFAULT_COLORS, **s.get('theme_colors', {})},
        theme_fonts={**_DEFAULT_FONTS,  **s.get('theme_fonts', {})},
        lang=_meet_lang(meet),
    )


@app.get('/meet/{meet_id}/config', tags=['Public'])
def route_meet_config(meet_id: str):
    """A meet's display config as JSON — for native attendee clients (iOS/Android)
    that render the board natively instead of loading the HTML page."""
    with _lock:
        meet = _get_meet(meet_id)
        live = meet_id in _meets
    if not meet:
        raise HTTPException(404)
    return {
        'name':             meet.get('name', ''),
        'location':         meet.get('location', ''),
        'sport':            meet.get('sport', ''),
        'app_window_title': meet.get('app_window_title', ''),
        'meet_date':        meet.get('meet_date', ''),
        'live':             live,
        'settings':         meet.get('settings', {}),
    }


@app.get('/meet/{meet_id}/schedule', tags=['Public'])
def route_meet_schedule(meet_id: str):
    """A meet's full start list as JSON — what ``/mobile/schedule`` embeds.

    Same structure as the page's ``heats_json``: every heat in running order,
    each with its lanes. An empty ``heats`` means the meet is loaded but carries
    no schedule yet, which is not an error — the client shows its no-schedule
    state and waits for ``schedule_update`` on ``/ws/schedule``."""
    with _lock:
        meet = _get_meet(meet_id)
    if not meet:
        raise HTTPException(404)
    return {'heats': _build_heats_json(meet.get('schedule_data', {}))}


@app.get('/search_suggestions', tags=['Public'])
def route_search_suggestions(request: Request):
    import unicodedata
    def fold(s):
        return unicodedata.normalize('NFD', s.lower()).encode('ascii', 'ignore').decode()

    meet_id = request.query_params.get('meet_id', '')
    q       = fold(request.query_params.get('q', '').strip())
    if not q:
        return []
    with _lock:
        meet = _get_meet(meet_id)
    if not meet:
        return []
    start_list = meet.get('schedule_data', {}).get('start_list', {})
    swimmers, clubs = {}, set()
    for ev, heats in start_list.items():
        for ht, lanes in heats.items():
            for lane, entry in lanes.items():
                if entry.get('club'):
                    clubs.add(entry['club'])
                if entry.get('name'):
                    swimmers[entry['name']] = entry.get('club', '')
                for sw in entry.get('swimmers', []):
                    if sw.get('name'):
                        swimmers[sw['name']] = entry.get('club', '')
    results = []
    for name, club in sorted(swimmers.items()):
        if q in fold(name):
            results.append({'type': 'swimmer', 'name': name, 'club': club})
    for club in sorted(clubs):
        if q in fold(club):
            results.append({'type': 'club', 'name': club})
    return results[:20]


@app.get('/logout', tags=['Admin'])
def route_logout():
    return Response(
        'Logged out — <a href="/admin">sign in again</a>', status_code=401,
        headers={'WWW-Authenticate': 'Basic realm="Splouch Admin"'})


@app.get('/ping', tags=['Public'])
def route_ping():
    return Response('ok', media_type='text/plain')


@app.get('/manifest/{meet_id}', tags=['Public'])
def route_manifest(meet_id: str):
    with _lock:
        meet = _get_meet(meet_id)
    if not meet:
        raise HTTPException(404)
    has_icon = bool(meet.get('settings', {}).get('home_icon_b64'))
    icons = ([
        {'src': f'/icon/{meet_id}', 'sizes': '192x192', 'type': 'image/png'},
        {'src': f'/icon/{meet_id}', 'sizes': '512x512', 'type': 'image/png'},
    ] if has_icon else [
        {'src': '/static/img/default_mobile_icon.png', 'sizes': '1024x1024', 'type': 'image/png'},
    ])
    app_title = meet.get('app_window_title') or meet.get('name') or 'Splouch'
    manifest = {
        'name':             app_title,
        'short_name':       app_title,
        'start_url':        f'/mobile?meet={meet_id}',
        'display':          'standalone',
        'background_color': '#000000',
        'theme_color':      '#000000',
        'icons':            icons,
    }
    return Response(json.dumps(manifest), media_type='application/manifest+json')


@app.get('/icon/{meet_id}', tags=['Public'])
def route_icon(meet_id: str):
    with _lock:
        meet = _get_meet(meet_id)
    if not meet:
        raise HTTPException(404)
    icon_b64 = meet.get('settings', {}).get('home_icon_b64', '')
    if not icon_b64:
        raise HTTPException(404)
    data = base64.b64decode(icon_b64)
    return Response(data, media_type='image/png',
                    headers={'Cache-Control': 'public, max-age=3600'})


@app.get('/picker_image/{meet_id}', tags=['Public'])
def route_meet_picker_image(meet_id: str):
    with _lock:
        meet = _get_meet(meet_id)
    if not meet:
        raise HTTPException(404)
    img_b64 = meet.get('settings', {}).get('picker_image_b64', '')
    if not img_b64:
        raise HTTPException(404)
    data = base64.b64decode(img_b64)
    return Response(data, media_type='image/png',
                    headers={'Cache-Control': 'public, max-age=60'})


@app.get('/picker_logo', tags=['Public'])
def route_picker_logo():
    creds    = _load_creds()
    logo_b64 = creds.get('picker_logo_b64', '')
    if not logo_b64:
        raise HTTPException(404)
    data = base64.b64decode(logo_b64)
    mime = creds.get('picker_logo_mime', 'image/png')
    return Response(data, media_type=mime,
                    headers={'Cache-Control': 'public, max-age=300'})


@app.get('/picker_icon', tags=['Public'])
def route_picker_icon():
    icon_b64 = _load_creds().get('picker_icon_b64', '')
    if not icon_b64:
        default = os.path.join(_HERE, 'static', 'img', 'default_mobile_icon.png')
        if not os.path.exists(default):
            raise HTTPException(404)
        return FileResponse(default, media_type='image/png')
    data = base64.b64decode(icon_b64)
    return Response(data, media_type='image/png',
                    headers={'Cache-Control': 'public, max-age=300'})


@app.get('/favicon.ico', tags=['Public'])
def route_favicon():
    # Browsers auto-request this; serve a lean, scalable brand mark for the tab.
    return FileResponse(os.path.join(_HERE, 'static', 'img', 'favicon.svg'),
                        media_type='image/svg+xml')


@app.get('/picker_manifest', tags=['Public'])
def route_picker_manifest():
    creds = _load_creds()
    raw_wt = creds.get('picker_window_title')
    app_title = ('Splouch' if raw_wt is None else raw_wt) or 'Splouch'
    manifest = {
        'name':             app_title,
        'short_name':       app_title,
        'start_url':        '/',
        'display':          'standalone',
        'background_color': '#000000',
        'theme_color':      '#000000',
        'icons': [
            {'src': '/picker_icon', 'sizes': '192x192', 'type': 'image/png'},
            {'src': '/picker_icon', 'sizes': '512x512', 'type': 'image/png'},
        ],
    }
    return Response(json.dumps(manifest), media_type='application/manifest+json')


@app.post('/admin/picker_appearance', tags=['Admin'], response_model=ActionResult,
          response_model_exclude_none=True, dependencies=[Depends(require_admin)])
async def route_picker_appearance(request: Request):
    form  = await request.form()
    creds = _load_creds()
    if 'picker_title' in form:
        creds['picker_title']        = form.get('picker_title', '').strip()
        creds['picker_window_title'] = form.get('picker_window_title', '').strip()
        creds['picker_logo_above']   = form.get('picker_logo_above') == '1'
    if form.get('picker_logo_clear') == '1':
        creds['picker_logo_b64'] = ''
        creds.pop('picker_logo_mime', None)
    else:
        logo = form.get('picker_logo')
        if logo and logo.filename:
            data = await logo.read()
            mime = logo.content_type or 'image/png'
            creds['picker_logo_b64']  = base64.b64encode(data).decode()
            creds['picker_logo_mime'] = mime
    if form.get('picker_icon_clear') == '1':
        creds['picker_icon_b64'] = ''
    else:
        icon = form.get('picker_icon')
        if icon and icon.filename:
            creds['picker_icon_b64'] = base64.b64encode(await icon.read()).decode()
    await run_in_threadpool(_save_creds, creds)
    return {'ok': True}


_LOGIN_FIELDS = ('user', 'password_hash', 'salt')


@app.get('/admin/backup/keys', tags=['Admin'], dependencies=[Depends(require_admin)])
def route_backup_keys(request: Request):
    try:
        with open(KEYS_FILE) as f:
            keys = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        keys = {}
    # keys + credentials.json (picker appearance, analytics toggle, locale). By
    # default the admin login is excluded, so a routine backup never carries the
    # password hash. ?full=1 adds the login (marked 'full') for a bare-metal
    # rebuild — that file contains the password hash + salt, so keep it private.
    full  = request.query_params.get('full') == '1'
    creds = _load_creds()
    if not full:
        creds = {k: v for k, v in creds.items() if k not in _LOGIN_FIELDS}
    backup = {'version': 2, 'keys': keys, 'credentials': creds}
    if full:
        backup['full'] = True
    name = 'splouch-backup-full.json' if full else 'splouch-backup.json'
    return Response(
        json.dumps(backup, indent=2),
        media_type='application/json',
        headers={'Content-Disposition': f'attachment; filename="{name}"'})


@app.post('/admin/restore/keys', tags=['Admin'], response_model=RestoreResult,
          response_model_exclude_none=True, dependencies=[Depends(require_admin)])
async def route_restore_keys(request: Request):
    uploaded = (await request.form()).get('keys_file')
    if not uploaded:
        return JSONResponse({'error': 'No file provided'}, status_code=400)
    try:
        data = json.loads(await uploaded.read())
        if not isinstance(data, dict) or not isinstance(data.get('keys'), dict):
            raise ValueError('not a valid backup file')
        keys = data['keys']
        await run_in_threadpool(_save_keys, keys)
        # Merge the backup's credentials (appearance, analytics, locale) onto the
        # current ones so the backup wins but no required field goes missing. The
        # admin login is only touched when the file is an explicit full backup —
        # otherwise restoring a routine backup would silently reset the password.
        creds_in = data.get('credentials')
        if isinstance(creds_in, dict):
            creds_in = dict(creds_in)
            if not data.get('full'):
                for f in _LOGIN_FIELDS:
                    creds_in.pop(f, None)
            await run_in_threadpool(_save_creds, {**_load_creds(), **creds_in})
        return {'ok': True, 'count': len(keys)}
    except (json.JSONDecodeError, ValueError) as e:
        return JSONResponse({'error': f'Invalid file: {e}'}, status_code=400)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@app.get('/admin/backup/meets', tags=['Admin'], dependencies=[Depends(require_admin)])
def route_backup_meets():
    with _lock:
        meets = dict(_retained)
    backup = {'version': 1, 'meets': meets}
    return Response(
        json.dumps(backup, indent=2),
        media_type='application/json',
        headers={'Content-Disposition': 'attachment; filename="splouch-meets.json"'})


@app.post('/admin/restore/meets', tags=['Admin'], response_model=RestoreResult,
          response_model_exclude_none=True, dependencies=[Depends(require_admin)])
async def route_restore_meets(request: Request):
    uploaded = (await request.form()).get('meets_file')
    if not uploaded:
        return JSONResponse({'error': 'No file provided'}, status_code=400)
    try:
        data = json.loads(await uploaded.read())
        if not isinstance(data, dict):
            raise ValueError('expected a JSON object')
        meets = data['meets'] if 'meets' in data else data
        if not isinstance(meets, dict):
            raise ValueError('invalid meets section')
        with _lock:
            # Merge (upsert) the backup's meets into the store — never clear. A
            # meet not in the backup is left alone, and a currently-live meet is
            # skipped so its fresh state isn't overwritten by a stale backup. This
            # is additive: retained meets auto-expire, and "Delete meet" removes
            # one. Nothing is wiped, so a concurrent register can't be clobbered.
            incoming = {mid: rec for mid, rec in meets.items() if mid not in _meets}
            _retained.update(incoming)
            recs = {mid: dict(_retained[mid]) for mid in incoming}
        for mid, rec in recs.items():
            await run_in_threadpool(_write_meet_files, mid, rec, True, True)
        return {'ok': True, 'count': len(incoming)}
    except (json.JSONDecodeError, ValueError) as e:
        return JSONResponse({'error': f'Invalid file: {e}'}, status_code=400)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=500)


@app.post('/admin/update', tags=['Admin'], dependencies=[Depends(require_admin)])
async def route_update(request: Request):
    version = (await request.form()).get('version', 'latest')
    # The webhook call is a blocking HTTP request — run it off the event loop
    # so attendee broadcasts keep flowing.
    return await run_in_threadpool(_trigger_update, version)


def _trigger_update(version):
    url    = os.environ.get('DEPLOY_WEBHOOK_URL', '')
    secret = os.environ.get('DEPLOY_WEBHOOK_SECRET', '')
    if not url or not secret:
        return JSONResponse({'error': 'Deploy webhook not configured'}, status_code=503)
    try:
        body = json.dumps({'version': version}).encode()
        req = urllib.request.Request(url, data=body, method='POST')
        req.add_header('X-Deploy-Token', secret)
        req.add_header('Content-Type', 'application/json')
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return {'status': 'started'}
            return JSONResponse({'error': f'webhook {resp.status}'}, status_code=502)
    except Exception as e:
        return JSONResponse({'error': str(e)}, status_code=502)


@app.get('/admin/update_log', tags=['Admin'], dependencies=[Depends(require_admin)])
def route_update_log():
    webhook_url = os.environ.get('DEPLOY_WEBHOOK_URL', '')
    secret      = os.environ.get('DEPLOY_WEBHOOK_SECRET', '')
    if not webhook_url or not secret:
        return {'lines': [], 'done': None}

    log_url = webhook_url.rsplit('/', 1)[0] + '/log'
    try:
        req = urllib.request.Request(log_url, method='GET')
        req.add_header('X-Deploy-Token', secret)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return Response(resp.read(), media_type='application/json')
    except Exception:
        return {'lines': [], 'done': None}


@app.get('/admin/logs', tags=['Admin'], dependencies=[Depends(require_admin)])
def route_logs(request: Request):
    webhook_url = os.environ.get('DEPLOY_WEBHOOK_URL', '')
    secret      = os.environ.get('DEPLOY_WEBHOOK_SECRET', '')
    if not webhook_url or not secret:
        return JSONResponse({'ok': False, 'error': 'not configured'}, status_code=503)

    source = request.query_params.get('source', 'app')
    tail   = request.query_params.get('tail', '300')
    logs_url = webhook_url.rsplit('/', 1)[0] + f'/logs?source={source}&tail={tail}'
    try:
        req = urllib.request.Request(logs_url, method='GET')
        req.add_header('X-Deploy-Token', secret)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return Response(resp.read(), media_type='application/json')
    except Exception as e:
        return JSONResponse({'ok': False, 'error': str(e)}, status_code=502)


@app.get('/admin/versions', tags=['Admin'], dependencies=[Depends(require_admin)])
def route_versions():
    webhook_url = os.environ.get('DEPLOY_WEBHOOK_URL', '')
    secret      = os.environ.get('DEPLOY_WEBHOOK_SECRET', '')
    if not webhook_url or not secret:
        return JSONResponse({'ok': False, 'error': 'not configured'}, status_code=503)

    versions_url = webhook_url.rsplit('/', 1)[0] + '/versions'
    try:
        req = urllib.request.Request(versions_url, method='GET')
        req.add_header('X-Deploy-Token', secret)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return Response(resp.read(), media_type='application/json')
    except Exception as e:
        return JSONResponse({'ok': False, 'error': str(e)}, status_code=502)


@app.get('/admin/stats', tags=['Admin'], response_model=StatsResult,
         dependencies=[Depends(require_admin)])
def route_stats(request: Request):
    if not _analytics_enabled():
        return {'enabled': False, 'count': None}
    meet_id = request.query_params.get('meet_id', '')
    window  = request.query_params.get('window', '24h')
    if window == 'all':
        since = 0
    else:
        delta = _ANALYTICS_WINDOWS.get(window, _ANALYTICS_WINDOWS['24h'])
        since = int((datetime.datetime.now() - delta).timestamp())
    return {'enabled': True, 'count': _attendee_count(meet_id, since)}


@app.get('/admin', tags=['Admin'], dependencies=[Depends(require_admin)])
@app.post('/admin', tags=['Admin'], dependencies=[Depends(require_admin)])
async def route_admin(request: Request):
    keys = _load_keys()

    if request.method == 'POST':
        form   = await request.form()
        action = form.get('action')
        if action == 'add':
            org = form.get('organizer', '').strip()
            if org:
                new_key = secrets.token_urlsafe(32)
                keys[new_key] = {
                    'organizer': org,
                    'created':   datetime.date.today().isoformat(),
                    'active':    True,
                }
                await run_in_threadpool(_save_keys, keys)
        elif action == 'revoke':
            key = form.get('key', '')
            if key in keys:
                keys[key]['active'] = False
                await run_in_threadpool(_save_keys, keys)
        elif action == 'delete':
            key = form.get('key', '')
            if key in keys:
                del keys[key]
                await run_in_threadpool(_save_keys, keys)
        elif action == 'set_expiry':
            meet_id = form.get('meet_id', '')
            raw     = form.get('expires_at', '').strip()
            with _lock:
                rec = None
                if meet_id in _retained and meet_id not in _meets and raw:
                    try:
                        exp = datetime.datetime.fromisoformat(raw)
                        _retained[meet_id]['expires_at'] = exp.isoformat(timespec='seconds')
                        rec = _record_copy_locked(meet_id)
                    except ValueError:
                        pass
            if rec is not None:
                await run_in_threadpool(_write_meet_files, meet_id, rec, False, False)  # metadata only
        elif action == 'delete_meet':
            meet_id = form.get('meet_id', '')
            with _lock:
                gone = meet_id in _retained and meet_id not in _meets
                if gone:
                    del _retained[meet_id]
            if gone:
                await run_in_threadpool(_delete_meet_files, meet_id)
        elif action == 'set_analytics':
            creds = _load_creds()
            creds['analytics_enabled'] = form.get('analytics_enabled') == '1'
            await run_in_threadpool(_save_creds, creds)
            return RedirectResponse('/admin', status_code=303)
        elif action == 'change_locale':
            locale = form.get('locale', '')
            creds  = _load_creds()
            creds['locale'] = locale
            await run_in_threadpool(_save_creds, creds)
            _locale_cache.clear()
            return RedirectResponse('/admin', status_code=303)
        elif action == 'change_credentials':
            t         = _load_cloud_strings(request)
            creds     = _load_creds()
            cur_pw    = form.get('current_password', '')
            new_user  = form.get('new_user', '').strip()
            new_pw1   = form.get('new_password', '')
            new_pw2   = form.get('new_password2', '')
            cur_hash, _ = _hash_password(cur_pw, creds['salt'])
            if not hmac.compare_digest(cur_hash, creds['password_hash']):
                error = t.get('err_wrong_password', 'Incorrect current password.')
            elif new_pw1 != new_pw2:
                error = t.get('err_password_mismatch', 'New passwords do not match.')
            elif not new_pw1:
                error = t.get('err_empty_password', 'Password cannot be empty.')
            else:
                creds['user'] = new_user or creds['user']
                creds['password_hash'], creds['salt'] = _hash_password(new_pw1)
                await run_in_threadpool(_save_creds, creds)
                return Response(
                    'Credentials updated — <a href="/admin">sign in with new credentials</a>',
                    status_code=401,
                    headers={'WWW-Authenticate': 'Basic realm="Splouch Admin"'})
            return render(request, 'admin.html', keys=keys,
                          active_meets=_admin_meet_list(),
                          t=t, creds_error=error,
                          user_name=_load_creds().get('user', 'Admin'),
                          locales=_available_locales(),
                          current_locale=_load_creds().get('locale', ''),
                          ui_lang_cookie=_ui_lang_cookie(request),
                          has_deploy=bool(os.environ.get('DEPLOY_WEBHOOK_URL')),
                          analytics_enabled=_analytics_enabled(),
                          **_picker_appearance())
        return RedirectResponse('/admin', status_code=303)

    await run_in_threadpool(_sweep_expired)
    return render(request, 'admin.html', keys=keys,
                  active_meets=_admin_meet_list(),
                  t=_load_cloud_strings(request), creds_error=None,
                  user_name=_load_creds().get('user', 'Admin'),
                  locales=_available_locales(),
                  current_locale=_load_creds().get('locale', ''),
                  ui_lang_cookie=_ui_lang_cookie(request),
                  has_deploy=bool(os.environ.get('DEPLOY_WEBHOOK_URL')),
                  analytics_enabled=_analytics_enabled(),
                  **_picker_appearance())


# ── WebSocket — /ws/relay (Pi connections) ─────────────────────────────────────

async def _on_relay_register(ws, sid, data):
    key  = data.get('key', '')
    keys = _load_keys()

    if key not in keys or not keys[key].get('active', False):
        await manager.send(ws, 'rejected', {'reason': 'invalid or inactive key'})
        return

    # Meet id is stable per (key, meet_uid): one relay key can publish several
    # meets (e.g. a meet split across days), each on its own picker card and
    # reattaching on reload. Legacy relays without a meet_uid keep one slot/key.
    meet_uid = data.get('meet_uid', '')
    if meet_uid:
        meet_id = _meet_id_for(key, meet_uid)
    else:
        meet_id = keys[key].get('meet_id')
        if not meet_id:
            meet_id = secrets.token_urlsafe(8)
            keys[key]['meet_id'] = meet_id
            await run_in_threadpool(_save_keys, keys)

    with _lock:                                   # fast: in-memory only
        # If this socket was publishing a different meet (operator switched
        # LENEX files), retire it so it stays available as schedule-only.
        prev_id  = _relay_sids.get(sid)
        prev_rec = None
        if prev_id and prev_id != meet_id:
            _retire_mem(prev_id)
            prev_rec = _record_copy_locked(prev_id) if prev_id in _retained else None

        prev = _meets.get(meet_id, {})          # already-live data (settings re-register)
        snap = _retained.get(meet_id, {})        # persisted snapshot (fresh reconnect)
        _meets[meet_id] = {
            'relay_key':        key,
            'relay_sid':        sid,
            'organizer':        keys[key]['organizer'],
            'name':             data.get('name', ''),
            'location':         data.get('location', ''),
            'sport':            data.get('sport', ''),
            'app_window_title': data.get('app_window_title', ''),
            'meet_date':        data.get('meet_date', ''),
            'settings':         data.get('settings', {}),
            'connected_at':     prev.get('connected_at') or datetime.datetime.now().strftime('%H:%M:%S'),
            'last_scoreboard':  prev.get('last_scoreboard', {}),
            'clock_at':         0.0,   # monotonic() of the last `running_time` sent
            'last_results':     prev.get('last_results', {}),
            'last_next_heats':  prev.get('last_next_heats', {}),
            # Restore the retained schedule on a fresh reconnect so it shows
            # immediately, before the relay re-sends its schedule_snapshot.
            'schedule_data':    prev.get('schedule_data') or snap.get('schedule_data', {}),
        }
        _relay_sids[sid] = meet_id
        _persist_meet_mem(meet_id, _meets[meet_id])
        rec = _record_copy_locked(meet_id)
    # Off the loop: write this meet's files (metadata + schedule + images); and
    # the retired meet's metadata, if this register displaced one.
    await run_in_threadpool(_write_meet_files, meet_id, rec, True, True)
    if prev_rec is not None:
        await run_in_threadpool(_write_meet_files, prev_id, prev_rec, False, False)

    await manager.send(ws, 'registered', {'meet_id': meet_id})
    await _emit_meet_live(meet_id, True)
    print(f'[cloud] {keys[key]["organizer"]} registered as meet {meet_id}', flush=True)


async def _on_relay_disconnect(sid):
    rec = None
    with _lock:
        meet_id = _relay_sids.pop(sid, None)
        meet    = _meets.get(meet_id) if meet_id else None
        # Guard against a reconnect race: only retire the meet if this socket is
        # still the one bound to it (a newer socket may have re-registered).
        retired = bool(meet and meet.get('relay_sid') == sid)
        if retired:
            _retire_mem(meet_id)
            rec = _record_copy_locked(meet_id) if meet_id in _retained else None
    if rec is not None:
        await run_in_threadpool(_write_meet_files, meet_id, rec, False, False)  # metadata only
    if retired:
        await _emit_meet_live(meet_id, False)
    if meet_id:
        print(f'[cloud] meet {meet_id} disconnected', flush=True)


async def _forward(sid, event, data):
    """Cache and broadcast a relay event to all attendees of the sending meet."""
    with _lock:
        meet_id = _relay_sids.get(sid)
        meet    = _meets.get(meet_id)
    if not meet_id or not meet:
        return

    if event == 'update_scoreboard':
        # `running_time` is the race clock, and the console sends it on every
        # timing tick. Forwarding that to every attendee is the traffic
        # notes/cloud_parity.md refused; dropping it outright left the phones with
        # no clock at all. So throttle it: the client re-bases on what we send and
        # interpolates in between (docs/mobile-features.md `L-12`).
        clock = data.pop('running_time', None)

        # Cache the frame without the clock. The join replay sends the snapshot
        # with no way to say how old it is, and a stale clock is worse than none —
        # a client joining mid-heat waits for the next re-base instead.
        meet['last_scoreboard'].update(data)

        # A frame that also moves a lane's running flag is a start, a touch, the
        # end of the console's split hold or a finish: rare, and exactly where the
        # value has to be right.
        if clock is not None:
            now = time.monotonic()
            if (now - meet.get('clock_at', 0.0) >= _CLOCK_SYNC_SECS
                    or any(k.startswith('lane_running') for k in data)):
                meet['clock_at']     = now
                data['running_time'] = clock

        await manager.broadcast(_ch('scoreboard', meet_id), event, data)
    elif event == 'results_snapshot':
        meet['last_results'] = data
        await manager.broadcast(_ch('results', meet_id), event, data)
    elif event == 'next_heats':
        meet['last_next_heats'] = data
        await manager.broadcast(_ch('results', meet_id), event, data)
    elif event == 'schedule_snapshot':
        meet['schedule_data'] = data
        with _lock:                               # fast: in-memory only
            _persist_meet_mem(meet_id, meet)
            rec = _record_copy_locked(meet_id)
        # Off the loop: metadata + the (changed) schedule; images are untouched.
        await run_in_threadpool(_write_meet_files, meet_id, rec, True, False)
        await manager.broadcast(_ch('schedule', meet_id), 'schedule_update')


async def _on_relay_reload(sid):
    with _lock:
        meet_id = _relay_sids.get(sid)
    if not meet_id:
        return
    await manager.broadcast(_ch('scoreboard', meet_id), 'reload')
    await manager.broadcast(_ch('results', meet_id), 'reload')


async def _on_relay_stats(ws, sid):
    """Reply to a relay's stats request with attendance counts for its *own*
    meet. The meet id is taken from the socket's registration, so a relay can
    only ever read its own numbers. Sends `{'enabled': False}` when the admin
    hasn't turned analytics on, so the operator panel can say so."""
    with _lock:
        meet_id = _relay_sids.get(sid)
    if not meet_id:
        return
    if not _analytics_enabled():
        await manager.send(ws, 'stats', {'enabled': False})
        return
    counts = await run_in_threadpool(_attendee_counts, meet_id)
    await manager.send(ws, 'stats', {'enabled': True, 'counts': counts})


@app.websocket('/ws/relay')
async def ws_relay(ws: WebSocket):
    await ws.accept()
    sid = id(ws)
    try:
        while True:
            msg = await ws.receive_json()
            event, data = msg.get('event'), msg.get('data') or {}
            if event == 'register':
                await _on_relay_register(ws, sid, data)
            elif event in ('update_scoreboard', 'results_snapshot',
                           'next_heats', 'schedule_snapshot'):
                await _forward(sid, event, data)
            elif event == 'reload':
                await _on_relay_reload(sid)
            elif event == 'get_stats':
                await _on_relay_stats(ws, sid)
            elif event == 'ping':
                await manager.send(ws, 'pong')
    except WebSocketDisconnect:
        pass
    finally:
        await _on_relay_disconnect(sid)


# ── WebSocket — attendee namespaces ────────────────────────────────────────────

async def _emit_meet_live(meet_id, live):
    """Tell scoreboard/results attendees whether a relay is currently feeding this
    meet, so live-only UI (the running-lane glow, the results board) reverts to its
    idle state when no console is connected."""
    for ns in ('scoreboard', 'results'):
        await manager.broadcast(_ch(ns, meet_id), 'meet_live', {'live': live})


@app.websocket('/ws/scoreboard')
async def ws_scoreboard(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get('event') == 'ping':
                await manager.send(ws, 'pong')
                continue
            if msg.get('event') != 'join_meet':
                continue
            data = msg.get('data') or {}
            meet_id = data.get('meet_id', '')
            with _lock:
                meet = _get_meet(meet_id)
                live = meet_id in _meets
            if not meet:
                continue
            manager.join(ws, _ch('scoreboard', meet_id))
            _log_connection(meet_id, data.get('vid', ''), 'scoreboard')
            # Send live status first so the page knows whether to animate before
            # the cached scoreboard snapshot is applied.
            await manager.send(ws, 'meet_live', {'live': live})
            if meet.get('last_scoreboard'):
                await manager.send(ws, 'update_scoreboard', meet['last_scoreboard'])
    except WebSocketDisconnect:
        pass
    finally:
        manager.leave_all(ws)


@app.websocket('/ws/results')
async def ws_results(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get('event') == 'ping':
                await manager.send(ws, 'pong')
                continue
            if msg.get('event') != 'join_meet':
                continue
            data = msg.get('data') or {}
            meet_id = data.get('meet_id', '')
            with _lock:
                meet = _get_meet(meet_id)
                live = meet_id in _meets
            if not meet:
                continue
            manager.join(ws, _ch('results', meet_id))
            _log_connection(meet_id, data.get('vid', ''), 'results')
            # Live status first, so the page reverts to "Waiting…" when no relay is feeding.
            await manager.send(ws, 'meet_live', {'live': live})
            if meet.get('last_results'):
                await manager.send(ws, 'results_snapshot', meet['last_results'])
            if meet.get('last_next_heats'):
                await manager.send(ws, 'next_heats', meet['last_next_heats'])
    except WebSocketDisconnect:
        pass
    finally:
        manager.leave_all(ws)


@app.websocket('/ws/schedule')
async def ws_schedule(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get('event') == 'ping':
                await manager.send(ws, 'pong')
                continue
            if msg.get('event') != 'join_meet':
                continue
            data = msg.get('data') or {}
            meet_id = data.get('meet_id', '')
            with _lock:
                meet = _get_meet(meet_id)
            if not meet:
                continue
            manager.join(ws, _ch('schedule', meet_id))
            _log_connection(meet_id, data.get('vid', ''), 'schedule')
    except WebSocketDisconnect:
        pass
    finally:
        manager.leave_all(ws)


# ── Theme defaults (fallback when Pi hasn't sent settings yet) ─────────────────

_DEFAULT_COLORS = {
    'bg': '#0d0d0d', 'header_bg': '#1a1a1a', 'header_border': '#2e2e2e',
    'header_label': '#ffffff', 'header_value': '#e0e0e0',
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


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000)
