# Console recordings

What the Test tab replays (Settings → Test). Each one is a capture or a
reconstruction of a CTS Gen6 console's serial output, with a companion `.lxf`
beside it holding the start lists its event and heat numbers refer to — that
pairing is the whole reason a test session loads one.

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

| File | Event | Heats | Lanes | Splits | Source |
| --- | --- | --- | --- | --- | --- |
| `50m_sprint.cts` | 1 · 50m Freestyle | 1 | 8 | — | authored |
| `50m_sprint_2heats.cts` | 1 · 50m Freestyle | 2 | 8 | — | authored |
| `100m_freestyle.cts` | 2 · 100m Freestyle | 1 | 6 | 50m | authored |
| `200m_medley_2heats.cts` | 3 · 200m Medley | 2 | 8 | 50m, 100m, 150m | authored |
| `real_console.cts` | — | — | — | — | captured, idle console |
| `real_console5.raw` | 1 · 400m Freestyle | 1 | 8 | — | captured |
| `real_console6.raw` | 1 · 50m Freestyle | 1 | 8 | — | captured, no finish |

`tests/test_console_recordings.py` checks the authored ones as data: that each
matches its companion meet file, that places agree with the times, and that the
splits are what they claim to be.

The captured ones are not checked that way — they are a console's own output, so
there is nothing to hold them to beyond what they are. Two things to know about
them: `real_console.cts` is an idle console and carries no race at all, and
`real_console6.raw` is seventeen seconds of starts and resets that never reaches a
finish, so it shows a running clock and no times. Its companion `.lxf` named the
event `Event 1` — which is the string `lenex_parser` falls back to when an event
has neither a name nor a `SWIMSTYLE`, written into the file and then read back as
though it were a title. Its own seed times are 24.87–25.89, so it is a 50m.

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
