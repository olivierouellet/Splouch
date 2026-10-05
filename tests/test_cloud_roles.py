"""One server or several (docs/architecture/scaling.md, batch 7): a server runs
any of three parts — control plane, relay workers, monitoring — chosen by ROLES,
and the deploy starts exactly those. Plus the nightly backup, the dump/restore tool
for moving the control plane, and the WireGuard configs between servers."""

import os
import subprocess
import types

import pytest
import yaml

import cloud_backup
import cloud_deploy
import cloud_workers

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def text(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize(
    "env,want",
    [
        ({}, {"control", "workers"}),  # a full server, as before roles
        ({"ROLES": "workers"}, {"workers"}),
        ({"ROLES": "control, monitoring"}, {"control", "monitoring"}),
        ({"ROLES": "control,bogus"}, {"control"}),
        ({"MONITORING": "1"}, {"control", "workers", "monitoring"}),
    ],
)
def test_the_parts(env, want):
    assert cloud_workers.roles(env) == want


def test_a_server_without_workers_runs_none():
    assert cloud_workers.worker_count({"ROLES": "control"}, cores=16) == 0


def test_profiles_are_the_compose_side_of_the_parts():
    assert cloud_deploy.profiles({}) == "control,workers"
    assert cloud_deploy.profiles({"ROLES": "workers,monitoring"}) == "workers"


def test_compose_puts_each_service_in_its_part():
    services = yaml.safe_load(read("cloud", "docker-compose.yml"))["services"]
    assert services["control"]["profiles"] == ["control"]
    assert services["postgres"]["profiles"] == ["control"]
    assert services["backup"]["profiles"] == ["control"]
    assert services["app"]["profiles"] == ["workers"]
    assert "profiles" not in services["caddy"], "every server has Caddy"
    assert services["app"]["depends_on"]["control"]["required"] is False
    assert (
        services["app"]["environment"]["CONTROL_URL"]
        == "${CONTROL_URL:-http://control:8000}"
    )


def test_extra_workers_stay_in_the_workers_part():
    override = yaml.safe_load(cloud_workers.compose_override(3))["services"]
    assert override["app2"]["profiles"] == ["workers"]


# ── Deploy per part ────────────────────────────────────────────────────────────


@pytest.fixture
def box(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.setattr(cloud_workers, "ENV_FILE", str(env))
    for name in ("COMPOSE_OUT", "CADDY_OUT", "TARGETS_OUT"):
        monkeypatch.setattr(cloud_workers, name, str(tmp_path / name))
    for name in ("ROLES_ROUTE", "GRAFANA_ROUTE", "STATUS_SITE", "AGENT_CONFIG"):
        monkeypatch.setattr(cloud_deploy, name, str(tmp_path / "out" / name))
    os.makedirs(tmp_path / "out")
    return env


def deploy(env_text, box):
    box.write_text(env_text)
    calls = []

    def run(argv, env, cwd, check):
        calls.append((argv, env.get("COMPOSE_PROFILES")))
        return types.SimpleNamespace(returncode=0)

    assert cloud_deploy.deploy("v2026.10.9", runner=run) == 0
    return calls


def test_a_workers_only_node_pulls_and_starts_only_workers(box):
    calls = deploy("ROLES=workers\nPICKER_URL=https://splouch.org/\nWORKERS=2\n", box)
    pull = next(c for c in calls if "pull" in c[0])
    assert pull[0][-1] == "app" and "control" not in pull[0]
    assert pull[1] == "workers"
    route = text(cloud_deploy.ROLES_ROUTE)
    assert "redir https://splouch.org{uri} 302" in route


def test_a_control_plane_only_server_builds_from_control_and_has_no_worker_routes(box):
    box.write_text("ROLES=control\n")
    calls = []

    def run(argv, env, cwd, check):
        calls.append(argv)
        return types.SimpleNamespace(returncode=1 if "pull" in argv else 0)

    cloud_deploy.deploy("v2026.10.9", runner=run)
    assert any(c[2:4] == ["build", "control"] for c in calls)
    assert "handle" not in text(cloud_workers.CADDY_OUT)


def test_a_node_with_a_hub_runs_the_agent_and_no_monitoring(box):
    calls = deploy(
        "ROLES=workers\nWG_HUB=10.73.0.1\nNODE_NAME=us1\nPICKER_URL=https://s/\n", box
    )
    agent = [c for c in calls if "monitoring/agent.yml" in c[0]]
    monitoring = [c for c in calls if "monitoring/docker-compose.yml" in c[0]]
    assert agent[0][0][-3:] == ["up", "-d", "--remove-orphans"]
    assert monitoring[0][0][-2:] == ["down", "--remove-orphans"]
    config = yaml.safe_load(text(cloud_deploy.AGENT_CONFIG))
    assert config["remote_write"] == [{"url": "http://10.73.0.1:9090/api/v1/write"}]
    assert config["global"]["external_labels"] == {"node": "us1"}
    jobs = {j["job_name"] for j in config["scrape_configs"]}
    assert jobs == {"workers", "node", "containers"}, "no control plane here"


def test_the_hub_runs_monitoring_and_no_agent(box):
    calls = deploy("ROLES=control,workers,monitoring\nWG_ADDRESS=10.73.0.1\n", box)
    agent = [c for c in calls if "monitoring/agent.yml" in c[0]]
    assert agent[0][0][-2:] == ["down", "--remove-orphans"]


def test_the_agent_only_sends():
    services = yaml.safe_load(read("cloud", "monitoring", "agent.yml"))["services"]
    assert "--agent" in services["agent"]["command"]
    assert not any("ports" in s for s in services.values()), "nothing listens on a node"


# ── Backups ────────────────────────────────────────────────────────────────────


def test_the_nightly_backup_dumps_keeps_and_pings():
    backup = yaml.safe_load(read("cloud", "docker-compose.yml"))["services"]["backup"]
    script = backup["command"][2]
    assert "pg_dump -Fc" in script and ".part" in script, "a dump is whole or absent"
    assert '-mtime +"$$BACKUP_KEEP" -delete' in script
    assert script.index("mv ") < script.index('BACKUP_PING_URL" ] ||'), (
        "ping after a good dump"
    )
    assert backup["volumes"] == ["/var/backups/splouch:/backups"]
    assert backup["environment"]["BACKUP_HOUR"] == "${BACKUP_HOUR:-03}"


def test_dump_writes_whole_files_only(box, tmp_path):
    box.write_text("ROLES=control\n")
    seen = {}

    def run(argv, env, cwd, stdout, check):
        seen["argv"], seen["profiles"] = argv, env["COMPOSE_PROFILES"]
        stdout.write(b"PGDMP")
        return types.SimpleNamespace(returncode=0)

    out = tmp_path / "x.dump"
    assert cloud_backup.dump(str(out), runner=run) == 0
    assert out.read_bytes() == b"PGDMP" and not (tmp_path / "x.dump.part").exists()
    assert seen["argv"][2:] == [
        "exec",
        "-T",
        "postgres",
        "pg_dump",
        "-U",
        "splouch",
        "-Fc",
        "splouch",
    ]
    assert seen["profiles"] == "control"


def test_a_failed_dump_leaves_nothing(box, tmp_path):
    box.write_text("ROLES=control\n")

    def run(argv, env, cwd, stdout, check):
        return types.SimpleNamespace(returncode=1)

    out = tmp_path / "x.dump"
    assert cloud_backup.dump(str(out), runner=run) == 1
    assert not out.exists() and not (tmp_path / "x.dump.part").exists()


def test_restore_replaces_the_store_from_the_file(box, tmp_path):
    box.write_text("ROLES=control\n")
    src = tmp_path / "x.dump"
    src.write_bytes(b"PGDMP")
    seen = {}

    def run(argv, env, cwd, stdin, check):
        seen["argv"], seen["data"] = argv, stdin.read()
        return types.SimpleNamespace(returncode=0)

    assert cloud_backup.restore(str(src), runner=run) == 0
    assert "--clean" in seen["argv"] and "--if-exists" in seen["argv"]
    assert seen["data"] == b"PGDMP"


# ── WireGuard ──────────────────────────────────────────────────────────────────

WG = os.path.join(REPO, "install", "scripts", "wireguard.sh")
KEY = "a" * 42 + "A="


def wg(fn, *args):
    return subprocess.run(
        ["bash", "-c", f'source "{WG}"; {fn} "$@"', "_", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_hub_listens_on_its_tunnel_address():
    conf = wg("hub_conf", "PRIV").stdout
    assert "Address = 10.73.0.1/24" in conf and "ListenPort = 51820" in conf


def test_a_node_reaches_the_hub_and_nothing_else():
    conf = wg("node_conf", "PRIV", "10.73.0.12", "mon.example.org", KEY).stdout
    assert "Address = 10.73.0.12/32" in conf
    assert "Endpoint = mon.example.org:51820" in conf
    assert "AllowedIPs = 10.73.0.1/32" in conf, "the hub only, never another node"
    assert "PersistentKeepalive = 25" in conf


def test_a_peer_block_names_its_node():
    block = wg("peer_block", "us1", KEY, "10.73.0.12").stdout
    assert "# us1" in block and "AllowedIPs = 10.73.0.12/32" in block


@pytest.mark.parametrize(
    "fn,value,ok",
    [
        ("valid_key", KEY, True),
        ("valid_key", "not-a-key", False),
        ("valid_address", "10.73.0.12", True),
        ("valid_address", "10.73.0.1", False),  # the hub's
        ("valid_address", "10.73.0.255", False),
        ("valid_address", "192.168.1.5", False),
        ("valid_name", "us1", True),
        ("valid_name", "us1; rm -rf /", False),
        ("valid_host", "mon.example.org", True),
        ("valid_host", "a b", False),
    ],
)
def test_inputs_are_checked_before_anything_is_written(fn, value, ok):
    assert (wg(fn, value).returncode == 0) is ok


def test_the_private_key_stays_root_only_and_out_of_the_output():
    script = read("install", "scripts", "wireguard.sh")
    assert "umask 077" in script
    assert 'cat "$WG_DIR/splouch.key"' not in script.split("echo")[-1]
    assert 'install -m 644 "$WG_DIR/splouch.pub"' in script, (
        "only the public key is shared"
    )


def test_the_installer_asks_for_the_parts_once():
    script = read("install", "install.sh")
    assert "grep -q '^ROLES=.'" in script
    assert 'wireguard.sh" hub' in script and 'wireguard.sh" node' in script


def test_each_backup_run_leaves_its_status_for_the_panel():
    script = yaml.safe_load(read("cloud", "docker-compose.yml"))["services"]["backup"][
        "command"
    ][2]
    assert "status true" in script and "status false" in script
    assert "status.json.part" in script and "mv /backups/status.json.part" in script
    control = yaml.safe_load(read("cloud", "docker-compose.yml"))["services"]["control"]
    assert "/var/backups/splouch:/backups:ro" in control["volumes"]
