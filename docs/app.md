# Splouch mobile — feature contract

**Contract version: `v2`** · Clients: the web phone pages (this repo), `Splouch-ios`,
`Splouch-android` (see §0.1).

This file is the *behaviour* contract: what a spectator sees and does on a phone, and
what drives it. It binds all three phone clients, the web pages included — where the web
and this text disagree, the web is behind.

[`api.md`](api.md) is the *data* contract — sockets, events, payload shapes.

---

## 0. How to use this file

### 0.1 Who owns what

| | Owns | Lives in |
| --- | --- | --- |
| **This file** | *what* each feature is, what drives it, whether it is required | Splouch (this repo) |
| **Parity ledger** | *whether* it is implemented on that client, and why not | [`web-parity.md`](web-parity.md) here; `parity.md` in `Splouch-ios` and `Splouch-android` |

Each client keeps a ledger, one row per ID below; **per-client status does not belong
here.** A ledger note says where a feature lives, what tests it and when it was last seen
on a device — why it exists is this file's job.

| Status | Meaning |
| --- | --- |
| `done` | built as this file describes, within the latitude of §0.4 |
| `deferred` | not built yet |
| `diverges` | built, deliberately not what this file says; the note says what instead, and why. **Temporary**: within a release this file absorbs it or the client reverts |
| `n/a — <reason>` | the level says it does not apply to this client (§0.3) |

IDs are the join key between the repos: **never renumber**. A retired feature keeps its
ID with a `**retired**` note; a new one takes the next free number in its section, and is
claimed here — a one-line row will do — before any ledger uses it.

### 0.2 What a client connects to

Phones connect to the **cloud relay** by default ([`api.md`](api.md) §3). An app, unlike
a web page, can also be pointed at another cloud or at a Pi on the pool's network
(`P-11`–`P-13`), and the kind changes the session's shape — a Pi has one meet and no
picker — so a client asks `GET /server`. Both servers render the same web templates,
differing only in whether `MEET_ID` is set:

| Surface | Web template |
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

