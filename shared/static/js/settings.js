// @ts-check
// Settings page behaviour. Loaded by templates/settings.html.
//
// This was 1389 lines inline in that template. Exactly one thing in it was ever
// server-rendered — the `T` strings table — so that stays in the page as a small
// data island and everything else lives here, where an editor can lint it and a
// browser can cache it.
//
// `T` is a global, set immediately before this file loads. The shared shell (theme
// toggle, the tab switcher `window.panelShowTab`, hold-to-confirm, copyKey) is
// /static/js/panel.js; page-specific logic follows.

// The Account tab's confirm field appears once the new password is long enough to
// be worth confirming. Called from an `oninput=` attribute, which resolves globals
// at event time — so living in this file rather than the page changes nothing.
function onPasswordInput(form) {
    document.getElementById('pw-confirm-row').style.visibility =
        form.password.value.length > 3 ? 'visible' : 'hidden';
}

// ── Restore tab + flash button after form submit ──────────────────────────
// Deferred so all tab-init functions are defined first
function _refreshMeetTab() {
    fetch('/meet_status')
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            var sel = document.getElementById('meet-file-select');
            if (sel) {
                sel.innerHTML = '<option value="">' + T.meet_no_file + '</option>';
                (d.file_list || []).forEach(function (f) {
                    var opt = document.createElement('option');
                    opt.value = f;
                    opt.textContent = f;
                    if (f === d.active) opt.selected = true;
                    sel.appendChild(opt);
                });
                if (!d.active) sel.value = '';
                var loadBtn = sel.parentElement.querySelector(
                    'button[name="meet_file_load_submit"]',
                );
                if (loadBtn) loadBtn.disabled = !(d.file_list && d.file_list.length);
            }
            var actions = document.getElementById('meet-file-actions');
            if (d.active) {
                actions.innerHTML =
                    '<a id="meet-preview-btn" href="' +
                    d.preview_url +
                    '" class="btn btn-secondary btn-sm flex-shrink-0">' +
                    T.btn_preview +
                    '</a>' +
                    '<label class="btn btn-secondary btn-sm m-0 flex-shrink-0" style="cursor:pointer; white-space:nowrap;">' +
                    T.meet_update_file_btn +
                    '<input id="meet_update_file" type="file" accept=".csv,.lxf" style="display:none;" onchange="updateMeetFile(this)"></label>' +
                    '<button class="btn btn-outline-danger btn-sm flex-shrink-0" type="button" data-hold data-hold-fn="meetDeleteHeld" data-hold-label="' +
                    T.hold_delete +
                    '">' +
                    T.btn_delete +
                    '</button>' +
                    '<button class="btn btn-outline-danger btn-sm flex-shrink-0" type="button" data-hold data-hold-href="/meet_clear" data-hold-label="' +
                    T.hold_delete_all +
                    '">' +
                    T.btn_delete_all +
                    '</button>';
            } else {
                actions.innerHTML = '';
            }
            var warn = document.getElementById('meet-test-warning');
            var input = document.getElementById('meet_file');
            var label = document.querySelector('#tab-meet .file-picker label');
            warn.style.display = d.playing ? '' : 'none';
            input.disabled = d.playing;
            if (label) label.style.opacity = d.playing ? '0.5' : '';
        });
}

function _tabInitFor(tabHref) {
    var fns = {
        '#tab-meet': _refreshMeetTab,
        '#tab-test': function () {
            _loadTestStatus();
            _ensureUpdateSock();
            _updateSock.off('test_status').on('test_status', _loadTestStatus);
        },
        '#tab-network': function () {
            _netTabActive();
        },
        '#tab-netgroup': function () {
            _netTabActive();
        },
        '#tab-time': function () {
            _loadTimeStatus();
            _loadRtcStatus();
        },
        '#tab-update': function () {
            loadVersions();
        },
        '#tab-debug': function () {
            _loadXterm && _loadXterm(function () {});
            fetch('/debug_status')
                .then(function (r) {
                    return r.json();
                })
                .then(function (d) {
                    _applyDebugState(d.enabled);
                });
        },
    };
    if (fns[tabHref]) fns[tabHref]();
}

// The generic sidebar tab switcher lives in panel.js as window.panelShowTab;
// per-tab side effects are wired via each tab's own click listener below and
// _tabInitFor() on the post-submit restore path.

(function () {
    var state = JSON.parse(localStorage.getItem('cts_tab_restore') || 'null');
    if (state) {
        localStorage.removeItem('cts_tab_restore');
        var target = state.nested || state.tab;
        if (target) {
            panelShowTab(target);
            setTimeout(function () {
                _tabInitFor(target);
            }, 0);
        }
        if (state.btn) {
            var btn = document.getElementById(state.btn);
            if (btn) {
                btn.classList.remove('btn-secondary', 'btn-warning');
                btn.classList.add('btn-success');
                setTimeout(function () {
                    btn.classList.remove('btn-success');
                    btn.classList.add('btn-secondary');
                }, 2500);
            }
        }
    } else {
        // Fresh load: activate the default section (Meet Setup) in the sidebar.
        panelShowTab('#tab-meet');
    }
})();

// Save active tab + button ID before any settings form submits
document.querySelectorAll('form[action="/settings"]').forEach(function (form) {
    form.addEventListener('submit', function () {
        var activeLink = document.querySelector('.app-nav .nav-link.active');
        var btn = form.querySelector('[type="submit"]');
        if (!btn && form.id)
            btn = document.querySelector('[form="' + form.id + '"][type="submit"]');
        localStorage.setItem(
            'cts_tab_restore',
            JSON.stringify({
                tab: activeLink ? activeLink.getAttribute('data-target') : null,
                nested: null,
                btn: btn ? btn.id : null,
            }),
        );
    });
});
// docs/app.md `P-01`: keep a meet past its dates on the cloud's picker a while.
// Held (hold.js), then the page comes back on the tab it was held on.
function keepListedHeld() {
    var activeLink = document.querySelector('.app-nav .nav-link.active');
    try {
        localStorage.setItem(
            'cts_tab_restore',
            JSON.stringify({ tab: activeLink ? activeLink.getAttribute('data-target') : null }),
        );
    } catch (_e) {}
    fetch('/meet_keep_listed', { method: 'POST' }).finally(function () {
        location.reload();
    });
}
function _fsubmit(f) {
    if (f.requestSubmit) f.requestSubmit();
    else f.submit();
}
// Replace the loaded meet's file in place. The server only accepts a file
// that is the same meet (same meet_uid), so the cloud link is preserved.
function updateMeetFile(input) {
    var file = input.files && input.files[0];
    if (!file) return;
    var msg = document.getElementById('meet-update-msg');
    msg.style.display = 'block';
    _statusColor(msg, 'muted');
    msg.textContent = T.js_checking;
    var fd = new FormData();
    fd.append('meet_file', file);
    fetch('/meet_update_file', { method: 'POST', body: fd })
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            if (d.ok) {
                _statusColor(msg, 'ok');
                msg.textContent = T.js_updated_reloading;
                setTimeout(function () {
                    location.reload();
                }, 3000);
            } else {
                _statusColor(msg, 'err');
                msg.textContent = d.error || T.js_update_failed;
            }
        })
        .catch(function () {
            _statusColor(msg, 'err');
            msg.textContent = T.js_update_failed;
        });
    input.value = '';
}

// ── Status colours ─────────────────────────────────────────────────────────
// Toggle Bootstrap's semantic (theme-aware) text colours instead of inline hex,
// so "OK" greens / errors stay legible in BS5 dark mode.
function _statusColor(el, kind) {
    if (!el) return;
    el.classList.remove(
        'text-success',
        'text-danger',
        'text-warning',
        'text-secondary',
        'text-body-secondary',
    );
    el.classList.add(
        kind === 'ok'
            ? 'text-success'
            : kind === 'err'
              ? 'text-danger'
              : kind === 'warn'
                ? 'text-warning'
                : 'text-body-secondary',
    );
}

