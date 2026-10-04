"""The worker set a node runs (cloud/cloud_workers.py): one relay process per spare
core, written as a compose override and a Caddy snippet on every deploy."""

import pytest
import yaml

import cloud_workers as cw


@pytest.mark.parametrize(
    "env,cores,want",
    [
        ({}, 8, 6),  # every core but the two reserved
        ({}, 2, 1),  # never below one
        ({}, 1, 1),
        ({"RESERVED_CORES": "1"}, 8, 7),  # a worker-only node
        ({"WORKERS": "3"}, 16, 3),  # pinned
        ({"WORKERS": "0"}, 4, 2),  # unset in practice
        ({"WORKERS": "x", "RESERVED_CORES": "y"}, 4, 2),  # garbage falls back
    ],
)
def test_the_count(env, cores, want):
    assert cw.worker_count(env, cores=cores) == want


def test_the_override_numbers_every_worker_and_tells_each_the_total():
    services = yaml.safe_load(cw.compose_override(3))["services"]
    assert sorted(services) == ["app", "app2", "app3"]
    assert services["app"]["environment"] == {"NODE_WORKERS": "3"}
    assert services["app3"]["extends"] == {
        "file": "docker-compose.yml",
        "service": "app",
    }
    assert services["app3"]["environment"] == {"WORKER": "3", "NODE_WORKERS": "3"}


def test_one_worker_is_just_app():
    assert sorted(yaml.safe_load(cw.compose_override(1))["services"]) == ["app"]


def test_each_route_reaches_its_worker():
    routes = cw.caddy_routes(2)
    assert "handle_path /w1/* {\n    reverse_proxy app:5000\n}" in routes
    assert "handle_path /w2/* {\n    reverse_proxy app2:5000\n}" in routes


def test_env_names_both_compose_files_once(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SPLOUCH_DOMAIN=x\nCOMPOSE_FILE=old.yml\n")
    cw.ensure_compose_file(str(env))
    cw.ensure_compose_file(str(env))
    lines = env.read_text().splitlines()
    assert lines == ["SPLOUCH_DOMAIN=x", f"COMPOSE_FILE={cw.COMPOSE_FILES}"]


def test_the_generated_files_are_not_tracked():
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, ".gitignore"), encoding="utf-8") as f:
        ignore = f.read()
    assert "cloud/docker-compose.workers.yml" in ignore
    assert "cloud/caddy.d/*.caddy" in ignore
