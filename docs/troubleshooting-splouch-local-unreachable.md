# `splouch.local` won't load in the browser (but ping works)

## Symptom

- `ping splouch.local` works.
- Loading `http://splouch.local/` in a browser fails / hangs.
- Connecting to the Pi **by its IP address** works:
  `http://10.8.20.103/` and `http://10.8.20.103:5000/live` both load.

So the Pi, the FastAPI service, and the port-80 redirect are all healthy — only
the **hostname** fails to load.

## Cause

The Pi is multihomed (e.g. WiFi `wlan0` has an IP, `eth0` does not, or vice
versa). avahi advertises **every** address it has for `splouch.local`,
including the **IPv6 link-local** address (`fe80::…`) on the active interface.

When a browser resolves `splouch.local` it receives both an `AAAA`
(IPv6 link-local) and an `A` (IPv4) record. Many browsers/OSes **prefer IPv6**,
try the `fe80::…` address first, and a link-local IPv6 address can't be reached
without a zone index (`%iface`) — so the connection hangs or fails. `ping` and
direct-IP access worked because they used IPv4.

You can spot the IPv6 link-local being published in the avahi log:

```text
avahi-daemon[680]: Registering new address record for fe80::e65f:1ff:fe86:772 on wlan0.*.
```

## Solution

Stop avahi from publishing the IPv6 record, so `splouch.local` only ever
resolves to the reachable IPv4 address. **Two** settings are involved in
`/etc/avahi/avahi-daemon.conf`:

```ini
[server]
use-ipv6=no                 # disables the IPv6 mDNS transport

[publish]
publish-aaaa-on-ipv4=no     # stops the AAAA record being announced over IPv4
```

> ⚠️ `use-ipv6=no` alone is **not enough** — `publish-aaaa-on-ipv4` defaults to
> `yes`, so avahi keeps announcing the `fe80::…` AAAA record over IPv4 even with
> the IPv6 transport disabled. That setting is the actual culprit.

Apply both (handles the "commented", "set", and "missing" cases):

```bash
# use-ipv6=no  (under [server])
if grep -q '^#*[[:space:]]*use-ipv6=' /etc/avahi/avahi-daemon.conf; then
    sudo sed -i 's/^#*[[:space:]]*use-ipv6=.*/use-ipv6=no/' /etc/avahi/avahi-daemon.conf
else
    sudo sed -i '/^\[server\]/a use-ipv6=no' /etc/avahi/avahi-daemon.conf
fi

# publish-aaaa-on-ipv4=no  (under [publish])
if grep -q '^#*[[:space:]]*publish-aaaa-on-ipv4=' /etc/avahi/avahi-daemon.conf; then
    sudo sed -i 's/^#*[[:space:]]*publish-aaaa-on-ipv4=.*/publish-aaaa-on-ipv4=no/' /etc/avahi/avahi-daemon.conf
else
    sudo sed -i '/^\[publish\]/a publish-aaaa-on-ipv4=no' /etc/avahi/avahi-daemon.conf
fi

sudo systemctl restart avahi-daemon
```

Confirm it took — the IPv6 lookup should now fail and only IPv4 should resolve:

```bash
avahi-resolve -n splouch.local -6     # should return nothing
avahi-resolve -n splouch.local        # should return only the IPv4 address
```

Then flush the client's DNS/mDNS cache and reload `http://splouch.local/`:

- **macOS:** `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`
- **Windows:** `ipconfig /flushdns`

> Browsers cache the failed/stale resolution aggressively. If a private/incognito
> window works but a normal window doesn't, the server is fixed — you just need
> to clear the browser cache or fully quit and reopen the browser (on Safari,
> ⌘Q, or Develop → Empty Caches).

## A second cause: the browser is trying https

### How it looks

- `http://splouch.local/` typed **with the `http://` prefix** loads fine.
- Typing just `splouch.local` hangs, or the address bar shows `https://` and the
  page never arrives.

### Why it happens

Chrome, Safari and Firefox upgrade a typed hostname to `https://` before trying
`http://`. Nothing on the Pi serves TLS, and it never will by default — a
Let's Encrypt certificate needs a public domain, which no pool-deck LAN has.

That upgrade is normally harmless: the browsers fall back to http as soon as the
https attempt fails **fast** (a TCP reset — "connection refused"). The trap is
that ufw's `deny` policy **drops** packets silently instead of resetting them.
When the SYN is dropped, nothing comes back, the browser waits, and the server
looks dead.

On `eth0`/`wlan0` the blanket `ufw allow in on …` rules let the SYN through and
the kernel resets it, so the fallback works. The hang shows up on any other
path — the Pi reached through a router, or a USB WiFi dongle that enumerates as
`wlxXXXXXXXX` rather than `wlan0`, so the allow rule never matches it.

> There is no way for the server to redirect an https request to http, or to
> serve a "no https here" warning page. An HTTP response travels *inside* the TLS
> session, so sending one requires a certificate the browser already trusts. The
> only thing the Pi can do is fail fast enough that the browser retries on http
> by itself.

### The fix

Make port 443 refuse connections instead of swallowing them:

```bash
sudo ufw reject 443/tcp comment "no TLS here — reset fast so browsers fall back to http"
sudo ufw status numbered | grep 443
```

Then confirm the reset arrives promptly — this should return
`Connection refused` immediately, not hang:

```bash
curl -sS --max-time 5 https://splouch.local/ ; echo "exit=$?"
```

Remaining cases that this cannot fix, because only a trusted certificate can:

- A bookmark or link saved as `https://splouch.local/` — the explicit scheme
  disables the fallback. Re-save it as `http://splouch.local/`.
- Firefox with **HTTPS-Only Mode** switched on. Add an exception for the site,
  or turn the mode off.

## Notes

- Both fixes are handled automatically by `install.sh` for new installs — the
  IPv6 one in the **mDNS aliases** section, the port 443 reject in the
  **Firewall** section. Existing Pis are nudged to reinstall by the provisioning
  version check (`install/PROVISION_VERSION`), since the in-app update cannot
  change firewall rules itself.
- Disabling IPv6 in avahi is safe for this LAN scoreboard use case — all
  clients reach the Pi over IPv4.
- If the page is still unreachable after this and **even direct IP fails from
  the client**, the network has WiFi client/AP isolation enabled (common on
  corporate networks). That blocks device-to-device traffic and can only be
  fixed on the network side, or by connecting clients to the pool-deck `eth0`
  network instead.
