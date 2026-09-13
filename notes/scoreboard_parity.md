# `/live` vs the Qt board — layout parity

The Qt display in [`scoreboard/`](../scoreboard/) replaced a Chromium kiosk pointed at
`http://splouch.local`, which redirects to `/live`. So the reference for every layout
decision is [`server/templates/live.html`](../server/templates/live.html) plus
[`shared/static/css/timing_display.css`](../shared/static/css/timing_display.css). A second,
diverged template (`scoreboard.html`) once sat beside it with different column widths and a
different heat transition; it has been deleted, and the rows that cited it now stand on their
own reasons.

This file is the ledger. Every row below is one of:

| | |
| --- | --- |
| **match** | the two behave the same, by different means |
| **intentional** | they differ on purpose, with the reason given |
| **gap** | known missing work, tracked at the bottom |

Where a difference was drift rather than a decision, the fix went into whichever side was
wrong — usually Qt, once into `live.html`. Both directions are recorded here.

---

## How layout is expressed

Nothing is shared between the two implementations; they are two renderings of the same
`/config` and the same `update_scoreboard` frames. The mechanisms have no common vocabulary,
so most "match" rows below mean *the same result by unrelated means*.

| Aspect | `/live` | Qt board |
| --- | --- | --- |
| layout model | flex column; the table is `table-layout: fixed` | `QVBoxLayout` + `QHBoxLayout` with stretch weights |
| sizing unit | `vw` / `vh`, off the viewport | fractions of the **parent widget** — row height, bar height |
| where sizes are computed | the CSS engine, continuously | each widget's own `resizeEvent` |
| text overflow | `fitNameFontSize()` scales the cell down by the overflow ratio, ellipsis as the floor | `FitLabel` binary-searches the font size, elides at the 10px floor |
| row backgrounds | a `linear-gradient` synthesized into `#timing-bg` from each row's computed colour | `autoFillBackground` per `LaneRow` |
| animation | CSS `transition` / `@keyframes` | `QVariantAnimation` / `QPropertyAnimation`, interpolating by hand |
| colour changes | `transition: background-color` gives them free | Qt stylesheets do not animate — every fade is interpolated and re-applied |

**Shrink-to-fit was ported Qt → browser.** `/live` used to clip a long swimmer name
outright (`overflow: hidden`), while the Qt board had `FitLabel` from the start — drift,
not a decision, and the browser was the side that was wrong. `fitNameFontSize()` now
scales the cell by the overflow ratio, keeping the ellipsis as a floor for a name too
long to shrink into the cell at all. It runs only when a frame carries a `lane_name`
key, so it stays off the per-tick path during a race.

The two arrive at it differently: Qt binary-searches for the largest size that fits,
the browser computes one ratio from `scrollWidth` / `clientWidth` and applies it. Close
enough at a glance, and a search would cost a synchronous layout per step.

Note this is the *swimmer* name in a lane row. The **event name** in the header is a
separate case and still differs on purpose — see the header table below.

**Easing is an approximation, deliberately.** CSS `ease` is
`cubic-bezier(0.25, 0.1, 0.25, 1)`, which is front-loaded; `QEasingCurve.InOutCubic` and
`InOutQuad` are symmetric. At 500ms the difference is imperceptible and not worth a
hand-built Bézier spline.

---

## Header bar

Geometry first. The bar height is the one number everything else derives from.

| Aspect | `/live` | Qt board | Status |
| --- | --- | --- | --- |
| bar height | 65px, 85px above `min-height: 700px` | `_H_BAR` = 10.5% of the window | **intentional** — 85px at 1080p is the floor of what reads across a pool deck |
| bottom border | `1px solid header_border` | `1px solid header_border` | match |
| cell dividers | `border-left: 1px solid header_border`, not on the first | same, on the four cells after the first | match |
| cell widths | `flex: 0 0` 10 / 10 / 51 / 16 / 13 % | stretch weights 10 / 10 / 51 / 16 / 13 | match — *ported Qt → browser*, see below |
| cell padding | `.header_cell` `6px 2vw` | 7% of the bar height (6px of 85), 2% of the window width | match |

**Fixed cell widths were ported the other way.** `/live` sized event/heat/chrono/clock to
their content and gave the name cell `flex: 1`, so the whole row shifted whenever the event
number gained a digit or the chrono appeared. The Qt board had fixed weights from the start;
`live.html` now uses the same five percentages. They sum to 100, so the weights *are* the
percentages — the same convention as the column weights.

