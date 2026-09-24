# Plan : notifications par serveur et création de serveurs

Conception validée le 24/09/2026. Trois phases, chacune testée, commitée et
poussée avant de passer à la suivante.

| Phase | Contenu | État |
| --- | --- | --- |
| 1 | Notifications Discord par serveur, webhook global recentré | terminée |
| 2 | Création de serveur de zéro — backend | en cours |
| 3 | Création de serveur de zéro — interface | à faire |

---

## Phase 1 — Notifications Discord

### Webhook global

**Settings → Discord notifications** reste, mais ne sert plus qu'aux événements
de MSM lui-même :

- **Server created**
- **Server deleted**

L'URL déjà enregistrée est conservée. Ses événements cochés sont remplacés par
ces deux-là lors de la mise à jour.

### Webhook par serveur

Nouvel onglet **Notifications** dans chaque serveur : URL, activation, événements
cochés et bouton de test. Les événements sont ceux qui concernent un serveur :

- plantage ;
- redémarrage automatique ;
- démarrage ;
- arrêt ;
- sauvegarde réussie ;
- sauvegarde échouée ;
- tâche programmée échouée.

Un serveur sans webhook n'envoie rien. Il faut le droit de modifier le serveur
(`server:edit`).

### Côté code

- Nouvelle table `server_notifications` : une ligne par serveur, URL chiffrée
  avec la clé applicative comme aujourd'hui. Migration Alembic, qui recentre
  aussi les événements du réglage global.
- Le notifier regroupe les messages par serveur et envoie chaque lot au salon
  de ce serveur. Les événements globaux partent vers le webhook global.
- Le formulaire actuel devient un composant partagé entre l'onglet serveur et
  les réglages globaux.

---

## Phase 2 — Créer un serveur de zéro (backend)

### Déroulement

La création est une **tâche de fond** qui publie sa progression :

1. création du dossier ;
2. téléchargement, avec vérification de l'empreinte quand la source en publie
   une (Mojang, Paper, Purpur, NeoForge et Mohist/Youer en publient ; Fabric
   non) ;
3. pour NeoForge seulement : lancement de l'installeur officiel avec Java
   (`--installServer`, 1 à 3 min). Il produit `run.sh`, et MSM écrit la mémoire
   choisie dans `user_jvm_args.txt` ;
4. écriture de `eula.txt` (seulement si le CLUF a été accepté) et du port dans
   `server.properties` ;
5. enregistrement du serveur dans MSM.

En cas d'échec, le dossier créé est supprimé et l'erreur est rendue avec sa
cause et la correction à faire.

### Sources

Vérifiées le 24/09/2026.

| Type | Source | Fichier obtenu |
| --- | --- | --- |
| Vanilla, Paper, Purpur | déjà utilisées par MSM | JAR direct |
| Fabric | `meta.fabricmc.net` | JAR de lancement direct (pas d'empreinte publiée) |
| NeoForge | `maven.neoforged.net` | installeur + empreintes SHA-256 |
| Mohist (Forge), Youer (NeoForge) | `api.mohistmc.com` | JAR direct + SHA-256 |

Youer n'existe que pour MC 1.21.1 et 26.2 à cette date.

NeoForge a deux schémas de numérotation à gérer :

- `21.1.x` → MC 1.21.1, `20.4.x` → MC 1.20.4, `21.0.x` → MC 1.21 ;
- `26.3.0.x` → MC 26.3 (nouvelle numérotation de Minecraft).

### Côté code

- Nouveau module `msm/provisioning/` : une interface commune « distribution »
  (lister les versions et les builds, résoudre le fichier, étapes
  d'installation) et le déroulé de la tâche, qui publie sa progression.
- Routes :
  - `GET /provisioning/distributions…` pour les listes de types, versions et
    builds ;
  - `POST /provisioning` qui lance la création ;
  - `GET /provisioning/{job}` pour suivre l'état.
- Les nouvelles sources à JAR direct (Fabric, Mohist, Youer) deviennent aussi
  disponibles dans l'installeur de version des serveurs existants.

---

## Phase 3 — Créer un serveur de zéro (interface)

### Dashboard

Deux boutons :

- **Create a server** (nouveau) ;
- **Add an existing server** (l'actuel, renommé).

### Formulaire de création

- **Nom** et **dossier** : pré-rempli `<racine des serveurs>/<nom-simplifié>`,
  par exemple `/data/minecraft/survie-2`. Refusé si le dossier existe déjà et
  n'est pas vide.
- **Type** : Vanilla, Paper, Purpur, Fabric, NeoForge, Mohist (Forge),
  Youer (NeoForge).
- **Version de Minecraft**, puis **build** quand il y en a (loader Fabric,
  version NeoForge, build Mohist ou Youer). Le plus récent stable est proposé
  par défaut.
- **Mémoire** min/max et **port**.
- **Case CLUF** avec le lien officiel : `eula=true` n'est écrit que si elle est
  cochée.
- **Démarrer une fois créé** : facultatif.

La progression de la tâche s'affiche dans la fenêtre, étape par étape ; une
erreur affiche sa cause et la correction à faire.
