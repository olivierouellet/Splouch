import http.client
import ipaddress
import json
import re
import socket
import subprocess
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

import state
from web import ActionResult, EnabledFlag, failure, render, require_login

router = APIRouter(tags=["Network"])


class EthIP(BaseModel):
    # Same rules as the installer's prompt (install.sh ask_static_address /
    # ask_gateway). `internet` says whether eth0's network reaches the internet.
    # When it does, the router is required: without it eth0 has no route out and
    # the Cloud relay drops. When it doesn't (a closed console network), eth0 never
    # takes the default route, so the internet and the Cloud go over Wi-Fi, and the
    # router is optional. DNS defaults to the router.
    ip: ipaddress.IPv4Address
    prefix: int = Field(24, ge=8, le=30)
    gateway: ipaddress.IPv4Address | None = None
    dns: ipaddress.IPv4Address | None = None
    internet: bool = True

    @model_validator(mode="after")
    def _in_subnet(self):
        net = ipaddress.IPv4Network(f"{self.ip}/{self.prefix}", strict=False)
        if self.ip in (net.network_address, net.broadcast_address):
            raise ValueError(f"{self.ip} is the network or broadcast address of {net}")
        if self.gateway is None:
            if self.internet:
                raise ValueError("A router is required when Ethernet has internet")
            return self
        if self.gateway not in net:
            raise ValueError(f"Router {self.gateway} is outside {net}")
        if self.gateway == self.ip:
            raise ValueError("The router can't share this Pi's address")
        return self


class EthDhcp(BaseModel):
    internet: bool = True


class WifiMac(BaseModel):
    # '' puts the Pi's own address back.
    mac: str = ""


class WifiConnect(BaseModel):
    ssid: str = ""
    password: str = ""


class WifiStatus(BaseModel):
    enabled: bool
    ssid: str
    wifi_ip: str
    eth_ip: str
    eth_gateway: str = ""
    eth_dns: str = ""
    # False when eth0 is set never to take the default route (a closed console
    # network): the internet and the Cloud then go over Wi-Fi.
    eth_internet: bool = True
    # The address the Pi shows on Wi-Fi now, and the one the active Wi-Fi
    # profile borrows ('' when it uses the Pi's own) — see /wifi_mac_set.
    wifi_mac: str = ""
    wifi_mac_cloned: str = ""
    # eth0 and Wi-Fi on overlapping subnets: the Pi can't tell which port an
    # address on them is behind, so one of the two stops working.
    overlap: bool = False


class Connectivity(BaseModel):
    # full: the Cloud answers. portal: a sign-in page is in the way (captive
    # portal). limited: a network, but the Cloud doesn't answer. none: no network
    # out. unknown: no Cloud address to try and NetworkManager doesn't know.
    state: str
    # The port the internet goes through: 'ethernet', 'wifi' or ''.
    via: str = ""
    portal_url: str = ""


class CloudStatus(BaseModel):
    connected: bool
    running: bool
    url: str
    # Attendance snapshot from the cloud: {'enabled': False} when analytics is
    # off there, else {'enabled': True, 'counts': {window: n}}. None until the
    # relay has heard back (or when disconnected).
    stats: dict | None = None
    # The region the cloud put this organizer in (`ca`, `us`, `mx`, `eu`), '' until the
    # Pi has an assignment. The admin's call; shown read-only.
    region: str = ""


def _nmcli(*args, timeout=8):
    return subprocess.run(
        ["nmcli", *args], capture_output=True, text=True, timeout=timeout, check=False
    )


def _split_terse(line, count):
    """Split an nmcli terse ('-t') line into `count` fields.

    nmcli escapes ':' and '\\' inside values, so we split on unescaped ':'
    only, then unescape. The last requested field absorbs any trailing
    unescaped ':' (there shouldn't be any given the field order)."""
    fields, buf, i = [], [], 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            buf.append(line[i + 1])
            i += 2
        elif c == ":" and len(fields) < count - 1:
            fields.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(c)
            i += 1
    fields.append("".join(buf))
    while len(fields) < count:
        fields.append("")
    return fields


