# Validated results from Meet Manager

> **Built** on the Pi, the cloud and the web Schedule. The iOS and Android apps follow
> through their own parity ledgers (`S-22`, `S-23`).

## Context

A lane has three times, and the Schedule tab should show the best one it has:

1. **Seed** (Lenex `entrytime`): the swimmer's best time on record. This is what the Schedule shows today.
2. **Console**: what the timing console read at the finish. It is provisional, and today it lives only in memory for the last heat.
3. **Official**: validated in Splash Meet Manager (MM). It may differ from the console (officials used the manual chronos) or be a DSQ the console never knew about.

The operator exports the meet from MM as a Lenex .lxf file and uploads it on a short local page. The Schedule then shows the official result in place of the console time, on web (Pi and cloud), iOS and Android.

## Source: a Lenex .lxf export from MM

Options considered with the user:

- MM's HTTP/JSON server: not designed for third-party queries.
- Secondary FTP live results: one event at a time.
- External database server: too hard for operators who aren't technical.
- **Lenex .lxf: chosen.** Public spec, native MM export, and Splouch already parses its start lists.

One .lxf holds both the current start list and the results entered so far. A single upload refreshes reseeded heats, scratches and results. There is no automatic folder pick-up: the upload page is enough.

## 1. Parser (`server/meet_parsers/lenex_parser.py`)

- Read the `HEAT status` attribute.
- Collect `RESULT` elements in all three layouts the parser already handles:
  - inside HEAT/EVENT entries
  - under `ATHLETE > RESULTS`
  - under `CLUB > RELAY > RESULTS`
- Resolve each result's heat and lane with the existing `_resolve_heat` / `heatid_map`.
- Which results to keep, by heat status:
  - `OFFICIAL` and `SEEDED`: **kept**.
  - `INOFFICIAL`: **dropped**. The console time stands in, and by upload time these heats should all be official.
  - **No status attribute**: **dropped**.
- New `LenexData.results`: `{ev: {heat: {lane: {"time": str, "status": str}}}}`. `status` is a Lenex result status (`DSQ`, `DNS`, `DNF`, `WDR`, `EXH`, `SICK`) or empty.
- **Time format rule (all three times):** the data and wire form is always `HH:MM:SS.hh`. Console times are normalised to it too. Only the display drops a leading `00:` (shown as `MM:SS.hh`, or whatever form each client already uses for seeds). A status with a missing or `NT` time leaves the time empty.
- The existing safety caps are unchanged.

## 2. Remembering console times

### On the Pi

- `state.console_times`: an in-memory dict `{(ev, ht): {lane: time}}`, filled in `worker._on_race_state_changed` at the moment `_last_results_snapshot` is built. One write per finished heat.
- **SD-card friendly:**
  - Append one JSON line per finished heat (about 200 bytes: event, heat, lane times) to `~/SplouchData/meet/<meet_uid>.console.jsonl`.
  - Open in append mode, write, close, **no fsync**.
  - That is a few hundred tiny appends per meet day, which is negligible next to the OS's own logging.
  - Nothing is rewritten in place, and there is no database.
  - A re-run heat simply appends again; the last line wins.
- On boot or meet load, replay the file if its `meet_uid` matches. Loading a different meet starts a fresh file. Clearing the meet deletes the file.
- The cloud gets nothing new: it already receives `results_snapshot` per heat.

### In the cloud (`cloud/cloud_server.py` `_forward`)

- On `results_snapshot`, merge the heat's lane times into `meet["console_times"]`.
- Add `console_times` to `_RECORD_FIELDS` (`cloud/cloud_store.py:29`), so it survives a restart through the existing `cloud_meetstore.update`.

### Live updates

The Schedule page already listens for `results_snapshot` (for the highlight). It now also patches that heat's console times in place, with no re-fetch. On join it gets the full state from `GET /schedule`.

**To verify:** the snapshot's `channel` → lane mapping, in case lanes are numbered from 0.

## 3. Wire fields (additive, `docs/api.md` §5.8)

| Where | New field | Meaning |
| --- | --- | --- |
| lane | `console_time` | console time, `""` if none |
| lane | `result_time` | official time, `""` if none |
| lane | `result_status` | `DSQ`, `DNS` … or `""` |
| lane | `result_delta_seconds`, `result_delta_better` | official − seed, from `meet_data.delta_fields`; the same structured pair `results_snapshot` already uses. `null` when there is no seed, no time, or a status |
| heat | `official` | `true` when every lane with a swimmer has a `result_time` or `result_status` |

