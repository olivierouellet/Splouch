"""Every meet the relay knows, live or retained — the control plane's registry.

One row per meet in Postgres (`cloud_db`). A worker holds a live meet's state in
memory and reports to this registry over the internal API: a Pi registers (the
key is checked here and the meet's metadata, settings and images stored), sends a
schedule, and disconnects (the meet is retired with an expiry). The picker, the
admin panel and a worker serving a retained meet's pages all read from here.

A row is **live** while some worker holds the meet's relay. Workers say which
meets they hold every few seconds (`heartbeat`); a meet a worker stops naming, or
whose node stops calling, is retired the same way a disconnect retires it — so a
crashed worker cannot leave a meet showing as live forever.

Times are naive local datetimes, as the relay has always kept them: the expiry a
retained meet shows in the admin table is midnight after its last session, local
to the server.
"""

import datetime
import hashlib

from psycopg.types.json import Jsonb

import cloud_auth
import cloud_db
from splouch_regions import clean_location

# A node that has not called in this long is not offered new meets, and its live
# meets are retired (`retire_silent`). Workers report every
# `cloud_node.HEARTBEAT_SECS`; a few missed beats is a dead worker, not a slow one.
SILENT_AFTER_SECS = 90
# How long a moved meet may be missing from every worker's heartbeat — the old one
# has let it go, the Pi has not yet reached the new one — before it is retired.
MOVE_GRACE_SECS = 30
# How long the old worker keeps being told to let a moved meet go.
MOVE_NOTICE_SECS = 120

# The picker's columns. Never the start list or the two images.
_LIST_COLUMNS = (
    "m.id, m.organizer, m.name, m.location, m.sport, m.meet_date, m.live, "
    "m.connected_at, m.expires_at, m.settings, m.node, m.worker, n.host AS node_url, "
    "(m.picker_image_b64 <> '') AS has_picker_image, "
    "coalesce(o.country, '') AS country, coalesce(o.province, '') AS province"
)


def worker_url(node_url, worker):
    """A worker's public base: its node's URL and its `/wN` prefix
    (docs/architecture/scaling.md). Caddy strips the prefix on the way in."""
    return f"{(node_url or '').rstrip('/')}/w{int(worker)}"


def meet_base(meet, here):
    """Where a client reaches a meet (`app.md` `C-11`): a live meet's worker, else
    `here` — this server, whose default route serves any retained meet."""
    if meet.get("live") and meet.get("node_url") and meet.get("worker"):
        return worker_url(meet["node_url"], meet["worker"])
    return here.rstrip("/")


def page_url(meet):
    """Where a meet's page is served. A live meet's worker holds it in memory, so
    the link goes there; a retained one can be served by any worker, from its
    record, and goes to this domain's default route."""
    if meet.get("live") and meet.get("node_url") and meet.get("worker"):
        return (
            f"{worker_url(meet['node_url'], meet['worker'])}/mobile?meet={meet['id']}"
        )
    return f"/mobile?meet={meet['id']}"


def meet_id_for(key, meet_uid):
    """Deterministic meet id for a (relay key, meet_uid) pair.

    Lets one relay key publish several meets — e.g. a meet split across days into
    separate LENEX files — each landing on its own stable picker card and
    reattaching on reload.
    """
    return hashlib.sha256(f"{key}:{meet_uid}".encode()).hexdigest()[:11]


def compute_expiry(meet_date, when=None):
    """Midnight after the final session date, or after `when` if no meet date."""
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


def _split_settings(settings):
    """(settings without the two images, home icon, picker image)."""
    settings = dict(settings or {})
    icon = settings.pop("home_icon_b64", "") or ""
    picker = settings.pop("picker_image_b64", "") or ""
    return settings, icon, picker


def _record(row):
    """A full row as the record shape workers and backups use."""
    settings = dict(row["settings"] or {})
    if row.get("home_icon_b64"):
        settings["home_icon_b64"] = row["home_icon_b64"]
    if row.get("picker_image_b64"):
        settings["picker_image_b64"] = row["picker_image_b64"]
    exp = row["expires_at"]
    seen = row["last_seen"]
    return {
        "id": row["id"],
        "organizer": row["organizer"],
        "relay_key": row["organizer_key"] or "",
        "name": row["name"],
        "location": row["location"],
        "sport": row["sport"],
        "app_window_title": row["app_window_title"],
        "meet_date": row["meet_date"],
        "settings": settings,
        "schedule_data": row["schedule"] or {},
        "live": row["live"],
        "node": row["node"],
        "worker": row["worker"],
        "connected_at": row["connected_at"],
        "last_seen": seen.isoformat(timespec="seconds") if seen else None,
        "expires_at": exp.isoformat(timespec="seconds") if exp else None,
    }


