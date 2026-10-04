"""The worker and the control plane, wired together in-process.

A worker owns nothing durable: a Pi's key, its meet id, a retained meet's record
and the attendance counts all live on the control plane, reached through
`cloud_node` (docs/architecture/scaling.md). These tests route `cloud_node`'s calls
straight into the control plane's ASGI app — the real routes, the real
`NODE_SECRET` check, the real request models — with no network in between, and
drive the worker the way a Pi and a phone do.

The last section needs no database: what the worker does when the control plane
cannot be reached at all.
"""

import asyncio
import json

import pytest

import cloud_control
import cloud_node
import cloud_server as cs
import cloud_store

SECRET = "test-node-secret"
META = {"name": "Coupe", "meet_date": "2026-10-04", "settings": {"locale": "fr"}}


def _asgi(method, path, body=None, secret=SECRET):
    """One request through the control plane's app: (status, parsed JSON)."""
    raw = json.dumps(body).encode() if body is not None else b""
    path, _, query = path.partition("?")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": [
            (b"host", b"control"),
            (b"content-type", b"application/json"),
            (b"authorization", f"Bearer {secret}".encode()),
        ],
        "client": ("10.0.0.2", 5000),
        "server": ("control", 8000),
    }
    sent = {"status": None, "body": b""}

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    asyncio.run(cloud_control.app(scope, receive, send))
    return sent["status"], json.loads(sent["body"] or b"null")


@pytest.fixture
def wired(pg, monkeypatch, tmp_path):
    """`cloud_node` talks to the in-process control plane; the worker starts empty."""
    monkeypatch.setenv("NODE_SECRET", SECRET)
    monkeypatch.setenv("NODE_NAME", "ca1")
    monkeypatch.setenv("WORKER", "1")
    monkeypatch.setattr(cloud_node, "REGISTER_CACHE_FILE", str(tmp_path / "rc.json"))
    monkeypatch.setattr(cloud_node, "_records", {})

    def call(method, path, body=None, timeout=5):
        status, data = _asgi(method, path, body)
        if status == 404:
            return None
        if status in (401, 403):
            raise cloud_node.Refused((data or {}).get("reason", f"HTTP {status}"))
        if status >= 400:
            raise cloud_node.ControlError(f"HTTP {status}")
        return data

    monkeypatch.setattr(cloud_node, "_call", call)
    monkeypatch.setattr(cloud_store, "_meets", {})
    monkeypatch.setattr(cloud_store, "_relay_sids", {})
    monkeypatch.setattr(cs, "_meets", cloud_store._meets)
    monkeypatch.setattr(cs, "_relay_sids", cloud_store._relay_sids)
    import cloud_auth

    return cloud_auth.add_organizer("Club", region="ca")


class FakeWS:
    def __init__(self):
        self.frames = []

    async def send_json(self, frame):
        self.frames.append(frame)

    async def send_text(self, text):
        self.frames.append(json.loads(text))

    def events(self):
        return [f["event"] for f in self.frames]


def register(key, sid="sid-1", uid="uid-1"):
    ws = FakeWS()
    asyncio.run(cs._on_relay_register(ws, sid, {"key": key, "meet_uid": uid, **META}))
    return ws


# ── Over the wire ──────────────────────────────────────────────────────────────


def test_the_internal_api_refuses_a_wrong_secret(wired):
    status, _ = _asgi("GET", "/internal/meets/x", secret="wrong")
    assert status == 401


def test_the_internal_api_refuses_everyone_without_a_secret(wired, monkeypatch):
    monkeypatch.delenv("NODE_SECRET")
    status, _ = _asgi("GET", "/internal/meets/x")
    assert status == 503


def test_the_internal_routes_stay_out_of_the_public_schema(wired):
    paths = cloud_control.app.openapi()["paths"]
    assert not [p for p in paths if p.startswith("/internal")]


# ── A Pi on the worker ─────────────────────────────────────────────────────────


def test_a_pi_registers_through_the_control_plane(wired):
    ws = register(wired)
    assert ws.events() == ["registered"]
    meet_id = ws.frames[0]["data"]["meet_id"]
    assert meet_id in cs._meets
    rec = cloud_control.cloud_registry.get(meet_id)
    assert rec["live"] and rec["node"] == "ca1" and rec["name"] == "Coupe"


def test_a_bad_key_is_rejected_with_the_control_plane_s_reason(wired):
    ws = register("not-a-key")
    assert ws.frames == [
        {"event": "rejected", "data": {"reason": "invalid or inactive key"}}
    ]
    assert not cs._meets


def test_a_disconnect_retires_the_meet_and_its_page_still_serves(wired):
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    asyncio.run(cs._on_relay_disconnect("sid-1"))
    assert meet_id not in cs._meets
    assert not cloud_control.cloud_registry.get(meet_id)["live"]
    assert cs.route_meet_config(meet_id)["live"] is False


