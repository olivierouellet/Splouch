"""Every meet the relay knows, live or retained — the control plane's registry.

One row per meet in Postgres (`cloud_db`): its **picker card** — name, date,
location, organizer, where it lives, expiry — and its picker image. Nothing else
of a meet comes here: its start list (every athlete entered, with their club), its
settings and its home icon stay on the node that carries it (`cloud_meetstore`),
live or finished, so they stay in the meet's region. A worker reports to this
registry over the internal API: a Pi registers (the key is checked here), and
disconnects (the meet is retired with an expiry). The picker and the admin panel
read from here.

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
import cloud_meetstore
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
    "m.last_frame_at, m.session_dates, m.utc_offset, "
    "coalesce(o.country, '') AS country, coalesce(o.province, '') AS province"
)


def worker_url(node_url, worker):
    """A worker's public base: its node's URL and its `/wN` prefix
    (docs/architecture/scaling.md). Caddy strips the prefix on the way in."""
    return f"{(node_url or '').rstrip('/')}/w{int(worker)}"


def meet_base(meet, here):
    """Where a client reaches a meet (`app.md` `C-11`): a live meet's worker; a
    finished one's node, whose every worker reads the node's store (worker 1); or,
    for a card with no node, `here`."""
    if meet.get("node_url") and meet.get("worker") and meet.get("live"):
        return worker_url(meet["node_url"], meet["worker"])
    if meet.get("node_url"):
        return worker_url(meet["node_url"], 1)
    return here.rstrip("/")


def page_url(meet):
    """Where a meet's page is served: on its base (`meet_base`)."""
    return f"{meet_base(meet, '')}/mobile?meet={meet['id']}"


def meet_id_for(key, meet_uid):
    """Deterministic meet id for a (relay key, meet_uid) pair.

    Lets one relay key publish several meets — e.g. a meet split across days into
    separate LENEX files — each landing on its own stable picker card and
    reattaching on reload.
    """
    return hashlib.sha256(f"{key}:{meet_uid}".encode()).hexdigest()[:11]


compute_expiry = cloud_meetstore.compute_expiry


def _card_settings(settings):
    """(what the registry keeps of a meet's settings, its picker image): the console
    and the language, which the admin table shows — never labels, theme or icon."""
    settings = dict(settings or {})
    picker = settings.pop("picker_image_b64", "") or ""
    keep = {k: settings[k] for k in ("console", "locale") if settings.get(k)}
    return keep, picker


def _record(row):
    """A row as the card shape workers and backups use: no start list, no icon."""
    settings = dict(row["settings"] or {})
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

    Returns `{"meet_id", "organizer", "connected_at"}`. Only the card is stored; the
    worker keeps the rest on its node. `location` is where the organizer says it is based (`{"country", "province"}`,
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
    settings, picker = _card_settings(meta.get("settings"))
    now = datetime.datetime.now()
    with cloud_db.conn() as c:
        prev = c.execute(
            "SELECT live, connected_at FROM meets WHERE id = %s", (meet_id,)
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
            "app_window_title, meet_date, settings, picker_image_b64, "
            "live, node, worker, connected_at, last_seen, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s, %s, NULL) "
            "ON CONFLICT (id) DO UPDATE SET organizer_key = EXCLUDED.organizer_key, "
            "organizer = EXCLUDED.organizer, name = EXCLUDED.name, "
            "location = EXCLUDED.location, sport = EXCLUDED.sport, "
            "app_window_title = EXCLUDED.app_window_title, "
            "meet_date = EXCLUDED.meet_date, settings = EXCLUDED.settings, "
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
                picker,
                node,
                worker,
                connected_at,
                now,
            ),
        )
        # Which days the meet runs, and which day it is at the pool: what tells a
        # running meet from a Pi plugged in ahead (`running`).
        c.execute(
            "UPDATE meets SET session_dates = %s, utc_offset = %s WHERE id = %s",
            (
                Jsonb(_dates(meta.get("session_dates"))),
                _offset(meta.get("utc_offset_minutes")),
                meet_id,
            ),
        )
    return {
        "meet_id": meet_id,
        "organizer": org["name"],
        "connected_at": connected_at,
    }


