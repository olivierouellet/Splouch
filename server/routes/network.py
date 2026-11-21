import base64
import http.client
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import threading
import time
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator
from starlette.concurrency import run_in_threadpool

import paths
import state
from web import ActionResult, EnabledFlag, failure, render, require_login

router = APIRouter(tags=["Network"])


_DOMAIN = re.compile(
    r"^(?=.{1,253}$)([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$"
)


# The wired ports Settings manages: the Pi's own, and a USB adapter (Raspberry Pi
# OS names it eth1), for a console network kept apart from the venue's. Fixed
# names, because the sudoers rules name each one's profile (see _eth_con).
Wired = Literal["eth0", "eth1"]
WIRED = ("eth0", "eth1")


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
    iface: Wired = "eth0"

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
    iface: Wired = "eth0"


class ConsoleIface(BaseModel):
    iface: Wired = "eth0"


class WifiCountry(BaseModel):
    country: str


class WifiMac(BaseModel):
    # '' puts the Pi's own address back.
    mac: str = ""


class WifiConnect(BaseModel):
    ssid: str = ""
    password: str = ""
    # WPA2/WPA3-Enterprise (802.1X: eduroam, school and city networks) when
    # `identity` is set. PEAP with MSCHAPv2 inside is what nearly all of them use.
    identity: str = ""
    eap: Literal["peap", "ttls"] = "peap"
    phase2: Literal["mschapv2", "pap"] = "mschapv2"
    anonymous_identity: str = ""
    # The RADIUS server's domain. Set, the Pi checks the server's certificate
    # against the system CAs and that domain; blank, it checks nothing.
    domain: str = ""
    # A network that doesn't broadcast its name: scanning can't find it or tell its
    # security, so the profile is made from what the operator says (a password: WPA
    # Personal; none: open; a username: Enterprise).
    hidden: bool = False

    @model_validator(mode="after")
    def _plain_text(self):
        # nmcli takes these as one line each (the password through `con edit`):
        # a line break would end the value and start another command.
        for name in ("ssid", "password", "identity", "anonymous_identity", "domain"):
            if re.search(r"[\x00-\x1f\x7f]", getattr(self, name)):
                raise ValueError(f"{name} can't contain control characters")
        if self.domain and not _DOMAIN.match(self.domain):
            raise ValueError(f"Not a domain name: {self.domain}")
        return self


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
    # Every wired port present, eth0 first: {iface, ip (addr/prefix), gateway,
    # dns, internet}. The eth_* fields above are eth0's, as before.
    wired: list[dict] = []
    # The wired port the console listener uses (the `console_iface` setting).
    console_iface: str = "eth0"
    # The Wi-Fi regulatory country (ISO code), '' while unset: then the radio stays
    # on the world-wide minimum, and some 5 GHz networks never show up.
    wifi_country: str = ""
    # The console's address, as its last UDP datagram showed it, and how long ago
    # (seconds). `console_subnet` is False when it's outside its port's subnet.
    console_from: str = ""
    console_age: int = 0
    console_subnet: bool = True


class Connectivity(BaseModel):
    # full: the Cloud answers. portal: a sign-in page is in the way (captive
    # portal). clock: the Cloud is there, but the Pi's date is wrong, so its
    # certificate fails. limited: a network, but the Cloud doesn't answer. none: no
    # network out. unknown: no Cloud address to try and NetworkManager doesn't know.
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
    # What the network watcher found while the relay can't reach the Cloud (see
    # NetWatch), None while there's nothing to report.
    network: Connectivity | None = None


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


def any_overlap(cidrs):
    """Whether any two of these `addr/prefix` strings share a subnet."""
    present = [c for c in cidrs if c]
    return any(
        subnets_overlap(a, b) for i, a in enumerate(present) for b in present[i + 1 :]
    )


def console_iface():
    iface = state.settings.get("console_iface") or "eth0"
    return iface if iface in WIRED else "eth0"


