# Console recordings

What the Test tab replays (Settings → Test). Each one is a capture or a
reconstruction of a CTS Gen6 console's serial output, with a companion `.lxf`
beside it holding the start lists its event and heat numbers refer to — that
pairing is the whole reason a test session loads one.

## Which console decodes them

A recording is a capture of a wire, so the player feeds its bytes to whatever
decoder the *configured* console has — a replay is the one path where the console
setting and the file have to agree. Two consequences:

- **A console with no wire cannot read one.** The manual console's decoder answers
  nothing by design, so a replay under it once produced the test badge and eight
  empty lanes for the whole recording. A test session now borrows `cts_gen6` for the
  duration whenever the configured decoder reports `requires_serial = False`, puts
  the console's own decoder back at the end, and the Test tab says which one is
  driving the board (`worker.use_replay_decoder`).
- **A console with a wire keeps its own.** These files are CTS Gen6, so replaying
  one under a Quantum decodes to nothing — but substituting there would be the wrong
  call, because a Quantum operator's own capture is the thing they are trying to
  play. Record your own console's output (Test → Record) rather than replaying these.

## The two formats

| | Contents | Timestamps | Playback |
| --- | --- | --- | --- |
| `.cts` | hex text, one packet per line, each prefixed `[unix_time]` | **yes** | real time, **once** |
| `.raw` | hex text, no line structure and no timestamps | no | ~720 bytes/s, **looped** |

A `.cts` carries the console's own timing and plays through once, which is what
makes it usable as a fixture. A `.raw` carries none, so `worker._play_cts_file`
paces it artificially and loops it until stopped.

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
| `50m_sprint.cts` | 1 · 50m Freestyle | 1 | 8 | — | 8s | authored |
| `50m_sprint_2heats.cts` | 1 · 50m Freestyle | 2 | 8 | — | 8s | authored |
| `100m_freestyle.cts` | 2 · 100m Freestyle | 1 | 6 | 50m | **11s** | authored |
| `200m_medley_2heats.cts` | 3 · 200m Medley | 2 | 8 | 50m, 100m, 150m | 8s | authored |
| `real_console6.raw` | 1 · 50m Freestyle | 1 | 8 | — | — | captured, no finish |

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
