#!/usr/bin/env bash
# Splouch — first entry: pick a version, then run that version's own installer.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/setup.sh -o setup.sh && bash setup.sh
#   bash setup.sh [role] [version]     role: server, kiosk or cloud; version: master, a tag, a branch or a commit
#
# Offers master and the 10 latest releases, or a version typed in. The installer is
# downloaded from that version, so a release is installed by the installer it
# shipped with rather than by master's.

set -euo pipefail

REPO="olivierouellet/Splouch"
REPO_URL="https://github.com/$REPO.git"
RAW_URL="https://raw.githubusercontent.com/$REPO"

RED='\033[0;31m'
YELLOW='\033[1;33m'
BOLD='\033[1m'
NC='\033[0m'
warn() { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

ROLE="${1:-}"
VERSION="${2:-}"

echo -e "${BOLD}"
cat <<'EOF'
 ____    ____    _        ___    _   _    ____   _   _
/ ___|  |  _ \  | |      / _ \  | | | |  / ___| | | | |
\___ \  | |_) | | |     | | | | | | | | | |     | |_| |
 ___) | |  __/  | |___  | |_| | | |_| | | |___  |  _  |
|____/  |_|     |_____|  \___/   \___/   \____| |_| |_|
EOF
echo -e "${NC}"

# The installer provisions Raspberry Pi OS, Debian or Ubuntu (apt, systemd, getent).
if [[ "$(uname -s)" != "Linux" && "${SPLOUCH_SETUP_ANY_OS:-}" != "1" ]]; then
    error "Splouch installs on a Raspberry Pi or a Debian/Ubuntu server, not on $(uname -s)."
    error "SSH into the Pi or the VM and run this there."
    exit 1
fi

# Release tags, newest first. git when it is there (a Pi has it); otherwise the
# GitHub API over curl, which a fresh VM always has since it fetched this script.
release_tags() {
    {
        if command -v git &>/dev/null; then
            git ls-remote --tags --refs "$REPO_URL" 2>/dev/null | sed 's|.*refs/tags/||'
        else
            curl -fsSL "https://api.github.com/repos/$REPO/tags?per_page=100" 2>/dev/null \
                | grep -o '"name": *"[^"]*"' | sed 's/.*"\([^"]*\)"$/\1/'
        fi
    } | grep -E '^v[0-9]{4}\.[0-9]{2}\.[0-9]+$' | sort -t. -k1,1r -k2,2nr -k3,3nr
}

# The version's installer: install/install.sh, or install.sh at the root for the
# releases from before the installer moved.
fetch_installer() {
    local ref="$1" dst="$2" path
    for path in install/install.sh install.sh; do
        if curl -fsSL "$RAW_URL/$ref/$path" -o "$dst" 2>/dev/null \
            && ! grep -q 'install.sh has moved to install/install.sh' "$dst"; then
            return 0
        fi
    done
    return 1
}

TAGS=()
while IFS= read -r _t; do TAGS+=("$_t"); done < <(release_tags | head -10)
NEWEST="${TAGS[0]:-}"

while true; do
    if [[ -z "$VERSION" ]]; then
        [[ ${#TAGS[@]} -eq 0 ]] && warn "Could not list the releases — master or a typed version only."
        # Not `select`: it has no default, and Enter should take the latest release.
        _labels=() _values=()
        [[ -n "$NEWEST" ]] && _labels+=("$NEWEST (latest release)") _values+=("$NEWEST")
        _labels+=("master (development branch)") _values+=("master")
        for _t in ${TAGS[@]+"${TAGS[@]:1}"}; do _labels+=("$_t") _values+=("$_t"); done
        _labels+=("Custom (type a tag, branch or commit)" "Quit") _values+=("" "")
        _custom=$((${#_labels[@]} - 1)) _quit=${#_labels[@]}
        echo "Which version to install?"
        for _i in "${!_labels[@]}"; do printf '%2d) %s\n' $((_i + 1)) "${_labels[_i]}"; done
        while true; do
            read -rp "Choice [1]: " _n || exit 1 # end of input
            _n="${_n:-1}"
            if [[ "$_n" =~ ^[0-9]+$ ]] && ((_n >= 1 && _n <= _quit)); then break; fi
            warn "Type a number from 1 to $_quit."
        done
        if ((_n == _quit)); then
            exit 0
        elif ((_n == _custom)); then
            read -rp "Version: " VERSION || exit 1
            [[ -z "$VERSION" ]] && continue
        else
            VERSION="${_values[_n - 1]}"
        fi
    fi

    _dir="$(mktemp -d "${TMPDIR:-/tmp}/splouch-setup.XXXXXX")"
    _installer="$_dir/install.sh"
    if ! fetch_installer "$VERSION" "$_installer"; then
        error "No installer found for '$VERSION'."
        VERSION=""
        continue
    fi

    # An installer from before version pinning knows only `latest` and `master`:
    # given anything else it installs master. Fine for master and for the newest
    # release; any other release cannot be installed that way.
    _arg="$VERSION"
    if ! grep -q SPLOUCH_PINS_REF "$_installer"; then
        if [[ "$VERSION" == "master" ]]; then
            _arg="master"
        elif [[ "$VERSION" == "$NEWEST" ]]; then
            _arg="latest"
        else
            error "$VERSION's installer can only install master or the latest release — pick another version."
            VERSION=""
            continue
        fi
    fi
    break
done

echo
echo -e "Installing ${BOLD}$VERSION${NC} with its own installer…"
exec bash "$_installer" "$ROLE" "$_arg"
