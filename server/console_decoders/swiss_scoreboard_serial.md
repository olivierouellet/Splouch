# Swiss Timing Scoreboard Lines (UNT4 / GALACTICA) — Serial Protocol Reference

> **Status: Documented** from Swiss Timing's *Transmission protocols — ARES 21
> Swimming* (doc 0100.064 v1.10, §1). Not yet run against a live console.
> Implemented in `swiss_scoreboard.py` (`SwissScoreboardDecoder`, decoder key
> `swiss_scb`).

Console entries: **ARES 21 — SCB — UNT4 / Calypso / Piccolo / GALACTICA**
(`ares21_unt4`) and **OmniSport 2000 — J2/J3 Scoreboard outputs set to OMEGA**
(`dak_os2000_omega`).

---

## Hardware Interface

| Parameter | ARES 21 | OmniSport 2000 |
| --------- | ------- | -------------- |
| Connector | IF-ARES SCB, Sub-D 9 F: pin 4 LD+, 3 LD−, 7 GND (RS-485); 9/8 = 20 mA | J2 or J3, 1/4" jack: tip CL+, ring CL−, sleeve GND (20 mA current loop), *Scoreboard Outputs → OMEGA* |
| Baud / format | not documented — options, 9600 7-E-1 by default | not documented — options, 9600 7-E-1 by default |

---

## Messages

Every message is `SOH <text> EOT`. `<LF>*n` selects the board line (no LF = line 1).

| Message | Text | Decoder |
| --- | --- | --- |
| Running time | `DC4 R02 STX BS '1 ' STX HH:MM:SS.D` (HH:MM:S may be blank) | `running_time`; leaving zero starts the race; back to zero resets the lanes |
| New race | `DC4 S02 STX BS '1 ' STX '       0,0'` | lanes reset, race started (as is any zeroed line before the race); a clock still at zero resets it until the gun |
| Net time | `DC4 S02 STX BS <LF>*n A ' ' STX BB ' ' TIME` | lane time + place, lane stopped, `lane_splits` += split step |
| Clear line | `DC4 S02 STX BS <LF>*n A ' ' STX <blank>` | lane time cleared; while the race runs, the lane runs again (the time was a split) |
| GALACTICA reaction time | `DC1 97 STX RT\|LL\|+/-s.dcm` | ignored |
| Event / heat | `ACK BS '8'` + 11 × LF; if char 14 is not LF: event 20–22, heat 23–25 | `event_changed` — undocumented, unconfirmed |

**A and BB.** *Lane mode*: A = lane (1 char, `0` = 10), BB = rank. *Rank mode*: A =
rank, BB = lane. GALACTICA is rank mode, the default. Nothing on the wire tells them apart. In a pool whose
first lane is 0 (Settings → Meet → Pool), a one-character `0` is lane 0 instead; BB,
two characters, is the lane's full number either way. A pool numbered 0–10 cannot be
told apart in one character.

**Splits.** The ARES erases an intermediate time after a configurable delay (manual
§5); the decoder takes an erase during the race as "still swimming". A time that stays
is a finish. A console set not to erase splits would make the first split look like a
finish.

**Not carried:** lap count, and event/heat apart from the message above — names and
seed times will not load from the meet file unless that message arrives.

---

## Options

| Option | Values | Default |
| --- | --- | --- |
| `scb_line_mode` | `lane`, `rank` | `rank` |
| `serial_baud` | `9600`, `19200` | `9600` |
| `serial_format` | `8N1`, `7E1` | `7E1` |

---

## Sources

- Swiss Timing, doc 0100.064 v1.10 (Nov 2009), *ARES 21 Swimming transmission protocols*, §1 SCB output (ARES 21 CD `APP3330.600`)
- ARES Swimming 2.24e scoreboard kinds (`UNT/Calypso/Piccolo Rk`, `… La`, `Galactica…`)
