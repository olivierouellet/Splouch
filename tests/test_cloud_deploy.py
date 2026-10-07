"""Bringing a box to a version (cloud/cloud_deploy.py), and what calls it.

The webhook and the installer both end here: size the worker set, record the
version, pull the image CI published — or build it when there is none — start the
containers, and reload Caddy, which keeps every open socket.
"""

import json
import os
import types

import pytest
import yaml

import cloud_deploy
import cloud_workers

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def box(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("SPLOUCH_DOMAIN=x\nWORKERS=2\n")
    monkeypatch.setattr(cloud_workers, "ENV_FILE", str(env))
    monkeypatch.setattr(cloud_workers, "COMPOSE_OUT", str(tmp_path / "w.yml"))
    monkeypatch.setattr(
        cloud_workers, "CADDY_OUT", str(tmp_path / "caddy.d" / "w.caddy")
    )
    return env


def main(calls):
    """The main stack's commands, without the monitoring stack's."""
    return [c for c in calls if c[0][0] != "--env-file"]


HEAD = "c0ffee"


def runner(fail=(), running=(), head=HEAD, built=HEAD):
    """`running`: the compose files whose project has containers (`ps -a -q`).
    `head`: the checkout's commit (`git rev-parse`, '' when unreadable); `built`:
    the commit the pulled image is stamped with ('' when unstamped)."""
    calls = []

    def run(argv, env, cwd, check, **kw):
        if argv[-3:] == ["ps", "-a", "-q"]:
            return types.SimpleNamespace(
                returncode=0, stdout="abc\n" if argv[5] in running else ""
            )
        if argv[0] == "git":
            return types.SimpleNamespace(returncode=0 if head else 128, stdout=head)
        if argv[1:3] == ["image", "inspect"]:
            return types.SimpleNamespace(returncode=0, stdout=built or "<no value>")
        calls.append((argv[2:], env.get("COMPOSE_FILE", ""), env.get("SPLOUCH_COMMIT")))
        return types.SimpleNamespace(returncode=1 if argv[2] in fail else 0)

    return run, calls


def test_a_release_is_pulled_started_and_caddy_reloaded(box):
    run, calls = runner()
    assert cloud_deploy.deploy("v2026.10.3", runner=run) == 0
    calls = main(calls)
    assert [c[0][0] for c in calls] == ["pull", "up", "exec"]
    assert "--remove-orphans" in calls[1][0]
    assert calls[2][0][-4:-2] == ["--config", "/etc/caddy/Caddyfile"]
    assert "build" not in calls[1][1]
    assert "SPLOUCH_VERSION=v2026.10.3" in box.read_text().splitlines()


def test_no_image_to_pull_builds_it_here(box):
    run, calls = runner(fail={"pull"})
    assert cloud_deploy.deploy("v2026.10.4", runner=run) == 0
    calls = main(calls)
    assert [c[0][0] for c in calls] == ["pull", "build", "up", "exec"]
    assert calls[2][0][:4] == ["up", "-d", "--pull", "never"]
    assert calls[1][1].endswith(":docker-compose.build.yml")


def test_master_pulled_before_ci_published_it_is_built_here(box):
    """The webhook checks the new commit out at once; CI takes minutes to publish
    its image, so the pull gets the previous one."""
    run, calls = runner(built="0ld")
    assert cloud_deploy.deploy("master", runner=run) == 0
    calls = main(calls)
    assert [c[0][0] for c in calls] == ["pull", "build", "up", "exec"]
    assert calls[1][2] == HEAD, "the local build is stamped with the checkout"
    assert calls[2][0][:4] == ["up", "-d", "--pull", "never"]


def test_master_pulled_after_ci_published_it_is_used(box):
    run, calls = runner()
    assert cloud_deploy.deploy("master", runner=run) == 0
    assert [c[0][0] for c in main(calls)] == ["pull", "up", "exec"]


def test_an_unstamped_master_image_is_built_here(box):
    run, calls = runner(built="")
    cloud_deploy.deploy("master", runner=run)
    assert "build" in [c[0][0] for c in main(calls)]


def test_an_unstamped_release_image_is_trusted(box):
    """Published before images were stamped: its tag only ever named one build."""
    run, calls = runner(built="")
    cloud_deploy.deploy("v2026.10.3", runner=run)
    assert [c[0][0] for c in main(calls)] == ["pull", "up", "exec"]


def test_a_release_image_of_another_commit_is_built_here(box):
    run, calls = runner(built="0ld")
    cloud_deploy.deploy("v2026.10.3", runner=run)
    assert "build" in [c[0][0] for c in main(calls)]


def test_no_checkout_commit_no_check(box):
    run, calls = runner(head="", built="0ld")
    cloud_deploy.deploy("master", runner=run)
    assert [c[0][0] for c in main(calls)] == ["pull", "up", "exec"]


def test_a_branch_is_built_without_trying_to_pull(box):
    run, calls = runner()
    assert cloud_deploy.deploy("local-fix", build=True, runner=run) == 0
    assert [c[0][0] for c in main(calls)] == ["build", "up", "exec"]


def test_a_failed_start_stops_before_caddy(box):
    run, calls = runner(fail={"up"})
    assert cloud_deploy.deploy("master", runner=run) == 1
    assert calls[-1][0][0] == "up"


def test_the_worker_set_is_sized_on_every_deploy(box):
    run, _ = runner()
    cloud_deploy.deploy("master", runner=run)
    with open(cloud_workers.COMPOSE_OUT, encoding="utf-8") as f:
        services = yaml.safe_load(f)["services"]
    assert sorted(services) == ["app", "app2"]


def test_the_version_replaces_the_old_line(box):
    cloud_deploy.set_env("SPLOUCH_VERSION", "a")
    cloud_deploy.set_env("SPLOUCH_VERSION", "b")
    assert [ln for ln in box.read_text().splitlines() if "VERSION" in ln] == [
        "SPLOUCH_VERSION=b"
    ]


# ── What calls it ──────────────────────────────────────────────────────────────


def read(*parts):
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def test_the_webhook_deploys_the_image_of_the_ref_it_checked_out():
    src = read("cloud", "deploy_webhook.py")
    assert 'image = "master"' in src
    assert "image = version" in src  # a release tag
    assert 'image = "--build local-" + version.replace("/", "-")' in src  # a branch
    assert "image = '\"${LATEST:-master}\"'" in src
    assert "python3 cloud_deploy.py {image}" in src
    assert "docker compose up" not in src, "the webhook starts nothing itself"


def test_the_installer_deploys_the_checkout_it_made():
    src = read("install", "install.sh")
    assert "describe --tags --exact-match HEAD 2>/dev/null || true" in src
    assert "_version=master _deploy_args=master" in src
    assert '_deploy_args="--build $_version"' in src  # a branch or commit
    assert '_version="local-$(git -C "$INSTALL_DIR" rev-parse --short HEAD)"' in src
    assert "python3 cloud_deploy.py $_deploy_args" in src


def test_compose_pulls_the_published_image_and_every_worker_reports_it():
    compose = yaml.safe_load(read("cloud", "docker-compose.yml"))["services"]
    for name in ("control", "app"):
        assert compose[name]["image"].startswith(
            "${SPLOUCH_IMAGE:-ghcr.io/olivierouellet/splouch-cloud}:${SPLOUCH_VERSION"
        )
        assert "build" not in compose[name]
    assert compose["app"]["environment"]["SPLOUCH_VERSION"].startswith(
        "${SPLOUCH_VERSION"
    )
    build = yaml.safe_load(read("cloud", "docker-compose.build.yml"))["services"]
    assert build["control"]["build"]["dockerfile"] == "cloud/Dockerfile"


def test_ci_publishes_one_tag_for_both_architectures():
    wf = yaml.safe_load(read(".github", "workflows", "image.yml"))
    on = wf[True] if True in wf else wf["on"]
    assert on["push"]["tags"] == ["v*"] and on["push"]["branches"] == ["master"]
    arches = {m["arch"] for m in wf["jobs"]["build"]["strategy"]["matrix"]["include"]}
    assert arches == {"amd64", "arm64"}
    assert "imagetools create" in str(wf["jobs"]["manifest"])
    # Stamped with its commit, which a deploy checks against its checkout.
    assert '--build-arg "SPLOUCH_COMMIT=$GITHUB_SHA"' in str(wf["jobs"]["build"])
    dockerfile = read("cloud", "Dockerfile")
    assert "LABEL org.opencontainers.image.revision=$SPLOUCH_COMMIT" in dockerfile
    build = yaml.safe_load(read("cloud", "docker-compose.build.yml"))["services"]
    assert build["control"]["build"]["args"]["SPLOUCH_COMMIT"] == "${SPLOUCH_COMMIT:-}"
    assert wf["permissions"] == {}, "write access only where a job needs it"


# ── Monitoring ─────────────────────────────────────────────────────────────────


@pytest.fixture
def routes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cloud_deploy, "GRAFANA_ROUTE", str(tmp_path / "monitoring.caddy")
    )
    monkeypatch.setattr(cloud_deploy, "STATUS_SITE", str(tmp_path / "monitoring.site"))
    return tmp_path


