"""Splouch control plane: the picker, the admin panel, and the meet registry.

The half of the relay that decides who and where (docs/architecture/scaling.md).
Low traffic, one instance, the only holder of the store — organizers and their
keys, the admin login and settings, every meet's metadata, and the attendance
counts, all in Postgres (`cloud_db`). The other half, the workers
(`cloud_server`), carry the frames: they hold live meets in memory and report
here over the internal API at the bottom of this module.

Public routes are the picker (`/`, `/meets`, `/picker/config` and its images),
the server's identity (`/server`, `/servers`), the QR hand-off (`/add`,
`/.well-known/*`), `/privacy`, and the locale tables. `/admin` is every tab of
the panel.
"""

import asyncio
import base64
import datetime
import hmac
import io
import json
import mimetypes
import os
import re
import traceback
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import asynccontextmanager, suppress
from typing import Any

import resvg_py
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import ExifTags, Image, ImageOps
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

# Starlette's, not FastAPI's: `request.form()` yields Starlette's UploadFile, and
# FastAPI's is a subclass of it, so an isinstance check against FastAPI's never
# matches and every uploaded file reads as absent.
from starlette.datastructures import UploadFile

import cloud_analytics
import cloud_attendance
import cloud_auth
import cloud_db
import cloud_i18n
import cloud_metrics
import cloud_registry
import cloud_ticket
import splouch_links
from cloud_analytics import (
    analytics_enabled as _analytics_enabled,
    attendee_count as _attendee_count,
)
from cloud_auth import require_admin
from cloud_paths import _HERE, DATA_DIR, STATIC_DIR
from cloud_web import (
    _browser_lang,
    _client_palette,
    _etagged,
    _meet_lang,
    _pref,
    _remember_prefs,
    render,
)
from splouch_i18n import READER_THEMES
from splouch_links import INVITE_PARAM, INVITE_PATH
from splouch_regions import COUNTRIES, clean_location

_ANALYTICS_WINDOWS = cloud_analytics._ANALYTICS_WINDOWS
_load_keys = cloud_auth.load_keys
_load_creds = cloud_auth.load_creds
_save_creds = cloud_auth.save_creds
_hash_password = cloud_auth.hash_password
_check_admin = cloud_auth.check_admin
_ADMIN_FAIL_MAX = cloud_auth._ADMIN_FAIL_MAX
_admin_fails = cloud_auth._admin_fails

_available_locales = cloud_i18n.available_locales
_strings = cloud_i18n.strings
_panel_strings = cloud_i18n.panel_strings
_i18n_bundle = cloud_i18n.i18n_bundle
_locale_name = cloud_i18n.locale_name


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


def _server_lang(request):
    # Admin's pinned locale wins; otherwise follow the browser.
    available = {code for code, _ in _available_locales()}
    stored = _load_creds().get("locale", "")
    if stored and stored in available:
        return stored
    return _browser_lang(request) or "en"


def _picker_lang(request):
    # Public picker: the visitor's stored choice (`?lang=`, then the cookie) wins,
    # then the browser language; the server-wide default (creds['locale']) is only
    # a fallback when neither names a language this server ships. Set from the
    # Appearance tab — see change_locale.
    return (
        _pref(request, "lang", {c for c, _ in _available_locales()})
        or _browser_lang(request)
        or _server_lang(request)
    )


def _admin_lang(request):
    # The admin panel language is per-device, independent of the server-wide
    # public-page locale above: the ui_lang cookie wins, else the browser, else
    # English. Same contract as the operator Settings panel.
    available = {code for code, _ in _available_locales()}
    cookie = request.cookies.get("ui_lang", "")
    if cookie in available:
        return cookie
    return _browser_lang(request) or "en"


def _ui_lang_cookie(request):
    # The explicitly-pinned panel language, or '' for "Auto" — so a stale cookie
    # for a removed locale reads as Auto, matching _admin_lang() resolution.
    available = {code for code, _ in _available_locales()}
    cookie = request.cookies.get("ui_lang", "")
    return cookie if cookie in available else ""


def _load_cloud_strings(request):
    """`[chrome]` underneath `[cloud]`: the sidebar and theme switcher are the same
    markup as the Pi's Settings panel, so their words live in one section both read."""
    lang = _admin_lang(request)
    return {**_panel_strings(lang, "chrome"), **_panel_strings(lang, "cloud")}


# How often the maintenance task runs. How long a live meet may go without its
# worker vouching for it is `cloud_registry.SILENT_AFTER_SECS`.
_MAINTENANCE_SECS = 30


def _maintain():
    """One maintenance pass: retire silent meets, sweep expired ones and their
    attendance numbers. Blocking — run off the loop. The visitor ids are pruned on
    the nodes that keep them (`cloud_attendance`)."""
    cloud_registry.retire_silent()
    cloud_registry.sweep_expired()
    cloud_analytics.forget_gone()
    cloud_registry.advance_rollout()


async def _maintenance_loop():
    while True:
        try:
            await run_in_threadpool(_maintain)
        except Exception as e:
            # A database hiccup must not end the loop: it is what retires meets
            # whose worker died.
            print(f"[control] maintenance failed: {e!r}", flush=True)
        await asyncio.sleep(_MAINTENANCE_SECS)


