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
# Far ahead: a retired meet expires at midnight after its date, and a date that
# has passed would be swept before a test could read it back.
META = {"name": "Coupe", "meet_date": "2099-06-04", "settings": {"locale": "fr"}}


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
    _fresh_node_store(monkeypatch, tmp_path)

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


def test_the_start_list_stays_on_the_node(wired):
    """It names every athlete: the node's store keeps it; the control plane never
    sees it (docs/architecture/scaling.md)."""
    import cloud_meetstore

    meet_id = register(wired).frames[0]["data"]["meet_id"]
    sched = {
        "events": [[1, [1]]],
        "start_list": {"1": {"1": {"4": {"name": "Ledecky"}}}},
    }
    asyncio.run(cs._forward("sid-1", "schedule_snapshot", sched))
    assert cloud_meetstore.get(meet_id)["schedule_data"] == sched
    assert "Ledecky" not in repr(cloud_control.cloud_registry.get(meet_id))


def test_a_finished_meet_is_served_from_the_node_after_a_restart(wired):
    """An update restarts the workers; the node's store is on disk."""
    import cloud_meetstore

    meet_id = register(wired).frames[0]["data"]["meet_id"]
    sched = {"events": [[1, [1]]], "names": {"1": "100 Free"}}
    asyncio.run(cs._forward("sid-1", "schedule_snapshot", sched))
    asyncio.run(cs._on_relay_disconnect("sid-1"))
    cloud_meetstore.close()  # a fresh process
    cloud_node._records.clear()
    assert cs.route_meet_schedule(meet_id)["heats"][0]["event"] == 1
    assert cs.route_meet_config(meet_id)["live"] is False


def test_the_node_drops_what_the_control_plane_no_longer_lists(wired, monkeypatch):
    import cloud_meetstore

    meet_id = register(wired).frames[0]["data"]["meet_id"]
    asyncio.run(cs._on_relay_disconnect("sid-1"))
    monkeypatch.setattr(cloud_meetstore, "FRESH_SECS", 0)
    cloud_control.cloud_registry.delete(meet_id)
    cloud_node.heartbeat([])  # worker 1: the reply lists what to keep
    assert cloud_meetstore.get(meet_id) is None


def test_the_relay_key_never_reaches_the_node_s_store(wired):
    import cloud_meetstore

    meet_id = register(wired).frames[0]["data"]["meet_id"]
    assert wired not in repr(cloud_meetstore.get(meet_id))


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


def _fresh_node_store(monkeypatch, tmp_path):
    """The node's stores in a directory of this test's own."""
    import cloud_attendance
    import cloud_meetstore
    import cloud_paths

    monkeypatch.setattr(cloud_paths, "ANALYTICS_FILE", str(tmp_path / "analytics.db"))
    cloud_meetstore.close()
    cloud_attendance.close()


@pytest.fixture
def control_down(monkeypatch, tmp_path):
    _fresh_node_store(monkeypatch, tmp_path)
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


def test_a_finished_meet_is_served_while_the_control_plane_is_down(control_down):
    import cloud_meetstore

    cloud_meetstore.save(
        "m1", {"name": "Coupe", "settings": {}}, expires="2099-01-01T00:00:00"
    )
    assert cs.route_meet_config("m1")["name"] == "Coupe"


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
        ids, attendees, _ = cs._heartbeat_snapshot()
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
            "data": {
                "url": f"https://ca1.example/w1/mobile?meet={meet_id}",
                "base": "https://ca1.example/w1",
            },
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
        asyncio.run(
            cs._on_moves(
                [{"meet_id": meet_id, "url": url, "base": "https://ca1.example/w2"}]
            )
        )
    finally:
        cs._relay_sockets.pop("sid-1", None)
        cs.manager.channels.pop(cs._ch("scoreboard", meet_id), None)
    assert phone.frames == [
        {"event": "moved", "data": {"url": url, "base": "https://ca1.example/w2"}}
    ]
    assert ws_pi.frames == [
        {"event": "rejected", "data": {"reason": "moved", "reassign": True}}
    ]
    assert ws_pi.closed


def test_a_revoked_key_s_pi_is_dropped(wired):
    """The key is checked only at register, so revoking it left a connected Pi
    publishing until it dropped by itself. Not told to reassign: `/api/assign`
    would only refuse it."""
    ws_pi = FakeWS()
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    cs._relay_sockets["sid-1"] = ws_pi
    try:
        asyncio.run(cs._on_revoked([meet_id, "not-held-here"]))
    finally:
        cs._relay_sockets.pop("sid-1", None)
    assert ws_pi.frames == [
        {"event": "rejected", "data": {"reason": "invalid or inactive key"}}
    ]
    assert ws_pi.closed


def test_the_panel_lists_nodes_and_offers_a_move(pg, monkeypatch):
    import cloud_auth

    reg = cloud_control.cloud_registry
    reg.heartbeat("ca1", 1, [], host="https://ca1.example", region="ca", workers=2)
    key = cloud_auth.add_organizer("Club", region="ca")
    reg.register(key, "uid", META, "ca1", 1)
    html = _admin_get(monkeypatch)
    assert 'id="tab-nodes"' in html and 'text-break">https://ca1.example</div>' in html
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


