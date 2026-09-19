"""Opt-in attendance counting, in its own SQLite file.

Off unless the admin turns it on. One row per `join_meet`, keyed by a random
per-device id the mobile page keeps in localStorage — no IP, no personal data —
so "how many people in the last hour" is a COUNT(DISTINCT) over a window.

Split out of ``cloud_server`` because it owns everything it touches: its own
database, its own lock, its own queue and its own flush task. The only thing it
asks of the rest of the relay is whether the admin has enabled it.
"""
import asyncio
import datetime
import os
import queue
import sqlite3
import threading

from starlette.concurrency import run_in_threadpool

import cloud_paths
from cloud_auth import load_creds


# ── Attendee analytics (opt-in) ────────────────────────────────────────────────
# When the admin enables it, each attendee `join_meet` is logged as one row keyed
# by a random per-device id the mobile page stores in localStorage. "How many
# people in the last X" is then COUNT(DISTINCT visitor_id) over that window. No IP
# or personal data is ever stored. Off by default — see the legal note in the
# admin panel. Lives in its own SQLite file inside the existing /data volume.

_ANALYTICS_RETENTION_DAYS = 120
_ANALYTICS_FLUSH_SECS     = 5      # how often the background task drains the queue
_analytics_lock  = threading.Lock()
_analytics_db    = None
_analytics_queue = queue.Queue()   # pending joins, flushed to the DB off the loop

# window key -> timedelta; 'all' means since the beginning of time.
_ANALYTICS_WINDOWS = {
    '1h':  datetime.timedelta(hours=1),
    '3h':  datetime.timedelta(hours=3),
    '12h': datetime.timedelta(hours=12),
    '24h': datetime.timedelta(hours=24),
    '7d':  datetime.timedelta(days=7),
}


def _get_analytics_db():
    """Lazily open the analytics DB. Caller holds _analytics_lock."""
    global _analytics_db
    if _analytics_db is None:
        os.makedirs(cloud_paths.DATA_DIR, exist_ok=True)
        db = sqlite3.connect(cloud_paths.ANALYTICS_FILE, check_same_thread=False)
        db.execute('CREATE TABLE IF NOT EXISTS connections ('
                   'meet_id TEXT, visitor_id TEXT, ts INTEGER, namespace TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_conn_meet_ts '
                   'ON connections (meet_id, ts)')
        db.commit()
        _analytics_db = db
    return _analytics_db


def analytics_enabled():
    return bool(load_creds().get('analytics_enabled'))


def log_connection(meet_id, visitor_id, namespace):
    """Queue one attendee join. Called from the WS connect handlers on the event
    loop, so it does no I/O — just a non-blocking in-memory enqueue. The
    background flush task batches these off the loop and drops them if analytics
    is disabled (so a reconnect storm can't stall the loop with per-join commits)."""
    if not meet_id or not visitor_id:
        return
    _analytics_queue.put((meet_id, str(visitor_id)[:64],
                          int(datetime.datetime.now().timestamp()), namespace))


def flush_analytics():
    """Drain queued joins into the DB in one transaction. Blocking (disk I/O) —
    run off the loop. Rows are discarded when analytics is disabled."""
    rows = []
    try:
        while True:
            rows.append(_analytics_queue.get_nowait())
    except queue.Empty:
        pass
    if not rows or not analytics_enabled():
        return
    with _analytics_lock:
        db = _get_analytics_db()
        db.executemany('INSERT INTO connections VALUES (?, ?, ?, ?)', rows)
        db.commit()


async def analytics_flush_loop():
    """Periodically flush queued analytics joins to the DB, off the event loop."""
    while True:
        await asyncio.sleep(_ANALYTICS_FLUSH_SECS)
        try:
            await run_in_threadpool(flush_analytics)
        except Exception as e:
            # A transient DB error (locked, disk full) must not kill the loop —
            # that would stop all future flushes and grow the queue unbounded.
            print(f'[cloud] analytics flush failed: {e}', flush=True)


def attendee_count(meet_id, since_ts):
    """Distinct visitors of a meet since `since_ts` (unix seconds)."""
    with _analytics_lock:
        db = _get_analytics_db()
        row = db.execute('SELECT COUNT(DISTINCT visitor_id) FROM connections '
                         'WHERE meet_id = ? AND ts >= ?', (meet_id, since_ts)).fetchone()
    return row[0] if row else 0


def attendee_counts(meet_id):
    """Distinct-visitor counts for a meet across every analytics window plus
    all-time, computed under a single lock so the relay gets one consistent
    snapshot. Blocking (disk I/O) — run off the loop."""
    now     = datetime.datetime.now()
    windows = {k: int((now - d).timestamp()) for k, d in _ANALYTICS_WINDOWS.items()}
    windows['all'] = 0
    out = {}
    with _analytics_lock:
        db = _get_analytics_db()
        for key, since in windows.items():
            row = db.execute('SELECT COUNT(DISTINCT visitor_id) FROM connections '
                             'WHERE meet_id = ? AND ts >= ?', (meet_id, since)).fetchone()
            out[key] = row[0] if row else 0
    return out


def analytics_prune():
    """Drop rows past the retention window so the DB stays small."""
    cutoff = int((datetime.datetime.now()
                  - datetime.timedelta(days=_ANALYTICS_RETENTION_DAYS)).timestamp())
    with _analytics_lock:
        db = _get_analytics_db()
        db.execute('DELETE FROM connections WHERE ts < ?', (cutoff,))
        db.commit()
