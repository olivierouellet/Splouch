"""The Pi's Appearance panel applies as you change it, with no Update button.

Flow, Display Options and Theme each used to carry an Update button that was both the
trigger and the receipt — amber while dirty, green for 2.5s once the page came back.
They now save themselves and report it in an inline note.

The forms are POSTed with `fetch`, not submitted. Submitting navigates, and a settings
page that reloads on every checkbox is worse than the button was; on Theme it would be
unusable, since the inputs are colour pickers and the page would reload out from under
one. Each save also emits `reload` to the TV displays and every connected phone
(`server/routes/settings.py`), which is what the debounce is protecting.

Three forms deliberately keep their buttons: the cloud connection, the admin account
and the per-meet cloud appearance all carry something a stray change should not send.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from conftest import settings_markup  # noqa: E402
SETTINGS = os.path.join(REPO, 'server', 'templates', 'settings.html')

needs_js = pytest.mark.skipif(not shutil.which('osascript'),
                              reason='needs JavaScriptCore (macOS)')


@pytest.fixture(scope='module')
def src():
    return settings_markup()


@pytest.mark.parametrize('form_id,note_id', [
    ('timing_tuning_form',    'timing-tuning-note'),
    ('display-settings-form', 'display-save-note'),
    ('theme_update_form',     'theme-save-note'),
])
def test_each_appearance_form_saves_itself(src, form_id, note_id):
    assert 'id="%s"' % form_id in src
    assert 'id="%s"' % note_id in src
    assert "autoSave(document.getElementById('%s'), '%s'" % (form_id, note_id) in src


@pytest.mark.parametrize('btn', ['btn-update-display', 'btn-update-theme'])
def test_the_appearance_update_buttons_are_gone(src, btn):
    assert 'id="%s"' % btn not in src


@pytest.mark.parametrize('btn', ['btn-update-cloud', 'btn-update-meet', 'btn-update-account'])
def test_the_deliberate_buttons_stay(src, btn):
    """Not an oversight: a relay key, a password and a per-meet record each want an
    explicit press rather than saving on the way past."""
    assert 'id="%s"' % btn in src
    assert "markDirty(" in src


def test_the_note_is_announced_to_assistive_tech(src):
    """The receipt replaced a button going green, which a screen reader never saw
    either — but a live region is the reason to do it properly now."""
    for note in ('timing-tuning-note', 'display-save-note', 'theme-save-note'):
        span = re.search(r'<span id="%s"[^>]*>' % note, src).group(0)
        assert 'role="status"' in span and 'aria-live="polite"' in span


def test_reverting_a_swatch_reaches_the_server(src):
    """The ↺ on a colour swatch sets `input.value` from script, which fires no event.

    It dispatched `input` alone, for the swatch's own changed-hint. `autoSave` listens
    for `change`, so with only `input` the colour would revert on screen and never
    save — worse than the old button, which `markDirty` flipped on either event.
    """
    # Anchor on the handler, not the class name — there is a `.cs-reset` CSS rule too.
    block = src[src.index("querySelectorAll('#tab-theme .cs-reset')"):]
    block = block[:block.index('})();')]
    assert "new Event('input'" in block
    assert "new Event('change'" in block


@needs_js
def test_autosave_debounces_and_reports(src):
    """Run the page's own `autoSave` and check what it does with one change."""
    fn = re.search(r'^    function autoSave\(form, noteId, opts\) \{.*?^    \}',
                   src, re.S | re.M)
    assert fn, 'autoSave is no longer a top-level function'

    harness = '''
    var T = { js_request_failed_c: 'Failed: ' };
    var posts = [], __timers = [];
    var note = { className: '', textContent: '' };
    var document = { getElementById: function () { return note; } };
    function setTimeout(f, ms) { __timers.push([f, ms]); return __timers.length; }
    function clearTimeout(i) { if (i) __timers[i - 1] = null; }
    var location = { reload: function () { posts.push('RELOAD'); } };
    function FormData(f) { this.form = f; }
    function fetch(url) {
      posts.push(url);
      return { then: function (f) { var v = f({ ok: true, status: 200 });
        return { then: function (g) { g(v); return this; },
                 catch: function () { return this; },
                 finally: function (h) { h(); return this; } }; } };
    }
    var handlers = [];
    var form = { action: '/settings', querySelectorAll: function () {
      var els = [{ addEventListener: function (ev, fn) { handlers.push([ev, fn]); } }];
      els.forEach = Array.prototype.forEach.bind(els);
      return els; } };
    ''' + fn.group(0) + '''
    autoSave(form, 'note');
    handlers.forEach(function (h) { h[1](); });      // three changes in a row
    handlers.forEach(function (h) { h[1](); });
    var live = __timers.filter(Boolean);
    live[live.length - 1][0]();                      // fire the surviving timer
    JSON.stringify({ events: handlers.map(function (h) { return h[0]; }),
                     scheduled: __timers.length, alive: live.length,
                     debounce: live[live.length - 1][1],
                     posts: posts, note: note.textContent });
    '''
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(harness)
        path = fh.name
    try:
        res = subprocess.run(['osascript', '-l', 'JavaScript', path],
                             capture_output=True, text=True)
    finally:
        os.unlink(path)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)

    # `input` fires per keystroke and per pixel of a colour drag; `change` lands on
    # commit, which is the only moment a value is meant.
    assert set(out['events']) == {'change'}
    # Several changes, one save: the debounce is what keeps a colour drag from
    # bouncing every display in the building.
    assert out['alive'] == 1, 'each change must replace the pending save, not add one'
    assert out['posts'] == ['/settings'], out['posts']
    assert out['debounce'] >= 300, 'too tight to coalesce a drag'
    # Silent on success: a "Saving…"/"Saved" on every checkbox is noise during a meet,
    # and the change is its own confirmation — the field holds what you typed.
    assert out['note'] == '', f'a success notice is back: {out["note"]!r}'


