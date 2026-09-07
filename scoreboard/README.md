# Qt scoreboard (TV display)

The native display for the kiosk Pi. Replaces the Chromium kiosk that rendered
`/live` in a browser.

> **`/live` is the reference, not `/scoreboard`.** The kiosk pointed at
> `http://splouch.local`, which redirects to `/live`. The two templates have
> diverged, and where this display departs from `/live` it is called out:
>
> | | `/live` (the kiosk) | `/scoreboard` | here |
> | --- | --- | --- | --- |
> | between heats | instant cut | five-step dissolve | dissolve |
> | delta column | 15vw | 9vw | 15vw |
> | title on the splash | absent | present | present |
> | idle splash timeout | yes | yes | not implemented |
>
> [`notes/scoreboard_parity.md`](../notes/scoreboard_parity.md) is the full ledger —
> every layout aspect of the two, marked *match*, *intentional* or *gap*. Read it
> before changing anything visual on either side.

## Qt binding

**PySide6 (Qt 6)**, installed by `uv sync --extra scoreboard`. It replaced PyQt5
for three reasons, in order:

1. **PyQt5 does not run on a 64-bit Raspberry Pi from PyPI.** Neither `pyqt5` nor
   its `pyqt5-qt5` runtime publishes a linux aarch64 wheel, and piwheels does not
   build them either, so `uv sync` cannot resolve it at all. The only route was
   Debian's `python3-pyqt5` plus a `--system-site-packages` venv pinned to
   `/usr/bin/python3` — which takes the Python version out of uv's hands.
2. **Licence.** PyQt5 is GPLv3; this project is MIT. PySide6 is LGPL.
3. **Variable fonts.** Qt 5 has no support; three of the bundled faces are
   variable and only rendered correctly by accident. Qt 6.7+ handles them.

Porting notes, if you ever touch this boundary again:

- `pyqtSignal` → `Signal`; `exec_()` → `exec()` (the old name still works but is
  deprecated).
- `QWIDGETSIZE_MAX` is not exported by PySide6 — `board.py` defines it.
- `QFontDatabase` query methods are **static** in Qt 6; PyQt5 needed an instance.
- **Zero-valued enums are truthy** in PySide6. `bool(QAbstractAnimation.Stopped)`
  is `True`, where PyQt5 returned a falsy `0`. Compare explicitly
  (`state() != Running`), never `if anim.state():`.
- Every *unscoped* enum form the board uses still works (`Qt.AlignCenter`,
  `QFrame.NoFrame`, `QSizePolicy.Ignored`, …) — all 18 were checked.

## Why native

- **Names fit.** CSS can only truncate an overlong swimmer name; Qt measures text
  before drawing, so `FitLabel` shrinks it to fit. This is the reason the
  migration exists.
- **4K without a layout engine.** Chromium spends RAM and CPU on a DOM the
  scoreboard does not need. Qt draws the rows directly.

## Why it lives in this repo

Same language, same `uv`, same Pi, no app store — none of the reasons that
justify a separate repo for the iOS/Android clients apply here. More importantly,
one repo means `install.sh server <version>` and `install.sh kiosk <version>`
resolve to the same git ref, so the display and the server can never disagree
about the WebSocket contract. See
[`notes/native_app_strategy.md`](../notes/native_app_strategy.md).

The convenience is not a licence to cheat: this package **must not import from
`server/`**. It is a remote client of the documented API in
[`docs/api.md`](../docs/api.md), exactly like the phone apps.

## Running

```bash
uv sync --extra scoreboard                       # PySide6 — kiosk role only
.venv/bin/python -m scoreboard --windowed        # development, against splouch.local
.venv/bin/python -m scoreboard --server http://10.10.10.10:5000 --windowed
```

On the kiosk Pi the desktop session autostarts
[`install/scripts/start-scoreboard.sh`](../install/scripts/start-scoreboard.sh),
which resolves the venv from its own location, reads the server address from
`~/.config/splouch/scoreboard.env`, and restarts the app if it exits.

## Operator keys

The kiosk window has no decorations and no menu, so these keys are the only way
off the board without an SSH session.

| key | effect |
| --- | --- |
| **Ctrl+Q** | quit to the desktop |
| **F11** or **Ctrl+F** | toggle fullscreen |
| **Esc** | leave fullscreen (never quits) |

Ctrl+Q is deliberately two-handed — a stray keypress must not blank the TV
mid-meet — and Esc deliberately does *not* quit, only un-fullscreens.

