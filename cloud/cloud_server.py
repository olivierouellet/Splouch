"""Splouch relay worker: a meet's live board, results and schedule.

The half of the relay that carries frames (docs/architecture/scaling.md). Pis
connect on ``/ws/relay``; attendees join a meet on ``/ws/scoreboard``,
``/ws/results`` and ``/ws/schedule``, and load its pages under ``/mobile``. Each
WebSocket path and each per-meet room is a channel keyed
``<namespace>:<meet_id>``; messages are JSON frames ``{"event", "data"}``.

A worker owns nothing durable. Live meets are in memory (``cloud_store``); keys,
meet ids, a retained meet's record and the attendance counts belong to the control
plane (``cloud_control``), reached through ``cloud_node``.
"""

import asyncio
import base64
import datetime
import json
import os
import time
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

# The `cloud_` prefix is not decoration: `server/` and `cloud/` are both flat on
# sys.path when the suite runs, so a plain `bus.py` here would shadow the Pi's —
# which is exactly why `cloud_server.py` is named that way too.
import cloud_bus
import cloud_i18n
import cloud_node
import cloud_ticket
from cloud_bus import manager
from cloud_paths import _HERE, DATA_DIR, STATIC_DIR
from cloud_store import _lock, _meets, _relay_sids, _retire_mem, meet_for
from cloud_web import (
    _client_labels,
    _client_lang,
    _client_palette,
    _client_style,
    _remember_prefs,
    render,
)
from splouch_i18n import DEFAULT_THEME_FONTS as _DEFAULT_FONTS

_ch = cloud_bus.ch
_strings = cloud_i18n.strings
_panel_strings = cloud_i18n.panel_strings
_i18n_bundle = cloud_i18n.i18n_bundle

# How often a running race clock is re-based on the attendees' devices. They tick
# it themselves in between, so this is a correction rate, not a frame rate: it
# bounds drift and how long a phone joining mid-heat waits for a clock.
_CLOCK_SYNC_SECS = 2.0


def _heartbeat_snapshot():
    """The meets held here and each one's attendees — phones on its board — for the
    control plane to balance new meets on."""
    with _lock:
        ids = list(_meets)
    return ids, {m: len(manager.channels.get(_ch("scoreboard", m), ())) for m in ids}


@asynccontextmanager
async def lifespan(app):
    os.makedirs(DATA_DIR, exist_ok=True)
    tasks = [
        asyncio.create_task(cloud_node.heartbeat_loop(_heartbeat_snapshot)),
        asyncio.create_task(cloud_node.analytics_flush_loop()),
    ]
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        # Send anything still queued, without letting a failed send error shutdown.
        with suppress(Exception):
            await run_in_threadpool(cloud_node.flush_analytics)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC_DIR, check_dir=False), name="static")


@app.get("/mobile", tags=["Public"])
def route_mobile(request: Request):
    meet_id = request.query_params.get("meet", "")
    meet = meet_for(meet_id)
    if not meet:
        return RedirectResponse("/", status_code=303)
    return _remember_prefs(
        request,
        render(
            request,
            "mobile.html",
            meet_id=meet_id,
            app_title=(meet.get("app_window_title") or meet["name"] or "Splouch"),
            t=_strings(_client_lang(request, meet), "mobile"),
            lang=_client_lang(request, meet),
            # No Results tab for a meet run by hand: nothing will ever fill it
            # (docs/app.md `A-11`). True for a relay too old to say, which is
            # what every relay before this said by having a console at all.
            show_results=meet.get("settings", {}).get("console", {}).get("timed", True),
            # Passed down to the tab iframes so one choice covers all three.
            ui_style=_client_style(request, meet),
            **_client_palette(request),
        ),
    )


