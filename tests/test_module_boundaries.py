"""The dependency direction inside `server/`, enforced rather than hoped for.

`state` used to be one 1069-line module holding the filesystem layout, every
language and theme helper, the settings dict, the loaded meet, the console log
ring, the worker's runtime flags and the live decoder. Thirty-eight files import
it, so every one of them pulled all of that in to read a directory name.

It is now three modules with one direction of travel:

    paths  ──►  i18n  ──►  state

* `paths` — where files live. Imports nothing of ours.
* `i18n`  — anything answerable from a language code alone. Imports `paths`.
* `state` — this server's runtime: settings, meet, decoder, worker flags. Imports
  both, and re-exports their names so existing `state.X` callers are unaffected.

The tests below guard the two properties that make that worth having: no cycles,
and `i18n` staying free of runtime state. The second is the one with a payoff
still to come — `cloud_server.py` carries its own copy of this logic, and a module
that reads no settings is one that can move to `shared/` and be used by both
(notes/cloud_parity.md).
"""
import ast
import os
import re
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, 'server')
sys.path.insert(0, SERVER)

# The cloud modules read DATA_DIR at import time, so it has to be pointed
# somewhere disposable before the first import — and `cloud/` has to be on the
# path here rather than relied on from whichever test file happened to run first.
os.environ.setdefault('DATA_DIR', tempfile.mkdtemp(prefix='splouch-boundaries-'))
sys.path.insert(0, os.path.join(REPO, 'cloud'))


def _imports(module):
    """Top-level module names `module` imports, ours and the stdlib's alike."""
    tree = ast.parse(open(os.path.join(SERVER, module + '.py'), encoding='utf-8').read())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split('.')[0])
    return found


LOCAL = {'paths', 'i18n', 'state', 'bus', 'worker', 'relay', 'web', 'meet_data', 'app'}


def test_paths_depends_on_nothing_of_ours():
    """It is the bottom of the stack: everything else may need to find a file."""
    assert _imports('paths') & LOCAL == set()


def test_i18n_depends_only_on_paths():
    assert _imports('i18n') & LOCAL == {'paths'}


def test_there_is_no_cycle_back_into_state():
    """`state` may import the other two; neither may import it back."""
    assert 'state' not in _imports('paths')
    assert 'state' not in _imports('i18n')
    assert {'paths', 'i18n'} <= _imports('state')


def test_i18n_reads_no_settings():
    """The property that makes it movable into `shared/`.

    A `settings` lookup creeping back in is what would quietly re-couple this to
    one Pi's runtime, and it would not fail any other test — the wrappers in
    `state` would still work.
    """
    src = open(os.path.join(SERVER, 'i18n.py'), encoding='utf-8').read()
    tree = ast.parse(src)
    offenders = [
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id == 'settings'
    ]
    assert not offenders, f'i18n.py reads `settings` at line(s) {offenders}'


def test_the_language_helpers_take_a_code_rather_than_finding_one():
    """Every public entry point in `i18n` is answerable without this server."""
    import i18n

    for name in ('i18n_bundle', 'labels_for', 'display_strings', 'panel_strings',
                 'event_translations', 'locale_section', 'panel_section'):
        fn = getattr(i18n, name)
        first = fn.__code__.co_varnames[0]
        assert first == 'code', f'i18n.{name} takes {first!r}, not a language code'


@pytest.mark.parametrize('name', [
    # A sample of what callers reach for as `state.X`; the split must not have
    # moved any of them out from under the thirty-eight files that do.
    'MEET_FOLDER', 'IMAGES_DIR', 'SERVICE_NAME', 'REPO_DIR', 'app_dir',
    'session_secret', 'DEFAULT_THEME_COLORS', 'DEFAULT_THEME_FONTS',
    'load_locale', 'load_theme', 'i18n_bundle', 'load_event_translations',
    'parse_event_name', 'translate_event_name', 'settings_strings',
    'display_strings', 'available_locales', 'list_locales', 'resolve_labels',
])
def test_the_moved_names_still_resolve_on_state(name):
    import state
    assert hasattr(state, name), f'state.{name} disappeared in the split'


def test_the_wrappers_still_default_to_the_meets_language(monkeypatch):
    """`state.load_locale()` with no argument must still answer in the language
    the meet is being run in — that defaulting is the wrappers' whole job."""
    import i18n
    import state

    monkeypatch.setitem(state.settings, 'locale', 'fr')
    monkeypatch.setitem(state.settings, 'label_style', 'long')
    assert state.load_locale() == i18n.labels_for('fr', 'long')
    assert state.i18n_bundle()['lang'] == 'fr'
    assert state.load_event_translations() == i18n.event_translations('fr')

    # An explicit code still wins over the setting.
    assert state.display_strings('es') == i18n.display_strings('es')