# ── Race detection (Timing pane) ───────────────────────────────────────────────
#
# `finish_debounce` holds the results back on every screen at once — the board, the
# Results page and the cloud — so a value left off the default is worth saying out
# loud. It used to sit in an Appearance > Flow pane beside three timeouts that only
# `scoreboard.html` read; that template is gone and these two moved to Timing, which
# is what they actually are: console behaviour, not display behaviour.

def test_the_flow_pane_is_gone(src):
    assert 'id="tab-flow"' not in src
    assert 'flow_settings_submit' not in src
    assert 'nav_flow' not in src, 'the sidebar still links to a pane that does not exist'


def test_race_detection_lives_in_the_timing_pane(src):
    timing = src[src.index('id="tab-debug"'):]
    timing = timing[:timing.index('id="tab-appearance"')] if 'id="tab-appearance"' in timing else timing
    for field in ('finish_debounce', 'split_min_duration'):
        assert 'name="%s"' % field in timing, f'{field} is not in the Timing pane'


def test_both_fields_are_explained(src):
    """They are the two settings most likely to be changed without knowing the cost."""
    for key in ('timing_finish_debounce_help', 'timing_split_min_help'):
        assert '{{ t.%s }}' % key in src


def test_the_default_comes_from_the_server(src):
    """Retyping 3.0 into the template is how it drifts from `state`."""
    assert 'data-default="{{ finish_debounce_default }}"' in src
    import sys
    sys.path.insert(0, os.path.join(REPO, 'server'))
    import state
    assert state.settings['finish_debounce'] == state.FINISH_DEBOUNCE_DEFAULT


