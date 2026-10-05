#!/usr/bin/env bash
# WireGuard between Splouch's cloud servers, for monitoring only
# (docs/architecture/scaling.md, *Monitoring*). Hub and spokes: the server running
# monitoring is the hub, 10.73.0.1; each other server is a spoke with one peer, the
# hub, and sends its metrics there (cloud/monitoring/agent.yml). Pis, attendees and
# the control plane's own calls never use the tunnel — a broken tunnel blinds the
# monitoring and nothing else.
#
#   sudo wireguard.sh hub                                   on the monitoring server
#   sudo wireguard.sh node <10.73.0.N> <hub host> <hub key> on every other server
#   sudo wireguard.sh add-peer <name> <node key> <10.73.0.N> on the hub, per node
#
# Each server makes its own private key, readable by root only, and it never leaves
# the server; only public keys are exchanged. A server's public key is also left in
# /etc/splouch/wg-public.key, which its workers report to the control plane's
# Nodes tab.

set -euo pipefail

WG_DIR="${WG_DIR:-/etc/wireguard}"
SPLOUCH_DIR="${SPLOUCH_DIR:-/etc/splouch}"
HUB_ADDRESS="10.73.0.1"
PORT=51820

# ── The files, as text: no side effects, so they can be tested ────────────────

hub_conf() {
    local private_key="$1"
    printf '[Interface]\nAddress = %s/24\nListenPort = %s\nPrivateKey = %s\n' \
        "$HUB_ADDRESS" "$PORT" "$private_key"
}

node_conf() {
    local private_key="$1" address="$2" hub_host="$3" hub_key="$4"
    printf '[Interface]\nAddress = %s/32\nPrivateKey = %s\n\n' "$address" "$private_key"
    # The hub only: a node never reaches another node over the tunnel.
    printf '[Peer]\nPublicKey = %s\nEndpoint = %s:%s\nAllowedIPs = %s/32\n' \
        "$hub_key" "$hub_host" "$PORT" "$HUB_ADDRESS"
    # The node is behind whatever NAT its provider has; keep the path open.
    printf 'PersistentKeepalive = 25\n'
}

peer_block() {
    local name="$1" key="$2" address="$3"
    printf '\n# %s\n[Peer]\nPublicKey = %s\nAllowedIPs = %s/32\n' "$name" "$key" "$address"
}

valid_key() { [[ "$1" =~ ^[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=$ ]]; }
valid_address() { [[ "$1" =~ ^10\.73\.0\.([2-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-4])$ ]]; }
valid_name() { [[ "$1" =~ ^[A-Za-z0-9._-]{1,32}$ ]]; }
valid_host() { [[ "$1" =~ ^[A-Za-z0-9.-]{1,253}$ ]]; }

# ── Doing it ──────────────────────────────────────────────────────────────────

ensure_wireguard() {
    command -v wg >/dev/null 2>&1 || apt-get install -y --no-install-recommends wireguard-tools
}

# This server's key pair, made once. The private key stays in WG_DIR, root only.
ensure_keys() {
    umask 077
    mkdir -p "$WG_DIR" "$SPLOUCH_DIR"
    [[ -s "$WG_DIR/splouch.key" ]] || wg genkey >"$WG_DIR/splouch.key"
    wg pubkey <"$WG_DIR/splouch.key" >"$WG_DIR/splouch.pub"
    install -m 644 "$WG_DIR/splouch.pub" "$SPLOUCH_DIR/wg-public.key"
}

write_conf() {
    umask 077
    cat >"$WG_DIR/wg0.conf"
    systemctl enable --now wg-quick@wg0
    systemctl restart wg-quick@wg0
}

cmd_hub() {
    ensure_wireguard
    ensure_keys
    hub_conf "$(cat "$WG_DIR/splouch.key")" | write_conf
    if command -v ufw >/dev/null 2>&1; then ufw allow "$PORT/udp" comment "WireGuard (Splouch monitoring)"; fi
    echo "WireGuard hub up at $HUB_ADDRESS, UDP $PORT."
    echo "Hub public key: $(cat "$WG_DIR/splouch.pub")"
    echo "On each other server: sudo install/scripts/wireguard.sh node 10.73.0.N <this server's host> <hub public key>"
}

cmd_node() {
    local address="$1" hub_host="$2" hub_key="$3"
    valid_address "$address" || {
        echo "Address must be 10.73.0.2 – 10.73.0.254" >&2
        exit 2
    }
    valid_host "$hub_host" || {
        echo "Not a host name or address: $hub_host" >&2
        exit 2
    }
    valid_key "$hub_key" || {
        echo "Not a WireGuard public key: $hub_key" >&2
        exit 2
    }
    ensure_wireguard
    ensure_keys
    node_conf "$(cat "$WG_DIR/splouch.key")" "$address" "$hub_host" "$hub_key" | write_conf
    echo "WireGuard up at $address, sending to $HUB_ADDRESS."
    echo "On the hub: sudo install/scripts/wireguard.sh add-peer $(hostname -s) $(cat "$WG_DIR/splouch.pub") $address"
}

cmd_add_peer() {
    local name="$1" key="$2" address="$3"
    valid_name "$name" || {
        echo "Name: letters, digits, . _ - only" >&2
        exit 2
    }
    valid_key "$key" || {
        echo "Not a WireGuard public key: $key" >&2
        exit 2
    }
    valid_address "$address" || {
        echo "Address must be 10.73.0.2 – 10.73.0.254" >&2
        exit 2
    }
    if grep -q "AllowedIPs = $address/32" "$WG_DIR/wg0.conf"; then
        echo "$address already has a peer in $WG_DIR/wg0.conf" >&2
        exit 1
    fi
    peer_block "$name" "$key" "$address" >>"$WG_DIR/wg0.conf"
    # Apply without dropping the peers already up.
    wg syncconf wg0 <(wg-quick strip wg0)
    echo "Peer $name added at $address."
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    case "${1:-}" in
        hub) cmd_hub ;;
        node) cmd_node "${2:?address}" "${3:?hub host}" "${4:?hub public key}" ;;
        add-peer) cmd_add_peer "${2:?name}" "${3:?public key}" "${4:?address}" ;;
        *)
            sed -n '2,16p' "$0"
            exit 2
            ;;
    esac
fi
