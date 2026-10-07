"""Sending a heat notification: APNs for iOS, Firebase Cloud Messaging for Android.

The half of follow-a-swimmer that talks to Apple and Google (docs/app.md §10);
`cloud_follows` decides *what* to send and *when*. Each node sends for the meets it
carries, so a token and the names it follows never leave the meet's region except
to the platform that delivers the notification.

Configured per node, in `cloud/.env` (docs/cloud.md). Either half may be missing:
a node without the APNs key simply offers no bell to an iPhone (`platforms()` →
`GET /meet/{id}/config` → `push`).

* **APNs** — a token-based key (`.p8`) from the Apple developer account:
  `APNS_KEY_FILE`, `APNS_KEY_ID`, `APNS_TEAM_ID`, and `APNS_TOPIC`, the app's
  bundle id. HTTP/2 only, so `httpx` with `h2`; the provider token is an ES256 JWT,
  reused for 50 minutes (Apple refuses one older than an hour, and one renewed
  more often than every 20).
* **FCM** — the HTTP v1 API with a service account's JSON key:
  `FCM_SERVICE_ACCOUNT_FILE`. The account's RS256 JWT is traded for an OAuth access
  token, kept until shortly before it expires.

A send never raises: it answers `ok`, `gone` (the token is dead — uninstalled,
or registered against the other APNs environment — and its follows are dropped) or
`failed` (anything else; the notification is lost, the follow is kept).
"""

import contextlib
import json
import os
import time
from typing import Any

APNS_HOSTS = {
    False: "https://api.push.apple.com",
    True: "https://api.sandbox.push.apple.com",
}
FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
# A heat notification is worth nothing once the heat has been swum: either
# platform may drop it after this rather than deliver it late.
EXPIRY_SECS = 15 * 60
_APNS_TOKEN_SECS = 50 * 60
_TIMEOUT = 10.0

OK, GONE, FAILED = "ok", "gone", "failed"

_state: dict[str, Any] = {
    "client": None,
    "apns_jwt": None,
    "apns_at": 0.0,
    "fcm": None,
    # Why the last send did not answer `ok`: what Apple or Google said, or the
    # exception. Read by `send` into the counters below.
    "reason": "",
}

# What this worker has sent since it started, per platform: the admin's Debug tab
# sums them per node (heartbeat → `push_stats`), so "nobody gets notifications"
# and "one phone does not" read differently. In memory only, counts and the last
# reason — never a token.
_stats: dict[str, dict[str, Any]] = {
    p: {OK: 0, GONE: 0, FAILED: 0, "last_reason": "", "last_at": 0.0}
    for p in ("apns", "fcm")
}


def _env(name):
    return os.environ.get(name, "").strip()


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def apns_configured():
    return bool(
        _env("APNS_KEY_ID")
        and _env("APNS_TEAM_ID")
        and _env("APNS_TOPIC")
        and _env("APNS_KEY_FILE")
        and os.path.isfile(_env("APNS_KEY_FILE"))
    )


def fcm_configured():
    path = _env("FCM_SERVICE_ACCOUNT_FILE")
    return bool(path and os.path.isfile(path))


def platforms():
    """The platforms this node can notify — `GET /meet/{id}/config` → `push`."""
    out = []
    if apns_configured():
        out.append("apns")
    if fcm_configured():
        out.append("fcm")
    return out


def _client():
    if _state["client"] is None:
        import httpx  # the worker's only HTTP/2 user; imported when first needed

        _state["client"] = httpx.AsyncClient(http2=True, timeout=_TIMEOUT)
    return _state["client"]


async def close():
    client, _state["client"] = _state["client"], None
    if client is not None:
        await client.aclose()


def _apns_jwt(now):
    if _state["apns_jwt"] and now - _state["apns_at"] < _APNS_TOKEN_SECS:
        return _state["apns_jwt"]
    import jwt

    token = jwt.encode(
        {"iss": _env("APNS_TEAM_ID"), "iat": int(now)},
        _read(_env("APNS_KEY_FILE")),
        algorithm="ES256",
        headers={"kid": _env("APNS_KEY_ID")},
    )
    _state["apns_jwt"], _state["apns_at"] = token, now
    return token


