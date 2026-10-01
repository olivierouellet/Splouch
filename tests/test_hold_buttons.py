"""Destructive or connection-dropping buttons take a press-and-hold, not a tap.

Every Delete, the cloud Disconnect, the Clock's RTC Remove, the Ethernet Static and
DHCP switches, the three Update & Backup installs, and the Test tab's Play and Start
go through `[data-hold]` (`shared/static/js/hold.js`), like Restart and Reboot. The
hold replaces the `confirm()` dialogs some of them had: one confirmation, not two.
"""

import os
import re

from conftest import settings_source

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tag(src, needle):
    """The opening tag that contains `needle`."""
    i = src.index(needle)
    return src[src.rindex("<", 0, i) : src.index(">", i) + 1]


def test_settings_buttons_hold():
    src = settings_source()
    for needle in (
        'data-hold-fn="meetDeleteHeld"',
        'href="/splash_delete"',
        'href="/theme_delete"',
        'href="/picker_image_delete?',
        'href="/icon_delete?',
        'id="btn-rtc-remove"',
        'data-hold-fn="setEthIp"',
        'data-hold-fn="setEthDhcp"',
        'id="btn-run-update"',
        'id="btn-update-displays"',
        'id="btn-os-update"',
        'id="btn-record"',
    ):
        tag = _tag(src, needle)
        assert "data-hold" in tag and "data-hold-label" in tag, tag
        assert "confirm(" not in tag, f"hold and confirm() both: {tag}"


def test_js_built_rows_hold():
    """The session rows are built in settings.js, so the markup check cannot see
    them as tags — check the strings that build them."""
    src = settings_source()
    assert 'data-hold-fn="testPlayHeld"' in src
    assert 'data-hold-fn="testDeleteHeld"' in src
    assert 'onclick="testPlay(' not in src and 'onclick="testDelete(' not in src


def test_toggles_hold_only_on_their_risky_half():
    """Disconnect and Start hold; Connect and Stop stay a tap."""
    src = settings_source()
    cloud = re.search(
        r"function _applyCloudStatus\(d\) \{.*?^\}", src, re.DOTALL | re.MULTILINE
    )
    assert cloud and cloud.group(0).count("_setHold(btn, true, T.hold_disconnect)") == 2
    assert cloud.group(0).count("_setHold(btn, false)") == 2
    assert "_setHold(btn, !_recording, T.hold_start)" in src
    for btn_id in ("btn-cloud-toggle", "btn-record"):
        tag = _tag(src, f'id="{btn_id}"')
        assert "if (!this.hasAttribute('data-hold'))" in tag, tag


def test_no_confirm_left_on_the_ethernet_switches():
    src = settings_source()
    for fn in ("setEthIp", "setEthDhcp"):
        body = re.search(
            rf"^function {fn}\(\) \{{.*?^\}}", src, re.DOTALL | re.MULTILINE
        )
        assert body and "confirm(" not in body.group(0), fn


def test_cloud_admin_deletes_hold():
    base = os.path.join(REPO, "cloud", "templates", "admin")
    for name in ("organizers.html", "meets.html"):
        with open(os.path.join(base, name), encoding="utf-8") as f:
            html = f.read()
        deletes = re.findall(r"<button[^>]*>\{\{ t\.delete[^}]*\}\}</button>", html)
        assert deletes, name
        for tag in deletes:
            assert "data-hold" in tag, f"{name}: {tag}"
        assert 'onsubmit="return confirm(' not in html, name


def test_hold_submits_a_form_button():
    """Cloud admin Deletes are submit buttons: hold.js has to submit their form."""
    with open(
        os.path.join(REPO, "shared", "static", "js", "hold.js"), encoding="utf-8"
    ) as f:
        js = f.read()
    assert "el.form.requestSubmit(el)" in js


def test_cloud_admin_hold_buttons():
    """Enable (attendance), Revoke, the Appearance Removes and Update all hold."""
    base = os.path.join(REPO, "cloud", "templates")

    def read(name):
        with open(os.path.join(base, name), encoding="utf-8") as f:
            return f.read()

    for name, needles in (
        ("admin/meets.html", ['data-hold-fn="openAnalyticsConsent"']),
        ("admin/organizers.html", ["{{ t.revoke }}"]),
        (
            "admin/appearance.html",
            ['id="picker-logo-remove-btn"', 'id="picker-icon-remove-btn"'],
        ),
        ("admin/update.html", ['id="update-btn"']),
    ):
        html = read(name)
        for needle in needles:
            i = 0
            while (i := html.find(needle, i)) != -1:
                tag = (
                    _tag(html, needle)
                    if needle.startswith(("id=", "data-"))
                    else html[html.rindex("<button", 0, i) : i]
                )
                assert "data-hold" in tag and "data-hold-label" in tag, f"{name}: {tag}"
                assert "onclick=" not in tag, f"{name}: hold and onclick both: {tag}"
                i += len(needle)
    admin = read("admin.html")
    assert "confirm('Pull " not in admin and "confirm('Remove the " not in admin
