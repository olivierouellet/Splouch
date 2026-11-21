"""Settings → Network at a venue: a console-only Ethernet and a Wi-Fi sign-in page.

Two set-ups that used to leave the Pi with either the console or the Cloud, never both:

* The console broadcasts UDP on a closed network plugged into eth0, and the internet
  comes over Wi-Fi. eth0 must then never take the default route, or the Cloud's
  traffic goes into the closed network and stops there.
* The venue Wi-Fi has a captive portal. The Pi has no browser, so the operator
  accepts the portal on a phone or laptop and the Pi borrows that device's MAC.

And what follows from them: a closed network with no DHCP server (eth0 falls back
to link-local, and Settings shows where the console sends from), and a portal
sign-in that lapses mid-meet (a watcher notices the Cloud drop and says why).
"""

import asyncio
import http.client
import json
import os
import ssl
import subprocess
import tempfile
from pathlib import Path

import pytest

from jsc import HAS_JS_ENGINE, js_argv
from routes import network
from test_settings_eth_ip import _shipped

# ── Ethernet without internet ─────────────────────────────────────────────────


@pytest.fixture
def applied(monkeypatch):
    calls = []
    monkeypatch.setattr(network, "_eth_apply", lambda *p: calls.append(p) or {"ok": 1})
    return calls


def _props(call):
    return dict(zip(call[::2], call[1::2], strict=True))


def test_static_without_internet_never_takes_the_default_route(applied):
    body = network.EthIP.model_validate(
        {"ip": "192.168.0.10", "prefix": 24, "gateway": None, "internet": False}
    )
    asyncio.run(network.route_eth_ip_set(body))
    p = _props(applied[0])
    assert p["ipv4.never-default"] == "yes"
    assert p["ipv4.ignore-auto-dns"] == "yes"
    assert p["ipv4.gateway"] == ""
    assert p["ipv4.dns"] == ""


def test_static_with_internet_keeps_the_route_and_dns(applied):
    body = network.EthIP.model_validate(
        {"ip": "192.168.1.50", "prefix": 24, "gateway": "192.168.1.1"}
    )
    asyncio.run(network.route_eth_ip_set(body))
    p = _props(applied[0])
    assert p["ipv4.never-default"] == "no"
    assert p["ipv4.gateway"] == "192.168.1.1"
    assert p["ipv4.dns"] == "192.168.1.1"


@pytest.mark.parametrize(
    ("body", "flag"),
    [(None, "no"), (network.EthDhcp(), "no"), (network.EthDhcp(internet=False), "yes")],
)
def test_dhcp_internet_flag(applied, body, flag):
    # No body is how the page posted it before: eth0 still carries the internet.
    asyncio.run(network.route_eth_dhcp_set(body))
    p = _props(applied[0])
    assert p["ipv4.method"] == "auto"
    assert p["ipv4.never-default"] == flag


@pytest.mark.parametrize(
    ("eth", "wifi", "overlap"),
    [
        ("192.168.1.50/24", "192.168.1.20/24", True),
        ("192.168.0.10/16", "192.168.1.20/24", True),
        ("10.10.10.2/24", "192.168.1.20/24", False),
        ("", "192.168.1.20/24", False),
        ("192.168.1.50/24", "", False),
    ],
)
def test_subnets_overlap(eth, wifi, overlap):
    assert network.subnets_overlap(eth, wifi) is overlap


# ── Captive portal: detection ─────────────────────────────────────────────────


def _fake(answers):
    """A `get` answering each (scheme) with a response tuple, or raising it."""
    seen = []

    def get(scheme, host, port, path, timeout):
        seen.append((scheme, host, port, path))
        a = answers[scheme]
        if isinstance(a, Exception):
            raise a
        return a

    return get, seen


