<p><a href="#cloud-relay">English</a> · <a href="#cloud-relay-fr">Français</a></p>

# Cloud Relay

The cloud relay lets remote attendees (parents, coaches, officials) follow the scoreboard from their phones over the internet, without adding load to the pool-deck Pi.

```mermaid
flowchart LR
    pi["Pi #1"] -->|"outbound WebSocket /ws/relay"| caddy
    phones["Attendees (phones, laptops)"] -->|HTTPS| caddy
    subgraph vm["Cloud VM (Docker)"]
        caddy["Caddy"]
        app["Relay worker<br/>/ws/*, /mobile — live boards"]
        control["Control plane<br/>picker, /admin"]
        pg[("Postgres<br/>organizers, meets, counts")]
        caddy --> app
        caddy --> control
        app -->|internal API| control
        control --- pg
    end
```

The relay is two apps on one VM: a **worker** that carries a meet's live frames, and a
**control plane** that owns the picker, the admin panel and the store
([`docs/architecture/scaling.md`](architecture/scaling.md)).

- Pi #1 opens a single outbound connection — works behind double-NAT with no port forwarding required.
- The cloud server re-emits events to all attendees; Pi #1 is unaffected by attendee load.
- Only scoreboard, results, and schedule data is exposed — meet files (`.lxf`, `.csv`) are never sent.
- Multiple organizers can share one cloud server simultaneously, each with their own revocable key.
- Caddy handles HTTPS and auto-renews Let's Encrypt certificates — no manual certificate management.

---

## Deploying the cloud server

**Requirements:**

- A Debian 12+ or Ubuntu 24.04+ VM, any cloud provider. The relay runs in Docker
  on its own Python 3.13, so the OS mostly doesn't matter; the deploy webhook runs
  on the VM's `python3` and needs 3.11 or newer. The installer checks this before
  it changes anything, and stops on Ubuntu 22.04 (Python 3.10).
- Ports 80 and 443 open in the VM's firewall / security group
- A domain name with an `A` record pointing at the VM's public IP

SSH into the VM and run:

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/install.sh -o install.sh && bash install.sh cloud
```

The script handles everything interactively:

- Installs Docker, fail2ban, and unattended security upgrades
- Clones the repo and generates a `SECRET_KEY`, a `POSTGRES_PASSWORD` and a `NODE_SECRET`
- Prompts for admin username and password
- Prompts for your domain name and updates `Caddyfile`
- Generates a `DEPLOY_SECRET` and installs the deploy webhook as a systemd service
- Configures `ufw` (ports 22, 80, 443 TCP + 443 UDP for HTTP/3)
- Builds and starts the compose stack (control plane + Postgres + relay worker + Caddy)

When it finishes, open `https://yourdomain/admin` and add organizers.

---

## Managing organizers

The `/admin` page (HTTP basic auth — the login you chose at install; change it from the user
menu → **Change password**) lets you, under **Organizers** and **Active Meets**:

- **Add an organizer** — enter an organization name, and where it is based: country, state/province, and the **region** whose servers carry its meets (suggested from the country). A cryptographically random 32-byte key is generated automatically.
- **Edit a location** — under the organizer's name. When the organizer's Pi reports a different country or state/province, the row shows *Their Pi says: …* with **Accept**; accepting copies it, and the region stays yours to change.
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
| **Country**, **State / province** | Where the club is based; the cloud's administrator sees it |
| **Region** | Read-only — the region the cloud put this organizer in, shown once connected |
| **Location** | Venue or city (auto-filled from Lenex if blank) |
| **Sport** | Optional — shown on the meet picker (e.g. `Swimming`) |

Click **Save**. The Pi asks the cloud which server carries its meet, connects there, and
appears in the cloud's meet picker.
Location, sport and the rest of the picker card (title, image, home icon) only show once a
meet file is loaded.

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
**Cloud** → **QR code for spectators**. It carries **this cloud**, taken from the Pi's **Cloud →
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

## Privacy policy

`https://yourdomain/privacy` is the policy a store listing links to, in English, French and
Spanish (`?lang=`). It says what the software does — attendance counting, what the app and
the site keep on the device — so it is the same on every deployment. The one per-deployment
part is who answers for it; add to `cloud/.env`:

```ini
PRIVACY_CONTACT=privacy@yourdomain
```

Unset, the page has no Contact section. Its text is `[privacy]` in `shared/locales/*.toml`;
bump `PRIVACY_UPDATED` in `cloud/cloud_control.py` with any change to it.

---

## Updating the cloud server

