# Admin Guide

## Default credentials

| | |
| --- | --- |
| URL | `http://splouch.local/settings` |
| Username | `score` |
| Password | `swimming` |

Change these in **Settings → Account** before deploying at a meet.

---

## Pages

| URL | Description |
| --- | --- |
| `/` | Redirects to `/live` |
| `/live` | The scoreboard (lane count from Meet Setup settings) — the reference display, and what the Qt board mirrors |
| `/operator` | Operator control view |
| `/mobile` | Mobile shell — three-tab view (Scoreboard, Results, Schedule) |
| `/results` | Results after each heat |
| `/schedule` | Meet schedule with start times and heat entry lists |
| `/console` | Live serial console viewer |
| `/settings` | Admin settings (login required) |

Append `?test` to any scoreboard URL to show mode control buttons (Splash, Intro, Running, Results, Next Heat) overlaid on the display — useful for testing without a live console.

---

## Meet-day workflow

1. In Splash Meet Manager: **File → Export → Lenex** → save as a `.lxf` file.
2. In **Settings → Meet Setup**, click **Add Meet File** and upload the `.lxf` — swimmer and
   club names go live on the scoreboard immediately. *(Alternatively, upload a Hytek `.csv`
   event schedule so event names appear in the header.)*
3. Open the scoreboard on the TV at `http://splouch.local/`.
4. Start the CTS console — times appear automatically as heats run.