They are filled in:

- `meet_data.build_heats` (`server/meet_data.py:303`)
- `relay._serialise_start_list` (`server/relay.py:362`): official results only; the cloud adds its own console times.
- `cloud_server._build_heats_json` (`cloud/cloud_server.py:341`)

Older app builds ignore unknown keys.

**Does the cloud get all three times? Yes.**

- **Seed and official**: in `schedule_snapshot`, sent on every meet load and every `/mm` upload.
- **Console**: from the `results_snapshot` the Pi already relays after each heat, stored and persisted per meet (§2).
- So `GET /meet/{id}/schedule` returns all three per lane, and live `results_snapshot` frames patch console times between fetches.
- Each app picks the colour from which field is filled: official/status > console > seed.

## 4. UI on all three surfaces: new `S-22` and `S-23`

### `S-22`: the time column shows the best time available

| Lane state | Shows | Theme key | Dark | Light |
| --- | --- | --- | --- | --- |
| Nothing swum yet | seed time | `schedule_seed` | white | black |
| Console time, not yet official | console time | `schedule_console` | yellow | blue |
| Official time | official time | `schedule_official` | bright green | green |
| Official non-finish | localised `DSQ` / `DNS` / `DNF` … | `schedule_official` | bright green | green |

- Hex values (`docs/api.md` §6.1): dark `#e0e0e0` / `#FFD700` / `#4ade80`, light `#111111` / `#0055aa` / `#2e7d32`. Every one is at least 4.7:1 on both row backgrounds.
- Colour is never the only signal:
  - an official time is drawn in a heavier weight;
  - the screen reader says "seed time…", "console time…", "official time…" or "disqualified".

#### Theme keys and colour pickers

- **Three new palette keys:**
  - added to `DEFAULT_THEME_COLORS` (dark) and `LIGHT` in `shared/py/splouch_i18n.py`;
  - added to `server/themes/*.toml` and `shared/templates/_palette.html`;
  - added to `docs/api.md` §6.1, the table both apps copy key for key.
- **Colour pickers for the Pi's `/schedule`:**
  - Add three pickers, each with a reset button, to the Schedule group of the existing colour grid in Settings → Appearance → Theme (`server/templates/settings/theme.html`).
  - Only the Schedule pages read these keys. The Qt board, `/live` and `/live-mobile` are unchanged.
