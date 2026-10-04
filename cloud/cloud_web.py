"""What both halves of the relay render pages with.

The control plane (`cloud_control`: picker, admin) and the workers
(`cloud_server`: a meet's board, results and schedule) are two apps since the
scaling split (docs/architecture/scaling.md), but they serve one site and draw
the same templates. The visitor's preferences (language, Appearance), the label
tables and the template environment are therefore one module both import, so a
rule changed here changes on every page at once.
"""

import hashlib
import json
import os

from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

import cloud_i18n
from cloud_paths import _HERE, SHARED_TEMPLATES_DIR
from splouch_i18n import reader_palette

_available_locales = cloud_i18n.available_locales
_i18n_bundle = cloud_i18n.i18n_bundle

templates = Jinja2Templates(
    directory=[os.path.join(_HERE, "templates"), SHARED_TEMPLATES_DIR]
)


def render(request, name, **ctx):
    return templates.TemplateResponse(request, name, ctx)


# The visitor's choice, per device and per server (docs/app.md `T-08`): the picker
# writes these, every meet page reads them, and the URL carries nothing. The Pi
# uses the same names (server/web.py), so the rule is one rule.
PREF_COOKIES = {
    "lang": "splouch_lang",
    "style": "splouch_style",
    "theme": "splouch_theme",
}
PREF_MAX_AGE = 365 * 24 * 3600


def _pref(request, name, valid):
    """`?name=` for this request, else the cookie, else '' — invalid values ignored.

    The query string still wins for one request so a shared link opens the way its
    sender saw it and an old bookmark keeps working; `_remember_prefs` then writes
    it to the cookie so the next page needs no parameter at all.
    """
    for value in (
        request.query_params.get(name, ""),
        request.cookies.get(PREF_COOKIES[name], ""),
    ):
        if value in valid:
            return value
    return ""


def _remember_prefs(request, response):
    """Turn a valid `?lang=` on this request into the device cookie.

    Not `?style=`: the cloud's style is fixed (`_client_style`), so there is no
    choice to remember.
    """
    value = request.query_params.get("lang", "")
    if value in {c for c, _ in _available_locales()} and value != request.cookies.get(
        PREF_COOKIES["lang"]
    ):
        response.set_cookie(
            PREF_COOKIES["lang"], value, max_age=PREF_MAX_AGE, samesite="lax"
        )
    return response


def _client_lang(request, meet):
    """The language to render a meet page in: the visitor's choice, else the meet's.

    The picker stores the choice in a cookie and every meet page reads it, so one
    control reaches the tabs (docs/app.md `T-06`, `T-08`); `?lang=` wins for one
    request so a shared link opens as sent. An unknown code falls back rather than
    erroring: a stale bookmark must not break the board.
    """
    return _pref(
        request, "lang", {code for code, _ in _available_locales()}
    ) or _meet_lang(meet)


def _client_labels(meet, lang, style):
    """Column headers for a chosen language and style.

    In the meet's own language and the style the meet's labels were resolved in,
    this is exactly `settings.labels` — what the operator picked, byte for byte.
    Otherwise it is the shipped table for that language and style, the same body
    `GET /i18n/{lang}` serves (api.md §5.9) and the one the apps draw from.
    """
    s = meet.get("settings", {})
    if lang == _meet_lang(meet) and style == s.get("label_style", "short"):
        return s.get("labels", {})
    labels = dict(_i18n_bundle(lang)["labels"].get(style, {}))
    # The relay folds a few [mobile] strings into `labels`; keep whatever else the
    # meet sent so nothing that read them starts rendering blank.
    for key, value in s.get("labels", {}).items():
        labels.setdefault(key, value)
    return labels


def _client_style(request, meet):
    """Always `long` on the board (`T-09`), as in the apps: the picker offers no
    label control, and a stale `splouch_style` cookie or `?style=` link is ignored
    rather than cleared, so a control that comes back finds each visitor's choice
    where they left it. The schedule's cards take the short words instead
    (`S-01`) — see route_schedule."""
    return "long"


def _client_palette(request):
    """The reader's Appearance (docs/app.md `P-15`), as template context: the
    picker, the shell and the three tabs all draw from it, never from the meet's
    `theme_colors`. Cookie only — the picker applies it in place, so there is no
    link parameter to honour or remember."""
    return reader_palette(request.cookies.get(PREF_COOKIES["theme"], ""))


def _etagged(request, payload):
    """JSON with an ETag, and a 304 when the client already has that body.

    One response serves every meet on this server, so it is worth caching and worth
    revalidating cheaply. `no-cache` means revalidate, not do not cache.
    """
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/json", headers=headers)


def _browser_lang(request):
    """First Accept-Language entry matching an available locale, or None."""
    available = {code for code, _ in _available_locales()}
    accept = request.headers.get("Accept-Language", "")
    for part in accept.replace("-", "_").split(","):
        code = part.split(";")[0].strip().split("_")[0].lower()
        if code in available:
            return code
    return None


def _meet_lang(meet):
    return meet.get("settings", {}).get("locale") or "en"
