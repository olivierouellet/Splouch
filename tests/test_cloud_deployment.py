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
import re
import sys
import tempfile

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, 'cloud'))
# Importing the module writes credentials.json on first load; keep that out of /data.
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='splouch-cloud-test-'))

import cloud_server as cs        # noqa: E402

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


# ── The deploy webhook's unit, and how its failure reaches the operator ─────────
#
# The webhook is a systemd service outside Docker, and systemd needs absolute paths, so
# install.sh bakes the install directory into its unit. Rename the checkout and the unit
# stops starting. That is recoverable; what made it cost an afternoon is that `/admin`
# swallowed the failure — the version select sat on "Loading…" and the Update button
# stayed live, so a dead webhook looked exactly like a server with no releases.

SERVICE = os.path.join(REPO, 'cloud', 'deploy_webhook.service')
ADMIN   = os.path.join(REPO, 'cloud', 'templates', 'admin.html')


def test_the_unit_keeps_its_placeholders_for_the_installer():
    """install.sh substitutes these; a literal path here would ship someone's homedir."""
    unit = open(SERVICE, encoding='utf-8').read()
    body = unit[unit.index('[Service]'):]
    for line in ('WorkingDirectory=', 'ExecStart=', 'EnvironmentFile=', 'Environment=REPO_DIR='):
        assert line in body, line
    for key in ('ExecStart', 'EnvironmentFile', 'Environment=REPO_DIR', 'WorkingDirectory'):
        assert 'YOUR_INSTALL_DIR' in [l for l in body.splitlines() if l.startswith(key)][0], key
    assert '/home/' not in unit


def test_the_unit_says_what_a_rename_costs():
    """The comment is the only warning at the point someone would move the checkout."""
    unit = open(SERVICE, encoding='utf-8').read()
    header = unit[:unit.index('[Unit]')].lower()
    assert 'renaming' in header or 'rename' in header
    assert 'install.sh' in header


def test_the_admin_page_reports_an_unreachable_webhook():
    """`/admin/versions` already answers `{ok: false, error}` with a 502; the page used
    to `return` on it and swallow fetch failures outright."""
    src = open(ADMIN, encoding='utf-8').read()
    assert 'function updateUnavailable' in src
    # Scoped to loadVersions: the ping loop after a deploy swallows its rejections on
    # purpose, because the server being briefly unreachable is what it is waiting for.
    load = src[src.index('function loadVersions'):src.index('function updateUnavailable')]
    assert 'if (!d.ok) return;' not in load, 'the silent early return is back'
    assert 'catch(() => {})' not in load, 'fetch failures are being swallowed again'
    assert 'updateUnavailable' in load, 'loadVersions no longer reports its failures'
    # The wording lives in the panel table, so it is translated like the rest of the
    # page; what the markup has to carry is the lookup.
    assert 't.webhook_unreachable_hint' in src
    import tomllib
    panel = tomllib.load(open(os.path.join(REPO, 'shared', 'locales', 'panel', 'en.toml'), 'rb'))
    # Names the service, so the message points at the thing to look at.
    assert 'systemctl status deploy-webhook' in panel['cloud']['webhook_unreachable_hint']


def test_an_unavailable_update_section_disables_its_button():
    """Leaving it live only buys a second, vaguer failure when it is pressed."""
    src = open(ADMIN, encoding='utf-8').read()
    body = src[src.index('function updateUnavailable'):]
    body = body[:body.index('// ── Update ──')]
    assert "getElementById('update-btn').disabled = true" in body
    assert 'sel.disabled = true' in body


