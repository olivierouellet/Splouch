"""The QR code an operator downloads to put on a poster (`app.md` `P-16`).

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
  Settings panel says which field to fill instead of offering the download.

**The address is drawn into the image, under the code.** A file that is only a
code is a poster that fails completely the moment a camera will not focus on a
wall — a cracked lens, a low phone, a reader who would rather type. It is also the
only way anyone checks that the poster on the wall is the right one. Drawing it in
is what lets this be a download rather than a page: the operator drops one file
into whatever they are making and nothing is lost on the way.
"""
import io
import os

import segno
from fastapi import APIRouter
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont

import paths
import splouch_links
import state

router = APIRouter(tags=['Invite'])

# Monospace, the same choice the admin panel makes for an address: a host is read
# character by character, and `l`/`1` and `0`/`O` are the characters an operator
# will be asked about over the phone. Bundled rather than a system face, so the
# file a Pi produces does not depend on what the Pi happens to have installed.
FONT_FILE = 'RobotoMono[wght].ttf'

# Across the code, in pixels. 1200 px is ~10 cm at 300 dpi, which is the size a
# code wants on a poster to be read from one or two metres. The PNG carries that
# dpi in its header, so a word processor places it at 10 cm rather than at the
# 31 cm that 1200 px at the default 96 dpi would give.
TARGET_PX = 1200
PRINT_DPI = 300


def _font_path():
    return os.path.join(paths.STATIC_DIR, 'fonts', FONT_FILE)


def invite():
    """The link the code carries, or the reason there is none.

    One helper rather than logic in the route, so the reason a code is missing is
    decided in the same place the code is.

    It reads no request: the link says nothing about how the panel was reached,
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


def poster(link, origin):
    """A print-resolution PNG of *link*, with *origin* written underneath.

    `error='m'` — 15% recovery. A poster on a pool deck gets splashed, taped over
    a corner and photographed at an angle; `l` is the smallest code and the one
    that stops scanning first, and `h` buys robustness this link is too long to
    spend modules on.

    The code is rendered straight to its final pixel size rather than drawn small
    and scaled up: a QR is squares, and any resampling softens the edges a camera
    is looking for. The module count changes with the length of the URL, so the
    scale is derived per symbol — which keeps the output about the same physical
    size whichever cloud is named, instead of making a club's longer address
    produce a bigger poster.
    """
    code = segno.make(link, error='m')
    # `symbol_size` counts the quiet zone, so the border is already in the width
    # every measurement below is taken from.
    modules = code.symbol_size(border=4)[0]
    buf = io.BytesIO()
    code.save(buf, kind='png', scale=max(1, round(TARGET_PX / modules)), border=4)
    buf.seek(0)
    drawn = Image.open(buf).convert('RGB')
    width, height = drawn.size

    # Fit the address to the code's width. Sized to fill rather than to a constant,
    # so a long club address and a short one read with the same weight instead of
    # the long one shrinking into a caption — and so neither can overflow.
    size = min(int(width * 0.085), 200)
    while size > 8:
        font = ImageFont.truetype(_font_path(), size)
        if font.getbbox(origin)[2] <= width * 0.86:
            break
        size -= 2
    font = ImageFont.truetype(_font_path(), size)
    # `getbbox` measures in float units, so the canvas height is rounded rather
    # than handed to Pillow as a fraction of a pixel.
    left, top, right, bottom = font.getbbox(origin)
    pad = int(size * 0.55)

    sheet = Image.new('RGB', (width, round(height + (bottom - top) + pad * 2)), 'white')
    sheet.paste(drawn, (0, 0))
    # `- top` because getbbox measures from the baseline origin, not the glyph top.
    ImageDraw.Draw(sheet).text(((width - (right - left)) / 2, height + pad - top),
                               origin, font=font, fill='black')
    out = io.BytesIO()
    sheet.save(out, format='PNG', dpi=(PRINT_DPI, PRINT_DPI))
    return out.getvalue()


@router.get('/qr.png')
def route_qr_png():
    """The download itself. 404 when no cloud is configured.

    A 404 rather than a placeholder image: the Settings panel already hides the
    button in that case, so a request arriving here is a stale page or a typed
    URL, and a poster-shaped image saying "not configured" is the one thing worse
    than no file — it is the file that ends up on a wall.
    """
    data = invite()
    if not data['link']:
        return Response(status_code=404)
    host = (data['origin'] or '').split('://')[-1].replace(':', '-').replace('/', '-')
    return Response(
        content=poster(data['link'], data['origin']), media_type='image/png',
        headers={'Content-Disposition': f'attachment; filename="splouch-qr-{host}.png"',
                 'Cache-Control': 'no-cache'})