// ── Apply-as-you-change ───────────────────────────────────────────────────
// Flow, Display Options and Theme used to each carry an Update button that was
// both the trigger and the receipt: amber while dirty, green for 2.5s after the
// page came back. They now save themselves, so the receipt moved to an inline
// note beside each card.
//
// POSTed with fetch rather than submitted: these forms navigate, and a settings
// page that reloads on every checkbox is worse than the button was. It matters
// most on Theme, where the inputs are colour pickers — a submit per adjustment
// would reload the panel out from under the picker, and every one of these saves
// also emits `reload` to the TV displays and every connected phone
// (server/routes/settings.py), so a drag across the spectrum would bounce the
// whole pool. Hence the debounce: one save once the changes stop.
function autoSave(form, noteId, opts) {
    opts = opts || {};
    var note = document.getElementById(noteId);
    var timer = null,
        inFlight = false;

    // Nothing is said on the way through. A "Saving…" then "Saved" on every
    // checkbox is noise: the panel is for a meet in progress, and the change is
    // its own confirmation — the field holds what you typed and the board shows
    // it. A failure is not self-evident, so that still speaks.
    function fail(text) {
        if (!note) return;
        note.className = 'small ms-2 text-danger';
        note.textContent = text;
    }
    function clear() {
        if (note) note.textContent = '';
    }
    function save() {
        if (inFlight) {
            schedule();
            return;
        } // coalesce onto the next window
        inFlight = true;
        clear();
        fetch(form.action, { method: 'POST', body: new FormData(form) })
            .then(function (r) {
                if (!r.ok) throw new Error('HTTP ' + r.status);
                // Some fields change how this very page renders — the scoreboard
                // locale is also the panel language when no ui_lang cookie is set.
                if (opts.reloadAfter && opts.reloadAfter()) location.reload();
            })
            .catch(function (e) {
                fail(T.js_request_failed_c + e.message);
            })
            .finally(function () {
                inFlight = false;
            });
    }
    function schedule() {
        clearTimeout(timer);
        timer = setTimeout(save, 600);
    }

    form.querySelectorAll('input, select, textarea').forEach(function (el) {
        // `change` only, never `input`: `input` fires per keystroke and per pixel
        // of a colour picker's drag. `change` lands on blur and on commit, which
        // is the moment a value is actually meant.
        el.addEventListener('change', schedule);
    });
    return { saveNow: save };
}

function markDirty(tabId, btnId) {
    document.querySelectorAll('#' + tabId + ' input, #' + tabId + ' select').forEach(function (el) {
        ['input', 'change'].forEach(function (evt) {
            el.addEventListener(evt, function () {
                var btn = document.getElementById(btnId);
                btn.classList.remove('btn-secondary');
                btn.classList.add('btn-warning');
            });
        });
    });
}
// Still button-driven: these carry a credential, a connection or a per-meet
// record, where an accidental change should not reach the server on its own.
markDirty('cloud_settings_form', 'btn-update-cloud');
markDirty('cloud_appearance_form', 'btn-update-meet');
markDirty('tab-account', 'btn-update-account');

// Appearance saves as you go.
var _localeAtLoad = (document.getElementById('locale') || {}).value;
function _localeChanged() {
    var el = document.getElementById('locale');
    return !!el && el.value !== _localeAtLoad;
}

autoSave(document.getElementById('display-settings-form'), 'display-save-note', {
    // Changing the scoreboard language changes this panel's own language too,
    // unless a ui_lang cookie overrides it — so re-render rather than sit stale.
    reloadAfter: function () {
        return _localeChanged();
    },
});
autoSave(document.getElementById('theme_update_form'), 'theme-save-note');

// ── Race detection ────────────────────────────────────────────────────────
// Both of these change how the meet is read, not how it looks: `finish_debounce`
// holds the results back on every screen at once — the board, the Results page and
// the cloud — and `split_min_duration` decides whether a turn is counted at all. A
// value left off the default is worth saying out loud rather than being
// rediscovered mid-meet, so each gets a warning and its default back in one press.
function defaultWarning(inputId, fieldId, resetId) {
    var input = document.getElementById(inputId);
    var field = document.getElementById(fieldId);
    var reset = document.getElementById(resetId);
    if (!input || !field || !reset) return;

    function sync() {
        // Compare as numbers: "3" and "3.0" are the same delay.
        var isDefault = parseFloat(input.value) === parseFloat(input.dataset.default);
        // `.changed` highlights the label and shows ↺, like a Theme swatch.
        field.classList.toggle('changed', !isDefault);
    }
    input.addEventListener('input', sync);
    input.addEventListener('change', sync);
    reset.addEventListener('click', function () {
        input.value = input.dataset.default;
        sync();
        // Set from script, so neither event fires on its own — and `change` is
        // what autoSave listens for (see the colour-swatch revert).
        input.dispatchEvent(new Event('change', { bubbles: true }));
    });
    sync();
}

autoSave(document.getElementById('timing_tuning_form'), 'timing-tuning-note');
defaultWarning('finish_debounce', 'finish-debounce-field', 'finish-debounce-reset');
defaultWarning('split_min_duration', 'split-min-field', 'split-min-reset');

// ── Theme colour pickers: flag changes from default + per-swatch revert ────
(function () {
    function syncSwatch(inp) {
        var cell = inp.closest('.cs');
        if (!cell) return;
        var def = (inp.dataset.default || '').toLowerCase();
        cell.classList.toggle('changed', inp.value.toLowerCase() !== def);
    }
    document.querySelectorAll('#tab-theme input[type="color"]').forEach(function (inp) {
        syncSwatch(inp);
        inp.addEventListener('input', function () {
            syncSwatch(inp);
        });
    });
    document.querySelectorAll('#tab-theme .cs-reset').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var inp = btn.closest('.cs').querySelector('input[type="color"]');
            inp.value = inp.dataset.default;
            // Both events, and both are load-bearing: 'input' re-syncs the changed
            // hint on the swatch, 'change' is what autoSave() listens for. A colour
            // set from script fires neither on its own, so reverting a swatch would
            // look reverted and never reach the server.
            inp.dispatchEvent(new Event('input', { bubbles: true }));
            inp.dispatchEvent(new Event('change', { bubbles: true }));
        });
    });
})();

// ── Test tab ─────────────────────────────────────────────────────────────
var _recording = false;

function _loadTestStatus() {
    fetch('/test_status')
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            var bar = document.getElementById('test-status-bar');
            var hr = document.getElementById('test-status-hr');
            var text = document.getElementById('test-status-text');
            if (d.playing) {
                bar.style.display = 'flex';
                hr.style.display = '';
                text.textContent = T.js_playing_c + d.session;
            } else {
                bar.style.display = 'none';
                hr.style.display = 'none';
                text.textContent = '';
            }
            if (d.speed !== undefined) _highlightSpeed(d.speed);
            _recording = d.recording;
            var btn = document.getElementById('btn-record');
            btn.textContent = _recording ? T.test_stop : T.test_start;
            btn.className = _recording ? 'btn btn-danger btn-sm' : 'btn btn-secondary btn-sm';
            // Start is a hold, Stop a tap — the inline onclick only fires without it.
            _setHold(btn, !_recording, T.hold_start);
            btn.disabled = d.playing;
            document.getElementById('test-record-name').disabled = d.playing;
            document.getElementById('test-record-format').disabled = d.playing || _recording;
            document.getElementById('test-record-status').textContent = _recording
                ? T.js_recording
                : '';

            // A loaded meet is held aside for the test and put back afterwards —
            // the operator no longer has to delete and re-upload it.
            var aside = document.getElementById('test-meet-aside-note');
            aside.style.display = d.has_meet || d.meet_set_aside ? '' : 'none';

            // A console with no wire decodes a recording to nothing, so the replay runs
            // under a CTS instead. Shown before Play as well as during, so the operator
            // knows in advance that the board will not be their own console's.
            var replayNote = document.getElementById('test-replay-console-note');
            if (replayNote)
                replayNote.style.display =
                    d.replay_console || d.replay_console_needed ? '' : 'none';

            _renderSessions(d.sessions, d.playing);

            // Test meet section
            var meetSection = document.getElementById('test-meet-section');
            var meetStatus = document.getElementById('test-meet-status');
            var meetUploadBtn = document.getElementById('test-meet-upload-btn');
            if (d.playing && !d.has_meet) {
                if (d.test_meet) {
                    meetSection.style.display = '';
                    meetStatus.textContent = T.js_test_meet_loaded_c + d.test_meet_name;
                    _statusColor(meetStatus, 'ok');
                    meetUploadBtn.style.display = 'none';
                } else {
                    // Only show manual upload for custom sessions (built-ins auto-load their companion)
                    var isBuiltin = d.sessions.some(function (s) {
                        return s.name === d.session && s.source === 'builtin';
                    });
                    meetSection.style.display = isBuiltin ? 'none' : '';
                    meetStatus.textContent = T.test_no_meet_hint;
                    _statusColor(meetStatus, 'muted');
                    meetUploadBtn.style.display = '';
                }
            } else {
                meetSection.style.display = 'none';
            }

            // Meet Setup tab warning
            var testPlaying = d.playing;
            document.getElementById('meet-test-warning').style.display = testPlaying ? '' : 'none';
            document.getElementById('meet_file').disabled = testPlaying;
            var meetFileLabel = document.querySelector(
                'label[for="meet_file_label"], #tab-meet .file-picker label',
            );
            if (meetFileLabel) meetFileLabel.style.opacity = testPlaying ? '0.5' : '';
        });
}

