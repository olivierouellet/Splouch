"""Several cloud admin users, each opening only some of the panel's tabs."""

import base64
import os
import sys
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "cloud"))


class _Req:
    def __init__(self, user, pw, method="GET"):
        tok = base64.b64encode(f"{user}:{pw}".encode()).decode()
        self.method = method
        self.headers = {"Authorization": "Basic " + tok}
        self.client = SimpleNamespace(host="203.0.113.9")
        self.state = SimpleNamespace()


def _status(dep, user, pw):
    try:
        dep(cast(Request, _Req(user, pw)))
        return 200
    except HTTPException as e:
        return e.status_code


@pytest.fixture
def accounts(monkeypatch, tmp_path, pg):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ADMIN_USER", "owner")
    monkeypatch.setenv("ADMIN_PASSWORD", "owner-pw")
    import cloud_auth

    monkeypatch.setattr(cloud_auth, "_admin_fails", {})
    cloud_auth.load_creds()  # seeds the owner, as on a new install
    cloud_auth.add_user("meetdesk", "m-pw", ["meets"])
    cloud_auth.add_user("deputy", "d-pw", ["admin"])
    return cloud_auth


def test_migration_9_adds_the_users_table(pg):
    with pg.conn() as c:
        cols = {
            r["column_name"]
            for r in c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'admin_users'"
            )
        }
    assert {"username", "password_hash", "salt", "roles"} <= cols


@pytest.mark.parametrize(
    "user, pw, tabs",
    [
        ("owner", "owner-pw", {"admin", "meets", "organizers", "appearance"}),
        ("deputy", "d-pw", {"admin", "meets", "organizers", "appearance"}),
        ("meetdesk", "m-pw", {"meets"}),
    ],
)
def test_each_user_opens_only_their_tabs(accounts, user, pw, tabs):
    allowed = {
        r
        for r in accounts.CLOUD_ROLES
        if _status(accounts.require_role(r), user, pw) == 200
    }
    assert allowed == tabs
    assert _status(accounts.require_role(None), user, pw) == 200


def test_a_wrong_password_or_unknown_name_is_refused(accounts):
    assert _status(accounts.require_role(None), "meetdesk", "owner-pw") == 401
    assert _status(accounts.require_role(None), "nobody", "owner-pw") == 401


def test_a_deleted_user_is_shut_out(accounts):
    accounts.delete_user("meetdesk")
    assert _status(accounts.require_role(None), "meetdesk", "m-pw") == 401


def test_the_full_backup_carries_the_users_and_restores_them(accounts):
    dump = accounts.dump_users()
    accounts.delete_user("meetdesk")
    accounts.restore_users(dump)
    assert _status(accounts.require_role("meets"), "meetdesk", "m-pw") == 200
    assert "m-pw" not in repr(dump)


def test_a_user_changes_only_their_own_password(accounts, monkeypatch):
    import cloud_control

    monkeypatch.setattr(cloud_control, "_load_cloud_strings", lambda r: {})

    req = _Req("meetdesk", "m-pw", method="POST")
    req.state.admin_user, req.state.admin_roles = "meetdesk", {"meets"}
    form = {
        "action": "change_credentials",
        "current_password": "m-pw",
        "new_user": "owner",  # not theirs to take
        "new_password": "new-pw",
        "new_password2": "new-pw",
    }
    cloud_control._admin_action(_Form(form), req)
    assert accounts.verify_user("meetdesk", "new-pw") == {"meets"}
    assert accounts.verify_user("owner", "owner-pw") is not None
    assert accounts.load_creds()["user"] == "owner"


class _Form(dict):
    def getlist(self, key):
        v = self.get(key, [])
        return v if isinstance(v, list) else [v]


@pytest.mark.parametrize(
    "action, roles, allowed",
    [
        ("delete_meet", {"meets"}, True),
        ("revoke", {"meets"}, False),
        ("set_analytics", {"organizers"}, False),
        ("rollout_stop", {"meets", "organizers", "appearance"}, False),
        ("user_add", {"meets"}, False),
        ("change_credentials", {"meets"}, True),
    ],
)
def test_each_panel_action_needs_its_tab(monkeypatch, action, roles, allowed):
    """No database: every action handler is stubbed, only the gate is real."""
    import cloud_control

    monkeypatch.setattr(cloud_control, "_load_cloud_strings", lambda r: {})
    monkeypatch.setattr(cloud_control, "_admin_page", lambda *a, **k: "page")
    monkeypatch.setattr(cloud_control, "_load_creds", lambda: {"user": "o"})
    monkeypatch.setattr(cloud_control, "_save_creds", lambda c: None)
    monkeypatch.setattr(cloud_control, "_user_action", lambda a, f: None)
    for name in ("delete", "set_expiry"):
        monkeypatch.setattr(cloud_control.cloud_registry, name, lambda *a, **k: None)
    monkeypatch.setattr(cloud_control.cloud_registry, "stop_rollout", lambda: None)
    monkeypatch.setattr(
        cloud_control.cloud_auth, "update_organizer", lambda *a, **k: None
    )
    monkeypatch.setattr(cloud_control.cloud_auth, "verify_user", lambda *a: None)

    req = SimpleNamespace(state=SimpleNamespace(admin_user="u", admin_roles=roles))
    resp = cloud_control._admin_action(_Form({"action": action}), req)
    refused = getattr(resp, "status_code", None) == 403
    assert refused is not allowed


def test_the_tab_routes_name_their_role():
    import cloud_control

    def role(path, method="GET"):
        route = next(
            r
            for r in cloud_control.app.routes
            if getattr(r, "path", "") == path and method in getattr(r, "methods", ())
        )
        return [
            getattr(d.dependency, "role", "?")
            for d in getattr(route, "dependencies", [])
        ]

    assert role("/admin/picker_appearance", "POST") == ["appearance"]
    assert role("/admin/stats") == ["meets"]
    assert role("/admin") == [None]
    assert role("/admin/backup/keys") == ["admin"]
    assert role("/admin/update", "POST") == ["admin"]