@pytest.mark.parametrize(
    ("answers", "state", "portal_url"),
    [
        ({"https": (200, "", b"ok\n")}, "full", ""),
        # TLS broken by the portal; plain HTTP redirected to its sign-in page.
        (
            {
                "https": OSError("certificate verify failed"),
                "http": (302, "http://portal.venue.net/login?x=1", b""),
            },
            "portal",
            "http://portal.venue.net/login?x=1",
        ),
        # The portal serves its page in place of /ping.
        (
            {"https": OSError("reset"), "http": (200, "", b"<html>Welcome")},
            "portal",
            "",
        ),
        ({"https": OSError("reset"), "http": (511, "", b"")}, "portal", ""),
        # The Cloud's own HTTP -> HTTPS redirect is not a portal.
        (
            {
                "https": OSError("timed out"),
                "http": (308, "https://cloud.example/ping", b""),
            },
            "limited",
            "",
        ),
        ({"https": OSError("x"), "http": OSError("unreachable")}, "limited", ""),
        (
            {"https": http.client.BadStatusLine("x"), "http": OSError("x")},
            "limited",
            "",
        ),
    ],
)
def test_classify(answers, state, portal_url):
    get, _ = _fake(answers)
    assert network.classify("https://cloud.example", get=get) == {
        "state": state,
        "portal_url": portal_url,
    }


@pytest.mark.parametrize(
    ("url", "first"),
    [
        ("cloud.example", ("https", "cloud.example", None, "/ping")),
        ("wss://cloud.example/", ("https", "cloud.example", None, "/ping")),
        ("ws://10.0.0.5:8080", ("http", "10.0.0.5", 8080, "/ping")),
    ],
)
def test_classify_asks_the_cloud_the_way_the_relay_does(url, first):
    get, seen = _fake({"https": (200, "", b"ok"), "http": (200, "", b"ok")})
    assert network.classify(url, get=get)["state"] == "full"
    assert seen[0] == first


def test_no_default_route_is_no_internet(monkeypatch):
    monkeypatch.setattr(network, "_default_via", lambda: "")
    assert network.connectivity()["state"] == "none"


@pytest.mark.parametrize(
    ("routes", "via"),
    [
        ([{"dev": "wlan0", "metric": 600}, {"dev": "eth0", "metric": 100}], "ethernet"),
        ([{"dev": "wlan0", "metric": 600}], "wifi"),
        ([], ""),
    ],
)
def test_default_via_takes_the_lowest_metric(monkeypatch, routes, via):
    done = subprocess.CompletedProcess([], 0, stdout=json.dumps(routes), stderr="")
    monkeypatch.setattr(network, "_run", lambda *a, **k: done)
    assert network._default_via() == via


# ── Captive portal: borrowing a MAC ───────────────────────────────────────────

MACS = [
    ("aa:bb:cc:dd:ee:ff", "AA:BB:CC:DD:EE:FF"),
    ("AA-BB-CC-DD-EE-FF", "AA:BB:CC:DD:EE:FF"),  # Windows
    ("aabb.ccdd.eeff", "AA:BB:CC:DD:EE:FF"),  # Cisco
    (" 3a:bb:cc:dd:ee:01 ", "3A:BB:CC:DD:EE:01"),  # a phone's private address
    ("AABBCCDDEEFF", "AA:BB:CC:DD:EE:FF"),
    ("01:00:5e:00:00:fb", None),  # multicast
    ("00:00:00:00:00:00", None),
    ("aa:bb:cc:dd:ee", None),
    ("gg:bb:cc:dd:ee:ff", None),
    ("", None),
]


@pytest.mark.parametrize(("text", "mac"), MACS)
def test_normalize_mac(text, mac):
    assert network.normalize_mac(text) == mac


@pytest.mark.skipif(not HAS_JS_ENGINE, reason="needs a JS engine to run settings.js")
def test_page_mac_check_agrees_with_server():
    js = "\n".join(
        [
            _shipped("_normalizeMac"),
            f"var c = {json.dumps([m[0] for m in MACS])};",
            "JSON.stringify(c.map(_normalizeMac))",
        ]
    )
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(js)
        path = fh.name
    try:
        res = subprocess.run(js_argv(path), capture_output=True, text=True, check=False)
    finally:
        os.unlink(path)
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout) == [m[1] or "" for m in MACS]


def _nmcli_recorder(monkeypatch):
    runs = []

    def fake(*args, timeout=8):
        runs.append(list(args))
        out = ""
        if args[:2] == ("-t", "-f"):
            out = "0b6c-uuid:802-3-ethernet\n0f00-uuid:802-11-wireless\n"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr(network, "_nmcli", fake)
    return runs