function _renderSessions(sessions, anyPlaying) {
    var div = document.getElementById('test-session-list');
    if (!sessions.length) {
        div.innerHTML = '<span class="text-body-secondary">No sessions found.</span>';
        return;
    }
    var html = '';
    sessions.forEach(function (s) {
        var isPlaying =
            document.getElementById('test-status-text').textContent === T.js_playing_c + s.name;
        /* min-width, not width: `Reproduciendo` and `Supprimer` are half as
           long again as `Playing` and `Delete`, and a fixed width wraps them
           inside the button. */
        var W = 'min-width:70px;';
        var playBtn = isPlaying
            ? '<button class="btn btn-success btn-sm text-nowrap" style="' +
              W +
              '" disabled>' +
              T.js_playing +
              '</button>'
            : '<button class="btn btn-secondary btn-sm text-nowrap" style="' +
              W +
              '" data-hold data-hold-fn="testPlayHeld" data-hold-label="' +
              T.hold_play +
              '" data-session="' +
              _escAttr(s.name) +
              '"' +
              (anyPlaying ? ' disabled' : '') +
              '>' +
              T.js_play +
              '</button>';
        /* The placeholder for a built-in session, which has nothing to delete,
           is the delete button itself made invisible. An empty box of a fixed
           width only lined up while the button was one too — in French it left
           every built-in row short by the difference. */
        var delBtn =
            s.source === 'custom'
                ? '<button class="btn btn-secondary btn-sm me-1 text-nowrap" style="' +
                  W +
                  '" data-hold data-hold-fn="testDeleteHeld" data-hold-label="' +
                  T.hold_delete +
                  '" data-session="' +
                  _escAttr(s.name) +
                  '">' +
                  T.btn_delete +
                  '</button>'
                : '<button class="btn btn-secondary btn-sm me-1 text-nowrap" style="' +
                  W +
                  ' visibility:hidden;" tabindex="-1" aria-hidden="true">' +
                  T.btn_delete +
                  '</button>';
        var badge =
            s.source === 'builtin'
                ? '<span class="small text-body-secondary ms-1">' + T.js_builtin + '</span>'
                : '';
        html +=
            '<div class="d-flex align-items-center border-bottom py-1 gap-1">' +
            '<span class="flex-fill text-break" style="min-width:0;">' +
            s.name +
            badge +
            '</span>' +
            '<div class="d-flex flex-shrink-0">' +
            delBtn +
            playBtn +
            '</div>' +
            '</div>';
    });
    div.innerHTML = html;
}

// For a value going into a double-quoted attribute of markup built as a string.
function _escAttr(v) {
    return String(v).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function setSpeed(s) {
    fetch('/test_set_speed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ speed: s }),
    })
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            _highlightSpeed(d.speed);
        });
}

function _highlightSpeed(s) {
    var map = { 0.25: '¼×', 0.5: '½×', 1: '1×', 2: '2×', 4: '4×', 10: '10×' };
    document.querySelectorAll('.speed-btn').forEach(function (btn) {
        var match = Math.abs(parseFloat(btn.textContent) - s) < 0.01 || btn.textContent === map[s];
        btn.className = 'btn btn-sm speed-btn ' + (match ? 'btn-primary' : 'btn-secondary');
    });
}

function testPlay(name) {
    fetch('/test_play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name }),
    }).then(function () {
        setTimeout(_loadTestStatus, 400);
    });
}

function testStop() {
    fetch('/test_stop', { method: 'POST' }).then(function () {
        setTimeout(_loadTestStatus, 400);
    });
}

// The Meet tab's Delete: held, then deletes whichever file the picker shows.
function meetDeleteHeld() {
    var f = document.getElementById('meet-file-select').value;
    if (f) location.href = '/meet_delete?file=' + encodeURIComponent(f);
}

// Hold handlers get the button; the session rides on it as data-session.
function testPlayHeld(el) {
    testPlay(el.dataset.session);
}

function testDeleteHeld(el) {
    testDelete(el.dataset.session);
}

function testDelete(name) {
    fetch('/test_session_delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name }),
    }).then(function () {
        _loadTestStatus();
    });
}

function testUpload(input) {
    if (!input.files || !input.files[0]) return;
    var fd = new FormData();
    fd.append('session_file', input.files[0]);
    var status = document.getElementById('test-session-status');
    _statusColor(status, 'muted');
    status.textContent = T.loading;
    fetch('/test_session_upload', { method: 'POST', body: fd })
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            if (d.ok) {
                status.textContent = '';
                _loadTestStatus();
            } else {
                _statusColor(status, 'err');
                status.textContent = d.error || T.js_upload_failed;
            }
        })
        .catch(function () {
            _statusColor(status, 'err');
            status.textContent = T.js_upload_failed;
        });
    // Cleared so re-picking the same file fires `change` again. Without it a
    // failed upload could not be retried with the same file — the value never
    // changes, so the browser never fires the event.
    input.value = '';
}

function testRecordToggle() {
    if (_recording) {
        fetch('/test_record_stop', { method: 'POST' }).then(function () {
            _loadTestStatus();
        });
    } else {
        var name = document.getElementById('test-record-name').value.trim() || 'recording';
        fetch('/test_record_start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                format: document.getElementById('test-record-format').value,
            }),
        }).then(function () {
            _loadTestStatus();
        });
    }
}

function testMeetUpload(input) {
    if (!input.files || !input.files[0]) return;
    var fd = new FormData();
    fd.append('meet_file', input.files[0]);
    var status = document.getElementById('test-meet-status');
    _statusColor(status, 'muted');
    status.textContent = T.loading;
    fetch('/test_meet_upload', { method: 'POST', body: fd })
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            if (d.ok) {
                setTimeout(_loadTestStatus, 300);
            } else {
                _statusColor(status, 'err');
                status.textContent = d.error || T.js_upload_failed;
            }
        });
    input.value = ''; // so the same file can be picked again after a failure
}

document.querySelectorAll('.app-nav .nav-link').forEach(function (link) {
    if (link.getAttribute('data-target') === '#tab-test')
        link.addEventListener('click', function () {
            _ensureUpdateSock();
            _updateSock.off('test_status').on('test_status', _loadTestStatus);
            _loadTestStatus();
        });
    if (link.getAttribute('data-target') === '#tab-devtools')
        link.addEventListener('click', function () {
            _ensureUpdateSock();
            _updateSock.off('test_status').on('test_status', _loadTestStatus);
            _loadTestStatus();
        });
    if (link.getAttribute('data-target') === '#tab-meet')
        link.addEventListener('click', _refreshMeetTab);
});

// ── Network tab ─────────────────────────────────────────────────────────
var _joinSsid = '';

function _applyWifiStatus(d) {
    var btn = document.getElementById('btn-wifi-toggle');
    var wtext = document.getElementById('wifi-status-text');
    var eip = document.getElementById('eth-ip-text');
    btn.disabled = false;
    btn.textContent = d.enabled ? T.js_disable_wifi : T.js_enable_wifi;
    // Turning WiFi *off* can drop the operator's own connection to this page, so
    // it takes the same press-and-hold as Reboot and Shutdown. Turning it on is
    // harmless and stays a plain tap, so `data-hold` comes and goes with the
    // state — panel.js reads the attribute at press time, so a delegated handler
    // picks that up with no re-binding.
    //
    // Driven by `d.enabled`, never by the label: this used to sniff the button's
    // text for "Disable", which is a word that only appears in English. In French
    // and Spanish the hold never armed and the button fired on a single tap —
    // exactly the press it was there to prevent.
    _setHold(btn, d.enabled, T.js_hold_disable);
    if (!d.enabled) {
        _statusColor(wtext, 'muted');
        wtext.textContent = T.js_disabled;
    } else if (d.wifi_ip) {
        _statusColor(wtext, 'ok');
        wtext.textContent = d.ssid ? d.ssid + ' · ' + d.wifi_ip : d.wifi_ip;
    } else if (d.ssid) {
        _statusColor(wtext, 'muted');
        wtext.textContent = d.ssid + ' · ' + T.js_no_ip;
    } else {
        _statusColor(wtext, 'muted');
        wtext.textContent = T.js_not_connected;
    }
    if (d.eth_ip) {
        _statusColor(eip, 'ok');
        eip.textContent = d.eth_ip;
    } else {
        _statusColor(eip, 'muted');
        eip.textContent = T.js_not_connected;
    }
    if (d.eth_ip) {
        document.getElementById('eth-ip-input').value = d.eth_ip.split('/')[0];
    }
    if (d.eth_gateway) document.getElementById('eth-gateway-input').value = d.eth_gateway;
    if (d.eth_dns) document.getElementById('eth-dns-input').value = d.eth_dns;
}

// Arm or disarm a press-and-hold on a toggle whose risky half is one state only
// (WiFi off, cloud Disconnect, recording Start). hold.js reads the attribute at
// press time, so flipping it is all a state change needs.
function _setHold(btn, on, label) {
    if (on) {
        btn.setAttribute('data-hold', '');
        btn.setAttribute('data-hold-label', label);
    } else {
        btn.removeAttribute('data-hold');
        btn.removeAttribute('data-hold-label');
    }
}

function setEthDhcp() {
    var status = document.getElementById('eth-ip-status');
    _statusColor(status, 'muted');
    status.textContent = T.js_applying;
    fetch('/eth_dhcp_set', { method: 'POST' })
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            if (d.ok) {
                _statusColor(status, 'ok');
                status.textContent = T.js_dhcp_switched;
            } else {
                _statusColor(status, 'err');
                status.textContent = d.error || T.js_failed;
            }
        });
}