@asynccontextmanager
async def lifespan(app):
    os.makedirs(DATA_DIR, exist_ok=True)
    await run_in_threadpool(cloud_db.migrate)
    tasks = [
        asyncio.create_task(_maintenance_loop()),
        asyncio.create_task(cloud_metrics.lag_loop(cloud_metrics.CONTROL_LOOP_LAG)),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        cloud_db.close()


# Built-in docs are disabled here and re-served below behind `require_admin`, so
# the OpenAPI schema and Swagger/ReDoc UIs require admin Basic-auth credentials.
app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")


# What the Appearance tab accepts. The logo is drawn by a browser `<img>`, so the
# list is the formats every current browser renders; the icon is also fed to the web
# manifest, which names `image/png` for both sizes, so it stays PNG-only.
#
# The cap is small on purpose: both images live base64-encoded inside
# credentials.json, and `_load_creds()` re-reads and re-parses that file on every
# request. A few megabytes of logo would be paid for on every page view.
LOGO_MIME_TYPES = (
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
)
ICON_MIME_TYPES = ("image/png",)
MAX_IMAGE_BYTES = 2 * 1024 * 1024
# The apps draw the logo with the platform's bitmap decoder (UIImage, BitmapFactory)
# and neither reads SVG, so an SVG logo also gets a PNG copy, rendered once at
# upload, for every client whose Accept does not name SVG. The longest side is the
# Android app's own decode cap: anything larger it would only scale down.
LOGO_RASTER_SIDE = 1024


def _svg_aspect(root):
    """Width over height of an SVG, from width/height in one unit, else its viewBox."""

    def length(name):
        m = re.fullmatch(r"\s*([0-9]*\.?[0-9]+)\s*([a-z]*)\s*", root.get(name) or "")
        return (float(m[1]), m[2]) if m and float(m[1]) > 0 else None

    w, h = length("width"), length("height")
    if w and h and w[1] == h[1]:
        return w[0] / h[0]
    box = re.split(r"[\s,]+", (root.get("viewBox") or "").strip())
    with suppress(ValueError):
        if len(box) == 4 and float(box[2]) > 0 and float(box[3]) > 0:
            return float(box[2]) / float(box[3])
    return 1.0


def _upright(data):
    """A raster logo with its EXIF rotation applied to the pixels, or None if unreadable.

    Browsers and UIImage turn a photo by its EXIF orientation; Android's
    BitmapFactory ignores it, so a phone's JPEG drew sideways there alone. Only a
    still image that needs turning is re-encoded, so every other upload is kept
    byte for byte.
    """
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
        with Image.open(io.BytesIO(data)) as img:
            if img.getexif().get(ExifTags.Base.Orientation, 1) == 1:
                return data
            if getattr(img, "is_animated", False):
                return data
            fmt = img.format
            out = io.BytesIO()
            ImageOps.exif_transpose(img).save(out, fmt, quality=95)
            return out.getvalue()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None


def _svg_to_png(data):
    """The PNG copy of an SVG logo, or None when the SVG cannot be read.

    External references are dropped first. A browser's `<img>` loads none, so the
    copy matches what the web page shows, and resvg would otherwise open any path an
    `<image href>` names, drawing this server's own files into the logo.
    """
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return None
    for el in root.iter():
        for key in [k for k in el.attrib if k == "href" or k.endswith("}href")]:
            if not el.attrib[key].lstrip().startswith(("#", "data:")):
                del el.attrib[key]
    wide = _svg_aspect(root) >= 1
    # resvg's default DPI is 0, which turns every mm/pt/in size (as print tools
    # export) into an "invalid size"; 96 is the CSS pixel browsers assume.
    try:
        png = resvg_py.svg_to_bytes(
            svg_string=ET.tostring(root, "unicode"),
            width=LOGO_RASTER_SIDE if wide else None,
            height=None if wide else LOGO_RASTER_SIDE,
            dpi=96,
        )
        return bytes(png)
    except ValueError:
        return None


def _picker_appearance():
    creds = _load_creds()
    raw = creds.get("picker_title")
    raw_wt = creds.get("picker_window_title")
    return {
        "picker_title_form": "Splouch" if raw is None else raw,
        "picker_window_title_form": "Splouch" if raw_wt is None else raw_wt,
        "has_picker_logo": bool(creds.get("picker_logo_b64", "")),
        "has_picker_icon": bool(creds.get("picker_icon_b64", "")),
        "picker_logo_above": creds.get("picker_logo_above", False),
        "picker_max_upload": MAX_IMAGE_BYTES,
    }


def _admin_meet_list():
    """Live and retained meets for the admin table, live first."""
    out: list[dict[str, Any]] = []
    for m in cloud_registry.list_meets():
        exp = None if m["live"] else m["expires_at"]
        # Which console the operator is running on, straight off the relay's
        # `settings` block (docs/api.md §6). `key` is diagnostic — *which console
        # did this meet run on* — and the admin table is exactly the support
        # screen it was published for. A relay too old to send one leaves both
        # None: the table says so rather than guessing a console for it.
        console = (m["settings"] or {}).get("console") or {}
        out.append(
            {
                "id": m["id"],
                "name": m["name"],
                "location": m["location"],
                "sport": m["sport"],
                "organizer": m["organizer"],
                "connected_at": m["connected_at"],
                "language": _locale_name(_meet_lang(m)),
                "console": console.get("key", ""),
                "console_timed": console.get("timed"),
                "live": m["live"],
                # Running, not just connected: what a rollout waits for.
                "running": bool(m["live"] and cloud_registry.running(m)),
                "node": m["node"] or "",
                "worker": m["worker"],
                "expires_at": exp.isoformat(timespec="seconds") if exp else None,
                "expires_display": exp.strftime("%Y-%m-%d %H:%M") if exp else "",
                # for <input type=datetime-local>
                "expires_input": exp.isoformat(timespec="minutes") if exp else "",
            }
        )
    return out


# ── API docs (admin-gated) ─────────────────────────────────────────────────────
# 401 → the browser prompts for admin Basic-auth credentials, which it then also
# resends when Swagger UI fetches /openapi.json from the same origin.


@app.get(
    "/openapi.json", include_in_schema=False, dependencies=[Depends(require_admin)]
)
async def route_openapi():
    return app.openapi()


@app.get("/docs", include_in_schema=False, dependencies=[Depends(require_admin)])
async def route_docs():
    return get_swagger_ui_html(
        openapi_url="/openapi.json", title="Splouch Cloud API docs"
    )


@app.get("/redoc", include_in_schema=False, dependencies=[Depends(require_admin)])
async def route_redoc():
    return get_redoc_html(openapi_url="/openapi.json", title="Splouch Cloud API docs")


# ── Routes ─────────────────────────────────────────────────────────────────────


def _public_meet_list(here=""):
    """Meets for the picker — live and retained alike, live ones first.

    Shared by the HTML picker and ``GET /meets`` so a native client's list can
    never drift from the web one. Deliberately excludes anything an attendee has
    no business seeing (relay keys, expiry, connection times); the admin table
    has its own builder, ``_admin_meet_list``.

    Live first because a spectator opening the list is almost always after a
    meet that is running now; a retained one is a meet they are looking back
    at. The registry returns them in that order.

    ``base`` is where a client reaches each meet (`app.md` `C-11`): a live meet's
    worker, or ``here`` — this server — for a retained one. ``country`` and
    ``province`` are the organizer's (`P-01`, `P-17`).
    """
    return [
        {
            "id": m["id"],
            "name": m["name"],
            "location": m["location"],
            "sport": m["sport"],
            "organizer": m["organizer"],
            "meet_date": m["meet_date"],
            "offline": not m["live"],
            "has_picker_image": m["has_picker_image"],
            "country": m["country"],
            "province": m["province"],
            "base": cloud_registry.meet_base(m, here),
            "url": cloud_registry.page_url(m),
        }
        # A finished meet whose node is not reporting is left out until it is.
        for m in cloud_registry.list_meets(reachable_only=True)
    ]


def _picker_branding():
    """Operator-set look of the meet list, in the shape public clients consume.

    ``None`` and ``''`` mean different things for the titles: unset falls back to
    'Splouch', while an explicitly blank title hides it. Preserve that — see
    route_picker_appearance.
    """
    creds = _load_creds()
    raw = creds.get("picker_title")
    raw_wt = creds.get("picker_window_title")
    return {
        "title": "Splouch" if raw is None else raw,
        "window_title": "Splouch" if raw_wt is None else raw_wt,
        "has_logo": bool(creds.get("picker_logo_b64", "")),
        "logo_above": creds.get("picker_logo_above", False),
    }


# More meets than this and the picker draws compact rows with no images (`app.md`
# `P-18`): five hundred picker images would be the page's whole weight.
COMPACT_AFTER = 10

# The picker chrome a native client renders itself. Kept server-side rather than
# shipped in the app because results_disclaimer and privacy_note are compliance
# text: they must be correctable without waiting on an App Store review.
_PICKER_STRING_KEYS = (
    "page_title",
    "no_meets",
    "unnamed_meet",
    "meet_search",
    "no_meets_match",
    "results_disclaimer",
    "privacy_note",
    "results_disclaimer_short",
    "privacy_note_short",
    "notice_collapse",
)


@app.get("/", tags=["Public"])
def route_index(request: Request):
    meets = _public_meet_list(_here(request))
    brand = _picker_branding()
    # The list spans meets that may each run in a different language, so this page
    # follows the visitor, not a meet. Per-meet language starts at /mobile.
    lang = _picker_lang(request)
    # `P-10`: the reader's own store, narrowed exactly as `/add` narrows it, so the
    # two pages cannot disagree about where the app lives — one button for a phone
    # we recognise, every listing for an agent we cannot place. Empty until a
    # listing exists for this reader, and the page offers Add to Home Screen instead.
    platform = _phone_platform(request)
    mobile = _strings(lang, "mobile")
    store_buttons = [
        {"url": url, "label": mobile.get(f"add_store_{key}", "")}
        for key, url in _store_links().items()
        if platform is None or key == platform
    ]
    return _remember_prefs(
        request,
        render(
            request,
            "picker.html",
            meets=meets,
            compact=len(meets) > COMPACT_AFTER,
            store_buttons=store_buttons,
            t=_strings(lang, "mobile"),
            lang=lang,
            # For the display-preferences menu: the languages this server can serve,
            # and the Appearance choices.
            locales=_available_locales(),
            reader_themes=READER_THEMES,
            **_client_palette(request),
            picker_title=brand["title"],
            picker_window_title=brand["window_title"],
            picker_logo=brand["has_logo"],
            picker_logo_above=brand["logo_above"],
            analytics_enabled=_analytics_enabled(),
        ),
    )


# The contracts this build implements, for the handshake below. Bumped with the
# headers of docs/api.md and docs/app.md, which a test pins.
API_CONTRACT = "v2"
APP_CONTRACT = "v3"

SERVERS_FILE = os.path.join(DATA_DIR, "servers.json")


@app.get("/server", tags=["Public"])
def route_server():
    """Who this server is — the handshake a native client makes before anything else.

    An app can be pointed at a Pi or at a cloud (docs/app.md `P-11`),
    and the two are not interchangeable: a Pi has one meet and no picker, this has
    many. Guessing from a 404 on `/meets` would be a protocol by accident. It also
    validates a hand-typed address before a client saves it, and carries the
    contract versions.
    """
    return {
        "kind": "cloud",
        "name": _picker_branding().get("title") or "Splouch",
        "contract": {"api": API_CONTRACT, "app": APP_CONTRACT},
    }


@app.get("/servers", tags=["Public"])
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
    here = str(request.base_url).rstrip("/")
    servers = [
        {
            "name": _picker_branding().get("title") or "Splouch",
            "url": here,
            "kind": "cloud",
        }
    ]
    try:
        with open(SERVERS_FILE, "rb") as f:
            extra = json.load(f)
    except Exception:
        extra = []
    seen = {here}
    for entry in extra if isinstance(extra, list) else []:
        if not isinstance(entry, dict):
            continue
        # The same floor a typed or scanned address meets (`P-12`): `http` only to
        # the local network. A cleartext row to a public host is one every client
        # refuses, so it is dropped here rather than shipped as a dead row. Checked,
        # not rewritten: an entry keeps its own spelling, base path included.
        url = str(entry.get("url", "")).strip().rstrip("/")
        if not url or url in seen or not splouch_links.parse_origin(url):
            continue
        seen.add(url)
        servers.append(
            {
                "name": entry.get("name") or url,
                "url": url,
                "kind": entry.get("kind", "cloud"),
            }
        )
    return {"servers": servers}


# ── QR-code hand-off (`app.md` `P-16`) ─────────────────────────────────────────
# A poster at a pool carries `https://<this host>/add?server=<a cloud>` — a cloud
# and never a Pi, since a `.local` name resolves only for a phone already on the
# venue's wifi and a poster cannot ask which network it is being read on. With the
# app installed the OS opens it; without it, nothing intercepts it and the browser
# lands on `GET /add` below, which is the only page whose absence a spectator meets
# as a 404 after scanning something.
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
APPLINKS_FILE = os.path.join(DATA_DIR, "applinks.json")

# The Android application id. Fixed by the app's own manifest, not by a keystore,
# so unlike the fingerprints it has a real default here.
ANDROID_PACKAGE = "app.splouch.android"

# `<TEAMID>.<bundle id>` from `Splouch-ios` — the team and bundle the Xcode project
# is configured with, not a guess. Also a property of the app rather than of a
# signing key, so it too defaults rather than being required.
IOS_APP_IDS = ("L86UD2L8Q5.app.splouch.ios",)


def _applinks_file():
    """`applinks.json` from the data dir, or `{}`. Never raises."""
    try:
        with open(APPLINKS_FILE, "rb") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _as_list(value):
    """A config value that may be a list, or one string holding several."""
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [part for part in re.split(r"[,\s]+", str(value or "")) if part]


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
            hexed = raw.replace(":", "").strip().upper()
            if len(hexed) != 64 or any(c not in "0123456789ABCDEF" for c in hexed):
                continue
            value = ":".join(hexed[i : i + 2] for i in range(0, 64, 2))
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
    ios = list(
        dict.fromkeys(
            _as_list(os.environ.get("IOS_APP_IDS", ""))
            + _as_list(stored.get("ios_app_ids"))
        )
    )
    return {
        "android_package": (
            str(stored.get("android_package", ""))
            or os.environ.get("ANDROID_PACKAGE_NAME", "")
            or ANDROID_PACKAGE
        ),
        "android_fingerprints": _fingerprints(
            os.environ.get("ANDROID_CERT_FINGERPRINTS", ""),
            stored.get("android_fingerprints"),
        ),
        "ios_app_ids": ios or list(IOS_APP_IDS),
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
    for key, env in (("android", "STORE_URL_ANDROID"), ("ios", "STORE_URL_IOS")):
        url = str(stored.get(f"store_{key}", "") or os.environ.get(env, "")).strip()
        if url.lower().startswith("https://"):
            out[key] = url
    return out


def _phone_platform(request):
    """Which store to offer, from the User-Agent, or ``None`` when it is not a phone.

    Sniffing a User-Agent is usually the wrong tool, and it is the right one here
    for a narrow reason: this page is reached by pointing a camera at a poster, so
    the reader is holding the device the answer is about, and two buttons where one
    applies is a choice nobody standing at a pool wants to make.

    ``None`` is mostly **iPad**: iPadOS asks for desktop sites by default and is
    indistinguishable from macOS from here — there is no server-side tell, since
    Safari sends no `Sec-CH-UA-Platform` and `navigator.maxTouchPoints` is only
    reachable from script. Android tablets are not in that bucket; Chrome and
    Firefox both keep `Android` in a tablet's agent.

    The caller never guesses on a ``None``. It is only ever allowed to *narrow*,
    so an agent this cannot place is shown everything there is.
    """
    agent = request.headers.get("user-agent", "")
    if "Android" in agent:
        return "android"
    if any(device in agent for device in ("iPhone", "iPad", "iPod")):
        return "ios"
    return None


def _json(payload):
    """A JSON body with the content type spelled out rather than inferred.

    `apple-app-site-association` has no file extension on purpose — Apple fetches
    that exact path — so nothing downstream can guess its type from a name.
    """
    return Response(
        json.dumps(payload, indent=2, sort_keys=True).encode(),
        media_type="application/json",
    )


@app.get("/.well-known/assetlinks.json", tags=["Public"], include_in_schema=False)
def route_assetlinks():
    """Android App Links: which app may open `https://<this host>/add`.

    **404 while no fingerprint is configured**, rather than a well-formed file
    with an empty list. Both leave the app unverified, but only one of them says
    so to `curl -i`: an empty list looks deployed and fails at install time on a
    phone nobody is watching.
    """
    links = _app_links()
    if not links["android_fingerprints"]:
        raise HTTPException(status_code=404)
    return _json(
        [
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": links["android_package"],
                    "sha256_cert_fingerprints": links["android_fingerprints"],
                },
            }
        ]
    )


