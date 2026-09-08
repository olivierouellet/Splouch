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
SERVER_IP="10.10.10.10/24"
KIOSK_GATEWAY="10.0.0.1"
SERVER_HOSTNAME="splouch"                   # broadcasts as splouch.local on the network
MDNS_ALIASES="tableau.local marcador.local tremplin.local"  # translated aliases + legacy name for old bookmarks
SCOREBOARD_URL="http://${SERVER_HOSTNAME}.local"
SERIAL_PORT="/dev/ttyUSB0"
# ──────────────────────────────────────────────────────────────────────────────

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
section() { echo -e "\n${BOLD}──── $* ────${NC}"; }
# Auto-answer No in non-interactive mode (the in-app Reinstall has no TTY), so
# optional prompts (static IP, RTC, reboot) safely keep the current config.
confirm() {
    if [[ "${SPLOUCH_NONINTERACTIVE:-}" == "1" ]]; then
        info "Non-interactive — skipping: $1"
        return 1
    fi
    read -rp "$1 [y/N] " _r; [[ "${_r:-}" =~ ^[Yy]$ ]]
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
# See docs/troubleshooting-apt-http-proxy.md
ensure_https_apt_sources() {
    sudo sed -i \
        -e 's|http://deb.debian.org|https://deb.debian.org|g' \
        -e 's|http://archive.raspberrypi.com|https://archive.raspberrypi.com|g' \
        -e 's|http://security.debian.org|https://security.debian.org|g' \
        /etc/apt/sources.list \
        /etc/apt/sources.list.d/*.list \
        /etc/apt/sources.list.d/*.sources 2>/dev/null || true
}

# ── Role selection ─────────────────────────────────────────────────────────────
ROLE="${1:-}"
if [[ -z "$ROLE" && "${SPLOUCH_NONINTERACTIVE:-}" == "1" ]]; then ROLE="server"; fi
if [[ -z "$ROLE" ]]; then
    echo
    echo "Which role is this?"
    PS3="Choice: "
    select _choice in \
        "Server  (Pi #1 — pool deck, FastAPI + serial decoder)" \
        "Kiosk   (Pi #2 — TV display, Chromium fullscreen)" \
        "Cloud   (Debian VM — public relay server)" \
        "Quit"; do
        case "$_choice" in
            Server*) ROLE="server"; break ;;
            Kiosk*)  ROLE="kiosk";  break ;;
            Cloud*)  ROLE="cloud";  break ;;
            Quit)    exit 0 ;;
        esac
    done
fi

if [[ "$ROLE" != "server" && "$ROLE" != "kiosk" && "$ROLE" != "cloud" ]]; then
    error "Unknown role '$ROLE'. Use 'server', 'kiosk', or 'cloud'."
    exit 1
fi
info "Role: $ROLE"

# ── Version selection ─────────────────────────────────────────────────────────
VERSION_CHOICE="${2:-}"
if [[ -z "$VERSION_CHOICE" && "${SPLOUCH_NONINTERACTIVE:-}" == "1" ]]; then VERSION_CHOICE="master"; fi
if [[ -z "$VERSION_CHOICE" ]]; then
    echo
    echo "Which version to install?"
    PS3="Choice: "
    select _choice in \
        "Latest release (recommended)" \
        "Master (development branch)"; do
        case "$_choice" in
            Latest*) VERSION_CHOICE="latest"; break ;;
            Master*) VERSION_CHOICE="master"; break ;;
        esac
    done
fi

# ── System packages ────────────────────────────────────────────────────────────
section "System packages"
ensure_https_apt_sources
sudo apt-get update -qq
sudo apt-get upgrade -y
sudo apt-get install -y git curl ufw

# ── Static IP helper ───────────────────────────────────────────────────────────
configure_static_ip() {
    local ip="$1" gateway="${2:-}"

    echo
    warn "About to set eth0 to static IP ${ip%/*}."
    warn "If you are connected via SSH over Ethernet this will disconnect you."
    confirm "Configure static IP now?" || { info "Skipping network configuration."; return 0; }

    if systemctl is-active --quiet dhcpcd 2>/dev/null; then
        # Raspberry Pi OS Bullseye — dhcpcd
        local conf=/etc/dhcpcd.conf
        if ! grep -q "# Splouch" "$conf" 2>/dev/null; then
            {
                printf '\n# Splouch\ninterface eth0\nstatic ip_address=%s\n' "$ip"
                [[ -n "$gateway" ]] && printf 'static routers=%s\n' "$gateway"
            } | sudo tee -a "$conf" > /dev/null
        else
            warn "dhcpcd.conf already has a Splouch entry — skipping."
        fi
        sudo systemctl restart dhcpcd

    elif command -v nmcli &>/dev/null; then
        # Raspberry Pi OS Bookworm — NetworkManager
        local con="splouch-eth"
        local -a args=(type ethernet ifname eth0 con-name "$con"
            ipv4.method manual ipv4.addresses "$ip"
            connection.autoconnect yes)
        [[ -n "$gateway" ]] && args+=(ipv4.gateway "$gateway")

        if nmcli con show "$con" &>/dev/null; then
            sudo nmcli con mod "$con" ipv4.addresses "$ip" \
                ${gateway:+ipv4.gateway "$gateway"}
        else
            sudo nmcli con add "${args[@]}"
        fi
        sudo nmcli con up "$con"

    else
        warn "Cannot detect network manager (no dhcpcd or nmcli). Configure static IP manually."
        return 0
    fi

    info "Static IP configured: ${ip%/*}"
}

# ── Version checkout ───────────────────────────────────────────────────────────
# Shared by the server and kiosk roles so both resolve $VERSION_CHOICE to the SAME
# ref. That is what keeps the Qt display and the server speaking the same
# WebSocket contract — see notes/native_app_strategy.md.
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
    else
        git -C "$dir" checkout master 2>/dev/null \
            || git -C "$dir" checkout main 2>/dev/null || true
        info "Version: master"
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

    # Migrate from previous install directory name if needed
    for _old_dir in "$HOME/CTS_Scoreboard_Rpi" "$HOME/CTS_Scoreboard" "$HOME/Scoreboard_Pi"; do
        if [[ ! -d "$INSTALL_DIR" && -d "$_old_dir/.git" ]]; then
            info "Found old installation at $_old_dir — migrating to $INSTALL_DIR"
            sudo systemctl stop tremplin 2>/dev/null || sudo systemctl stop scoreboard 2>/dev/null || true
            mv "$_old_dir" "$INSTALL_DIR"
            git -C "$INSTALL_DIR" remote set-url origin "$REPO_URL"
            info "Directory renamed and git remote updated."
            break
        fi
    done

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
                git -C "$INSTALL_DIR" fetch --tags --quiet || true
                git -C "$INSTALL_DIR" pull --quiet --ff-only 2>/dev/null || true
            else
                warn "Working tree has local changes — reinstalling on-disk code (no pull)."
            fi
        fi
    elif [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Updating existing repo at $INSTALL_DIR"
        git -C "$INSTALL_DIR" fetch --tags
        git -C "$INSTALL_DIR" pull
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

    section "Sudo permissions"
    SUDOERS_FILE="/etc/sudoers.d/splouch"
    sudo tee "$SUDOERS_FILE" > /dev/null <<EOF
$TARGET_USER ALL=(ALL) NOPASSWD: /usr/bin/timedatectl, /usr/bin/systemctl restart systemd-timesyncd, /usr/bin/nmcli, /usr/bin/apt-get, /usr/bin/systemctl restart splouch, /usr/sbin/reboot, /usr/sbin/poweroff, $INSTALL_DIR/install/scripts/rtc_setup.sh *, $INSTALL_DIR/install/scripts/refresh-service.sh, $INSTALL_DIR/install/scripts/web-reinstall.sh *
EOF
    sudo chmod 0440 "$SUDOERS_FILE"
    sudo rm -f /etc/sudoers.d/tremplin        # retire the pre-rename grant
    info "Sudoers rules written to $SUDOERS_FILE"

    section "Serial port access"
    if ! groups "$TARGET_USER" | grep -qw dialout; then
        sudo usermod -aG dialout "$TARGET_USER"
        warn "Added $TARGET_USER to 'dialout' group — takes effect after next login / reboot."
    else
        info "$TARGET_USER already in 'dialout' group."
    fi

    section "Data folders"
    # Migrate the pre-Splouch data dir if present (the app also does this on start).
    if [[ ! -d "$TARGET_HOME/SplouchData" && -d "$TARGET_HOME/TremplinData" ]]; then
        as_user mv "$TARGET_HOME/TremplinData" "$TARGET_HOME/SplouchData"
        info "Migrated ~/TremplinData → ~/SplouchData."
    fi
    as_user mkdir -p "$TARGET_HOME/SplouchData/meet" "$TARGET_HOME/SplouchData/images" \
                     "$TARGET_HOME/SplouchData/icons" "$TARGET_HOME/SplouchData/recorded"
    info "~/SplouchData/{meet,images,icons,recorded} created."

    section "Settings"
    if [[ ! -f "$TARGET_HOME/SplouchData/settings.json" ]]; then
        as_user cp "$INSTALL_DIR/server/settings.default.json" "$TARGET_HOME/SplouchData/settings.json"
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
        curl -fsSL "https://cdn.jsdelivr.net/npm/xterm@${XTERM_VER}/css/xterm.css"    -o "$XTERM_CSS"
        info "xterm.js ${XTERM_VER} downloaded."
    else
        info "xterm.js already present."
    fi

    section "Desktop shortcuts"
    mkdir -p "$TARGET_HOME/Desktop"
    rm -f "$TARGET_HOME/Desktop/Tremplin.desktop"   # retire the pre-rename launcher

    cat > "$TARGET_HOME/Desktop/Splouch.desktop" <<EOF
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
    cat > "$TARGET_HOME/Desktop/Settings.desktop" <<EOF
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
    cat > "$TARGET_HOME/Desktop/Mobile.desktop" <<EOF
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
    cat > "$TARGET_HOME/Desktop/Reinstall.desktop" <<EOF
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
        echo -e "[config]\nquick_exec=1" >> "$TARGET_HOME/.config/libfm/libfm.conf"
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
            cat > "$conf" <<WALLEOF
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
        info "VNC enabled. Connect with RealVNC Viewer → ${SERVER_IP%/*}"
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

    section "Hostname"
    sudo hostnamectl set-hostname "$SERVER_HOSTNAME"
    if grep -q "127.0.1.1" /etc/hosts; then
        sudo sed -i "s/127\.0\.1\.1.*/127.0.1.1\t${SERVER_HOSTNAME}/" /etc/hosts
    else
        echo "127.0.1.1	${SERVER_HOSTNAME}" | sudo tee -a /etc/hosts > /dev/null
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
    # See docs/troubleshooting-splouch-local-unreachable.md
    _avahi_set() {  # _avahi_set <key> <value> <section>
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
    sudo systemctl disable --now tremplin-mdns-aliases 2>/dev/null || true   # retire pre-rename unit
    sudo rm -f /etc/systemd/system/tremplin-mdns-aliases.service
    sudo tee /etc/systemd/system/splouch-mdns-aliases.service > /dev/null <<EOF
[Unit]
Description=mDNS aliases for Splouch
After=avahi-daemon.service
Requires=avahi-daemon.service

[Service]
Type=simple
ExecStart=$INSTALL_DIR/install/scripts/mdns-aliases.sh $MDNS_ALIASES
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
    # on the pool WiFi never types an address (docs/mobile-features.md `P-12`).
    # `kind` and `path` mirror GET /server so a client can list before it connects.
    sudo mkdir -p /etc/avahi/services
    sudo tee /etc/avahi/services/splouch.service > /dev/null <<EOF
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
    sudo systemctl disable --now tremplin-redirect 2>/dev/null || true   # retire pre-rename unit
    sudo rm -f /etc/systemd/system/tremplin-redirect.service
    sudo tee /etc/systemd/system/splouch-redirect.service > /dev/null <<EOF
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
    info "Port 80 redirects to 5000 — http://${SERVER_IP%/*}/ reaches the scoreboard"

    section "Network — Pi #1"
    configure_static_ip "$SERVER_IP"

    section "Real-time clock (Adafruit PiRTC DS3231)"
    echo "Adds a hardware clock so the Pi keeps accurate time without network access."
    if confirm "Install Adafruit PiRTC (DS3231) support now?"; then
        sudo bash "$INSTALL_DIR/install/scripts/rtc_setup.sh" enable
        info "RTC configured — will become active after reboot."
    else
        info "Skipping RTC setup — can be installed later from Settings → Clock."
    fi

    section "Done — Pi #1 (server)"
    echo
    echo -e "  Install dir : $INSTALL_DIR"
    echo -e "  Start server: ${BOLD}sudo systemctl start splouch${NC}"
    echo -e "  Logs        : ${BOLD}journalctl -u splouch -f${NC}"
    echo -e "  Scoreboard  : ${BOLD}http://${SERVER_HOSTNAME}.local/${NC}  or  http://${SERVER_IP%/*}/"
    echo -e "  Admin UI    : ${BOLD}http://${SERVER_HOSTNAME}.local/settings${NC}"
    echo -e "  Mobile view : ${BOLD}http://${SERVER_HOSTNAME}.local/mobile${NC}"
    echo -e "  Aliases     : $MDNS_ALIASES"
    echo -e "  Meet files  : ~/SplouchData/meet/*.lxf"
    echo -e "  Settings    : ~/SplouchData/settings.json"
    echo
    echo
    confirm "Reboot now to apply group membership and network changes?" && sudo reboot
fi

# ═══════════════════════════════════════════════════════════════════════════════
# KIOSK (Pi #2)
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$ROLE" == "kiosk" ]]; then

    section "Project"
    # The kiosk now runs code (the Qt scoreboard in scoreboard/) rather than a
    # browser pointed at a URL, so it needs the repo — and, crucially, the SAME
    # git ref as the server, so the two agree on the WebSocket contract.
    # See notes/native_app_strategy.md.
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
                git -C "$INSTALL_DIR" fetch --tags --quiet || true
                git -C "$INSTALL_DIR" pull --quiet --ff-only 2>/dev/null || true
            else
                warn "Working tree has local changes — reinstalling on-disk code (no pull)."
            fi
        fi
    elif [[ -d "$INSTALL_DIR/.git" ]]; then
        info "Updating existing repo at $INSTALL_DIR"
        git -C "$INSTALL_DIR" fetch --tags
        git -C "$INSTALL_DIR" pull
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
               "$SCOREBOARD_URL" > "$SCOREBOARD_ENV"
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
    if ! grep -q "# Splouch kiosk" "$CONFIG_TXT"; then
        sudo tee -a "$CONFIG_TXT" > /dev/null <<EOF

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
    # Drop any previous Splouch line (the Chromium kiosk from before the Qt
    # display) so a re-run replaces it instead of launching both.
    sed -i '/# Splouch kiosk/,+1d' "$LABWC_AUTOSTART"
    printf '\n# Splouch kiosk\n%s &\n' "$KIOSK_CMD" >> "$LABWC_AUTOSTART"

    # Older Raspberry Pi OS releases — LXDE / X11 session
    AUTOSTART_DIR=/etc/xdg/lxsession/LXDE-pi
    sudo mkdir -p "$AUTOSTART_DIR"
    sudo tee "$AUTOSTART_DIR/autostart" > /dev/null <<EOF
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
    cat > "$TARGET_HOME/Desktop/Scoreboard.desktop" <<EOF
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
    cat > "$TARGET_HOME/Desktop/Settings.desktop" <<EOF
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
        echo -e "[config]\nquick_exec=1" >> "$TARGET_HOME/.config/libfm/libfm.conf"
    fi
    info "Desktop shortcuts created (Scoreboard, Settings)."

    section "Desktop wallpaper"
    # Now served from the checkout, like the server role — the kiosk has the repo.
    WALLPAPER="$INSTALL_DIR/shared/static/img/scoreboard_bg.png"
    if [[ -f "$WALLPAPER" ]]; then
        PCMANFM_CONF="$HOME/.config/pcmanfm/LXDE-pi"
        mkdir -p "$PCMANFM_CONF"
        for conf in "$PCMANFM_CONF/desktop-items-0.conf" "$PCMANFM_CONF/desktop-items-1.conf"; do
            cat > "$conf" <<WALLEOF
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
    warn "Pi #1 (server) must be running and reachable at $KIOSK_GATEWAY before the kiosk boots."
    echo
    confirm "Reboot now?" && sudo reboot
fi

# ═══════════════════════════════════════════════════════════════════════════════
# CLOUD (Debian VM — public relay server)
# ═══════════════════════════════════════════════════════════════════════════════
if [[ "$ROLE" == "cloud" ]]; then

    # ── User bootstrap (runs once as root on a fresh server) ──────────────────
    if [[ "$(id -u)" == "0" ]]; then
        section "User setup"
        TREMPLIN_USER="splouch"

        if ! id "$TREMPLIN_USER" &>/dev/null; then
            useradd -m -s /bin/bash "$TREMPLIN_USER"
            info "User '$TREMPLIN_USER' created."
        else
            info "User '$TREMPLIN_USER' already exists."
        fi

        usermod -aG sudo "$TREMPLIN_USER"

        # Set a password so splouch can use sudo normally after the install
        echo
        while true; do
            read -rsp "Set a password for '$TREMPLIN_USER': " _pw1; echo
            read -rsp "Confirm password: " _pw2; echo
            if [[ "$_pw1" == "$_pw2" && -n "$_pw1" ]]; then
                echo "$TREMPLIN_USER:$_pw1" | chpasswd
                info "Password set for '$TREMPLIN_USER'."
                unset _pw1 _pw2
                break
            fi
            warn "Passwords did not match or were empty — try again."
        done

        # Passwordless sudo only for the duration of the install
        echo "$TREMPLIN_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/splouch
        chmod 0440 /etc/sudoers.d/splouch
        info "Temporary NOPASSWD sudo granted for install."

        # Copy root's SSH authorized_keys so the server stays reachable
        if [[ -f /root/.ssh/authorized_keys ]]; then
            install -d -m 700 -o "$TREMPLIN_USER" -g "$TREMPLIN_USER" \
                "/home/$TREMPLIN_USER/.ssh"
            install -m 600 -o "$TREMPLIN_USER" -g "$TREMPLIN_USER" \
                /root/.ssh/authorized_keys \
                "/home/$TREMPLIN_USER/.ssh/authorized_keys"
            info "SSH authorized_keys copied from root → '$TREMPLIN_USER' can log in via SSH."
        else
            warn "No /root/.ssh/authorized_keys — configure SSH access for '$TREMPLIN_USER' manually."
        fi

        # Harden root access
        passwd -l root
        info "Root password locked."
        sed -i 's/^#*PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
        sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
        systemctl restart ssh
        info "Root SSH login disabled, password auth disabled — key-only SSH from now on."

        # Copy this script to splouch's home and re-exec as that user
        _script_src="$(realpath "${BASH_SOURCE[0]}")"
        _script_dst="/home/$TREMPLIN_USER/install.sh"
        install -m 755 -o "$TREMPLIN_USER" -g "$TREMPLIN_USER" \
            "$_script_src" "$_script_dst"
        info "Re-running install as '$TREMPLIN_USER'…"
        exec sudo -H -u "$TREMPLIN_USER" bash "$_script_dst" cloud "$VERSION_CHOICE"
    fi
    # ──────────────────────────────────────────────────────────────────────────

    section "System packages"
    ensure_https_apt_sources
    sudo apt-get update -qq
    sudo apt-get upgrade -y
    sudo apt-get install -y git curl fail2ban unattended-upgrades

    section "fail2ban"
    sudo tee /etc/fail2ban/jail.d/sshd.local > /dev/null <<'EOF'
[sshd]
enabled  = true
maxretry = 5
bantime  = 1h
findtime = 10m
EOF
    sudo systemctl enable --now fail2ban
    info "fail2ban enabled — SSH: 5 failures in 10 min → 1 h ban."

    section "Automatic security updates"
    sudo tee /etc/apt/apt.conf.d/20auto-upgrades > /dev/null <<'EOF'
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
        git -C "$INSTALL_DIR" pull
    else
        info "Cloning $REPO_URL → $INSTALL_DIR"
        git clone "$REPO_URL" "$INSTALL_DIR"
    fi

    CLOUD_DIR="$INSTALL_DIR/cloud"

    section "Environment file"
    if [[ ! -f "$CLOUD_DIR/.env" ]]; then
        cp "$CLOUD_DIR/.env.example" "$CLOUD_DIR/.env"
        SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        sed -i "s/^SECRET_KEY=.*/SECRET_KEY=${SECRET}/" "$CLOUD_DIR/.env"

        echo
        read -rp "Set the admin username for the /admin panel [admin]: " _au
        _au="${_au:-admin}"
        sed -i "s/^ADMIN_USER=.*/ADMIN_USER=${_au}/" "$CLOUD_DIR/.env"
        info "ADMIN_USER set to '${_au}'."

        while true; do
            read -rsp "Set the admin password for the /admin panel: " _ap1; echo
            read -rsp "Confirm admin password: " _ap2; echo
            if [[ "$_ap1" == "$_ap2" && -n "$_ap1" ]]; then
                sed -i "s/^ADMIN_PASSWORD=.*/ADMIN_PASSWORD=${_ap1}/" "$CLOUD_DIR/.env"
                info "ADMIN_PASSWORD set."
                unset _ap1 _ap2
                break
            fi
            warn "Passwords did not match or were empty — try again."
        done

        info "Created $CLOUD_DIR/.env with generated SECRET_KEY, ADMIN_USER, and ADMIN_PASSWORD."
    else
        info ".env already exists — skipping."
    fi

    section "Deploy webhook"
    if grep -q "^DEPLOY_SECRET=change_me" "$CLOUD_DIR/.env" 2>/dev/null || \
       ! grep -q "^DEPLOY_SECRET=" "$CLOUD_DIR/.env" 2>/dev/null; then
        _deploy_secret=$(python3 -c "import secrets; print(secrets.token_hex(32))")
        if grep -q "^DEPLOY_SECRET=" "$CLOUD_DIR/.env" 2>/dev/null; then
            sed -i "s/^DEPLOY_SECRET=.*$/DEPLOY_SECRET=${_deploy_secret}/" "$CLOUD_DIR/.env"
        else
            echo "DEPLOY_SECRET=${_deploy_secret}" >> "$CLOUD_DIR/.env"
        fi
        info "DEPLOY_SECRET generated and saved to .env"
    else
        info "DEPLOY_SECRET already set in .env — keeping existing value."
    fi
    sed \
        -e "s|YOUR_INSTALL_DIR|${INSTALL_DIR}|g" \
        -e "s|YOUR_USER|${USER}|g" \
        "$CLOUD_DIR/deploy_webhook.service" \
        > /tmp/deploy-webhook.service
    sudo install -m 644 /tmp/deploy-webhook.service /etc/systemd/system/deploy-webhook.service
    rm /tmp/deploy-webhook.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now deploy-webhook
    info "Deploy webhook enabled on port 9000 — powers the Update button in /admin."
    sudo ufw allow from 172.16.0.0/12 to any port 9000 comment "Docker → deploy webhook"
    info "ufw: Docker bridge networks (172.16/12) allowed to reach port 9000."

    # Allow the webhook process to restart itself after a deploy (no password prompt)
    echo "${USER} ALL=(ALL) NOPASSWD: /usr/bin/systemctl restart deploy-webhook" \
        | sudo tee /etc/sudoers.d/splouch-webhook > /dev/null
    sudo chmod 0440 /etc/sudoers.d/splouch-webhook
    info "Sudoers rule added: deploy-webhook can self-restart without a password."

    section "Caddyfile domain"
    _current_domain=$(grep -E '^\S+\s*\{' "$CLOUD_DIR/Caddyfile" | awk '{print $1}')
    echo
    echo "  Current domain: ${_current_domain:-not set}"
    read -rp "  Enter domain name (leave blank to keep current): " _domain
    if [[ -n "$_domain" && "$_domain" != "$_current_domain" ]]; then
        sed -i "s|^\S\+\s*{|${_domain} {|" "$CLOUD_DIR/Caddyfile"
        info "Caddyfile updated: $_domain"
    else
        info "Domain unchanged: ${_current_domain}"
    fi

    section "Firewall"
    if command -v ufw &>/dev/null; then
        sudo ufw --force enable
        sudo ufw default deny incoming
        sudo ufw default allow outgoing
        sudo ufw allow 22/tcp    # SSH
        sudo ufw allow 80/tcp    # HTTP  (Caddy ACME challenge + redirect)
        sudo ufw allow 443/tcp   # HTTPS
        sudo ufw allow 443/udp   # HTTP/3
        info "Firewall enabled — ports 22, 80, 443 open."
    else
        warn "ufw not found — configure firewall manually (open ports 22, 80, 443)."
    fi

    section "Build and start"
    cd "$CLOUD_DIR"
    sg docker -c "docker compose --env-file .env up -d --build"
    info "Cloud server started."

    section "Done — Cloud server"
    echo
    echo -e "  Install dir  : $INSTALL_DIR"
    echo -e "  Cloud dir    : $CLOUD_DIR"
    echo -e "  Logs         : ${BOLD}cd $CLOUD_DIR && docker compose logs -f${NC}"
    _final_domain=$(grep -E '^\S+\s*\{' "$CLOUD_DIR/Caddyfile" | awk '{print $1}')
    echo -e "  Admin UI     : ${BOLD}https://${_final_domain}/admin${NC}"
    echo -e "  Update       : ${BOLD}Update button in /admin${NC}  (or: cd $INSTALL_DIR && git pull && cd cloud && docker compose up -d --build)"
    echo
    echo

    # Remove the temporary NOPASSWD rule — sudo now requires the password set above
    sudo rm -f /etc/sudoers.d/tremplin
    info "Temporary NOPASSWD sudo rule removed."
fi
