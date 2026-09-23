# Intégration avec un launcher personnalisé

MSM peut relier chaque serveur Minecraft au **serveur de fichiers** d'un launcher
personnalisé (FrankuMC ou tout autre launcher qui distribue un modpack par
manifest). La liaison fonctionne dans les deux sens :

| Sens | Autorité | Effet |
| --- | --- | --- |
| Serveur de fichiers → MSM | le serveur de fichiers, pour le **contenu** | les mods du modpack sont installés sur le serveur Minecraft, mis à jour, retirés |
| MSM → serveur de fichiers | MSM, pour l'**activation** | un mod désactivé dans MSM est retiré du modpack des joueurs à leur prochaine synchronisation |

**Tout part de MSM.** MSM lit le manifest et envoie l'état des mods ; il n'a jamais
besoin d'être joignable depuis Internet. Le serveur de fichiers, lui, doit ajouter
une seule route (`PUT /msm/state`) pour recevoir l'état.

Sommaire :

1. [Mise en place](#1-mise-en-place)
2. [Protocole v1](#2-protocole-v1)
3. [Route de référence (Express)](#3-route-de-référence-express)
4. [Côté launcher (facultatif)](#4-côté-launcher-facultatif)
5. [Comportement de la synchronisation](#5-comportement-de-la-synchronisation)
6. [Sécurité](#6-sécurité)

---

## 1. Mise en place

1. Sur le serveur de fichiers, ajouter la route [`PUT /msm/state`](#3-route-de-référence-express)
   et choisir un jeton long et aléatoire (`msm secret` en génère un).
2. Dans MSM, onglet **Launcher** du serveur Minecraft : renseigner l'adresse du
   serveur de fichiers (celle qu'interroge le launcher, par exemple
   `https://frankumc.frankulin.fr`), le même jeton, les dossiers synchronisés
   (`mods/` par défaut) et l'intervalle.
3. La première synchronisation part dans les secondes qui suivent. Le compte à
   rebours de la suivante s'affiche dans l'en-tête du serveur et dans l'onglet.

Sans jeton, MSM synchronise quand même les mods ; il ne publie simplement rien aux
joueurs. Un serveur de fichiers différent peut être relié à chaque serveur.

## 2. Protocole v1

### 2.1 Manifest — `GET <base>/manifest.json`

Le format FrankuMC, inchangé, avec deux ajouts **facultatifs** :

```json
{
  "packVersion": "1.4.2",
  "mcVersion": "1.21.1",
  "neoforgeVersion": "21.1.77",
  "files": [
    { "path": "mods/create-0.5.1.jar", "sha256": "…64 hex…", "size": 1048576 },
    { "path": "mods/journeymap-5.9.jar", "sha256": "…", "size": 524288, "side": "client" }
  ],
  "disabledFiles": [
    { "path": "mods/lithium-0.14.jar", "sha256": "…", "size": 262144 }
  ]
}
```

| Champ | Rôle |
| --- | --- |
| `files[].side` | `client`, `server` ou `both` (`*` accepté). Un mod `client` n'est **jamais** installé sur le serveur Minecraft. Absent : MSM lit le JAR (`fabric.mod.json`, `quilt.mod.json`, `mods.toml`, `neoforge.mods.toml`). |
| `disabledFiles` | Mods désactivés dans MSM. Écrit par la route `PUT /msm/state`, jamais à la main. Un launcher qui ignore ce champ ne les télécharge simplement plus. |

MSM valide le manifest entier avant d'agir : un chemin absolu, contenant `..`, un
antislash, une empreinte mal formée ou un doublon font **refuser tout le
manifest** — rien n'est supprimé sur la foi d'un manifest à moitié valide.

L'en-tête `ETag` est honoré : un manifest inchangé n'est pas retéléchargé.

### 2.2 Fichiers — `GET <base>/files/<chemin>`

Chaque segment du chemin est encodé séparément (`encodeURIComponent`), comme le
fait le launcher FrankuMC. MSM vérifie la taille et l'empreinte SHA-256 de chaque
fichier reçu ; un fichier altéré est rejeté.

### 2.3 État des mods — `PUT <base>/msm/state`

Requête envoyée par MSM :

```http
PUT /msm/state HTTP/1.1
Authorization: Bearer <jeton>
Content-Type: application/json

{
  "protocol": 1,
  "server": "frankumc",
  "revision": 7,
  "disabledFiles": ["mods/lithium-0.14.jar"]
}
```

| Champ | Rôle |
| --- | --- |
| `protocol` | Toujours `1` pour cette version. Refuser toute autre valeur (`400`). |
| `server` | Nom du serveur dans MSM, pour les journaux. |
| `revision` | Compteur croissant, **informatif** : chaque envoi porte l'état complet, la route applique le dernier reçu. |
| `disabledFiles` | Liste **complète** des chemins désactivés. Liste vide : tout est actif. Un chemin inconnu du manifest est ignoré. |

Réponses attendues :

| Code | Signification pour MSM |
| --- | --- |
| `2xx` | État publié. |
| `400` | Corps invalide. |
| `401` / `403` | Jeton refusé — affiché tel quel dans l'onglet Launcher. |
| `404` | Route absente — MSM renvoie vers cette documentation. |
| autre | Erreur temporaire : nouvel essai deux minutes plus tard. |

MSM publie dès qu'un mod est activé ou désactivé, après chaque synchronisation,
et à la demande (bouton **Publier**). Il ne suit **aucune redirection** : le jeton
ne part qu'à l'adresse configurée.

## 3. Route de référence (Express)

À placer dans le serveur Express du serveur de fichiers. Le module applique l'état
reçu au manifest, et le **réapplique après chaque régénération** du manifest —
sans quoi un mod désactivé réapparaîtrait pour les joueurs à la prochaine mise à
jour du modpack.

```js
// msm-state.js
const crypto = require('crypto')
const express = require('express')
const fs = require('fs/promises')
const path = require('path')

const MANIFEST = process.env.MANIFEST_PATH || path.join(__dirname, 'public', 'manifest.json')
const STATE = path.join(path.dirname(MANIFEST), '.msm-state.json')
const TOKEN = process.env.MSM_TOKEN || ''

// Une écriture à la fois : deux envois rapprochés ne doivent pas s'entrelacer.
let queue = Promise.resolve()
const serialized = (task) => (queue = queue.then(task, task))

function sameToken(header) {
  const expected = Buffer.from(`Bearer ${TOKEN}`)
  const received = Buffer.from(header || '')
  return TOKEN.length >= 16 && expected.length === received.length &&
    crypto.timingSafeEqual(expected, received)
}

// Écriture atomique : le launcher ne lit jamais un manifest à moitié écrit.
async function writeAtomic(file, data) {
  const temporary = `${file}.${process.pid}.tmp`
  await fs.writeFile(temporary, JSON.stringify(data, null, 2))
  await fs.rename(temporary, file)
}

async function readDisabled() {
  try {
    return new Set(JSON.parse(await fs.readFile(STATE, 'utf8')).disabledFiles)
  } catch {
    return new Set()
  }
}

/** Range les entrées du manifest entre `files` et `disabledFiles`. */
async function applyStoredState() {
  const disabled = await readDisabled()
  const manifest = JSON.parse(await fs.readFile(MANIFEST, 'utf8'))
  const all = [...(manifest.files || []), ...(manifest.disabledFiles || [])]
  manifest.files = all.filter((entry) => !disabled.has(entry.path))
  manifest.disabledFiles = all.filter((entry) => disabled.has(entry.path))
  await writeAtomic(MANIFEST, manifest)
}

const router = express.Router()

router.put('/msm/state', express.json({ limit: '1mb' }), (req, res) => {
  if (!sameToken(req.get('authorization'))) return res.sendStatus(401)

  const { protocol, revision, disabledFiles } = req.body || {}
  const valid = protocol === 1 && Number.isInteger(revision) &&
    Array.isArray(disabledFiles) && disabledFiles.every((p) => typeof p === 'string')
  if (!valid) return res.status(400).json({ error: 'corps invalide' })

  serialized(async () => {
    await writeAtomic(STATE, { revision, disabledFiles, receivedAt: new Date().toISOString() })
    await applyStoredState()
  }).then(
    () => res.sendStatus(204),
    (error) => {
      console.error('msm/state', error)
      res.sendStatus(500)
    },
  )
})

module.exports = { router, applyStoredState: () => serialized(applyStoredState) }
```

Branchement :

```js
const msm = require('./msm-state')
app.use(msm.router)

// À la fin du script qui régénère manifest.json :
await msm.applyStoredState()
```

Si le script de génération écrit `manifest.json` directement, le faire écrire dans
un fichier temporaire puis renommer (même principe que `writeAtomic`) : un launcher
qui lit pendant l'écriture recevrait sinon un JSON tronqué.

Derrière nginx, la route passe comme les autres. Pour la réserver à MSM :

```nginx
location = /msm/state {
    allow 203.0.113.10;   # adresse publique de la machine MSM
    deny all;
    proxy_pass http://127.0.0.1:3000;
}
```

## 4. Côté launcher (facultatif)

**Sans modification**, un launcher comme FrankuMC fonctionne déjà : il ignore
`disabledFiles`, ne voit plus le mod dans `files`, et sa suppression sélective le
retire du dossier du joueur. Réactivé dans MSM, le mod revient dans `files` et
est retéléchargé.

**Pour éviter le retéléchargement**, le launcher peut traiter `disabledFiles`
explicitement :

- mod de `disabledFiles` présent chez le joueur → le renommer en `<nom>.disabled`
  (Forge, NeoForge et Fabric ignorent ces fichiers) et le garder comme fichier
  géré dans `.frankumc-sync-state.json`, pour que la suppression sélective ne le
  supprime pas ;
- mod de `files` présent sous `<nom>.disabled` avec la bonne empreinte → le
  renommer sans le retélécharger.

## 5. Comportement de la synchronisation

- **Dossiers synchronisés** : seules les entrées sous ces dossiers (`mods/` par
  défaut) sont considérées. Les configurations du modpack sont destinées aux
  joueurs, pas au serveur.
- **Mods client** : jamais installés. Le côté vient, par ordre de priorité, du
  choix forcé dans MSM, du champ `side` du manifest, puis de la lecture du JAR.
  Dans le doute, un mod est installé : un mod manquant empêche le serveur de
  démarrer, un mod client de trop se voit dans la console. Un mod mal détecté se
  corrige dans la liste du modpack de l'onglet Launcher.
- **Mods ajoutés à la main** : jamais touchés. MSM ne retire que les fichiers qu'il
  a lui-même installés ; un fichier identique à celui du manifest est repris en
  charge sans téléchargement.
- **Désactivation** : un mod désactivé dans MSM reste désactivé, même quand le
  modpack en publie une nouvelle version. `disabledFiles` fait partie du modpack :
  un mod désactivé n'est pas supprimé du serveur.
- **Serveur en marche** : les fichiers sont téléchargés et vérifiés tout de suite,
  mais appliqués **au prochain démarrage**, juste avant le lancement de Java — ou
  dès que le serveur est trouvé arrêté. Aucun joueur n'est déconnecté.
- **Suppression massive** : si le manifest ferait retirer plus de 5 fichiers et
  plus de la moitié de ceux installés par MSM (manifest régénéré à vide, mauvais
  dossier…), la synchronisation est bloquée jusqu'à confirmation explicite, en
  ressaisissant le nom du serveur.
- Les fichiers préparés attendent dans `<MSM_DATA_DIR>/launcher-staging/`, hors du
  dossier du serveur : un démarrage ne charge jamais un fichier à moitié téléchargé.

La boucle de fond se réveille toutes les `MSM_LAUNCHER_TICK_S` secondes (30 par
défaut) : c'est la précision du compte à rebours.

## 6. Sécurité

- HTTPS est exigé, sauf pour une adresse locale ou du réseau privé : le jeton
  voyage avec chaque publication.
- Le jeton est chiffré en base (même clé que les autres secrets de MSM), n'est
  jamais renvoyé par l'API — seuls ses quatre derniers caractères sont affichés —
  ni écrit dans le journal d'audit.
- Aucune redirection n'est suivie à la publication.
- Chaque chemin du manifest est validé puis confiné au dossier du serveur ; chaque
  fichier est vérifié par taille et SHA-256 avant d'être mis en place, par
  remplacement atomique.
- Configurer, synchroniser et publier demandent la permission `server:edit`.
