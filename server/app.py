#! /usr/bin/python3
import asyncio
import datetime
import glob
import os
import time
import traceback
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Form, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import bus
import state
from meet_data import _get_next_heats, send_event_info
from web import NotAuthenticated, render, require_login
from worker import _worker_adjust_splits, _worker_next_heat, main_thread_worker

from routes.scoreboard import router as scoreboard_router
from routes.meet       import router as meet_router
from routes.settings   import router as settings_router
from routes.debug      import router as debug_router
from routes.system     import router as system_router
from routes.network    import router as network_router
from routes.appearance import router as appearance_router
from routes.i18n       import router as i18n_router

SECRET_KEY = 'rimnqiuqnewiornhf7nfwenjmqvliwynhtmlfnlsklrmqwe'


async def _meet_live_watchdog():
    """Publish `meet_live` whenever the console link starts or stops feeding.

    Runs on the event loop rather than in the serial worker: the worker only ticks
    while its port is open, so a dead port would never report itself dead. Only
    transitions are broadcast — a client learns the current value from the burst
    ws_scoreboard sends on connect.
    """
    while True:
        live = (time.monotonic() - state._last_packet_at) < state.MEET_LIVE_STALE
        if live != state._meet_live:
            state._meet_live = live
            for channel in ('/scoreboard', '/results'):
                bus.emit(channel, 'meet_live', {'live': live})
        await asyncio.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Capture the event loop so the serial worker thread can broadcast onto it.
    bus.set_loop(asyncio.get_running_loop())
    state.install_log_capture()
    import relay
    from console_decoders import load_custom_decoders
    from routes.settings import _load_meet_file
    state.load_settings()
    load_custom_decoders(state.CUSTOM_DECODERS_FOLDER)
    relay.start()
    _register_locale_aliases()
    _last = state.settings.get('last_meet_file', '')
    if _last:
        _path = os.path.join(state.MEET_FOLDER, _last)
        if os.path.isfile(_path):
            _load_meet_file(_path)
    _watchdog = asyncio.create_task(_meet_live_watchdog())
    try:
        yield
    finally:
        _watchdog.cancel()


# Built-in docs are disabled here and re-served below behind `require_login`, so
# the OpenAPI schema and Swagger/ReDoc UIs are only reachable once signed in.
app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY)
app.mount('/static', StaticFiles(directory=state.STATIC_DIR), name='static')

app.include_router(i18n_router)
app.include_router(scoreboard_router)
app.include_router(meet_router)
app.include_router(settings_router)
app.include_router(debug_router)
app.include_router(system_router)
app.include_router(network_router)
app.include_router(appearance_router)


@app.exception_handler(NotAuthenticated)
async def _redirect_to_login(request: Request, exc: NotAuthenticated):
    return RedirectResponse('/login', status_code=303)


@app.exception_handler(RequestValidationError)
async def _on_validation_error(request: Request, exc: RequestValidationError):
    """Return request-body validation failures in the app's ``{ok, error}`` shape.

    The JSON front-end reads ``d.ok`` / ``d.error`` off a parsed body and never
    inspects the HTTP status, so reshaping FastAPI's default ``{detail: [...]}``
    here lets endpoints push validation into their Pydantic models while the
    friendly single-line error the UI shows stays intact. ``detail`` is kept for
    API/`/docs` consumers.
    """
    errors = exc.errors()
    error  = 'Invalid request.'
    if errors:
        e   = errors[0]
        msg = e.get('msg', 'Invalid input')
        # A field_validator raising ValueError('X') surfaces as 'Value error, X'.
        if msg.startswith('Value error, '):
            error = msg[len('Value error, '):]
        else:
            loc   = [str(x) for x in e.get('loc', ()) if x != 'body']
            field = '.'.join(loc)
            error = f'{field}: {msg}' if field else msg
    # A field_validator's ValueError lands in each error's ``ctx`` as a live
    # exception object, which JSONResponse can't serialize — keep only the
    # JSON-safe fields so ``detail`` never breaks the response.
    detail = [{k: e[k] for k in ('type', 'loc', 'msg', 'input') if k in e}
              for e in errors]
    return JSONResponse({'ok': False, 'error': error, 'detail': detail}, status_code=422)