def _dates(value):
    """Session dates as sent: ISO `YYYY-MM-DD` strings only, at most 60."""
    if not isinstance(value, list):
        return []
    out = []
    for d in value[:60]:
        try:
            out.append(datetime.date.fromisoformat(str(d)).isoformat())
        except ValueError:
            continue
    return sorted(set(out))


def _offset(value):
    """A UTC offset in minutes, within the ones that exist, else None."""
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return None
    return minutes if -12 * 60 <= minutes <= 14 * 60 else None


# A meet is running — and a rollout waits for it — while its console sent a board
# frame this recently, or on one of its session days at the pool. A Pi plugged in a
# week ahead, sending only its schedule, is connected but not running.
RUNNING_FRAME_SECS = 2 * 3600


def running(meet, now=None):
    """Whether a live meet is running: recent board frames, or a session day today
    in the pool's own time (its UTC offset; the server's when unknown). A Pi too
    old to send session dates is judged by its last one, `meet_date`."""
    now = now or datetime.datetime.now(datetime.UTC)
    last = meet.get("last_frame_at")
    if last and (now - last).total_seconds() < RUNNING_FRAME_SECS:
        return True
    offset = meet.get("utc_offset")
    today = (
        (now + datetime.timedelta(minutes=offset)).date()
        if offset is not None
        else now.astimezone().date()
    )
    days = list(meet.get("session_dates") or []) or [meet.get("meet_date") or ""]
    return today.isoformat() in days


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
    version="",
    frames=None,
):
    """A worker says which meets it holds, and how many attendees each has.

    Returns `{"retired": [ids], "revoked": [ids], "moves": [{"meet_id", "url"}],
    "update_to"}`: the meets it no longer names are retired, the revoked ones are
    held here under a key the admin has withdrawn, the moves are meets the admin moved off
    this worker, with the page URL their attendees should go to, and `update_to`
    is the version a rollout released this node to, while it runs another.
    """
    now = datetime.datetime.now()
    grace = now - datetime.timedelta(seconds=MOVE_GRACE_SECS)
    with cloud_db.conn() as c:
        c.execute(
            "INSERT INTO nodes (name, region, host, workers, wg_pubkey, version, "
            "last_seen) VALUES (%s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (name) DO UPDATE SET "
            "region = COALESCE(EXCLUDED.region, nodes.region), host = EXCLUDED.host, "
            "workers = EXCLUDED.workers, "
            "wg_pubkey = COALESCE(NULLIF(EXCLUDED.wg_pubkey, ''), nodes.wg_pubkey), "
            "version = COALESCE(NULLIF(EXCLUDED.version, ''), nodes.version), "
            "last_seen = now()",
            (node, region or None, host, workers, wg_pubkey, version or ""),
        )
        target = c.execute(
            "SELECT target_version, version FROM nodes WHERE name = %s", (node,)
        ).fetchone()
        c.execute(
            "UPDATE meets SET last_seen = %s WHERE live AND node = %s AND worker = %s "
            "AND id = ANY(%s)",
            (now, node, worker, list(live_ids)),
        )
        for meet_id, at in (frames or {}).items():
            c.execute(
                "UPDATE meets SET last_frame_at = to_timestamp(%s) "
                "WHERE id = %s AND live AND node = %s AND worker = %s",
                (float(at), meet_id, node, worker),
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
        # Live here under a key the admin has since revoked or deleted. The key is
        # checked only when a Pi registers, so without this a revoked organizer's
        # connected Pi kept publishing until it happened to drop.
        revoked = [
            r["id"]
            for r in c.execute(
                "SELECT m.id FROM meets m "
                "LEFT JOIN organizers o ON o.key = m.organizer_key "
                "WHERE m.live AND m.node = %s AND m.worker = %s AND m.id = ANY(%s) "
                "AND (o.key IS NULL OR NOT o.active)",
                (node, worker, list(live_ids)),
            ).fetchall()
        ]
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
    update_to = (
        target["target_version"]
        if target["target_version"] and target["target_version"] != target["version"]
        else None
    )
    return {
        "retired": retired,
        "revoked": revoked,
        "update_to": update_to,
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
    assert column == "picker_image_b64"
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


def list_meets(reachable_only=False):
    """Live and retained meets, live first then by name — list columns only.

    `reachable_only` (the picker): a finished meet is served by its node, so while
    that node is not reporting its card is left out rather than leading to an error;
    it comes back with the node, until it expires.
    """
    sweep_expired()
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        seconds=SILENT_AFTER_SECS
    )
    where = "WHERE m.live OR n.last_seen >= %s" if reachable_only else ""
    with cloud_db.conn() as c:
        return c.execute(
            f"SELECT {_LIST_COLUMNS} FROM meets m LEFT JOIN nodes n ON n.name = m.node "
            f"LEFT JOIN organizers o ON o.key = m.organizer_key {where} "
            "ORDER BY NOT m.live, lower(m.name), m.id",
            (cutoff,) if reachable_only else (),
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


def restore(meets, node=None):
    """Upsert a backup's meet cards, retained. A meet live right now is skipped so
    its fresh state isn't overwritten by a stale backup; nothing else is touched.
    Cards only: a start list, if the backup has one, is not stored here (it lives on
    the node — `cloud_import.py` puts a pre-split box's there). `node` places cards
    with none of their own. Returns the ids written."""
    written = []
    with cloud_db.conn() as c:
        live = {r["id"] for r in c.execute("SELECT id FROM meets WHERE live")}
        keys = {r["key"] for r in c.execute("SELECT key FROM organizers")}
        for meet_id, rec in meets.items():
            if meet_id in live or not isinstance(rec, dict):
                continue
            settings, picker = _card_settings(rec.get("settings"))
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
                "sport, app_window_title, meet_date, settings, picker_image_b64, "
                "node, live, expires_at) VALUES "
                "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s) "
                "ON CONFLICT (id) DO UPDATE SET organizer_key = EXCLUDED.organizer_key, "
                "organizer = EXCLUDED.organizer, name = EXCLUDED.name, "
                "location = EXCLUDED.location, sport = EXCLUDED.sport, "
                "app_window_title = EXCLUDED.app_window_title, "
                "meet_date = EXCLUDED.meet_date, settings = EXCLUDED.settings, "
                "picker_image_b64 = EXCLUDED.picker_image_b64, "
                "node = COALESCE(EXCLUDED.node, meets.node), live = false, "
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
                    picker,
                    rec.get("node") or node,
                    exp,
                ),
            )
            written.append(meet_id)
    return written


