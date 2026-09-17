#!/usr/bin/env bash
# Publish Splouch's translated mDNS aliases (tableau.local, marcador.local, …)
# at the Pi's CURRENT primary-interface IP, re-detected every time the service
# starts. This replaces the old install-time-baked static 10.10.10.10, so the
# aliases resolve correctly on a DHCP setup (i.e. when the static IP was
# declined). Run by splouch-mdns-aliases.service; aliases are passed as args,
# falling back to the built-in localized set.
#
# splouch.local itself comes from the hostname (avahi advertises it
# automatically) — this only handles the extra translated aliases.
set -euo pipefail

ALIASES=("$@")
if [[ ${#ALIASES[@]} -eq 0 ]]; then
    ALIASES=(tableau.local marcador.local)
fi

# Primary IPv4 = the source address the kernel would use to reach off-link,
# falling back to the first address reported by `hostname -I`.
IP="$(ip route get 1.1.1.1 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p' | head -n1)"
[[ -z "${IP:-}" ]] && IP="$(hostname -I 2>/dev/null | awk '{print $1}')"

if [[ -z "${IP:-}" ]]; then
    echo "mdns-aliases: could not determine an IPv4 address; nothing to publish." >&2
    exit 1
fi

pids=()
for alias in "${ALIASES[@]}"; do
    avahi-publish -a -R "$alias" "$IP" &
    pids+=("$!")
done
wait "${pids[@]}"