def console_source(source, eth_cidr, now):
    """The WifiStatus fields for where the console's UDP comes from."""
    address = (source or {}).get("address", "")
    if not address:
        return {}
    inside = True
    if eth_cidr:
        try:
            net = ipaddress.IPv4Interface(eth_cidr).network
            inside = ipaddress.IPv4Address(address) in net
        except ValueError:
            pass
    return {
        "console_from": address,
        "console_age": max(0, int(now - source.get("at", now))),
        "console_subnet": inside,
    }


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


def _profile_uuid(ssid):
    """UUID of the saved Wi-Fi profile named `ssid`, '' when there is none."""
    r = _nmcli("-t", "-f", "UUID,TYPE,NAME", "con", "show")
    for line in r.stdout.splitlines():
        uuid, typ, name = _split_terse(line, 3)
        if typ == "802-11-wireless" and name == ssid:
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
        wifi_dev = ""
        wired_devs = []
        for line in r2.stdout.splitlines():
            parts = line.split(":")
            if len(parts) < 2:
                continue
            dev, typ = parts[0], parts[1]
            if typ == "wifi" and not wifi_dev:
                wifi_dev = dev
            elif typ == "ethernet" and dev in WIRED:
                wired_devs.append(dev)
        wired_devs.sort()

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
        wired = []
        for dev in wired_devs:
            never = _nmcli("-g", "ipv4.never-default", "con", "show", _eth_con(dev))
            wired.append(
                {
                    "iface": dev,
                    "ip": get_cidr(dev),
                    "gateway": get_field(dev, "IP4.GATEWAY"),
                    "dns": get_field(dev, "IP4.DNS"),
                    "internet": never.stdout.strip() != "yes",
                }
            )
        by_iface = {w["iface"]: w for w in wired}
        eth0 = by_iface.get("eth0", {})
        console = console_iface()
        cidrs = [wifi_cidr, *(w["ip"] for w in wired)]
        return {
            "enabled": enabled,
            "ssid": ssid,
            "wifi_ip": wifi_cidr.split("/")[0],
            "eth_ip": eth0.get("ip", "").split("/")[0],
            "eth_gateway": eth0.get("gateway", ""),
            "eth_dns": eth0.get("dns", ""),
            "eth_internet": eth0.get("internet", True),
            "wifi_mac": _read_mac(wifi_dev),
            "wifi_mac_cloned": _cloned_mac(_active_wifi_uuid()),
            "overlap": any_overlap(cidrs),
            "wired": wired,
            "console_iface": console,
            "wifi_country": wifi_country(),
            **console_source(
                state._udp_source, by_iface.get(console, {}).get("ip", ""), time.time()
            ),
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

    return {**relay.status(), "network": WATCH.report()}


@router.post(
    "/cloud_toggle", response_model=CloudStatus, dependencies=[Depends(require_login)]
)
def route_cloud_toggle():
    import relay

    if relay.status()["running"]:
        relay.stop()
    else:
        relay.start()
    return {**relay.status(), "network": WATCH.report()}


@router.post(
    "/wifi_connect", response_model=ActionResult, dependencies=[Depends(require_login)]
)
async def route_wifi_connect(body: WifiConnect):
    # nmcli connect can take up to 30 s — run it off the event loop so live
    # scoreboard broadcasts keep flowing while it works.
    if body.identity or body.hidden:
        return await run_in_threadpool(_wifi_connect_profile, body)
    return await run_in_threadpool(_wifi_connect, body.ssid, body.password)


# NetworkManager gives up on a profile after 4 failed attempts and waits 5 minutes
# before trying again. On a pool deck everything powers up at once and the venue
# router is often the slowest to boot, so the Pi used to sit offline for those 5
# minutes. 0: keep trying.
KEEP_TRYING = ("connection.autoconnect-retries", "0")


def keep_trying_profiles():
    """Set KEEP_TRYING on profiles made before it existed (or by Raspberry Pi
    Imager): every saved Wi-Fi profile, and `splouch-eth`. Once, at startup."""
    try:
        r = _nmcli("-t", "-f", "UUID,TYPE,NAME", "con", "show")
    except (OSError, subprocess.SubprocessError):
        return  # no NetworkManager here (a dev machine)
    for line in r.stdout.splitlines():
        uuid, typ, name = _split_terse(line, 3)
        retries = _nmcli("-g", KEEP_TRYING[0], "con", "show", uuid).stdout.strip()
        if retries == KEEP_TRYING[1]:
            continue
        if typ == "802-11-wireless":
            _nmcli("con", "mod", uuid, *KEEP_TRYING)
        elif name in {_eth_con(i) for i in WIRED}:
            # Root's: the sudoers rules allow `con mod splouch-eth[1] *`.
            subprocess.run(
                ["sudo", "nmcli", "con", "mod", name, *KEEP_TRYING],
                capture_output=True,
                timeout=8,
                check=False,
            )


def _replace_profile(ssid):
    """Delete any saved profile for `ssid`; returns the MAC it was borrowing.

    nmcli otherwise reuses an existing profile (e.g. from a previous attempt on an
    open network) that lacks 802-11-wireless-security.key-mgmt, which then fails
    validation once a password is supplied. A MAC address borrowed for a captive
    portal (see /wifi_mac_set) belongs to the network, so it is read first and
    survives the profile being made again."""
    borrowed = _cloned_mac(_profile_uuid(ssid))
    # `--` first: an SSID is attacker-chosen text off the air, and one beginning
    # with `-` would otherwise be parsed as an nmcli option rather than as the name
    # of a connection.
    subprocess.run(
        ["sudo", "nmcli", "connection", "delete", "--", ssid],
        capture_output=True,
        timeout=8,
        check=False,
    )
    return borrowed


def _wifi_connect(ssid, password):
    if not ssid:
        return JSONResponse({"error": "No SSID provided"}, status_code=400)
    try:
        borrowed = _replace_profile(ssid)

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
        if r.returncode != 0:
            return JSONResponse(
                {"error": (r.stderr or r.stdout).strip()}, status_code=400
            )
        uuid = _active_wifi_uuid()
        if uuid:
            _nmcli("con", "mod", uuid, *KEEP_TRYING)
        if borrowed:
            return _wifi_mac_set(borrowed)
        return {"ok": True}
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Timed out"}, status_code=500)
    except Exception:
        return JSONResponse({"error": failure("Connecting")}, status_code=500)


def _wifi_connect_profile(body):
    """Join a network `dev wifi connect` can't: WPA-Enterprise, or a hidden one.

    A profile with no secret in it, the secret set through `nmcli con edit` on
    stdin, then brought up. The secret must never be an argument, where every
    process on the Pi can read it from `ps`, and it must be saved, or the Pi can't
    rejoin after a reboot with nobody there to type it."""
    if not body.ssid:
        return JSONResponse({"error": "No SSID provided"}, status_code=400)
    try:
        borrowed = _replace_profile(body.ssid)
        props = [
            *("connection.id", body.ssid, "802-11-wireless.ssid", body.ssid),
            # Off until the secret is in, so it can't start without one.
            *("connection.autoconnect", "no"),
            *KEEP_TRYING,
        ]
        if body.hidden:
            props += ["802-11-wireless.hidden", "yes"]
        secret = ""
        if body.identity:
            secret = "802-1x.password"
            props += [
                *("802-11-wireless-security.key-mgmt", "wpa-eap"),
                *("802-1x.eap", body.eap, "802-1x.phase2-auth", body.phase2),
                *("802-1x.identity", body.identity),
            ]
        elif body.password:
            secret = "802-11-wireless-security.psk"
            props += ["802-11-wireless-security.key-mgmt", "wpa-psk"]
        if body.identity and body.anonymous_identity:
            props += ["802-1x.anonymous-identity", body.anonymous_identity]
        if body.identity and body.domain:
            props += ["802-1x.system-ca-certs", "yes"]
            props += ["802-1x.domain-suffix-match", body.domain]
        if borrowed:
            props += ["802-11-wireless.cloned-mac-address", borrowed]
        r = _nmcli("con", "add", "type", "wifi", *props)
        uuid = re.search(r"\(([0-9a-f-]{36})\)", r.stdout)
        if r.returncode != 0 or not uuid:
            return JSONResponse(
                {"error": (r.stderr or r.stdout).strip() or "nmcli error"},
                status_code=400,
            )
        uuid = uuid[1]
        if secret:
            r = subprocess.run(
                ["nmcli", "con", "edit", "uuid", uuid],
                input=f"set {secret} {body.password}\nsave persistent\nquit\n",
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if r.returncode != 0:
                _nmcli("con", "delete", uuid)
                return JSONResponse(
                    {"error": (r.stderr or r.stdout).strip() or "nmcli error"},
                    status_code=500,
                )
        _nmcli("con", "mod", uuid, "connection.autoconnect", "yes")
        # EAP takes longer than a PSK (a round trip to the venue's RADIUS server),
        # and a hidden network has to be probed for by name.
        r = _nmcli("con", "up", uuid, timeout=45)
        if r.returncode != 0:
            # A wrong password would otherwise keep retrying in the background.
            _nmcli("con", "delete", uuid)
            return JSONResponse(
                {"error": (r.stderr or r.stdout).strip() or "nmcli error"},
                status_code=400,
            )
        return {"ok": True}
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "Timed out"}, status_code=500)
    except Exception:
        return JSONResponse({"error": failure("Connecting")}, status_code=500)


