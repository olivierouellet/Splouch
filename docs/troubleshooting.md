<p><a href="#troubleshooting">English</a> · <a href="#troubleshooting-fr">Français</a></p>

# Troubleshooting

The installer already fixes the known network problems for you, so most of this
page is about telling which one you are looking at. **First step, always:** if
**Settings** shows the *Reinstall needed* banner, re-run the installer on that Pi —
the **Reinstall Splouch** desktop icon, or over SSH:

```bash
bash ~/Splouch/install/install.sh server
```

The in-app update cannot change system files (firewall, avahi, apt sources), so a
Pi installed before a fix only gets it from a reinstall.

| Problem | What `install.sh` does about it |
| --- | --- |
| `splouch.local` hangs although `ping` works | Stops avahi publishing IPv6 link-local addresses (`use-ipv6=no`, `publish-aaaa-on-ipv4=no`) |
| Typing `splouch.local` hangs, `http://splouch.local` works | Makes port 443 refuse connections (`ufw reject 443/tcp`), so browsers fall back to http at once |
| `apt update` fails with 404 on every repository | Switches the apt sources to `https://` before installing anything |

What is left below are the cases the Pi cannot fix by itself.

<details>
<summary><strong><code>splouch.local</code> won't load, but the IP address does</strong></summary>

**How it looks.** `ping splouch.local` works, and `http://<Pi's IP>/` loads, but
`http://splouch.local/` hangs or fails.

**Why.** On a Pi with two interfaces (WiFi and Ethernet), avahi used to publish an
IPv6 link-local address (`fe80::…`) for `splouch.local`. Browsers prefer IPv6,
try that address first, and cannot reach it without a zone index. `ping` and the
raw IP use IPv4, so they work.

**Fix.** Re-run the installer (above). To check the Pi is fixed:

```bash
avahi-resolve -n splouch.local -6     # should return nothing
avahi-resolve -n splouch.local        # should return only the IPv4 address
```

**Still failing on one computer?** It is holding on to the old answer. Flush its cache:

- **macOS:** `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`
- **Windows:** `ipconfig /flushdns`

Browsers cache too: if a private window works and a normal one does not, the Pi is
fine — clear the browser cache, or quit and reopen the browser (Safari: ⌘Q, or
Develop → Empty Caches).

</details>

<details>
<summary><strong>Typing <code>splouch.local</code> hangs, but <code>http://splouch.local</code> works</strong></summary>

**How it looks.** The page loads when you type the `http://` prefix. Typing just
`splouch.local` hangs, or the address bar shows `https://`.

**Why.** Browsers try `https://` first and fall back to `http://` only when the
https attempt fails *fast*. The Pi never serves https — a certificate needs a public
domain, which no pool-deck network has — and it cannot redirect https to http,
because that answer would itself have to travel over TLS. The installer makes port
443 refuse connections so the fallback is immediate.

**Fix.** Re-run the installer (above). To check, this should print
`Connection refused` straight away, not hang:

```bash
curl -sS --max-time 5 https://splouch.local/ ; echo "exit=$?"
```

**Cases only the browser can fix:**

- A bookmark or link saved as `https://splouch.local/` — the explicit scheme disables
  the fallback. Re-save it as `http://splouch.local/`.
- Firefox with **HTTPS-Only Mode** on — add an exception for the site, or turn the
  mode off.

</details>

<details>
<summary><strong>Even the IP address does not load from a phone or laptop</strong></summary>

The network has WiFi client (AP) isolation turned on, common on corporate and guest
networks. It blocks device-to-device traffic, so nothing on the Pi can fix it.
Either have it turned off on the network side, or connect the clients to the
pool-deck router that Pi #1 is plugged into.

</details>

<details>
<summary><strong>Can't sign in — forgotten password, or after installing an older version</strong></summary>

**Why.** The admin password is stored as a hash (`password_hash` in
`~/SplouchData/settings.json`). A version from before that change reads only the
`password` field, which now holds a random value — so after going back to one, no
password works. That is deliberate: the alternative was falling back to the
password printed in this documentation.

