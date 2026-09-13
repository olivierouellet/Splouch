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
SETTINGS = os.path.join(REPO, 'server', 'templates', 'settings.html')

needs_js = pytest.mark.skipif(not shutil.which('osascript'),
                              reason='needs JavaScriptCore (macOS)')


@pytest.fixture(scope='module')
def src():
    return open(SETTINGS, encoding='utf-8').read()


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
    var T = { js_saved: 'Saved', js_saving: 'Saving…', js_request_failed_c: 'Failed: ' };
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
    assert out['note'] == 'Saved'


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
    blk = re.search(r'^    \(function \(\) \{\n        var input = '
                    r'document\.getElementById\(.finish_debounce.\);.*?^    \}\)\(\);',
                    src, re.S | re.M)
    assert blk, 'the race-detection block is no longer a top-level IIFE'

    harness = '''
    var els = {};
    function mk(id) { var o = { id:id, hidden:false, value:'', dataset:{}, _h:{},
      addEventListener:function(e,f){ (this._h[e]=this._h[e]||[]).push(f); },
      dispatchEvent:function(ev){ (this._h[ev.type]||[]).forEach(function(f){f();}); } };
      els[id]=o; return o; }
    var input = mk('finish_debounce'); input.dataset.default = '3'; input.value = '3.0';
    var warn = mk('finish-debounce-warn'), reset = mk('finish-debounce-reset');
    mk('timing_tuning_form');
    var document = { getElementById: function (id) { return els[id] || null; } };
    function Event(t) { this.type = t; }
    var saves = 0;
    function autoSave() { return { saveNow: function () { saves++; } }; }
    ''' + blk.group(0) + '''
    var steps = [];
    function snap(l) { steps.push([l, input.value, warn.hidden]); }
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
