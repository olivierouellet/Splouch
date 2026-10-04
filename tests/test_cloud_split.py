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
import cloud_ticket

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
    monkeypatch.setenv("NODE_REGION", "ca")
    monkeypatch.setenv("NODE_URL", "https://ca1.example")
    monkeypatch.setenv("WORKER", "1")
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

    cloud_node.heartbeat([])  # the node exists, so /api/assign can pick it
    return cloud_auth.add_organizer("Club", region="ca")


class FakeWS:
    def __init__(self):
        self.frames = []
        self.closed = False

    async def close(self):
        self.closed = True

    async def send_json(self, frame):
        self.frames.append(frame)

    async def send_text(self, text):
        self.frames.append(json.loads(text))

    def events(self):
        return [f["event"] for f in self.frames]


def assign(key, uid="uid-1"):
    """What a Pi gets from `POST /api/assign`: (status, body)."""
    return _asgi("POST", "/api/assign", {"key": key, "meet_uid": uid})


def ticket(key, meet_id="m1", node="ca1", worker=1, now=None):
    return cloud_ticket.sign(SECRET, meet_id, node, worker, key, "Club", now=now)


def register(key, sid="sid-1", uid="uid-1", tk=None):
    """A Pi's register on the worker. By default it asks `/api/assign` first, as
    a Pi does; `tk` hands it a ticket instead."""
    if tk is None:
        tk = assign(key, uid)[1]["ticket"]
    ws = FakeWS()
    data = {"key": key, "meet_uid": uid, "ticket": tk, **META}
    asyncio.run(cs._on_relay_register(ws, sid, data))
    return ws


REASSIGN = {
    "event": "rejected",
    "data": {"reason": "not assigned here", "reassign": True},
}


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
    ws = register("not-a-key", tk=ticket("not-a-key"))
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
    assert cloud_node.heartbeat([])["retired"] == [meet_id]


# ── With the control plane down ────────────────────────────────────────────────


@pytest.fixture
def control_down(monkeypatch):
    monkeypatch.setenv("NODE_SECRET", SECRET)
    monkeypatch.setenv("NODE_NAME", "ca1")
    monkeypatch.setenv("WORKER", "1")
    monkeypatch.setattr(cloud_node, "_records", {})

    def call(*a, **k):
        raise cloud_node.ControlError("connection refused")

    monkeypatch.setattr(cloud_node, "_call", call)
    monkeypatch.setattr(cloud_store, "_meets", {})
    monkeypatch.setattr(cs, "_meets", cloud_store._meets)
    monkeypatch.setattr(cs, "_relay_sids", {})


def test_a_pi_with_a_live_ticket_gets_in_while_the_control_plane_is_down(
    control_down,
):
    ws = register("k", tk=ticket("k", "m1"))
    assert ws.frames == [{"event": "registered", "data": {"meet_id": "m1"}}]
    assert cs._meets["m1"]["organizer"] == "Club"


def test_a_pi_without_one_is_sent_back_to_ask(control_down):
    assert register("k", tk="").frames == [REASSIGN]
    assert not cs._meets


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


# ── Assignment and tickets ─────────────────────────────────────────────────────


def test_assign_names_the_worker_socket_and_the_region(wired):
    status, body = assign(wired)
    assert status == 200
    assert body["relay_url"] == f"wss://ca1.example/w1/ws/relay?meet={body['meet_id']}"
    assert body["region"] == "ca"
    assert cloud_ticket.verify(SECRET, body["ticket"])["n"] == "ca1"


def test_assign_refuses_a_bad_key(wired):
    assert assign("nope") == (403, {"reason": "invalid or inactive key"})


def test_assign_never_leaves_the_organizer_s_region(wired):
    import cloud_auth

    key = cloud_auth.add_organizer("Club EU", country="FR", region="eu")
    assert assign(key) == (503, {"reason": "no server available"})


@pytest.mark.parametrize(
    "tk",
    [
        lambda key: ticket(key, worker=2),  # another worker's
        lambda key: ticket(key, node="us1"),  # another node's
        lambda key: ticket("someone-else"),  # another key's
        lambda key: ticket(key, now=0),  # long expired
        lambda key: ticket(key)[:-4] + "AAAA",  # tampered
    ],
    ids=["worker", "node", "key", "expired", "forged"],
)
def test_a_ticket_not_for_this_worker_sends_the_pi_back(wired, tk):
    assert register(wired, tk=tk(wired)).frames == [REASSIGN]
    assert not cs._meets


def test_a_ticket_for_another_meet_sends_the_pi_back(wired):
    """The operator switched meets: the old ticket names the old meet."""
    old = assign(wired, "uid-old")[1]["ticket"]
    assert register(wired, uid="uid-new", tk=old).frames == [REASSIGN]


def test_the_pi_s_location_reaches_the_admin_beside_the_record(wired):
    import cloud_auth

    ws = FakeWS()
    data = {
        "key": wired,
        "meet_uid": "uid-1",
        "ticket": assign(wired)[1]["ticket"],
        "organizer_location": {"country": "ca", "province": "QC"},
        **META,
    }
    asyncio.run(cs._on_relay_register(ws, "sid-1", data))
    info = cloud_auth.load_keys()[wired]
    assert info["country"] == "" and info["reported"] == {
        "country": "CA",
        "province": "QC",
    }
    cloud_auth.accept_location(wired)
    info = cloud_auth.load_keys()[wired]
    assert (info["country"], info["province"], info["region"]) == ("CA", "QC", "ca")
    assert info["reported"] is None


