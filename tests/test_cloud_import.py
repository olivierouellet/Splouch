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
    assert rec["name"] == "Coupe" and not rec["live"]
    assert rec["schedule_data"] == {"events": [[1, [1]]]}
    assert rec["settings"]["home_icon_b64"] == "SUNPTg=="
    assert rec["relay_key"] == "k1"
    assert cloud_registry.register("k1", "", {}, "ca1", 1)["meet_id"] == "legacy01"


def test_running_it_twice_duplicates_nothing(legacy):
    cloud_import.import_dir(str(legacy))
    second = cloud_import.import_dir(str(legacy))
    assert second["attendance rows (skipped: already imported)"] == 2
    import cloud_analytics

    assert cloud_analytics.attendee_count("m1", 0) == 2
