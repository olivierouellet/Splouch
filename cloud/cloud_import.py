"""Bring a pre-split relay's data directory into the control plane's Postgres.

Before the scaling split (docs/architecture/scaling.md) one relay process kept
everything in files under its data directory:

    keys.json             organizers and their relay keys
    credentials.json      the admin login and the server settings
    retained/<id>.*       each retained meet: metadata, schedule, two images
    analytics.db          attendance counts (SQLite)

Run once, on the control plane, with that directory mounted:

    docker compose run --rm control python cloud_import.py /data

Safe to run again: organizers and meets are upserted, the login and settings are
overwritten with the file's. A live meet in the store is never overwritten by a
retained one from the files.

`analytics.db` is not imported: attendance ids stay on the node that carries the
meet (`cloud_attendance`), and that file — on the box's data volume — is already
the node's store.

    docker compose run --rm control python cloud_import.py --attendance

hands back the attendance rows the control plane stored itself before it kept
numbers only, into this box's node store, and empties its table.
"""

import glob
import json
import os
import sys

import cloud_attendance
import cloud_auth
import cloud_db
import cloud_registry


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def retained_meets(data_dir):
    """Every retained meet under `data_dir/retained`, as full records."""
    root = os.path.join(data_dir, "retained")
    out = {}
    for path in glob.glob(os.path.join(root, "*.json")):
        if path.endswith(".schedule.json"):
            continue
        meet_id = os.path.basename(path)[: -len(".json")]
        rec = _read_json(path, None)
        if not isinstance(rec, dict):
            continue
        schedule = _read_json(os.path.join(root, meet_id + ".schedule.json"), None)
        if schedule:
            rec["schedule_data"] = schedule
        settings = rec.setdefault("settings", {})
        for suffix, field in (
            (".icon", "home_icon_b64"),
            (".picker", "picker_image_b64"),
        ):
            blob = _read_text(os.path.join(root, meet_id + suffix))
            if blob:
                settings[field] = blob
        out[meet_id] = rec
    return out


def handoff_attendance():
    """Move the control plane's own attendance rows (stored by early versions of
    the split) into this box's node store, then empty the table. Returns the count.

    Only meaningful where the control plane and a node share the data volume — the
    one-box deployment this was ever run on."""
    cloud_db.migrate()
    with cloud_db.conn() as c:
        rows = c.execute(
            "SELECT meet_id, visitor_id, ts, namespace FROM analytics"
        ).fetchall()
    cloud_attendance.record(
        [(r["meet_id"], r["visitor_id"], r["ts"], r["namespace"]) for r in rows]
    )
    with cloud_db.conn() as c:
        c.execute("DELETE FROM analytics")
    return len(rows)


def import_dir(data_dir):
    """Import everything; returns what was written, for the operator to check."""
    cloud_db.migrate()
    counts = {}

    keys = _read_json(os.path.join(data_dir, "keys.json"), {})
    if isinstance(keys, dict):
        cloud_auth.restore_keys(keys)
    counts["organizers"] = len(keys) if isinstance(keys, dict) else 0

    creds = _read_json(os.path.join(data_dir, "credentials.json"), None)
    if isinstance(creds, dict) and all(
        k in creds for k in ("user", "password_hash", "salt")
    ):
        cloud_auth.save_creds(creds)
        counts["login and settings"] = 1

    counts["retained meets"] = len(cloud_registry.restore(retained_meets(data_dir)))
    return counts


if __name__ == "__main__":
    if sys.argv[1:] == ["--attendance"]:
        print(f"attendance rows handed to the node: {handoff_attendance()}")
        sys.exit(0)
    source = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DATA_DIR", "/data")
    for what, n in import_dir(source).items():
        print(f"{what}: {n}")
