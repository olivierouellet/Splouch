# Splouch API contract

**Contract version: `v2`** · Server implementation: this repo (`server/app.py` local, `cloud/cloud_server.py` cloud).

This is the source-of-truth contract that every non-browser client follows — the
Qt/PySide TV display (`Splouch-tv`), the iOS app (`Splouch-ios`), and the
Android app (`Splouch-android`). There is intentionally **no shared client
library**: the platforms are too different. They agree only on this document.

The phone clients also follow [`app.md`](app.md), which
covers the *behaviour* side — what a spectator sees and can do — where this
document covers the wire format.

When you change an event's shape or add a field, bump the version and note it in
the changelog at the bottom. Additive fields are backward-compatible; renames and
removals are breaking.

---

## 1. Transport & envelope

All realtime traffic is **plain WebSocket** (no Socket.IO). Each former namespace
is its own path. Every message — both directions — is a single JSON text frame:

```json
{ "event": "<name>", "data": <any> }
```

- `data` is usually an object; a few events carry `{}` or a bare string.
- Frames on one connection are ordered. Unknown events must be ignored.
- Reconnect is the client's responsibility (WebSockets don't auto-reconnect).
  Reconnect with capped backoff; on every (re)connect, a cloud attendee must
  re-send `join_meet` (see §3). The reference browser client is
  [`static/js/ws.js`](../static/js/ws.js).

There are **two servers** with distinct roles:

| Server | Who connects | Base URL | Rooms? |
| --- | --- | --- | --- |
| **Local** (Pi on pool LAN) | Qt TV display, admin browser | `ws://<pi>:5000` | No — one meet per server |
| **Cloud** (public relay) | phones/spectators; the Pi as a *relay producer* | `wss://<host>` | Yes — many meets, joined by `meet_id` |

---

## 2. Local server (Qt TV display → the Pi)

The Qt display connects directly to the Pi. There is exactly one meet, so there is
no join step — the server starts pushing on connect.

### `/ws/scoreboard`
On connect the server sends, in order: `test_mode`, `display_overlay`,
`columns_state`, `meet_live`, then an `update_scoreboard` snapshot.

**Server → client**
| event | data | meaning |
| --- | --- | --- |
| `update_scoreboard` | *partial* scoreboard dict (§5.1) | live lane/time/place changes; **only changed fields** are sent |
| `race_finished` | `{}` | results are confirmed after the finish-debounce window |
| `test_mode` | `{ "active": bool }` | a recorded session is playing |
| `display_overlay` | `{ "active": bool }` | fullscreen overlay on/off |
| `columns_state` | `{ "hidden": bool }` | optional columns collapsed/expanded |
| `meet_live` | `{ "live": bool }` | is the timing console feeding this display (§2.1) |
| `reload` | `{}` | settings/theme changed — client should re-fetch config and redraw |
| `update` | `{ "target": "<ref>" }` | move to *ref* and restart (native clients only) |

#### 2.1 `meet_live`

The local twin of the cloud's flag of the same name (§3): there it means *a relay is
connected*, here *the console is feeding us*. Same event, same shape, so a client
gates live-only UI on it identically against either server.

It is keyed off **packet arrival**, not the serial port's state — a cable left open
against a powered-off console reports the port as open indefinitely. After
`MEET_LIVE_STALE` seconds of silence the link reads as dead; the window matches the
Qt display's own `_STALE` (`scoreboard/client.py`) so the TV and the phones give up
at the same moment instead of contradicting each other. Test-session playback counts
as live.

Only **transitions** are broadcast. A client learns the current value from the
connect burst above, so a late joiner is never left guessing.

Sent on `/ws/results` too, on connect and on every transition.

**Client → server**
| event | data | effect |
| --- | --- | --- |
| `register` | `{ "role", "hostname", "version", "commit", "dirty" }` | identify a native client; sent on every (re)connect |
| `update_log` | `{ "text", "error": bool, "done": bool\|null }` | progress while handling `update`; `done` null = still running |
| `set_overlay` | `{ "active": bool }` | toggle overlay (rebroadcast as `display_overlay`) |
| `set_columns` | `{ "hidden": bool }` | toggle columns (rebroadcast as `columns_state`) |
| `adjust_splits` | `{ "lane": 1‑12, "delta": int }` | nudge a lane's split count; server replies `update_scoreboard {"lane_splits<n>": v}` |
| `next_heat` | `{}` | advance to the next event/heat (Hytek/manual mode) |