# ── Worker reports ─────────────────────────────────────────────────────────────


def _meet_id(key, meet_uid):
    return meet_id_for(key, meet_uid) if meet_uid else cloud_auth.legacy_meet_id(key)


class NoNode(Exception):
    """No live node in the organizer's region can take the meet."""


def assign(key, meet_uid):
    """Where a Pi should publish a meet: `{"meet_id", "organizer", "node", "worker",
    "url", "region"}`, or None when the key is refused. Raises NoNode.

    A meet already held, or last held, by a worker that is still up goes back there,
    so a Pi reconnecting after a drop lands where its attendees already are.
    Otherwise the least-loaded worker on a live node in the organizer's region —
    fewest attendees, then fewest meets. An organizer with no region yet (imported
    from before regions existed) may go to any node; one with a region only ever
    goes to that region's nodes (data residency).
    """
    org = cloud_auth.organizer(key)
    if org is None or not org["active"]:
        return None
    meet_id = _meet_id(key, meet_uid)
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        seconds=SILENT_AFTER_SECS
    )
    with cloud_db.conn() as c:
        nodes = c.execute(
            "SELECT * FROM nodes WHERE state = 'active' AND last_seen >= %s "
            "AND (%s::text IS NULL OR region = %s) ORDER BY name",
            (cutoff, org["region"], org["region"]),
        ).fetchall()
        if not nodes:
            raise NoNode(org["region"] or "")
        by_name = {n["name"]: n for n in nodes}
        prev = c.execute(
            "SELECT node, worker FROM meets WHERE id = %s", (meet_id,)
        ).fetchone()
        if (
            prev
            and prev["node"] in by_name
            and 1 <= (prev["worker"] or 0) <= by_name[prev["node"]]["workers"]
        ):
            node, worker = by_name[prev["node"]], prev["worker"]
        else:
            load = {
                (r["node"], r["worker"]): (r["attendees"], r["meets"])
                for r in c.execute(
                    "SELECT node, worker, sum(attendees) AS attendees, "
                    "count(*) AS meets FROM meets WHERE live GROUP BY node, worker"
                ).fetchall()
            }
            # Fewest attendees, then fewest meets, then the first by name: the
            # tuple compares in that order.
            _, name, worker = min(
                (load.get((n["name"], w), (0, 0)), n["name"], w)
                for n in nodes
                for w in range(1, n["workers"] + 1)
            )
            node = by_name[name]
    return {
        "meet_id": meet_id,
        "organizer": org["name"],
        "node": node["name"],
        "worker": worker,
        "url": node["host"],
        "region": node["region"] or "",
    }