def test_mac_set_targets_the_active_wifi_profile_by_uuid(monkeypatch):
    runs = _nmcli_recorder(monkeypatch)
    assert network._wifi_mac_set("3a-bb-cc-dd-ee-01") == {"ok": True}
    mod = [r for r in runs if r[:2] == ["con", "mod"]]
    up = [r for r in runs if r[:2] == ["con", "up"]]
    assert mod == [
        [
            "con",
            "mod",
            "0f00-uuid",
            "802-11-wireless.cloned-mac-address",
            "3A:BB:CC:DD:EE:01",
        ]
    ]
    assert up == [["con", "up", "0f00-uuid"]]


def test_mac_reset_clears_the_borrowed_address(monkeypatch):
    runs = _nmcli_recorder(monkeypatch)
    assert network._wifi_mac_set("") == {"ok": True}
    assert ["con", "mod", "0f00-uuid", "802-11-wireless.cloned-mac-address", ""] in runs


def test_mac_set_refuses_a_bad_address_before_touching_nmcli(monkeypatch):
    runs = _nmcli_recorder(monkeypatch)
    assert network._wifi_mac_set("not-a-mac").status_code == 400
    assert runs == []


def test_mac_set_needs_wifi(monkeypatch):
    def fake(*args, timeout=8):
        return subprocess.CompletedProcess(
            args, 0, stdout="u:802-3-ethernet\n", stderr=""
        )

    monkeypatch.setattr(network, "_nmcli", fake)
    assert network._wifi_mac_set("AA:BB:CC:DD:EE:FF").status_code == 400


@pytest.mark.parametrize(
    ("out", "mac"),
    [
        ("3A\\:BB\\:CC\\:DD\\:EE\\:01\n", "3A:BB:CC:DD:EE:01"),
        ("\n", ""),
        ("preserve\n", ""),
        ("random\n", ""),
    ],
)
def test_cloned_mac_reads_nmcli_escapes(monkeypatch, out, mac):
    done = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
    monkeypatch.setattr(network, "_nmcli", lambda *a, **k: done)
    assert network._cloned_mac("uuid") == mac


def test_reconnecting_keeps_the_borrowed_mac(monkeypatch):
    """Connecting from the page deletes the profile first: the address borrowed for
    the portal belongs to the network, so it goes back on the new profile."""
    nm = []

    def fake_nmcli(*args, timeout=8):
        nm.append(list(args))
        out = ""
        if args[:4] == ("-t", "-f", "UUID,TYPE,NAME", "con"):
            out = "old-uuid:802-11-wireless:Pool\\:Guest\nx:802-3-ethernet:Pool:Guest\n"
        elif args[:2] == ("-g", "802-11-wireless.cloned-mac-address"):
            out = "3A\\:BB\\:CC\\:DD\\:EE\\:01\n" if args[-1] == "old-uuid" else "\n"
        elif args[:4] == ("-t", "-f", "UUID,TYPE", "con"):
            out = "new-uuid:802-11-wireless\n"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    def fake_run(args, **kw):
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(network, "_nmcli", fake_nmcli)
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert network._wifi_connect("Pool:Guest", "pw") == {"ok": True}
    mac = "3A:BB:CC:DD:EE:01"
    assert ["con", "mod", "new-uuid", "802-11-wireless.cloned-mac-address", mac] in nm
    assert ["con", "up", "new-uuid"] in nm


def test_reconnecting_without_a_borrowed_mac_changes_nothing(monkeypatch):
    nm = []

    def fake_nmcli(*args, timeout=8):
        nm.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(network, "_nmcli", fake_nmcli)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )
    assert network._wifi_connect("Pool", "pw") == {"ok": True}
    assert not [r for r in nm if r[:2] in (["con", "mod"], ["con", "up"])]


# ── A console network without DHCP ────────────────────────────────────────────


def test_dhcp_falls_back_to_link_local(applied):
    network._eth_dhcp(False)
    assert _props(applied[0])["ipv4.link-local"] == "fallback"