def node_meet_ids(node):
    """Every meet the registry places on `node`, live or not — what the node keeps
    in its store; it drops the rest (`cloud_meetstore.keep_only`)."""
    with cloud_db.conn() as c:
        return [
            r["id"] for r in c.execute("SELECT id FROM meets WHERE node = %s", (node,))
        ]


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


# ── Rolling updates ────────────────────────────────────────────────────────────
#
# Pulled, never pushed: a rollout only records a version and, one node at a time,
# a node's target. The node's worker 1 sees its target in a heartbeat reply and calls
# the node's own deploy webhook; the node is done when its heartbeat reports the
# version. Nodes are released by name, each only while it carries no *running* meet
# (`running`: a Pi plugged in ahead does not hold it back; the admin may also force
# the rollout, or schedule it for the night); a node silent this long after release
# stops the rollout for the admin to look at.

ROLLOUT_TIMEOUT_SECS = 15 * 60


def _settings_get(c, name):
    row = c.execute("SELECT value FROM settings WHERE name = %s", (name,)).fetchone()
    return row["value"] if row else None


def _settings_put(c, name, value):
    c.execute(
        "INSERT INTO settings (name, value) VALUES (%s, %s) "
        "ON CONFLICT (name) DO UPDATE SET value = EXCLUDED.value",
        (name, Jsonb(value)),
    )


