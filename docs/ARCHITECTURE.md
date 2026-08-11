# Architecture — Minecraft Server Manager 2.0

Document de référence : conception, décisions techniques et leurs justifications.
Il décrit la cible complète ; l'état d'avancement réel est suivi dans le
[README](../README.md).

---

## 1. Contraintes structurantes

Six exigences déterminent l'essentiel des choix. Tout le reste en découle.

| # | Contrainte | Conséquence |
|---|---|---|
| 1 | N serveurs, isolation stricte des processus | Le backend est **propriétaire** des processus → un seul processus applicatif, asynchrone plutôt que multi-worker |
| 2 | Logs temps réel sans interrogation périodique | Pipeline poussé : lecteur → tampon → bus → WebSocket |
| 3 | Commandes envoyées sur l'entrée standard | Les tubes restent ouverts pendant toute la vie du serveur → état en mémoire, non reconstructible depuis la base |
| 4 | Généricité (aucun JAR codé en dur) | Registres et abstractions (`Launcher`, `Capability`, `Action`) plutôt que des `if type == "mohist"` |
| 5 | Sécurité prioritaire | RBAC par serveur, audit, résolution de chemin sûre — au niveau infrastructure, pas dans les routes |
| 6 | Machines distantes à terme | Le runtime est derrière une interface `Agent` dès le premier jour |

### Trois limites assumées et documentées

**Le ping par joueur n'existe pas en Minecraft vanilla.** Aucune commande console
ne le fournit. Le champ `ping_ms` existe dans l'API et vaut toujours `null` ;
l'interface affiche `—` plutôt qu'une valeur inventée. Un fournisseur RCON ou un
plugin pourra le renseigner sans changer le contrat.

**Les statuts des joueurs viennent des fichiers du serveur, pas des logs.**
`ops.json`, `banned-players.json`, `whitelist.json` et `usercache.json` sont la
seule source fiable : la console n'annonce pas qui est opérateur, et rejouer
l'historique des commandes serait faux dès qu'un fichier a été édité à la main.
Ces fichiers sont lus, jamais écrits — accorder un statut passe toujours par une
commande console, pour que le serveur en tienne compte immédiatement.

**`run.sh` et l'entrée standard.** Écrire dans l'entrée d'un script fonctionne
seulement si celui-ci ne la redirige pas et ne met pas Java en arrière-plan. Trois
parades : détection du vrai processus Java descendant, mode PTY optionnel, repli
RCON. Quand aucune ne s'applique, la console passe en lecture seule **et le dit**.

**Redémarrage de MSM pendant qu'un serveur tourne.** Les tubes sont perdus, et
définitivement : ils appartenaient au processus MSM précédent. Le couple `pid` +
`date de création` est persisté à chaque changement d'état ; au démarrage, un
serveur encore vivant est **réadopté** — état `UNKNOWN`, console alimentée par
lecture de `logs/latest.log`, arrêt possible via le groupe de processus. La
console reste en lecture seule et l'interface l'annonce, plutôt que de laisser
croire qu'une commande a été transmise.

La comparaison de la date de création n'est pas décorative : sans elle, un PID
réattribué par le système ferait adopter — puis arrêter — un programme sans
aucun rapport.

---

## 2. Vue en couches

```
┌───────────────────────────────────────────────────────────────┐
│  FRONTEND  React + TypeScript                                 │
│  REST (TanStack Query)          WebSocket (reprise par seq)   │
└───────────┬───────────────────────────────┬───────────────────┘
┌───────────▼───────────────────────────────▼───────────────────┐
│  API — FastAPI                                                │
│  routeurs · authentification · RBAC · validation Pydantic     │
│  erreurs → {code, message, cause, remediation, trace_id}      │
├───────────────────────────────────────────────────────────────┤
│  SERVICES  (logique métier, sans I/O HTTP)                    │
├───────────────────────────────────────────────────────────────┤
│  DOMAINE  msm/core/ — aucune dépendance framework             │
│  états · permissions · analyse de logs · commandes · danger   │
├───────────────────────────────────────────────────────────────┤
│  RUNTIME  Agent → Supervisor → ServerRuntime[]                │
│     ├── ProcessHandle (séquence d'arrêt, entrée/sortie)       │
│     ├── ProcessBackend (POSIX | Windows)                      │
│     ├── LogPipeline → RingBuffer                              │
│     └── StatsCollector                                        │
├───────────────────────────────────────────────────────────────┤
│  BUS D'ÉVÉNEMENTS  (publication/abonnement en mémoire)        │
├───────────────────────────────────────────────────────────────┤
│  INFRASTRUCTURE  SQLAlchemy · SafeFS · RCON · skins           │
└───────────────────────────────────────────────────────────────┘
```