def test_dhcp_retries_without_link_local_on_an_older_networkmanager(monkeypatch):
    calls = []

    def apply(*props):
        calls.append(props)
        if "ipv4.link-local" in props:
            return {"ok": False, "error": "invalid 'ipv4.link-local': 'fallback'"}
        return {"ok": True}

    monkeypatch.setattr(network, "_eth_apply", apply)
    assert network._eth_dhcp(True) == {"ok": True}
    assert len(calls) == 2 and "ipv4.link-local" not in calls[1]


def test_dhcp_does_not_retry_on_another_error(monkeypatch):
    calls = []

    def apply(*props):
        calls.append(props)
        return {"ok": False, "error": "nmcli error"}

    monkeypatch.setattr(network, "_eth_apply", apply)
    assert network._eth_dhcp(True)["ok"] is False
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("source", "eth", "fields"),
    [
        ({}, "192.168.0.10/24", {}),
        (
            {"address": "192.168.0.20", "at": 990.0},
            "192.168.0.10/24",
            {"console_from": "192.168.0.20", "console_age": 10, "console_subnet": True},
        ),
        # Link-local Pi, console on its own subnet: heard by limited broadcast only.
        (
            {"address": "192.168.0.20", "at": 1000.0},
            "169.254.7.7/16",
            {"console_from": "192.168.0.20", "console_age": 0, "console_subnet": False},
        ),
        (
            {"address": "192.168.0.20", "at": 1000.0},
            "",
            {"console_from": "192.168.0.20", "console_age": 0, "console_subnet": True},
        ),
    ],
)
def test_console_source(source, eth, fields):
    assert network.console_source(source, eth, 1000.0) == fields


# ── Watching the Cloud while the meet runs ────────────────────────────────────

UP = {"url": "https://cloud.example", "running": True, "connected": True}
DOWN = {**UP, "connected": False}


def _watch(answer="portal"):
    probes = []

    def probe():
        probes.append(1)
        return {"state": answer, "via": "wifi", "portal_url": ""}

    return network.NetWatch(probe), probes


def test_watch_waits_out_a_reconnect_before_probing():
    w, probes = _watch()
    w.tick(DOWN, 0)
    w.tick(DOWN, w.GRACE - 1)
    assert probes == [] and w.report() is None
    w.tick(DOWN, w.GRACE)
    assert probes == [1]
    assert w.report() == {"state": "portal", "via": "wifi", "portal_url": ""}


def test_watch_probes_once_a_minute_while_down():
    w, probes = _watch("limited")
    w.tick(DOWN, 0)
    for t in range(w.GRACE, w.GRACE + w.EVERY, w.TICK):
        w.tick(DOWN, t)
    assert len(probes) == 1
    w.tick(DOWN, w.GRACE + w.EVERY)
    assert len(probes) == 2


def test_watch_clears_when_the_relay_connects():
    w, _ = _watch()
    w.tick(DOWN, 0)
    w.tick(DOWN, w.GRACE)
    assert w.report()
    w.tick(UP, w.GRACE + 1)
    assert w.report() is None
    # And a later drop gets its grace period again.
    w.tick(DOWN, w.GRACE + 2)
    assert w.report() is None


@pytest.mark.parametrize(
    "status",
    [
        {**DOWN, "running": False},  # the operator disconnected it
        {**DOWN, "url": ""},  # no Cloud set up
    ],
)
def test_watch_stays_quiet_when_the_cloud_is_not_wanted(status):
    w, probes = _watch()
    for t in range(0, 200, 10):
        w.tick(status, t)
    assert probes == [] and w.report() is None


@pytest.mark.parametrize("answer", ["full", "unknown"])
def test_watch_has_nothing_to_report_when_the_cloud_answers(answer):
    w, _ = _watch(answer)
    w.tick(DOWN, 0)
    w.tick(DOWN, w.GRACE)
    assert w.report() is None


def test_cloud_status_carries_the_watch(monkeypatch):
    import relay

    monkeypatch.setattr(relay, "status", lambda: {**DOWN, "stats": None, "region": ""})
    w, _ = _watch()
    w.tick(DOWN, 0)
    w.tick(DOWN, w.GRACE)
    monkeypatch.setattr(network, "WATCH", w)
    body = network.CloudStatus.model_validate(network.route_cloud_status())
    assert body.network and body.network.state == "portal"


