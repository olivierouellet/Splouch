# Splouch mobile — feature contract

**Contract version: `v1`** · Reference implementation: `cloud/templates/` (see §0.2).

This file is the *behaviour* contract: what a spectator can see and do on a phone, and
what drives it. It exists because the phone clients — `Splouch-ios` (Swift) and
`Splouch-android` (Kotlin) — and the TV app live in their own repos, and the web mobile
view is otherwise the only complete statement of the product.

[`api.md`](api.md) is the *data* contract — sockets, events, payload shapes.

---

## 0. How to use this file

### 0.1 Who owns what

| | Owns | Lives in |
| --- | --- | --- |
| **This file** | *what* each feature is, what drives it, whether it is required | Splouch (this repo) |
| **App parity ledger** | *whether* it is implemented on that platform, and why not | `Splouch-ios`, `Splouch-android` |

Each app repo keeps a short `parity.md`, one row per ID below, status `done` /
`deferred` / `n/a — <reason>`. **Per-platform status does not belong here**.

The IDs are the join key between the three repos, so **never renumber**. A retired
feature keeps its ID and gains a `**retired**` note; new ones take the next free number
in their section.

### 0.2 What the apps are clients of

Phones connect to the **cloud relay** by default, not the Pi ([`api.md`](api.md) §3),
but an app is not fixed to one server the way a web page is fixed to its origin: it can
be pointed at another cloud, or at a Pi on the pool's own network (`P-11`–`P-13`). Which
one changes the shape of the session, not just the address — a Pi has one meet and no
picker — so a client asks `GET /server` rather than inferring it. Both servers render
the *same* templates, differing only in whether `MEET_ID` is set:

| Surface | Reference template |
| --- | --- |
| meet picker | [`cloud/templates/picker.html`](../cloud/templates/picker.html) — cloud-only, the Pi has one meet |
| app shell / tabs | [`shared/templates/mobile.html`](../shared/templates/mobile.html) |
| Scoreboard tab | [`shared/templates/live-mobile.html`](../shared/templates/live-mobile.html) + [`shared/templates/scoreboard_base.html`](../shared/templates/scoreboard_base.html) |
| Results tab | [`shared/templates/results.html`](../shared/templates/results.html) + `scoreboard_base.html` |
| Schedule tab | [`shared/templates/schedule.html`](../shared/templates/schedule.html) |
| socket client | [`shared/static/js/ws.js`](../shared/static/js/ws.js) |

What also differs is **kiosk versus phone**: the kiosk board (`server/templates/live.html`,
mirrored by the Qt display) keeps the carousel, the test banner and the operator column
collapse that the phone pages deliberately drop
([`notes/cloud_parity.md`](../notes/cloud_parity.md)).

Everything a phone needs is reachable as JSON — no screen is HTML-only, nothing requires
scraping ([`api.md`](api.md) §4). Each browser page renders from the same helper its
JSON endpoint returns (`_public_meet_list`, `_build_heats_json`, `_picker_branding`), so
web and native cannot drift: a field added for one appears in the other by construction.
Extend the helper, never the route.

### 0.3 Requirement levels

| Level | Meaning |
| --- | --- |
| **must** | the app is not at parity without it |
| **should** | expected, but a first release can ship without it |
| **web-only** | an artifact of running in a browser; a native app satisfies it by existing, or not at all |
| **native-only** | the mirror image: meaningless on the web, which has no choice to make. Server selection is the case — a web page's origin *is* its server |
| **n/a** | present in the Pi/kiosk product, deliberately absent from mobile |

### 0.4 Describe behaviour, not markup

Rows state observable behaviour and its data source. Where the web implementation is an
accident of HTML, the row says so. **Reproducing a workaround is not parity** — implement
the effect, never the mechanism:

