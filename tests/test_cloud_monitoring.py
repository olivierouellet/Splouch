"""Monitoring (docs/architecture/scaling.md, *Monitoring*): `/metrics` on every
process, counts only and private; the stack in cloud/monitoring/ that reads it."""

import asyncio
import json
import os

import pytest
import yaml

import cloud_metrics
import cloud_server as cs

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MON = os.path.join(REPO, "cloud", "monitoring")


def get(app, path, client):
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
        "headers": [(b"host", b"x")],
        "client": (client, 1234),
        "server": ("x", 5000),
    }
    sent = {"body": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    asyncio.run(app(scope, receive, send))
    return sent["status"], sent["body"].decode()


# ── /metrics ───────────────────────────────────────────────────────────────────


def test_a_worker_s_metrics_reach_the_private_network():
    status, body = get(cs.app, "/metrics", "172.18.0.5")
    assert status == 200
    for name in (
        "splouch_meets_live",
        'splouch_sockets{page="scoreboard"}',
        "splouch_frames_total",
        "splouch_sends_total",
        "splouch_event_loop_lag_seconds",
        'splouch_info{role="worker"',
    ):
        assert name in body, name


@pytest.mark.parametrize("client", ["8.8.8.8", "2606:4700:4700::1111", "not-an-ip"])
def test_and_nobody_else(client):
    """Behind Caddy the client is the visitor: a public address gets nothing."""
    assert get(cs.app, "/metrics", client)[0] == 404


def test_they_are_counts_and_never_ids(monkeypatch):
    monkeypatch.setattr(cs, "_meets", {"secret-meet-id": {"name": "Coupe"}})
    phone = object()
    cs.manager.join(phone, cs._ch("scoreboard", "secret-meet-id"))
    try:
        _, body = get(cs.app, "/metrics", "10.0.0.2")
    finally:
        cs.manager.channels.pop(cs._ch("scoreboard", "secret-meet-id"), None)
    assert "splouch_meets_live 1.0" in body
    assert 'splouch_sockets{page="scoreboard"} 1.0' in body
    assert "secret-meet-id" not in body and "Coupe" not in body


def test_the_lag_loop_measures_how_late_a_timer_fires():
    gauge = cloud_metrics.LOOP_LAG

    async def run():
        task = asyncio.create_task(cloud_metrics.lag_loop(gauge, interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()

    gauge.set(-1)
    asyncio.run(run())
    assert gauge._value.get() >= 0


@pytest.mark.usefixtures("pg")
def test_the_control_plane_s_metrics_count_the_registry():
    import cloud_control
    import cloud_registry

    cloud_registry.heartbeat("ca1", 1, [], host="https://ca1.example")
    cloud_registry.restore({"old": {"name": "Old"}})
    status, body = get(cloud_control.app, "/metrics", "10.0.0.3")
    assert status == 200
    assert 'splouch_nodes{state="up"} 1.0' in body
    assert 'splouch_meets{state="retained"} 1.0' in body
    assert get(cloud_control.app, "/metrics", "1.1.1.1")[0] == 404


# ── The stack ──────────────────────────────────────────────────────────────────


def load(*parts):
    with open(os.path.join(MON, *parts), encoding="utf-8") as f:
        return f.read()


def test_prometheus_scrapes_the_control_plane_workers_host_and_containers():
    jobs = {
        j["job_name"]: j
        for j in yaml.safe_load(load("prometheus", "prometheus.yml"))["scrape_configs"]
    }
    assert set(jobs) == {"control", "workers", "node", "containers"}
    assert jobs["control"]["static_configs"][0]["targets"] == ["control:8000"]
    assert jobs["workers"]["file_sd_configs"][0]["files"] == [
        "/etc/prometheus/targets/workers.json"
    ]


def test_the_alerts_are_the_ones_the_plan_names():
    doc = yaml.safe_load(load("grafana", "provisioning", "alerting", "splouch.yml"))
    rules = {r["uid"]: r for r in doc["groups"][0]["rules"]}

    def threshold(uid):
        cond = rules[uid]["data"][1]["model"]["conditions"][0]["evaluator"]
        return cond["type"], cond["params"][0], rules[uid]["for"]

    assert threshold("splouch-worker-cpu") == ("gt", 0.7, "5m")
    assert threshold("splouch-loop-lag") == ("gt", 0.1, "2m")
    assert threshold("splouch-target-down") == ("lt", 1, "2m")
    assert threshold("splouch-disk") == ("gt", 0.8, "10m")
    receiver = doc["contactPoints"][0]["receivers"][0]
    assert receiver["type"] == "pushover" and receiver["settings"]["priority"] == 2
    assert receiver["settings"]["apiToken"] == "${PUSHOVER_TOKEN}", (
        "secrets come from .env"
    )


def test_the_dashboard_is_valid_and_reads_the_one_datasource():
    dash = json.loads(load("grafana", "dashboards", "splouch.json"))
    assert dash["uid"] == "splouch-overview"
    uids = {t["datasource"]["uid"] for p in dash["panels"] for t in p["targets"]}
    assert uids == {"prometheus"}


def test_the_stack_keeps_its_admin_doors_shut():
    services = yaml.safe_load(load("docker-compose.yml"))["services"]
    assert services["uptime-kuma"]["ports"] == ["127.0.0.1:3001:3001"], (
        "Kuma's first visitor creates its admin: loopback only until set up"
    )
    assert "ports" not in services["prometheus"] and "ports" not in services["grafana"]
    assert services["grafana"]["environment"]["GF_USERS_ALLOW_SIGN_UP"] == "false"
    assert ":?" in services["grafana"]["environment"]["GF_SECURITY_ADMIN_PASSWORD"]


def test_the_watchdog_pings_only_while_prometheus_is_healthy():
    cmd = yaml.safe_load(load("docker-compose.yml"))["services"]["watchdog"]["command"][
        2
    ]
    assert "prometheus:9090/-/healthy" in cmd and "&& wget" in cmd