# ── WPA-Enterprise (802.1X) ───────────────────────────────────────────────────


def _enterprise(monkeypatch, up_ok=True, borrowed=""):
    """Run an enterprise connect against a fake nmcli; returns (nmcli calls, edits)."""
    nm, edits = [], []

    def fake_nmcli(*args, timeout=8):
        nm.append(list(args))
        out, rc = "", 0
        if args[:2] == ("con", "add"):
            out = (
                "Connection 'eduroam' (1d4e3c42-8a0b-4a55-9d1e-2b7a6f0c9e11) "
                "successfully added.\n"
            )
        elif args[:2] == ("-g", "802-11-wireless.cloned-mac-address"):
            out = borrowed
        elif args[:4] == ("-t", "-f", "UUID,TYPE,NAME", "con"):
            out = "old:802-11-wireless:eduroam\n" if borrowed else ""
        elif args[:2] == ("con", "up") and not up_ok:
            rc = 4
        return subprocess.CompletedProcess(args, rc, stdout=out, stderr="")

    def fake_run(args, **kw):
        edits.append((list(args), kw.get("input")))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(network, "_nmcli", fake_nmcli)
    monkeypatch.setattr(subprocess, "run", fake_run)
    return nm, edits


UUID = "1d4e3c42-8a0b-4a55-9d1e-2b7a6f0c9e11"


def _body(**kw):
    return network.WifiConnect.model_validate(
        {"ssid": "eduroam", "password": "s3cret pw", "identity": "jo@uni.edu", **kw}
    )


def test_enterprise_password_goes_to_nmcli_on_stdin_only(monkeypatch):
    nm, edits = _enterprise(monkeypatch)
    assert network._wifi_connect_profile(_body()) == {"ok": True}
    every_arg = [a for call in nm for a in call] + [a for c, _ in edits for a in c]
    assert all("s3cret" not in a for a in every_arg)
    edit = [i for c, i in edits if c[:3] == ["nmcli", "con", "edit"]]
    assert edit == ["set 802-1x.password s3cret pw\nsave persistent\nquit\n"]


def test_enterprise_profile(monkeypatch):
    nm, _ = _enterprise(monkeypatch)
    network._wifi_connect_profile(_body())
    add = next(c for c in nm if c[:2] == ["con", "add"])
    p = dict(zip(add[4::2], add[5::2], strict=True))
    assert p["802-11-wireless.ssid"] == "eduroam"
    assert p["802-11-wireless-security.key-mgmt"] == "wpa-eap"
    assert p["802-1x.eap"] == "peap" and p["802-1x.phase2-auth"] == "mschapv2"
    assert p["802-1x.identity"] == "jo@uni.edu"
    # Created off, so it can't start before the password is in.
    assert p["connection.autoconnect"] == "no"
    assert "802-1x.system-ca-certs" not in p
    order = [c[:2] for c in nm if c[0] == "con"]
    assert order == [["con", "add"], ["con", "mod"], ["con", "up"]]
    assert ["con", "mod", UUID, "connection.autoconnect", "yes"] in nm


def test_enterprise_with_a_domain_checks_the_certificate(monkeypatch):
    nm, _ = _enterprise(monkeypatch)
    body = _body(
        eap="ttls",
        phase2="pap",
        domain="radius.uni.edu",
        anonymous_identity="a@uni.edu",
    )
    network._wifi_connect_profile(body)
    add = next(c for c in nm if c[:2] == ["con", "add"])
    p = dict(zip(add[4::2], add[5::2], strict=True))
    assert p["802-1x.system-ca-certs"] == "yes"
    assert p["802-1x.domain-suffix-match"] == "radius.uni.edu"
    assert p["802-1x.anonymous-identity"] == "a@uni.edu"
    assert p["802-1x.eap"] == "ttls" and p["802-1x.phase2-auth"] == "pap"