On the browser side this is **opt-in**, through a `header_cells_fixed` class on the bar.
`timing_display.css` is shared with `live-mobile.html` and `results.html`,
and only the kiosk page wants fixed shares — a phone in particular does not.

It only holds because the race clock keeps its slot when it has nothing to show. Both sides
now blank the text rather than removing the cell; `live.html`'s `stop_chrono()` used to set
`display: none`, which would have reflowed the fixed widths at the end of every heat.

### Cells, left to right

| Cell | `/live` | Qt board | Status |
| --- | --- | --- | --- |
| meet title | `#header_meet_title` — idle only | absent; it lives on the splash | **intentional** — `live.html` only ever calls `set_header_mode(true)`, which hides its title cell, so the header the kiosk showed never carried one |
| EVENT / HEAT | small word above a large number, `.header_cell` column flex | `HeaderCell`, a `QVBoxLayout` of two `FitLabel`s | match |
| — position | after the meet title | first, hard against the left edge | **intentional** — event and heat are what an official glances at first |
| — text alignment | `align-items: center` | `AlignLeft` | **intentional** — follows from leading the bar |
| — word colour | `header_label` | `header_label` | match |
| — number colour | `header_label` (`#current_event`) | `header_value` | **intentional** — the browser leaves the header's four text elements in two near-identical greys for no gain |
| — word size | 12px in an 85px bar | `_R_LABEL` = 15% of the bar | match |
| — number size | 4.5vh, digits font | `_R_DIGITS` = 57% of the bar, digits font | match |
| — letter-spacing | `0.08em` on `.header_label` | `PercentageSpacing, 108` | match |
| event name | 2-line `-webkit-line-clamp`, 3vh, centred | one line, `FitLabel`, centred, `_R_VALUE` = 50% of the bar | **intentional** — CSS cannot shrink text to fit; wrapping is its only answer to a long name, and shrink-to-fit is the reason this display exists |
| race clock | `#live_chrono`, 4.5vh, `time`, digits font | same, `_R_DIGITS` | match |
| — between heats | text blanked, cell kept | text blanked, widget kept | match — a removed cell drops out of the layout and everything to its left slides across |
| wall clock | `#meet_datetime`, 4.5vh, `header_value`, digits font | same, ticking every 10s (HH:MM only) | match |
| idle state | title takes the event/heat/name share; both clocks in place | event/heat/name blanked; both clocks in place | match — the region differs only by the title, which is intentional above |

The mechanisms differ here and have to. In the browser event/heat/name are `display: none`
while idle, and the title cell's `flex: 1` picks up exactly their combined 71%. Qt has no
title cell to hand that space to, so it **blanks** those three rather than hiding them: a
hidden widget leaves the layout entirely and Qt redistributes its stretch, which under fixed
percentages is precisely what they exist to prevent. It used to hide them, and the wall clock
sat about three-quarters of the way across an idle board instead of hard right.

---

## Lane table

### Columns

Widths come straight from the CSS: `.lane-column` 5vw, `.club-column` 8vw, and 6/17/15vw for
place/time/delta out of `expand_cols()`. Name takes the remainder, as it does in CSS. They
sum to 100, so `_W_*` in `board.py` are the percentages directly.

| Column | width | data alignment | header alignment | font | Status |
| --- | --- | --- | --- | --- | --- |
| lane | 5 | centre | centre | **digits** | match |
| name | 49 | left | left | family | match |
| club | 8 | left | left | family | match |
| time | 17 | centre | centre | timing | match |
| delta | 15 | right | centre | timing | match |
| place | 6 | centre | centre | **digits** | match |

The digits font on lane numbers and places comes from
`table.timing-table tbody td:first-child, [id^="lane_place"]` in the CSS. Qt gave both the
main family until this audit, which on the stock theme is the difference between a
seven-segment board and a monospace one — the most visible thing that was wrong.

The delta header is **centred in both**, over right-aligned values. That is the browser's own
inconsistency, kept for parity.

The time *header* takes the `time` colour and the timing font, because `#time-column` shares a
CSS rule with `.td_time`. It is the only column title that is not `th_text`.

| Padding | `/live` | Qt board | Status |
| --- | --- | --- | --- |
| name cell | `0 2vw` | 2% of row width, left and right | match |
| club cell | `padding-right: 1vw` | 1% of row width | match |
| delta cell | `padding-right: 0.5vw` | 0.5% of row width | match |

Everything is proportional on both sides; there are no absolute pixel sizes in the Qt layout.

### Rows