| Web mechanism | Why it exists there | Native equivalent |
| --- | --- | --- |
| 28px edge strips for the swipe (`A-03`) | a full-width listener would swallow touches meant for the schedule list inside the `<iframe>` tab | the platform's standard pager, full-width and drag-tracking |
| `sessionStorage['tab']` (`A-04`) | a browser page restores no state of its own | platform state restoration |
| 80px pull threshold, rotating indicator (`A-05`) | hand-rolled; the browser has no refresh control | the platform's refresh control |
| `env(safe-area-inset-*)` (`A-06`) | the only way a page learns where the notch is | safe-area layout guides — free |
| `<title>`, `apple-mobile-web-app-title`, manifest `name` (`A-08`) | the browser tab, and the installed icon's label | none — the store listing fixes the label (Android may set `TaskDescription`) |
| parent re-dispatches `resize`; `contentWindow.on_tab_shown()` (`L-14`, `R-10`) | an `<iframe>` is never told it was revealed | the on-appear callback |
| the `#edgeT` / `#filter-header` 65px alignment contract in `mobile.html` | two documents have to line up as one screen | none — it is one view |
| one `scrollWidth`/`clientWidth` ratio, applied on a gated frame (`L-17`) | CSS cannot shrink text to fit | `UILabel.adjustsFontSizeToFitWidth`, Android `autoSizeTextType` |

The right-hand column is the requirement. Where the platform does the job better than the
web can — `A-03`'s pager, `L-17`'s auto-shrink — matching the web is the floor, not the
target.

---

## 1. Meet picker (`P`)

The entry screen. On the web it is the site root; in an app it is the launch screen, and
where the user returns via `A-02`.

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `P-01` | List of meets as cards: name, date, location, sport | `GET /meets` ([`api.md`](api.md) §5.6) | must |
| `P-02` | Per-meet picker image on the card, when the meet supplies one | `settings.picker_image_b64` → `GET /picker_image/{meet_id}` | should |
| `P-03` | Offline meets stay listed, marked with a dimmed status dot | `offline` = meet retained but no relay connected | must |
| `P-04` | Empty state when no meets are active | `strings.no_meets` | must |
| `P-05` | Picker branding: title, logo, logo above or below the title | `GET /picker/config` → `title`, `has_logo`, `logo_above`; image at `GET /picker_logo` | should |
| `P-06` | Unofficial-results disclaimer under the list | `GET /picker/config` → `strings.results_disclaimer` | **must** — see note |
| `P-07` | Privacy note, shown whenever attendance counting is on for this server | `strings.privacy_note`, gated on `analytics_enabled` | must |
| `P-08` | Selecting a meet opens the app shell for it | `GET /meet/{id}/config` | must |
| `P-09` | Pull-to-refresh re-fetches the meet list | — | should |
| `P-10` | Install hand-off: store links to the native iOS/Android apps once they ship, Add-to-Home-Screen until then | — | web-only — see note |
| `P-11` | Choose which server to connect to, from a list, in the picker's menu | `GET /servers` ([`api.md`](api.md) §5.11), each entry verified with `GET /server` | native-only — see note |
| `P-12` | Servers on the local network are offered without anyone typing an address | mDNS browse for `_splouch._tcp` (do not use `splouch.local`)| native-only — should |
| `P-13` | A server can be added by hand, checked before it is saved | `GET /server` must answer | native-only — must |

> **`P-06` is not decoration.** The disclaimer — live, unofficial results pending
> validation, with SplashMe for validated ones — is the only thing between a live feed
> and a spectator treating it as a result. It belongs on the meet list, not in an About
> screen, and it renders the server's text rather than a compiled-in copy so wording can
> be fixed without a store review.

> **`P-10` is a hand-off, not a feature of the apps.** Serve the store URLs from
> `/picker/config` beside `P-06`'s disclaimer and hide the affordance when they are
> absent; a store listing that moves must not need a deploy. Inside a native app the
> slot renders nothing — an app cannot install itself, which is what `web-only` means
> here. `api.md` gains the fields when the first app is submitted.