@app.get(
    "/.well-known/apple-app-site-association", tags=["Public"], include_in_schema=False
)
def route_aasa():
    """iOS Universal Links, the twin of the file above.

    `components` names the path *and* the query parameter, so this host claims
    `/add?server=…` and nothing else of the site: every other page — the picker,
    a meet, `/admin` — keeps opening in the browser where it belongs.

    No `.json` extension: Apple fetches this exact path, and adding one would
    serve a file nothing asks for.
    """
    return _json(
        {
            "applinks": {
                "details": [
                    {
                        "appIDs": _app_links()["ios_app_ids"],
                        "components": [{"/": INVITE_PATH, "?": {INVITE_PARAM: "?*"}}],
                    },
                ]
            }
        }
    )


@app.get("/add", tags=["Public"])
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
    server = splouch_links.parse_origin(request.query_params.get(INVITE_PARAM, ""))
    # One store, the reader's own, where the agent says which. A platform with no
    # listing yet leaves nothing rather than offering the other one — an App Store
    # link is not an answer to an Android phone.
    #
    # An agent we cannot place is shown **every** listing instead: sniffing may
    # narrow, never guess. It is also the honest answer for the bucket, which is
    # mostly iPads asking for desktop sites — the reader gets the App Store button
    # they came for, and learns from the one beside it that the other app exists.
    platform = _phone_platform(request)
    stores = _store_links()
    if platform:
        stores = {k: v for k, v in stores.items() if k == platform}
    # The browser is offered exactly when the store offer is not a confident, whole
    # answer: when there is no store button at all, and when the agent left us
    # guessing. A phone we recognised, whose app is listed, gets the one button —
    # a browser link beside it is the easier tap and the one that ends the hand-off
    # (`P-10`). It leads to the **picker**, never straight to a meet, so a reader
    # who takes it still passes `P-06`'s disclaimer.
    response = _remember_prefs(
        request,
        render(
            request,
            "add.html",
            lang=lang,
            t=_strings(lang, "mobile"),
            server=server,
            # Whether the code named *this* server, which is the ordinary case: a Pi
            # prints a code for the cloud it publishes to, and for most deployments
            # that is the same cloud serving this page. The app would answer such a
            # scan with "you're already on this server" and open the meet list, so
            # promising that it "will offer to add this server" would be a small lie
            # told to the majority of readers.
            is_here=bool(server)
            and server == splouch_links.parse_origin(str(request.base_url)),
            stores=stores,
            web=not stores or platform is None,
            **_picker_branding(),
        ),
    )
    # The body depends on the agent, so say so. Nothing in front of this caches
    # today, and a proxy that one day does must not hand an iPhone Google Play.
    response.headers["Vary"] = "User-Agent"
    return response