La couche `msm/core/` ne dépend d'aucun framework : c'est ce qui la rend
intégralement testable sans démarrer d'application ni de base de données.

---

## 3. Stack technique

### Backend

| Choix | Justification |
|---|---|
| **FastAPI + uvicorn (1 worker)** | WebSocket natif, injection de dépendances adaptée au RBAC par serveur, typage de bout en bout. Un seul worker car le superviseur possède les processus |
| **asyncio** | 3 à 10 flux de sortie lus en continu pendant que l'API répond : cas d'usage exact de l'asynchrone |
| **SQLAlchemy 2.0 async + Alembic** | API typée ; SQLite aujourd'hui, PostgreSQL par simple changement d'URL |
| **Pydantic v2 / pydantic-settings** | Validation des entrées et de la configuration, schéma OpenAPI généré |
| **Argon2id** | Standard actuel du hachage de mots de passe |
| **psutil** | Statistiques par arbre de processus, vérification fiable de vie d'un PID |
| **structlog** | Logs MSM structurés, séparés des logs Minecraft |

**FastAPI plutôt que Flask** : Flask + gevent gère mal les sous-processus et les
tubes. **WebSocket natif plutôt que Socket.IO** : la reprise par numéro de
séquence, indispensable ici, n'est pas fournie par Socket.IO et devrait de toute
façon être écrite.

### Frontend (phase 1)

React 18 + TypeScript + Vite · Tailwind + shadcn/ui · TanStack Query · Zustand ·
TanStack Virtual · Monaco Editor.

**Console : liste virtualisée, pas xterm.js.** L'émulation de terminal n'apporte
rien ici et complique la recherche, le filtrage et le clic sur un pseudo. Les
codes couleur Minecraft (`§a`) et ANSI sont analysés et convertis en styles.

---

## 4. Gestion des processus

### Machine à états

```
                    ┌──────────── start() ───────────┐
                    ▼                                │
  OFFLINE ──▶ STARTING ──(« Done (x.xxxs)! »)──▶ ONLINE
     ▲            │                                  │
     │            │ sortie inattendue                │ stop()
     │            ▼                                  ▼
     │         CRASHED ◀──── sortie non demandée ── STOPPING
     │            │                                  │
     └── reset ───┘◀──── sortie demandée, code 0 ────┘

  UNKNOWN : serveur réadopté après un redémarrage de MSM
```

Les transitions autorisées sont déclarées explicitement dans
`msm/core/states.py` ; une transition illégale lève une exception plutôt que de
corrompre silencieusement l'état affiché.

### Séquence d'arrêt

```
1. « stop » sur l'entrée standard      → arrêt propre, le monde est sauvegardé
2. attente de stop_timeout_s (60 s)
3. signal d'arrêt au groupe            → SIGTERM (POSIX uniquement)
4. attente de kill_timeout_s (15 s)
5. terminaison de l'arbre              → SIGKILL au groupe / TerminateJobObject
```

Chaque étape ne cible **que** le groupe de processus du serveur concerné. Aucune
n'effectue de recherche par nom ou par ligne de commande. Le backend POSIX refuse
en outre de signaler le groupe de MSM lui-même : un identifiant corrompu ne peut
pas faire s'auto-terminer le panel.

### Portabilité

