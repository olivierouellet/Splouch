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

_state: dict[str, Any] = {"client": None, "apns_jwt": None, "apns_at": 0.0, "fcm": None}


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
        return FAILED
    if resp.status_code == 200:
        return OK
    reason = ""
    with contextlib.suppress(Exception):
        reason = resp.json().get("reason", "")
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
    if note["platform"] == "apns":
        if not apns_configured():
            return FAILED
        return await _send_apns(note, note.get("sandbox"))
    if note["platform"] == "fcm":
        if not fcm_configured():
            return FAILED
        return await _send_fcm(note)
    return GONE