F11 is the Linux-wide fullscreen convention and the one to document first;
Ctrl+F is a second binding for hands used to it. Ctrl+F normally means Find, but
the board has nothing to search, so nothing is displaced.

**Quit and relaunch are two halves of one design.** `start-scoreboard.sh` relaunches
the app whenever it exits **non-zero** (a crash, a killed process) but not on a
clean exit, which is what Ctrl+Q produces. So quitting really does return you to
the desktop and stay there. Getting back is the **Scoreboard** icon the installer
puts on the desktop; there is also a **Settings** icon that opens the server's
admin page in a browser.

Consequence worth remembering: anything that makes the app exit 0 looks like a
deliberate quit and will *not* relaunch. That is why the lane-count rebuild in
`app.py` builds the new window before closing the old one — closing the only
window emits `lastWindowClosed`, which quits cleanly under Qt's default
`quitOnLastWindowClosed` and would leave the TV black until someone clicked the
icon.

## How it works

| module | role |
| --- | --- |
| `app.py` | entry point; owns the window, the link, and the config-refresh policy |
| `client.py` | `GET /config` + the `/ws/scoreboard` receive loop, in a daemon thread |
| `board.py` | header bar and lane rows; merges partial `update_scoreboard` frames |
| `widgets.py` | `FitLabel` — the shrink-to-fit label |
| `theme.py` | normalises `/config` (colours, fonts, labels, strings, `show_*` flags) |
| `cache.py` | remembers the last `/config` so a cold boot starts themed and translated |
| `splash.py` | the carousel overlay — title, sponsor images, background |
| `format.py` | value formatting (deltas); Qt-free so CI can test it |
| `fonts.py` | registers the bundled TTFs, resolves family names, falls back to monospace |
| `version.py` | what git ref this checkout is, reported on `register` |
| `updater.py` | self-update on the server's command, then exit for a relaunch |

### Theming

Everything comes from **Settings → Display → Theme** on the server — the same
settings that theme the web scoreboard. Nothing is hard-coded but the fallbacks in
`theme.py`, which only apply before the first `/config` on a fresh install.

All 18 board colours are honoured, including the two that are easy to conflate:
`header_label` (the word *EVENT*) and `header_value` (the number after it) are
separate swatches, so the header is built from `HeaderCell` widgets holding two
labels rather than one string.

Two of them, `connection_lost` and `connection_lost_text`, are the only colours no
browser page uses: nothing but this display can tell that the console has stopped
talking to it. The first is the warning colour — the badge's pill and the frozen
clock — and the second is the text on that pill. It defaults to the board
background, so a fresh install looks unchanged; it is a separate swatch because the
pill behind it is a warning colour rather than a board colour, and what reads well
on it is not necessarily what reads well on the board.

The three fonts are three distinct settings and land in three places:

| setting | key | used for |
| --- | --- | --- |
| Font | `family` | names, clubs, headers |
| Timing Font | `timing` | lane times and deltas |
| Digit Font | `digits` | the running clock — usually a seven-segment face |

Only the `schedule_*` colours are unused, and correctly so: they belong to the
schedule and next-heats pages, which this display does not render.

`tests/test_scoreboard_startup.py` paints a distinct sentinel colour into every
key and asserts each one reaches a widget. That test exists because two settings
— `header_label` and Digit Font — were silently ignored at first, and a theme key
the display quietly drops is invisible until someone changes it on meet day.

Two rules the code depends on:

- **Frames are partial.** `update_scoreboard` carries only changed keys. They are
  merged into `BoardWindow.snapshot`; never replace it.
- **Deltas come from the structured fields.** Use `lane_delta_seconds<i>` and
  `lane_delta_better<i>`, not the HTML `lane_delta<i>` blob meant for the browser
  (`docs/api.md` §5.1). All three carry the same number — finish minus seed — but
  the HTML one bakes in formatting *and* styling via CSS classes that resolve to
  nothing outside the page. `format.fmt_delta` reproduces the server's own
  formatting from the structured value, including the switch to `m:ss.hh` past a
  minute; `tests/test_scoreboard_format.py` cross-checks it against
  `meet_data._delta_html` so the two can't drift.

### The column reveal

Time, delta and place collapse to nothing when a heat loads and slide open over
500ms when the race starts, matching `.timing-anim`'s `0.5s ease` in the browser.
It is purely cosmetic, and the contrast is the whole point: between heats the
board is a calm start list with the names given the full width, then the race
begins and the timing columns arrive.

