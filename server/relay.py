"""Outbound relay client — forwards scoreboard events to a cloud server.

Uses a plain WebSocket (the sync ``websocket-client`` library) instead of
Socket.IO. It runs a reconnect loop in its own daemon thread; scoreboard events
from the worker thread are forwarded via :func:`relay_emit`. Every message is a
JSON frame ``{"event", "data"}`` — the same contract the cloud relay speaks.

Before connecting, the Pi asks the cloud where to publish (``POST /api/assign``,
docs/api.md §5.12): the answer is the worker socket for this meet and a ticket the
worker checks on ``register``. The assignment is kept and reused — across a
dropped link, and while the cloud's control plane cannot be reached, for as long as
the ticket lasts — and asked for again when the meet, the key or the server
changes, when a worker refuses the ticket, or after repeated failed connects.
"""

import base64
import contextlib
import datetime
import json
import os
import threading
import time
import urllib.error
import urllib.request

import state

_client = None
_connected = False
_meet_id = None  # cloud meet id assigned on 'registered', for diagnostics
_stats = None  # latest attendance snapshot from the cloud, or None
_lock = threading.Lock()
_stop = threading.Event()
_thread = None
# Where this meet publishes, from `/api/assign`: `for` (server URL, key, meet uid),
# meet_id, relay_url, ticket, region, until (epoch seconds the ticket lasts to).
_assignment = None
_reassign = threading.Event()  # set: ask `/api/assign` again before connecting

_ASSIGN_TIMEOUT = 10
_FAILS_BEFORE_REASSIGN = 3  # failed connects to one worker before asking again


def _api_url(url):
    """Turn a cloud_relay_url (http(s)://host or ws(s)://host) into /api/assign."""
    url = url.strip().rstrip("/")
    if url.startswith("wss://"):
        url = "https://" + url[len("wss://") :]
    elif url.startswith("ws://"):
        url = "http://" + url[len("ws://") :]
    elif not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url + "/api/assign"


class Refused(Exception):
    """The cloud refused the key — no point asking again soon."""


