"""Shared FastAPI web helpers for the local Splouch server.

Centralises Jinja templating (with the global template context injected into
every render), the login dependency, and small shared helpers (``redirect``,
``save_upload``) so the route modules stay lean.
"""
import os
import shutil
from urllib.parse import urlparse

from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import state


class ActionResult(BaseModel):
    """Standard body for endpoints that just report success/failure.

    Used as ``response_model`` so the ``{ok, error}`` shape shows up in /docs.
    ``error`` is only present on failure. Handlers that need to return extra
    fields (a filename, a path, …) should not use this model, since a
    ``response_model`` filters the response down to the declared fields.
    """
    ok: bool
    error: str | None = None


class EnabledFlag(BaseModel):
    """Standard body for endpoints reporting / toggling a single on-off flag."""
    enabled: bool


class LogLine(BaseModel):
    """One line of a long-running job's output, as the panel renders it."""
    text: str
    error: bool


class LogTail(BaseModel):
    """What a log-polling endpoint returns: the lines so far, and whether it ended.

    Shared by the app update, the OS update and the RTC install — three panels that
    poll the same way, which is why this lives here rather than with any one of them.
    """
    lines: list[LogLine]
    done: bool | None = None
    # Only the app-update log sets this: the run stopped on a dirty checkout, so the
    # panel should offer "Repair checkout". Defaulted, so the OS-update and RTC logs
    # that share this model are unaffected.
    repair: bool = False


class NotAuthenticated(Exception):
    """Raised by :func:`require_login` when no session user is set.

    The app registers an exception handler that turns this into a redirect to
    /login, keeping the auth check as a clean dependency (no redirect plumbing
    smuggled through an HTTPException).
    """

# Two roots: this server's own templates, then shared/templates for the ones the
# cloud renders too — scoreboard_base.html, which live-mobile.html extends on both
# sides. Own-first, so a name here always wins over a shared one.
templates = Jinja2Templates(directory=[
    os.path.join(state.app_dir, 'templates'),
    os.path.join(state.REPO_DIR, 'shared', 'templates'),
])


def _url_for(name, **kw):
    """``url_for`` helper for the handful of endpoints templates use."""
    if name == 'static':
        return '/static/' + kw.get('filename', '')
    if name == 'appearance.serve_image':
        return '/images/' + kw.get('filename', '')
    return '/' + (kw.get('filename') or '')


templates.env.globals['url_for'] = _url_for


def _globals():
    """Global template values merged into every render.

    Recomputed per render so live settings changes take effect immediately.
    """
    return dict(
        splash_url=state.settings.get('splash_url', ''),
        labels=state.load_locale(),
        # The vocabulary `event_name_parts` composes against, in the same language
        # `labels` is read in (docs/app.md `T-11`).
        event_vocab=state.load_event_translations(),
        # BCP-47 tag for <html lang>, from the same setting `labels` is read in.
        # Display pages only — the admin UI has its own per-device language
        # (`ui_lang`), so those templates deliberately keep lang="en".
        lang=state.settings.get('locale', 'en'),
        show_lane_header=state.settings.get('show_lane_header', True),
        show_name_header=state.settings.get('show_name_header', True),
        show_club_header=state.settings.get('show_club_header', True),
        show_time_header=state.settings.get('show_time_header', True),
        show_delta_header=state.settings.get('show_delta_header', True),
        show_position_header=state.settings.get('show_position_header', True),
        show_name=state.settings.get('show_name', True),
        show_club=state.settings.get('show_club', True),
        show_delta=state.settings.get('show_delta', True),
        show_position=state.settings.get('show_position', True),
        show_podium=state.settings.get('show_podium', True),
        show_laps=state.settings.get('show_laps', False),
        lap_direction=state.settings.get('lap_direction', 'up'),
        num_lanes=int(state.settings.get('num_lanes', 6)),
        finish_debounce=float(state.settings.get('finish_debounce', 3.0)),
        # The Timing pane warns when the delay is off the default and offers it
        # back, so the default has to reach the template rather than be retyped.
        finish_debounce_default=state.FINISH_DEBOUNCE_DEFAULT,
        split_min_duration=float(state.settings.get('split_min_duration', 1.0)),
        split_min_duration_default=state.SPLIT_MIN_DEFAULT,
        pool_length=int(state.settings.get('pool_length', 25)),
        touchpad_sides=int(state.settings.get('touchpad_sides', 1)),
        lenex_pool_length=int(state.meet.meet_info.get('pool_length_lenex') or 0),
        theme_colors={**state.DEFAULT_THEME_COLORS, **state.settings.get('theme_colors', {})},
        theme_fonts={**state.DEFAULT_THEME_FONTS,  **state.settings.get('theme_fonts',  {})},
        provision_stale=state.provisioning_stale(),
        # Cache key for the page's own static JS. This Pi updates itself, so a
        # browser can hold a cached script against markup deployed since; the ref
        # changes with every update and `git_describe()` is cached, so this costs
        # nothing per render.
        server_version=state.git_describe()['version'],
        # Shown as a banner on the settings panel until the login is changed off
        # the one every install ships with (and that the docs print).
        default_credentials=state.using_default_credentials(),
    )