| trigger | effect |
| --- | --- |
| `current_event` **or** `current_heat` changes, results on screen | the heat transition below |
| same, but the board is empty | collapse at once — nothing to dissolve |
| first lane starts running | reveal, animated |
| `columns_state {hidden}` from `/operator` | either, animated |
| `reset()` / idle board | expanded |

Either half of the `(event, heat)` pair changing counts as a new race — event 3
heat 1 → event 4 heat 1 is a new start list even though the heat number held. Only
a *change* counts, since the console resends both fields constantly. The board
starts expanded so an idle display looks finished rather than half-drawn.

Driven by `maximumWidth`, not layout stretch: these cells use
`QSizePolicy.Ignored`, which carries the Expand flag, so a stretch of 0 would not
reliably close them.

### The heat transition

**A deliberate departure from `/live`.** `live.html`'s `mode_to_intro()` collapses
the columns instantly — it strips the `timing-anim` class, sets the widths to 0,
forces a reflow, then puts the class back, which is the standard "change this
without animating" trick. Only `scoreboard.html` dissolves. This display follows
`scoreboard.html` here because the dissolve looks better on a TV; everything else
follows `/live`.

Going from one heat's results to the next start list is a five-step dissolve,
mirroring `mode_to_intro()` in `scoreboard.html`. 500ms per step:

1. **Podium tints fade** back to the row stripes
2. **Columns close**
3. **The table fades out**
4. **The new heat is painted while it is invisible**
5. **The table fades back in**

Three things make it work:

- **The table pauses.** From step 1 until step 4, incoming frames merge into the
  snapshot but are not painted (`BoardWindow.paused`). Otherwise the next heat's
  names would appear on top of the outgoing results. The header bar keeps updating
  live, as it does in the browser.
- **A race cancels it.** `cancel_heat_transition()` snaps straight to the current
  state if a lane starts running mid-dissolve — a swimmer on the blocks outranks
  an animation.
- **An empty board skips it.** Two seconds of dissolving nothing just looks slow,
  so a heat loaded onto a clear board collapses outright.

The previous heat's `lane_time`/`lane_place`/`lane_delta` keys are dropped from the
snapshot at the moment of the heat change — *except* any that arrived in that same
frame, which belong to the new heat. They are not merely hidden behind the closed
columns: the operator can reopen those at any moment, and `refresh()` repaints
from the snapshot.

Qt stylesheets do not animate, so the podium fade interpolates the colour itself
and re-applies it; the browser gets that free from a CSS `background-color`
transition.

### Why every cell is a `FitLabel`

Not just the names. Qt clips a label to its own rect, so an oversized value never
reaches the neighbouring cell — but it is cut *through a glyph*, which reads as
corruption rather than as truncation. At six lanes on 1080p the row is tall
enough that `1:12.44` at 52% of row height overflowed the time column, and the
clipped delta beside it rendered as `1:12.44).06`. Long translated headers did the
same (`COULOIR` in a five-unit lane column).

Below the font floor (`min_px`, 10px) the text is elided instead, so it ends in
`…` rather than half a character — a 27-character club in an 8vw column cannot fit
at any readable size. `text()` still returns the full string; `displayed_text()`
returns what is painted. `tests/test_scoreboard_columns.py` measures the painted
text against every column width.

**`setFont` re-fits, and it has to.** A `QFont` carries a size as well as a face, so
the plain `QLabel` behaviour is to discard whatever size the fit arrived at and fall
back to the family's default — about 13px, which on a 4K TV is invisible.
`apply_theme` assigns a font to every label and runs on **every** `/config` reload,
including the first one seconds after the kiosk window opens. So this fired at every
boot.

It looked intermittent because most cells are re-fitted moments later by `refresh()`
writing their text back, and `setText` re-fits. The ones with no text to re-set —
the **lane number**, whose text is set once in `__init__`, and the **EVENT/HEAT**
cells before a heat is loaded — simply stayed tiny until someone resized the window,
which is what made the bug look like a rendering glitch rather than a restyle.

Anything that sets a font on a `FitLabel` therefore goes through the override; inside
`_refit` itself every assignment uses `super().setFont`, or it recurses.

**Three labels are not `FitLabel`s** and need the same care by hand: the status
overlay's two lines and the test-session badge. They are sized directly from the
window (`resizeEvent`, `Badge.place`) rather than fitted to a cell, so
`apply_theme` restyles them through `board._restyle()`, which carries the current
pixel size onto the new font. Without it the waiting message — the only thing on
screen on a board that cannot reach the server — came up at ~13px on every boot.