| Opération | Linux | Windows |
|---|---|---|
| Isolation | `start_new_session=True` → PGID | `CREATE_NEW_PROCESS_GROUP` + Job Object |
| Arrêt propre | `stop` sur l'entrée standard | **identique** |
| Étape intermédiaire | `SIGTERM` au groupe | *aucune* — voir ci-dessous |
| Terminaison forcée | `SIGKILL` au groupe | `TerminateJobObject`, repli par arbre psutil |
| Anti-PID recyclé | date de création (`/proc`) | date de création (psutil) |

**Il n'existe pas d'équivalent Windows au `SIGTERM` pour une JVM.**
`CTRL_BREAK_EVENT` y déclenche un vidage de threads, pas un arrêt, et
`CTRL_C_EVENT` est ignoré par un processus créé avec son propre groupe. La
séquence saute donc l'étape 3 sous Windows au lieu d'attendre en vain
(`ProcessBackend.supports_graceful_signal`).

**Le Job Object n'active pas `KILL_ON_JOB_CLOSE`** : un plantage du panel ne doit
pas emporter les parties en cours. Comme sous POSIX, les serveurs survivent à MSM.

### Redémarrage automatique

`NEVER` / `ON_CRASH` / `ALWAYS`, avec délai exponentiel borné et plafond de
plantages consécutifs. Le compteur est remis à zéro dès que le serveur a tenu en
ligne assez longtemps pour être jugé stable. Passé le plafond, MSM abandonne et le
signale — une boucle infinie de redémarrage est structurellement impossible.

---

## 5. Pipeline de logs

```
sortie du processus → analyse → numérotation → tampon → détection → bus → WebSocket
```

- **Numérotation** : chaque ligne porte un numéro monotone propre au serveur. Un
  client déconnecté renvoie son dernier numéro et récupère exactement la suite.
- **Tampon circulaire borné** : les N dernières lignes en mémoire, et un compteur
  des lignes écartées. L'interface affiche « X lignes antérieures non conservées »
  au lieu de laisser croire à un historique complet.
- **Abonné saturé** : les événements les plus anciens sont perdus et comptés,
  jamais accumulés jusqu'à étouffer le panel.
- **Sans abonné, rien n'est produit** : un serveur bavard dont aucune console
  n'est ouverte ne consomme que son tampon.