@pytest.mark.skipif(not __import__('jsc').HAS_JSC, reason='needs JavaScriptCore (macOS)')
def test_the_unavailable_state_is_what_the_operator_sees():
    """Run the page's own `updateUnavailable()` and check what it leaves on screen.

    The whole admin page cannot go through `tests/jsc.py` — it loads htmx, which wants
    XPath the stub deliberately does not model — so this lifts the one function out and
    drives it, the way `test_search_suggestions` does with the fold.
    """
    import json as _json
    import re as _re
    import subprocess as _sp
    import tempfile as _tf

    import tomllib
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(
        [os.path.join(REPO, 'cloud', 'templates'), os.path.join(REPO, 'shared', 'templates')]))
    env.globals['url_for'] = lambda n, **kw: '/static/' + kw.get('filename', '')
    with open(os.path.join(REPO, 'shared', 'locales', 'panel', 'fr.toml'), 'rb') as f:
        t = tomllib.load(f)['cloud']
    # Rendered, not raw: the message is `{{ t.… | tojson }}` now, and French proves the
    # operator gets their own language rather than a hard-coded English sentence.
    src = env.get_template('admin.html').render(
        t=t, has_deploy=True, creds_error=None, keys=[], active_meets=[],
        user_name='Admin', locales=[], current_locale='', ui_lang_cookie='',
        analytics_enabled=False, picker_window_title_form='', picker_title_form='',
        picker_logo_above=False, has_picker_logo=False, has_picker_icon=False,
        picker_max_upload=2 * 1024 * 1024)
    fn = _re.search(r'^        function updateUnavailable\(reason\) \{.*?^        \}',
                    src, _re.S | _re.M)
    assert fn, 'updateUnavailable is no longer a top-level function in admin.html'

    harness = '''
    var els = {};
    function el(id) {
      if (!els[id]) els[id] = { id: id, innerHTML: '', textContent: '', className: '',
                                disabled: false, kids: [],
                                appendChild: function (c) { this.kids.push(c); } };
      return els[id];
    }
    var document = { getElementById: el,
                     createElement: function (t) { return { tag: t, textContent: '',
                                                            disabled: false, selected: false }; } };
    ''' + fn.group(0) + '''
    updateUnavailable('Connection refused');
    JSON.stringify({
      selectDisabled: els['update-version'].disabled,
      buttonDisabled: els['update-btn'].disabled,
      current:        els['current-version'].textContent,
      status:         els['update-status'].textContent,
      statusClass:    els['update-status'].className,
      optionCount:    els['update-version'].kids.length
    })
    '''
    with _tf.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(harness)
        path = fh.name
    try:
        res = _sp.run(['osascript', '-l', 'JavaScript', path], capture_output=True, text=True)
    finally:
        os.remove(path)
    assert res.returncode == 0, res.stderr
    out = _json.loads(res.stdout)

    assert out['selectDisabled'] is True
    assert out['buttonDisabled'] is True, 'a live button here only fails again, vaguer'
    assert out['optionCount'] == 1, 'the select must not keep sitting on "Loading…"'
    assert 'Connection refused' in out['status'], 'the real reason has to reach the page'
    assert 'systemctl status deploy-webhook' in out['status']
    assert t['webhook_unreachable'] in out['status'], 'not shown in the panel language'
    assert out['current'] == t['unknown']
    assert 'danger' in out['statusClass'], 'must not read as ordinary secondary text'


# ── Appearance tab: the logo upload ────────────────────────────────────────────
#
# Choosing a file only stages it; nothing reaches the server until Save. The preview
# is therefore the whole of the feedback that the pick registered — and it never
# appeared, because the JS reveals it with `hidden = false` while the markup hid it
# with an inline `display:none` that no attribute can lift. The upload looked broken
# from the first click, and the working Save button below it went unused.

@pytest.mark.parametrize('pane', ['picker-logo-preview', 'picker-icon-preview'])
def test_the_upload_previews_are_hidden_the_way_the_script_unhides_them(pane):
    src = open(ADMIN, encoding='utf-8').read()
    div = src[src.index('id="%s"' % pane):]
    div = div[:div.index('>')]
    assert 'hidden' in div, f'{pane} must use the attribute previewImage() clears'
    assert 'display:none' not in div.replace(' ', ''), \
        f'{pane}: an inline display:none outranks `hidden = false`, so it never shows'


def test_the_logo_field_names_the_formats_it_takes():
    """`image/*` offers the operator HEIC and TIFF, which no browser will draw."""
    src = open(ADMIN, encoding='utf-8').read()
    field = src[src.index('name="picker_logo"'):]
    accept = re.search(r'accept="([^"]*)"', field).group(1).split(',')
    assert accept == list(cs.LOGO_MIME_TYPES), 'the dialog and the server must agree'
    assert '{{ t.logo_hint }}' in src, 'the accepted formats are not shown on the page'


def test_the_icon_field_takes_only_what_the_manifest_promises():
    """/picker_manifest declares `image/png` for both icon sizes."""
    src = open(ADMIN, encoding='utf-8').read()
    field = src[src.index('name="picker_icon"'):]
    accept = re.search(r'accept="([^"]*)"', field).group(1).split(',')
    assert accept == list(cs.ICON_MIME_TYPES)


def test_the_accepted_formats_in_the_hint_are_the_ones_the_server_stores():
    """A hint that promises more than the server takes is the bug, rearranged."""
    import tomllib
    panel = tomllib.load(open(os.path.join(REPO, 'shared', 'locales', 'panel', 'en.toml'), 'rb'))
    hint = panel['cloud']['logo_hint'].upper()
    for mime in cs.LOGO_MIME_TYPES:
        assert mime.split('/')[-1].split('+')[0].upper() in hint, \
            f'{mime} is accepted but the operator is never told'
    assert '%d MB' % (cs.MAX_IMAGE_BYTES // (1024 * 1024)) in panel['cloud']['logo_hint']


def test_an_svg_logo_cannot_run_script_on_this_origin():
    """SVG is a document. The picker's `<img>` inerts it; opening /picker_logo does not."""
    src = open(os.path.join(REPO, 'cloud', 'cloud_server.py'), encoding='utf-8').read()
    body = src[src.index('def route_picker_logo'):]
    body = body[:body.index('@app.get', 1)]
    assert 'image/svg+xml' in cs.LOGO_MIME_TYPES, 'this test is only needed while SVG is taken'
    assert 'Content-Security-Policy' in body and 'sandbox' in body
