"""Attendance stays on the node; the control plane sees numbers only.

Visitor ids are personal data in some places they are collected, and a meet's
visitors should stay in the region that carries it (docs/architecture/scaling.md).
So the node counts them (cloud/cloud_attendance.py) and its heartbeat carries
distinct counts per window — never an id.
"""

import datetime

import pytest

import cloud_attendance as att
import cloud_node
import cloud_paths


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(cloud_paths, "ANALYTICS_FILE", str(tmp_path / "analytics.db"))
    att.close()
    yield
    att.close()


NOW = datetime.datetime(2026, 10, 4, 12, 0)


def ts(**ago):
    return int((NOW - datetime.timedelta(**ago)).timestamp())


def test_distinct_visitors_per_window():
    att.record(
        [
            ("m1", "a", ts(minutes=10), "scoreboard"),
            ("m1", "a", ts(minutes=5), "results"),  # same phone again
            ("m1", "b", ts(hours=2), "scoreboard"),
            ("m1", "c", ts(days=3), "scoreboard"),
            ("m2", "a", ts(minutes=1), "scoreboard"),  # another meet
        ]
    )
    c = att.counts("m1", now=NOW)
    assert (c["1h"], c["3h"], c["24h"], c["7d"], c["all"]) == (1, 2, 2, 3, 3)


def test_only_recently_visited_meets_are_reported():
    att.record([("old", "a", ts(days=30), "s"), ("new", "a", ts(hours=1), "s")])
    assert set(att.active_counts(now=NOW)) == {"new"}


def test_the_prune_keeps_the_promise():
    att.record(
        [("m", "a", ts(days=att.RETENTION_DAYS + 1), "s"), ("m", "b", ts(days=1), "s")]
    )
    att.prune(now=NOW)
    assert att.counts("m", now=NOW)["all"] == 1


def test_the_store_is_safe_for_several_workers():
    att.record([("m", "a", ts(minutes=1), "s")])
    with att._lock:
        mode = att._conn().execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


def test_the_heartbeat_carries_numbers_never_ids(monkeypatch):
    sent = {}
    monkeypatch.setenv("WORKER", "1")
    monkeypatch.setattr(cloud_node, "_beats", {"n": 0, "pruned": 1e18})
    monkeypatch.setattr(
        cloud_node, "_call", lambda m, p, body=None, **k: sent.update(body or {}) or {}
    )
    att.record(
        [("m1", "device-secret-id", int(datetime.datetime.now().timestamp()), "s")]
    )
    cloud_node.heartbeat([])
    assert sent["attendance"]["m1"]["all"] == 1
    assert "device-secret-id" not in repr(sent)


def test_only_worker_1_sends_them(monkeypatch):
    sent = []
    monkeypatch.setenv("WORKER", "2")
    monkeypatch.setattr(
        cloud_node, "_call", lambda m, p, body=None, **k: sent.append(body) or {}
    )
    att.record([("m1", "a", int(datetime.datetime.now().timestamp()), "s")])
    for _ in range(cloud_node.ATTENDANCE_EVERY):
        cloud_node.heartbeat([])
    assert all(b["attendance"] is None for b in sent)


def test_the_pi_is_answered_from_the_node(monkeypatch):
    """No call: the Cloud tab's numbers keep coming while the control plane is down."""
    monkeypatch.setitem(cloud_node._settings, "analytics_enabled", True)

    def down(*a, **k):
        raise cloud_node.ControlError("down")

    monkeypatch.setattr(cloud_node, "_call", down)
    att.record([("m1", "a", int(datetime.datetime.now().timestamp()), "s")])
    assert cloud_node.counts("m1") == {"enabled": True, "counts": att.counts("m1")}


def test_counting_off_answers_off(monkeypatch):
    monkeypatch.setitem(cloud_node._settings, "analytics_enabled", False)
    assert cloud_node.counts("m1") == {"enabled": False}


# ── The control plane's numbers ────────────────────────────────────────────────


@pytest.mark.usefixtures("pg")
def test_the_control_plane_adds_nodes_and_forgets_swept_meets():
    import cloud_analytics
    import cloud_registry

    cloud_registry.restore({"m1": {"name": "Coupe"}})
    cloud_analytics.store("ca1", {"m1": {"1h": 3, "all": 10, "bogus": 99}})
    cloud_analytics.store("us1", {"m1": {"1h": 1, "all": 4}})  # moved across
    assert cloud_analytics.attendee_count("m1", "1h") == 4
    assert cloud_analytics.attendee_count("m1", "all") == 14
    assert cloud_analytics.attendee_count("m1", "bogus") == 0
    cloud_registry.delete("m1")
    cloud_analytics.forget_gone()
    assert cloud_analytics.attendee_count("m1", "all") == 0


@pytest.mark.usefixtures("pg")
def test_the_control_plane_has_no_id_table_in_use():
    """Nothing writes the old `analytics` table any more: no route takes ids."""
    import cloud_control

    paths = {getattr(r, "path", "") for r in cloud_control.app.routes}
    assert "/internal/analytics" not in paths
    assert not [p for p in paths if p.startswith("/internal/analytics/")]
