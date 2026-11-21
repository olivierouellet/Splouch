#!/usr/bin/env bash
# Splouch — set the Wi-Fi regulatory country (which channels and power the radio
# may use; 5 GHz networks can stay invisible while it is unset).
#
# Usage (must run as root):
#   wifi-country.sh CC    CC: an ISO 3166 country code, such as CA
#
# Settings → Network runs it through sudo. raspi-config writes the code into the
# kernel command line (cfg80211.ieee80211_regdom=) without checking it, so this
# script is the check: two capital letters that tzdata lists as a country, and
# nothing else, ever reaches it.

set -euo pipefail

CODE="${1:-}"
ISO_TABLE="/usr/share/zoneinfo/iso3166.tab"

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Must be run as root (sudo)." >&2
    exit 1
fi

if ! [[ $CODE =~ ^[A-Z]{2}$ ]] || ! grep -q "^${CODE}[[:space:]]" "$ISO_TABLE"; then
    echo "Not a country code: $CODE" >&2
    exit 2
fi

if command -v raspi-config >/dev/null; then
    # Persists across reboots (kernel command line) and applies now (iw reg set).
    raspi-config nonint do_wifi_country "$CODE"
else
    iw reg set "$CODE"
fi
echo "Wi-Fi country set to $CODE."
