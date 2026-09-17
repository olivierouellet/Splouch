"""The cloud role's re-exec must tell the second run who it is installing for.

A fresh cloud VM is provisioned as root. The installer creates the `splouch`
account, hardens SSH, then re-execs itself as that user to do the actual install.
The second run resolves the target user again at the top of the script:

    TARGET_USER="${SPLOUCH_TARGET_USER:-${SUDO_USER:-$USER}}"

and `SUDO_USER` is documented as "the login name of the user who invoked sudo" —
root, not the account sudo switched to. So without `SPLOUCH_TARGET_USER` the second
run computes `TARGET_HOME=/root`, and the clone dies as an unprivileged process:

    fatal: could not create work tree dir '/root/Splouch': Permission denied

It failed only on a genuinely fresh VM, which is the one install nobody repeats,
and it failed *after* the bootstrap had already locked the root account and turned
off password SSH — so the machine was half-hardened with no checkout on it.

`env VAR=value` rather than `sudo VAR=value`: sudo rejects the latter unless the
sudoers entry carries `setenv`.
"""
import os
import re
import subprocess

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(REPO, 'install', 'install.sh')


@pytest.fixture(scope='module')
def installer():
    return open(INSTALLER, encoding='utf-8').read()


def test_the_script_is_valid_bash(installer):
    subprocess.run(['bash', '-n', INSTALLER], check=True)


def test_the_re_exec_passes_the_target_user(installer):
    """The one line that was missing."""
    exec_line = re.search(r'exec sudo -H -u "\$CLOUD_USER".*?cloud "\$VERSION_CHOICE"',
                          installer, re.S)
    assert exec_line, 'the cloud re-exec changed shape — check this still applies'
    assert 'SPLOUCH_TARGET_USER="$CLOUD_USER"' in exec_line.group(0), \
        're-exec does not pass SPLOUCH_TARGET_USER; the second run will resolve to root'
    assert re.search(r'\benv SPLOUCH_TARGET_USER=', exec_line.group(0)), \
        'pass it via `env`: `sudo VAR=value` needs setenv in sudoers'


def _resolve(target_user_env, sudo_user, user):
    """Run the installer's own resolution line under a given environment."""
    script = (
        'TARGET_USER="${SPLOUCH_TARGET_USER:-${SUDO_USER:-$USER}}"; echo "$TARGET_USER"'
    )
    env = {**os.environ, 'SPLOUCH_TARGET_USER': target_user_env,
           'SUDO_USER': sudo_user, 'USER': user}
    out = subprocess.run(['bash', '-c', script], capture_output=True, text=True, env=env)
    return out.stdout.strip()


def test_the_resolution_line_still_prefers_the_explicit_variable(installer):
    """Pinned against the script's actual text, so the simulation below can't drift."""
    assert 'TARGET_USER="${SPLOUCH_TARGET_USER:-${SUDO_USER:-$USER}}"' in installer


@pytest.mark.parametrize('label, explicit, sudo_user, user, expected', [
    # The re-exec'd run: sudo reports root as the invoker even though the process
    # is splouch. This is the case that broke.
    ('cloud re-exec',        'splouch', 'root', 'splouch', 'splouch'),
    # The in-app Reinstall, which always passed the variable and always worked.
    ('in-app reinstall',     'splouch', '',     'root',    'splouch'),
    # An existing VM, installer run directly by the account that owns the checkout.
    ('run as the owner',     '',        '',     'splouch', 'splouch'),
    # Ordinary `sudo bash install.sh` from a login shell: provision for the human.
    ('sudo from a login',    '',        'olivier', 'root',  'olivier'),
])
def test_the_target_user_resolves_to_the_checkout_owner(label, explicit, sudo_user,
                                                        user, expected):
    assert _resolve(explicit, sudo_user, user) == expected, label


def test_without_the_variable_the_re_exec_would_pick_root():
    """The bug itself, kept as a test so the reasoning above stays checkable."""
    assert _resolve('', 'root', 'splouch') == 'root'


def test_the_bootstrap_hardens_ssh_before_it_re_execs(installer):
    """Ordering worth knowing about: by the time the clone can fail, root SSH is
    already off and root's password locked. Anyone debugging a failed cloud install
    has to confirm the new account's key works before dropping their session."""
    cloud = installer.split("if [[ \"$ROLE\" == \"cloud\" ]]; then", 1)[1]
    lockdown = cloud.index('passwd -l root')
    reexec = cloud.index('exec sudo -H -u "$CLOUD_USER"')
    assert lockdown < reexec
    # And the key copy has to come before the lockdown, or there is no way back in.
    assert cloud.index('authorized_keys') < lockdown
