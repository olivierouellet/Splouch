# Splouch mobile — feature contract

**Contract version: `v2`** · Clients: the web phone pages (this repo), `Splouch-ios`,
`Splouch-android` (see §0.1).

This file is the *behaviour* contract: what a spectator can see and do on a phone, and
what drives it. It is normative for all three phone clients, the web pages included:
the web is a client of this file, not its reference, and where the web and this text
disagree the web is the one that is behind.

[`api.md`](api.md) is the *data* contract — sockets, events, payload shapes.

---

## 0. How to use this file

### 0.1 Who owns what

| | Owns | Lives in |
| --- | --- | --- |
| **This file** | *what* each feature is, what drives it, whether it is required | Splouch (this repo) |
| **Parity ledger** | *whether* it is implemented on that client, and why not | [`web-parity.md`](web-parity.md) here; `parity.md` in `Splouch-ios` and `Splouch-android` |

Each client keeps a short ledger, one row per ID below. **Per-client status does not
belong here.** A ledger note says where the feature lives, what tests it, and when it
was last seen on a device — not why the feature exists, which is this file's job.

| Status | Meaning |
| --- | --- |
| `done` | built as this file describes, within the latitude of §0.4 |
| `deferred` | not built yet |
| `diverges` | built, deliberately not what this file says. The note names what the client does instead and why. **A divergence is temporary**: within a release, either this file absorbs it (and the row becomes `done`) or the client reverts. A `diverges` row with no open change against this file is a stale ledger |
| `n/a — <reason>` | the level says it does not apply to this client (§0.3) |

The IDs are the join key between the repos, so **never renumber**. A retired feature
keeps its ID and gains a `**retired**` note; new ones take the next free number in
their section. An ID a ledger needs before this file has it is claimed here first —
a one-line row is enough — never in the ledger.

### 0.2 What a client connects to

Phones connect to the **cloud relay** by default, not the Pi ([`api.md`](api.md) §3),
but an app is not fixed to one server the way a web page is fixed to its origin: it can
be pointed at another cloud, or at a Pi on the pool's own network (`P-11`–`P-13`). Which
one changes the shape of the session, not just the address — a Pi has one meet and no
picker — so a client asks `GET /server` rather than inferring it. Both servers render
the *same* web templates, differing only in whether `MEET_ID` is set. They are the web
client, listed here because the payloads below were first shaped by them:

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