# ── The cloud relay's modules ─────────────────────────────────────────────────
# `cloud_server.py` was one 2117-line file. It is now several flat modules with
# the same one-way dependency rule as `server/`:
#
#     cloud_paths ──► cloud_auth ──► cloud_analytics
#                 ──► cloud_bus
#                          all ──► cloud_server
#
# The `cloud_` prefix is load-bearing rather than decorative: `server/` and
# `cloud/` are both flat on sys.path when this suite runs, so a plain `bus.py` in
# `cloud/` would shadow the Pi's and the failure would look like nonsense in an
# unrelated test. It is also what the Dockerfile globs on.

CLOUD = os.path.join(REPO, 'cloud')
CLOUD_MODULES = ('cloud_paths', 'cloud_bus', 'cloud_auth', 'cloud_analytics')


def _cloud_imports(module):
    tree = ast.parse(open(os.path.join(CLOUD, module + '.py'), encoding='utf-8').read())
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split('.')[0])
    return found


def test_cloud_paths_depends_on_nothing_of_ours():
    assert not (_cloud_imports('cloud_paths') & set(CLOUD_MODULES + ('cloud_server',)))


@pytest.mark.parametrize('module', CLOUD_MODULES)
def test_no_cloud_module_imports_the_app_back(module):
    """`cloud_server` may import all of them; none may import it."""
    assert 'cloud_server' not in _cloud_imports(module)


def test_every_cloud_module_carries_the_prefix():
    """A module here without it would shadow the Pi's same-named one."""
    stray = [f for f in os.listdir(CLOUD)
             if f.endswith('.py') and not f.startswith('cloud_')
             and f != 'deploy_webhook.py']
    assert not stray, f'{stray} would collide with server/ on sys.path'


def test_the_dockerfile_ships_every_cloud_module():
    """The image used to copy one file. A module added without a Dockerfile edit
    would import fine in tests and crash the container on boot."""
    dockerfile = open(os.path.join(CLOUD, 'Dockerfile'), encoding='utf-8').read()
    assert 'COPY cloud/cloud_*.py' in dockerfile, \
        'the image no longer globs the cloud modules — check every one is copied'
    # And the host-only webhook stays out of the image.
    assert 'COPY cloud/deploy_webhook.py' not in dockerfile


def test_the_container_entrypoint_still_names_a_real_app():
    dockerfile = open(os.path.join(CLOUD, 'Dockerfile'), encoding='utf-8').read()
    assert 'cloud_server:app' in dockerfile
    assert os.path.exists(os.path.join(CLOUD, 'cloud_server.py'))


# ── The relay's meet store ────────────────────────────────────────────────────
# `cloud_store` owns `_meets`, `_retained`, `_relay_sids` and the lock over them.
# `cloud_server` imports the objects, not copies: they are bound once and only ever
# mutated in place, which is what lets ~30 `with _lock:` blocks and ~43 `_meets` /
# `_retained` accesses in the routes stay exactly as they were.
#
# That contract is invisible in the source. Rebinding one of them in `cloud_store`
# — `_retained = {}` in a reset helper, say — would leave `cloud_server` holding the
# old dict, and the relay would serve meets that registrations no longer reach. No
# other test would notice, so these check it directly.

STORE_OBJECTS = ('_meets', '_retained', '_relay_sids', '_lock')


@pytest.mark.parametrize('name', STORE_OBJECTS)
def test_the_store_is_shared_by_reference_not_copied(name):
    import cloud_server
    import cloud_store
    assert getattr(cloud_server, name) is getattr(cloud_store, name), (
        f'cloud_server.{name} is a different object from cloud_store.{name} — '
        'something rebound it instead of mutating it')


def test_a_write_through_one_name_is_seen_through_the_other():
    import cloud_server
    import cloud_store

    cloud_server._meets['__probe__'] = {'name': 'probe'}
    try:
        assert cloud_store._meets.get('__probe__') == {'name': 'probe'}
    finally:
        cloud_store._meets.pop('__probe__', None)
    assert '__probe__' not in cloud_server._meets