# ── API docs (login-gated) ───────────────────────────────────────────────────
# Unauthenticated hits raise NotAuthenticated → redirect to /login. Once signed
# in, the session cookie also authorizes Swagger UI's fetch of /openapi.json.

@app.get('/openapi.json', include_in_schema=False,
         dependencies=[Depends(require_login)])
async def route_openapi():
    return app.openapi()


@app.get('/docs', include_in_schema=False, dependencies=[Depends(require_login)])
async def route_docs():
    return get_swagger_ui_html(openapi_url='/openapi.json', title='Splouch API docs')


@app.get('/redoc', include_in_schema=False, dependencies=[Depends(require_login)])
async def route_redoc():
    return get_redoc_html(openapi_url='/openapi.json', title='Splouch API docs')


# ── Auth ───────────────────────────────────────────────────────────────────────

@app.get('/login', tags=['Auth'])
async def route_login_form(request: Request):
    # render(), not a bare TemplateResponse: the page needs `lang` from _globals()
    # to declare the scoreboard language like every other page on this server.
    return render(request, 'login.html')


@app.post('/login', tags=['Auth'])
async def route_login(request: Request,
                      username: str = Form(''), password: str = Form('')):
    if (username == state.settings['username'] and
            password == state.settings['password']):
        request.session['user'] = username
        return RedirectResponse(request.query_params.get('next') or '/', status_code=303)
    resp = render(request, 'login.html', login_failed=True)
    resp.status_code = 401
    return resp


@app.get('/logout', tags=['Auth'])
async def route_logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse('/', status_code=303)


# ── WebSocket endpoints ─────────────────────────────────────────────────────────

@app.websocket('/ws/scoreboard')
async def ws_scoreboard(ws: WebSocket):
    await bus.manager.connect(ws, '/scoreboard')
    state._scoreboard_clients[id(ws)] = {
        'ip': ws.client.host if ws.client else '',
        'at': datetime.datetime.now().strftime('%H:%M:%S'),
    }
    if state.main_thread is None:
        state.main_thread = bus.run_bg(main_thread_worker)
    await bus.manager.send(ws, 'test_mode',       {'active': state._test_session is not None})
    await bus.manager.send(ws, 'display_overlay', {'active': state._overlay_active})
    await bus.manager.send(ws, 'columns_state',   {'hidden': state._cols_hidden})
    await bus.manager.send(ws, 'meet_live',       {'live': state._meet_live})
    send_event_info()
    try:
        while True:
            msg = await ws.receive_json()
            ev, d = msg.get('event'), msg.get('data') or {}
            if ev == 'register':
                # A native display identifying itself (docs/api.md §2). Browser
                # tabs never send this, so `role` is what tells the two apart in
                # Settings → Network — and `version` is what makes a kiosk running
                # a different git ref than the server visible at all.
                state._scoreboard_clients.setdefault(id(ws), {}).update({
                    'role':     str(d.get('role', ''))[:20],
                    'hostname': str(d.get('hostname', ''))[:64],
                    'version':  str(d.get('version', ''))[:64],
                    'commit':   str(d.get('commit', ''))[:16],
                    'dirty':    bool(d.get('dirty', False)),
                })
            elif ev == 'update_log':
                # Progress from a display updating itself (docs/api.md §2). Kept
                # per-client and capped: this is unauthenticated LAN input, and a
                # chatty or malfunctioning client must not grow state without end.
                client = state._scoreboard_clients.setdefault(id(ws), {})
                lines = client.setdefault('update_lines', [])
                lines.append({'text': str(d.get('text', ''))[:400],
                              'error': bool(d.get('error', False))})
                del lines[:-state.UPDATE_LOG_MAX]
                done = d.get('done')
                client['update_state'] = ('updating' if done is None
                                          else 'ok' if done else 'failed')
            elif ev == 'set_overlay':
                state._overlay_active = bool(d.get('active', False))
                bus.emit('/scoreboard', 'display_overlay', {'active': state._overlay_active})
            elif ev == 'set_columns':
                state._cols_hidden = bool(d.get('hidden', False))
                bus.emit('/scoreboard', 'columns_state', {'hidden': state._cols_hidden})
            elif ev == 'adjust_splits':
                lane  = int(d.get('lane', 0))
                delta = int(d.get('delta', 0))
                if 1 <= lane <= 12 and delta != 0:
                    # Hand decoder work to the worker (its sole owner) — see worker.py.
                    state._worker_cmds.put(lambda l=lane, dl=delta: _worker_adjust_splits(l, dl))
            elif ev == 'next_heat':
                state._worker_cmds.put(_worker_next_heat)
            elif ev == 'ping':
                await bus.manager.send(ws, 'pong')
    except WebSocketDisconnect:
        pass
    finally:
        bus.manager.disconnect(ws, '/scoreboard')
        state._scoreboard_clients.pop(id(ws), None)