def test_a_retained_meet_is_fetched_by_a_worker_that_never_held_it(wired):
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    asyncio.run(cs._on_relay_disconnect("sid-1"))
    cloud_node._records.clear()  # a different worker: nothing cached
    assert cs.route_meet_config(meet_id)["name"] == "Coupe"


def test_the_schedule_reaches_the_control_plane(wired):
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    sched = {"events": [[1, [1]]], "names": {"1": "100 Free"}}
    asyncio.run(cs._forward("sid-1", "schedule_snapshot", sched))
    assert cloud_control.cloud_registry.get(meet_id)["schedule_data"] == sched


def test_the_pi_reads_its_attendance_through_the_worker(wired):
    import cloud_auth

    creds = cloud_auth.load_creds()
    creds["analytics_enabled"] = True
    cloud_auth.save_creds(creds)
    ws = register(wired)
    meet_id = ws.frames[0]["data"]["meet_id"]
    cloud_node.heartbeat(list(cs._meets))  # learns counting is on
    cloud_node.log_connection(meet_id, "device-1", "scoreboard")
    cloud_node.log_connection(meet_id, "device-2", "results")
    cloud_node.flush_analytics()
    asyncio.run(cs._on_relay_stats(ws, "sid-1"))
    stats = ws.frames[-1]
    assert stats["event"] == "stats" and stats["data"]["enabled"] is True
    assert stats["data"]["counts"]["all"] == 2


def test_joins_are_not_queued_while_counting_is_off(wired):
    cloud_node.heartbeat([])
    cloud_node.log_connection("m", "device-1", "scoreboard")
    assert cloud_node._analytics_queue.empty()


def test_the_heartbeat_retires_a_meet_the_worker_lost(wired):
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    cs._meets.clear()  # the worker restarted: it holds nothing now
    assert cloud_node.heartbeat([]) == [meet_id]


# ── With the control plane down ────────────────────────────────────────────────


@pytest.fixture
def control_down(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud_node, "REGISTER_CACHE_FILE", str(tmp_path / "rc.json"))
    monkeypatch.setattr(cloud_node, "_records", {})

    def call(*a, **k):
        raise cloud_node.ControlError("connection refused")

    monkeypatch.setattr(cloud_node, "_call", call)
    monkeypatch.setattr(cloud_store, "_meets", {})
    monkeypatch.setattr(cs, "_meets", cloud_store._meets)
    monkeypatch.setattr(cs, "_relay_sids", {})


def test_an_unknown_pi_is_told_to_retry(control_down):
    ws = register("some-key")
    assert ws.frames == [
        {"event": "rejected", "data": {"reason": "control plane unreachable"}}
    ]


def test_a_pi_this_worker_has_admitted_before_gets_back_in(control_down):
    cloud_node.atomic_write(
        cloud_node.REGISTER_CACHE_FILE,
        json.dumps({"k\nuid-1": {"meet_id": "m1", "organizer": "Club"}}),
    )
    ws = register("k")
    assert ws.frames == [{"event": "registered", "data": {"meet_id": "m1"}}]
    assert "m1" in cs._meets


def test_a_page_keeps_serving_the_last_record_seen(control_down):
    cloud_node.remember("m1", {"name": "Coupe", "settings": {}})
    cloud_node._records["m1"] = (cloud_node._records["m1"][0], -1e9)  # stale
    assert cloud_node.fetch_meet("m1")["name"] == "Coupe"


def test_a_lost_analytics_batch_is_dropped_not_hoarded(control_down, monkeypatch):
    monkeypatch.setitem(cloud_node._settings, "analytics_enabled", True)
    cloud_node.log_connection("m1", "device-1", "scoreboard")
    cloud_node.flush_analytics()
    assert cloud_node._analytics_queue.empty()


# ── The panel ──────────────────────────────────────────────────────────────────


def _admin_get(monkeypatch):
    """GET /admin through the whole app, signed in with the seeded login."""
    import base64

    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "pw")
    token = base64.b64encode(b"admin:pw").decode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/admin",
        "raw_path": b"/admin",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"splouch.org"),
            (b"authorization", f"Basic {token}".encode()),
        ],
        "client": ("203.0.113.9", 5000),
        "server": ("splouch.org", 443),
    }
    sent = {"status": None, "body": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    asyncio.run(cloud_control.app(scope, receive, send))
    assert sent["status"] == 200, sent["body"][:300]
    return sent["body"].decode()


def test_the_panel_shows_where_an_organizer_is_based(pg, monkeypatch):
    import cloud_auth

    cloud_auth.add_organizer("Club Natation", country="CA", province="QC", region="ca")
    html = _admin_get(monkeypatch)
    assert "Club Natation" in html
    assert "· CA · QC" in html
    assert 'name="action" value="update_org"' in html
    # Every country the form offers says which region it suggests.
    assert 'value="FR" data-region="eu"' in html
