# `apt update` fails behind an HTTP-filtering network

## Symptom

On the Pi, `sudo apt update` fails for **every** repository with a `404 NOT FOUND`
on the `Release` file, even though the network is otherwise working (you can
`ping` the mirrors fine):

```text
Err:4 http://deb.debian.org/debian trixie Release
  404  NOT FOUND [IP: 151.101.138.132 80]
Err:8 http://archive.raspberrypi.com/debian trixie Release
  404  NOT FOUND [IP: 93.93.135.118 80]
Error: The repository 'http://deb.debian.org/debian trixie Release' does not have a Release file.
```

## Cause

The Pi is on a network (e.g. a corporate/intranet connection) whose **proxy or
firewall intercepts plain HTTP traffic** and returns its own `404` page instead
of passing the request through to the package mirrors.

The giveaway is that **two completely independent mirror providers**
(`deb.debian.org`, served by Fastly, and `archive.raspberrypi.com`, the
Raspberry Pi Foundation's own server) fail at the *same time*. Independent
mirrors don't break simultaneously — something local to the network is the
common factor.

You can confirm it by comparing HTTP vs HTTPS for the same file:

```bash
# Plain HTTP — intercepted by the proxy, returns a fake 404 HTML page:
curl -sI http://deb.debian.org/debian/dists/trixie/Release | head
#   HTTP/1.1 404 NOT FOUND
#   Content-Type: text/html; charset=utf-8     <- proxy error page, not Debian's
#   Vary: Cookie

# HTTPS — the proxy can't tamper with it, returns the real file:
curl -sI https://deb.debian.org/debian/dists/trixie/Release | head
#   HTTP/2 200
#   server: Apache
#   last-modified: ...
#   x-clacks-overhead: GNU Terry Pratchett     <- genuinely from Debian
```

If HTTP gives a `text/html` 404 but HTTPS gives a `200`, the proxy is the
culprit. (A wrong system clock can cause similar errors, so also check `date`
is correct — but in this case the clock was fine.)

## Solution

Switch apt's sources from `http://` to `https://`. The proxy can't rewrite
encrypted traffic, so the requests reach the real mirrors.

On Raspberry Pi OS / Debian Trixie the sources live in the deb822 `.sources`
files under `/etc/apt/sources.list.d/`. Rewrite them in place:

```bash
sudo sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://archive.raspberrypi.com|https://archive.raspberrypi.com|g' \
  /etc/apt/sources.list.d/*.sources

# Clear the stale, 404'd package lists, then retry:
sudo rm -rf /var/lib/apt/lists/*
sudo apt update
```

> On older systems the sources may instead be in `/etc/apt/sources.list` and
> `/etc/apt/sources.list.d/*.list`. Run the same `sed` against those paths too
> if present.

Verify the change took:

```bash
cat /etc/apt/sources.list.d/*.sources
# All URIs should now start with https://
```

### Before

```text
Types: deb
URIs: http://deb.debian.org/debian/
Suites: trixie trixie-updates
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp
```

### After

```text
Types: deb
URIs: https://deb.debian.org/debian/
Suites: trixie trixie-updates
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.pgp
```

## Notes

- This is a property of the **network**, not the Pi. Using HTTPS sources works
  on any network, so there is no downside to leaving them as HTTPS permanently.
- If even **HTTPS** returns a 404 or times out, the network is blocking the
  package mirrors entirely. In that case either get `deb.debian.org` and
  `archive.raspberrypi.com` whitelisted, or run `apt update` from a different
  network (e.g. a phone hotspot).
