"""A node's meets: everything a meet's pages need, kept on the node that carries it.

A meet's start list names every athlete entered, with their club; its settings and
home icon are the organizer's. All of it stays in the region of the meet — on the
node — and the control plane keeps only the picker card (name, date, location,
organizer, where it lives, expiry) and the picker image (docs/architecture/
scaling.md). A finished meet is served from here until it expires.

One SQLite file on the node's data volume, shared by its workers (WAL), so a
worker restart — an update — loses nothing: an offline meet's pages answer again as
soon as the node is back. A meet leaves this store when the control plane no longer
lists it for this node (it expired, was deleted, or moved), or, while the control
plane cannot be asked, a day after its own expiry.

Imported by the workers, and by the import tool on a box upgraded from before the
split; the control plane never opens the file.
"""

import datetime
import json
import os
import sqlite3
import threading
import time

import cloud_paths

# A row written this recently is kept even if the control plane's list does not
# name it yet: the register that made it may still be on its way.
FRESH_SECS = 600

_lock = threading.Lock()
_db = None
_db_path = None


def _conn():
    global _db, _db_path
    path = os.path.join(os.path.dirname(cloud_paths.ANALYTICS_FILE), "meets.db")
    if _db is None or _db_path != path:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        db = sqlite3.connect(path, check_same_thread=False, timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE IF NOT EXISTS meets ("
            "id TEXT PRIMARY KEY, record TEXT NOT NULL, "
            "updated REAL NOT NULL, expires TEXT)"
        )
        db.commit()
        _db, _db_path = db, path
    return _db


def compute_expiry(meet_date, when=None):
    """Midnight after the final session date, or after `when` if no meet date —
    when an offline meet stops being served. The control plane decides it; a node
    uses the same rule while it cannot ask."""
    when = when or datetime.datetime.now()
    base = None
    if meet_date:
        try:
            base = datetime.date.fromisoformat(meet_date)
        except ValueError:
            base = None
    if base is None:
        base = when.date()
    return datetime.datetime.combine(base, datetime.time.min) + datetime.timedelta(
        days=1
    )


def save(meet_id, record, expires=None):
    """Store (or replace) a meet's record: metadata, settings with images, start
    list. `expires` is an ISO date-time while it is offline, None while live."""
    with _lock:
        db = _conn()
        db.execute(
            "INSERT INTO meets (id, record, updated, expires) VALUES (?, ?, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET record = excluded.record, "
            "updated = excluded.updated, expires = excluded.expires",
            (meet_id, json.dumps(record), time.time(), expires),
        )
        db.commit()


def update(meet_id, **fields):
    """Merge fields into a stored record (a new start list, say). No-op if absent."""
    with _lock:
        db = _conn()
        row = db.execute("SELECT record FROM meets WHERE id = ?", (meet_id,)).fetchone()
        if row is None:
            return
        record = {**json.loads(row[0]), **fields}
        db.execute(
            "UPDATE meets SET record = ?, updated = ? WHERE id = ?",
            (json.dumps(record), time.time(), meet_id),
        )
        db.commit()


def set_expiry(meet_id, expires):
    with _lock:
        db = _conn()
        db.execute("UPDATE meets SET expires = ? WHERE id = ?", (expires, meet_id))
        db.commit()


def get(meet_id, now=None):
    """A meet's record, or None — including once it is past its own expiry. A
    record still live (no expiry: its Pi is on one of this node's workers) carries
    `"live": True`; a finished one `False`."""
    with _lock:
        row = (
            _conn()
            .execute("SELECT record, expires FROM meets WHERE id = ?", (meet_id,))
            .fetchone()
        )
    if row is None:
        return None
    if row[1] and datetime.datetime.fromisoformat(row[1]) <= (
        now or datetime.datetime.now()
    ):
        return None
    return {**json.loads(row[0]), "live": row[1] is None}


def ids():
    with _lock:
        return [r[0] for r in _conn().execute("SELECT id FROM meets").fetchall()]


def keep_only(known, now=None):
    """Drop every meet the control plane no longer lists for this node — except
    one written in the last FRESH_SECS. Returns the ids dropped."""
    cutoff = (now or time.time()) - FRESH_SECS
    known = set(known)
    with _lock:
        db = _conn()
        gone = [
            r[0]
            for r in db.execute("SELECT id, updated FROM meets").fetchall()
            if r[0] not in known and r[1] < cutoff
        ]
        db.executemany("DELETE FROM meets WHERE id = ?", [(g,) for g in gone])
        db.commit()
    return gone


def prune_expired(now=None):
    """While the control plane cannot be asked: drop meets a day past their expiry."""
    now = now or datetime.datetime.now()
    limit = (now - datetime.timedelta(days=1)).isoformat()
    with _lock:
        db = _conn()
        db.execute(
            "DELETE FROM meets WHERE expires IS NOT NULL AND expires < ?", (limit,)
        )
        db.commit()


def close():
    global _db, _db_path
    with _lock:
        if _db is not None:
            _db.close()
        _db = _db_path = None