> **`P-11`–`P-13` — the server list is data, and the LAN is the case that matters.**
> Fetched, never compiled in: the app ships knowing one URL, the default cloud, and
> everything else arrives as data or is typed. At a pool the useful server is the Pi in
> the building — no internet dependency, an unthrottled race clock — and it publishes
> `_splouch._tcp` over mDNS, so `P-12` is a browse, not a prompt.
>
> - **Ask `GET /server` before saving anything** (`P-13`) — a typo must fail at entry,
>   not at the first blank board. The same call says whether this is a Pi (no meet list;
>   open the board directly) and which contract versions it implements.
> - **`vid` is per server, never shared** — see `C-10`.
> - **Show the server in the header when it is not the default.** A user who switched and
>   forgot cannot answer "where did my meet go?" from a screen that looks identical
>   either way. The menu changes it; the header keeps it visible.

> **`P-12` — Cleartext for the local network only.** A Pi is plain HTTP, anything
> remote must be HTTPS: a *scoped* ATS exception on iOS (local networking,
> `NSLocalNetworkUsageDescription`, Bonjour service declared) and an Android
> `network_security_config` allowing cleartext for `.local` and private ranges. A
> blanket exception is a review risk on both stores and is not needed.

> **Picker language is the device's, not a meet's.** The list spans meets that may each
> run in a different language, so `/picker/config` resolves from `?lang=` or
> `Accept-Language`. Per-meet language starts at `T-06`, once a meet is chosen.

---

## 2. App shell (`A`)

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `A-01` | Three tabs — Scoreboard, Results, Schedule — each with icon and label | `mobile.scoreboard` / `.results` / `.schedule` | must |
| `A-02` | Back affordance to the meet picker | — | must |
| `A-03` | Horizontal swipe moves between adjacent tabs, and the movement is visible — the tabs follow the finger and settle on release | web: 28px edge strips only, ≥40px travel, switched on `touchend` with nothing in between | must |
| `A-04` | The selected tab survives a relaunch | web: `sessionStorage['tab']` | should |
| `A-05` | Pull-to-refresh re-fetches config and rejoins the sockets | web: 80px threshold, rotating indicator | should |
| `A-06` | Content clears notch, Dynamic Island, and home indicator | web: `env(safe-area-inset-*)` | must (free natively) |
| `A-07` | Portrait stacks label under icon; landscape drops labels to save height | CSS media queries | should |
| `A-08` | Window and home-screen title is the meet's `app_window_title`, falling back to its `name` | `settings.app_window_title`, then `name`, then `Splouch` | web-only |
| `A-09` | Meet goes offline mid-session → return to the picker | `GET /mobile` 303s to `/` when the meet is gone | must |

---

## 3. Scoreboard tab (`L`)

Live lane state during a heat. The busiest screen and the one most worth getting right.

### 3.1 Header

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `L-01` | EVENT number, HEAT number, each a small label above a large value | `current_event`, `current_heat` | must |
| `L-02` | Event name | `event_name` — already localised by the server | must |
| `L-03` | Wall clock, `HH:MM`, ticking every second | **device local time**, not the server | must |

### 3.2 Lane table

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `L-04` | One row per lane, `num_lanes` rows always present | `settings.num_lanes` | must |
| `L-05` | Columns: lane · name (+ alt sub-line) · club · time · delta · place | `lane_*<i>` | must |
| `L-06` | Relay member names on a dimmed second line under the name | `lane_name_alt<i>` | must |
| `L-07` | Column visibility follows config: `show_name`, `show_club`, `show_delta`, `show_position` | meet `settings` | must |
| `L-08` | Column *headers* hide independently of the columns: `show_*_header` | meet `settings` | should |
| `L-09` | Empty lanes render blank in place — rows never collapse or shift | — | must |
| `L-10` | Frames are partial: merge changed keys into local state, never replace | `update_scoreboard` (§5.1) | must |
| `L-11` | A running lane's time is styled distinctly; on stop it plays a one-shot "locked" transition, cancelled if the lane starts running again | `lane_running<i>` false-edge | **must** — it is what separates a live clock from a frozen split (`L-12`) |
| `L-12` | Every running lane's time cell shows the **race clock**: one value for the heat, re-based by the server every couple of seconds and ticked by the device in between | `running_time` (throttled by the relay) + `lane_running<i>` + `meet_live` — see note | **must** |
| `L-13` | Event or heat change blanks all times, deltas, and places | `current_event` / `current_heat` change | must |
| `L-14` | Returning to the tab re-runs layout and refreshes the clock | web: parent re-dispatches `resize` | must (native: on-appear) |

