"""Attendance numbers, as the nodes send them — the control plane's half.

Off unless the admin turns it on. The visitor ids behind the numbers never come
here: each node keeps its meets' joins (`cloud_attendance`) and sends distinct
counts per window with its heartbeat, so a meet's visitors stay in the region that
carries it. The control plane keeps the latest numbers per meet and node, for the
admin panel; a meet seen on two nodes (moved across) adds both up.
"""

from psycopg.types.json import Jsonb

import cloud_attendance
import cloud_db
from cloud_auth import load_creds

# What `/privacy` states and the nodes prune to (`cloud_attendance`).
_ANALYTICS_RETENTION_DAYS = cloud_attendance.RETENTION_DAYS
_ANALYTICS_WINDOWS = cloud_attendance.WINDOWS


def analytics_enabled():
    return bool(load_creds().get("analytics_enabled"))


def store(node, attendance):
    """A node's latest numbers: `{meet_id: {window: count}}`. Counts only."""
    with cloud_db.conn() as c:
        for meet_id, counts in (attendance or {}).items():
            clean = {
                k: max(int(v), 0)
                for k, v in (counts or {}).items()
                if k in _ANALYTICS_WINDOWS or k == "all"
            }
            c.execute(
                "INSERT INTO attendance_counts (meet_id, node, counts, updated_at) "
                "VALUES (%s, %s, %s, now()) ON CONFLICT (meet_id, node) DO UPDATE "
                "SET counts = EXCLUDED.counts, updated_at = now()",
                (str(meet_id)[:64], node, Jsonb(clean)),
            )


def attendee_count(meet_id, window):
    """Distinct visitors of a meet in `window` ('1h' … '7d', 'all'), summed over
    the nodes that carried it."""
    with cloud_db.conn() as c:
        row = c.execute(
            "SELECT coalesce(sum((counts ->> %s)::int), 0) AS n "
            "FROM attendance_counts WHERE meet_id = %s",
            (window, meet_id),
        ).fetchone()
    return row["n"]


def forget_gone():
    """Drop the numbers of meets no longer in the registry (swept or deleted)."""
    with cloud_db.conn() as c:
        c.execute(
            "DELETE FROM attendance_counts a WHERE NOT EXISTS "
            "(SELECT 1 FROM meets m WHERE m.id = a.meet_id)"
        )
