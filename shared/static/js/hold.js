/* ============================================================================
   Press-and-hold confirmation for [data-hold] elements.

   Lifted out of panel.js so pages that are not operator panels can use it too.
   /manual is one: it is a standalone phone page like operator.html, and pulling in
   the whole panel shell — tab switcher, sidebar, theme toggle, panel.css — to get
   one button behaviour would be the wrong trade. panel.js still loads this file, so
   there is one implementation and the Power tab's Reboot button and /manual's Next
   button behave identically.

   Usage:
     data-hold                 mark the element (required)
     data-hold-fn="name"       global function to call on completion
     data-hold-href="/path"    else navigate here (falls back to href)
     data-hold-label="Hold…"   swap the text while holding
     data-hold-ms="1500"       override the ~1.2 s default

   The duration lives in two places that must agree — the JS timer and the CSS fill
   animation — so the JS writes --hold-ms and the CSS reads it. Setting one without
   the other is what makes a hold button lie about its own progress.
   ========================================================================== */
(function () {
    'use strict';

    var HOLD_MS = 1200;
    var active = null, timer = null, origLabel = null;

    function reset(el) {
        el.classList.remove('btn-holding');
        el.style.removeProperty('--hold-ms');
        if (origLabel !== null) { el.textContent = origLabel; origLabel = null; }
    }
    function run(el) {
        var fn = el.getAttribute('data-hold-fn');
        var href = el.getAttribute('data-hold-href') || el.getAttribute('href');
        if (fn && typeof window[fn] === 'function') window[fn]();
        else if (href) window.location.href = href;
    }
    function cancel() {
        if (timer) { clearTimeout(timer); timer = null; }
        if (active) { reset(active); active = null; }
    }
    document.addEventListener('click', function (e) {
        if (e.target.closest('[data-hold]')) e.preventDefault();  // no plain-click action
    }, true);
    document.addEventListener('pointerdown', function (e) {
        var el = e.target.closest('[data-hold]');
        if (!el || el.disabled || el.classList.contains('disabled')) return;
        e.preventDefault();
        active = el;
        var ms = parseInt(el.getAttribute('data-hold-ms'), 10);
        if (!(ms > 0)) ms = HOLD_MS;
        /* Before the class, so the fill animation starts at the right duration
           rather than running once at the default and snapping. */
        el.style.setProperty('--hold-ms', ms + 'ms');
        el.classList.add('btn-holding');
        var lbl = el.getAttribute('data-hold-label');
        if (lbl !== null) { origLabel = el.textContent; el.textContent = lbl; }
        timer = setTimeout(function () {
            timer = null;
            var el2 = active; active = null;
            if (el2) { reset(el2); run(el2); }
        }, ms);
    });
    document.addEventListener('pointerup', cancel);
    document.addEventListener('pointercancel', cancel);
    document.addEventListener('pointermove', function (e) {
        if (active && e.target.closest('[data-hold]') !== active) cancel();
    });
})();
