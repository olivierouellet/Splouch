# Native App Strategy

## Context

Tremplin currently serves its scoreboard and mobile views as HTML/CSS pages rendered in a browser. This document summarises a conversation about replacing those with native clients.

## Target Stack

| Surface | Technology | Replaces |
|---|---|---|
| iPhone | Swift (WKWebView → native UI) | Mobile web app |
| Android | Kotlin (WebView → native UI) | Mobile web app |
| Raspberry Pi / TV | Python + Qt (PySide6) | Chromium browser in kiosk mode |
| Server | FastAPI + plain WebSockets | Flask + Flask-SocketIO |

## Why Move Away From HTML/CSS

### Mobile (phones)

- Swimmer names truncate with CSS `text-overflow: ellipsis` — no way to shrink text to fit, only cut it
- Safe area handling (`env(safe-area-inset-*)`) is fragile and leaks into complex layout hacks
- The current `mobile.html` shell embeds 3 iframes inside a browser, adding a layer of complexity that causes its own rendering issues
- Native `UILabel` (iOS) and `TextView` (Android) support auto-shrink to fit (`adjustsFontSizeToFitWidth`, `autoSizeTextType`) — the name truncation problem disappears

### Raspberry Pi / TV

- Chromium uses significant RAM and CPU on RPi just to run a layout engine
- 4K rendering in a browser on RPi is sluggish — the GPU is not used efficiently
- Python + Qt (PySide6) renders natively, measures text precisely before drawing, and handles 4K/HiDPI as a first-class feature

## Why Drop Socket.IO

Socket.IO is a JavaScript-first library. Native clients exist (`socket.io-client-swift`, Paho Android) but are less maintained and add dependency risk. The actual usage in Tremplin is minimal:

1. Connect to a namespace
2. Emit `join_meet` once with the meet ID
3. Receive `update_scoreboard` and `reload` events

Plain WebSockets handle all three with zero third-party libraries on iOS (`URLSessionWebSocketTask`, built into the OS since iOS 13) and with OkHttp on Android (industry standard). Python uses the `websockets` library (excellent).

## Why FastAPI over Flask

Flask is WSGI (synchronous). Flask-SocketIO works around this with `eventlet`/`gevent` monkey-patching, which is fragile. FastAPI is ASGI (async-native) — WebSockets are built in, no extra library needed, and broadcasting to many concurrent clients (phones at a swim meet) is clean and efficient.

Server-side broadcast becomes straightforward:

```python
async def broadcast(meet_id: str, data: dict):
    for ws in connections[meet_id]:
        await ws.send_json(data)
```

Nothing changes on the client side — Swift, Kotlin, and Python all connect to a plain `ws://` endpoint regardless of server framework.

## WebSocket vs MQTT

MQTT was considered as an alternative (publish/subscribe, built-in reconnect, lightweight). Decision: stay with WebSockets because:

- iOS has **native** WebSocket support (no library); MQTT requires `CocoaMQTT` (smaller ecosystem)
- Python `paho-mqtt` is excellent, but the iOS side tips the balance
- Manual reconnect for WebSockets is ~50 lines of boilerplate (detect silent drops via ping, reconnect on app foreground) — manageable and written once
- For a scoreboard, missing 2–3 seconds during a reconnect is not critical — the next update catches up

## Raspberry Pi Hardware

### Display RPi (Qt scoreboard on TV)

| Resolution | Minimum | Comfortable |
| --- | --- | --- |
| 1080p | RPi 4 2GB | RPi 4 2GB |
| 4K | RPi 4 4GB | RPi 4 4GB |

RPi 3 (1GB) is ruled out for the display role:

- HDMI 1.3 caps at 1080p — no 4K regardless of software
- 1GB RAM is tight once the OS and Qt process are both running

RPi 4 2GB is the sweet spot for 1080p: Python + Qt sits around 80–150MB, leaving ample headroom. The 4GB/8GB models are unnecessary for a scoreboard.

Note on Qt version: **PySide6 (Qt 6)** — decided after PyQt5 turned out to be unusable on the Pi. Neither `pyqt5` nor `pyqt5-qt5` publishes a linux aarch64 wheel (PyPI or piwheels), so `uv sync` cannot resolve it on 64-bit Raspberry Pi OS at all; the only route was apt plus a system-site-packages venv. PySide6 also avoids PyQt5's GPLv3 under this repo's MIT licence, and supports the variable fonts three of the bundled faces use. The earlier worry about Qt 6 on lower-RAM hardware still needs checking on a 2GB Pi 4.

### Server RPi (timing console + WebSocket relay)

The server process (FastAPI + uvicorn + serial port handling) uses ~100–200MB. Raspberry Pi OS Lite (no desktop) adds ~150MB. Total stays comfortably under 500MB.

- **RPi 3 (1GB) is sufficient** for a dedicated server RPi — it renders nothing, just parses serial data and pushes WebSocket updates
- Even 100 phones connected simultaneously adds only ~5MB of WebSocket state

| Configuration | Recommended hardware |
| --- | --- |
| Server only | RPi 3 (1GB) or any spare RPi |
| Display only (1080p) | RPi 4 2GB |
| Display only (4K) | RPi 4 4GB |
| Server + display on one box | RPi 4 2GB (1080p) or 4GB (4K) |

