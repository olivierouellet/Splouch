"""The cloud's deployment config — the parts an update can quietly break.

`cloud/Caddyfile` is tracked, and the Update button's master and branch modes deploy
with `git reset --hard`. Anything edited into a tracked file is therefore reverted on
update. The domain used to be edited in exactly there, so an update reset it to the
placeholder; because the Caddyfile is bind-mounted and nothing restarts Caddy at deploy
time, the site kept working until Caddy next restarted and then served the wrong host
with no certificate it could renew.

The domain now lives in `cloud/.env`, which is untracked (and, since this was noticed,
ignored). These tests pin that arrangement from both ends: that nothing puts a literal
domain back into the tracked file, and that the migration lifts one out of an install
made before the change.
"""
import os
import sys

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'cloud'))

CADDYFILE = os.path.join(REPO, 'cloud', 'Caddyfile')
COMPOSE   = os.path.join(REPO, 'cloud', 'docker-compose.yml')
ENV_EXAMPLE = os.path.join(REPO, 'cloud', '.env.example')


def _caddy_site_address():
    for line in open(CADDYFILE, encoding='utf-8'):
        line = line.strip()
        if line.startswith('#') or '{' not in line:
            continue
        return line.rsplit('{', 1)[0].strip()
    return ''


def test_the_caddyfile_carries_no_literal_domain():
    """A domain here is reverted by the next master-mode deploy."""
    assert _caddy_site_address() == '{$SPLOUCH_DOMAIN}'


def test_compose_passes_the_domain_to_caddy_and_nothing_else():
    """Only this one key: `env_file` here would hand Caddy the app's secrets."""
    caddy = yaml.safe_load(open(COMPOSE))['services']['caddy']
    assert 'env_file' not in caddy
    assert list(caddy['environment']) == ['SPLOUCH_DOMAIN']
    # `:?` so an unset domain fails the compose command loudly, leaving the running
    # containers up, rather than restarting Caddy onto an empty site address.
    assert caddy['environment']['SPLOUCH_DOMAIN'].startswith('${SPLOUCH_DOMAIN:?')


def test_the_env_template_documents_the_domain():
    assert 'SPLOUCH_DOMAIN=' in open(ENV_EXAMPLE, encoding='utf-8').read()


def test_the_env_file_is_ignored_but_its_template_is_not():
    """`.env` holds SECRET_KEY, ADMIN_PASSWORD, DEPLOY_SECRET and now the domain."""
    import subprocess
    def ignored(path):
        return subprocess.run(['git', 'check-ignore', '-q', path],
                              cwd=REPO).returncode == 0
    assert ignored('cloud/.env')
    assert not ignored('cloud/.env.example')


# ── The one-time migration ─────────────────────────────────────────────────────

@pytest.fixture
def deploy(monkeypatch, tmp_path):
    """`deploy_webhook` with REPO pointed at a throwaway checkout."""
    os.environ.setdefault('DEPLOY_SECRET', 'test')
    import deploy_webhook as dw
    cloud = tmp_path / 'cloud'
    cloud.mkdir()
    monkeypatch.setattr(dw, 'REPO', str(tmp_path))
    return dw, cloud


def _run(deploy, caddyfile, env):
    dw, cloud = deploy
    (cloud / 'Caddyfile').write_text(caddyfile, encoding='utf-8')
    (cloud / '.env').write_text(env, encoding='utf-8')
    dw._preserve_domain()
    return (cloud / '.env').read_text(encoding='utf-8')


def test_it_lifts_a_domain_out_of_an_old_caddyfile(deploy):
    """The install that predates the change — the one a deploy would have broken."""
    out = _run(deploy, 'splouch.ca {\n    reverse_proxy app:5000\n}\n',
               'SECRET_KEY=x\n')
    assert 'SPLOUCH_DOMAIN=splouch.ca\n' in out


def test_it_appends_cleanly_to_an_env_with_no_trailing_newline(deploy):
    out = _run(deploy, 'splouch.ca {\n}\n', 'SECRET_KEY=x')
    assert out.endswith('SPLOUCH_DOMAIN=splouch.ca\n')
    assert 'SECRET_KEY=x\n' in out


def test_it_leaves_an_already_migrated_env_alone(deploy):
    out = _run(deploy, '{$SPLOUCH_DOMAIN} {\n}\n',
               'SPLOUCH_DOMAIN=splouch.ca\nSECRET_KEY=x\n')
    assert out.count('SPLOUCH_DOMAIN=') == 1


def test_it_does_not_migrate_the_placeholder(deploy):
    """Never configured, so there is nothing to save — let compose say so."""
    out = _run(deploy, 'scores.example.com {\n}\n', 'SECRET_KEY=x\n')
    assert 'SPLOUCH_DOMAIN' not in out


def test_it_ignores_the_comment_block_above_the_site(deploy):
    """The current Caddyfile leads with several `#` lines that contain braces."""
    out = _run(deploy, open(CADDYFILE, encoding='utf-8').read(), 'SECRET_KEY=x\n')
    assert 'SPLOUCH_DOMAIN' not in out, 'read a domain out of the comments'


def test_it_survives_a_missing_env(deploy):
    """A fresh install has no .env yet; the migration must not create one."""
    dw, cloud = deploy
    (cloud / 'Caddyfile').write_text('splouch.ca {\n}\n', encoding='utf-8')
    dw._preserve_domain()
    assert not (cloud / '.env').exists()
