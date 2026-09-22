#!/usr/bin/env bash
# Launch the Qt scoreboard on the kiosk Pi.
#
# Called from the desktop session's autostart (labwc on Bookworm/Trixie, LXDE on
# older releases). It is a script rather than an inline command for three reasons:
#
#   1. It resolves the repo and its venv from its OWN location, so re-cloning or
#      moving the checkout can never leave a stale autostart line pointing at a
#      path that no longer exists.
#   2. It restarts the app if it exits. A kiosk has nobody sitting at a keyboard;
#      a crash must self-heal, not leave a black TV for the rest of the meet.
#   3. The server address lives in ~/.config/splouch/scoreboard.env, outside the
#      repo, so a git pull or a version switch never overwrites a site's address.
#
# Run it by hand to test: install/scripts/start-scoreboard.sh
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="$INSTALL_DIR/.venv/bin/python"

# Site-specific config (SPLOUCH_SERVER=…), written once by install.sh.
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/splouch/scoreboard.env"
# shellcheck disable=SC1090
[[ -f "$ENV_FILE" ]] && source "$ENV_FILE"
SPLOUCH_SERVER="${SPLOUCH_SERVER:-http://splouch.local}"
export SPLOUCH_SERVER

if [[ ! -x "$PYTHON" ]]; then
    echo "start-scoreboard: no venv at $PYTHON — run install/install.sh kiosk" >&2
    exit 1
fi

cd "$INSTALL_DIR" || { echo "start-scoreboard: no checkout at $INSTALL_DIR" >&2; exit 1; }

# The app has its own reconnect loop, so a normal exit here means it crashed or
# was killed. Back off briefly so a persistent failure (missing Qt plugin, no
# display) doesn't spin the CPU at boot.
while true; do
    "$PYTHON" -m scoreboard --server "$SPLOUCH_SERVER"
    status=$?
    # 0 means somebody closed it deliberately (dev running with --windowed).
    [[ $status -eq 0 ]] && break
    echo "start-scoreboard: scoreboard exited ($status) — restarting in 5s" >&2
    sleep 5
done
