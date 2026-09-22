"""The QR-code link shape (`app.md` `P-16`), for both servers, from one file.

Two halves of one feature live in different servers here. The **cloud** parses a
link that a camera handed to a browser (`GET /add`) and must show the reader what
they scanned; the **Pi** mints one so an operator can print it. They have to agree
about the shape down to the percent-encoding, and neither of them is the client —
that is an app in another repo, which agreed to the same shape before either of
these existed. So the shape is written once, here, and nothing in this module
knows which server imported it.

The link is::

    https://<the app's default host>/add?server=<origin, percent-encoded>

`parse_origin` is the same rule the apps' own `ServerAddress.parse` applies, and
deliberately so: `http` is accepted only for a `.local` name or a developer
loopback (`app.md` `P-12`), everything else must be `https`, and two spellings of
one server normalise to one string. A printed code is a stranger's input in a way
a typed address is not, so this side holds it to exactly the floor the client
does rather than a looser one — a page that cheerfully displays an origin the app
would refuse is a page that sends the reader to a dead end.

Nothing here reaches the network or the filesystem. Minting a link does not check
that the server answers, and parsing one does not either: the client asks
`GET /server` after the reader says yes (`P-13`), and that is the only handshake
in the feature.

Imported as `splouch_links` by `cloud/cloud_server.py` and `server/routes/qr.py`;
both put `shared/py/` on `sys.path` at import time (`cloud_paths`, `server/paths.py`).
"""
import ipaddress
import re
from urllib.parse import quote, urlsplit

# The path the App Link and the Universal Link are verified for, and the query
# parameter carrying the server. The Android manifest declares this exact path —
# a prefix match would also swallow `/address` and anything else starting with
# those four characters — and the `apple-app-site-association` components block
# names both.
INVITE_PATH = '/add'
INVITE_PARAM = 'server'

# `http` is allowed to these and to `*.local`, and to nothing else. The first is
# the pool's Pi by its mDNS name; the rest are what a developer's emulator dials.
# The apps' network policies list the same names and cannot express IP ranges,
# which is why a Pi travels by name and never as a raw address.
LOCAL_HOSTS = frozenset({'localhost', '127.0.0.1', '10.0.2.2', '::1'})

_DEFAULT_PORTS = {'http': 80, 'https': 443}

# A host name, or an IPv4 literal — labels of letters, digits and hyphens, none of
# them starting or ending with a hyphen.
#
# This is here because `urlsplit` is far more permissive than the URL parsers on
# the other side of this link. It hands back `<script>alert(1)<` as the "host" of
# `https://<script>alert(1)</script>`, where `java.net.URI` — which is what the
# Android client parses the very same string with — rejects it outright. Without
# this check the `/add` page would quote whatever a scanned code carried back at
# the reader as though it were an address; escaped, so not an injection, but
# nonsense presented as a server, and one no app would ever accept.
_HOST_RE = re.compile(
    r'^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))*$')


def _valid_host(host):
    """Whether *host* is a name or address a stricter parser would also accept."""
    if ':' in host:                       # only an IPv6 literal may contain a colon
        try:
            return ipaddress.ip_address(host).version == 6
        except ValueError:
            return False
    return len(host) <= 253 and bool(_HOST_RE.match(host))


def is_local_name(host):
    """Whether cleartext is acceptable to *host* — `.local`, or a loopback."""
    return host.endswith('.local') or host in LOCAL_HOSTS


def parse_origin(text):
    """Normalise *text* to a scheme://host[:port] origin, or return ``None``.

    ``None`` covers both ways a link can fail — unparseable, and parseable but
    cleartext to somewhere that is not the local network. The callers here have
    nothing different to say about the two: the page shows no server either way,
    and the Pi never mints either. The apps distinguish them because they answer
    the reader in words (`P-16`'s prompt); a server does not.

    A bare `splouch.local:5000` is read as `https://` first, like the app's
    address field, and then fails the cleartext rule — which is correct. The
    scheme is part of an origin, and guessing `http` for anything with a dot in
    it would quietly widen the one rule this module exists to hold.
    """
    s = (text or '').strip()
    if not s:
        return None
    if '://' not in s:
        s = 'https://' + s
    try:
        parts = urlsplit(s)
        port = parts.port           # raises on a non-numeric or out-of-range port
    except ValueError:
        return None
    scheme = (parts.scheme or '').lower()
    if scheme not in _DEFAULT_PORTS:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    host = (parts.hostname or '').strip().lower()
    if not host or not _valid_host(host):
        return None
    if port == _DEFAULT_PORTS[scheme]:
        port = None
    if scheme == 'http' and not is_local_name(host):
        return None
    # urlsplit strips the brackets an IPv6 literal needs back in the origin.
    if ':' in host:
        host = '[' + host + ']'
    return f'{scheme}://{host}' + (f':{port}' if port else '')


def invite_link(host_origin, server_origin):
    """The link a QR code carries: *server_origin* offered via *host_origin*.

    *host_origin* is the cloud whose `/add` page and `/.well-known/` files back
    the link — the app's default server, and the only authority the app will
    accept (`P-16`). It is normalised with the same rule, so a trailing slash or
    a redundant `:443` in an operator's setting cannot change the link a Pi
    prints from one boot to the next.

    Returns ``None`` when either side fails to parse, so a caller with a
    half-configured server renders nothing rather than a code that scans into an
    error.
    """
    host = parse_origin(host_origin)
    server = parse_origin(server_origin)
    if not host or not server:
        return None
    # `safe=''` on purpose: the `:` and `/` of the origin are percent-encoded, so
    # the value cannot be read as a path of its own by anything between the
    # camera and the app.
    return f'{host}{INVITE_PATH}?{INVITE_PARAM}={quote(server, safe="")}'


def mdns_origin(hostname, port=None):
    """`http://<hostname>.local[:port]` — the one form of a Pi's own address.

    Never a raw IP: a client refuses cleartext to anything but a `.local` name
    and the loopbacks (`P-12`), so a code minted from the address an operator
    happens to be browsing by would scan into "cannot add this server" on the
    deck. The name is the Pi's own — avahi publishes it, and `_splouch._tcp`
    advertises the same host (`api.md` §4).

    *port* is dropped when it is 80, which is where an installed Pi answers: the
    iptables redirect in `install.sh` puts port 80 in front of uvicorn's 5000, and
    a shorter URL is a sparser QR code to read across a pool deck.
    """
    host = (hostname or '').strip().lower().rstrip('.')
    if not host:
        return None
    # `socket.gethostname()` already ends in `.local` on some platforms (macOS,
    # and a Pi whose search domain is set); appending a second one is a name that
    # resolves nowhere.
    if host.endswith('.local'):
        host = host[:-len('.local')]
    if not host or '.' in host:
        # A fully-qualified name that is not `.local` is not the mDNS name, and
        # a dotted label would make `<host>.local` a subdomain avahi never
        # publishes. Nothing sensible to mint.
        return None
    return parse_origin(f'http://{host}.local' + (f':{port}' if port and int(port) != 80 else ''))