def apns_request(note, sandbox, now=None):
    """`(url, headers, body)` for one APNs alert. Split out so a test can read it."""
    now = time.time() if now is None else now
    body = {
        "aps": {
            "alert": {"title": note["title"], "body": note["body"]},
            "sound": "default",
            # One thread per meet in Notification Center, and Time Sensitive: a
            # heat in five minutes is what that level exists for. The app carries
            # the entitlement; a user can still turn it off per app.
            "thread-id": note["meet_id"],
            "interruption-level": "time-sensitive",
            "relevance-score": 1.0 if note["kind"] == "selected" else 0.8,
        },
        "meet_id": note["meet_id"],
        "event": note["event"],
        "heat": note["heat"],
        "kind": note["kind"],
    }
    headers = {
        "authorization": f"bearer {_apns_jwt(now)}",
        "apns-topic": _env("APNS_TOPIC"),
        "apns-push-type": "alert",
        "apns-priority": "10",
        "apns-expiration": str(int(now) + EXPIRY_SECS),
        # The console's heat replaces the "in 5 minutes" one for the same heat.
        "apns-collapse-id": note["collapse"][:64],
    }
    return (
        f"{APNS_HOSTS[bool(sandbox)]}/3/device/{note['token']}",
        headers,
        json.dumps(body, ensure_ascii=False).encode(),
    )


async def _send_apns(note, sandbox):
    url, headers, body = apns_request(note, sandbox)
    try:
        resp = await _client().post(url, headers=headers, content=body)
    except Exception as e:
        print(f"[push] apns: {e!r}", flush=True)
        _state["reason"] = repr(e)
        return FAILED
    if resp.status_code == 200:
        return OK
    reason = ""
    with contextlib.suppress(Exception):
        reason = resp.json().get("reason", "")
    _state["reason"] = f"{resp.status_code} {reason}".strip()
    # 410: uninstalled. BadDeviceToken / DeviceTokenNotForTopic: a token from the
    # other environment or another app — never going to work against this key.
    if resp.status_code == 410 or reason in (
        "BadDeviceToken",
        "DeviceTokenNotForTopic",
    ):
        return GONE
    print(f"[push] apns {resp.status_code} {reason}", flush=True)
    return FAILED


async def _fcm_token(now):
    cached = _state["fcm"]
    if cached and cached["exp"] - 60 > now:
        return cached
    import jwt

    account = json.loads(_read(_env("FCM_SERVICE_ACCOUNT_FILE")) or "{}")
    assertion = jwt.encode(
        {
            "iss": account.get("client_email", ""),
            "scope": FCM_SCOPE,
            "aud": account.get("token_uri", "https://oauth2.googleapis.com/token"),
            "iat": int(now),
            "exp": int(now) + 3600,
        },
        account.get("private_key", ""),
        algorithm="RS256",
    )
    resp = await _client().post(
        account.get("token_uri", "https://oauth2.googleapis.com/token"),
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
    )
    resp.raise_for_status()
    got = resp.json()
    _state["fcm"] = {
        "token": got["access_token"],
        "exp": now + int(got.get("expires_in", 3600)),
        "project": account.get("project_id", ""),
    }
    return _state["fcm"]


def fcm_message(note):
    """The FCM v1 body for one notification. Data only, high priority: the app
    builds the notification itself, in the channel for its kind, so a spectator
    can tune "upcoming" and "on the console" apart in Android's settings."""
    return {
        "message": {
            "token": note["token"],
            "android": {
                "priority": "HIGH",
                "ttl": f"{EXPIRY_SECS}s",
                "collapse_key": note["collapse"],
            },
            "data": {
                "title": note["title"],
                "body": note["body"],
                "meet_id": note["meet_id"],
                "event": str(note["event"]),
                "heat": str(note["heat"]),
                "kind": note["kind"],
            },
        }
    }


async def _send_fcm(note):
    now = time.time()
    try:
        auth = await _fcm_token(now)
        resp = await _client().post(
            f"https://fcm.googleapis.com/v1/projects/{auth['project']}/messages:send",
            headers={"authorization": f"Bearer {auth['token']}"},
            json=fcm_message(note),
        )
    except Exception as e:
        print(f"[push] fcm: {e!r}", flush=True)
        _state["reason"] = repr(e)
        return FAILED
    if resp.status_code == 200:
        return OK
    status = ""
    try:
        err = resp.json().get("error", {})
        status = err.get("status", "")
        for detail in err.get("details", []):
            status = detail.get("errorCode", status)
    except Exception:
        pass
    _state["reason"] = f"{resp.status_code} {status}".strip()
    if status in ("UNREGISTERED", "NOT_FOUND") or (
        resp.status_code == 400 and status == "INVALID_ARGUMENT"
    ):
        return GONE
    print(f"[push] fcm {resp.status_code} {status}", flush=True)
    return FAILED


async def send(note):
    """Deliver one notification; `ok`, `gone` or `failed`. `note` carries `token`,
    `platform` (`apns` / `fcm`), `sandbox`, `meet_id`, `event`, `heat`, `kind`,
    `title`, `body` and `collapse`."""
    _state["reason"] = ""
    result = await _deliver(note)
    counts = _stats.get(note["platform"])
    if counts is not None:
        counts[result] += 1
        if result != OK:
            counts["last_reason"] = _state["reason"] or "not configured"
            counts["last_at"] = time.time()
    return result


def stats():
    """This worker's counters, for the heartbeat."""
    return {p: dict(c) for p, c in _stats.items()}