@app.get("/mobile/live", tags=["Public"])
def route_live(request: Request):
    meet_id = request.query_params.get("meet", "")
    meet = meet_for(meet_id)
    if not meet:
        return render(request, "offline.html")
    s = meet.get("settings", {})
    return render(
        request,
        "live-mobile.html",
        meet_id=meet_id,
        num_lanes=s.get("num_lanes", 8),
        show_lane_header=s.get("show_lane_header", True),
        show_name_header=s.get("show_name_header", True),
        show_club_header=s.get("show_club_header", True),
        show_time_header=s.get("show_time_header", True),
        show_delta_header=s.get("show_delta_header", True),
        show_position_header=s.get("show_position_header", True),
        show_name=s.get("show_name", True),
        show_club=s.get("show_club", True),
        show_delta=s.get("show_delta", True),
        show_position=s.get("show_position", True),
        # Live-board only — the Results tab has no running lanes to count lengths
        # for, so `results.html` does not take this.
        show_laps=s.get("show_laps", False),
        lap_direction=s.get("lap_direction", "up"),
        # The reader's palette, not the meet's (docs/app.md `P-15`). Matches
        # route_results and route_schedule.
        **_client_palette(request),
        theme_fonts={**_DEFAULT_FONTS, **s.get("theme_fonts", {})},
        labels=_client_labels(
            meet, _client_lang(request, meet), _client_style(request, meet)
        ),
        # EVENT / HEAT inline, on a short window (scoreboard_base.html).
        short_labels=_client_labels(meet, _client_lang(request, meet), "short"),
        # The vocabulary `event_name_parts` composes against, in the language the
        # page is rendered in (docs/app.md `T-11`).
        event_vocab=_strings(_client_lang(request, meet), "event_name"),
        lang=_client_lang(request, meet),
    )


@app.get("/mobile/results", tags=["Public"])
def route_results(request: Request):
    meet_id = request.query_params.get("meet", "")
    meet = meet_for(meet_id)
    if not meet:
        return render(request, "offline.html")
    s = meet.get("settings", {})
    return render(
        request,
        "results.html",
        meet_id=meet_id,
        num_lanes=s.get("num_lanes", 8),
        show_lane_header=s.get("show_lane_header", True),
        show_name_header=s.get("show_name_header", True),
        show_club_header=s.get("show_club_header", True),
        show_time_header=s.get("show_time_header", True),
        show_delta_header=s.get("show_delta_header", True),
        show_position_header=s.get("show_position_header", True),
        show_name=s.get("show_name", True),
        show_club=s.get("show_club", True),
        show_delta=s.get("show_delta", True),
        show_position=s.get("show_position", True),
        show_podium=s.get("show_podium", True),
        t=_strings(_client_lang(request, meet), "mobile"),
        **_client_palette(request),
        # Merged, not a wholesale fallback: a relay sending only one font would
        # otherwise leave the other two CSS variables empty. Same as route_live.
        theme_fonts={**_DEFAULT_FONTS, **s.get("theme_fonts", {})},
        labels=_client_labels(
            meet, _client_lang(request, meet), _client_style(request, meet)
        ),
        # EVENT / HEAT inline, on a short window (scoreboard_base.html).
        short_labels=_client_labels(meet, _client_lang(request, meet), "short"),
        # The vocabulary `event_name_parts` composes against, in the language the
        # page is rendered in (docs/app.md `T-11`).
        event_vocab=_strings(_client_lang(request, meet), "event_name"),
        lang=_client_lang(request, meet),
    )


def _build_heats_json(sched):
    if not sched or not sched.get("events"):
        return []
    names = sched.get("names", {})
    name_parts = sched.get("name_parts", {})
    times = sched.get("times", {})
    start_list = sched.get("start_list", {})
    heats = []
    for ev, sorted_heats in sched["events"]:
        ev_str = str(ev)
        for ht in sorted_heats:
            ht_str = str(ht)
            lanes_data = start_list.get(ev_str, {}).get(ht_str, {})
            lanes = []
            for lane_str in sorted(
                lanes_data, key=lambda x: int(x) if x.lstrip("-").isdigit() else 0
            ):
                entry = lanes_data[lane_str]
                lanes.append(
                    {
                        "lane": int(lane_str)
                        if lane_str.lstrip("-").isdigit()
                        else lane_str,
                        "name": entry.get("name", ""),
                        "club": entry.get("club", ""),
                        "seed_time": entry.get("seed_time", ""),
                        "swimmers": entry.get("swimmers", []),
                    }
                )
            heats.append(
                {
                    "event": ev,
                    "heat": ht,
                    "event_name": names.get(ev_str, ""),
                    "event_name_parts": name_parts.get(ev_str),
                    "time": times.get(ev_str, {}).get(ht_str, ""),
                    "lanes": lanes,
                }
            )
    return heats


