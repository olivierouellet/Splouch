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
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import time
import urllib.request
from contextlib import asynccontextmanager
from typing import Any

from fastapi import (Depends, FastAPI, HTTPException, Request, UploadFile, WebSocket,
                     WebSocketDisconnect)
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

# ── Re-exports ─────────────────────────────────────────────────────────────────
# Where things live now. Both used to be sections of this module, and the tests
# and route handlers reach many of these names directly, so they stay resolvable
# here. Patch the owning module, not this one, when redirecting a path in a test:
# the code that reads `CREDS_FILE` now lives in `paths`.
# E402 on the imports below: this file binds module-level aliases (`_ch`,
# `_load_creds`, …) between the import groups, so the later groups sit past
# the top of the file on purpose.
#
# The `cloud_` prefix is not decoration: `server/` and `cloud/` are both flat on
# sys.path when the suite runs, so a plain `bus.py` here would shadow the Pi's —
# which is exactly why `cloud_server.py` is named that way too.
import cloud_bus
from cloud_paths import (DATA_DIR, KEYS_FILE, STATIC_DIR,
                         SHARED_TEMPLATES_DIR, _HERE)
from cloud_bus import manager
_ch = cloud_bus.ch
import cloud_analytics  # noqa: E402
import cloud_auth  # noqa: E402
from cloud_auth import require_admin  # noqa: E402
from cloud_analytics import (log_connection as _log_connection,  # noqa: E402
                             analytics_enabled as _analytics_enabled,
                             attendee_count as _attendee_count,
                             attendee_counts as _attendee_counts,
                             analytics_flush_loop as _analytics_flush_loop,
                             analytics_prune as _analytics_prune,
                             flush_analytics as _flush_analytics)
_ANALYTICS_WINDOWS = cloud_analytics._ANALYTICS_WINDOWS
# Names the routes and the tests still reach for directly.
_load_keys      = cloud_auth.load_keys
_save_keys      = cloud_auth.save_keys
_load_creds     = cloud_auth.load_creds
_save_creds     = cloud_auth.save_creds
_hash_password  = cloud_auth.hash_password
_check_admin    = cloud_auth.check_admin
_ADMIN_FAIL_MAX = cloud_auth._ADMIN_FAIL_MAX
_admin_fails    = cloud_auth._admin_fails
import cloud_i18n  # noqa: E402
from cloud_i18n import (_DEFAULT_COLORS, _DEFAULT_FONTS)  # noqa: E402
# The QR-code link shape, shared verbatim with the Pi that mints one — `cloud_paths`
# has already put `shared/py/` on the path.
import splouch_links  # noqa: E402
from splouch_links import INVITE_PARAM, INVITE_PATH  # noqa: E402
# The store's two dicts and their lock are imported as objects, not copied values:
# they are bound once in `cloud_store` and only ever mutated in place, so every
# `with _lock:` block and every `_meets[...]` in this file goes on addressing the
# same thing it always did.
from cloud_store import (_meets, _relay_sids, _retained, _lock, _write_meet_files, _delete_meet_files, _persist_meet_mem, _record_copy_locked, _meet_id_for, _retire_mem, _sweep_expired, _get_meet,  # noqa: E402
                         _merged_meets)
_available_locales = cloud_i18n.available_locales
_strings           = cloud_i18n.strings
_panel_strings     = cloud_i18n.panel_strings
_resolve_labels    = cloud_i18n.resolve_labels
_i18n_bundle       = cloud_i18n.i18n_bundle
_locale_name       = cloud_i18n.locale_name


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



# The visitor's choice, per device and per server (docs/app.md `T-08`): the picker
# writes these, every meet page reads them, and the URL carries nothing. The Pi
# uses the same names (server/web.py), so the rule is one rule.
PREF_COOKIES = {'lang': 'splouch_lang', 'style': 'splouch_style'}
PREF_MAX_AGE = 365 * 24 * 3600