If you add a widget with a computed font size, it belongs in one of those two camps.
Assigning a bare `QFont` to it in `apply_theme` is the bug.

### Sizing is per-row, never from the window

Each `LaneRow` and `HeaderRow` scales its own fonts from its **own** `resizeEvent`.
Reading a child's height from the window's `resizeEvent` returns the geometry from
*before* the layout ran: it reported 480px for rows that ended up 161px tall, so
every cell came out about three times too large.

Cells also use `QSizePolicy.Ignored` in **both** directions with a zero minimum.
Their fonts are derived from the row height, so a font-driven minimum height feeds
straight back into the layout — a 1080p board inflated to 1460px tall, which on a
fullscreen TV means the bottom lane is cut off.

### Staying in step with the server

The single-repo decision assumes the kiosk and the server run the **same git ref**
— that is what guarantees they agree about the WebSocket contract. Two pieces keep
that true without an SSH session per Pi.

**The display registers.** On every connect it sends `register` with its role,
hostname, `git describe` output and whether the checkout is dirty. Settings →
Network lists them and flags any display whose ref differs from the server's, or
that has uncommitted changes. A version split used to be invisible until something
broke.

**The server can update them.** *Settings → Update → Update displays* broadcasts
`update` carrying the server's own ref. Each display fetches, checks out, runs
`uv sync`, streams progress back as `update_log`, and exits
non-zero — `start-scoreboard.sh` relaunches it on the new code, so no systemd unit
or sudo is involved.

Four rules make it safe:

- **The display follows the server; it cannot be aimed elsewhere.** No target is
  accepted from the operator, because letting one pick reintroduces the split this
  exists to close. Update the server first, then press the button.
- **Never mid-race.** The server refuses while any lane is running and the display
  refuses too, in case it missed a frame. Finishing an update means restarting.
- **Only exit on success.** A failed `uv sync` leaves the old code running and
  reports why. Restarting into a broken checkout means a black TV and a walk to
  the kiosk — worse than being a version behind.
- **Never touch a dirty checkout.** `git checkout` would discard someone's work.

### The header bar

**The bar height is a fixed fraction of the window** (`_H_BAR`, 10.5% — larger
than the browser's 85px at 1080p, which is the floor of what reads across a pool
deck). Every text size is then a fraction of *the bar*, so `_H_BAR` is the single
knob that scales the whole header.

That order matters. The bar height must be set first and independently; deriving
it from its content and the content from it is circular, and was the bug that left
the header at 34px with unreadable text.

**There is no meet title in the header** — it belongs to the splash overlay.
`live.html` has a title cell, but only ever calls `set_header_mode(true)`, which
hides it; the header the kiosk actually displayed never carried one.

**The cells have fixed widths** — 10 / 10 / 51 / 16 / 13 percent, summing to 100 as
the column weights do. Nothing in the bar moves when the event number gains a digit
or the race clock blanks out. This is the one place the browser followed the display
rather than the other way round: `.header_cells_fixed` in `timing_display.css`
carries the same five numbers, and `live.html` opts into it.

Left to right:

| cell | content | colour | size (of the bar) |
| --- | --- | --- | --- |
| EVENT / HEAT | small word **above** a large number, both left-aligned | word `header_label`, number `header_value` | 15% / 57% |
| event name | centred | `header_value` | 50% |
| race clock | digits font, blanks between heats | `time` | 57% |
| wall clock | digits font | `header_value` | 57% |

**The race clock clears when the heat ends** — it has nothing to report once
nobody is swimming, which is what `stop_chrono()` does in the browser. We clear
its *text* rather than hiding the widget: a hidden widget drops out of the layout
and every cell to its left slides across, so the event name would jump on every
heat. `live.html` used to hide the cell and now blanks it too, for the same reason.

So the title, both numbers and the wall clock are all one colour, and the race
clock is the only header element that stands out. **This diverges from the
browser**, which gives `#current_event` the *label* colour — leaving the header's
text in two near-identical greys for no benefit. `header_label` here means only
the small EVENT/HEAT word.

The event/heat cells are a *vertical* stack, matching `.header_cell`'s column
flex: small word on top, large number beneath.

**They lead the bar**, hard against the left edge, and are blank until a heat is
loaded — an idle board shows only the wall clock. Blanking rather than hiding: a
hidden widget leaves the layout and Qt hands its stretch to the neighbours, which is
exactly what the fixed widths above exist to prevent. Hiding them used to leave the
wall clock about three-quarters of the way across an idle board instead of at the
right edge. Same rule as the race clock, which keeps its slot for the same reason.

