# Splouch mobile — feature contract

**Contract version: `v4`** · Reference implementation: `cloud/templates/` (see §0.2).

[`api.md`](api.md) is the *data* contract — sockets, events, payload shapes. This
document is the *behaviour* contract: what a spectator can see and do on a phone,
and what drives each of those things. Together they are what an app repo needs.

It exists because the phone clients — `Splouch-ios` (Swift) and `Splouch-android`
(Kotlin) — live in their own repos. The web mobile view is the only complete
statement of the product; without this file, "is the app finished?" has no answer
other than reading Jinja templates.

---

## 0. How to use this file

### 0.1 Who owns what

| | Owns | Lives in |
| --- | --- | --- |
| **This file** | *what* each feature is, what drives it, whether it is required | Splouch (this repo) |
| **App parity ledger** | *whether* it is implemented on that platform, and why not | `Splouch-ios`, `Splouch-android` |

Each app repo keeps a short `parity.md` with one row per ID below and a status of
`done` / `deferred` / `n/a — <reason>`. **Per-platform status does not belong here.**
Putting it here would mean a commit in this repo every time an app ships a screen,
which is exactly the two-repo coordination tax [`notes/native_app_strategy.md`](../notes/native_app_strategy.md)
argues against.

The IDs (`A-03`, `S-07`, …) are stable. They are the join key between the three
repos, so **never renumber**. A retired feature keeps its ID and gains a
`**retired**` note; new features take the next free number in their section.

### 0.2 What the apps are clients of

Phones connect to the **cloud relay** by default, not the Pi ([`api.md`](api.md) §3),
but an app is not fixed to one server the way a web page is fixed to its origin: it
can be pointed at another cloud or at a Pi on the pool's own network (`P-11`–`P-13`).
Which one it is talking to changes the shape of the session, not just the address —
a Pi has one meet and no picker — so a client asks `GET /server` rather than
inferring it. Both servers render the *same* templates — the four phone pages live in
`shared/templates/` and differ only in whether `MEET_ID` is set:

| Surface | Reference template |
| --- | --- |
| meet picker | [`cloud/templates/picker.html`](../cloud/templates/picker.html) — cloud-only, the Pi has one meet |
| app shell / tabs | [`shared/templates/mobile.html`](../shared/templates/mobile.html) |
| Scoreboard tab | [`shared/templates/live-mobile.html`](../shared/templates/live-mobile.html) + [`shared/templates/scoreboard_base.html`](../shared/templates/scoreboard_base.html) |
| Results tab | [`shared/templates/results.html`](../shared/templates/results.html) + `scoreboard_base.html` |
| Schedule tab | [`shared/templates/schedule.html`](../shared/templates/schedule.html) |
| socket client | [`shared/static/js/ws.js`](../shared/static/js/ws.js) |

There is no separate Pi mobile view any more, so there is no risk of speccing
against the wrong one. What still differs is **kiosk versus phone**, not Pi versus
cloud: the kiosk board (`server/templates/live.html`, mirrored by the Qt display)
keeps the carousel, the test banner and the operator column collapse that the phone
pages deliberately drop. [`notes/cloud_parity.md`](../notes/cloud_parity.md) records
that split.

Everything a phone needs is reachable as JSON — no screen is HTML-only, and nothing
here requires scraping a page ([`api.md`](api.md) §4). Each browser page renders from
the same helper its JSON endpoint returns (`_public_meet_list`, `_build_heats_json`,
`_picker_branding`), so web and native cannot drift: a field added for one appears in
the other by construction. Keep it that way — extend the helper, never the route.

### 0.3 Requirement levels

| Level | Meaning |
| --- | --- |
| **must** | the app is not at parity without it |
| **should** | expected, but a first release can ship without it |
| **web-only** | an artifact of running in a browser; a native app satisfies it by existing, or not at all |
| **native-only** | the mirror image: meaningless on the web, which has no choice to make. Server selection is the case — a web page's origin *is* its server |
| **n/a** | present in the Pi/kiosk product, deliberately absent from mobile |

### 0.4 Describe behaviour, not markup

Rows state observable behaviour and its data source. Where the web implementation
is an accident of HTML — iframes, `env(safe-area-inset-*)`, 28px edge strips — that
is called out as such. **Reproducing a workaround is not parity.** Where the
divergence is expected to be visible, the row says what the native equivalent is.

---

## 1. Meet picker (`P`)

