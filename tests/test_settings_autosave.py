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
    ('flow-settings-form',    'flow-save-note'),
    ('display-settings-form', 'display-save-note'),
    ('theme_update_form',     'theme-save-note'),
])
def test_each_appearance_form_saves_itself(src, form_id, note_id):
    assert 'id="%s"' % form_id in src
    assert 'id="%s"' % note_id in src
    assert "autoSave(document.getElementById('%s'), '%s'" % (form_id, note_id) in src


@pytest.mark.parametrize('btn', ['btn-update-flow', 'btn-update-display', 'btn-update-theme'])
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
    for note in ('flow-save-note', 'display-save-note', 'theme-save-note'):
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