def test_cloud_store_never_rebinds_its_own_containers():
    """The source-level guard for the two tests above: after the initial binding,
    these names must only ever be subscripted, never assigned."""
    src = open(os.path.join(CLOUD, 'cloud_store.py'), encoding='utf-8').read()
    tree = ast.parse(src)
    rebinds = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in STORE_OBJECTS:
                    # The one binding at module level is the definition itself.
                    if node.col_offset != 0:
                        rebinds.append((target.id, node.lineno))
    assert not rebinds, f'rebound inside a function: {rebinds}'


def test_the_store_holds_no_web_framework_import():
    """It is a data layer. A Request or a route decorator turning up here means the
    boundary has started to blur."""
    imports = _cloud_imports('cloud_store')
    assert not (imports & {'fastapi', 'starlette'}), imports


# ── The one copy both servers share ──────────────────────────────────────────
# `shared/py/splouch_i18n.py` is the label-style rule, the locale readers, the
# `GET /i18n/{lang}` body and the shipped palette — once, for the Pi and the relay
# both. Each side keeps a thin module (`server/i18n.py`, `cloud/cloud_i18n.py`)
# holding what is genuinely its own: the Pi re-reads locale files so an operator
# editing one sees it immediately; the relay caches, serving one fixed set baked
# into its image to many phones.

SHARED_PY = os.path.join(REPO, 'shared', 'py')