_MAC = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")


def normalize_mac(text):
    """`text` as `AA:BB:CC:DD:EE:FF`, or None when it isn't a usable address.

    Takes what phones and laptops show: colons, dashes (Windows) or bare hex, in
    either case. A multicast or all-zero address can't be a Wi-Fi card's own."""
    hexes = re.sub(r"[\s:.-]", "", text or "").upper()
    if len(hexes) != 12:
        return None
    mac = ":".join(hexes[i : i + 2] for i in range(0, 12, 2))
    if not _MAC.match(mac) or int(hexes[:2], 16) & 1 or hexes == "0" * 12:
        return None
    return mac


def subnets_overlap(a, b):
    """Whether two `addr/prefix` strings sit on overlapping subnets."""
    if not a or not b:
        return False
    try:
        net_a = ipaddress.IPv4Interface(a).network
        net_b = ipaddress.IPv4Interface(b).network
    except ValueError:
        return False
    return net_a.overlaps(net_b)


def _read_mac(device):
    if not device:
        return ""
    try:
        with open(f"/sys/class/net/{device}/address", encoding="ascii") as f:
            return f.read().strip().upper()
    except OSError:
        return ""


def _active_wifi_uuid():
    """UUID of the active Wi-Fi profile, '' when none. By UUID, never by name: a
    profile is named after its SSID, which is anyone's text off the air."""
    r = _nmcli("-t", "-f", "UUID,TYPE", "con", "show", "--active")
    for line in r.stdout.splitlines():
        uuid, typ = _split_terse(line, 2)
        if typ == "802-11-wireless":
            return uuid
    return ""


def _cloned_mac(uuid):
    """The address a profile borrows, '' when it uses the Pi's own (or a policy
    such as `random`, which Splouch never sets)."""
    if not uuid:
        return ""
    r = _nmcli("-g", "802-11-wireless.cloned-mac-address", "con", "show", uuid)
    return normalize_mac(r.stdout.strip().replace("\\", "")) or ""


@router.get(
    "/wifi_status", response_model=WifiStatus, dependencies=[Depends(require_login)]
)
def route_wifi_status():
    try:
        r = _nmcli("radio", "wifi")
        enabled = r.returncode == 0 and "enabled" in r.stdout.lower()

        r2 = _nmcli("-t", "-f", "DEVICE,TYPE", "--escape", "no", "device")
        wifi_dev = eth_dev = ""
        for line in r2.stdout.splitlines():
            parts = line.split(":")
            if len(parts) < 2:
                continue
            dev, typ = parts[0], parts[1]
            if typ == "wifi" and not wifi_dev:
                wifi_dev = dev
            elif typ == "ethernet" and not eth_dev:
                eth_dev = dev

        def get_cidr(device):
            if not device:
                return ""
            r = _nmcli(
                "-t", "-f", "IP4.ADDRESS", "--escape", "no", "dev", "show", device
            )
            for line in r.stdout.splitlines():
                if line.startswith("IP4.ADDRESS"):
                    return line.split(":")[-1]
            return ""

        def get_field(device, field):
            if not device:
                return ""
            r = _nmcli("-t", "-f", field, "--escape", "no", "dev", "show", device)
            for line in r.stdout.splitlines():
                key, _, value = line.partition(":")
                if key.startswith(field) and value and value != "--":
                    return value
            return ""

        # IN-USE is the correct field for dev wifi (not ACTIVE); active AP is marked with '*'
        r3 = _nmcli("-t", "-f", "IN-USE,SSID", "--escape", "no", "dev", "wifi")
        ssid = ""
        for line in r3.stdout.splitlines():
            if line.startswith("*:"):
                ssid = line.split(":", 1)[1]
                break

        wifi_cidr = get_cidr(wifi_dev) if enabled else ""
        eth_cidr = get_cidr(eth_dev)
        never = _nmcli("-g", "ipv4.never-default", "con", "show", ETH_CON)
        return {
            "enabled": enabled,
            "ssid": ssid,
            "wifi_ip": wifi_cidr.split("/")[0],
            "eth_ip": eth_cidr.split("/")[0],
            "eth_gateway": get_field(eth_dev, "IP4.GATEWAY"),
            "eth_dns": get_field(eth_dev, "IP4.DNS"),
            "eth_internet": never.stdout.strip() != "yes",
            "wifi_mac": _read_mac(wifi_dev),
            "wifi_mac_cloned": _cloned_mac(_active_wifi_uuid()),
            "overlap": subnets_overlap(eth_cidr, wifi_cidr),
        }
    except FileNotFoundError:
        return JSONResponse({"error": "nmcli not found"}, status_code=503)
    except Exception:
        return JSONResponse(
            {"error": failure("Reading the network status")}, status_code=500
        )


