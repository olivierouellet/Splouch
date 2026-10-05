"""The control plane's Postgres: connection pool and schema.

Everything the control plane owns lives here — the admin login and server
settings, organizers and their relay keys, the meet registry (metadata, settings,
images, schedule, expiry, which node and worker holds it), the nodes, and the
attendance counts. Workers never connect: they go through the control plane's
internal API (`cloud_control`), so the database can move with the control plane
without touching a node (docs/architecture/scaling.md).

Synchronous psycopg behind a pool, on purpose. The control plane's routes are
plain `def` handlers that Starlette already runs in its threadpool, the same
model as the rest of the relay (docs/architecture/async-architecture.md), and
its traffic is a few requests a minute, not a frame rate.

The pool opens lazily on first use, from `DATABASE_URL`, so importing a module
never needs a database — the worker imports nothing from here, and a test that
does not touch the store does not need Postgres either.
"""

import atexit
import contextlib
import os
import threading

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool = None
_pool_lock = threading.Lock()
_url = None

# Numbered, append-only. A migration that has shipped is never edited: a change
# is a new entry, applied once by `migrate()` on every control-plane start.
MIGRATIONS = [
    (
        1,
        """
        CREATE TABLE admin (
            id            integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            username      text NOT NULL,
            password_hash text NOT NULL,
            salt          text NOT NULL
        );

        -- Server-wide settings the admin panel edits: picker appearance, the
        -- analytics switch and its acknowledgement, the public-page locale.
        CREATE TABLE settings (
            name  text PRIMARY KEY,
            value jsonb NOT NULL
        );

        CREATE TABLE regions (
            code text PRIMARY KEY,
            name text NOT NULL
        );
        INSERT INTO regions (code, name) VALUES
            ('ca', 'Canada'), ('us', 'United States'), ('eu', 'Europe');

        CREATE TABLE organizers (
            key            text PRIMARY KEY,
            name           text NOT NULL,
            created        date NOT NULL DEFAULT current_date,
            active         boolean NOT NULL DEFAULT true,
            country        text NOT NULL DEFAULT '',
            province       text NOT NULL DEFAULT '',
            region         text REFERENCES regions (code),
            -- The one meet id a relay without a `meet_uid` publishes under.
            legacy_meet_id text
        );

        CREATE TABLE nodes (
            name      text PRIMARY KEY,
            region    text REFERENCES regions (code),
            host      text NOT NULL DEFAULT '',
            state     text NOT NULL DEFAULT 'active',
            workers   integer NOT NULL DEFAULT 1,
            wg_pubkey text NOT NULL DEFAULT '',
            last_seen timestamptz
        );

        -- One row per meet, live or retained. The two images and the start list
        -- have columns of their own so the picker's list query never reads them.
        CREATE TABLE meets (
            id               text PRIMARY KEY,
            organizer_key    text REFERENCES organizers (key) ON DELETE SET NULL,
            organizer        text NOT NULL DEFAULT '',
            name             text NOT NULL DEFAULT '',
            location         text NOT NULL DEFAULT '',
            sport            text NOT NULL DEFAULT '',
            app_window_title text NOT NULL DEFAULT '',
            meet_date        text NOT NULL DEFAULT '',
            settings         jsonb NOT NULL DEFAULT '{}',
            schedule         jsonb,
            home_icon_b64    text NOT NULL DEFAULT '',
            picker_image_b64 text NOT NULL DEFAULT '',
            live             boolean NOT NULL DEFAULT false,
            node             text,
            worker           integer,
            connected_at     text NOT NULL DEFAULT '',
            last_seen        timestamp,
            -- Naive local time, like every other time the relay has kept: NULL
            -- while live, set when the console disconnects.
            expires_at       timestamp
        );

        -- Opt-in attendance counting: one row per attendee join, keyed by a
        -- random per-device id. No IP, no personal data.
        CREATE TABLE analytics (
            meet_id    text NOT NULL,
            visitor_id text NOT NULL,
            ts         bigint NOT NULL,
            namespace  text NOT NULL DEFAULT ''
        );
        CREATE INDEX analytics_meet_ts ON analytics (meet_id, ts);
        """,
    ),
    (
        2,
        """
        -- Where the organizer says it is based, from the Pi's Cloud tab. Kept
        -- beside what the admin recorded, never over it: the panel flags a
        -- difference and the admin accepts it or not. The region is the admin's.
        ALTER TABLE organizers
            ADD COLUMN reported_country  text NOT NULL DEFAULT '',
            ADD COLUMN reported_province text NOT NULL DEFAULT '';

        -- Attendees on the meet right now, from its worker's heartbeat: what
        -- `/api/assign` balances workers on.
        ALTER TABLE meets ADD COLUMN attendees integer NOT NULL DEFAULT 0;
        """,
    ),
    (
        3,
        """
        -- A live move (docs/architecture/scaling.md): `node`/`worker` already name
        -- the target; these name the worker that must let the meet go, and when.
        -- The holder learns it from its next heartbeat; the Pi's register on the
        -- target clears them.
        ALTER TABLE meets
            ADD COLUMN move_from_node   text,
            ADD COLUMN move_from_worker integer,
            ADD COLUMN moved_at         timestamp;
        """,
    ),
    (
        4,
        """
        -- Attendance as numbers only. The visitor ids stay on the node that
        -- carries the meet (cloud_attendance); each node sends its distinct
        -- counts per window, and a meet seen on two nodes adds them up.
        CREATE TABLE attendance_counts (
            meet_id    text NOT NULL,
            node       text NOT NULL,
            counts     jsonb NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (meet_id, node)
        );
        -- The old `analytics` table (visitor ids) is no longer written. Rows left
        -- in it from before go back to the node with `cloud_import.py --attendance`,
        -- which empties it.
        """,
    ),
    (
        5,
        """
        -- Rolling updates (docs/architecture/scaling.md): the version a node runs,
        -- from its heartbeat, and the one a rollout released it to, and when. The
        -- node learns its target from the heartbeat reply and updates itself.
        ALTER TABLE nodes
            ADD COLUMN version        text NOT NULL DEFAULT '',
            ADD COLUMN target_version text,
            ADD COLUMN target_set_at  timestamptz;
        """,
    ),
    (
        6,
        """
        -- Whether a meet is *running*, not just connected: a rollout waits for a
        -- running meet only (docs/architecture/scaling.md). Its console's last
        -- board frame, its session days, and the pool's UTC offset that says which
        -- day it is there.
        ALTER TABLE meets
            ADD COLUMN last_frame_at timestamptz,
            ADD COLUMN session_dates jsonb NOT NULL DEFAULT '[]',
            ADD COLUMN utc_offset    integer;
        """,
    ),
    (
        7,
        """
        -- A meet's content stays on the node that carries it (cloud_meetstore):
        -- its start list names every athlete entered, with their club. The
        -- registry keeps the picker card and the picker image only, and from its
        -- settings just what the admin table shows (console, language).
        ALTER TABLE meets DROP COLUMN schedule, DROP COLUMN home_icon_b64;
        UPDATE meets SET settings = jsonb_strip_nulls(jsonb_build_object(
            'console', settings -> 'console', 'locale', settings -> 'locale'));
        """,
    ),
]

