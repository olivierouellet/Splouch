"""Relay tickets: the control plane's word that a Pi may publish a meet on a worker.

`POST /api/assign` on the control plane picks a worker for a meet and hands the Pi
a ticket for it; the Pi presents the ticket with its key when it registers on that
worker (docs/architecture/scaling.md). The worker checks the ticket itself — no
call needed — so a ticket also lets a Pi back in while the control plane is down.

A ticket names the meet, the node and worker, the organizer, and a hash of the
relay key, and expires after one meet day. It is signed with `NODE_SECRET`, which
only the control plane and the nodes hold:

    base64url(json payload) "." base64url(HMAC-SHA256(secret, payload))

Not encrypted: nothing in it is secret, and the key is only there as a hash, so a
ticket alone publishes nothing — the worker still wants the key that hashes to it.
"""

import base64
import hashlib
import hmac
import json
import time

TTL_SECONDS = 24 * 3600


def key_hash(key):
    return hashlib.sha256(str(key).encode()).hexdigest()[:16]


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(secret, meet_id, node, worker, key, organizer="", now=None):
    payload = {
        "m": meet_id,
        "n": node,
        "w": int(worker),
        "k": key_hash(key),
        "o": organizer,
        "e": int((now or time.time()) + TTL_SECONDS),
    }
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return f"{_b64(body)}.{_b64(mac)}"


def verify(secret, token, now=None):
    """The ticket's payload, or None when it is malformed, forged or expired."""
    if not secret or not isinstance(token, str) or token.count(".") != 1:
        return None
    body_b64, mac_b64 = token.split(".")
    try:
        body, mac = _unb64(body_b64), _unb64(mac_b64)
    except ValueError:
        return None
    want = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, want):
        return None
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    if not isinstance(payload, dict) or payload.get("e", 0) < (now or time.time()):
        return None
    return payload