**`register`** is optional but expected of native clients. Browser tabs never send
it, so `role` is what tells the two apart in Settings → Network. `version` is
`git describe --tags --always --dirty` from the client's own checkout, and `dirty`
is true when that checkout has uncommitted changes. The server compares `version`
against its own and flags a mismatch: the display and the server must run the same
ref, or they can disagree about this contract. Fields are truncated server-side —
treat the frame as untrusted LAN input.

**`update`** is sent by `POST /displays_update` and always carries the server's own
ref — a display follows the server, it does not choose. The server refuses to send
it while any lane is running, and a display refuses to act on it for the same
reason: finishing an update means restarting. A display must report failure rather
than restarting into a broken checkout, and must refuse outright if its own
checkout has local changes.

### `/ws/results`
On connect the server sends `meet_live` (§2.1), then the last `results_snapshot`
(if any) and `next_heats`.
**Server → client:** `results_snapshot` (§5.2), `next_heats` (§5.3), `meet_live`,
`reload`. Client sends nothing.

### `/ws/schedule`
**Server → client:** `schedule_update` `{}` — a meet file was loaded, so any start
list a client is holding belongs to the previous meet. Carries no payload: it means
*re-fetch*, not *here is the new data*. Client sends nothing.

Same name and shape as the cloud channel (§3), so the phone Schedule page behaves
identically against either server.

### `/ws/settings` (admin UI only)
**Server → client:** `serial_log {state, msg}`, `test_status {}`, `test_mode {active}`,
`debug_line {hex, text}`. Client sends nothing. Not needed by the TV display.

### `/ws/terminal` (admin UI only)
Bidirectional PTY. Server → client: `output <string>`, `exit {}`.
Client → server: `input <string>`, `resize {rows, cols}`.

---

## 3. Cloud server (phones → public relay)

Spectator apps connect to the cloud, then **join a meet**. `meet_id` comes from the
picker/meet list (§4). `vid` is a random per-device id the client generates once and
stores locally (used only for anonymous attendance counts; send it or omit it).

Join handshake, then listen — identical pattern on all three attendee paths:

```json
→ { "event": "join_meet", "data": { "meet_id": "<id>", "vid": "<device-uuid>" } }
```

### `/ws/scoreboard`
**Server → client:** `meet_live {live}` (sent first, then whenever the console
connects/disconnects), `update_scoreboard` (§5.1; the cloud throttles
`running_time` and never caches it), `reload`.

### `/ws/results`
**Server → client:** `meet_live {live}`, `results_snapshot` (§5.2), `next_heats` (§5.3), `reload`.

### `/ws/schedule`
**Server → client:** `schedule_update` (no data) — signal to re-fetch the schedule
JSON from `GET /mobile/schedule` or the meet's `schedule_data`.

> **Re-join on reconnect.** After any drop the client must re-send `join_meet`;
> the server replays `meet_live` + the latest cached snapshot so the UI catches up.

### `/ws/relay` (the Pi relay — not a spectator)
Documented for completeness; implemented by [`relay.py`](../relay.py). The Pi is a
*producer*: it registers once, then forwards the same events it broadcasts locally.

**Client (relay) → server:** `register` (metadata §5.4), then `update_scoreboard`,
`results_snapshot`, `next_heats`, `schedule_snapshot` (§5.5), `reload`.
**Server → relay:** `registered {meet_id}`, `rejected {reason}`.

---

## 4. REST endpoints native clients need

JSON/asset endpoints (everything else the servers expose is HTML for the browser UI).

### Local (Pi)
| method · path | returns |
| --- | --- |
| `GET /config` | **display config JSON** — `num_lanes`, `theme_colors`, `theme_fonts`, `show_*` flags, `labels`, `meet_title`, `locale`, `display_strings`, `carousel_images`, `carousel_interval` (§6). Lets the Qt display theme *and translate* itself without a rendered page |
| `GET /server` | **who this server is** (§5.10) — `kind: "pi"`, its name, and the contract versions this build implements |
| `GET /i18n/{lang}` | **client strings for one language** (§5.9). Layers this Pi's `scoreboard/locale/{lang}.toml` over the bundled file, so custom wording reaches every client |
| `GET /locales` | `[{ "code": "fr", "name": "Français" }]` — the languages this server can serve, custom files included |
| `GET /manifest.json` | PWA manifest (app title, icons) |
| `GET /home_icon`, `/home_icon_512` | meet home-screen icon PNG |
| `GET /picker_image` | active picker image PNG |
| `GET /images/{filename}` | splash/carousel images |