def _pref(request, name, valid):
    """`?name=` for this request, else the cookie, else '' — invalid values ignored.

    The query string still wins for one request so a shared link opens the way its
    sender saw it and an old bookmark keeps working; `_remember_prefs` then writes
    it to the cookie so the next page needs no parameter at all.
    """
    for value in (request.query_params.get(name, ''),
                  request.cookies.get(PREF_COOKIES[name], '')):
        if value in valid:
            return value
    return ''


def _remember_prefs(request, response):
    """Turn a valid `?lang=` / `?style=` on this request into the device cookie."""
    for name, valid in (('lang', {c for c, _ in _available_locales()}),
                        ('style', ('short', 'long'))):
        value = request.query_params.get(name, '')
        if value in valid and value != request.cookies.get(PREF_COOKIES[name]):
            response.set_cookie(PREF_COOKIES[name], value, max_age=PREF_MAX_AGE,
                                samesite='lax')
    return response


def _client_lang(request, meet):
    """The language to render a meet page in: the visitor's choice, else the meet's.

    The picker stores the choice in a cookie and every meet page reads it, so one
    control reaches the tabs (docs/app.md `T-06`, `T-08`); `?lang=` wins for one
    request so a shared link opens as sent. An unknown code falls back rather than
    erroring: a stale bookmark must not break the board.
    """
    return _pref(request, 'lang', {code for code, _ in _available_locales()}) \
        or _meet_lang(meet)


def _client_labels(meet, lang, style):
    """Column headers for a chosen language and style.

    With no choice made this is exactly `settings.labels` — what the operator picked,
    byte for byte. With one, it is the shipped table for that language, the same
    body `GET /i18n/{lang}` serves (api.md §5.9).
    """
    s = meet.get('settings', {})
    if lang == _meet_lang(meet) and style == s.get('label_style', 'short'):
        return s.get('labels', {})
    labels = dict(_i18n_bundle(lang)['labels'].get(style, {}))
    # The relay folds a few [mobile] strings into `labels`; keep whatever else the
    # meet sent so nothing that read them starts rendering blank.
    for key, value in s.get('labels', {}).items():
        labels.setdefault(key, value)
    return labels


def _client_style(request, meet):
    """`short` or `long` — the visitor's pick, else the operator's (`T-09`)."""
    return _pref(request, 'style', ('short', 'long')) \
        or meet.get('settings', {}).get('label_style', 'short')


def _etagged(request, payload):
    """JSON with an ETag, and a 304 when the client already has that body.

    One response serves every meet on this server, so it is worth caching and worth
    revalidating cheaply. `no-cache` means revalidate, not do not cache.
    """
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
    headers = {'ETag': etag, 'Cache-Control': 'no-cache'}
    if request.headers.get('if-none-match') == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type='application/json', headers=headers)


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
    # Public picker: the visitor's stored choice (`?lang=`, then the cookie) wins,
    # then the browser language; the server-wide default (creds['locale']) is only
    # a fallback when neither names a language this server ships. Set from the
    # Appearance tab — see change_locale.
    return (_pref(request, 'lang', {c for c, _ in _available_locales()})
            or _browser_lang(request) or _server_lang(request))

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
    """`[chrome]` underneath `[cloud]`: the sidebar and theme switcher are the same
    markup as the Pi's Settings panel, so their words live in one section both read."""
    lang = _admin_lang(request)
    return {**_panel_strings(lang, 'chrome'), **_panel_strings(lang, 'cloud')}

def _meet_lang(meet):
    return meet.get('settings', {}).get('locale') or 'en'


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




# How often a running race clock is re-based on the attendees' devices. They tick
# it themselves in between, so this is a correction rate, not a frame rate: it
# bounds drift and how long a phone joining mid-heat waits for a clock.
_CLOCK_SYNC_SECS = 2.0

