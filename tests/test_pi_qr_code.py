"""The QR code an operator downloads for a poster (`app.md` `P-16`).

**The code names the cloud, not this Pi**, and that is the single property worth
defending here, because the other answer is the tempting one. The useful server at
a pool *is* the Pi in the building — no internet dependency, an unthrottled race
clock (`P-11`) — so a code carrying `http://splouch.local:5000` looks obviously
right until you ask who reads a poster:

* a spectator on cellular, for whom a `.local` name resolves to nothing;
* a spectator on a guest network with client isolation, where mDNS is blocked even
  though they did join the wifi;
* and a spectator whose phone reaches it fine — the only one of the three it works
  for.

A poster cannot ask which of those it is being read by, so it names the address
that works from all three. The Pi is offered afterwards, by `P-12`'s browse, once
the reader is on the picker and their phone is on the venue's network.

The second thing that falls out of naming a cloud: a cloud session's launch screen
is the **meet list**, so the reader lands there rather than on a board — which is
where `P-06`'s unofficial-results disclaimer is. A Pi session skips the picker
(§0.2), so a code naming a Pi would have walked a first-time spectator straight
past a **must**.

**The file is the poster, not a picture of a code.** It is a download rather than a
page, so whatever it does not carry is lost for good — which is why the address is
drawn into the image and why the resolution is fixed to something printable rather
than to whatever a screen wanted.
"""
import io
import os
import sys

import pytest
from PIL import Image

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (REPO, os.path.join(REPO, 'server'), os.path.join(REPO, 'shared', 'py')):
    sys.path.insert(0, path)

import splouch_links                                    # noqa: E402
import state                                            # noqa: E402
from routes.qr import invite, poster, route_qr_png      # noqa: E402

QR_ROUTE = os.path.join(REPO, 'server', 'routes', 'qr.py')
CLOUD_TAB = os.path.join(REPO, 'server', 'templates', 'settings', 'cloud.html')


@pytest.fixture
def cloud(monkeypatch):
    """Point this Pi at a cloud, restoring whatever the settings dict held."""
    def set_url(url):
        monkeypatch.setitem(state.settings, 'cloud_relay_url', url)
    set_url('https://splouch.ca')
    return set_url


def image(link=None, origin=None):
    data = invite()
    return Image.open(io.BytesIO(poster(link or data['link'], origin or data['origin'])))


# ── what the code carries ──────────────────────────────────────────────────────

def test_the_code_names_the_cloud_this_pi_publishes_to(cloud):
    assert invite()['link'] == \
        'https://splouch.ca/add?server=https%3A%2F%2Fsplouch.ca'


def test_no_local_address_is_ever_minted(cloud, monkeypatch):
    """The property this whole feature turns on.

    A `.local` name resolves only for a device already on the venue's wifi, and a
    poster cannot ask. Whatever the Pi's own hostname is, none of it may reach the
    code.
    """
    monkeypatch.setattr('socket.gethostname', lambda: 'splouch')
    data = invite()
    assert '.local' not in data['link']
    assert data['origin'] == 'https://splouch.ca'


def test_a_club_cloud_is_carried_under_the_apps_default_host(cloud):
    """The two halves come from different places, and only one is configurable.

    The authority must be the host the app verified; the `server=` value is the
    cloud this Pi publishes to. They are the same string in the canonical
    deployment and differ for a club running its own relay.
    """
    cloud('https://scores.myclub.ca')
    link = invite()['link']
    assert link.startswith(f'{splouch_links.DEFAULT_APP_SERVER}/add?')
    assert link.endswith('server=https%3A%2F%2Fscores.myclub.ca')


def test_the_default_host_is_not_taken_from_the_pi_s_own_setting(cloud):
    """A Pi cannot be asked what the app on a stranger's phone was built against."""
    cloud('https://scores.myclub.ca')
    assert 'scores.myclub.ca/add' not in invite()['link']


def test_the_code_does_not_depend_on_how_the_panel_was_reached(cloud):
    """`invite()` takes no request, which is the structural form of this test.

    A poster minted from the operator's address bar would carry whatever they
    happened to type, and there is no way for that to leak in if the function
    cannot see it.
    """
    import inspect
    assert not inspect.signature(invite).parameters


def test_the_code_leads_to_a_meet_list_and_not_to_a_board(cloud):
    """`P-06`'s disclaimer lives on the picker, and a Pi session skips the picker.

    A cloud origin is what keeps the notice in the path of a spectator who arrived
    by camera — the reader who has most likely never seen it.
    """
    origin = invite()['origin']
    assert origin.startswith('https://')
    assert not splouch_links.is_local_name(origin.split('://', 1)[1].split(':')[0])