**Fix.** On the Pi (keyboard or SSH), edit `~/SplouchData/settings.json`: delete the
`"password_hash"` line, set `"password"` to the new password, then
`sudo systemctl restart splouch`. A current version hashes it again on start.

</details>

<details>
<summary><strong>The Pi answers <code>Unknown host</code> (421)</strong></summary>

**How it looks.** The Pi is reached through a DNS name of the venue's own, such as
`scores.club.example`, and every page answers *Unknown host*.

**Why.** The Pi only answers to names the local network alone can resolve — its
`.local` names, its hostname, `.lan` / `.home` names — and to bare addresses. That is
what stops a website from re-pointing its own name at the Pi (DNS rebinding) and
driving the board from a spectator's phone.

**Fix.** Add the name to `~/SplouchData/settings.json` and restart the service:

```json
"allowed_hosts": ["scores.club.example"]
```

</details>

<details>
<summary><strong><code>apt update</code> fails with <code>404 NOT FOUND</code> on every repository</strong></summary>

**How it looks.** Every repository fails at once, `deb.debian.org` and
`archive.raspberrypi.com` alike, although the network otherwise works:

```text
Err:4 http://deb.debian.org/debian trixie Release
  404  NOT FOUND [IP: 151.101.138.132 80]
```

**Why.** The network's proxy intercepts plain HTTP and answers with its own 404 page.
Two independent mirrors do not fail together; a local proxy is the common factor.

**Fix.** The installer switches apt to `https://` before it installs anything, which
the proxy cannot tamper with. If you hit this outside the installer, run the same
rewrite by hand:

```bash
sudo sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://archive.raspberrypi.com|https://archive.raspberrypi.com|g; s|http://security.debian.org|https://security.debian.org|g' \
  /etc/apt/sources.list.d/*.sources
sudo rm -rf /var/lib/apt/lists/* && sudo apt update
```

**If HTTPS fails too**, the network blocks the mirrors outright. Have
`deb.debian.org` and `archive.raspberrypi.com` allowed, or install from another
network (a phone hotspot works). A wrong clock gives similar errors — check `date`.

</details>

<details>
<summary><strong>The console's UDP stream or the Cloud, never both</strong></summary>

**How it looks.** The console broadcasts on a closed network plugged into the Pi's
Ethernet port, and the internet comes over the venue WiFi. With Ethernet set up, the
board gets the console but the Cloud drops; without it, the reverse.

**Why.** Splouch assumed Ethernet was the venue router. A static address needed a
router, and that router took the default route ahead of WiFi, so the Cloud's traffic
went into the closed network and stopped there. A closed network rarely has a DHCP
server either, so on DHCP Ethernet got no address and the UDP listener never opened.

**Fix.** **Settings → Network → Ethernet Static IP**: untick *Ethernet has internet*,
enter an address on the console's subnet (router left blank), and hold **Static**.
Ethernet then never takes the default route; **Internet** in *Status* should read
*via WiFi*. If *Status* warns about overlapping subnets, the console network and the
WiFi use the same addresses: pick the Ethernet address on another subnet if the
console allows it.

With no DHCP server on the console network, Ethernet on DHCP falls back to a
`169.254.x.x` address (*Status* says *no DHCP server*): enough to hear a console that
broadcasts to every network. *Status* then shows where the console sends from
(*console 192.168.0.20, 5 s ago*); if it warns that the console is on another subnet,
set a static address on that subnet.

</details>

<details>
<summary><strong>The venue WiFi has a sign-in page (captive portal)</strong></summary>

**How it looks.** The Pi joins the WiFi and gets an address, but the Cloud never
connects. **Settings → Network → Internet** reads *Sign-in page in the way*.

**Why.** The portal lets through only the device that accepted it, by its MAC
address. The Pi has no browser, and accepting it from Settings on another computer
lets in that computer, not the Pi.

