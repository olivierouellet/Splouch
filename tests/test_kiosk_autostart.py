"""The kiosk's autostart line, and the Chromium one it had to replace.

The TV Pi boots to a desktop session and starts the display from an autostart file.
That file is appended to, not rewritten, so every installer this project has shipped
has left a line in it — and two of those lines start a *fullscreen* app.

The one that bit: the Tremplin→Splouch rename changed the marker comment
(`# Tremplin kiosk` → `# Splouch kiosk`) in one release, and the Chromium kiosk was
replaced by the Qt display in a later one. The cleanup only ever knew the new
marker, so a Pi provisioned before the rename and upgraded since still carried the
old block and still launched Chromium on `/live` on top of the Qt board. Two
fullscreen apps fight over the TV and the browser tends to win, because it starts
first — so the display looked like it had simply not been updated.

`strip_kiosk_autostart` is read out of `install/install.sh` and run for real here.
It is awk rather than sed precisely so that it *can* be: deleting a matched line
plus the one after it (`addr,+1`) and matching either marker in one expression are
both GNU extensions, fine on the Pi and untestable anywhere else, which is how the
Tremplin block survived as long as it did.
"""
import os
import re
import subprocess
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(REPO, 'install', 'install.sh')

KIOSK_CMD = '/home/pi/Splouch/install/scripts/start-scoreboard.sh'
OLD_CHROMIUM = ('chromium-browser --kiosk --app=http://splouch.local '
                '--noerrdialogs --disable-infobars --password-store=basic &')


@pytest.fixture(scope='module')
def helper():
    """The function itself, lifted out of the installer — not a copy of it."""
    src = open(INSTALLER, encoding='utf-8').read()
    match = re.search(r'^strip_kiosk_autostart\(\) \{.*?^\}', src, re.S | re.M)
    assert match, 'strip_kiosk_autostart is gone from install.sh'
    return match.group(0)