The entry screen. On the web it is the site root; in an app it is the launch screen,
and the place the user returns to via `A-02`.

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `P-01` | List of meets as cards: name, date, location, sport | `GET /meets` ([`api.md`](api.md) §5.6) | must |
| `P-02` | Per-meet picker image on the card, when the meet supplies one | `settings.picker_image_b64` → `GET /picker_image/{meet_id}` | should |
| `P-03` | Offline meets stay listed, marked with a dimmed status dot | `offline` = meet is retained but no relay connected | must |
| `P-04` | Empty state when no meets are active | `strings.no_meets` | must |
| `P-05` | Picker branding: title, logo, logo above or below the title | `GET /picker/config` → `title`, `has_logo`, `logo_above`; image at `GET /picker_logo` | should |
| `P-06` | Unofficial-results disclaimer under the list | `GET /picker/config` → `strings.results_disclaimer` | **must** — see note |
| `P-07` | Privacy note, shown whenever attendance counting is on for this server | `strings.privacy_note`, gated on `analytics_enabled` | must |
| `P-08` | Selecting a meet opens the app shell for it | `GET /meet/{id}/config` | must |
| `P-09` | Pull-to-refresh re-fetches the meet list | — | should |
| `P-10` | Install hand-off: store links to the native iOS/Android apps once they ship, Add-to-Home-Screen until then | — | web-only — see note |
| `P-11` | Choose which server to connect to, from a list, in the picker's menu | `GET /servers` ([`api.md`](api.md) §5.11), each entry verified with `GET /server` | native-only — see note |
| `P-12` | Servers on the local network are offered without anyone typing an address | mDNS browse for `_splouch._tcp` | native-only — should |
| `P-13` | A server can be added by hand, checked before it is saved | `GET /server` must answer | native-only — must |

> **`P-06` is not decoration.** The disclaimer states these are live, unofficial
> results subject to validation, and points at SplashMe for validated ones. It is
> the only thing standing between a live feed and a spectator treating it as a
> result. It must be visible on the meet list, not buried in an About screen.
>
> Render the server's text rather than a copy compiled into the app: it is served
> from `/picker/config` precisely so wording can be corrected without waiting on a
> store review.

> **`P-11`–`P-13` — the server list is data, and the LAN is the case that matters.**
> The list is *fetched*, never compiled in, for the reason `T-05` gives about
> strings: a club standing up its own instance must not wait on a store release to
> become reachable. The app ships knowing one URL — the default cloud — and
> everything else arrives as data or is typed.
>
> At a pool the useful server is usually the Pi in the building: no internet
> dependency, and the race clock arrives unthrottled. A Pi publishes
> `_splouch._tcp` over mDNS, so `P-12` is a browse, not a prompt. Its
> `splouch.local` alias is an A record — it only helps someone who already knows
> what to type, which is nobody.
>
> Three rules that are cheaper to decide now than to migrate to later:
>
> - **Ask `GET /server` before saving anything** (`P-13`). A typo must fail at
>   entry, not at the first blank board. The same call says whether this is a Pi —
>   in which case there is no meet list and the app opens the board directly — and
>   which contract versions the server implements.
> - **`vid` is per server, never shared** — see `C-10`.
> - **Cleartext only for the local network.** A Pi is plain HTTP; anything remote
>   must be HTTPS. That means a *scoped* ATS exception on iOS (local networking,
>   with `NSLocalNetworkUsageDescription` and the Bonjour service declared) and an
>   Android `network_security_config` permitting cleartext for `.local` and private
>   ranges only. A blanket exception is a review risk on both stores, and it is not
>   needed.
>
> **Show the server when it is not the default.** A user who has switched and
> forgotten has no way to answer "where did my meet go?" from a screen that looks
> identical either way. The menu is the place to change it; the header is where it
> has to be visible.

> **`P-10` is a hand-off, not a feature of the apps.** The Add-to-Home-Screen
> hint exists because the phone clients do not yet. When they ship, the same slot
> points at the App Store or Play Store entry for the device it is running on, and
> falls back to Add-to-Home-Screen only where there is no app to send people to —
> desktop, or a platform we do not publish for. Inside a native app it renders
> nothing: an app cannot install itself. That is what `web-only` means on this row.
>
> Serve the store URLs from `/picker/config`, next to `P-06`'s disclaimer, rather
> than compiling them into the page, and hide the affordance when they are absent;
> a store listing that moves must not need a deploy. `api.md` gains the fields when
> the first app is submitted.

> **Picker language is the device's, not a meet's.** The list spans meets that may
> each run in a different language, so `/picker/config` resolves from `?lang=` or
> `Accept-Language`. Per-meet language starts at `T-06`, once a meet is chosen.

---