@needs_js
def test_the_warning_tracks_the_default_and_the_reset_saves(src):
    """`3` and `3.0` are the same delay, so the comparison has to be numeric — and
    the reset sets the value from script, which fires no event by itself."""
    blk = re.search(r'^    function defaultWarning\(inputId, warnId, resetId\) \{.*?^    \}',
                    src, re.S | re.M)
    assert blk, 'defaultWarning is no longer a top-level function'

    harness = '''
    var els = {};
    function mk(id) { var o = { id:id, value:'', dataset:{}, _h:{}, _cls:{},
      addEventListener:function(e,f){ (this._h[e]=this._h[e]||[]).push(f); },
      dispatchEvent:function(ev){ (this._h[ev.type]||[]).forEach(function(f){f();}); } };
      o.classList = { toggle: function (c, on) { o._cls[c] = !!on; } };
      els[id]=o; return o; }
    var input = mk('finish_debounce'); input.dataset.default = '3'; input.value = '3.0';
    var warn = mk('finish-debounce-warn'), reset = mk('finish-debounce-reset');
    var document = { getElementById: function (id) { return els[id] || null; } };
    function Event(t) { this.type = t; }
    ''' + blk.group(0) + '''
    defaultWarning('finish_debounce', 'finish-debounce-warn', 'finish-debounce-reset');
    var steps = [];
    // What the browser actually goes by — `hidden` loses to `.d-flex` in Bootstrap.
    function snap(l) { steps.push([l, input.value,
                       warn._cls['d-none'] === true && warn._cls['d-flex'] === false]); }
    snap('load');
    input.value = '5';   input.dispatchEvent(new Event('change')); snap('changed');
    input.value = '3.0'; input.dispatchEvent(new Event('change')); snap('back to default');
    input.value = '0.5'; input.dispatchEvent(new Event('change')); snap('low');
    reset.dispatchEvent(new Event('click'));                        snap('reset');
    JSON.stringify({ steps: steps, changeHandlers: (input._h['change'] || []).length });
    '''
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(harness)
        path = fh.name
    try:
        res = subprocess.run(['osascript', '-l', 'JavaScript', path],
                             capture_output=True, text=True)
    finally:
        os.unlink(path)
    assert res.returncode == 0, res.stderr
    steps = dict((s[0], (s[1], s[2])) for s in json.loads(res.stdout)['steps'])

    assert steps['load'][1] is True,            'warns on a value that is the default'
    assert steps['changed'][1] is False,        'no warning on a non-default value'
    assert steps['back to default'][1] is True, '"3.0" and "3" are the same delay'
    assert steps['low'][1] is False
    assert steps['reset'] == ('3', True),       'reset must restore the default and clear'


@needs_js
def test_a_failed_save_still_speaks(src):
    """Silence is right for success and wrong for failure.

    A change that never reached the server looks exactly like one that did — the
    field still holds what you typed — so this is the one case the note exists for.
    """
    fn = re.search(r'^    function autoSave\(form, noteId, opts\) \{.*?^    \}',
                   src, re.S | re.M)
    assert fn
    harness = '''
    var T = { js_request_failed_c: 'Failed: ' };
    var __timers = [];
    var note = { className: '', textContent: '' };
    var document = { getElementById: function () { return note; } };
    function setTimeout(f, ms) { __timers.push([f, ms]); return __timers.length; }
    function clearTimeout(i) { if (i) __timers[i - 1] = null; }
    function FormData(f) {}
    function fetch() {
      return { then: function () {
        return { then: function () { return this; },
                 catch: function (h) { h(new Error('HTTP 500')); return this; },
                 finally: function (h) { h(); return this; } }; } };
    }
    var handlers = [];
    var form = { action: '/settings', querySelectorAll: function () {
      var els = [{ addEventListener: function (ev, fn) { handlers.push(fn); } }];
      els.forEach = Array.prototype.forEach.bind(els);
      return els; } };
    ''' + fn.group(0) + '''
    autoSave(form, 'note');
    handlers[0]();
    __timers.filter(Boolean)[0][0]();
    JSON.stringify({ text: note.textContent, cls: note.className });
    '''
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(harness)
        path = fh.name
    try:
        res = subprocess.run(['osascript', '-l', 'JavaScript', path],
                             capture_output=True, text=True)
    finally:
        os.unlink(path)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert 'HTTP 500' in out['text'], 'the reason has to reach the operator'
    assert 'text-danger' in out['cls']