# The visitor's choice lives in these cookies, one per device and per server
# (docs/app.md `T-08`). A year, because the choice is meant to outlive the meet.
PREF_COOKIES  = {'lang': 'splouch_lang', 'style': 'splouch_style'}
PREF_MAX_AGE  = 365 * 24 * 3600


def _pref(request: Request, name, valid):
    """`?name=` for this request, else the cookie, else '' — invalid values ignored.

    The query string still wins for one request so a shared link opens the way its
    sender saw it and an old bookmark keeps working; `remember_prefs` then writes
    it to the cookie so the next page needs no parameter at all.
    """
    for value in (request.query_params.get(name, ''),
                  request.cookies.get(PREF_COOKIES[name], '')):
        if value in valid:
            return value
    return ''


def client_prefs(request: Request):
    """The visitor's language and label style for a phone page, else this meet's.

    Unknown values fall back rather than erroring — a stale bookmark or a cookie
    for a language this server no longer ships must not break the board
    (docs/app.md `T-06`, `T-08`, `T-09`).
    """
    lang  = _pref(request, 'lang', dict(state.available_locales())) \
            or state.settings.get('locale', 'en')
    style = _pref(request, 'style', ('short', 'long')) \
            or state.settings.get('label_style', 'long')
    return lang, style


def remember_prefs(request: Request, response):
    """Turn a valid `?lang=` / `?style=` on this request into the device cookie.

    Called by the shell, the one page a shared link lands on: after it, the tabs
    and every later visit read the cookie and the URL carries nothing.
    """
    for name, valid in (('lang', dict(state.available_locales())),
                        ('style', ('short', 'long'))):
        value = request.query_params.get(name, '')
        if value in valid and value != request.cookies.get(PREF_COOKIES[name]):
            response.set_cookie(PREF_COOKIES[name], value, max_age=PREF_MAX_AGE,
                                samesite='lax')
    return response


def client_strings(request: Request):
    """`t`, `labels`, `lang` and `ui_style` for a phone page, honouring `client_prefs`.

    With no choice made these are exactly what `_globals()` and `_mobile_strings()`
    produce today. With one, they come from the same bundle `GET /i18n/{lang}`
    serves — this Pi's custom wording included, since it reads the files directly.
    """
    lang, style = client_prefs(request)
    default_lang  = state.settings.get('locale', 'en')
    default_style = state.settings.get('label_style', 'long')
    if lang == default_lang and style == default_style:
        return dict(t=state._mobile_strings(), labels=state.load_locale(),
                    event_vocab=state.load_event_translations(),
                    lang=lang, ui_style=style)
    bundle = state.i18n_bundle(lang)
    return dict(t=bundle['mobile'], labels=bundle['labels'][style],
                event_vocab=bundle['event_name'],
                lang=lang, ui_style=style)


def render(request: Request, name: str, **ctx):
    """Render a template, merging in the global context."""
    return templates.TemplateResponse(request, name, {**_globals(), **ctx})