## 2. App shell (`A`)

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `A-01` | Three tabs — Scoreboard, Results, Schedule — each with icon and label | `mobile.scoreboard` / `.results` / `.schedule` | must |
| `A-02` | Back affordance to the meet picker | — | must |
| `A-03` | Horizontal swipe moves between adjacent tabs, and the movement is visible — the tabs follow the finger and settle on release | web: 28px edge strips only, ≥40px travel, switched on `touchend` with nothing in between | must — **see note** |
| `A-04` | The selected tab survives a relaunch | web: `sessionStorage['tab']` | should |
| `A-05` | Pull-to-refresh re-fetches config and rejoins the sockets | web: 80px threshold, rotating indicator | should |
| `A-06` | Content clears notch, Dynamic Island, and home indicator | web: `env(safe-area-inset-*)` | must (free natively) |
| `A-07` | Portrait stacks label under icon; landscape drops labels to save height | CSS media queries | should |
| `A-08` | Window and home-screen title is the meet's `app_window_title`, falling back to the meet's `name` | `settings.app_window_title`, then `name` | web-only — see note |
| `A-09` | Add-to-Home-Screen hint | **retired** — removed from the shell; the picker does the steering now (`P-10`) | n/a |
| `A-10` | Meet goes offline mid-session → return to the picker | `GET /mobile` 303s to `/` when the meet is gone | must |

> **`A-03` — do not port the edge strips.** The web restricts swipe to two 28px
> strips at the screen edges purely because each tab is an `<iframe>`, and a
> full-width listener would swallow touches meant for the schedule list. A native
> pager has no such problem: **use a normal full-width swipe** with the platform's
> standard pager. This is the clearest case in the file where matching the web
> implementation would make the app worse.
>
> **Answer the gesture while it happens.** The web switches on `touchend` and
> draws nothing in between, so the screen is either one tab or the next: a
> half-committed swipe looks like nothing happened, and a swipe the user did not
> mean to make looks like a glitch. A platform pager tracks the drag, reveals the
> neighbouring tab's edge, and animates the settle — take it, together with the
> tab-bar indicator moving with the drag rather than jumping after it. Where
> tracking is genuinely impractical the floor is an animated transition on the
> switch, never an instant cut.

> **`A-08` is chrome, not a screen.** No mobile page draws this string. On the
> web it is the `<title>` (the browser tab), the `apple-mobile-web-app-title`, and
> the PWA manifest's `name` / `short_name` — the label under the icon after a
> `P-10` install. The shell has no title bar of its own: the tab strip and the
> back arrow are the whole chrome, deliberately.
>
> A native app cannot retitle itself per meet — the store listing fixes the icon
> label — so it satisfies this row by existing. Android may set the recents-card
> label from it (`TaskDescription`); iOS has no equivalent. Do **not** grow a title
> bar in the shell to have somewhere to put it. The fallback order is the
> operator's `app_window_title`, then the meet `name` shown on the `P-01` card,
> then `Splouch`.

> **The iframes themselves are `web-only` throughout.** Anything the templates do
> to work around them — re-dispatching `resize` on tab switch, calling into
> `contentWindow.on_tab_shown()`, the `#edgeT` / `#filter-header` 65px alignment
> contract documented in `mobile.html` — has no native counterpart. Implement the
> *effect* (`R-06`, `L-14`), never the mechanism.

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

