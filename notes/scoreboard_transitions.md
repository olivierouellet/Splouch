# Scoreboard State Transitions

## States

| State | Code | Description |
| ------- | ------ | ------------- |
| Splash | 0 | Background image, no scoreboard content |
| Intro | 1 | New heat announced — names visible, no time/place/delta columns |
| Running | 2 | Heat in progress — all columns visible, times updating live |
| Results | 3 | Heat finished — final times and places, podium highlighted |

---

## Trigger Conditions (from socket updates)

- **→ Splash (0):** Server explicitly resets state. There is no idle timeout: `/live` has no watchdog, and the configurable `server_update_timeout` this line used to cite belonged to a second template that has since been deleted.
- **→ Intro (1):** `current_event` or `current_heat` value changes in a server update.
- **→ Running (2):** At least one `lane_running` flag is `true`.
- **→ Results (3):** All lanes with a recorded time have a non-blank place (heat is fully done and no lane is still running).

State is evaluated on every `update_scoreboard` socket message. The switch only fires when `current_state` differs from `last_state`.

---

## Transition Sequences

### Any State → Splash

1. Scoreboard fades out (`opacity: 0`, 500ms CSS transition).
2. Splash image fades in (`opacity: 0 → 1`, 3000ms CSS transition).
Scoreboard is invisible by this point. Prepare for next transition.
3. Scoreboard content cleared (names, clubs, times, diff, podium, place)
4. Columns at the right of Club instantly collapsed (no animation — `anim` class removed, widths set to 0, re-added).

**Timeout triggers:** none. The board stays in whatever state the console last put it in; the configurable `INTRO_TIMEOUT` / `RESULTS_TIMEOUT` these notes used to cite were the retired template's.

---

### Any State → Intro (new heat called)

#### Splash → Intro

`scoreboard_paused = true`, `hold_pause_on_run = true`. Header fades in (600ms). Splash image fades out (3000ms). Scoreboard fades in after 500ms delay (500ms, or 250ms if `intro_fast_mode`). If `lane_running = true` arrives during the pause, `intro_fast_mode` is set, fade-in completes at 250ms, then immediately transitions to Running.

#### Results → Intro

`scoreboard_paused = true`, `hold_pause_on_run = true`. If `lane_running = true` arrives at any point, `intro_fast_mode` is set; content table fade steps run at 250ms instead of 500ms.

**t = 0** — Podium row colors fade to plain grey (500ms `background-color` transition on `td`).

**t = 500ms** — Columns collapse to width 0 (500ms `width` transition). Results data remains visible while columns shrink.

**t = 1000ms** — Content table (`#scoreboard_content`) fades out (500ms, or 250ms). Background layer (`#scoreboard-bg`) stays fully visible — plain alternating row colors show through.

**On `transitionend`** (content table fully transparent):

- `td` transitions disabled, `reset_times()` and time column header cleared instantly, `td` transitions re-enabled.
- `hold_pause_on_run = false`, buffered names/clubs/event/heat applied, `scoreboard_paused = false`.
- Content table fades **in** (500ms, or 250ms). No time, place, or delta columns visible.
- If `intro_fast_mode`: after fade-in, immediately transitions to Running.

**Timeout:** none — the start list stays up until the next heat or an operator splash.

---

### Intro → Running (heat starts)

#### t = 0 ms

- Columns expand from width 0 to their target widths at their natural position (right of Club):
  - Place: 6vw
  - Time: 17vw
  - Delta: 9vw
- Club column slides left to make room.
- Time column header label restored.
- Animation duration: 500ms (CSS `width` transition).

The scoreboard is always fully visible before `mode_to_running()` is called — every Intro path guarantees its fade-in completes first.

---

### Running → Results (all swimmers finished)

- Columns remain at their current widths (no slide animation).
- If scoreboard was not visible, it fades in after 500ms.
- Podium rows highlighted (gold/silver/bronze).
- `scoreboard_paused = true` for 10 seconds to freeze display while results are read.

**Timeout:** none — results stay up until the next heat arrives.

**Edge case — new heat arrives while still Running:** Transitions to Results without highlights and without pause (`brief_results = true`), then automatically goes to Intro after 3 seconds.

---

## Pause Mechanism (`scoreboard_paused`)

Set to `true` during Results and the Results → Intro transition to prevent live updates from overwriting the display prematurely.

- **Splash → Intro pause** (`hold_pause_on_run = true`): Holds updates through the 500ms delay and fade-in. If `lane_running = true` arrives, sets `intro_fast_mode` and speeds up the fade-in to 250ms, then immediately transitions to Running.
- **Results pause:** Freezes the display for 10 seconds while results are read. Skipped if `brief_results` (Running → Results edge case).
- **Results → Intro transition pause** (`hold_pause_on_run = true`): Holds updates through the slide-out and fade-out. If `lane_running = true` arrives, sets `intro_fast_mode` but does not cancel — animations complete at 250ms speed, then immediately transitions to Running.

---

## Splash Image

- Stored at `~/SplouchData/images/` on the host machine.
- Served via the FastAPI route `/images/<filename>`.
- Rendered as a `position: fixed` full-screen element (`z-index: 150`) — above the scoreboard content but below nothing else.
- Positioned to start **below the header** (measured at show time via `getBoundingClientRect`).
- `object-fit: contain` — scales proportionally, no cropping.
- Fade in/out: `opacity` CSS transition (3000ms). A pending fade-out timer is cancelled if the image is shown again before it completes.