@app.get("/mobile/schedule", tags=["Public"])
def route_schedule(request: Request):
    meet_id = request.query_params.get("meet", "")
    meet = meet_for(meet_id)
    if not meet:
        return render(request, "offline.html")
    s = meet.get("settings", {})
    sched = meet.get("schedule_data", {})
    heats = _build_heats_json(sched)
    return render(
        request,
        "schedule.html",
        meet_id=meet_id,
        heats=heats,
        has_meet=bool(heats),
        meet_name=meet["name"],
        t=_strings(_client_lang(request, meet), "mobile"),
        # Short on the cards whatever the board says (docs/app.md `S-01`): the
        # identifier repeats once per heat and its width is the event name's.
        labels=_client_labels(meet, _client_lang(request, meet), "short"),
        spoken_labels=_client_labels(
            meet, _client_lang(request, meet), _client_style(request, meet)
        ),
        event_vocab=_strings(_client_lang(request, meet), "event_name"),
        **_client_palette(request),
        theme_fonts={**_DEFAULT_FONTS, **s.get("theme_fonts", {})},
        lang=_client_lang(request, meet),
    )


@app.get("/meet/{meet_id}/config", tags=["Public"])
def route_meet_config(meet_id: str):
    """A meet's display config as JSON — for native attendee clients (iOS/Android)
    that render the board natively instead of loading the HTML page."""
    meet = meet_for(meet_id)
    with _lock:
        live = meet_id in _meets
    if not meet:
        raise HTTPException(404)
    return {
        "name": meet.get("name", ""),
        "location": meet.get("location", ""),
        "sport": meet.get("sport", ""),
        "app_window_title": meet.get("app_window_title", ""),
        "meet_date": meet.get("meet_date", ""),
        "live": live,
        "settings": meet.get("settings", {}),
    }


@app.get("/meet/{meet_id}/schedule", tags=["Public"])
def route_meet_schedule(meet_id: str):
    """A meet's full start list as JSON — what ``/mobile/schedule`` embeds.

    Same structure as the page's ``heats_json``: every heat in running order,
    each with its lanes. An empty ``heats`` means the meet is loaded but carries
    no schedule yet, which is not an error — the client shows its no-schedule
    state and waits for ``schedule_update`` on ``/ws/schedule``."""
    meet = meet_for(meet_id)
    if not meet:
        raise HTTPException(404)
    return {"heats": _build_heats_json(meet.get("schedule_data", {}))}


@app.get("/ping", tags=["Public"])
def route_ping():
    return Response("ok", media_type="text/plain")


@app.get("/manifest/{meet_id}", tags=["Public"])
def route_manifest(meet_id: str):
    meet = meet_for(meet_id)
    if not meet:
        raise HTTPException(404)
    has_icon = bool(meet.get("settings", {}).get("home_icon_b64"))
    icons = (
        [
            {"src": f"/icon/{meet_id}", "sizes": "192x192", "type": "image/png"},
            {"src": f"/icon/{meet_id}", "sizes": "512x512", "type": "image/png"},
        ]
        if has_icon
        else [
            {
                "src": "/static/img/default_mobile_icon.png",
                "sizes": "1024x1024",
                "type": "image/png",
            },
        ]
    )
    app_title = meet.get("app_window_title") or meet.get("name") or "Splouch"
    manifest = {
        "name": app_title,
        "short_name": app_title,
        "start_url": f"/mobile?meet={meet_id}",
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": "#000000",
        "icons": icons,
    }
    return Response(json.dumps(manifest), media_type="application/manifest+json")