> **`L-12` — one clock per heat, re-based by the server, ticked by the device.**
> This is the same clock the Qt board runs, minus its header slot: on the kiosk
> the value sits top-right beside the wall clock *and* is mirrored into every
> running lane's time cell, and the phone header has no room for it, so on mobile
> the lane cells are the whole of it. There is **no per-lane elapsed time.** All
> running lanes show the same figure; a lane's own time only becomes meaningful at
> its split, and that arrives as `lane_time<i>`.
>
> **Throttle the field, do not strip it.** What
> [`notes/cloud_parity.md`](../notes/cloud_parity.md) refused was the *frequency* —
> the console sends `running_time` on every timing tick, and the cloud's answer was
> to drop it outright. Forwarding it **at most once every ~2s, plus on any frame
> that also carries a `lane_running<i>` key**, costs roughly one short string every
> two seconds per meet instead of ten to twenty a second, and buys back everything
> a purely local clock cannot have: no drift, no accumulated error over a 1500m,
> and a client that joins mid-heat catching up within one interval instead of
> never. The `lane_running` exception is what makes the moments that must be exact
> — a start, a touch, the end of a split hold, a finish — exact; they are rare by
> nature.
>
> Between re-bases the device advances the clock itself, ~10Hz off the platform's
> display link from a *monotonic* clock, re-basing hard on each `running_time`
> frame rather than easing towards it. The Qt board does exactly this on a 50ms
> ticker ([`scoreboard/board.py`](../scoreboard/board.py), `_tick_clock`); the
> behaviour is pinned by
> [`tests/test_scoreboard_clock.py`](../tests/test_scoreboard_clock.py), which is
> the best statement of it in the repo.
>
> **Mirror the interpolated value into the cells, not the last frame.** The kiosk
> browser interpolates its header chrono and repaints the lane cells only when a
> frame lands — invisible at console frame rate. At one re-base every two seconds
> that would be a clock that moves twice a minute. Follow the Qt board, where a
> running lane's cell and the race clock are the same string on every tick.
>
> - **A split freezes the lane, never the clock.** On the touch the console drops
>   `lane_running<i>` and sends the lap in `lane_time<i>`; that cell holds the
>   split while the heat clock runs on for everyone else, and when the flag comes
>   back the lane rejoins it. So **never start or reset the clock from a lane
>   edge** — it would restart at every length. `lane_running<i>` decides only
>   whether lane *i* displays the clock.
> - **The freeze is the console's, and it is a fixed length.** It starts at the
>   touch and ends a set number of seconds later — a console setting, nothing to
>   do with when the swimmer leaves the pad. Leaving the pad does not end it, and
>   a slow turn does not extend it, so do not model the hold as "the length of the
>   turn" or try to detect its end from anything but the flag.
>
>   The client therefore needs **no timer of its own**, which is also why this does
>   not conflict with `L-21`: gate the clock write on `lane_running<i>`, paint
>   `lane_time<i>` when it arrives, and the split stays up until the console says
>   the lane is running again. Both edges are `lane_running<i>` frames, so both
>   re-base the clock (see the throttle rule above) — the second one is where a
>   lane that has been frozen for several seconds picks the race clock back up,
>   and it must be right.
> - **A running lane ignores `lane_time<i>`.** The split stays in the merged
>   snapshot (`L-10`), so every later frame touching that lane re-stamps it. The
>   kiosk gets away with painting it and overwriting it, because the next
>   `running_time` lands a few hundred milliseconds later; at a two-second re-base
>   the stale lap would sit on top of the live clock for seconds. Skip the write
>   instead of racing it.
> - **Say which one you are looking at.** A ticking clock and a frozen split are
>   the same digits in the same place; only the styling separates them. That is
>   `L-11`'s job and it stops being cosmetic here: running is dimmed, a split locks
>   with the one-shot flash, and a flash still in flight is cancelled when the lane
>   rejoins the clock, whatever the hold's length — nothing coordinates the two.
> - **Show tenths** — `1:02.4`. The interpolation is good to well under a frame,
>   but the value carries the relay path's latency as a near-constant offset, so it
>   reads low by tens to hundreds of milliseconds. Hundredths would claim a
>   precision the path does not have; the kiosk is on the LAN and can afford them.
> - **Joining mid-heat costs one interval at most.** Do **not** keep
>   `running_time` in the relay's replay snapshot — a cached clock with no age on
>   it is worse than no clock. A client that joins mid-heat shows the
>   **lane-number pulse** (the number cycling between row text colour and timing
>   colour) until the first `running_time` arrives, then switches to the clock.
>   Let a pulse finish its cycle before dropping it: it begins and ends on the row
>   colour, and every lane re-bases off the same frame, so stopping them all
>   mid-cycle flicks the whole column at once.
> - **A missed re-base is not a problem; a missing feed is.** The clock is an
>   offset from the last re-base, not a sum of ticks, so a skipped one costs
>   nothing and the next one corrects it in a single hard set. But a lane must not
>   count forever because the frame that stopped it never came: if no
>   `running_time` has arrived for **three sync intervals** while a lane is still
>   flagged running, stop the ticker and fall back to the pulse. Freeze the digits
>   where they stopped — three intervals past the last re-base, not back at it;
>   rewinding a clock in front of a spectator is worse than losing it. And freeze,
>   do not blank: a board that empties mid-race reads as a crash. The real safety net is
>   still `meet_live` and the heartbeat (`C-04`, `C-05`), which turn a dead socket
>   into a stopped clock within seconds; this only covers a feed that keeps talking
>   while saying nothing about the race.
> - **Backgrounding**: stop the ticker when the tab or app leaves the screen and
>   leave it stopped on return — the base is stale, and resuming from it jumps.
>   The next `running_time` re-bases it, within the sync interval. Never
>   accumulate ticks across a suspend.
> - **When the last lane stops**, each lane holds its final time and the clock
>   simply has nothing more to say. `meet_live` false or a disconnect (`C-09`)
>   stops every clock on the board, so stale lane state cannot masquerade as a
>   live race.
>
> **The relay half is done; the clients are not.** `_forward()` in
> `cloud/cloud_server.py` throttles the field and keeps it out of the join
> snapshot, and [`api.md`](api.md) §5.1 documents it. No phone client reads it
> yet — the web board still shows only the pulse — so this row is the spec for
> all three, the reference implementation included.

### 3.3 Layout

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `L-15` | Portrait: two-line compact row — lane number spanning left, name on line 1 with club right-aligned, time and delta and place on line 2; a place is prefixed `#`, and nothing is when there is no place | — | must |
| `L-16` | Landscape: full table with a header row, row font scaled to lane count | — | should |
| `L-17` | Long names shrink to fit their cell, ellipsis only as a floor | — | must — see note |

