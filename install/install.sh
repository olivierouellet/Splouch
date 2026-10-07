#!/usr/bin/env bash
# Splouch — Raspberry Pi install script
#
# Usage:
#   bash install.sh           interactive role selection
#   bash install.sh server    Pi #1 — pool deck FastAPI server
#   bash install.sh kiosk     Pi #2 — TV kiosk display
#
# Run from inside the cloned repo, or from anywhere (it will clone automatically).

set -euo pipefail

# ── Configuration ──────────────────────────────────────────────────────────────
REPO_URL="https://github.com/olivierouellet/Splouch.git"

# Provision for the real owner of the checkout, not root. The in-app Reinstall
# runs this script non-interactively in a detached systemd scope as root, passing
# SPLOUCH_TARGET_USER; interactive runs resolve to the invoking (or sudo) user.
TARGET_USER="${SPLOUCH_TARGET_USER:-${SUDO_USER:-$USER}}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
TARGET_HOME="${TARGET_HOME:-$HOME}"

INSTALL_DIR="$TARGET_HOME/Splouch"          # default for fresh installs; existing checkouts are auto-detected
SERVER_HOSTNAME="splouch"                   # broadcasts as splouch.local on the network
MDNS_ALIASES="tableau.local marcador.local" # the board's name in each language it ships
SCOREBOARD_URL="http://${SERVER_HOSTNAME}.local"
SERIAL_PORT="/dev/ttyUSB0"
# ──────────────────────────────────────────────────────────────────────────────

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'
info() { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
section() { echo -e "\n${BOLD}──── $* ────${NC}"; }
# Auto-answer No in non-interactive mode (the in-app Reinstall has no TTY), so
# optional prompts (static IP, RTC, reboot) safely keep the current config.
# STATIC_IP holds eth0's address once this run pinned one, so messages only quote
# a raw IP when it is actually the Pi's address. ETH_PENDING marks an eth0 change
# saved but not yet active: activating it drops an SSH session or the web terminal
# over Ethernet, which would kill the script, so it only takes effect through the
# automatic reboot that ends the run (see reboot_for_network).
STATIC_IP=
ETH_PENDING=0
confirm() {
    if [[ "${SPLOUCH_NONINTERACTIVE:-}" == "1" ]]; then
        info "Non-interactive — skipping: $1"
        return 1
    fi
    read -rp "$1 [y/N] " _r
    [[ "${_r:-}" =~ ^[Yy]$ ]]
}

# Run a command as the target user when we're root (detached reinstall); run it
# directly otherwise. Keeps user-owned files (data dir, venv, desktop) correct.
as_user() {
    if [[ ${EUID:-$(id -u)} -eq 0 && "$TARGET_USER" != "root" ]]; then
        sudo -u "$TARGET_USER" -H "$@"
    else
        "$@"
    fi
}

# Some networks (corporate/intranet proxies) intercept plain HTTP and return a
# fake 404 for apt's Release files, breaking `apt-get update`. Switching the apt
# sources to HTTPS sidesteps that, and is harmless on networks without a proxy.
# See docs/troubleshooting.md
ensure_https_apt_sources() {
    sudo sed -i \
        -e 's|http://deb.debian.org|https://deb.debian.org|g' \
        -e 's|http://archive.raspberrypi.com|https://archive.raspberrypi.com|g' \
        -e 's|http://security.debian.org|https://security.debian.org|g' \
        /etc/apt/sources.list \
        /etc/apt/sources.list.d/*.list \
        /etc/apt/sources.list.d/*.sources 2>/dev/null || true
}

# The blanket NOPASSWD grant the cloud role needs while it installs, in a file
# named for its lifetime. Defined here so the code that writes it and the cleanup
# that removes it can never drift onto different names again.
TEMP_SUDOERS_FILE="/etc/sudoers.d/splouch-install-temp"

# ── Role selection ─────────────────────────────────────────────────────────────
ROLE="${1:-}"
if [[ -z "$ROLE" && "${SPLOUCH_NONINTERACTIVE:-}" == "1" ]]; then ROLE="server"; fi
if [[ -z "$ROLE" ]]; then
    echo
    PS3="Type a number and press Enter: "
    echo "What is this machine for?"
    select _choice in \
        "Server — the Raspberry Pi connected to the timing console; it runs the scoreboard" \
        "Kiosk  — a Raspberry Pi plugged into a TV; it shows the server's scoreboard" \
        "Cloud  — your own internet server (Debian/Ubuntu) so people can follow from their phones; only if you do not use splouch.org" \
        "Quit"; do
        case "$_choice" in
            Server*)
                ROLE="server"
                break
                ;;
            Kiosk*)
                ROLE="kiosk"
                break
                ;;
            Cloud*)
                ROLE="cloud"
                break
                ;;
            Quit) exit 0 ;;
        esac
    done
fi

if [[ "$ROLE" != "server" && "$ROLE" != "kiosk" && "$ROLE" != "cloud" ]]; then
    error "Unknown role '$ROLE'. Use 'server', 'kiosk', or 'cloud'."
    exit 1
fi
info "Role: $ROLE"

# ── Version selection ─────────────────────────────────────────────────────────
# `latest`, `master`, or any tag, branch or commit. install/setup.sh downloads the
# installer of the version picked and passes that version here; it checks this
# script for SPLOUCH_PINS_REF before trusting it to stay on that version (older
# installers know only `latest` and `master`).
VERSION_CHOICE="${2:-}"
if [[ -z "$VERSION_CHOICE" && "${SPLOUCH_NONINTERACTIVE:-}" == "1" ]]; then VERSION_CHOICE="master"; fi
if [[ -z "$VERSION_CHOICE" ]]; then
    echo
    echo "Which version to install?"
    PS3="Type a number and press Enter: "
    select _choice in \
        "Latest release (recommended)" \
        "Master (development branch)"; do
        case "$_choice" in
            Latest*)
                VERSION_CHOICE="latest"
                break
                ;;
            Master*)
                VERSION_CHOICE="master"
                break
                ;;
        esac
    done
fi

# ── System packages ────────────────────────────────────────────────────────────
section "System packages"
ensure_https_apt_sources
sudo apt-get update -qq
sudo apt-get upgrade -y
sudo apt-get install -y git curl ufw

# ── eth0 addressing ────────────────────────────────────────────────────────────
# eth0 joins the venue router (which also carries the Pi to the cloud). DHCP is
# the default; a static address is opt-in. Both live in one NetworkManager
# profile, `splouch-eth`, which Settings → Network also edits — so it always
# exists after install, and choosing DHCP clears any static address left in it.
ETH_CON="splouch-eth"

# Dotted quad <-> integer, for validating an address against its subnet.
ip_to_int() {
    local IFS=. a b c d
    read -r a b c d <<<"$1"
    echo $(((10#$a << 24) | (10#$b << 16) | (10#$c << 8) | 10#$d))
}
int_to_ip() {
    echo "$((($1 >> 24) & 255)).$((($1 >> 16) & 255)).$((($1 >> 8) & 255)).$(($1 & 255))"
}
valid_ipv4() {
    [[ $1 =~ ^([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})\.([0-9]{1,3})$ ]] || return 1
    local o
    for o in "${BASH_REMATCH[@]:1}"; do ((10#$o <= 255)) || return 1; done
}

# Ask until valid. Prints the answer on stdout; prompts go to stderr.
ask_static_address() {
    local def="$1" ans addr prefix host mask
    while :; do
        read -rp "Static address for eth0 (CIDR, /24 if omitted)${def:+ [$def]}: " ans >&2
        ans="${ans:-$def}"
        addr="${ans%/*}"
        prefix=24
        [[ $ans == */* ]] && prefix="${ans#*/}"
        if ! valid_ipv4 "$addr"; then
            warn "Not an IPv4 address: $addr" >&2
            continue
        fi
        if ! [[ $prefix =~ ^[0-9]+$ ]] || ((prefix < 8 || prefix > 30)); then
            warn "Prefix must be 8–30, got /$prefix" >&2
            continue
        fi
        mask=$(((0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF))
        host=$(($(ip_to_int "$addr") & ~mask & 0xFFFFFFFF))
        if ((host == 0 || host == (~mask & 0xFFFFFFFF))); then
            warn "$addr is the network or broadcast address of /$prefix" >&2
            continue
        fi
        echo "$addr/$prefix"
        return
    done
}

