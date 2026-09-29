# Flask → FastAPI Migration Guide

This document maps every Flask pattern used in this project to its FastAPI
equivalent, and flags where a mechanical swap is insufficient and a design
decision must be made first.

---

## Outcome (migration complete)

> **This migration is done.** The app now runs on FastAPI + uvicorn with plain
> WebSockets; no Flask, `flask-socketio`, or `flask-login` remains. The sections
> below are kept as a record of the options that were weighed. Where the shipped
> code diverged from the recommendation written here, it is noted inline — read
> those notes before trusting a "recommended" label.
>
> Key decisions as actually built:
>
> | Area | Guide recommended | Shipped | Note |
> | ---- | ----------------- | ------- | ---- |
> | Real-time transport (§1) | Option B — `python-socketio` ASGI | **Option A — native plain WebSockets** | Client JS was rewritten to `static/js/ws.js`; server fan-out lives in `bus.py` (local) and `cloud_server.py` (cloud). `python-socketio` is not a dependency. |
> | Authentication (§2) | Option B — `SessionMiddleware` | Option B — `SessionMiddleware` | As recommended (`Tremplin.py`). Cloud uses hashed credentials + `hmac.compare_digest`. |
> | Template context (§3) | Option B — `Depends(base_context)` | **`render()` helper** (DRY Option C) | A single `web.render()` wrapper merges `_globals()` per call — less boilerplate than a per-route `Depends`, and no "forgot the dependency" footgun. |
> | Form handling (§4) | Option B — split typed endpoints | Mixed — `await request.form()` dispatch + JS auto-save | |
> | Entry point (§6) | `uvicorn` | `uvicorn Tremplin:app` via systemd | As recommended. |

---

## What Stays the Same

| Item | Why unchanged |
|------|---------------|
| All Jinja2 templates (`.html` files) | FastAPI uses the same Jinja2 engine; template syntax is identical |
| CSS, JS, fonts (static assets) | Fully framework-agnostic |
| `state.py` | Pure Python data container, no framework dependency |
| `meet_data.py` | Pure business logic |
| Console decoders (`console_decoders/`) | Pure Python, no framework dependency |
| Meet file parsers (`parsers/`) | Pure Python |
| Socket.IO client-side JS (`socket.io.min.js`, all `io('/ns')` calls) | Unchanged if Option A (python-socketio ASGI) is chosen for the server side |
| TOML locale files | Pure data |
| `relay.py` | Pure Python (depends on socketio object but not on Flask itself) |
| `deploy_webhook.py` (cloud) | Already uses plain `http.server`, no Flask |
| `cloud_server.py` (cloud) | Already uses Flask, would be migrated separately |
| systemd service files | Minor change: replace `socketio.run()` with `uvicorn` invocation |
| Docker setup | Minor change: same as above |
| Test files | Logic-only tests are unaffected |

---

## Simple Mechanical Swaps

These have a direct one-to-one equivalent; no architectural decision needed.

| Flask | FastAPI | Notes |
|-------|---------|-------|
| `flask.Flask(__name__)` | `fastapi.FastAPI()` | `SECRET_KEY` moves to session middleware config |
| `Blueprint('name', __name__)` | `APIRouter()` | Same grouping concept |
| `app.register_blueprint(bp)` | `app.include_router(router)` | |
| `@bp.route('/path', methods=['GET'])` | `@router.get('/path')` | One decorator per method instead of a list |
| `@bp.route('/path', methods=['POST'])` | `@router.post('/path')` | |
| `flask.request.args.get('key')` | `key: str = Query(default=None)` as a function parameter | Or `request.query_params.get('key')` |
| `flask.request.remote_addr` | `request.client.host` | |
| `flask.request.headers.get('X-Foo')` | `request.headers.get('x-foo')` | Headers are lowercased in Starlette |
| `flask.jsonify({...})` | `return {...}` | FastAPI auto-serializes dicts to JSON |
| `flask.redirect(url)` | `RedirectResponse(url=url, status_code=303)` | Import from `starlette.responses` |
| `flask.abort(404)` | `raise HTTPException(status_code=404)` | Import from `fastapi` |
| `flask.send_from_directory(dir, filename)` | `FileResponse(os.path.join(dir, filename))` | |
| `@app.errorhandler(401)` | `@app.exception_handler(HTTPException)` | Check `exc.status_code` inside handler |
| `app.add_url_rule('/path', view_func=fn)` | `app.add_api_route('/path', fn)` | Used for locale aliases |
| `socketio.start_background_task(fn)` | `asyncio.create_task(fn())` | Once the whole app runs on asyncio |
| `socketio.sleep(0)` | `await asyncio.sleep(0)` | Yield to event loop |
| `app.mount("/static", StaticFiles(...))` | Same — Starlette `StaticFiles` | |

---

## Changes Requiring a Design Decision

### 1. Real-time transport

**Current**: `flask-socketio` with `async_mode='gevent'`.

**FastAPI standard**: Native `@app.websocket()` — what FastAPI's own docs show and what most new FastAPI projects use. Socket.IO is not the default; it is carried over when there is existing Socket.IO infrastructure to preserve.