### Settings panel

The settings panel stays as a browser-based web page — it is used by the meet operator from a laptop on the local network, not by spectators. No native UI needed there.

## Why Not a PWA or WebView Wrapper

- **PWA**: already partially implemented (manifest, A2HS prompt), but iOS App Store does not accept PWAs; text rendering problems remain
- **Capacitor / WebView wrapper**: gets onto the App Store with minimal code change, but the inner pages still render as HTML — the swimmer name problem is unchanged

## Effort Estimate (with AI assistance)

- **Coding only**: 1–2 weeks of developer time across all three clients
- **Process overhead** (Apple Developer account, code signing, App Store review, Play Store review): adds days, cannot be compressed
- **Server migration** (Flask → FastAPI + plain WebSockets): moderate, well-contained, and worth doing at the same time to avoid a second migration later

## Open Questions

- Whether the web browser scoreboard (for users who access via a laptop/desktop) is maintained in parallel with the native clients or deprecated

## Validated Results from Meet Manager

A separate idea explored: showing officially validated heat results (not just live timing console data) on the scoreboard, sourced from Splash Meet Manager.

- **Live Results (PDF)**: Meet Manager can auto-publish start lists/results as PDF reports via FTP. An embedded FTP server (`pyftpdlib`) on the Tremplin Pi could receive these, but PDFs aren't structured data — parsing the result-list table with `pdfplumber` is feasible but template/locale-fragile. A simpler fallback is to just serve the PDFs as documents on a "Results" page rather than parsing them into live data.
- **Database server (preferred, untested)**: Meet Manager can store meet data in PostgreSQL/MariaDB/MySQL instead of an `.mdb` file, with near-instant sync across clients. If Tremplin ran Postgres on the same Pi, it could query results directly — no FTP, no PDF parsing. The schema is undocumented, so this needs a spike: point a test Meet Manager install at a local Postgres DB, run a small meet, and inspect the resulting tables for result/heat/time data before committing to this approach.
- Either way, no separate database is needed for Tremplin's own state — results would slot into the existing in-memory `state.py` structures (e.g., a `lenex_results` dict), broadcast over the existing `/results` Socket.IO namespace.

## Repository structure (multi-platform)

The split is **per toolchain, not per surface**. A separate repo is worth its coordination cost only when a client has its own language, its own dependency manager, and its own release process. That is true of the phone apps and false of the Qt display:

- **Splouch** (this repo) — server, cloud relay, web scoreboard/admin UI, **and the Qt scoreboard** (`scoreboard/`).
- **Splouch-ios** — iOS app (Swift, Xcode, App Store review, code signing).
- **Splouch-android** — Android app (Kotlin, Gradle, Play Store review).

### Why the Qt display stays in this repo

- **The kiosk role already exists.** `install/install.sh` provisions three roles — `server`, `kiosk`, `cloud`. The Qt app replaces the Chromium autostart block *inside* the kiosk role; it is not a new deployment target. Users install it with the same script, the same command, the same version prompt.
- **Version lockstep, for free.** Today the kiosk holds no code — it renders whatever HTML the server serves, so display and server can never disagree. A Qt app ends that: the display becomes a real client carrying its own copy of the WebSocket contract. In one repo, `install.sh kiosk <version>` and `install.sh server <version>` resolve to the same git ref, so the two are in lockstep by construction. Across two repos, every change to an `update_scoreboard` payload becomes a coordinated two-repo release.
- **Same toolchain.** Python, `uv`, one `pyproject.toml`, Raspberry Pi OS. No store, no signing, no separate CI. The "own toolchain / own release process" argument that justifies splitting Swift and Kotlin simply does not apply here.
- **Shared assets are real.** The Qt display needs `shared/locales/` (en/fr/es), `shared/static/fonts/`, `shared/static/img/`, and `server/themes/`. The current kiosk installer already downloads `scoreboard_bg.png` over `raw.githubusercontent.com` — a cross-repo split would force generalising that hack instead of retiring it.
- **One update path.** `server/routes/system.py::_run_update` (git fetch → checkout → `uv sync` → `refresh-service.sh`) and the `PROVISION_VERSION` reinstall banner already exist. Extending them to the kiosk role is incremental; a second repo needs a second copy.

Dependency weight is the one real cost — Qt has no business on the server Pi or the cloud VM. That is solved with an optional dependency group, not a repo boundary: `[project.optional-dependencies] scoreboard = ["pyside6-essentials"]`, installed only by the kiosk role via `uv sync --extra scoreboard`. The cloud installs from `cloud/requirements.txt` and is unaffected.

This is not a one-way door in the expensive direction. Extracting `scoreboard/` later with `git filter-repo` — if it ever grows its own release cadence — is cheap. Re-merging two repos whose protocols have drifted is not.

### The shared contract

Avoid a dedicated "common" repo unless real shared *logic* emerges across platforms (unlikely given how different Python/Qt/Swift/Kotlin are). Treat the WebSocket/REST API in `server/app.py` and `server/relay.py` as the shared contract: it is documented in [`docs/api.md`](../docs/api.md), versioned with the server's API version, and each *external* client repo follows it as the source of truth. `scoreboard/` follows the same document — living in this repo is a delivery convenience, not a licence to reach into `server/` internals.
