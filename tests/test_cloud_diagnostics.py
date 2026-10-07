"""/admin → Debug: diagnostics run on a node, app-link checks, push counters and tests.

A node cannot be called; it calls. So a diagnostic is a job queued on the control
plane, handed to the node's worker 1 in its next heartbeat answer, and posted back
on the internal API. These tests pin that round trip, the checks' verdicts from
canned answers, and what the push side counts and accepts — nothing here reaches
Apple, Google or a real node.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

import cloud_control as cc
import cloud_node
import cloud_push

# ── The job queue ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _empty_queue(monkeypatch):
    monkeypatch.setattr(cc, "_diag_queue", {})
    monkeypatch.setattr(cc, "_diag_jobs", {})
    monkeypatch.setattr(cc, "_push_stats", {})


def test_a_job_goes_to_its_node_once_and_comes_back():
    job_id = cc.diag_enqueue("us1", "push_check", {})
    assert cc.diag_take("ca1") == []
    jobs = cc.diag_take("us1")
    assert [j["id"] for j in jobs] == [job_id]
    assert cc.diag_take("us1") == []  # handed over once
    assert cc._diag_jobs[job_id]["state"] == "running"
    assert cc.diag_store("us1", job_id, {"ok": True})
    assert cc.route_diag_result(job_id) == {"state": "done", "result": {"ok": True}}


def test_another_node_cannot_answer_for_a_job():
    job_id = cc.diag_enqueue("us1", "info", {})
    assert not cc.diag_store("ca1", job_id, {"ok": True})
    assert cc._diag_jobs[job_id]["state"] == "queued"


def test_a_job_nobody_takes_ends_as_no_answer(monkeypatch):
    job_id = cc.diag_enqueue("gone1", "info", {})
    later = cc._diag_jobs[job_id]["at"] + cc._DIAG_PICKUP_SECS + 1
    monkeypatch.setattr(cc.time, "time", lambda: later)
    result = cc.route_diag_result(job_id)
    assert result == {"state": "done", "result": {"ok": False, "error": "no_answer"}}
    assert cc.diag_take("gone1") == []


def test_a_node_queue_is_bounded():
    for _ in range(cc._DIAG_QUEUE_MAX):
        assert cc.diag_enqueue("us1", "info", {})
    assert cc.diag_enqueue("us1", "info", {}) is None


def test_push_counters_are_summed_per_node_from_fresh_reports():
    now = 1_000_000.0
    cc._push_stats[("ca1", 1)] = (
        now,
        {"apns": {"ok": 3, "gone": 1, "failed": 0, "last_reason": "", "last_at": 0}},
    )
    cc._push_stats[("ca1", 2)] = (
        now,
        {
            "apns": {
                "ok": 2,
                "gone": 0,
                "failed": 1,
                "last_reason": "403 InvalidProviderToken",
                "last_at": now - 5,
            }
        },
    )
    cc._push_stats[("ca1", 3)] = (now - 600, {"apns": {"ok": 99}})  # stale
    apns = cc.push_stats_by_node(now)["ca1"]["apns"]
    assert (apns["ok"], apns["gone"], apns["failed"]) == (5, 1, 1)
    assert apns["last_reason"] == "403 InvalidProviderToken"


# ── App links ─────────────────────────────────────────────────────────────────

FP = "14:6D:E9:83:C5:73:06:50:D8:EE:B9:95:2F:34:FC:64:16:A0:83:42:E6:1D:BE:A8:8A:04:96:B2:3F:CF:44:E5"


def _fetcher(answers):
    def fetch(url, timeout=8):
        for prefix, answer in answers.items():
            if url.startswith(prefix):
                return answer
        return 404, "", b""

    return fetch


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ANDROID_CERT_FINGERPRINTS", FP)
    monkeypatch.setenv("STORE_URL_ANDROID", "https://play.google.com/x")
    monkeypatch.setenv("STORE_URL_IOS", "https://apps.apple.com/x")
    monkeypatch.setattr(cc, "_applinks_file", dict)


def _by_key(checks):
    return {c["key"]: c for c in checks}


def test_everything_right_reads_all_green(configured):
    pkg = cc.ANDROID_PACKAGE
    assetlinks = json.dumps(
        [{"target": {"package_name": pkg, "sha256_cert_fingerprints": [FP]}}]
    )
    google = json.dumps(
        {"statements": [{"target": {"androidApp": {"packageName": pkg}}}]}
    )
    apple = json.dumps({"applinks": {"details": [{"appIDs": list(cc.IOS_APP_IDS)}]}})
    checks = cc.applinks_check(
        "s.example",
        _fetcher(
            {
                "https://s.example/.well-known/assetlinks.json": (
                    200,
                    "application/json",
                    assetlinks.encode(),
                ),
                "https://digitalassetlinks.googleapis.com/": (
                    200,
                    "application/json",
                    google.encode(),
                ),
                "https://s.example/.well-known/apple-app-site-association": (
                    200,
                    "application/json",
                    b"{}",
                ),
                "https://app-site-association.cdn-apple.com/a/v1/s.example": (
                    200,
                    "application/json",
                    apple.encode(),
                ),
            }
        ),
    )
    assert all(c["ok"] for c in checks), checks


def test_a_redirect_fails_the_android_file_and_says_so(configured):
    checks = _by_key(
        cc.applinks_check(
            "s.example",
            _fetcher({"https://s.example/.well-known/assetlinks.json": (301, "", b"")}),
        )
    )
    assert not checks["assetlinks"]["ok"]
    assert "redirect" in checks["assetlinks"]["detail"]


def test_apples_copy_missing_our_app_is_named(configured):
    apple = json.dumps({"applinks": {"details": [{"appIDs": ["OTHER.app"]}]}})
    checks = _by_key(
        cc.applinks_check(
            "s.example",
            _fetcher(
                {
                    "https://app-site-association.cdn-apple.com/": (
                        200,
                        "application/json",
                        apple.encode(),
                    )
                }
            ),
        )
    )
    assert not checks["apple_cdn"]["ok"]
    assert cc.IOS_APP_IDS[0] in checks["apple_cdn"]["detail"]


def test_no_fingerprint_is_blamed_on_the_setting(monkeypatch):
    for name in ("ANDROID_CERT_FINGERPRINTS", "STORE_URL_ANDROID", "STORE_URL_IOS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cc, "_applinks_file", dict)
    checks = _by_key(cc.applinks_check("s.example", _fetcher({})))
    assert "ANDROID_CERT_FINGERPRINTS" in checks["assetlinks"]["detail"]
    assert not checks["stores"]["ok"]


# ── Push: tokens, counters, key checks ────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "parsed"),
    [
        ("apns:production:ABCdef0123", ("apns", False, "ABCdef0123")),
        ("  apns:sandbox:abcdef  ", ("apns", True, "abcdef")),
        ("fcm:dQw4:APA91b-x_y", ("fcm", False, "dQw4:APA91b-x_y")),
        ("apns:production:not hex!", None),
        ("apns:prod:abcdef", None),
        ("fcm:", None),
        ("fcm:with space", None),
        ("abcdef", None),
    ],
)
def test_the_support_token_line_is_parsed_strictly(text, parsed):
    assert cloud_push.parse_support_token(text) == parsed


@pytest.fixture
def counters(monkeypatch):
    stats = {
        p: {"ok": 0, "gone": 0, "failed": 0, "last_reason": "", "last_at": 0.0}
        for p in ("apns", "fcm")
    }
    monkeypatch.setattr(cloud_push, "_stats", stats)
    monkeypatch.setattr(cloud_push, "apns_configured", lambda: True)
    return stats


def test_sends_are_counted_with_the_last_reason(monkeypatch, counters):
    answers = iter(
        [
            (cloud_push.OK, ""),
            (cloud_push.FAILED, "403 InvalidProviderToken"),
            (cloud_push.GONE, "410 Unregistered"),
        ]
    )

    async def fake(note, sandbox):
        result, reason = next(answers)
        cloud_push._state["reason"] = reason
        return result

    monkeypatch.setattr(cloud_push, "_send_apns", fake)
    for _ in range(3):
        asyncio.run(cloud_push.send({"platform": "apns", "token": "t"}))
    apns = cloud_push.stats()["apns"]
    assert (apns["ok"], apns["failed"], apns["gone"]) == (1, 1, 1)
    assert apns["last_reason"] == "410 Unregistered"


def test_a_test_send_is_not_counted_and_carries_no_meet(monkeypatch, counters):
    seen = {}

    async def fake(note, sandbox):
        seen.update(note, sandbox=sandbox)
        return cloud_push.OK

    monkeypatch.setattr(cloud_push, "_send_apns", fake)
    result = asyncio.run(cloud_push.send_test("apns:sandbox:abcd", "T", "B"))
    assert result["ok"]
    assert seen["meet_id"] == "" and seen["sandbox"] is True and seen["title"] == "T"
    assert cloud_push.stats()["apns"]["ok"] == 0


class _Client:
    def __init__(self, answers):
        self.answers = answers

    async def post(self, url, **_kwargs):
        for prefix, (status, payload) in self.answers.items():
            if url.startswith(prefix):
                return SimpleNamespace(status_code=status, json=lambda p=payload: p)
        raise AssertionError(url)


def test_apns_keys_are_good_when_apple_only_refuses_the_dummy_token(monkeypatch):
    monkeypatch.setattr(cloud_push, "apns_configured", lambda: True)
    monkeypatch.setattr(cloud_push, "fcm_configured", lambda: False)
    monkeypatch.setattr(cloud_push, "_apns_jwt", lambda now: "jwt")
    client = _Client(
        {
            "https://api.push.apple.com": (400, {"reason": "BadDeviceToken"}),
            "https://api.sandbox.push.apple.com": (400, {"reason": "BadDeviceToken"}),
        }
    )
    monkeypatch.setattr(cloud_push, "_client", lambda: client)
    assert asyncio.run(cloud_push.check())["apns"]["ok"]
    client.answers["https://api.push.apple.com"] = (
        403,
        {"reason": "InvalidProviderToken"},
    )
    result = asyncio.run(cloud_push.check())["apns"]
    assert not result["ok"] and "InvalidProviderToken" in result["detail"]


def test_fcm_keys_are_checked_with_a_validate_only_send(monkeypatch):
    monkeypatch.setattr(cloud_push, "apns_configured", lambda: False)
    monkeypatch.setattr(cloud_push, "fcm_configured", lambda: True)

    async def token(now):
        return {"token": "tok", "project": "splouch-x", "exp": now + 3600}

    monkeypatch.setattr(cloud_push, "_fcm_token", token)
    monkeypatch.setattr(
        cloud_push,
        "_client",
        lambda: _Client({"https://fcm.googleapis.com": (200, {})}),
    )
    result = asyncio.run(cloud_push.check())
    assert result["fcm"]["ok"] and "splouch-x" in result["fcm"]["detail"]
    assert result["apns"] == {"configured": False, "ok": False, "detail": ""}


# ── The node side ─────────────────────────────────────────────────────────────


def test_a_node_runs_only_the_log_sources_it_has(monkeypatch):
    monkeypatch.setattr(
        cloud_node, "_webhook_get", lambda path: pytest.fail("must not call")
    )
    job = {"id": "0" * 32, "kind": "logs", "params": {"source": "control"}}
    assert asyncio.run(cloud_node._diag_result(job)) == {
        "ok": False,
        "error": "unknown source",
    }


def test_a_job_with_a_strange_id_is_ignored(monkeypatch):
    monkeypatch.setattr(cloud_node, "_call", lambda *a, **k: pytest.fail("posted"))
    asyncio.run(cloud_node.run_diag({"id": "../heartbeat", "kind": "info"}))


def test_a_result_is_posted_to_its_job(monkeypatch):
    posted = {}

    async def result(job):
        return {"ok": True}

    monkeypatch.setattr(cloud_node, "_diag_result", result)
    monkeypatch.setattr(
        cloud_node,
        "_call",
        lambda method, path, body: posted.update(path=path, body=body),
    )
    asyncio.run(cloud_node.run_diag({"id": "a" * 32, "kind": "info"}))
    assert posted == {"path": "/internal/diag/" + "a" * 32, "body": {"ok": True}}