// Dotted quad -> unsigned 32-bit int, or null when it isn't one.
function _ipv4ToInt(s) {
    var m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(s);
    if (!m) return null;
    var n = 0;
    for (var i = 1; i <= 4; i++) {
        var o = +m[i];
        if (o > 255) return null;
        n = n * 256 + o;
    }
    return n;
}

// Same rules as the server's EthIP model and install.sh: a host address in an
// /8–/30, a router inside that subnet, DNS optional (the router when blank).
// Returns the localized error, or '' when the form is valid.
function _ethFormError(ip, prefix, gateway, dns) {
    if (!ip) return T.js_enter_ip;
    var a = _ipv4ToInt(ip);
    var p = parseInt(prefix, 10);
    if (a === null || !(p >= 8 && p <= 30)) return T.js_eth_bad_ip;
    var size = 2 ** (32 - p);
    var host = a % size;
    if (host === 0 || host === size - 1) return T.js_eth_bad_ip;
    var g = _ipv4ToInt(gateway);
    if (g === null || g === a || Math.floor(g / size) !== Math.floor(a / size))
        return T.js_eth_bad_gateway;
    if (dns && _ipv4ToInt(dns) === null) return T.js_eth_bad_dns;
    return '';
}

function setEthIp() {
    var ip = document.getElementById('eth-ip-input').value.trim();
    var prefix = document.getElementById('eth-prefix-input').value.trim();
    var gateway = document.getElementById('eth-gateway-input').value.trim();
    var dns = document.getElementById('eth-dns-input').value.trim();
    var status = document.getElementById('eth-ip-status');
    var err = _ethFormError(ip, prefix, gateway, dns);
    if (err) {
        _statusColor(status, 'err');
        status.textContent = err;
        return;
    }
    _statusColor(status, 'muted');
    status.textContent = T.js_applying;
    fetch('/eth_ip_set', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ip: ip, prefix: prefix, gateway: gateway, dns: dns || null }),
    })
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            if (d.ok) {
                _statusColor(status, 'ok');
                status.textContent = T.js_applied_reconnect_c + ip + '/settings';
            } else {
                _statusColor(status, 'err');
                status.textContent = d.error || T.js_failed;
            }
        });
}

function _netTabActive() {
    fetch('/wifi_status')
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            if (d.error) {
                document.getElementById('wifi-status-text').textContent = d.error;
                return;
            }
            _applyWifiStatus(d);
        })
        .catch(function () {
            document.getElementById('wifi-status-text').textContent = T.js_error_loading_status;
        });
}

function toggleWifi() {
    document.getElementById('btn-wifi-toggle').disabled = true;
    fetch('/wifi_toggle', { method: 'POST' })
        .then(function (r) {
            return r.json();
        })
        .then(function () {
            // Re-fetch full status so IPs are accurate after toggle
            fetch('/wifi_status')
                .then(function (r) {
                    return r.json();
                })
                .then(_applyWifiStatus);
        });
}

// Enabling is a plain tap. `panel.js` swallows the click on anything carrying
// `data-hold`, so this only ever fires in the state where the attribute is off.
document.getElementById('btn-wifi-toggle').addEventListener('click', function () {
    if (!this.hasAttribute('data-hold')) toggleWifi();
});

// WiFi network list is HTMX-driven: the Scan button does hx-get="/wifi_scan",
// which renders settings/fetched/wifi_networks.html into
// #wifi-net-list. The "Connect" buttons in that fragment call showJoinForm().

function showJoinForm(ssid, needsPassword) {
    _joinSsid = ssid;
    document.getElementById('wifi-join-ssid').textContent = ssid;
    document.getElementById('wifi-join-pw').value = '';
    document.getElementById('wifi-join-pw').style.display = needsPassword ? '' : 'none';
    document.getElementById('wifi-join-status').textContent = '';
    document.getElementById('wifi-join-status').style.color = '';
    document.getElementById('wifi-join-form').style.display = '';
    if (needsPassword) document.getElementById('wifi-join-pw').focus();
}

function cancelJoin() {
    document.getElementById('wifi-join-form').style.display = 'none';
}

function connectWifi() {
    var pw = document.getElementById('wifi-join-pw').value;
    var status = document.getElementById('wifi-join-status');
    _statusColor(status, 'muted');
    status.textContent = T.js_connecting;
    fetch('/wifi_connect', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ssid: _joinSsid, password: pw }),
    })
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            if (d.ok) {
                _statusColor(status, 'ok');
                status.textContent = T.js_connected_excl;
                setTimeout(function () {
                    cancelJoin();
                    _netTabActive();
                    if (window.htmx) htmx.ajax('GET', '/wifi_scan', '#wifi-net-list');
                }, 1500);
            } else {
                _statusColor(status, 'err');
                status.textContent = d.error || T.js_connection_failed;
            }
        })
        .catch(function () {
            _statusColor(status, 'err');
            status.textContent = T.js_request_failed;
        });
}

// Connected-clients list is now HTMX-driven (hx-get="/clients_fragment",
// polled every 10s) — see the #client-list container in the Network tab.

// Load network status when the Network tab is clicked
document.querySelectorAll('.app-nav .nav-link').forEach(function (link) {
    if (
        link.getAttribute('data-target') === '#tab-network' ||
        link.getAttribute('data-target') === '#tab-netgroup'
    )
        link.addEventListener('click', function () {
            _netTabActive();
        });
});

var _updateSock = null;

function _ensureUpdateSock() {
    if (!_updateSock) _updateSock = splouchSocket('/ws/settings');
}

function startDisplaysUpdate() {
    var btn = document.getElementById('btn-update-displays');
    var msg = document.getElementById('displays-update-status');
    btn.disabled = true;
    msg.textContent = T.loading;
    fetch('/displays_update', { method: 'POST' })
        .then(function (r) {
            return r.json().then(function (d) {
                return { ok: r.ok, d: d };
            });
        })
        .then(function (res) {
            btn.disabled = false;
            msg.textContent = res.ok ? '' : res.d.error || T.js_error;
            // The list is polled every 5s and carries each display's progress.
            htmx.trigger('#displays-list', 'load');
        })
        .catch(function () {
            btn.disabled = false;
            msg.textContent = T.js_error;
        });
}

function loadVersions() {
    _ensureUpdateSock();
    var sel = document.getElementById('version-select');
    var cur = document.getElementById('current-version');
    cur.textContent = T.loading;
    sel.disabled = true;
    fetch('/version_list')
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            sel.disabled = false;
            if (!d.ok) {
                cur.textContent = d.error || T.js_error;
                return;
            }
            cur.textContent = d.current || 'untagged';
            sel.innerHTML = '';
            d.versions.forEach(function (v, i) {
                var opt = document.createElement('option');
                opt.value = v;
                opt.textContent =
                    v + (i === 0 ? ' (latest)' : '') + (v === d.current ? '  ← current' : '');
                sel.appendChild(opt);
            });
            var sep = document.createElement('option');
            sep.textContent = '------------';
            sep.disabled = true; // separator: never selectable
            sel.appendChild(sep);
            var mopt = document.createElement('option');
            mopt.value = 'master';
            mopt.textContent = T.js_dev_master;
            sel.appendChild(mopt);
            (d.branches || []).forEach(function (b) {
                var opt = document.createElement('option');
                opt.value = b;
                opt.textContent = b;
                sel.appendChild(opt);
            });
        })
        .catch(function () {
            sel.disabled = false;
            cur.textContent = T.js_error;
        });
}

/* Install and Repair write to the same log and share one output pane, so they
   share the reader too. `onDone(ok)` runs once, when the server reports the run
   finished; the returned function cancels the poll if the request never started.

   `repair` is decided by the server, which looks at the tree rather than
   guessing from git's error text — so the button appears only when discarding
   local changes is genuinely the answer. */
/* ── Reload once the server is actually back ──────────────────────────────────
   Every caller here restarts the service under the page that is watching, so the
   panel has to reload itself when the app returns. It used to guess: 6 seconds
   after an update, 5 after a service restart. The guess measured the wrong thing.
   `routes/update._run_update` publishes `done`, *then* sleeps 2s, refreshes the
   systemd unit and only then runs `systemctl restart` — so the browser's timer was
   already half spent before the server began going down, and on a Pi the Python
   cold start finished well after it. The reload landed on a dead port.

   So: probe until the app answers again, however long that takes.

   The subtlety is that the *old* process is still answering for the first couple of
   seconds, and reloading against it is the same bug wearing a different hat. A
   success is therefore only believed once a probe has failed — we have to watch it
   go down before watching it come back. `graceMs` keeps us from burning probes
   before the restart is even scheduled, and if the server never appears to drop
   (a restart quick enough to fall between two probes) `assumeAfterMs` gives up on
   seeing it and reloads anyway. */
