/* ============================================================================
   Splouch operator panels — shared UI behaviour.
   Used by BOTH the Pi server Settings panel and the cloud Admin panel.

   Single canonical copy: shared/static/js/panel.js. The server serves it
   directly; the cloud image copies shared/static/ at build time.
   Loaded after bootstrap.bundle.js and BEFORE each page's own inline <script>
   (so window.panelShowTab / copyKey are defined first).

   Provides:
     - Colour theme toggle (Light / Dark / Auto), key "cts_theme".
     - Sidebar tab switcher window.panelShowTab(target); also dispatches a
       'panel:tab-shown' CustomEvent (detail.target) for optional per-tab init.
     - window.copyKey(btn, text) copy-to-clipboard with feedback.
   Press-and-hold confirmation for [data-hold] lives in its own hold.js, which every
   page loading this file must load too (it is shared with /manual).
   The anti-flash pre-paint theme snippet stays inline in each <head>.
   ========================================================================== */
(function () {
    'use strict';

    /* ── Colour theme toggle (Light / Dark / Auto) ── */
    var THEME_KEY = 'cts_theme';
    var mq = matchMedia('(prefers-color-scheme: dark)');
    function applyTheme(pref) {
        var mode = (pref === 'auto') ? (mq.matches ? 'dark' : 'light') : pref;
        document.documentElement.setAttribute('data-bs-theme', mode);
        document.querySelectorAll('[data-theme-set]').forEach(function (b) {
            b.classList.toggle('active', b.getAttribute('data-theme-set') === pref);
        });
    }
    applyTheme(localStorage.getItem(THEME_KEY) || 'dark');
    document.querySelectorAll('[data-theme-set]').forEach(function (b) {
        b.addEventListener('click', function () {
            var pref = b.getAttribute('data-theme-set');
            localStorage.setItem(THEME_KEY, pref);
            applyTheme(pref);
        });
    });
    mq.addEventListener('change', function () {
        if ((localStorage.getItem(THEME_KEY) || 'dark') === 'auto') applyTheme('auto');
    });

    /* ── Sidebar tab switcher ──
       Activates the target pane and every .tab-pane ancestor (nested panes),
       marks the sidebar link active, expands the collapse group that holds it,
       and dispatches 'panel:tab-shown'. Page-specific per-tab side effects can
       either bind their own click listeners or listen for that event. */
    function showTab(target) {
        var pane = target && document.querySelector(target);
        if (!pane) return;
        document.querySelectorAll('.tab-pane').forEach(function (p) { p.classList.remove('active'); });
        var el = pane;
        while (el) {
            if (el.classList && el.classList.contains('tab-pane')) el.classList.add('active');
            el = el.parentElement;
        }
        document.querySelectorAll('.app-nav .nav-link').forEach(function (a) { a.classList.remove('active'); });
        var link = document.querySelector('.app-nav .nav-link[data-target="' + target + '"]');
        if (link) {
            link.classList.add('active');
            var grp = link.closest('.collapse');
            if (grp && window.bootstrap) bootstrap.Collapse.getOrCreateInstance(grp, { toggle: false }).show();
        }
        document.dispatchEvent(new CustomEvent('panel:tab-shown', { detail: { target: target } }));
    }
    window.panelShowTab = showTab;

    document.querySelectorAll('.app-nav .nav-link[data-target]').forEach(function (link) {
        link.addEventListener('click', function (e) {
            e.preventDefault();
            var target = link.getAttribute('data-target');
            showTab(target);
            try { history.replaceState(null, '', target); } catch (_) {}
            var oc = document.getElementById('app-sidebar');
            if (window.bootstrap && oc) {
                var inst = bootstrap.Offcanvas.getInstance(oc);
                if (inst) inst.hide();
            }
        });
    });

    /* ── Press-and-hold confirmation for destructive actions ──
       Now in shared/static/js/hold.js, loaded alongside this file: /manual needs the
       same behaviour without the rest of the panel shell, and one copy is the point.
       See that file for the [data-hold] attributes. */

    /* ── Copy to clipboard (with "copied!" feedback) ── */
    window.copyKey = function (btn, text) {
        var orig = btn.textContent;
        var copied = btn.dataset.copied || 'Copied!';
        navigator.clipboard.writeText(text).then(function () {
            btn.textContent = copied; btn.classList.add('copied');
            setTimeout(function () { btn.textContent = orig; btn.classList.remove('copied'); }, 2000);
        });
    };

    /* ── UI-language selector: store a per-device cookie and reload so the
       server re-renders the panel in the chosen language. 'auto' (or empty)
       clears the cookie so the panel follows the browser language again.
       Drives both the segmented button group and the dropdown fallback (shown
       when custom locales make the button group too wide). ── */
    function setUiLang(value) {
        if (!value || value === 'auto') {
            document.cookie = 'ui_lang=;path=/;max-age=0';
        } else {
            document.cookie = 'ui_lang=' + encodeURIComponent(value) + ';path=/;max-age=31536000';
        }
        location.reload();
    }
    document.querySelectorAll('[data-ui-lang-set]').forEach(function (b) {
        b.addEventListener('click', function () { setUiLang(b.getAttribute('data-ui-lang-set')); });
    });
    document.querySelectorAll('[data-ui-lang]').forEach(function (sel) {
        sel.addEventListener('change', function () { setUiLang(sel.value); });
    });
})();