# What the Appearance tab accepts. The logo is drawn by a browser `<img>`, so the
# list is the formats every current browser renders; the icon is also fed to the web
# manifest, which names `image/png` for both sizes, so it stays PNG-only.
#
# The cap is small on purpose: both images live base64-encoded inside
# credentials.json, and `_load_creds()` re-reads and re-parses that file on every
# request. A few megabytes of logo would be paid for on every page view.
LOGO_MIME_TYPES = ('image/png', 'image/jpeg', 'image/gif', 'image/webp', 'image/svg+xml')
ICON_MIME_TYPES = ('image/png',)
MAX_IMAGE_BYTES = 2 * 1024 * 1024


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
        'picker_max_upload':        MAX_IMAGE_BYTES,
    }


def _admin_meet_list():
    """Merged live + retained meets for the admin table, live first."""
    out: list[dict[str, Any]] = []
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
            # Which console the operator is running on, straight off the relay's
            # `settings` block (docs/api.md §6). `key` is diagnostic — *which console
            # did this meet run on* — and the admin table is exactly the support
            # screen it was published for. A relay too old to send one leaves both
            # None: the table says so rather than guessing a console for it.
            console = m.get('settings', {}).get('console') or {}
            out.append({
                'id':              mid,
                'name':            m.get('name', ''),
                'location':        m.get('location', ''),
                'sport':           m.get('sport', ''),
                'organizer':       m.get('organizer', ''),
                'connected_at':    m.get('connected_at', ''),
                'language':        _locale_name(_meet_lang(m)),
                'console':         console.get('key', ''),
                'console_timed':   console.get('timed'),
                'live':            live,
                'expires_at':      exp,
                'expires_display': disp,
                'expires_input':   (exp or '')[:16],   # for <input type=datetime-local>
            })
    out.sort(key=lambda x: (not x['live'], (x['name'] or '').lower()))
    return out



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
    return _remember_prefs(request, render(request, 'picker.html', meets=meets,
        t=_strings(lang, 'mobile'),
        lang=lang,
        # For the display-preferences menu: the languages this server can serve.
        locales=_available_locales(),
        picker_title=brand['title'],
        picker_window_title=brand['window_title'],
        picker_logo=brand['has_logo'],
        picker_logo_above=brand['logo_above'],
        analytics_enabled=_analytics_enabled()))


# The contracts this build implements, for the handshake below. Bumped with the
# headers of docs/api.md and docs/app.md, which a test pins.
API_CONTRACT    = 'v2'
APP_CONTRACT    = 'v1'

SERVERS_FILE = os.path.join(DATA_DIR, 'servers.json')


@app.get('/server', tags=['Public'])
def route_server():
    """Who this server is — the handshake a native client makes before anything else.

    An app can be pointed at a Pi or at a cloud (docs/app.md `P-11`),
    and the two are not interchangeable: a Pi has one meet and no picker, this has
    many. Guessing from a 404 on `/meets` would be a protocol by accident. It also
    validates a hand-typed address before a client saves it, and carries the
    contract versions.
    """
    return {
        'kind':     'cloud',
        'name':     _picker_branding().get('title') or 'Splouch',
        'contract': {'api': API_CONTRACT, 'app': APP_CONTRACT},
    }


@app.get('/servers', tags=['Public'])
def route_servers(request: Request):
    """Servers a client may offer to connect to — a directory, not a whitelist.

    Fetched rather than compiled into an app, for the reason `T-05` gives about
    strings: a club standing up its own instance must not need a store release to
    become reachable. This server is always first and is derived from the request,
    so the endpoint is useful with no configuration at all; anything further comes
    from `servers.json` in the data directory, deduplicated by URL.

    A client keeps its own additions (docs/app.md `P-13`) — this list
    informs the menu, it does not replace what the user typed.
    """
    here = str(request.base_url).rstrip('/')
    servers = [{'name': _picker_branding().get('title') or 'Splouch',
                'url': here, 'kind': 'cloud'}]
    try:
        with open(SERVERS_FILE, 'rb') as f:
            extra = json.load(f)
    except Exception:
        extra = []
    seen = {here}
    for entry in extra if isinstance(extra, list) else []:
        url = str(entry.get('url', '')).rstrip('/')
        if not url or url in seen:
            continue
        seen.add(url)
        servers.append({'name': entry.get('name') or url,
                        'url': url, 'kind': entry.get('kind', 'cloud')})
    return {'servers': servers}