def test_the_warning_is_hidden_by_class_not_by_hidden(src):
    """`hidden` does not work on this element, and the failure is invisible in a stub.

    Bootstrap's reboot has `[hidden]{display:none!important}` and its utilities have
    `.d-flex{display:flex!important}` — equal specificity, both important, and
    `.d-flex` comes later in `bootstrap-5.3.3.min.css`, so it wins and the alert stays
    on screen however often the script sets `hidden`. The JS toggles the two display
    utilities instead, and the server renders the right one so the warning is correct
    before any script runs.
    """
    for warn_id in ('finish-debounce-warn', 'split-min-warn'):
        el = re.search(r'<div id="%s"[^>]*>' % warn_id, src).group(0)
        assert ' hidden' not in el, f'{warn_id}: hidden is back and does nothing here'
        assert 'd-none' in el and 'd-flex' in el, f'{warn_id}: start state must be server-side'
    block = re.search(r'^    function defaultWarning\(.*?^    \}', src, re.S | re.M).group(0)
    assert 'warn.hidden' not in block
    assert "classList.toggle('d-none'" in block and "classList.toggle('d-flex'" in block


@pytest.mark.parametrize('field,warn,reset,default_var', [
    ('finish_debounce',    'finish-debounce-warn', 'finish-debounce-reset',
     'finish_debounce_default'),
    ('split_min_duration', 'split-min-warn',       'split-min-reset',
     'split_min_duration_default'),
])
def test_both_race_detection_fields_warn_off_default(src, field, warn, reset, default_var):
    """Each changes how the meet is read, not how it looks, so neither should sit off
    its default quietly."""
    assert 'data-default="{{ %s }}"' % default_var in src
    el = re.search(r'<div id="%s"[^>]*>' % warn, src).group(0)
    assert ' hidden' not in el, 'hidden does not work here — see the Bootstrap note'
    assert 'd-none' in el and 'd-flex' in el, 'the start state must come from the server'
    assert 'id="%s"' % reset in src
    assert "defaultWarning('%s'" % field in src


def test_the_defaults_are_named_once(src):
    """Retyping 3.0 or 1.0 into the template is how it drifts from `state`."""
    import sys
    sys.path.insert(0, os.path.join(REPO, 'server'))
    import state
    assert state.settings['finish_debounce'] == state.FINISH_DEBOUNCE_DEFAULT
    assert state.settings['split_min_duration'] == state.SPLIT_MIN_DEFAULT


@needs_js
def test_the_shared_warning_helper_works_for_the_split_field(src):
    """One helper, two fields — so the second is not a copy that drifts."""
    blk = re.search(r'^    function defaultWarning\(inputId, warnId, resetId\) \{.*?^    \}',
                    src, re.S | re.M)
    assert blk
    harness = '''
    var els = {};
    function mk(id) { var o = { id:id, value:'', dataset:{}, _h:{}, _cls:{},
      addEventListener:function(e,f){ (this._h[e]=this._h[e]||[]).push(f); },
      dispatchEvent:function(ev){ (this._h[ev.type]||[]).forEach(function(f){f();}); } };
      o.classList = { toggle: function (c, on) { o._cls[c] = !!on; } };
      els[id]=o; return o; }
    var input = mk('split_min_duration'); input.dataset.default = '1'; input.value = '1.0';
    var warn = mk('split-min-warn'), reset = mk('split-min-reset');
    var document = { getElementById: function (id) { return els[id] || null; } };
    function Event(t) { this.type = t; }
    ''' + blk.group(0) + '''
    defaultWarning('split_min_duration', 'split-min-warn', 'split-min-reset');
    var steps = [];
    function hidden() { return warn._cls['d-none'] === true && warn._cls['d-flex'] === false; }
    steps.push(['load', input.value, hidden()]);
    input.value = '2.5'; input.dispatchEvent(new Event('change'));
    steps.push(['changed', input.value, hidden()]);
    reset.dispatchEvent(new Event('click'));
    steps.push(['reset', input.value, hidden()]);
    JSON.stringify(steps);
    '''
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(harness)
        path = fh.name
    try:
        res = subprocess.run(['osascript', '-l', 'JavaScript', path],
                             capture_output=True, text=True)
    finally:
        os.unlink(path)
    assert res.returncode == 0, res.stderr
    steps = dict((s[0], (s[1], s[2])) for s in json.loads(res.stdout))
    assert steps['load'][1] is True,    'no warning when the value is the default'
    assert steps['changed'][1] is False
    assert steps['reset'] == ('1', True), 'reset restores the default and clears'
