"""Outbound relay client — forwards scoreboard events to a cloud server.

Uses a plain WebSocket (the sync ``websocket-client`` library) instead of
Socket.IO. It runs a reconnect loop in its own daemon thread; scoreboard events
from the worker thread are forwarded via :func:`relay_emit`. Every message is a
JSON frame ``{"event", "data"}`` — the same contract the cloud relay speaks.
"""
import base64
import json
import os
import threading
import time

import state

_client    = None
_connected = False
_meet_id   = None   # cloud meet id assigned on 'registered', for diagnostics
_stats     = None   # latest attendance snapshot from the cloud, or None
_lock      = threading.Lock()
_stop      = threading.Event()
_thread    = None


def _ws_url(url):
    """Turn a cloud_relay_url (http(s)://host or ws(s)://host) into the /ws/relay endpoint."""
    url = url.strip().rstrip('/')
    if url.startswith('https://'):
        url = 'wss://' + url[len('https://'):]
    elif url.startswith('http://'):
        url = 'ws://' + url[len('http://'):]
    elif not url.startswith(('ws://', 'wss://')):
        url = 'wss://' + url
    return url + '/ws/relay'


def _send_raw(ws, event, data):
    ws.send(json.dumps({'event': event, 'data': data}))


# ── Meet metadata ──────────────────────────────────────────────────────────────

def _get_metadata():
    meet_info = state.meet.meet_info
    flat_labels = dict(state.load_locale(style=state.settings.get('cloud_label_style', 'short')))
    # Add UI strings the cloud templates need
    for key in ('waiting_results', 'no_upcoming', 'no_schedule'):
        val = state._mobile_strings().get(key)
        if val:
            flat_labels[key] = val

    meta = {
        'name':     state.settings.get('cloud_meet_title') or state.settings.get('meet_title') or meet_info.get('name', ''),
        'location': meet_info.get('city')  or state.settings.get('meet_location', ''),
        'sport':    state.settings.get('meet_sport', ''),
        'app_window_title': state.settings.get('app_window_title', ''),
        'meet_date': _last_session_date(),
        'meet_uid':  state.meet_uid(),
        'settings': {
            'num_lanes':            int(state.settings.get('num_lanes', 8)),
            'show_podium':          state.settings.get('show_podium', True),
            'show_name':            state.settings.get('show_name', True),
            'show_club':            state.settings.get('show_club', True),
            'show_delta':           state.settings.get('show_delta', True),
            'show_position':        state.settings.get('show_position', True),
            'show_lane_header':     state.settings.get('show_lane_header', True),
            'show_name_header':     state.settings.get('show_name_header', True),
            'show_club_header':     state.settings.get('show_club_header', True),
            'show_time_header':     state.settings.get('show_time_header', True),
            'show_delta_header':    state.settings.get('show_delta_header', True),
            'show_position_header': state.settings.get('show_position_header', True),
            'theme_colors':         {**state.DEFAULT_THEME_COLORS,
                                     **state.settings.get('theme_colors', {})},
            'theme_fonts':          {**state.DEFAULT_THEME_FONTS,
                                     **state.settings.get('theme_fonts', {})},
            'locale':               state.settings.get('locale', 'en'),
            'labels':               flat_labels,
            # Which of the two styles the phone labels above were resolved in, so a
            # client offering the choice knows where to start (api.md §5.4). The
            # kiosk's `label_style` is a separate setting and stays out of this.
            'label_style':          state.settings.get('cloud_label_style', 'short'),
            # Only what this Pi's custom locale files change. The bundled table is
            # the same for every meet and travels as GET /i18n/{lang}; these files
            # exist on this box alone, so the diff has to ride along.
            'label_overrides':      state.label_overrides(),
        },
    }
    icon = _icon_b64()
    if icon:
        meta['settings']['home_icon_b64'] = icon
    picker_img = _picker_image_b64()
    if picker_img:
        meta['settings']['picker_image_b64'] = picker_img
    return meta


def _last_session_date():
    """Latest session date from the loaded LENEX ('YYYY-MM-DD'), or '' if unknown.

    LENEX dates are ISO-formatted, so a lexical max is also the chronological max.
    The cloud uses this to expire a retained meet the day after its final session.
    """
    dates = [s.get('date', '') for s in state.meet.meet_info.get('sessions', [])]
    dates = [d for d in dates if d]
    return max(dates) if dates else ''


def _icon_b64():
    try:
        if os.path.exists(state.HOME_ICON_PATH):
            with open(state.HOME_ICON_PATH, 'rb') as f:
                return base64.b64encode(f.read()).decode()
    except Exception:
        pass
    return None


def _picker_image_b64():
    try:
        active = state.settings.get('active_picker_image', '')
        if active:
            path = os.path.join(state.PICKER_DIR, active)
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    return base64.b64encode(f.read()).decode()
    except Exception:
        pass
    return None


# ── Public API ─────────────────────────────────────────────────────────────────

def relay_emit(event, data):
    """Forward an event to the cloud relay. Non-blocking; silently drops if not connected."""
    with _lock:
        c, ok = _client, _connected
    if c and ok:
        try:
            _send_raw(c, event, data)
        except Exception:
            pass


def update_metadata():
    """Re-send registration metadata to the cloud (call after settings change)."""
    with _lock:
        c, ok = _client, _connected
    if c and ok:
        key = state.settings.get('cloud_relay_key', '').strip()
        try:
            _send_raw(c, 'register', {**_get_metadata(), 'key': key})
        except Exception:
            pass


