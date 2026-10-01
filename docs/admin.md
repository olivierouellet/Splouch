<p><a href="#admin-guide">English</a> · <a href="#admin-guide-fr">Français</a></p>

# Admin Guide

The admin UI is at `http://splouch.local/settings`. The default login is in
[Credentials](installation.md#credentials) — change it before meet day from the user menu at
the bottom of the sidebar → **Change password**.

---

## Pages

| URL | Description |
| --- | --- |
| `/` | Redirects to `/live` |
| `/live` | The scoreboard (lane count from Meet Setup settings) — the reference display, and what the Qt board mirrors |
| `/operator` | Operator control view |
| `/manual` | Manual heat control — set the current event and heat by hand ([guide](consoles/manual.md)) |
| `/mobile` | Mobile shell — three-tab view (Scoreboard, Results, Schedule) |
| `/results` | Results after each heat |
| `/schedule` | Meet schedule with start times and heat entry lists |
| `/console` | Live serial console viewer |
| `/settings` | Admin settings (login required) |

Append `?test` to `/live` to overlay mode buttons (Intro, Running, Results, Next Heat) on the board — useful for testing without a live console.

---

## Meet-day workflow

1. In Splash Meet Manager: **File → Export → Lenex** → save as a `.lxf` file.
2. In **Settings → Meet Setup**, click **Add Meet File** and upload the `.lxf` — swimmer and
   club names go live on the scoreboard immediately. *(Alternatively, upload a Hytek `.csv`
   event schedule so event names appear in the header.)*
3. Turn on Pi #2: the TV boots straight into the scoreboard. Any other screen can open
   `http://splouch.local/` in a browser.
4. Start the timing console — times appear automatically as heats run.

> Prefer the command line? See [Manual and CLI reference](#manual-and-cli-reference) for
> placing meet files directly in `~/SplouchData/meet/`.

### No timing console?

Set **Settings → Timing → Timing Equipment** to **Manual — no timing console** and drive
the meet from `/manual` on a phone: hold Previous/Next to step through the heats, or
tap a heat to preview its swimmers and commit it with **▸**. The boards then show the
event, the heat, the event name, the heat time and every swimmer — everything but the
times, which need a console. Full guide: [manual.md](consoles/manual.md).

Worth knowing even with a console: a Daktronics Omnisport 2000 times races but sends
no event or heat number at all, so `/manual` is how you supply it.

---

## Settings tabs

| Tab | Description |
| --- | --- |
| **Meet Setup** | Upload Lenex `.lxf` / Hytek `.csv` meet files; pool length, touchpads, lane count |
| **Timing** | Console type and serial port, connection status, serial monitor (raw hex packets), finish debounce. Choosing **Manual — no timing console** drops the port, badge and monitor and links to `/manual` |
| **Clock** | Sync with NTP; set date and time manually when offline; install/remove Adafruit PiRTC (DS3231) hardware clock |
| **Display** | Show/hide column headers and columns (Name, Club, Delta, Position); podium highlighting |
| **Theme** | Built-in colour schemes; override individual colours and fonts; save as a custom theme |
| **Network** | WiFi management; Ethernet DHCP or static IP (address, router, DNS); view connected scoreboard clients |
| **Update & Backup** | Pull latest version from GitHub, sync dependencies, restart; download or restore a backup of `~/SplouchData` |
| **Test** | Play back pre-recorded sessions; adjust playback speed; record live serial sessions. Safe to run with a meet loaded — see [Test sessions](#test-sessions) |
| **Terminal** | In-browser terminal — Shell, raspi-config, Scoreboard logs, dmesg, serial ports |
| **Cloud** | Cloud relay URL and key; per-meet picker appearance (title, image, home icon, location, sport) |
| **Power** | Restart the app service, reboot, or shut down the Pi — press-and-hold to confirm |
| **Account** | Change the admin UI username and password (via the sidebar account menu) |

> In the sidebar, **Display / Theme** live under the **Scoreboard** group, and **Test /
> Terminal** under **Debug**; **Account** opens from the user menu at the bottom.

---

## Data folders on Pi #1

| Path | Contents |
| --- | --- |
| `~/SplouchData/meet/` | Lenex `.lxf` and Hytek `.csv` meet files (uploaded via Meet Setup, or [placed here manually](#manual-and-cli-reference)) |
| `~/SplouchData/images/` | Sponsor or club logo images for the splash screen |
| `~/SplouchData/icons/` | Home-screen icon for the phone pages (Cloud tab) |
| `~/SplouchData/picker/` | Meet image shown on the cloud's meet picker (Cloud tab) |
| `~/SplouchData/logs/` | Logs saved from the Terminal tab |
| `~/SplouchData/recorded/` | Custom recorded sessions for playback in the Test tab |
| `~/SplouchData/test_meet/` | Start lists for a running test session — cleared when it ends, never mixed with `meet/` |
| `~/SplouchData/themes/` | Custom theme `.toml` files |
| `~/SplouchData/console_decoders/` | Local-only decoder plugins (`.py` files) — loaded at startup, not tracked by git |
| `~/SplouchData/settings.json` | All admin UI settings |

---

## Updating the displays

Three ways, and which one you reach for depends on what is in front of you.

| From | How | Use when |
| --- | --- | --- |
| The server's admin page | Settings → Update & Backup → **Update displays** | The usual way. Moves every *registered* display to the ref this server is on. Update the server first. |
| The display itself | **F1** on the TV's keyboard → *Update to the server's version* | No browser to hand, or the display is too old for the button above to see it. |
| An SSH session | `bash install.sh kiosk` on the TV Pi | The display will not start, or is so old it does not have the menu. |

All three land on the **same commit the server is running** — never a branch, so
the two ends cannot drift apart and disagree about the WebSocket contract. The
server must be on a clean commit that has been pushed; being off a release tag is
fine, being dirty is not.

> **"Update displays" says no displays are registered, but I can see one.** A
> display announces itself with a `register` frame, and only the Qt scoreboard
> sends one — a browser tab does not, and a Chromium kiosk showing `/live` *is* a
> browser tab. An announced display shows a hostname, a `kiosk` badge and a version
> in the list; a row with only an IP is a browser, or a kiosk installed before
> v2026.09.0 when the Qt display replaced Chromium.
>
> That older kiosk cannot be rescued remotely: it is too old to announce itself and
> too old to act on the update it would be sent. Do the first hop at the display —
> F1 if it has the menu, otherwise `bash install.sh kiosk` — and the remote button
> works from then on. See also [Upgrading a kiosk from the Chromium
> display](installation.md#upgrading-a-kiosk-from-the-chromium-display).

> **A display that updated and now will not start.** Versions before this fix ran a
> bare `uv sync` when they updated themselves. `uv sync` makes the environment match
> the lockfile for the extras it was *given* and removes everything else, and the
> kiosk's Qt lives in the optional `scoreboard` extra — so the update uninstalled
> PySide6 and the app came back to a stack trace. On the Pi:
>
> ```bash
> cd ~/Splouch && uv sync --extra scoreboard
> install/scripts/start-scoreboard.sh
> ```
>
> Re-running `install.sh kiosk` does the same thing, and from this version the
> display syncs the extra itself and refuses to restart into a checkout whose Qt
> will not import.

---

## Test sessions

The Test tab replays a recorded console session, so the board behaves exactly as it
does during a real race. Two things used to make that awkward, and neither does now.

**Your meet stays loaded.** A recording's event and heat numbers refer to the start
lists in the companion `.lxf` shipped beside it, so that is what a replay runs
against. Your own meet is held in place while it does: the files in
`~/SplouchData/meet/` are never touched, the meet is still the active one, and it is
reloaded the moment the session ends — whether you press **Stop** or the recording
simply runs out. Deleting the meet first and re-uploading it afterwards is no longer
part of the job.

**Keep this test local.** Ticked, the replay reaches the TV display and phones on the
pool's own network, and nothing else: the cloud link is closed for the duration, so
spectators watching remotely see the meet as offline rather than a recording dressed
up as the race in front of them. It is ticked and locked whenever a meet is loaded —
publishing invented times under a live meet's identity is not something a checkbox
should allow. With no meet loaded it is yours to set, and the choice is remembered.

When the session ends, every board is wiped of the replay, the meet comes back, the
cloud link is restored if it was up before, and playback speed returns to 1×.

Starting a session clears the console's idea of which event and heat is on, and
ending one puts back whatever it was *before* the session. Without that, swapping the
meet published the previous session's event and heat number against start lists that
do not contain it — a plausible number over eight blank lanes, until the recording
announced its own. It read as "only the first recording I play shows the names",
because the first one after a restart is the only one that finds nothing stale.

The way out restores rather than clears on purpose. A CTS re-announces its event and
heat several times a second, so forgetting costs it nothing; a Quantum announces once,
when the heat is readied, so a board told to forget would show no event and no names
until somebody readied the next one. Running a test session mid-meet therefore costs
you nothing on either console.

### Recording a session

A recording is a console's serial output saved to a file the Test tab can replay.
There are two formats, and which one to make depends on whether Splouch already
decodes your console.

| Format | What it holds | Replays | Make it when |
| --- | --- | --- | --- |
| `.serial` | one packet per line, each stamped with the time it arrived | at the console's own pace, once | Splouch already decodes your console |
| `.raw` | the bytes exactly as they arrived, sixteen to a line, no timing | at ~720 bytes/s, looped | Splouch does not decode your console yet |

A `.serial` is the better replay, but its packets are split by the configured
console's decoder. On a console that decoder does not know, the split is a guess, and
the file keeps the guess. A `.raw` keeps the wire untouched, which is what someone
writing a decoder for your console needs.

**With Splouch's recorder.** Connect the console and select it in Settings → Timing,
open the Test tab, type a name under **Record Live Session**, pick `.serial` or
`.raw`, and press **Start**. Run a heat or two, then press **Stop**. The file lands
in `~/SplouchData/recorded/` and appears in the Recorded Sessions list straight away.
If your console is not in the list yet, pick one whose serial settings (shown as
"RS-232 · 9600 baud · 8-E-1" and so on) match yours, and record a `.raw`.

**With PuTTY, or any other capture tool.** Useful when the console is wired to a
laptop rather than the Pi. In PuTTY, open the serial port at the console's settings,
then under Session → Logging choose **All session output** and a file name. Two
details matter:

- Do **not** choose "Printable output". Console protocols are mostly bytes that are
  not printable, and that mode drops them.
- PuTTY writes a header line at the top of the file
  (`=~=~=~=~=~=~=~=~=~=~=~= PuTTY log …`). Delete it, or its characters are replayed
  as if the console had sent them.

What PuTTY saves is binary (a `.cap`), and the Test tab takes hex. Convert it first:

```bash
python3 server/console_recordings/cap-to-raw.py session.cap   # -> session.raw
```

Then upload the `.raw` with **Upload** in the Recorded Sessions list. The result is
the same file Splouch's own recorder writes in `.raw` mode.

To share a recording in a [console report](https://github.com/olivierouellet/Splouch/issues/new?template=console_report.yml)
or a pull request, copy it out of `~/SplouchData/recorded/` and attach it.

---

## Localisation

One file in `shared/locales/` is one language, and it is what a spectator reads:
the column labels, the event-name vocabulary, the phone pages' chrome and the TV
display's status lines. The Pi, the cloud, the phone apps and the TV all read it,
the apps through `GET /i18n/{lang}` ([api.md](api.md) §5.9).

| File | Language |
| --- | --- |
| `shared/locales/en.toml` | English — the fallback for every key |
| `shared/locales/fr.toml` | Français |
| `shared/locales/es.toml` | Español |
| `shared/locales/panel/<code>.toml` | the operator panel, meet preview and cloud admin — optional |

Each served file carries the same sections, and the test suite fails when a
language lacks a key English has:

```toml
[meta]
name = "English"

[labels]                       # column headers, short and long forms
event = { short = "EV",   long = "EVENT" }
heat  = { short = "HT",   long = "HEAT"  }

[event_name]                   # the words an event name is composed from
freestyle = "Freestyle"

[mobile]                       # phone pages, apps and picker chrome
scoreboard = "Scoreboard"

[display]                      # TV display status lines
waiting_server = "Waiting for the timing server"
```

**Adding a language** is a pull request with one new file in `shared/locales/`,
complete against `en.toml`. It appears in every Language control on the next
deploy; the phone apps pick it up from `GET /locales` without a release. A
matching `panel/<code>.toml` is welcome but not required — every panel string it
lacks renders in English, key by key.

**What is not translated here.** Words about an app or a device — the server
sheet, connection errors, OS requirements — live in each app repo, natively.
The rule is in [app.md](app.md) `T-05`: if the web page shows the word, the
server owns it; otherwise the app does.

There is no per-Pi locale file. A club that wants different wording changes the
shipped file, so every server and every client agree.

---

## Manual and CLI reference

Everything here can also be done from the admin UI — these are the manual equivalents and
lower-level tools for when you're SSH'd into Pi #1.

### Load meet files manually

Instead of uploading in **Meet Setup**, copy `.lxf` / `.csv` files to `~/SplouchData/meet/`.
They appear in the Meet Setup file dropdown — select one to load it live.

### Service management

The app runs as a systemd service named **`splouch`**. The **Power** tab does restart /
reboot / shutdown and **Terminal** has a "Scoreboard logs" launcher and "Save Logs", but
over SSH:

```sh
sudo systemctl restart splouch    # restart after manual changes (same as the Power tab)
sudo systemctl stop splouch       # stop the service
sudo systemctl start splouch      # start it again
systemctl status splouch          # current state
journalctl -u splouch -f          # follow live logs
```

### CLI troubleshooting

**The service won't start.** Run `journalctl -u splouch -f` to see the error. Common
causes: wrong serial port, missing Python dependencies (run `uv sync` in the repo
directory), or another process already bound to port 5000.

**Serial adapter not detected.** Run `ls /dev/ttyUSB*` on Pi #1 to list adapters. The
service user must be in the `dialout` group — check with `groups`; if missing, `sudo
usermod -aG dialout <user>` and reboot. (The installer normally handles this.)

**`splouch.local` unreachable, or it hangs unless you type `http://` in front.** See
[troubleshooting.md](troubleshooting.md).
The Pi never serves https — there is no public domain to get a certificate for — so
browsers that upgrade the address have to fall back, and the two known causes of them
failing to are covered there.

---

<a id="admin-guide-fr"></a>

## Guide d'administration — Français

<p><a href="#admin-guide">English</a> · <a href="#admin-guide-fr">Français</a></p>

L'interface d'administration se trouve à `http://splouch.local/settings`. L'identifiant par
défaut figure dans [Identifiants](installation.md#identifiants) — changez-le avant la
compétition depuis le menu utilisateur en bas de la barre latérale → **Changer le mot de
passe**.

---

### Les pages

| URL | Description |
| --- | --- |
| `/` | Redirige vers `/live` |
| `/live` | Le tableau (nombre de couloirs selon les réglages de Compétition) — l'affichage de référence, que le tableau Qt reproduit |
| `/operator` | Vue de contrôle de l'opérateur |
| `/manual` | Contrôle manuel des séries — choisir l'épreuve et la série en cours à la main ([guide](consoles/manual.md)) |
| `/mobile` | Coquille mobile — trois onglets (Tableau, Résultats, Programme) |
| `/results` | Résultats après chaque série |
| `/schedule` | Programme de la compétition avec heures de départ et listes de départ par série |
| `/console` | Visualiseur de la console série en direct |
| `/settings` | Réglages d'administration (connexion requise) |

Ajoutez `?test` à `/live` pour superposer des boutons de mode (Intro, Running, Results, Next
Heat) sur le tableau — utile pour tester sans console branchée.

---

### Déroulement d'une compétition

1. Dans Splash Meet Manager : **Fichier → Exporter → Lenex** → enregistrez un fichier `.lxf`.
2. Dans **Réglages → Compétition**, cliquez sur **Ajouter un fichier** et téléversez le
   `.lxf` — les noms des nageurs et des clubs apparaissent aussitôt sur le tableau.
   *(Sinon, téléversez un programme d'épreuves Hytek `.csv` pour que le nom des épreuves
   s'affiche dans l'en-tête.)*
3. Allumez le Pi n° 2 : le téléviseur démarre directement sur le tableau. Tout autre écran peut
   ouvrir `http://splouch.local/` dans un navigateur.
4. Démarrez la console de chronométrage — les temps apparaissent automatiquement au fil des
   séries.

> Vous préférez la ligne de commande ? Voir [Référence manuelle et CLI](#référence-manuelle-et-cli)
> pour déposer les fichiers de compétition directement dans `~/SplouchData/meet/`.

#### Pas de console de chronométrage ?

Réglez **Réglages → Chronométrage → Équipement de chronométrage** sur **Manuel — sans console de chronométrage** et
pilotez la compétition depuis `/manual` sur un téléphone : maintenez Précédente/Suivante
pour parcourir les séries, ou touchez une série pour prévisualiser ses nageurs et validez-la
avec **▸**. Les tableaux affichent alors l'épreuve, la série, le nom de l'épreuve, l'heure de
la série et chaque nageur — tout sauf les temps, qui exigent une console. Guide complet :
[manual.md](consoles/manual.md).

Bon à savoir même avec une console : une Daktronics Omnisport 2000 chronomètre les courses
mais n'envoie aucun numéro d'épreuve ni de série ; c'est donc `/manual` qui les fournit.

---

### Onglets des réglages

| Onglet | Description |
| --- | --- |
| **Compétition** | Téléverser les fichiers Lenex `.lxf` / Hytek `.csv` ; longueur du bassin, plaques de touche, nombre de couloirs |
| **Chronométrage** | Type de console et port série, état de la connexion, moniteur série (paquets hexadécimaux bruts), anti-rebond d'arrivée. Choisir **Manuel — sans console de chronométrage** retire le port, le badge et le moniteur, et renvoie vers `/manual` |
| **Horloge** | Synchronisation NTP ; réglage manuel de la date et de l'heure hors ligne ; installer/retirer l'horloge matérielle Adafruit PiRTC (DS3231) |
| **Affichage** | Afficher/masquer les en-têtes et les colonnes (Nom, Club, Écart, Position) ; mise en valeur du podium |
| **Thème** | Jeux de couleurs intégrés ; personnaliser couleurs et polices ; enregistrer comme thème personnalisé |
| **Réseau** | Gestion du WiFi ; Ethernet en DHCP ou IP statique (adresse, routeur, DNS) ; clients d'affichage connectés |
| **Mise à jour et sauvegarde** | Récupérer la dernière version depuis GitHub, synchroniser les dépendances, redémarrer ; télécharger ou restaurer une sauvegarde de `~/SplouchData` |
| **Test** | Rejouer des sessions enregistrées ; régler la vitesse de lecture ; enregistrer des sessions série en direct. Sans risque avec une compétition chargée — voir [Sessions de test](#sessions-de-test) |
| **Terminal** | Terminal dans le navigateur — Shell, raspi-config, journaux du tableau, dmesg, ports série |
| **Nuage** | URL et clé du relais cloud ; apparence de la compétition dans le sélecteur (titre, image, icône, lieu, sport) |
| **Alimentation** | Redémarrer le service, redémarrer ou éteindre le Pi — maintenir appuyé pour confirmer |
| **Compte** | Changer l'identifiant et le mot de passe d'administration (depuis le menu utilisateur de la barre latérale) |

> Dans la barre latérale, **Affichage / Thème** sont regroupés sous **Tableau**, et **Test /
> Terminal** sous **Débogage** ; **Compte** s'ouvre depuis le menu utilisateur en bas.

---

### Dossiers de données sur le Pi n° 1

| Chemin | Contenu |
| --- | --- |
| `~/SplouchData/meet/` | Fichiers de compétition Lenex `.lxf` et Hytek `.csv` (téléversés via Compétition, ou [déposés ici à la main](#référence-manuelle-et-cli)) |
| `~/SplouchData/images/` | Logos de commanditaires ou de clubs pour l'écran d'accueil |
| `~/SplouchData/icons/` | Icône d'écran d'accueil des pages mobiles (onglet Nuage) |
| `~/SplouchData/picker/` | Image de la compétition dans le sélecteur du cloud (onglet Nuage) |
| `~/SplouchData/logs/` | Journaux enregistrés depuis l'onglet Terminal |
| `~/SplouchData/recorded/` | Sessions enregistrées pour la lecture dans l'onglet Test |
| `~/SplouchData/test_meet/` | Listes de départ d'une session de test en cours — vidé à la fin, jamais mêlé à `meet/` |
| `~/SplouchData/themes/` | Thèmes personnalisés `.toml` |
| `~/SplouchData/console_decoders/` | Décodeurs locaux (fichiers `.py`) — chargés au démarrage, non suivis par git |
| `~/SplouchData/settings.json` | Tous les réglages de l'interface d'administration |

---

### Mettre à jour les afficheurs

Trois façons ; le choix dépend de ce que vous avez sous la main.

| Depuis | Comment | Quand |
| --- | --- | --- |
| La page d'administration du serveur | Réglages → Mise à jour et sauvegarde → **Mettre à jour les afficheurs** | La façon habituelle. Fait passer chaque afficheur *enregistré* à la référence du serveur. Mettez d'abord le serveur à jour. |
| L'afficheur lui-même | **F1** sur le clavier du téléviseur → *mettre à jour vers la version du serveur* | Pas de navigateur sous la main, ou afficheur trop ancien pour que le bouton ci-dessus le voie. |
| Une session SSH | `bash install.sh kiosk` sur le Pi du téléviseur | L'afficheur ne démarre plus, ou il est si ancien qu'il n'a pas le menu. |

Les trois aboutissent au **même commit que celui du serveur** — jamais une branche, de sorte
que les deux bouts ne peuvent pas diverger sur le contrat WebSocket. Le serveur doit être sur
un commit propre et poussé ; être hors d'une étiquette de version est permis, avoir des
modifications locales ne l'est pas.

> **« Mettre à jour les afficheurs » dit qu'aucun afficheur n'est enregistré, mais j'en vois
> un.** Un afficheur s'annonce par une trame `register`, et seul le tableau Qt en envoie une —
> un onglet de navigateur non, et un kiosque Chromium affichant `/live` *est* un onglet de
> navigateur. Un afficheur annoncé montre un nom d'hôte, un badge `kiosk` et une version dans
> la liste ; une ligne avec seulement une IP est un navigateur, ou un kiosque installé avant
> la v2026.09.0, lorsque l'afficheur Qt a remplacé Chromium.
>
> Ce kiosque plus ancien ne peut pas être récupéré à distance : il est trop ancien pour
> s'annoncer et pour appliquer la mise à jour qu'on lui enverrait. Faites le premier saut sur
> l'afficheur — F1 s'il a le menu, sinon `bash install.sh kiosk` — et le bouton à distance
> fonctionne ensuite. Voir aussi [Passer de l'afficheur Chromium au kiosque
> Qt](installation.md#passer-de-lafficheur-chromium-au-kiosque-qt).

> **Un afficheur mis à jour ne démarre plus.** Les versions antérieures à ce correctif
> lançaient un simple `uv sync` en se mettant à jour. `uv sync` aligne l'environnement sur le
> fichier de verrouillage pour les extras *demandés* et retire tout le reste ; or le Qt du
> kiosque vit dans l'extra optionnel `scoreboard` — la mise à jour désinstallait donc PySide6
> et l'application revenait avec une trace d'erreur. Sur le Pi :
>
> ```bash
> cd ~/Splouch && uv sync --extra scoreboard
> install/scripts/start-scoreboard.sh
> ```
>
> Relancer `install.sh kiosk` fait la même chose, et à partir de cette version l'afficheur
> synchronise l'extra lui-même et refuse de redémarrer sur un dépôt dont le Qt ne s'importe
> pas.

---

### Sessions de test

L'onglet Test rejoue une session de console enregistrée, si bien que le tableau se comporte
exactement comme pendant une vraie course. Deux choses rendaient cela pénible ; ce n'est plus
le cas.

**Votre compétition reste chargée.** Les numéros d'épreuve et de série d'un enregistrement
renvoient aux listes de départ du `.lxf` livré à côté ; c'est donc sur lui que la lecture
s'appuie. Votre propre compétition est mise de côté pendant ce temps : les fichiers de
`~/SplouchData/meet/` ne sont jamais touchés, la compétition reste l'active, et elle est
rechargée dès la fin de la session — que vous appuyiez sur **Arrêter** ou que
l'enregistrement arrive simplement à son terme. Supprimer la compétition puis la téléverser à
nouveau ne fait plus partie du travail.

**Garder ce test local.** Coché, la lecture atteint le téléviseur et les téléphones du réseau
de la piscine, et rien d'autre : le lien cloud est fermé pour la durée de la session, de sorte
que les spectateurs à distance voient la compétition hors ligne plutôt qu'un enregistrement
déguisé en course réelle. La case est cochée et verrouillée dès qu'une compétition est
chargée — publier des temps inventés sous l'identité d'une compétition en direct n'est pas
une chose qu'une case à cocher devrait permettre. Sans compétition chargée, le choix vous
revient, et il est mémorisé.

À la fin de la session, chaque tableau est vidé de la lecture, la compétition revient, le lien
cloud est rétabli s'il était actif avant, et la vitesse de lecture revient à 1×.

Démarrer une session efface l'épreuve et la série que la console croit en cours, et la
terminer remet celles d'*avant* la session. Sans cela, changer de compétition publiait
l'épreuve et la série de la session précédente face à des listes de départ qui ne les
contiennent pas — un numéro plausible au-dessus de huit couloirs vides, jusqu'à ce que
l'enregistrement annonce les siens. On aurait dit que « seul le premier enregistrement lu
affiche les noms », car le premier après un redémarrage est le seul à ne rien trouver de
périmé.

La sortie restaure plutôt qu'elle n'efface, volontairement. Une CTS réannonce son épreuve et
sa série plusieurs fois par seconde, donc oublier ne lui coûte rien ; une Quantum ne
l'annonce qu'une fois, quand la série est préparée, de sorte qu'un tableau à qui l'on dirait
d'oublier n'afficherait ni épreuve ni noms jusqu'à la préparation de la série suivante.
Lancer une session de test en pleine compétition ne coûte donc rien, sur l'une comme sur
l'autre console.

#### Enregistrer une session

Un enregistrement est la sortie série d'une console sauvegardée dans un fichier que l'onglet
Test peut rejouer. Il existe deux formats ; lequel produire dépend de si Splouch décode déjà
votre console.

| Format | Contenu | Lecture | À produire quand |
| --- | --- | --- | --- |
| `.serial` | un paquet par ligne, chacun horodaté à son arrivée | au rythme de la console, une fois | Splouch décode déjà votre console |
| `.raw` | les octets tels qu'ils sont arrivés, seize par ligne, sans horodatage | à ~720 octets/s, en boucle | Splouch ne décode pas encore votre console |

Un `.serial` donne la meilleure lecture, mais ses paquets sont découpés par le décodeur de la
console configurée. Sur une console que ce décodeur ne connaît pas, le découpage est une
supposition, et le fichier la conserve. Un `.raw` garde le flux intact, ce dont a besoin
quiconque écrit un décodeur pour votre console.

**Avec l'enregistreur de Splouch.** Branchez la console et sélectionnez-la dans Réglages →
Chronométrage, ouvrez l'onglet Test, saisissez un nom sous **Enregistrer une session en
direct**, choisissez `.serial` ou `.raw`, puis appuyez sur **Démarrer**. Lancez une série ou
deux, puis appuyez sur **Arrêter**. Le fichier arrive dans `~/SplouchData/recorded/` et
apparaît aussitôt dans la liste **Sessions enregistrées**. Si votre console n'est pas encore
dans la liste, choisissez-en une dont les paramètres série (affichés sous la forme
« RS-232 · 9600 baud · 8-E-1 », etc.) correspondent aux vôtres, et enregistrez un `.raw`.

**Avec PuTTY, ou tout autre outil de capture.** Utile quand la console est branchée à un
portable plutôt qu'au Pi. Dans PuTTY, ouvrez le port série avec les paramètres de la console,
puis sous Session → Logging choisissez **All session output** et un nom de fichier. Deux
détails comptent :

- Ne choisissez **pas** « Printable output ». Les protocoles de console sont surtout faits
  d'octets non imprimables, et ce mode les supprime.
- PuTTY écrit une ligne d'en-tête en haut du fichier
  (`=~=~=~=~=~=~=~=~=~=~=~= PuTTY log …`). Supprimez-la, sinon ses caractères sont rejoués
  comme si la console les avait envoyés.

PuTTY enregistre du binaire (un `.cap`), et l'onglet Test attend de l'hexadécimal.
Convertissez d'abord :

```bash
python3 server/console_recordings/cap-to-raw.py session.cap   # -> session.raw
```

Puis téléversez le `.raw` avec **Téléverser** dans la liste **Sessions enregistrées**. Le
résultat est le même fichier que l'enregistreur de Splouch écrit en mode `.raw`.

Pour joindre un enregistrement à un [rapport de console](https://github.com/olivierouellet/Splouch/issues/new?template=console_report.yml)
ou à une pull request, copiez-le depuis `~/SplouchData/recorded/` et joignez-le.

---

### Traduction

Un fichier dans `shared/locales/` correspond à une langue, et c'est ce que lit un spectateur :
les intitulés de colonnes, le vocabulaire des noms d'épreuves, l'habillage des pages mobiles
et les lignes d'état de l'afficheur TV. Le Pi, le cloud, les applications mobiles et le
téléviseur le lisent tous, les applications via `GET /i18n/{lang}` ([api.md](api.md) §5.9).

| Fichier | Langue |
| --- | --- |
| `shared/locales/en.toml` | English — la langue de repli pour chaque clé |
| `shared/locales/fr.toml` | Français |
| `shared/locales/es.toml` | Español |
| `shared/locales/panel/<code>.toml` | le panneau de l'opérateur, l'aperçu de compétition et l'administration cloud — facultatif |

Chaque fichier servi porte les mêmes sections (exemple dans la [partie anglaise](#localisation)),
et la suite de tests échoue quand une langue n'a pas une clé que l'anglais possède.

**Ajouter une langue**, c'est une pull request avec un nouveau fichier dans `shared/locales/`,
complet par rapport à `en.toml`. Elle apparaît dans chaque sélecteur de langue au déploiement
suivant ; les applications mobiles la récupèrent via `GET /locales` sans nouvelle version. Un
`panel/<code>.toml` assorti est bienvenu mais pas requis — chaque chaîne du panneau qui
manque s'affiche en anglais, clé par clé.

**Ce qui n'est pas traduit ici.** Les mots qui parlent d'une application ou d'un appareil — la
fiche du serveur, les erreurs de connexion, la version minimale de l'OS — vivent dans chaque
dépôt d'application, en natif. La règle est dans [app.md](app.md) `T-05` : si la page web
affiche le mot, le serveur en est responsable ; sinon, c'est l'application.

Il n'y a pas de fichier de langue propre à un Pi. Un club qui veut une autre formulation
modifie le fichier livré, pour que tous les serveurs et tous les clients s'accordent.

---

### Référence manuelle et CLI

Tout ce qui suit peut aussi se faire depuis l'interface d'administration — ce sont les
équivalents manuels et les outils de bas niveau pour quand vous êtes connecté en SSH au Pi
n° 1.

#### Charger des fichiers de compétition à la main

Au lieu de les téléverser dans **Compétition**, copiez les fichiers `.lxf` / `.csv` dans
`~/SplouchData/meet/`. Ils apparaissent dans la liste déroulante de Compétition —
sélectionnez-en un pour le charger en direct.

#### Gestion du service

L'application tourne comme service systemd nommé **`splouch`**. L'onglet **Alimentation**
redémarre / relance / éteint, et **Terminal** propose un lanceur « Journaux du tableau » et
« Enregistrer les journaux », mais en SSH :

```sh
sudo systemctl restart splouch    # redémarrer après des modifications (comme l'onglet Alimentation)
sudo systemctl stop splouch       # arrêter le service
sudo systemctl start splouch      # le relancer
systemctl status splouch          # état actuel
journalctl -u splouch -f          # suivre les journaux en direct
```

#### Dépannage en ligne de commande

**Le service ne démarre pas.** Lancez `journalctl -u splouch -f` pour voir l'erreur. Causes
fréquentes : mauvais port série, dépendances Python manquantes (lancez `uv sync` dans le
dossier du dépôt), ou un autre processus déjà lié au port 5000.

**Adaptateur série non détecté.** Lancez `ls /dev/ttyUSB*` sur le Pi n° 1 pour lister les
adaptateurs. L'utilisateur du service doit faire partie du groupe `dialout` — vérifiez avec
`groups` ; s'il manque, `sudo usermod -aG dialout <utilisateur>` puis redémarrez.
(L'installateur s'en charge normalement.)

**`splouch.local` injoignable, ou bloqué tant qu'on ne tape pas `http://` devant.** Voir
[troubleshooting.md](troubleshooting.md).
Le Pi ne sert jamais en https — il n'y a pas de domaine public pour lequel obtenir un
certificat — les navigateurs qui forcent l'adresse en https doivent donc revenir en http, et
les deux causes connues de leur échec sont décrites là.