# ── QR-code hand-off (`app.md` `P-16`) ─────────────────────────────────────────
# A poster at a pool carries `https://<this host>/add?server=<the pool's Pi>`. With
# the app installed the OS opens it; without it, nothing intercepts it and the
# browser lands on `GET /add` below, which is the only page whose absence a
# spectator meets as a 404 after scanning something.
#
# The two `/.well-known/` files are what make the first half true, and they are
# the half a deploy forgets. Android fetches `assetlinks.json` at install time,
# follows no redirects, and while it is missing
# `adb shell pm get-app-links app.splouch.android` reports `1024` and the OS shows
# a chooser instead of opening the app.
#
# None of it is a constant here. A certificate fingerprint is per-keystore and
# per-build, and with Play App Signing the one that must be published is the
# *App signing* key's, not the upload key's — a value the developer reads off the
# Play Console and this repo cannot know. So it is configuration, from either of
# two places, and the two accumulate rather than override: the release fingerprint
# goes in the environment once at deploy time, and a debug one can be added to
# `applinks.json` in the data dir to test a debug build without cutting a release.
APPLINKS_FILE = os.path.join(DATA_DIR, 'applinks.json')

# The Android application id. Fixed by the app's own manifest, not by a keystore,
# so unlike the fingerprints it has a real default here.
ANDROID_PACKAGE = 'app.splouch.android'

# `<TEAMID>.<bundle id>` from `Splouch-ios` — the team and bundle the Xcode project
# is configured with, not a guess. Also a property of the app rather than of a
# signing key, so it too defaults rather than being required.
IOS_APP_IDS = ('L86UD2L8Q5.app.splouch.ios',)


def _applinks_file():
    """`applinks.json` from the data dir, or `{}`. Never raises."""
    try:
        with open(APPLINKS_FILE, 'rb') as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_list(value):
    """A config value that may be a list, or one string holding several."""
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [part for part in re.split(r'[,\s]+', str(value or '')) if part]


def _fingerprints(*sources):
    """Normalised SHA-256 certificate fingerprints, in order, without duplicates.

    Accepts what the two tools that produce them print: `keytool -list -v` uses
    the colon-separated form, and the Play Console's *App signing* page can be
    copied either way. Anything that is not 32 bytes of hex is dropped rather
    than served — a malformed entry invalidates the whole file for Android, so
    one typo in an env var would silently take the *working* fingerprint down
    with it.
    """
    out = []
    for source in sources:
        for raw in _as_list(source):
            hexed = raw.replace(':', '').strip().upper()
            if len(hexed) != 64 or any(c not in '0123456789ABCDEF' for c in hexed):
                continue
            value = ':'.join(hexed[i:i + 2] for i in range(0, 64, 2))
            if value not in out:
                out.append(value)
    return out


def _app_links():
    """Who the apps are, for the two `/.well-known/` files.

    The environment is the deploy's answer and the data file is the operator's;
    the lists are the union of both, and the package name is a single value the
    file may override.
    """
    stored = _applinks_file()
    ios = [a for a in dict.fromkeys(_as_list(os.environ.get('IOS_APP_IDS', ''))
                                    + _as_list(stored.get('ios_app_ids')))]
    return {
        'android_package': (str(stored.get('android_package', ''))
                            or os.environ.get('ANDROID_PACKAGE_NAME', '')
                            or ANDROID_PACKAGE),
        'android_fingerprints': _fingerprints(
            os.environ.get('ANDROID_CERT_FINGERPRINTS', ''),
            stored.get('android_fingerprints')),
        'ios_app_ids': ios or list(IOS_APP_IDS),
    }