def register(key, meet_uid, meta, node, worker, location=None):
    """A Pi registered a meet on `node`/`worker`. None when the key is refused.

    Returns `{"meet_id", "organizer", "schedule_data"}` — the stored start list, so a
    meet reconnecting after a drop shows its schedule before the Pi re-sends it.
    `location` is where the organizer says it is based (`{"country", "province"}`,
    from the Pi's Cloud tab); it is recorded beside the admin's, never over it.
    """
    org = cloud_auth.organizer(key)
    if org is None or not org["active"]:
        return None
    if isinstance(location, dict):
        country, province = clean_location(
            location.get("country"), location.get("province")
        )
        cloud_auth.report_location(key, country, province)
    meet_id = _meet_id(key, meet_uid)
    settings, icon, picker = _split_settings(meta.get("settings"))
    now = datetime.datetime.now()
    with cloud_db.conn() as c:
        prev = c.execute(
            "SELECT live, connected_at, schedule FROM meets WHERE id = %s", (meet_id,)
        ).fetchone()
        # A settings re-register (the operator changed something) keeps the time
        # the meet first connected; a fresh connect starts a new one.
        connected_at = (
            prev["connected_at"]
            if prev and prev["live"] and prev["connected_at"]
            else now.strftime("%H:%M:%S")
        )
        c.execute(
            "INSERT INTO meets (id, organizer_key, organizer, name, location, sport, "
            "app_window_title, meet_date, settings, home_icon_b64, picker_image_b64, "
            "live, node, worker, connected_at, last_seen, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s, %s, NULL) "
            "ON CONFLICT (id) DO UPDATE SET organizer_key = EXCLUDED.organizer_key, "
            "organizer = EXCLUDED.organizer, name = EXCLUDED.name, "
            "location = EXCLUDED.location, sport = EXCLUDED.sport, "
            "app_window_title = EXCLUDED.app_window_title, "
            "meet_date = EXCLUDED.meet_date, settings = EXCLUDED.settings, "
            "home_icon_b64 = EXCLUDED.home_icon_b64, "
            "picker_image_b64 = EXCLUDED.picker_image_b64, live = true, "
            "node = EXCLUDED.node, worker = EXCLUDED.worker, "
            "move_from_node = NULL, move_from_worker = NULL, moved_at = NULL, "
            "connected_at = EXCLUDED.connected_at, last_seen = EXCLUDED.last_seen, "
            "expires_at = NULL",
            (
                meet_id,
                key,
                org["name"],
                str(meta.get("name", "")),
                str(meta.get("location", "")),
                str(meta.get("sport", "")),
                str(meta.get("app_window_title", "")),
                str(meta.get("meet_date", "")),
                Jsonb(settings),
                icon,
                picker,
                node,
                worker,
                connected_at,
                now,
            ),
        )
    return {
        "meet_id": meet_id,
        "organizer": org["name"],
        "connected_at": connected_at,
        "schedule_data": (prev["schedule"] if prev else None) or {},
    }


def set_schedule(meet_id, schedule):
    with cloud_db.conn() as c:
        c.execute(
            "UPDATE meets SET schedule = %s WHERE id = %s",
            (Jsonb(schedule) if schedule else None, meet_id),
        )


def _retire_where(c, clause, params, now):
    """Retire every live meet matching `clause`: not live, expiring after its date."""
    rows = c.execute(
        f"SELECT id, meet_date FROM meets WHERE live AND {clause}", params
    ).fetchall()
    for r in rows:
        c.execute(
            "UPDATE meets SET live = false, attendees = 0, last_seen = %s, "
            "expires_at = %s WHERE id = %s",
            (now, compute_expiry(r["meet_date"], now), r["id"]),
        )
    return [r["id"] for r in rows]


def retire(meet_id, node, worker):
    """The Pi disconnected. Only the holder may retire: a newer register elsewhere
    has already moved the meet, and an old socket closing must not undo that."""
    with cloud_db.conn() as c:
        _retire_where(
            c,
            "id = %s AND node = %s AND worker = %s",
            (meet_id, node, worker),
            datetime.datetime.now(),
        )


def heartbeat(
    node,
    worker,
    live_ids,
    host="",
    region=None,
    workers=1,
    wg_pubkey="",
    attendees=None,
):
    """A worker says which meets it holds, and how many attendees each has.

    Returns `{"retired": [ids], "moves": [{"meet_id", "url"}]}`: the meets it no
    longer names are retired, and the moves are meets the admin moved off this
    worker, with the page URL their attendees should go to.
    """
    now = datetime.datetime.now()
    grace = now - datetime.timedelta(seconds=MOVE_GRACE_SECS)
    with cloud_db.conn() as c:
        c.execute(
            "INSERT INTO nodes (name, region, host, workers, wg_pubkey, last_seen) "
            "VALUES (%s, %s, %s, %s, %s, now()) ON CONFLICT (name) DO UPDATE SET "
            "region = COALESCE(EXCLUDED.region, nodes.region), host = EXCLUDED.host, "
            "workers = EXCLUDED.workers, "
            "wg_pubkey = COALESCE(NULLIF(EXCLUDED.wg_pubkey, ''), nodes.wg_pubkey), "
            "last_seen = now()",
            (node, region or None, host, workers, wg_pubkey),
        )
        c.execute(
            "UPDATE meets SET last_seen = %s WHERE live AND node = %s AND worker = %s "
            "AND id = ANY(%s)",
            (now, node, worker, list(live_ids)),
        )
        for meet_id, n in (attendees or {}).items():
            c.execute(
                "UPDATE meets SET attendees = %s "
                "WHERE id = %s AND live AND node = %s AND worker = %s",
                (max(int(n), 0), meet_id, node, worker),
            )
        # A meet just moved here is not yet held by anyone; give the Pi its grace.
        retired = _retire_where(
            c,
            "node = %s AND worker = %s AND NOT (id = ANY(%s)) "
            "AND (moved_at IS NULL OR moved_at < %s)",
            (node, worker, list(live_ids), grace),
            now,
        )
        moves = c.execute(
            "SELECT m.id, m.worker, n.host FROM meets m JOIN nodes n ON n.name = m.node "
            "WHERE m.move_from_node = %s AND m.move_from_worker = %s "
            "AND m.moved_at >= %s AND m.id = ANY(%s)",
            (
                node,
                worker,
                now - datetime.timedelta(seconds=MOVE_NOTICE_SECS),
                list(live_ids),
            ),
        ).fetchall()
    return {
        "retired": retired,
        "moves": [
            {
                "meet_id": r["id"],
                "base": worker_url(r["host"], r["worker"]),
                "url": f"{worker_url(r['host'], r['worker'])}/mobile?meet={r['id']}",
            }
            for r in moves
        ],
    }


