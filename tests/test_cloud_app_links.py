"""The two `/.well-known/` files that make a scanned code open the app (`P-16`).

This is the half of the feature with no symptom on the server. Everything here
returns 200 in a browser whether or not it is right; what reads these files is
the operating system, once, at install time, on someone else's phone. Android
fetches `assetlinks.json` and **follows no redirects**; while it is wrong or
missing, `adb shell pm get-app-links app.splouch.android` reports `splouch.ca:
1024` — no response — and the OS offers a chooser instead of opening the app.
Nobody deploying the cloud would notice.

So the tests below pin the things that would silently break it:

* the exact paths, the `application/json` content type, and 200 without a hop —
  `apple-app-site-association` in particular has **no extension**, which is the
  first thing a well-meaning rename would add;
* the fingerprints staying out of source and coming from configuration, with the
  environment and the data file *accumulating* so a debug build can be tested
  without cutting a release;
* a 404 rather than a well-formed file with nothing in it, when nothing is
  configured at all;
* `cloud/Caddyfile` still letting `/.well-known/` through to the app.
"""
import json
import os
import sys
import tempfile

import pytest
import yaml

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='splouch-cloud-test-'))
sys.path.insert(0, os.path.join(REPO, 'cloud'))

import cloud_server as cs            # noqa: E402
from fastapi import HTTPException    # noqa: E402

COMPOSE = os.path.join(REPO, 'cloud', 'docker-compose.yml')
CADDYFILE = os.path.join(REPO, 'cloud', 'Caddyfile')

RELEASE = '14:6D:E9:83:C5:73:06:50:D8:EE:B9:95:2F:34:FC:64:16:A0:83:42:E6:1D:BE:A8:8A:04:96:B2:3F:CF:44:E5'
DEBUG = 'AA:11:BB:22:CC:33:DD:44:EE:55:FF:66:77:88:99:00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00'


@pytest.fixture
def config(tmp_path, monkeypatch):
    """A clean data dir and a clean environment for the app-link config.

    Both sources are read per request rather than at import, so a test can set
    either and call the route — which is also what lets an operator add a debug
    fingerprint without restarting the container.
    """
    monkeypatch.setattr(cs, 'APPLINKS_FILE', str(tmp_path / 'applinks.json'))
    for key in ('ANDROID_CERT_FINGERPRINTS', 'ANDROID_PACKAGE_NAME', 'IOS_APP_IDS',
                'STORE_URL_ANDROID', 'STORE_URL_IOS'):
        monkeypatch.delenv(key, raising=False)

    def write(**data):
        (tmp_path / 'applinks.json').write_text(json.dumps(data))
    return write


def body(response):
    return json.loads(response.body)


# ── assetlinks.json ────────────────────────────────────────────────────────────

def test_assetlinks_is_the_shape_android_reads(config, monkeypatch):
    monkeypatch.setenv('ANDROID_CERT_FINGERPRINTS', RELEASE)
    response = cs.route_assetlinks()
    assert response.status_code == 200
    assert response.media_type == 'application/json'
    statement, = body(response)
    assert statement['relation'] == ['delegate_permission/common.handle_all_urls']
    assert statement['target'] == {
        'namespace': 'android_app',
        'package_name': 'app.splouch.android',
        'sha256_cert_fingerprints': [RELEASE],
    }


def test_no_fingerprint_is_a_404_and_not_an_empty_list(config):
    """Both leave the app unverified; only one of them says so to `curl -i`.

    A well-formed file with an empty list looks deployed. It fails at install
    time, on a phone, weeks later, with `1024` as the whole diagnosis.
    """
    with pytest.raises(HTTPException) as raised:
        cs.route_assetlinks()
    assert raised.value.status_code == 404


def test_the_fingerprint_is_configuration_and_not_source():
    """With Play App Signing the value to publish is the *App signing* key's.

    Not the upload key's, and different again for a debug build — a value this
    repo cannot know and must never carry.
    """
    source = open(os.path.join(REPO, 'cloud', 'cloud_server.py'), encoding='utf-8').read()
    assert 'sha256_cert_fingerprints' in source, 'sanity: this is the right file'
    assert RELEASE not in source
    # A colon-separated 32-byte hex string anywhere in the module would be one.
    assert not [line for line in source.splitlines()
                if line.count(':') > 20 and 'FINGERPRINT' not in line.upper()]


def test_the_two_configuration_sources_accumulate(config, monkeypatch):
    """The reason there are two: a debug build tested without cutting a release.

    The release fingerprint is set once at deploy time in the environment; a
    debug one goes in `applinks.json` in the data volume, which survives an
    update and needs no compose edit.
    """
    monkeypatch.setenv('ANDROID_CERT_FINGERPRINTS', RELEASE)
    config(android_fingerprints=[DEBUG])
    assert body(cs.route_assetlinks())[0]['target']['sha256_cert_fingerprints'] == \
        [RELEASE, DEBUG]


@pytest.mark.parametrize('raw,expected', [
    (DEBUG.replace(':', ''), DEBUG),                  # the Play Console's copy button
    (DEBUG.lower(), DEBUG),                           # keytool, lowercased somewhere
    (f'{RELEASE} , {DEBUG}', [RELEASE, DEBUG]),        # a list in one env var
    (f'{RELEASE},{RELEASE}', [RELEASE]),               # both sources naming the same key
])
def test_fingerprints_are_normalised_to_the_form_android_compares(config, monkeypatch,
                                                                  raw, expected):
    monkeypatch.setenv('ANDROID_CERT_FINGERPRINTS', raw)
    got = body(cs.route_assetlinks())[0]['target']['sha256_cert_fingerprints']
    assert got == (expected if isinstance(expected, list) else [expected])


