# Console recordings

What the Test tab replays (Settings → Test). Each one is a capture or a
reconstruction of a CTS Gen6 console's serial output, with a companion `.lxf`
beside it holding the start lists its event and heat numbers refer to — that
pairing is the whole reason a test session loads one.

## The three formats

| | Contents | Timestamps | Playback |
| --- | --- | --- | --- |
| `.cts` | hex text, one packet per line, each prefixed `[unix_time]` | **yes** | real time, **once** |
| `.raw` | hex text, no line structure and no timestamps | no | ~720 bytes/s, **looped** |
| `.cap` | the same bytes as `.raw`, **binary** rather than hex text | no | ~720 bytes/s, **looped** |

`.cap` and `.raw` of the same name are the same capture in two encodings —
byte-for-byte identical once the hex is decoded. `.cap` is what came off the wire;
`.raw` is its hex transcription, which is what makes it greppable and diffable.
Neither carries timing, so `worker._play_cts_file` and `_play_cap_file` pace them
artificially and loop them until stopped. A `.cts` carries the console's own
timing and plays through once, which is what makes it useful as a fixture.

## What is in them

| File | Event | Heats | Lanes | Splits | Source |
| --- | --- | --- | --- | --- | --- |
| `50m_sprint.cts` | 1 · 50m Freestyle | 1 | 8 | — | authored |
| `50m_sprint_2heats.cts` | 1 · 50m Freestyle | 2 | 8 | — | authored |
| `100m_freestyle.cts` | 2 · 100m Freestyle | 1 | 6 | 50m | authored |
| `200m_medley_2heats.cts` | 3 · 200m Medley | 2 | 8 | 50m, 100m, 150m | authored |
| `real_console.cts` | — | — | — | — | captured, idle console |
| `real_console5.raw` / `.cap` | 1 · 400m Freestyle | 1 | 8 | — | captured |
| `real_console6.raw` / `.cap` | 1 · Event 1 | 1 | 8 | — | captured |

`tests/test_console_recordings.py` checks the authored ones as data: that each
matches its companion meet file, that places agree with the times, and that the
splits are what they claim to be.

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