def test_the_heartbeat_carries_each_meet_s_attendees(wired):
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    cs.manager.join(FakeWS(), cs._ch("scoreboard", meet_id))
    try:
        ids, attendees = cs._heartbeat_snapshot()
        assert attendees == {meet_id: 1}
        cloud_node.heartbeat(ids, attendees)
    finally:
        cs.manager.channels.pop(cs._ch("scoreboard", meet_id), None)
    import cloud_db

    with cloud_db.conn() as c:
        row = c.execute("SELECT attendees FROM meets WHERE id = %s", (meet_id,))
        assert row.fetchone()["attendees"] == 1


def test_the_panel_flags_a_location_the_pi_reports(pg, monkeypatch):
    import cloud_auth

    key = cloud_auth.add_organizer("Club", country="CA", province="QC", region="ca")
    cloud_auth.report_location(key, "CA", "ON")
    html = _admin_get(monkeypatch)
    assert "Their Pi says: CA · ON" in html
    assert 'name="action" value="accept_location"' in html


# ── Several workers ────────────────────────────────────────────────────────────


def _page(path, query=b""):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query,
            "headers": [(b"host", b"ca1.example")],
            "app": cs.app,
        }
    )


def test_a_worker_s_pages_link_back_under_its_prefix(wired, monkeypatch):
    monkeypatch.setenv("WORKER", "3")
    meet_id = register(
        wired,
        tk=ticket(
            wired, cloud_control.cloud_registry.meet_id_for(wired, "uid-1"), worker=3
        ),
    ).frames[0]["data"]["meet_id"]
    shell = cs.route_mobile(_page("/mobile", f"meet={meet_id}".encode())).body.decode()
    assert f'src="/w3/mobile/live?meet={meet_id}' in shell
    assert f'href="/w3/manifest/{meet_id}"' in shell
    board = cs.route_live(
        _page("/mobile/live", f"meet={meet_id}".encode())
    ).body.decode()
    assert "splouchSocket('/w3/ws/scoreboard')" in board
    manifest = json.loads(cs.route_manifest(meet_id).body)
    assert manifest["start_url"] == f"/w3/mobile?meet={meet_id}"


def test_a_page_for_a_meet_live_on_another_worker_goes_there(wired, monkeypatch):
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    cs._meets.clear()  # seen from another worker, which does not hold it
    cloud_node._records.clear()
    monkeypatch.setenv("WORKER", "2")
    resp = cs.route_mobile(_page("/mobile", f"meet={meet_id}".encode()))
    assert resp.status_code == 307
    assert resp.headers["location"] == f"https://ca1.example/w1/mobile?meet={meet_id}"


def test_a_socket_for_a_meet_live_elsewhere_is_sent_there(wired, monkeypatch):
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    cs._meets.clear()
    cloud_node._records.clear()
    monkeypatch.setenv("WORKER", "2")

    class Joiner(FakeWS):
        def __init__(self):
            super().__init__()
            self.inbox = [{"event": "join_meet", "data": {"meet_id": meet_id}}]

        async def accept(self):
            pass

        async def receive_json(self):
            if self.inbox:
                return self.inbox.pop()
            raise cs.WebSocketDisconnect

    ws = Joiner()
    asyncio.run(cs.ws_scoreboard(ws))  # ty: ignore[invalid-argument-type]
    assert ws.frames == [
        {
            "event": "moved",
            "data": {"url": f"https://ca1.example/w1/mobile?meet={meet_id}"},
        }
    ]


def test_a_moved_meet_s_pi_and_attendees_are_sent_on(wired):
    ws_pi = FakeWS()
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    cs._relay_sockets["sid-1"] = ws_pi
    phone = FakeWS()
    cs.manager.join(phone, cs._ch("scoreboard", meet_id))
    url = f"https://ca1.example/w2/mobile?meet={meet_id}"
    try:
        asyncio.run(cs._on_moves([{"meet_id": meet_id, "url": url}]))
    finally:
        cs._relay_sockets.pop("sid-1", None)
        cs.manager.channels.pop(cs._ch("scoreboard", meet_id), None)
    assert phone.frames == [{"event": "moved", "data": {"url": url}}]
    assert ws_pi.frames == [
        {"event": "rejected", "data": {"reason": "moved", "reassign": True}}
    ]
    assert ws_pi.closed


def test_the_panel_lists_nodes_and_offers_a_move(pg, monkeypatch):
    import cloud_auth

    reg = cloud_control.cloud_registry
    reg.heartbeat("ca1", 1, [], host="https://ca1.example", region="ca", workers=2)
    key = cloud_auth.add_organizer("Club", region="ca")
    reg.register(key, "uid", META, "ca1", 1)
    html = _admin_get(monkeypatch)
    assert 'id="tab-nodes"' in html and "https://ca1.example" in html
    assert '<option value="ca1:2">ca1 · w2</option>' in html
    assert '<option value="ca1:1">' not in html, "the worker holding it is not a target"


def test_a_worker_reached_directly_answers_under_its_prefix(monkeypatch):
    """No proxy in front (a local run, a missing route): `/w2/ping` still works."""
    monkeypatch.setenv("WORKER", "2")
    sent = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] = sent.get("body", b"") + message.get("body", b"")

    for path in ("/w2/ping", "/ping"):
        sent.clear()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"host", b"w")],
            "client": ("10.0.0.2", 1),
            "server": ("w", 5000),
        }
        asyncio.run(cs.app(scope, receive, send))
        assert (sent["status"], sent["body"]) == (200, b"ok"), path
