<p><a href="#installation">English</a> · <a href="#installation-fr">Français</a></p>

# Installation

## Requirements

| | Minimum |
| --- | --- |
| Raspberry Pi OS | **Trixie** (October 2025) |
| Python | **3.13** (included in Trixie) |

Earlier releases (Bookworm / Python 3.11) are not supported.

---

## Hardware

| Item | Purpose |
| --- | --- |
| Raspberry Pi 3B+ or 4 | **Pi #1** — serial decoder + scoreboard server + admin UI |
| Raspberry Pi 4 (64-bit OS) | **Pi #2** — Qt scoreboard driving the TV |
| Router with internet access | Pool-deck network for all devices; carries Pi #1 to the Cloud |
| Cat5e cables | Pi #1 and Pi #2 ↔ router |
| Console serial adapter | Depends on your timing console — see the per-console guides in [`docs/consoles/`](consoles/) |

The console-specific adapter and wiring (USB-to-RS232 vs RS-485, tap cable, pinout) live in
the per-console guides, and a summary for the selected console is shown in **Settings →
Timing**.

Topology:

```text
Timing console
      │  serial tap / adapter (see docs/consoles/)
   Pi #1 ── eth0 ──┐
                   ├── Router ── internet (Cloud)
   Pi #2 ── eth0 ──┤
   Laptop ─────────┘  (Ethernet or the router's WiFi)
```

A router is required: it hands out addresses (DHCP) and gives Pi #1 the internet access the
Cloud needs. A bare switch with no router is not supported.

---

## Network & firewall

All pool-deck devices join the router's network — the Pis over `eth0`. Pi #1 can also join
WiFi (`wlan0`), handy for SSH at home.

| Device | Address | Role |
| --- | --- | --- |
| Pi #1 | `splouch.local` (hostname, via mDNS) | Serial decoder + FastAPI server + admin UI |
| Pi #2 | automatic | Qt kiosk — scoreboard on the TV |
| Laptop | automatic | Admin browser to `http://splouch.local` |

Devices find Pi #1 by name, so no IP needs to be configured: the router assigns one by DHCP.
The installer offers a static address instead, for typing a raw IP; DHCP is the default.

**Firewall:** both Pis deny incoming traffic by default and allow everything arriving on
`eth0` and `wlan0`.

---

## Pi #1 — Server

Flash **Raspberry Pi OS Trixie** using Raspberry Pi Imager. Enable SSH during flash.

> **Tip:** Configure WiFi in Imager before flashing. The Pi will have `wlan0` (home WiFi) and `eth0` (pool network) active simultaneously — useful for SSH access at home.

SSH in and run:

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/setup.sh -o setup.sh && bash setup.sh server
```

The script:

- Asks which version to install — master, one of the 10 latest releases, or one you type — and runs that version's own installer
- Installs Python dependencies via `uv`
- Creates the `splouch` systemd service (starts on boot)
- Adds the user to the `dialout` group for serial port access
- Creates `~/SplouchData/` with `meet/`, `images/`, `icons/`, and `recorded/` subdirectories
- Copies `settings.default.json` to `~/SplouchData/settings.json`
- Downloads xterm.js
- Asks how `eth0` gets its address: **DHCP** (default) or a **static IP** you enter, with its router and DNS (validated: must be a host address, router inside the same subnet). Choosing DHCP on a re-run clears a static address set earlier. Both live in the `splouch-eth` profile that **Settings → Network** edits too. The change is saved, then applied as the very last step (by the reboot, or 3 s after the script exits), so an SSH session over Ethernet only drops once the install is done — reconnect to `splouch.local` or the new IP
- Sets the hostname to `splouch` (accessible as `splouch.local` on the network)

---

## Pi #2 — Kiosk

Flash **Raspberry Pi OS Trixie — Desktop, 64-bit** with SSH enabled. Desktop (not Lite)
because the display needs a graphical session; 64-bit because PySide6 ships no 32-bit
Raspberry Pi wheel.

SSH in and run:

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/setup.sh -o setup.sh && bash setup.sh kiosk
```

The script:

- Clones the repo to `~/Splouch` at the **same version** you install on Pi #1 — display
  and server must agree on the WebSocket contract