The wall clock is the far-right cell (`#meet_datetime`), ticking every 10s since
it only shows HH:MM.

### The podium

Gold, silver and bronze tint the top three rows when **Settings → Display → Podium**
is on. Two things about *when*:

- **Not until the heat is over.** The trigger is the browser's own `all_done`: no
  lane running, and no lane holding a time without a place. Tinting as each place
  lands instead — which this display did at first — sends the first finisher's row
  gold while the rest of the heat is still in the water.
- **Then staggered**, 400ms apart in placing order, each easing in over 500ms. That
  is `highlight_podium()`'s `{1: 0, 2: 400, 3: 800}` plus the 0.5s CSS
  `background-color` transition it rides on.

One reveal per heat: `_podium_shown` is re-armed when the event or heat number
changes, and `highlight_podium()` is a no-op while there is no podium to show, so an
empty start list between heats cannot consume it. `race_finished` calls it too, as
the browser does, in case the placing frames were missed.

Qt stylesheets do not animate, so both the fade in and the fade out interpolate the
colour and re-apply it; the browser gets both free from CSS.

### The test-session badge

A recorded session (Settings → Test) replays real console traffic, so the board
looks exactly like a live race — which is the point, since the operator is
watching it to check the board. `test_mode {active}` therefore shows a small pill
at the bottom centre, matching `.test-overlay` in the browser: inverted colours,
75% opacity, 2.5% up from the bottom.

It must **not** use `set_status()`. That overlay is opaque and full-screen, so
routing `test_mode` through it hid the entire scoreboard behind the words TEST
SESSION — which is exactly what happened first time round.

Badges are a shared `Badge` widget, and there are two: this one along the bottom and
the link-lost one along the top, so they can never overlap. A recorded session can
drop its link like any other, and both notices have to survive it.

### The splash / carousel overlay

Raised over the whole board by the carousel button on `/operator`, which arrives
as `display_overlay {active}`.

| what | behaviour |
| --- | --- |
| shown by | the `/operator` carousel button |
| hidden by | the same button, **or** any lane starting to run |
| contents | meet title along the top, sponsor images cross-fading beneath |
| background | `shared/static/img/scoreboard_bg.png` |
| timings | 0.8s fade in/out, 1s cross-fade, slide interval from `carousel_interval` |

**A race dismisses it**, so the operator does not have to remember. The display
also sends `set_overlay {active: false}` back rather than just hiding: otherwise
the button on `/operator` stays lit with nothing behind it, and the next press
appears to do nothing because the server still believes the overlay is on.

#### With more than one kiosk

The overlay has always been all-displays-or-none — it is server state
(`state._overlay_active`), and the `/operator` button already drove every browser
client at once. Several kiosks therefore behave correctly, with two properties
worth keeping:

- **Each kiosk sends exactly one frame per dismissal.** `splash_visible` reports
  the *intent*, not `isVisible()`: it flips to False the moment a dismissal starts
  rather than when the 800ms fade ends. Without that, `update_scoreboard` (which
  arrives ~10×/second during a race) re-triggered on every frame — ten
  `set_overlay` frames per kiosk, each of which the server rebroadcasts to all the
  others.
- **`set_overlay` is an absolute set, not a toggle**, so N kiosks all asking for
  `active: false` converge rather than flip-flopping, and the resulting
  `display_overlay` rebroadcasts are no-ops on a display already going down.

**The meet title is a deliberate addition.** `live.html` has no title on its
carousel — `scoreboard.html` does, and this follows `scoreboard.html`. It comes
from Settings → Display → Title (`meet_title` in `/config`).

**The background is `scoreboard_bg.png`**, not a black rectangle. Sponsor logos are
usually transparent PNGs, so what sits behind them is most of what the audience
sees while the overlay is up. It is read from the repo, like the fonts — same git
ref as the server, so no fetch needed.

**Images are fetched off the GUI thread** from `GET /images/{filename}`, one signal
per image as it lands, so the first slide appears while the rest are still coming
in. `/config` carries `carousel_images` and `carousel_interval`.

### The race clock

A lane's time cell has two owners, and which one is in charge is the whole
mechanism:

| `lane_running<i>` | the time cell shows | colour | why |
| --- | --- | --- | --- |
| `true` | the race clock, ticking | grey `#a0a0a0` | the swimmer is mid-length |
| `false` | `lane_time<i>`, frozen | 0.8s flash from white down to `time` | they touched a wall — this is the split |