# Tables holding data, in an order TRUNCATE accepts. Tests empty these between
# cases; `regions` is reference data and keeps its rows.
DATA_TABLES = (
    "attendance_counts",
    "analytics",
    "meets",
    "nodes",
    "organizers",
    "settings",
    "admin",
)


def configure(url):
    """Point the pool at `url`, closing any pool already open. For tests and tools."""
    global _pool, _url
    with _pool_lock:
        if _pool is not None:
            _pool.close()
        _pool = None
        _url = url


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            url = _url or os.environ.get("DATABASE_URL", "")
            if not url:
                raise RuntimeError("DATABASE_URL is not set")
            _pool = ConnectionPool(
                url,
                min_size=1,
                max_size=int(os.environ.get("DATABASE_POOL_SIZE", "10")),
                kwargs={"row_factory": dict_row},
                open=True,
            )
            # A short-lived process (the import tool, a shell one-liner) would
            # otherwise wait out the pool's worker threads on exit.
            atexit.register(close)
        return _pool


@contextlib.contextmanager
def conn():
    """A pooled connection, committed when the block exits cleanly."""
    with _get_pool().connection() as c:
        yield c


def close():
    configure(None)


def migrate():
    """Apply every migration not yet recorded. Safe to run on every start."""
    with conn() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS schema_version (version integer PRIMARY KEY)"
        )
        # Two control planes starting at once must not both apply migration 1.
        c.execute("SELECT pg_advisory_xact_lock(7350135)")
        done = {r["version"] for r in c.execute("SELECT version FROM schema_version")}
        for version, sql in MIGRATIONS:
            if version in done:
                continue
            c.execute(sql)
            c.execute("INSERT INTO schema_version (version) VALUES (%s)", (version,))


def truncate_all():
    """Empty every data table. Tests only."""
    with conn() as c:
        c.execute(f"TRUNCATE {', '.join(DATA_TABLES)}")
