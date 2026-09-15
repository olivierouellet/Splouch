# Installation

## Requirements

| | Minimum |
| --- | --- |
| Raspberry Pi OS | **Trixie** (October 2025) |
| Python | **3.13** (included in Trixie) |

Earlier releases (Bookworm / Python 3.11) are not supported.

---

## Hardware

| Item | Purpose |
| --- | --- |
| Raspberry Pi 3B+ or 4 | **Pi #1** — serial decoder + scoreboard server + admin UI |
| Raspberry Pi 4 (64-bit OS) | **Pi #2** — Qt scoreboard driving the TV |
| Unmanaged network switch | Wired pool-deck network for all devices |
| Cat5e cables | Pi #1 ↔ switch ↔ Pi #2 (and a laptop) |
| Console serial adapter | Depends on your timing console — see the per-console guides in [`docs/consoles/`](consoles/) |

The console-specific adapter and wiring (USB-to-RS232 vs RS-485, tap cable, pinout) live in
the per-console guides, and a summary for the selected console is shown in **Settings →
Timing**.

Topology:

```text
Timing console
      │  serial tap / adapter (see docs/consoles/)
   Pi #1 ── eth0 ───┐
                    ├── Unmanaged switch ── Pi #2 (TV kiosk)
   Laptop ── eth0 ──┘                    └─ Laptop (admin browser)
```

---

## Network & firewall

All pool-deck devices talk over a dedicated wired network on `eth0`. Pi #1 also joins home /
venue WiFi (`wlan0`) for internet access and remote management.

| Device | IP address | Role |
| --- | --- | --- |
| Pi #1 | `10.10.10.10/24` (static, `eth0`) | Serial decoder + FastAPI server + admin UI |
| Pi #2 | DHCP | Qt kiosk — scoreboard on the TV |
| Laptop | DHCP or static | Admin browser to `http://splouch.local` (or `10.10.10.10:5000`) |

**Firewall:** Pi #1 blocks incoming connections over WiFi — SSH and VNC are reachable only
via `eth0`. Connect your laptop by Ethernet to reach the admin UI or terminal at the pool.

---

## Pi #1 — Server

Flash **Raspberry Pi OS Trixie** using Raspberry Pi Imager. Enable SSH during flash.

> **Tip:** Configure WiFi in Imager before flashing. The Pi will have `wlan0` (home WiFi) and `eth0` (static pool network `10.10.10.10`) active simultaneously — useful for SSH access at home and a clean pool network at the venue.

SSH in and run:

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/install.sh -o install.sh && bash install.sh server
```

The script:
- Installs Python dependencies via `uv`
- Creates the `splouch` systemd service (starts on boot)
- Adds the user to the `dialout` group for serial port access
- Creates `~/SplouchData/` with `meet/`, `images/`, `icons/`, and `recorded/` subdirectories
- Copies `settings.default.json` to `~/SplouchData/settings.json`
- Downloads xterm.js
- Sets the static IP to `10.10.10.10/24` (asks for confirmation — this will drop your SSH session if connected over Ethernet)
- Sets the hostname to `splouch` (accessible as `splouch.local` on the network)

---

## Pi #2 — Kiosk

Flash **Raspberry Pi OS Trixie — Desktop, 64-bit** with SSH enabled. Desktop (not Lite)
because the display needs a graphical session; 64-bit because PySide6 ships no 32-bit
Raspberry Pi wheel.

SSH in and run:

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/install.sh -o install.sh && bash install.sh kiosk
```

The script:

- Clones the repo to `~/Splouch` at the **same version** you install on Pi #1 — display
  and server must agree on the WebSocket contract
- Installs Qt via `uv sync --extra scoreboard` (PySide6; only this role pulls Qt)
- Writes the server address to `~/.config/splouch/scoreboard.env`
- Enables desktop autologin and autostarts the [Qt scoreboard](../scoreboard/README.md)
  fullscreen on boot
- Forces 1920×1080 HDMI output

> **Install the same version on both Pis.** The kiosk now runs code, not just a browser.
> Pick the same answer at the version prompt on Pi #1 and Pi #2.
>
> Pi #1 must be running and reachable before the kiosk boots — though the display no
> longer needs it at startup: it opens immediately and connects when the server appears.

### Leaving and reopening the scoreboard