> **`L-12` — one clock per heat, re-based by the server, ticked by the device.** There
> is **no per-lane elapsed time** — every running lane shows the same figure, and a
> lane's own time only becomes meaningful at its split, `lane_time<i>`.
>
> **What lane *i* shows**, from `lane_running<i>` and how recently a `running_time`
> landed:
>
> | `lane_running<i>` | Race clock | Time cell | Lane number |
> | --- | --- | --- | --- |
> | true | re-based within 3 sync intervals | the race clock, ticked by the device | normal |
> | true | none yet — joined mid-heat, or the heat just changed | — | **pulse** |
> | true | silent for 3 sync intervals | frozen where it stopped | **pulse** |
> | false | — | `lane_time<i>`, held until the lane runs again | normal |
> | either | `meet_live` false, or disconnected (`C-09`) | last value, held | normal |
>
> The pulse is the lane number cycling between row text colour and timing colour. Let a
> cycle finish before dropping it: it begins and ends on the row colour, and every lane
> re-bases off the same frame, so stopping them all mid-cycle flicks the whole column at
> once.
>
> **What moves the clock** — and, just as much, what does not:
>
> | Event | Effect on the clock |
> | --- | --- |
> | a `running_time` frame | hard re-base; never ease towards it |
> | between frames | the device advances it ~10Hz off the platform's display link, from a *monotonic* clock |
> | a `lane_running<i>` edge, either direction | re-bases — the relay forces a `running_time` onto that frame |
> | event or heat change | blanks everything (`L-13`) |
> | 3 sync intervals with no `running_time` | stop the ticker, freeze the digits, fall back to the pulse |
> | tab or app backgrounded | stop the ticker; never accumulate ticks across a suspend, and do not resume from a stale base |
> | `meet_live` false, or a disconnect | every clock on the board stops, so stale lane state cannot masquerade as a live race |
>
> A split therefore freezes the lane, never the clock:
>
> - **Never start or reset the clock from a lane edge** — it would restart at every
>   length. `lane_running<i>` decides only whether lane *i* displays the clock.
> - **The hold is the console's, and a fixed length** — a set number of seconds from the
>   touch, unrelated to when the swimmer leaves the pad.
> - **Show tenths** — `1:02.4`. Interpolation is good to well under a frame, but the
>   value carries the relay path's latency as a near-constant offset.
> - **Freeze forward, and freeze rather than blank.** The digits stop three intervals past
>   the last re-base, not back at it.
>
> **Throttle the field.** Forward `running_time` instead **at most
> once every ~2s, plus on any frame carrying a `lane_running<i>` key**: one short string
> every two seconds per meet rather than ten to twenty a second

### 3.3 Layout

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `L-15` | Portrait: two-line compact row — lane number spanning left, name on line 1 with club right-aligned, time and delta and place on line 2; a place is prefixed `#`, and nothing is when there is no place | — | must |
| `L-16` | Landscape: full table with a header row, row font scaled to lane count | — | should |
| `L-17` | Long names shrink to fit their cell, ellipsis only as a floor | — | must — see note |

> **`L-17` — shrink, on this tab and on Results (`R-08`).** Row heights are floored by
> `min-height` in portrait and shared out by the table in landscape, so shrinking a name
> changes type size and nothing else.
>
> **Keep the re-fit off the per-frame path**: measuring forces a synchronous layout per
> lane. Don't re-fit every label on every update frame. Names arrive on a heat change,
> so the web gates the call on a frame carrying a `lane_name` key, plus resize and
> tab-reveal. The platform's own auto-shrink (§0.4)
> does the same job properly and cheaply, with a real minimum size.

### 3.4 Not on this tab