> **`L-17` — shrink, on this tab and on Results (`R-08`).** Row heights are
> floored by `min-height` in portrait and shared out by the table in landscape, so
> shrinking a name changes type size and nothing else — and the Qt board, which is
> what spectators actually watch, has shrunk names from the start
> ([`notes/scoreboard_parity.md`](../notes/scoreboard_parity.md)).
>
> **Do not put the re-fit on the per-frame path.** Measuring forces a synchronous
> layout per lane. Names arrive on a heat change, so the web gates the call on a
> frame carrying a `lane_name` key, plus resize and tab-reveal. An app that re-fits
> every label on every update frame will drop frames mid-race.
>
> **Native still does this better.** Per
> [`notes/native_app_strategy.md`](../notes/native_app_strategy.md), the web
> computes one ratio from `scrollWidth`/`clientWidth` and applies it, whereas
> `UILabel.adjustsFontSizeToFitWidth` and Android's `autoSizeTextType` fit properly
> and cheaply, with a real minimum size. Matching the web here is the floor, not
> the target.

### 3.4 Not on this tab

| ID | Feature | Level |
| --- | --- | --- |
| `L-18` | Carousel / fullscreen image overlay | n/a — images are local to the Pi and are never relayed |
| `L-19` | Podium highlight animation | n/a — Pi-local, `race_finished` is not forwarded |
| `L-20` | Animated column show/hide, operator-driven | n/a — cloud columns are always visible |
| `L-21` | Any timed hold on a state — the kiosk's 3s `brief_results` flash, its results pause, its leave-results debounce | n/a — still real on the kiosk board, still deliberately absent here: the phone shows the last frame received and runs no clock that decides *what* is on screen, so a client joining mid-sequence cannot land out of step with the console. `L-12`'s clock only fills a cell; it gates no transition |
| `L-22` | Independent per-lane clock, each lane timing its own length | n/a — the console has one race clock and the lanes mirror it; a lane's own figure exists only as its split, `lane_time<i>`. See `L-12` |

---

## 4. Results tab (`R`)

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `R-01` | Until the first snapshot: an empty grid, with "Waiting for results…" below it wherever there is room to say so | `mobile.waiting_results` | must — see note |
| `R-02` | A disconnect, or `meet_live` going false, **wipes the board** and returns it to that state | `disconnect`, `meet_live` | must |
| `R-03` | Header shows the snapshot's own event, heat, and event name | `results_snapshot` | must |
| `R-04` | Same six columns and visibility flags as the Scoreboard tab | shared config | must |
| `R-05` | **Lane sort**: row index = `channel`; a lane with no final time leaves its row blank | `sort == "lane"`, and when `sort` is absent | must |
| `R-06` | **Place sort**: rows fill top-down as a ranking | `sort == "place"` | must |
| `R-07` | A missing time renders as `—`, not blank; a missing **place** renders empty — no dash, and no `#` in front of it | — | should |
| `R-08` | Long names shrink to fit rather than clipping | — | should — see `L-17` |
| `R-09` | Final times carry the "locked" styling | `r.time` non-empty | should |
| `R-10` | Returning to the tab re-joins the meet, reconnecting first if needed | web: `on_tab_shown` | must |

> **`R-01` / `R-02` — clear first, then say so.** The message is not an overlay
> and never covers the board: it sits in the flow and takes whatever room the rows
> leave. In portrait they take their natural height, so it lands under a visible
> empty grid; in landscape they share out the full height and there is no room, so
> the web suppresses it and the empty grid carries the meaning on its own. Either
> layout is parity — an app with a taller results area may well have room in both.
>
> What is **not** optional is the wipe. A results screen holds still by design,
> which is exactly what makes a stale heat read as a current one: an app that
> shows the message over the last heat it received is worse than one that shows
> nothing. Clear the rows, then say you are waiting.

> **`R-05` / `R-06` — `sort` says what a row index *means*.** In lane mode the
> payload is a set of lanes and the row *is* the lane: `channel` 4 renders in row
> 4, and a lane that never posted a time leaves row 4 blank. In place mode the
> payload is already ordered as a ranking and the rows fill from the top: the
> first element is first place, whatever lane it swam in.
>
> Read one as the other and nothing errors — every swimmer simply appears in
> somebody else's row, which on a results screen is indistinguishable from a
> result. Lane-ordered data rendered as a ranking makes lane 1 the winner. A relay
> predating the field omits `sort` entirely, and those relays send lanes, so
> **absent must be read as `lane`** and never as a default of `place`.

---

## 5. Schedule tab (`S`)

The richest screen, and the only one with real client-side state. A spectator uses
it to find *their* swimmer among several hundred.

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

