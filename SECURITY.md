# Security Policy

## Reporting a vulnerability

**Don't open a public issue.** Report privately through GitHub:

* [Report a vulnerability](https://github.com/olivierouellet/Splouch/security/advisories/new)
  from the repository's **Security** tab — visible only to the maintainer, or
* Contact [@olivierouellet](https://github.com/olivierouellet) directly.

Useful to include:

* Which component — Pi #1 (server), Pi #2 (Qt kiosk), the admin UI, or the cloud relay.
* Whether the attacker needs a login, a position on the pool-deck LAN, a page opened in
  the operator's browser, or nothing at all.
* A request, file, or sequence that shows it. A `.lxf` or `.csv` that misbehaves is
  ideal — meet files are the main untrusted input.

This is a one-maintainer project that gets most of its attention on weekends around swim
meets. Expect acknowledgement within 2 weeks; a fix takes as long as it takes, and you'll
be told where it stands. Credit in the release notes if you'd like it, and no objection
to you publishing once a fix has shipped.

---

## Supported versions

| Version | Supported |
| --- | --- |
| Latest release tag (for example `v2026.09.3`) | ✅ Fixes land here |
| `master` | ✅ Fixes land here first |
| Any earlier tag | ❌ No backports — update instead |

Releases are calendar-versioned (`vYYYY.MM.N`). There is no long-term support branch:
updating is a button in **Settings → Update & Backup**, or a `git pull` and a service
restart. Update Pi #1 first, then Pi #2 to the same version — see
[docs/installation.md](docs/installation.md#updating).

---

## What Splouch assumes

Most of the design rests on the network in the [README](README.md#network): an unmanaged
switch on the pool deck, with no route to the internet. Reports are most useful when they
break one of these assumptions rather than start from a different one.

| | |
| --- | --- |
| **The pool-deck LAN is trusted for reading** | Anyone on the switch can watch the scoreboard, results, and schedule. That is the point of it. |
| **Changing anything needs a session** | Meet files, settings, updates, backups, the serial terminal — all of it is behind the admin login, including the WebSockets, not just the pages that open them. |
| **Meet files are untrusted text** | Swimmer and club names come from whoever typed them into Splash. They reach templates escaped, and the Lenex parser refuses a `DOCTYPE` outright. |
| **Pi #1 is never internet-facing** | Remote viewing goes through the cloud relay, which Pi #1 reaches by an *outbound* WebSocket. No port forwarding, no inbound anything. |
| **The relay carries board data only** | Scoreboard, results, schedule. Meet files (`.lxf`, `.csv`) are never sent to it. |
| **Each organizer's relay key is revocable** | Keys are per-organizer and can be withdrawn from the cloud admin panel, which throttles guesses. |

### Out of scope

* Physical access to either Pi, or to the serial line.
* Exposing Pi #1 directly to the internet, or putting it on a hostile network instead of
  the isolated switch.
* A self-hosted cloud VM's own configuration — your firewall, your DNS, your Docker host.
  What the relay *serves* is in scope; the VM under it is yours.
* Local decoders dropped in `~/SplouchData/console_decoders/`. They are arbitrary Python,
  loaded on purpose, and anyone who can write there can already run code as the service
  user.
* The shipped admin password before you change it. It is a default, not a secret, and the
  Settings page nags until it is replaced.

---

## Track record

A security audit in September 2026 found and fixed defects in session key handling,
WebSocket origin checking, terminal access, start-list escaping, backup extraction,
update targets, and the installer's sudo grant.

Every one of them is pinned to a regression test in
[`tests/test_security_hardening.py`](tests/test_security_hardening.py), grouped by what
the attacker needed to be able to do — from "nothing at all" upward. It is worth reading
before reporting: it documents the shape of the bugs this project has already had, and
the tests that stop them coming back.

---

## Hardening your install

1. **Change the admin password.** The default is public; the Settings page warns until
   you replace it.
2. **Keep Pi #1 and Pi #2 on the same, current release.**
3. **Keep the pool-deck switch off the internet.** Use the cloud relay for remote
   viewing — that is what it exists for.
4. **Serve the relay over HTTPS.** `install.sh` sets Caddy up with Let's Encrypt and
   auto-renewal; see [docs/cloud.md](docs/cloud.md).
5. **Revoke relay keys after a meet** if the organizer no longer needs one.