def test_monitoring_on_starts_its_stack_and_routes_grafana(box, routes):
    box.write_text(box.read_text() + "MONITORING=1\nSTATUS_DOMAIN=status.example.org\n")
    run, calls = runner()
    assert cloud_deploy.deploy("master", runner=run) == 0
    stack = [c for c in calls if "monitoring/docker-compose.yml" in " ".join(c[0])]
    assert stack and stack[0][0][-3:] == ["up", "-d", "--remove-orphans"]
    assert "reverse_proxy grafana:3000" in (routes / "monitoring.caddy").read_text()
    assert (routes / "monitoring.site").read_text().splitlines()[
        1
    ] == "status.example.org {"
    assert calls[-1][0][0] == "exec", "Caddy reloads last, with the routes in place"


def test_monitoring_off_stops_it_and_removes_its_routes(box, routes):
    (routes / "monitoring.caddy").write_text("old")
    run, calls = runner(running={cloud_deploy.MONITORING_COMPOSE})
    cloud_deploy.deploy("master", runner=run)
    downs = [c for c in calls if c[0][-2:] == ["down", "--remove-orphans"]]
    assert [c[0][3] for c in downs] == [cloud_deploy.MONITORING_COMPOSE]
    assert not (routes / "monitoring.caddy").exists()


def test_a_stack_that_never_ran_is_not_stopped(box, routes):
    """`down` on a project with no containers warns "No resource found to remove"."""
    run, calls = runner()
    assert cloud_deploy.deploy("master", runner=run) == 0
    assert not any(c[0][-2:] == ["down", "--remove-orphans"] for c in calls)


def test_no_status_domain_no_public_kuma():
    assert cloud_deploy.monitoring_routes({"MONITORING": "1"})[1] == ""


def test_prometheus_learns_every_worker():
    targets = json.loads(cloud_workers.prometheus_targets(3))
    assert [t["targets"] for t in targets] == [
        ["app:5000"],
        ["app2:5000"],
        ["app3:5000"],
    ]
