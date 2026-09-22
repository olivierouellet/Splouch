"""This Pi's own QR code, for an operator to print (`app.md` `P-16`).

A spectator at a pool wants the meet on the Pi in the building — no internet
dependency, an unthrottled race clock (`P-11`) — and typing `splouch.local:5000`
into a phone is the step that loses them. A code taped to the wall is the answer,
and the operator is the only person who can put one there, so the Pi has to be
able to draw its own.

Three things about the link are not this page's to choose:

* **The host is the cloud, never this Pi.** An App Link is verified per host, and
  a Pi has no `https` and no certificate, so nothing can verify one. The Pi
  travels in the query. The host comes from `cloud_relay_url` — the cloud this
  Pi publishes to, which is the app's default server in any deployment where
  scanning works at all. With no cloud configured there is no host to name and
  the page says so rather than inventing one.
* **The Pi's own address is its mDNS name**, `http://<host>.local[:port]`, never
  the raw IP the operator may be browsing by: a client refuses cleartext to
  anything but a `.local` name and the loopbacks (`P-12`), so a code minted from
  an address bar would scan into "cannot add this server" on the deck. The port
  follows the request, so a Pi reached on `:5000` mints `:5000` and an installed
  one — port 80 in front of uvicorn, via the iptables redirect in `install.sh` —
  mints the shorter form.
* **The shape itself** lives in `shared/py/splouch_links.py`, the one copy the
  cloud's `GET /add` parses with. This module only decides *what* to put in it.

The page is unauthenticated, like `/live` and `/operator`: it carries the mDNS
name that avahi already broadcasts to the whole LAN and `_splouch._tcp` already
advertises, and an operator who has to log in to print a poster prints no poster.
"""
import socket

import segno
from fastapi import APIRouter, Request

import splouch_links
import state
from web import render

router = APIRouter(tags=['Invite'])


def invite(request: Request):
    """Everything the page draws: the link, its two halves, and why there is none.

    One helper rather than logic in the route, so the reason a code is missing is
    decided in the same place the code is — a template that worked out for itself
    when to show an error would be a second, quieter copy of this rule.
    """
    cloud = (state.settings.get('cloud_relay_url') or '').strip()
    origin = splouch_links.mdns_origin(socket.gethostname(), request.url.port)
    link = splouch_links.invite_link(cloud, origin) if cloud else None
    if link:
        reason = ''
    elif not cloud:
        reason = 'no_cloud'
    elif not origin:
        reason = 'no_hostname'
    else:
        # A cloud URL that will not parse — cleartext to a public name, most
        # likely, which the app would refuse as a server and refuse again as a
        # link host. Same words as a missing one: the fix is the same field.
        reason = 'no_cloud'
    return {'link': link, 'origin': origin, 'cloud': cloud, 'reason': reason}


@router.get('/qr')
def route_qr(request: Request):
    """The printable page: the code, the address under it, and nothing else.

    `error='m'` — 15% recovery. A poster on a pool deck gets splashed, taped over
    a corner and photographed at an angle; `l` is the smallest code and the one
    that stops scanning first, and `h` buys robustness this link is too long to
    spend modules on. `scale`/`border` are left to CSS so the same SVG prints at
    whatever size the paper gives it.

    The address is printed as text below the code on purpose. A code nobody can
    read is a code nobody can check against, and it is also the fallback for the
    phone whose camera will not focus on a wall.
    """
    data = invite(request)
    svg = ''
    if data['link']:
        svg = segno.make(data['link'], error='m').svg_inline(
            svgclass=None, lineclass=None, omitsize=True, dark='#000000', light='#ffffff')
    # The panel's per-device language, the same one Settings resolves: this page is
    # opened from that sidebar and an operator should not change language to print.
    return render(request, 'qr.html', svg=svg,
                  t=state.settings_strings(state.ui_locale(request)), **data)
