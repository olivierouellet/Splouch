#!/usr/bin/env bash
# Compatibility shim: the installer moved to install/install.sh during the repo
# reorganisation. Desktop launchers (and any bookmarks) created before the move
# still invoke the repo-root install.sh, so forward to the real location.
#
# This is intentionally a forwarding *script*, not a symlink: install.sh locates
# itself via its own directory (SCRIPT_DIR) to find ../server, so it must run
# with BASH_SOURCE pointing at install/install.sh. `exec bash <real>` preserves
# that; a root symlink would set SCRIPT_DIR to the repo root and break detection.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
REAL="$DIR/install/install.sh"

if [[ -f "$REAL" ]]; then
    exec bash "$REAL" "$@"
fi

echo "install.sh has moved to install/install.sh."
echo "Fresh install:"
echo "  curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/setup.sh -o setup.sh && bash setup.sh"
exit 1
