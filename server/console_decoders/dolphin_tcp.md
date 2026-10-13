# CTS Dolphin — TCP/IP Streaming API Reference

> **Status: Documented** from the Dolphin user instructions (F912 Rev 0519, "TCP/IP
> Streaming") and the Dolphin software 5.0.19. Not yet run against a live system.
> Implemented in `dolphin.py` (`DolphinDecoder`, decoder key `cts_dolphin`, console
> entry `cts_dolphin`). Transport: **TCP** (`ConsoleDecoder.transport = "tcp"`).

---

## Connection

| Parameter | Value |
| --------- | ----- |
| Server | the PC running the Dolphin software (*Settings → TCP/IP Streaming* on) |
| Port | 13382 (default), 13441, 14559, 14917 or 15828 — chosen in Dolphin |
| Encoding | ASCII, one message per line, CR LF; fields separated by commas |
| Clients | several at once |

Splouch is a client: `console_host` + the `tcp_port` option.

---

## Commands (client → Dolphin)

From F912:

| Command | Effect |
| --- | --- |
| `autoSendUpdates,ON` / `OFF` | Push race and event updates, and the current event and heat |
| `includeSplitData,ON` / `OFF` | Include split times in race data |
| `getCurrentEventAndHeat` | Reply with the current event and heat |
| `getEventInfo` | Reply with every event |
| `getRaceTimes,<eventIndex>,<heat>,<showSplits>` | Reply with one race |
| `getAvailableRaces,<showSplits>` | Reply with every race |
| `setEventInfo,…` / `setCurrentEventAndHeat,…` / `clearEvents` / `reset` | Control Dolphin (not used) |

On connect the decoder sends `autoSendUpdates,ON`, `includeSplitData,ON` and
`getCurrentEventAndHeat` (`post_open_bytes`).

---

## Messages (Dolphin → client)

| Message | When | Decoder |
| --- | --- | --- |
| `CurrentEventAndHeat,<version>,<eventIndex>,<eventNumber>,<heat>,<raceNumber>` | heat change (auto) or on request | `event_changed` with the event *number* (leading digits) and heat |
| `EventChanged,<index>,<number>,<name>,<heats>,<splits>,<round>` | event edited | ignored |
| `Event <index>  Heat <heat>  Race <race>` | start of a race block | starts ranking a new race |
| `<lane>,<final>,<dq>,<empty>[,1,A,B,C,split,…,10,A,B,C,split]` | one line per lane of the race | `lane_time`, `lane_place` (ranked), `lane_splits` (splits with a time) |
| `All Events Cleared`, `… set to ON`, `Event set`, `Error - …` | replies | ignored |

- `<dq>` and `<empty>` are `True` / `False`. A DQ lane shows `DQ` and no place; an
  empty lane shows nothing.
- `<lane>` is the lane's position counted from 1, even when Dolphin is set to *Start
  With Lane Zero* — that setting only relabels Dolphin's own screen and files.
- Times are `[H:][MM:]SS.hh`, blank for no time; `A`, `B`, `C` are the three watches.
- A race block is sent **once, after the race is reset** on the watches or starter.
  There is no start signal and no running clock.

---

## Decoder Behaviour

- Places are ranked from the final times; equal times share a place.
- The first lane line marks the race finished (`race_finished`); the app's finish
  delay collects the rest of the block.
- **Results hold.** Dolphin can advance to the next heat right after saving a race.
  A heat change that arrives within `dolphin_results_hold` seconds of a race is kept
  pending and applied by `tick()` when the hold ends, so the results stay on the
  board.

## Options

| Option | Values | Default |
| --- | --- | --- |
| `tcp_port` | 13382, 13441, 14559, 14917, 15828 | 13382 |
| `dolphin_results_hold` | 0, 5, 10, 20, 30 s | 10 |

---

## Sources

- Colorado Time Systems, *Dolphin Wireless Stopwatch Timing System* extended instructions F912 (Rev 0519), "TCP/IP Streaming", "Logging", "Scoreboard"
- Dolphin software 5.0.19