### Cloud
| method · path | returns |
| --- | --- |
| `GET /` | picker page (HTML) — meet cards |
| `GET /meets` | **meet list JSON** — `{ "meets": [ … ] }`, the same records the picker cards render (§5.6) |
| `GET /picker/config` | **picker chrome JSON** — branding, localised strings, analytics flag (§5.7) |
| `GET /meet/{meet_id}/config` | **meet config JSON** — `name`, `location`, `sport`, `meet_date`, `live`, and the `settings` block (§5.4). Lets a phone render the board without scraping the HTML page |
| `GET /meet/{meet_id}/schedule` | **start list JSON** — `{ "heats": [ … ] }` (§5.8); 404 for an unknown meet, empty `heats` when the meet has no schedule yet |
| `GET /server` | **who this server is** (§5.10) — `kind: "cloud"` |
| `GET /servers` | **server directory** (§5.11) — where else a client may connect; this server always first |
| `GET /i18n/{lang}` | **client strings for one language** (§5.9). No meet in the path: the table is a property of this server's locale files, not of a meet |
| `GET /locales` | as local |
| `GET /manifest/{meet_id}` | per-meet PWA manifest |
| `GET /icon/{meet_id}` | meet icon PNG · `GET /picker_image/{meet_id}` picker image PNG |
| `GET /search_suggestions?meet_id=&q=` | `[{type:"swimmer"|"club", name, club?}]` swimmer/club typeahead |
| `GET /mobile/schedule?meet=<id>` | schedule page (HTML embedding the same `heats` list) |

---

## 5. Payload shapes

### 5.1 `update_scoreboard` (partial)
A flat dict; **only changed keys are sent** each frame — merge into local state.
Lane keys are 1-indexed (`<i>` = 1…12).

| key | type | notes |
| --- | --- | --- |
| `current_event`, `current_heat` | string | e.g. `"3"`, `"1"` |
| `event_name` | string | display name, composed in the **meet's** locale |
| `event_name_parts` | object\|null | the same name, language-neutral, for a client rendering in a language the meet is not run in — see below |
| `heat_time` | string | scheduled time, may be `""` |
| `running_time` | string | the race clock for the heat — one value, not per lane. The Pi sends it on every timing tick; **the cloud forwards at most one every 2s**, plus any frame that also carries a `lane_running<i>` key, and never keeps it in the join snapshot. Clients re-base on each one and tick locally in between |
| `expected_splits` | int | laps expected for the event |
| `lane_name<i>` | string | swimmer/relay display name |
| `lane_club<i>` | string | club |
| `lane_name_alt<i>` | string | relay member names, else `""` |
| `lane_time<i>` | string | finish/split time, e.g. `"0:25.61"` |
| `lane_place<i>` | string | rank, space when none |
| `lane_running<i>` | bool | lane clock active |
| `lane_delta<i>` | string | **HTML** `<span class="delta-better\|delta-worse">±s.hh</span>` vs seed (for the browser) |
| `lane_delta_seconds<i>` | float\|null | **structured** signed delta vs seed in seconds (negative = faster); `null` when no seed/time |
| `lane_delta_better<i>` | bool\|null | `true` when faster than seed; `null` when no delta |
| `lane_splits<i>` | int | reply to `adjust_splits` |

> Native clients should use `lane_delta_seconds<i>` / `lane_delta_better<i>` and
> ignore the HTML `lane_delta<i>`. On a heat change all three reset (`""` / `null`).

**`event_name_parts`** is an event name decomposed into keys rather than words, so
one broadcast frame serves viewers reading in different languages (`app.md` `T-04`):

```json
"event_name": "200 m dos  —  Filles < 12",
"event_name_parts": { "raw": "200 Backstroke Girls 12 & Under",
                      "dist": "200", "stroke": "backstroke", "relay": false,
                      "gender": "girls", "age": "< 12", "age_key": "" }
```