def _run(helper, tmp_path, contents, times=1):
    """Apply the cleanup and the append, as the kiosk role does."""
    autostart = tmp_path / 'autostart'
    autostart.write_text(contents, encoding='utf-8')
    script = (f'{helper}\n'
              f'for i in $(seq {times}); do\n'
              f'  strip_kiosk_autostart "{autostart}"\n'
              f"  printf '\\n# Splouch kiosk\\n%s &\\n' '{KIOSK_CMD}' >> '{autostart}'\n"
              f'done\n')
    done = subprocess.run(['bash', '-c', script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return autostart.read_text(encoding='utf-8')


# ── The upgrade that was broken ────────────────────────────────────────────────

def test_a_pre_rename_chromium_kiosk_is_removed(helper, tmp_path):
    """The actual file a Pi provisioned before the rename is carrying."""
    out = _run(helper, tmp_path, f'\n# Tremplin kiosk\n{OLD_CHROMIUM}\n')
    assert 'chromium' not in out, 'Chromium still starts on top of the Qt display'
    assert 'Tremplin' not in out, 'the stale marker was left behind'
    assert out.count(KIOSK_CMD) == 1


def test_a_post_rename_chromium_kiosk_is_removed(helper, tmp_path):
    """The Chromium kiosk shipped under the new marker too, for one release."""
    out = _run(helper, tmp_path, f'\n# Splouch kiosk\n{OLD_CHROMIUM}\n')
    assert 'chromium' not in out
    assert out.count(KIOSK_CMD) == 1


def test_both_generations_at_once(helper, tmp_path):
    """A Pi that went Tremplin → Splouch → Qt has collected one of each."""
    out = _run(helper, tmp_path,
               f'\n# Tremplin kiosk\n{OLD_CHROMIUM}\n'
               f'\n# Splouch kiosk\n{KIOSK_CMD} &\n')
    assert 'chromium' not in out
    assert out.count(KIOSK_CMD) == 1, 'the display would start twice'


def test_a_marker_whose_comment_was_edited_away(helper, tmp_path):
    """Someone tidying the file by hand leaves the command and loses the comment."""
    out = _run(helper, tmp_path, f'\n{OLD_CHROMIUM}\n')
    assert 'chromium' not in out


# ── What it must not touch ─────────────────────────────────────────────────────

def test_unrelated_autostart_lines_survive(helper, tmp_path):
    """This file is the user's, not ours. A kiosk Pi may well run something else."""
    out = _run(helper, tmp_path,
               '/usr/bin/nm-applet &\n'
               'wlr-randr --output HDMI-A-1 --transform 90 &\n'
               f'\n# Tremplin kiosk\n{OLD_CHROMIUM}\n')
    assert 'nm-applet' in out
    assert 'wlr-randr' in out


def test_someone_elses_chromium_kiosk_survives(helper, tmp_path):
    """`--kiosk --app=` is the flag pair the old installer wrote; a Chromium
    autostart that belongs to another project on this Pi is not ours to delete."""
    other = 'chromium-browser --kiosk https://dashboard.example.org &'
    out = _run(helper, tmp_path, f'{other}\n\n# Tremplin kiosk\n{OLD_CHROMIUM}\n')
    assert other in out, 'deleted a Chromium kiosk that was not ours'
    assert 'splouch.local' not in out, 'but ours had to go'


# ── Re-running the installer ───────────────────────────────────────────────────

def test_re_running_does_not_stack_launchers(helper, tmp_path):
    """The whole reason the file is cleaned before it is appended to."""
    out = _run(helper, tmp_path, '', times=4)
    assert out.count(KIOSK_CMD) == 1, f'{out.count(KIOSK_CMD)} launchers'
    assert out.count('# Splouch kiosk') == 1


def test_a_missing_file_is_not_an_error(helper, tmp_path):
    """First install: the labwc config directory has only just been created."""
    script = f'{helper}\nstrip_kiosk_autostart "{tmp_path / "nope"}"\n'
    done = subprocess.run(['bash', '-c', script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


# ── The installer's own text ───────────────────────────────────────────────────

def test_the_installer_no_longer_offers_a_chromium_kiosk():
    """The role menu described the kiosk as "Chromium fullscreen" long after it had
    stopped being one — which is the answer an operator gets when they ask what the
    kiosk role does."""
    src = open(INSTALLER, encoding='utf-8').read()
    menu = re.search(r'"Kiosk\s+\([^"]*\)"', src)
    assert menu, 'the role menu entry moved'
    assert 'Chromium' not in menu.group(0), menu.group(0)


def test_the_hdmi_block_is_not_appended_twice():
    """Same dual-marker problem, lower stakes: the pre-rename installer wrote the
    same three lines under its own marker, so checking only for the new one appends
    a second copy on every re-run."""
    src = open(INSTALLER, encoding='utf-8').read()
    guard = re.search(r'if ! grep -q "([^"]*kiosk[^"]*)" "\$CONFIG_TXT"', src)
    assert guard, 'the config.txt guard moved'
    assert 'Tremplin' in guard.group(1), guard.group(1)


# ── "No displays are registered" ───────────────────────────────────────────────
# The other half of the same upgrade. A Chromium kiosk shows `/live`, which opens
# `/ws/scoreboard` like any browser tab and never sends `register` — so it appears
# in Settings → Network as a bare IP row while "Update displays" reports that
# nothing is there. The operator sees a connected client and a message saying
# there is none, and the button that would fix it is the one refusing to run.
#
# It cannot be fixed by counting browser tabs: a display old enough to miss
# `register` is also old enough to ignore the `update` frame. The message has to
# carry the diagnosis instead.

@pytest.fixture
def clients(monkeypatch):
    sys.path.insert(0, os.path.join(REPO, 'server'))
    import state
    monkeypatch.setattr(state, '_scoreboard_clients', {}, raising=False)
    monkeypatch.setattr(state, '_running_lanes', set(), raising=False)
    return state._scoreboard_clients


def _update(clients):
    import asyncio

    import routes.system as system
    result = asyncio.run(system.route_displays_update())
    if hasattr(result, 'body'):
        import json
        return result.status_code, json.loads(result.body)
    return 200, result


def test_a_chromium_kiosk_is_not_mistaken_for_a_display(clients):
    """What `/live` in a browser looks like on the server: an IP and nothing else."""
    clients[1] = {'ip': '10.0.0.42', 'at': '09:15:03'}
    status, body = _update(clients)
    assert status == 404
    assert 'browser' in body['error'].lower(), body['error']
    assert 'install.sh' in body['error'], 'the message names no way forward'


def test_the_empty_case_still_reads_as_empty(clients):
    status, body = _update(clients)
    assert status == 404
    assert 'connected' in body['error'].lower()
    assert 'browser' not in body['error'].lower(), 'blamed a tab that is not there'


def test_a_registered_display_is_found(clients):
    """`role` is what `register` sets and a browser tab never has."""
    clients[1] = {'ip': '10.0.0.42', 'at': '09:15:03', 'role': 'kiosk',
                  'hostname': 'splouch-tv', 'version': 'v2026.09.0'}
    status, body = _update(clients)
    # Either it dispatched, or it refused for a reason that is not "none found" —
    # a dev checkout is dirty, which the route rightly declines to broadcast.
    assert status != 404, body