**Fix.** Under **WiFi Networks → WiFi asks for a sign-in page?**: accept the portal on
a phone or laptop, copy that device's WiFi address for this network, disconnect the
device from that WiFi, then enter the address and hold **Use**. The Pi reconnects
with it, and the portal sees the device it already let in. **Pi's own** puts the Pi's
address back. Otherwise, ask the venue to let the Pi's MAC (shown there) through, or
use a phone hotspot.

**When the sign-in expires** (often after a few hours), a banner over every Settings
tab says *Cloud down: the WiFi wants its sign-in page again*. The same device must
accept again while the Pi is off that WiFi: disable the Pi's WiFi (from Ethernet or
the Pi's own screen), accept on the device, disconnect it, then enable the Pi's WiFi.

</details>

---

<a id="troubleshooting-fr"></a>

## Dépannage — Français

<p><a href="#troubleshooting">English</a> · <a href="#troubleshooting-fr">Français</a></p>

L'installateur corrige déjà les problèmes réseau connus ; cette page sert surtout à
reconnaître celui que vous avez sous les yeux. **Premier réflexe, toujours :** si les
**Réglages** affichent la bannière *Réinstallation requise*, relancez l'installateur sur
ce Pi — l'icône **Reinstall Splouch** du bureau, ou par SSH :

```bash
bash ~/Splouch/install/install.sh server
```

La mise à jour intégrée ne peut pas modifier les fichiers système (pare-feu, avahi,
sources apt) ; un Pi installé avant un correctif ne le reçoit donc que par une
réinstallation.

| Problème | Ce que fait `install.sh` |
| --- | --- |
| `splouch.local` reste bloqué alors que `ping` répond | Empêche avahi de publier les adresses IPv6 lien-local (`use-ipv6=no`, `publish-aaaa-on-ipv4=no`) |
| Taper `splouch.local` bloque, `http://splouch.local` fonctionne | Fait refuser les connexions sur le port 443 (`ufw reject 443/tcp`), pour que les navigateurs reviennent aussitôt en http |
| `apt update` échoue en 404 sur tous les dépôts | Passe les sources apt en `https://` avant toute installation |

Ce qui suit couvre les cas que le Pi ne peut pas corriger seul.

<details>
<summary><strong><code>splouch.local</code> ne se charge pas, mais l'adresse IP oui</strong></summary>

**Symptôme.** `ping splouch.local` répond et `http://<IP du Pi>/` se charge, mais
`http://splouch.local/` reste bloqué ou échoue.

**Cause.** Sur un Pi à deux interfaces (WiFi et Ethernet), avahi publiait une adresse
IPv6 lien-local (`fe80::…`) pour `splouch.local`. Les navigateurs préfèrent l'IPv6,
essaient d'abord cette adresse et ne peuvent pas la joindre sans indice de zone. `ping`
et l'IP brute passent par l'IPv4, d'où leur succès.

**Correctif.** Relancez l'installateur (ci-dessus). Pour vérifier que le Pi est corrigé :

```bash
avahi-resolve -n splouch.local -6     # ne doit rien renvoyer
avahi-resolve -n splouch.local        # ne doit renvoyer que l'adresse IPv4
```

**Toujours en échec sur un ordinateur ?** Il garde l'ancienne réponse en cache. Videz-le :

- **macOS :** `sudo dscacheutil -flushcache; sudo killall -HUP mDNSResponder`
- **Windows :** `ipconfig /flushdns`

Les navigateurs ont aussi un cache : si une fenêtre privée fonctionne et une fenêtre
normale non, le Pi va bien — videz le cache du navigateur, ou quittez-le et rouvrez-le
(Safari : ⌘Q, ou Développement → Vider les caches).

</details>

<details>
<summary><strong>Taper <code>splouch.local</code> bloque, mais <code>http://splouch.local</code> fonctionne</strong></summary>

**Symptôme.** La page se charge quand on tape le préfixe `http://`. Taper seulement
`splouch.local` bloque, ou la barre d'adresse affiche `https://`.

