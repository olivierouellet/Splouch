/* Attendance counting the spectator can refuse (docs/app.md `C-10`).

   One place for the attendance id, so the picker and every meet page agree on it:
   whether counting is allowed, the id itself, and its age. Off deletes the id and
   makes none; back on makes a new one, never the old. An id older than 13 months
   is replaced. A browser sending Global Privacy Control starts off, until the
   spectator says otherwise.

   Per origin, like everything in localStorage. A meet page on another host learns
   the picker's choice from the fragment the picker hands it (`#vid=<id>`, or
   `#vid=0` when off) — `accept()` below. */
var SplouchCount = (function () {
    var VID = 'splouch_vid',
        AT = 'splouch_vid_at',
        CHOICE = 'splouch_count';
    var MAX_AGE = 395 * 24 * 3600 * 1000; // ~13 months

    function get(k) {
        try {
            return localStorage.getItem(k);
        } catch (e) {
            return null;
        }
    }
    function put(k, v) {
        try {
            localStorage.setItem(k, v);
        } catch (e) {}
    }
    function drop(k) {
        try {
            localStorage.removeItem(k);
        } catch (e) {}
    }

    function allowed() {
        var c = get(CHOICE);
        if (c === 'on') return true;
        if (c === 'off') return false;
        return !(typeof navigator !== 'undefined' && navigator.globalPrivacyControl);
    }

    function forget() {
        drop(VID);
        drop(AT);
    }

    function set(on) {
        put(CHOICE, on ? 'on' : 'off');
        if (!on) forget();
    }

    function fresh() {
        return window.crypto && window.crypto.randomUUID
            ? window.crypto.randomUUID()
            : 'v' + Date.now() + Math.random().toString(36).slice(2);
    }

    /* The id to send with `join_meet`, or '' while counting is refused. An id from
       before ages were kept is dated now rather than replaced, so nobody is
       counted twice the day this shipped. */
    function vid() {
        if (!allowed()) {
            forget();
            return '';
        }
        var v = get(VID),
            at = parseInt(get(AT), 10);
        if (v && !at) {
            put(AT, String(Date.now()));
            return v;
        }
        if (!v || Date.now() - at > MAX_AGE) {
            v = fresh();
            put(VID, v);
            put(AT, String(Date.now()));
        }
        return v;
    }

    /* A meet page on another host: take the picker's choice from the fragment,
       then clear it from the address bar so it stays out of history and out of
       any link the spectator shares. */
    function accept() {
        var m = /(?:^#|&)vid=([^&]+)/.exec(location.hash);
        if (!m) return;
        var v = decodeURIComponent(m[1]).slice(0, 64);
        if (v === '0') {
            set(false);
        } else {
            put(CHOICE, 'on');
            if (get(VID) !== v) {
                put(VID, v);
                put(AT, String(Date.now()));
            }
        }
        try {
            history.replaceState(null, '', location.pathname + location.search);
        } catch (e) {}
    }

    return { allowed: allowed, set: set, vid: vid, accept: accept };
})();