**The colour is what says which owner has the cell.** A ticking clock and a frozen
split are the same digits in the same place otherwise. `.time-running` and the
`time-lock-flash` keyframes do this in the browser, and the two values are hardcoded
there — they are not theme keys, so they are hardcoded here too rather than becoming
settings that exist on only one of the two displays.

**A lane pauses at every wall.** On the touch the console drops the running flag
and sends the lap in `lane_time<i>`, then holds it there for a fixed number of
seconds — a setting on the console, not the length of the turn, and not ended by
the swimmer leaving the pad — before the flag returns and the lane rejoins the
clock. Freezing it is the entire point: without that, the lap time is overwritten
before anyone sees it.

Consequences worth knowing before touching this code:

- **A running lane ignores `lane_time<i>`.** The split stays in the snapshot after
  it lands, so any later frame touching that lane would stamp the stale split back
  over the live clock. `LaneRow.update_from` skips the time cell while running.
- **All running lanes show the same value** — the console's race clock — as in the
  browser. There is no independent per-lane timer; a lane's own elapsed time only
  becomes meaningful at its split.
- **The clock is interpolated locally.** The console sends `running_time` a few
  times a second, which would visibly step. The board re-bases on each frame and
  ticks at 50ms in between, so it never free-runs for long.
- **The header is painted on every frame, not only by the ticker.** The ticker
  stops when no lane is running, and during the seconds when every lane is paused
  at a wall the header would otherwise freeze.
- **`race_finished` calls `stop_clock()`** as a backstop. The console normally
  clears the flags first, but a missed frame would leave the ticker counting up
  over the final times.

### Starting before the server does

The display depends on the server for two separate things, over two separate
connections:

| | what | when |
| --- | --- | --- |
| `GET /config` | plain HTTP request/response — theme, labels, lane count, column flags | at startup, and again on every `reload` |
| `/ws/scoreboard` | WebSocket — the live frames | held open for the whole meet |

Both fail while the server Pi is still booting, and each recovers on its own:
`ConfigLoader` retries on a timer, `ServerLink` runs its own reconnect loop.

The window therefore opens **before either succeeds**. At a meet both Pis power up
together and the kiosk usually wins — it has no service to start — so the board
draws immediately with fallback theming and a waiting message, then adopts the
real config when `/config` answers.

**Neither call may run on the GUI thread.** This is the whole reason `ConfigLoader`
exists rather than a direct `fetch_config()`. A server that is reachable but not
yet answering does not fail fast; it hangs for the full 10s timeout. Called inline
from `ScoreboardApp.__init__` that runs *before the first paint*, so the TV shows
nothing at all — measured at 10.05s to first paint before the fix, 0.05s after.
`tests/test_scoreboard_startup.py` guards this.

### Losing the server

Two different problems, so they look different. Confusing them is what made a
one-second network blip blank a live scoreboard.

| | board holds | treatment |
| --- | --- | --- |
| **never connected** | nothing | full-screen *Waiting for the timing server* |
| **dropped mid-meet** | a real start list or real results | a badge, top centre |

**A cold boot gets the whole screen** because there is nothing to cover and whoever
is setting up needs to know why the TV is blank. Under the headline sits a dimmer
line, `splouch.local · retrying · 47 s`. The elapsed count ticks every second for
one reason: it is the only thing on screen that says the display is still trying
rather than hung. A static message looks identical to a crash.

**A mid-meet drop never covers the board.** Everything on screen is still the last
thing the console actually said, and an official reading lane 4's split does not
want it replaced by an apology. The badge sits below the header bar — top centre,
but clear of the event, heat and clocks, which are exactly what stays useful during
an outage — and carries the same elapsed count: `⚠ CONNEXION PERDUE · 12 s`.

**How long until it shows.** Two delays add up, and only one of them is a choice:

| | |
| --- | --- |
| noticing | `_STALE` + `_PING_EVERY` in `client.py`, ~10s for a silent drop; instant for a clean close |
| then waiting | whatever is left of `_DROP_GRACE_MS` (4s) |

A pulled cable is not an error the socket raises. `recv()` just blocks until its
timeout, and the `ping` sent afterwards succeeds too — it only has to reach the
kernel's send buffer. So the link is declared dead by *silence*, and the heartbeat
constants are the detection time. They were 20s and 50s, which took three ping cycles
to trip: up to a minute of confidently ticking, entirely fictional race clock.

