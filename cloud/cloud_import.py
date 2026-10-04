"""Bring a pre-split relay's data directory into the control plane's Postgres.

Before the scaling split (docs/architecture/scaling.md) one relay process kept
everything in files under its data directory:

    keys.json             organizers and their relay keys
    credentials.json      the admin login and the server settings
    retained/<id>.*       each retained meet: metadata, schedule, two images
    analytics.db          attendance counts (SQLite)

Run once, on the control plane, with that directory mounted:

    docker compose run --rm -v <old data volume>:/legacy control \\
        python cloud_import.py /legacy

Safe to run again: organizers and meets are upserted, the login and settings are
overwritten with the file's, and analytics rows already imported are skipped.
A live meet in the store is never overwritten by a retained one from the files.
"""

import glob
import json
import os
import sqlite3
import sys

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


def analytics_rows(data_dir):
    path = os.path.join(data_dir, "analytics.db")
    if not os.path.exists(path):
        return []
    db = sqlite3.connect(path)
    try:
        return db.execute(
            "SELECT meet_id, visitor_id, ts, namespace FROM connections"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()


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

    rows = analytics_rows(data_dir)
    with cloud_db.conn() as c:
        already = c.execute("SELECT count(*) AS n FROM analytics").fetchone()["n"]
        if rows and not already:
            with c.cursor() as cur:
                cur.executemany("INSERT INTO analytics VALUES (%s, %s, %s, %s)", rows)
            counts["attendance rows"] = len(rows)
        elif rows:
            counts["attendance rows (skipped: already imported)"] = len(rows)
    return counts


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DATA_DIR", "/data")
    for what, n in import_dir(source).items():
        print(f"{what}: {n}")
