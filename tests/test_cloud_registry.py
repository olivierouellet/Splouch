"""The control plane's registry: who may publish, and what it remembers of a meet.

A worker reports a register, a schedule and a disconnect; the registry turns them
into one row per meet (cloud/cloud_registry.py). The things worth pinning are the
ones a worker cannot see: a revoked key is refused, an id stays stable across
reconnects, an old socket closing cannot retire a meet that moved, and a worker
that dies stops vouching for its meets and they retire on their own.
"""

import datetime

import pytest

import cloud_auth
import cloud_registry as reg

pytestmark = pytest.mark.usefixtures("pg")

META = {
    "name": "Coupe",
    "location": "Montréal",
    "sport": "Swimming",
    "meet_date": "2026-10-04",
    "settings": {"locale": "fr", "home_icon_b64": "SUNPTg==", "picker_image_b64": ""},
}


@pytest.fixture
def key():
    return cloud_auth.add_organizer("Club", country="CA", province="QC", region="ca")


def test_a_registered_meet_is_live_with_its_metadata(key):
    out = reg.register(key, "uid-1", META, "ca1", 1)
    rec = reg.get(out["meet_id"])
    assert rec["live"] and rec["node"] == "ca1" and rec["worker"] == 1
    assert rec["organizer"] == "Club"
    assert rec["name"] == "Coupe" and rec["settings"]["locale"] == "fr"
    assert rec["settings"]["home_icon_b64"] == "SUNPTg=="
    assert rec["expires_at"] is None


def test_the_id_is_stable_per_key_and_meet_uid(key):
    a = reg.register(key, "uid-1", META, "ca1", 1)["meet_id"]
    b = reg.register(key, "uid-1", META, "ca1", 2)["meet_id"]
    c = reg.register(key, "uid-2", META, "ca1", 1)["meet_id"]
    assert a == b == reg.meet_id_for(key, "uid-1")
    assert c != a


def test_a_relay_without_a_meet_uid_keeps_one_id(key):
    a = reg.register(key, "", META, "ca1", 1)["meet_id"]
    assert reg.register(key, "", META, "ca1", 1)["meet_id"] == a


def test_a_revoked_or_unknown_key_is_refused(key):
    cloud_auth.update_organizer(key, active=False)
    assert reg.register(key, "uid", META, "ca1", 1) is None
    assert reg.register("nope", "uid", META, "ca1", 1) is None


def test_a_re_register_keeps_the_first_connect_time(key):
    first = reg.register(key, "uid", META, "ca1", 1)
    with_new_settings = reg.register(key, "uid", META, "ca1", 1)
    assert with_new_settings["connected_at"] == first["connected_at"]


def test_the_stored_schedule_comes_back_on_reconnect(key):
    mid = reg.register(key, "uid", META, "ca1", 1)["meet_id"]
    reg.set_schedule(mid, {"events": [[1, [1]]]})
    reg.retire(mid, "ca1", 1)
    again = reg.register(key, "uid", META, "ca1", 1)
    assert again["schedule_data"] == {"events": [[1, [1]]]}


def test_a_retired_meet_expires_after_its_date(key):
    mid = reg.register(key, "uid", META, "ca1", 1)["meet_id"]
    reg.retire(mid, "ca1", 1)
    rec = reg.get(mid)
    assert not rec["live"]
    assert rec["expires_at"] == "2026-10-05T00:00:00"


def test_only_the_holder_retires_a_meet(key):
    """A Pi that moved to another worker: the old socket closing must not undo it."""
    mid = reg.register(key, "uid", META, "ca1", 1)["meet_id"]
    reg.register(key, "uid", META, "ca1", 2)
    reg.retire(mid, "ca1", 1)
    assert reg.get(mid)["live"]


def test_a_heartbeat_retires_what_its_worker_no_longer_holds(key):
    a = reg.register(key, "a", META, "ca1", 1)["meet_id"]
    b = reg.register(key, "b", META, "ca1", 1)["meet_id"]
    other = reg.register(key, "c", META, "ca1", 2)["meet_id"]
    assert reg.heartbeat("ca1", 1, [a]) == [b]
    assert reg.get(a)["live"] and not reg.get(b)["live"]
    assert reg.get(other)["live"], "another worker's meet is not this heartbeat's"


def test_a_silent_worker_s_meets_retire_on_their_own(key):
    mid = reg.register(key, "uid", META, "ca1", 1)["meet_id"]
    assert reg.retire_silent(90) == []
    later = datetime.datetime.now() + datetime.timedelta(seconds=120)
    assert reg.retire_silent(90, now=later) == [mid]
    assert not reg.get(mid)["live"]


def test_the_heartbeat_records_the_node(key):
    reg.heartbeat("ca1", 1, [], host="ca1.splouch.org", region="ca", workers=4)
    (node,) = reg.nodes()
    assert node["host"] == "ca1.splouch.org" and node["workers"] == 4