| ID | Feature | Level |
| --- | --- | --- |
| `L-18` | Carousel / fullscreen image overlay | n/a — images are local to the Pi and are never relayed |
| `L-19` | Podium highlight animation | n/a — Pi-local, `race_finished` is not forwarded |
| `L-20` | Animated column show/hide, operator-driven | n/a — cloud columns are always visible |
| `L-21` | Any timed hold on a state — the kiosk's 3s `brief_results` flash, its results pause, its leave-results debounce | n/a — still real on the kiosk, still deliberately absent here: the phone shows the last frame received and runs no clock that decides *what* is on screen, so a client joining mid-sequence cannot land out of step with the console. `L-12`'s clock only fills a cell; it gates no transition |
| `L-22` | Independent per-lane clock, each lane timing its own length | n/a — the console has one race clock and the lanes mirror it; a lane's own figure exists only as its split, `lane_time<i>`. See `L-12` |

---

## 4. Results tab (`R`)

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `R-01` | Until the first snapshot: an empty grid, with "Waiting for results…" below it wherever there is room to say so | `mobile.waiting_results` | must |
| `R-02` | A disconnect, or `meet_live` going false, **wipes the board** and returns it to that state | `disconnect`, `meet_live` | must |
| `R-03` | Header shows the snapshot's own event, heat, and event name | `results_snapshot` | must |
| `R-04` | Same six columns and visibility flags as the Scoreboard tab | shared config | must |
| `R-05` | **Lane sort**: row index = `channel`; a lane with no final time leaves its row blank | `sort == "lane"`, and when `sort` is absent | must |
| `R-06` | **Place sort**: rows fill top-down as a ranking | `sort == "place"` | must |
| `R-07` | A missing time renders as `—`, not blank; a missing **place** renders empty — no dash, and no `#` in front of it | — | should |
| `R-08` | Long names shrink to fit rather than clipping | — | should — see `L-17` |
| `R-09` | Final times carry the "locked" styling | `r.time` non-empty | should |
| `R-10` | Returning to the tab re-joins the meet, reconnecting first if needed | web: `on_tab_shown` | must |

---

## 5. Schedule tab (`S`)

The richest screen, and the only one with real client-side state. A spectator uses it to
find *their* swimmer among several hundred.

### 5.1 The list

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `S-01` | Every heat as a card: scheduled time, "Event N — Heat M", event name | `GET /meet/{id}/schedule` ([`api.md`](api.md) §5.8) | must |
| `S-02` | Each card lists its lanes: lane number, name, club, seed time | `lanes[]` | must |
| `S-03` | Relay entries show member first names joined by `·` | `lane.swimmers[].first`, falling back to `.name` | should |
| `S-04` | Alternating card backgrounds, computed over *visible* cards so filtering keeps the stripe | — | should |
| `S-05` | The heat the meet is on is highlighted in the list | `update_scoreboard.current_event`/`current_heat` **and** `results_snapshot.event`/`heat` | must |
| `S-06` | The list auto-scrolls to the current heat once per appearance | re-armed on returning to the foreground | must |
| `S-07` | Empty state when no meet file is loaded | `mobile.no_schedule` / `mobile.no_meet` | must |

> **`S-05` reads the current heat off the *other* two sockets, on purpose.** This tab has
> no feed of its own for it — `/ws/schedule` only signals that the start list changed
> (`S-21`) — so the page opens all three and takes the event/heat from whichever speaks
> last.

