"""The QR code an operator prints for this meet (`app.md` `P-16`).

**The code names the cloud, not this Pi.** That is the whole of the design and it
is worth stating plainly, because the opposite is the tempting answer: the useful
server at a pool *is* the Pi in the building (`P-11`) — no internet dependency, an
unthrottled race clock — so why not print its address?

Because a poster is read by whoever walks past it, and a `.local` name resolves
only for a device already joined to the venue's wifi. A spectator on cellular gets
nothing; a guest network with client isolation blocks mDNS even for one that did
join; and `splouch.local` is fragile enough on a multihomed Pi to have its own
troubleshooting page. A printed code cannot ask which network the reader is on, so
it has to name the address that works from anywhere.

So the code carries the cloud this Pi publishes to, and the reader lands on its
**picker** — which is also where `P-06`'s unofficial-results disclaimer lives, so
a spectator arriving by camera passes the notice rather than being dropped onto a
live board. Getting to the Pi itself stays `P-11`–`P-13`'s job: the mDNS browse
offers it to a phone that is already on the right network, which is exactly the
phone the offer makes sense to.

Two halves of the link, from two different places:

* the **authority** is the app's default server (`splouch_links.DEFAULT_APP_SERVER`),
  because an App Link is verified per host and the app matches that one host. It is
  a property of the published app, not of any server here.
* the **`server=` value** is `cloud_relay_url`, the cloud this Pi actually
  publishes to. For the canonical deployment the two are the same string and the
  reader is simply taken to the picker they would have reached anyway; for a club
  running its own cloud they differ, and the code adds that cloud before opening
  its picker. With no cloud configured there is no meet to point anyone at and the
  page says which field to fill.

The page is unauthenticated, like `/live` and `/operator`: it carries a public
cloud URL and nothing else, and an operator who has to log in to print a poster
prints no poster.
"""
import segno
from fastapi import APIRouter, Request

import splouch_links
import state
from web import render

router = APIRouter(tags=['Invite'])


def invite():
    """The link the page draws, or the reason there is none.

    One helper rather than logic in the route, so the reason a code is missing is
    decided in the same place the code is — a template that worked out for itself
    when to show an error would be a second, quieter copy of this rule.

    It reads no request: the link says nothing about how this page was reached,
    which is the point. A poster minted from the operator's address bar would
    carry whatever they happened to type.
    """
    cloud = (state.settings.get('cloud_relay_url') or '').strip()
    origin = splouch_links.parse_origin(cloud)
    link = splouch_links.invite_link(splouch_links.DEFAULT_APP_SERVER, origin) if origin else None
    # One reason, because there is one field. A cloud URL that will not parse —
    # cleartext to a public name, most likely — is the same answer as a missing
    # one: the fix is the same box in Settings → Cloud.
    return {'link': link, 'origin': origin, 'cloud': cloud,
            'reason': '' if link else 'no_cloud'}


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
    data = invite()
    svg = ''
    if data['link']:
        svg = segno.make(data['link'], error='m').svg_inline(
            svgclass=None, lineclass=None, omitsize=True, dark='#000000', light='#ffffff')
    # The panel's per-device language, the same one Settings resolves: this page is
    # opened from that sidebar and an operator should not change language to print.
    return render(request, 'qr.html', svg=svg,
                  t=state.settings_strings(state.ui_locale(request)), **data)
