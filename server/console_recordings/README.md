# Console recordings

What the Test tab replays (Settings → Test). Each one is a capture or a
reconstruction of a console's serial output — CTS Gen6 unless a `.console` file
beside it says otherwise — with a companion `.lxf` beside it holding the start
lists its event and heat numbers refer to. That pairing is the whole reason a test
session loads one.

## Which console decodes them

A recording is a capture of a wire, and only the decoder for the console that made
it can read it — a CTS decoder finds no packet in an Omnisport capture at all. So a
test session replays each recording under **the console it was made on**, borrowing
that decoder for the duration and putting the configured one back at the end
(`worker.use_replay_decoder`). The Test tab names the console beside each built-in
and, while one plays under a stand-in, says which console is driving the board.

How the console is known (`worker.recording_console`):

- **A `<name>.console` file** beside the recording holds its console key, e.g.
  `dak_2000`. Only `omnisport_2000.raw` has one.
- **A built-in without one** is CTS Gen6 — every recording shipped before the
  Omnisport one.
- **An uploaded recording** is the operator's own console's output, so it plays under
  the configured decoder. One exception: a console with no wire (the manual console,
  or a portless plugin, `requires_serial = False`) reads nothing by design, so there
  it falls back to `cts_gen6` rather than the test badge over eight empty lanes.

Decoders are compared by class, not key: a System 5 or Gen7 Legacy pool shares the
Gen6 decoder and replays the CTS files without a stand-in.

## The two formats

| | Contents | Timestamps | Playback |
| --- | --- | --- | --- |
| `.serial` | hex text, one packet per line, each prefixed `[unix_time]` | **yes** | real time, **once** |
| `.raw` | hex text, no line structure and no timestamps | no | ~720 bytes/s, **looped** |

A `.serial` carries the console's own timing and plays through once, which is what
makes it usable as a fixture. A `.raw` carries none, so `worker._play_recording`
paces it artificially and loops it until stopped.

Test → Record writes either one: `.serial` from the packets the decoder splits,
`.raw` from the bytes ahead of it (`worker._record_raw_byte`). How to make one, with
Splouch or with PuTTY, is in [docs/admin.md](../../docs/admin.md#recording-a-session).

### What happened to `.cap`

A third format used to be listed: the same bytes as a `.raw`, binary rather than
hex. Every capture was kept as both, so the Test tab showed each one **twice** —
two rows that played identically — and an operator had no way to tell which was
which. The binary copies bought nothing the hex ones did not have, so they are
gone, along with the separate player they needed.

A capture tool still writes binary, so converting one is the first step before it
can be used here:

```bash
python3 server/console_recordings/cap-to-raw.py session.cap   # -> session.raw
```

It writes the same hex these files use, sixteen bytes to a line. Going the other
way, if it is ever needed, is `xxd -r -p session.raw > session.cap`.

## What is in them

| File | Event | Heats | Lanes | Splits | Start list | Source |
| --- | --- | --- | --- | --- | --- | --- |
| `50m_sprint.serial` | 1 · 50m Freestyle | 1 | 8 | — | 8s | authored |
| `50m_sprint_2heats.serial` | 1 · 50m Freestyle | 2 | 8 | — | 8s | authored |
| `100m_freestyle.serial` | 2 · 100m Freestyle | 1 | 6 | 50m | **11s** | authored |
| `200m_medley_2heats.serial` | 3 · 200m Medley | 2 | 8 | 50m, 100m, 150m | 8s | authored |
| `real_console6.raw` | 1 · 50m Freestyle | 1 | 8 | — | — | captured, no finish |
| `omnisport_2000.raw` | 5 · 100m Butterfly | 1 | 6 (2–7) | — | — | captured (Omnisport 2000), no finish |

**Course.** All four authored races are long course, and the splits are what say
so: the 100m touches once and the 200m three times, so each length is 50m. The
companion `.lxf` declares it — `course="LCM"` on the `MEET`, and a `SWIMSTYLE`
distance on the event — because the server divides that distance by the operator's
own `pool_length` to publish `expected_splits`, which is what a lap count on the
board is measured against (`docs/app.md` `L-23`).

That division uses the **setting**, not the file: Settings → Meet warns when the two
disagree but still uses yours, so replaying one of these at the stock 25m gives a
200m eight expected lengths against the three splits it actually carries, and the
last-length pulse never fires. Set the pool to 50m to see these replay as they swam.
`tests/test_console_recordings.py` holds the distance, the course and the split
count to each other, so an edit to one of the three fails until the others follow.

**Start list** is the gap between the event announcement — which is what puts names
on the board — and the first lane going active. It only works because the player
flushes each packet at *its own* timestamp: a packet is otherwise dispatched by the
arrival of the next one's first byte, which on a live wire is milliseconds and in a
recording is the whole gap. Every file here opens with its announcement and then
says nothing until the race, so the names used to arrive with the first dive. It is how long an operator has to
read a heat before it starts, so it is pinned per file rather than left to whatever
a regeneration would default to.

`tests/test_console_recordings.py` checks the authored ones as data: that each
matches its companion meet file, that places agree with the times, and that the
splits are what they claim to be.

The captured one is not checked that way — it is a console's own output, so there is
nothing to hold it to beyond what it is. `real_console6.raw` is seventeen seconds of
starts and resets that never reaches a finish, so it shows a running clock and no
times; it is kept for the wire format rather than the race. Its companion `.lxf`
named the event `Event 1` — the string `lenex_parser` falls back to when an event has
neither a name nor a `SWIMSTYLE`, written into the file and then read back as though
it were a title. Its own seed times are 24.87–25.89, so it is a 50m.

### `omnisport_2000.raw`

The only non-CTS recording: three seconds of a Daktronics Omnisport 2000's RTD port
(J5), reconstructed byte for byte from the raw dump published by XY Kao
(<https://xy-kao.com/projects/decoding-daktronics-omnisport-2000/>). The dump shows
control characters as dots, but every one of its 89 packets carries a checksum, and
all 89 come out right — so the bytes here are the console's, not a guess. The clock
runs 0.1 → 3.0, the result lines carry lanes 1–10 with swimmers in 2–7, and event 5
heat 1 (a 100 fly, 4 lengths) arrives at 1.2 s. No touch is made.

One change from the wire: the six swimmers were real children, so their names and
club codes are swapped for the fictional ones the other recordings use, at the same
field widths, with those six packets' checksums recomputed. The companion `.lxf`
holds the same six. Field map and framing: `console_decoders/omnisport_2000_serial.md`.

It also shows the empty-lane rule: the start is one signal for the whole pool, and
only lanes 2–7 — the ones with a swimmer in the start lists — go running
(`ConsoleDecoder.set_heat_lanes`).

## How a split is written

The lane's running bit drops, the packet carries the split time, the console
repaints it every half second for **three seconds**, then the bit comes back and
the lane rejoins the race clock. Two details are load-bearing:

- **The split time matches the race clock** at the moment of the touch, to within
  the clock channel's own tenth. They arrive on different channels, so nothing but
  care keeps them in step.
- **A split carries no place.** A place is awarded at the finish. The board reads
  "a time with no place" as *still being placed*, which is what stops eight lanes
  resting at the same wall from satisfying `heat_is_done()` and tinting a podium
  in the middle of a race.

The hold has to outlast `split_min_duration` (Settings → Timing, 1s by default) or
the decoder never counts the length behind it — three seconds leaves room, and is
long enough to read across a hall.