Click **Update** in `/admin` → **Update & Backup** — it fetches from GitHub and rebuilds the container automatically. The page polls until the server is back up, then reloads. Prefer it: it resolves the right ref for the way this server was installed, which the manual commands below leave to you.

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

Caddy, the `pgdata` volume (Postgres: organizers, keys, meets, the admin login) and the
`data` volume are preserved across updates. So is your edited `Caddyfile`, as long as you move between refs with `checkout` rather than `reset --hard` — the domain you set at install time lives in that tracked file and a hard reset would revert it.

## Upgrading from a single relay

A server installed before the control plane existed kept everything in files on the
`data` volume (`keys.json`, `credentials.json`, `retained/`, `analytics.db`). The update
brings two new containers, and compose refuses to start until `cloud/.env` holds their two
secrets. Once:

```bash
cd ~/Splouch && git pull          # or the Update button, which will fail to start
bash install/install.sh cloud     # keeps .env, adds POSTGRES_PASSWORD and NODE_SECRET
cd cloud && docker compose run --rm control python cloud_import.py /data
docker compose restart
```

The import prints what it brought across and is safe to run again. The old files stay on
the volume, untouched, until you delete them.

## Backing up the store

The Update & Backup tab downloads the organizers and the meets as JSON. For the whole
database, from the VM:

```bash
cd ~/Splouch/cloud && docker compose exec -T postgres pg_dump -U splouch splouch | gzip > splouch-$(date +%F).sql.gz
```

Keep the dump off the VM: it holds every relay key and the admin password hash.

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
compose file (`cloud`), not its parent, so containers and the `pgdata` and `data` volumes
survive a rename of the checkout.

## Logs

```bash
cd ~/Splouch/cloud && docker compose logs -f
```

---

<a id="cloud-relay-fr"></a>

## Relais cloud — Français

<p><a href="#cloud-relay">English</a> · <a href="#cloud-relay-fr">Français</a></p>

Le relais cloud permet aux spectateurs à distance (parents, entraîneurs, officiels) de suivre
le tableau sur leur téléphone par internet, sans charger le Pi du bord de piscine.

```mermaid
flowchart LR
    pi["Pi n° 1"] -->|"WebSocket sortant /ws/relay"| caddy
    phones["Spectateurs (téléphones, portables)"] -->|HTTPS| caddy
    subgraph vm["VM cloud (Docker)"]
        caddy["Caddy"]
        app["Relais (worker)<br/>/ws/*, /mobile — tableaux en direct"]
        control["Plan de contrôle<br/>liste des compétitions, /admin"]
        pg[("Postgres<br/>organisateurs, compétitions, comptes")]
        caddy --> app
        caddy --> control
        app -->|API interne| control
        control --- pg
    end
```

Le relais est composé de deux applications sur une même VM : un **worker** qui transporte
les trames en direct d'une compétition, et un **plan de contrôle** qui gère la liste des
compétitions, le panneau d'administration et les données
([`docs/architecture/scaling.md`](architecture/scaling.md)).

- Le Pi n° 1 ouvre une seule connexion sortante — fonctionne derrière un double NAT, sans
  redirection de port.
- Le serveur cloud relaie les événements à tous les spectateurs ; leur nombre n'affecte pas
  le Pi n° 1.
- Seules les données du tableau, des résultats et du programme sont exposées — les fichiers
  de compétition (`.lxf`, `.csv`) ne sont jamais envoyés.
- Plusieurs organisateurs peuvent partager un même serveur cloud en même temps, chacun avec
  sa propre clé révocable.
- Caddy gère le HTTPS et renouvelle automatiquement les certificats Let's Encrypt — aucune
  gestion manuelle des certificats.

---

### Déployer le serveur cloud

**Prérequis :**

- Une VM Debian 12+ ou Ubuntu 24.04+, chez n'importe quel fournisseur. Le relais tourne dans
  Docker avec son propre Python 3.13, donc l'OS importe peu ; le webhook de déploiement tourne
  avec le `python3` de la VM et exige la 3.11 ou plus récente. L'installateur le vérifie avant
  de modifier quoi que ce soit, et s'arrête sur Ubuntu 22.04 (Python 3.10).
- Ports 80 et 443 ouverts dans le pare-feu / groupe de sécurité de la VM
- Un nom de domaine avec un enregistrement `A` pointant vers l'IP publique de la VM

Connectez-vous en SSH à la VM et lancez :

```bash
curl -fsSL https://raw.githubusercontent.com/olivierouellet/Splouch/master/install/install.sh -o install.sh && bash install.sh cloud
```

Le script s'occupe de tout, de façon interactive :