@router.get("/wifi_scan", dependencies=[Depends(require_login)])
def route_wifi_scan(request: Request):
    # Returns an HTML fragment (HTMX hx-get) for the Network tab's list.
    t = state.settings_strings(state.ui_locale(request))
    try:
        # `dev wifi list` returns the *cached* scan immediately — right after
        # connecting that cache often holds only the associated AP, which is
        # why the list showed a single network. Trigger an explicit rescan and
        # give the driver a moment to report neighbours before listing.
        # rescan needs root: unprivileged callers get a polkit "not authorized"
        # error and the cache is never refreshed, so run it through sudo like
        # the other privileged nmcli calls here. It also exits non-zero if a
        # scan is already in progress — both cases are harmless, so ignore.
        subprocess.run(
            ["sudo", "nmcli", "dev", "wifi", "rescan"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        time.sleep(4)
        # Terse (-t) output is one AP per line with ':'-separated fields, far
        # more robust than parsing the fixed-width table (whose column offsets
        # shift with content and silently drop most rows). SSID is requested
        # last because it can contain ':' — nmcli escapes those as '\:', which
        # _split_terse() honours so the SSID stays intact.
        r = _nmcli(
            "-t", "-f", "IN-USE,SIGNAL,SECURITY,SSID", "dev", "wifi", "list", timeout=20
        )
        # A single SSID can appear several times (one row per BSSID/band). Merge
        # them by SSID so each network shows once, keeping the strongest signal
        # and marking it active if *any* of its BSSIDs is the in-use one — the
        # connected AP is often not the strongest row, so dropping duplicates
        # naively would lose the '*' and hide the "Connected" state.
        by_ssid = {}
        for line in r.stdout.splitlines():
            if not line:
                continue
            active, signal, security, ssid = _split_terse(line, 4)
            if not ssid:
                continue
            sig = int(signal) if signal.isdigit() else 0
            net = by_ssid.get(ssid)
            if net is None:
                by_ssid[ssid] = {
                    "ssid": ssid,
                    "signal": sig,
                    "security": "" if security in ("--", "") else security,
                    "active": "*" in active,
                }
            else:
                net["signal"] = max(net["signal"], sig)
                net["active"] = net["active"] or ("*" in active)
        networks = sorted(by_ssid.values(), key=lambda n: n["signal"], reverse=True)
        return render(
            request, "settings/fetched/wifi_networks.html", networks=networks, t=t
        )
    except FileNotFoundError:
        return render(
            request,
            "settings/fetched/wifi_networks.html",
            error=t["net_wifi_unavailable"],
            t=t,
        )
    except Exception as e:
        return render(request, "settings/fetched/wifi_networks.html", error=str(e), t=t)


@router.post(
    "/wifi_toggle", response_model=EnabledFlag, dependencies=[Depends(require_login)]
)
def route_wifi_toggle():
    try:
        r = _nmcli("radio", "wifi")
        currently_on = "enabled" in r.stdout.lower()
        st = "off" if currently_on else "on"
        r = subprocess.run(
            ["sudo", "nmcli", "radio", "wifi", st],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if r.returncode != 0:
            return JSONResponse(
                {"error": r.stderr.strip() or "nmcli error"}, status_code=500
            )
        return {"enabled": not currently_on}
    except Exception:
        return JSONResponse({"error": failure("Switching Wi-Fi")}, status_code=500)


@router.get(
    "/cloud_status", response_model=CloudStatus, dependencies=[Depends(require_login)]
)
def route_cloud_status():
    import relay

    return relay.status()


@router.post(
    "/cloud_toggle", response_model=CloudStatus, dependencies=[Depends(require_login)]
)
def route_cloud_toggle():
    import relay

    if relay.status()["running"]:
        relay.stop()
    else:
        relay.start()
    return relay.status()


@router.post(
    "/wifi_connect", response_model=ActionResult, dependencies=[Depends(require_login)]
)
async def route_wifi_connect(body: WifiConnect):
    # nmcli connect can take up to 30 s — run it off the event loop so live
    # scoreboard broadcasts keep flowing while it works.
    return await run_in_threadpool(_wifi_connect, body.ssid, body.password)


def _wifi_connect(ssid, password):
    if not ssid:
        return JSONResponse({"error": "No SSID provided"}, status_code=400)
    try:
        # Remove any stale connection profile for this SSID first. nmcli
        # otherwise reuses an existing profile (e.g. from a previous attempt
        # on an open network) that lacks 802-11-wireless-security.key-mgmt,
        # which then fails validation once a password is supplied.
        # `--` first: an SSID is attacker-chosen text off the air, and one
        # beginning with `-` would otherwise be parsed as an nmcli option rather
        # than as the name of a connection.
        subprocess.run(
            ["sudo", "nmcli", "connection", "delete", "--", ssid],
            capture_output=True,
            timeout=8,
            check=False,
        )

        # The password goes in on stdin, answering `--ask`'s prompt — never as an
        # argument, where every process on the Pi can read it from `ps` for as long
        # as nmcli runs. An open network asks for nothing and the input is unread.
        r = subprocess.run(
            ["nmcli", "--ask", "dev", "wifi", "connect", "--", ssid],
            input=password + "\n" if password else "",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if r.returncode == 0:
            return {"ok": True}
        return JSONResponse({"error": (r.stderr or r.stdout).strip()}, status_code=400)
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Timed out"}, status_code=500)
    except Exception:
        return JSONResponse({"error": failure("Connecting")}, status_code=500)


# eth0's addressing lives in one NetworkManager profile, `splouch-eth`, shared
# with install.sh. It is created here when missing (an install that predates
# it), outranking the default wired profile so it wins at boot.
ETH_CON = "splouch-eth"


def _eth_apply(*props):
    try:
        if _nmcli("-t", "con", "show", ETH_CON).returncode == 0:
            cmd = ["sudo", "nmcli", "con", "mod", ETH_CON]
        else:
            cmd = ["sudo", "nmcli", "con", "add", "type", "ethernet", "ifname", "eth0"]
            cmd += ["con-name", ETH_CON]
            cmd += ["connection.autoconnect", "yes"]
            cmd += ["connection.autoconnect-priority", "100"]
        r = subprocess.run(
            [*cmd, *props], capture_output=True, text=True, timeout=8, check=False
        )
        if r.returncode != 0:
            return {"ok": False, "error": r.stderr.strip() or "nmcli error"}
        subprocess.run(
            ["sudo", "nmcli", "con", "up", ETH_CON],
            capture_output=True,
            timeout=8,
            check=False,
        )
        return {"ok": True}
    except Exception:
        return {"ok": False, "error": failure("Applying the Ethernet settings")}


def _eth_route_props(internet):
    # Without internet on eth0 (a closed console network), eth0 never takes the
    # default route nor hands out DNS, so both stay with Wi-Fi.
    flag = "no" if internet else "yes"
    return ("ipv4.never-default", flag, "ipv4.ignore-auto-dns", flag)


@router.post(
    "/eth_dhcp_set", response_model=ActionResult, dependencies=[Depends(require_login)]
)
async def route_eth_dhcp_set(body: EthDhcp | None = None):
    # No body: as before, eth0 carries the internet.
    internet = body.internet if body else True
    return await run_in_threadpool(
        _eth_apply,
        "ipv4.method",
        "auto",
        "ipv4.addresses",
        "",
        "ipv4.gateway",
        "",
        "ipv4.dns",
        "",
        *_eth_route_props(internet),
    )


@router.post(
    "/eth_ip_set", response_model=ActionResult, dependencies=[Depends(require_login)]
)
async def route_eth_ip_set(body: EthIP):
    # Validated by the EthIP model. Method too: after DHCP the profile is `auto`,
    # where an address would only be added beside the leased one.
    gateway = str(body.gateway) if body.gateway else ""
    dns = str(body.dns or body.gateway) if body.internet else ""
    return await run_in_threadpool(
        _eth_apply,
        "ipv4.method",
        "manual",
        "ipv4.addresses",
        f"{body.ip}/{body.prefix}",
        "ipv4.gateway",
        gateway,
        "ipv4.dns",
        dns,
        *_eth_route_props(body.internet),
    )


# ── Captive portal ────────────────────────────────────────────────────────────
# A venue Wi-Fi with a sign-in page lets nothing through until a browser accepts
# it, and it accepts the *device* that did, by its MAC address. The Pi has no
# browser and Settings runs on another device, so: detect the portal, have the
# operator accept it on a phone or laptop, then let the Pi borrow that device's
# Wi-Fi MAC address (the profile's cloned-mac-address) — the portal then sees the
# device it already let in.


def _default_via():
    """The port the default route takes: 'ethernet', 'wifi' or ''."""
    r = _run(["ip", "-j", "route", "show", "default"], timeout=3)
    try:
        routes = json.loads(r.stdout or "[]") if r is not None else []
    except ValueError:
        routes = []
    routes.sort(key=lambda x: x.get("metric", 0))
    for route in routes:
        dev = route.get("dev", "")
        if dev.startswith(("eth", "en")):
            return "ethernet"
        if dev.startswith(("wlan", "wl")):
            return "wifi"
    return ""


def _get(scheme, host, port, path, timeout):
    """(status, Location, body) of one request that follows no redirect."""
    cls = (
        http.client.HTTPSConnection if scheme == "https" else http.client.HTTPConnection
    )
    conn = cls(host, port, timeout=timeout)
    try:
        conn.request("GET", path, headers={"User-Agent": "Splouch connectivity"})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Location") or "", resp.read(512)
    finally:
        conn.close()


def classify(cloud_url, get=_get, timeout=5):
    """{'state', 'portal_url'} from asking the Cloud's /ping, which says `ok`.

    The real request goes the way the relay's does (HTTPS unless the Cloud is set
    up on plain HTTP). When it fails, a plain-HTTP one tells a portal from a dead
    line: a portal answers it itself, with its sign-in page or a redirect to it,
    while the Cloud only ever redirects to its own HTTPS address."""
    url = cloud_url.strip().rstrip("/")
    for ws, web in (("wss://", "https://"), ("ws://", "http://")):
        if url.startswith(ws):
            url = web + url[len(ws) :]
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        return {"state": "unknown", "portal_url": ""}
    try:
        status, _, body = get(parts.scheme, host, parts.port, "/ping", timeout)
        if status == 200 and body.strip() == b"ok":
            return {"state": "full", "portal_url": ""}
    except (OSError, http.client.HTTPException):
        pass
    try:
        status, location, body = get("http", host, None, "/ping", timeout)
    except (OSError, http.client.HTTPException):
        # Nothing answers, not even a portal — but there is a route out.
        return {"state": "limited", "portal_url": ""}
    if 300 <= status < 400:
        if urlsplit(location).hostname in (host, None):
            return {"state": "limited", "portal_url": ""}
        return {"state": "portal", "portal_url": location}
    if status == 200 and body.strip() == b"ok":
        return {"state": "limited", "portal_url": ""}
    return {"state": "portal", "portal_url": ""}


def _nm_connectivity():
    # NetworkManager's own check, when the Cloud isn't set up. It is off unless a
    # connectivity URI is configured, and then says 'unknown'.
    r = _run(["nmcli", "networking", "connectivity", "check"], timeout=10)
    value = r.stdout.strip() if r is not None else ""
    return value if value in ("full", "portal", "limited", "none") else "unknown"


def connectivity():
    via = _default_via()
    if not via:
        return {"state": "none", "via": "", "portal_url": ""}
    cloud = state.settings.get("cloud_relay_url", "")
    if cloud.strip():
        result = classify(cloud)
    else:
        result = {"state": _nm_connectivity(), "portal_url": ""}
    return {**result, "via": via}


@router.get(
    "/net_connectivity",
    response_model=Connectivity,
    dependencies=[Depends(require_login)],
)
async def route_net_connectivity(request: Request):
    # Up to a few seconds of requests: off the event loop, and only on demand.
    return await run_in_threadpool(connectivity)


@router.post(
    "/wifi_mac_set", response_model=ActionResult, dependencies=[Depends(require_login)]
)
async def route_wifi_mac_set(body: WifiMac):
    # Reconnecting can take a while: off the event loop, like /wifi_connect.
    return await run_in_threadpool(_wifi_mac_set, body.mac)


def _wifi_mac_set(text):
    mac = ""
    if text.strip():
        mac = normalize_mac(text)
        if mac is None:
            return JSONResponse({"error": "Not a MAC address"}, status_code=400)
    try:
        uuid = _active_wifi_uuid()
        if not uuid:
            return JSONResponse({"error": "Not connected to Wi-Fi"}, status_code=400)
        # Unprivileged, like /wifi_connect, which created this profile.
        r = _nmcli("con", "mod", uuid, "802-11-wireless.cloned-mac-address", mac or "")
        if r.returncode != 0:
            return JSONResponse(
                {"error": (r.stderr or r.stdout).strip() or "nmcli error"},
                status_code=500,
            )
        # The new address only takes effect on a fresh association.
        r = _nmcli("con", "up", uuid, timeout=30)
        if r.returncode != 0:
            return JSONResponse(
                {"error": (r.stderr or r.stdout).strip() or "nmcli error"},
                status_code=500,
            )
        return {"ok": True}
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Timed out"}, status_code=500)
    except Exception:
        return JSONResponse(
            {"error": failure("Changing the Wi-Fi address")}, status_code=500
        )


@router.get("/clients_fragment", dependencies=[Depends(require_login)])
async def route_clients_fragment(request: Request):
    # Browser tabs connected to the scoreboard WebSocket, shown (HTMX hx-get) in
    # the Network tab. async so it runs on the event loop — the same context that
    # mutates _scoreboard_clients (the WS connect/disconnect handlers) — so this
    # snapshot can't be preempted mid-iteration by a connect/disconnect.
    return render(
        request,
        "settings/fetched/clients.html",
        clients=list(state._scoreboard_clients.values()),
        server_version=state.git_describe()["version"],
        t=state.settings_strings(state.ui_locale(request)),
    )


# ── Discovery (mDNS) ──────────────────────────────────────────────────────────
# What the phone apps and browsers rely on to find this Pi without an address:
# the `_splouch._tcp` service they browse for, and the `.local` names typed by
# hand. Checked from the Pi's side with the same avahi tools the installer sets
# up — it proves the Pi answers, not that a given phone can hear it, which is why
# the card also lists what usually sits in between (see the template).

SERVICE_TYPE = "_splouch._tcp"
# install.sh MDNS_ALIASES, and mdns-aliases.sh's own fallback. Read off the
# installed unit when there is one, so a Pi set up with other names checks those.
_DEFAULT_ALIASES = ("tableau.local", "marcador.local")
_ALIASES_UNIT = "/etc/systemd/system/splouch-mdns-aliases.service"


def _run(cmd, timeout=6):
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _active(unit):
    r = _run(["systemctl", "is-active", unit], timeout=3)
    return r is not None and r.stdout.strip() == "active"


def mdns_aliases(unit_path=_ALIASES_UNIT):
    try:
        with open(unit_path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("ExecStart="):
                    names = line.split()[1:]
                    return tuple(names) or _DEFAULT_ALIASES
    except OSError:
        pass
    return _DEFAULT_ALIASES


def own_addresses():
    r = _run(["hostname", "-I"], timeout=3)
    return set(r.stdout.split()) if r is not None else set()


def _unescape(value):
    """avahi-browse -p escapes a byte as `\\DDD` (decimal) and itself as `\\\\`."""
    return re.sub(
        r"\\(\d{3}|.)", lambda m: chr(int(m[1])) if m[1].isdigit() else m[1], value
    )


def parse_browse(output):
    """Resolved rows of `avahi-browse -rtp` as `{name, host, address, port}`.

    `=;eth0;IPv4;Splouch;_splouch._tcp;local;splouch.local;192.168.1.50;5000;"…"`.
    The same service shows once per interface and protocol; one row per address.
    """
    seen, found = set(), []
    for line in output.splitlines():
        # Split on ';' not preceded by a backslash: an escaped one is part of a name.
        fields = re.split(r"(?<!\\);", line)
        if len(fields) < 9 or fields[0] != "=":
            continue
        name, host, address = _unescape(fields[3]), fields[6], fields[7]
        if (name, address) in seen:
            continue
        seen.add((name, address))
        found.append(
            {"name": name, "host": host, "address": address, "port": fields[8]}
        )
    return found


def resolve(name):
    """The IPv4 address `name` resolves to over mDNS, or '' when nothing answers."""
    r = _run(["avahi-resolve", "-4", "-n", name], timeout=5)
    if r is None or r.returncode != 0:
        return ""
    parts = r.stdout.split()
    return parts[1] if len(parts) >= 2 else ""


def mdns_check():
    """Everything the Discovery card shows, with each problem named by a key."""
    tools = _run(["avahi-browse", "--version"], timeout=3) is not None
    own = own_addresses()
    daemon = _active("avahi-daemon")
    services = []
    if tools and daemon:
        r = _run(["avahi-browse", "-rtp", SERVICE_TYPE], timeout=8)
        services = parse_browse(r.stdout) if r is not None else []
    for svc in services:
        svc["own"] = svc["address"] in own
    host = socket.gethostname().split(".")[0] + ".local"
    names = []
    for name in (host, *mdns_aliases()):
        address = resolve(name) if tools and daemon else ""
        names.append({"name": name, "address": address, "own": address in own})

    problems = []
    if not tools:
        problems.append("no_tools")
    elif not daemon:
        problems.append("no_daemon")
    else:
        if not any(s["own"] for s in services):
            problems.append("not_advertised")
        aliases_up = _active("splouch-mdns-aliases")
        for n in names:
            if not n["address"]:
                alias = n["name"] != host
                key = "no_alias" if alias and not aliases_up else "no_name"
                problems.append((key, n["name"], ""))
            elif not n["own"]:
                problems.append(("name_taken", n["name"], n["address"]))
    return {
        "own": sorted(own),
        "services": services,
        "names": names,
        # (key, name, address): the locale's `net_mdns_p_<key>`, with its blanks.
        "problems": [p if isinstance(p, tuple) else (p, "", "") for p in problems],
    }


@router.get("/mdns_fragment", dependencies=[Depends(require_login)])
async def route_mdns_fragment(request: Request):
    # An HTML fragment (HTMX hx-get) for the Network tab's Discovery card. A few
    # seconds of avahi lookups, so it runs off the event loop and only on demand.
    result = await run_in_threadpool(mdns_check)
    return render(
        request,
        "settings/fetched/mdns.html",
        t=state.settings_strings(state.ui_locale(request)),
        **result,
    )