# Ask for an address inside the static subnet (the router). Prints it on stdout.
ask_gateway() {
    local cidr="$1" addr="${1%/*}" prefix="${1#*/}" mask net def ans
    mask=$(((0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF))
    net=$(($(ip_to_int "$addr") & mask))
    def="$(int_to_ip $((net + 1)))"
    [[ $def == "$addr" ]] && def=""
    while :; do
        read -rp "Router (gateway) address${def:+ [$def]}: " ans >&2
        ans="${ans:-$def}"
        if ! valid_ipv4 "$ans"; then
            warn "Not an IPv4 address: ${ans:-(empty)}" >&2
        elif ((($(ip_to_int "$ans") & mask) != net)); then
            warn "$ans is outside $cidr" >&2
        elif [[ $ans == "$addr" ]]; then
            warn "The router can't share this Pi's address" >&2
        else
            echo "$ans"
            return
        fi
    done
}

configure_network() {
    echo
    if [[ "${SPLOUCH_NONINTERACTIVE:-}" == "1" ]]; then
        info "Non-interactive — keeping the current eth0 configuration."
        return 0
    fi
    if ! command -v nmcli &>/dev/null; then
        warn "NetworkManager (nmcli) not found — configure eth0 manually."
        return 0
    fi

    local exists=0 method="" current=""
    if nmcli -t con show "$ETH_CON" &>/dev/null; then
        exists=1
        method="$(nmcli -g ipv4.method con show "$ETH_CON" 2>/dev/null)"
        [[ $method == manual ]] \
            && current="$(nmcli -g ipv4.addresses con show "$ETH_CON" 2>/dev/null)"
    fi
    info "Every device reaches this Pi as ${SERVER_HOSTNAME}.local — no fixed IP needed."
    info "eth0 is currently: ${current:+static $current}${current:-DHCP}"
    warn "Changing eth0 reboots the Pi automatically at the end of this install."
    warn "SSH sessions and the Settings → Terminal page will disconnect; reconnect"
    warn "to ${SERVER_HOSTNAME}.local once it is back up."
    echo "  1) DHCP — address from the venue router (default)"
    echo "  2) Static IP"
    local choice
    while :; do
        read -rp "eth0 addressing [1]: " choice
        case "${choice:-1}" in
            1 | 2) break ;;
            *) warn "Enter 1 or 2." ;;
        esac
    done

    # Priority above NetworkManager's default wired profile, so ours wins at boot.
    local -a base=(connection.autoconnect yes connection.autoconnect-priority 100)

    if [[ ${choice:-1} == 1 ]]; then
        local -a dhcp=(ipv4.method auto ipv4.addresses "" ipv4.gateway "" ipv4.dns "")
        if ((! exists)); then
            # Takes over from the default profile at the next boot — no drop now.
            sudo nmcli con add type ethernet ifname eth0 con-name "$ETH_CON" "${base[@]}" "${dhcp[@]}"
            info "eth0 on DHCP."
        elif [[ -n $current ]]; then
            sudo nmcli con mod "$ETH_CON" "${base[@]}" "${dhcp[@]}"
            ETH_PENDING=1
            info "eth0 back to DHCP (removing static $current) — applied at the end."
        else
            info "Keeping eth0 on DHCP."
        fi
        return 0
    fi

    local cidr gateway dns
    cidr="$(ask_static_address "$current")"
    gateway="$(ask_gateway "$cidr")"
    while :; do
        read -rp "DNS server [$gateway]: " dns
        dns="${dns:-$gateway}"
        valid_ipv4 "$dns" && break
        warn "Not an IPv4 address: $dns"
    done

    confirm "Set eth0 to $cidr via $gateway (DNS $dns)?" || {
        info "Leaving eth0 unchanged."
        return 0
    }
    local -a props=("${base[@]}" ipv4.method manual ipv4.addresses "$cidr"
        ipv4.gateway "$gateway" ipv4.dns "$dns")
    if ((exists)); then
        sudo nmcli con mod "$ETH_CON" "${props[@]}"
    else
        sudo nmcli con add type ethernet ifname eth0 con-name "$ETH_CON" "${props[@]}"
    fi
    ETH_PENDING=1
    STATIC_IP="${cidr%/*}"
    info "Static IP $STATIC_IP saved — the Pi reboots at the end to apply it."
}

# Reboot to activate a saved eth0 change, as the script's very last act and
# without asking: the profile is saved with autoconnect priority, so the boot
# brings it up, and nothing is left to run after the connection drops. Detached
# through systemd so the reboot still happens after the session it drops (SSH or
# the web terminal) has taken this shell down.
reboot_for_network() {
    warn "eth0 changed — rebooting in 10 s to apply it. This session will drop."
    info "Reconnect to ${SERVER_HOSTNAME}.local${STATIC_IP:+ or $STATIC_IP} in about a minute."
    sudo systemd-run --quiet --collect --on-active=10 systemctl reboot
}

# ── Fetching without assuming a tracked branch ────────────────────────────────
# A display that has taken a remote update is sitting on the local branch
# `display`, pinned to the commit the server was on (scoreboard/updater.py). That
# branch has no upstream, because it tracks a *commit* and not a branch — so a bare
# `git pull` there fails with "There is no tracking information for the current
# branch", and with `set -e` that aborts the whole install. The one command an
# operator reaches for when a display is broken was the one that could not run.
#
# So: always fetch, and only fast-forward when there is something to fast-forward
# to. `checkout_version` moves to the right ref immediately afterwards either way.
fetch_and_ff() {
    local dir="$1"
    git -C "$dir" fetch --tags --quiet || true
    local upstream
    upstream="$(git -C "$dir" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
    if [[ -z "$upstream" ]]; then
        info "On a branch with no upstream — skipping the pull."
        return 0
    fi
    git -C "$dir" merge --ff-only "$upstream" --quiet 2>/dev/null \
        || warn "Could not fast-forward onto $upstream — using the on-disk code."
}

# ── Version checkout ───────────────────────────────────────────────────────────
# Shared by the server and kiosk roles so both resolve $VERSION_CHOICE to the SAME
# ref. That is what keeps the Qt display and the server speaking the same
# WebSocket contract — see docs/architecture/native-app-strategy.md.
checkout_version() {
    local dir="$1"
    if [[ "$VERSION_CHOICE" == "latest" ]]; then
        local latest_tag
        latest_tag=$(git -C "$dir" tag -l --sort=-version:refname \
            | grep -E '^v[0-9]{4}\.[0-9]{2}\.[0-9]+$' | head -1)
        if [[ -n "$latest_tag" ]]; then
            git -C "$dir" checkout -B release "$latest_tag"
            info "Version: $latest_tag"
        else
            warn "No release tags found — using master."
        fi
    elif [[ "$VERSION_CHOICE" != "master" && "$VERSION_CHOICE" != "main" ]]; then
        # A version picked in setup.sh: a tag, else a branch, else a commit.
        if git -C "$dir" rev-parse --verify --quiet "refs/tags/$VERSION_CHOICE" >/dev/null; then
            git -C "$dir" checkout -B release "refs/tags/$VERSION_CHOICE" --quiet
        elif git -C "$dir" rev-parse --verify --quiet "origin/$VERSION_CHOICE" >/dev/null; then
            git -C "$dir" checkout -B "$VERSION_CHOICE" "origin/$VERSION_CHOICE" --quiet
        elif git -C "$dir" rev-parse --verify --quiet "$VERSION_CHOICE^{commit}" >/dev/null; then
            git -C "$dir" checkout -B release "$VERSION_CHOICE" --quiet
        else
            error "Version '$VERSION_CHOICE' is not a tag, branch or commit of this repo."
            exit 1
        fi
        info "Version: $VERSION_CHOICE"
    else
        # `-B … origin/<branch>`, not a bare checkout. A display arriving here has
        # just skipped its pull (it was on the untracked `display` branch), so its
        # *local* master is whatever it was when the Pi was last installed — often
        # months behind the origin/master the operator believes they are choosing.
        # Falls back to the local branch if there is no remote to point at.
        local branch
        for branch in master main; do
            if git -C "$dir" rev-parse --verify --quiet "origin/$branch" >/dev/null; then
                git -C "$dir" checkout -B "$branch" "origin/$branch" --quiet
                info "Version: $branch (origin/$branch)"
                return 0
            fi
        done
        git -C "$dir" checkout master 2>/dev/null \
            || git -C "$dir" checkout main 2>/dev/null || true
        info "Version: master (no origin — using the local branch)"
    fi
}

# ── uv (Python package manager) ────────────────────────────────────────────────
ensure_uv() {
    section "uv (Python package manager)"
    if ! command -v uv &>/dev/null; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
        export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    fi
    info "uv $(uv --version)"
}