> Prefer the command line? See [Manual and CLI reference](#manual-and-cli-reference) for
> placing meet files directly in `~/SplouchData/meet/`.

---

## Settings tabs

| Tab | Description |
| --- | --- |
| **Meet Setup** | Upload Lenex `.lxf` / Hytek `.csv` meet files; pool length, touchpads, lane count |
| **Timing** | Serial port, console type, connection status, serial monitor (raw hex packets) |
| **Clock** | Sync with NTP; set date and time manually when offline; install/remove Adafruit PiRTC (DS3231) hardware clock |
| **Flow** | Intro, results, and server-update timeouts; finish debounce |
| **Display** | Show/hide column headers and columns (Name, Club, Delta, Position); podium highlighting |
| **Theme** | Built-in colour schemes; override individual colours and fonts; save as a custom theme |
| **Network** | WiFi management; view connected scoreboard clients |
| **Update & Backup** | Pull latest version from GitHub, sync dependencies, restart; download or restore a backup of `~/SplouchData` |
| **Test** | Play back pre-recorded sessions; adjust playback speed; record live serial sessions. Safe to run with a meet loaded — see [Test sessions](#test-sessions) |
| **Terminal** | In-browser terminal — Shell, raspi-config, Scoreboard logs, dmesg, serial ports |
| **Cloud** | Cloud relay URL and key; per-meet picker appearance (title, image, home icon, location, sport) |
| **Power** | Restart the app service, reboot, or shut down the Pi — press-and-hold to confirm |
| **Account** | Change the admin UI username and password (via the sidebar account menu) |

> In the sidebar, **Flow / Display / Theme** live under the **Scoreboard** group; **Cloud**
> and **Network** are top-level.

---

## Data folders on Pi #1

| Path | Contents |
| --- | --- |
| `~/SplouchData/meet/` | Lenex `.lxf` and Hytek `.csv` meet files (uploaded via Meet Setup, or [placed here manually](#manual-and-cli-reference)) |
| `~/SplouchData/images/` | Sponsor or club logo images for the splash screen |
| `~/SplouchData/recorded/` | Custom recorded sessions for playback in the Test tab |
| `~/SplouchData/test_meet/` | Start lists for a running test session — cleared when it ends, never mixed with `meet/` |
| `~/SplouchData/themes/` | Custom theme `.toml` files |
| `~/SplouchData/console_decoders/` | Local-only decoder plugins (`.py` files) — loaded at startup, not tracked by git |
| `~/SplouchData/settings.json` | All admin UI settings |

---

## Updating the displays

Three ways, and which one you reach for depends on what is in front of you.

| From | How | Use when |
| --- | --- | --- |
| The server's admin page | Settings → Update → **Update displays** | The usual way. Moves every *registered* display to the ref this server is on. Update the server first. |
| The display itself | **F1** on the TV's keyboard → *Update to the server's version* | No browser to hand, or the display is too old for the button above to see it. |
| An SSH session | `bash install.sh kiosk` on the TV Pi | The display will not start, or is so old it does not have the menu. |

All three land on the **same commit the server is running** — never a branch, so
the two ends cannot drift apart and disagree about the WebSocket contract. The
server must be on a clean commit that has been pushed; being off a release tag is
fine, being dirty is not.

> **"Update displays" says no displays are registered, but I can see one.** A
> display announces itself with a `register` frame, and only the Qt scoreboard
> sends one — a browser tab does not, and a Chromium kiosk showing `/live` *is* a
> browser tab. An announced display shows a hostname, a `kiosk` badge and a version
> in the list; a row with only an IP is a browser, or a kiosk installed before
> v2026.09.0 when the Qt display replaced Chromium.
>
> That older kiosk cannot be rescued remotely: it is too old to announce itself and
> too old to act on the update it would be sent. Do the first hop at the display —
> F1 if it has the menu, otherwise `bash install.sh kiosk` — and the remote button
> works from then on. See also [Upgrading a kiosk from the Chromium
> display](installation.md#upgrading-a-kiosk-from-the-chromium-display).

---

## Test sessions

The Test tab replays a recorded console session, so the board behaves exactly as it
does during a real race. Two things used to make that awkward, and neither does now.

**Your meet stays loaded.** A recording's event and heat numbers refer to the start
lists in the companion `.lxf` shipped beside it, so that is what a replay runs
against. Your own meet is held in place while it does: the files in
`~/SplouchData/meet/` are never touched, the meet is still the active one, and it is
reloaded the moment the session ends — whether you press **Stop** or the recording
simply runs out. Deleting the meet first and re-uploading it afterwards is no longer
part of the job.

**Keep this test local.** Ticked, the replay reaches the TV display and phones on the
pool's own network, and nothing else: the cloud link is closed for the duration, so
spectators watching remotely see the meet as offline rather than a recording dressed
up as the race in front of them. It is ticked and locked whenever a meet is loaded —
publishing invented times under a live meet's identity is not something a checkbox
should allow. With no meet loaded it is yours to set, and the choice is remembered.

When the session ends, every board is wiped of the replay, the meet comes back, the
cloud link is restored if it was up before, and playback speed returns to 1×.

---

## Localisation

One file in `shared/locales/` is one language, and it is what a spectator reads:
the column labels, the event-name vocabulary, the phone pages' chrome and the TV
display's status lines. The Pi, the cloud, the phone apps and the TV all read it,
the apps through `GET /i18n/{lang}` ([api.md](api.md) §5.9).

| File | Language |
| --- | --- |
| `shared/locales/en.toml` | English — the fallback for every key |
| `shared/locales/fr.toml` | Français |
| `shared/locales/es.toml` | Español |
| `shared/locales/panel/<code>.toml` | the operator panel, meet preview and cloud admin — optional |

Each served file carries the same sections, and the test suite fails when a
language lacks a key English has:

```toml
[meta]
name = "English"

[labels]                       # column headers, short and long forms
event = { short = "EV",   long = "EVENT" }
heat  = { short = "HT",   long = "HEAT"  }

[event_name]                   # the words an event name is composed from
freestyle = "Freestyle"

[mobile]                       # phone pages, apps and picker chrome
scoreboard = "Scoreboard"

[display]                      # TV display status lines
waiting_server = "Waiting for the timing server"
```

**Adding a language** is a pull request with one new file in `shared/locales/`,
complete against `en.toml`. It appears in every Language control on the next
deploy; the phone apps pick it up from `GET /locales` without a release. A
matching `panel/<code>.toml` is welcome but not required — every panel string it
lacks renders in English, key by key.

**What is not translated here.** Words about an app or a device — the server
sheet, connection errors, OS requirements — live in each app repo, natively.
The rule is in [app.md](app.md) `T-05`: if the web page shows the word, the
server owns it; otherwise the app does.

There is no per-Pi locale file. A club that wants different wording changes the
shipped file, so every server and every client agree.

---

## Manual and CLI reference

Everything here can also be done from the admin UI — these are the manual equivalents and
lower-level tools for when you're SSH'd into Pi #1.

### Load meet files manually

Instead of uploading in **Meet Setup**, copy `.lxf` / `.csv` files to `~/SplouchData/meet/`.
They appear in the Meet Setup file dropdown — select one to load it live.

### Service management

The app runs as a systemd service named **`splouch`**. The **Power** tab does restart /
reboot / shutdown and **Terminal** has a "Scoreboard logs" launcher and "Save Logs", but
over SSH:

```sh
sudo systemctl restart splouch    # restart after manual changes (same as the Power tab)
sudo systemctl stop splouch       # stop the service
sudo systemctl start splouch      # start it again
systemctl status splouch          # current state
journalctl -u splouch -f          # follow live logs
```

### CLI troubleshooting

**The service won't start.** Run `journalctl -u splouch -f` to see the error. Common
causes: wrong serial port, missing Python dependencies (run `uv sync` in the repo
directory), or another process already bound to port 5000.

**Serial adapter not detected.** Run `ls /dev/ttyUSB*` on Pi #1 to list adapters. The
service user must be in the `dialout` group — check with `groups`; if missing, `sudo
usermod -aG dialout <user>` and reboot. (The installer normally handles this.)

**`splouch.local` unreachable.** See [troubleshooting-splouch-local-unreachable.md](troubleshooting-splouch-local-unreachable.md).