function reloadWhenServerReturns(statusEl, opts) {
    opts = opts || {};
    var graceMs = opts.graceMs || 3000;
    var everyMs = opts.everyMs || 1000;
    var assumeAfterMs = opts.assumeAfterMs || 20000;
    var timeoutMs = opts.timeoutMs || 180000;
    var waited = 0,
        sawDown = false;

    setTimeout(function () {
        var poll = setInterval(function () {
            waited += everyMs;
            if (waited >= timeoutMs) {
                clearInterval(poll);
                if (statusEl) {
                    _statusColor(statusEl, 'err');
                    statusEl.textContent = T.js_restart_timeout;
                }
                return;
            }
            fetch('/settings', { cache: 'no-store' })
                .then(function (r) {
                    if (!r.ok) {
                        sawDown = true;
                        return;
                    }
                    if (sawDown || waited >= assumeAfterMs) {
                        clearInterval(poll);
                        // Come back on the tab that started it (Update, Power…), not
                        // Meet Setup: the same restore a settings form submit uses.
                        var activeLink = document.querySelector('.app-nav .nav-link.active');
                        localStorage.setItem(
                            'cts_tab_restore',
                            JSON.stringify({
                                tab: activeLink ? activeLink.getAttribute('data-target') : null,
                                nested: null,
                                btn: null,
                            }),
                        );
                        location.reload();
                    }
                })
                .catch(function () {
                    sawDown = true;
                });
        }, everyMs);
    }, graceMs);
}

function _followUpdateLog(onDone) {
    var out = document.getElementById('update-output');
    var row = document.getElementById('repair-row');
    var seen = 0;
    var poll = setInterval(function () {
        fetch('/update_log')
            .then(function (r) {
                return r.json();
            })
            .then(function (d) {
                for (var i = seen; i < d.lines.length; i++) {
                    var span = document.createElement('span');
                    span.textContent = d.lines[i].text;
                    if (d.lines[i].error) _statusColor(span, 'err');
                    out.appendChild(span);
                }
                seen = d.lines.length;
                out.scrollTop = out.scrollHeight;
                row.classList.toggle('d-none', !d.repair);
                row.classList.toggle('d-flex', !!d.repair);
                if (d.done !== null) {
                    clearInterval(poll);
                    onDone(d.done);
                }
            })
            .catch(function () {});
    }, 500);
    return function () {
        clearInterval(poll);
    };
}

function startUpdate() {
    var btn = document.getElementById('btn-run-update');
    var out = document.getElementById('update-output');
    var status = document.getElementById('update-status');
    var target = document.getElementById('version-select').value || null;
    btn.disabled = true;
    document.getElementById('btn-os-update').disabled = true;
    out.innerHTML = '';
    out.style.display = 'block';
    _statusColor(status, 'muted');
    status.textContent = T.js_running;
    var stop = _followUpdateLog(function (ok) {
        document.getElementById('btn-os-update').disabled = false;
        if (ok) {
            _statusColor(status, 'ok');
            status.textContent = T.js_done_restarting;
            reloadWhenServerReturns(status);
        } else {
            _statusColor(status, 'err');
            status.textContent = T.js_failed_see_output;
            btn.disabled = false;
        }
    });
    fetch('/update_start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: target }),
    })
        .then(function (r) {
            if (!r.ok) {
                stop();
                _statusColor(status, 'err');
                status.textContent = T.js_could_not_start_update;
                btn.disabled = false;
                document.getElementById('btn-os-update').disabled = false;
            }
        })
        .catch(function (e) {
            stop();
            _statusColor(status, 'err');
            status.textContent = T.js_request_failed_c + e;
            btn.disabled = false;
            document.getElementById('btn-os-update').disabled = false;
        });
}

/* Discards local edits, so it asks first — and names what it will throw away,
   which the log above has already listed. */
function repairCheckout() {
    if (!confirm(T.js_repair_confirm)) return;
    var btn = document.getElementById('btn-repair-checkout');
    var run = document.getElementById('btn-run-update');
    var out = document.getElementById('update-output');
    var status = document.getElementById('update-status');
    btn.disabled = true;
    run.disabled = true;
    out.innerHTML = '';
    out.style.display = 'block';
    _statusColor(status, 'muted');
    status.textContent = T.js_running;
    var stop = _followUpdateLog(function (ok) {
        btn.disabled = false;
        run.disabled = false;
        _statusColor(status, ok ? 'ok' : 'err');
        status.textContent = ok ? T.js_repair_done : T.js_failed_see_output;
    });
    fetch('/repair_checkout', { method: 'POST' })
        .then(function (r) {
            if (!r.ok) {
                stop();
                _statusColor(status, 'err');
                status.textContent = T.js_failed_see_output;
                btn.disabled = false;
                run.disabled = false;
            }
        })
        .catch(function (e) {
            stop();
            _statusColor(status, 'err');
            status.textContent = T.js_request_failed_c + e;
            btn.disabled = false;
            run.disabled = false;
        });
}

function startOsUpdate() {
    var btn = document.getElementById('btn-os-update');
    var out = document.getElementById('os-update-output');
    var status = document.getElementById('os-update-status');
    btn.disabled = true;
    document.getElementById('btn-run-update').disabled = true;
    out.innerHTML = '';
    out.style.display = 'block';
    _statusColor(status, 'muted');
    status.textContent = T.js_running;
    var seen = 0;
    var poll = setInterval(function () {
        fetch('/os_update_log')
            .then(function (r) {
                return r.json();
            })
            .then(function (d) {
                for (var i = seen; i < d.lines.length; i++) {
                    var span = document.createElement('span');
                    span.textContent = d.lines[i].text;
                    if (d.lines[i].error) _statusColor(span, 'err');
                    out.appendChild(span);
                }
                seen = d.lines.length;
                out.scrollTop = out.scrollHeight;
                if (d.done !== null) {
                    clearInterval(poll);
                    document.getElementById('btn-run-update').disabled = false;
                    if (d.done) {
                        _statusColor(status, 'ok');
                        status.textContent = T.js_done;
                    } else {
                        _statusColor(status, 'err');
                        status.textContent = T.js_failed_see_output;
                    }
                    btn.disabled = false;
                }
            })
            .catch(function () {});
    }, 500);
    fetch('/os_update_start', { method: 'POST' })
        .then(function (r) {
            if (!r.ok) {
                clearInterval(poll);
                _statusColor(status, 'err');
                status.textContent = T.js_could_not_start_os;
                btn.disabled = false;
                document.getElementById('btn-run-update').disabled = false;
            }
        })
        .catch(function (e) {
            clearInterval(poll);
            _statusColor(status, 'err');
            status.textContent = T.js_request_failed_c + e;
            btn.disabled = false;
            document.getElementById('btn-run-update').disabled = false;
        });
}

function restoreBackup(input) {
    var file = input.files[0];
    if (!file) return;
    input.value = '';
    var status = document.getElementById('restore-status');
    _statusColor(status, 'muted');
    status.textContent = T.cloud_uploading;
    var fd = new FormData();
    fd.append('backup_file', file);
    fetch('/backup_restore', { method: 'POST', body: fd })
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            if (!d.ok) {
                _statusColor(status, 'err');
                status.textContent = d.error || T.js_restore_failed;
                return;
            }
            _statusColor(status, 'muted');
            status.textContent = T.js_restored_restarting;
            reloadWhenServerReturns(status);
        })
        .catch(function () {
            _statusColor(status, 'err');
            status.textContent = T.js_upload_failed;
        });
}

document.querySelectorAll('.app-nav .nav-link').forEach(function (link) {
    if (link.getAttribute('data-target') === '#tab-update')
        link.addEventListener('click', loadVersions);
});

function updateFontPreview(select, previewId) {
    document.getElementById(previewId).style.fontFamily = "'" + select.value + "', monospace";
}

function saveCustomTheme() {
    var n = prompt(T.js_theme_name_prompt);
    if (n && n.trim()) {
        document.getElementById('theme_save_name').value = n.trim();
        document.getElementById('theme_save_form').submit();
    }
}

document.querySelectorAll('.file-picker input[type="file"]').forEach(function (input) {
    input.addEventListener(
        'change',
        /** @this {HTMLInputElement} */ function () {
            // A wrapper without a span is one that reports through its own status
            // line instead; it must not throw out of here, because this listener
            // shares the `change` event with the handler doing the actual upload.
            var msg = this.closest('.file-picker').querySelector('.file-msg');
            if (msg && this.files && this.files[0]) {
                msg.textContent = this.files[0].name;
                msg.style.transition = 'none';
                msg.style.opacity = '1';
                var el = msg;
                setTimeout(function () {
                    el.style.transition = 'opacity 2s';
                    el.style.opacity = '0';
                }, 2000);
            }
        },
    );
});

// ── Time tab ─────────────────────────────────────────────────────────────
function _loadTimeStatus() {
    fetch('/time_status')
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            document.getElementById('time-display').textContent = d.date + '  ' + d.time;
            document.getElementById('time-tz').textContent = d.timezone;
            var ntpEl = document.getElementById('ntp-status');
            if (d.ntp_active && d.synchronized) {
                ntpEl.textContent = T.clock_ntp_synced;
                _statusColor(ntpEl, 'ok');
            } else if (d.ntp_active) {
                ntpEl.textContent = T.clock_ntp_syncing;
                _statusColor(ntpEl, 'muted');
            } else {
                ntpEl.textContent = T.clock_ntp_inactive;
                _statusColor(ntpEl, 'err');
            }
            document.getElementById('time-set-date').value = d.date.replace(/-/g, '/');
            document.getElementById('time-set-time').value = d.time;
        });
}