The grace period then spends only what is **left** of itself. Detection has already
cost ~10s of silence, so a pulled cable is announced at once — it is provably not a
blip. What the grace still buys is the case noticed instantly: a server closing the
socket cleanly on a restart, usually back in seconds. `live.html` hedges the same way
with `LEAVE_RESULTS_DEBOUNCE`.

**The count is backdated** to when the server actually went quiet, via
`ServerLink.silent_seconds()`. Otherwise the badge starts from zero on a link that
has already been down for ten seconds, and the one number an operator uses to judge
the outage is wrong by the whole detection window.

Measured end to end against a socket that accepts and then goes silent: link
declared dead at 11.4s, badge up at 11.6s reading `⚠ CONNECTION LOST · 11 s`.

**The clock freezes the instant the link goes**, grace period or not, and this is the
part that is not cosmetic. The ticker interpolates between `running_time` frames, so
left alone it keeps counting up smoothly off a base that stopped arriving — the board
would show a confident, fabricated race time, and until now the full-screen overlay
was the only thing stopping anyone from reading it. Frozen and tinted with the
theme's `connection_lost`,
it says "this is the last figure the console gave me". Every running lane's time is
tinted with it, so the badge and the stale numbers obviously belong together.

The lanes' `running` flags are left alone: they are what the console said, the race
is presumably still going, and clearing them would fire the split-lock flash on every
lane and lose the state needed to resume. On reconnect the ticker stays stopped until
the next `running_time` re-bases it — restarting from the stale base would make the
clock jump.

**`connection_lost` is a theme colour** like every other one on the board — Settings →
Display → Theme → *Status*. It is in its own group there because it is not part of
the board's normal look: it appears only on the TV, and only when the console has
stopped talking. The three shipped presets each pick a red that stands clear of
their own `time` colour (`#ef5350` dark, `#ff5c7a` blue, `#c62828` white).

It is also the first colour added since installs existed in the wild, which exposed
a gap worth knowing about: `load_settings()` merges the stored theme *over* the
defaults, because `settings.update()` is shallow and would otherwise drop any key
added after that `settings.json` was written. Without that merge the Settings picker
renders a missing colour as empty — black — and saves it.

**Translated** from the `[display]` section of the locale files, selected by
**Settings → Display → Scoreboard language** — the same setting that translates the
board itself. The server ships the strings in `/config` as `display_strings`
(English-merged, so a partially translated locale falls back per key). Add a
language by adding a `[display]` section to its `.toml`;
`tests/test_scoreboard_i18n.py` fails if a locale is missing a key.

### The config cache

Those strings create a chicken-and-egg problem: the waiting screen exists to be
shown *before* `/config` arrives, but `/config` is what carries the language,
theme and lane count.

`cache.py` resolves it by writing the last config to
`~/.cache/splouch/scoreboard-config.json` and loading it at startup. Only the very
first boot after installation looks generic; after that the kiosk comes up in the
right language and colours even if the server never answers at all.

Two details that matter on a Pi someone switches off at the wall: the write is
atomic (temp file + `os.replace`), so a power cut cannot leave a truncated cache
that poisons every later boot; and an unchanged config is not rewritten, since
`/config` is re-fetched on every reconnect and that would be pure SD-card wear.
Any unreadable cache reads as absent and falls back to the built-in English.

> A WebSocket is only HTTP for its handshake: the client opens with a normal
> `GET /ws/scoreboard` carrying `Upgrade: websocket`, the server replies `101
> Switching Protocols`, and from then on the connection carries frames, not
> HTTP messages. Same host and port as `/config` either way — it is all one
> uvicorn process.

### Fonts

The theme faces ship with the repo as TTF in
[`shared/static/fonts/`](../shared/static/fonts/) — alongside the woff2 the browser
uses, since Qt cannot read woff2 — and `fonts.py` registers them at startup. No
system font packages are required.

One trap is worth knowing before you touch font names. The browser declares its
own family label in CSS (`@font-face { font-family: 'DSEG7Classic' }`), which need
not match anything inside the file; the font itself is called `DSEG7 Classic`,
with a space. Qt reads the real name, so an exact-match lookup drops both DSEG
faces to the fallback. `resolve_family()` therefore matches ignoring spaces and
case. See that directory's [README](../shared/static/fonts/README.md) for
provenance, licences, and the variable-font caveat.

## Font sizing

Nothing is sized in absolute pixels. Every size derives from the window, so the
same code fills a 1080p TV and a 4K one without a second layout.