> **`S-05` reads the current heat off the *other* two sockets, on purpose.** This
> tab has no feed of its own for it: `/ws/schedule` only signals that the start
> list changed (`S-21`), and says nothing about where the meet has got to. So the
> page opens all three and takes the event/heat from whichever speaks last.
>
> They arrive at different moments and that is the point. `update_scoreboard`
> carries `current_event` / `current_heat` and moves as soon as the operator
> advances the console, before anyone has swum. `results_snapshot` carries the
> event and heat its *results* belong to, and lands when a heat is confirmed. With
> only the scoreboard the highlight can sit ahead of results that have since been
> published; with only the results it trails a heat behind all meeting.

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

> **`S-17` cuts by position in the start list, not by the clock.** It finds the
> current heat's index and drops everything before it. Scheduled times are
> planning figures — a session runs early or late all day — so filtering on them
> would hide heats that have not swum yet. If the current heat is unknown (nothing
> has arrived on either socket) or is not in this list at all, the toggle must
> change nothing rather than empty the screen.

> **`S-16` exists to answer "when does my kid swim next?"** With filters on and
> All-heats off, the list collapses to only the heats they are in — the common
> case. Toggled on, the full running order returns with their lanes still
> highlighted, so the spectator can see how many heats away it is.

### 5.3 Refresh

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `S-21` | A new schedule from the Pi refreshes the list | `schedule_update` on `/ws/schedule` → re-fetch `GET /meet/{id}/schedule` | must |

> `schedule_update` carries no payload — it is a signal to re-fetch
> `GET /meet/{id}/schedule`. Preserve the user's active filters across it where the
> filtered names still exist; silently dropping them mid-meet is worse than a stale
> list. An empty `heats` means "loaded, no schedule yet" — show `S-07`, do not treat
> it as an error.

---

## 6. Connection and session (`C`)

The rules in [`ws.js`](../shared/static/js/ws.js). These are the difference between an
app that works on a pool deck and one that shows a frozen board after a screen lock.

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

> **`C-05` is the one that bites phones.** iOS and Android freeze background
> sockets without ever firing a close: the connection is dead but looks open, so
> backoff never starts and the board sits frozen after every screen lock. The
> foreground probe is what makes the reconnect prompt. Do not rely on `C-03` alone.

> **`C-10` — privacy constraints are binding.** `vid` is a random UUID generated
> once **per server** and stored locally, used server-side only for
> `COUNT(DISTINCT)` to estimate attendance.
>
> Per server is not a detail. Once a user can add a server (`P-11`), one shared id
> means every server they ever connect to learns the same identifier the default
> cloud uses, and any two of them can confirm they saw the same person. A fresh
> UUID per server costs nothing and removes that entirely — but only if it is done
> from the start, because a deployed app cannot re-anonymise ids it has already
> sent. It must **not** be `identifierForVendor`, an advertising id, a device
> id, or anything derived from one, and it must not be correlated with a name or an
> IP address. It is also what `P-07`'s privacy note describes to the user — that
> notice and this field ship together or not at all. Both stores require the
> disclosure to match the behaviour.

---

## 7. Theme and language (`T`)

Every meet themes itself. The app renders the operator's choices; it does not have
a look of its own.

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `T-01` | Palette from the meet's config: `bg`, `header_bg`, `header_border`, `header_label`, `header_value`, `th_text`, `th_bg`, `row_odd`, `row_even`, `row_text`, `time`, `delta_better`, `delta_worse` | `settings.theme_colors` | must |
| `T-02` | Schedule-specific colours `schedule_event`, `schedule_time`, `schedule_name`, `schedule_club`, each with a built-in default | `settings.theme_colors` | should |
| `T-03` | Three font roles — `family` (text), `digits` (clock), `timing` (times and deltas) | `settings.theme_fonts` | must |
| `T-04` | Column headers and header labels are the server's words, never the app's | `settings.labels` for the default; `GET /i18n/{lang}` → `labels` when the user has chosen | must — see note |
| `T-05` | The app's own chrome — tab names, empty states, filter UI — is **fetched and cached**, not translated in the app | `GET /i18n/{lang}` → `mobile` ([`api.md`](api.md) §5.9) | must — see note |
| `T-06` | Language defaults to the **meet's** locale and the user may override it | `settings.locale`, then the stored preference | must |
| `T-07` | Missing theme keys fall back to the documented defaults rather than rendering unstyled | — | must |
| `T-08` | A language control, per device, applying to every meet opened afterwards | `GET /locales` for the list | should — see note |
| `T-09` | A short/long label control, starting from the operator's pick | `settings.label_style` | should |
| `T-10` | A bundled snapshot of the strings is the floor: shipped with the app, refreshed from the server, cached to disk | — | must — see note |

