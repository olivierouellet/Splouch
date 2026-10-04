"""`GET /servers` — the scheme it advertises itself under (`api.md` §5.11).

The cloud runs uvicorn behind Caddy, which terminates TLS and proxies over plain
HTTP, so the ASGI scope says `http`. The row for this server is derived from the
request (`request.base_url`), so it inherits that — and uvicorn trusts
`X-Forwarded-Proto` only from `127.0.0.1` by default, while Caddy reaches the app
from a compose-bridge address. Production advertised `http://splouch.ca`.

That is worse than cosmetic. The iOS app deduplicates its server menu by origin, so
`https://splouch.ca:443` and `http://splouch.ca:80` are two servers and "Splouch"
appears twice; and its ATS policy is `NSAllowsLocalNetworking` only, so the cleartext
row is one the OS refuses to connect to. Tapping it strands the user on a dead picker.

The fix is `FORWARDED_ALLOW_IPS` in `cloud/docker-compose.yml`, not a constant: a Pi
or a dev cloud is legitimately cleartext (`app.md` `P-12`), so the scheme has to follow
the deployment. These tests pin both directions.
"""

import asyncio
import os
from pathlib import Path

import pytest
import yaml
from starlette.requests import Request
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

import cloud_control as cs

# The picker reads its branding and the meet list from the control plane's
# store (cloud/cloud_db.py).
pytestmark = pytest.mark.usefixtures("pg")

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COMPOSE = os.path.join(REPO, "cloud", "docker-compose.yml")


def _trusted():
    """The deployed trust list, read from compose so the tests follow the config."""
    env = yaml.safe_load(Path(COMPOSE).read_text(encoding="utf-8"))["services"]["app"][
        "environment"
    ]
    raw = env["FORWARDED_ALLOW_IPS"]
    return raw.split(":-", 1)[1].rstrip("}") if raw.startswith("${") else raw


def _scope(host, client, forwarded_proto=None):
    headers = [(b"host", host.encode())]
    if forwarded_proto:
        headers.append((b"x-forwarded-proto", forwarded_proto.encode()))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "http",
        "method": "GET",
        "path": "/servers",
        "raw_path": b"/servers",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": client,
        "server": (host.split(":")[0], 80),
    }


def _advertised(scope, trusted=None):
    """Drive the real middleware over the scope, then the real route."""
    if trusted is None:
        trusted = _trusted()
    seen = {}

    async def inner(scope, receive, send):
        seen["scope"] = scope

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        pass

    asyncio.run(
        ProxyHeadersMiddleware(inner, trusted_hosts=trusted)(scope, receive, send)
    )
    return cs.route_servers(Request(seen["scope"]))["servers"][0]


# Caddy's address on the compose bridge is whatever Docker hands out; the trust list
# covers the private ranges rather than one literal so a renumber cannot break it.
@pytest.mark.parametrize(
    "caddy_ip", ["172.18.0.3", "172.31.255.1", "10.5.0.2", "127.0.0.1"]
)
def test_a_trusted_proxy_sets_the_scheme(caddy_ip):
    row = _advertised(_scope("splouch.ca", (caddy_ip, 54321), "https"))
    assert row["url"] == "https://splouch.ca"


def test_a_public_client_cannot_spoof_the_scheme():
    """Why the trust list is private ranges and not `*`."""
    row = _advertised(_scope("splouch.ca", ("203.0.113.7", 54321), "https"))
    assert row["url"] == "http://splouch.ca"


def test_a_cleartext_deployment_stays_cleartext():
    """`app.md` `P-12`: a dev cloud on the local network is cleartext, and correct.

    No proxy, so no `X-Forwarded-Proto` — the fix must not force a scheme.
    """
    row = _advertised(_scope("127.0.0.1:5055", ("127.0.0.1", 54321)))
    assert row["url"] == "http://127.0.0.1:5055"


def test_the_row_shape_is_unchanged():
    """`api.md` §5.11 fixes `{name, url, kind}`. Only the `url` value was wrong."""
    row = _advertised(_scope("splouch.ca", ("172.18.0.3", 54321), "https"))
    assert set(row) == {"name", "url", "kind"}
    assert row["kind"] == "cloud"


def test_the_deployment_trusts_the_proxy_it_runs_behind():
    """The fix lives in config, so the config is what a regression would drop.

    uvicorn's own default is `127.0.0.1`, which is exactly what does not work here.
    """
    import ipaddress

    trusted = _trusted()
    assert trusted != "127.0.0.1", (
        "back to the uvicorn default — Caddy is not on loopback"
    )
    networks = [ipaddress.ip_network(p) for p in trusted.split(",") if "/" in p]
    # Docker hands compose bridges addresses out of 172.16/12 by default, and out of
    # 10/8 when the daemon is configured with a custom address pool.
    for probe in ("172.18.0.3", "10.5.0.2"):
        addr = ipaddress.ip_address(probe)
        assert any(addr in n for n in networks), probe
    assert "*" not in trusted, "would trust a public client to set its own scheme"


def test_the_app_container_is_not_directly_reachable():
    """What makes trusting a private range safe: only Caddy can reach the app."""
    services = yaml.safe_load(Path(COMPOSE).read_text(encoding="utf-8"))["services"]
    assert not services["app"].get("ports"), "app must stay behind Caddy"
    assert services["caddy"].get("ports") == ["80:80", "443:443"]


def test_the_directory_never_lists_cleartext_to_a_public_host(tmp_path, monkeypatch):
    """`P-12`'s floor applies to a listed address as to a typed or scanned one.

    A cleartext row to a public host is one every client refuses, so the cloud
    drops it from `servers.json` rather than shipping a dead row. Local cleartext
    — a Pi by name or by private address — and `https` anywhere stay listed, in
    their own spelling.
    """
    import json

    servers = tmp_path / "servers.json"
    servers.write_text(
        json.dumps(
            [
                {"name": "Club", "url": "https://x.example/base/"},
                {"name": "Pi", "url": "http://poolpi.local:5000"},
                {"name": "Pi by address", "url": "http://192.168.1.10:5000"},
                {"name": "Public cleartext", "url": "http://scores.example.com"},
                {"name": "Public IP", "url": "http://203.0.113.5:5000"},
                {"name": "Mapped public", "url": "http://[::ffff:8.8.8.8]"},
                {"name": "Junk", "url": "not a url"},
                "not an entry",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cs, "SERVERS_FILE", str(servers))
    rows = cs.route_servers(Request(_scope("splouch.ca", ("127.0.0.1", 1), "https")))[
        "servers"
    ]
    assert [r["url"] for r in rows[1:]] == [
        "https://x.example/base",
        "http://poolpi.local:5000",
        "http://192.168.1.10:5000",
    ]