@app.get("/icon/{meet_id}", tags=["Public"])
def route_icon(meet_id: str):
    meet = meet_for(meet_id)
    if not meet:
        raise HTTPException(404)
    icon_b64 = meet.get("settings", {}).get("home_icon_b64", "")
    if not icon_b64:
        raise HTTPException(404)
    data = base64.b64decode(icon_b64)
    return Response(
        data, media_type="image/png", headers={"Cache-Control": "public, max-age=3600"}
    )


@app.get("/favicon.ico", tags=["Public"])
def route_favicon():
    # Browsers auto-request this; serve a lean, scalable brand mark for the tab.
    return FileResponse(
        os.path.join(_HERE, "static", "img", "favicon.svg"), media_type="image/svg+xml"
    )


# ── WebSocket — /ws/relay (Pi connections) ─────────────────────────────────────


async def _on_relay_register(ws, sid, data):
    key = data.get("key", "")
    # Meet id is stable per (key, meet_uid): one relay key can publish several
    # meets (e.g. a meet split across days), each on its own picker card and
    # reattaching on reload. Legacy relays without a meet_uid keep one slot/key.
    # The control plane checks the key and owns the id (cloud_registry).
    meet_uid = data.get("meet_uid", "")
    meta = {
        k: data.get(k, "")
        for k in ("name", "location", "sport", "app_window_title", "meet_date")
    }
    meta["settings"] = data.get("settings", {})
    # The control plane's word that this meet belongs on this worker (`/api/assign`,
    # cloud_ticket). Anything wrong with it sends the Pi back to ask again.
    ticket = cloud_ticket.verify(os.environ.get("NODE_SECRET", ""), data.get("ticket"))
    if not (
        ticket
        and ticket.get("n") == cloud_node.node_name()
        and ticket.get("w") == cloud_node.worker_index()
        and ticket.get("k") == cloud_ticket.key_hash(key)
    ):
        await _reassign(ws)
        return
    try:
        result = await run_in_threadpool(
            cloud_node.register, key, meet_uid, meta, data.get("organizer_location")
        )
    except cloud_node.Refused as e:
        await manager.send(ws, "rejected", {"reason": str(e)})
        return
    except cloud_node.ControlError:
        # The ticket is signed and unexpired: the control plane said yes within
        # the last day, so the Pi is let back in while it cannot be asked again.
        print("[cloud] control plane unreachable — admitting on the ticket", flush=True)
        result = {"meet_id": ticket["m"], "organizer": ticket.get("o", "")}
    meet_id = result["meet_id"]
    if meet_id != ticket.get("m"):
        # A ticket for another meet (the operator switched meets on this key).
        await _reassign(ws)
        return

    with _lock:  # fast: in-memory only
        # If this socket was publishing a different meet (operator switched
        # LENEX files), retire it so it stays available as schedule-only.
        prev_id = _relay_sids.get(sid)
        displaced = bool(prev_id and prev_id != meet_id and prev_id in _meets)
        if displaced:
            _retire_mem(prev_id)

        prev = _meets.get(meet_id, {})  # already-live data (settings re-register)
        _meets[meet_id] = {
            "relay_key": key,
            "relay_sid": sid,
            "organizer": result["organizer"],
            **meta,
            "connected_at": prev.get("connected_at")
            or result.get("connected_at")
            or datetime.datetime.now().strftime("%H:%M:%S"),
            "last_scoreboard": prev.get("last_scoreboard", {}),
            "clock_at": 0.0,  # monotonic() of the last `running_time` sent
            "last_results": prev.get("last_results", {}),
            "last_next_heats": prev.get("last_next_heats", {}),
            # Restore the stored schedule on a fresh reconnect so it shows
            # immediately, before the relay re-sends its schedule_snapshot.
            "schedule_data": prev.get("schedule_data")
            or result.get("schedule_data")
            or {},
        }
        _relay_sids[sid] = meet_id
    if displaced:
        await run_in_threadpool(cloud_node.retire, prev_id)
        await _emit_meet_live(prev_id, False)

    await manager.send(ws, "registered", {"meet_id": meet_id})
    await _emit_meet_live(meet_id, True)
    print(f"[cloud] {result['organizer']} registered as meet {meet_id}", flush=True)