**Cause.** Les navigateurs essaient d'abord `https://` et ne reviennent à `http://` que
si la tentative https échoue *vite*. Le Pi ne sert jamais en https — un certificat exige
un domaine public, ce qu'aucun réseau de bord de piscine n'a — et il ne peut pas
rediriger le https vers le http, car cette réponse devrait elle-même passer par TLS.
L'installateur fait refuser les connexions sur le port 443 pour que le repli soit
immédiat.

**Correctif.** Relancez l'installateur (ci-dessus). Pour vérifier, cette commande doit
afficher `Connection refused` aussitôt, sans bloquer :

```bash
curl -sS --max-time 5 https://splouch.local/ ; echo "exit=$?"
```

**Cas que seul le navigateur peut corriger :**

- Un favori ou un lien enregistré en `https://splouch.local/` — le schéma explicite
  désactive le repli. Enregistrez-le de nouveau en `http://splouch.local/`.
- Firefox avec le **mode HTTPS uniquement** activé — ajoutez une exception pour le site,
  ou désactivez ce mode.

</details>

<details>
<summary><strong>Même l'adresse IP ne se charge pas depuis un téléphone ou un portable</strong></summary>

Le réseau a l'isolation des clients WiFi (isolation AP) activée, courante sur les réseaux
d'entreprise et les réseaux invités. Elle bloque le trafic entre appareils ; rien sur le
Pi ne peut donc la contourner. Faites-la désactiver côté réseau, ou connectez les
clients au routeur du bord de piscine auquel le Pi n° 1 est branché.

</details>

<details>
<summary><strong>Connexion impossible — mot de passe oublié, ou après l'installation d'une version antérieure</strong></summary>

**Cause.** Le mot de passe d'administration est enregistré sous forme de hachage
(`password_hash` dans `~/SplouchData/settings.json`). Une version antérieure à ce
changement ne lit que le champ `password`, qui contient désormais une valeur
aléatoire : après un retour en arrière, aucun mot de passe ne fonctionne. C'est
voulu — l'autre option était de revenir au mot de passe imprimé dans cette
documentation.

**Correctif.** Sur le Pi (clavier ou SSH), modifiez `~/SplouchData/settings.json` :
supprimez la ligne `"password_hash"`, mettez le nouveau mot de passe dans
`"password"`, puis `sudo systemctl restart splouch`. Une version à jour le hache de
nouveau au démarrage.

</details>

<details>
<summary><strong>Le Pi répond <code>Unknown host</code> (421)</strong></summary>

**Symptôme.** Le Pi est joint par un nom DNS propre au site, comme
`scores.club.example`, et chaque page répond *Unknown host*.

**Cause.** Le Pi ne répond qu'aux noms que seul le réseau local sait résoudre — ses
noms `.local`, son nom d'hôte, les noms `.lan` / `.home` — et aux adresses IP. C'est ce
qui empêche un site web de rediriger son propre nom vers le Pi (DNS rebinding) et de
piloter le tableau depuis le téléphone d'un spectateur.

**Correctif.** Ajoutez le nom dans `~/SplouchData/settings.json` et redémarrez le
service :

```json
"allowed_hosts": ["scores.club.example"]
```

</details>

<details>
<summary><strong><code>apt update</code> échoue en <code>404 NOT FOUND</code> sur tous les dépôts</strong></summary>

**Symptôme.** Tous les dépôts échouent en même temps, `deb.debian.org` comme
`archive.raspberrypi.com`, alors que le réseau fonctionne par ailleurs :

```text
Err:4 http://deb.debian.org/debian trixie Release
  404  NOT FOUND [IP: 151.101.138.132 80]
```

**Cause.** Le proxy du réseau intercepte le HTTP en clair et répond avec sa propre page
404. Deux miroirs indépendants ne tombent pas en même temps ; le proxy local est le
facteur commun.

**Correctif.** L'installateur passe apt en `https://` avant toute installation, ce que
le proxy ne peut pas altérer. Si le problème survient hors de l'installateur, appliquez
la même réécriture à la main :