def rollout():
    """The current rollout — `{"version", "state", "note", "force", "not_before"}` —
    or None."""
    with cloud_db.conn() as c:
        return _settings_get(c, "rollout")


def start_rollout(version, force=False, not_before=None):
    """Roll `version` out to every node — now, or from `not_before` (an aware
    datetime) — waiting for running meets unless `force`."""
    when = not_before.astimezone(datetime.UTC).isoformat() if not_before else None
    with cloud_db.conn() as c:
        c.execute("UPDATE nodes SET target_version = NULL, target_set_at = NULL")
        _settings_put(
            c,
            "rollout",
            {
                "version": version,
                "state": "scheduled" if when else "running",
                "note": "",
                "force": bool(force),
                "not_before": when,
            },
        )
    advance_rollout()


def stop_rollout():
    """Stop releasing nodes. A node already updating finishes on its own."""
    with cloud_db.conn() as c:
        r = _settings_get(c, "rollout")
        if r and r.get("state") in ("running", "waiting", "scheduled"):
            _settings_put(c, "rollout", {**r, "state": "stopped", "note": ""})


def advance_rollout(now=None):
    """One step: start a scheduled rollout whose time has come, wait for the node
    updating, else release the next free one — a node with no *running* meet, or
    any node when forced. Run by the control plane's maintenance pass. Returns the
    rollout, or None."""
    now = now or datetime.datetime.now(datetime.UTC)
    cutoff = now - datetime.timedelta(seconds=SILENT_AFTER_SECS)
    with cloud_db.conn() as c:
        r = _settings_get(c, "rollout")
        if r and r.get("state") == "scheduled":
            if datetime.datetime.fromisoformat(r["not_before"]) > now:
                return r
            r = {**r, "state": "running"}
        if not r or r.get("state") not in ("running", "waiting"):
            return r
        version = r["version"]
        nodes = c.execute("SELECT * FROM nodes ORDER BY name").fetchall()
        busy = {}
        if not r.get("force"):
            for m in c.execute(
                "SELECT node, last_frame_at, session_dates, utc_offset, meet_date "
                "FROM meets WHERE live"
            ).fetchall():
                if running(m, now):
                    busy[m["node"]] = True
        updating = [
            n
            for n in nodes
            if n["target_version"] and n["version"] != n["target_version"]
        ]
        if updating:
            n = updating[0]
            if n["target_set_at"] < now - datetime.timedelta(
                seconds=ROLLOUT_TIMEOUT_SECS
            ):
                c.execute(
                    "UPDATE nodes SET target_version = NULL WHERE name = %s",
                    (n["name"],),
                )
                r = {**r, "state": "failed", "note": n["name"]}
            else:
                r = {**r, "state": "running", "note": n["name"]}
            _settings_put(c, "rollout", r)
            return r
        pending = [
            n
            for n in nodes
            if n["version"] != version and n["last_seen"] and n["last_seen"] >= cutoff
        ]
        if not pending:
            r = {**r, "state": "done", "note": ""}
        else:
            free = [n for n in pending if not busy.get(n["name"])]
            if free:
                c.execute(
                    "UPDATE nodes SET target_version = %s, target_set_at = %s "
                    "WHERE name = %s",
                    (version, now, free[0]["name"]),
                )
                r = {**r, "state": "running", "note": free[0]["name"]}
            else:
                names = ", ".join(n["name"] for n in pending)
                r = {**r, "state": "waiting", "note": names}
        _settings_put(c, "rollout", r)
        return r