def _store_links():
    """Store URLs for the native apps, as `P-10` says to serve them: as data.

    Absent until an app is listed, and absent is meaningful — a client hides the
    affordance rather than showing a dead button, and this page does the same. A
    listing that moves is an env var or a line in `applinks.json`, never a
    release: the reason `P-10` calls it a hand-off and not a feature.

    Only `https` is offered. These are links this server hands a phone, and a
    store's real address has never been anything else.
    """
    stored = _applinks_file()
    out = {}
    for key, env in (('android', 'STORE_URL_ANDROID'), ('ios', 'STORE_URL_IOS')):
        url = str(stored.get(f'store_{key}', '') or os.environ.get(env, '')).strip()
        if url.lower().startswith('https://'):
            out[key] = url
    return out


def _json(payload):
    """A JSON body with the content type spelled out rather than inferred.

    `apple-app-site-association` has no file extension on purpose — Apple fetches
    that exact path — so nothing downstream can guess its type from a name.
    """
    return Response(json.dumps(payload, indent=2, sort_keys=True).encode(),
                    media_type='application/json')


@app.get('/.well-known/assetlinks.json', tags=['Public'], include_in_schema=False)
def route_assetlinks():
    """Android App Links: which app may open `https://<this host>/add`.

    **404 while no fingerprint is configured**, rather than a well-formed file
    with an empty list. Both leave the app unverified, but only one of them says
    so to `curl -i`: an empty list looks deployed and fails at install time on a
    phone nobody is watching.
    """
    links = _app_links()
    if not links['android_fingerprints']:
        raise HTTPException(status_code=404)
    return _json([{
        'relation': ['delegate_permission/common.handle_all_urls'],
        'target': {'namespace': 'android_app',
                   'package_name': links['android_package'],
                   'sha256_cert_fingerprints': links['android_fingerprints']},
    }])


@app.get('/.well-known/apple-app-site-association', tags=['Public'],
         include_in_schema=False)
def route_aasa():
    """iOS Universal Links, the twin of the file above.

    `components` names the path *and* the query parameter, so this host claims
    `/add?server=…` and nothing else of the site: every other page — the picker,
    a meet, `/admin` — keeps opening in the browser where it belongs.

    No `.json` extension: Apple fetches this exact path, and adding one would
    serve a file nothing asks for.
    """
    return _json({'applinks': {'details': [
        {'appIDs': _app_links()['ios_app_ids'],
         'components': [{'/': INVITE_PATH, '?': {INVITE_PARAM: '?*'}}]},
    ]}})


@app.get('/add', tags=['Public'])
def route_add(request: Request):
    """Where a scanned code lands when the app is not installed (`app.md` `P-16`).

    **It always answers.** A spectator standing in front of a poster has already
    done the one thing the poster asked; a 404 here is the worst outcome in the
    feature, and worse than any of the ways the link itself can be wrong. So a
    missing, malformed or cleartext-to-nowhere `server` renders the page without
    a server rather than an error.

    It shows the origin the code named, so the reader can see what they scanned,
    and offers the store links. It does **not** redirect, or try a scheme, or
    claim to be the app: a phone that has the app never arrives here — the OS
    intercepted the link long before the request — so anything this page did to
    reach the app would only ever run on a phone that cannot.

    The origin is held to the same rule the client applies (`P-12`, `P-13`), via
    the same helper the Pi mints with. A page that displayed an address the app
    would refuse would be sending the reader to a dead end with a store link
    under it.
    """
    lang = _picker_lang(request)
    return _remember_prefs(request, render(request, 'add.html',
        lang=lang,
        t=_strings(lang, 'mobile'),
        server=splouch_links.parse_origin(request.query_params.get(INVITE_PARAM, '')),
        stores=_store_links(),
        **_picker_branding()))


@app.get('/locales', tags=['Public'])
def route_locales(request: Request):
    """The languages this server can serve — for a client offering the choice."""
    return _etagged(request, [{'code': c, 'name': n} for c, n in _available_locales()])


