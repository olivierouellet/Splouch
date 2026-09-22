"""The Settings page's file-picker buttons, and the three ways one goes quiet.

Every upload on this page is a hidden `<input type="file">` behind a styled label, so
the only thing an operator sees is what the handler puts on screen afterwards. That
makes silence indistinguishable from success, and all three of these shipped:

* **The dialog and the server disagreed.** `/test_session_upload` stored more
  formats than the input offered, so some of them could not be picked at all. The
  two are pinned together now — `SESSION_UPLOAD_EXTS` and the `accept` attribute —
  which is what kept them in step when `.cap` was retired.
* **A refusal looked like a success.** That route answered the same redirect to
  /settings whether it saved the file or dropped it, and the page reloaded its session
  list either way — the upload "worked", the row just never appeared.
* **The same file could not be picked twice.** `change` does not fire when the value
  is unchanged, so a handler that never clears `input.value` gets one attempt per
  file per page load. After a failure, re-picking the file it rejected does nothing.

Plus the shared `.file-picker` listener, which shares the `change` event with the
handler that does the upload and so must not throw into it.

Qt-free: the route is driven directly, the markup read as text.
"""
import asyncio
import io
import os
import re
import subprocess
import sys
from typing import cast
import tempfile

import pytest
from fastapi import Request, UploadFile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from conftest import matched, settings_source  # noqa: E402
from jsc import HAS_JS_ENGINE  # noqa: E402
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, 'server'))

import state                     # noqa: E402
import routes.debug as debug     # noqa: E402

SETTINGS = os.path.join(REPO, 'server', 'templates', 'settings.html')

needs_js = pytest.mark.skipif(
    not HAS_JS_ENGINE, reason='needs a JavaScript engine (osascript or node)')


@pytest.fixture(scope='module')
def src():
    return settings_source()


def _Upload(name):
    """A real UploadFile, not a stand-in.

    The upload routes narrow on the type now — a form value is `str | UploadFile`
    and only the second has a `.filename` — so a double that merely looked the
    part would pass a test the route would reject in production.
    """
    return UploadFile(file=io.BytesIO(b'recorded bytes'), filename=name)


class _Req:
    def __init__(self, upload):
        self._upload = upload

    async def form(self):
        return {'session_file': self._upload}


@pytest.fixture
def sessions_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(state, 'CUSTOM_SESSIONS_FOLDER', str(tmp_path))
    monkeypatch.setattr(debug.state, 'CUSTOM_SESSIONS_FOLDER', str(tmp_path))
    return tmp_path


# ── The session upload route ───────────────────────────────────────────────────

@pytest.mark.parametrize('name', ['rec.cts', 'rec.raw', 'REC.CTS'])
def test_every_recording_format_is_stored(sessions_dir, name):
    """Upper case too: the check used to be `endswith`, so `.CTS` was dropped."""
    out = asyncio.run(debug.route_test_session_upload(cast(Request, _Req(_Upload(name)))))
    assert out == {'ok': True}
    assert (sessions_dir / name).exists()


def test_a_wrong_extension_is_refused_in_words(sessions_dir):
    """Not a redirect: the page cannot tell a silent drop from a save."""
    out = asyncio.run(debug.route_test_session_upload(cast(Request, _Req(_Upload('notes.txt')))))
    assert out['ok'] is False
    for ext in debug.SESSION_UPLOAD_EXTS:
        assert ext in out['error'], 'the refusal must name what it would take'
    assert not list(sessions_dir.iterdir())


def test_no_file_at_all_is_refused(sessions_dir):
    out = asyncio.run(debug.route_test_session_upload(cast(Request, _Req(None))))
    assert out['ok'] is False and out['error']


def test_the_dialog_offers_exactly_what_the_route_stores(src):
    """These drifted apart, and the dialog is the half an operator can see."""
    field = src[src.index("onchange=\"testUpload(this)\"") - 400:]
    field = field[field.index('<input'):]
    accept = matched(r'accept="([^"]*)"', field).split(',')
    assert accept == list(debug.SESSION_UPLOAD_EXTS)


# ── The page's own handlers ────────────────────────────────────────────────────

@pytest.mark.parametrize('fn', ['testUpload', 'testMeetUpload', 'updateMeetFile',
                                'restoreBackup'])
def test_every_fetch_upload_clears_the_input_it_read(src, fn):
    """`change` does not fire on an unchanged value — one shot per file otherwise.

    Only the handlers that upload with `fetch` need this. The ones that submit the
    form navigate away, so the page (and the input) is rebuilt regardless.
    """
    body = src[src.index('function %s(' % fn):]
    body = body[:body.index('\n    }\n')]
    assert "input.value = ''" in body, \
        f'{fn} never clears its input, so the same file cannot be picked twice'


def test_the_session_upload_reports_a_refusal(src):
    body = src[src.index('function testUpload('):]
    body = body[:body.index('\n    }\n')]
    assert 'r.json()' in body, 'the response is ignored, so a refusal reads as success'
    assert 'd.error' in body, 'the server says why; show it'
    assert "getElementById('test-session-status')" in body
    assert 'id="test-session-status"' in src, 'nowhere to put the message'


@needs_js
def test_the_filename_listener_survives_a_picker_with_no_message_span(src):
    """It shares `change` with the upload handler, so a throw here is not contained.

    Two wrappers carry no `.file-msg` — they report through a status line of their
    own. One of them, the Test tab's, ran *after* its upload handler with the files
    still attached, and threw a TypeError on every pick.
    """
    blk = re.search(r"^document\.querySelectorAll\('\.file-picker input.*?^\}\);",
                    src, re.S | re.M)
    assert blk, 'the shared .file-picker listener has moved'
    harness = '''
    function mkInput(hasMsg) {
      var msg = hasMsg ? { textContent: '', style: {} } : null;
      var picker = { querySelector: function (s) { return s === '.file-msg' ? msg : null; } };
      var hs = [];
      return { files: [{ name: 'rec.cts' }], closest: function () { return picker; },
               addEventListener: function (e, fn) { hs.push(fn); },
               fire: function () { var s = this; hs.forEach(function (h) { h.call(s); }); } };
    }
    var inputs = [mkInput(true), mkInput(false)];
    var document = { querySelectorAll: function () { return inputs; } };
    function setTimeout() {}
    ''' + blk.group(0) + '''
    var out = [];
    inputs.forEach(function (i) {
      try { i.fire(); out.push('ok'); } catch (e) { out.push(e.name); }
    });
    out.join(',');
    '''
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write(harness)
        path = fh.name
    try:
        res = subprocess.run(['osascript', '-l', 'JavaScript', path],
                             capture_output=True, text=True)
    finally:
        os.remove(path)
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == 'ok,ok', \
        'a wrapper without a .file-msg must not throw out of the shared listener'


def test_no_file_picker_without_a_message_span_is_left_unguarded(src):
    """The guard is what makes the two bare wrappers legal — keep them accounted for."""
    listener = src[src.index(".file-picker input[type=\"file\"]"):]
    listener = listener[:listener.index('\n    });')]
    assert 'msg &&' in listener, 'no null guard: a wrapper with no span throws again'