async def _deliver(note):
    if note["platform"] == "apns":
        if not apns_configured():
            return FAILED
        return await _send_apns(note, note.get("sandbox"))
    if note["platform"] == "fcm":
        if not fcm_configured():
            return FAILED
        return await _send_fcm(note)
    return GONE


# ── Diagnostics (admin → Debug → App checks) ──────────────────────────────────
# Run on a node at the admin's request (cloud_node.run_diag), never on their own.

# A token no device has: Apple answers `BadDeviceToken` to it only once the key,
# team and topic are accepted, so that answer is the "credentials work" one.
_DUMMY_APNS_TOKEN = "0" * 64
_APNS_KEY_OK = ("BadDeviceToken", "DeviceTokenNotForTopic")


async def check():
    """Whether each configured platform's credentials are accepted, without a phone.

    `{platform: {"configured", "ok", "detail"}}`. APNs: a send to a token that does
    not exist, which Apple refuses for the token only when the key is good (in both
    environments — one key serves both). FCM: an access token, then a
    `validate_only` send to a topic, which Google checks fully but delivers nowhere.
    """
    out = {}
    if apns_configured():
        envs = {}
        for sandbox in (False, True):
            note = {
                "token": _DUMMY_APNS_TOKEN,
                "meet_id": "diag",
                "event": 0,
                "heat": 0,
                "kind": "diag",
                "title": "",
                "body": "",
                "collapse": "diag",
            }
            try:
                url, headers, body = apns_request(note, sandbox)
                resp = await _client().post(url, headers=headers, content=body)
                reason = ""
                with contextlib.suppress(Exception):
                    reason = resp.json().get("reason", "")
                envs["sandbox" if sandbox else "production"] = (
                    reason in _APNS_KEY_OK,
                    f"{resp.status_code} {reason}".strip(),
                )
            except Exception as e:
                envs["sandbox" if sandbox else "production"] = (False, repr(e))
        out["apns"] = {
            "configured": True,
            "ok": all(ok for ok, _ in envs.values()),
            "detail": "; ".join(f"{env}: {d}" for env, (_, d) in envs.items()),
        }
    else:
        out["apns"] = {"configured": False, "ok": False, "detail": ""}
    if fcm_configured():
        try:
            auth = await _fcm_token(time.time())
            resp = await _client().post(
                f"https://fcm.googleapis.com/v1/projects/{auth['project']}/messages:send",
                headers={"authorization": f"Bearer {auth['token']}"},
                json={
                    "validate_only": True,
                    "message": {"topic": "splouch-diag", "data": {"kind": "diag"}},
                },
            )
            status = ""
            with contextlib.suppress(Exception):
                status = resp.json().get("error", {}).get("status", "")
            out["fcm"] = {
                "configured": True,
                "ok": resp.status_code == 200,
                "detail": f"{resp.status_code} {status}".strip()
                + f" (project {auth['project']})",
            }
        except Exception as e:
            out["fcm"] = {"configured": True, "ok": False, "detail": repr(e)}
    else:
        out["fcm"] = {"configured": False, "ok": False, "detail": ""}
    return out


def parse_support_token(text):
    """`apns:production:<hex>`, `apns:sandbox:<hex>` or `fcm:<token>` — what the
    apps show on a long press of their version (docs/app.md `N-10`) — as
    `(platform, sandbox, token)`, or None."""
    parts = (text or "").strip().split(":", 2)
    if len(parts) == 3 and parts[0] == "apns" and parts[1] in ("production", "sandbox"):
        token = parts[2].strip()
        if token and all(c in "0123456789abcdefABCDEF" for c in token):
            return "apns", parts[1] == "sandbox", token
    if len(parts) >= 2 and parts[0] == "fcm":
        # FCM tokens hold ':' themselves: everything after the prefix is the token.
        token = text.strip()[len("fcm:") :]
        if token and len(token) <= 4096 and not any(c.isspace() for c in token):
            return "fcm", False, token
    return None


async def send_test(text, title, body):
    """One fixed test notification to a token pasted by the admin. Not counted in
    the node's numbers, and the token is not kept. `{ok, result, detail}`."""
    parsed = parse_support_token(text)
    if parsed is None:
        return {"ok": False, "result": FAILED, "detail": "unrecognised token"}
    platform, sandbox, token = parsed
    # No meet: an empty `meet_id` is what tells the app this is a test, so a tap
    # opens the app and nothing else (docs/app.md `N-10`).
    note = {
        "token": token,
        "platform": platform,
        "sandbox": sandbox,
        "meet_id": "",
        "event": 0,
        "heat": 0,
        "kind": "selected",
        "title": title,
        "body": body,
        "collapse": "diag",
    }
    _state["reason"] = ""
    result = await _deliver(note)
    return {"ok": result == OK, "result": result, "detail": _state["reason"]}