def test_enterprise_keeps_the_borrowed_mac(monkeypatch):
    nm, _ = _enterprise(monkeypatch, borrowed="3A\\:BB\\:CC\\:DD\\:EE\\:01\n")
    network._wifi_connect_profile(_body())
    add = next(c for c in nm if c[:2] == ["con", "add"])
    p = dict(zip(add[4::2], add[5::2], strict=True))
    assert p["802-11-wireless.cloned-mac-address"] == "3A:BB:CC:DD:EE:01"


def test_enterprise_failure_removes_the_profile(monkeypatch):
    # A wrong password would otherwise keep retrying in the background.
    nm, _ = _enterprise(monkeypatch, up_ok=False)
    assert network._wifi_connect_profile(_body()).status_code == 400
    assert nm[-1] == ["con", "delete", UUID]


@pytest.mark.parametrize(
    "fields",
    [
        {"password": "a\nsave"},  # would end the `set` line and run another command
        {"identity": "jo\r"},
        {"domain": "not a domain"},
        {"domain": "-x.edu"},
        {"eap": "tls"},
        {"phase2": "chap"},
    ],
)
def test_enterprise_fields_are_checked(fields):
    with pytest.raises(ValueError):
        _body(**fields)


def test_a_username_selects_enterprise(monkeypatch):
    seen = []
    monkeypatch.setattr(network, "_wifi_connect_profile", lambda b: seen.append("ent"))
    monkeypatch.setattr(network, "_wifi_connect", lambda s, p: seen.append("psk"))
    asyncio.run(network.route_wifi_connect(_body()))
    asyncio.run(
        network.route_wifi_connect(network.WifiConnect(ssid="Pool", password="x"))
    )
    assert seen == ["ent", "psk"]


# ── Hidden networks ───────────────────────────────────────────────────────────


def _hidden(**kw):
    return network.WifiConnect.model_validate({"ssid": "Deck", "hidden": True, **kw})


def _add_props(nm):
    add = next(c for c in nm if c[:2] == ["con", "add"])
    return dict(zip(add[4::2], add[5::2], strict=True))


def test_hidden_wpa_network(monkeypatch):
    nm, edits = _enterprise(monkeypatch)
    assert network._wifi_connect_profile(_hidden(password="poolside1")) == {"ok": True}
    p = _add_props(nm)
    assert p["802-11-wireless.hidden"] == "yes"
    assert p["802-11-wireless-security.key-mgmt"] == "wpa-psk"
    assert "802-1x.identity" not in p
    assert all("poolside1" not in a for c in nm for a in c)
    edit = [i for c, i in edits if c[:3] == ["nmcli", "con", "edit"]]
    assert edit == [
        "set 802-11-wireless-security.psk poolside1\nsave persistent\nquit\n"
    ]


def test_hidden_open_network_has_no_secret(monkeypatch):
    nm, edits = _enterprise(monkeypatch)
    assert network._wifi_connect_profile(_hidden()) == {"ok": True}
    p = _add_props(nm)
    assert p["802-11-wireless.hidden"] == "yes"
    assert "802-11-wireless-security.key-mgmt" not in p
    assert not [c for c, _ in edits if c[:3] == ["nmcli", "con", "edit"]]
    assert ["con", "up", UUID] in nm


def test_hidden_enterprise_network(monkeypatch):
    nm, _ = _enterprise(monkeypatch)
    network._wifi_connect_profile(_hidden(identity="jo", password="pw"))
    p = _add_props(nm)
    assert p["802-11-wireless.hidden"] == "yes"
    assert p["802-11-wireless-security.key-mgmt"] == "wpa-eap"


def test_a_visible_network_has_no_hidden_flag(monkeypatch):
    nm, _ = _enterprise(monkeypatch)
    network._wifi_connect_profile(_body())
    assert "802-11-wireless.hidden" not in _add_props(nm)


def test_hidden_selects_the_profile_path(monkeypatch):
    seen = []
    monkeypatch.setattr(network, "_wifi_connect_profile", lambda b: seen.append("p"))
    monkeypatch.setattr(network, "_wifi_connect", lambda s, p: seen.append("psk"))
    asyncio.run(network.route_wifi_connect(_hidden(password="poolside1")))
    assert seen == ["p"]


# ── Keep trying: a venue router that boots after the Pi ───────────────────────


