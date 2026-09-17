"""Buttons have to survive translation.

The admin panel is English-first and the labels on it are not: `Refresh` becomes
`Actualiser`, `Start` becomes `Démarrer`, `Playing` becomes `Reproduciendo` — half
as long again, and in one case nearly three times. Several buttons carried a hard
`style="width:70px"`, which is a ceiling as well as a floor, so the longer word
wrapped onto a second line *inside* the button and the row grew to fit it.

`min-width` plus `text-nowrap` keeps what the fixed width was there for — a column
of buttons that line up — and lets a long word out of the box.

Markup only: no server, no browser. The session list is built in JavaScript, so that
half runs for real under JavaScriptCore.
"""
import os
import re
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from conftest import settings_source  # noqa: E402
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import state                          # noqa: E402
from jsc import HAS_JSC               # noqa: E402

SETTINGS = os.path.join(REPO, 'server', 'templates', 'settings.html')


@pytest.fixture(scope='module')
def src():
    return settings_source()


def _buttons(src):
    """Every `<button …>label</button>`, tag and label separately."""
    return re.findall(r'(<button\b[^>]*>)(.*?)</button>', src, re.S)


# ── The rule ───────────────────────────────────────────────────────────────────

def test_no_button_with_a_translated_label_has_a_fixed_width(src):
    """A fixed pixel width is a ceiling. The label inside it is not fixed at all.

    Percentages are fine and deliberately not caught: `width:100%` on a menu item
    is the container's width, which grows with the page rather than pinning a word.
    """
    offenders = [tag for tag, label in _buttons(src)
                 if '{{ t.' in label + tag
                 and re.search(r'style="[^"]*(?<!-)\bwidth:\s*\d+px', tag)]
    assert not offenders, offenders


def test_every_button_that_sets_a_minimum_also_refuses_to_wrap(src):
    """`min-width` lets the button grow; `text-nowrap` is what stops the word
    breaking first when a flex parent squeezes it."""
    offenders = [tag for tag, _ in _buttons(src)
                 if 'min-width' in tag and 'text-nowrap' not in tag]
    assert not offenders, offenders


def test_the_inputs_that_do_want_a_fixed_width_still_have_one(src):
    """Not a blanket ban: an IP field holds no translated text and should not
    resize with the page."""
    assert 'id="eth-ip-input"' in src
    ip = re.search(r'<input[^>]*id="eth-ip-input"[^>]*>', src).group(0)
    assert re.search(r'width:\s*\d+px', ip), ip


@pytest.mark.parametrize('key', ['btn_refresh', 'test_start', 'test_stop',
                                 'btn_enable', 'btn_clear', 'net_scan',
                                 'net_static', 'clock_set_time'])
def test_the_reported_buttons_are_covered(src, key):
    """Named explicitly so a future edit that re-pins one of these is caught by a
    test that says which button it was."""
    tags = [tag for tag, label in _buttons(src) if '{{ t.%s }}' % key in label]
    assert tags, f'no button renders t.{key} any more'
    for tag in tags:
        if 'min-width' in tag or 'width' in tag:
            assert 'min-width' in tag, f'{key}: {tag}'
            assert 'text-nowrap' in tag, f'{key}: {tag}'


# ── The translations these have to hold ────────────────────────────────────────

@pytest.mark.parametrize('key, longest', [
    ('btn_refresh', 'Actualiser'),
    ('test_start',  'Démarrer'),
    ('js_playing',  'Reproduciendo'),
    ('btn_delete',  'Supprimer'),
])
def test_the_long_translations_are_still_there(key, longest):
    """If these ever shorten, the rule above stops being load-bearing and somebody
    should know that rather than discover it by pinning a width again."""
    shipped = {code: state.settings_strings(code).get(key, '')
               for code in ('en', 'fr', 'es')}
    assert longest in shipped.values(), shipped
    assert max(len(v) for v in shipped.values()) > len(shipped['en']), shipped


# ── The half that is built in JavaScript ───────────────────────────────────────

@pytest.mark.skipif(not HAS_JSC, reason='needs JavaScriptCore (macOS)')
def test_the_session_list_rows_are_built_unwrappable(src):
    """`_renderSessions` writes its own buttons, so the markup rules above cannot
    see them. Run it and read what it produced."""
    import json
    import subprocess
    import tempfile

    body = src[src.index('function _renderSessions'):src.index('function setSpeed')]
    labels = {'js_play': 'Reproducir', 'js_playing': 'Reproduciendo',
              'btn_delete': 'Supprimer', 'js_builtin': 'intégré',
              'js_playing_c': 'Lecture : '}
    program = f'''
var T = {json.dumps(labels)};
var __html = '';
var document = {{
    getElementById: function (id) {{
        return {{ textContent: '',
                  set innerHTML(v) {{ __html = v; }},
                  get innerHTML() {{ return __html; }} }};
    }}
}};
{body}
_renderSessions([{{name: 'a.cts', source: 'builtin'}},
                 {{name: 'b.cts', source: 'custom'}}], false);
__html
'''
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False,
                                     encoding='utf-8') as handle:
        handle.write(program)
        path = handle.name
    try:
        done = subprocess.run(['osascript', '-l', 'JavaScript', path],
                              capture_output=True, text=True)
    finally:
        os.unlink(path)

    assert done.returncode == 0, done.stderr
    html = done.stdout
    assert 'Reproducir' in html and 'Supprimer' in html, html

    built = re.findall(r'<button\b[^>]*>', html)
    assert len(built) == 4, f'{len(built)} buttons for two rows: {built}'
    for tag in built:
        assert 'min-width' in tag, tag
        assert 'text-nowrap' in tag, tag
        assert not re.search(r'style="[^"]*(?<!-)\bwidth:\s*\d+px', tag), tag


@pytest.mark.skipif(not HAS_JSC, reason='needs JavaScriptCore (macOS)')
def test_a_builtin_row_lines_up_with_a_custom_one(src):
    """The built-in rows have nothing to delete. The placeholder has to be the
    delete button made invisible, not an empty box of a guessed width — those only
    matched while the button was one too, and stopped matching in French."""
    body = src[src.index('function _renderSessions'):src.index('function setSpeed')]
    assert 'visibility:hidden' in body, 'the placeholder is not a hidden button'
    placeholder = re.search(r"visibility:hidden[^']*'\s*\+\s*(.*?)\s*\+", body)
    assert 'btn_delete' in body[body.index('visibility:hidden'):
                                body.index('visibility:hidden') + 200], body