Everything a phone needs is reachable as JSON ([`api.md`](api.md) §4). Each web page
renders from the helper its JSON endpoint returns (`_public_meet_list`,
`_build_heats_json`, `_picker_branding`, the Pi's `build_heats`), so a field added for one
reaches the other. Extend the helper, never the route.

**A Pi session is the same app at different addresses.** Once `GET /server` says
`kind: "pi"`, the cloud endpoints named below resolve as follows; nothing else changes:

| Needed for | Cloud | Pi |
| --- | --- | --- |
| meet config, theme, labels (`P-08`, `T-*`) | `GET /meet/{id}/config` → `settings` | `GET /config` (same keys, flattened) |
| meet name in the shell | `GET /meet/{id}/config` → `app_window_title`, then `name` | `GET /config` → `meet_title` |
| start list (`S-01`, and `S-09`'s index) | `GET /meet/{id}/schedule` | `GET /schedule.json` |
| strings (`T-05`) | `GET /i18n/{lang}` | `GET /i18n/{lang}` |
| `join_meet` (`C-02`) | on every connect | **never** — the Pi pushes on connect |
| `vid` (`C-10`) | one per server | none — nothing receives it |
| meet gone (`A-09`) | `GET /meet/{id}/config` → 404 | n/a — one meet, it cannot go away; an unreachable Pi is `C-03` |
| meet picker (`P-*`) | the launch screen | skipped — `P-11`'s server list is the only list |

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
| 28px edge strips for the swipe (`A-03`) | a full-width listener would swallow touches meant for the schedule list inside the `<iframe>` tab | the platform's own way of moving between peer sections — a full-width pager that follows the finger where that is the platform's idiom (`A-10`), the tab bar alone where it is not |
| `@media (orientation: …)` (`A-07`, `L-15`, `L-16`) | the only layout switch CSS had when the pages were written | the window's width — the full table from 600pt/dp/px |
| `sessionStorage['tab']` (`A-04`) | a browser page restores no state of its own | platform state restoration |
| 80px pull threshold, rotating indicator (`A-05`) | hand-rolled; the browser has no refresh control | the platform's refresh control |
| `env(safe-area-inset-*)` (`A-06`) | the only way a page learns where the notch is | safe-area layout guides — free |
| `<title>`, `apple-mobile-web-app-title`, manifest `name` (`A-08`) | the browser tab, and the installed icon's label | none — the store listing fixes the label (Android may set `TaskDescription`) |
| parent re-dispatches `resize`; `contentWindow.on_tab_shown()` (`L-14`, `R-10`) | an `<iframe>` is never told it was revealed | the on-appear callback |
| the `#edgeT` / `#filter-header` 65px alignment contract in `mobile.html` | two documents have to line up as one screen | none — it is one view |
| the ~220ms debounce on the filter search (`S-09`) | every keystroke was a request to the server for names the page already had | none — the index is local, so the wait is pure lag |
| one `scrollWidth`/`clientWidth` ratio, applied on a gated frame (`L-17`) | CSS cannot shrink text to fit | `UILabel.adjustsFontSizeToFitWidth`, Android `autoSizeTextType` |

The right-hand column is the requirement. Where the platform does better — `A-03`'s
gesture, `L-17`'s auto-shrink — the web is the floor. Where the platform's idiom is
narrower, the idiom still wins, and the row says which part of the behaviour survives.

**What a row requires, and what it leaves to the client.** A row requires an observable
outcome and its data source: what a spectator sees or does, in which words, driven by
which field. Unless the row says otherwise, these are the client's choice:

- **where** a control sits — which bar, the top or the bottom of the screen, a menu or a
  sheet;
- **which** platform component draws it — a system search field, a pager, a rail;
- sizes, spacing, type scale and the grouping of cells inside a row;
- how the layout adapts to the window, within the 600-wide split the rows name.

Using that latitude is `done`, not `diverges`. A row that needs a placement or a format —
`P-06` above the meets, `S-01`'s heading — says so.

---

## 1. Meet picker (`P`)

The entry screen. On the web it is the site root; in an app it is the launch screen, and
where the user returns via `A-02`.

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `P-01` | List of meets as cards: name, date, location, sport | `GET /meets` ([`api.md`](api.md) §5.6) | must |
| `P-02` | Per-meet picker image on the card, when the meet supplies one | `settings.picker_image_b64` → `GET /picker_image/{meet_id}` | should |
| `P-03` | Offline meets stay listed with a dimmed dot; opened, one shows its last scoreboard frame and an empty Results tab (`R-02`) | `offline`: retained, no relay connected | must |
| `P-04` | Empty state when no meets are active | `strings.no_meets` | must |
| `P-05` | Picker branding: a title, and a logo above or below it, sized from its own aspect ratio within the list's width under a height cap | `GET /picker/config` → `title`, `has_logo`, `logo_above`; `GET /picker_logo` (PNG, JPEG, GIF, WebP or SVG — read `Content-Type`) | should |
| `P-06` | Unofficial-results disclaimer **above** the meets, in full until folded to a pill that reopens it | `GET /picker/config` → `strings.results_disclaimer`, `results_disclaimer_short`, `notice_collapse` | **must** — see note |
| `P-07` | Privacy note, shown whenever attendance counting is on for this server — beside `P-06`, folding the same way | `strings.privacy_note`, pill `strings.privacy_note_short`, gated on `analytics_enabled` | must |
| `P-08` | Selecting a meet opens the app shell for it | `GET /meet/{id}/config` | must |
| `P-09` | Pull-to-refresh re-fetches the meet list | — | should |
| `P-10` | Install hand-off: store links to the native iOS/Android apps once they ship, Add-to-Home-Screen until then | — | web-only — see note |
| `P-11` | Choose the server from a list in the picker's menu. The meet list always names it; a meet names it when it is not the default | `GET /servers` ([`api.md`](api.md) §5.11), each checked with `GET /server` | native-only — see note |
| `P-12` | Servers on the local network are offered without anyone typing an address | mDNS browse for `_splouch._tcp` (do not use `splouch.local`) | native-only — should |
| `P-13` | A server can be added by hand, checked before it is saved | `GET /server` must answer | native-only — must |
| `P-14` | A server on other contract versions gets a one-line notice naming both; the app **connects regardless** | `GET /server` → `contract.api`, `contract.app` ([`api.md`](api.md) §5.10) | native-only — should |
| `P-15` | The reader's Appearance — Dark (default), Light or Automatic — chosen in the picker's menu, holding on every screen of every meet | the stored preference; the server's two palettes ([`api.md`](api.md) §6.1), never `settings.theme_colors`; `strings.appearance`, `appearance_dark` / `_light` / `_auto` | should — see note |
| `P-16` | Scanning a QR code adds a server: the app asks, and on a yes adds it, selects it and lands on the **meet list**. Without the app, the code's page offers the store | `https://<default host>/add?server=<origin>`; the host's two `/.well-known/` files and `GET /add` ([`api.md`](api.md) §4) | native-only — should |
| `P-17` | Search the meet list, from **3** meets, narrowing as the reader types, with its own empty state; the field goes where the platform puts search | matched locally over `GET /meets` → `name`, `meet_date`, `location`, `sport`, `organizer`; `strings.meet_search`, `no_meets_match` | should — see note |

> **`P-06` is not decoration.** It is the only thing between a live feed and a spectator
> treating it as a result, so it sits on the meet list, never in an About screen, and
> renders the server's text so the wording changes without a store review.
>
> **Both notices fold; neither goes away.** They sit above the meets, under the title and
> logo — below them a season of meets pushes them out of sight. Whether that is also above
> `P-17`'s field depends on where the platform puts search. The X folds a notice to its
> pill; the pill opens it again. Open, a notice has the row to itself; folded, the pills
> share one. The pill icons are the same on every client: an **hourglass** for `P-06`
> (SF `hourglass`, Material `hourglass_top`, Lucide `hourglass`) and **two people** for
> `P-07` (`person.2`, `group`, `users`) — never a shield or a raised hand, which read as
> a privacy control, and there is none.
>
> **Not a first-launch dialog, and not a consent.** A dialog accepted once would never
> show a second server's text or counting turned on later, and counting is not the
> reader's to refuse (`C-10`).
>
> - **A fold is remembered per server, against the exact words folded**, so a reworded
>   or re-translated notice shows in full once. The web's `localStorage` is per origin
>   already; an app keys on the server's origin.
> - **`P-07`'s fold is forgotten whenever the server reports counting off.**

> **`P-17` filters what the list already holds; there is no search endpoint.** `P-01`
> already has every meet, so each keystroke filters the cards, with no debounce (`S-09`).
>
> - Every word of the query, in any order, must be a substring of the meet's folded
>   fields joined by spaces — `quebec 2026` matches a location and a date.
> - Fold both sides with `S-09`'s four steps; the web uses `foldName()` from
>   `shared/static/js/fold.js`.
> - The organizer is searched but not shown.
> - `P-06` stays above the list whatever the filter hides.
> - The query survives a return from a meet (`A-02`) and `P-09`, not a cold launch: the
>   web keeps it in `sessionStorage`, an app while the picker is on its stack.
> - Keep the server's order — live meets first ([`api.md`](api.md) §5.6).

> **`P-10` is a hand-off.** The store URLs are `stores` in [`api.md`](api.md) §5.7, keyed
> by platform and present only once listed, so a listing that moves needs no deploy and an
> absent one hides the button. `P-16`'s `GET /add` draws from the same dict. Inside an app
> the slot renders nothing.

> **`P-11`–`P-13` — the server list is data.** The app ships knowing one URL, the default
> cloud; everything else is fetched or typed. At a pool the useful server is the Pi in the
> building — no internet dependency, an unthrottled race clock — and it publishes
> `_splouch._tcp`, so `P-12` is a browse.
>
> - **Ask `GET /server` before saving anything** (`P-13`): a typo fails at entry, and the
>   same answer says whether it is a Pi (no meet list) and which contract it speaks.
> - **`vid` is per server** (`C-10`).
> - **The picker always names the server; a meet names it when it is not the default**,
>   so a reader who switched and forgot can tell why the meets changed.

> **`P-14` — a notice, not a gate.** A newer server is additive and an older one
> degrades a feature (`L-12`'s clock against a v1 relay) rather than breaking the board.
> Show the line once per session, beside the server name (`P-11`); never block a connect.

> **`P-16` — the link shape is forced.** The code carries
> `https://<the app's default host>/add?server=<origin>`, the origin percent-encoded.
>
> - **Not a `splouch://` scheme.** Stock cameras will not open one, and it has no answer
>   for a reader without the app. An `https` link is caught by the installed app and
>   otherwise lands on `GET /add`, which offers the store (`P-10`).
> - **The host is the app's default server**, since links are verified per host and a Pi
>   has no certificate. A link naming another host does not parse: a server cannot mint
>   a code that adds a different server.
> - **The address inside is parsed exactly as a typed one** (`P-13`): `http` only for a
>   `.local` name or a developer loopback, then `GET /server` before anything is saved.
> - **Scanning proposes; it does not act.** The prompt names the address, and nothing is
>   asked of it before the yes. It asks only what is left: *add* an unknown server,
>   *switch* to a listed one, nothing for the one already in use and answering. A link
>   that does not parse still raises the prompt, with the reason.
> - **Every outcome ends on the picker**, where `P-06` is — a reader who arrived by
>   camera is the one who has never seen it.
>
> **A printed code names a cloud, never a Pi.** A `.local` name resolves only on the
> venue's wifi, and a poster cannot know its reader's network; the Pi is offered
> afterwards by `P-12`'s browse. This rules what a server mints, not what a client accepts.
>
> The server half — the two `/.well-known/` files and `GET /add` — is in
> [`api.md`](api.md) §4; deployment, fingerprints and the Pi's poster code in
> [`cloud.md`](cloud.md).

> **`P-15` — the reader's palette, not the meet's.** A choice the next meet could
> overrule is not a choice, so on a phone it replaces `settings.theme_colors` (`T-01`,
> `T-02`); a meet's club colours reach the kiosk and the Qt display only. Dark is the
> default, as the pages were before the choice existed.
>
> The two palettes are copied key for key from [`api.md`](api.md) §6.1, never re-picked
> by eye. The web keeps the choice in the `splouch_theme` cookie (`dark`, `light`,
> `auto`), Automatic sending both palettes behind `prefers-color-scheme`. The Pi's phone
> pages keep the operator's palette: with no picker (`T-08`), nothing sets the cookie
> there. An app stores the choice itself.

> **`P-12` — cleartext for the local network only**, through a scoped exception: iOS
> local networking (`NSLocalNetworkUsageDescription`, Bonjour service declared), Android
> `network_security_config` for `.local` and private ranges. Never a blanket exception.

> **Picker language is the device's, not a meet's.** The list spans meets that may each
> run in a different language, so `/picker/config` resolves from `?lang=` or
> `Accept-Language`. Per-meet language starts at `T-06`, once a meet is chosen.

---

## 2. App shell (`A`)

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `A-01` | Three tabs — Scoreboard, Results, Schedule — each with icon and label | `mobile.scoreboard` / `.results` / `.schedule` | must |
| `A-02` | Back affordance to the meet picker | label `mobile.back_to_meets` where the client draws its own; a platform back button labels itself | must |
| `A-03` | Move between tabs the platform's way: a tab bar, plus a swipe where swiping is the platform's idiom | web: tab bar and 28px edge strips | must — see note |
| `A-04` | The selected tab survives a relaunch | web: `sessionStorage['tab']` | should |
| `A-05` | Pull-to-refresh re-fetches config and rejoins the sockets | web: 80px threshold, rotating indicator | should |
| `A-06` | Content clears notch, Dynamic Island, and home indicator | web: `env(safe-area-inset-*)` | must (free natively) |
| `A-07` | On a short window the tabs stop costing height — compacted, or moved aside | the window's height; placement is the platform's (§0.4) | should |
| `A-08` | Window and home-screen title is the meet's `app_window_title`, falling back to its `name` | `settings.app_window_title`, then `name`, then `Splouch` | web-only |
| `A-09` | Meet gone mid-session → back to the picker | cloud: `GET /meet/{id}/config` answers **404**, checked on reconnect, foreground, `A-05` and `C-08`; web: `GET /mobile` 303s to `/`; Pi: n/a | must — see note |
| `A-10` | Where `A-03` has a swipe, the tabs follow the finger and settle on release | — | should — n/a without a swipe |
| `A-11` | A meet run with **no timing console** has no Results tab at all — not an empty one | `settings.console.timed` false (cloud: `GET /meet/{id}/config`; Pi: `GET /config`) | must — see note |

> **`A-03` is the platform's navigation; `A-10` is the pager where there is one.**
> Android's idiom is a full-width pager, which meets both rows. iOS switches its tab bar on
> tap — the HIG keeps swipe paging for page controls — so the tab bar alone is `A-03`
> there, and `A-10` does not apply. The web keeps its edge strips. No client gives up its
> real tab bar to imitate another platform's gesture (§0.4).
>
> **The leading edge belongs to the system.** Where the platform owns an edge gesture —
> an interactive back, the leading ~24pt — the tab gesture leaves it alone; a full-width
> pager and `A-02`'s back swipe cannot share that edge.

> **`A-09` has no socket signal.** `join_meet` for a meet the cloud no longer holds is
> silently ignored, so the app asks: a 404 on the config fetch it already makes at those
> moments means gone. Any other failure is a network fault (`C-03`), and a silent socket
> is not the signal.

> **`A-11` — the tab goes, not its contents.** With no timing console the operator drives
> the boards from the Pi's `/manual` page and no `results_snapshot` is ever sent
> ([`api.md`](api.md) §2.3), so a Results tab would wait all meet and `R-01`'s line would
> be false.
>
> - Re-evaluate on every config fetch — reconnect, foreground, `A-05`, `C-08` — since the
>   operator can switch consoles mid-meet. Keep the spectator on a tab that still exists
>   (`A-04` stores a choice, not an index).
> - Read `console.timed`, never `console.key`. A server too old to send `console` has a
>   console.
> - Nothing else changes: the Scoreboard and Schedule tabs are the same either way.

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
| `L-11` | A running lane's time is styled distinctly; on stop it plays a one-shot "locked" transition, cancelled if it runs again | `lane_running<i>` false-edge | **must** — it tells a live clock from a frozen split |
| `L-12` | Every running lane shows the **race clock**: one value per heat, re-based by the server, ticked by the device | `running_time` + `lane_running<i>` + `meet_live` — see note | **must** |
| `L-13` | An event or heat change blanks times, deltas and places — except after a running lane, and for the first heat after a connect | `current_event` / `current_heat`, compared as strings | must — see note |
| `L-14` | Returning to the tab re-runs layout and refreshes the clock | web: parent re-dispatches `resize` | must (native: on-appear) |
| `L-23` | While a lane swims, its **delta cell** shows its lengths in the header's accent colour; the delta takes the cell back at the finish. The header never changes | `lane_splits<i>`, gated on `settings.show_laps`; `settings.lap_direction` | should — see note |

> **`L-23` — one cell, two tenants.** (Out of sequence: `L-15`–`L-22` were taken.) A lap
> is not a result, so it gets no column and never borrows the place's — `#3` and `3` a
> length apart read the same. The delta column is empty until the finish, so the handover
> is the signal: the colour changes, the header does not.
>
> **Show a lap only when all of these hold:**
>
> | condition | why |
> | --- | --- |
> | `settings.show_laps` | off by default; not every console's count is exact |
> | `lane_splits<i> > 0`, **or** counting down in a lane with a swimmer | counting up waits for the first wall; counting down shows from the start, but never in an empty lane |
> | the lane has no place | the finish ends the lap, delta or not |
> | the delta is empty | for the frame where both arrive together |
>
> **Direction** is `settings.lap_direction`: `up` shows the console's count, `down`
> shows `expected_splits` minus it, clamped at 0; with `expected_splits` 0 (no distance
> in the meet file) `down` falls back to `up`. **`split_step`** (§5.1) is 2 in a pool
> with touchpads at one end, so the count arrives in twos. Counts are exact from a Quantum
> or an Omnisport 2000, inferred on a CTS Gen6 and absent on a Gen7 or an ARES 21; the
> operator corrects drift with `adjust_splits`, which is why the setting ships off.

> **`L-12` — one clock per heat, re-based by the server, ticked by the device.** There
> is no per-lane elapsed time: every running lane shows the same figure, and a lane's own
> time exists only at its split, `lane_time<i>`.
>
> | `lane_running<i>` | Race clock | Time cell | Lane number |
> | --- | --- | --- | --- |
> | true | re-based within 3 sync intervals | the race clock, ticked by the device | normal |
> | true | none yet — joined mid-heat, or the heat just changed | — | **pulse** |
> | true | silent for 3 sync intervals | frozen where it stopped | **pulse** |
> | false | — | `lane_time<i>`, held until the lane runs again | normal |
> | either | `meet_live` false, or disconnected (`C-09`) | last value, held | normal |
>
> The pulse cycles the lane number between the row colour and the timing colour; let a
> cycle finish before stopping it, or the whole column flicks at once.
>
> | Event | Effect on the clock |
> | --- | --- |
> | a `running_time` frame | hard re-base; never ease towards it |
> | between frames | the device advances it ~10Hz off the platform's display link, from a *monotonic* clock |
> | a `lane_running<i>` edge, either direction | re-bases — the relay forces a `running_time` onto that frame |
> | event or heat change | stops the ticker; what replaces the digits is `L-13`'s call |
> | 3 sync intervals with no `running_time` | stop the ticker, freeze the digits, fall back to the pulse |
> | tab or app backgrounded | stop the ticker; never accumulate ticks across a suspend, and do not resume from a stale base |
> | `meet_live` false, or a disconnect | every clock on the board stops, so stale lane state cannot masquerade as a live race |
>
> - **Never start or reset the clock from a lane edge**; `lane_running<i>` only decides
>   whether lane *i* shows it. A split freezes the lane, never the clock.
> - **Show tenths** (`1:02.4`); the value carries the relay's near-constant latency.
> - **Freeze forward, never blank**: three intervals past the last re-base.
> - The relay forwards `running_time` **at most every ~2s, plus on any frame carrying a
>   `lane_running<i>` key**, which is why the device ticks in between.
> - Parse `m:ss.hh` or `ss.hh` ([`api.md`](api.md) §5.1); a value that does not match is
>   no re-base — not a freeze, not a blank.

> **`L-13` — three cases, two of them exceptions.**
>
> | Frame carries a new event or heat, and… | Do |
> | --- | --- |
> | it is the first event/heat this connection has seen | nothing — it is the **baseline**. The join replay (cloud) or the connect snapshot (Pi) carries the current heat's times beside its number; blanking them throws away the only state a late joiner has |
> | a lane was running on the previous frame | keep every time as a result: the console advanced before it published results, and the next frame moves on. Blanking here erases what the swimmers just posted |
> | otherwise | blank all times, deltas and places; names and clubs arrive in the same frame |
>
> A lane running on *this* frame outranks all three (`L-12`). Start the remembered event
> and heat as *unseen*, not `0`, or the join replay reads as a change.

### 3.3 Layout

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `L-15` | **Under 600 wide**: a two-line row — lane number spanning; name, club right; time, delta, place (`#`-prefixed, nothing when empty) | the window's width | must |
| `L-16` | **600 wide and up**: the full table with a header row, type scaled to each lane's height; on a short window the header row goes first | the window's width | should |
| `L-17` | Long names shrink to fit their cell, ellipsis only as a floor | — | must — see note |
| `L-24` | A crowded board under 600 wide gives up, in order: the header row to the top bar → the relay line → type down to 0.72× → scrolling | measured row heights | should — see note |

> **`L-15` / `L-16` — width decides, not orientation.** The full table from **600**
> points, dp or CSS px: on a phone that means landscape; on a tablet, either way up.
> Android: `WindowWidthSizeClass` leaving `Compact`. Web: `min-width: 600px`. iOS: the
> width itself, **not** `horizontalSizeClass`, which stays compact on most iPhones held
> sideways.

> **`L-24` — cheapest first, each step only if the last was not enough.** (1) The
> EVENT/HEAT row moves into the top bar, short labels, no clock — it costs the meet's title
> and `P-11`'s server line, so only on need. (2) The relay line goes. (3) The row's type
> shrinks as one, to 0.72× at most. (4) The board scrolls. Decide from measured heights,
> and decide the bar from the height the lanes would have *with* the row in it, so the
> choice cannot oscillate. The web has no top bar and starts at step 2.

> **`L-17` — shrink, here and on Results (`R-08`).** Rows share the board's height, so
> shrinking a name changes only its type size. Keep the re-fit off the per-frame path:
> names arrive on a heat change, so the web re-fits on a `lane_name` frame, a resize and a
> tab reveal. A platform's own auto-shrink (§0.4) does it better.

### 3.4 Not on this tab

| ID | Feature | Level |
| --- | --- | --- |
| `L-18` | Carousel / fullscreen image overlay | n/a — images are local to the Pi and are never relayed |
| `L-19` | Podium highlight animation | n/a — Pi-local, `race_finished` is not forwarded |
| `L-20` | Animated column show/hide, operator-driven | n/a — cloud columns are always visible |
| `L-21` | Any timed hold on a state — the kiosk's `brief_results` flash, its results pause and debounce | n/a — the phone shows the last frame received, so a late joiner cannot fall out of step; `L-12`'s clock gates no transition |
| `L-22` | Independent per-lane clock, each lane timing its own length | n/a — the console has one race clock and the lanes mirror it; a lane's own figure exists only as its split, `lane_time<i>`. See `L-12` |

---

## 4. Results tab (`R`)

The whole tab is absent for a meet with no timing console (`A-11`); everything below
describes it where it exists.

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `R-01` | Until the first snapshot: "Waiting for results…" in place of the table, not a table of blank rows | `mobile.waiting_results` | must — see note |
| `R-02` | A disconnect, or `meet_live` going false, **wipes the board** and returns it to that state | `disconnect`, `meet_live` | must |
| `R-03` | Header shows the snapshot's own event, heat, and event name | `results_snapshot` | must |
| `R-04` | Same six columns and visibility flags as the Scoreboard tab | shared config | must |
| `R-05` | **Lane sort**: row index = `channel`; a lane with no final time leaves its row blank | `sort == "lane"`, and when `sort` is absent | must |
| `R-06` | **Place sort**: rows fill top-down as a ranking | `sort == "place"` | must |
| `R-07` | A missing time renders as `—`, not blank; a missing **place** renders empty — no dash, and no `#` in front of it | — | should |
| `R-08` | Long names shrink to fit rather than clipping | — | should — see `L-17` |
| `R-09` | Final times carry the "locked" styling | `r.time` non-empty | should |
| `R-10` | Returning to the tab re-joins the meet, reconnecting first if needed | web: `on_tab_shown` | must |

> **`R-01` — the line replaces the table.** Blank rows on the Scoreboard mean a heat is
> filling in (`L-09`); before the first snapshot Results has no heat to fill, so the line
> is the screen and the table arrives with the data. `R-02`'s wipe returns to exactly
> this. `mobile.waiting_results` promises results rather than reporting their absence.

---

## 5. Schedule tab (`S`)

The richest screen, and the only one with real client-side state. A spectator uses it to
find *their* swimmer among several hundred.

### 5.1 The list

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `S-01` | Every heat as a card, heading on one line: `EV 12  HT 3` in the **short** labels, the event name, the scheduled time trailing | `GET /meet/{id}/schedule` ([`api.md`](api.md) §5.8); Pi: `GET /schedule.json` | must |
| `S-02` | Each card lists its lanes: lane number, name, club, seed time | `lanes[]` | must |
| `S-03` | Relay entries show member first names joined by `·` | `lane.swimmers[].first`, falling back to `.name` | should |
| `S-04` | Alternating card backgrounds, computed over *visible* cards so filtering keeps the stripe | — | should |
| `S-05` | The current heat is highlighted | `update_scoreboard.current_event` / `current_heat` and `results_snapshot.event` / `heat`, compared **as strings** ([`api.md`](api.md) §5.1) | must |
| `S-06` | The list auto-scrolls to the current heat once per appearance | re-armed on returning to the foreground | must |
| `S-07` | Empty state when no meet file is loaded | `mobile.no_schedule` / `mobile.no_meet` | must |

> **`S-01` — short on the card, long out loud.** The identifier repeats on every card and
> its width is the event name's, so the card takes the short labels (the `short` table of
> `GET /i18n/{lang}`, or the short form of `settings.labels`); the board keeps the long
> ones (`T-09`). The doubled space groups `EV 12` against `HT 3` — no dash, they are not a
> range. A heat with no scheduled time draws nothing for it. A screen reader hears
> `EVENT 12, HEAT 3, <name>, <time>`.

> **`S-05` reads the current heat off the *other* two sockets, on purpose.** This tab has
> no feed of its own for it — `/ws/schedule` only signals that the start list changed
> (`S-21`) — so the page opens all three and takes the event/heat from whichever speaks
> last.

### 5.2 Filtering

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `S-08` | Full-screen filter sheet, opened from a button in the top bar | — | must |
| `S-09` | Typeahead search over swimmers and clubs, answered locally and without delay | an index built from `S-01`'s `heats[]` — `lane.name`, `lane.club`, `lane.swimmers[].name` | must — see note |
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

> **`S-09` builds its own index; there is no endpoint.** `GET /search_suggestions` was
> removed ([`api.md`](api.md) §7): everything it read is in `S-01`'s payload, and
> answering from the server's list could offer a name this one lacked. Build from what
> you rendered; rebuild with `S-21`.

> **The index.** One entry per distinct `lane.name` (relay team names included) and
> `lane.swimmers[].name`, each with its lane's club — a name in two clubs keeps the later
> one — then one per distinct club. Match a substring of the folded name against the
> folded query. Swimmers before clubs, each by name, cut to 20.

> **The fold, in four steps:** lowercase; NFD-decompose; expand the 17 letters below,
> which have no canonical decomposition and would otherwise be deleted by the next step;
> drop every codepoint above `U+007F`.
>
> | | | | | | | | |
> | --- | --- | --- | --- | --- | --- | --- | --- |
> | `ß`→`ss` | `æ`→`ae` | `ð`→`d` | `ø`→`o` | `þ`→`th` | `đ`→`d` | `ħ`→`h` | `ı`→`i` |
> | `ĳ`→`ij` | `ĸ`→`k` | `ŀ`→`l` | `ł`→`l` | `ŉ`→`n` | `ŋ`→`n` | `œ`→`oe` | `ŧ`→`t` |
> | `ſ`→`s` | | | | | | | |
>
> Not `String.folding(.diacriticInsensitive)`, nor a regex over `U+0300`–`U+036F`: both
> skip step 3, and `Île-des-Sœurs` would index as `ile-des-surs`. Every client folds
> identically.

> **`S-16` answers "when does my kid swim next?"** Filters on and All-heats off shows only
> their heats; on, the whole running order returns with their lanes still highlighted.

> **`S-17` cuts by position, not by the clock** — scheduled times are planning figures.
> With the current heat unknown or not in the list, the toggle changes nothing.

### 5.3 Refresh

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `S-21` | A new schedule from the Pi refreshes the list | `schedule_update` on `/ws/schedule` → re-fetch `GET /meet/{id}/schedule` | must |

> `schedule_update` carries no payload; re-fetch `GET /meet/{id}/schedule`. Keep the
> active filters whose names still exist. An empty `heats` is "loaded, no schedule yet":
> show `S-07`, not an error.

---

## 6. Connection and session (`C`)

The rules in [`ws.js`](../shared/static/js/ws.js). These are the difference between an
app that works on a pool deck and one that shows a frozen board after a screen lock.
Each of the three sockets runs this loop independently:

```text
  connect
     │
     ├─► join_meet {meet_id, vid}      cloud only: on every connect and reconnect     C-02
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
| `C-02` | `join_meet {meet_id, vid}` on **every** connect and reconnect — cloud only; a Pi pushes on connect | `GET /server` → `kind` | must |
| `C-03` | Automatic reconnect, capped exponential backoff (web: 500ms → 5s) | — | must |
| `C-04` | Heartbeat `ping` every 15s; no inbound frame for 35s means dead — close and reconnect | server replies `pong` | must |
| `C-05` | On foreground or network-restored: probe with a `ping`; no `pong` within ~4s means dead | — | **must** |
| `C-06` | Frames sent while disconnected are queued and flushed on connect | — | should |
| `C-07` | Unknown events are ignored, not treated as errors | — | must |
| `C-08` | `reload` → re-fetch config and redraw (web: full page reload) | — | must |
| `C-09` | `meet_live` gates live affordances; a `disconnect` implies `meet_live = false` | — | must |
| `C-10` | Anonymous **per-server** id (`vid`) sent with `join_meet` | a random UUID per normalised origin, created on first use | must — see note |

> **`C-05` is the one that bites phones.** iOS and Android freeze background sockets
> without a close, so backoff never starts and the board sits frozen after a screen lock.
> The foreground probe is what reconnects; `C-03` alone is not enough.

> **`C-10` — privacy is binding.** A random UUID per server origin (scheme, host, port),
> stored locally, created on the first `join_meet` to that origin and used only for
> `COUNT(DISTINCT)` attendance. A Pi never gets one (`C-02`). Never derive one `vid` from
> another, or send one to a different server.

---

## 7. Theme and language (`T`)

A meet chooses its faces (`T-03`) and its words (`T-04`). Its colours reach the kiosk
and the Qt display; on a phone the reader's Appearance (`P-15`) replaces them with one of
the server's two palettes (`T-01`, `T-02`). Either way the client has no look of its own:
every colour, face and word it draws on the board comes from the server.

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `T-01` | The board's palette — `bg`, `header_*`, `th_*`, `row_*`, `time`, `delta_*` — is one of the server's two, chosen by `P-15`; a meet's `theme_colors` reach the kiosk and the Qt display only | [`api.md`](api.md) §6.1, key for key | must |
| `T-02` | The schedule's colours — `schedule_event`, `schedule_time`, `schedule_name`, `schedule_club` — from the same palette as `T-01` | [`api.md`](api.md) §6.1 | should |
| `T-03` | Three font roles — `family` (text), `digits` (clock), `timing` (times and deltas) | `settings.theme_fonts` | must |
| `T-04` | Column headers and header labels are the server's words, never the app's | `settings.labels` for the default; `GET /i18n/{lang}` → `labels` when the user has chosen | must — see note |
| `T-05` | The app's own chrome — tab names, empty states, filter UI — is **fetched and cached**, not translated in the app | `GET /i18n/{lang}` → `mobile` ([`api.md`](api.md) §5.9) | must |
| `T-06` | Language defaults to the **meet's** locale and the user may override it | `settings.locale`, then the stored preference | must |
| `T-07` | Missing theme keys fall back to the defaults rather than rendering unstyled | the two palettes and the default faces in [`api.md`](api.md) §6.1 | must |
| `T-08` | A language control, per device, applying to every meet opened afterwards | `GET /locales` for the list | should — see note |
| `T-09` | The board's EVENT and HEAT read **long** on every client; an optional per-device control may switch those two only | the stored preference; words from `GET /i18n/{lang}` → `labels` | should — see note |
| `T-10` | A built-in snapshot of the strings is the floor: compiled into the app, refreshed from the server, cached to disk | — | must — see note |
| `T-11` | The event name follows the chosen language, composed from parts the server sends | `update_scoreboard.event_name_parts` + `GET /i18n/{lang}` → `event_name`; falls back to `event_name` | should — see note |

> **`T-03`**: the bundled faces are in [`shared/static/fonts/`](../shared/static/fonts/)
> — Overpass Mono, DSEG7 Classic, DSEG14 Classic, Share Tech Mono, Orbitron, Roboto Mono.
> Apps embed them rather than downloading, and fall back to a system monospace for an
> unknown name.

> **`T-04` is not a translation.** The server resolves each label from the meet's
> `locale` and the operator's `label_style`, so the app never holds the table:
>
> | | `long` | `short` |
> | --- | --- | --- |
> | `locale = "en"` | `EVENT` · `HEAT` | `EV` · `HT` |
> | `locale = "fr"` | `ÉPREUVE` · `SÉRIE` | `ÉP` · `SÉR` |
> | `locale = "es"` | `PRUEBA` · `SERIE` | `PR` · `SER` |
>
> Only EVENT and HEAT have a long form (`T-09`). Render `settings.labels` as sent, or,
> once the reader has chosen a language, that language's `GET /i18n/{lang}` → `labels`.

> **Never translate what the server sent.** `labels` and `event_name` arrive in the
> meet's language; another language is asked of the server (`T-05`, `T-08`), never
> produced by the client.

> **`T-05` — whose word is it?** A word the web page also shows is the server's, in
> `[mobile]`: tab names, empty states, the filter sheet, the picker's chrome and controls,
> the notices. A word about the app or the device is the app's, translated natively: the
> server sheet, "nearby", connection and address errors, OS requirements, standard
> buttons. English fills a language the native table lacks.

> **`T-08` — one choice per device, made on the picker**, where every meet is in view.
> The web stores it in the `splouch_lang` cookie; `?lang=` wins for one request, and the
> shell writes it to the cookie. An app stores it and sends `lang`. The Pi has no picker
> (§0.2), so its phone pages follow the operator's language.

> **`T-09` — EVENT and HEAT, nothing else.** Lane and place are the narrow columns and
> resolve short whatever the style; the server's `long` table already says so, so a
> client renders what it is given. Long because `EV` / `HT` must be decoded and the phone
> header has room (`L-01`); short is the kiosk's. A control, if offered, has two options,
> never a "meet default" — `settings.label_style` is how the server resolves `labels`, not
> a reader's choice. A client that withdraws the control keeps the stored choice,
> answering long until it returns; the web ignores rather than clears `splouch_style` and
> `?style=`.

> **`T-10` — fetch, but never depend on the fetch.** Draw from the cache and
> revalidate in the background. Resolve each key, first hit wins:
>
> ```text
> key ─► cached server value ─► built-in value ─► built-in English ─► the key's own name
> ```
>
> **Built-in** is compiled into the app — not the server's bundled `shared/locales/` —
> and carries English, so a first launch, an offline start, or a key an older server
> lacks still lands. It is **captured, never transcribed**: the verbatim
> `GET /i18n/{lang}` body for each language in `GET /locales`, written by a script in
> the app repo before each release and whenever `shared/locales/` changes. The build-time
> file and the run-time cache share one shape and one decoder.

> **`T-11` — the app joins, it does not parse.** The server decomposes each event name
> into `event_name_parts` — distance, stroke, relay, gender, age — beside `event_name`
> ([`api.md`](api.md) §5.1), and `GET /i18n/{lang}` → `event_name` holds the words. Given
> `{dist: "200", stroke: "backstroke", gender: "girls", age: "< 12"}` and Spanish,
> `200 m espalda  —  Niñas < 12` is a lookup and a join: `dist + unit`, stroke, relay,
> `separator`, gender, age. Never re-implement the decomposition. Fall back to
> `event_name` when the parts are absent or compose to nothing.

---

## 8. Accessibility (`X`)

Taken from what the two native clients built independently and agreed on. Each rule is
about what a screen reader, a large text size or a less steady finger meets; none is a
platform API. "Heard" — checked under VoiceOver or TalkBack by ear — is a ledger fact,
not a level here.

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `X-01` | A board lane is **one** accessibility element saying the whole lane in the server's column words; an empty lane says only its number | `labels` (`T-04`) | must |
| `X-02` | The EVENT and HEAT words and their numbers read as one each, and say nothing before a number arrives | — | must |
| `X-03` | A start-list lane is one utterance in the same words, seed time included; a heat's heading is one utterance in the **long** words (`S-01`) | `labels` | should |
| `X-04` | Heat headings, the picker's title and every empty-state title are headings, so the reader can jump heat to heat | — | should |
| `X-05` | Every tappable target is at least the platform's minimum — 44pt on iOS, 48dp on Android, 44px on the web — the filter chip's × included, without growing the chip | — | must |
| `X-06` | In a list of choices — server, language, Appearance — the current one is announced as selected, not only marked with a glyph | — | must |
| `X-07` | Decorative glyphs beside text that already says the same thing are hidden; anything laid out only to be measured never reaches the accessibility tree | — | should |
| `X-08` | Text off the board follows the device's text size; the board sizes from its height and does not scale twice. On the web: at 200% zoom and 320px wide, nothing is cut and nothing scrolls sideways | native: the text-size setting; web: browser zoom (WCAG 1.4.4, 1.4.10) | should |
| `X-09` | Decorative motion — the picker's live dot — honours the reduce-motion setting. `L-11`'s lock flash and `L-12`'s pulse are information, not decoration, and may keep running | — | should |
| `X-10` | When a control replaces itself — `P-06`'s X folding to a pill, the pill opening again — focus moves to its replacement | — | should |

> **`X-01` — the lap count's word is the client's own.** No column names it (`L-23`), so
> it is spoken as *Laps 4* — an app's native string, the web's `[mobile] spoken_laps` —
> never as a bare number heard as a second time.

> **Contrast is checkable once**, being the palettes' (`P-15`). Known shortfall: dark
> `th_text` `#666666` on `row_even` `#202020` is 2.84:1, under the 3:1 large-text bar;
> the fix belongs in the server's table.

## 9. Out of scope

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

- **v2** (2026-09-30) — the contract reconciled with the two native apps, which had
  built ahead of it and recorded each departure as `diverges`. **`contract.app` is `v2`**,
  so `P-14` tells a v1 client. Nothing a v1 client did breaks against a v2 server; what
  changed is what counts as parity.

  - **Model.** The web is a client of this file, not its reference (§0.1, §0.2), with
    its own ledger, [`web-parity.md`](web-parity.md). `diverges` is a defined status
    and a temporary one. §0.4 says what a row leaves to the client: placement,
    component, sizes and grouping are latitude.
  - **Changed rows.** `A-03` is the platform's own navigation between peer sections —
    a tab bar alone on iOS, a pager on Android — and `A-10` applies only where there
    is a swipe. `A-07` is written on the effect; `L-15` and `L-16` switch at 600
    wide — points, dp or CSS px — not on orientation, nor on iOS's size class. `T-09`:
    the board's EVENT/HEAT read long on every client. `S-01`'s heading is the short
    `EV 12  HT 3`, spoken long. `P-11`: the picker always names the server. `P-17`'s
    field goes where the platform puts search, and `P-06` is required above the meets
    rather than above that field. `P-03`: an offline meet shows its last scoreboard and
    no results. `L-23`'s "centred" applies where the delta is a column. `T-01`: the
    palettes are copied from [`api.md`](api.md) §6.1, dark `header_label` `#3b9eff` —
    both apps had white, a transcription slip, now fixed and tested key for key.
  - **New rows.** `L-24`, the crowded-board order; §8, accessibility (`X-01`–`X-10`),
    where `X-08` holds the web to browser zoom — 200% and a 320px window with nothing
    cut and nothing scrolling sideways — rather than to the OS text size.
  - **New strings.** `[mobile] spoken_laps` (`X-01`) and `filter_done` (`X-06`): the
    web's words for the lap count read aloud and the filter sheet's ✓. Additive; an app
    that ignores them is unaffected.
  - **Folded in from "added since v1"**: `P-15` (the reader's Appearance, departing from
    `T-01`/`T-02` on purpose), `P-16` (a server added from a QR code; a printed code
    names a cloud, never a Pi, so the reader lands on the picker and `P-06`), `P-17`
    (meet search, live meets first), `A-11` (no Results tab without a timing console).
    `P-15` was claimed by the app ledgers before it was written here, which is why
    `P-16` was numbered around it; §0.1 now has IDs claimed here first.
  - **And the v1 revisions that were waiting on this bump**: `A-03`'s split from
    `A-10`, `R-01`'s waiting line *instead of* the empty grid, `T-09`'s two options
    starting from long.
  - **Moved out.** `P-16`'s server obligations live in [`api.md`](api.md) §4 and
    [`cloud.md`](cloud.md); the default palettes in [`api.md`](api.md) §6.1.

- **v1, clarified while the iOS app was built** (no bump — nothing a conforming
  client did became wrong): `L-13` states its two exceptions, the running-lane hold
  and the post-connect baseline; `A-09` names its cloud signal, a 404 on the config
  fetch; `C-02` and `C-10` are gated on `kind`; `S-05` compares as strings; `T-10`
  says how the snapshot is made; `P-14` added; §0.2 gains the Pi-session table,
  with `GET /schedule.json` added on the Pi to honour the no-HTML-only rule.

- **v1** — First statement of the mobile feature contract, taken from the cloud templates
  as of the FastAPI/plain-WebSocket server. Tracks `api.md` v2.