**The ceiling.** `_FONT_MAIN = 0.52` in `board.py`: on each resize,
`LaneRow.set_row_height` sets the row's text to 52% of the row height and hands
the same number to the name label as its *maximum*. Names therefore never grow
larger than the lane / club / time / place text beside them — a short name simply
matches its row rather than ballooning to fill the column.

Relay member names use `_FONT_ALT`, which is `0.7 × _FONT_MAIN` — `.name-sub`'s
`0.7em` in the browser. A relay row splits its name cell 50 : 35 between the two
lines, and each is then capped from the slice it actually gets: `FitLabel` only ever
solves for *width*, so a ceiling taken from the whole row would size the team name
to roughly twice its own cell and clip it top and bottom.

The header bar sizes its text off its own height: 15% for the small EVENT/HEAT word,
50% for the event name, 57% for the numbers and both clocks. The column titles use
`_FONT_HEADER = 0.62` of the header row, which is half a lane row — landing at the
browser's 3vh against the rows' 5vh.

| ceiling | 6 lanes | 8 lanes | 10 lanes |
| --- | --- | --- | --- |
| **1080p** | 83px | 63px | 52px |
| **4K** | 170px | 130px | 105px |

**The floor.** `FitLabel(min_px=10)` in `widgets.py`. Below 10px the search stops
and the text clips instead of shrinking further. In practice it is unreachable —
a 44-character name still resolves to 27px at 1080p, and only a ~90-character
string gets near it.

What a row actually renders at, 8 lanes:

```text
1080p  →  short 63px · typical 44px · long 27px · 90-char 13px
4K     →  short 130px · typical 91px · long 56px · 90-char 27px
```

### Open question: the spread

The ceiling is settled; the *variation between rows* is not. At 1080p a short
name draws at 63px next to a long one at 27px, which reads as ragged in a way the
browser version never did — CSS truncated everything to one uniform size instead.

Three options, in order of preference:

1. **Fit per heat.** Measure the longest name in the current heat, then apply that
   one size to all rows. Keeps every name complete *and* makes the block uniform.
   Costs a second measuring pass on each heat change.
2. **Leave it.** Maximum information, uneven look.
3. **Raise the floor** to ~50% of the ceiling and elide beyond it. Caps the
   raggedness, but reintroduces the truncation this display exists to avoid.

Not worth deciding from a screenshot — judge it with real names on the real TV.

## Testing

`tests/test_scoreboard_config.py` (config normalisation),
`tests/test_scoreboard_format.py` (delta formatting) and
`tests/test_scoreboard_fonts.py` (family-name matching, bundled-font inventory)
are deliberately Qt-free, so they run in CI without the `scoreboard` extra
installed. Keep pure logic out of `board.py` for that reason — it is the module
that drags in PySide6.

`tests/test_scoreboard_startup.py`, `test_scoreboard_clock.py`,
`test_scoreboard_columns.py` and `test_scoreboard_splash.py` need Qt and
`importorskip` without it. Run them where PySide6 is available:

```bash
uv run pytest tests/
```

They share the session-scoped `qt_app` fixture in `tests/conftest.py`, which also
points `XDG_CACHE_HOME` at a temp dir. **Do not** give a QApplication a narrower
fixture scope: whichever fixture yields it holds the only Python reference, so
PySide destroys the C++ object at teardown — and Qt discards every font registered
with `addApplicationFont` along with it. The next module then runs against a
font-less application while `fonts._APP_FONTS_LOADED` still reports them loaded,
and every family silently falls back to monospace.

Widget behaviour needs a `QApplication`. Drive it headless with
`QT_QPA_PLATFORM=offscreen`, build a `BoardWindow`, feed it frames, and assert on
label text and font pixel sizes — that is how shrink-to-fit is verified (a long
name must resolve to a smaller pixel size than a short one, with its text
intact).

## Known gaps

This is a working scaffold, not the finished display. Still to do:

- **Automatic idle splash.** The overlay is operator-driven only. `/live` also
  drops to a splash on its own after `INTRO_TIMEOUT` / `RESULTS_TIMEOUT`; here the
  board holds the last start list until someone presses the button.
- **Background image behind the board.** `scoreboard_bg.png` backs the splash on
  this display. Neither board puts it behind the lane table — `.background` in
  `timing_display.css` is a flat `--color-bg` fill and `#splash_img` exists only in
  `scoreboard.html` — but both would be better for it.
- **Results hold.** The browser pauses on results for `RESULTS_TIMEOUT` and
  distinguishes a brief result from a full one; here results simply stay until
  the next heat arrives.
