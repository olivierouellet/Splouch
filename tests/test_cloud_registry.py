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
    # Far ahead: a retired meet expires at midnight after its date, and a date
    # that has passed would be swept before a test could read it back.
    "meet_date": "2099-06-04",
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
    assert rec["expires_at"] == "2099-06-05T00:00:00"


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
    assert reg.heartbeat("ca1", 1, [a])["retired"] == [b]
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


# ── Moves ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def held(key):
    """A live meet on ca1 · w1, with w2 free to take it."""
    _node(workers=2)
    return reg.register(key, "uid", META, "ca1", 1)["meet_id"]


def test_a_move_points_the_meet_at_its_new_worker(key, held):
    assert reg.move(held, "ca1", 2)
    assert reg.assign(key, "uid")["worker"] == 2, "the Pi's next assign lands there"


def test_the_old_worker_is_told_to_let_go(held):
    reg.move(held, "ca1", 2)
    moves = reg.heartbeat("ca1", 1, [held], host="https://ca1.example", workers=2)
    assert moves["moves"] == [
        {
            "meet_id": held,
            "base": "https://ca1.example/w2",
            "url": f"https://ca1.example/w2/mobile?meet={held}",
        }
    ]
    assert reg.heartbeat("ca1", 2, [], workers=2)["moves"] == []


def test_the_old_worker_s_disconnect_does_not_retire_a_moved_meet(held):
    reg.move(held, "ca1", 2)
    reg.retire(held, "ca1", 1)
    assert reg.get(held)["live"]


def test_the_new_worker_gives_the_pi_time_to_arrive(held):
    reg.move(held, "ca1", 2)
    assert reg.heartbeat("ca1", 2, [], workers=2)["retired"] == []
    assert reg.get(held)["live"]


def test_the_pi_arriving_completes_the_move(key, held):
    reg.move(held, "ca1", 2)
    reg.register(key, "uid", META, "ca1", 2)
    assert reg.heartbeat("ca1", 1, [held], workers=2)["moves"] == []


@pytest.mark.parametrize(
    "target",
    [("ca1", 1), ("ca1", 3), ("nowhere", 1)],
    ids=["same", "no-such-worker", "no-such-node"],
)
def test_a_move_nowhere_useful_is_refused(held, target):
    assert not reg.move(held, *target)


def test_a_retained_meet_cannot_be_moved(held):
    reg.retire(held, "ca1", 1)
    assert not reg.move(held, "ca1", 2)


def test_a_live_meet_s_page_is_on_its_worker_and_a_retained_one_anywhere(key, held):
    (row,) = reg.list_meets()
    assert reg.page_url(row) == f"https://ca1.example/w1/mobile?meet={held}"
    reg.retire(held, "ca1", 1)
    (row,) = reg.list_meets()
    assert reg.page_url(row) == f"/mobile?meet={held}"


def test_a_record_carries_its_node_s_address(held):
    assert reg.get(held)["node_url"] == "https://ca1.example"


# ── Nodes ──────────────────────────────────────────────────────────────────────


def test_nodes_count_their_live_meets_and_attendees(held):
    reg.heartbeat("ca1", 1, [held], workers=2, attendees={held: 12})
    (node,) = reg.nodes()
    assert (node["meets"], node["attendees"]) == (1, 12)


def test_a_draining_node_comes_back(key, held):
    reg.set_node_state("ca1", "draining")
    with pytest.raises(reg.NoNode):
        reg.assign(key, "other")
    reg.set_node_state("ca1", "active")
    assert reg.assign(key, "other")["node"] == "ca1"


def test_a_node_carrying_a_live_meet_is_not_forgotten(held):
    reg.forget_node("ca1")
    assert [n["name"] for n in reg.nodes()] == ["ca1"]
    reg.retire(held, "ca1", 1)
    reg.forget_node("ca1")
    assert reg.nodes() == []


# ── The picker's view (`app.md` `C-11`, `P-01`) ────────────────────────────────


def test_the_list_carries_the_organizer_s_country_and_province(key, held):
    (row,) = reg.list_meets()
    assert (row["country"], row["province"]) == ("CA", "QC")


def test_a_live_meet_is_reached_at_its_worker_and_a_retained_one_here(held):
    (row,) = reg.list_meets()
    assert reg.meet_base(row, "https://splouch.org") == "https://ca1.example/w1"
    reg.retire(held, "ca1", 1)
    (row,) = reg.list_meets()
    assert reg.meet_base(row, "https://splouch.org/") == "https://splouch.org"


# ── Rolling updates ────────────────────────────────────────────────────────────


def _nodes(*names, version="v1"):
    for name in names:
        reg.heartbeat(
            name, 1, [], host=f"https://{name}.example", region="ca", version=version
        )


def _target(name):
    return next(n for n in reg.nodes() if n["name"] == name)["target_version"]


def test_a_rollout_releases_one_node_at_a_time(pg):
    _nodes("ca1", "ca2")
    reg.start_rollout("v2")
    assert (_target("ca1"), _target("ca2")) == ("v2", None)
    assert reg.rollout()["state"] == "running"
    reg.advance_rollout()
    assert _target("ca2") is None, "ca1 has not reported v2 yet"


def test_a_node_learns_its_target_from_its_heartbeat(pg):
    _nodes("ca1")
    reg.start_rollout("v2")
    assert reg.heartbeat("ca1", 1, [], version="v1")["update_to"] == "v2"
    assert reg.heartbeat("ca1", 1, [], version="v2")["update_to"] is None