| key | effect |
| --- | --- |
| **Ctrl+Q** | quit to the desktop |
| **F11** or **Ctrl+F** | toggle fullscreen |
| **Esc** | leave fullscreen (does not quit) |

Quitting with Ctrl+Q returns you to the desktop and stays there — it is treated as
deliberate, so nothing relaunches. Double-click the **Scoreboard** icon on the desktop
to start it again. A **Settings** icon opens the server's admin page in a browser.

Useful commands on the kiosk:

```bash
~/Splouch/install/scripts/start-scoreboard.sh                    # run it by hand
cd ~/Splouch && .venv/bin/python -m scoreboard --windowed         # windowed, for testing
```

### Upgrading a kiosk from the Chromium display

Re-run `bash install.sh kiosk`. The script removes every autostart line this project has
written — both the `# Splouch kiosk` marker and the pre-rename `# Tremplin kiosk` one —
before adding the Qt launcher, so there is nothing to uninstall first. Chromium itself is
left installed; it is simply no longer started.

> **If the TV still shows the old web page after an update**, the Pi was provisioned before
> the Tremplin→Splouch rename and is starting Chromium *as well as* the Qt display. Only the
> new marker used to be cleaned up, so the old block survived every re-run; the browser
> starts faster and ends up on top. Re-running the installer now clears it. To check by
> hand:
>
> ```bash
> grep -n -e kiosk -e chromium ~/.config/labwc/autostart
> ```
>
> One `# Splouch kiosk` block naming `start-scoreboard.sh`, and nothing else, is correct.

---

## Cloud server

See [cloud.md](cloud.md) for deploying the optional public relay server.

---

## Credentials

### Pi server

| Credential | Where set | Default | Action |
| --- | --- | --- | --- |
| **Pi user password** | Raspberry Pi Imager, before flashing | *(you choose)* | Needed for SSH and sudo |
| **Admin UI username** | `~/SplouchData/settings.json` | `score` | Change in **Settings → Account** before meet day |
| **Admin UI password** | `~/SplouchData/settings.json` | `swimming` | Change in **Settings → Account** before meet day |

### Cloud

| Credential | Where set | Default | Action |
| --- | --- | --- | --- |
| **SSH key** | Must exist on the server before running the install | — | Required — the script copies `root`'s `authorized_keys` to the `splouch` user; password auth is disabled after install |
| **`splouch` Linux password** | Prompted by the install script | *(you choose)* | Needed for SSH login and sudo after install |
| **`/admin` panel username** | Prompted by the install script | `admin` | Stored in `cloud/.env` |
| **`/admin` panel password** | Prompted by the install script | *(you choose)* | Stored in `cloud/.env` |
| **`SECRET_KEY`** | Auto-generated by the install script | — | Stored in `cloud/.env`; no need to record |
| **`DEPLOY_SECRET`** | Auto-generated by the install script | — | Stored in `cloud/.env`; no need to record |
| **Organizer relay keys** | Generated in `/admin` after install | — | Share each key with the corresponding Pi operator |

---

## Running

The service starts automatically on boot. Manual control on Pi #1:

```bash
sudo systemctl start   splouch
sudo systemctl stop    splouch
sudo systemctl restart splouch
journalctl -u splouch -f          # live logs
```

---

## Updating

The easiest way is from the **Update & Backup** tab in the admin UI — it pulls the latest release, syncs dependencies, and restarts the service.

To update manually over SSH:

```bash
cd ~/Splouch
git pull
uv sync
sudo systemctl restart splouch
```

**Pi #2 must now be updated too.** The kiosk used to reload the scoreboard from Pi #1 on
every boot, so a reboot was enough. It now runs the Qt display from its own checkout, so
it needs the same version as Pi #1 or the two can disagree on the WebSocket contract:

```bash
cd ~/Splouch
git pull
uv sync --extra scoreboard
sudo reboot
```

Update Pi #1 first, then Pi #2 to the same version. Easier: **Settings → Update →
Update displays** moves every connected kiosk to the version the server is on.

---

## Reinstalling

If the server is down and the web UI is unreachable, re-run the install script directly on Pi #1.

**From the desktop** — double-click the **Reinstall Scoreboard** icon created during install.

**From the terminal:**

```bash
bash ~/Splouch/reinstall.sh
# or pass the role directly:
bash ~/Splouch/reinstall.sh server
```
