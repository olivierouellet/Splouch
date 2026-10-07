"""Settings → Debug: the Terminal's command list, the Logs view and the Hardware tab.

The Terminal tests call the routes directly with the PTY faked out — nothing here
spawns a shell. What matters is the decision each route makes: type into a shell
that is idle at its prompt, start one when there is none, and never type into one
that is busy unless told to replace it, since a command line typed into a running
install would answer its next question.
"""

import os
import re
import tomllib
from types import SimpleNamespace

import pytest

import hardware
import routes.debug as debug
import routes.system as system
import state

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── Terminal commands ─────────────────────────────────────────────────────────


def test_reinstall_runs_this_checkouts_installer_by_full_path(monkeypatch):
    monkeypatch.setattr(state, "REPO_DIR", "/home/pool deck/Splouch")
    line = dict(debug.terminal_commands())["reinstall"]
    assert line == "bash '/home/pool deck/Splouch/install/install.sh' server"


def test_every_listed_command_can_be_run_and_nothing_else():
    allowed = set(debug.TerminalRun.model_fields["cmd"].annotation.__args__)
    assert allowed == {key for key, _ in debug.terminal_commands()}


def test_every_listed_command_has_a_title_in_english():
    with open(os.path.join(REPO, "shared/locales/panel/en.toml"), "rb") as f:
        strings = tomllib.load(f)["settings"]
    for key, _ in debug.terminal_commands():
        assert f"term_cmd_{key}" in strings


@pytest.fixture
def pty(monkeypatch):
    """A fake terminal: records what is typed, started and stopped."""
    log = SimpleNamespace(typed=[], started=[], stopped=0, busy=False)

    def start(key):
        log.started.append(key)
        state._pty_fd, state._pty_pid, state._pty_cmd = 99, 1234, key
        return {"ok": True}

    def stop():
        log.stopped += 1
        state._pty_fd = state._pty_pid = state._pty_cmd = None
        return {"ok": True}

    monkeypatch.setattr(debug, "_start_pty", start)
    monkeypatch.setattr(debug, "route_terminal_stop", stop)
    monkeypatch.setattr(debug, "_terminal_input", log.typed.append)
    monkeypatch.setattr(debug.time, "sleep", lambda _s: None)
    monkeypatch.setattr(debug, "_has_children", lambda _pid: log.busy)
    yield log
    state._pty_fd = state._pty_pid = state._pty_cmd = None


def _running(cmd):
    state._pty_fd, state._pty_pid, state._pty_cmd = 99, 1234, cmd


def test_with_no_terminal_a_shell_is_started_and_the_line_typed(pty):
    assert debug.route_terminal_run(debug.TerminalRun(cmd="service"))["ok"]
    assert pty.started == ["bash"]
    assert pty.typed == [dict(debug.terminal_commands())["service"] + "\n"]


def test_a_shell_idle_at_its_prompt_is_typed_into_as_is(pty):
    _running("bash")
    assert debug.route_terminal_run(debug.TerminalRun(cmd="checkout"))["ok"]
    assert pty.started == [] and pty.stopped == 0
    assert pty.typed == [dict(debug.terminal_commands())["checkout"] + "\n"]


@pytest.mark.parametrize("cmd", ["bash", "raspi-config"])
def test_a_busy_terminal_is_refused_and_left_alone(pty, cmd):
    _running(cmd)
    pty.busy = True  # bash running a child; raspi-config is never idle
    result = debug.route_terminal_run(debug.TerminalRun(cmd="reinstall"))
    assert result == {"ok": False, "error": "busy"}
    assert pty.typed == [] and pty.stopped == 0


def test_replace_stops_the_busy_terminal_and_runs_in_a_fresh_shell(pty):
    _running("raspi-config")
    result = debug.route_terminal_run(debug.TerminalRun(cmd="checkout", replace=True))
    assert result["ok"]
    assert pty.stopped == 1 and pty.started == ["bash"]
    assert len(pty.typed) == 1