`stroke`, `gender` and `age_key` name entries in `GET /i18n/{lang}`'s `event_name`
section (§5.9); `age` is a numeric band that needs no translation, and only one of
`age`/`age_key` is ever set. Compose as `dist + unit`, stroke, `relay` — then
`separator`, then gender and age; fall back to `raw` when nothing parsed. The same
field rides on `results_snapshot` (§5.2) and each heat of `GET /meet/{id}/schedule`
(§5.8), as `name_parts` in the relay's `schedule_snapshot` (§5.5).

It is **additive and optional**: `event_name` is still sent and is already right for
every viewer who has not chosen a language, so a client may ignore the parts
entirely.

### 5.2 `results_snapshot`
```json
{ "event": "3", "heat": "1", "event_name": "…", "sort": "lane"|"place",
  "lanes": [ { "channel": 4, "place": "1", "place_int": 1, "time": "2:20.92",
              "name": "…", "club": "…", "alt": "…",
              "delta": "<span …>", "delta_seconds": -0.46, "delta_better": true } ] }
```
Lanes without a final time are omitted. `sort` tells the client whether to place each
row by lane (blank gaps) or by finishing place. `delta` is browser HTML;
`delta_seconds`/`delta_better` are the structured equivalents (`null` when no seed).

### 5.3 `next_heats`
```json
{ "heats": [ { "event": 3, "heat": 1, "event_name": "…", "time": "10:42",
              "swimmers": [ { "lane": 1, "name": "…", "club": "…", "alt": "…" } ] } ] }
```

### 5.4 relay `register` metadata (Pi → cloud)
```json
{ "key": "<relay key>", "meet_uid": "<stable per LENEX>", "name": "…",
  "location": "…", "sport": "…", "app_window_title": "…", "meet_date": "YYYY-MM-DD",
  "settings": { "num_lanes": 8, "show_name": true, "show_club": true, "show_delta": true,
                "show_position": true, "show_podium": true, "show_*_header": true,
                "theme_colors": { … }, "theme_fonts": { … }, "locale": "fr",
                "labels": { … }, "label_style": "short", "label_overrides": { … },
                "home_icon_b64": "…?", "picker_image_b64": "…?" } }
```
This `settings` block is the meet's display config — the same values a native
attendee needs to render the board (lane count, visible columns, theme, labels).

`label_style` and `label_overrides` are additive: a client that ignores them
behaves exactly as before they existed.

| field | meaning |
| --- | --- |
| `labels` | as now — resolved for the meet's `locale` and the operator's style. The default a client renders before any user preference, and the whole story for a client that wants no more than that |
| `label_style` | `"short"` or `"long"` — *which* of the two the operator picked, so a client offering the choice knows where to start. The Pi's phone style is `cloud_label_style`, separate from the kiosk's `label_style` |
| `label_overrides` | only what this Pi's `scoreboard/locale/*.toml` changes from the bundled table, keyed by language then style: `{ "fr": { "long": { "event": "COURSE" } } }`. Normally absent. It is the one part of the label table that is genuinely meet-scoped, because it exists on that Pi and nowhere else. A `long` override on a narrow column is ignored, the same as in the bundled table (`app.md` `T-09`) |

The full label table does **not** travel here. It is the same for every meet on a
server and would be duplicated per meet, persisted per meet, and re-sent to every
phone in languages it will never render; it comes from `GET /i18n/{lang}` instead,
one language at a time and cached (§5.9).

### 5.5 relay `schedule_snapshot` (Pi → cloud)
```json
{ "events": [ [3, [1,2]] ], "names": { "3": "…" }, "times": { "3": { "1": "10:42" } },
  "start_list": { "3": { "1": { "1": { "name":"…","club":"…","seed_time":"…","swimmers":[…] } } } } }
```

### 5.6 `GET /meets`
```json
{ "meets": [ { "id": "aBc123", "name": "…", "location": "…", "sport": "…",
               "organizer": "…", "meet_date": "YYYY-MM-DD",
               "offline": false, "has_picker_image": true } ] }
```
`offline` marks a retained meet with no relay currently connected — still listed
on purpose, so an attendee can read the last known state. `has_picker_image` says
whether `GET /picker_image/{id}` will return an image. Both live and retained
meets appear; expired ones are swept before the list is built.

### 5.7 `GET /picker/config`
```json
{ "title": "Splouch", "window_title": "Splouch", "has_logo": false,
  "logo_above": false, "lang": "fr", "analytics_enabled": true,
  "strings": { "page_title": "…", "no_meets": "…", "unnamed_meet": "…",
               "results_disclaimer": "…", "privacy_note": "…" } }
```
Language resolves from `?lang=` when it names an available locale, else
`Accept-Language`, else the server default; the resolved code comes back as
`lang`. The meet list has no locale of its own — per-meet language starts at
`GET /meet/{id}/config`.

