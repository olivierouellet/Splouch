"""Run a rendered page's scripts the way a browser would, and fail on a load error.

The rest of the template suite matches substrings in the rendered HTML. That catches
markup, and misses everything that happens after the page is parsed — `schedule.html`
once shipped with `ws.js` loaded *after* the block that called into it, so the first
thing the page did was throw `ReferenceError`, the whole script died, and the Schedule
tab rendered nothing at all. Every substring test still passed.

So: run the page's scripts in document order under JavaScriptCore against a stub DOM,
each as its own program the way a browser compiles each `<script>`, and assert nothing
was thrown. This is a smoke test, not a browser.
It proves the page's load path executes — declarations resolve, top-level calls
complete, handlers register. It does not prove anything was drawn, and it cannot see an
error thrown later from a socket frame or a tap.

JavaScriptCore is reached through `osascript -l JavaScript`, which is macOS-only; where
it is missing the tests skip rather than fail.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(REPO, 'shared', 'static')

HAS_JSC = shutil.which('osascript') is not None

# A stub, deliberately dumb: every element is the same object, and it answers whatever
# the pages ask of it. Widen it when a page needs something, rather than teaching it to
# model layout — the moment it has opinions it starts passing pages a browser would
# not, which is worse than not running them.
_DOM = r'''
var __calls = [];
function __node(tag) {
  var n = {
    tagName: (tag || 'div').toUpperCase(),
    innerHTML: '', textContent: '', value: '', hidden: false, checked: false,
    dataset: {}, style: {}, children: [], parentNode: null,
    offsetWidth: 320, offsetHeight: 40, scrollWidth: 320, clientWidth: 320,
    scrollTop: 0, scrollHeight: 800, clientHeight: 600,
    classList: { _s: {},
      add: function () {}, remove: function () {},
      toggle: function () {}, contains: function () { return false; } },
    addEventListener: function () {}, removeEventListener: function () {},
    appendChild: function (c) { this.children.push(c); return c; },
    removeChild: function (c) { return c; },
    setAttribute: function () {}, getAttribute: function () { return null; },
    removeAttribute: function () {},
    querySelector: function () { return __node(); },
    querySelectorAll: function () { return __list([]); },
    closest: function () { return null; },
    focus: function () {}, blur: function () {}, click: function () {},
    scrollIntoView: function () {}, getBoundingClientRect: function () {
      return { top: 0, left: 0, right: 320, bottom: 40, width: 320, height: 40 }; },
    // An <iframe> the shell reveals; the tab callback is optional there too.
    contentWindow: { on_tab_shown: function () {}, dispatchEvent: function () {},
                     addEventListener: function () {}, location: { reload: function () {} } }
  };
  return n;
}
function __list(a) { a.forEach = Array.prototype.forEach.bind(a); a.item = function (i) { return a[i]; }; return a; }

var __byId = {};
var document = {
  documentElement: __node('html'), body: __node('body'), head: __node('head'),
  hidden: false, visibilityState: 'visible', readyState: 'complete',
  cookie: '', title: '',
  getElementById: function (id) { if (!__byId[id]) __byId[id] = __node(); return __byId[id]; },
  querySelector: function () { return __node(); },
  querySelectorAll: function () { return __list([]); },
  getElementsByClassName: function () { return __list([]); },
  getElementsByTagName: function () { return __list([]); },
  createElement: function (t) { return __node(t); },
  createTextNode: function () { return __node('#text'); },
  addEventListener: function () {}, removeEventListener: function () {},
  dispatchEvent: function () { return true; }
};

function __storage() { var m = {}; return {
  getItem: function (k) { return Object.prototype.hasOwnProperty.call(m, k) ? m[k] : null; },
  setItem: function (k, v) { m[k] = String(v); },
  removeItem: function (k) { delete m[k]; }, clear: function () { m = {}; } }; }
var localStorage = __storage(), sessionStorage = __storage();

function setTimeout(fn, ms) { __calls.push('setTimeout'); return 0; }
function setInterval(fn, ms) { __calls.push('setInterval'); return 0; }
function clearTimeout() {} function clearInterval() {}
function requestAnimationFrame(fn) { __calls.push('raf'); return 0; }
function cancelAnimationFrame() {}
function confirm() { return true; } function alert() {} function prompt() { return ''; }

// Never actually opens: a load-path test must not depend on a server. Handlers are
// registered and left unfired, which is the point — we are testing what runs before
// any frame arrives.
function WebSocket(url) {
  this.url = url; this.readyState = 0;
  this.send = function () {}; this.close = function () {};
  this.addEventListener = function () {};
}
WebSocket.CONNECTING = 0; WebSocket.OPEN = 1; WebSocket.CLOSING = 2; WebSocket.CLOSED = 3;

function fetch() { return { then: function () { return this; }, catch: function () { return this; } }; }

var location = { href: 'https://splouch.test/', protocol: 'https:', host: 'splouch.test',
                 hostname: 'splouch.test', port: '', pathname: '/', search: '', hash: '',
                 origin: 'https://splouch.test', reload: function () {}, replace: function () {},
                 assign: function () {} };
var navigator = { userAgent: 'jsc-harness', language: 'fr-CA', languages: ['fr-CA'],
                  onLine: true, serviceWorker: undefined };
var screen = { width: 390, height: 844, orientation: { type: 'portrait-primary' } };
var performance = { now: function () { return 0; } };

var window = {
  document: document, location: location, navigator: navigator, screen: screen,
  performance: performance, localStorage: localStorage, sessionStorage: sessionStorage,
  innerWidth: 390, innerHeight: 844, devicePixelRatio: 2,
  crypto: { randomUUID: function () { return 'stub-uuid'; },
            getRandomValues: function (a) { return a; } },
  setTimeout: setTimeout, setInterval: setInterval,
  clearTimeout: clearTimeout, clearInterval: clearInterval,
  requestAnimationFrame: requestAnimationFrame,
  addEventListener: function () {}, removeEventListener: function () {},
  dispatchEvent: function () { return true; },
  matchMedia: function () { return { matches: false, addListener: function () {},
                                     addEventListener: function () {} }; },
  getComputedStyle: function () { return { getPropertyValue: function () { return ''; },
                                           fontSize: '16px' }; },
  parent: null, top: null, WebSocket: WebSocket, fetch: fetch
};
window.parent = window; window.top = window; window.self = window;
var self = window;
var crypto = window.crypto;
function getComputedStyle() { return window.getComputedStyle(); }
function matchMedia() { return window.matchMedia(); }
'''


class PageScriptError(AssertionError):
    pass


def _scripts(html):
    """Every script in document order: (label, source). `src` is read off disk."""
    out = []
    for i, m in enumerate(re.finditer(r'<script([^>]*)>(.*?)</script>', html, re.S)):
        attrs, body = m.group(1), m.group(2)
        src = re.search(r'src="([^"]+)"', attrs)
        if src:
            path = src.group(1)
            if not path.startswith('/static/'):
                continue                      # external; a browser would fetch it, we skip
            disk = os.path.join(STATIC, path[len('/static/'):])
            if not os.path.isfile(disk):
                raise PageScriptError('page references %s, which is not in shared/static' % path)
            out.append((path, open(disk, encoding='utf-8').read()))
        else:
            out.append(('inline #%d' % i, body))
    return out


def run_page(html, extra=''):
    """Execute the page's scripts in order. Raises on the first uncaught error.

    Each script runs through an indirect `eval`, which is a separate program in the
    shared global scope — the same shape as a browser, where every `<script>` is its
    own compilation unit over one `window`. Running them as one concatenated block
    instead would hoist each script's function declarations over the ones before it,
    and a page that calls a helper it has not loaded yet would pass. That is precisely
    the bug this harness exists to catch, so the distinction is the whole design.

    Returns what the page scheduled (timers, animation frames), for a test that wants
    to assert more than "it did not throw".
    """
    scripts = _scripts(html)
    program = [
        _DOM, extra,
        'var __src = %s;' % json.dumps([body for _, body in scripts]),
        'var __labels = %s;' % json.dumps([label for label, _ in scripts]),
        'var __current = "harness";',
        '__result = (function () {',
        '  for (var i = 0; i < __src.length; i++) {',
        '    __current = __labels[i];',
        '    try { (0, eval)(__src[i]); }',
        r'    catch (e) { return ["ERROR", __current, e && e.name, e && e.message].join("\t"); }',
        '  }',
        '  return "OK";',
        '})();',
        r'__result + "\n" + __calls.join(",")',
    ]

    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as fh:
        fh.write('\n'.join(program))
        path = fh.name
    try:
        res = subprocess.run(['osascript', '-l', 'JavaScript', path],
                             capture_output=True, text=True)
    finally:
        os.unlink(path)

    if res.returncode != 0:
        raise PageScriptError('JavaScriptCore refused the program:\n' + res.stderr.strip())

    head, _, scheduled = res.stdout.strip().partition('\n')
    if head.startswith('ERROR'):
        _, where, name, message = head.split('\t', 3)
        raise PageScriptError(
            'the page threw while executing %s\n    %s: %s\n'
            'A browser stops the whole block there, so everything after it never runs.'
            % (where, name, message))
    return scheduled
