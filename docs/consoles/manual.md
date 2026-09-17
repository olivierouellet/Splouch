# Manual console — no timing console

For a meet with no timing console at all: a club time trial, a practice meet, a
borrowed pool, or the day the serial cable does not turn up. The operator sets which
event and heat is on from a phone, and every board follows.

| | |
| --- | --- |
| Adapter | None |
| Wiring | Nothing is connected to the server |
| Protocol | None — event and heat are set by hand from `/manual` |

## What you get

The boards show the **event number, heat number, event name, the heat's scheduled
time, and every lane's swimmer and club**, straight out of the loaded Lenex or Hytek
file — the same header and start list a real console produces. `/next_heats` advances
with you, `/schedule` moves its current-heat highlight, and the cloud relay carries
all of it to spectators' phones.

## What you do not get

**No times, no places, no results.** There is no console, so there is nothing to time
with. The race clock never runs, the Results page stays on its waiting state all meet,
and no podium is ever shown. This is a start-list display, not a timing system.

**Spectators are told, rather than left waiting.** The server publishes which console
a meet is run on, and whether it times anything, to the phones — so the Splouch app
and the web phone view drop their Results tab for the whole meet instead of offering
a screen that can never fill. Scoreboard and Schedule are unaffected. Switching the
console type in Settings takes effect on the phones without anyone reloading
anything. (`docs/api.md` §5.4, `docs/app.md` `A-11`.)

## Setting it up

1. **Settings → Meet Setup** — load your `.lxf` or `.csv` meet file as usual.
2. **Settings → Timing → Console type** — choose **Manual — no timing console**.
   The serial-port picker, the Connection badge and the Serial Monitor disappear:
   there is no wire for any of them to describe.
3. Click **Open the manual console**, or open `/manual` directly on a phone. It is
   also in the Settings sidebar under **Open**.

## Running a meet

**Nothing on this page changes the boards on a tap.** Every control that reaches them
— Previous, Next, Clear and each row's **▸** — takes a press and hold of about a
second and a half. (**◎**, which only scrolls the list, is a plain tap.) The button fills as it goes, the same press-and-hold the Power tab
uses; let go early and nothing happens. Putting the wrong heat up mid-race is the
mistake the page has to be hard to make.

**Between heats:** hold **◂** or **▸** in the header. They walk the meet in running
order and stop at the ends, and they stay put while you scroll the list.

**Jumping somewhere else:** tap any heat to expand it and check the swimmers, then
hold that row's **▸** to put it on the boards. A tap only ever previews.

**Clearing the boards:** hold **✕** to take the meet off them entirely, back to the
blank header a cold boot shows. Useful between sessions — warm-up, or the gap between
morning and afternoon — where leaving the last heat swum on the TV reads as though it
is about to happen again.

**Finding your place:** tap **◎** (or the event and heat numbers in the header) to
scroll the list back to the heat that is on. It is a plain tap, not a hold — it moves
the list and nothing else.

The heat on the boards is outlined in the board's yellow and sits at the top of the
list; the heat after it is outlined in blue and expanded too, so you can see who to
call up next. The highlight only moves once the server confirms the change, so what
the page shows is always what the boards show.

## Using it with a real console

`/manual` works whatever console is selected, and stays in the Settings sidebar for
that reason. It is genuinely useful on a **Daktronics Omnisport 2000**, which times
races but transmits no event or heat number at all — the console supplies the times
and you supply the heat.

With a console selected, the page shows a warning: the console re-announces its own
event and heat, and will overwrite anything you set here, usually within a second.

## Notes

- With no meet file loaded the page says so and the buttons do nothing — there is no
  running order to step through.
- The page is not password-protected, like `/operator`. Anyone on the pool's network
  can change the heat.
- Playing a recorded session from **Settings → Devtools → Test** while the manual
  console is selected will show nothing: a recording is console packets, and this
  console does not decode any. Switch to the console the recording came from.
