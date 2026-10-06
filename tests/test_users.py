"""Several users on one Pi, each allowed only some pages (docs/admin.md, Users)."""

import contextlib
from typing import cast

import pytest
from fastapi import Request

import state
import web
from routes import scoreboard as scoreboard_routes, settings as settings_routes


@pytest.fixture(autouse=True)
def _accounts(monkeypatch):
    monkeypatch.setattr(state, "settings", {**state.settings, "username": "score"})
    monkeypatch.setattr(state, "save_settings", lambda: None)
    state.set_password("owner-pw")
    state.settings["users"] = []
    state.add_user("timer", "timer-pw", ["console"])
    state.add_user("mm", "mm-pw", ["mm"])
    state.add_user("ref", "ref-pw", ["admin"])


def _session(user):
    return {"user": user, "cred": web.credentials_stamp(user)}


class _Req:
    def __init__(self, session, form=None):
        self.headers = {}
        self.session = session
        self.method = "POST"
        self.url = type("U", (), {"path": "/settings"})()
        self.cookies = {}
        self._form = form or {}


def _allowed(session, role):
    try:
        web.require_role(role)(cast(Request, _Req(session)))
    except (web.NotAuthenticated, web.NotAllowed):
        return False
    return True


@pytest.mark.parametrize(
    "user, pages",
    [
        ("score", {"admin", "console", "manual", "mm"}),
        ("ref", {"admin", "console", "manual", "mm"}),
        ("timer", {"console"}),
        ("mm", {"mm"}),
    ],
)
def test_each_user_opens_only_their_pages(user, pages):
    session = _session(user)
    assert {r for r in state.ROLES if _allowed(session, r)} == pages


def test_a_user_without_the_page_is_refused_not_sent_to_sign_in():
    with pytest.raises(web.NotAllowed):
        web.require_role("admin")(cast(Request, _Req(_session("timer"))))
    with pytest.raises(web.NotAuthenticated):
        web.require_role("admin")(cast(Request, _Req({})))


def test_the_console_and_mm_pages_name_their_role():
    from routes import meet as meet_routes

    def roles(router, path):
        route = next(r for r in router.routes if getattr(r, "path", "") == path)
        return [getattr(d.dependency, "role", None) for d in route.dependencies]

    assert "console" in roles(scoreboard_routes.router, "/console")
    assert "manual" in roles(scoreboard_routes.router, "/manual")
    assert "mm" in roles(meet_routes.router, "/mm")
    upload = next(
        r
        for r in settings_routes.router.routes
        if getattr(r, "path", "") == "/meet_update_file"
    )
    assert "mm" in [getattr(d.dependency, "role", None) for d in upload.dependencies]


def test_each_user_signs_in_with_their_own_password():
    assert state.check_user("timer", "timer-pw")
    assert not state.check_user("timer", "mm-pw")
    assert state.check_user("score", "owner-pw")
    assert not state.check_user("nobody", "owner-pw")


def test_an_unknown_name_still_costs_a_hash(monkeypatch):
    calls = []
    real = state.hash_password
    monkeypatch.setattr(
        state, "hash_password", lambda *a, **k: calls.append(1) or real(*a, **k)
    )
    state.check_user("timer", "wrong")
    known = len(calls)
    calls.clear()
    state.check_user("nobody", "wrong")
    assert len(calls) >= known


def test_changing_one_users_login_ends_only_their_sessions():
    timer, mm, owner = _session("timer"), _session("mm"), _session("score")
    state.update_user("timer", password="new-pw")
    assert not web.signed_in(timer)
    assert web.signed_in(mm) and web.signed_in(owner)

    mm_before = _session("mm")
    state.update_user("mm", roles=["mm", "console"])
    assert not web.signed_in(mm_before)

    state.delete_user("mm")
    assert not web.signed_in(_session("mm"))
    assert web.signed_in(owner)


def test_a_settings_file_from_before_users_still_signs_the_owner_in():
    del state.settings["users"]
    assert state.check_user("score", "owner-pw")
    assert web.signed_in(_session("score"))
    assert state.user_roles("score") == set(state.ROLES)


@pytest.mark.parametrize(
    "user, nxt, lands",
    [
        ("mm", "/mm", "/mm"),
        ("mm", "/settings", "/mm"),
        ("mm", "", "/mm"),
        ("timer", "/docs", "/console"),
        ("score", "/console", "/console"),
        ("score", "", "/"),
        ("ref", "https://evil.example", "/"),
    ],
)
def test_signing_in_lands_on_a_page_the_user_may_open(user, nxt, lands):
    import app

    assert app._landing(user, nxt) == lands


class _Form(dict):
    def getlist(self, key):
        v = self.get(key, [])
        return v if isinstance(v, list) else [v]


def _post(form, user="score"):
    request = _Req(_session(user))
    with contextlib.suppress(Exception):
        settings_routes._settings_view(request, _Form(form))
    return request


def test_the_users_tab_adds_changes_and_deletes():
    _post(
        {
            "users_action": "add",
            "users_name": "announcer",
            "users_password": "a-pw",
            "users_roles": ["mm"],
        }
    )
    assert state.check_user("announcer", "a-pw")
    assert state.user_roles("announcer") == {"mm"}

    _post(
        {"users_action": "save", "users_name": "announcer", "users_roles": ["console"]}
    )
    assert state.user_roles("announcer") == {"console"}
    assert state.check_user("announcer", "a-pw")  # empty password keeps it

    _post({"users_action": "delete", "users_name": "announcer"})
    assert state.find_user("announcer") is None


@pytest.mark.parametrize(
    "form",
    [
        {"users_name": "score", "users_password": "x", "users_roles": ["mm"]},
        {"users_name": "timer", "users_password": "x", "users_roles": ["mm"]},
        {"users_name": "new", "users_roles": ["mm"]},
        {"users_name": "new", "users_password": "x"},
        {"users_name": "", "users_password": "x", "users_roles": ["mm"]},
    ],
)
def test_the_users_tab_refuses_a_bad_user(form):
    before = list(state.users())
    _post({"users_action": "add", **form})
    assert [u["name"] for u in state.users()] == [u["name"] for u in before]


def test_a_user_changes_their_own_password_not_the_owners():
    request = _post({"password": "mine", "password2": "mine"}, user="ref")
    assert state.check_user("ref", "mine")
    assert state.check_password("owner-pw")
    assert web.signed_in(request.session)


def test_mismatched_passwords_change_nothing():
    _post({"password": "one", "password2": "two"})
    assert state.check_password("owner-pw")


def test_the_owner_cannot_take_another_users_name():
    _post({"user_name": "timer"})
    assert state.settings["username"] == "score"


def test_renaming_the_owner_keeps_them_signed_in():
    request = _post({"user_name": "boss"})
    assert state.settings["username"] == "boss"
    assert request.session["user"] == "boss" and web.signed_in(request.session)
