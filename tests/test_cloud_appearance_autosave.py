"""The cloud panel's Appearance tab applies as you change it, with no Save button.

The same move the Pi's Appearance panel made (`test_settings_autosave.py`), for the
same reason: the button was both the trigger and the receipt, so a change that had not
been pressed through looked saved and was not. The file fields made it worse — picking
an image visibly did nothing until the operator found the button further down.

Two things here are not in the Pi's version, and both are load-bearing:

* **File inputs are bound separately.** A pick is validated in the page first (format
  and size, the same two the server enforces). A rejected one must not reach the
  server, and must not have its own message wiped by a save firing on the same
  `change` event. So the debounced listener skips `input[type=file]`, and a *valid*
  pick calls `saveNow()` directly — there is no keystroke burst to wait out.
* **`change`, never `input`.** On a text field that lands on blur or Enter, so a title
  is sent once it is meant rather than once per keystroke. This is the part an
  operator asks about, and the part a refactor to `input` would quietly ruin.

Only failures speak. The field holds what was typed and the public page shows it; a
"Saved." on every blur is noise.
"""
import os
import re
import subprocess
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from conftest import admin_source, stub_url_for  # noqa: E402
from jsc import HAS_JS_ENGINE, js_argv  # noqa: E402

needs_js = pytest.mark.skipif(
    not HAS_JS_ENGINE, reason='needs a JavaScript engine (osascript or node)')


@pytest.fixture(scope='module')
def src():
    return admin_source()


@pytest.fixture(scope='module')
def form(src):
    """The picker-appearance form's markup, opening tag to close."""
    start = src.index('<form id="picker-form"')
    return src[start:src.index('</form>', start)]


# ── The button is gone, and the form can still be posted ───────────────────────

def test_the_appearance_form_has_no_submit_button(form):
    assert 'type="submit"' not in form, 'the Save button is back'
    assert '{{ t.save }}' not in form


def test_the_account_form_keeps_its_button(src):
    """Not an oversight: it carries a password, which should not save on the way past.

    The Pi draws the same line — `markDirty` forms there, this one here.
    """
    account = src[src.index('name="current_password"'):]
    account = account[:account.index('</form>')]
    assert 'type="submit"' in account


def test_the_form_still_says_where_it_posts(form):
    """`autoSave` reads `form.action`; without it the save goes to the page URL."""
    assert 'action="/admin/picker_appearance"' in form


def test_a_stray_enter_cannot_navigate(form):
    """Two text fields means implicit submission is already inert, but not by design."""
    assert 'onsubmit="return false;"' in form


def test_there_is_somewhere_to_put_a_failure(form):
    assert 'id="picker-appearance-note"' in form
    note = form[form.index('id="picker-appearance-note"'):]
    assert 'aria-live="polite"' in note[:note.index('>')], \
        'a failure that only appears visually is missed by a screen reader'


# ── The wiring ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope='module')
def rendered():
    """The real template, rendered — the raw source still carries Jinja expressions."""
    import tomllib

    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(
        [os.path.join(REPO, 'cloud', 'templates'),
         os.path.join(REPO, 'shared', 'templates')]))
    stub_url_for(env)
    with open(os.path.join(REPO, 'shared', 'locales', 'panel', 'en.toml'), 'rb') as f:
        t = tomllib.load(f)
    return env.get_template('admin.html').render(
        t={**t['chrome'], **t['cloud']}, has_deploy=True, creds_error=None, keys=[],
        active_meets=[], user_name='Admin', locales=[], current_locale='',
        ui_lang_cookie='', analytics_enabled=False, picker_window_title_form='',
        picker_title_form='', picker_logo_above=False, has_picker_logo=False,
        has_picker_icon=False, picker_max_upload=2 * 1024 * 1024)


@pytest.fixture(scope='module')
def script(rendered):
    """The picker-appearance block, from its first statement to the next section.

    Starts at `pickerNote` now rather than at `const PICKER_MAX_UPLOAD`: the
    server-rendered values moved up into the page's data island, so the block
    itself begins with code.
    """
    return rendered[rendered.index('function pickerNote('):
                    rendered.index('function clearPickerLogo()')]


def test_text_fields_commit_on_change_not_on_every_keystroke(script):
    assert "addEventListener('change', schedule)" in script
    assert "addEventListener('input'" not in script, \
        'input fires per keystroke: a title would be POSTed nine times on the way in'


def test_a_burst_of_edits_is_debounced(script):
    assert re.search(r'setTimeout\(save,\s*\d+\)', script), 'no debounce'


def test_file_inputs_are_not_on_the_debounced_listener(script):
    assert 'input:not([type="file"])' in script, \
        'a rejected pick would POST anyway and wipe its own message'


def test_a_valid_pick_uploads_without_waiting_for_the_debounce(script):
    body = script[script.index('function previewImage('):]
    body = body[:body.index('\n        function previewPickerLogo')]
    assert 'pickerAppearance.saveNow()' in body
    # The early return for a rejected file must come first, or it saves anyway.
    assert body.index('pickerNote(bad)') < body.index('pickerAppearance.saveNow()')


def test_success_is_silent_and_failure_is_not(script):
    assert 'T.saveFailed' in script, 'the failure notice no longer reads a string'
    for gone in ("'Saving…'", "'Saved.'", "textContent = 'Saved"):
        assert gone not in script, f'{gone} is back: a receipt on every blur is noise'


