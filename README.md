# Splouch

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Live swimming scoreboard display for timing consoles.

Decodes the serial feed from the timing console, adds swimmer names and club names from a **Splash Meet Manager** Lenex export, and displays a fullscreen scoreboard on a TV — all on a local network with no internet required at the pool.

Originally inspired by [STU940652/CTS_Scoreboard](https://github.com/STU940652/CTS_Scoreboard).
CTS serial protocol documentation by [hwbrill/vsCTS](https://github.com/hwbrill/vsCTS) and [Marco's Corner](https://marcoscorner.walther-family.org/2015/07/colorado-timing-console-scoreboard-protocol/).

---

## Documentation

| | |
| --- | --- |
| [Installation](docs/installation.md) | Pi #1 (server), Pi #2 (kiosk), updating, reinstalling |
| [Admin guide](docs/admin.md) | Meet-day workflow, settings tabs, pages, localisation |
| [Cloud relay](docs/cloud.md) | Public scoreboard for remote attendees |
| [Development](docs/development.md) | Data flow, adding a console decoder, bundled assets |
| [API contract](docs/api.md) | WebSocket + REST contract for native clients (TV / iOS / Android) |

---

## Supported Consoles

| Console | Status | Doc |
| --- | --- | --- |
| Colorado Time Systems — System 5 / System 6 / Gen7 Legacy | ✅ Tested | [cts-gen6.md](docs/consoles/cts-gen6.md) |
| Colorado Time Systems — Gen7 Serial | ⚠️ Untested | [cts-gen7.md](docs/consoles/cts-gen7.md) |
| Daktronics Omnisport 2000 | ⚠️ Untested | [omnisport-2000.md](docs/consoles/omnisport-2000.md) |
| Swiss Timing Omega — Ares 21 | ⚠️ Untested | [ares-21.md](docs/consoles/ares-21.md) |
| Swiss Timing Omega — Quantum | ⚠️ Untested | [quantum.md](docs/consoles/quantum.md) |
| Manual — no timing console | ✅ Tested | [manual.md](docs/consoles/manual.md) |

---

## Network

```text
Timing console
      │
   Serial adapter (see console doc)
      │
  Pi #1 ── eth0 ──┐
                  ├── Unmanaged switch ── Laptop
  Pi #2 ── eth0 ──┘
```

| Device | IP | Role |
| --- | --- | --- |
| Pi #1 | `10.10.10.10` | Serial decoder + FastAPI server + admin UI |
| Pi #2 | DHCP | Qt kiosk — fullscreen scoreboard on TV |

| Item | Purpose |
| --- | --- |
| Raspberry Pi 3B+ or 4 | Pi #1 — scoreboard server |
| Raspberry Pi 4 | Pi #2 — TV kiosk |
| Cat5e cable + unmanaged switch | Connect all pool-deck devices |

---

## Requirements

| | Minimum |
| --- | --- |
| Raspberry Pi OS | **Trixie** (October 2025) |
| Python | **3.13** (included in Trixie) |

---

## Quick install

Flash **Raspberry Pi OS Trixie** on each Pi with SSH enabled, then run on each:

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/install.sh -o install.sh && bash install.sh
```

The script asks which role to install: **Server**, **Kiosk**, or **Cloud**. See [docs/installation.md](docs/installation.md) for details.

---

## Community

Participation in this project is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