def _assign(url, key, meet_uid):
    """Ask the cloud where to publish this meet. Raises Refused for a bad key."""
    body = json.dumps({"key": key, "meet_uid": meet_uid}).encode()
    req = urllib.request.Request(_api_url(url), data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=_ASSIGN_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 403:
            try:
                reason = json.loads(e.read()).get("reason", "")
            except Exception:
                reason = ""
            raise Refused(reason or "key refused") from e
        raise
    return {
        "for": (url, key, meet_uid),
        "meet_id": data["meet_id"],
        "relay_url": data["relay_url"],
        "ticket": data["ticket"],
        "region": data.get("region", ""),
        "until": time.time() + int(data.get("expires_in", 0)),
    }


def _current_assignment(url, key):
    """The assignment to connect with, asking for a fresh one when needed, or None.

    While the control plane cannot be reached, a kept assignment for the same meet
    is used as long as its ticket lasts: the worker admits a valid ticket on its own.
    """
    global _assignment
    wanted = (url, key, state.meet_uid())
    kept = _assignment if _assignment and _assignment["for"] == wanted else None
    now = time.time()
    if kept and not _reassign.is_set() and kept["until"] - now > 3600:
        return kept
    try:
        fresh = _assign(*wanted)
    except Refused:
        raise
    except Exception as e:
        print(f"[relay] could not reach {_api_url(url)}: {e}", flush=True)
        return kept if kept and kept["until"] > now else None
    _reassign.clear()
    _assignment = fresh
    return fresh


def _organizer_location():
    """Where the organizer says it is based (the Cloud tab), or None when unset."""
    country = state.settings.get("cloud_country", "")
    province = state.settings.get("cloud_province", "")
    if not country and not province:
        return None
    return {"country": country, "province": province}


def _register_payload(key, assignment):
    return {
        **_get_metadata(),
        "key": key,
        "ticket": assignment["ticket"],
        "organizer_location": _organizer_location(),
    }


def _send_raw(ws, event, data):
    ws.send(json.dumps({"event": event, "data": data}))


# ── Meet metadata ──────────────────────────────────────────────────────────────


def _get_metadata():
    meet_info = state.meet.meet_info
    flat_labels = dict(
        state.load_locale(style=state.settings.get("cloud_label_style", "short"))
    )
    # Add UI strings the cloud templates need
    for key in ("waiting_results", "no_upcoming", "no_schedule"):
        val = state._mobile_strings().get(key)
        if val:
            flat_labels[key] = val

    meta = {
        "name": state.settings.get("cloud_meet_title")
        or state.settings.get("meet_title")
        or meet_info.get("name", ""),
        "location": meet_info.get("city") or state.settings.get("meet_location", ""),
        "sport": state.settings.get("meet_sport", ""),
        "app_window_title": state.settings.get("app_window_title", ""),
        "meet_date": last_session_date(),
        # docs/app.md `P-01`: a meet past its dates, kept on the picker a while by
        # its operator. The cloud holds it to three days past the last session.
        "keep_listed_until": _keep_listed_iso(),
        # Every session day, and this Pi's UTC offset: how the cloud tells a meet
        # in progress from a Pi plugged in ahead of it, which an update may not
        # wait for (docs/architecture/scaling.md).
        "session_dates": _session_dates(),
        "utc_offset_minutes": _utc_offset_minutes(),
        "meet_uid": state.meet_uid(),
        "settings": {
            "num_lanes": int(state.settings.get("num_lanes", 8)),
            "show_podium": state.settings.get("show_podium", True),
            "show_name": state.settings.get("show_name", True),
            "show_club": state.settings.get("show_club", True),
            "show_delta": state.settings.get("show_delta", True),
            "show_position": state.settings.get("show_position", True),
            "show_laps": state.settings.get("show_laps", False),
            "lap_direction": state.settings.get("lap_direction", "up"),
            "show_lane_header": state.settings.get("show_lane_header", True),
            "show_name_header": state.settings.get("show_name_header", True),
            "show_club_header": state.settings.get("show_club_header", True),
            "show_time_header": state.settings.get("show_time_header", True),
            "show_delta_header": state.settings.get("show_delta_header", True),
            "show_position_header": state.settings.get("show_position_header", True),
            "theme_colors": {
                **state.DEFAULT_THEME_COLORS,
                **state.settings.get("theme_colors", {}),
            },
            "theme_fonts": {
                **state.DEFAULT_THEME_FONTS,
                **state.settings.get("theme_fonts", {}),
            },
            "locale": state.settings.get("locale", "en"),
            "labels": flat_labels,
            # Which of the two styles the phone labels above were resolved in, so a
            # client offering the choice knows where to start (api.md §5.4). The
            # kiosk's `label_style` is a separate setting and stays out of this.
            "label_style": state.settings.get("cloud_label_style", "short"),
            # The console this meet is run on, and whether it times anything
            # (api.md §5.4). A meet driven by hand never produces a result, so a
            # phone hides its Results tab instead of waiting all meet for one.
            "console": state.console_state(),
        },
    }
    icon = _icon_b64()
    if icon:
        meta["settings"]["home_icon_b64"] = icon
    picker_img = _picker_image_b64()
    if picker_img:
        meta["settings"]["picker_image_b64"] = picker_img
    return meta


def _session_dates():
    """Every session date in the loaded LENEX, sorted and unique."""
    dates = {s.get("date", "") for s in state.meet.meet_info.get("sessions", [])}
    return sorted(d for d in dates if d)


def _utc_offset_minutes():
    """This Pi's offset from UTC right now, in minutes (DST included)."""
    offset = datetime.datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else None


# How long past the meet's end its operator may keep it on the cloud's picker
# (`keep_listed_until`); the cloud holds it to the same.
KEEP_LISTED_HOURS = 72


def meet_end():
    """When the loaded meet ends, local time: its last session's `endtime`, else
    the end of that day. None without session dates (a Hytek CSV)."""
    last = last_session_date()
    try:
        day = datetime.date.fromisoformat(last)
    except ValueError:
        return None
    ends = []
    for s in state.meet.meet_info.get("sessions", []):
        if s.get("date") == last:
            with contextlib.suppress(ValueError):
                ends.append(datetime.time.fromisoformat(s.get("endtime") or ""))
    if ends:
        return datetime.datetime.combine(day, max(ends))
    return datetime.datetime.combine(
        day + datetime.timedelta(days=1), datetime.time.min
    )


def meet_over(today=None):
    """Whether the loaded meet's last session day is behind it: the cloud no
    longer lists it (docs/app.md `P-01`). A meet is never over on its last day."""
    last = last_session_date()
    today = today or datetime.date.today()
    return bool(last) and last < today.isoformat()


def keep_listed_deadline():
    """The latest the operator may keep the meet listed: `KEEP_LISTED_HOURS` past
    its end. None without dates."""
    end = meet_end()
    return end + datetime.timedelta(hours=KEEP_LISTED_HOURS) if end else None


def can_keep_listed(now=None):
    """Whether *Keep listing* is offered: the meet is over, not yet kept, and still
    within `KEEP_LISTED_HOURS` of its end."""
    deadline = keep_listed_deadline()
    now = now or datetime.datetime.now()
    return meet_over() and not keep_listed_until() and bool(deadline) and now < deadline


def keep_listed_until(now=None):
    """Until when the operator keeps this past meet listed (local, naive), or None.
    Held for this meet only: loading another one drops it."""
    if (
        not state.meet_uid()
        or state.settings.get("cloud_keep_listed") != state.meet_uid()
    ):
        return None
    deadline = keep_listed_deadline()
    now = now or datetime.datetime.now()
    return deadline if deadline and now < deadline else None


def _keep_listed_iso():
    until = keep_listed_until()
    return until.astimezone(datetime.UTC).isoformat() if until else None


def last_session_date():
    """Latest session date from the loaded LENEX ('YYYY-MM-DD'), or '' if unknown.

    LENEX dates are ISO-formatted, so a lexical max is also the chronological max.
    The cloud uses this to expire a retained meet the day after its final session.
    """
    dates = [s.get("date", "") for s in state.meet.meet_info.get("sessions", [])]
    dates = [d for d in dates if d]
    return max(dates) if dates else ""


def _icon_b64():
    try:
        if os.path.exists(state.HOME_ICON_PATH):
            with open(state.HOME_ICON_PATH, "rb") as f:
                return base64.b64encode(f.read()).decode()
    except OSError:
        pass
    return None


def _picker_image_b64():
    try:
        active = state.settings.get("active_picker_image", "")
        if active:
            path = os.path.join(state.PICKER_DIR, active)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    return base64.b64encode(f.read()).decode()
    except OSError:
        pass
    return None


# ── Public API ─────────────────────────────────────────────────────────────────


def _local_only():
    """True while a test session is running that must not reach the cloud.

    The relay is stopped outright for the duration (see `worker.end_test_session`),
    so in the ordinary case there is no socket to send on and this changes nothing.
    It is here for the window that stopping cannot close: the relay thread can be
    mid-reconnect when a test starts, and `_run` sends a registration, a schedule
    and the last results snapshot the instant it gets a socket. A test meet or a
    replay is never relayed, whichever of the three flags says so first.
    """
    return (
        state._test_local_only
        or state._test_session is not None
        or state._test_meet_active
    )


def relay_emit(event, data):
    """Forward an event to the cloud relay. Non-blocking; silently drops if not connected."""
    if _local_only():
        return
    with _lock:
        c, ok = _client, _connected
    if c and ok:
        with contextlib.suppress(Exception):
            _send_raw(c, event, data)


def update_metadata():
    """Re-send registration metadata to the cloud (call after settings change).

    A different meet, key or server needs a different assignment, so the link is
    dropped instead and the thread reconnects with a fresh one.
    """
    if _local_only():
        return
    with _lock:
        c, ok, a = _client, _connected, _assignment
    if not (c and ok and a):
        return
    url = state.settings.get("cloud_relay_url", "").strip()
    key = state.settings.get("cloud_relay_key", "").strip()
    if a["for"] != (url, key, state.meet_uid()):
        _reassign.set()
        with contextlib.suppress(Exception):
            c.close()
        return
    with contextlib.suppress(Exception):
        _send_raw(c, "register", _register_payload(key, a))


def send_schedule(client=None, clear=False):
    """Send the current schedule snapshot to the cloud. Call after a meet file is loaded.

    Pass `client` directly when calling from inside a connect handler, because
    _client is not yet assigned at that point and relay_emit would silently drop.

    With no start list nothing is sent, unless `clear`: the start list was taken
    away (a test ending with no meet to go back to, a meet unloaded) and the cloud
    must stop serving the old one. Not on connect — at boot the relay starts before
    the last meet file has been re-read.
    """
    if _local_only():
        return
    from meet_data import _build_meet_data

    m = state.meet
    if not (m.start_list or m.event_info.events):
        if clear:
            _send_schedule_data(client, _EMPTY_SCHEDULE)
        return
    try:
        md = _build_meet_data()
        data = {
            "events": [[ev, sorted(heats)] for ev, heats in md["events_grouped"]],
            "names": {str(k): v for k, v in md["event_names"].items()},
            # Language-neutral parts beside the composed names, so the cloud can
            # serve a schedule in a language this Pi's meet is not run in
            # (docs/app.md `T-04`). The cloud never sees the raw name otherwise.
            "name_parts": {
                str(k): v for k, v in md.get("event_name_parts", {}).items()
            },
            "times": {
                str(k): {str(h): t for h, t in v.items()}
                for k, v in md["heat_times"].items()
            },
            # The day each event is swum, from its Lenex session: `times` are times
            # of day, and the cloud's heat notifications need the date to place
            # them (docs/app.md `N-05`). Absent for an event no session names.
            "dates": _event_dates(md.get("meet_info", {})),
            "start_list": _serialise_start_list(md["start_list"]),
            # Meet Manager's official results (docs/app.md `S-22`). Console times
            # are not sent: the cloud keeps its own from `results_snapshot`.
            "results": {
                str(ev): {
                    str(ht): {str(lane): r for lane, r in lanes.items()}
                    for ht, lanes in heats.items()
                }
                for ev, heats in md.get("results", {}).items()
            },
        }
        _send_schedule_data(client, data)
    except Exception as e:
        # Broad on purpose — the relay must outlive a bad meet file — but not
        # silent: this is our own code, and a failure here means the cloud never
        # gets a schedule.
        print(f"[relay] schedule snapshot failed: {e!r}", flush=True)


# What `cloud_server._build_heats_json` reads as a meet with no heats.
_EMPTY_SCHEDULE = {
    "events": [],
    "names": {},
    "name_parts": {},
    "times": {},
    "dates": {},
    "start_list": {},
    "results": {},
}


def _event_dates(meet_info):
    """`{"<event>": "YYYY-MM-DD"}` from the Lenex sessions. An event a session
    names twice keeps its first day; a session without a date gives none."""
    out = {}
    for s in meet_info.get("sessions", []):
        if not s.get("date"):
            continue
        for ev in s.get("events", []):
            out.setdefault(str(ev), s["date"])
    return out


def _send_schedule_data(client, data):
    if client is not None:
        _send_raw(client, "schedule_snapshot", data)
    else:
        relay_emit("schedule_snapshot", data)


def _serialise_start_list(sl):
    out = {}
    for ev, heats in sl.items():
        out[str(ev)] = {}
        for ht, lanes in heats.items():
            out[str(ev)][str(ht)] = {}
            for lane, entry in lanes.items():
                out[str(ev)][str(ht)][str(lane)] = {
                    "name": entry.get("name", ""),
                    "club": entry.get("club", ""),
                    "seed_time": entry.get("seed_time", ""),
                    "swimmers": entry.get("swimmers", []),
                }
    return out


# ── Background thread ──────────────────────────────────────────────────────────

_PING_EVERY = 20  # recv() timeout: heartbeat cadence when the link is idle
_STALE = 50  # no inbound (incl. pong) for this long => dead link, reconnect
_STATS_EVERY = 30  # how often to ask the cloud for its attendance counts


def _run(stop=None):
    """The relay thread. `stop` is this thread's own event, never re-read from the
    module: `start()` replaces `_stop`, so a thread still connecting when it was
    stopped and restarted used to read the *new*, unset event and carry on — two
    threads, two sockets, the meet registered twice."""
    global _client, _connected, _meet_id, _stats
    stop = stop or _stop
    from websocket import WebSocketTimeoutException, create_connection

    fails = 0
    while not stop.is_set():
        url = state.settings.get("cloud_relay_url", "").strip()
        key = state.settings.get("cloud_relay_key", "").strip()
        if not url or not key or _local_only():
            # `_local_only` is belt and braces: a local-only test stops this thread
            # outright, and a thread already inside `create_connection` would
            # otherwise register the meet and push a schedule on the way out.
            stop.wait(10)
            continue

        try:
            assignment = _current_assignment(url, key)
        except Refused as e:
            print(f"[relay] rejected: {e}", flush=True)
            stop.wait(60)
            continue
        if assignment is None:
            stop.wait(10)
            continue

        ws = None
        try:
            try:
                ws = create_connection(assignment["relay_url"], timeout=15)
            except Exception:
                fails += 1
                if fails >= _FAILS_BEFORE_REASSIGN:
                    _reassign.set()  # that worker may be gone; ask where now
                    fails = 0
                raise
            fails = 0
            ws.settimeout(_PING_EVERY)  # recv() unblocks so we can heartbeat
            ws.enable_multithreading = (
                True  # guard concurrent send() from worker threads
            )

            _send_raw(ws, "register", _register_payload(key, assignment))
            with _lock:
                _client = ws
                _connected = True
            send_schedule(client=ws)
            # The cloud's join replay is only what it has merged since this
            # register: after a dropped link it starts empty, and a late joiner got
            # finals with no header or names until the next heat. The board cache
            # holds every published frame, clock already stripped (state.record_board).
            if state.board:
                _send_raw(ws, "update_scoreboard", dict(state.board))
            if state._last_results_snapshot:
                _send_raw(ws, "results_snapshot", state._last_results_snapshot)
            print("[relay] connected to cloud", flush=True)

            # Receive loop: the relay is mostly outbound, but recv() keeps the
            # thread alive, detects a server-side close, and handles 'rejected'.
            # A heartbeat detects a silently-dead cloud link (mobile/proxy half-open)
            # and reconnects instead of blocking forever with no updates flowing.
            last_rx = time.time()
            last_stats = 0.0
            while not stop.is_set():
                # Ask the cloud for fresh attendance counts on a slow cadence so
                # the Settings → Cloud tab can show them. The cloud answers only
                # for this relay's own meet, so there's nothing to spoof.
                if time.time() - last_stats >= _STATS_EVERY:
                    last_stats = time.time()
                    try:
                        _send_raw(ws, "get_stats", {})
                    except Exception:
                        break
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    if time.time() - last_rx > _STALE:
                        print(
                            "[relay] no response from cloud — reconnecting", flush=True
                        )
                        break
                    try:
                        _send_raw(ws, "ping", {})  # cloud replies 'pong'
                    except Exception:
                        break  # send failed => link is dead
                    continue
                if not raw:
                    break
                last_rx = time.time()
                try:
                    obj = json.loads(raw)
                except ValueError:  # malformed frame
                    continue
                ev = obj.get("event")
                if ev == "pong":
                    continue
                if ev == "registered":
                    _meet_id = (obj.get("data") or {}).get("meet_id")
                elif ev == "stats":
                    with _lock:
                        _stats = obj.get("data") or {}
                elif ev == "rejected":
                    data = obj.get("data") or {}
                    print(f"[relay] rejected: {data.get('reason')}", flush=True)
                    if data.get("reassign"):
                        _reassign.set()  # the worker wants a fresh ticket
                    break
        except Exception as e:
            print(f"[relay] error: {e}", flush=True)
        finally:
            with _lock:
                _connected = False
                _stats = None  # numbers are stale once the link drops
                if _client is ws:
                    _client = None
            try:
                if ws:
                    ws.close()
            except Exception:
                pass
            print("[relay] disconnected from cloud", flush=True)

        if not stop.is_set():
            # A reassignment (new meet, refused ticket) reconnects promptly.
            stop.wait(1 if _reassign.is_set() else 5)


def status():
    with _lock:
        connected = _connected
        stats = _stats
    running = _thread is not None and _thread.is_alive()
    return {
        "connected": connected,
        "running": running,
        "url": state.settings.get("cloud_relay_url", "").strip(),
        "stats": stats,
        # The region the cloud put this organizer in — the admin's call, shown
        # read-only in the Cloud tab.
        "region": (_assignment or {}).get("region", ""),
    }


def start():
    global _thread, _stop
    _stop = threading.Event()
    _thread = threading.Thread(
        target=_run, args=(_stop,), daemon=True, name="cloud-relay"
    )
    _thread.start()


def stop():
    _stop.set()
    with _lock:
        c = _client
    if c:
        # Closing unblocks the recv loop, so the thread exits promptly.
        with contextlib.suppress(Exception):
            c.close()
