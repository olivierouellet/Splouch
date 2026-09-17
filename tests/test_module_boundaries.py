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
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(REPO, 'server')
sys.path.insert(0, SERVER)


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
