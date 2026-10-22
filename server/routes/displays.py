"""Settings → Displays → TVs: every Qt display that has registered, by its ID.

A TV sends a `device_id` in `register` (docs/api.md §2) — a UUID it made up on
first launch and keeps (scoreboard/identity.py). The server keeps one entry per
ID in `settings["displays"]`, so a TV keeps its name across reboots and meets,
and lists the ones not connected right now too, so a TV left in the cupboard can
be forgotten.

A display too old to send an ID still shows under Network and Update; it just
cannot be listed here until it is updated.
"""

import re
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import bus
import state
from web import ActionResult, render, require_login

router = APIRouter(tags=["Displays"])

_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# What a TV can show, in Settings order. A view the meet cannot feed is not offered,
# and a TV on one goes back to Live — see available_views.
VIEWS = ("live", "results")
# How long Identify keeps the TV's clock green.
IDENTIFY_SECONDS = 10
NAME_MAX = 40


def available_views() -> list[str]:
    """The views this meet can feed. Results needs a console that times
    (`console_state()['timed']`, the phones' `A-11`): with none, no result ever
    arrives. Read off the operator's console, not a test replay standing in for
    it, so a replay cannot offer Results on a manual meet for its duration."""
    decoder = state._test_saved_decoder or state._decoder
    views = ["live"]
    if getattr(decoder, "requires_serial", True):
        views.append("results")
    return views


def view_of(device_id: str) -> str:
    """The view this TV shows: its own, or Live when the meet cannot feed it."""
    entry = state.settings.get("displays", {}).get(device_id) or {}
    view = entry.get("view", "live")
    return view if view in available_views() else "live"


async def send_view(sockets, device_id: str) -> None:
    """Tell a TV which view to show. Going to Live, the board follows — the
    TV dropped it while on another view, and frames are partial (§5.1)."""
    view = view_of(device_id)
    for ws in list(sockets):
        await bus.manager.send(ws, "display_view", {"view": view})
        if view == "live" and state.board:
            await bus.manager.send(ws, "update_scoreboard", dict(state.board))


def fall_back_unavailable() -> None:
    """Move every TV on a view the meet can no longer feed back to Live.

    Called when the console changes. Saved as Live rather than left for
    `view_of` to override, so Settings shows what the TV shows and a later
    console change does not switch it back on its own. Any thread.
    """
    views = available_views()
    moved = [
        device_id
        for device_id, entry in state.settings.get("displays", {}).items()
        if entry.get("view", "live") not in views
    ]
    if not moved:
        return
    for device_id in moved:
        state.settings["displays"][device_id]["view"] = "live"
    state.save_settings()
    for device_id in moved:
        sockets = state._display_sockets.get(device_id)
        if sockets:
            bus.submit(send_view(set(sockets), device_id))


def register_display(ws, client: dict, payload: dict) -> str | None:
    """Note a TV's ID on its connection, and remember the TV.

    Called by `app.ws_scoreboard` on `register`, which then sends the TV its
    view. An ID that is not a UUID is ignored — the frame is unauthenticated LAN
    input, and the ID becomes a key in settings.json. Settings are written only
    for a new TV or a new hostname, not on every reconnect. Returns the ID, or
    ``None`` when there is none.
    """
    device_id = str(payload.get("device_id", "")).strip().lower()
    if not _ID.match(device_id):
        return None
    client["device_id"] = device_id
    state._display_sockets.setdefault(device_id, set()).add(ws)
    displays = state.settings.setdefault("displays", {})
    entry = displays.get(device_id)
    hostname = client.get("hostname", "")
    if entry is None:
        displays[device_id] = {"name": "", "hostname": hostname, "view": "live"}
        state.save_settings()
    elif entry.get("hostname") != hostname:
        entry["hostname"] = hostname
        state.save_settings()
    return device_id


def unregister_display(ws, client: dict) -> None:
    """Drop a closed connection from its TV's sockets."""
    device_id = client.get("device_id")
    sockets = state._display_sockets.get(device_id) if device_id else None
    if device_id is None or sockets is None:
        return
    sockets.discard(ws)
    if not sockets:
        del state._display_sockets[device_id]
        state._identify_until.pop(device_id, None)


def _identify_left(device_id: str) -> int:
    """Seconds of Identify left on this TV, 0 when it is not identifying."""
    until = state._identify_until.get(device_id)
    if until is None:
        return 0
    left = until - time.monotonic()
    if left <= 0:
        del state._identify_until[device_id]
        return 0
    return int(left + 0.999)