```bash
sudo sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://archive.raspberrypi.com|https://archive.raspberrypi.com|g; s|http://security.debian.org|https://security.debian.org|g' \
  /etc/apt/sources.list.d/*.sources
sudo rm -rf /var/lib/apt/lists/* && sudo apt update
```

**Si le HTTPS échoue aussi**, le réseau bloque complètement les miroirs. Faites autoriser
`deb.debian.org` et `archive.raspberrypi.com`, ou installez depuis un autre réseau (un
partage de connexion de téléphone fonctionne). Une horloge erronée produit des erreurs
semblables — vérifiez `date`.

</details>

<details>
<summary><strong>Le flux UDP de la console ou le Cloud, jamais les deux</strong></summary>

**Symptôme.** La console diffuse sur un réseau fermé branché au port Ethernet du Pi,
et internet arrive par le WiFi du site. Avec Ethernet configuré, le tableau reçoit la
console mais le Cloud décroche ; sans, c'est l'inverse.

**Cause.** Splouch supposait qu'Ethernet était le routeur du site. Une adresse
statique exigeait un routeur, qui prenait la route par défaut devant le WiFi : le
trafic du Cloud partait dans le réseau fermé et s'y arrêtait. Un réseau fermé a
rarement un serveur DHCP non plus : en DHCP, Ethernet n'avait pas d'adresse et
l'écoute UDP ne s'ouvrait jamais.

**Solution.** **Réglages → Réseau → IP statique Ethernet** : décocher *Ethernet a
internet*, entrer une adresse sur le sous-réseau de la console (routeur vide) et
maintenir **Statique**. Ethernet ne prend alors jamais la route par défaut ;
**Internet** dans *État* devrait indiquer *par WiFi*. Si *État* signale des
sous-réseaux qui se chevauchent, le réseau de la console et le WiFi utilisent les
mêmes adresses : choisir l'adresse Ethernet sur un autre sous-réseau si la console le
permet.

Sans serveur DHCP sur le réseau de la console, Ethernet en DHCP se rabat sur une
adresse `169.254.x.x` (*État* indique *pas de serveur DHCP*) : assez pour entendre une
console qui diffuse à tous les réseaux. *État* montre alors d'où la console envoie
(*console 192.168.0.20, il y a 5 s*) ; s'il signale que la console est sur un autre
sous-réseau, donner une adresse statique sur ce sous-réseau.

</details>

<details>
<summary><strong>Le WiFi du site a une page de connexion (portail captif)</strong></summary>

**Symptôme.** Le Pi rejoint le WiFi et obtient une adresse, mais le Cloud ne se
connecte jamais. **Réglages → Réseau → Internet** indique *Page de connexion en
travers*.

**Cause.** Le portail ne laisse passer que l'appareil qui l'a accepté, par son adresse
MAC. Le Pi n'a pas de navigateur, et l'accepter depuis les Réglages sur un autre
ordinateur fait passer cet ordinateur, pas le Pi.

**Solution.** Sous **Réseaux WiFi → Le WiFi demande une page de connexion ?** :
accepter le portail sur un téléphone ou un portable, copier l'adresse WiFi de cet
appareil pour ce réseau, déconnecter l'appareil de ce WiFi, puis entrer l'adresse et
maintenir **Utiliser**. Le Pi se reconnecte avec elle, et le portail voit l'appareil
qu'il a déjà accepté. **Celle du Pi** remet l'adresse du Pi. Sinon, demander au site
de laisser passer la MAC du Pi (affichée là), ou utiliser un partage de connexion.

**Quand la connexion expire** (souvent après quelques heures), un bandeau au-dessus
de chaque onglet des Réglages indique *Cloud coupé : le WiFi redemande sa page de
connexion*. Le même appareil doit accepter à nouveau pendant que le Pi est hors de ce
WiFi : désactiver le WiFi du Pi (par Ethernet ou l'écran du Pi), accepter sur
l'appareil, le déconnecter, puis réactiver le WiFi du Pi.

</details>