Everything a phone needs is reachable as JSON — no screen is HTML-only, nothing requires
scraping ([`api.md`](api.md) §4). Each browser page renders from the same helper its
JSON endpoint returns (`_public_meet_list`, `_build_heats_json`, `_picker_branding`,
the Pi's `build_heats`), so web and native cannot drift: a field added for one appears
in the other by construction. Extend the helper, never the route.

**A Pi session is the same app with different addresses.** `GET /server` says
`kind: "pi"`; from then on the rows below that name a cloud endpoint resolve as follows,
and nothing else about the session changes:

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

The right-hand column is the requirement. Where the platform does the job better than the
web can — `A-03`'s gesture, `L-17`'s auto-shrink — matching the web is the floor, not the
target. Where the platform's own idiom is *narrower* than the web's mechanism, the idiom
is still the requirement: a native equivalent that no platform convention supports is a
workaround in the other direction, and the row says which half of the behaviour survives
rather than asking for all of it everywhere.

**What a row requires, and what it leaves to the client.** A row's requirement is its
observable outcome and its data source: what a spectator can see or do, with which words,
driven by which field. Unless the row says otherwise, these are the client's own choice:

- **where** a control sits — which bar, the top or the bottom of the screen, a menu or a
  sheet;
- **which** platform component draws it — a system search field, a pager, a rail;
- sizes, spacing, type scale and the grouping of cells inside a row;
- how the layout adapts to the window, within the 600-wide split the rows name.

A client exercising that latitude is `done`, not `diverges`. A row that needs a
placement or a format — `P-06` above the meets, `S-01`'s heading — says so in its text.

---

## 1. Meet picker (`P`)

The entry screen. On the web it is the site root; in an app it is the launch screen, and
where the user returns via `A-02`.

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `P-01` | List of meets as cards: name, date, location, sport | `GET /meets` ([`api.md`](api.md) §5.6) | must |
| `P-02` | Per-meet picker image on the card, when the meet supplies one | `settings.picker_image_b64` → `GET /picker_image/{meet_id}` | should |
| `P-03` | Offline meets stay listed, marked with a dimmed status dot. Opened, an offline meet shows its last scoreboard frame; its Results tab is empty (`R-02`) | `offline` = meet retained but no relay connected | must |
| `P-04` | Empty state when no meets are active | `strings.no_meets` | must |
| `P-05` | Picker branding: title, logo, logo above or below the title | `GET /picker/config` → `title`, `has_logo`, `logo_above`; image at `GET /picker_logo` — PNG, JPEG, GIF, WebP or SVG, read the response's `Content-Type` rather than assuming. Size it from its own aspect ratio: operators upload both wide banners and square badges, so fit it inside the list's content width with a height cap rather than a fixed box | should |
| `P-06` | Unofficial-results disclaimer **above** the list, in full until the reader folds it with an X to a pill that opens it again | `GET /picker/config` → `strings.results_disclaimer`, pill `strings.results_disclaimer_short`, X label `strings.notice_collapse` | **must** — see note |
| `P-07` | Privacy note, shown whenever attendance counting is on for this server — beside `P-06`, folding the same way | `strings.privacy_note`, pill `strings.privacy_note_short`, gated on `analytics_enabled` | must |
| `P-08` | Selecting a meet opens the app shell for it | `GET /meet/{id}/config` | must |
| `P-09` | Pull-to-refresh re-fetches the meet list | — | should |
| `P-10` | Install hand-off: store links to the native iOS/Android apps once they ship, Add-to-Home-Screen until then | — | web-only — see note |
| `P-11` | Choose which server to connect to, from a list, in the picker's menu. The meet list always names the server in use; inside a meet it is named when it is not the default | `GET /servers` ([`api.md`](api.md) §5.11), each entry verified with `GET /server` | native-only — see note |
| `P-12` | Servers on the local network are offered without anyone typing an address | mDNS browse for `_splouch._tcp` (do not use `splouch.local`) | native-only — should |
| `P-13` | A server can be added by hand, checked before it is saved | `GET /server` must answer | native-only — must |
| `P-14` | A server whose contract versions differ from the app's gets a one-line notice naming both; the app **connects regardless** | `GET /server` → `contract.api`, `contract.app`, compared for equality with the versions the app was built against ([`api.md`](api.md) §5.10) | native-only — should |
| `P-15` | The reader's Appearance — Dark, Light or Automatic — chosen in the picker's menu and holding everywhere: the picker, the shell and every tab of every meet. **Dark is the default**, and Automatic follows the device | the stored preference; the two palettes are the server's own — [`api.md`](api.md) §6.1 — never the meet's `settings.theme_colors` (`T-01`); menu words `strings.appearance`, `appearance_dark`, `appearance_light`, `appearance_auto` | should — see note |
| `P-16` | A server can be added by scanning a QR code: the code opens the app, the app asks, and on a yes the server is added, selected, and the **meet list** is what the reader lands on. Without the app installed the same code lands on a web page offering the store | `https://<the app's default host>/add?server=<origin>`, percent-encoded; the host's `/.well-known/assetlinks.json` and `/.well-known/apple-app-site-association`, and its `GET /add` page ([`api.md`](api.md) §4, §5.7) | native-only — should |
| `P-17` | Search the meet list, offered once the list holds **3 or more** meets, narrowing it as the spectator types, with its own empty state when nothing matches. The field goes where the platform puts search (§0.4) | matches locally over `GET /meets` fields `name`, `meet_date`, `location`, `sport`, `organizer`; placeholder `strings.meet_search`, empty state `strings.no_meets_match` from `GET /picker/config` | should — see note |

> **`P-06` is not decoration.** The disclaimer — live, unofficial results pending
> validation, with SplashMe for validated ones — is the only thing between a live feed
> and a spectator treating it as a result. It belongs on the meet list, not in an About
> screen, and it renders the server's text rather than a compiled-in copy so wording can
> be fixed without a store review.
>
> **`P-06` and `P-07` fold; they never go away.** Both sit above the meets — under the
> title and logo — because below them a season of meets pushes them out of sight.
> Whether they also sit above `P-17`'s field depends on where the platform puts search:
> the web draws the field in the list, under the notices; iOS and Android draw it in a
> bar, outside the list. Above the meets is the requirement. Each shows its full text with an X; the X folds it to a
> pill (`results_disclaimer_short`, `privacy_note_short`), and a tap on the pill
> opens it again. Expanded, a notice has the row to itself; folded, the pills share
> one. Each pill leads with its own icon, the same thing on every client: an
> **hourglass** for `P-06` — pending validation, not an error — and **two people** for
> `P-07`, the visitors being counted. SF Symbols `hourglass` and `person.2`, Material
> Symbols `hourglass_top` and `group`; the web draws Lucide's `hourglass` and `users`.
> Not a shield or a raised hand: those read as a privacy control, and there is none. **Not a first-launch dialog, and not a consent**: a dialog accepted once
> would never show a second server's text (`P-11`, `P-16`), nor counting that a
> server turns on later, and counting is not the reader's to refuse (`C-10`) — an
> Accept button would promise a choice there is none of.
>
> - **A fold is remembered per server, against the exact words folded** — store the
>   text itself, not a flag. A reworded notice, or the same notice in another
>   language, shows in full once. The web keeps it in `localStorage`, which is per
>   origin and so per server already; an app keys it on the server's origin.
> - **`P-07`'s fold is forgotten whenever the server reports counting off**, so a
>   server that turns it back on says so in full.

> **`P-17` filters what the list already holds. There is no search endpoint.** A cloud
> serves tens of meets, rarely a couple of hundred, and `P-01` has already fetched them
> all, so a query is a filter over the cards, answered on every keystroke without a
> debounce, the same reasoning as `S-09`.
>
> - **Match every word, in any order.** Split the folded query on whitespace; a card is
>   shown when each word is a substring of its folded fields joined by spaces. So
>   `quebec 2026` finds a meet whose location holds one and date the other.
> - **Fold with `S-09`'s four steps**, table included, on both sides. The web picker
>   runs the typeahead's own `foldName()` from `shared/static/js/fold.js`; a second
>   fold would make `montreal` find `Montréal` on one client and not another.
> - **The organizer is searched but not shown.** A spectator may know the meet by the
>   club running it.
> - **`P-06` stays put.** The disclaimer is above the list whatever the filter hides.
> - **The query outlives a return from a meet, not the session.** The web keeps it for
>   the tab (`sessionStorage`), so the back arrow (`A-02`) and pull-to-refresh (`P-09`)
>   come back to the same filtered list; a shared link or a home-screen launch opens
>   the whole list. An app keeps it while the picker is on its navigation stack and
>   drops it on a cold launch.
> - **Live meets come first** in `GET /meets` ([`api.md`](api.md) §5.6), so an empty
>   query and a filtered one both lead with what is running now. Keep the server's
>   order; do not re-sort.

> **`P-10` is a hand-off, not a feature of the apps.** Serve the store URLs from
> `/picker/config` beside `P-06`'s disclaimer and hide the affordance when they are
> absent; a store listing that moves must not need a deploy. Inside a native app the
> slot renders nothing — an app cannot install itself, which is what `web-only` means
> here. The fields are `stores` in [`api.md`](api.md) §5.7, keyed by platform and
> present only for a platform that has a listing. `P-16`'s `GET /add` renders its
> buttons from the same dict, so the page a scanned code lands on and the native
> picker cannot disagree about where the app lives.

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
> - **The picker always names the server; a meet names it when it is not the default.**
>   A user who switched and forgot cannot answer "where did my meet go?" from a screen
>   that looks identical either way. On the meet list the server *is* the context — every
>   card came from it — so it is named whichever server it is. Inside a meet the bar
>   belongs to the meet, and the server earns a line there only when it is not the one
>   the app ships with. The menu changes it; the name keeps it visible.

> **`P-14` — a notice, not a gate.** A newer server is additive by contract and an
> older one degrades a feature (`L-12`'s clock against a v1 relay, say) rather than
> breaking the board. Either is better than refusing a pool's only server because its
> Pi is a release behind. Show the line once per session, where the server name
> already shows (`P-11`), and never block a connect on it.

> **`P-16` — the link shape is forced, and the web half is what makes it work.** The
> code carries **`https://<the app's default host>/add?server=<origin>`**, the origin
> percent-encoded, and nothing about that is a preference.
>
> - **Not a `splouch://` scheme.** The reader uses the camera they already have, and no
>   stock camera opens a private scheme from a code taped to a pool wall — several
>   scanners refuse one outright. More to the point, a scheme has no answer for the case
>   the poster is printed for: the spectator who **does not have the app yet**. An
>   `https` URL answers it for free. With the app installed the verified link
>   intercepts it; without the app nothing intercepts it at all, the browser lands on
>   the page, and the page offers the store (`P-10`'s hand-off, still web-only).
> - **The host is the app's own default server** — the one URL the app ships knowing
>   (`P-11`) — because a link is verified per host and no app can verify a pool's Pi,
>   which has no `https` and no certificate. A link naming any other host does not
>   parse: *a server cannot mint a code that adds a different server.*
> - **The address inside is held to exactly what a typed one is** (`P-13`): the same
>   parse, so `http` only for a `.local` name or a developer loopback (`P-12`), and
>   then `GET /server` before anything is saved. A printed code is a stranger's input
>   in a way a typed address is not, so the floor cannot be lower here.
> - **Scanning proposes; it does not act.** The prompt is the whole of the consent, so
>   it names the address being agreed to, and a scan makes no request of that address
>   until the reader says yes. It asks only what is left to ask — *add* for a server
>   offered nowhere yet, *switch* for one already listed, and nothing at all of the
>   network for the server already in use and answering. A link that does not parse
>   still raises the prompt: a code that opens the app and then appears to do nothing
>   cannot be told from a dead app.
> - **However it resolves, the reader ends on the picker.** A code names a cloud (see
>   below), and a cloud session's launch screen is the meet list — so the yes leads to
>   the list rather than onto a board. That is not incidental: the picker is where
>   `P-06`'s unofficial-results disclaimer is, and a spectator who arrived by camera is
>   exactly the one who has never seen it. A scan must not be a way past a **must**.
>
> **A printed code names a cloud, never a Pi.** The Pi in the building is the better
> server — no internet dependency, an unthrottled race clock, which is what `P-11`'s
> note is about — and it is still the wrong thing to put on a poster. A `.local` name
> resolves only for a device already joined to the venue's wifi: a spectator on
> cellular gets a handshake failure, a guest network with client isolation blocks mDNS
> even for one that did join, and a poster cannot ask which network the reader is on.
> So a code names the address that works from anywhere, and the Pi is offered *after*
> the reader is on the picker, by `P-12`'s browse, to a phone that has by then joined
> the right network. Nothing in the link shape enforces this — `server=` will carry any
> address the client would accept — it is a rule about what a server **mints**.
>
> **The server half is specified elsewhere**, and without it the feature is inert: the
> two `/.well-known/` files and `GET /add` in [`api.md`](api.md) §4 (including how `/add`
> narrows its store button by `User-Agent`), their deployment and fingerprints in
> [`cloud.md`](cloud.md), and the Pi's downloadable poster code in `cloud.md` as well.

> **`P-15` — the reader's palette, not the meet's.** A preference the next meet could
> overrule is not a preference: a spectator who chose Light would get it until they
> opened a meet, which is where they were going. So the choice replaces
> `settings.theme_colors` on a phone rather than sitting beside it (`T-01`, `T-02`),
> and the cost is named rather than hidden — an operator who themed a meet in club
> colours sees them on the kiosk and the Qt display, not on a phone.
>
> **Dark by default** because the pages were dark before the choice existed, and a
> spectator who never opens the menu should see what they saw yesterday.
>
> On the web the choice is a cookie, `splouch_theme` (`dark`, `light`, `auto`), set by
> the picker, which restyles in place, and read by the cloud for the shell and its
> tabs. Automatic draws dark and carries the light palette behind a
> `prefers-color-scheme` query, since the server cannot see the device's setting. **The
> Pi's phone pages keep the operator's palette**: it has no picker (`T-08`), so nothing
> could set the cookie there. An app stores the choice itself.
>
> **The two palettes are copied, key for key, from [`api.md`](api.md) §6.1** — never
> re-picked by eye. A transcription that drifts is a board that no longer looks like
> Splouch: the dark `header_label` is the accent blue `#3b9eff`, which is also what
> `L-23`'s lap count leans on to read as a label rather than a number to race against.

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
| `A-02` | Back affordance to the meet picker | label `mobile.back_to_meets` where the client draws its own; a platform back button labels itself | must |
| `A-03` | Move between the tabs the platform's own way for peer sections — a tab bar everywhere, plus a horizontal swipe where swiping between peer sections is the platform's idiom | web: tab bar, and 28px edge strips, ≥40px travel, switched on `touchend` | must — see note |
| `A-04` | The selected tab survives a relaunch | web: `sessionStorage['tab']` | should |
| `A-05` | Pull-to-refresh re-fetches config and rejoins the sockets | web: 80px threshold, rotating indicator | should |
| `A-06` | Content clears notch, Dynamic Island, and home indicator | web: `env(safe-area-inset-*)` | must (free natively) |
| `A-07` | On a short window the tabs stop costing height — compacted, or moved beside the content — rather than taking the same band as on a tall one | the platform's size classes | should |
| `A-08` | Window and home-screen title is the meet's `app_window_title`, falling back to its `name` | `settings.app_window_title`, then `name`, then `Splouch` | web-only |
| `A-09` | Meet gone mid-session → return to the picker | cloud: `GET /meet/{id}/config` answers **404**. Re-fetch it on every reconnect, foreground, pull-to-refresh (`A-05`) and `reload` (`C-08`); web: `GET /mobile` 303s to `/` on page load. Pi: n/a (§0.2) | must — see note |
| `A-10` | Where `A-03` includes a swipe, the movement is visible: the tabs follow the finger through the drag and settle on release, rather than changing on release alone | — | should — see note; n/a where the platform has no swipe between tabs |
| `A-11` | A meet run with **no timing console** has no Results tab at all — not an empty one | `settings.console.timed` false (cloud: `GET /meet/{id}/config`; Pi: `GET /config`) | must — see note |

> **`A-03` is the platform's navigation; `A-10` is the pager where there is one.**
> Moving between peer sections is something every platform does, and each has its own
> way. On Android a full-width pager is the Material idiom: the tabs track the drag, and
> one control meets both rows. On iOS a tab bar switches on tap — no Apple app swipes
> between tab-bar sections, and the HIG keeps swipe paging for pages with a page control
> — so the tab bar alone is the whole of `A-03` there, and `A-10` does not apply. The web
> keeps its edge strips because a page has no platform to defer to. What is not allowed
> is a client giving up the platform's real tab bar to imitate another platform's
> gesture (§0.4).
>
> **The leading edge belongs to the system, not to the swipe.** Where the platform owns
> an edge gesture — an interactive back, typically the leading ~24pt — the tab gesture
> starts outside that strip and leaves it alone. This is not a detail: a full-width
> drag-tracking pager claims that edge, and that is precisely why a pager and `A-02`'s
> back swipe cannot both exist there. One of the two has to yield, and it is not the
> system's gesture that gives way.

> **`A-09` has no socket signal, on purpose.** `join_meet` for a meet the cloud no
> longer holds is silently ignored — no reply, no `meet_live`, nothing
> ([`api.md`](api.md) §3) — and an expiring meet announces nothing to the sockets
> already on it. So the app *asks*: the config fetch it already makes on the moments
> listed is the check, and a 404 there means gone. A socket that connects and stays
> silent is **not** the signal — it is also what a live meet between frames looks
> like. Any other status, or no answer, is a network fault (`C-03`), not a missing
> meet.

> **`A-11` — the tab goes, not its contents.** Some meets have no timing console: a
> club time trial, or a console whose cable never turned up. The operator drives the
> boards by hand from the Pi's `/manual` page, and the meet runs — names, heats, the
> whole schedule — but nothing is ever timed, so no `results_snapshot` is ever sent
> ([`api.md`](api.md) §2.3). A Results tab there is not empty for a while; it is empty
> for the entire meet, and `R-01`'s "waiting for results…" turns from a status into a
> falsehood. So the tab is removed, and a spectator is never offered a screen that
> cannot fill.
>
> The flag comes from the config fetch the client already makes (§0.2), so this costs
> no request. **Re-evaluate it wherever that fetch is repeated** — reconnect,
> foreground, pull-to-refresh, `reload` (`C-08`) — because the operator can switch
> consoles mid-meet, in either direction. When the tab count changes under a selected
> tab, keep the spectator on a tab that still exists rather than on an index (`A-04`
> stores a choice, not a number).
>
> Read `console.timed`; do not match on `console.key`. The server derives `timed` from
> the decoder itself, so a console added as a local plugin and driven by hand is
> covered too ([`api.md`](api.md) §5.4). A server too old to send `console` is a server
> with a console: default to showing the tab.
>
> Nothing else is conditional on this. The Scoreboard tab is exactly as useful — it is
> what the operator is driving — and the Schedule tab is the full start list either way.

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
| `L-13` | Event or heat change blanks all times, deltas, and places — **unless** a lane was running on the previous frame, in which case the times stay on screen as results. The first event and heat seen after a connect are a baseline, not a change | `current_event` / `current_heat` change, compared as strings | must — see note |
| `L-14` | Returning to the tab re-runs layout and refreshes the clock | web: parent re-dispatches `resize` | must (native: on-appear) |
| `L-23` | While a lane is swimming the **delta cell** carries that lane's lengths — centred where the delta is a column, after the time where the row flows (`L-15`) — in the header's accent colour; the delta takes the cell back at the finish, in its better/worse colour. The column header never changes | `lane_splits<i>`, gated on meet `settings.show_laps`; direction from `settings.lap_direction` | should — see note |

> **`L-23` — one cell, two tenants.** (`L-15`–`L-22` are spoken for further down and
> §0.1 says never renumber, so the next free ID lands out of sequence here. The rule
> belongs in this table, not at the end of the file.)
>
> A lap is not a result, so it is not given a
> column of its own and it never borrows the place column: `#3` and `3` a length
> apart are the same glyph, and swapping the header between them mid-heat is a
> distinction nobody reads across a hall. The delta column is empty for the whole
> race and fills only at the finish, so the handover is the signal — the colour
> changes, the header does not.
>
> **Show a lap when all of these hold**, and nothing when any fails:
>
> | condition | why |
> | --- | --- |
> | `settings.show_laps` | off by default; not every console's count is exact |
> | `lane_splits<i> > 0`, **or** counting down in a lane that has a swimmer | counting up waits for the first wall — a column of noughts under a start list is noise. Counting down has the whole race to report, so it shows from the moment the heat loads; an empty lane in a short heat must not advertise lengths nobody swims |
> | the lane has no place | the finish ends the lap, whatever the delta is doing — a swimmer with no seed time never gets a delta at all |
> | the delta is empty | belt and braces for the frame where both arrive together |
>
> **Which way it counts** is `settings.lap_direction`: `up` shows the lengths a lane
> has completed — the console's own number — and `down` shows `expected_splits`
> minus that, clamped at 0 so an over-count reads as the last length rather than a
> negative one. `expected_splits` is 0 for any event whose meet file carries no
> distance, and there is nothing to count down from, so `down` falls back to `up`
> there rather than running to a number nobody reaches.
>
> Note `split_step` (§5.1) when reading the count: in a pool with touchpads at one
> end only the swimmer is seen every *second* length, so the number arrives in twos
> and never lands on an odd length. Both numbers describe the venue, so neither
> varies by console.
>
> **Accuracy varies by console** (§5.1): exact from a Quantum or an Omnisport 2000,
> inferred from touchpad stops on a CTS Gen6, absent on a Gen7 or an ARES 21. The
> operator corrects a drifting count with `adjust_splits`, which is why the setting
> exists and why it ships off.

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
> | event or heat change | stops the ticker; what replaces the digits is `L-13`'s call |
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
>
> **Parse it with the one pattern** [`api.md`](api.md) §5.1 gives — `m:ss.hh` or
> `ss.hh` — and treat a value that does not match as no re-base at all: the ticker
> carries on from its last base. Not a freeze, not a blank.

> **`L-13` — three cases, two of them exceptions.**
>
> | Frame carries a new event or heat, and… | Do |
> | --- | --- |
> | it is the first event/heat this connection has seen | nothing — it is the **baseline**. The join replay (cloud) or the connect snapshot (Pi) carries the current heat's times beside its number; blanking them throws away the only state a late joiner has |
> | a lane was running on the previous frame | keep every time as a result: the console advanced before it published results, and the next frame moves on. Blanking here erases what the swimmers just posted |
> | otherwise | blank all times, deltas and places; names and clubs arrive in the same frame |
>
> A lane running on *this* frame outranks all three: the lane shows the clock
> (`L-12`). Start the remembered event and heat as *unseen*, not as `0`: a client
> that starts at `0` reads the join replay as a change and blanks it — the web did,
> until 2026-09-30.

### 3.3 Layout

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `L-15` | **Under 600 wide**: two-line row — lane number spanning left, name on line 1 with club right-aligned, time and delta and place on line 2; a place is prefixed `#`, and nothing is when there is no place | the window's width, at 600pt/dp/px | must |
| `L-24` | When the lanes do not fit a compact-width board, give up, in this order: the EVENT/HEAT row moves into the top bar (short labels, no wall clock) → the relay line (`L-06`) is dropped → the row type shrinks, to no less than 0.72× → the board scrolls | measured row heights, never device constants | should — see note |
| `L-16` | **600 wide and up**: full table with a header row, row font scaled to the height each lane gets; on a short window the header row is the first thing dropped | the window's width, at 600pt/dp/px | should |
| `L-17` | Long names shrink to fit their cell, ellipsis only as a floor | — | must — see note |

> **`L-15` / `L-16` — width decides, not orientation.** On a phone the two agree: portrait
> is compact, landscape is regular. They part on a tablet or an unfolded foldable, where
> a portrait window is wide enough for the full table and the two-line row would waste
> most of it. Branch on the window's width — the full table from **600** points, dp or
> CSS px — never on which way the device is held. On Android that is
> `WindowWidthSizeClass` leaving `Compact`; on the web a `min-width: 600px` query. On
> iOS it is the width itself, **not** `horizontalSizeClass`: that stays compact on most
> iPhones held sideways, and would hand them the two-line row.

> **`L-24` — what a crowded board gives up, cheapest first.** Twelve lanes in a phone's
> portrait height is the case. Each step runs only when the one before was not enough:
>
> 1. **The EVENT/HEAT row moves into the top bar**, with short labels and without the
>    wall clock (the status bar already shows the time). It costs the meet's title and
>    `P-11`'s server line, so a board that fits without it keeps them.
> 2. **The relay line goes** (`L-06`): it is the one line on a row that is not a
>    swimmer, a time or a place.
> 3. **The type shrinks**, everything in the row together, to no less than 0.72× its
>    size.
> 4. **The board scrolls.** Twelve lanes at an unreadable size would fit; scrolling is
>    the honest answer past the floor.
>
> Decide each step from measured heights — a real row laid out off screen — and decide
> whether the header belongs in the bar from the height the lanes would have *with* it
> there, so the decision cannot oscillate. The web has no top bar to move the header
> into; it scrolls past a fixed row height.
>
> (`L-24` takes the next free number in this section; §0.1.)

> **`L-17` — shrink, on this tab and on Results (`R-08`).** Rows share the board's height
> with a floor, so shrinking a name changes type size and nothing else.
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

> **`R-01` — the waiting state replaces the table; it does not sit under it.** Blank rows
> mean something on the Scoreboard: a heat is under way and they fill in as it runs, which
> is why rows never collapse there (`L-09`). Results before the first snapshot has no heat
> to fill them. The grid the web draws is there to occupy the page, and a spectator reads
> nothing from it that the line does not already say — so the line is the screen, and the
> table appears with the data.
>
> The string does not change: `mobile.waiting_results` promises results rather than
> reporting their absence, which during a meet is the truer of the two. `R-02`'s wipe
> returns the tab to exactly this state.

---

## 5. Schedule tab (`S`)

The richest screen, and the only one with real client-side state. A spectator uses it to
find *their* swimmer among several hundred.

### 5.1 The list

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `S-01` | Every heat as a card, its heading on one line: the heat identifier `EV 12  HT 3` in the **short** labels, the event name beside it, the scheduled time trailing |  `GET /meet/{id}/schedule` ([`api.md`](api.md) §5.8); Pi: `GET /schedule.json`, same body | must |
| `S-02` | Each card lists its lanes: lane number, name, club, seed time | `lanes[]` | must |
| `S-03` | Relay entries show member first names joined by `·` | `lane.swimmers[].first`, falling back to `.name` | should |
| `S-04` | Alternating card backgrounds, computed over *visible* cards so filtering keeps the stripe | — | should |
| `S-05` | The heat the meet is on is highlighted in the list | `update_scoreboard.current_event`/`current_heat` **and** `results_snapshot.event`/`heat`, compared to the schedule's `event`/`heat` **as strings** — they are integers there ([`api.md`](api.md) §5.1) | must |
| `S-06` | The list auto-scrolls to the current heat once per appearance | re-armed on returning to the foreground | must |
| `S-07` | Empty state when no meet file is loaded | `mobile.no_schedule` / `mobile.no_meet` | must |

> **`S-01` — short on the card, long out loud.** The identifier repeats once per card,
> and the long words buy nothing the numbers beside them do not already say, while the
> width they cost is the event name's. So the card takes the short labels
> (`labels.event` / `labels.heat` from the `short` table of `GET /i18n/{lang}`, or the
> short form of `settings.labels`) — the board's own header keeps the long ones (`T-09`).
> The two halves are separated by a doubled space rather than a dash: they are not a
> range, and twice the within-pair gap is what groups `EV 12` against `HT 3` in a
> monospaced face. A heat with no scheduled time draws nothing for it, and the name runs
> to the edge.
>
> **A screen reader hears the long words** — `EVENT 12, HEAT 3, <name>, <time>` as one
> utterance. `EV` and `HT` are a width decision, and a listener has no width to save.

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

> **`S-09` builds its own index. There is no endpoint for it.** `GET /search_suggestions`
> was removed from both servers ([`api.md`](api.md) §7) — it read nothing `S-01` has
> not already given you: every `lane.name`, `lane.club` and `lane.swimmers[].name`.
> It also opened a window the local index closes by construction: the server answered
> from *its* start list, so between a `schedule_update` and the re-fetch it could
> offer a swimmer this list did not have, and the chip then matched nothing. Build the
> index from the payload you rendered, and rebuild it with `S-21`.

> **What goes in the index, and how it matches.** One entry per distinct name:
> every `lane.name` — relay **team** names included, since a spectator may know the
> team and not one swimmer on it — and every `lane.swimmers[].name`, each carrying its
> lane's club; then one entry per distinct club. A name in two lanes with different
> clubs keeps the later one. Match is a substring of the *folded* name, folding both
> the query and the indexed name, so `elise` finds `Élise`. Swimmers sort before clubs,
> each group by name, and the list is cut to 20.

> **The fold, in four steps.** Lowercase; NFD-decompose; expand the letters in the
> table below; drop every codepoint still above `U+007F`. Steps 2 and 4 are what make
> accents transparent — `é` decomposes to `e` plus a combining acute, and the acute is
> then swept away.
>
> Step 3 is the one that is easy to omit, and must not be. These 17 letters have **no**
> canonical decomposition, so NFD leaves them whole and step 4 would delete the letter
> itself, punching a hole in the word. Expanding them after NFD also covers their
> accented forms for nothing: `ǿ` decomposes to `ø` plus an acute, and the `ø` is then
> expanded like any other.
>
> | | | | | | | | |
> | --- | --- | --- | --- | --- | --- | --- | --- |
> | `ß`→`ss` | `æ`→`ae` | `ð`→`d` | `ø`→`o` | `þ`→`th` | `đ`→`d` | `ħ`→`h` | `ı`→`i` |
> | `ĳ`→`ij` | `ĸ`→`k` | `ŀ`→`l` | `ł`→`l` | `ŉ`→`n` | `ŋ`→`n` | `œ`→`oe` | `ŧ`→`t` |
> | `ſ`→`s` | | | | | | | |
>
> Do not reach for a platform convenience — `String.folding(.diacriticInsensitive)`, or
> a regex that strips only the combining range `U+0300`–`U+036F`. Both skip step 3, and
> `Île-des-Sœurs` then indexes as `ile-des-surs`, which nobody will ever type. Fold
> identically or `S-10`'s rows depend on which client the spectator is holding.

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
| `C-02` | `join_meet {meet_id, vid}` on **every** connect, including every reconnect — **gated on `kind == "cloud"`** from `GET /server`. A Pi pushes on connect and ignores the frame, so nothing is sent there | `GET /server` → `kind` | must |
| `C-03` | Automatic reconnect, capped exponential backoff (web: 500ms → 5s) | — | must |
| `C-04` | Heartbeat `ping` every 15s; no inbound frame for 35s means dead — close and reconnect | server replies `pong` | must |
| `C-05` | On foreground or network-restored: probe with a `ping`; no `pong` within ~4s means dead | — | **must** |
| `C-06` | Frames sent while disconnected are queued and flushed on connect | — | should |
| `C-07` | Unknown events are ignored, not treated as errors | — | must |
| `C-08` | `reload` → re-fetch config and redraw (web: full page reload) | — | must |
| `C-09` | `meet_live` gates live affordances; a `disconnect` implies `meet_live = false` | — | must |
| `C-10` | Anonymous per-install, **per-server** id (`vid`) sent with `join_meet` | random UUID, created the first time a `join_meet` goes to that server and stored once, keyed by normalised scheme, host and port | must — see note |

> **`C-05` is the one that bites phones.** iOS and Android freeze background sockets
> without ever firing a close: the connection is dead but looks open, so backoff never
> starts and the board sits frozen after every screen lock. The foreground probe is what
> makes the reconnect prompt. Do not rely on `C-03` alone.

> **`C-10` — privacy constraints are binding.** `vid` is a random UUID generated once
> **per server** and stored locally, used server-side only for `COUNT(DISTINCT)` to
> estimate attendance. It exists only where something receives it: the key is the
> server's normalised origin (scheme, host, port), created on the first `join_meet`
> to that origin. A Pi has no `join_meet` (`C-02`), so pointing the app at one
> creates nothing. Never derive one `vid` from another, and never send a server an
> id minted for a different one.

---

## 7. Theme and language (`T`)

A meet chooses its faces (`T-03`) and its words (`T-04`). Its colours reach the kiosk
and the Qt display; on a phone the reader's Appearance (`P-15`) replaces them with one of
the server's two palettes (`T-01`, `T-02`). Either way the client has no look of its own:
every colour, face and word it draws on the board comes from the server.

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `T-01` | Palette from the meet's config: `bg`, `header_bg`, `header_border`, `header_label`, `header_value`, `th_text`, `th_bg`, `row_odd`, `row_even`, `row_text`, `time`, `delta_better`, `delta_worse` | `settings.theme_colors` — on a phone, `P-15`'s palette instead | must |
| `T-02` | Schedule-specific colours `schedule_event`, `schedule_time`, `schedule_name`, `schedule_club`, each with a built-in default | `settings.theme_colors` — on a phone, `P-15`'s palette instead | should |
| `T-03` | Three font roles — `family` (text), `digits` (clock), `timing` (times and deltas) | `settings.theme_fonts` | must |
| `T-04` | Column headers and header labels are the server's words, never the app's | `settings.labels` for the default; `GET /i18n/{lang}` → `labels` when the user has chosen | must — see note |
| `T-05` | The app's own chrome — tab names, empty states, filter UI — is **fetched and cached**, not translated in the app | `GET /i18n/{lang}` → `mobile` ([`api.md`](api.md) §5.9) | must |
| `T-06` | Language defaults to the **meet's** locale and the user may override it | `settings.locale`, then the stored preference | must |
| `T-07` | Missing theme keys fall back to the defaults rather than rendering unstyled | the two palettes and the default faces in [`api.md`](api.md) §6.1 | must |
| `T-08` | A language control, per device, applying to every meet opened afterwards | `GET /locales` for the list | should — see note |
| `T-09` | The board's EVENT and HEAT headers read **long** on every client; a client may offer a short/long control over those two only, per device, starting from long | the stored preference; the words themselves from `GET /i18n/{lang}` → `labels` | should — see note |
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
> translating anything itself. There is no per-meet override of the label table to
> layer: `settings.labels` is the operator's pick and `GET /i18n/{lang}` is the
> rest, both resolved from the same file.

> **`T-05` — which words are the server's, and which are the app's.** The line is
> what the word is about, not which repo renders it. If the web page shows the same
> word, the server owns it and it is in `[mobile]`: tab names, empty states, the
> filter sheet (`filter`, `no_filters`, `no_search_results`, `no_matches`,
> `swimmer`, `club`), and the picker's chrome and preference controls
> (`language`, `language_auto`, `prefs_*`, `results_disclaimer`, `privacy_note`).
> If the word is about the app or the device, the app owns it and translates it
> natively: the server sheet, "nearby", connection and address errors, OS
> requirements, and the standard buttons the platform localises anyway. A timing
> server has no business translating an Android version requirement, and a new
> language may reach the native table a release later than the server — English
> fills the gap until it does.

> **`T-08` — one choice, stored per device, set where every meet is in view.** The
> natural home is the meet picker, which already resolves the visitor's language
> rather than a meet's ([`api.md`](api.md) §5.7): set once, every meet opened
> afterwards follows. On the web the choice is a cookie, `splouch_lang`, written by
> the picker and read by the server for every page it renders; `?lang=` on a link
> wins for that one request so a shared link opens as sent, and the shell writes it
> to the cookie. Neither server reads `splouch_style` / `?style=` any more (`T-09`).
> An app stores the choice itself and sends `lang` on the request; it has no cookie
> to keep.
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
> renders what it is given is correct.
>
> **Two options, and it starts from long.** Short and long — there is no third "meet
> default" row. `settings.label_style` is how the *server* resolves the `labels` it sends
> (`T-04`); it is not an option the control offers back, and a preference whose third
> value means "whatever this operator picked" changes meaning when the spectator opens
> the next meet, which is not a choice anyone can hold in their head. Long is the
> starting point because `EV` / `HT` are contractions an attendee has to decode, and the
> header they sit in has room for the word (`L-01`) — it is the operator's fixed-width
> board that needs the short form, not a phone.
>
> **A release may withdraw the control without discarding the choice.** `should` means
> what §0.3 says it means: shipping with the headers fixed at long and no control at all
> is within the contract. What is not within it is dropping a preference a user has
> already set — resolve the stored value through something that answers long while the
> control is away, so returning the control returns each user's choice rather than
> resetting everyone to the default. The server side is unaffected either way:
> `prefs_labels`, `prefs_short` and `prefs_long` stay served and stay in `T-10`'s
> snapshot, and `settings.label_style` stays in the config whether or not a client
> consults it.
>
> **No client offers the control today**, and all three are fixed at long. The web's
> phone pages resolve their headers long whatever the operator's `label_style`, a
> `?style=` link or a `splouch_style` cookie says; the cookie is ignored rather than
> cleared, as above, so a control that returns finds each visitor's choice. The kiosk
> board is unaffected: it is the operator's fixed-width display, and `label_style` is
> still its setting.

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
>
> **The snapshot is captured, never transcribed.** It is the JSON body of
> `GET /i18n/{lang}`, verbatim, one checked-in file per language the default cloud
> lists in `GET /locales`, written by a script in the app repo that fetches them from
> a running server and regenerated on demand — before a release, and whenever
> `shared/locales/` changes. Strings are data, so this is not a copy of the docs; but
> a hand-maintained copy of the TOML would be a second source of truth and would
> drift within a season, which is the problem `T-05` exists to remove. The file the
> app reads at build time and the file it caches at run time have the **same
> shape**, so there is one decoder and the fallback chain above is a lookup order,
> not a format conversion.

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

## 8. Accessibility (`X`)

Taken from what the two native clients built independently and agreed on. Each rule is
about what a screen reader, a large text size or a less steady finger meets; none is a
platform API. "Heard" — checked under VoiceOver or TalkBack by ear — is a ledger fact,
not a level here.

| ID | Feature | Driven by | Level |
| --- | --- | --- | --- |
| `X-01` | A lane on the board is **one** accessibility element saying the whole lane, in the server's column words — `LN 1, <name>, CLUB <club>, TIME <time>, DIFF <delta>, PL <place>`; an empty lane says only its number | `settings.labels` / `GET /i18n/{lang}` → `labels` (`T-04`) | must |
| `X-02` | The EVENT and HEAT words and their numbers read as one each, and say nothing before a number arrives | — | must |
| `X-03` | A start-list lane is one utterance in the same words, seed time included; a heat's heading is one utterance in the **long** words (`S-01`) | `labels` | should |
| `X-04` | Heat headings, the picker's title and every empty-state title are headings, so the reader can jump heat to heat | — | should |
| `X-05` | Every tappable target is at least the platform's minimum — 44pt on iOS, 48dp on Android — the filter chip's × included, without growing the chip | — | must |
| `X-06` | In a list of choices — server, language, Appearance — the current one is announced as selected, not only marked with a glyph | — | must |
| `X-07` | Decorative glyphs beside text that already says the same thing are hidden; anything laid out only to be measured never reaches the accessibility tree | — | should |
| `X-08` | Everything off the board follows the device's text size. The board sizes itself from the height it has (`L-16`, `L-24`) and does not scale a second time. **On the web** the reader's text size is the browser's zoom: at 200%, and at a 320px-wide window, nothing is cut off and nothing scrolls sideways | native: the platform's text-size setting; web: browser zoom (WCAG 1.4.4, 1.4.10) | should |
| `X-09` | Decorative motion — the picker's live dot — honours the reduce-motion setting. `L-11`'s lock flash and `L-12`'s pulse are information, not decoration, and may keep running | — | should |
| `X-10` | When a control replaces itself — `P-06`'s X folding to a pill, the pill opening again — focus moves to its replacement | — | should |

> **`X-01` — the one word on the board that is the client's.** `L-23`'s lap count has no
> column word on the wire (`labels` names six columns and stops), so a client speaks it
> with a native string — *Laps 4* — in the app's languages rather than the meet's. Read
> as a bare integer after the time, it would be heard as a second time.

> **Contrast is the palettes', and so checkable once.** A phone draws one of the two
> palettes in [`api.md`](api.md) §6.1 (`P-15`), so contrast is a property of those two
> tables rather than of each meet. One known shortfall: in the dark table `th_text`
> `#666666` on `row_even` `#202020` is 2.84:1 — the club on every even row — under the
> 3:1 large-text bar. The fix is a lighter `th_text` in the server's table, never a
> client's own.

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
    is a swipe. `A-07`, `L-15`, `L-16` switch on width class, not orientation. `T-09`:
    the board's EVENT/HEAT read long on every client. `S-01`'s heading is the short
    `EV 12  HT 3`, spoken long. `P-11`: the picker always names the server. `P-17`'s
    field goes where the platform puts search, and `P-06` is required above the meets
    rather than above that field. `P-03`: an offline meet shows its last scoreboard and
    no results. `L-23`'s "centred" applies where the delta is a column. `T-01`: the
    palettes are copied from [`api.md`](api.md) §6.1, dark `header_label` `#3b9eff`.
  - **New rows.** `L-24`, the crowded-board order; §8, accessibility (`X-01`–`X-10`).
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
