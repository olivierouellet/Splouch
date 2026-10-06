"""Turning attendance counting on goes through an acknowledgement; turning it off does not.

The fold under the card used to be the only place the legal notes lived, and a
"read before enabling" nobody has to open is read by nobody. So Enable now opens a
dialog: who is responsible (the administrator of this server), the usual "as is"
line, the regional notes, and a box to tick before the Accept button comes alive.

Two things here are load-bearing:

* **The server holds the line too.** A post without `analytics_ack` leaves counting
  off, so the checkbox is not a page-side gate a stale tab or a hand-made form can
  step around. The acceptance is stored with the setting: who, and when.
* **Disable never asks.** Stopping collection is always the safe direction; a
  dialog in its way would only slow down the operator who has just learned they
  should not be counting.
"""

import asyncio
import os
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

import cloud_auth
import cloud_control as cs

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def render_admin(analytics_enabled, privacy_incomplete=False):
    import tomllib

    from jinja2 import Environment, FileSystemLoader

    from conftest import stub_url_for

    env = Environment(
        loader=FileSystemLoader(
            [
                os.path.join(REPO, "cloud", "templates"),
                os.path.join(REPO, "shared", "templates"),
            ]
        )
    )
    stub_url_for(env)
    with open(os.path.join(REPO, "shared", "locales", "panel", "en.toml"), "rb") as f:
        t = tomllib.load(f)
    return env.get_template("admin.html").render(
        roles=("admin", "meets", "organizers", "appearance"),
        t={**t["chrome"], **t["cloud"]},
        has_deploy=False,
        creds_error=None,
        keys=[],
        regions=[],
        countries={},
        active_meets=[],
        user_name="Admin",
        locales=[],
        current_locale="",
        ui_lang_cookie="",
        analytics_enabled=analytics_enabled,
        privacy_incomplete=privacy_incomplete,
        picker_window_title_form="",
        picker_title_form="",
        picker_logo_above=False,
        has_picker_logo=False,
        has_picker_icon=False,
        picker_max_upload=2 * 1024 * 1024,
    )


def dialog(page):
    start = page.index('id="analytics-consent"')
    return page[start : page.index("</form>", start)]


# ── The page ───────────────────────────────────────────────────────────────────


def test_enable_opens_the_dialog_instead_of_posting():
    page = render_admin(False)
    # A hold that opens the dialog — not a form of its own.
    assert 'data-hold-fn="openAnalyticsConsent"' in page
    assert (
        'name="analytics_enabled" value="1"'
        not in page[: page.index('id="analytics-consent"')]
    ), (
        "an Enable form outside the dialog would turn counting on without the acknowledgement"
    )


def test_the_dialog_carries_the_responsibility_and_the_regional_notes():
    d = dialog(render_admin(False))
    assert "you alone are responsible" in d
    assert "without warranty" in d
    for region in ("Canada", "United States", "Europe (EU / UK)"):
        assert region in d


def test_accept_stays_off_until_the_box_is_ticked():
    d = dialog(render_admin(False))
    box = d[d.index('id="analytics-ack"') - 200 : d.index('id="analytics-ack"') + 200]
    assert 'name="analytics_ack"' in box and "required" in box
    submit = d[d.index('type="submit"') :]
    assert "disabled" in submit[: submit.index(">")]


def test_the_dialog_forgets_the_tick_when_it_closes():
    page = render_admin(False)
    assert "hidden.bs.modal" in page
    assert "form').reset()" in page


def test_disable_posts_directly_and_there_is_no_dialog():
    page = render_admin(True)
    assert 'id="analytics-consent"' not in page
    assert 'name="analytics_enabled" value="0"' in page


def test_the_regional_notes_stay_readable_once_counting_is_on():
    page = render_admin(True)
    fold = page[page.index("<details") : page.index("</details>")]
    assert "Canada" in fold and "Europe (EU / UK)" in fold


# ── The server ─────────────────────────────────────────────────────────────────


@pytest.fixture
def client(pg):
    cloud_auth.save_creds({"user": "pool-admin", "password_hash": "x", "salt": "y"})


def creds():
    return cloud_auth.load_creds()


def post(client, **form):
    """POST /admin straight into the handler; `require_admin` is the route's, not its."""
    body = urlencode({"action": "set_analytics", **form}).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/admin",
            "headers": [(b"content-type", b"application/x-www-form-urlencoded")],
            # What `require_role` leaves behind for the owner it let through.
            "state": {
                "admin_user": "pool-admin",
                "admin_roles": set(cloud_auth.CLOUD_ROLES),
            },
        },
        receive,
    )
    return asyncio.run(cs.route_admin(request))


def test_enabling_without_the_acknowledgement_is_refused(client):
    assert post(client, analytics_enabled="1").status_code == 303
    assert not creds().get("analytics_enabled")
    assert "analytics_ack" not in creds()


def test_enabling_with_it_records_who_and_when(client):
    post(client, analytics_enabled="1", analytics_ack="1")
    c = creds()
    assert c["analytics_enabled"] is True
    assert c["analytics_ack"]["user"] == "pool-admin"
    assert c["analytics_ack"]["at"].endswith("+00:00")


def test_disabling_needs_nothing_and_keeps_the_record(client):
    post(client, analytics_enabled="1", analytics_ack="1")
    post(client, analytics_enabled="0")
    c = creds()
    assert c["analytics_enabled"] is False
    assert c["analytics_ack"]["user"] == "pool-admin"


def test_the_card_flags_a_privacy_policy_missing_its_operator_or_contact():
    """GDPR and Law 25 need the policy to name who answers for it (`/privacy`)."""
    assert "PRIVACY_OPERATOR" not in render_admin(False)
    assert "PRIVACY_OPERATOR" in render_admin(False, privacy_incomplete=True)