# eth0's addressing lives in one NetworkManager profile, `splouch-eth`, shared
# with install.sh. It is created here when missing (an install that predates
# it), outranking the default wired profile so it wins at boot.
ETH_CON = "splouch-eth"


def _eth_con(iface):
    """The NetworkManager profile for a wired port: `splouch-eth` for eth0 (as
    install.sh names it), `splouch-eth1` for a USB adapter."""
    return ETH_CON if iface == "eth0" else f"splouch-{iface}"


def _eth_apply(iface, *props):
    con = _eth_con(iface)
    try:
        if _nmcli("-t", "con", "show", con).returncode == 0:
            cmd = ["sudo", "nmcli", "con", "mod", con]
        else:
            cmd = ["sudo", "nmcli", "con", "add", "type", "ethernet", "ifname", iface]
            cmd += ["con-name", con]
            cmd += ["connection.autoconnect", "yes"]
            cmd += ["connection.autoconnect-priority", "100"]
            cmd += [*KEEP_TRYING]
        r = subprocess.run(
            [*cmd, *props], capture_output=True, text=True, timeout=8, check=False
        )
        if r.returncode != 0:
            return {"ok": False, "error": r.stderr.strip() or "nmcli error"}
        subprocess.run(
            ["sudo", "nmcli", "con", "up", con],
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
    body = body or EthDhcp()
    return await run_in_threadpool(_eth_dhcp, body.internet, body.iface)


def _eth_dhcp(internet, iface="eth0"):
    props = (
        *("ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", ""),
        *("ipv4.dns", ""),
        *_eth_route_props(internet),
    )
    # A closed console network seldom has a DHCP server, and without an address
    # the UDP listener can't open. `fallback` gives eth0 a link-local 169.254.x.x
    # address when no lease comes, enough to hear a console broadcasting to
    # 255.255.255.255 (and to show where it sends from). NetworkManager after 1.48
    # only: an older one refuses the value, so try again without it.
    result = _eth_apply(iface, *props, "ipv4.link-local", "fallback")
    if not result["ok"] and "link-local" in result.get("error", ""):
        result = _eth_apply(iface, *props)
    return result


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
        body.iface,
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


@router.post(
    "/console_iface_set",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
def route_console_iface_set(body: ConsoleIface):
    # The UDP listener checks every 5 s and rebinds on the new port by itself.
    state.settings["console_iface"] = body.iface
    state.save_settings()
    return {"ok": True}


# ── Wi-Fi country ─────────────────────────────────────────────────────────────
# Which channels and transmit power the radio may use. Unset, it keeps to the
# world-wide minimum, where some 5 GHz networks are never seen. Set through a
# root-owned script that checks the code (install/scripts/wifi-country.sh): the
# value ends up in the kernel command line.

ISO_TABLE = "/usr/share/zoneinfo/iso3166.tab"


def wifi_countries(path=ISO_TABLE):
    """Every ISO 3166 code tzdata lists, sorted; the page draws the names."""
    try:
        with open(path, encoding="utf-8") as f:
            codes = {ln.split("\t", 1)[0] for ln in f if ln[:1].isalpha()}
    except OSError:
        return []
    return sorted(c for c in codes if re.fullmatch(r"[A-Z]{2}", c))


def parse_reg(output):
    """The global country from `iw reg get`, '' when unset (`00`)."""
    for line in output.splitlines():
        m = re.match(r"country ([A-Z0-9]{2}):", line.strip())
        if m:
            return "" if m[1] == "00" else m[1]
    return ""


def wifi_country():
    r = _run(["iw", "reg", "get"], timeout=3)
    return parse_reg(r.stdout) if r is not None else ""


@router.post(
    "/wifi_country_set",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
async def route_wifi_country_set(body: WifiCountry):
    return await run_in_threadpool(_wifi_country_set, body.country)


def _wifi_country_set(country):
    if country not in wifi_countries():
        return JSONResponse({"error": "Not a country code"}, status_code=400)
    script = paths.privileged_script("wifi-country.sh")
    r = _run(["sudo", "-n", script, country], timeout=30)
    if r is None or r.returncode != 0:
        err = (r.stderr or r.stdout).strip() if r is not None else ""
        return JSONResponse({"error": err or "wifi-country.sh failed"}, status_code=500)
    return {"ok": True}


class CloudProxy(BaseModel):
    proxy: str = ""


@router.post(
    "/cloud_proxy_set",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
def route_cloud_proxy_set(body: CloudProxy):
    # An HTTP proxy for the Cloud relay, for venues that let nothing out without
    # one. The relay reads it on its next connection attempt.
    import relay

    try:
        relay.proxy_parts(body.proxy)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    state.settings["cloud_proxy"] = body.proxy.strip()
    state.save_settings()
    return {"ok": True}


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


def _get(scheme, host, port, path, timeout, proxy=None):
    """(status, Location, body) of one request that follows no redirect.

    Through `proxy` ((host, port, auth), see relay.proxy_parts) when given, the way
    the relay goes: a CONNECT tunnel for HTTPS, an absolute URL for HTTP."""
    headers = {"User-Agent": "Splouch connectivity"}
    target = path
    if proxy is None:
        cls = (
            http.client.HTTPSConnection
            if scheme == "https"
            else http.client.HTTPConnection
        )
        conn = cls(host, port, timeout=timeout)
    else:
        p_host, p_port, auth = proxy
        auth_header = {}
        if auth:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
            auth_header = {"Proxy-Authorization": f"Basic {token}"}
        if scheme == "https":
            conn = http.client.HTTPSConnection(p_host, p_port, timeout=timeout)
            conn.set_tunnel(host, port or 443, headers=auth_header)
        else:
            conn = http.client.HTTPConnection(p_host, p_port, timeout=timeout)
            target = f"http://{host}{':' + str(port) if port else ''}{path}"
            headers.update(auth_header)
    try:
        conn.request("GET", target, headers=headers)
        resp = conn.getresponse()
        return resp.status, resp.getheader("Location") or "", resp.read(512)
    finally:
        conn.close()


# OpenSSL's X509_V_ERR_CERT_NOT_YET_VALID and X509_V_ERR_CERT_HAS_EXPIRED.
CERT_DATE_ERRORS = (9, 10)
# Older than any Splouch release: a date before it is wrong, whatever else is.
MIN_YEAR = 2026


def classify(cloud_url, get=_get, timeout=5, now=time.time):
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
    # A Pi with no clock battery boots on a stale date, and venue firewalls often
    # block network time: every certificate then looks expired or not yet valid.
    clock = time.gmtime(now()).tm_year < MIN_YEAR
    try:
        status, _, body = get(parts.scheme, host, parts.port, "/ping", timeout)
        if status == 200 and body.strip() == b"ok":
            return {"state": "full", "portal_url": ""}
    except ssl.SSLCertVerificationError as e:
        clock = clock or e.verify_code in CERT_DATE_ERRORS
    except (OSError, http.client.HTTPException):
        pass
    # Not reaching the Cloud with a wrong date: say the date, the thing to fix —
    # unless a portal is in the way, which has to be dealt with first.
    limited = {"state": "clock" if clock else "limited", "portal_url": ""}
    try:
        status, location, body = get("http", host, None, "/ping", timeout)
    except (OSError, http.client.HTTPException):
        # Nothing answers, not even a portal — but there is a route out.
        return limited
    if 300 <= status < 400:
        if urlsplit(location).hostname in (host, None):
            return limited
        return {"state": "portal", "portal_url": location}
    if status == 200 and body.strip() == b"ok":
        return limited
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
    import relay

    cloud = state.settings.get("cloud_relay_url", "")
    proxy = relay._proxy()
    if cloud.strip():
        result = classify(cloud, get=lambda *a: _get(*a, proxy=proxy))
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


# ── Watching the way out ──────────────────────────────────────────────────────
# A portal's sign-in lasts hours, not a meet: when it lapses the Cloud drops and
# nobody is looking at the Network tab. While the relay is meant to be connected
# and isn't, this checks why once a minute; Settings shows a banner from the
# result (it rides on /cloud_status, which the page already polls).


class NetWatch:
    TICK = 10  # s between looks at the relay
    GRACE = 30  # s the relay may take to (re)connect before it counts as down
    EVERY = 60  # s between probes while it stays down

    def __init__(self, probe):
        self._probe = probe
        self._lock = threading.Lock()
        self._report = None
        self._down_since = None
        self._probed_at = None

    def report(self):
        with self._lock:
            return dict(self._report) if self._report else None

    def tick(self, relay_status, now):
        """One look. Probes (blocking) only when the relay has been down a while."""
        if (
            not (relay_status["url"] and relay_status["running"])
            or relay_status["connected"]
        ):
            with self._lock:
                self._report = None
            self._down_since = self._probed_at = None
            return
        if self._down_since is None:
            self._down_since = now
        if now - self._down_since < self.GRACE:
            return
        if self._probed_at is not None and now - self._probed_at < self.EVERY:
            return
        self._probed_at = now
        result = self._probe()
        # 'full' means the Cloud answers and the relay will catch up: no banner.
        with self._lock:
            self._report = None if result["state"] in ("full", "unknown") else result

    def run(self):
        import relay

        while True:
            time.sleep(self.TICK)
            try:
                self.tick(relay.status(), time.monotonic())
            except Exception as e:
                # Anything: a watcher must outlive any one look.
                print(f"[network] watch failed: {e}", flush=True)


WATCH = NetWatch(lambda: connectivity())
_watch_started = False


def start_watch():
    global _watch_started
    if _watch_started:
        return
    _watch_started = True
    threading.Thread(target=WATCH.run, daemon=True, name="net-watch").start()
    threading.Thread(
        target=keep_trying_profiles, daemon=True, name="net-keep-trying"
    ).start()


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