async def _reassign(ws):
    """Refuse a register and send the Pi back to `/api/assign` for a fresh ticket."""
    await manager.send(
        ws, "rejected", {"reason": "not assigned here", "reassign": True}
    )


async def _on_relay_disconnect(sid):
    with _lock:
        meet_id = _relay_sids.pop(sid, None)
        meet = _meets.get(meet_id) if meet_id else None
        # Guard against a reconnect race: only retire the meet if this socket is
        # still the one bound to it (a newer socket may have re-registered).
        retired = bool(meet and meet.get("relay_sid") == sid)
        if retired:
            _retire_mem(meet_id)
    if retired:
        await run_in_threadpool(cloud_node.retire, meet_id)
        await _emit_meet_live(meet_id, False)
    if meet_id:
        print(f"[cloud] meet {meet_id} disconnected", flush=True)


async def _forward(sid, event, data):
    """Cache and broadcast a relay event to all attendees of the sending meet."""
    with _lock:
        meet_id = _relay_sids.get(sid)
        meet = _meets.get(meet_id)
    if not meet_id or not meet:
        return

    if event == "update_scoreboard":
        # `running_time` is the race clock, and the console sends it on every
        # timing tick. Forwarding that to every attendee is the traffic
        # docs/architecture/cloud-parity.md refused; dropping it outright left the phones with
        # no clock at all. So throttle it: the client re-bases on what we send and
        # interpolates in between (docs/app.md `L-12`).
        clock = data.pop("running_time", None)

        # Cache the frame without the clock. The join replay sends the snapshot
        # with no way to say how old it is, and a stale clock is worse than none —
        # a client joining mid-heat waits for the next re-base instead.
        meet["last_scoreboard"].update(data)

        # A frame that also moves a lane's running flag is a start, a touch, the
        # end of the console's split hold or a finish: rare, and exactly where the
        # value has to be right.
        if clock is not None:
            now = time.monotonic()
            if now - meet.get("clock_at", 0.0) >= _CLOCK_SYNC_SECS or any(
                k.startswith("lane_running") for k in data
            ):
                meet["clock_at"] = now
                data["running_time"] = clock

        await manager.broadcast(_ch("scoreboard", meet_id), event, data)
    elif event == "results_snapshot":
        meet["last_results"] = data
        await manager.broadcast(_ch("results", meet_id), event, data)
    elif event == "next_heats":
        meet["last_next_heats"] = data
        await manager.broadcast(_ch("results", meet_id), event, data)
    elif event == "schedule_snapshot":
        meet["schedule_data"] = data
        # Off the loop: the control plane keeps it for the retained meet's pages.
        await run_in_threadpool(cloud_node.schedule, meet_id, data)
        await manager.broadcast(_ch("schedule", meet_id), "schedule_update")


async def _on_relay_reload(sid):
    with _lock:
        meet_id = _relay_sids.get(sid)
    if not meet_id:
        return
    await manager.broadcast(_ch("scoreboard", meet_id), "reload")
    await manager.broadcast(_ch("results", meet_id), "reload")


async def _on_relay_stats(ws, sid):
    """Reply to a relay's stats request with attendance counts for its *own*
    meet. The meet id is taken from the socket's registration, so a relay can
    only ever read its own numbers. Sends `{'enabled': False}` when the admin
    hasn't turned analytics on, so the operator panel can say so."""
    with _lock:
        meet_id = _relay_sids.get(sid)
    if not meet_id:
        return
    stats = await run_in_threadpool(cloud_node.counts, meet_id)
    await manager.send(ws, "stats", stats)