# The date at the top of `/privacy`. Bumped by hand with any change to `[privacy]`
# in the locale files, which is what the page promises under "Changes".
PRIVACY_UPDATED = "2026-10-06"


def _privacy_contact():
    """Who answers for this deployment's privacy policy, or '' while unset.

    Per deployment, like the store links: the software cannot know who runs the
    server it is installed on. Unset leaves the Contact section out rather than
    printing an address nobody reads.
    """
    contact = os.environ.get("PRIVACY_CONTACT", "").strip()
    return contact if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", contact) else ""


def _privacy_operator():
    """Who runs this deployment — the name the policy gives as responsible for it
    (GDPR controller, Quebec's person in charge) — or '' while unset. One line,
    so a stray newline in `.env` cannot break the paragraph it sits in."""
    operator = " ".join(os.environ.get("PRIVACY_OPERATOR", "").split())
    return operator[:120]


@app.get("/privacy", tags=["Public"])
def route_privacy(request: Request):
    """The privacy policy, the URL a store listing points at.

    One page for the site and the Android app, in the visitor's language. The
    words are `[privacy]` in the locale files, never `[mobile]`, so nothing here
    reaches `GET /i18n/{lang}` or an app. The retention it states is read from
    `cloud_analytics`, so the page cannot drift from what the prune deletes.
    """
    lang = _picker_lang(request)
    return _remember_prefs(
        request,
        render(
            request,
            "privacy.html",
            lang=lang,
            t=_strings(lang, "privacy"),
            locales=_available_locales(),
            host=request.url.hostname or "",
            days=cloud_attendance.RETENTION_DAYS,
            updated=PRIVACY_UPDATED,
            contact=_privacy_contact(),
            operator=_privacy_operator(),
        ),
    )


@app.get("/locales", tags=["Public"])
def route_locales(request: Request):
    """The languages this server can serve — for a client offering the choice."""
    return _etagged(request, [{"code": c, "name": n} for c, n in _available_locales()])


@app.get("/i18n/{lang}", tags=["Public"])
def route_i18n(lang: str, request: Request):
    """One language: app chrome plus both label styles (§5.9).

    No meet in the path on purpose — the table is a property of this server's locale
    files, identical for every meet, so it is fetched once per language and cached
    rather than repeated inside each meet's config.
    """
    return _etagged(request, _i18n_bundle(lang))


def _here(request):
    """This server's public origin, as the client reached it."""
    return str(request.base_url).rstrip("/")


@app.get("/meets", tags=["Public"])
def route_meets(request: Request):
    """The meet list as JSON — the native picker's equivalent of ``GET /``.

    ``offline`` meets are retained ones with no relay currently connected; they
    stay listed on purpose so a spectator can still read the last state.
    ``has_picker_image`` says whether ``GET /picker_image/{id}`` will return an
    image for that meet. ``base`` is where to reach each one (`app.md` `C-11`).

    Readable from any origin: a meet page served by a worker on another host checks
    the list is up before sending the spectator back to it (`A-12`)."""
    return JSONResponse(
        {"meets": _public_meet_list(_here(request))},
        headers={"Access-Control-Allow-Origin": "*"},
    )


@app.get("/picker/config", tags=["Public"])
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
    strings = _strings(lang, "mobile")
    return {
        **_picker_branding(),
        "lang": lang,
        "analytics_enabled": _analytics_enabled(),
        "stores": _store_links(),
        "strings": {k: strings[k] for k in _PICKER_STRING_KEYS if k in strings},
    }


@app.get("/logout", tags=["Admin"])
def route_logout():
    return Response(
        'Logged out — <a href="/admin">sign in again</a>',
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Splouch Admin"'},
    )


@app.get("/ping", tags=["Public"])
def route_ping():
    return Response("ok", media_type="text/plain")


@app.get("/picker_image/{meet_id}", tags=["Public"])
def route_meet_picker_image(meet_id: str):
    img_b64 = cloud_registry.image(meet_id, "picker_image_b64")
    if not img_b64:
        raise HTTPException(404)
    data = base64.b64decode(img_b64)
    return Response(
        data, media_type="image/png", headers={"Cache-Control": "public, max-age=60"}
    )


