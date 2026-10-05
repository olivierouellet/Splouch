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
* **attendance** — joins are counted on this node (`cloud_attendance`), and only
  the numbers travel, with worker 1's heartbeat; the visitor ids stay here.

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

import cloud_attendance
from cloud_metrics import CONTROL_ERRORS

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
        CONTROL_ERRORS.inc()
        raise ControlError(f"HTTP {e.code}") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        CONTROL_ERRORS.inc()
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
    """Attendance for the Pi's Cloud tab, from this node's own store:
    `{"enabled", "counts"}`. No call: it answers while the control plane is down."""
    if not analytics_enabled():
        return {"enabled": False}
    return {"enabled": True, "counts": cloud_attendance.counts(meet_id)}


def wg_pubkey():
    """This box's WireGuard public key, when the installer left one to report."""
    path = os.environ.get("WG_PUBKEY_FILE", "/etc/splouch/wg-public.key")
    with contextlib.suppress(OSError), open(path, encoding="utf-8") as f:
        return f.read().strip()
    return ""


def version():
    """The image tag this node runs (`SPLOUCH_VERSION`, set by cloud_deploy.py)."""
    return os.environ.get("SPLOUCH_VERSION", "")


def update_node(target):
    """A rollout released this node to `target`: worker 1 asks the node's own deploy
    webhook for it, once. The webhook restarts every container here, this one too;
    the node is done when its heartbeat reports `target`."""
    if worker_index() != 1 or _settings.get("deploying") == target:
        return
    url = os.environ.get("DEPLOY_WEBHOOK_URL", "")
    secret = os.environ.get("DEPLOY_WEBHOOK_SECRET", "")
    if not url or not secret:
        print(
            "[node] rollout reached this node, but no deploy webhook is set", flush=True
        )
        return
    req = urllib.request.Request(
        url, data=json.dumps({"version": target}).encode(), method="POST"
    )
    req.add_header("X-Deploy-Token", secret)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5):
            pass
    except (urllib.error.URLError, OSError) as e:
        print(f"[node] deploy webhook did not answer: {e}", flush=True)
        return
    _settings["deploying"] = target
    print(f"[node] updating this node to {target}", flush=True)


# Worker 1 of each node sends the node's attendance numbers — the store is shared,
# so one sender is enough — on every ATTENDANCE_EVERY-th heartbeat, and prunes it
# once a day.
ATTENDANCE_EVERY = 3
_beats = {"n": 0, "pruned": 0.0}


def _attendance_due():
    if worker_index() != 1:
        return False
    _beats["n"] += 1
    return _beats["n"] % ATTENDANCE_EVERY == 1


def heartbeat(live_ids, attendees=None, frames=None):
    """Report the meets this worker holds, with each one's attendee count. Returns
    `{"retired": [ids], "moves": [{"meet_id", "url"}]}` — the moves are meets the
    admin moved off this worker.

    Worker 1 also sends the node's attendance numbers now and then: distinct
    visitors per meet and window, never the ids behind them.
    """
    attendance = None
    if _attendance_due():
        attendance = cloud_attendance.active_counts()
        if time.time() - _beats["pruned"] >= 24 * 3600:
            cloud_attendance.prune()
            _beats["pruned"] = time.time()
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
            "attendance": attendance,
            "version": version(),
            "frames": frames or {},
        },
    )
    if not isinstance(result, dict):
        return {"retired": [], "moves": []}
    _settings["analytics_enabled"] = bool(result.get("analytics_enabled"))
    if result.get("update_to"):
        update_node(result["update_to"])
    return {"retired": result.get("retired") or [], "moves": result.get("moves") or []}


async def heartbeat_loop(snapshot, on_moves=None):
    """Report every HEARTBEAT_SECS. `snapshot()` returns the meets held right now,
    `{meet_id: attendees}` and `{meet_id: last board frame}`; `on_moves(moves)`
    lets go of the ones moved away."""
    while True:
        try:
            result = await run_in_threadpool(heartbeat, *snapshot())
            if on_moves and result["moves"]:
                await on_moves(result["moves"])
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
    """Write queued joins to this node's store in one transaction. Blocking — run
    off the loop. The ids stay on the node (`cloud_attendance`)."""
    rows = []
    with contextlib.suppress(queue.Empty):
        while True:
            rows.append(_analytics_queue.get_nowait())
    if rows:
        cloud_attendance.record(rows)


async def analytics_flush_loop():
    while True:
        await asyncio.sleep(_ANALYTICS_FLUSH_SECS)
        try:
            await run_in_threadpool(flush_analytics)
        except Exception as e:
            print(f"[node] analytics flush failed: {e!r}", flush=True)
