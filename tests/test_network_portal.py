"""Settings → Network at a venue: a console-only Ethernet and a Wi-Fi sign-in page.

Two set-ups that used to leave the Pi with either the console or the Cloud, never both:

* The console broadcasts UDP on a closed network plugged into eth0, and the internet
  comes over Wi-Fi. eth0 must then never take the default route, or the Cloud's
  traffic goes into the closed network and stops there.
* The venue Wi-Fi has a captive portal. The Pi has no browser, so the operator
  accepts the portal on a phone or laptop and the Pi borrows that device's MAC.
"""

import asyncio
import http.client
import json
import os
import subprocess
import tempfile

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
