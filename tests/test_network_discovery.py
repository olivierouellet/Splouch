"""Settings → Network → Discovery: the Pi's own mDNS check.

Every avahi call is faked: the check's job is to turn what avahi says into named
problems, each with a fix the operator can act on, and that is what is tested.
"""

import os
import tomllib
from types import SimpleNamespace

import pytest

import routes.network as net

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBLEMS = {
    "no_tools",
    "no_daemon",
    "not_advertised",
    "no_alias",
    "no_name",
    "name_taken",
}

BROWSE = r"""+;eth0;IPv4;Splouch;_splouch._tcp;local
=;eth0;IPv4;Pool\032A;_splouch._tcp;local;splouch.local;10.0.0.5;5000;"v=1"
=;wlan0;IPv4;Pool\032A;_splouch._tcp;local;splouch.local;10.0.0.5;5000;"v=1"
=;eth0;IPv4;Odd\;Name;_splouch._tcp;local;other.local;10.0.0.9;5000;"x"
"""
_OTHER_ONLY = BROWSE.splitlines()[3]


def test_resolved_rows_are_parsed_once_per_address_and_unescaped():
    assert net.parse_browse(BROWSE) == [
        {
            "name": "Pool A",
            "host": "splouch.local",
            "address": "10.0.0.5",
            "port": "5000",
        },
        {
            "name": "Odd;Name",
            "host": "other.local",
            "address": "10.0.0.9",
            "port": "5000",
        },
    ]


def test_aliases_come_from_the_installed_unit(tmp_path):
    unit = tmp_path / "aliases.service"
    unit.write_text(
        "[Service]\nExecStart=/usr/local/lib/splouch/mdns-aliases.sh a.local b.local\n"
    )
    assert net.mdns_aliases(str(unit)) == ("a.local", "b.local")
    assert net.mdns_aliases(str(tmp_path / "missing")) == net._DEFAULT_ALIASES


@pytest.fixture
def avahi(monkeypatch):
    """A fake avahi: tweak the namespace, then call net.mdns_check()."""
    env = SimpleNamespace(
        tools=True,
        active={"avahi-daemon", "splouch-mdns-aliases"},
        browse=BROWSE,
        resolved={
            "splouch.local": "10.0.0.5",
            "tableau.local": "10.0.0.5",
            "marcador.local": "10.0.0.5",
        },
    )

    def run(cmd, timeout=6):
        if not env.tools and cmd[0].startswith("avahi"):
            return None
        return SimpleNamespace(stdout=env.browse, returncode=0)

    monkeypatch.setattr(net, "_run", run)
    monkeypatch.setattr(net, "_active", lambda unit: unit in env.active)
    monkeypatch.setattr(net, "own_addresses", lambda: {"10.0.0.5"})
    monkeypatch.setattr(net, "resolve", lambda name: env.resolved.get(name, ""))
    monkeypatch.setattr(
        net, "mdns_aliases", lambda: ("tableau.local", "marcador.local")
    )
    monkeypatch.setattr(net.socket, "gethostname", lambda: "splouch")
    return env


def _keys(result):
    return [(k, n) for k, n, _ in result["problems"]]


def test_a_healthy_pi_has_no_problems(avahi):
    d = net.mdns_check()
    assert d["problems"] == []
    assert [s["own"] for s in d["services"]] == [True, False]
    assert all(n["own"] for n in d["names"])


def test_missing_tools_or_daemon_is_the_only_problem_reported(avahi):
    avahi.active.discard("avahi-daemon")
    assert _keys(net.mdns_check()) == [("no_daemon", "")]
    avahi.tools = False
    assert _keys(net.mdns_check()) == [("no_tools", "")]


def test_a_pi_that_does_not_advertise_itself_is_flagged(avahi):
    avahi.browse = _OTHER_ONLY
    assert ("not_advertised", "") in _keys(net.mdns_check())


def test_an_unresolved_alias_blames_the_stopped_alias_service(avahi):
    avahi.active.discard("splouch-mdns-aliases")
    del avahi.resolved["tableau.local"]
    del avahi.resolved["splouch.local"]
    assert _keys(net.mdns_check()) == [
        ("no_name", "splouch.local"),
        ("no_alias", "tableau.local"),
    ]


def test_a_name_answered_by_another_device_names_that_device(avahi):
    avahi.resolved["marcador.local"] = "10.0.0.77"
    assert net.mdns_check()["problems"] == [
        ("name_taken", "marcador.local", "10.0.0.77")
    ]


@pytest.mark.parametrize("code", ["en", "fr", "es"])
def test_every_problem_and_cause_has_words(code):
    with open(os.path.join(REPO, f"shared/locales/panel/{code}.toml"), "rb") as f:
        strings = tomllib.load(f)["settings"]
    for key in PROBLEMS:
        assert f"net_mdns_p_{key}" in strings
    for key in ("isolation", "multicast", "vlan", "firewall", "phone"):
        assert f"net_mdns_r_{key}" in strings and f"net_mdns_r_{key}_text" in strings


def test_the_fix_points_at_the_terminal_tab_not_ssh():
    with open(
        os.path.join(REPO, "server/templates/settings/fetched/mdns.html"),
        encoding="utf-8",
    ) as f:
        src = f.read()
    assert "panelShowTab('#tab-terminal')" in src