function timeSync() {
    var btn = document.getElementById('btn-time-sync');
    var ntpEl = document.getElementById('ntp-status');
    btn.disabled = true;
    _statusColor(ntpEl, 'muted');
    ntpEl.textContent = T.js_checking_network;

    function doSync() {
        ntpEl.textContent = T.js_syncing;
        fetch('/time_sync', { method: 'POST' })
            .then(function (r) {
                return r.json();
            })
            .then(function (d) {
                btn.disabled = false;
                if (d.ok) {
                    setTimeout(_loadTimeStatus, 2000);
                } else {
                    _statusColor(ntpEl, 'err');
                    ntpEl.textContent = T.js_error_c + (d.error || T.js_sync_failed);
                }
            });
    }

    fetch('/wifi_status')
        .then(function (r) {
            return r.json();
        })
        .then(function (net) {
            var hasWifi = net.wifi_ip && net.wifi_ip.trim();
            var hasEth = net.eth_ip && net.eth_ip.trim() && net.eth_ip !== T.js_not_connected;
            if (!hasWifi && !hasEth) {
                btn.disabled = false;
                _statusColor(ntpEl, 'err');
                var msg;
                if (!net.enabled) msg = T.js_no_eth_wifi_off;
                else if (!net.ssid) msg = T.js_no_eth_wifi_disc;
                else msg = T.js_no_network_ntp;
                ntpEl.innerHTML =
                    msg +
                    ' <a href="#" onclick="document.querySelector(\'[href=\\"#tab-netgroup\\"]\').click();return false;">' +
                    T.js_go_to_network +
                    '</a>';
                return;
            }
            doSync();
        })
        .catch(doSync); // nmcli unavailable (dev machine) — try anyway
}

function timeSet() {
    var date = document.getElementById('time-set-date').value.replace(/\//g, '-');
    var time = document.getElementById('time-set-time').value;
    var status = document.getElementById('time-set-status');
    if (!date || !time) {
        _statusColor(status, 'err');
        status.textContent = T.js_enter_datetime;
        return;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
        _statusColor(status, 'err');
        status.textContent = T.js_use_date_format;
        return;
    }
    _statusColor(status, 'muted');
    status.textContent = T.js_setting;
    fetch('/time_set', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ date: date, time: time }),
    })
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            if (d.ok) {
                _statusColor(status, 'ok');
                status.textContent = T.js_time_set;
                setTimeout(_loadTimeStatus, 500);
            } else {
                _statusColor(status, 'err');
                status.textContent = d.error || T.js_failed;
            }
        });
}

document.querySelectorAll('.app-nav .nav-link').forEach(function (link) {
    if (link.getAttribute('data-target') === '#tab-time')
        link.addEventListener('click', function () {
            _loadTimeStatus();
            _loadRtcStatus();
        });
});

// ── RTC (Adafruit PiRTC DS3231) ─────────────────────────────────────────────
function _loadRtcStatus() {
    var el = document.getElementById('rtc-status');
    fetch('/rtc_status')
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            if (d.active) {
                _statusColor(el, 'ok');
                el.textContent = T.clock_rtc_active;
            } else if (d.configured) {
                _statusColor(el, 'muted');
                el.textContent = T.clock_rtc_configured;
            } else {
                _statusColor(el, 'muted');
                el.textContent = T.clock_rtc_not_installed;
            }
        })
        .catch(function () {});
}

function _runRtc(startUrl) {
    var btnInstall = document.getElementById('btn-rtc-install');
    var btnRemove = document.getElementById('btn-rtc-remove');
    var out = document.getElementById('rtc-output');
    var status = document.getElementById('rtc-status');
    var rebootRow = document.getElementById('rtc-reboot-row');
    btnInstall.disabled = true;
    btnRemove.disabled = true;
    rebootRow.style.display = 'none';
    out.innerHTML = '';
    out.style.display = 'block';
    _statusColor(status, 'muted');
    status.textContent = T.js_running;
    var seen = 0;
    var poll = setInterval(function () {
        fetch('/rtc_log')
            .then(function (r) {
                return r.json();
            })
            .then(function (d) {
                for (var i = seen; i < d.lines.length; i++) {
                    var span = document.createElement('span');
                    span.textContent = d.lines[i].text;
                    if (d.lines[i].error) _statusColor(span, 'err');
                    out.appendChild(span);
                }
                seen = d.lines.length;
                out.scrollTop = out.scrollHeight;
                if (d.done !== null) {
                    clearInterval(poll);
                    btnInstall.disabled = false;
                    btnRemove.disabled = false;
                    if (d.done) {
                        _statusColor(status, 'ok');
                        status.textContent = T.js_done;
                        rebootRow.style.display = 'flex';
                    } else {
                        _statusColor(status, 'err');
                        status.textContent = T.js_failed_see_output;
                    }
                }
            })
            .catch(function () {});
    }, 500);
    fetch(startUrl, { method: 'POST' })
        .then(function (r) {
            if (!r.ok) {
                clearInterval(poll);
                _statusColor(status, 'err');
                status.textContent = T.js_could_not_start;
                btnInstall.disabled = false;
                btnRemove.disabled = false;
            }
        })
        .catch(function (e) {
            clearInterval(poll);
            _statusColor(status, 'err');
            status.textContent = T.js_request_failed_c + e;
            btnInstall.disabled = false;
            btnRemove.disabled = false;
        });
}

function startRtcInstall() {
    _runRtc('/rtc_install_start');
}
function startRtcRemove() {
    _runRtc('/rtc_remove_start');
}

function rtcReboot() {
    var status = document.getElementById('rtc-status');
    document.getElementById('rtc-reboot-row').style.display = 'none';
    _statusColor(status, 'muted');
    status.textContent = T.js_rebooting;
    fetch('/system_reboot', { method: 'POST' });
}

// ── Save logs (Logs tab) ────────────────────────────────────────────────────────────
function toggleLogMenu(e) {
    e.stopPropagation();
    var m = document.getElementById('log-menu');
    m.style.display = m.style.display === 'block' ? 'none' : 'block';
}
document.addEventListener('click', function () {
    var m = document.getElementById('log-menu');
    if (m) m.style.display = 'none';
});
function downloadLogs() {
    document.getElementById('log-menu').style.display = 'none';
    window.location.href = '/logs_download';
}
function saveLogsToPi() {
    document.getElementById('log-menu').style.display = 'none';
    var status = document.getElementById('log-save-status');
    status.textContent = T.js_saving_logs;
    fetch('/logs_save', { method: 'POST' })
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            status.textContent = d.ok
                ? T.js_saved_c + d.path
                : T.js_save_failed_c + (d.error || '');
            setTimeout(function () {
                status.textContent = '';
            }, 8000);
        })
        .catch(function () {
            status.textContent = T.js_save_failed;
        });
}

// ── System power ─────────────────────────────────────────────────────────
function serviceRestart() {
    var status = document.getElementById('system-power-status');
    status.textContent = T.js_restarting_service;
    fetch('/system_service_restart', { method: 'POST' });
    // The app (which serves this page) goes down briefly; wait for it to answer
    // again rather than guessing how long that takes.
    reloadWhenServerReturns(status);
}

function systemReboot() {
    document.getElementById('system-power-status').textContent = T.js_rebooting;
    fetch('/system_reboot', { method: 'POST' });
}

function systemShutdown() {
    document.getElementById('system-power-status').textContent = T.js_shutting_down;
    fetch('/system_shutdown', { method: 'POST' });
}

// (Press-and-hold confirmation for [data-hold] destructive actions is in panel.js.)

// ── Terminal tab ─────────────────────────────────────────────────────────
var _term = null;
var _termSock = null;

function _loadXterm(callback) {
    if (window.Terminal) {
        callback();
        return;
    }
    var link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = '/static/css/xterm.min.css';
    document.head.appendChild(link);
    var script = document.createElement('script');
    script.src = '/static/js/xterm.min.js';
    script.onload = callback;
    script.onerror = function () {
        document.getElementById('terminal-container').innerHTML =
            '<p class="text-danger" style="padding:8px 0;">xterm.js not found — run install.sh to download it.</p>';
    };
    document.head.appendChild(script);
}

function _initTerminal() {
    if (_term) return;
    _term = new Terminal({
        cursorBlink: true,
        fontSize: 14,
        fontFamily: '"Courier New", monospace',
        rows: 24,
        cols: 80,
    });
    _term.open(document.getElementById('terminal-container'));
    _term.onData(function (data) {
        if (_termSock) _termSock.emit('input', data);
    });
    if (!_termSock) {
        _termSock = splouchSocket('/ws/terminal');
        _termSock.on('output', function (data) {
            if (_term) _term.write(data);
        });
        _termSock.on('exit', function () {
            if (_term) {
                _term.write('\r\n\x1b[33m[Process exited]\x1b[0m\r\n');
            }
            document.getElementById('btn-term-stop').style.display = 'none';
        });
    }
}