# ── the file is the poster ─────────────────────────────────────────────────────

def test_the_image_is_big_enough_to_print(cloud):
    """~10 cm across at 300 dpi, the size a code wants to be read from a metre or two.

    Checked as a physical size rather than a pixel count, because the pixel count
    alone does not say how large anything prints.
    """
    sheet = image()
    # PNG stores density as whole pixels per *metre*, so 300 dpi comes back as
    # 299.9994 — the format cannot represent it exactly and the rounding is not
    # a bug to chase.
    dpi = sheet.info['dpi'][0]
    assert round(dpi) == 300, 'without this a word processor lays it out at 96 dpi'
    cm = sheet.width / dpi * 2.54
    assert 9 <= cm <= 12, f'{cm:.1f} cm across'


def test_a_longer_address_does_not_make_a_bigger_poster(cloud):
    """The module count grows with the URL, so the scale is derived per symbol.

    Left as a constant, a club with a long domain would get a physically larger
    code than `splouch.ca` from the same button.
    """
    short = image()
    cloud('https://scores.swimclub-montreal.ca')
    long = image()
    assert abs(short.width - long.width) < short.width * 0.1


def test_the_address_is_drawn_under_the_code(cloud):
    """A download carries no page around it, so what is not in the file is lost.

    A code alone fails completely the moment a camera will not focus on a wall,
    and it is also the only way anyone checks the poster says the right thing.
    """
    sheet = image()
    assert sheet.height > sheet.width, 'no room was left under the code'
    strip = sheet.crop((0, sheet.width, sheet.width, sheet.height)).convert('L')
    assert strip.getextrema()[0] < 128, 'the space under the code is blank'


def test_the_address_never_overflows_the_width(cloud):
    """The type is fitted to the code, so a long club domain cannot run off the edge."""
    cloud('https://scores.a-very-long-swimming-club-name-indeed.example.com')
    sheet = image()
    strip = sheet.crop((0, sheet.width, sheet.width, sheet.height)).convert('L')
    edges = [strip.crop((0, 0, 4, strip.height)), strip.crop((strip.width - 4, 0, strip.width, strip.height))]
    for edge in edges:
        assert edge.getextrema()[0] > 200, 'ink is touching the edge of the sheet'


def test_the_code_is_drawn_crisp_and_not_scaled_up(cloud):
    """A QR is squares, and resampling softens the edges a camera looks for.

    Pure black and white with nothing in between is what says no interpolation
    happened on the way out.
    """
    code = image().crop((0, 0, image().width, image().width)).convert('L')
    greys = [value for value, count in enumerate(code.histogram()) if count and 16 < value < 240]
    assert not greys, f'intermediate greys in the code: {greys[:5]}'


# ── the download ───────────────────────────────────────────────────────────────

def test_the_download_is_a_png_attachment_named_for_the_server(cloud):
    response = route_qr_png()
    assert response.status_code == 200
    assert response.media_type == 'image/png'
    assert response.headers['content-disposition'] == \
        'attachment; filename="splouch-qr-splouch.ca.png"'


def test_no_cloud_configured_serves_no_image(cloud):
    """Rather than a poster-shaped picture saying "not configured".

    That is the one output worse than none, because it is the one that ends up on
    a wall.
    """
    cloud('')
    assert invite()['link'] is None and invite()['reason'] == 'no_cloud'
    assert route_qr_png().status_code == 404


def test_a_cloud_url_the_app_would_refuse_is_the_same_answer(cloud):
    """Cleartext to a public name: refused as a server, so refused as a code."""
    cloud('http://scores.example.com')
    assert invite()['reason'] == 'no_cloud'
    assert route_qr_png().status_code == 404


# ── where the operator finds it ────────────────────────────────────────────────

def test_the_button_lives_with_the_field_it_is_made_of():
    """The Cloud tab, because the code is built from the Server URL above it."""
    tab = open(CLOUD_TAB, encoding='utf-8').read()
    assert 'href="/qr.png" download' in tab
    assert 'cloud_relay_url' in tab


def test_the_button_is_hidden_rather_than_dead_without_a_cloud():
    """And the line in its place names the field to fill."""
    tab = open(CLOUD_TAB, encoding='utf-8').read()
    assert '{% if qr_link %}' in tab
    assert 't.qr_no_cloud' in tab


def test_the_link_shape_is_the_shared_one():
    """The Pi and the cloud's `GET /add` must not drift, so neither owns the shape."""
    source = open(QR_ROUTE, encoding='utf-8').read()
    assert 'splouch_links' in source
    assert '/add?' not in source, 'the link is built by the shared helper, not here'
    assert splouch_links.INVITE_PATH == '/add'
