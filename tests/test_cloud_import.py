"""A pre-split relay's data directory comes across whole (cloud/cloud_import.py).

The files are the shape the single-process relay wrote: `keys.json`,
`credentials.json`, per-meet files under `retained/`, and `analytics.db`. Running
the import twice must not duplicate anything.
"""

import json
import sqlite3

import pytest

import cloud_auth
import cloud_import
import cloud_registry

pytestmark = pytest.mark.usefixtures("pg")


@pytest.fixture
def legacy(tmp_path):
    (tmp_path / "keys.json").write_text(
        json.dumps(
            {
                "k1": {
                    "organizer": "Club",
                    "created": "2025-03-01",
                    "active": True,
                    "meet_id": "legacy01",
                }
            }
        )
    )
    (tmp_path / "credentials.json").write_text(
        json.dumps(
            {
                "user": "pool",
                "password_hash": "h",
                "salt": "s",
                "picker_title": "Splouch QC",
                "analytics_enabled": True,
            }
        )
    )
    ret = tmp_path / "retained"
    ret.mkdir()
    (ret / "m1.json").write_text(
        json.dumps(
            {
                "organizer": "Club",
                "relay_key": "k1",
                "name": "Coupe",
                "meet_date": "2099-01-01",
                "settings": {"locale": "fr"},
                "expires_at": "2099-01-02T00:00:00",
            }
        )
    )
    (ret / "m1.schedule.json").write_text(json.dumps({"events": [[1, [1]]]}))
    (ret / "m1.icon").write_text("SUNPTg==")
    db = sqlite3.connect(tmp_path / "analytics.db")
    db.execute(
        "CREATE TABLE connections (meet_id TEXT, visitor_id TEXT, ts INTEGER, namespace TEXT)"
    )
    db.executemany(
        "INSERT INTO connections VALUES (?, ?, ?, ?)",
        [("m1", "a", 1, "scoreboard"), ("m1", "b", 2, "results")],
    )
    db.commit()
    db.close()
    return tmp_path


def test_everything_comes_across(legacy):
    cloud_import.import_dir(str(legacy))
    assert cloud_auth.load_keys()["k1"]["organizer"] == "Club"
    creds = cloud_auth.load_creds()
    assert creds["user"] == "pool" and creds["picker_title"] == "Splouch QC"
    rec = cloud_registry.get("m1")
    assert rec["name"] == "Coupe" and not rec["live"] and rec["node"] == "ca1"
    assert rec["relay_key"] == "k1"
    assert "schedule_data" not in rec, "the card only"
    import cloud_meetstore

    stored = cloud_meetstore.get("m1")
    assert stored["schedule_data"] == {"events": [[1, [1]]]}
    assert stored["settings"]["home_icon_b64"] == "SUNPTg=="
    assert cloud_registry.register("k1", "", {}, "ca1", 1)["meet_id"] == "legacy01"


def test_running_it_twice_changes_nothing(legacy):
    first = cloud_import.import_dir(str(legacy))
    assert cloud_import.import_dir(str(legacy)) == first
    assert len(cloud_registry.backup()) == 1


def test_the_attendance_file_stays_where_the_node_reads_it(legacy, monkeypatch):
    """`analytics.db` is not imported: on the box's volume it already is the node's
    store, so the counts carry on with no copy and no id reaches Postgres."""
    import cloud_attendance
    import cloud_paths

    cloud_import.import_dir(str(legacy))
    monkeypatch.setattr(cloud_paths, "ANALYTICS_FILE", str(legacy / "analytics.db"))
    cloud_attendance.close()
    try:
        assert cloud_attendance.counts("m1")["all"] == 2
    finally:
        cloud_attendance.close()
    with cloud_import.cloud_db.conn() as c:
        assert c.execute("SELECT count(*) AS n FROM analytics").fetchone()["n"] == 0


def test_rows_the_control_plane_kept_go_back_to_the_node(tmp_path, monkeypatch):
    import cloud_attendance
    import cloud_paths

    with cloud_import.cloud_db.conn() as c:
        c.execute(
            "INSERT INTO analytics VALUES ('m9', 'a', 1, 'scoreboard'), "
            "('m9', 'b', 2, 'results')"
        )
    monkeypatch.setattr(cloud_paths, "ANALYTICS_FILE", str(tmp_path / "analytics.db"))
    cloud_attendance.close()
    try:
        assert cloud_import.handoff_attendance() == 2
        assert cloud_attendance.counts("m9")["all"] == 2
    finally:
        cloud_attendance.close()
    with cloud_import.cloud_db.conn() as c:
        assert c.execute("SELECT count(*) AS n FROM analytics").fetchone()["n"] == 0
