# Cloud vs Pi — Design Decisions

Intentional differences between the Pi server and the cloud relay, and the reasoning behind them.

---

## Display philosophy

The cloud view is intentionally simpler than the Pi for two reasons:

1. **Resilience** — the cloud serves remote attendees over a relay connection that can drop and reconnect at any time. A stateless display (always showing whatever the latest data says) means any attendee joining mid-meet immediately sees a correct screen without needing to replay a sequence of events. State-dependent transitions can leave the display stuck if an event is missed.

2. **Load** — high-frequency events that only drive cosmetic display features are stripped before forwarding, since each event is multiplied by the number of connected attendees.

---

## Always-on columns, no transitions

The Pi's **kiosk** board (`live.html`, the page the Qt display mirrors) transitions between intro, running and results with column animations. Neither phone view does: columns are always visible and the screen directly reflects the latest frame.

- **`columns_state`** — the operator collapses and expands columns on the kiosk. The relay does not forward mid-meet column changes, and the phone board no longer listens for them.
- **`race_finished` / podium animation** — the podium highlight is triggered locally by the Pi. Not forwarded; adds complexity with little benefit on mobile.
- **`display_overlay` (carousel) and `test_mode`** — kiosk-only for the same reason; see *Carousel / image overlay* below.

**This is no longer a cloud-versus-Pi split.** Both servers now render the same
`shared/templates/live-mobile.html`, so the phone board behaves identically on the
pool LAN and over the relay. The divergence that remains is *kiosk vs phone*, not
*Pi vs cloud*.

Implied results are the one case worth spelling out. When the operator advances the
heat before the console publishes results, the board still shows the times rather
than blanking them — but it holds no timer. The Pi's phone view used to revert to
intro after 3 seconds (`brief_results`); that was removed, because it deliberately
put the screen out of step with the console, which is wrong for the operators who
now use this page and unsafe for a phone that reconnects into the middle of it. The
kiosk keeps its own version of the behaviour.

---

## Chronometer

The Pi sends `running_time` as part of `update_scoreboard` at high frequency during a race (every timing tick). Forwarding that to every attendee of every meet is a large share of the relay's event traffic, so the cloud used to strip the field outright.

It no longer does — the objection was always the frequency, not the field. `_forward()` now **throttles** it to at most one frame every `_CLOCK_SYNC_SECS` (2s), plus any frame that also carries a `lane_running<i>` key, since a start, a wall, a push-off and a finish are rare and are exactly where the value has to be right. That is roughly one short string every two seconds per meet instead of ten to twenty a second, and the phone gets a real clock back: it re-bases on each frame and interpolates in between, the way the Qt board already does between console frames. Stripping the field saved a little more traffic and cost the phone every sign that a race was under way.

The clock is deliberately **not** merged into `last_scoreboard`. The join replay sends that snapshot with no way to say how old it is, and a stale clock is worse than none; a phone joining mid-heat shows the lane-number pulse — the number cycling between the row text colour and the timing colour — until the first re-base arrives, at most one interval later. `docs/mobile-features.md` `L-12` is the full client contract, splits and backgrounding included.

---

## Carousel / image overlay

Carousel images are files local to the Pi. Relaying them would require encoding them as base64 and caching on the cloud server — significant complexity for a feature mainly useful on the pool-deck display, not remote phones.

---

## Name overflow: every display now shrinks

`fitNameFontSize()` measures each `.name-primary` against its cell and scales the cell's font down by the overflow ratio, keeping the ellipsis as a floor for a name too long to shrink in at all. It lives in `shared/templates/scoreboard_base.html` and runs on the phone board, the Results tab and the kiosk `live.html` alike. The Qt board does the same thing with `FitLabel` — see [`scoreboard_parity.md`](scoreboard_parity.md).

**This is not a cloud-versus-Pi difference and never really was.** It used to be Pi-results-only, and the entry here read as though clipping on a live board were a decision: uniform row heights and one font size across lanes. It was not. Portrait rows are floored by `min-height`, and landscape rows are table rows sharing the table's height, so shrinking a name changes type size and nothing else. The Qt display — the one spectators actually watch — had shrink-to-fit from the start.

The cost is a synchronous layout pass per lane, so it is gated: it runs when a frame carries a `lane_name` key (a heat change), on resize, and when a hidden tab is revealed. Never per tick.

What remains a real limitation is the *event name* in the header, which CSS can only wrap or clamp — see the header table in `scoreboard_parity.md`. That, and finer control at small sizes, is what `adjustsFontSizeToFitWidth` and `autoSizeTextType` still buy the native apps (`R-08` in [`../docs/mobile-features.md`](../docs/mobile-features.md)).

---

## Cloud-only features

The following exist on the cloud but not on the Pi:

- **Pull-to-refresh** — swipe down from the top of the mobile view to reload
- **Safe-area insets** — notch and Dynamic Island support on iOS
- **Add-to-Home-Screen hint** — iOS Safari prompt for full-screen PWA install
