"""Strings for clients that render themselves, and who this server is.

The Qt display has always been served its status strings (`display_strings` in
`GET /config`); these two endpoints do the same for everything else, so no client
repo carries a copy of `shared/locales/` (docs/api.md §5.9,
docs/mobile-features.md `T-05`). Adding a language is one file here.
"""
import hashlib
import json

from fastapi import APIRouter, Request
from fastapi.responses import Response

import socket

import state

router = APIRouter(tags=['Strings'])

# The contracts this build implements, for the handshake below. Bumped with the
# headers of docs/api.md and docs/mobile-features.md, which a test pins.
API_CONTRACT    = 'v2'
MOBILE_CONTRACT = 'v4'


@router.get('/server')
def route_server():
    """Who this server is — the handshake a native client makes before anything else.

    An app can be pointed at a Pi or at a cloud (docs/mobile-features.md `P-11`),
    and the two are not interchangeable: this one has a single meet and no picker,
    so a client that lands here goes straight to the board instead of asking for a
    meet list. Guessing from a 404 on `/meets` would be a protocol by accident.

    It doubles as the check a client runs before saving a hand-typed address, and
    as the version handshake now that both contracts are numbered.
    """
    return {
        'kind':     'pi',
        'name':     (state.settings.get('meet_title')
                     or socket.gethostname() or 'Splouch'),
        'contract': {'api': API_CONTRACT, 'mobile': MOBILE_CONTRACT},
    }


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