// Ask before replacing a session that is still running something — an install
// halfway through its questions, a log being followed. A shell idle at its prompt
// is let go silently: nothing is lost but the scrollback.
function _termConfirmReplace(then) {
    fetch('/terminal_status')
        .then(function (r) {
            return r.json();
        })
        .then(function (st) {
            if (st.running && !st.idle_shell && !confirm(T.js_term_busy_confirm)) return;
            then();
        });
}

// Run `fn` once the terminal is drawn and its socket is up — output that arrives
// before then would be lost.
function _termReady(fn) {
    _loadXterm(function () {
        _initTerminal();
        if (_termSock.connected) fn();
        else _termSock.once('connect', fn);
    });
}

function _termStarted() {
    document.getElementById('btn-term-stop').style.display = '';
    _termSock.emit('resize', { rows: _term.rows, cols: _term.cols });
}

function _termError(d) {
    _term.write('\r\n\x1b[31mError: ' + (d.error || T.js_failed_to_start) + '\x1b[0m\r\n');
}

function termLaunch(cmdKey) {
    _termConfirmReplace(function () {
        _termReady(function () {
            fetch('/terminal_stop', { method: 'POST' })
                .then(function () {
                    if (_term) _term.clear();
                    return fetch('/terminal_start', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ cmd: cmdKey || 'bash' }),
                    });
                })
                .then(function (r) {
                    return r.json();
                })
                .then(function (d) {
                    if (d.ok) _termStarted();
                    else _termError(d);
                });
        });
    });
}

// Type one of the Commands card's lines into the shell — into the one already at
// its prompt, or a fresh one. The server refuses a busy terminal ('busy') unless
// told to replace it, so the question is asked only when it matters.
function termRun(key) {
    _termReady(function () {
        function send(replace) {
            fetch('/terminal_run', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ cmd: key, replace: replace }),
            })
                .then(function (r) {
                    return r.json();
                })
                .then(function (d) {
                    if (d.ok) {
                        _termStarted();
                        _term.focus();
                        document
                            .getElementById('terminal-container')
                            .scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    } else if (d.error === 'busy') {
                        if (confirm(T.js_term_busy_confirm)) {
                            if (_term) _term.clear();
                            send(true);
                        }
                    } else {
                        _termError(d);
                    }
                });
        }
        send(false);
    });
}

function termStop() {
    if (_term) _term.clear();
    fetch('/terminal_stop', { method: 'POST' }).then(function () {
        document.getElementById('btn-term-stop').style.display = 'none';
    });
}

// A session outlives the page (an install keeps going through a reload), so on
// opening the tab, reattach to one that is running instead of showing it stopped.
document.addEventListener('panel:tab-shown', function (e) {
    if (/** @type {CustomEvent} */ (e).detail.target !== '#tab-terminal') return;
    _loadXterm(function () {});
    fetch('/terminal_status')
        .then(function (r) {
            return r.json();
        })
        .then(function (st) {
            if (st.running) _termReady(_termStarted);
        });
});

// ── Logs tab ─────────────────────────────────────────────────────────────
var _logFollowTimer = null;

function loadLogs() {
    var source = document.getElementById('log-source').value;
    var out = document.getElementById('log-output');
    var status = document.getElementById('log-status');
    var wasAtBottom = out.scrollHeight - out.scrollTop <= out.clientHeight + 40;
    fetch('/logs_view?source=' + encodeURIComponent(source) + '&tail=1000')
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            if (!d.ok) {
                out.textContent = d.error || T.js_request_failed;
                status.textContent = '';
                return;
            }
            out.textContent = d.lines.length ? d.lines.join('\n') : T.js_logs_empty;
            if (wasAtBottom) out.scrollTop = out.scrollHeight;
            status.textContent = new Date().toLocaleTimeString();
        })
        .catch(function () {
            status.textContent = T.js_request_failed;
        });
}

function _stopLogFollow() {
    clearInterval(_logFollowTimer);
    _logFollowTimer = null;
}

function toggleLogFollow() {
    _stopLogFollow();
    if (document.getElementById('log-follow').checked) {
        loadLogs();
        _logFollowTimer = setInterval(loadLogs, 3000);
    }
}

document.addEventListener('panel:tab-shown', function (e) {
    if (/** @type {CustomEvent} */ (e).detail.target === '#tab-logs') {
        var out = document.getElementById('log-output');
        loadLogs();
        out.scrollTop = out.scrollHeight;
        toggleLogFollow();
    } else {
        _stopLogFollow();
    }
});

// ── Hardware tab ─────────────────────────────────────────────────────────
// Polled only while the tab is open; the history itself is sampled server-side
// (server/hardware.py), so a closed tab misses nothing.
var _hwTimer = null;

function _hwBytes(n) {
    return (n / 1073741824).toFixed(1) + ' GB';
}

function _hwDuration(sec) {
    var d = Math.floor(sec / 86400),
        h = Math.floor((sec % 86400) / 3600),
        m = Math.floor((sec % 3600) / 60);
    return (d ? d + 'd ' : '') + (d || h ? h + 'h ' : '') + m + 'min';
}

function _hwText(id, text) {
    document.getElementById(id).textContent = text;
}

// The last hour as a line: temperature on a band from 30 °C to whichever is
// higher of 85 °C (where the firmware throttles) and the hottest reading, with
// the 80 °C soft limit dashed and every under-voltage sample ticked in red below.
function _hwChart(history, every) {
    var svg = document.getElementById('hw-chart');
    var w = svg.clientWidth || 600,
        h = 140,
        pad = 4;
    var pts = history.filter(function (s) {
        return s[1] !== null;
    });
    var span = Math.max(3600, history.length ? history[0][0] + every : 0);
    var hi = Math.max(
        85,
        Math.max.apply(
            null,
            pts
                .map(function (s) {
                    return s[1];
                })
                .concat([0]),
        ),
    );
    var lo = 30;
    function x(ago) {
        return w - (ago / span) * w;
    }
    function y(temp) {
        return pad + (1 - (Math.min(Math.max(temp, lo), hi) - lo) / (hi - lo)) * (h - 2 * pad - 8);
    }
    var cs = getComputedStyle(document.body);
    var line = cs.getPropertyValue('--bs-primary') || '#0d6efd';
    var grid = cs.getPropertyValue('--bs-border-color') || '#888';
    var danger = cs.getPropertyValue('--bs-danger') || '#dc3545';
    var parts = [
        '<line x1="0" x2="' +
            w +
            '" y1="' +
            y(80) +
            '" y2="' +
            y(80) +
            '" stroke="' +
            grid +
            '" stroke-dasharray="4 4"/>',
        '<text x="2" y="' + (y(80) - 3) + '" font-size="10" fill="' + grid + '">80 °C</text>',
    ];
    if (pts.length > 1) {
        var d = pts
            .map(function (s, i) {
                return (i ? 'L' : 'M') + x(s[0]).toFixed(1) + ' ' + y(s[1]).toFixed(1);
            })
            .join(' ');
        parts.push('<path d="' + d + '" fill="none" stroke="' + line + '" stroke-width="2"/>');
    }
    history.forEach(function (s) {
        if (s[2])
            parts.push(
                '<rect x="' +
                    (x(s[0]) - 1).toFixed(1) +
                    '" y="' +
                    (h - 6) +
                    '" width="2" height="6" fill="' +
                    danger +
                    '"/>',
            );
    });
    svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
    svg.innerHTML = parts.join('');
    _hwText('hw-chart-from', '−' + Math.round(span / 60) + ' min');
}

function _hwRender(d) {
    document.getElementById('hw-unavailable').style.display = d.available ? 'none' : '';
    document.getElementById('hw-body').style.display = d.available ? '' : 'none';
    if (!d.available) return;
    var latest = d.latest || {};
    _hwText(
        'hw-temp',
        latest.temp !== null && latest.temp !== undefined ? latest.temp + ' °C' : '—',
    );
    _hwText(
        'hw-temp-range',
        d.temp_min !== null
            ? T.hw_min_max.replace('{min}', d.temp_min + ' °C').replace('{max}', d.temp_max + ' °C')
            : '',
    );
    _hwText('hw-freq', latest.freq_mhz ? latest.freq_mhz + ' MHz' : '—');
    var power = document.getElementById('hw-power');
    var uv = d.flags && d.flags.undervoltage;
    power.textContent = !uv ? '—' : uv.now ? T.hw_power_low : T.hw_power_ok;
    power.className =
        'fs-3 fw-semibold' +
        (uv && uv.now ? ' text-danger' : uv && uv.since_boot ? ' text-warning' : '');
    _hwText('hw-power-detail', uv && !uv.now && uv.since_boot ? T.hw_power_low_boot : '');
    ['undervoltage', 'freq_capped', 'throttled', 'soft_temp_limit'].forEach(function (k) {
        var f = d.flags && d.flags[k];
        [
            ['now', 'now'],
            ['boot', 'since_boot'],
        ].forEach(function (pair) {
            var cell = document.getElementById('hw-flag-' + k + '-' + pair[0]);
            var on = f && f[pair[1]];
            cell.textContent = !f ? '—' : on ? T.js_hw_yes : T.js_hw_no;
            cell.className = 'text-center' + (on ? ' text-danger fw-semibold' : '');
        });
    });
    _hwChart(d.history || [], d.sample_every || 5);
    _hwText('hw-model', d.model || '—');
    _hwText('hw-uptime', d.uptime !== null ? _hwDuration(d.uptime) : '—');
    _hwText('hw-load', d.load !== null ? d.load.toFixed(2) : '—');
    _hwText(
        'hw-memory',
        d.memory ? _hwBytes(d.memory.used) + ' / ' + _hwBytes(d.memory.total) : '—',
    );
    _hwText('hw-disk', d.disk ? _hwBytes(d.disk.used) + ' / ' + _hwBytes(d.disk.total) : '—');
}