- Installe Docker, fail2ban et les mises à jour de sécurité automatiques
- Clone le dépôt et génère une `SECRET_KEY`, un `POSTGRES_PASSWORD` et un `NODE_SECRET`
- Demande l'identifiant et le mot de passe d'administration
- Demande votre nom de domaine et met à jour le `Caddyfile`
- Génère un `DEPLOY_SECRET` et installe le webhook de déploiement comme service systemd
- Configure `ufw` (ports 22, 80, 443 TCP + 443 UDP pour HTTP/3)
- Construit et démarre la pile compose (plan de contrôle + Postgres + relais + Caddy)

Une fois terminé, ouvrez `https://votredomaine/admin` et ajoutez des organisateurs.

---

### Gérer les organisateurs

La page `/admin` (authentification HTTP basic — l'identifiant choisi à l'installation ; à
changer depuis le menu utilisateur → **Changer le mot de passe**) permet, dans
**Organisateurs** et **Compétitions actives** :

- **Ajouter un organisateur** — saisissez le nom d'un organisme et où il est établi :
  pays, état/province, et la **région** dont les serveurs diffusent ses compétitions
  (suggérée d'après le pays). Une clé aléatoire cryptographique de 32 octets est générée
  automatiquement.
- **Modifier un emplacement** — sous le nom de l'organisateur. Quand le Pi de
  l'organisateur indique un autre pays ou une autre province, la ligne affiche *Leur Pi
  indique : …* avec **Accepter** ; accepter le recopie, et la région reste la vôtre.
- **Révoquer une clé** — le Pi qui l'utilise est déconnecté et refusé à sa prochaine
  connexion.
- **Supprimer une clé** — la retire complètement de la liste.
- **Voir les compétitions actives** — montre chaque Pi connecté avec le nom de sa
  compétition, le lieu, le sport, l'organisateur, la console et l'heure de connexion.

La colonne **Console** indique la clé de console que rapporte le Pi (`cts_gen6`, `manual`,
la clé propre d'un plugin) ; une question de support — *sur quelle console tournait cette
compétition ?* — trouve donc sa réponse sans appeler l'opérateur. Une compétition dont la
console ne produit aucun temps est marquée *no times* en dessous : ses spectateurs n'ont pas
d'onglet Résultats, par conception ([Pas de console de
chronométrage ?](admin.md#pas-de-console-de-chronométrage-)). Un Pi dont la version précède
ce rapport de console affiche `—`.

Transmettez la clé générée à l'organisateur. Il la colle dans l'onglet **Réglages → Nuage**
de son Pi.

---

### Connecter un Pi au cloud

Dans l'interface d'administration du Pi n° 1 (`/settings` → onglet **Nuage**) :

| Champ | Valeur |
| --- | --- |
| **URL du serveur** | `https://votredomaine` |
| **Clé de relais** | Clé obtenue dans `/admin` sur le serveur cloud |
| **Pays**, **État / province** | Où le club est établi ; l'administrateur du cloud le voit |
| **Région** | Lecture seule — la région attribuée par le cloud, affichée une fois connecté |
| **Lieu** | Lieu ou ville (rempli depuis le Lenex s'il est vide) |
| **Sport** | Facultatif — affiché dans le sélecteur de compétitions (p. ex. `Natation`) |

Cliquez sur **Enregistrer**. Le Pi demande au cloud quel serveur diffuse sa compétition,
s'y connecte et apparaît dans le sélecteur de compétitions du cloud. Le lieu, le sport et le reste de la fiche du sélecteur (titre, image,
icône) n'apparaissent qu'une fois un fichier de compétition chargé.

---

### Comment les spectateurs se connectent

Les spectateurs vont à `https://votredomaine` sur n'importe quel téléphone ou navigateur :

- Si une seule compétition est active, le tableau s'ouvre directement.
- Si plusieurs sont actives, un sélecteur affiche le nom, le lieu et le sport de chacune.

Le tableau (`/mobile`) a trois onglets — **Tableau**, **Résultats** et **Programme** — et
reprend le thème et les réglages d'affichage du Pi de l'organisateur.

Sur iOS, touchez **Partager → Sur l'écran d'accueil** pour une expérience plein écran façon
application (l'invitation s'affiche automatiquement à la première visite).

---

### Ouvrir l'application depuis un code QR

Une affiche à la piscine porte un code vers `https://votredomaine/add?server=<le Pi de la
piscine>` ([`app.md`](app.md) `P-16`). Avec l'application installée, le téléphone l'ouvre
dans l'application ; sans elle, le navigateur arrive sur `https://votredomaine/add`, qui
indique le serveur nommé par le code et propose la boutique. La page fonctionne telle quelle.
La partie qui ouvre l'**application** exige deux fichiers, et l'empreinte de l'un d'eux est
propre à chaque déploiement.

Ajoutez à `cloud/.env`, puis `docker compose up -d` (voir l'exemple commenté dans la
[partie anglaise](#letting-a-qr-code-open-the-app)) :

- `ANDROID_CERT_FINGERPRINTS` — empreinte SHA-256 du certificat de signature Android
  (séparées par des virgules s'il y en a plusieurs). Avec Play App Signing, c'est la valeur de
  la page *App signing* de la Play Console — **pas** celle de la clé d'envoi.
- `STORE_URL_ANDROID`, `STORE_URL_IOS` — les fiches des boutiques, une fois les applications
  publiées. Absentes, les boutons sont masqués, jamais morts.

Vérifiez ensuite — les deux doivent répondre `200` avec `content-type: application/json` et
**sans redirection**, car Android n'en suit aucune :

```bash
curl -i https://votredomaine/.well-known/assetlinks.json
curl -i https://votredomaine/.well-known/apple-app-site-association
```

`assetlinks.json` renvoie **404 tant qu'aucune empreinte n'est définie**. C'est voulu : un
fichier vide mais bien formé a l'air déployé et échoue plus tard, en silence, à l'installation
sur le téléphone de quelqu'un — où le seul diagnostic est `adb shell pm get-app-links
app.splouch.android` qui répond `1024` et l'OS qui affiche un sélecteur au lieu d'ouvrir
l'application. Android vérifie une fois, à l'installation, et met la réponse en cache ; une
piscine sans internet n'est donc pas affectée.

**Une version de débogage, sans publier de version.** Tout ce qui se trouve dans
`applinks.json` du volume de données *s'ajoute* à ce que fournit `.env` ; une empreinte de
test n'exige donc ni modification du compose ni redémarrage :

```bash
docker compose exec app sh -c 'cat > /data/applinks.json' <<'JSON'
{ "android_fingerprints": ["AA:11:…:FF:00"] }
JSON
```

Le même fichier accepte aussi `store_android`, `store_ios`, `android_package` et
`ios_app_ids`.

**Côté opérateur.** Chaque Pi peut fournir à son opérateur un code prêt à afficher — Réglages
→ **Nuage** → **Code QR pour les spectateurs**. Il porte **ce cloud**, tiré du champ **URL du
serveur** du Pi ; ce champ doit donc être rempli pour que les boutons apparaissent. Les deux
PNG font ~10 cm de large à 300 ppp :

- **Code QR avec l'adresse** — l'adresse est imprimée sous le code. À utiliser pour tout ce
  qui est collé au mur : c'est le recours quand un appareil photo ne fait pas la mise au point,
  et la seule façon de vérifier que l'affiche dit la bonne chose.
- **Code QR seul** — le code nu, pour un programme ou une diapositive qui imprime déjà
  l'adresse.

Il ne porte volontairement *pas* l'adresse propre du Pi, `http://splouch.local:5000`. Cette
adresse ne se résout que pour un téléphone déjà connecté au WiFi du lieu — un spectateur en
données mobiles n'obtient rien, et un réseau invité avec isolation des clients la bloque même
pour qui s'y est connecté. Une affiche ne peut pas demander sur quel réseau se trouve le
lecteur ; elle donne donc l'adresse qui fonctionne de partout. Les spectateurs sur le WiFi de
la piscine peuvent ensuite choisir le Pi dans la liste de serveurs de l'application, qui sert
précisément à cela.

---

### Politique de confidentialité

`https://votredomaine/privacy` est la politique vers laquelle pointe une fiche de boutique,
en anglais, en français et en espagnol (`?lang=`). Elle décrit ce que fait le logiciel —
comptage de l'assistance, ce que l'application et le site conservent sur l'appareil — et est
donc la même pour chaque déploiement. La seule partie propre au déploiement est la personne
qui en répond ; ajoutez à `cloud/.env` :

```ini
PRIVACY_CONTACT=confidentialite@votredomaine
```

Sans cette valeur, la page n'a pas de section Contact. Le texte est `[privacy]` dans
`shared/locales/*.toml` ; mettez à jour `PRIVACY_UPDATED` dans `cloud/cloud_control.py` à
chaque modification.

---

### Mettre à jour le serveur cloud

Cliquez sur **Mettre à jour** dans `/admin` → **Mise à jour & Sauvegarde** — il récupère le
code depuis GitHub et reconstruit le conteneur automatiquement. La page interroge le serveur
jusqu'à son retour, puis se recharge. Préférez cette voie : elle choisit la bonne référence
selon la façon dont ce serveur a été installé, ce que les commandes manuelles ci-dessous vous
laissent faire.

Pour mettre à jour par SSH, vérifiez d'abord sur quelle voie se trouve le dépôt —
`install.sh` en propose deux, qui se mettent à jour différemment :

```bash
cd ~/Splouch && git branch --show-current
```

**`master`** — une installation de développement. La branche suit le dépôt distant, un pull
suffit :

```bash
git pull && cd cloud && docker compose up -d --build
```

**`release`** — une installation « Latest release ». `install.sh` a créé cette branche depuis
une *étiquette* ; elle n'a donc pas d'amont et `git pull` échoue avec *« There is no tracking
information for the current branch »*. Déplacez-la plutôt vers l'étiquette voulue :

```bash
git fetch --tags
git checkout -B release "$(git tag -l --sort=-version:refname | grep -E '^v[0-9]{4}\.[0-9]{2}\.[0-9]+$' | head -1)"
cd cloud && docker compose up -d --build
```

Pour faire passer une installation release sur la branche de développement, nommez la branche
distante pour que l'amont soit défini — après quoi `git pull` y fonctionne aussi :

```bash
git fetch origin && git checkout -B master origin/master
```

Caddy, le volume `pgdata` (Postgres : organisateurs, clés, compétitions, identifiant
d'administration) et le volume `data` sont conservés d'une mise à jour à l'autre. Votre `Caddyfile` modifié aussi, tant que vous changez de référence avec `checkout`
plutôt qu'avec `reset --hard` — le domaine défini à l'installation vit dans ce fichier suivi,
et une réinitialisation forcée le rétablirait.

### Passer d'un relais unique au plan de contrôle

Un serveur installé avant le plan de contrôle conservait tout dans des fichiers du volume
`data` (`keys.json`, `credentials.json`, `retained/`, `analytics.db`). La mise à jour ajoute
deux conteneurs, et compose refuse de démarrer tant que `cloud/.env` ne contient pas leurs
deux secrets. Une seule fois :

```bash
cd ~/Splouch && git pull          # ou le bouton Mettre à jour, qui échouera au démarrage
bash install/install.sh cloud     # conserve .env, ajoute POSTGRES_PASSWORD et NODE_SECRET
cd cloud && docker compose run --rm control python cloud_import.py /data
docker compose restart
```

L'import affiche ce qu'il a repris et peut être relancé sans risque. Les anciens fichiers
restent sur le volume, intacts, jusqu'à ce que vous les supprimiez.

### Sauvegarder les données

L'onglet Mise à jour & Sauvegarde télécharge les organisateurs et les compétitions en JSON.
Pour la base complète, depuis la VM :

```bash
cd ~/Splouch/cloud && docker compose exec -T postgres pg_dump -U splouch splouch | gzip > splouch-$(date +%F).sql.gz
```

Conservez la sauvegarde hors de la VM : elle contient toutes les clés de relais et
l'empreinte du mot de passe d'administration.

### Déplacer ou renommer le dossier d'installation

Le webhook de déploiement tourne comme unité systemd hors de Docker, et systemd exige des
chemins absolus ; `install.sh` inscrit donc le dossier d'installation dans
`/etc/systemd/system/deploy-webhook.service`. Renommez ou déplacez le dépôt et cette unité ne
démarre plus — ce qui emporte **la liste des versions et le bouton Mettre à jour** de
`/admin`, puisque tous deux sont servis par le webhook.

Relancez `install.sh` après le déplacement (il réécrit l'unité pour le dossier actuel), ou
réparez-la à la main :

```bash
sudo grep -n ANCIEN_NOM /etc/systemd/system/deploy-webhook.service
sudo sed -i 's|ANCIEN_NOM|NOUVEAU_NOM|g' /etc/systemd/system/deploy-webhook.service
sudo systemctl daemon-reload && sudo systemctl restart deploy-webhook
systemctl status deploy-webhook --no-pager
```

Vérifiez ensuite que rien d'autre n'a gardé l'ancien chemin :

```bash
sudo grep -rl ANCIEN_NOM /etc/systemd/system/ /etc/sudoers.d/ 2>/dev/null
```

Docker n'est pas affecté : le projet compose porte le nom du dossier qui contient le fichier
compose (`cloud`), pas celui de son parent ; les conteneurs et les volumes `pgdata` et `data`
survivent donc au renommage du dépôt.

### Journaux

```bash
cd ~/Splouch/cloud && docker compose logs -f
```
