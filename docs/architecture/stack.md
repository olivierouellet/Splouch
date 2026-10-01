# Stack

The tools Splouch is built on and the main ones it replaced. The runtime model
(event loop, threads, bus) is in [`async-architecture.md`](async-architecture.md),
and the choice of native clients in [`native-app-strategy.md`](native-app-strategy.md).

## Server (Pi) and cloud relay

| Tool | Role |
| --- | --- |
| **FastAPI** + **uvicorn** | ASGI app (`server/app.py`, `cloud/cloud_server.py`), run by systemd on the Pi and in Docker on the cloud |
| Plain **WebSockets** | Real-time transport: `server/bus.py` fans out on the Pi, `cloud_server.py` on the relay, `shared/static/js/ws.js` in the browser |
| Starlette `SessionMiddleware` | Operator login, signed cookies (`itsdangerous`) |
| **Jinja2** | Templates, rendered through the single `render()` helper in `server/web.py` |
| `python-multipart` | Form posts and uploads |
| `pyserial` | Timing console serial feed |
| Pillow, `segno` | Image handling and the server-rendered QR code |

## Browser UI

| Tool | Role |
| --- | --- |
| **Bootstrap 5.3** + Bootstrap Icons | Operator pages and cloud admin; dark theme through `data-bs-theme` color modes |
| **HTMX 2** | Settings panel: endpoints return HTML fragments in place of hand-written `fetch` → DOM code |
| xterm.js | Settings terminal tab, downloaded by `install.sh` |

Everything is vendored under `shared/static/`, so the Pi works offline at the pool.
No jQuery.

## Qt display

**PySide6** (`pyside6-essentials`), an optional extra installed only on the kiosk role
(`uv sync --extra scoreboard`). See `scoreboard/README.md`.

## Tooling

`uv` for Python and the lockfile; `ruff` (lint and format), `ty`, `pytest`; Biome and
TypeScript for JavaScript checks; the opt-in `lint` group in `pyproject.toml` pins the
rest (rumdl, djlint, shellcheck, shfmt, taplo, yamllint, actionlint, zizmor).

## Replaced

| Was | Now | Why |
| --- | --- | --- |
| Flask + Flask-SocketIO + Flask-Login | FastAPI + plain WebSockets + `SessionMiddleware` | ASGI-native WebSockets; no eventlet monkey-patching; native clients need no Socket.IO library |
| Bootstrap 3 + jQuery | Bootstrap 5.3 | Built-in dark mode replaced a pile of `!important` overrides; no jQuery |
| Hand-written `fetch` glue in the settings panel | HTMX | Far less JavaScript, same markup |
| Chromium kiosk | PySide6 board | Text measured before drawing; lighter on a Pi |
| PyQt5 | PySide6 | No aarch64 wheel for PyQt5; GPL vs LGPL; variable fonts |

Considered and not taken: `python-socketio` (keeps a socket layer the phones don't
need), NiceGUI for the settings panel (its own Vue runtime and per-tab WebSocket),
MQTT (no native iOS client).