### 5.2 Filtering

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `S-08` | Full-screen filter sheet, opened from a button in the top bar | — | must |
| `S-09` | Typeahead search over swimmers and clubs, debounced ~220ms | `GET /search_suggestions?meet_id=&q=` | must |
| `S-10` | Suggestions show type (swimmer/club), name, and club; already-added ones are marked and inert | — | should |
| `S-11` | Active filters appear as chips; tapping a chip's × removes it | — | must |
| `S-12` | A count badge on the filter button shows how many filters are active | — | should |
| `S-13` | Filters are OR-ed: a lane matches if it hits *any* club or swimmer filter | `laneMatches()` | must |
| `S-14` | A swimmer filter matches relay members, not just the lane's display name | `lane.swimmers[]` | must |
| `S-15` | With filters on, non-matching lanes are hidden and heats with no match disappear | — | must |
| `S-16` | **All heats** toggle: keep every heat visible, still filtering the lanes inside | — | should |
| `S-17` | **Upcoming** toggle: hide every heat listed *ahead* of the current one, keeping that one | the current heat's position in the start list (`S-05`) | should — see note |
| `S-18` | Reset clears filters and both toggles, behind a confirmation | `mobile.reset_confirm` | should |
| `S-19` | Distinct empty states for "no swimmers match these filters" and "no search results" | — | should |
| `S-20` | Filters live only for the session — not persisted | — | should |

> **`S-16` exists to answer "when does my kid swim next?"** With filters on and All-heats
> off, the list collapses to only the heats they are in — the common case. Toggled on,
> the full running order returns with their lanes still highlighted, so the spectator can
> see how many heats away it is.

> **`S-17` cuts by position in the start list, not by the clock.** It finds the current
> heat's index and drops everything before it. Scheduled times are planning figures — a
> session runs early or late all day — so filtering on them would hide heats that have
> not swum yet. If the current heat is unknown (nothing has arrived on either socket) or
> is not in this list, the toggle must change nothing rather than empty the screen.

### 5.3 Refresh

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `S-21` | A new schedule from the Pi refreshes the list | `schedule_update` on `/ws/schedule` → re-fetch `GET /meet/{id}/schedule` | must |

> `schedule_update` carries no payload — it is a signal to re-fetch
> `GET /meet/{id}/schedule`. Preserve the user's active filters across it where the
> filtered names still exist; silently dropping them mid-meet is worse than a stale list.
> An empty `heats` means "loaded, no schedule yet" — show `S-07`, not an error.

---

## 6. Connection and session (`C`)

The rules in [`ws.js`](../shared/static/js/ws.js). These are the difference between an
app that works on a pool deck and one that shows a frozen board after a screen lock.
Each of the three sockets runs this loop independently:

```text
  connect
     │
     ├─► join_meet {meet_id, vid}      on every connect, including every reconnect   C-02
     │   queued frames flush                                                         C-06
     │
     ├─► ping every 15s ──────────► pong                                             C-04
     │
     └─► dead, by either test:
           no inbound frame for 35s                                                  C-04
           foregrounded or network back: ping, no pong within ~4s                    C-05
             │
             └─► close ─► backoff 500ms → 5s ─► connect again                        C-03
```

A dead socket implies `meet_live = false` (`C-09`), which stops the clocks (`L-12`) and
wipes the results board (`R-02`).

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `C-01` | Three independent sockets: `/ws/scoreboard`, `/ws/results`, `/ws/schedule` | §2–3 of `api.md` | must |
| `C-02` | `join_meet {meet_id, vid}` on **every** connect, including every reconnect | — | must |
| `C-03` | Automatic reconnect, capped exponential backoff (web: 500ms → 5s) | — | must |
| `C-04` | Heartbeat `ping` every 15s; no inbound frame for 35s means dead — close and reconnect | server replies `pong` | must |
| `C-05` | On foreground or network-restored: probe with a `ping`; no `pong` within ~4s means dead | — | **must** |
| `C-06` | Frames sent while disconnected are queued and flushed on connect | — | should |
| `C-07` | Unknown events are ignored, not treated as errors | — | must |
| `C-08` | `reload` → re-fetch config and redraw (web: full page reload) | — | must |
| `C-09` | `meet_live` gates live affordances; a `disconnect` implies `meet_live = false` | — | must |
| `C-10` | Anonymous per-install, **per-server** id (`vid`) sent with `join_meet` | random UUID per server, stored once each | must — see note |

> **`C-05` is the one that bites phones.** iOS and Android freeze background sockets
> without ever firing a close: the connection is dead but looks open, so backoff never
> starts and the board sits frozen after every screen lock. The foreground probe is what
> makes the reconnect prompt. Do not rely on `C-03` alone.