@app.get("/picker_logo", tags=["Public"])
def route_picker_logo(request: Request):
    creds = _load_creds()
    logo_b64 = creds.get("picker_logo_b64", "")
    if not logo_b64:
        raise HTTPException(404)
    mime = creds.get("picker_logo_mime", "image/png")
    # Every browser's `<img>` names SVG in its Accept; the apps' loaders do not, and
    # get the PNG copy. A logo stored before the copy existed has none to give.
    png_b64 = creds.get("picker_logo_png_b64", "")
    accept = request.headers.get("accept", "")
    if mime == "image/svg+xml" and png_b64 and "image/svg+xml" not in accept:
        logo_b64, mime = png_b64, "image/png"
    data = base64.b64decode(logo_b64)
    # An SVG logo is a document, not a bitmap: opened directly (rather than through
    # the `<img>` on the picker page, which already inerts it) it would run its own
    # script on this origin. The sandbox costs nothing for the other formats.
    return Response(
        data,
        media_type=mime,
        headers={
            "Cache-Control": "public, max-age=300",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Vary": "Accept",
            "X-Content-Type-Options": "nosniff",
        },
    )


@app.get("/picker_icon", tags=["Public"])
def route_picker_icon():
    icon_b64 = _load_creds().get("picker_icon_b64", "")
    if not icon_b64:
        default = os.path.join(_HERE, "static", "img", "default_mobile_icon.png")
        if not os.path.exists(default):
            raise HTTPException(404)
        return FileResponse(default, media_type="image/png")
    data = base64.b64decode(icon_b64)
    return Response(
        data, media_type="image/png", headers={"Cache-Control": "public, max-age=300"}
    )


@app.get("/favicon.ico", tags=["Public"])
def route_favicon():
    # Browsers auto-request this; serve a lean, scalable brand mark for the tab.
    return FileResponse(
        os.path.join(_HERE, "static", "img", "favicon.svg"), media_type="image/svg+xml"
    )


@app.get("/picker_manifest", tags=["Public"])
def route_picker_manifest():
    creds = _load_creds()
    raw_wt = creds.get("picker_window_title")
    app_title = ("Splouch" if raw_wt is None else raw_wt) or "Splouch"
    manifest = {
        "name": app_title,
        "short_name": app_title,
        "start_url": "/",
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": "#000000",
        "icons": [
            {"src": "/picker_icon", "sizes": "192x192", "type": "image/png"},
            {"src": "/picker_icon", "sizes": "512x512", "type": "image/png"},
        ],
    }
    return Response(json.dumps(manifest), media_type="application/manifest+json")


def _form_text(form, key):
    """A form field as text.

    Starlette types a form value `str | UploadFile`, because a client is free to
    post a file part under a name this server means as text. Reading `.strip()`
    off that raised AttributeError — a 500 for what is really a bad request — so
    anything that is not text reads as absent.
    """
    value = form.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def _failure(what):
    """Log the exception being handled, traceback and all, and say what failed.

    For an admin route's catch-all `except`. The traceback goes to the journal that
    /admin/logs shows; the reply carries only this fixed sentence, so nothing from
    inside the exception reaches the browser.
    """
    traceback.print_exc()
    return f"{what} failed — see the log"


async def _read_image(upload, allowed):
    """(bytes, settled MIME type, None) for an uploaded image, or (None, None, reason).

    The browser's `content_type` is a claim, and an empty one is common enough (some
    clients send `application/octet-stream` for anything they do not recognise) that
    the filename extension is a better second opinion than a blanket default. Both
    have to agree with `allowed` before the bytes are stored — the form's `accept`
    filters the file dialog and nothing else, so drag-and-drop and any non-browser
    client arrive here unchecked.
    """
    mime = (upload.content_type or "").split(";")[0].strip().lower()
    if mime in ("", "application/octet-stream"):
        mime = (mimetypes.guess_type(upload.filename)[0] or "").lower()
    if mime == "image/jpg":  # non-standard, but some tools still send it
        mime = "image/jpeg"
    if mime not in allowed:
        names = ", ".join(m.split("/")[-1].split("+")[0].upper() for m in allowed)
        return None, None, f"Unsupported image format. Accepted: {names}."
    data = await upload.read()
    if len(data) > MAX_IMAGE_BYTES:
        return (
            None,
            None,
            (f"Image is too large (max {MAX_IMAGE_BYTES // (1024 * 1024)} MB)."),
        )
    return data, mime, None


@app.post(
    "/admin/picker_appearance",
    tags=["Admin"],
    response_model=ActionResult,
    response_model_exclude_none=True,
    dependencies=[Depends(require_admin)],
)
async def route_picker_appearance(request: Request):
    form = await request.form()
    creds = _load_creds()
    if "picker_title" in form:
        creds["picker_title"] = _form_text(form, "picker_title")
        creds["picker_window_title"] = _form_text(form, "picker_window_title")
        creds["picker_logo_above"] = form.get("picker_logo_above") == "1"
    if form.get("picker_logo_clear") == "1":
        creds["picker_logo_b64"] = ""
        creds.pop("picker_logo_mime", None)
        creds.pop("picker_logo_png_b64", None)
    else:
        logo = form.get("picker_logo")
        if isinstance(logo, UploadFile) and logo.filename:
            data, mime, error = await _read_image(logo, LOGO_MIME_TYPES)
            if error:
                return {"ok": False, "error": error}
            creds.pop("picker_logo_png_b64", None)
            if mime == "image/svg+xml":
                png = await run_in_threadpool(_svg_to_png, data)
                if png is None:
                    return {"ok": False, "error": "This SVG could not be read."}
                creds["picker_logo_png_b64"] = base64.b64encode(png).decode()
            else:
                data = await run_in_threadpool(_upright, data)
                if data is None:
                    return {"ok": False, "error": "This image could not be read."}
            creds["picker_logo_b64"] = base64.b64encode(data).decode()
            creds["picker_logo_mime"] = mime
    if form.get("picker_icon_clear") == "1":
        creds["picker_icon_b64"] = ""
    else:
        icon = form.get("picker_icon")
        if isinstance(icon, UploadFile) and icon.filename:
            data, _, error = await _read_image(icon, ICON_MIME_TYPES)
            if error:
                return {"ok": False, "error": error}
            creds["picker_icon_b64"] = base64.b64encode(data).decode()
    await run_in_threadpool(_save_creds, creds)
    return {"ok": True}


_LOGIN_FIELDS = ("user", "password_hash", "salt")