def display_rows() -> list[dict]:
    """Every known TV, connected ones first, then by name.

    Connection details come from `_scoreboard_clients`: the IP and version of
    the first connection, and how many there are — more than one is a cloned
    SD card.
    """
    connections: dict[str, list[dict]] = {}
    for client in state._scoreboard_clients.values():
        if client.get("device_id"):
            connections.setdefault(client["device_id"], []).append(client)
    rows = []
    for device_id, entry in state.settings.get("displays", {}).items():
        conns = connections.get(device_id, [])
        first = conns[0] if conns else {}
        rows.append(
            {
                "device_id": device_id,
                "name": entry.get("name", ""),
                "hostname": first.get("hostname") or entry.get("hostname", ""),
                "ip": first.get("ip", ""),
                "version": first.get("version", ""),
                "connected": bool(conns),
                "copies": len(conns),
                "identify_left": _identify_left(device_id) if conns else 0,
                "view": view_of(device_id),
            }
        )
    rows.sort(
        key=lambda r: (not r["connected"], str(r["name"] or r["hostname"]).lower())
    )
    return rows


def _unlisted() -> int:
    """Native displays connected without an ID — too old to be listed."""
    return sum(
        1
        for c in state._scoreboard_clients.values()
        if c.get("role") and not c.get("device_id")
    )


@router.get("/displays_fragment", dependencies=[Depends(require_login)])
async def route_displays_fragment(request: Request):
    # async: reads the connection tables the WebSocket handlers write, on the
    # same loop, so a connect cannot land mid-iteration.
    return render(
        request,
        "settings/fetched/displays.html",
        displays=display_rows(),
        views=available_views(),
        unlisted=_unlisted(),
        t=state.settings_strings(state.ui_locale(request)),
    )


class DisplayRef(BaseModel):
    device_id: str


class IdentifyBody(DisplayRef):
    active: bool = True


class NameBody(DisplayRef):
    name: str = ""


class ViewBody(DisplayRef):
    view: str


def _not_found():
    return JSONResponse({"error": "No such display."}, status_code=404)


@router.post(
    "/display_identify",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
async def route_display_identify(body: IdentifyBody):
    """Turn a TV's clock green for `IDENTIFY_SECONDS`, or stop early.

    Allowed mid-race: it changes only the colour of the wall clock. The TV runs
    its own timer, so it goes back to normal even if the link drops.
    """
    sockets = state._display_sockets.get(body.device_id)
    if not sockets:
        return JSONResponse(
            {"error": "This display is not connected."}, status_code=409
        )
    if body.active:
        state._identify_until[body.device_id] = time.monotonic() + IDENTIFY_SECONDS
        data = {"active": True, "seconds": IDENTIFY_SECONDS}
    else:
        state._identify_until.pop(body.device_id, None)
        data = {"active": False}
    for ws in list(sockets):
        await bus.manager.send(ws, "identify", data)
    return {"ok": True}


@router.post(
    "/display_view",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
async def route_display_view(body: ViewBody):
    """Put a TV on Live or Results. Allowed mid-race, as Identify is; a TV not
    connected takes it on its next connect."""
    entry = state.settings.get("displays", {}).get(body.device_id)
    if entry is None:
        return _not_found()
    if body.view not in available_views():
        return JSONResponse(
            {"error": "This meet cannot show that view."}, status_code=409
        )
    entry["view"] = body.view
    state.save_settings()
    sockets = state._display_sockets.get(body.device_id)
    if sockets:
        await send_view(set(sockets), body.device_id)
    return {"ok": True}


@router.post(
    "/display_name",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
async def route_display_name(body: NameBody):
    entry = state.settings.get("displays", {}).get(body.device_id)
    if entry is None:
        return _not_found()
    entry["name"] = " ".join(body.name.split())[:NAME_MAX]
    state.save_settings()
    return {"ok": True}


@router.post(
    "/display_forget",
    response_model=ActionResult,
    dependencies=[Depends(require_login)],
)
async def route_display_forget(body: DisplayRef):
    """Drop a TV that is not connected. A connected one would only come back."""
    displays = state.settings.get("displays", {})
    if body.device_id not in displays:
        return _not_found()
    if state._display_sockets.get(body.device_id):
        return JSONResponse({"error": "This display is connected."}, status_code=409)
    del displays[body.device_id]
    state.save_settings()
    return {"ok": True}