> **`C-10` — privacy constraints are binding.** `vid` is a random UUID generated once
> **per server** and stored locally, used server-side only for `COUNT(DISTINCT)` to
> estimate attendance.

---

## 7. Theme and language (`T`)

Every meet themes itself. The app renders the operator's choices; it does not have a look
of its own.

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `T-01` | Palette from the meet's config: `bg`, `header_bg`, `header_border`, `header_label`, `header_value`, `th_text`, `th_bg`, `row_odd`, `row_even`, `row_text`, `time`, `delta_better`, `delta_worse` | `settings.theme_colors` | must |
| `T-02` | Schedule-specific colours `schedule_event`, `schedule_time`, `schedule_name`, `schedule_club`, each with a built-in default | `settings.theme_colors` | should |
| `T-03` | Three font roles — `family` (text), `digits` (clock), `timing` (times and deltas) | `settings.theme_fonts` | must |
| `T-04` | Column headers and header labels are the server's words, never the app's | `settings.labels` for the default; `GET /i18n/{lang}` → `labels` when the user has chosen | must — see note |
| `T-05` | The app's own chrome — tab names, empty states, filter UI — is **fetched and cached**, not translated in the app | `GET /i18n/{lang}` → `mobile` ([`api.md`](api.md) §5.9) | must |
| `T-06` | Language defaults to the **meet's** locale and the user may override it | `settings.locale`, then the stored preference | must |
| `T-07` | Missing theme keys fall back to the documented defaults rather than rendering unstyled | — | must |
| `T-08` | A language control, per device, applying to every meet opened afterwards | `GET /locales` for the list | should — see note |
| `T-09` | A short/long control over the EVENT and HEAT headers only, starting from short | `settings.label_style` | should — see note |
| `T-10` | A built-in snapshot of the strings is the floor: compiled into the app, refreshed from the server, cached to disk | — | must — see note |
| `T-11` | The event name follows the chosen language, composed from parts the server sends | `update_scoreboard.event_name_parts` + `GET /i18n/{lang}` → `event_name`; falls back to `event_name` | should — see note |

> **`T-03`**: the bundled faces are in [`shared/static/fonts/`](../shared/static/fonts/)
> — Overpass Mono, DSEG7 Classic, DSEG14 Classic, Share Tech Mono, Orbitron, Roboto Mono.
> Apps embed them rather than downloading, and fall back to a system monospace for an
> unknown name.

> **`T-04` is not a translation, which is why it is not a `[mobile]` string.** The label
> set is not a fixed vocabulary an app could ship. The server resolves each entry from
> *two* settings — the meet's `locale` and the operator's `label_style` — so one column
> header has six possible values before anyone customises anything:
>
> | | `long` | `short` |
> | --- | --- | --- |
> | `locale = "en"` | `EVENT` · `HEAT` | `EV` · `HT` |
> | `locale = "fr"` | `ÉPREUVE` · `SÉRIE` | `ÉP` · `SÉR` |
> | `locale = "es"` | `PRUEBA` · `SERIE` | `PR` · `SER` |
>
> Only those two keys have a long form (`T-09`). Every other header — lane, place,
> time, name, club, delta — renders its short word in both styles.
>
> **The app never holds this table.** It renders `settings.labels` as sent, or — if the
> user has chosen a language or a style (`T-08`, `T-09`) — the matching entry from
> `GET /i18n/{lang}`'s `labels` section. Both come from the server, so neither can drift
> from `shared/locales/` and neither needs a release to gain a language.

> **`T-04` + `T-06`: never translate anything the server sent.** `labels` and
> `event_name` arrive already localised in the meet's language. `T-06` lets the user
> pick, and the app asks the server for that language (`T-05`, `T-08`) rather than
> translating anything itself.

> **`T-08` — one choice, stored per device, set where every meet is in view.** The
> natural home is the meet picker, which already resolves `?lang=` from the visitor
> rather than a meet ([`api.md`](api.md) §5.7): set once, every meet opened
> afterwards follows.
>
> **The picker is cloud-only.** The Pi serves the shell and the tabs but has no
> picker (§0.2), so a picker-only control leaves its phone pages following the
> operator's settings with no way to override.