- Installs Qt via `uv sync --extra scoreboard` (PySide6; only this role pulls Qt)
- Writes the server address to `~/.config/splouch/scoreboard.env`
- Enables desktop autologin and autostarts the [Qt scoreboard](../scoreboard/README.md)
  fullscreen on boot
- Forces 1920×1080 HDMI output
- Enables VNC (RealVNC) for remote access — find the kiosk's address in the router
- Turns on the same firewall as Pi #1

> **Install the same version on both Pis.** The kiosk now runs code, not just a browser.
> Pick the same answer at the version prompt on Pi #1 and Pi #2.
>
> Pi #1 must be running and reachable before the kiosk boots — though the display no
> longer needs it at startup: it opens immediately and connects when the server appears.

### Leaving and reopening the scoreboard

| key | effect |
| --- | --- |
| **F1** | open the display menu — update, restart, quit, versions |
| **Ctrl+Q** | quit to the desktop |
| **F11** or **Ctrl+F** | toggle fullscreen |
| **Esc** | close the menu, else leave fullscreen (never quits) |

Quitting with Ctrl+Q returns you to the desktop and stays there — it is treated as
deliberate, so nothing relaunches. Double-click the **Scoreboard** icon on the desktop
to start it again. A **Settings** icon opens the server's admin page in a browser.

### The display menu (F1)

A keyboard plugged into the TV Pi is all this needs, which is the point: it works
when the server's admin page is not to hand, and when the link to the server is
down. It shows what version this display and its server are on, whether the two are
in step, and whether the link is up — then offers to **update to the server's
version**, **restart the display**, or **quit to the desktop**.

Arrows or `1`–`3` choose, Enter confirms, Esc closes. The update prints its progress
on the TV and restarts the display when it finishes; if it fails, the display stays
on the version it was already running and the panel says why.

**Nothing on the menu will act while a race is running.** Every entry blanks the TV
for a few seconds at least, and F1-then-a-digit is two keystrokes. Ctrl+Q is still
the unconditional way out.