@app.websocket("/ws/relay")
async def ws_relay(ws: WebSocket):
    await ws.accept()
    sid = id(ws)
    try:
        while True:
            msg = await ws.receive_json()
            event, data = msg.get("event"), msg.get("data") or {}
            if event == "register":
                await _on_relay_register(ws, sid, data)
            elif event in (
                "update_scoreboard",
                "results_snapshot",
                "next_heats",
                "schedule_snapshot",
            ):
                await _forward(sid, event, data)
            elif event == "reload":
                await _on_relay_reload(sid)
            elif event == "get_stats":
                await _on_relay_stats(ws, sid)
            elif event == "ping":
                await manager.send(ws, "pong")
    except WebSocketDisconnect:
        pass
    finally:
        await _on_relay_disconnect(sid)


# ── WebSocket — attendee namespaces ────────────────────────────────────────────


async def _emit_meet_live(meet_id, live):
    """Tell scoreboard/results attendees whether a relay is currently feeding this
    meet, so live-only UI (the running-lane glow, the results board) reverts to its
    idle state when no console is connected."""
    for ns in ("scoreboard", "results"):
        await manager.broadcast(_ch(ns, meet_id), "meet_live", {"live": live})


@app.websocket("/ws/scoreboard")
async def ws_scoreboard(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("event") == "ping":
                await manager.send(ws, "pong")
                continue
            if msg.get("event") != "join_meet":
                continue
            data = msg.get("data") or {}
            meet_id = data.get("meet_id", "")
            meet = await run_in_threadpool(meet_for, meet_id)
            with _lock:
                live = meet_id in _meets
            if not meet:
                continue
            manager.join(ws, _ch("scoreboard", meet_id))
            cloud_node.log_connection(meet_id, data.get("vid", ""), "scoreboard")
            # Send live status first so the page knows whether to animate before
            # the cached scoreboard snapshot is applied.
            await manager.send(ws, "meet_live", {"live": live})
            if meet.get("last_scoreboard"):
                await manager.send(ws, "update_scoreboard", meet["last_scoreboard"])
    except WebSocketDisconnect:
        pass
    finally:
        manager.leave_all(ws)


@app.websocket("/ws/results")
async def ws_results(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("event") == "ping":
                await manager.send(ws, "pong")
                continue
            if msg.get("event") != "join_meet":
                continue
            data = msg.get("data") or {}
            meet_id = data.get("meet_id", "")
            meet = await run_in_threadpool(meet_for, meet_id)
            with _lock:
                live = meet_id in _meets
            if not meet:
                continue
            manager.join(ws, _ch("results", meet_id))
            cloud_node.log_connection(meet_id, data.get("vid", ""), "results")
            # Live status first, so the page reverts to "Waiting…" when no relay is feeding.
            await manager.send(ws, "meet_live", {"live": live})
            if meet.get("last_results"):
                await manager.send(ws, "results_snapshot", meet["last_results"])
            if meet.get("last_next_heats"):
                await manager.send(ws, "next_heats", meet["last_next_heats"])
    except WebSocketDisconnect:
        pass
    finally:
        manager.leave_all(ws)


@app.websocket("/ws/schedule")
async def ws_schedule(ws: WebSocket):
    await ws.accept()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("event") == "ping":
                await manager.send(ws, "pong")
                continue
            if msg.get("event") != "join_meet":
                continue
            data = msg.get("data") or {}
            meet_id = data.get("meet_id", "")
            meet = await run_in_threadpool(meet_for, meet_id)
            if not meet:
                continue
            manager.join(ws, _ch("schedule", meet_id))
            cloud_node.log_connection(meet_id, data.get("vid", ""), "schedule")
    except WebSocketDisconnect:
        pass
    finally:
        manager.leave_all(ws)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=5000)