def test_a_malformed_fingerprint_does_not_take_the_working_one_down(config, monkeypatch):
    """One bad entry invalidates the whole file for Android.

    So a typo beside a good value must drop the typo, not the file — otherwise a
    fat-fingered env var silently unverifies an app that was working.
    """
    monkeypatch.setenv('ANDROID_CERT_FINGERPRINTS', f'{RELEASE},not-a-fingerprint,DE:AD')
    assert body(cs.route_assetlinks())[0]['target']['sha256_cert_fingerprints'] == [RELEASE]


def test_the_package_name_matches_the_app_that_claims_the_link(config, monkeypatch):
    """`app.splouch.android` — the applicationId in `Splouch-android`'s manifest."""
    monkeypatch.setenv('ANDROID_CERT_FINGERPRINTS', RELEASE)
    assert body(cs.route_assetlinks())[0]['target']['package_name'] == 'app.splouch.android'
    config(android_package='app.splouch.android.dev', android_fingerprints=[DEBUG])
    assert body(cs.route_assetlinks())[0]['target']['package_name'] == \
        'app.splouch.android.dev'


# ── apple-app-site-association ─────────────────────────────────────────────────

def test_aasa_is_the_shape_ios_reads(config):
    response = cs.route_aasa()
    assert response.media_type == 'application/json'
    detail, = body(response)['applinks']['details']
    assert detail['appIDs'] == ['L86UD2L8Q5.app.splouch.ios']
    assert detail['components'] == [{'/': '/add', '?': {'server': '?*'}}]


def test_the_ios_app_id_is_the_team_and_bundle_the_xcode_project_carries():
    """Read from `Splouch-ios`, never guessed — a wrong team id fails silently.

    Skipped rather than failed where the sibling checkout is absent: this repo
    has to build on its own, and the value is pinned above in any case.
    """
    project = os.path.join(REPO, os.pardir, 'Splouch-ios', 'App', 'Splouch.xcodeproj',
                           'project.pbxproj')
    if not os.path.isfile(project):
        pytest.skip('Splouch-ios is not checked out beside this repo')
    source = open(project, encoding='utf-8').read()
    team, bundle = cs.IOS_APP_IDS[0].split('.', 1)
    assert f'DEVELOPMENT_TEAM = {team};' in source
    assert f'PRODUCT_BUNDLE_IDENTIFIER = {bundle};' in source


def test_aasa_claims_the_add_path_and_nothing_else_of_the_site():
    """The picker, a meet and `/admin` must keep opening in the browser."""
    components = body(cs.route_aasa())['applinks']['details'][0]['components']
    assert [c['/'] for c in components] == ['/add']


def test_aasa_is_served_without_a_fingerprint(config):
    """The two files are configured independently: iOS has no keystore to wait on.

    An app id is a property of the app, not of a signing key, so it defaults and
    this file is never the missing half.
    """
    assert body(cs.route_aasa())['applinks']['details']


# ── the paths themselves ───────────────────────────────────────────────────────

@pytest.mark.parametrize('path', ['/.well-known/assetlinks.json',
                                  '/.well-known/apple-app-site-association'])
def test_the_paths_are_exactly_what_the_platforms_fetch(path):
    routes = {r.path for r in cs.app.routes if hasattr(r, 'path')}
    assert path in routes


def test_the_apple_file_has_no_extension():
    """Apple fetches this exact path; a helpful `.json` serves a file nothing asks for."""
    routes = {r.path for r in cs.app.routes if hasattr(r, 'path')}
    assert '/.well-known/apple-app-site-association.json' not in routes


def test_neither_file_is_behind_the_admin_password():
    """`require_admin` on either would be a 401 the OS reads as "no response"."""
    for route in cs.app.routes:
        path = getattr(route, 'path', '')
        if path.startswith('/.well-known/'):
            assert not getattr(route, 'dependencies', []), path


# ── the deployment that has to leave them reachable ────────────────────────────

def test_caddy_sends_well_known_to_the_app():
    """A `handle` or `file_server` above the proxy would swallow both files.

    The only symptom would be `1024` from `pm get-app-links`, on a phone, later.
    Caddy's own ACME handler is scoped to `/.well-known/acme-challenge/*` and does
    not overlap either path.
    """
    directives = [line.strip() for line in open(CADDYFILE, encoding='utf-8')
                  if line.strip() and not line.strip().startswith('#')]
    body_lines = [d for d in directives if d not in ('}',) and not d.endswith('{')]
    assert body_lines == ['reverse_proxy app:5000'], \
        'the site block gained a directive — check it cannot shadow /.well-known/'


def test_the_deployment_passes_the_fingerprints_in():
    """The value lives in `cloud/.env`; compose is what carries it to the app."""
    env = yaml.safe_load(open(COMPOSE))['services']['app']['environment']
    assert env['ANDROID_CERT_FINGERPRINTS'].startswith('${ANDROID_CERT_FINGERPRINTS')
    assert 'app.splouch' not in json.dumps(env), 'a fingerprint or id pinned in compose'