def test_expired_retained_meets_are_swept_and_live_ones_never(key):
    live = reg.register(key, "live", META, "ca1", 1)["meet_id"]
    reg.restore({"old": {"name": "Old", "expires_at": "2000-01-01T00:00:00"}})
    reg.sweep_expired()
    assert reg.get("old") is None
    assert reg.get(live)["live"]


def test_the_list_puts_live_first_and_carries_no_images(key):
    reg.restore({"r": {"name": "A", "settings": {"picker_image_b64": "eA=="}}})
    live = reg.register(key, "uid", {**META, "name": "Z"}, "ca1", 1)["meet_id"]
    rows = reg.list_meets()
    assert [r["id"] for r in rows] == [live, "r"]
    assert rows[1]["has_picker_image"] is True
    assert "picker_image_b64" not in rows[1]


def test_backup_and_restore_round_trip(key):
    mid = reg.register(key, "uid", META, "ca1", 1)["meet_id"]
    reg.set_schedule(mid, {"events": []})
    reg.retire(mid, "ca1", 1)
    saved = reg.backup()
    reg.delete(mid)
    assert reg.get(mid) is None
    assert reg.restore(saved) == [mid]
    rec = reg.get(mid)
    assert rec["settings"]["home_icon_b64"] == "SUNPTg=="
    assert rec["expires_at"] == saved[mid]["expires_at"]


def test_a_restore_never_overwrites_a_live_meet(key):
    mid = reg.register(key, "uid", META, "ca1", 1)["meet_id"]
    assert reg.restore({mid: {"name": "Stale"}}) == []
    assert reg.get(mid)["name"] == "Coupe"


def test_the_admin_cannot_expire_or_delete_a_live_meet(key):
    mid = reg.register(key, "uid", META, "ca1", 1)["meet_id"]
    reg.set_expiry(mid, datetime.datetime(2000, 1, 1))
    reg.delete(mid)
    assert reg.get(mid)["expires_at"] is None


# ── Organizers ─────────────────────────────────────────────────────────────────


def test_an_organizer_keeps_where_it_is_based(key):
    info = cloud_auth.load_keys()[key]
    assert (info["country"], info["province"], info["region"]) == ("CA", "QC", "ca")
    cloud_auth.update_organizer(key, country="US", province="NY", region="us")
    info = cloud_auth.load_keys()[key]
    assert (info["country"], info["province"], info["region"]) == ("US", "NY", "us")


def test_a_pre_scaling_backup_restores(key):
    """`keys.json` had no location, and kept a no-uid relay's id as `meet_id`."""
    cloud_auth.restore_keys(
        {
            "old-key": {
                "organizer": "Old",
                "created": "2025-01-02",
                "active": True,
                "meet_id": "legacy01",
            }
        }
    )
    assert cloud_auth.load_keys()["old-key"]["region"] == ""
    assert reg.register("old-key", "", META, "ca1", 1)["meet_id"] == "legacy01"


def test_a_cleared_setting_is_gone(pg):
    creds = cloud_auth.load_creds()
    creds["picker_logo_b64"] = "eA=="
    cloud_auth.save_creds(creds)
    creds = cloud_auth.load_creds()
    creds.pop("picker_logo_b64")
    cloud_auth.save_creds(creds)
    assert "picker_logo_b64" not in cloud_auth.load_creds()


# ── Assignment ─────────────────────────────────────────────────────────────────


def _node(name="ca1", region="ca", workers=1):
    reg.heartbeat(
        name, 1, [], host=f"https://{name}.example", region=region, workers=workers
    )


def test_a_new_meet_goes_to_the_least_loaded_worker(key):
    _node(workers=2)
    busy = reg.register(key, "busy", META, "ca1", 1)["meet_id"]
    reg.heartbeat("ca1", 1, [busy], workers=2, attendees={busy: 50})
    assert reg.assign(key, "next")["worker"] == 2


def test_a_meet_goes_back_to_the_worker_that_held_it(key):
    _node(workers=2)
    mid = reg.register(key, "uid", META, "ca1", 2)["meet_id"]
    reg.retire(mid, "ca1", 2)
    assert reg.assign(key, "uid")["worker"] == 2


def test_a_silent_node_is_not_offered(key):
    _node()
    import cloud_db

    with cloud_db.conn() as c:
        c.execute("UPDATE nodes SET last_seen = now() - interval '10 minutes'")
    with pytest.raises(reg.NoNode):
        reg.assign(key, "uid")


def test_a_draining_node_is_not_offered(key):
    _node("ca1")
    _node("ca2")
    import cloud_db

    with cloud_db.conn() as c:
        c.execute("UPDATE nodes SET state = 'draining' WHERE name = 'ca1'")
    assert reg.assign(key, "uid")["node"] == "ca2"


def test_an_organizer_without_a_region_may_go_anywhere(pg):
    _node("eu1", region="eu")
    key = cloud_auth.add_organizer("Imported")
    assert reg.assign(key, "uid")["node"] == "eu1"


def test_assign_refuses_a_revoked_key(key):
    _node()
    cloud_auth.update_organizer(key, active=False)
    assert reg.assign(key, "uid") is None