Les lignes émises par MSM lui-même (annonces d'action, diagnostics) traversent le
même pipeline mais **ne sont pas soumises à la détection d'événements** : le panel
ne doit pas déclencher une arrivée de joueur en écrivant un message qui y
ressemble.

---

## 6. Sécurité

### Commandes console

Une commande est écrite sur l'entrée standard, où le séparateur d'instructions est
le saut de ligne. Tout caractère de contrôle est donc **rejeté**, jamais nettoyé en
silence : sans cela, une requête autorisée à exécuter une commande pourrait en
exécuter dix, dont une seule serait auditée.

La classification en `SAFE` / `SENSITIVE` / `DESTRUCTIVE` porte sur le **verbe
normalisé**, jamais sur une recherche de sous-chaîne — `say attention je vais stop
le serveur` reste un message anodin. Le préfixe d'espace de noms est retiré
(`minecraft:kill` → `kill`), sans quoi la classification serait contournable. Une
commande sensible visant tous les joueurs est escaladée : `ban @a` n'a pas la
portée de `ban Flavien`.

### Permissions

Trois rôles (`ADMIN`, `MODERATOR`, `VIEWER`) donnant chacun un jeu de permissions
atomiques, plus une surcharge par serveur. Toute vérification porte sur le couple
`(permission, serveur)`. **En cas de conflit entre octroi et révocation, le refus
l'emporte.**

### Système de fichiers

Toute opération passe par `resolve_within` — aucune route ne construit de chemin
elle-même. La comparaison porte sur le chemin **résolu**, donc après résolution
des liens symboliques : un lien `mods/evasion` pointant vers `/etc` désigne bien
`/etc` et se fait refuser, là où une vérification de préfixe textuel l'aurait
laissé passer. Sous Windows, la comparaison est normalisée en casse, sans quoi
`MODS/../..` contournerait le contrôle.

Les noms de fichiers téléversés sont **reconstruits**, jamais réutilisés : seuls
les caractères reconnus sont conservés, l'extension est vérifiée contre une liste
courte propre à chaque dossier, les noms réservés de Windows sont refusés, et le
bit d'exécution est retiré. MSM ne lance jamais un fichier reçu.

**Les écritures sont atomiques** : fichier temporaire du même dossier puis
remplacement. Une coupure ne peut pas laisser un `server.properties` tronqué,
ce qui empêcherait le serveur de redémarrer.

L'éditeur de configurations **valide sans jamais reformater** : le contenu soumis
est vérifié syntaxiquement puis écrit tel quel, fins de ligne comprises. Passer
par un analyseur puis un sérialiseur détruirait les commentaires dont dépendent
la plupart des configurations de mods.

### Audit

Table en ajout seul. `actor_username` et `actor_role` sont des copies figées au
moment de l'action : si le compte change de rôle ou disparaît, le journal continue
de dire qui a fait quoi, avec quels droits, à ce moment-là.

---

## 7. Modèle de données

`users` · `user_sessions` · `servers` · `server_settings` · `server_runtime_state`
· `server_permissions` · `audit_logs` · `players` · `skin_cache` ·
`event_definitions` · `event_runs` · `backups` · `metric_samples` · `app_settings`

Deux points notables :

- **`user_sessions` stocke l'empreinte du jeton, jamais le jeton.** Une fuite de
  la base ne permet pas d'usurper une session, et la révocation côté serveur reste
  possible — ce qu'un jeton autoporteur interdit.
- **`server_runtime_state.process_create_time`** accompagne le PID : c'est ce qui
  permet de vérifier, à la réadoption, que le processus est bien le nôtre et non un
  PID recyclé.

Les tables des phases 4 et 5 (`event_*`, `backups`) sont créées dès maintenant :
les définir tôt évite une migration de schéma sur une base déjà en service.

---

## 8. API REST

Base `/api/v1`, cookie de session HttpOnly, jeton CSRF sur les mutations.

Format d'erreur unique, qui alimente directement l'affichage « Cause / Action » :

```json
{
  "code": "SERVER_START_FAILED",
  "message": "Impossible de démarrer le serveur.",
  "cause": "run.sh n'est pas exécutable.",
  "remediation": "chmod +x /data/minecraft/modded/run.sh",
  "trace_id": "a3f9c21b4e07"
}
```

Une exception imprévue ne fuit jamais son détail : le message reste générique et
la trace complète part dans `logs/msm.log`, retrouvable par son `trace_id`.

Principaux points d'entrée : `auth` · `users` · `servers` (CRUD, `detect`,
`capabilities`) · cycle de vie (`start`, `stop`, `restart`, `kill`) · `logs`
(historique et recherche uniquement) · `command` · `players` et leurs actions ·
`files/{area}` (mods, plugins, datapacks — logique commune) · `configs` ·
`properties` · `events` · `audit` · `system`.

Deux écarts par rapport à une lecture littérale de la spécification, et leur
raison : le chemin de fichier passe en paramètre de requête plutôt qu'en segment
d'URL (les sous-dossiers casseraient un segment) ; les commandes passent par REST
et non par WebSocket, pour qu'elles empruntent le même chemin d'autorisation et
d'audit que toute autre action.

---

## 9. Événements WebSocket

Un endpoint unique `/ws`, authentifié au handshake.

Enveloppe : `{ "t": type, "sid": server_id, "seq": n, "ts": …, "d": {…} }`

Client → serveur : `subscribe` (avec `resume_from`), `unsubscribe`, `ping`.

Serveur → client : `server.status` · `server.log` (groupé toutes les 100 ms) ·
`server.stats` · `server.players` · `server.player.join` / `.leave` ·
`server.crash` · `server.restart.scheduled` · `event.run` · `backup.progress` ·
`system.stats` · `notification` · `log.truncated`.

Ce dernier message existe parce qu'un client trop lent **doit** savoir qu'il a
perdu des lignes : tronquer en silence serait pire que tronquer.

---

## 10. Moteur d'événements

Un événement est une suite d'étapes exécutées **dans l'ordre**, avec des pauses
possibles. Trois décisions le structurent :