> **`T-04` is not a translation, which is why it is not a `[mobile]` string.** The
> label set is not a fixed vocabulary an app could ship. The server resolves each
> entry from *two* settings — the meet's `locale` and the operator's `label_style`
> — so the same column header has six possible values before anyone customises
> anything:
>
> | | `long` | `short` |
> | --- | --- | --- |
> | `locale = "en"` | `EVENT` · `HEAT` · `LANE` · `PLACE` | `EV` · `HT` · `LN` · `PL` |
> | `locale = "fr"` | `ÉPREUVE` · `SÉRIE` · `COULOIR` · `POS` | `ÉP` · `SÉR` · `CL` · `POS` |
> | `locale = "es"` | `PRUEBA` · `SERIE` · `CALLE` · `POSICIÓN` | `PR` · `SER` · `CA` · `POS` |
>
> Note that the two styles are not a mechanical truncation, and not every entry
> even has two forms: French `POS` and `TEMPS`, Spanish `TIEMPO`, English `TIME`
> and `NAME` are the same string in both columns, while `ÉPREUVE` → `ÉP` and
> `POSICIÓN` → `POS` are not. There is no rule to reimplement — there is a table,
> and the server owns it (`load_locale()` in `server/state.py`).
>
> On top of that, a Pi reads `scoreboard/locale/*.toml` before the bundled
> locales, so a club can ship `fr.toml` with `lane = { short = "CO", long = "CORRIDOR" }`
> and every display in that pool says `CORRIDOR`. The value never appears in this
> repo at all.
>
> So an app with `"EVENT"` in its own string table gets it wrong three ways: a
> French meet reads `EVENT` over `ÉPREUVE`, the operator's short/long toggle stops
> working, and the club's custom wording is ignored. Worse, the labels are the
> *only* strings on that screen a meet controls, so the board would end up half in
> the device's language and half in the meet's.
>
> **The app never holds this table.** It renders `settings.labels` as sent, or — if
> the user has chosen a language or a style (`T-08`, `T-09`) — the matching entry
> from `GET /i18n/{lang}`'s `labels` section. Both come from the server, so neither
> can drift from `shared/locales/` and neither needs a release to gain a language.
>
> A style flag on its own would not have been enough, and this is why: it selects a
> column of a table, and no table an app ships has `CORRIDOR` in it. Custom wording
> lives in one Pi's `scoreboard/locale/*.toml`. The Pi serves it directly on
> `GET /i18n/{lang}`; over the relay it arrives as `settings.label_overrides`,
> which the client layers on top of the bundled table
> ([`api.md`](api.md) §5.4). Note the one thing it cannot do: a club writes its
> custom wording in *its* language, so a user reading in Spanish gets `CALLE`, not
> `CORRIDOR`. Falling back to the bundled word is right — inventing a translation
> would be worse — but say so in the UI if you offer the choice.
>
> Two styles, not one setting: `label_style` drives the kiosk and
> `cloud_label_style` the phones, chosen independently. What reaches a phone as
> `settings.label_style` is the phone one; `T-09` starts there and lets the user
> move.

> **`T-05` inverted in v3: fetch the chrome, do not ship it.** The earlier rule —
> the app bundles `[mobile]` for every locale — made this repo's locale files the
> source of truth and then copied them into two app repos by hand. `Splouch-tv`
> already showed the alternative: it is a separate repo on a separate release
> cycle and ships no translations at all, because `GET /config` hands it
> `display_strings`. Same problem, and the phone answer was the one that scales
> worst: a fourth language, or a typo in the third, meant two store submissions and
> users who never update; nothing detects a drifted copy; and a club's custom
> wording could never reach it.
>
> `mobile.scoreboard` = `Scoreboard` / `Tableau` / `Marcador`,
> `mobile.upcoming_only` = `Upcoming` / `À venir` / `Próximas`. Nothing about those
> is meet data — but nothing about them belongs in three repos either.

> **`T-10` — fetch, but never depend on the fetch.** Ship a snapshot of the strings
> in the app and treat the endpoint as a refresh: read the cache, draw, then
> revalidate in the background and store what comes back. Resolution order per key
> is **cached server value → bundled value → English → the key's own name**, so a
> server older than the app (a Pi that predates a screen) leaves the new screen in
> English rather than blank. That is the same English-merge rule
> [`api.md`](api.md) §5.9 applies server-side, applied again on the client because
> the two sides can be different ages.
>
> This is what makes `T-05` safe: no network on first launch, no spinner in front
> of a tab bar, and a language added on the server appears the next time the app
> revalidates instead of the next time it ships.

> **`T-08` — one choice, stored per device, set where every meet is in view.** The
> natural home is the meet picker, which already resolves `?lang=` from the visitor
> rather than a meet ([`api.md`](api.md) §5.7): a spectator sets it once and every
> meet they open afterwards follows. Two consequences to design for rather than
> discover:
>
> - **The picker is cloud-only.** The Pi serves the shell and the tabs but has no
>   picker (§0.2), so a picker-only control leaves the Pi's phone pages following
>   the operator's settings with no way to override. Either give the shell a second
>   home for it, or accept that — those users are mostly operators.
> - **`event_name` never follows the choice.** It is meet data, composed on the Pi
>   in the meet's language from the LENEX entry (`T-04`'s labels and the chrome can
>   both move; a translated event name does not exist to move to). A spectator
>   reading in Spanish at a French meet gets Spanish chrome, Spanish labels, and a
>   French event name. That is the residual, and it is correct: the alternative is
>   an app inventing a translation of a name the meet owns.