def test_the_next_node_goes_when_the_last_reports_the_version(pg):
    _nodes("ca1", "ca2")
    reg.start_rollout("v2")
    reg.heartbeat("ca1", 1, [], version="v2")
    reg.advance_rollout()
    assert _target("ca2") == "v2"
    reg.heartbeat("ca2", 1, [], version="v2")
    assert reg.advance_rollout()["state"] == "done"


def test_a_node_that_never_comes_back_stops_the_rollout(pg):
    _nodes("ca1", "ca2")
    reg.start_rollout("v2")
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
        seconds=reg.ROLLOUT_TIMEOUT_SECS + 60
    )
    reg.heartbeat("ca2", 1, [], version="v1")  # still reporting
    r = reg.advance_rollout(now=later)
    assert (r["state"], r["note"]) == ("failed", "ca1")
    assert _target("ca2") is None, "nothing else is released after a failure"


def test_a_stopped_rollout_releases_nothing_more(pg):
    _nodes("ca1", "ca2")
    reg.start_rollout("v2")
    reg.stop_rollout()
    reg.heartbeat("ca1", 1, [], version="v2")
    reg.advance_rollout()
    assert _target("ca2") is None and reg.rollout()["state"] == "stopped"


def test_a_silent_node_is_not_waited_for(pg):
    _nodes("ca1", "ca2")
    import cloud_db

    with cloud_db.conn() as c:
        c.execute(
            "UPDATE nodes SET last_seen = now() - interval '1 hour' WHERE name = 'ca2'"
        )
    reg.start_rollout("v2")
    reg.heartbeat("ca1", 1, [], version="v2")
    assert reg.advance_rollout()["state"] == "done"


# ── Running, not just connected ────────────────────────────────────────────────

NOW = datetime.datetime(2026, 10, 4, 12, 0, tzinfo=datetime.UTC)


def test_recent_board_frames_mean_running():
    m = {"last_frame_at": NOW - datetime.timedelta(minutes=90), "session_dates": []}
    assert reg.running(m, NOW)
    m["last_frame_at"] = NOW - datetime.timedelta(hours=3)
    assert not reg.running(m, NOW)


def test_a_session_day_at_the_pool_means_running():
    """12:00 UTC is still Saturday in Vancouver (UTC−7) but already... Saturday too;
    01:00 UTC on Sunday is Saturday evening there."""
    m = {"session_dates": ["2026-10-03"], "utc_offset": -7 * 60}
    late = datetime.datetime(2026, 10, 4, 1, 0, tzinfo=datetime.UTC)
    assert reg.running(m, late), "Saturday 18:00 at the pool"
    assert not reg.running(m, NOW), "Sunday 05:00 at the pool"


def test_a_pi_plugged_in_a_week_ahead_is_not_running():
    m = {"session_dates": ["2026-10-11", "2026-10-12"], "utc_offset": -4 * 60}
    assert not reg.running(m, NOW)


def test_an_older_pi_is_judged_by_its_last_date():
    assert reg.running(
        {"session_dates": [], "meet_date": "2026-10-04", "utc_offset": 0}, NOW
    )


def test_register_keeps_only_real_dates_and_offsets(key):
    meta = {
        **META,
        "session_dates": ["2026-10-11", "nope", "2026-10-11"],
        "utc_offset_minutes": 99999,
    }
    mid = reg.register(key, "uid", meta, "ca1", 1)["meet_id"]
    import cloud_db

    with cloud_db.conn() as c:
        row = c.execute(
            "SELECT session_dates, utc_offset FROM meets WHERE id = %s", (mid,)
        ).fetchone()
    assert (row["session_dates"], row["utc_offset"]) == (["2026-10-11"], None)


def _ahead(key, uid="uid", **extra):
    """A live meet on ca1 whose sessions are next week."""
    meta = {**META, "session_dates": ["2099-01-01"], "utc_offset_minutes": 0, **extra}
    return reg.register(key, uid, meta, "ca1", 1)["meet_id"]


def test_a_pi_plugged_in_ahead_does_not_hold_a_rollout_back(key):
    _nodes("ca1")
    _ahead(key)
    reg.start_rollout("v2")
    assert _target("ca1") == "v2"


def test_a_meet_in_progress_holds_its_node(key):
    _nodes("ca1")
    mid = _ahead(key)
    import time as _time

    reg.heartbeat("ca1", 1, [mid], version="v1", frames={mid: _time.time()})
    reg.start_rollout("v2")
    r = reg.rollout()
    assert (r["state"], r["note"]) == ("waiting", "ca1") and _target("ca1") is None


def test_force_goes_through_a_meet_in_progress(key):
    _nodes("ca1")
    mid = _ahead(key)
    import time as _time

    reg.heartbeat("ca1", 1, [mid], version="v1", frames={mid: _time.time()})
    reg.start_rollout("v2", force=True)
    assert _target("ca1") == "v2"


def test_a_scheduled_rollout_waits_for_its_hour(pg):
    _nodes("ca1")
    # Just ahead of now, so the node still counts as reporting when the hour comes.
    at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=30)
    reg.start_rollout("v2", not_before=at)
    assert reg.rollout()["state"] == "scheduled" and _target("ca1") is None
    reg.advance_rollout(now=at - datetime.timedelta(seconds=1))
    assert _target("ca1") is None
    assert reg.advance_rollout(now=at)["state"] == "running"
    assert _target("ca1") == "v2"


def test_a_scheduled_rollout_can_be_stopped(pg):
    _nodes("ca1")
    reg.start_rollout(
        "v2", not_before=datetime.datetime(2099, 1, 1, tzinfo=datetime.UTC)
    )
    reg.stop_rollout()
    assert reg.rollout()["state"] == "stopped"