@needs_js
def test_the_whole_thing_behaves_when_driven(script):
    """Run the real block against a stub DOM and check what it actually does."""
    harness = r'''
    // The page hands its server-rendered strings to the script as `T` (the data
    // island at the top of admin.html); this block reads them from it.
    var T = { saveFailed: 'Could not save.',
              imageFormatRejected: 'Unsupported image format.',
              imageTooLarge: 'Image is too large.' };
    var PICKER_MAX_UPLOAD = 2 * 1024 * 1024;
    var els = {}, posted = [], timers = [], nextTimer = 1, FAIL = false;
    function el(id) { if (!els[id]) els[id] = { id: id, hidden: true, src: '',
                                                textContent: '', className: '' };
                      return els[id]; }
    function mkField(type, name, accept) {
      return { type: type, name: name, accept: accept, files: [], value: '', handlers: {},
               getAttribute: function (a) { return a === 'accept' ? this.accept : null; },
               addEventListener: function (e, fn) { (this.handlers[e] = this.handlers[e] || []).push(fn); },
               fire: function (e) { (this.handlers[e] || []).forEach(function (h) { h(); }); } };
    }
    var wtitle = mkField('text', 'picker_window_title');
    var title  = mkField('text', 'picker_title');
    var above  = mkField('checkbox', 'picker_logo_above');
    var logo   = mkField('file', 'picker_logo', 'image/png,image/jpeg');
    var icon   = mkField('file', 'picker_icon', 'image/png');
    var ALL = [wtitle, title, above, logo, icon];
    var form = { action: '/admin/picker_appearance',
      querySelectorAll: function (s) {
        if (s.indexOf('not([type="file"])') === -1) throw new Error('selector changed: ' + s);
        return ALL.filter(function (f) { return f.type !== 'file'; }); },
      querySelector: function (s) { var m = /name="([^"]+)"/.exec(s);
        return ALL.filter(function (f) { return f.name === m[1]; })[0] || null; } };
    els['picker-form'] = form;
    var document = { getElementById: el };
    function setTimeout(fn, ms) { timers.push({ id: nextTimer, fn: fn }); return nextTimer++; }
    function clearTimeout(id) { timers = timers.filter(function (t) { return t.id !== id; }); }
    function runTimers() { var t = timers; timers = []; t.forEach(function (x) { x.fn(); }); }
    function FormData(f) { this.f = f; }
    function chain(v) {
      if (v && v.__chain) return v;
      return { __chain: true, then: function (g) { return chain(g(v)); },
               catch: function () { return chain(v); },
               finally: function (g) { g(); return chain(v); } };
    }
    function fetch(url, opts) {
      posted.push(url);
      return chain({ json: function () { return chain(FAIL ? { ok: false, error: 'nope' }
                                                           : { ok: true }); } });
    }
    function FileReader() { this.readAsDataURL = function () {}; }
    function confirm() { return true; }
    ''' + script + r'''
    var out = {};
    out.bound = ALL.filter(function (f) { return f.handlers['change']; })
                   .map(function (f) { return f.name; });
    posted = []; timers = [];
    title.fire('change');
    out.postedBeforeDebounce = posted.length;
    runTimers();
    out.postedAfterDebounce = posted.length;
    posted = []; timers = [];
    wtitle.fire('change'); title.fire('change'); above.fire('change');
    runTimers();
    out.threeEditsPost = posted.length;
    posted = []; timers = [];
    logo.files = [{ type: 'image/png', size: 10 }];
    previewPickerLogo(logo);
    out.validPickPosts = posted.length;
    out.validPickTimers = timers.length;
    out.validPickNote = el('picker-appearance-note').textContent;
    posted = []; timers = [];
    icon.files = [{ type: 'application/pdf', size: 10 }];
    previewPickerIcon(icon);
    out.badPickPosts = posted.length;
    out.badPickNote = el('picker-appearance-note').textContent;
    FAIL = true; posted = [];
    title.fire('change'); runTimers();
    out.failNote = el('picker-appearance-note').textContent;
    JSON.stringify(out);
    '''
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(harness)
        path = fh.name
    try:
        res = subprocess.run(js_argv(path),
                             capture_output=True, text=True)
    finally:
        os.remove(path)
    assert res.returncode == 0, res.stderr
    import json
    out = json.loads(res.stdout)

    assert out['bound'] == ['picker_window_title', 'picker_title', 'picker_logo_above'], \
        'the file inputs must not be on the debounced listener'
    assert out['postedBeforeDebounce'] == 0, 'a title went out before the field settled'
    assert out['postedAfterDebounce'] == 1
    assert out['threeEditsPost'] == 1, 'tabbing through the form must be one POST'
    assert out['validPickPosts'] == 1, 'a picked image must upload without a button'
    assert out['validPickTimers'] == 0, 'an upload should not wait out the debounce'
    assert out['validPickNote'] == '', 'a successful save must say nothing'
    assert out['badPickPosts'] == 0, 'a refused file must not reach the server'
    assert out['badPickNote'], 'a refused file must say why'
    assert out['failNote'] == 'nope', "the server's reason must reach the operator"
