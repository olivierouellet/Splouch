"""Turning WiFi off takes a press-and-hold, in every language.

Disabling WiFi on the Pi can drop the operator's own connection to the page they
are pressing it on, so it carries the same ~1.2s hold as Reboot and Shutdown. It
used to carry a hand-rolled one that decided which half of the toggle it was
looking at by testing the button's text:

    isDisable = /Disable/.test(btn.textContent)

`Disable` is a word that only appears in English. Under `Désactiver le WiFi` or
`Desactivar WiFi` the hold never armed, `release(true)` took the plain-tap branch,
and a single press killed the connection — precisely the press the hold existed to
prevent.

It now uses the shared `[data-hold]` mechanism in `shared/static/js/panel.js`, the
one Reboot and Shutdown use, armed from the *state* rather than the label. The
attribute comes and goes with the toggle: enabling WiFi is harmless and stays a tap.

The JavaScript runs for real under JavaScriptCore; skips without it.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from conftest import matched, settings_source  # noqa: E402
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import state                     # noqa: E402
from jsc import HAS_JS_ENGINE, js_argv  # noqa: E402

SETTINGS = os.path.join(REPO, 'server', 'templates', 'settings.html')
PANEL_JS = os.path.join(REPO, 'shared', 'static', 'js', 'panel.js')
# The [data-hold] mechanism moved out of panel.js into its own file so /manual could
# use it without the rest of the operator-panel shell. Same code, same behaviour —
# this test follows it rather than re-testing the half that stayed behind.
HOLD_JS = os.path.join(REPO, 'shared', 'static', 'js', 'hold.js')

pytestmark = pytest.mark.skipif(not HAS_JS_ENGINE, reason='needs a JavaScript engine (osascript or node)')


@pytest.fixture(scope='module')
def src():
    return settings_source()


def _run(program):
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False,
                                     encoding='utf-8') as handle:
        handle.write(program)
        path = handle.name
    try:
        done = subprocess.run(js_argv(path),
                              capture_output=True, text=True)
    finally:
        os.unlink(path)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


def _apply_status(src, lang, enabled):
    """Run `_applyWifiStatus` against a stub button and report its attributes."""
    labels = state.settings_strings(lang)
    body = src[src.index('function _applyWifiStatus'):src.index('function toggleWifi')]
    program = f'''
var T = {json.dumps({k: labels.get(k, '') for k in
                     ('js_disable_wifi', 'js_enable_wifi', 'js_hold_disable',
                      'js_disabled', 'js_not_connected')})};
function _statusColor() {{}}
function _el() {{
    return {{ attrs: {{}}, textContent: '', disabled: true,
              setAttribute: function (k, v) {{ this.attrs[k] = v; }},
              removeAttribute: function (k) {{ delete this.attrs[k]; }},
              hasAttribute: function (k) {{ return k in this.attrs; }} }};
}}
var btn = _el(), rest = _el();
var document = {{ getElementById: function (id) {{
    return id === 'btn-wifi-toggle' ? btn : rest;
}} }};
{body}
_applyWifiStatus({{enabled: {json.dumps(enabled)}, ssid: 'pool', wifi_ip: '10.0.0.5'}});
JSON.stringify({{label: btn.textContent, attrs: btn.attrs}})
'''
    return json.loads(_run(program))


# ── The behaviour, in every language we ship ───────────────────────────────────

@pytest.mark.parametrize('lang', ['en', 'fr', 'es'])
def test_disabling_wifi_arms_the_hold(src, lang):
    """The bug: this worked in English and in no other language."""
    out = _apply_status(src, lang, enabled=True)
    assert 'data-hold' in out['attrs'], (
        f'{lang}: {out["label"]!r} does not arm the hold — a tap would disable WiFi')
    assert out['attrs']['data-hold-label'] == state.settings_strings(lang)['js_hold_disable']


@pytest.mark.parametrize('lang', ['en', 'fr', 'es'])
def test_enabling_wifi_stays_a_plain_tap(src, lang):
    """Turning it on cannot strand anybody, and a hold would only be in the way."""
    out = _apply_status(src, lang, enabled=False)
    assert 'data-hold' not in out['attrs'], out
    assert out['label'] == state.settings_strings(lang)['js_enable_wifi']


def test_the_state_decides_not_the_label(src):
    """What went wrong before. The two disagree by design in every language but one,
    so reading the label is reading the wrong thing."""
    block = src[src.index('function _applyWifiStatus'):src.index('function toggleWifi')]
    assert 'd.enabled' in block
    assert not re.search(r'/Disable/|textContent\s*\)?\.\s*(match|indexOf)', block), block


# ── The same mechanism Reboot and Shutdown use ─────────────────────────────────

def test_it_is_wired_to_the_shared_hold(src):
    button = matched(r'<button id="btn-wifi-toggle"[^>]*>', src, group=0)
    assert 'data-hold-fn="toggleWifi"' in button, button
    assert 'onclick' not in button, (
        'a plain click handler fires even while the hold is armed — panel.js '
        'preventDefaults the click, which does not stop an inline onclick')


def test_there_is_no_second_hold_implementation(src):
    """The bespoke one is what carried the bug; two of them is how it comes back."""
    assert 'setupWifiToggleHold' not in src
    assert src.count('HOLD_MS') == 0, 'a local hold duration is still defined here'


@pytest.mark.parametrize('label', ['power_reboot', 'power_shutdown'])
def test_reboot_and_shutdown_still_hold_the_same_way(src, label):
    """The comparison the fix is measured against."""
    button = re.search(r'<button[^>]*>[^<]*(?:<i[^>]*></i>)?\s*\{\{ t\.%s \}\}' % label,
                       src).group(0)
    assert 'data-hold' in button and 'data-hold-fn' in button, button


# ── panel.js honours what the button now asks for ──────────────────────────────

def test_a_held_button_runs_its_function_and_a_tapped_one_does_not():
    """Driven through `hold.js` itself: the attribute is read at press time, which
    is what lets it be added and removed as the toggle flips."""
    panel = open(HOLD_JS, encoding='utf-8').read()
    program = f'''
var __ran = [], __timers = [];
function setTimeout(fn, ms) {{ __timers.push([fn, ms]); return __timers.length; }}
function clearTimeout(i) {{ if (i) __timers[i - 1] = null; }}
function toggleWifi() {{ __ran.push('toggleWifi'); }}
var __listeners = {{}};
function _btn(attrs) {{
    return {{ _a: attrs, disabled: false, textContent: 'x',
              classList: {{ add: function () {{}}, remove: function () {{}},
                            contains: function () {{ return false; }} }},
              style: {{ _p: {{}},
                        setProperty: function (k, v) {{ this._p[k] = v; }},
                        removeProperty: function (k) {{ delete this._p[k]; }} }},
              getAttribute: function (k) {{ return k in this._a ? this._a[k] : null; }},
              hasAttribute: function (k) {{ return k in this._a; }},
              closest: function (sel) {{
                  return sel === '[data-hold]' && 'data-hold' in this._a ? this : null;
              }} }};
}}
var document = {{
    addEventListener: function (type, fn) {{ (__listeners[type] = __listeners[type] || []).push(fn); }},
    querySelectorAll: function () {{ return []; }},
    querySelector: function () {{ return null; }},
    getElementById: function () {{ return null; }},
    documentElement: {{ classList: {{ add: function () {{}}, remove: function () {{}},
                                      toggle: function () {{}}, contains: function () {{ return false; }} }},
                        setAttribute: function () {{}}, getAttribute: function () {{ return null; }} }},
    body: {{ classList: {{ add: function () {{}}, remove: function () {{}} }} }},
    cookie: ''
}};
var window = this;
var localStorage = {{ getItem: function () {{ return null; }}, setItem: function () {{}} }};
var matchMedia = function () {{ return {{ matches: false, addEventListener: function () {{}} }}; }};
try {{ {panel} }} catch (e) {{}}

function press(btn) {{
    (__listeners['pointerdown'] || []).forEach(function (fn) {{
        fn({{ target: btn, preventDefault: function () {{}} }});
    }});
}}
function finish() {{ __timers.forEach(function (t) {{ if (t) t[0](); }}); __timers = []; }}

press(_btn({{'data-hold': '', 'data-hold-fn': 'toggleWifi'}}));
finish();
var held = __ran.length;
__ran = [];
press(_btn({{'data-hold-fn': 'toggleWifi'}}));      // no data-hold: not a hold target
finish();
JSON.stringify({{held: held, tapped: __ran.length}})
'''
    out = json.loads(_run(program))
    assert out['held'] == 1, 'holding a [data-hold] button did not run its function'
    assert out['tapped'] == 0, 'panel.js acted on a button that is not asking for a hold'