@app.websocket('/ws/results')
async def ws_results(ws: WebSocket):
    await bus.manager.connect(ws, '/results')
    await bus.manager.send(ws, 'meet_live', {'live': state._meet_live})
    if state._last_results_snapshot:
        await bus.manager.send(ws, 'results_snapshot', state._last_results_snapshot)
    ev, ht = state._decoder.last_event_sent if state._decoder.last_event_sent != (0, 0) else (0, 0)
    await bus.manager.send(ws, 'next_heats',
                           {'heats': _get_next_heats(ev, ht,
                                                     num_lanes=int(state.settings.get('num_lanes', 6)))})
    try:
        while True:
            msg = await ws.receive_json()   # results is receive-only apart from heartbeat
            if msg.get('event') == 'ping':
                await bus.manager.send(ws, 'pong')
    except WebSocketDisconnect:
        pass
    finally:
        bus.manager.disconnect(ws, '/results')


@app.websocket('/ws/schedule')
async def ws_schedule(ws: WebSocket):
    """Start-list invalidation. Carries one event, `schedule_update`, sent when a
    meet file is loaded so an open Schedule tab re-fetches instead of showing the
    previous meet's heats. Mirrors the cloud channel of the same name so the phone
    page is identical against either server (docs/api.md §2)."""
    await bus.manager.connect(ws, '/schedule')
    try:
        while True:
            msg = await ws.receive_json()   # receive-only apart from the heartbeat
            if msg.get('event') == 'ping':
                await bus.manager.send(ws, 'pong')
    except WebSocketDisconnect:
        pass
    finally:
        bus.manager.disconnect(ws, '/schedule')


@app.websocket('/ws/settings')
async def ws_settings(ws: WebSocket):
    await bus.manager.connect(ws, '/settings')
    if state.main_thread is None and state._test_session is None:
        state.main_thread = bus.run_bg(main_thread_worker)
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get('event') == 'ping':
                await bus.manager.send(ws, 'pong')
    except WebSocketDisconnect:
        pass
    finally:
        bus.manager.disconnect(ws, '/settings')


# ── Locale URL aliases ─────────────────────────────────────────────────────────

def _register_locale_aliases():
    import tomllib
    seen = set()
    for path in glob.glob(os.path.join(state.LOCALES_DIR, '*.toml')) + \
                glob.glob(os.path.join(state.CUSTOM_LOCALE_FOLDER, '*.toml')):
        try:
            with open(path, 'rb') as f:
                aliases = tomllib.load(f).get('aliases', {})
            for alias, target in aliases.items():
                if alias in seen:
                    continue
                seen.add(alias)
                def make_redirect(t):
                    async def view():
                        return RedirectResponse(t, status_code=303)
                    return view
                app.add_api_route('/' + alias, make_redirect(target), methods=['GET'])
        except Exception:
            pass


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import uvicorn
    try:
        uvicorn.run(app, host='0.0.0.0', port=5000)
    except Exception:
        traceback.print_exc()