# ═══════════════════════════════════════════════════════════════════════════════
# SERVER (Pi #1)
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$ROLE" == "server" ]]; then

    section "Project"
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-install.sh}")" 2>/dev/null && pwd)" || SCRIPT_DIR=""

    if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/../server/app.py" ]]; then
        INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
        info "Running from project directory: $INSTALL_DIR"
        # Self-update so a reinstall (e.g. the desktop Reinstall icon) provisions
        # the LATEST code, not whatever is already on disk — otherwise a reinstall
        # just re-runs the stale on-disk installer. Skip the pull when the working
        # tree has local edits (a dev checkout) so we never clobber them; the
        # version-selection step below then checks out the requested ref.
        if [[ -d "$INSTALL_DIR/.git" ]]; then
            # `uv sync` rewrites uv.lock; discard that expected drift so it doesn't
            # look like a local edit and block the self-update. `HEAD --`, not a bare
            # `--`: the latter restores from the index, so a *staged* uv.lock stays
            # different from HEAD, keeps the tree dirty, and skips the pull below.
            git -C "$INSTALL_DIR" checkout HEAD -- uv.lock 2>/dev/null || true
            if [[ -z "$(git -C "$INSTALL_DIR" status --porcelain 2>/dev/null)" ]]; then
                info "Fetching latest code before reinstalling…"
                fetch_and_ff "$INSTALL_DIR"
            else
                warn "Working tree has local changes — reinstalling on-disk code (no pull)."
            fi
        fi
    elif [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Updating existing repo at $INSTALL_DIR"
        fetch_and_ff "$INSTALL_DIR"
    else
        info "Cloning $REPO_URL → $INSTALL_DIR"
        git clone "$REPO_URL" "$INSTALL_DIR"
        git -C "$INSTALL_DIR" fetch --tags
    fi

    checkout_version "$INSTALL_DIR"

    ensure_uv

    section "Python dependencies"
    cd "$INSTALL_DIR"
    uv sync
    info "Virtual environment ready at $INSTALL_DIR/.venv"

    section "Privileged scripts"
    # Root-owned copies of the scripts that run as root. The checkout belongs to the
    # service user, so a grant naming a script *in* it let that user edit the script
    # and then run it as root: anything running as the service user was root.
    # `INSTALL_DIR` tells refresh-service.sh which checkout it serves, now that it no
    # longer sits inside one. Re-copied on every install; a release that changes one
    # of these bumps install/PROVISION_VERSION, so the panel asks for a reinstall.
    PRIV_DIR="/usr/local/lib/splouch"
    sudo install -d -o root -g root -m 0755 "$PRIV_DIR"
    for _script in rtc_setup.sh refresh-service.sh mdns-aliases.sh; do
        sudo install -o root -g root -m 0755 "$INSTALL_DIR/install/scripts/$_script" "$PRIV_DIR/$_script"
    done
    echo "$INSTALL_DIR" | sudo tee "$PRIV_DIR/INSTALL_DIR" >/dev/null
    sudo chmod 0644 "$PRIV_DIR/INSTALL_DIR"
    info "Privileged scripts installed in $PRIV_DIR (root-owned)."

    section "Sudo permissions"
    # Exact commands, with exact arguments wherever the app's are fixed: a bare
    # `/usr/bin/apt-get` or `/usr/bin/nmcli` lets the caller choose the options, and
    # both have options that run commands. `""` means "no arguments at all".
    SUDOERS_FILE="/etc/sudoers.d/splouch"
    sudo tee "$SUDOERS_FILE" >/dev/null <<EOF
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/timedatectl set-ntp true, /usr/bin/timedatectl set-ntp false, /usr/bin/timedatectl set-time *, /usr/bin/systemctl restart systemd-timesyncd, /usr/bin/systemctl restart splouch, /usr/sbin/reboot "", /usr/sbin/poweroff ""
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get update, /usr/bin/apt-get upgrade -y
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/nmcli dev wifi rescan, /usr/bin/nmcli radio wifi on, /usr/bin/nmcli radio wifi off, /usr/bin/nmcli connection delete -- *, /usr/bin/nmcli con mod splouch-eth *, /usr/bin/nmcli con add type ethernet ifname eth0 con-name splouch-eth *, /usr/bin/nmcli con up splouch-eth
$TARGET_USER ALL=(ALL) NOPASSWD: $PRIV_DIR/rtc_setup.sh enable, $PRIV_DIR/rtc_setup.sh disable, $PRIV_DIR/rtc_setup.sh status, $PRIV_DIR/refresh-service.sh ""
EOF
    sudo chmod 0440 "$SUDOERS_FILE"
    if sudo visudo -cf "$SUDOERS_FILE" >/dev/null; then
        info "Sudoers rules written to $SUDOERS_FILE"
    else
        # A sudoers file that does not parse breaks sudo for everyone on the box.
        sudo rm -f "$SUDOERS_FILE"
        error "The sudo rule did not validate and was removed — the panel's system actions will not work."
    fi

    section "Serial port access"
    if ! groups "$TARGET_USER" | grep -qw dialout; then
        sudo usermod -aG dialout "$TARGET_USER"
        warn "Added $TARGET_USER to 'dialout' group — takes effect after next login / reboot."
    else
        info "$TARGET_USER already in 'dialout' group."
    fi

    section "Data folders"
    as_user mkdir -p "$TARGET_HOME/SplouchData/meet" "$TARGET_HOME/SplouchData/images" \
        "$TARGET_HOME/SplouchData/icons" "$TARGET_HOME/SplouchData/recorded"
    # shellcheck disable=SC2088  # printed at the operator, never expanded — ~ is
    # what they see in the admin UI and the docs, so $HOME would read worse.
    info "~/SplouchData/{meet,images,icons,recorded} created."

    section "Settings"
    if [[ ! -f "$TARGET_HOME/SplouchData/settings.json" ]]; then
        as_user cp "$INSTALL_DIR/server/settings.default.json" "$TARGET_HOME/SplouchData/settings.json"
        as_user chmod 600 "$TARGET_HOME/SplouchData/settings.json" # holds the admin password
        info "settings.json copied from default."
    else
        info "settings.json already exists — skipping."
    fi

    # The realtime client (static/js/ws.js) ships with the repo — no download
    # needed since the move to plain WebSockets.

    section "xterm.js (browser terminal)"
    XTERM_VER="5.3.0"
    XTERM_JS="$INSTALL_DIR/shared/static/js/xterm.min.js"
    XTERM_CSS="$INSTALL_DIR/shared/static/css/xterm.min.css"
    if [[ ! -f "$XTERM_JS" ]]; then
        curl -fsSL "https://cdn.jsdelivr.net/npm/xterm@${XTERM_VER}/lib/xterm.min.js" -o "$XTERM_JS"
        curl -fsSL "https://cdn.jsdelivr.net/npm/xterm@${XTERM_VER}/css/xterm.css" -o "$XTERM_CSS"
        info "xterm.js ${XTERM_VER} downloaded."
    else
        info "xterm.js already present."
    fi

    section "Desktop shortcuts"
    mkdir -p "$TARGET_HOME/Desktop"

    cat >"$TARGET_HOME/Desktop/Splouch.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Splouch
Comment=Open the live scoreboard
Exec=xdg-open http://${SERVER_HOSTNAME}.local/live
Icon=video-display
Terminal=false
StartupNotify=false
EOF
    cat >"$TARGET_HOME/Desktop/Settings.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Settings
Comment=Open the scoreboard admin page
Exec=xdg-open http://${SERVER_HOSTNAME}.local/settings
Icon=preferences-system
Terminal=false
StartupNotify=false
EOF
    cat >"$TARGET_HOME/Desktop/Mobile.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Mobile
Comment=Open the mobile view
Exec=xdg-open http://${SERVER_HOSTNAME}.local/mobile
Icon=input-tablet
Terminal=false
StartupNotify=false
EOF
    cat >"$TARGET_HOME/Desktop/Reinstall.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Reinstall Splouch
Comment=Re-run the Splouch install script
Exec=lxterminal -e bash -c 'bash ${INSTALL_DIR}/install/install.sh; echo; read -rp "Press Enter to close…"'
Icon=system-software-install
Terminal=false
StartupNotify=false
EOF
    chmod +x "$TARGET_HOME/Desktop/Splouch.desktop" \
        "$TARGET_HOME/Desktop/Settings.desktop" \
        "$TARGET_HOME/Desktop/Mobile.desktop" \
        "$TARGET_HOME/Desktop/Reinstall.desktop"

    # Disable the "executable script" dialog in PCManFM/libfm
    mkdir -p "$TARGET_HOME/.config/libfm"
    if grep -q "quick_exec" "$TARGET_HOME/.config/libfm/libfm.conf" 2>/dev/null; then
        sed -i 's/quick_exec=.*/quick_exec=1/' "$TARGET_HOME/.config/libfm/libfm.conf"
    else
        echo -e "[config]\nquick_exec=1" >>"$TARGET_HOME/.config/libfm/libfm.conf"
    fi
    info "Desktop shortcuts created (Splouch, Settings, Mobile)."

    section "Chromium bookmarks"
    python3 - <<'PYEOF'
import json, os, uuid
from itertools import chain

path = os.path.expanduser('~/.config/chromium/Default/Bookmarks')
os.makedirs(os.path.dirname(path), exist_ok=True)

empty = {"checksum": "", "version": 1, "roots": {
    "bookmark_bar": {"id": "1", "type": "folder", "name": "Bookmarks bar",
                     "guid": "0bc5d13f-2cba-5d74-951f-3f233fe6c908",
                     "children": [], "date_added": "13270163645000000",
                     "date_last_used": "0", "date_modified": "0"},
    "other":        {"id": "2", "type": "folder", "name": "Other bookmarks",
                     "guid": "82b081ec-3dd3-529c-8475-ab6c344590dd",
                     "children": [], "date_added": "13270163645000000",
                     "date_last_used": "0", "date_modified": "0"},
    "synced":       {"id": "3", "type": "folder", "name": "Mobile bookmarks",
                     "guid": "4cf2e351-0e85-532b-bb37-df045d8f8d0f",
                     "children": [], "date_added": "13270163645000000",
                     "date_last_used": "0", "date_modified": "0"},
}}

data = json.load(open(path)) if os.path.exists(path) else empty

def all_ids(node):
    yield int(node.get('id', 0))
    for c in node.get('children', []):
        yield from all_ids(c)

next_id = max(chain(
    all_ids(data['roots']['bookmark_bar']),
    all_ids(data['roots']['other']),
    all_ids(data['roots']['synced']),
), default=0) + 1

bookmarks = [
    ("Splouch", "http://localhost:5000/live"),
    ("Settings",   "http://localhost:5000/settings"),
    ("Help",       "http://localhost:5000/help"),
]

bar = data['roots']['bookmark_bar']
existing = {c['url'] for c in bar.get('children', []) if c.get('type') == 'url'}

added = 0
for name, url in bookmarks:
    if url not in existing:
        bar.setdefault('children', []).append({
            "date_added": "13270163645000000", "date_last_used": "0",
            "guid": str(uuid.uuid4()), "id": str(next_id),
            "name": name, "type": "url", "url": url,
        })
        next_id += 1
        added += 1

json.dump(data, open(path, 'w'), indent=3)
print(f"Added {added} Chromium bookmark(s).")
PYEOF

    section "Desktop wallpaper"
    WALLPAPER="$INSTALL_DIR/shared/static/img/scoreboard_bg.png"
    if [[ -f "$WALLPAPER" ]]; then
        PCMANFM_CONF="$HOME/.config/pcmanfm/LXDE-pi"
        mkdir -p "$PCMANFM_CONF"
        # Set wallpaper for both monitor outputs (pcmanfm desktop config)
        for conf in "$PCMANFM_CONF/desktop-items-0.conf" "$PCMANFM_CONF/desktop-items-1.conf"; do
            cat >"$conf" <<WALLEOF
[*]
wallpaper_mode=fit
wallpaper_common=1
wallpaper=$WALLPAPER
WALLEOF
        done
        # Apply immediately if desktop is running
        pcmanfm --set-wallpaper "$WALLPAPER" --wallpaper-mode=fit 2>/dev/null || true
        info "Desktop wallpaper set to scoreboard_bg.png"
    else
        warn "Wallpaper image not found — skipping."
    fi

    section "systemd service"
    # splouch.service is generated by refresh-service.sh — the single source of
    # truth for the unit. The in-app update flow runs the same script before each
    # restart, so a changed entrypoint/layout self-heals instead of crash-looping
    # on a stale unit. See install/scripts/refresh-service.sh.
    sudo "$INSTALL_DIR/install/scripts/refresh-service.sh" splouch
    info "Service enabled (splouch.service). Serial port: $SERIAL_PORT"

    # Record which provisioning version this full install applied. The app
    # compares it against install/PROVISION_VERSION and nudges for a reinstall
    # when a change needs privileges/steps the in-app update can't self-apply.
    as_user cp "$INSTALL_DIR/install/PROVISION_VERSION" "$TARGET_HOME/SplouchData/.provisioned_version"
    warn "Change the serial port in the web UI if your adapter appears as a different device."

    section "VNC remote access"
    sudo apt-get install -y realvnc-vnc-server
    if command -v raspi-config &>/dev/null; then
        sudo raspi-config nonint do_vnc 0
        info "VNC enabled. Connect with RealVNC Viewer → ${SERVER_HOSTNAME}.local"
    else
        warn "raspi-config not found — enable VNC manually via: sudo raspi-config → Interface Options → VNC"
    fi

    section "Firewall"
    sudo ufw --force enable
    sudo ufw default deny incoming
    sudo ufw default allow outgoing
    sudo ufw allow in on eth0
    sudo ufw allow in on wlan0

    # Browsers upgrade a typed `splouch.local` to https and only fall back to http
    # when the https attempt fails *fast* — a TCP reset, i.e. connection refused.
    # Nothing here serves TLS, so the fast failure is what we want; the trap is that
    # ufw's `deny` policy is a silent DROP. On eth0/wlan0 the rules above let the SYN
    # through and the kernel resets it, but reached over any other path (a router, a
    # USB WiFi dongle that enumerates as wlxXXXX rather than wlan0) the SYN is dropped,
    # the browser hangs, and the server looks dead. `reject` sends the reset instead.
    # Ordered after the interface allows on purpose: those still match first for
    # pool-deck traffic, and the outcome is the same reset either way.
    sudo ufw reject 443/tcp comment "no TLS here — reset fast so browsers fall back to http"
    info "Firewall enabled — all incoming traffic allowed on eth0 and wlan0"
    info "Port 443 refused (not dropped) — https://${SERVER_HOSTNAME}.local falls back to http"

    section "Hostname"
    sudo hostnamectl set-hostname "$SERVER_HOSTNAME"
    if grep -q "127.0.1.1" /etc/hosts; then
        sudo sed -i "s/127\.0\.1\.1.*/127.0.1.1\t${SERVER_HOSTNAME}/" /etc/hosts
    else
        echo "127.0.1.1	${SERVER_HOSTNAME}" | sudo tee -a /etc/hosts >/dev/null
    fi
    info "Hostname set to ${SERVER_HOSTNAME} — device will appear as ${SERVER_HOSTNAME}.local"

    section "mDNS aliases"
    sudo apt-get install -y avahi-utils

    # Stop avahi from publishing IPv6 link-local (fe80::) records. On a
    # multihomed/WiFi Pi, browsers resolving splouch.local may prefer the
    # AAAA link-local address, which is unroutable without a zone index, so the
    # page fails to load even though IPv4 works fine.
    #   - use-ipv6=no            disables the IPv6 mDNS transport
    #   - publish-aaaa-on-ipv4=no stops the AAAA record being announced over
    #     IPv4 (this one defaults to YES and is the actual culprit)
    # See docs/troubleshooting.md
    _avahi_set() { # _avahi_set <key> <value> <section>
        if grep -q "^#*[[:space:]]*$1=" /etc/avahi/avahi-daemon.conf; then
            sudo sed -i "s/^#*[[:space:]]*$1=.*/$1=$2/" /etc/avahi/avahi-daemon.conf
        else
            sudo sed -i "/^\[$3\]/a $1=$2" /etc/avahi/avahi-daemon.conf
        fi
    }
    _avahi_set use-ipv6 no server
    _avahi_set publish-aaaa-on-ipv4 no publish
    sudo systemctl restart avahi-daemon

    # Translated aliases are published at the live interface IP, re-detected at
    # service start by mdns-aliases.sh — so they resolve on a DHCP setup instead
    # of the old install-time-baked static 10.10.10.10.
    sudo tee /etc/systemd/system/splouch-mdns-aliases.service >/dev/null <<EOF
[Unit]
Description=mDNS aliases for Splouch
After=avahi-daemon.service
Requires=avahi-daemon.service

[Service]
Type=simple
ExecStart=$PRIV_DIR/mdns-aliases.sh $MDNS_ALIASES
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now splouch-mdns-aliases
    info "mDNS aliases active: $MDNS_ALIASES → (live interface IP)"

    # A browsable service, not just names. The aliases above are A records: they
    # only help someone who already knows to type splouch.local. The phone apps
    # browse for `_splouch._tcp` instead and offer whatever answers, so a spectator
    # on the pool WiFi never types an address (docs/app.md `P-12`).
    # `kind` and `path` mirror GET /server so a client can list before it connects.
    sudo mkdir -p /etc/avahi/services
    sudo tee /etc/avahi/services/splouch.service >/dev/null <<EOF
<?xml version="1.0" standalone='no'?><!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Splouch on %h</name>
  <service>
    <type>_splouch._tcp</type>
    <port>5000</port>
    <txt-record>kind=pi</txt-record>
    <txt-record>path=/server</txt-record>
  </service>
</service-group>
EOF
    sudo systemctl restart avahi-daemon
    info "Discoverable as _splouch._tcp on port 5000"

    section "Port 80 redirect"
    sudo tee /etc/systemd/system/splouch-redirect.service >/dev/null <<EOF
[Unit]
Description=Splouch port 80 to 5000 redirect
After=network.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000 && iptables -t nat -A OUTPUT -p tcp --dport 80 -j REDIRECT --to-port 5000'
ExecStop=/bin/sh -c 'iptables -t nat -D PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5000; iptables -t nat -D OUTPUT -p tcp --dport 80 -j REDIRECT --to-port 5000'

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now splouch-redirect
    info "Port 80 redirects to 5000 — http://${SERVER_HOSTNAME}.local/ reaches the scoreboard"

    section "Real-time clock (Adafruit PiRTC DS3231)"
    echo "Adds a hardware clock so the Pi keeps accurate time without network access."
    if confirm "Install Adafruit PiRTC (DS3231) support now?"; then
        sudo bash "$INSTALL_DIR/install/scripts/rtc_setup.sh" enable
        info "RTC configured — will become active after reboot."
    else
        info "Skipping RTC setup — can be installed later from Settings → Clock."
    fi

    # Last question on purpose: an eth0 change ends the run with an automatic
    # reboot, so nothing may come after it that still needs an answer.
    section "Network — Pi #1"
    configure_network

    section "Done — Pi #1 (server)"
    echo
    echo -e "  Install dir : $INSTALL_DIR"
    echo -e "  Start server: ${BOLD}sudo systemctl start splouch${NC}"
    echo -e "  Logs        : ${BOLD}journalctl -u splouch -f${NC}"
    if [[ -n $STATIC_IP ]]; then
        echo -e "  Scoreboard  : ${BOLD}http://${SERVER_HOSTNAME}.local/${NC}  or  http://${STATIC_IP}/"
    else
        echo -e "  Scoreboard  : ${BOLD}http://${SERVER_HOSTNAME}.local/${NC}"
    fi
    echo -e "  Admin UI    : ${BOLD}http://${SERVER_HOSTNAME}.local/settings${NC}"
    echo -e "  Mobile view : ${BOLD}http://${SERVER_HOSTNAME}.local/mobile${NC}"
    echo -e "  Aliases     : $MDNS_ALIASES"
    echo -e "  Meet files  : ~/SplouchData/meet/*.lxf"
    echo -e "  Settings    : ~/SplouchData/settings.json"
    echo
    echo
    if ((ETH_PENDING)); then
        reboot_for_network
    elif confirm "Reboot now to apply group membership changes?"; then
        sudo reboot
    fi
fi

# Remove every autostart line this project has ever written, so a re-run replaces
# the display rather than launching a second one beside it.
#
# Three things get dropped, and the first is the one that bites:
#
#   * `# Splouch kiosk` — our own marker, from this installer or the Chromium one
#     that shipped under the same name.
#   * A bare `chromium … --kiosk … --app=` line, for one whose marker comment was
#     edited away. That flag pair is what the old installer wrote and nothing else
#     here writes, so an unrelated Chromium autostart on this Pi is left alone.
#
# awk rather than sed: deleting "a matched line and the one after it" needs GNU
# `addr,+1`, which is fine on the Pi but cannot be tested anywhere else.
strip_kiosk_autostart() {
    local file="$1"
    [[ -f "$file" ]] || return 0
    awk '
        skip      { skip = 0; next }
        /# Splouch kiosk/                       { skip = 1; next }
        /chromium.*--kiosk.*--app=/             { next }
        /start-scoreboard\.sh/                  { next }
        { print }
    ' "$file" >"$file.tmp" && mv "$file.tmp" "$file"
}

# ═══════════════════════════════════════════════════════════════════════════════
# KIOSK (Pi #2)
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$ROLE" == "kiosk" ]]; then

    section "Project"
    # The kiosk now runs code (the Qt scoreboard in scoreboard/) rather than a
    # browser pointed at a URL, so it needs the repo — and, crucially, the SAME
    # git ref as the server, so the two agree on the WebSocket contract.
    # See docs/architecture/native-app-strategy.md.
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-install.sh}")" 2>/dev/null && pwd)" || SCRIPT_DIR=""

    if [[ -n "$SCRIPT_DIR" && -f "$SCRIPT_DIR/../scoreboard/app.py" ]]; then
        INSTALL_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
        info "Running from project directory: $INSTALL_DIR"
        # Self-update before provisioning, exactly as the server role does, so a
        # re-run installs the latest display code rather than re-running whatever
        # stale copy is on disk. Skipped when the tree has local edits.
        if [[ -d "$INSTALL_DIR/.git" ]]; then
            # `HEAD --`, not a bare `--` — see the server role above.
            git -C "$INSTALL_DIR" checkout HEAD -- uv.lock 2>/dev/null || true
            if [[ -z "$(git -C "$INSTALL_DIR" status --porcelain 2>/dev/null)" ]]; then
                info "Fetching latest code before reinstalling…"
                fetch_and_ff "$INSTALL_DIR"
            else
                warn "Working tree has local changes — reinstalling on-disk code (no pull)."
            fi
        fi
    elif [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Updating existing repo at $INSTALL_DIR"
        fetch_and_ff "$INSTALL_DIR"
    else
        info "Cloning $REPO_URL → $INSTALL_DIR"
        git clone "$REPO_URL" "$INSTALL_DIR"
        git -C "$INSTALL_DIR" fetch --tags
    fi

    checkout_version "$INSTALL_DIR"

    ensure_uv

    section "Qt scoreboard dependencies"
    cd "$INSTALL_DIR"
    # Qt itself arrives in the PySide6 wheel, but that wheel links against the
    # platform's X11/Wayland client libraries, which are not bundled.
    #
    # The theme fonts ship with the repo as TTF (shared/static/fonts/) and the app
    # registers them itself, so no font packages are needed — fonts-dejavu-core is
    # only the last-resort monospace fallback if a theme names something we lack.
    sudo apt-get install -y libxcb-cursor0 libxkbcommon-x11-0 libgl1 \
        libxkbcommon0 libegl1 fonts-dejavu-core || true

    # --extra scoreboard pulls PySide6, which only the display role needs; the
    # server Pi and the cloud VM stay Qt-free (see pyproject.toml).
    if ! uv sync --extra scoreboard; then
        error "Failed to install the Qt dependencies."
        error "PySide6 needs 64-bit Raspberry Pi OS — reflash with the 64-bit image if this is a 32-bit install."
        exit 1
    fi
    if ! "$INSTALL_DIR/.venv/bin/python" -c 'import PySide6.QtWidgets' 2>/dev/null; then
        error "PySide6 installed but will not import — check the apt packages above."
        exit 1
    fi
    info "Virtual environment ready at $INSTALL_DIR/.venv (with PySide6)"

    section "Server address"
    # Which server this display follows. Kept outside the repo so a git pull or a
    # version switch never overwrites a site-specific address.
    SCOREBOARD_ENV="$TARGET_HOME/.config/splouch/scoreboard.env"
    mkdir -p "$(dirname "$SCOREBOARD_ENV")"
    if [[ ! -f "$SCOREBOARD_ENV" ]]; then
        printf '# Splouch scoreboard — server this display connects to.\nSPLOUCH_SERVER=%s\n' \
            "$SCOREBOARD_URL" >"$SCOREBOARD_ENV"
        info "Server address written to $SCOREBOARD_ENV ($SCOREBOARD_URL)"
    else
        info "Server address already set in $SCOREBOARD_ENV — keeping it."
    fi

    section "Desktop autologin"
    if command -v raspi-config &>/dev/null; then
        sudo raspi-config nonint do_boot_behaviour B4
        info "Desktop autologin enabled — kiosk will boot straight to the scoreboard."
    else
        warn "raspi-config not found — enable manually via: sudo raspi-config → System Options → Boot / Auto Login → Desktop Autologin"
    fi

    section "Display resolution (1080p)"
    if [[ -f /boot/firmware/config.txt ]]; then
        CONFIG_TXT="/boot/firmware/config.txt"
    else
        CONFIG_TXT="/boot/config.txt"
    fi
    # Appending a second copy would leave config.txt with the HDMI mode set twice.
    if ! grep -q "# Splouch kiosk" "$CONFIG_TXT"; then
        sudo tee -a "$CONFIG_TXT" >/dev/null <<EOF

# Splouch kiosk — force 1920x1080 HDMI output
hdmi_force_hotplug=1
hdmi_group=2
hdmi_mode=82
EOF
        info "HDMI forced to 1920x1080 (DMT mode 82) — takes effect after reboot."
    else
        info "Display resolution already configured — skipping."
    fi

    section "Scoreboard autostart"
    # A launcher script rather than an inline command: it resolves the repo and
    # the venv from its own location, so moving or re-cloning the checkout never
    # leaves a stale autostart line behind. It also restarts the app if it exits.
    KIOSK_CMD="$INSTALL_DIR/install/scripts/start-scoreboard.sh"
    chmod +x "$KIOSK_CMD"

    # Raspberry Pi OS Bookworm/Trixie — Wayland session via labwc
    LABWC_AUTOSTART="$HOME/.config/labwc/autostart"
    mkdir -p "$(dirname "$LABWC_AUTOSTART")"
    touch "$LABWC_AUTOSTART"
    strip_kiosk_autostart "$LABWC_AUTOSTART"
    printf '\n# Splouch kiosk\n%s &\n' "$KIOSK_CMD" >>"$LABWC_AUTOSTART"

    # Older Raspberry Pi OS releases — LXDE / X11 session
    AUTOSTART_DIR=/etc/xdg/lxsession/LXDE-pi
    sudo mkdir -p "$AUTOSTART_DIR"
    sudo tee "$AUTOSTART_DIR/autostart" >/dev/null <<EOF
@xset s off
@xset -dpms
@xset s noblank
@$KIOSK_CMD
EOF
    info "Kiosk autostart configured (Qt scoreboard) → $SCOREBOARD_URL"

    section "Desktop shortcuts"
    # Ctrl+Q quits the scoreboard to the desktop; this icon is how it gets back.
    # Without it the only way to restart the display is an SSH session.
    mkdir -p "$TARGET_HOME/Desktop"
    cat >"$TARGET_HOME/Desktop/Scoreboard.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Scoreboard
Comment=Start the Splouch TV scoreboard
Exec=$KIOSK_CMD
Icon=video-display
Terminal=false
StartupNotify=false
EOF
    cat >"$TARGET_HOME/Desktop/Settings.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Settings
Comment=Open the scoreboard admin page on the server
Exec=xdg-open ${SCOREBOARD_URL}/settings
Icon=preferences-system
Terminal=false
StartupNotify=false
EOF
    chmod +x "$TARGET_HOME/Desktop/Scoreboard.desktop" \
        "$TARGET_HOME/Desktop/Settings.desktop"

    # Disable PCManFM's "execute this file?" dialog so one double-click launches.
    mkdir -p "$TARGET_HOME/.config/libfm"
    if grep -q "quick_exec" "$TARGET_HOME/.config/libfm/libfm.conf" 2>/dev/null; then
        sed -i 's/quick_exec=.*/quick_exec=1/' "$TARGET_HOME/.config/libfm/libfm.conf"
    else
        echo -e "[config]\nquick_exec=1" >>"$TARGET_HOME/.config/libfm/libfm.conf"
    fi
    info "Desktop shortcuts created (Scoreboard, Settings)."

    section "Desktop wallpaper"
    # Now served from the checkout, like the server role — the kiosk has the repo.
    WALLPAPER="$INSTALL_DIR/shared/static/img/scoreboard_bg.png"
    if [[ -f "$WALLPAPER" ]]; then
        PCMANFM_CONF="$HOME/.config/pcmanfm/LXDE-pi"
        mkdir -p "$PCMANFM_CONF"
        for conf in "$PCMANFM_CONF/desktop-items-0.conf" "$PCMANFM_CONF/desktop-items-1.conf"; do
            cat >"$conf" <<WALLEOF
[*]
wallpaper_mode=fit
wallpaper_common=1
wallpaper=$WALLPAPER
WALLEOF
        done
        pcmanfm --set-wallpaper "$WALLPAPER" --wallpaper-mode=fit 2>/dev/null || true
        info "Desktop wallpaper set to scoreboard_bg.png"
    else
        warn "Wallpaper image not found — skipping."
    fi

    section "VNC remote access"
    sudo apt-get install -y realvnc-vnc-server
    if command -v raspi-config &>/dev/null; then
        sudo raspi-config nonint do_vnc 0
        info "VNC enabled. Connect with RealVNC Viewer → (DHCP — check router for kiosk IP)"
    else
        warn "raspi-config not found — enable VNC manually via: sudo raspi-config → Interface Options → VNC"
    fi

    section "Firewall"
    sudo ufw --force enable
    sudo ufw default deny incoming
    sudo ufw default allow outgoing
    sudo ufw allow in on eth0
    sudo ufw allow in on wlan0
    info "Firewall enabled — all incoming traffic allowed on eth0 and wlan0"

    section "Done — Pi #2 (kiosk)"
    echo
    echo -e "  Display     : ${BOLD}Qt scoreboard${NC} → $SCOREBOARD_URL"
    echo -e "  Project dir : $INSTALL_DIR"
    echo -e "  Server addr : $SCOREBOARD_ENV"
    echo -e "  Quit to desktop : ${BOLD}Ctrl+Q${NC}   ·  Fullscreen: ${BOLD}F11${NC} or ${BOLD}Ctrl+F${NC}  ·  Leave fullscreen: ${BOLD}Esc${NC}"
    echo -e "  Reopen      : ${BOLD}Scoreboard${NC} icon on the desktop"
    echo -e "  Run by hand : ${BOLD}$INSTALL_DIR/install/scripts/start-scoreboard.sh${NC}"
    echo -e "  Windowed    : ${BOLD}cd $INSTALL_DIR && .venv/bin/python -m scoreboard --windowed${NC}"
    echo
    warn "Pi #1 (server) must be running and reachable at ${SERVER_HOSTNAME}.local before the kiosk boots."
    echo
    confirm "Reboot now?" && sudo reboot
fi

# ═══════════════════════════════════════════════════════════════════════════════
# CLOUD (Debian 12+ / Ubuntu 24.04+ VM — public relay server)
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$ROLE" == "cloud" ]]; then

    # ── Python check ──────────────────────────────────────────────────────────
    # The relay runs in Docker on its own Python, so the OS barely matters. The one
    # thing that runs on the VM's python3 is the deploy webhook, which needs 3.11
    # for `tomllib`: Debian 12 and Ubuntu 24.04 have it, Ubuntu 22.04 (3.10) does
    # not (docs/cloud.md). Checked before the root bootstrap below locks root out,
    # so a VM that cannot run the webhook is left exactly as it was found.
    check_cloud_python() {
        if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
            error "The cloud server needs python3 3.11 or newer (Debian 12+, Ubuntu 24.04+);" \
                "this VM has $(python3 --version 2>/dev/null || echo 'no python3')."
            exit 1
        fi
    }
    if command -v python3 &>/dev/null; then
        check_cloud_python
    else
        info "No python3 yet — installing the distribution's, then checking its version."
    fi

    # ── User bootstrap (runs once as root on a fresh server) ──────────────────
    if [[ "$(id -u)" == "0" ]]; then
        section "User setup"
        # Asked rather than fixed: a well-known account name is the first one a
        # scanner tries. Root SSH and password SSH are off below either way.
        while true; do
            read -rp "Name of the account to create (or reuse) for running Splouch: " CLOUD_USER
            if [[ "$CLOUD_USER" =~ ^[a-z_][a-z0-9_-]{0,31}$ && "$CLOUD_USER" != "root" ]]; then
                break
            fi
            warn "Use lowercase letters, digits, '-' or '_' (not 'root'), starting with a letter."
        done

        if ! id "$CLOUD_USER" &>/dev/null; then
            useradd -m -s /bin/bash "$CLOUD_USER"
            info "User '$CLOUD_USER' created."
        else
            info "User '$CLOUD_USER' already exists."
        fi

        usermod -aG sudo "$CLOUD_USER"

        # Set a password so the account can use sudo normally after the install
        echo
        while true; do
            read -rsp "Set a password for '$CLOUD_USER': " _pw1
            echo
            read -rsp "Confirm password: " _pw2
            echo
            if [[ "$_pw1" == "$_pw2" && -n "$_pw1" ]]; then
                echo "$CLOUD_USER:$_pw1" | chpasswd
                info "Password set for '$CLOUD_USER'."
                unset _pw1 _pw2
                break
            fi
            warn "Passwords did not match or were empty — try again."
        done

        # Passwordless sudo only for the duration of the install. Its own file,
        # named for what it is: the cleanup at the end of this role deletes it by
        # name, and a grant this broad must not be able to hide behind a filename
        # that looks like a permanent part of the install.
        echo "$CLOUD_USER ALL=(ALL) NOPASSWD:ALL" >"$TEMP_SUDOERS_FILE"
        chmod 0440 "$TEMP_SUDOERS_FILE"
        info "Temporary NOPASSWD sudo granted for install."

        # Copy root's SSH authorized_keys so the server stays reachable
        if [[ -f /root/.ssh/authorized_keys ]]; then
            install -d -m 700 -o "$CLOUD_USER" -g "$CLOUD_USER" \
                "$(getent passwd "$CLOUD_USER" | cut -d: -f6)/.ssh"
            install -m 600 -o "$CLOUD_USER" -g "$CLOUD_USER" \
                /root/.ssh/authorized_keys \
                "$(getent passwd "$CLOUD_USER" | cut -d: -f6)/.ssh/authorized_keys"
            info "SSH authorized_keys copied from root → '$CLOUD_USER' can log in via SSH."
        else
            warn "No /root/.ssh/authorized_keys — configure SSH access for '$CLOUD_USER' manually."
        fi

        # Harden root access
        passwd -l root
        info "Root password locked."
        sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
        sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
        systemctl restart ssh
        info "Root SSH login disabled, password auth disabled — key-only SSH from now on."

        # Copy this script to the account's home and re-exec as that user
        _script_src="$(realpath "${BASH_SOURCE[0]}")"
        _script_dst="$(getent passwd "$CLOUD_USER" | cut -d: -f6)/install.sh"
        install -m 755 -o "$CLOUD_USER" -g "$CLOUD_USER" \
            "$_script_src" "$_script_dst"
        info "Re-running install as '$CLOUD_USER'…"
        # SPLOUCH_TARGET_USER is not optional here. The re-exec'd run resolves the
        # target user again at the top of this script, and by then `SUDO_USER` names
        # the user who *invoked* sudo — root — not the one sudo switched to. Without
        # this, the second run computes TARGET_HOME=/root and tries to clone into
        # /root/Splouch as an unprivileged user: "could not create work tree dir".
        #
        # Passed through `env` rather than as `sudo VAR=value`, which sudo refuses
        # unless the sudoers entry carries `setenv`.
        exec sudo -H -u "$CLOUD_USER" \
            env SPLOUCH_TARGET_USER="$CLOUD_USER" \
            bash "$_script_dst" cloud "$VERSION_CHOICE"
    fi
    # ──────────────────────────────────────────────────────────────────────────

    section "System packages"
    ensure_https_apt_sources
    sudo apt-get update -qq
    sudo apt-get upgrade -y
    sudo apt-get install -y git curl python3 fail2ban unattended-upgrades
    check_cloud_python

    section "fail2ban"
    sudo tee /etc/fail2ban/jail.d/sshd.local >/dev/null <<'EOF'
[sshd]
enabled  = true
maxretry = 5
bantime  = 1h
findtime = 10m
EOF
    sudo systemctl enable --now fail2ban
    info "fail2ban enabled — SSH: 5 failures in 10 min → 1 h ban."

    section "Automatic security updates"
    sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
    info "Unattended security upgrades enabled."

    section "Docker"
    if ! command -v docker &>/dev/null; then
        curl -fsSL https://get.docker.com | sh
        sudo usermod -aG docker "$USER"
        info "Docker installed. You may need to log out and back in for group membership to take effect."
    else
        info "Docker already installed: $(docker --version)"
    fi

    section "Project"
    if [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Updating existing repo at $INSTALL_DIR"
        fetch_and_ff "$INSTALL_DIR"
    else
        info "Cloning $REPO_URL → $INSTALL_DIR"
        git clone "$REPO_URL" "$INSTALL_DIR"
        git -C "$INSTALL_DIR" fetch --tags
    fi
    checkout_version "$INSTALL_DIR"

    CLOUD_DIR="$INSTALL_DIR/cloud"

    section "Environment file"
    # Rewrite one KEY=value line without sed: the admin password is user input, and
    # in a sed replacement a `/` ends the expression while `&` expands to the whole
    # match — so a perfectly good password could be silently mangled into a
    # different one, or corrupt the file. python does the substitution literally.
    _set_env() {
        python3 - "$CLOUD_DIR/.env" "$1" "$2" <<'PYEOF'
import sys
path, key, value = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path, encoding='utf-8').read().splitlines()
out, done = [], False
for line in lines:
    if line.startswith(key + '='):
        out.append(key + '=' + value); done = True
    else:
        out.append(line)
if not done:
    out.append(key + '=' + value)
open(path, 'w', encoding='utf-8').write('\n'.join(out) + '\n')
PYEOF
    }

    _env_created=0
    if [[ ! -f "$CLOUD_DIR/.env" ]]; then
        _env_created=1
        # Create it empty and locked down *before* any secret goes in. Copying the
        # template first would leave the file at the umask default (world-readable)
        # for the window in which SECRET_KEY, ADMIN_PASSWORD and DEPLOY_SECRET are
        # written into it.
        install -m 600 /dev/null "$CLOUD_DIR/.env"
        cat "$CLOUD_DIR/.env.example" >"$CLOUD_DIR/.env"
        SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        _set_env SECRET_KEY "$SECRET"

        echo
        # Both typed values are written single-quoted. docker compose expands `$NAME`
        # in an unquoted .env value — so a password beginning `$abc` reached the
        # control plane as an empty string and seeded /admin with no password — and
        # ` #` starts a comment. Inside single quotes nothing is expanded, which leaves
        # the quote itself as the one character the value cannot hold.
        while true; do
            read -rp "Set the admin username for the /admin panel [admin]: " _au
            _au="${_au:-admin}"
            [[ "$_au" =~ ^[A-Za-z0-9._@-]+$ ]] && break
            warn "Letters, digits and . _ @ - only — try again."
        done
        _set_env ADMIN_USER "'$_au'"
        info "ADMIN_USER set to '${_au}'."

        while true; do
            read -rsp "Set the admin password for the /admin panel: " _ap1
            echo
            read -rsp "Confirm admin password: " _ap2
            echo
            if [[ "$_ap1" == *"'"* ]]; then
                warn "The password cannot contain a single quote (') — try again."
            elif [[ "$_ap1" == "$_ap2" && -n "$_ap1" ]]; then
                _set_env ADMIN_PASSWORD "'$_ap1'"
                info "ADMIN_PASSWORD set."
                unset _ap1 _ap2
                break
            else
                warn "Passwords did not match or were empty — try again."
            fi
        done

        info "Created $CLOUD_DIR/.env with generated SECRET_KEY, ADMIN_USER, and ADMIN_PASSWORD."
    else
        info ".env already exists — skipping."
    fi
    # Also repairs an .env from an earlier install, which was created by `cp` and
    # left at 0644 with three secrets in it.
    chmod 600 "$CLOUD_DIR/.env"

    # The control plane's database password and the workers' shared secret. Filled
    # in on an .env from before the scaling split too (docs/cloud.md): compose
    # refuses to start without them, so re-running the installer is the upgrade.
    for _secret_key in POSTGRES_PASSWORD NODE_SECRET GRAFANA_PASSWORD; do
        if ! grep -q "^${_secret_key}=." "$CLOUD_DIR/.env" \
            || grep -q "^${_secret_key}=change_me$" "$CLOUD_DIR/.env"; then
            _set_env "$_secret_key" "$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")"
            info "${_secret_key} generated and saved to .env"
        fi
    done

    section "Parts"
    # What this server runs (docs/architecture/scaling.md): everything at first,
    # one part per server later. Asked once; ROLES in .env is the answer after that.
    # A fresh .env counts as unanswered: it carries .env.example's ROLES line. On a
    # re-run, the current answer is shown, with the settings that go with it, and
    # kept unless the operator asks to change it.
    _ask_parts=0
    if ((_env_created)) || ! grep -q '^ROLES=.' "$CLOUD_DIR/.env"; then
        _ask_parts=1
    elif [[ "${SPLOUCH_NONINTERACTIVE:-}" != "1" ]]; then
        echo "Current settings in $CLOUD_DIR/.env:"
        for _k in ROLES MONITORING CONTROL_URL NODE_NAME NODE_REGION REMOTE_NODES WG_ADDRESS WG_HUB SPLOUCH_DOMAIN; do
            _v=$(sed -n "s/^${_k}=//p" "$CLOUD_DIR/.env" | tail -1)
            [[ -n "$_v" ]] && printf '  %-14s %s\n' "$_k" "$_v"
        done
        confirm "Change the parts this server runs?" && _ask_parts=1
    fi
    if ((_ask_parts)) && [[ "${SPLOUCH_NONINTERACTIVE:-}" != "1" ]]; then
        echo "Which parts does this server run?"
        PS3="Type a number and press Enter: "
        select _parts in \
            "Everything (control plane + relay workers)" \
            "Everything + monitoring (a single server with its own Grafana)" \
            "Relay workers only (a regional node)" \
            "Control plane only" \
            "Control plane + monitoring" \
            "Monitoring only"; do
            case "$_parts" in
                "Everything + monitoring"*) _roles="control,workers,monitoring" ;;
                Everything*) _roles="control,workers" ;;
                "Relay workers"*) _roles="workers" ;;
                "Control plane only") _roles="control" ;;
                "Control plane + monitoring") _roles="control,monitoring" ;;
                Monitoring*) _roles="monitoring" ;;
                *) continue ;;
            esac
            break
        done
        _set_env ROLES "$_roles"
        info "ROLES=${_roles}"
    fi
    _roles=$(sed -n 's/^ROLES=//p' "$CLOUD_DIR/.env" | tail -1)
    _roles="${_roles:-control,workers}"

    if [[ ",$_roles," != *",control,"* && ",$_roles," == *",workers,"* ]] \
        && ! grep -q '^CONTROL_URL=.' "$CLOUD_DIR/.env"; then
        # A regional node: it reaches the control plane over HTTPS, with the shared
        # secret from the control plane's own .env.
        read -rp "Control plane address (e.g. https://splouch.org): " _control_url
        read -rsp "NODE_SECRET from the control plane's cloud/.env: " _node_secret
        echo
        read -rp "This node's name (e.g. us1): " _node_name
        read -rp "This node's region (ca, us, mx or eu): " _node_region
        _set_env CONTROL_URL "${_control_url%/}"
        _set_env PICKER_URL "${_control_url%/}/"
        _set_env NODE_SECRET "$_node_secret"
        _set_env NODE_NAME "$_node_name"
        _set_env NODE_REGION "$_node_region"
        unset _node_secret
        info "Node ${_node_name} (${_node_region}) will report to ${_control_url}."
    fi

    if [[ ",$_roles," == *",control,"* && ",$_roles," != *",workers,"* ]]; then
        # A control plane with no workers of its own serves only remote ones.
        _set_env REMOTE_NODES 1
    fi

    if [[ "${SPLOUCH_NONINTERACTIVE:-}" != "1" ]]; then
        # WireGuard carries monitoring between servers and nothing else.
        if [[ ",$_roles," == *",monitoring,"* ]] && ! grep -q '^WG_ADDRESS=.' "$CLOUD_DIR/.env"; then
            echo "Monitoring runs on this server. WireGuard is only for collecting metrics"
            echo "from OTHER servers (regional nodes); a single server does not need it."
            if confirm "Will other servers send their metrics here over WireGuard?"; then
                sudo bash "$INSTALL_DIR/install/scripts/wireguard.sh" hub
                _set_env WG_ADDRESS 10.73.0.1
            fi
        elif [[ ",$_roles," != *",monitoring,"* ]] && ! grep -q '^WG_HUB=.' "$CLOUD_DIR/.env"; then
            echo "This server runs no monitoring (ROLES=${_roles}). Monitoring on this same server"
            echo "needs no WireGuard: answer No, add ',monitoring' to ROLES in cloud/.env, run Update."
            if confirm "Is there a SEPARATE monitoring server this one should send its metrics to?"; then
                read -rp "This server's WireGuard address (10.73.0.2 – 10.73.0.254): " _wg_address
                read -rp "Monitoring server's host name or address: " _wg_host
                read -rp "Monitoring server's WireGuard public key: " _wg_key
                sudo bash "$INSTALL_DIR/install/scripts/wireguard.sh" node "$_wg_address" "$_wg_host" "$_wg_key"
                _set_env WG_HUB 10.73.0.1
            fi
        fi
    fi

    section "Domain"
    # The domain lives in .env, never in the Caddyfile. That file is tracked, so an
    # update that resets the working tree would revert a literal domain written there
    # and leave Caddy serving the placeholder from its next restart.
    #
    # Asked here, before the deploy webhook below is enabled, and not at the end where
    # it used to sit: systemd reads that unit's EnvironmentFile=.../cloud/.env once, at
    # start, and `docker compose` gives the environment it inherits precedence over the
    # .env file it reads itself. A domain written after the unit had started was
    # therefore overridden by the placeholder on the very first Update — the other half
    # of this is _deploy_env() in cloud/deploy_webhook.py.
    _current_domain=$(sed -n 's/^SPLOUCH_DOMAIN=//p' "$CLOUD_DIR/.env" | tail -1)
    # .env.example ships the placeholder, so a fresh .env arrives with the key already
    # set. Treat it as unset: otherwise the prompt offers to "keep current" and a bare
    # Enter has Caddy ask Let's Encrypt for scores.example.com.
    [[ "$_current_domain" == "scores.example.com" ]] && _current_domain=""
    if [[ -z "$_current_domain" ]]; then
        # Pre-existing install: lift the domain out of the Caddyfile, where earlier
        # versions of this script sed-ed it in, so nobody has to retype it.
        _caddy_domain=$(grep -E '^[^#[:space:]]+[[:space:]]*\{' "$CLOUD_DIR/Caddyfile" \
            | awk '{print $1}' | head -1)
        if [[ "$_caddy_domain" != '{$SPLOUCH_DOMAIN}' && "$_caddy_domain" != "scores.example.com" ]]; then
            _current_domain="$_caddy_domain"
            [[ -n "$_current_domain" ]] && info "Recovered domain from Caddyfile: $_current_domain"
        fi
    fi
    echo
    echo "  Current domain: ${_current_domain:-not set}"
    read -rp "  Enter domain name (leave blank to keep current): " _domain
    _domain="${_domain:-$_current_domain}"
    if [[ -z "$_domain" ]]; then
        warn "No domain set — Caddy cannot obtain a certificate and will refuse to start."
    fi
    if grep -q '^SPLOUCH_DOMAIN=' "$CLOUD_DIR/.env"; then
        sed -i "s|^SPLOUCH_DOMAIN=.*|SPLOUCH_DOMAIN=${_domain}|" "$CLOUD_DIR/.env"
    else
        echo "SPLOUCH_DOMAIN=${_domain}" >>"$CLOUD_DIR/.env"
    fi
    info "Domain set in .env: ${_domain:-<unset>}"

    section "Deploy webhook"
    if grep -q "^DEPLOY_SECRET=change_me" "$CLOUD_DIR/.env" 2>/dev/null \
        || ! grep -q "^DEPLOY_SECRET=" "$CLOUD_DIR/.env" 2>/dev/null; then
        _deploy_secret=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        if grep -q "^DEPLOY_SECRET=" "$CLOUD_DIR/.env" 2>/dev/null; then
            sed -i "s/^DEPLOY_SECRET=.*$/DEPLOY_SECRET=${_deploy_secret}/" "$CLOUD_DIR/.env"
        else
            echo "DEPLOY_SECRET=${_deploy_secret}" >>"$CLOUD_DIR/.env"
        fi
        info "DEPLOY_SECRET generated and saved to .env"
    else
        info "DEPLOY_SECRET already set in .env — keeping existing value."
    fi
    sed \
        -e "s|YOUR_INSTALL_DIR|${INSTALL_DIR}|g" \
        -e "s|YOUR_USER|${USER}|g" \
        "$CLOUD_DIR/deploy_webhook.service" \
        >/tmp/deploy-webhook.service
    sudo install -m 644 /tmp/deploy-webhook.service /etc/systemd/system/deploy-webhook.service
    rm /tmp/deploy-webhook.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now deploy-webhook
    info "Deploy webhook enabled on port 9000 — powers the Update button in /admin."
    sudo ufw allow from 172.16.0.0/12 to any port 9000 comment "Docker → deploy webhook"
    info "ufw: Docker bridge networks (172.16/12) allowed to reach port 9000."

    # Allow the webhook process to restart itself after a deploy (no password prompt)
    echo "${USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart deploy-webhook" \
        | sudo tee /etc/sudoers.d/splouch-webhook >/dev/null
    sudo chmod 0440 /etc/sudoers.d/splouch-webhook
    info "Sudoers rule added: deploy-webhook can self-restart without a password."

    section "Firewall"
    if command -v ufw &>/dev/null; then
        sudo ufw --force enable
        sudo ufw default deny incoming
        sudo ufw default allow outgoing
        sudo ufw allow 22/tcp  # SSH
        sudo ufw allow 80/tcp  # HTTP  (Caddy ACME challenge + redirect)
        sudo ufw allow 443/tcp # HTTPS
        sudo ufw allow 443/udp # HTTP/3
        info "Firewall enabled — ports 22, 80, 443 open."
    else
        warn "ufw not found — configure firewall manually (open ports 22, 80, 443)."
    fi

    section "Start"
    cd "$CLOUD_DIR"
    # cloud_deploy.py sizes the worker set (one per spare core), pulls the image CI
    # published for this checkout — the release tag it sits on, else master — or
    # builds it here when there is none, and starts everything.
    # A branch or commit other than master has no published image: built here,
    # tagged `local-<short commit>` like the webhook's branch builds.
    _version=$(git -C "$INSTALL_DIR" describe --tags --exact-match HEAD 2>/dev/null || true)
    if [[ -n "$_version" ]]; then
        _deploy_args="$_version"
    elif [[ "$(git -C "$INSTALL_DIR" rev-parse HEAD)" == "$(git -C "$INSTALL_DIR" rev-parse origin/master 2>/dev/null)" ]]; then
        _version=master _deploy_args=master
    else
        _version="local-$(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
        _deploy_args="--build $_version"
    fi
    sg docker -c "python3 cloud_deploy.py $_deploy_args"
    info "Cloud server started ($_version)."

    # Remove the temporary NOPASSWD rule — sudo now requires the password set above.
    # The result is checked rather than announced: an earlier version of this deleted
    # a file that was never written and reported success either way.
    #
    # One `sudo` for the whole thing, on purpose: it is the grant being removed that
    # makes these commands passwordless, so a second call after the file is gone
    # would sit at a password prompt in the middle of an unattended install. The
    # check on .../splouch matches on content because that filename holds the
    # *legitimate* restricted grant on a server-role machine.
    if sudo sh -c '
            rm -f "$1"
            if grep -qs "NOPASSWD:ALL" /etc/sudoers.d/splouch; then
                rm -f /etc/sudoers.d/splouch
                echo "removed-legacy"
            fi
            ! grep -rqs "NOPASSWD:ALL" /etc/sudoers.d/
        ' _ "$TEMP_SUDOERS_FILE"; then
        info "Temporary NOPASSWD sudo rule removed."
    else
        warn "Could not remove the temporary NOPASSWD sudo rule. Remove it by hand,"
        warn "after checking that '$USER' can still sudo with its password:"
        warn "  sudo rm -f $TEMP_SUDOERS_FILE /etc/sudoers.d/splouch"
    fi

    section "Done — Cloud server"
    echo
    echo -e "  Install dir  : $INSTALL_DIR"
    echo -e "  Cloud dir    : $CLOUD_DIR"
    echo -e "  Logs         : ${BOLD}cd $CLOUD_DIR && docker compose logs -f${NC}"
    _final_domain=$(sed -n 's/^SPLOUCH_DOMAIN=//p' "$CLOUD_DIR/.env" | tail -1)
    echo -e "  Admin UI     : ${BOLD}https://${_final_domain}/admin${NC}"
    echo -e "  Update       : ${BOLD}Update button in /admin${NC}  (or: cd $INSTALL_DIR && git pull && cd cloud && docker compose up -d --build)"
    echo
    echo
fi
