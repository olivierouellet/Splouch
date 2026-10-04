"""A worker's line to the control plane.

The worker (`cloud_server`) holds live meets in memory and owns nothing durable;
everything it must remember goes through here to the control plane's internal API
(`cloud_control`, docs/architecture/scaling.md):

* **register** — a Pi's key is checked there and the meet's metadata stored; the
  answer is the meet id. While the control plane is down, the ticket the Pi got
  from `/api/assign` is what lets it in (`cloud_server`, `cloud_ticket`).
* **schedule / retire** — best effort: a failure is logged and the next register
  or schedule from the Pi carries the same data again.
* **meet records** — a retained meet's pages are served from its record, fetched
  on demand and cached briefly.
* **heartbeat** — every few seconds, the meets this worker holds. The control plane
  retires the ones it no longer names, and answers with the settings a worker needs
  (whether attendance counting is on).
* **analytics** — joins are queued in memory and sent in batches.

Every call here blocks (plain `urllib`, like the deploy-webhook calls before it);
callers on the event loop wrap them in `run_in_threadpool`.
"""

import asyncio
import contextlib
import datetime
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request

from starlette.concurrency import run_in_threadpool

HEARTBEAT_SECS = 10
_ANALYTICS_FLUSH_SECS = 5
# A retained meet's record changes rarely (an admin edits its expiry, or it
# expires); a page view within this window reuses the last fetch.
_RECORD_TTL = 15.0
_MISSING_TTL = 5.0


class ControlError(Exception):
    """The control plane could not be reached, or failed."""


class Refused(Exception):
    """The control plane answered no (a bad or revoked key)."""


def node_name():
    return os.environ.get("NODE_NAME", "ca1")


def worker_index():
    return int(os.environ.get("WORKER", "1"))


def node_url():
    """This node's public base URL, which `/api/assign` builds a Pi's relay URL from."""
    return os.environ.get("NODE_URL", "").rstrip("/")


def _call(method, path, body=None, timeout=5):
    """One JSON call to the control plane. None on 404."""
    base = os.environ.get("CONTROL_URL", "").rstrip("/")
    if not base:
        raise ControlError("CONTROL_URL is not set")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {os.environ.get('NODE_SECRET', '')}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code in (401, 403):
            try:
                reason = json.loads(e.read()).get("reason", "")
            except Exception:
                reason = ""
            raise Refused(reason or f"HTTP {e.code}") from e
        raise ControlError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise ControlError(str(e)) from e


# ── Register ───────────────────────────────────────────────────────────────────


def register(key, meet_uid, meta, location=None):
    """Register a meet; returns `{"meet_id", "organizer", "connected_at",
    "schedule_data"}`. Raises Refused for a bad key, ControlError when the control
    plane cannot be reached."""
    return _call(
        "POST",
        "/internal/register",
        {
            "key": key,
            "meet_uid": meet_uid,
            "meta": meta,
            "node": node_name(),
            "worker": worker_index(),
            "location": location,
        },
    )


def _best_effort(what, method, path, body):
    try:
        _call(method, path, body)
    except (ControlError, Refused) as e:
        print(f"[node] {what} not reported: {e}", flush=True)


def schedule(meet_id, schedule_data):
    _best_effort(
        "schedule",
        "POST",
        f"/internal/meets/{meet_id}/schedule",
        {"schedule_data": schedule_data or None},
    )


def retire(meet_id):
    _best_effort(
        "disconnect",
        "POST",
        f"/internal/meets/{meet_id}/retire",
        {"node": node_name(), "worker": worker_index()},
    )


# ── Meet records ───────────────────────────────────────────────────────────────

_records = {}  # meet_id -> (record or None, fetched_at monotonic)
_records_lock = threading.Lock()


def remember(meet_id, record):
    """Seed the cache with a record this worker already has (a meet it just retired)."""
    with _records_lock:
        _records[meet_id] = (record, time.monotonic())


