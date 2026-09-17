"""Port 443 must be *refused* on the server Pi, never silently dropped.

Browsers upgrade a typed `splouch.local` to https before trying http, and fall back
to http only when the https attempt fails fast — a TCP reset. Nothing on the Pi
serves TLS, so failing is correct; failing *quietly* is not.

The trap is ufw: its `deny` policy is a DROP, which swallows the SYN and returns
nothing. On `eth0`/`wlan0` the installer's blanket `allow in on …` rules let the SYN
through and the kernel resets it, so the fallback works there by accident. Reached
over any other path — through a router, or a USB WiFi dongle that enumerates as
`wlxXXXXXXXX` instead of `wlan0` — the SYN hits the default DROP, the browser waits,
and the whole server looks dead when only the scheme was wrong.

The server cannot talk its way out of this: an HTTP redirect or a "no https here"
page travels inside the TLS session, so sending either would need a certificate the
browser already trusts. The reset is the only signal available, which is why the
rule is load-bearing rather than cosmetic.

See docs/troubleshooting-splouch-local-unreachable.md.
"""
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALLER = os.path.join(REPO, 'install', 'install.sh')


@pytest.fixture(scope='module')
def server_path():
    """The Pi #1 branch of the installer, up to its closing banner.

    Scoped deliberately: the kiosk Pi has its own firewall block and serves no web
    UI, so the rule is only asserted where someone actually types the hostname.
    """
    src = open(INSTALLER, encoding='utf-8').read()
    end = src.index('section "Done — Pi #1 (server)"')
    return src[:end]


def test_server_pi_rejects_443(server_path):
    assert re.search(r'^\s*sudo ufw reject 443/tcp\b', server_path, re.M), (
        'Pi #1 must `ufw reject 443/tcp`. Without it, a client reaching the Pi on any '
        'interface outside the eth0/wlan0 allow rules has its https SYN dropped, and '
        'the browser hangs instead of falling back to http.'
    )


def test_443_is_never_denied(server_path):
    """`deny` would look equivalent in the script and reintroduce the hang."""
    assert not re.search(r'^\s*sudo ufw deny .*\b443\b', server_path, re.M), (
        'ufw `deny` is a silent DROP — use `reject` so the client gets a TCP reset.'
    )


def test_rule_follows_the_interface_allows(server_path):
    """ufw matches in order; the blanket allows must keep winning on the pool deck.

    Same outcome either way (nothing listens, so the kernel resets), but putting the
    reject first would mean a future TLS listener on 443 is firewalled off by a rule
    nobody remembered was there.
    """
    allow = server_path.rindex('sudo ufw allow in on wlan0')
    reject = server_path.index('sudo ufw reject 443/tcp')
    assert reject > allow, 'the 443 reject belongs after the eth0/wlan0 allow rules'
