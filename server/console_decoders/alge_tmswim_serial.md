# ALGE TM-SWIM — Video (TV-Online) Output Reference

> **Status: Documented** from ALGE's *Swimming Timing System* manual (version
> E24-08-29, §5.1.1.2 *Video*). Not yet run against a live console.
> Implemented in `alge_tmswim.py` (`TMSwimDecoder`, decoder key `alge_tmswim`,
> console entry `alge_tmswim_video`).

---

## Hardware Interface

| Parameter | Value |
| --------- | ----- |
| Port | TM-SWIM **Video / TV-Online**, galvanically isolated |
| Connector | 8-pin DIN: pin 1 TX, pin 2 GND (3 RX not used, 4 CTS, 5 RTS) |
| Interface | RS-232, transmit only |
| Baud rate | **19 200** (4 800 – 38 400 set in SwimTime: *Advanced settings → Video settings*) |
| Format | 8-N-1 |
| Data | ASCII, one line per message, CR-terminated, at most 64 characters |

The TM-SWIM's other ports: display board (2 400, 8-N-1, ALGE GAZ protocol), two
printers (2 400) and the PC link over USB (internal protocol, not published).

---

## Messages

Columns are 1-based.

| Kind | Layout | Source |
| --- | --- | --- |
| Running time | `R eee hhh ␠ HH:MM:SS.zhtc` — event (2–4), heat (5–7), time from 9 | TM-SWIM |
| Start | `START` … (firmware 14xx and later) | TM-SWIM |
| Intermediate | `I LL TTT RR HH:MM:SS.zhtc` — lane (2–3), touch count (4–6), rank (7–8), time from 9 | TM-SWIM |
| Finish | `F LL TTT RR HH:MM:SS.zhtc` | TM-SWIM |
| Deleted time | `C LL TTT RR HH:MM:SS.zhtc` | TM-SWIM |
| Reaction / relay take-off | `M LL TTT ␠␠ HH:MM:SS.zhtc` | SwimTime |
| Event identification | `ID EV / HT / discipline / class` | SwimTime |
| Swimmer identification | `B : name / nation / club` (B = lane 0–12) | SwimTime |

`z h t c` are tenths to ten-thousandths. The clock line is sent every tenth of a
second. Lanes run 00–12. **RR**, the current rank, "is not sent on version higher
than 14xx".

---

## Decoder Behaviour

| Message | Emitted |
| --- | --- |
| Running time | `current_event`, `current_heat` (a new pair: `event_changed`, lanes reset); `running_time` to tenths. A clock at zero is a reset; a clock moving while no race runs is the start |
| `START` | the start: lanes cleared if they hold times (a re-swim, seeds kept), `lane_running=True` for the start list's lanes (every lane when none), `dismiss_overlay` |
| Event identification | event and heat, as the clock line |
| Intermediate | `lane_time` (hundredths, truncated), `lane_splits` = touches × split step, `lane_place` if RR is sent; the lane keeps running |
| Finish | `lane_time`, `lane_splits`, `lane_running=False`, `lane_place` from RR — or, with no RR, every finished lane ranked by time (a tie shares the place) |
| Deleted time | lane time and place cleared; while the race runs, the lane runs again |
| Reaction, swimmer lines | ignored — the meet file supplies names |

The split step follows Settings → Meet → Touchpads.

## Options

| Option | Values | Default |
| --- | --- | --- |
| `serial_baud` | `19200`, `9600`, `4800`, `38400` | `19200` |

## Known Uncertainties (needs hardware validation)

- Whether RR is blank or absent on firmware later than 14xx (both are read).
- The exact text after `START`.
- Whether the touch count includes the turn-end pads on a double-sided system
  (it is multiplied by the split step either way).
- The exact layout of the SwimTime lines (not decoded except event identification).

---

## Sources

- ALGE-TIMING, *Swimming Timing System — Manual*, version E24-08-29, §5.1.1 *Interface Description*
