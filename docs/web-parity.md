# Splouch web phone pages — parity ledger

One row per feature ID in [`app.md`](app.md) **v2**, for the phone pages both servers
render: the cloud picker (`cloud/templates/picker.html`) and the shell and tabs in
`shared/templates/` (`mobile.html`, `live-mobile.html`, `results.html`,
`schedule.html`, `scoreboard_base.html`). The kiosk board and the Qt display are not
phone clients and have no rows here.

`app.md` owns *what* each feature is; this file owns *whether the web does it, and why
not* (`app.md` §0.1). Status is `done` / `deferred` / `diverges` / `n/a — <reason>`, as
defined there. A `diverges` row is temporary: the page changes, or `app.md` does.

**State (2026-09-30).** Written when `app.md` became v2 and stopped treating these pages
as its reference. Rows marked `done` describe pages that `app.md` v1 was itself written
from; the `diverges` rows are where v2 moved past them.

## 1. Meet picker

| ID | Status | Notes |
| --- | --- | --- |
| `P-01` | `done` | `picker.html` over `_public_meet_list` |
| `P-02` | `done` | |
| `P-03` | `done` | |
| `P-04` | `done` | |
| `P-05` | `done` | |
| `P-06` | `done` | above the list and over the search field; fold stored per origin in `localStorage` against its exact text |
| `P-07` | `done` | |
| `P-08` | `done` | |
| `P-09` | `done` | |
| `P-10` | `done` | the install card offers the reader's own store when their platform has a listing — narrowed by `User-Agent` as `GET /add` narrows it, every listing for an agent that cannot be placed — and Add to Home Screen otherwise |
| `P-11` | `n/a` | native-only — a page's origin is its server |
| `P-12` | `n/a` | native-only |
| `P-13` | `n/a` | native-only |
| `P-14` | `n/a` | native-only — a page is always the server's own version |
| `P-15` | `done` | `splouch_theme` cookie; the Pi's pages keep the operator's palette (no picker) |
| `P-16` | `n/a` | native-only; the web half is `GET /add` ([`api.md`](api.md) §4) |
| `P-17` | `done` | field in the list from 3 meets, `foldName()` from `fold.js` |

## 2. App shell

| ID | Status | Notes |
| --- | --- | --- |
| `A-01` | `done` | |
| `A-02` | `done` | |
| `A-03` | `done` | tab bar plus 28px edge strips |
| `A-04` | `done` | `sessionStorage['tab']` |
| `A-05` | `done` | hand-rolled pull, 80px threshold |
| `A-06` | `done` | `env(safe-area-inset-*)` |
| `A-07` | `done` | `mobile.html`: a narrow window (≤480px) stacks each label under its icon, a short one (≤480px tall) drops the labels |
| `A-08` | `done` | |
| `A-09` | `done` | `GET /mobile` 303s to `/` |
| `A-10` | `deferred` | the edge-strip swipe changes tab on `touchend`; nothing follows the finger |
| `A-11` | `done` | `show_results` in `mobile.html` |

## 3. Scoreboard tab

| ID | Status | Notes |
| --- | --- | --- |
| `L-01` | `done` | small word above the number on any window taller than 500px; inline only on a phone on its side, where height runs out |
| `L-02`–`L-12` | `done` | |
| `L-13` | `done` | the first event/heat after a connect or a `reset` is a baseline (`last_event`/`last_heat` start at `null`); `tests/test_board_first_frame.py` |
| `L-14` | `done` | parent re-dispatches `resize` |
| `L-15` | `done` | two-line row below 600px wide (`scoreboard_base.html`) |
| `L-16` | `done` | full table from 600px wide, an upright tablet included; column titles capped at `3vw` so they fit there |
| `L-17` | `done` | `scrollWidth`/`clientWidth` ratio, gated on `lane_name` frames |
| `L-18`–`L-22` | `n/a` | not on a phone (`app.md`) |
| `L-23` | `done` | `SHOW_LAPS`, `lane_splits_n` in `scoreboard_base.html` |
| `L-24` | `deferred` | no top bar to take the header; rows keep a minimum height and the board scrolls |

## 4. Results tab

| ID | Status | Notes |
| --- | --- | --- |
| `R-01` | `done` | the line replaces the table until a snapshot (`body.has-results`), in both orientations; `R-02`'s wipe brings it back |
| `R-02`–`R-10` | `done` | |

## 5. Schedule tab

| ID | Status | Notes |
| --- | --- | --- |
| `S-01` | `done` | `EV 12  HT 3` in the short labels whatever the board's style, the name beside it, the time trailing; the heading is a `role="heading"` whose `aria-label` says the long words |
| `S-02`–`S-08` | `done` | |
| `S-09` | `done` | local index, `foldName()`, no debounce |
| `S-10`–`S-21` | `done` | |

## 6. Connection and session

| ID | Status | Notes |
| --- | --- | --- |
| `C-01`–`C-10` | `done` | `shared/static/js/ws.js` |

## 7. Theme and language

| ID | Status | Notes |
| --- | --- | --- |
| `T-01`, `T-02` | `done` | the reader's palette on the cloud (`P-15`); the operator's on the Pi |
| `T-03`–`T-08` | `done` | |
| `T-09` | `done` | long on the board on both servers (`_client_style`, `client_prefs`); the schedule route pins its cards short (`S-01`) |
| `T-10` | `n/a` | the server renders the strings into the page; there is no build to snapshot into |
| `T-11` | `done` | `composeEventName` |

## 8. Accessibility

| ID | Status | Notes |
| --- | --- | --- |
| `X-01`–`X-10` | `deferred` | not audited. The board, results and schedule templates carry no `aria-*` or `role` attributes today, so `X-01`–`X-04` are expected to fail |