function _hwLoad() {
    fetch('/hardware_status')
        .then(function (r) {
            return r.json();
        })
        .then(_hwRender)
        .catch(function () {});
}

document.addEventListener('panel:tab-shown', function (e) {
    clearInterval(_hwTimer);
    _hwTimer = null;
    if (/** @type {CustomEvent} */ (e).detail.target === '#tab-hardware') {
        _hwLoad();
        _hwTimer = setInterval(_hwLoad, 5000);
    }
});

// ── Debug tab ────────────────────────────────────────────────────────────
var _debugSock = null;

function _applyDebugState(enabled) {
    var btn = document.getElementById('btn-debug-toggle');
    btn.textContent = enabled ? T.js_disable : T.btn_enable;
    btn.className = 'btn btn-sm ' + (enabled ? 'btn-danger' : 'btn-secondary');
}

function debugToggle() {
    fetch('/debug_toggle', { method: 'POST' })
        .then(function (r) {
            return r.json();
        })
        .then(function (d) {
            _applyDebugState(d.enabled);
        });
}

function debugClear() {
    document.getElementById('debug-output').textContent = '';
}

function _applySerialStatus(d) {
    var badge = document.getElementById('serial-status-badge');
    if (!badge) return;
    var labels = {
        idle: '—',
        opening: T.js_opening,
        open: T.js_connected,
        error: T.js_error,
        manual: T.js_manual,
    };
    var variants = {
        idle: 'text-bg-secondary',
        opening: 'text-bg-warning',
        open: 'text-bg-success',
        error: 'text-bg-danger',
        manual: 'text-bg-info',
    };
    badge.textContent = labels[d.state] || d.state;
    badge.className = 'badge rounded-pill ' + (variants[d.state] || 'text-bg-secondary');
    if (d.msg) {
        var log = document.getElementById('serial-log');
        var ts = new Date().toLocaleTimeString();
        log.textContent += '[' + ts + '] ' + d.msg + '\n';
        log.scrollTop = log.scrollHeight;
    }
}

function _initDebugSock() {
    if (_debugSock) return;
    _debugSock = splouchSocket('/ws/settings');
    _debugSock.on('debug_line', function (d) {
        var out = document.getElementById('debug-output');
        var line = document.createElement('div');
        var hex = document.createElement('span');
        hex.textContent = d.hex;
        _statusColor(hex, 'muted');
        hex.style.marginRight = '16px';
        line.appendChild(hex);
        if (d.text) {
            var txt = document.createElement('span');
            txt.textContent = d.text;
            line.appendChild(txt);
        }
        out.appendChild(line);
        while (out.children.length > 200) out.removeChild(out.firstChild);
        out.scrollTop = out.scrollHeight;
    });
    _debugSock.on('serial_log', function (d) {
        _applySerialStatus(d);
    });
}

document.querySelectorAll('.app-nav .nav-link').forEach(function (link) {
    if (link.getAttribute('data-target') === '#tab-debug') {
        link.addEventListener('click', function () {
            _initDebugSock();
            fetch('/debug_status')
                .then(function (r) {
                    return r.json();
                })
                .then(function (d) {
                    _applyDebugState(d.enabled);
                });
            fetch('/serial_status')
                .then(function (r) {
                    return r.json();
                })
                .then(function (d) {
                    _applySerialStatus(d);
                });
        });
    }
});

// ── Cloud relay status ────────────────────────────────────────────────────
function _applyCloudStatus(d) {
    var text = document.getElementById('cloud-status-text');
    var btn = document.getElementById('btn-cloud-toggle');
    if (!d.url) {
        _statusColor(text, 'muted');
        text.textContent = T.js_not_configured;
        btn.textContent = T.net_connect;
        btn.disabled = true;
        _setHold(btn, false);
    } else if (d.connected) {
        _statusColor(text, 'ok');
        text.textContent = T.js_connected_c + d.url;
        btn.textContent = T.js_disconnect;
        btn.disabled = false;
        _setHold(btn, true, T.hold_disconnect);
    } else if (d.running) {
        _statusColor(text, 'warn');
        text.textContent = T.js_connecting;
        btn.textContent = T.js_disconnect;
        btn.disabled = false;
        _setHold(btn, true, T.hold_disconnect);
    } else {
        _statusColor(text, 'muted');
        text.textContent = T.js_disconnected;
        btn.textContent = T.net_connect;
        btn.disabled = false;
        _setHold(btn, false);
    }
    _applyCloudRegion(d);
    _applyCloudAttendance(d);
}

// The region the cloud assigned, read-only: the cloud administrator's call.
function _applyCloudRegion(d) {
    var el = document.getElementById('cloud-region');
    if (!el) return;
    el.textContent = (d.region && T['region_' + d.region]) || T.cloud_region_pending;
}

// Country names in the panel's language, from the browser's own table; a browser
// without it keeps the codes.
(function () {
    var sel = document.getElementById('cloud_country');
    // ES2020, newer than the lib these scripts are checked against.
    var DisplayNames = typeof Intl !== 'undefined' && /** @type {any} */ (Intl).DisplayNames;
    if (!sel || !DisplayNames) return;
    var names;
    try {
        names = new DisplayNames([document.documentElement.lang || 'en'], {
            type: 'region',
        });
    } catch (_e) {
        return;
    }
    sel.querySelectorAll('option[value]').forEach(function (opt) {
        if (opt.value) opt.textContent = names.of(opt.value) || opt.value;
    });
})();

// The state/province list: the chosen country's only, and no field for a country
// with none. Rebuilt rather than hidden: Safari shows hidden options.
(function () {
    var country = /** @type {HTMLSelectElement | null} */ (
        document.getElementById('cloud_country')
    );
    var prov = /** @type {HTMLSelectElement | null} */ (document.getElementById('cloud_province'));
    var field = document.getElementById('cloud_province_field');
    if (!country || !prov || !field) return;
    var all = Array.prototype.slice.call(prov.querySelectorAll('option[data-country]'));
    function sync(reset) {
        var keep = reset ? '' : prov.value;
        all.forEach(function (o) {
            o.remove();
        });
        var mine = all.filter(function (o) {
            return o.dataset.country === country.value;
        });
        mine.forEach(function (o) {
            prov.appendChild(o);
        });
        prov.value = keep;
        field.hidden = mine.length === 0;
    }
    sync(false);
    country.addEventListener('change', function () {
        sync(true);
    });
})();

// Attendance counts the cloud pushes back over the relay link. Only shown
// while connected; a muted note explains when the cloud admin has analytics
// turned off, so an empty panel is never mistaken for "zero visitors".
function _applyCloudAttendance(d) {
    var card = document.getElementById('cloud-attendance-card');
    var body = document.getElementById('cloud-attendance-body');
    if (!card || !body) return;
    if (!d.connected || !d.stats) {
        card.style.display = 'none';
        return;
    }
    card.style.display = '';
    if (!d.stats.enabled) {
        body.innerHTML =
            '<span class="text-body-secondary small">' + T.cloud_att_disabled + '</span>';
        return;
    }
    var c = d.stats.counts || {};
    var wins = [
        ['1h', T.cloud_att_1h],
        ['3h', T.cloud_att_3h],
        ['12h', T.cloud_att_12h],
        ['24h', T.cloud_att_24h],
        ['7d', T.cloud_att_7d],
        ['all', T.cloud_att_all],
    ];
    var html = '<div class="d-flex flex-wrap gap-4">';
    wins.forEach(function (w) {
        var n = c[w[0]];
        if (n == null) n = 0;
        html +=
            '<div class="text-center"><div class="fs-4 fw-bold lh-1">' +
            n +
            '</div><div class="text-body-secondary small">' +
            w[1] +
            '</div></div>';
    });
    html += '</div>';
    body.innerHTML = html;
}

function toggleCloud() {
    fetch('/cloud_toggle', { method: 'POST' })
        .then(function (r) {
            return r.json();
        })
        .then(_applyCloudStatus);
}

fetch('/cloud_status')
    .then(function (r) {
        return r.json();
    })
    .then(_applyCloudStatus);
setInterval(function () {
    fetch('/cloud_status')
        .then(function (r) {
            return r.json();
        })
        .then(_applyCloudStatus);
}, 2000);
