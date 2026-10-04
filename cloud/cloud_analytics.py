"""Opt-in attendance counting, on the control plane's Postgres.

Off unless the admin turns it on. One row per `join_meet`, keyed by a random
per-device id the mobile page keeps in localStorage — no IP, no personal data —
so "how many people in the last hour" is a COUNT(DISTINCT) over a window.

Workers queue joins and send them here in batches (`cloud_node`); this module is
the store: it takes the batches, answers the counts the Pi's Cloud tab and the
admin panel ask for, and prunes past the retention `/privacy` promises. Counting
in one place keeps a meet's numbers whole across workers and nodes.
"""

import datetime

import cloud_db
from cloud_auth import load_creds

_ANALYTICS_RETENTION_DAYS = 120
# How often the control plane prunes. Startup alone is not enough: `/privacy`
# promises the retention above, and a container can run for longer than that
# between deploys.
_ANALYTICS_PRUNE_SECS = 24 * 3600

# window key -> timedelta; 'all' means since the beginning of time.
_ANALYTICS_WINDOWS = {
    "1h": datetime.timedelta(hours=1),
    "3h": datetime.timedelta(hours=3),
    "12h": datetime.timedelta(hours=12),
    "24h": datetime.timedelta(hours=24),
    "7d": datetime.timedelta(days=7),
}


def analytics_enabled():
    return bool(load_creds().get("analytics_enabled"))


def record(rows):
    """Store a worker's batch of joins: `(meet_id, visitor_id, ts, namespace)`.

    Discarded when analytics is off — a worker may have queued them before the
    switch was turned off, and they must not land anyway."""
    rows = [
        (str(m)[:64], str(v)[:64], int(ts), str(ns)[:32])
        for m, v, ts, ns in rows
        if m and v
    ]
    if not rows or not analytics_enabled():
        return 0
    with cloud_db.conn() as c, c.cursor() as cur:
        cur.executemany("INSERT INTO analytics VALUES (%s, %s, %s, %s)", rows)
    return len(rows)


def attendee_count(meet_id, since_ts):
    """Distinct visitors of a meet since `since_ts` (unix seconds)."""
    with cloud_db.conn() as c:
        row = c.execute(
            "SELECT COUNT(DISTINCT visitor_id) AS n FROM analytics "
            "WHERE meet_id = %s AND ts >= %s",
            (meet_id, since_ts),
        ).fetchone()
    return row["n"]


def attendee_counts(meet_id):
    """Distinct-visitor counts for a meet across every analytics window plus
    all-time, in one query so the Pi gets one consistent snapshot."""
    now = datetime.datetime.now()
    windows = {k: int((now - d).timestamp()) for k, d in _ANALYTICS_WINDOWS.items()}
    windows["all"] = 0
    cols = ", ".join(
        f'COUNT(DISTINCT visitor_id) FILTER (WHERE ts >= %s) AS "{k}"' for k in windows
    )
    with cloud_db.conn() as c:
        row = c.execute(
            f"SELECT {cols} FROM analytics WHERE meet_id = %s",
            (*windows.values(), meet_id),
        ).fetchone()
    return {k: row[k] for k in windows}


def analytics_prune():
    """Drop rows past the retention window so the table stays small."""
    cutoff = int(
        (
            datetime.datetime.now() - datetime.timedelta(days=_ANALYTICS_RETENTION_DAYS)
        ).timestamp()
    )
    with cloud_db.conn() as c:
        c.execute("DELETE FROM analytics WHERE ts < %s", (cutoff,))
