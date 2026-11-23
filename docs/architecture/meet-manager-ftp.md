# Meet Manager results by FTP

> **Not built.** It waits on one capture of what Splash Meet Manager (MM) actually writes; see
> [Decision gate](#decision-gate-when-is-the-lenex-file-written).

## Context

Official results reach the Pi today by hand. The operator exports the meet from MM as a `.lxf` and uploads it
on `/mm` ([meet-manager-results.md](meet-manager-results.md)). MM's **Live Results** can instead push files to
an FTP server in the background, as results are entered. If the server Pi received that push, official times
would arrive with nobody at the upload page.

[meet-manager-results.md](meet-manager-results.md) set FTP aside earlier ("one event at a time"). What MM can
send has since turned out to include Lenex, which reopens the question.

## Source: what MM's Live Results sends

From the swimrankings wiki (Meet Manager 11 FAQ §7, the SplashMe page, the release notes) and the MM dialog:

- **Report PDFs** (start lists, result lists, …) plus a **live index** page with a custom CSS/header. Their
  layout follows each operator's report settings, so they are not a data source.
- **Uploaded** to any FTP server, FTPS (TLS 1.2 since 11.77960) or SFTP (since 11.81129, December 2024).
  "No automatic upload" writes the same files to a local folder instead.
- **Regenerated** when heat results change or an event is seeded.
- **A Lenex results file.** MM's *Configuration résultats en direct* → **Résultats** tab has
  *Fichiers résultats — Créer fichiers résultats dans format suivant (seul. si toutes les épreuves sont
  « officielles »)* with a **Fichier Lenex Splash** box (beside SDIF and DSV). Release 11.55322 (August 2018)
  names the file inside the archive `results.lef`.
- MM also runs a "live info server" over HTTP (`/records/NNNN`, entries). It is not publicly documented and
  stays out of scope, as before.

## Decision gate: when is the Lenex file written?

"Only if all events are official" reads two ways:

- **Per event**: an event's results are written once all its heats are official. → Build it.
- **Whole meet**: nothing is written until every event of the meet is official, which is after the meet.
  → Don't build; `/mm` stays the path.

**Capture**: in MM, tick *Fichier Lenex Splash*, choose "No automatic upload", make one event official, and
look in the local folder. Record:

1. whether a Lenex file appears, and after which action;
2. its name, and whether it is a zipped `.lxf` or a plain `.lef`;
3. what it holds: the whole meet (every event, start lists included) or only the official events'
   results;
4. its `MEET` name and session dates, which must give the same `meet_uid` as the start-list export.

Keep the folder as a test fixture.

## Design (if the gate passes)

### 1. FTP receiver on the server Pi

- `pyftpdlib`, in the server process: pure Python, MIT. Add it to `pyproject.toml` and to the licence table
  in `docs/development.md`.
- **Off by default.** Turned on in Settings, with one user whose password is set there too. No anonymous
  login.
- Chrooted to `~/SplouchData/ftp/`, write-only for that user, with a size cap per file.
- Port 21 and a small passive range. The `ufw` rules in `install.sh` already admit everything on the LAN
  interfaces, so no firewall change is needed. Plain FTP on the venue LAN only, never through the cloud
  relay. SFTP would need an SSH subsystem and is not worth it on a closed network.

### 2. Picking up the Lenex file

- Act on pyftpdlib's "file received" callback, never on a half-written file. PDFs, the index and anything
  else are ignored, and are deleted so the SD card does not fill over a meet day.
- The Lenex file goes through the same checks as `/meet_update_file` (`server/routes/settings.py`): a meet
  must be loaded and no test session running, and the file's `meet_uid` must match `state._active_meet_uid`.
  A different meet is refused and logged; it never replaces the loaded one.
- **What it replaces depends on capture item 3:**
  - *Whole meet*: reuse `_meet_update_file`'s path as is: overwrite the meet file, then `_reload_same_meet`.
  - *Results only*: overwriting would lose the start lists of events not yet swum. Merge instead: parse
    it, take only `LenexData.results`, merge them into the loaded meet's results, then emit the schedule
    as `_reload_same_meet` does. The meet file on disk is left alone; the merged results persist in a
    side file next to it, like `<meet_uid>.console.jsonl`, so they survive a restart.
- Either way the running heat, timers and console times are untouched. That is what `_reload_same_meet`
  already guarantees.

### 3. Showing it

- `/mm` shows "Last received by FTP at …" next to the last manual upload, and the same "heats official"
  summary.
- `docs/admin.md`: the MM Live Results settings (custom FTP server, the Pi's address, user, password,
  *Fichier Lenex Splash*), in English and French.
- No change to `docs/api.md` or `docs/app.md`: the wire fields of `S-22` stay as they are.

## Tests

- Upload with `ftplib` to a receiver on a free port. The captured file updates `/schedule.json`'s
  `result_time` and `official`.
- Another meet's file is refused, and the loaded meet is unchanged.
- An interrupted upload does nothing.
- A PDF upload is ignored and removed.
- Results-only case: start lists of unswum events survive, and a restart keeps the merged results.
- The receiver does not start while the setting is off.

## Open points

- The capture above.
- Whether MM retries a failed upload, or whether a Pi restart in the middle of a meet loses an event's file
  until the next "Recreate all documents".