def test_new_profiles_keep_trying(monkeypatch):
    nm, _ = _enterprise(monkeypatch)
    network._wifi_connect_profile(_hidden(password="poolside1"))
    assert _add_props(nm)["connection.autoconnect-retries"] == "0"


def test_a_scanned_network_keeps_trying(monkeypatch):
    nm = []

    def fake_nmcli(*args, timeout=8):
        nm.append(list(args))
        out = "new-uuid:802-11-wireless\n" if args[:2] == ("-t", "-f") else ""
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    monkeypatch.setattr(network, "_nmcli", fake_nmcli)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda args, **kw: subprocess.CompletedProcess(args, 0, stdout="", stderr=""),
    )
    assert network._wifi_connect("Pool", "pw") == {"ok": True}
    assert ["con", "mod", "new-uuid", "connection.autoconnect-retries", "0"] in nm


def test_existing_profiles_are_set_to_keep_trying(monkeypatch):
    nm, sudo = [], []
    retries = {"w1": "-1", "w2": "0", "e1": "-1", "o1": "-1"}

    def fake_nmcli(*args, timeout=8):
        nm.append(list(args))
        out = ""
        if args[:4] == ("-t", "-f", "UUID,TYPE,NAME", "con"):
            out = (
                "w1:802-11-wireless:Pool\nw2:802-11-wireless:Club\n"
                "e1:802-3-ethernet:splouch-eth\no1:802-3-ethernet:Wired connection 1\n"
            )
        elif args[:2] == ("-g", "connection.autoconnect-retries"):
            out = retries[args[-1]] + "\n"
        return subprocess.CompletedProcess(args, 0, stdout=out, stderr="")

    def fake_run(args, **kw):
        sudo.append(list(args))
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(network, "_nmcli", fake_nmcli)
    monkeypatch.setattr(subprocess, "run", fake_run)
    network.keep_trying_profiles()
    mods = [c for c in nm if c[:2] == ["con", "mod"]]
    assert mods == [["con", "mod", "w1", "connection.autoconnect-retries", "0"]]
    keep = ["connection.autoconnect-retries", "0"]
    assert sudo == [["sudo", "nmcli", "con", "mod", "splouch-eth", *keep]]


def test_keep_trying_without_networkmanager(monkeypatch):
    def missing(*a, **k):
        raise FileNotFoundError("nmcli")

    monkeypatch.setattr(network, "_nmcli", missing)
    network.keep_trying_profiles()  # a dev machine: nothing to do, no crash


def test_the_installer_keeps_trying_too():
    src = (Path(network.__file__).parents[2] / "install" / "install.sh").read_text()
    assert "connection.autoconnect-retries 0" in src


# ── A wrong clock ─────────────────────────────────────────────────────────────


def _cert_error(code):
    e = ssl.SSLCertVerificationError(1, "certificate verify failed")
    e.verify_code = code
    return e


@pytest.mark.parametrize(
    ("https", "http", "state"),
    [
        # Expired / not yet valid, and the Cloud's own redirect over HTTP: the date.
        (_cert_error(10), (308, "https://cloud.example/ping", b""), "clock"),
        (_cert_error(9), OSError("x"), "clock"),
        # A portal comes first: it has to be dealt with before anything else.
        (_cert_error(10), (302, "http://portal.venue.net/", b""), "portal"),
        # Unknown issuer (19, 20): not the date.
        (_cert_error(20), (308, "https://cloud.example/ping", b""), "limited"),
    ],
)
def test_classify_tells_a_wrong_date(https, http, state):
    get, _ = _fake({"https": https, "http": http})
    assert network.classify("https://cloud.example", get=get)["state"] == state


def test_a_date_before_any_release_is_wrong():
    get, _ = _fake({"https": OSError("x"), "http": OSError("y")})

    def jan_1970():  # a clock reset to the epoch
        return 0.0

    assert network.classify("cloud.example", get=get, now=jan_1970)["state"] == "clock"


def test_the_watcher_reports_a_wrong_date():
    w, _ = _watch("clock")
    w.tick(DOWN, 0)
    w.tick(DOWN, w.GRACE)
    assert w.report()["state"] == "clock"