**Option A — Native FastAPI WebSockets** (standard for new projects)

Use `@app.websocket('/ws/scoreboard')` with a custom JSON envelope `{type, data}`.
What FastAPI's own docs show; the right choice when starting from scratch.

Cost for this migration: all client-side JS must be rewritten across ~8 templates
(`io('/ns')` → `new WebSocket(...)`, every `socket.on` → manual type-dispatch,
every `socket.emit` → `ws.send(JSON.stringify(...))`, plus manual reconnect logic).
Socket.IO's automatic reconnection and namespace multiplexing must be reimplemented.

**Option B — `python-socketio` ASGI mode** (recommended for this migration)

Replace `flask-socketio` with `socketio.AsyncServer(async_mode='asgi')`, wrapped
in `socketio.ASGIApp`. The `@socketio.on` / `socketio.emit` API is identical to
`flask-socketio`. All client-side JS is unchanged.

Not the FastAPI-native approach, but the right choice here: the server-side
changes are minimal and `worker.py` logic is largely preserved. Option A becomes
attractive only if the client code is being rewritten anyway for other reasons.

**Files most affected**: `Tremplin.py` (all `@socketio.on` handlers), `worker.py`
(`socketio.emit`, `start_background_task`, `socketio.sleep`), `extensions.py`.

---

### 2. Authentication (`flask-login`)

**Current**: `flask_login.LoginManager` + `@login_required` decorator +
`UserMixin`. Session stored in a signed cookie via Flask's `SECRET_KEY`.

#### What is the standard today?

The right choice depends on the client type, not the deployment location:

| Client type | Standard approach |
| ----------- | ----------------- |
| Browser (HTML forms, admin UI) | **Session cookies** — still the norm in 2025. `SameSite=Lax` (Starlette's default) covers most CSRF without extra tokens. |
| SPA / mobile app / external API | **OAuth 2.0 + JWT** — what FastAPI's own docs promote for API-first designs |
| Service-to-service | **HMAC tokens or mTLS** — already in use for the Pi→cloud relay (`relay_key`) |

Both the Pi settings page and the cloud admin UI are browser-only HTML interfaces today, so session cookies are the appropriate choice for both — not because the cloud is less important, but because the client is a browser. JWT only earns its added complexity when non-browser clients (mobile app, external API) need to authenticate.

#### Options

| Option | Description | Trade-offs |
|--------|-------------|------------|
| **A — `starlette-login`** | Third-party port of flask-login for Starlette/FastAPI. Keeps `login_required`, `login_user`, `logout_user` semantics. | Closest to current code; **not** part of Starlette or FastAPI — a separate community library that may lag behind framework updates |
| **B — Starlette `SessionMiddleware` + dependency** (recommended) | `SessionMiddleware` is part of Starlette (same team as FastAPI, well-maintained). Add a `Depends(require_login)` function that checks `request.session`. The "custom" code is ~5 lines; the security-sensitive parts (cookie signing via `itsdangerous`) are handled by the framework. | Small, auditable, no extra dependency; must remember a few hardening details for the cloud (see below) |
| **C — OAuth 2.0 + JWT** | Issue a signed JWT on login; validate via `Depends`. Stateless. | Right choice if/when non-browser clients are added. Not needed today — add at that point, not before. |
| **D — FastAPI Users** | Full user management library: registration, email verification, password reset, OAuth. Requires a database backend (SQLAlchemy or Beanie). | **Not a good fit**: both deployments have one admin user stored in a JSON file. Would require replacing that with a real database to gain features this app does not need. |

#### Hardening checklist for Option B on the cloud

| Detail | Fix |
| ------ | --- |
| Cookie sent over HTTP | Set `https_only=True` on `SessionMiddleware` |
| Password compared with `==` | Use `hmac.compare_digest(stored, provided)` to prevent timing attacks |
| Session not regenerated on login | Call `request.session.clear()` before setting the new session value |
| Weak secret key | Generate with `secrets.token_hex(32)`, store in env var, never in source |

None of these require complex logic — they are one-line settings — but they must be explicitly added.

```python
# setup
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=True)

# login handler
if hmac.compare_digest(provided_password, state.settings["password"]):
    request.session.clear()
    request.session["logged_in"] = True
    return RedirectResponse("/", status_code=303)


# dependency used on every protected route
def require_login(request: Request):
    if not request.session.get("logged_in"):
        raise HTTPException(status_code=401)
```

Note: Pi→cloud relay authentication already uses HMAC tokens (`relay_key`) — the pattern that matters most for the internet-facing surface is already in place.

---

### 3. Template context processor (`@app.context_processor`)

**Current**: A single function called before every `render_template` that injects
~25 variables from `state.settings` into every template.

FastAPI has no built-in equivalent. Options:

| Option | Description | Trade-offs |
|--------|-------------|------------|
| **A — Jinja2 env globals (static only)** | `templates.env.globals["labels"] = ...` at startup. | Only works for values that never change after startup; `state.settings` changes at runtime, so this is not suitable here |
| **B — FastAPI `Depends` (recommended)** | Create `async def template_context(request: Request) -> dict` as a dependency; inject it into every route that calls `TemplateResponse`. | Explicit; works with runtime values; ~10 lines of code |
| **C — Pass variables per-route explicitly** | Each route calls `state.settings` directly and builds its own context dict. | No abstraction, but simple; may become verbose in routes that render templates |

Option B example:
```python
def base_context(request: Request) -> dict:
    return dict(
        request=request,  # required by FastAPI's TemplateResponse
        labels=state.load_locale(),
        num_lanes=int(state.settings.get("num_lanes", 6)),
        theme_colors={
            **state.DEFAULT_THEME_COLORS,
            **state.settings.get("theme_colors", {}),
        },
        # ... etc.
    )


@router.get("/live")
def route_live(ctx: dict = Depends(base_context)):
    return templates.TemplateResponse("live.html", ctx)
```

---

### 4. Form handling in `/settings`

**Current**: A single `GET / POST /settings` mega-endpoint that inspects which
hidden submit key is present in `request.form` to dispatch to the correct
sub-handler (e.g. `pool_setup_submit`, `timing_settings_submit`, etc.).

| Option | Description | Trade-offs |
|--------|-------------|------------|
| **A — Keep mega-form pattern** | Read `await request.form()` and replicate current dispatch logic. | Minimal restructuring; ugly but works; HTML forms stay unchanged |
| **B — Split into typed endpoints** | One `POST /settings/pool`, `POST /settings/timing`, etc., each with Pydantic models or `Form()` params. | Cleaner; easier to test; requires HTML form `action=` attributes to point to specific endpoints |

Option B aligns well with FastAPI's design and the settings page has already been
moving in that direction (separate auto-save calls via JS fetch). If most settings
are already auto-saved via JS fetch, Option B is the natural next step.

---

### 5. Concurrency model (gevent → asyncio)

**Current**: `async_mode='gevent'` patches Python's stdlib with cooperative
green threads. Everything looks synchronous but yields to the event loop implicitly.

**FastAPI** uses asyncio natively (via uvicorn/starlette). Route handlers can be:

| Option | Description | Trade-offs |
|--------|-------------|------------|
| **A — Keep sync `def` handlers** | FastAPI runs `def` handlers in a thread pool. Blocking calls (file I/O, subprocess) are safe but not concurrent. | Least code change; acceptable for a Pi app with few concurrent users |
| **B — Convert to `async def`** | Subprocess routes (`/update_start`, wifi routes) use `asyncio.create_subprocess_exec`. File routes use `aiofiles`. | Better throughput; required anyway for SocketIO ASGI handlers |

Option B is recommended for SocketIO handlers (forced) and subprocess routes
(significant blocking). Simple JSON/redirect routes can stay `def`.

---

### 6. Entry point / server runner

**Current**: `socketio.run(app, host='0.0.0.0')` which internally runs gunicorn
with gevent workers.

**FastAPI**: Use uvicorn directly.

| Current | FastAPI |
|---------|---------|
| `socketio.run(app, host='0.0.0.0')` | `uvicorn.run(app, host='0.0.0.0', port=5000)` |
| `pip install gevent flask-socketio` | `pip install uvicorn python-socketio[asyncio_client]` |
| `async_mode='gevent'` in SocketIO init | `async_mode='asgi'` |

Systemd `ExecStart` line changes from `python3 Tremplin.py` to
`uvicorn Tremplin:app --host 0.0.0.0 --port 5000` (or kept in-process with
`uvicorn.run`).

---

## Known Fragility: Decoder Interface

`worker.py` and `meet_data.py` access decoder attributes directly by name rather
than through methods:

```python
# worker.py / meet_data.py — accesses internal state directly
if i not in state._decoder.lane_seed_times:
    ...
state._decoder.lane_seed_times[i]
```

This caused a silent bug when the new `cts_gen6.py` decoder used `_seed_times`
instead of `lane_seed_times`: the `AttributeError` was swallowed by the
`except Exception` block in `_handle_packet`, so updates were never emitted.

**During the FastAPI migration**, these direct attribute accesses should be
replaced with interface methods on `ConsoleDecoder` (e.g. `get_seed_time(lane)`).
Errors in internal decoder state would then surface clearly rather than silently
dropping packets.

---

## Summary: Complexity per Area

| Area | Effort | Risk |
|------|--------|------|
| Route decorators + blueprints | Low — mechanical rename | Low |
| Request/response helpers | Low — mechanical rename | Low |
| Static files + file serving | Low | Low |
| Template rendering | Low — add `request=request` to context | Low |
| Template context processor | Medium — introduce `Depends` pattern | Low |
| Authentication | Medium — ~30 lines of session middleware | Low |
| Form handling in `/settings` | Medium to High — refactor or keep as-is | Medium |
| Socket.IO + background worker | High — async_mode change, all handlers become `async def` | Medium |
| Gevent → asyncio in `worker.py` | High — `socketio.sleep` / `start_background_task` → asyncio | Medium |