| Aspect | `/live` | Qt board | Status |
| --- | --- | --- | --- |
| striping | lane 1 = odd = `row_odd` | same | match |
| row text size | 5vh, fixed | `_FONT_MAIN` = 52% of the row height, as a **ceiling** | **intentional** — `FitLabel` shrinks from there so a long name stays complete |
| header text size | 3vh — 60% of the row text | `_FONT_HEADER` = 62% of a header row that is half a lane row = 60% | match |
| overflow | clipped mid-glyph | shrink, then elide with `…` below 10px | **intentional** — the reason the display stopped being a web page |

### Relay sub-names

| Aspect | `/live` | Qt board | Status |
| --- | --- | --- | --- |
| placement | `.name-sub`, a block under the team name in the same cell | second `FitLabel` in the name cell's `QVBoxLayout` | match |
| relative size | `0.7em` of the line above it | `_FONT_ALT` = 0.7 × `_FONT_MAIN` | match |
| opacity | `0.7` | `QGraphicsOpacityEffect` at 0.7 | match |
| empty | `.name-sub:empty { display: none }` | hidden when blank | match |
| team name on a relay row | still 5vh — the same size as a solo name | capped to the half-cell it now shares, so slightly smaller than a solo name | **intentional** — see below |

The two labels split the cell in the browser's own proportion (5 : 3.5), and each is then
capped from the height it actually gets. They used to split it 50/50 while the name kept a
ceiling derived from the *full* row height — and since `FitLabel` only ever solves for width,
a relay row's team name came out about twice the height of its own cell and clipped top and
bottom.

Capping it is where the two part company. CSS leaves the team name at 5vh whether or not a
relay line sits under it, and simply lets the pair overflow the row — which at ten lanes it
does. Qt cannot: an overflowing label is clipped through the glyph rather than spilling. So a
relay row's team name here is a little smaller than a solo swimmer's, which is the same
trade-off `FitLabel` makes everywhere else.

### Podium

| Aspect | `/live` | Qt board | Status |
| --- | --- | --- | --- |
| when | at race end only — `mode_to_results()` and `race_finished` | the same condition, evaluated at the end of `apply_update`, plus `race_finished` | match |
| order | gold, silver, bronze at 0 / 400 / 800ms | same | match |
| fade in | 0.5s `background-color` transition | 500ms interpolated `QVariantAnimation` | match |
| fade out | same transition, at the heat change | `fade_podium_out`, step 1 of the dissolve | match |
| gated by | `SHOW_PODIUM` | `cfg.show_podium` | match |

Qt used to tint from `update_from` on every frame, so the first finisher's row went gold while
everyone else was still swimming.

### Lane times

The time cell has two owners, and which one is in charge is the whole mechanism.

| `lane_running<i>` | shows | colour |
| --- | --- | --- |
| `true` | the race clock, ticking | grey `#a0a0a0` |
| `false`, just now | `lane_time<i>` — the split | 0.8s flash from white to `time` |
| `false`, settled | `lane_time<i>` | `time` |

| Aspect | `/live` | Qt board | Status |
| --- | --- | --- | --- |
| running colour | `.time-running { color: #a0a0a0 }` | same value | match |
| lock flash | `@keyframes time-lock-flash`, 0.8s `ease-out`, `#ffffff` → `time` | `QVariantAnimation`, 0.8s `OutQuad` | match |
| trigger | the running → not-running edge (`was_running`) | the same edge, in `apply_update` | match |
| reset | `reset_times()` from `mode_to_intro()` | `LaneRow.clear()`, step 4 of the dissolve | match |

`#a0a0a0` and `#ffffff` are hardcoded on both sides — they are not theme keys in the CSS
either, so the Qt board hardcodes them too rather than inventing settings the operator would
find in only one of the two displays.

### Column reveal

| Aspect | `/live` | Qt board | Status |
| --- | --- | --- | --- |
| what moves | time, delta, place | same | match |
| duration | `.timing-anim`, `0.5s ease` | `_COL_ANIM_MS` = 500, `InOutCubic` | match |
| mechanism | `width` + `max-width` on the `th`, inherited via `table-layout: fixed` | `maximumWidth` per cell | match |
| time header while collapsed | `_tc.innerHTML = ''` | text blanked | match |
| operator toggle | `columns_state {hidden}` | same | match |
| idle board | expanded | expanded | match — an idle board should look finished, not half-drawn |

Qt drives this through `maximumWidth` rather than layout stretch: these cells use
`QSizePolicy.Ignored`, which carries the Expand flag, so a stretch of 0 would not reliably
close them.

### Column and header visibility