`strings` is served rather than shipped in the app because `results_disclaimer`
and `privacy_note` are compliance text and must be correctable without an app
release. Show `privacy_note` only when `analytics_enabled` is true.

### 5.8 `GET /meet/{meet_id}/schedule`
```json
{ "heats": [ { "event": 3, "heat": 1, "event_name": "…", "time": "10:42",
               "lanes": [ { "lane": 4, "name": "…", "club": "…",
                            "seed_time": "…", "swimmers": [ … ] } ] } ] }
```
Every heat in running order — the whole start list, not just the next few
(compare `next_heats`, §5.3). Heats with no entries still appear with an empty
`lanes`. An empty `heats` is not an error: the meet is loaded but carries no
schedule yet, and the client waits for `schedule_update` on `/ws/schedule`.

---

### 5.9 `GET /i18n/{lang}`

One language, everything a client renders itself. `lang` falls back to `en` when
the server has no such locale.

```json
{ "lang": "fr",
  "mobile":  { "scoreboard": "Tableau", "results": "Résultats", … },
  "display": { "waiting_server": "…", "connection_lost": "…", … },
  "labels":  { "short": { "event": "ÉP", "heat": "SÉR", "lane": "CL", … },
               "long":  { "event": "ÉPREUVE", "heat": "SÉRIE", "lane": "CL", … } },
  "event_name": { "unit": "m", "separator": "  —  ",
                  "freestyle": "libre", "girls": "Filles", … } }
```

- **English-merged per key**, the rule `display_strings` already follows: a
  half-translated locale falls back word by word rather than rendering blank.
- **`ETag` + `Cache-Control`.** The body changes only when the server's locale files
  do, so a client fetches one language once and revalidates.
- **Not meet-scoped**, which is the point: one response serves every meet on the
  server. The Pi merges its own `scoreboard/locale/{lang}.toml` first, so custom
  wording is served rather than diffed; the cloud cannot see those files, which is
  why `settings.label_overrides` exists (§5.4).
- **`event_name`** is the vocabulary `update_scoreboard.event_name_parts` composes
  against (§5.1) — strokes, genders, age words, the unit and the separator. It is
  what lets an event name follow the reader's language instead of the meet's.
- **`long` differs from `short` for `event` and `heat` only.** Every other header is
  a narrow column and carries its short word in both tables, so a client renders
  whichever table the user picked without a rule of its own (`app.md` `T-09`).
- `labels` here is the *bundled* table. A client wanting exactly what the operator
  chose can ignore this section entirely and render `settings.labels`.

Cost of a new language, by design: one file in `shared/locales/`. Nothing is added
to any meet payload, and no client repo ships a string.

### 5.10 `GET /server`

The handshake a native client makes before anything else. Both servers answer it.

```json
{ "kind": "pi", "name": "Piscine Olympique",
  "contract": { "api": "v2", "app": "v1" } }
```

- **`kind`** decides the shape of the session, not just the base URL. A `pi` has one
  meet and no picker, so a client that lands on one goes straight to the board;
  a `cloud` starts at the meet list. Inferring this from a 404 on `/meets` is a
  protocol by accident.
- **`name`** is what a server list shows. A menu where every row reads "Splouch"
  is not a menu.
- **`contract`** is the versions *this build* implements, not the newest that
  exist — `api` for this document, `app` for [`app.md`](app.md). A client that needs
  a behaviour an older server lacks can then say so rather than rendering an empty
  screen; a test pins both to the documents' headers so they cannot drift.

It is also the request a client makes against a hand-typed address before saving
it — a typo should fail at entry, not at the first blank board.

### 5.11 `GET /servers` (cloud)

A directory, not a whitelist.

```json
{ "servers": [ { "name": "Splouch", "url": "https://splouch.app", "kind": "cloud" },
               { "name": "Club X",  "url": "https://x.example",   "kind": "cloud" } ] }
```

Fetched rather than compiled into an app, for the reason §5.9 gives about strings:
a club standing up its own instance must not need a store release to become
reachable. The first entry is always *this* server, derived from the request, so the
endpoint is useful with no configuration; further entries come from `servers.json`
in the data directory, deduplicated by URL, with malformed entries dropped rather
than rendered as dead rows.