def test_the_shared_module_knows_nothing_about_either_server():
    """The property that lets one file serve both. An import of `paths`,
    `cloud_paths`, `state` or `settings` here would tie it back to one of them."""
    tree = ast.parse(open(os.path.join(SHARED_PY, 'splouch_i18n.py'), encoding='utf-8').read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert imported <= {'glob', 'os', 'tomllib'}, f'reaches outside the stdlib: {imported}'

    names = {n.id for n in ast.walk(tree)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert not (names & {'settings', 'paths', 'cloud_paths', 'state'})


def test_both_servers_resolve_to_the_same_shared_module():
    import cloud_i18n
    import i18n
    import splouch_i18n
    assert i18n.splouch_i18n is splouch_i18n
    assert cloud_i18n.splouch_i18n is splouch_i18n


@pytest.mark.parametrize('name', ['DEFAULT_THEME_COLORS', 'DEFAULT_THEME_FONTS',
                                  'STYLED_LABEL_KEYS', 'resolve_labels'])
def test_the_shared_values_are_one_object_not_two(name):
    """Identity, not equality: two equal copies is the state this move ended."""
    import i18n
    import splouch_i18n
    assert getattr(i18n, name) is getattr(splouch_i18n, name)


@pytest.mark.parametrize('lang', ['en', 'fr', 'es', 'zz'])
def test_the_two_servers_serve_an_identical_bundle(lang):
    """docs/api.md §5.9 promises a client may cache either server's answer. It used
    to be two implementations that happened to agree; now it is one, and `zz` (a
    language neither ships) checks they still fall back to English together."""
    import json

    import cloud_i18n
    import state
    assert json.dumps(state.i18n_bundle(lang), sort_keys=True) == \
           json.dumps(cloud_i18n.i18n_bundle(lang), sort_keys=True)


def test_the_relay_still_caches_and_the_pi_still_does_not():
    """The deliberate difference. If the Pi started caching, an operator editing a
    locale file would need a restart to see it."""
    import cloud_i18n
    import i18n
    assert hasattr(cloud_i18n, '_locale_cache')
    assert not hasattr(i18n, '_locale_cache')


def test_the_image_ships_the_shared_module():
    """It is outside cloud/, so the `cloud_*.py` glob does not reach it. Without
    its own COPY the container imports fine in tests and dies on boot."""
    dockerfile = open(os.path.join(CLOUD, 'Dockerfile'), encoding='utf-8').read()
    assert 'COPY shared/py/' in dockerfile


# ── The Settings page's tab files ────────────────────────────────────────────
# `settings.html` was 2594 lines: fifteen tab panes of markup, then one inline
# <script>. The panes now live one-per-file in `templates/settings/`,
# named for the tab they draw, with the three grouped tabs including their own
# children so the files mirror what the sidebar shows.

SETTINGS_DIR = os.path.join(REPO, 'server', 'templates', 'settings')


def _settings_sources():
    import glob
    base = os.path.join(REPO, 'server', 'templates')
    return [os.path.join(base, 'settings.html')] + \
           sorted(glob.glob(os.path.join(SETTINGS_DIR, '**', '*.html'), recursive=True))


def test_every_tab_pane_lives_in_its_own_partial():
    """A pane added back into settings.html would work, and would start the file
    growing again — the reason this split happened."""
    parent = open(os.path.join(REPO, 'server', 'templates', 'settings.html'),
                  encoding='utf-8').read()
    panes = re.findall(r'<div class="tab-pane[^"]*" id="tab-([a-z-]+)"', parent)
    assert not panes, f'these panes are still inline in settings.html: {panes}'


def test_each_partial_is_named_for_the_tab_it_draws():
    """`fetched/` is excluded: those are route responses, not tab panes."""
    import glob
    for path in sorted(glob.glob(os.path.join(SETTINGS_DIR, '*.html'))):
        name = os.path.splitext(os.path.basename(path))[0]
        body = open(path, encoding='utf-8').read()
        # A group wrapper holds only includes; a leaf opens its own pane.
        if '{% include' in body and 'tab-pane' in body:
            assert f'id="tab-{name}"' in body, f'{name}.html does not open #tab-{name}'
        elif 'tab-pane' in body:
            assert body.lstrip().startswith(f'<div class="tab-pane'), name
            assert f'id="tab-{name}"' in body, f'{name}.html does not open #tab-{name}'


def test_the_include_tags_start_at_column_zero():
    """Whitespace before a Jinja tag is literal output, emitted on top of the
    partial's own indentation — an indented include silently double-indents the
    pane's opening line. The render is byte-identical only because they are flush
    left, so this is worth a test rather than a comment alone."""
    for path in _settings_sources():
        for n, line in enumerate(open(path, encoding='utf-8'), 1):
            if '{% include' in line:
                assert line.startswith('{% include'), \
                    f'{os.path.basename(path)}:{n} indents an include tag'


def test_no_jinja_block_straddles_a_partial():
    """Each file has to stand on its own: an `{% if %}` opened in one and closed
    in another renders today and breaks the moment either is edited."""
    opener = re.compile(r'\{%-?\s*(if|for|with|macro)\b')
    closer = re.compile(r'\{%-?\s*end(if|for|with|macro)\b')
    for path in _settings_sources():
        body = open(path, encoding='utf-8').read()
        assert len(opener.findall(body)) == len(closer.findall(body)), \
            f'{os.path.basename(path)} has an unbalanced Jinja block'


def test_the_fetched_fragments_are_not_included_anywhere():
    """`settings/fetched/` holds the two HTMX targets the Settings page
    pulls in after load — `clients.html` (Network *and* Update tabs) and
    `wifi_networks.html`. They are rendered as standalone responses with their own
    context, so an `{% include %}` of one would render blanks rather than fail.
    The subdirectory is what keeps that distinction visible; this keeps it true.
    """
    import glob
    fetched = {os.path.basename(p) for p in
               glob.glob(os.path.join(SETTINGS_DIR, 'fetched', '*.html'))}
    assert fetched == {'clients.html', 'wifi_networks.html'}
    for path in _settings_sources():
        body = open(path, encoding='utf-8').read()
        for name in fetched:
            assert f'include \'settings/fetched/{name}' not in body, \
                f'{os.path.basename(path)} includes a fetched fragment'


def test_every_fetched_fragment_has_a_route_that_renders_it():
    """The other half: a fragment nothing serves is dead markup."""
    import glob
    routes = ''.join(open(os.path.join(REPO, 'server', 'routes', f), encoding='utf-8').read()
                     for f in os.listdir(os.path.join(REPO, 'server', 'routes'))
                     if f.endswith('.py'))
    for path in glob.glob(os.path.join(SETTINGS_DIR, 'fetched', '*.html')):
        name = os.path.basename(path)
        assert f'settings/fetched/{name}' in routes, f'nothing renders {name}'


# ── The Settings page's script ───────────────────────────────────────────────
# The last 1389 lines of settings.html were one inline <script>. Exactly one thing
# in it was server-rendered — the `T` strings table — so that stayed in the page as
# a data island and the rest became `shared/static/js/settings.js`.

SETTINGS_JS = os.path.join(REPO, 'shared', 'static', 'js', 'settings.js')


def test_the_page_carries_no_behaviour_inline():
    """A function creeping back into the template is how the file grew the first
    time, and it would not be caught by anything else."""
    parent = open(os.path.join(REPO, 'server', 'templates', 'settings.html'),
                  encoding='utf-8').read()
    inline = re.findall(r'<script>(.*?)</script>', parent, re.S)
    for block in inline:
        # The pre-paint theme applier is the one exception: it sets data-bs-theme
        # from localStorage before first paint, and an external file — deferred by
        # definition — would show a flash of the wrong theme on every load.
        if 'before first paint' in block:
            continue
        assert 'function ' not in block, 'a function is back inline in settings.html'
    # The island that is allowed, and the reason it has to be.
    assert any('var T = {{ t | tojson }}' in b for b in inline)


def test_the_script_is_static_with_no_template_syntax():
    """If a `{{ … }}` ever lands in here it will ship to the browser verbatim —
    the file is served by StaticFiles, which does not render templates."""
    js = open(SETTINGS_JS, encoding='utf-8').read()
    for marker in ('{{', '{%'):
        assert marker not in js, f'settings.js contains Jinja syntax ({marker})'


def test_the_script_tag_is_cache_busted():
    """This Pi updates itself. Without a changing query string a browser can hold a
    cached settings.js against markup deployed since, and the mismatch looks like a
    bug in the page rather than a stale file."""
    parent = open(os.path.join(REPO, 'server', 'templates', 'settings.html'),
                  encoding='utf-8').read()
    assert re.search(r'src="/static/js/settings\.js\?v=\{\{\s*server_version\s*\}\}"', parent), \
        'settings.js is loaded without a version key'

    import web
    assert 'server_version' in web._globals(), \
        '_globals() no longer supplies server_version, so the key renders empty'


def test_the_island_is_set_before_the_script_loads():
    """`T` is a global the script reads at parse time; the order is load-bearing."""
    parent = open(os.path.join(REPO, 'server', 'templates', 'settings.html'),
                  encoding='utf-8').read()
    assert parent.index('var T = {{ t | tojson }}') < parent.index('/static/js/settings.js')


# ── The cloud admin's tab files ──────────────────────────────────────────────
# `admin.html` was 859 lines. Its six tab panes now live in `cloud/templates/admin/`,
# the relay's half of the arrangement `server/templates/settings/` uses on the Pi.
#
# Its script is still inline, unlike the Pi's: it carries `{% if creds_error %}` and
# `{% if has_deploy %}` blocks that gate whole sections of JS on server state, and
# those have to become runtime conditions before the file can move.

ADMIN_DIR = os.path.join(REPO, 'cloud', 'templates', 'admin')


def test_every_admin_tab_pane_lives_in_its_own_file():
    parent = open(os.path.join(REPO, 'cloud', 'templates', 'admin.html'),
                  encoding='utf-8').read()
    panes = re.findall(r'<div class="tab-pane[^"]*" id="tab-([a-z-]+)"', parent)
    assert not panes, f'these panes are still inline in admin.html: {panes}'


def test_each_admin_file_is_named_for_the_tab_it_draws():
    import glob
    for path in sorted(glob.glob(os.path.join(ADMIN_DIR, '*.html'))):
        name = os.path.splitext(os.path.basename(path))[0]
        body = open(path, encoding='utf-8').read()
        assert f'id="tab-{name}"' in body, f'admin/{name}.html does not open #tab-{name}'


def test_the_admin_include_tags_start_at_column_zero():
    """Same trap as the Pi's: whitespace before a tag is literal output, emitted on
    top of the file's own indentation, which double-indents its opening line."""
    import glob
    for path in [os.path.join(REPO, 'cloud', 'templates', 'admin.html')] + \
                sorted(glob.glob(os.path.join(ADMIN_DIR, '*.html'))):
        for n, line in enumerate(open(path, encoding='utf-8'), 1):
            if '{% include' in line:
                assert line.startswith('{% include'), \
                    f'{os.path.basename(path)}:{n} indents an include tag'


def test_no_jinja_block_straddles_an_admin_file():
    import glob
    opener = re.compile(r'\{%-?\s*(if|for|with|macro)\b')
    closer = re.compile(r'\{%-?\s*end(if|for|with|macro)\b')
    for path in [os.path.join(REPO, 'cloud', 'templates', 'admin.html')] + \
                sorted(glob.glob(os.path.join(ADMIN_DIR, '*.html'))):
        body = open(path, encoding='utf-8').read()
        assert len(opener.findall(body)) == len(closer.findall(body)), \
            f'{os.path.basename(path)} has an unbalanced Jinja block'


def test_the_image_ships_the_admin_tab_files():
    """`COPY cloud/templates/` takes the tree, so the subdirectory rides along — but
    a narrowing of that line would break /admin in the container only."""
    dockerfile = open(os.path.join(CLOUD, 'Dockerfile'), encoding='utf-8').read()
    assert 'COPY cloud/templates/ templates/' in dockerfile
