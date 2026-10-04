"""Attendance ids, kept on the node that carries the meet.

Opt-in counting (`app.md` `C-10`): one row per attendee join, keyed by a random
per-device id — no IP, no personal data — so "how many people in the last hour" is
a COUNT(DISTINCT) over a window. The ids never leave the node: it counts them here
and sends the control plane numbers only, so a European meet's visitors stay in
Europe (docs/architecture/scaling.md).

One SQLite file on the node's data volume, shared by its workers — WAL mode, so
several worker processes write at once. It is the file and the table the
single-process relay used (`analytics.db`, `connections`), so a box upgraded from
before the split keeps its history with nothing to import.

Imported by the workers (`cloud_node`, `cloud_server`) for the store, and by the
control plane for the retention it states on `/privacy`; the control plane never
opens the file.
"""

import datetime
import os
import sqlite3
import threading

import cloud_paths

RETENTION_DAYS = 120
# A meet keeps being counted, and its numbers sent, while it had a visitor this
# recently — so a finished meet's "last hour" still falls to zero on the panel.
ACTIVE_DAYS = 7

# window key -> timedelta; 'all' means since the beginning of time.
WINDOWS = {
    "1h": datetime.timedelta(hours=1),
    "3h": datetime.timedelta(hours=3),
    "12h": datetime.timedelta(hours=12),
    "24h": datetime.timedelta(hours=24),
    "7d": datetime.timedelta(days=7),
}

_lock = threading.Lock()
_db = None
_db_path = None


def _conn():
    """This process's connection, opened on first use. Caller holds _lock."""
    global _db, _db_path
    path = cloud_paths.ANALYTICS_FILE
    if _db is None or _db_path != path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        db = sqlite3.connect(path, check_same_thread=False, timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS connections ("
            "meet_id TEXT, visitor_id TEXT, ts INTEGER, namespace TEXT)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_conn_meet_ts ON connections (meet_id, ts)"
        )
        db.commit()
        _db, _db_path = db, path
    return _db


def record(rows):
    """Store a batch of joins: `(meet_id, visitor_id, ts, namespace)`. Blocking."""
    rows = [
        (str(m)[:64], str(v)[:64], int(ts), str(ns)[:32])
        for m, v, ts, ns in rows
        if m and v
    ]
    if not rows:
        return 0
    with _lock:
        db = _conn()
        db.executemany("INSERT INTO connections VALUES (?, ?, ?, ?)", rows)
        db.commit()
    return len(rows)


def _windows(now=None):
    now = now or datetime.datetime.now()
    since = {k: int((now - d).timestamp()) for k, d in WINDOWS.items()}
    since["all"] = 0
    return since


def counts(meet_id, now=None):
    """Distinct visitors of one meet in every window, in one consistent read."""
    since = _windows(now)
    cols = ", ".join(
        "COUNT(DISTINCT CASE WHEN ts >= ? THEN visitor_id END)" for _ in since
    )
    with _lock:
        row = (
            _conn()
            .execute(
                f"SELECT {cols} FROM connections WHERE meet_id = ?",
                (*since.values(), meet_id),
            )
            .fetchone()
        )
    return dict(zip(since, row, strict=True))


def active_counts(now=None):
    """`{meet_id: counts}` for every meet with a visitor in the last ACTIVE_DAYS —
    what the node sends the control plane. Numbers only."""
    now = now or datetime.datetime.now()
    cutoff = int((now - datetime.timedelta(days=ACTIVE_DAYS)).timestamp())
    with _lock:
        ids = [
            r[0]
            for r in _conn()
            .execute(
                "SELECT DISTINCT meet_id FROM connections WHERE ts >= ?", (cutoff,)
            )
            .fetchall()
        ]
    return {meet_id: counts(meet_id, now) for meet_id in ids}


def prune(now=None):
    """Drop joins past the retention `/privacy` promises."""
    now = now or datetime.datetime.now()
    cutoff = int((now - datetime.timedelta(days=RETENTION_DAYS)).timestamp())
    with _lock:
        db = _conn()
        db.execute("DELETE FROM connections WHERE ts < ?", (cutoff,))
        db.commit()


def close():
    """Close this process's connection (tests point the store at another file)."""
    global _db, _db_path
    with _lock:
        if _db is not None:
            _db.close()
        _db = _db_path = None