What it is not: an authority. A client keeps whatever the user typed
(`app.md` `P-13`), and a server absent from every directory still works.

**Discovery on a LAN is separate and matters more at a pool.** A Pi publishes
`_splouch._tcp` over mDNS (port 5000, `kind=pi`, `path=/server`), so an app browses
for it instead of asking anyone to type an address. The hostname aliases the Pi also
publishes — `splouch.local` and the translated ones — are A records: they only help
someone who already knows what to type.

---

## 6. Config for native clients

The browser clients receive display config through server-rendered templates
(`web._globals()` locally; the meet's `settings` block on the cloud). Native clients
have no template, so config is exposed as JSON — all three additions below are
**additive** (the browser UI is unchanged):

1. **Local `GET /config`** — what `web._globals()` injects (`num_lanes`, `theme_colors`,
   `theme_fonts`, `show_*` flags, `labels`) plus `meet_title`, `locale` and
   `display_strings`. The Qt display fetches this once on startup and again on a
   `reload` event.

   `display_strings` is the `[display]` section of the active locale file —
   status messages the *client* renders rather than the server (`waiting_server`,
   `connection_lost`, `retrying`), selected by the `locale` setting (Settings →
   Display → Scoreboard language) and English-merged so an untranslated key never
   renders blank. A native client needs these because it must say something while
   `/config` itself is still unreachable; the Qt display caches the last config on
   disk for exactly that reason.
2. **Cloud `GET /meet/{meet_id}/config`** — the meet's `settings` block (§5.4) plus
   `name`/`location`/`sport`/`meet_date`/`live`, so a phone can theme and render the
   board without scraping the HTML page.
3. **Structured delta** — `lane_delta_seconds<i>`/`lane_delta_better<i>` in
   `update_scoreboard` and `delta_seconds`/`delta_better` in `results_snapshot`,
   alongside the browser's HTML `lane_delta<i>` (§5.1, §5.2).

4. **Cloud `GET /meets`, `GET /picker/config`, `GET /meet/{id}/schedule`** (§5.6–5.8) —
   the picker and schedule screens as data. Previously both existed only as rendered
   HTML, so a native client had nothing to call; the browser pages now build on the
   same helpers and cannot drift from them.

5. **`GET /i18n/{lang}` and `GET /locales`** (§5.9). The Qt display was already
   served its strings (`display_strings`) while the phone clients were told to
   embed theirs, which was the same problem answered two different ways: a
   fourth language meant one file here and two store submissions there, and a Pi's
   custom wording could never reach an embedded table. These endpoints let every
   non-browser client fetch what the TV already fetches. Clients still ship a
   snapshot as a floor and refresh into it, so nothing depends on the network to
   draw its first frame.

A native client's flow: list meets → fetch that meet's config and schedule → open the
WebSockets → merge event frames. The cloud relay `register` metadata (§5.4) carries the
same `settings` shape, so the two config sources agree.

---

## Changelog
- **v2** — `running_time` over the relay. It was stripped from every forwarded
  frame; the cloud now **throttles** it instead — at most one every 2s, plus any
  frame that also carries a `lane_running<i>` key — and deliberately keeps it out
  of the snapshot replayed on `join_meet` (§3, §5.1). Not an additive change: a
  v1 client could reasonably assume the field never arrives over the relay, and
  one that renders each frame verbatim now shows a clock that steps every two
  seconds. What to do with it is `app.md` `L-12` — re-base and
  interpolate, do not render.

- **Added since v2, all additive so the version stands**: `GET /i18n/{lang}` and
  `GET /locales` (§5.9), `settings.label_style` / `settings.label_overrides`
  (§5.4), and `GET /server` / `GET /servers` (§5.10–5.11) with the Pi's
  `_splouch._tcp` mDNS record. A v2 client ignores all of it and is unaffected;
  `app.md` is what consumes them.

- **v1** — Initial contract after the Flask/Socket.IO → FastAPI/plain-WebSocket
  migration. Envelope `{event, data}`; local paths `/ws/scoreboard|results|settings|terminal`;
  cloud paths `/ws/relay|scoreboard|results|schedule` with `join_meet` rooms.
  Later additions, all additive and pre-production so the version stands: cloud
  `GET /meets`, `GET /picker/config`, `GET /meet/{id}/schedule` (§5.6–5.8).