def move(meet_id, node, worker):
    """Move a live meet to another worker. False when the meet is not live, the
    target is not a live active node's worker, or the meet is already there.

    `node`/`worker` are rewritten to the target at once, so the Pi's next
    `/api/assign` lands there and the old worker's disconnect cannot retire it; the
    old worker learns to let go from its next heartbeat (`heartbeat`).
    """
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        seconds=SILENT_AFTER_SECS
    )
    with cloud_db.conn() as c:
        meet = c.execute(
            "SELECT node, worker FROM meets WHERE id = %s AND live", (meet_id,)
        ).fetchone()
        target = c.execute(
            "SELECT workers FROM nodes WHERE name = %s AND state = 'active' "
            "AND last_seen >= %s",
            (node, cutoff),
        ).fetchone()
        if (
            meet is None
            or target is None
            or not 1 <= int(worker) <= target["workers"]
            or (meet["node"], meet["worker"]) == (node, int(worker))
        ):
            return False
        c.execute(
            "UPDATE meets SET move_from_node = node, move_from_worker = worker, "
            "node = %s, worker = %s, moved_at = %s WHERE id = %s",
            (node, int(worker), datetime.datetime.now(), meet_id),
        )
    return True


def retire_silent(max_age_seconds=SILENT_AFTER_SECS, now=None):
    """Retire live meets nobody has vouched for in `max_age_seconds` — a worker or a
    whole node that died without saying goodbye."""
    now = now or datetime.datetime.now()
    cutoff = now - datetime.timedelta(seconds=max_age_seconds)
    with cloud_db.conn() as c:
        return _retire_where(c, "last_seen < %s", (cutoff,), now)


# ── Reads ──────────────────────────────────────────────────────────────────────


def get(meet_id):
    """A meet's full record, or None. Carries its node's public URL, so a worker
    asked for a meet live elsewhere can send the visitor there."""
    with cloud_db.conn() as c:
        row = c.execute(
            "SELECT m.*, n.host AS node_url FROM meets m "
            "LEFT JOIN nodes n ON n.name = m.node WHERE m.id = %s",
            (meet_id,),
        ).fetchone()
    if not row:
        return None
    rec = _record(row)
    rec["node_url"] = row["node_url"] or ""
    return rec


def image(meet_id, column):
    """One meet's base64 picker image or home icon, '' when it has none."""
    assert column in ("picker_image_b64", "home_icon_b64")
    with cloud_db.conn() as c:
        row = c.execute(
            f"SELECT {column} AS b64 FROM meets WHERE id = %s", (meet_id,)
        ).fetchone()
    return row["b64"] if row else None


def sweep_expired(now=None):
    """Drop retained meets past their expiry. Live meets are never swept."""
    now = now or datetime.datetime.now()
    with cloud_db.conn() as c:
        c.execute("DELETE FROM meets WHERE NOT live AND expires_at <= %s", (now,))