# ── The picker and the meet's address (`app.md` v3) ────────────────────────────


def test_meets_names_each_meet_s_base(wired):
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    status, body = _asgi("GET", "/meets")
    (meet,) = body["meets"]
    assert status == 200
    assert meet["base"] == "https://ca1.example/w1"
    assert meet["url"] == f"https://ca1.example/w1/mobile?meet={meet_id}"
    assert {"country", "province"} <= set(meet)


def test_meets_is_readable_from_any_origin(wired):
    """A meet page on a worker's host asks it before going back (`A-12`)."""
    headers = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            headers.update(dict(message["headers"]))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "https",
        "path": "/meets",
        "raw_path": b"/meets",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"splouch.org")],
        "client": ("203.0.113.9", 1),
        "server": ("splouch.org", 443),
    }
    asyncio.run(cloud_control.app(scope, receive, send))
    assert headers[b"access-control-allow-origin"] == b"*"


def test_config_names_the_meet_s_base_here_and_after_a_move(wired, monkeypatch):
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    assert cs.route_meet_config(meet_id)["base"] == "https://ca1.example/w1"
    cs._meets.clear()
    cloud_node._records.clear()
    monkeypatch.setenv("WORKER", "2")  # asked of a worker that does not hold it
    config = cs.route_meet_config(meet_id)
    assert config["base"] == "https://ca1.example/w1" and config["live"] is True


def test_the_shell_s_back_link_checks_the_meet_list_first(wired, monkeypatch):
    monkeypatch.setenv("PICKER_URL", "https://splouch.org")
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    shell = cs.route_mobile(_page("/mobile", f"meet={meet_id}".encode())).body.decode()
    assert 'href="https://splouch.org/"' in shell and 'id="nav-back"' in shell
    assert 'id="picker-down"' in shell
    assert "new URL('meets', target)" in shell


def _picker(n):
    import cloud_auth

    reg = cloud_control.cloud_registry
    key = cloud_auth.add_organizer("Club", country="CA", province="QC", region="ca")
    image = {"picker_image_b64": "eA=="}
    for i in range(n):
        meta = {**META, "name": f"Meet {i}", "settings": image}
        reg.register(key, f"u{i}", meta, "ca1", 1)
    return cloud_control.route_index(_page("/")).body.decode()


def test_ten_meets_are_cards_with_their_images(wired):
    html = _picker(10)
    assert 'class="meets"' in html and "/picker_image/" in html


def test_eleven_are_compact_rows_and_load_no_image(wired):
    html = _picker(11)
    assert 'class="meets compact"' in html and "/picker_image/" not in html


def test_a_card_shows_and_searches_the_province_and_country(wired):
    html = _picker(1)
    assert '<span class="country" data-country="CA">CA</span>' in html
    assert "<span>QC</span>" in html
    assert 'data-search="Meet 0 2099-06-04 Club QC CA"' in html


# ── Rolling updates on the worker ──────────────────────────────────────────────


def test_worker_1_updates_its_node_once_when_released(monkeypatch):
    calls = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        calls.append((req.full_url, req.headers.get("X-deploy-token"), req.data))
        return Resp()

    monkeypatch.setenv("WORKER", "1")
    monkeypatch.setenv("DEPLOY_WEBHOOK_URL", "http://host:9000/deploy")
    monkeypatch.setenv("DEPLOY_WEBHOOK_SECRET", "s")
    monkeypatch.setattr(cloud_node.urllib.request, "urlopen", urlopen)
    monkeypatch.setitem(cloud_node._settings, "deploying", None)
    cloud_node.update_node("v2")
    cloud_node.update_node("v2")
    assert calls == [("http://host:9000/deploy", "s", b'{"version": "v2"}')]


def test_other_workers_leave_it_to_worker_1(monkeypatch):
    monkeypatch.setenv("WORKER", "2")
    monkeypatch.setitem(cloud_node._settings, "deploying", None)

    def boom(*a, **k):
        raise AssertionError("worker 2 called the webhook")

    monkeypatch.setattr(cloud_node.urllib.request, "urlopen", boom)
    cloud_node.update_node("v2")


def test_the_heartbeat_reports_the_version_and_acts_on_a_release(monkeypatch):
    sent, released = {}, []
    monkeypatch.setenv("SPLOUCH_VERSION", "v1")
    monkeypatch.setattr(
        cloud_node,
        "_call",
        lambda m, p, body=None, **k: sent.update(body or {}) or {"update_to": "v2"},
    )
    monkeypatch.setattr(cloud_node, "update_node", released.append)
    cloud_node.heartbeat([])
    assert sent["version"] == "v1" and released == ["v2"]


def test_the_panel_rolls_out_only_a_version_a_node_can_pull(pg, monkeypatch):
    from starlette.datastructures import FormData

    reg = cloud_control.cloud_registry
    reg.heartbeat("ca1", 1, [], host="https://ca1.example", version="v2026.10.1")
    for version, started in (
        ("latest", False),
        ("feature/x", False),
        ("v2026.10.2", True),
    ):
        form = FormData({"action": "rollout_start", "version": version})
        cloud_control._admin_action(form, None)
        assert bool(reg.rollout()) is started, version