Ten `show_*` flags, and a column can be visible while its title is not.

| Case | `/live` | Qt board | Status |
| --- | --- | --- | --- |
| column hidden (`show_name`, `show_club`, `show_delta`, `show_position`) | `display: none` on the header **and** the cells | `setVisible(False)` on both | match |
| title hidden only (`show_*_header`) | `visibility: hidden` — the width stays | `setText('')` — the widget stays | match |

The distinction is load-bearing. `show_lane_header` and `show_time_header` have no matching
column flag, so their columns always remain; Qt used to `setVisible(False)` the title, which
takes the widget out of the layout and redistributes its stretch, and the column titles
stopped lining up with the data underneath.

---

## Transitions between heats

| Aspect | `/live` | Qt board | Status |
| --- | --- | --- | --- |
| results → next heat | instant cut | five-step dissolve | **intentional** — a fade reads better at TV distance, where an instant cut looks like a glitch |
| state machine | intro / running / results, with a 350ms leave-results debounce and a 3s `brief_results` | none; heat-key change and `lane_running` drive it directly | **gap**, see below |

The dissolve is five 500ms steps: podium tints fade, columns close, the table fades out, the
new heat is painted while invisible, the table fades back in. The table *pauses* from step 1
to step 4 — frames still merge into the snapshot, they just are not painted, so the next
heat's names cannot appear over the outgoing results. A race starting cancels it outright,
and an empty board skips it, since two seconds of dissolving nothing just looks slow.

---

## Overlays

| Overlay | `/live` | Qt board | Status |
| --- | --- | --- | --- |
| splash / carousel | `#carousel-overlay`, `inset: 4%`, `object-fit: contain` | `SplashOverlay`, same 4% inset | match |
| — fade | 0.8s in/out, 1s cross-fade | `FADE_MS` 800, `CROSSFADE_MS` 1000 | match |
| — meet title | absent | across the top 12% | **intentional** — a splash with no idea whose meet it is helps nobody |
| — background | `#000` | `shared/static/img/scoreboard_bg.png`, cropped to cover | **intentional** — sponsor logos are usually transparent PNGs, and what sits behind them is most of what the audience sees |
| test badge | `.test-overlay` — bottom 2.5vh, `0.6vh 2.5vw`, 2.2vh bold, `0.15em`, radius 6, `row_text` at 75%, `bg` text | same proportions, same 75% | match |
| cold-boot waiting screen | — | full-screen, opaque, `set_status()` | **Qt only** — the browser has no equivalent; a kiosk with a blank TV needs to say why |
| link-lost badge | — | a pill, top centre, plus a frozen tinted clock | **Qt only** — `/live` shows stale data silently, which is worse |

`/live` never signals a dropped connection at all: it keeps whatever the last frame
said on screen indefinitely, with no indication that it is no longer live. The Qt
board deliberately goes further, because it is the one on the pool deck.

The status overlay must never be used for `test_mode`: it is opaque and full-screen, so it
would hide the very board the operator is testing.

---

## Known gaps

Carried from [`scoreboard/README.md`](../scoreboard/README.md). These are missing work, not
decisions.

| Gap | Detail |
| --- | --- |
| automatic idle splash | Neither display has one: the Qt overlay is operator-driven, and `/live` never had a timeout — the `INTRO_TIMEOUT` / `RESULTS_TIMEOUT` this row used to cite belonged to the retired second template. Both boards hold the last start list until someone presses the button. |
| results hold | `/live` distinguishes a brief result from a full one (`brief_results`). Here results simply stay until the next heat arrives. |
| background image behind the table | Neither display does this today — `.background` in the CSS is a flat `--color-bg` fill. Worth having on both. |

---

## Rules that keep the two in step

- **The Qt package must not import from `server/`.** It is a remote client of the API in
  [`docs/api.md`](../docs/api.md), exactly like the phone apps. Sharing a repo is a
  convenience, not a licence to cheat.
- **Frames are partial.** `update_scoreboard` carries only changed keys; both sides merge.
- **Deltas come from the structured fields** — `lane_delta_seconds<i>` and
  `lane_delta_better<i>`, not the HTML `lane_delta<i>` blob, whose CSS classes resolve to
  nothing outside the page (`docs/api.md` §5.1).
- **The two run the same git ref.** That is what guarantees they agree about the WebSocket
  contract; displays register their ref on connect and Settings → Network flags a mismatch.

`cloud/templates/live-mobile.html` is a third, deliberately simpler display and is **not** in
scope here — see [`cloud_parity.md`](cloud_parity.md).