**Une action se décrit elle-même.** Chaque action déclare ses champs
(`Field`), son résumé et son niveau de risque ; l'interface construit ses
formulaires à partir de ce catalogue (`GET /events/actions`). Ajouter une action
côté serveur la rend disponible sans toucher au frontend.

**Une séquence est validée à l'enregistrement**, pas à l'exécution. Découvrir
qu'une étape est invalide au milieu d'un tournoi serait le pire moment ; l'erreur
nomme donc l'étape fautive (`Étape 3 — Quantité invalide.`).

**L'exécution survit à la requête HTTP.** Une séquence peut durer une heure : la
tâche s'exécute en tâche de fond, ouvre ses propres sessions de base et publie sa
progression sur le bus (`event.run`). Elle reste annulable à tout instant, y
compris pendant une attente — et l'annulation est consignée avant de se propager,
sinon l'historique garderait une exécution éternellement « en cours ».

Le risque d'une séquence est celui de sa marche la plus dangereuse : une seule
étape `kill` exige la permission `event:run_destructive` **et** une confirmation
explicite pour l'ensemble.

---

## 11. Sauvegardes et historique

**Une sauvegarde emporte les mondes et les configurations, pas les mods.** Un
dossier moddé pèse plusieurs gigaoctets dont l'essentiel est re-téléchargeable ;
les mondes, eux, sont irremplaçables. Sauvegarder le tout rendrait l'opération
assez lente et volumineuse pour qu'on cesse de la faire — et une sauvegarde
qu'on ne fait pas ne protège de rien. Ce qui n'est pas emporté est **inventorié**
dans l'archive : la liste des mods et plugins installés, avec leur état, dit quoi
réinstaller après une reconstruction.

**Un serveur démarré est sauvegardé à chaud**, selon la séquence
`save-off` → `save-all flush` → copie → `save-on`. La confirmation du serveur est
attendue : sans elle, la copie surprendrait des régions en cours d'écriture et
produirait un monde subtilement incohérent — la sauvegarde est alors refusée
plutôt que douteuse. `save-on` part dans un `finally`, un serveur laissé sans
sauvegarde automatique perdrait tout au prochain plantage.

**Restaurer est traité comme destructif** : serveur arrêté obligatoire,
confirmation explicite, sauvegarde de sécurité prise automatiquement avant, et
mondes **remplacés** plutôt que fusionnés — extraire par-dessus laisserait des
régions récentes mêlées à celles d'hier. Chaque membre de l'archive est validé
avant écriture : ni chemin remontant, ni lien symbolique, ni fichier spécial.

L'**historique des ressources** (`metric_samples`) est échantillonné toutes les
30 s et purgé au-delà de sept jours. Les lectures sont agrégées **par la base**
en paliers, et retiennent le maximum de chaque palier : une moyenne lisserait
justement la pointe qu'on cherche.

---

## 12. Extensibilité

Les points d'extension prévus, et ce qu'ils permettent d'ajouter sans refonte :

| Registre | Extension |
|---|---|
| `launchers/registry.py` | nouvelle méthode de démarrage |
| `minecraft/types.py` (`Capability`) | nouvel onglet conditionné au contenu réel du dossier |
| `events/registry.py` | nouveau type d'action d'événement |
| `runtime/agent.py` (`Agent`) | machines distantes |
| `minecraft/players/sources.py` | nouvelle source de données joueur (RCON, plugin, query) |

Une seule anticipation est présente dans le code sans usage immédiat :
l'interface `Agent`. Elle est justifiée parce que l'introduire plus tard imposerait
de réécrire tous les services. Partout ailleurs, on écrit ce dont on a besoin
aujourd'hui.

---

## 13. Déploiement

Production Linux : utilisateur `msm` dédié (jamais root), unité systemd durcie
(`NoNewPrivileges`, `ProtectSystem=strict`, `ReadWritePaths` limité aux dossiers
nécessaires), `install.sh` pour la mise en place complète.

Windows est une plateforme de développement et de déploiement secondaire
pleinement supportée. La garantie ne repose pas sur l'intention mais sur la
**matrice CI** : chaque commit exécute toute la suite de tests — y compris
l'isolation des processus — sur Ubuntu et sur Windows.