def fetch_meet(meet_id):
    """A meet's record from the control plane, or None. Cached briefly; while the
    control plane is unreachable the last record seen keeps serving."""
    if not meet_id:
        return None
    now = time.monotonic()
    with _records_lock:
        hit = _records.get(meet_id)
    if hit:
        rec, at = hit
        if now - at < (_RECORD_TTL if rec else _MISSING_TTL):
            return rec
    try:
        rec = _call("GET", f"/internal/meets/{meet_id}")
    except (ControlError, Refused):
        return hit[0] if hit else None
    if rec and rec.get("expires_at"):
        try:
            if (
                datetime.datetime.fromisoformat(rec["expires_at"])
                <= datetime.datetime.now()
            ):
                rec = None
        except ValueError:
            pass
    with _records_lock:
        _records[meet_id] = (rec, now)
        # Bound the cache: unknown ids are cheap to ask for and must not pile up.
        if len(_records) > 2048:
            for k in [k for k, (_, at) in _records.items() if now - at > _RECORD_TTL]:
                del _records[k]
    return rec


# ── Counts and heartbeat ───────────────────────────────────────────────────────

_settings = {"analytics_enabled": False}


def analytics_enabled():
    return _settings["analytics_enabled"]


def counts(meet_id):
    """Attendance for the Pi's Cloud tab: `{"enabled", "counts"}`."""
    try:
        return _call("GET", f"/internal/analytics/{meet_id}") or {"enabled": False}
    except (ControlError, Refused):
        return {"enabled": analytics_enabled(), "counts": {}}


def wg_pubkey():
    """This box's WireGuard public key, when the installer left one to report."""
    path = os.environ.get("WG_PUBKEY_FILE", "/etc/splouch/wg-public.key")
    with contextlib.suppress(OSError), open(path, encoding="utf-8") as f:
        return f.read().strip()
    return ""


def heartbeat(live_ids, attendees=None):
    """Report the meets this worker holds, with each one's attendee count. Returns
    the ids the control plane retired."""
    result = _call(
        "POST",
        "/internal/heartbeat",
        {
            "node": node_name(),
            "worker": worker_index(),
            "host": node_url(),
            "region": os.environ.get("NODE_REGION", ""),
            "workers": int(os.environ.get("NODE_WORKERS", "1")),
            "wg_pubkey": wg_pubkey(),
            "live": list(live_ids),
            "attendees": attendees or {},
        },
    )
    if isinstance(result, dict):
        _settings["analytics_enabled"] = bool(result.get("analytics_enabled"))
        return result.get("retired") or []
    return []


async def heartbeat_loop(snapshot):
    """Report every HEARTBEAT_SECS. `snapshot()` returns the meets held right now
    and `{meet_id: attendees}`."""
    while True:
        try:
            await run_in_threadpool(heartbeat, *snapshot())
        except (ControlError, Refused) as e:
            print(f"[node] heartbeat failed: {e}", flush=True)
        await asyncio.sleep(HEARTBEAT_SECS)


# ── Analytics ──────────────────────────────────────────────────────────────────

_analytics_queue = queue.Queue()


def log_connection(meet_id, visitor_id, namespace):
    """Queue one attendee join. Called from the WS handlers on the event loop, so
    it does no I/O — just a non-blocking enqueue, dropped while counting is off."""
    if not meet_id or not visitor_id or not analytics_enabled():
        return
    _analytics_queue.put(
        (
            meet_id,
            str(visitor_id)[:64],
            int(datetime.datetime.now().timestamp()),
            namespace,
        )
    )


def flush_analytics():
    """Send queued joins in one call. Blocking — run off the loop. A batch that
    fails is dropped rather than retried: counts are approximate by nature, and a
    queue that grows while the control plane is down is a memory leak."""
    rows = []
    with contextlib.suppress(queue.Empty):
        while True:
            rows.append(_analytics_queue.get_nowait())
    if not rows:
        return
    try:
        _call("POST", "/internal/analytics", {"rows": rows})
    except (ControlError, Refused) as e:
        print(f"[node] {len(rows)} attendance rows dropped: {e}", flush=True)


async def analytics_flush_loop():
    while True:
        await asyncio.sleep(_ANALYTICS_FLUSH_SECS)
        try:
            await run_in_threadpool(flush_analytics)
        except Exception as e:
            print(f"[node] analytics flush failed: {e!r}", flush=True)