def send_schedule(client=None):
    """Send the current schedule snapshot to the cloud. Call after a meet file is loaded.

    Pass `client` directly when calling from inside a connect handler, because
    _client is not yet assigned at that point and relay_emit would silently drop.
    """
    from meet_data import _build_meet_data
    m = state.meet
    if not (m.start_list or m.event_info.events):
        return
    try:
        md = _build_meet_data()
        data = {
            'events': [[ev, sorted(heats)] for ev, heats in md['events_grouped']],
            'names':  {str(k): v for k, v in md['event_names'].items()},
            # Language-neutral parts beside the composed names, so the cloud can
            # serve a schedule in a language this Pi's meet is not run in
            # (docs/app.md `T-04`). The cloud never sees the raw name otherwise.
            'name_parts': {str(k): v for k, v in md.get('event_name_parts', {}).items()},
            'times':  {str(k): {str(h): t for h, t in v.items()}
                       for k, v in md['heat_times'].items()},
            'start_list': _serialise_start_list(md['start_list']),
        }
        if client is not None:
            _send_raw(client, 'schedule_snapshot', data)
        else:
            relay_emit('schedule_snapshot', data)
    except Exception:
        pass


def _serialise_start_list(sl):
    out = {}
    for ev, heats in sl.items():
        out[str(ev)] = {}
        for ht, lanes in heats.items():
            out[str(ev)][str(ht)] = {}
            for lane, entry in lanes.items():
                out[str(ev)][str(ht)][str(lane)] = {
                    'name':      entry.get('name', ''),
                    'club':      entry.get('club', ''),
                    'seed_time': entry.get('seed_time', ''),
                    'swimmers':  entry.get('swimmers', []),
                }
    return out


# ── Background thread ──────────────────────────────────────────────────────────

_PING_EVERY = 20   # recv() timeout: heartbeat cadence when the link is idle
_STALE      = 50   # no inbound (incl. pong) for this long => dead link, reconnect
_STATS_EVERY = 30  # how often to ask the cloud for its attendance counts


def _run():
    global _client, _connected, _meet_id, _stats
    from websocket import WebSocketTimeoutException, create_connection

    while not _stop.is_set():
        url = state.settings.get('cloud_relay_url', '').strip()
        key = state.settings.get('cloud_relay_key', '').strip()
        if not url or not key:
            _stop.wait(10)
            continue

        ws = None
        try:
            ws = create_connection(_ws_url(url), timeout=15)
            ws.settimeout(_PING_EVERY)       # recv() unblocks so we can heartbeat
            ws.enable_multithreading = True  # guard concurrent send() from worker threads

            _send_raw(ws, 'register', {**_get_metadata(), 'key': key})
            with _lock:
                _client    = ws
                _connected = True
            send_schedule(client=ws)
            if state._last_results_snapshot:
                _send_raw(ws, 'results_snapshot', state._last_results_snapshot)
            print('[relay] connected to cloud', flush=True)

            # Receive loop: the relay is mostly outbound, but recv() keeps the
            # thread alive, detects a server-side close, and handles 'rejected'.
            # A heartbeat detects a silently-dead cloud link (mobile/proxy half-open)
            # and reconnects instead of blocking forever with no updates flowing.
            last_rx    = time.time()
            last_stats = 0.0
            while not _stop.is_set():
                # Ask the cloud for fresh attendance counts on a slow cadence so
                # the Settings → Cloud tab can show them. The cloud answers only
                # for this relay's own meet, so there's nothing to spoof.
                if time.time() - last_stats >= _STATS_EVERY:
                    last_stats = time.time()
                    try:
                        _send_raw(ws, 'get_stats', {})
                    except Exception:
                        break
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    if time.time() - last_rx > _STALE:
                        print('[relay] no response from cloud — reconnecting', flush=True)
                        break
                    try:
                        _send_raw(ws, 'ping', {})   # cloud replies 'pong'
                    except Exception:
                        break                        # send failed => link is dead
                    continue
                if not raw:
                    break
                last_rx = time.time()
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                ev = obj.get('event')
                if ev == 'pong':
                    continue
                elif ev == 'registered':
                    _meet_id = (obj.get('data') or {}).get('meet_id')
                elif ev == 'stats':
                    with _lock:
                        _stats = obj.get('data') or {}
                elif ev == 'rejected':
                    print(f'[relay] rejected: {obj.get("data", {}).get("reason")}', flush=True)
                    break
        except Exception as e:
            print(f'[relay] error: {e}', flush=True)
        finally:
            with _lock:
                _connected = False
                _stats     = None   # numbers are stale once the link drops
                if _client is ws:
                    _client = None
            try:
                if ws:
                    ws.close()
            except Exception:
                pass
            print('[relay] disconnected from cloud', flush=True)

        if not _stop.is_set():
            _stop.wait(5)


def status():
    with _lock:
        connected = _connected
        stats     = _stats
    running = _thread is not None and _thread.is_alive()
    return {
        'connected': connected,
        'running':   running,
        'url':       state.settings.get('cloud_relay_url', '').strip(),
        'stats':     stats,
    }


def start():
    global _thread, _stop
    _stop = threading.Event()
    _thread = threading.Thread(target=_run, daemon=True, name='cloud-relay')
    _thread.start()


def stop():
    _stop.set()
    with _lock:
        c = _client
    if c:
        try:
            c.close()   # unblocks the recv loop so the thread exits promptly
        except Exception:
            pass