def test_status_reports_an_idle_shell_only_for_bash_with_no_child(pty):
    assert debug._terminal_status() == {"running": False, "idle_shell": False}
    _running("bash")
    assert debug._terminal_status() == {"running": True, "idle_shell": True}
    pty.busy = True
    assert debug._terminal_status() == {"running": True, "idle_shell": False}


# ── Logs ──────────────────────────────────────────────────────────────────────


def test_this_run_is_read_from_the_ring_newest_last(monkeypatch):
    ring = [f"line {i}" for i in range(200)]
    monkeypatch.setattr(state, "_log_ring", ring)
    d = system.route_logs_view(source="run", tail=50)
    assert d["ok"] and d["lines"] == ring[-50:]


def test_an_unknown_source_is_refused_without_running_anything(monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("journalctl must not run")

    monkeypatch.setattr(system.subprocess, "run", boom)
    assert not system.route_logs_view(source="; rm -rf /")["ok"]


@pytest.mark.parametrize(("source", "boot"), [("boot", "0"), ("prev", "-1")])
def test_the_journal_sources_read_this_service_for_their_boot(
    monkeypatch, source, boot
):
    seen = {}

    class Done:
        returncode, stdout, stderr = 0, "a\nb\n", ""

    def run(cmd, **_k):
        seen["cmd"] = cmd
        return Done()

    monkeypatch.setattr(system.subprocess, "run", run)
    d = system.route_logs_view(source=source, tail=100)
    assert d == {"ok": True, "lines": ["a", "b"]}
    cmd = seen["cmd"]
    assert cmd[:3] == ["journalctl", "-u", state.SERVICE_NAME]
    assert cmd[cmd.index("-b") + 1] == boot


# ── Hardware ──────────────────────────────────────────────────────────────────


def test_the_throttled_word_splits_into_now_and_since_boot():
    # 0x50005: under-voltage now and since boot, throttled now and since boot.
    flags = hardware.decode(0x50005)
    assert flags["undervoltage"] == {"now": True, "since_boot": True}
    assert flags["throttled"] == {"now": True, "since_boot": True}
    assert flags["freq_capped"] == {"now": False, "since_boot": False}
    # Recovered: only the high half is left.
    assert hardware.decode(0x10000)["undervoltage"] == {
        "now": False,
        "since_boot": True,
    }
    assert hardware.decode(None) is None


def test_status_summarises_the_ring(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware, "available", lambda: True)
    monkeypatch.setattr(hardware, "_samples", [])
    now = hardware.time.time()
    for i, temp in enumerate([50.0, 62.5, 55.0]):
        hardware._samples.append(
            {"t": now - 10 + i * 5, "temp": temp, "freq_mhz": 1500, "throttled": i == 1}
        )
    d = hardware.status(str(tmp_path))
    assert (d["temp_min"], d["temp_max"]) == (50.0, 62.5)
    assert [s[1] for s in d["history"]] == [50.0, 62.5, 55.0]
    assert [s[2] for s in d["history"]] == [False, True, False]
    assert d["latest"]["temp"] == 55.0
    assert d["disk"]["total"] > 0


def test_the_sampler_never_writes_to_disk():
    with open(os.path.join(REPO, "server/hardware.py"), encoding="utf-8") as f:
        src = f.read()
    assert not re.search(r"open\([^)]*['\"][wa]", src)


# ── Installer: an eth0 change ends the run with a reboot ─────────────────────


def _server_role():
    with open(os.path.join(REPO, "install/install.sh"), encoding="utf-8") as f:
        src = f.read()
    return src.split('if [[ "$ROLE" == "server" ]]; then', 1)[1].split("\nfi\n", 1)[0]


def test_the_network_question_is_the_installers_last():
    after = _server_role().split("    configure_network\n", 1)[1]
    # Nothing that waits for an answer, except the reboot prompt when eth0 is unchanged.
    assert re.findall(r"\b(?:confirm|read)\b", after) == ["confirm"]
    assert "elif confirm" in after


def test_an_eth0_change_reboots_without_asking():
    role = _server_role()
    tail = role[role.index("if ((ETH_PENDING)); then") :]
    assert tail.split("\n")[1].strip() == "reboot_for_network"