@app.get('/i18n/{lang}', tags=['Public'])
def route_i18n(lang: str, request: Request):
    """One language: app chrome plus both label styles (§5.9).

    No meet in the path on purpose — the table is a property of this server's locale
    files, identical for every meet, so it is fetched once per language and cached
    rather than repeated inside each meet's config.
    """
    return _etagged(request, _i18n_bundle(lang))


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
    ``analytics_enabled`` is true, matching the web picker.

    ``stores`` carries `P-10`'s hand-off — the native apps' store URLs, keyed by
    platform and present only for a platform that has one, so a client hides the
    affordance instead of offering a dead link. Empty until an app is listed, and
    the same dict `GET /add` renders its buttons from."""
    lang = _picker_lang(request)
    strings = _strings(lang, 'mobile')
    return {
        **_picker_branding(),
        'lang':              lang,
        'analytics_enabled': _analytics_enabled(),
        'stores':            _store_links(),
        'strings':           {k: strings[k] for k in _PICKER_STRING_KEYS if k in strings},
    }


@app.get('/mobile', tags=['Public'])
def route_mobile(request: Request):
    meet_id = request.query_params.get('meet', '')
    with _lock:
        meet = _get_meet(meet_id)
    if not meet:
        return RedirectResponse('/', status_code=303)
    return _remember_prefs(request, render(request, 'mobile.html',
                  meet_id=meet_id,
                  app_title=(meet.get('app_window_title') or meet['name'] or 'Splouch'),
                  t=_strings(_client_lang(request, meet), 'mobile'),
                  lang=_client_lang(request, meet),
                  # No Results tab for a meet run by hand: nothing will ever fill it
                  # (docs/app.md `A-11`). True for a relay too old to say, which is
                  # what every relay before this said by having a console at all.
                  show_results=meet.get('settings', {})
                                  .get('console', {}).get('timed', True),
                  # Passed down to the tab iframes so one choice covers all three.
                  ui_style=_client_style(request, meet)))


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
        # Live-board only — the Results tab has no running lanes to count lengths
        # for, so `results.html` does not take this.
        show_laps=s.get('show_laps', False),
        lap_direction=s.get('lap_direction', 'up'),
        # Merge over the defaults rather than falling back wholesale: a relay that
        # sends a partial theme_colors would otherwise leave every unlisted CSS
        # variable empty. Matches route_results and route_schedule.
        theme_colors={**_DEFAULT_COLORS, **s.get('theme_colors', {})},
        theme_fonts={**_DEFAULT_FONTS,  **s.get('theme_fonts',  {})},
        labels=_client_labels(meet, _client_lang(request, meet), _client_style(request, meet)),
        # The vocabulary `event_name_parts` composes against, in the language the
        # page is rendered in (docs/app.md `T-11`).
        event_vocab=_strings(_client_lang(request, meet), 'event_name'),
        lang=_client_lang(request, meet),
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
        t=_strings(_client_lang(request, meet), 'mobile'),
        theme_colors={**_DEFAULT_COLORS, **s.get('theme_colors', {})},
        # Merged, not a wholesale fallback: a relay sending only one font would
        # otherwise leave the other two CSS variables empty. Same as route_live.
        theme_fonts={**_DEFAULT_FONTS,  **s.get('theme_fonts',  {})},
        labels=_client_labels(meet, _client_lang(request, meet), _client_style(request, meet)),
        # The vocabulary `event_name_parts` composes against, in the language the
        # page is rendered in (docs/app.md `T-11`).
        event_vocab=_strings(_client_lang(request, meet), 'event_name'),
        lang=_client_lang(request, meet),
    )


def _build_heats_json(sched):
    if not sched or not sched.get('events'):
        return []
    names      = sched.get('names', {})
    name_parts = sched.get('name_parts', {})
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
                'event_name_parts': name_parts.get(ev_str),
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
        heats=heats,
        has_meet=bool(heats),
        meet_name=meet['name'],
        t=_strings(_client_lang(request, meet), 'mobile'),
        labels=_client_labels(meet, _client_lang(request, meet), _client_style(request, meet)),
        event_vocab=_strings(_client_lang(request, meet), 'event_name'),
        theme_colors={**_DEFAULT_COLORS, **s.get('theme_colors', {})},
        theme_fonts={**_DEFAULT_FONTS,  **s.get('theme_fonts', {})},
        lang=_client_lang(request, meet),
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
    # An SVG logo is a document, not a bitmap: opened directly (rather than through
    # the `<img>` on the picker page, which already inerts it) it would run its own
    # script on this origin. The sandbox costs nothing for the other formats.
    return Response(data, media_type=mime,
                    headers={'Cache-Control': 'public, max-age=300',
                             'Content-Security-Policy': "default-src 'none'; sandbox",
                             'X-Content-Type-Options': 'nosniff'})


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


def _form_text(form, key):
    """A form field as text.

    Starlette types a form value `str | UploadFile`, because a client is free to
    post a file part under a name this server means as text. Reading `.strip()`
    off that raised AttributeError — a 500 for what is really a bad request — so
    anything that is not text reads as absent.
    """
    value = form.get(key, '')
    return value.strip() if isinstance(value, str) else ''


async def _read_image(upload, allowed):
    """Bytes + settled MIME type of an uploaded image, or ValueError with the reason.

    The browser's `content_type` is a claim, and an empty one is common enough (some
    clients send `application/octet-stream` for anything they do not recognise) that
    the filename extension is a better second opinion than a blanket default. Both
    have to agree with `allowed` before the bytes are stored — the form's `accept`
    filters the file dialog and nothing else, so drag-and-drop and any non-browser
    client arrive here unchecked.
    """
    mime = (upload.content_type or '').split(';')[0].strip().lower()
    if mime in ('', 'application/octet-stream'):
        mime = (mimetypes.guess_type(upload.filename)[0] or '').lower()
    if mime == 'image/jpg':          # non-standard, but some tools still send it
        mime = 'image/jpeg'
    if mime not in allowed:
        names = ', '.join(m.split('/')[-1].split('+')[0].upper() for m in allowed)
        raise ValueError(f'Unsupported image format. Accepted: {names}.')
    data = await upload.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f'Image is too large (max {MAX_IMAGE_BYTES // (1024 * 1024)} MB).')
    return data, mime


@app.post('/admin/picker_appearance', tags=['Admin'], response_model=ActionResult,
          response_model_exclude_none=True, dependencies=[Depends(require_admin)])
async def route_picker_appearance(request: Request):
    form  = await request.form()
    creds = _load_creds()
    if 'picker_title' in form:
        creds['picker_title']        = _form_text(form, 'picker_title')
        creds['picker_window_title'] = _form_text(form, 'picker_window_title')
        creds['picker_logo_above']   = form.get('picker_logo_above') == '1'
    if form.get('picker_logo_clear') == '1':
        creds['picker_logo_b64'] = ''
        creds.pop('picker_logo_mime', None)
    else:
        logo = form.get('picker_logo')
        if isinstance(logo, UploadFile) and logo.filename:
            try:
                data, mime = await _read_image(logo, LOGO_MIME_TYPES)
            except ValueError as e:
                return {'ok': False, 'error': str(e)}
            creds['picker_logo_b64']  = base64.b64encode(data).decode()
            creds['picker_logo_mime'] = mime
    if form.get('picker_icon_clear') == '1':
        creds['picker_icon_b64'] = ''
    else:
        icon = form.get('picker_icon')
        if isinstance(icon, UploadFile) and icon.filename:
            try:
                data, _ = await _read_image(icon, ICON_MIME_TYPES)
            except ValueError as e:
                return {'ok': False, 'error': str(e)}
            creds['picker_icon_b64'] = base64.b64encode(data).decode()
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
    if not isinstance(uploaded, UploadFile):
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
    if not isinstance(uploaded, UploadFile):
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
            org = _form_text(form, 'organizer')
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
            raw     = _form_text(form, 'expires_at')
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
            cloud_i18n._locale_cache.clear()
            return RedirectResponse('/admin', status_code=303)
        elif action == 'change_credentials':
            t         = _load_cloud_strings(request)
            creds     = _load_creds()
            cur_pw    = form.get('current_password', '')
            new_user  = _form_text(form, 'new_user')
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
        # interpolates in between (docs/app.md `L-12`).
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


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000)
