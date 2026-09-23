# Cloud Relay

The cloud relay lets remote attendees (parents, coaches, officials) follow the scoreboard from their phones over the internet, without adding load to the pool-deck Pi.

```text
Pi #1 ──── outbound WebSocket ────► Cloud VM (Docker + Caddy)
                                         │
                             HTTPS ◄─────┼───── attendees (phones, laptops)
                                         │
                                    /mobile  — scoreboard, results, schedule
                                    /admin   — key management
```

- Pi #1 opens a single outbound connection — works behind double-NAT with no port forwarding required.
- The cloud server re-emits events to all attendees; Pi #1 is unaffected by attendee load.
- Only scoreboard, results, and schedule data is exposed — meet files (`.lxf`, `.csv`) are never sent.
- Multiple organizers can share one cloud server simultaneously, each with their own revocable key.
- Caddy handles HTTPS and auto-renews Let's Encrypt certificates — no manual certificate management.

---

## Deploying the cloud server

**Requirements:**
- A Debian 12+ or Ubuntu 22+ VM (any cloud provider)
- Ports 80 and 443 open in the VM's firewall / security group
- A domain name with an `A` record pointing at the VM's public IP

SSH into the VM and run:

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/install.sh -o install.sh && bash install.sh cloud
```

The script handles everything interactively:

- Installs Docker, fail2ban, and unattended security upgrades
- Clones the repo and generates a `SECRET_KEY`
- Prompts for admin username and password
- Prompts for your domain name and updates `Caddyfile`
- Generates a `DEPLOY_SECRET` and installs the deploy webhook as a systemd service
- Configures `ufw` (ports 22, 80, 443 TCP + 443 UDP for HTTP/3)
- Builds and starts the compose stack (app + Caddy)

When it finishes, open `https://yourdomain/admin` and add organizers.

---

## Managing organizers

The `/admin` page (HTTP basic auth with the credentials from `.env`) lets you:

- **Add an organizer** — enter an organization name; a cryptographically random 32-byte key is generated automatically.
- **Revoke a key** — the Pi with that key will be disconnected and refused on next connect.
- **Delete a key** — removes it from the list entirely.
- **View active meets** — shows every Pi currently connected with its meet name, location, sport, organizer, console, and connection time.