> **`T-04` + `T-06`: never translate a label the server sent.** `labels` and
> `event_name` arrive already localised in the meet's language. Re-translating
> them produces a screen in two languages at once *by accident*. What v3 adds is
> the same screen in two languages *on purpose*: `T-06` lets the user pick, and the
> app then asks the server for that language (`T-05`, `T-08`) instead of translating
> anything itself. Absent a choice, everything still follows `settings.locale`.

> **`T-03`**: the bundled faces are in [`shared/static/fonts/`](../shared/static/fonts/)
> — Overpass Mono, DSEG7 Classic, DSEG14 Classic, Share Tech Mono, Orbitron, Roboto
> Mono. Apps embed them rather than downloading, and fall back to a system monospace
> for an unknown name.

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

- **v4** — An app stops being tied to one server. `P-11`–`P-13` let a user pick
  from a fetched directory ([`api.md`](api.md) §5.11), browse the local network for
  a Pi over mDNS, or type an address that is verified with `GET /server` (§5.10)
  before it is saved — which is also how a client learns whether it is talking to a
  Pi (one meet, no picker) or a cloud, and which contracts that build implements.
  §0.3 gains a `native-only` level for rows the web cannot have an opinion about,
  its origin being its server.
  - **`C-10` changes, and it is a `must`.** `vid` becomes one id *per server*
    rather than one per install. With a single id, every server a user adds learns
    the identifier the default cloud already knows. This is why the version moved
    rather than being folded into v3: v3 is committed, and a client built against
    it would ship the wrong privacy behaviour.
  - Served already: both endpoints, and the Pi's `_splouch._tcp` record. What no
    client has yet is the menu — the web picker cannot have one, since its origin
    *is* its server.

- **v3** — Translation stops being something an app ships. `T-05` inverts: the
  chrome is fetched from `GET /i18n/{lang}` and cached behind a bundled snapshot
  (`T-10`), the way `Splouch-tv` has always taken `display_strings`, so a new
  language is one file in `shared/locales/` and no store submission. `T-06` gains a
  user override (`T-08`) with the meet's locale as the default, and `T-09` lets the
  user move off the operator's short/long pick. `T-04` restated around it: the
  words are still always the server's, custom wording included, via
  `settings.label_overrides`.
  - **Served, and wired on the web.** [`api.md`](api.md) §5.9 and §5.4 carry the
    endpoints and fields; both are additive, so that contract stands at v2 while
    this one moves. Both servers serve `/i18n/{lang}` and `/locales`; the cloud
    picker has the control, and the shell carries the choice into all three tabs.
    A client that implements none of it behaves as v2 — `settings.labels` and
    `settings.locale`, no override — which is also what every page does until a
    visitor touches the menu.

- **v2** — The scoreboard gets a real clock, and the picker starts handing off to
  the apps. Tracks `api.md` v2 — the relay throttling `running_time` rather than
  stripping it is the change that makes the clock below possible.
  - `L-12` replaced. Was: the lane number pulsing, the only sign of a running
    race once the cloud dropped `running_time`. Now: the heat's race clock in
    every running lane's time cell, re-based by the relay every couple of seconds
    and ticked by the device in between. The pulse survives as the fallback for a
    lane running with no clock — a client that joined mid-heat, or one whose feed
    went quiet. `L-22` restated to match: what is out of scope is a *per-lane*
    clock, not a clock.
  - `L-11` raised to **must**: with digits in the cell either way, the styling is
    the only thing separating a live clock from a frozen split.
  - `P-10` is now the install hand-off — store links to the native apps once they
    ship, Add-to-Home-Screen only where there is no app to send people to.
  - `A-03` now requires the swipe to be *visible* while it happens, not only to
    land on the next tab.
  - `A-08` restated as chrome — window title, home-screen label, manifest name —
    and dropped to `web-only`: an app cannot retitle itself per meet.
  - `R-07` split: a missing time still renders `—`, a missing place renders
    empty, with no `#` in front of it (`L-15`).
  - `L-21` names the kiosk holds it excludes; the `L-17` note lost its history.
  - **`A-09` was briefly deleted and `A-10` renumbered onto it. That is reverted.**
    Both keep the IDs they were given: the never-renumber rule in §0.1 has no
    exception for "nobody has adopted it yet", because the cost of finding out
    otherwise falls on the app repos, not on this one.

- **v1** — First statement of the mobile feature contract, taken from the cloud
  templates as of the FastAPI/plain-WebSocket server. Tracks `api.md` v1.