- **Hardcoded on iOS, Android and the cloud's `/mobile/schedule`:**
  - These use the fixed §6.1 defaults from the table above (`P-15`: spectator palettes, never the operator's).

### `S-23`: tap to compare with the seed

Only on heats where `official` is true.

- A tap on the heat's time column switches **all lanes of that heat** to the difference with the seed (e.g. `−1.23`).
  - Colours are the scoreboard's `delta_better` (green, faster) and `delta_worse` (grey, slower).
  - The data is `result_delta_seconds` / `result_delta_better`, formatted the way each client formats scoreboard deltas.
- **DSQ / non-finish lanes**: show the console time, in the console colour (yellow dark / blue light). If there is no console time, the status stays.
- **Lanes without a seed**: show `NT`, as before.
- It springs back to the times after **4 s**, or on a second tap.
- **Transition: idiomatic per platform**. The contract specifies only "animated swap, respects reduce-motion".
  - Web: a CSS crossfade.
  - iOS and Android choose their native transition (e.g. SwiftUI `contentTransition`, Compose `AnimatedContent`).
- A small hint on official heats shows that the time column can be tapped.
- Screen reader: the whole heat gets an action, "Show difference with seed".

### Per surface

- **Web**: `shared/templates/schedule.html`, `renderSchedule` (~l.600) plus CSS tokens. This covers the Pi `/schedule` and the cloud `/mobile/schedule`.
- **iOS** (`../Splouch-ios`):
  - `Payloads.swift` `ScheduleLane` / `ScheduleHeat`: new fields, defaulting to empty.
  - `ScheduleTab.swift`:
    - the time cell picks its state;
    - the widest-time template includes all time states;
    - the swap transition is chosen natively.
- **Android** (`../Splouch-android`):
  - `Payloads.kt`: the same new fields.
  - `ScheduleTab.kt`: `TimingCell` (l.303) and the screen-reader text (l.372), with the swap transition chosen natively.
- **i18n**: status words and the spoken prefixes in `shared/locales/{en,fr,es}.toml` under `mobile`, served by `/i18n`.

## 5. MM operator page: `/mm`

`/meet` is taken by the meet preview, and `/mm` is short, works in both languages and is free. URLs are English elsewhere (`/schedule`, `/settings`), so there is no French alias.

- Login required. Pi only. Linked from the Settings quick links next to `/operator`.
- One large **Upload updated meet file (.lxf)** button with a drop zone. It posts to the existing `/meet_update_file` (`server/routes/settings.py:104`), which refuses a different meet.
- Shows the meet name, the time of the last upload and progress such as "18 / 42 heats official".
- After each upload, a summary from diffing the old and new `state.meet.results`:
  - heats that became official;
  - DSQs added;
  - lanes where the official time differs from the console time.
- A short "How to export from Meet Manager" help box (exact menu path filled in once verified).
- New files: `server/templates/mm.html`, a route in `server/routes/meet.py`, and strings in the locale files.

### Same-meet reload must not disturb a running heat

`/meet_update_file` reloads through `_load_meet_file`, which resets the live results and finish timers. Split it in two:

- **New meet**: the full reset, as today.
- **Same meet** (`_reload_same_meet`): re-parse, call `state.set_lenex`, emit `schedule_update`, call `relay.send_schedule()`. The live snapshot, timers, meet profile and console times are all kept.

## Contracts and docs

- `docs/app.md`:
  - claim `S-22` and `S-23`. `/mm` gets no ID: `app.md` covers the spectator's phone, and operator pages (`/manual`, `/operator`) are not in it;
  - reconcile per ID with web, iOS and Android side by side.
- `docs/api.md` §5.5 and §5.8: the new fields, plus a changelog entry.
- Parity ledgers (`docs/web-parity.md`, both apps' `parity.md`): add `S-22` and `S-23`.
- `docs/admin.md`: the export → `/mm` upload workflow.
- `server/meet_parsers/unused_parser_data.md`: update.

## Tests

- **Parser**: new fixture `tests/fixtures/splash_results.lxf` (Lenex 3.0, Splash layout), with:
  - an OFFICIAL heat with a corrected time;
  - a SEEDED heat with a result;
  - an INOFFICIAL heat, which is dropped;
  - a heat with no status, which is dropped;
  - a DSQ, a DNS and a relay.
- **Console store**:
  - one append per finish;
  - replay on boot when `meet_uid` matches;
  - a re-run heat: the last line wins;
  - another meet's file is ignored.
- **`/schedule.json`**: all five lane fields, and heat `official` true or false (empty lanes ignored).
- **Cloud**:
  - `results_snapshot` fills `console_times` and persists it;
  - `GET /meet/{id}/schedule` returns the fields.
- **Same-meet update**: keeps `_last_results_snapshot` and console times, and emits `schedule_update`. A different meet is still refused.
- **`/mm`**: requires login; the summary diff is correct.
- **iOS and Android**: decoding (absent fields → empty), the cell state choice, and the toggle only on official heats.

## Verification

- `uv run --with pytest pytest tests/`, `uv run ruff format --check`, `uv run ruff check`, `uv run ty check`, plus each app's test suite.
- Replay a console recording with a start-list .lxf loaded. Finished heats turn yellow on `/schedule`. Restart the Pi and the yellow times are still there.
- Upload the results fixture on `/mm`:
  - official heats turn green, and the DSQ shows;
  - the INOFFICIAL heat stays yellow;
  - a heat that is running is not disturbed.
- Tap an official heat:
  - it animates to the deltas (green faster, grey slower);
  - the DSQ lane shows its console time in yellow;
  - a lane with no seed shows `NT`;
  - it springs back after 4 s.
- Change the three colours in Settings → Appearance:
  - the Pi's `/schedule` follows;
  - the Qt board, `/live`, `/live-mobile`, the cloud page and the apps are unchanged.

## Open points

- Confirm against one real MM .lxf export:
  - heat `status` values;
  - the DSQ `swimtime`;
  - where Splash nests RESULTs.