def list_meets():
    """Live and retained meets, live first then by name — list columns only."""
    sweep_expired()
    with cloud_db.conn() as c:
        return c.execute(
            f"SELECT {_LIST_COLUMNS} FROM meets m LEFT JOIN nodes n ON n.name = m.node "
            "LEFT JOIN organizers o ON o.key = m.organizer_key "
            "ORDER BY NOT m.live, lower(m.name), m.id"
        ).fetchall()


# ── Admin ──────────────────────────────────────────────────────────────────────


def set_expiry(meet_id, expires_at):
    with cloud_db.conn() as c:
        c.execute(
            "UPDATE meets SET expires_at = %s WHERE id = %s AND NOT live",
            (expires_at, meet_id),
        )


def delete(meet_id):
    with cloud_db.conn() as c:
        c.execute("DELETE FROM meets WHERE id = %s AND NOT live", (meet_id,))


def backup():
    """Every meet's full record, keyed by id — the shape restore() takes back."""
    with cloud_db.conn() as c:
        rows = c.execute("SELECT * FROM meets ORDER BY id").fetchall()
    out = {}
    for row in rows:
        rec = _record(row)
        out[rec.pop("id")] = rec
    return out


def restore(meets):
    """Upsert a backup's meets, retained. A meet live right now is skipped so its
    fresh state isn't overwritten by a stale backup; nothing else is touched.
    Returns the ids written."""
    written = []
    with cloud_db.conn() as c:
        live = {r["id"] for r in c.execute("SELECT id FROM meets WHERE live")}
        keys = {r["key"] for r in c.execute("SELECT key FROM organizers")}
        for meet_id, rec in meets.items():
            if meet_id in live or not isinstance(rec, dict):
                continue
            settings, icon, picker = _split_settings(rec.get("settings"))
            exp = rec.get("expires_at")
            try:
                exp = datetime.datetime.fromisoformat(exp) if exp else None
            except ValueError:
                exp = None
            if exp is None:
                exp = compute_expiry(rec.get("meet_date", ""))
            key = rec.get("relay_key") or None
            c.execute(
                "INSERT INTO meets (id, organizer_key, organizer, name, location, "
                "sport, app_window_title, meet_date, settings, schedule, "
                "home_icon_b64, picker_image_b64, live, expires_at) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s) "
                "ON CONFLICT (id) DO UPDATE SET organizer_key = EXCLUDED.organizer_key, "
                "organizer = EXCLUDED.organizer, name = EXCLUDED.name, "
                "location = EXCLUDED.location, sport = EXCLUDED.sport, "
                "app_window_title = EXCLUDED.app_window_title, "
                "meet_date = EXCLUDED.meet_date, settings = EXCLUDED.settings, "
                "schedule = EXCLUDED.schedule, home_icon_b64 = EXCLUDED.home_icon_b64, "
                "picker_image_b64 = EXCLUDED.picker_image_b64, live = false, "
                "expires_at = EXCLUDED.expires_at",
                (
                    meet_id,
                    key if key in keys else None,
                    str(rec.get("organizer", "")),
                    str(rec.get("name", "")),
                    str(rec.get("location", "")),
                    str(rec.get("sport", "")),
                    str(rec.get("app_window_title", "")),
                    str(rec.get("meet_date", "")),
                    Jsonb(settings),
                    Jsonb(rec["schedule_data"]) if rec.get("schedule_data") else None,
                    icon,
                    picker,
                    exp,
                ),
            )
            written.append(meet_id)
    return written


def nodes():
    """Every node, with how many live meets and attendees it carries."""
    with cloud_db.conn() as c:
        return c.execute(
            "SELECT n.*, count(m.id) AS meets, coalesce(sum(m.attendees), 0) AS attendees "
            "FROM nodes n LEFT JOIN meets m ON m.node = n.name AND m.live "
            "GROUP BY n.name ORDER BY n.name"
        ).fetchall()


def set_node_state(name, state):
    """`active` takes new meets; `draining` takes none, and its meets finish there."""
    if state not in ("active", "draining"):
        return
    with cloud_db.conn() as c:
        c.execute("UPDATE nodes SET state = %s WHERE name = %s", (state, name))


def forget_node(name):
    """Drop a node that is gone for good. Only one carrying no live meet."""
    with cloud_db.conn() as c:
        c.execute(
            "DELETE FROM nodes WHERE name = %s AND NOT EXISTS "
            "(SELECT 1 FROM meets WHERE node = %s AND live)",
            (name, name),
        )