def display_config():
    """Machine-readable display config for native clients (see docs/api.md §6).

    The same values ``_globals()`` injects into templates, plus the meet title —
    lane count, visible columns, theme colours/fonts, labels — so a native client
    (the Qt TV display) can theme itself without scraping a rendered page.
    """
    cfg = _globals()
    cfg['meet_title'] = state.settings.get('meet_title', '')
    # The display's own status strings ("waiting for the server"), plus the locale
    # code that selected them. Templates get these from Jinja; a native client has
    # no template, so they ride along here.
    cfg['locale'] = state.settings.get('locale', 'en')
    cfg['display_strings'] = state.display_strings()
    # Carousel overlay: the same list `/live` builds for its template, so a native
    # client can show the same splash. Filenames only — the images themselves come
    # from GET /images/{filename}.
    try:
        cfg['carousel_images'] = sorted(
            f for f in os.listdir(state.IMAGES_DIR)
            if os.path.isfile(os.path.join(state.IMAGES_DIR, f)))
    except OSError:
        cfg['carousel_images'] = []
    cfg['carousel_interval'] = int(state.settings.get('carousel_interval', 10))
    # Which console is feeding this server, and whether it produces times at all
    # (docs/api.md §6). The cloud's twin of this is the relay `settings` block, so a
    # client connecting straight to a Pi gates its Results screen on the same fact.
    cfg['console'] = state.console_state()
    # The ref this server is running, so a display can say whether it is in step and
    # update itself to match without the operator going to a browser. The same value
    # `/displays_update` broadcasts as `target` — a commit, never a branch, so the
    # two ends stay pinned together (docs/api.md §2, §6).
    cfg['server_version'] = state.git_describe()['version']
    return cfg


def redirect(url: str, status_code: int = 303):
    """See-Other redirect (GET on the target) for post-action navigation."""
    return RedirectResponse(url, status_code=status_code)


class CrossSiteRequest(Exception):
    """Raised by :func:`require_login` for a request another site set off."""


def require_login(request: Request):
    """FastAPI dependency: allow the request only when a session user is set.

    Unauthenticated requests raise :class:`NotAuthenticated`, which the app's
    exception handler turns into a redirect to /login (browser navigations follow
    it; XHR endpoints are only ever hit from the already-authenticated settings
    page).

    Cross-site requests are refused even when the session is valid. Several
    destructive endpoints here are plain GETs (`/meet_clear`, `/theme_delete_all`,
    …), and the session cookie is SameSite=Lax, which still attaches it to a
    top-level navigation — so a link handed to a signed-in operator was enough to
    wipe the meet files. `Sec-Fetch-Site` is set by the browser, cannot be
    overridden by the page, and is absent on every non-browser client (the Qt
    display, curl, the native apps), which is why absence has to mean allow.
    """
    if request.headers.get('sec-fetch-site') == 'cross-site':
        raise CrossSiteRequest()
    if not request.session.get('user'):
        raise NotAuthenticated()


def same_origin(ws) -> bool:
    """True unless this WebSocket handshake came from another site.

    The same-origin policy does not cover WebSockets: any page, anywhere, may open
    a socket to this Pi, and the browser attaches our cookies to the handshake. So
    the check every other request gets for free has to be made by hand here, or a
    visitor's browser becomes a bridge from the open internet onto the pool-deck
    LAN — see `/ws/terminal`, which carries a live shell.

    A missing Origin is allowed on purpose: only browsers send one. The Qt display
    and the native clients (docs/api.md §2) do not, and they are the reason this is
    a check on mismatch rather than a requirement.
    """
    origin = ws.headers.get('origin')
    if origin is None:
        return True
    try:
        netloc = urlparse(origin).netloc
    except ValueError:
        return False
    return bool(netloc) and netloc == ws.headers.get('host', '')


async def ws_guard(ws, login_required: bool = False) -> bool:
    """Close a WebSocket that fails the origin (and optionally session) check.

    Returns True when the handler may go on to accept the socket. Closes with 1008
    (policy violation) rather than accepting-then-closing, so a rejected client
    never sees a single frame.
    """
    if not same_origin(ws) or (login_required and not ws.session.get('user')):
        await ws.close(code=1008)
        return False
    return True


def save_upload(upload, dest: str):
    """Persist a Starlette UploadFile to *dest*."""
    upload.file.seek(0)
    with open(dest, 'wb') as f:
        shutil.copyfileobj(upload.file, f)