> **`T-09` — the style reaches EVENT and HEAT, and nothing else.** The lane and place
> columns are the two narrow ones on every board: a long word there either clips or
> shrinks the whole row to fit it, and `POS` / `LN` are not the words a spectator has to
> read anyway — the number under them is. So those headers resolve short whatever the
> style says, and the control is worth offering only because EVENT and HEAT sit in a
> header with room.
>
> This is the server's rule, not the client's: `GET /i18n/{lang}` already returns a
> `long` table whose narrow columns hold their short words, so a client that simply
> renders what it is given is correct. It holds for a club's custom wording too — a Pi
> shipping `lane = { long = "CORRIDOR" }` still gets `CO` in the lane header, because an
> override that outranked the rule would be a back door around it.

> **`T-10` — fetch, but never depend on the fetch.** Ship a snapshot of the strings and
> treat the endpoint as a refresh: read the cache, draw, revalidate in the background,
> store what comes back. Resolve each key in this order, first hit wins:
>
> ```text
> key ─► cached server value ─► built-in value ─► built-in English ─► the key's own name
> ```
>
> **Built-in** means compiled into the app at build time — not the server's
> `shared/locales/`, which [`api.md`](api.md) calls the *bundled* table. The cache is the
> live answer; the built-in copy is what a first launch, an offline start or a cleared
> cache falls back to, and it has to carry English so a key the chosen language lacks
> still lands somewhere.
>
> The server already merges English per key ([`api.md`](api.md) §5.9), so the client
> repeating the rule only matters when the two sides are different ages: an app newer
> than its server asks for a key that server has never heard of, gets nothing back, and
> falls through to English rather than rendering a gap.

> **`T-11` — an event name follows the reader too.** It is meet data, composed on the
> Pi from the LENEX entry, so it cannot simply be looked up the way a label is. It is
> not a free string either: the server decomposes it into keys — distance, stroke,
> relay, gender, age — and ships those as `event_name_parts` beside the composed
> `event_name` ([`api.md`](api.md) §5.1). The vocabulary those keys name is the
> `event_name` section of `GET /i18n/{lang}` (§5.9), fetched and cached exactly like
> the chrome.
>
> So the app joins, it does not parse. Given
> `{dist: "200", stroke: "backstroke", gender: "girls", age: "< 12"}` and the Spanish
> vocabulary, `200 m espalda  —  Niñas < 12` is a lookup and a concatenation —
> `dist + unit`, stroke, relay, then `separator`, then gender and age. **Do not
> re-implement the decomposition**: the regexes that turn `200 Backstroke Girls 12 &
> Under` into those keys live on the server for the same reason the label table does
> (`T-04`), and three client repos parsing event names would drift within a season.
>
> Fall back to `event_name` whenever the parts are absent or compose to nothing — a
> hand-entered name like `Club Handicap Final` parses into nothing, and a raw name in
> the meet's language beats a blank header. That fallback is also what makes this
> additive: a client that ignores the parts is still correct for every spectator who
> has not chosen a language.

---

## 8. Out of scope

Not on any phone client, now or planned:

| Feature | Where it lives |
| --- | --- |
| Operator controls — start, heat advance, column toggles | admin web UI on the Pi |
| Settings panel | browser page, laptop on the LAN ([`notes/native_app_strategy.md`](../notes/native_app_strategy.md)) |
| Cloud admin — meet retention, relay keys, attendance stats | `cloud/templates/admin.html`, password-gated |
| Console/terminal views, `/ws/settings`, `/ws/terminal` | admin only ([`api.md`](api.md) §2) |
| Full-screen kiosk board | the Qt display, [`notes/scoreboard_parity.md`](../notes/scoreboard_parity.md) |

---

## Changelog

- **v1** — First statement of the mobile feature contract, taken from the cloud templates
  as of the FastAPI/plain-WebSocket server. Tracks `api.md` v2.