@app.get("/admin/backup/keys", tags=["Admin"], dependencies=[Depends(require_admin)])
def route_backup_keys(request: Request):
    keys = _load_keys()
    # keys + credentials (picker appearance, analytics toggle, locale). By
    # default the admin login is excluded, so a routine backup never carries the
    # password hash. ?full=1 adds the login (marked 'full') for a bare-metal
    # rebuild — that file contains the password hash + salt, so keep it private.
    full = request.query_params.get("full") == "1"
    creds = _load_creds()
    if not full:
        creds = {k: v for k, v in creds.items() if k not in _LOGIN_FIELDS}
    backup = {"version": 2, "keys": keys, "credentials": creds}
    if full:
        backup["full"] = True
    name = "splouch-backup-full.json" if full else "splouch-backup.json"
    return Response(
        json.dumps(backup, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@app.post(
    "/admin/restore/keys",
    tags=["Admin"],
    response_model=RestoreResult,
    response_model_exclude_none=True,
    dependencies=[Depends(require_admin)],
)
async def route_restore_keys(request: Request):
    uploaded = (await request.form()).get("keys_file")
    if not isinstance(uploaded, UploadFile):
        return JSONResponse({"error": "No file provided"}, status_code=400)
    try:
        data = json.loads(await uploaded.read())
    except ValueError:  # not JSON, or not UTF-8
        return JSONResponse({"error": "Invalid file: not JSON"}, status_code=400)
    if not isinstance(data, dict) or not isinstance(data.get("keys"), dict):
        return JSONResponse(
            {"error": "Invalid file: not a valid backup file"}, status_code=400
        )
    try:
        keys = data["keys"]
        await run_in_threadpool(cloud_auth.restore_keys, keys)
        # Merge the backup's credentials (appearance, analytics, locale) onto the
        # current ones so the backup wins but no required field goes missing. The
        # admin login is only touched when the file is an explicit full backup —
        # otherwise restoring a routine backup would silently reset the password.
        creds_in = data.get("credentials")
        if isinstance(creds_in, dict):
            creds_in = dict(creds_in)
            if not data.get("full"):
                for f in _LOGIN_FIELDS:
                    creds_in.pop(f, None)
            await run_in_threadpool(_save_creds, {**_load_creds(), **creds_in})
        return {"ok": True, "count": len(keys)}
    except Exception:
        return JSONResponse({"error": _failure("Restoring the keys")}, status_code=500)


@app.get("/admin/backup/meets", tags=["Admin"], dependencies=[Depends(require_admin)])
def route_backup_meets():
    backup = {"version": 1, "meets": cloud_registry.backup()}
    return Response(
        json.dumps(backup, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="splouch-meets.json"'},
    )


@app.post(
    "/admin/restore/meets",
    tags=["Admin"],
    response_model=RestoreResult,
    response_model_exclude_none=True,
    dependencies=[Depends(require_admin)],
)
async def route_restore_meets(request: Request):
    uploaded = (await request.form()).get("meets_file")
    if not isinstance(uploaded, UploadFile):
        return JSONResponse({"error": "No file provided"}, status_code=400)
    try:
        data = json.loads(await uploaded.read())
    except ValueError:  # not JSON, or not UTF-8
        return JSONResponse({"error": "Invalid file: not JSON"}, status_code=400)
    if not isinstance(data, dict):
        return JSONResponse(
            {"error": "Invalid file: expected a JSON object"}, status_code=400
        )
    meets = data.get("meets", data)
    if not isinstance(meets, dict):
        return JSONResponse(
            {"error": "Invalid file: invalid meets section"}, status_code=400
        )
    try:
        # Merge (upsert) the backup's meets into the store — never clear. A meet
        # not in the backup is left alone, and a currently-live meet is skipped
        # so its fresh state isn't overwritten by a stale backup. This is
        # additive: retained meets auto-expire, and "Delete meet" removes one.
        written = await run_in_threadpool(cloud_registry.restore, meets)
        return {"ok": True, "count": len(written)}
    except Exception:
        return JSONResponse({"error": _failure("Restoring the meets")}, status_code=500)


@app.post("/admin/update", tags=["Admin"], dependencies=[Depends(require_admin)])
async def route_update(request: Request):
    version = (await request.form()).get("version", "latest")
    # The webhook call is a blocking HTTP request — run it off the event loop
    # so attendee broadcasts keep flowing.
    return await run_in_threadpool(_trigger_update, version)


def _trigger_update(version):
    url = os.environ.get("DEPLOY_WEBHOOK_URL", "")
    secret = os.environ.get("DEPLOY_WEBHOOK_SECRET", "")
    if not url or not secret:
        return JSONResponse({"error": "Deploy webhook not configured"}, status_code=503)
    try:
        body = json.dumps({"version": version}).encode()
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("X-Deploy-Token", secret)
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return {"status": "started"}
            return JSONResponse({"error": f"webhook {resp.status}"}, status_code=502)
    except Exception:
        return JSONResponse({"error": _failure("Starting the update")}, status_code=502)


@app.get("/admin/update_log", tags=["Admin"], dependencies=[Depends(require_admin)])
def route_update_log():
    webhook_url = os.environ.get("DEPLOY_WEBHOOK_URL", "")
    secret = os.environ.get("DEPLOY_WEBHOOK_SECRET", "")
    if not webhook_url or not secret:
        return {"lines": [], "done": None}

    log_url = webhook_url.rsplit("/", 1)[0] + "/log"
    try:
        req = urllib.request.Request(log_url, method="GET")
        req.add_header("X-Deploy-Token", secret)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return Response(resp.read(), media_type="application/json")
    except Exception:
        return {"lines": [], "done": None}


@app.get("/admin/logs", tags=["Admin"], dependencies=[Depends(require_admin)])
def route_logs(request: Request):
    webhook_url = os.environ.get("DEPLOY_WEBHOOK_URL", "")
    secret = os.environ.get("DEPLOY_WEBHOOK_SECRET", "")
    if not webhook_url or not secret:
        return JSONResponse({"ok": False, "error": "not configured"}, status_code=503)

    source = request.query_params.get("source", "app")
    tail = request.query_params.get("tail", "300")
    logs_url = webhook_url.rsplit("/", 1)[0] + f"/logs?source={source}&tail={tail}"
    try:
        req = urllib.request.Request(logs_url, method="GET")
        req.add_header("X-Deploy-Token", secret)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return Response(resp.read(), media_type="application/json")
    except Exception:
        return JSONResponse(
            {"ok": False, "error": _failure("Reaching the deploy webhook")},
            status_code=502,
        )


@app.get("/admin/versions", tags=["Admin"], dependencies=[Depends(require_admin)])
def route_versions():
    webhook_url = os.environ.get("DEPLOY_WEBHOOK_URL", "")
    secret = os.environ.get("DEPLOY_WEBHOOK_SECRET", "")
    if not webhook_url or not secret:
        return JSONResponse({"ok": False, "error": "not configured"}, status_code=503)

    versions_url = webhook_url.rsplit("/", 1)[0] + "/versions"
    try:
        req = urllib.request.Request(versions_url, method="GET")
        req.add_header("X-Deploy-Token", secret)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return Response(resp.read(), media_type="application/json")
    except Exception:
        return JSONResponse(
            {"ok": False, "error": _failure("Reaching the deploy webhook")},
            status_code=502,
        )


@app.get(
    "/admin/stats",
    tags=["Admin"],
    response_model=StatsResult,
    dependencies=[Depends(require_admin)],
)
def route_stats(request: Request):
    if not _analytics_enabled():
        return {"enabled": False, "count": None}
    meet_id = request.query_params.get("meet_id", "")
    window = request.query_params.get("window", "24h")
    if window != "all" and window not in _ANALYTICS_WINDOWS:
        window = "24h"
    return {"enabled": True, "count": _attendee_count(meet_id, window)}


ROLLOUT_VERSION_RE = re.compile(r"^v\d{4}\.\d{2}\.\d+$")


def _admin_nodes():
    """Every node for the Nodes tab, and the workers a live meet may move to."""
    now = datetime.datetime.now(datetime.UTC)
    out = []
    for n in cloud_registry.nodes():
        seen = n["last_seen"]
        up = (
            bool(seen)
            and (now - seen).total_seconds() < cloud_registry.SILENT_AFTER_SECS
        )
        out.append(
            {
                "name": n["name"],
                "region": n["region"] or "",
                "url": n["host"],
                "state": n["state"],
                "workers": n["workers"],
                "wg_pubkey": n["wg_pubkey"],
                "version": n["version"],
                "target": n["target_version"] or "",
                "meets": n["meets"],
                "attendees": n["attendees"],
                "up": up,
                "last_seen": seen.astimezone().strftime("%Y-%m-%d %H:%M:%S")
                if seen
                else "",
                # What the Move menu offers: workers on a live node taking meets.
                "targets": (
                    [f"{n['name']}:{w}" for w in range(1, n["workers"] + 1)]
                    if up and n["state"] == "active"
                    else []
                ),
            }
        )
    return out


# What the nightly backup service records after each run (docker-compose.yml,
# `backup`), on the host's /var/backups/splouch, mounted here read-only.
BACKUP_STATUS = os.path.join(os.environ.get("BACKUP_DIR", "/backups"), "status.json")


def _backup_status():
    """The last nightly dump: `{"at", "ok", "file", "bytes", "error", "stale"}`, or
    None before the first. `stale` when it is more than a day and a half old — a
    backup that stopped running, which the panel shows in red like a failure."""
    try:
        with open(BACKUP_STATUS, encoding="utf-8") as f:
            st = json.load(f)
        at = datetime.datetime.fromisoformat(st["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=datetime.UTC)
    age = datetime.datetime.now(datetime.UTC) - at
    return {
        "at": at.isoformat(),
        "ok": bool(st.get("ok")),
        "file": str(st.get("file", "")),
        "bytes": int(st.get("bytes") or 0),
        "error": str(st.get("error", "")),
        "stale": age > datetime.timedelta(hours=36),
    }


def _admin_page(request, t=None, creds_error=None):
    """The whole panel. Blocking (database) — run off the loop."""
    creds = _load_creds()
    return render(
        request,
        "admin.html",
        keys=_load_keys(),
        regions=cloud_auth.regions(),
        countries=COUNTRIES,
        active_meets=_admin_meet_list(),
        nodes=_admin_nodes(),
        rollout=cloud_registry.rollout(),
        backup=_backup_status(),
        t=t or _load_cloud_strings(request),
        ui_lang=_admin_lang(request),
        creds_error=creds_error,
        user_name=creds.get("user", "Admin"),
        locales=_available_locales(),
        current_locale=creds.get("locale", ""),
        ui_lang_cookie=_ui_lang_cookie(request),
        has_deploy=bool(os.environ.get("DEPLOY_WEBHOOK_URL")),
        analytics_enabled=bool(creds.get("analytics_enabled")),
        privacy_incomplete=not (_privacy_operator() and _privacy_contact()),
        **_picker_appearance(),
    )


def _org_fields(form):
    """Country, state/province and region from the organizer form, validated."""
    country, province = clean_location(
        _form_text(form, "country"), _form_text(form, "province")
    )
    region = _form_text(form, "region")
    if region not in {r["code"] for r in cloud_auth.regions()}:
        region = COUNTRIES.get(country, "")
    return {"country": country, "province": province, "region": region}


def _admin_action(form, request):
    """Apply one panel form post. Blocking (database) — run off the loop.

    Returns a response to send instead of the usual redirect back to the panel,
    or None.
    """
    action = form.get("action")
    if action == "add":
        org = _form_text(form, "organizer")
        if org:
            cloud_auth.add_organizer(org, **_org_fields(form))
    elif action == "update_org":
        cloud_auth.update_organizer(form.get("key", ""), **_org_fields(form))
    elif action == "rollout_start":
        version = _form_text(form, "version")
        # A version a node can pull: a release tag, or master. Never "latest", which
        # each node would resolve on its own, or a branch, which has no image.
        if version == "master" or ROLLOUT_VERSION_RE.match(version):
            # `not_before` comes from the browser as an ISO time with its offset, so
            # "2:00 tonight" is the admin's 2:00, whatever the server's clock says.
            when = None
            raw = _form_text(form, "not_before")
            if raw:
                try:
                    when = datetime.datetime.fromisoformat(raw)
                except ValueError:
                    when = None
                if when is not None and when.tzinfo is None:
                    when = None
            cloud_registry.start_rollout(
                version, force=form.get("force") == "1", not_before=when
            )
    elif action == "rollout_stop":
        cloud_registry.stop_rollout()
    elif action == "move_meet":
        node, _, worker = _form_text(form, "target").partition(":")
        if worker.isdigit():
            cloud_registry.move(form.get("meet_id", ""), node, int(worker))
    elif action == "node_state":
        cloud_registry.set_node_state(form.get("node", ""), _form_text(form, "state"))
    elif action == "forget_node":
        cloud_registry.forget_node(form.get("node", ""))
    elif action == "accept_location":
        cloud_auth.accept_location(form.get("key", ""))
    elif action == "revoke":
        cloud_auth.update_organizer(form.get("key", ""), active=False)
    elif action == "delete":
        cloud_auth.delete_organizer(form.get("key", ""))
    elif action == "set_expiry":
        raw = _form_text(form, "expires_at")
        try:
            exp = datetime.datetime.fromisoformat(raw) if raw else None
        except ValueError:
            exp = None
        if exp is not None:
            cloud_registry.set_expiry(form.get("meet_id", ""), exp)
    elif action == "delete_meet":
        cloud_registry.delete(form.get("meet_id", ""))
    elif action == "set_analytics":
        creds = _load_creds()
        enable = form.get("analytics_enabled") == "1"
        # Turning counting on needs the administrator's acknowledgement from the
        # consent dialog: the legal check is theirs, and the record of who
        # accepted it and when stays with the setting. Turning it off never does.
        if enable and form.get("analytics_ack") != "1":
            return None
        creds["analytics_enabled"] = enable
        if enable:
            creds["analytics_ack"] = {
                "user": creds.get("user", ""),
                "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
            }
        _save_creds(creds)
    elif action == "change_locale":
        creds = _load_creds()
        creds["locale"] = form.get("locale", "")
        _save_creds(creds)
        cloud_i18n._locale_cache.clear()
    elif action == "change_credentials":
        t = _load_cloud_strings(request)
        creds = _load_creds()
        cur_pw = form.get("current_password", "")
        new_user = _form_text(form, "new_user")
        new_pw1 = form.get("new_password", "")
        new_pw2 = form.get("new_password2", "")
        cur_hash, _ = _hash_password(cur_pw, creds["salt"])
        if not hmac.compare_digest(cur_hash, creds["password_hash"]):
            error = t.get("err_wrong_password", "Incorrect current password.")
        elif new_pw1 != new_pw2:
            error = t.get("err_password_mismatch", "New passwords do not match.")
        elif not new_pw1:
            error = t.get("err_empty_password", "Password cannot be empty.")
        else:
            creds["user"] = new_user or creds["user"]
            creds["password_hash"], creds["salt"] = _hash_password(new_pw1)
            _save_creds(creds)
            return Response(
                'Credentials updated — <a href="/admin">sign in with new credentials</a>',
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Splouch Admin"'},
            )
        return _admin_page(request, t=t, creds_error=error)
    return None


@app.get("/admin", tags=["Admin"], dependencies=[Depends(require_admin)])
@app.post("/admin", tags=["Admin"], dependencies=[Depends(require_admin)])
async def route_admin(request: Request):
    if request.method == "POST":
        form = await request.form()
        response = await run_in_threadpool(_admin_action, form, request)
        return response or RedirectResponse("/admin", status_code=303)
    return await run_in_threadpool(_admin_page, request)


# ── Metrics ────────────────────────────────────────────────────────────────────


def _gauges():
    """The registry in numbers, for `/metrics`: nodes up and down, meets live,
    in progress and retained. Never an id. Read at scrape time."""
    now = datetime.datetime.now(datetime.UTC)
    nodes = cloud_registry.nodes()
    up = sum(
        1
        for n in nodes
        if n["last_seen"]
        and (now - n["last_seen"]).total_seconds() < cloud_registry.SILENT_AFTER_SECS
    )
    meets = cloud_registry.list_meets()
    live = [m for m in meets if m["live"]]
    return {
        ("splouch_nodes", "up"): up,
        ("splouch_nodes", "down"): len(nodes) - up,
        ("splouch_meets", "live"): len(live),
        ("splouch_meets", "in_progress"): sum(
            1 for m in live if cloud_registry.running(m, now)
        ),
        ("splouch_meets", "retained"): len(meets) - len(live),
    }


cloud_metrics.CONTROL.register(
    cloud_metrics.Snapshot(
        _gauges,
        {
            "splouch_nodes": "Nodes by whether they reported in the last 90 seconds.",
            "splouch_meets": "Meets in the registry, by state.",
        },
        label="state",
    )
)
cloud_metrics.version_info(
    cloud_metrics.CONTROL, "control", os.environ.get("SPLOUCH_VERSION", "")
)


@app.get("/metrics", include_in_schema=False)
def route_metrics(request: Request):
    """Prometheus' view of the control plane; 404 outside the private network."""
    if not cloud_metrics.private_client(request):
        raise HTTPException(status_code=404)
    try:
        data, kind = cloud_metrics.body(cloud_metrics.CONTROL)
    except Exception:
        traceback.print_exc()
        raise HTTPException(status_code=503) from None
    return Response(data, media_type=kind)


# ── Assignment (Pi → control plane) ───────────────────────────────────────────


class AssignIn(BaseModel):
    key: str
    meet_uid: str = ""


def _relay_url(node_url, worker, meet_id):
    """The worker socket a Pi connects to: its node's public URL and `/wN` prefix.

    The control plane builds it, not the Pi, so how a worker is addressed can change
    without a Pi release.
    """
    base = cloud_registry.worker_url(node_url, worker)
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return f"{base}/ws/relay?meet={meet_id}"


@app.post("/api/assign", tags=["Relay"])
def route_assign(body: AssignIn):
    """Where a Pi publishes a meet, and the ticket that lets it.

    The Pi calls this before connecting, with its relay key and the meet's uid;
    the answer names the worker socket and carries a ticket the worker checks on
    `register` (docs/api.md §5.12). A bad key is a 403 with the same reason a
    worker gives; no node able to take the meet is a 503 the Pi retries.
    """
    secret = os.environ.get("NODE_SECRET", "")
    if not secret:
        raise HTTPException(status_code=503, detail="NODE_SECRET is not set")
    try:
        found = cloud_registry.assign(body.key, body.meet_uid)
    except cloud_registry.NoNode:
        return JSONResponse({"reason": "no server available"}, status_code=503)
    if found is None:
        return JSONResponse({"reason": "invalid or inactive key"}, status_code=403)
    return {
        "meet_id": found["meet_id"],
        "relay_url": _relay_url(found["url"], found["worker"], found["meet_id"]),
        "ticket": cloud_ticket.sign(
            secret,
            found["meet_id"],
            found["node"],
            found["worker"],
            body.key,
            organizer=found["organizer"],
        ),
        "region": found["region"],
        "expires_in": cloud_ticket.TTL_SECONDS,
    }


# ── Internal API (workers → control plane) ─────────────────────────────────────
# What a worker reports and asks for. Not for browsers or apps: every call carries
# `NODE_SECRET`, a value only the control plane and the nodes hold, and the routes
# stay out of the public OpenAPI schema. Workers reach these over HTTPS like anyone
# else (docs/architecture/scaling.md) — the secret is the whole gate.


def require_node(request: Request):
    secret = os.environ.get("NODE_SECRET", "")
    if not secret:
        # Unset is a deploy mistake, not an open door.
        raise HTTPException(status_code=503, detail="NODE_SECRET is not set")
    hdr = request.headers.get("authorization", "")
    if not hmac.compare_digest(hdr.encode(), f"Bearer {secret}".encode()):
        raise HTTPException(status_code=401)


internal = APIRouter(
    prefix="/internal", include_in_schema=False, dependencies=[Depends(require_node)]
)


class RegisterIn(BaseModel):
    key: str
    meet_uid: str = ""
    meta: dict = {}
    node: str
    worker: int = 1
    location: dict | None = None


class HolderIn(BaseModel):
    node: str
    worker: int = 1


class HeartbeatIn(BaseModel):
    node: str
    worker: int = 1
    host: str = ""
    region: str = ""
    workers: int = 1
    wg_pubkey: str = ""
    live: list[str] = []
    attendees: dict[str, int] = {}
    # The node's attendance numbers, from its worker 1 now and then: distinct
    # visitors per meet and window. Never the ids (cloud_attendance).
    attendance: dict[str, dict[str, int]] | None = None
    # The image tag this node runs (SPLOUCH_VERSION), for rollouts.
    version: str = ""
    # When each meet's console last sent a board frame (unix seconds): what makes
    # a meet running rather than just connected (cloud_registry.running).
    frames: dict[str, float] = {}


@internal.post("/register")
def internal_register(body: RegisterIn):
    """A Pi registered on a worker: check its key, store the meet, hand back its id."""
    result = cloud_registry.register(
        body.key, body.meet_uid, body.meta, body.node, body.worker, body.location
    )
    if result is None:
        return JSONResponse({"reason": "invalid or inactive key"}, status_code=403)
    return result


@internal.post("/meets/{meet_id}/retire")
def internal_retire(meet_id: str, body: HolderIn):
    cloud_registry.retire(meet_id, body.node, body.worker)
    return {"ok": True}


@internal.get("/meets/{meet_id}")
def internal_meet(meet_id: str):
    """A meet's full record, for a worker serving its pages."""
    rec = cloud_registry.get(meet_id)
    if rec is None:
        raise HTTPException(404)
    return rec


@internal.post("/heartbeat")
def internal_heartbeat(body: HeartbeatIn):
    """A worker says which meets it holds; the answer carries the settings it needs."""
    result = cloud_registry.heartbeat(
        body.node,
        body.worker,
        body.live,
        host=body.host,
        region=body.region,
        workers=body.workers,
        wg_pubkey=body.wg_pubkey,
        attendees=body.attendees,
        version=body.version,
        frames=body.frames,
    )
    if body.attendance:
        cloud_analytics.store(body.node, body.attendance)
    if body.worker == 1:
        # The meets this node should keep in its store; it drops the others —
        # expired, deleted, moved away (cloud_meetstore.keep_only).
        result["known"] = cloud_registry.node_meet_ids(body.node)
    return {"analytics_enabled": _analytics_enabled(), **result}


app.include_router(internal)
