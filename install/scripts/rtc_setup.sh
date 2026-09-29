#!/usr/bin/env bash
# Splouch — Adafruit PiRTC (DS3231) real-time clock setup/removal
# Product: https://www.adafruit.com/product/4282
# Guide:   https://learn.adafruit.com/adding-a-real-time-clock-to-raspberry-pi
#
# Usage (must run as root):
#   rtc_setup.sh enable    configure the DS3231 RTC overlay and disable fake-hwclock
#   rtc_setup.sh disable   remove the overlay and restore fake-hwclock
#   rtc_setup.sh status    print "configured=yes|no" and "active=yes|no"
#
# A reboot is required after enable/disable for changes to take effect.

set -euo pipefail

ACTION="${1:-}"
OVERLAY_LINE="dtoverlay=i2c-rtc,ds3231"
HWCLOCK_SET="/lib/udev/hwclock-set"

if [[ -f /boot/firmware/config.txt ]]; then
    CONFIG_TXT="/boot/firmware/config.txt"
else
    CONFIG_TXT="/boot/config.txt"
fi

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Must be run as root (sudo)." >&2
    exit 1
fi

patch_hwclock_set() {
    [[ -f "$HWCLOCK_SET" ]] || return 0
    # Comment out the systemd early-exit block (per Adafruit's guide) so
    # hwclock-set still syncs the RTC <-> system clock on shutdown.
    perl -0777 -pi -e \
        's/^if \[ -e \/run\/systemd\/system \] ; then\n\texit 0\nfi\n/#if [ -e \/run\/systemd\/system ] ; then\n#\texit 0\n#fi\n/m' \
        "$HWCLOCK_SET" 2>/dev/null || true
}

restore_hwclock_set() {
    [[ -f "$HWCLOCK_SET" ]] || return 0
    perl -0777 -pi -e \
        's/^#if \[ -e \/run\/systemd\/system \] ; then\n#\texit 0\n#fi\n/if [ -e \/run\/systemd\/system ] ; then\n\texit 0\nfi\n/m' \
        "$HWCLOCK_SET" 2>/dev/null || true
}

case "$ACTION" in
    enable)
        echo "Enabling I2C interface…"
        raspi-config nonint do_i2c 0

        if ! grep -qxF "$OVERLAY_LINE" "$CONFIG_TXT"; then
            echo "Adding '$OVERLAY_LINE' to $CONFIG_TXT"
            echo "$OVERLAY_LINE" >>"$CONFIG_TXT"
        else
            echo "'$OVERLAY_LINE' already present in $CONFIG_TXT"
        fi

        echo "Disabling fake-hwclock…"
        systemctl disable --now fake-hwclock >/dev/null 2>&1 || true
        apt-get -y remove fake-hwclock || true

        echo "Patching $HWCLOCK_SET…"
        patch_hwclock_set

        echo
        echo "RTC configured. Reboot required to activate the DS3231 hardware clock."
        ;;

    disable)
        echo "Removing '$OVERLAY_LINE' from $CONFIG_TXT…"
        sed -i "\\|^${OVERLAY_LINE}\$|d" "$CONFIG_TXT"

        echo "Restoring fake-hwclock…"
        apt-get -y install fake-hwclock || true
        systemctl enable fake-hwclock >/dev/null 2>&1 || true

        echo "Restoring $HWCLOCK_SET…"
        restore_hwclock_set

        echo
        echo "RTC removed. Reboot required to fully revert."
        ;;

    status)
        if grep -qxF "$OVERLAY_LINE" "$CONFIG_TXT" 2>/dev/null; then
            echo "configured=yes"
        else
            echo "configured=no"
        fi
        if [[ -e /sys/class/rtc/rtc0/name ]] && grep -qi "ds3231\|rtc-ds1307" /sys/class/rtc/rtc0/name 2>/dev/null; then
            echo "active=yes"
        else
            echo "active=no"
        fi
        ;;

    *)
        echo "Usage: $0 {enable|disable|status}" >&2
        exit 1
        ;;
esac