> This is the third way to update a display, and the only one that works on a kiosk
> installed before v2026.09.0 — see [Update displays](admin.md#updating-the-displays).

Useful commands on the kiosk:

```bash
~/Splouch/install/scripts/start-scoreboard.sh                    # run it by hand
cd ~/Splouch && .venv/bin/python -m scoreboard --windowed         # windowed, for testing
cd ~/Splouch && uv sync --extra scoreboard                        # reinstall Qt
```

> **`--extra scoreboard` is not optional here.** PySide6 is an optional dependency
> so the server Pi and the cloud VM never pull Qt, and `uv sync` without it will
> *remove* PySide6 from this venv — after which the display cannot start. If a
> kiosk ever comes back to a `ModuleNotFoundError: PySide6`, that is what happened;
> the command above puts it back.

> **"There is no tracking information for the current branch."** A display that has
> taken a remote update sits on the local branch `display`, pinned to the commit the
> server was on. That branch tracks a commit, not a branch, so there is nothing for
> `git pull` to pull from. It is not a broken checkout — `install.sh kiosk` skips the
> pull and moves to the right ref on its own.

### Upgrading a kiosk from the Chromium display

Re-run `bash install.sh kiosk`. The script removes every autostart line this project has
written before adding the Qt launcher, so there is nothing to uninstall first. Chromium
itself is left installed; it is simply no longer started.

> **If the TV still shows the old web page after an update**, the Pi is starting Chromium
> *as well as* the Qt display; the browser starts faster and ends up on top. Re-running the
> installer clears it. To check by hand:
>
> ```bash
> grep -n -e kiosk -e chromium ~/.config/labwc/autostart
> ```
>
> One `# Splouch kiosk` block naming `start-scoreboard.sh`, and nothing else, is correct.

---

## Cloud server

See [cloud.md](cloud.md) for deploying the optional public relay server.

---

## Credentials

### Pi server

| Credential | Where set | Default | Action |
| --- | --- | --- | --- |
| **Pi user password** | Raspberry Pi Imager, before flashing | *(you choose)* | Needed for SSH and sudo |
| **Admin UI username** | `~/SplouchData/settings.json` | `score` | Change before meet day: user menu (bottom of the sidebar) → **Change password** |
| **Admin UI password** | `~/SplouchData/settings.json` | `swimming` | Change before meet day: user menu (bottom of the sidebar) → **Change password** |

### Cloud

| Credential | Where set | Default | Action |
| --- | --- | --- | --- |
| **SSH key** | Must exist on the server before running the install | — | Required — the script copies `root`'s `authorized_keys` to the `splouch` user; password auth is disabled after install |
| **`splouch` Linux password** | Prompted by the install script | *(you choose)* | Needed for SSH login and sudo after install |
| **`/admin` panel username** | Prompted by the install script | `admin` | Seeded from `cloud/.env`; change in `/admin` → user menu → **Change password** |
| **`/admin` panel password** | Prompted by the install script | *(you choose)* | Seeded from `cloud/.env`; change in `/admin` → user menu → **Change password** |
| **`SECRET_KEY`** | Auto-generated by the install script | — | Stored in `cloud/.env`; no need to record |
| **`DEPLOY_SECRET`** | Auto-generated by the install script | — | Stored in `cloud/.env`; no need to record |
| **Organizer relay keys** | Generated in `/admin` after install | — | Share each key with the corresponding Pi operator |

---

## Running

The service starts automatically on boot. To control it over SSH, see
[Service management](admin.md#service-management).

---

## Updating

The easiest way is from the **Update & Backup** tab in the admin UI — it pulls the latest release, syncs dependencies, and restarts the service.

To update manually over SSH:

```bash
cd ~/Splouch
git pull
uv sync
sudo systemctl restart splouch
```

**Pi #2 must now be updated too.** The kiosk used to reload the scoreboard from Pi #1 on
every boot, so a reboot was enough. It now runs the Qt display from its own checkout, so
it needs the same version as Pi #1 or the two can disagree on the WebSocket contract:

```bash
cd ~/Splouch
git pull
uv sync --extra scoreboard
sudo reboot
```

Update Pi #1 first, then Pi #2 to the same version. Easier: **Settings → Update & Backup →
Update displays** moves every connected kiosk to the version the server is on.

---

## Reinstalling

If the server is down and the web UI is unreachable, re-run the install script directly on Pi #1.

**From the desktop** — double-click the **Reinstall Splouch** icon created during install.

**From the terminal:**

```bash
bash ~/Splouch/install/reinstall.sh
# or pass the role directly:
bash ~/Splouch/install/reinstall.sh server
```

---

<a id="installation-fr"></a>

## Installation — Français

<p><a href="#installation">English</a> · <a href="#installation-fr">Français</a></p>

### Prérequis

| | Minimum |
| --- | --- |
| Raspberry Pi OS | **Trixie** (octobre 2025) |
| Python | **3.13** (inclus dans Trixie) |

Les versions antérieures (Bookworm / Python 3.11) ne sont pas prises en charge.

---

### Matériel

| Matériel | Utilité |
| --- | --- |
| Raspberry Pi 3B+ ou 4 | **Pi n° 1** — décodeur série + serveur du tableau + interface d'administration |
| Raspberry Pi 4 (OS 64 bits) | **Pi n° 2** — tableau Qt affiché sur le téléviseur |
| Routeur avec accès internet | Réseau du bord de piscine pour tous les appareils ; relie le Pi n° 1 au Cloud |
| Câbles Cat5e | Pi n° 1 et Pi n° 2 ↔ routeur |
| Adaptateur série de la console | Selon votre console de chronométrage — voir les guides par console dans [`docs/consoles/`](consoles/) |

L'adaptateur et le câblage propres à chaque console (USB-RS232 ou RS-485, câble de dérivation,
brochage) sont décrits dans les guides par console, et un résumé pour la console choisie est
affiché dans **Réglages → Chronométrage**.

Topologie :

```text
Console de chronométrage
      │  dérivation série / adaptateur (voir docs/consoles/)
   Pi n° 1 ── eth0 ──┐
                     ├── Routeur ── internet (Cloud)
   Pi n° 2 ── eth0 ──┤
   Portable ─────────┘  (Ethernet ou WiFi du routeur)
```

Un routeur est requis : il distribue les adresses (DHCP) et donne au Pi n° 1 l'accès internet
dont le Cloud a besoin. Un simple commutateur sans routeur n'est pas pris en charge.

---

### Réseau et pare-feu

Tous les appareils du bord de piscine rejoignent le réseau du routeur — les Pi par `eth0`. Le
Pi n° 1 peut aussi rejoindre un WiFi (`wlan0`), pratique pour SSH à la maison.

| Appareil | Adresse | Rôle |
| --- | --- | --- |
| Pi n° 1 | `splouch.local` (nom d'hôte, via mDNS) | Décodeur série + serveur FastAPI + interface d'administration |
| Pi n° 2 | automatique | Kiosque Qt — tableau sur le téléviseur |
| Portable | automatique | Navigateur d'administration vers `http://splouch.local` |

Les appareils trouvent le Pi n° 1 par son nom ; aucune IP n'est donc à configurer : le routeur
en attribue une par DHCP. L'installateur propose plutôt une adresse statique, pour qui veut
taper une IP brute ; le DHCP est le choix par défaut.

**Pare-feu :** les deux Pi refusent par défaut le trafic entrant et acceptent tout ce qui
arrive sur `eth0` et `wlan0`.

---

### Pi n° 1 — Serveur

Flashez **Raspberry Pi OS Trixie** avec Raspberry Pi Imager. Activez SSH lors du flashage.

> **Astuce :** configurez le WiFi dans Imager avant de flasher. Le Pi aura `wlan0` (WiFi de la maison) et `eth0` (réseau de la piscine) actifs en même temps — utile pour y accéder en SSH à la maison.

Connectez-vous en SSH et lancez :

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/setup.sh -o setup.sh && bash setup.sh server
```

Le script :

- Demande quelle version installer — master, une des 10 dernières versions, ou une version saisie — et lance l'installateur de cette version
- Installe les dépendances Python via `uv`
- Crée le service systemd `splouch` (démarré au boot)
- Ajoute l'utilisateur au groupe `dialout` pour l'accès au port série
- Crée `~/SplouchData/` avec les sous-dossiers `meet/`, `images/`, `icons/` et `recorded/`
- Copie `settings.default.json` vers `~/SplouchData/settings.json`
- Télécharge xterm.js
- Demande comment `eth0` obtient son adresse : **DHCP** (par défaut) ou une **IP statique** que vous saisissez, avec son routeur et son DNS (validés : ce doit être une adresse d'hôte, et le routeur doit être dans le même sous-réseau). Choisir DHCP lors d'une nouvelle exécution efface une adresse statique définie auparavant. Les deux vivent dans le profil `splouch-eth`, que **Réglages → Réseau** modifie aussi. Le changement est enregistré, puis appliqué en toute dernière étape (au redémarrage, ou 3 s après la fin du script) : une session SSH par Ethernet ne tombe donc qu'une fois l'installation terminée — reconnectez-vous à `splouch.local` ou à la nouvelle IP
- Règle le nom d'hôte à `splouch` (joignable en `splouch.local` sur le réseau)

---

### Pi n° 2 — Kiosque

Flashez **Raspberry Pi OS Trixie — Desktop, 64 bits** avec SSH activé. Desktop (pas Lite)
parce que l'affichage a besoin d'une session graphique ; 64 bits parce que PySide6 ne fournit
aucun paquet (wheel) Raspberry Pi 32 bits.

Connectez-vous en SSH et lancez :

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/setup.sh -o setup.sh && bash setup.sh kiosk
```

Le script :

- Clone le dépôt dans `~/Splouch` à la **même version** que celle installée sur le Pi n° 1 —
  l'afficheur et le serveur doivent s'accorder sur le contrat WebSocket
- Installe Qt via `uv sync --extra scoreboard` (PySide6 ; seul ce rôle installe Qt)
- Écrit l'adresse du serveur dans `~/.config/splouch/scoreboard.env`
- Active la connexion automatique au bureau et lance le [tableau Qt](../scoreboard/README.md)
  en plein écran au démarrage
- Force la sortie HDMI en 1920×1080
- Active VNC (RealVNC) pour l'accès à distance — l'adresse du kiosque se trouve dans le routeur
- Active le même pare-feu que sur le Pi n° 1

> **Installez la même version sur les deux Pi.** Le kiosque exécute désormais du code, pas
> seulement un navigateur. Donnez la même réponse à la question de version sur le Pi n° 1 et
> le Pi n° 2.
>
> Le Pi n° 1 doit être en marche et joignable avant le démarrage du kiosque — même si
> l'afficheur n'en a plus besoin au lancement : il s'ouvre aussitôt et se connecte dès que le
> serveur apparaît.

#### Quitter et rouvrir le tableau

| touche | effet |
| --- | --- |
| **F1** | ouvre le menu de l'afficheur — mise à jour, redémarrage, quitter, versions |
| **Ctrl+Q** | quitte vers le bureau |
| **F11** ou **Ctrl+F** | bascule le plein écran |
| **Échap** | ferme le menu, sinon quitte le plein écran (ne quitte jamais l'application) |

Quitter avec Ctrl+Q ramène au bureau et y reste — c'est considéré comme volontaire, donc rien
ne se relance. Double-cliquez sur l'icône **Scoreboard** du bureau pour le relancer. Une icône
**Settings** ouvre la page d'administration du serveur dans un navigateur.

#### Le menu de l'afficheur (F1)

Un clavier branché sur le Pi du téléviseur suffit, et c'est tout l'intérêt : il fonctionne
quand la page d'administration du serveur n'est pas sous la main, et quand le lien avec le
serveur est coupé. Il indique la version de cet afficheur et celle de son serveur, si les deux
concordent et si le lien est actif — puis propose de **mettre à jour vers la version du
serveur**, de **redémarrer l'afficheur** ou de **quitter vers le bureau**.

Les flèches ou `1`–`3` choisissent, Entrée confirme, Échap ferme. La mise à jour affiche sa
progression sur le téléviseur et redémarre l'afficheur une fois terminée ; si elle échoue,
l'afficheur reste sur la version qu'il exécutait déjà et le panneau en donne la raison.

**Aucune entrée du menu n'agit pendant une course.** Chacune éteint le téléviseur au moins
quelques secondes, et F1 suivi d'un chiffre ne fait que deux touches. Ctrl+Q reste la sortie
inconditionnelle.

> C'est la troisième façon de mettre à jour un afficheur, et la seule qui fonctionne sur un
> kiosque installé avant la v2026.09.0 — voir [Update displays](admin.md#updating-the-displays).

Commandes utiles sur le kiosque :

```bash
~/Splouch/install/scripts/start-scoreboard.sh                    # le lancer à la main
cd ~/Splouch && .venv/bin/python -m scoreboard --windowed         # en fenêtre, pour tester
cd ~/Splouch && uv sync --extra scoreboard                        # réinstaller Qt
```

> **`--extra scoreboard` n'est pas facultatif ici.** PySide6 est une dépendance optionnelle,
> pour que le Pi serveur et la VM cloud n'installent jamais Qt, et `uv sync` sans cette option
> *retire* PySide6 de cet environnement — après quoi l'afficheur ne peut plus démarrer. Si un
> kiosque affiche un jour `ModuleNotFoundError: PySide6`, c'est ce qui s'est passé ; la
> commande ci-dessus le remet en place.

> **« There is no tracking information for the current branch. »** Un afficheur qui a reçu une
> mise à jour à distance se trouve sur la branche locale `display`, fixée au commit sur lequel
> était le serveur. Cette branche suit un commit, pas une branche, donc `git pull` n'a rien à
> tirer. Ce n'est pas un dépôt cassé — `install.sh kiosk` saute le pull et se place de
> lui-même sur la bonne référence.

#### Passer de l'afficheur Chromium au kiosque Qt

Relancez `bash install.sh kiosk`. Le script retire toutes les lignes de démarrage automatique
que ce projet a écrites avant d'ajouter le lanceur Qt ; il n'y a donc rien à désinstaller
d'abord. Chromium reste installé ; il n'est simplement plus lancé.

> **Si le téléviseur affiche encore l'ancienne page web après une mise à jour**, le Pi lance
> Chromium *en plus* de l'afficheur Qt ; le navigateur démarre plus vite et finit au premier
> plan. Relancer l'installateur corrige le problème. Pour vérifier à la main :
>
> ```bash
> grep -n -e kiosk -e chromium ~/.config/labwc/autostart
> ```
>
> Un seul bloc `# Splouch kiosk` qui nomme `start-scoreboard.sh`, et rien d'autre, est correct.

---

### Serveur cloud

Voir [cloud.md](cloud.md) pour déployer le serveur relais public, facultatif.

---

### Identifiants

#### Serveur Pi

| Identifiant | Où il est défini | Par défaut | Action |
| --- | --- | --- | --- |
| **Mot de passe de l'utilisateur Pi** | Raspberry Pi Imager, avant le flashage | *(à votre choix)* | Requis pour SSH et sudo |
| **Nom d'utilisateur de l'administration** | `~/SplouchData/settings.json` | `score` | À changer avant la compétition : menu utilisateur (bas de la barre latérale) → **Changer le mot de passe** |
| **Mot de passe de l'administration** | `~/SplouchData/settings.json` | `swimming` | À changer avant la compétition : menu utilisateur (bas de la barre latérale) → **Changer le mot de passe** |

#### Relais cloud

| Identifiant | Où il est défini | Par défaut | Action |
| --- | --- | --- | --- |
| **Clé SSH** | Doit exister sur le serveur avant l'installation | — | Obligatoire — le script copie les `authorized_keys` de `root` vers l'utilisateur `splouch` ; l'authentification par mot de passe est désactivée après l'installation |
| **Mot de passe Linux de `splouch`** | Demandé par le script d'installation | *(à votre choix)* | Requis pour la connexion SSH et sudo après l'installation |
| **Nom d'utilisateur du panneau `/admin`** | Demandé par le script d'installation | `admin` | Initialisé depuis `cloud/.env` ; à changer dans `/admin` → menu utilisateur → **Changer le mot de passe** |
| **Mot de passe du panneau `/admin`** | Demandé par le script d'installation | *(à votre choix)* | Initialisé depuis `cloud/.env` ; à changer dans `/admin` → menu utilisateur → **Changer le mot de passe** |
| **`SECRET_KEY`** | Généré automatiquement par le script | — | Enregistré dans `cloud/.env` ; inutile de le noter |
| **`DEPLOY_SECRET`** | Généré automatiquement par le script | — | Enregistré dans `cloud/.env` ; inutile de le noter |
| **Clés de relais des organisateurs** | Générées dans `/admin` après l'installation | — | Transmettez chaque clé à l'opérateur du Pi correspondant |

---

### Fonctionnement

Le service démarre automatiquement au boot. Pour le contrôler par SSH, voir
[Gestion du service](admin.md#gestion-du-service).

---

### Mise à jour

Le plus simple passe par l'onglet **Mise à jour et sauvegarde** de l'interface
d'administration — il récupère la dernière version, synchronise les dépendances et redémarre
le service.

Pour mettre à jour à la main par SSH :

```bash
cd ~/Splouch
git pull
uv sync
sudo systemctl restart splouch
```

**Le Pi n° 2 doit désormais être mis à jour lui aussi.** Le kiosque rechargeait autrefois le
tableau depuis le Pi n° 1 à chaque démarrage, si bien qu'un redémarrage suffisait. Il exécute
maintenant l'afficheur Qt depuis son propre dépôt ; il lui faut donc la même version que le
Pi n° 1, sinon les deux peuvent diverger sur le contrat WebSocket :

```bash
cd ~/Splouch
git pull
uv sync --extra scoreboard
sudo reboot
```

Mettez d'abord à jour le Pi n° 1, puis le Pi n° 2 à la même version. Plus simple :
**Réglages → Mise à jour et sauvegarde → Mettre à jour les afficheurs** fait passer chaque
kiosque connecté à la version du serveur.

---

### Réinstallation

Si le serveur est arrêté et l'interface web injoignable, relancez le script d'installation
directement sur le Pi n° 1.

**Depuis le bureau** — double-cliquez sur l'icône **Reinstall Splouch** créée à l'installation.

**Depuis le terminal :**

```bash
bash ~/Splouch/install/reinstall.sh
# ou indiquez directement le rôle :
bash ~/Splouch/install/reinstall.sh server
```
