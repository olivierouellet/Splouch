"""Strings for clients that render themselves.

The Qt display has always been served its status strings (`display_strings` in
`GET /config`); these two endpoints do the same for everything else, so no client
repo carries a copy of `shared/locales/` (docs/api.md §5.9,
docs/mobile-features.md `T-05`). Adding a language is one file here.
"""
import hashlib
import json

from fastapi import APIRouter, Request
from fastapi.responses import Response

import state

router = APIRouter(tags=['Strings'])


def etagged(request: Request, payload):
    """JSON with an ETag, and a 304 when the client already has that body.

    The strings change only when a locale file does, so a client fetches a language
    once and revalidates for a few bytes after that. `no-cache` means *revalidate*,
    not *do not cache*: an operator editing `scoreboard/locale/fr.toml` must not
    have to wait out a max-age for the pool's boards to pick it up.
    """
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    etag = '"' + hashlib.sha256(body).hexdigest()[:16] + '"'
    headers = {'ETag': etag, 'Cache-Control': 'no-cache'}
    if request.headers.get('if-none-match') == etag:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type='application/json', headers=headers)


@router.get('/locales')
def route_locales(request: Request):
    """The languages this server can serve, custom files included."""
    return etagged(request, [{'code': c, 'name': n} for c, n in state.available_locales()])


@router.get('/i18n/{lang}')
def route_i18n(lang: str, request: Request):
    """One language: chrome plus both label styles. Unknown `lang` falls back to en.

    `lang` never reaches the filesystem unchecked — `i18n_bundle` resolves it
    against `available_locales()` first.
    """
    return etagged(request, state.i18n_bundle(lang))