def test_the_panel_says_where_a_rollout_stands(pg, monkeypatch):
    monkeypatch.setenv("DEPLOY_WEBHOOK_URL", "http://host:9000/deploy")
    reg = cloud_control.cloud_registry
    reg.heartbeat("ca1", 1, [], host="https://ca1.example", version="v2026.10.1")
    reg.start_rollout("v2026.10.2")
    html = _admin_get(monkeypatch)
    assert "Rolling out v2026.10.2 — updating ca1" in html
    assert "v2026.10.1 → v2026.10.2" in html, "the Nodes tab shows the move"
    assert 'value="rollout_stop"' in html


def test_a_console_frame_marks_the_meet_running(wired):
    meet_id = register(wired).frames[0]["data"]["meet_id"]
    asyncio.run(cs._forward("sid-1", "update_scoreboard", {"lane_time1": "58.10"}))
    ids, _, frames = cs._heartbeat_snapshot()
    assert ids == [meet_id] and meet_id in frames
    cloud_node.heartbeat(ids, {}, frames)
    (row,) = cloud_control.cloud_registry.list_meets()
    assert row["last_frame_at"] is not None
    assert cloud_control.cloud_registry.running(row)


def test_the_register_carries_session_days_and_the_pool_s_offset(wired):
    ws = FakeWS()
    data = {
        "key": wired,
        "meet_uid": "uid-1",
        "ticket": assign(wired)[1]["ticket"],
        "session_dates": ["2099-05-01", "2099-05-02"],
        "utc_offset_minutes": -240,
        **META,
    }
    asyncio.run(cs._on_relay_register(ws, "sid-1", data))
    (row,) = cloud_control.cloud_registry.list_meets()
    assert (
        row["session_dates"] == ["2099-05-01", "2099-05-02"]
        and row["utc_offset"] == -240
    )
    assert not cloud_control.cloud_registry.running(row), "connected ahead, not running"


def test_the_panel_tells_a_meet_in_progress_from_one_connected_ahead(pg, monkeypatch):
    import cloud_auth

    reg = cloud_control.cloud_registry
    reg.heartbeat("ca1", 1, [], host="https://ca1.example")
    key = cloud_auth.add_organizer("Club", region="ca")
    reg.register(
        key,
        "u",
        {**META, "session_dates": ["2099-01-01"], "utc_offset_minutes": 0},
        "ca1",
        1,
    )
    assert "connected ahead" in _admin_get(monkeypatch)


def test_the_rollout_line_escapes_node_names(pg, monkeypatch):
    monkeypatch.setenv("DEPLOY_WEBHOOK_URL", "http://host:9000/deploy")
    reg = cloud_control.cloud_registry
    reg.heartbeat("<b>x</b>", 1, [], host="https://x.example", version="v1")
    reg.start_rollout("v2026.10.2")
    html = _admin_get(monkeypatch)
    assert "<b>x</b>" not in html.split('id="rollout-state"')[1][:300]


def test_the_panel_schedules_with_the_browser_s_offset(pg):
    import datetime

    from starlette.datastructures import FormData

    # Always ahead of the clock: a fixed date turns into the past and starts now.
    year = datetime.datetime.now(datetime.UTC).year + 1
    reg = cloud_control.cloud_registry
    form = FormData(
        {
            "action": "rollout_start",
            "version": "v2026.10.2",
            "not_before": f"{year}-10-05T06:00:00.000Z",
            "force": "1",
        }
    )
    cloud_control._admin_action(form, None)
    r = reg.rollout()
    assert r["state"] == "scheduled" and r["force"] is True
    assert r["not_before"] == f"{year}-10-05T06:00:00+00:00"


@pytest.mark.parametrize(
    "status,expect",
    [
        (None, "No nightly backup yet"),
        (
            {"ok": True, "file": "splouch-x.dump", "bytes": 2048, "error": ""},
            "splouch-x.dump · 2.0 kB",
        ),
        (
            {
                "ok": False,
                "file": "splouch-x.dump",
                "bytes": 0,
                "error": "pg_dump failed",
            },
            "Failed",
        ),
        (
            {"ok": True, "file": "old.dump", "bytes": 1, "error": "", "old": True},
            "the backup has stopped running",
        ),
    ],
    ids=["none", "ok", "failed", "stale"],
)
def test_the_panel_shows_the_last_nightly_backup(
    pg, monkeypatch, tmp_path, status, expect
):
    import datetime

    path = tmp_path / "status.json"
    if status is not None:
        at = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
            days=3 if status.pop("old", False) else 0
        )
        path.write_text(json.dumps({**status, "at": at.strftime("%Y-%m-%dT%H:%M:%SZ")}))
    monkeypatch.setattr(cloud_control, "BACKUP_STATUS", str(path))
    html = _admin_get(monkeypatch)
    block = html.split('id="backup-last"')[1][:600]
    assert expect in block
    assert ("text-danger" in block) is (
        expect in ("Failed", "the backup has stopped running")
    )
