#!/usr/bin/env bash
# Single source of truth for the splouch systemd unit.
#
# Idempotent and non-interactive: it (re)writes /etc/systemd/system/<name>.service
# to match the CURRENT repo layout, then daemon-reloads. Because the unit is
# code-coupled (its ExecStart / WorkingDirectory must track where the app lives),
# this script is called both by install.sh and by the in-app update flow
# (server/routes/system.py::_run_update) right before the service restart — so a
# changed entrypoint or moved directory can never leave a stale unit that
# crash-loops on restart.
#
# It touches ONLY the code-coupled unit (and re-detects the dynamic mDNS IP). It
# never re-runs environment provisioning (static IP, firewall, hostname), which
# is one-time and interactive by design.
#
# Must run as root (writes under /etc/systemd). Invoked as: sudo refresh-service.sh
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "refresh-service.sh must run as root (use sudo)." >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Service base-name. Optional: install.sh passes it explicitly, the in-app update
# calls this with no argument.
NAME="${1:-splouch}"

# The service runs as the owner of the repo checkout. Prefer $SUDO_USER (the user
# who invoked sudo — the installer or the running service account); fall back to
# the directory owner so it is correct even when called from an odd context.
RUN_USER="${SUDO_USER:-$(stat -c '%U' "$INSTALL_DIR")}"
UVICORN_BIN="$INSTALL_DIR/.venv/bin/uvicorn"

tee "/etc/systemd/system/${NAME}.service" > /dev/null <<EOF
[Unit]
Description=Splouch FastAPI server
After=network.target

[Service]
User=$RUN_USER
WorkingDirectory=$INSTALL_DIR/server
ExecStart=$UVICORN_BIN app:app --host 0.0.0.0 --port 5000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${NAME}.service" >/dev/null 2>&1 || true

# Re-publish the translated mDNS aliases at the current interface IP (dynamic).
# Best-effort: absent on a fresh install until install.sh creates the unit.
systemctl restart "${NAME}-mdns-aliases.service" 2>/dev/null || true

echo "${NAME}.service refreshed: User=$RUN_USER WorkingDirectory=$INSTALL_DIR/server ExecStart=$UVICORN_BIN app:app"