The **Console** column is the console key the Pi reports (`cts_gen6`, `manual`, a plugin's
own key), so a support question — *which console was that meet running on?* — is answered
without phoning the operator. A meet whose console produces no times at all is marked
*no times* underneath: its spectators get no Results tab, by design
([No timing console?](admin.md#no-timing-console)). A Pi running a version from before the
console was reported shows `—`.

Share the generated key with the organizer. They paste it into their Pi's **Settings → Cloud** tab.

---

## Connecting a Pi to the cloud

In the admin UI on Pi #1 (`/settings` → **Cloud** tab):

| Field | Value |
| --- | --- |
| **Server URL** | `https://yourdomain` |
| **Relay Key** | Key from `/admin` on the cloud server |
| **Location** | Venue or city (auto-filled from Lenex if blank) |
| **Sport** | Optional — shown on the meet picker (e.g. `Swimming`) |

Click **Save**. The Pi connects immediately and appears in the cloud's meet picker.

---

## How attendees connect

Attendees go to `https://yourdomain` on any phone or browser:

- If one meet is active, the scoreboard opens directly.
- If multiple meets are active, a picker shows each meet's name, location, and sport.

The scoreboard (`/mobile`) has three tabs — **Scoreboard**, **Results**, and **Schedule** — and uses the same theme and display settings as the organizer's Pi.

On iOS, tap **Share → Add to Home Screen** for a full-screen app-like experience (the prompt appears automatically on first visit).

---

## Letting a QR code open the app

A poster at a pool carries a code for `https://yourdomain/add?server=<the pool's Pi>`
([`app.md`](app.md) `P-16`). With the app installed the phone opens it in the app; without
it, the browser lands on `https://yourdomain/add`, which shows which server the code
named and offers the store. The page works out of the box. The part that opens the **app**
needs two files, and the fingerprint in one of them is per-deployment.

Add to `cloud/.env`, then `docker compose up -d`:

```ini
# SHA-256 fingerprint of the Android signing certificate, comma-separated for more
# than one. With Play App Signing this is the value on the Play Console's
# App signing page — NOT the upload key's. From a keystore directly:
#   keytool -list -v -keystore release.jks -alias <alias>
ANDROID_CERT_FINGERPRINTS=14:6D:E9:…:44:E5

# Store listings, once the apps are published. Served from /picker/config and
# rendered on /add; absent means the buttons are hidden, never dead.
STORE_URL_ANDROID=https://play.google.com/store/apps/details?id=app.splouch.android
STORE_URL_IOS=https://apps.apple.com/ca/app/splouch/id…
```

Check it afterwards — both must be `200` with `content-type: application/json` and **no
redirect**, because Android follows none:

```bash
curl -i https://yourdomain/.well-known/assetlinks.json
curl -i https://yourdomain/.well-known/apple-app-site-association
```

`assetlinks.json` returns **404 until a fingerprint is set**. That is deliberate: an empty
but well-formed file looks deployed and fails later, silently, at install time on someone's
phone — where the only diagnosis is `adb shell pm get-app-links app.splouch.android`
reporting `1024` and the OS showing a chooser instead of opening the app. Android verifies
once, at install, and caches the answer, so a pool with no internet is unaffected.

**A debug build, without cutting a release.** Anything in `applinks.json` in the data
volume is *added* to what `.env` supplies, so a test fingerprint needs no compose edit and
no restart:

```bash
docker compose exec app sh -c 'cat > /data/applinks.json' <<'JSON'
{ "android_fingerprints": ["AA:11:…:FF:00"] }
JSON
```

The same file also accepts `store_android`, `store_ios`, `android_package` and
`ios_app_ids`.

**The operator's side.** Each Pi can hand its operator a poster-ready code — Settings →
**Cloud** → *Download QR code*. It carries **this cloud**, taken from the Pi's **Cloud →
Server URL**, so that field must be filled before the buttons appear. Both PNGs are ~10 cm
across at 300 dpi:

- **QR code with address** — the address is printed under the code. Use this for anything
  taped to a wall: it is the fallback when a camera will not focus, and the only way
  anyone can check the poster says the right thing.
- **QR code only** — the bare code, for a programme or a slide that already prints the
  address itself.

It deliberately does *not* carry the Pi's own `http://splouch.local:5000`. That address
resolves only for a phone already joined to the venue's wifi — a spectator on cellular
gets nothing, and a guest network with client isolation blocks it even for one that did
join. A poster cannot ask which network the reader is on, so it names the address that
works from anywhere; spectators on the pool's wifi can pick the Pi out of the app's own
server list afterwards, which is what that list is for.

---

## Updating the cloud server

Click **Update** in `/admin` — it fetches from GitHub and rebuilds the container automatically. The page polls until the server is back up, then reloads. Prefer it: it resolves the right ref for the way this server was installed, which the manual commands below leave to you.

To update over SSH, check which track the checkout is on first — `install.sh` offers two, and they update differently:

```bash
cd ~/Splouch && git branch --show-current
```

**`master`** — a development install. The branch tracks the remote, so a pull is enough:

```bash
git pull && cd cloud && docker compose up -d --build
```

**`release`** — a "Latest release" install. `install.sh` created this branch from a *tag*, so it has no upstream and `git pull` fails with *"There is no tracking information for the current branch"*. Move it to the tag you want instead:

```bash
git fetch --tags
git checkout -B release "$(git tag -l --sort=-version:refname | grep -E '^v[0-9]{4}\.[0-9]{2}\.[0-9]+$' | head -1)"
cd cloud && docker compose up -d --build
```

To switch a release install onto the development branch, name the remote branch so the upstream is set — after which `git pull` works there too:

```bash
git fetch origin && git checkout -B master origin/master
```

Caddy and the `data` volume (which stores `keys.json`) are preserved across updates. So is your edited `Caddyfile`, as long as you move between refs with `checkout` rather than `reset --hard` — the domain you set at install time lives in that tracked file and a hard reset would revert it.

## Moving or renaming the install directory

The deploy webhook runs as a systemd unit outside Docker, and systemd needs absolute
paths, so `install.sh` bakes the install directory into
`/etc/systemd/system/deploy-webhook.service`. Rename or move the checkout and that unit
stops starting — which takes the **version list and the Update button** in `/admin` with
it, since both are served by the webhook.

Re-run `install.sh` after the move (it rewrites the unit for the current directory), or
repair it by hand:

```bash
sudo grep -n OLD_NAME /etc/systemd/system/deploy-webhook.service
sudo sed -i 's|OLD_NAME|NEW_NAME|g' /etc/systemd/system/deploy-webhook.service
sudo systemctl daemon-reload && sudo systemctl restart deploy-webhook
systemctl status deploy-webhook --no-pager
```

Then check nothing else kept the old path:

```bash
sudo grep -rl OLD_NAME /etc/systemd/system/ /etc/sudoers.d/ 2>/dev/null
```

Docker is unaffected: the compose project is named after the directory holding the
compose file (`cloud`), not its parent, so containers and the `data` volume that stores
`keys.json` survive a rename of the checkout.

## Logs

```bash
cd ~/Splouch/cloud && docker compose logs -f
```
