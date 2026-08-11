# Minecraft Server Manager (MSM) 2.0

Panneau de contrôle web pour administrer **plusieurs serveurs Minecraft** depuis une seule
interface : console temps réel, joueurs, mods, plugins, configurations, événements,
sauvegardes et tâches programmées.

> Réécriture complète. La version 1 (script Flask mono-serveur) reste consultable
> au tag [`v1.0-legacy`](../../tree/v1.0-legacy).

## Principes

| | |
|---|---|
| **Multi-serveurs** | Chaque serveur a son propre processus, son propre groupe de processus, sa propre console. Arrêter l'un n'affecte jamais les autres. |
| **Générique** | Vanilla, Forge, NeoForge, Mohist, Paper… Aucun nom de JAR codé en dur, l'interface s'adapte à ce que le dossier contient réellement. |
| **Temps réel** | WebSocket avec numéros de séquence et reprise après coupure. Aucun polling. |
| **Sécurisé** | Authentification obligatoire, RBAC par serveur, audit de toutes les actions, résolution de chemin stricte, aucune commande shell dangereuse. |
| **Portable** | Production Linux (systemd), développement et déploiement Windows pleinement supportés. Vérifié par CI sur les deux OS. |

## État d'avancement

- [x] **Phase 0** — fondations : configuration, logging, erreurs, base de données, CI
- [x] **Phase 1** — process manager, authentification, RBAC, audit, CRUD serveurs, console temps réel, WebSocket, interface React
- [x] **Phase 2** — joueurs : identité et historique, skins, statuts (opérateur, banni, liste blanche), modération
- [x] **Phase 3** — fichiers : mods, plugins, éditeur de configurations, server.properties
- [x] **Phase 4** — événements : actions immédiates, séquences enregistrées, exécution en tâche de fond annulable
- [x] **Phase 5** — administration : sauvegardes (mondes et configurations, à chaud), restauration, historique des ressources
- [x] **Phase 6** — extensions : planification, notifications Discord, installation de versions
- [ ] **Phase 7** — agents : piloter des serveurs hébergés sur d'autres machines

## Installation sur un serveur Linux

```bash
sudo ./install.sh
```

Utilisateur système dédié, unité systemd durcie, base initialisée, interface
compilée et servie par MSM lui-même. Détails et options : [docs/DEPLOY.md](docs/DEPLOY.md).

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — conception complète, décisions techniques et justifications
- [Déploiement](docs/DEPLOY.md) — installation, reverse proxy, sauvegarde, mise à jour
- [Développement](docs/DEVELOPMENT.md) — installation locale, tests, conventions

## Démarrage rapide (développement)

```bash
cd backend
python -m venv .venv
# Linux/macOS : source .venv/bin/activate
# Windows     : .venv\Scripts\activate
pip install -e ".[dev]"
```

Préparer la base et le premier compte, puis démarrer :

```bash
python -m msm.cli migrate
```

```bash
python -m msm.cli createadmin flavien
```

```bash
python -m msm.cli serve
```

L'API répond alors sur <http://127.0.0.1:8000/api/v1/health> et sa documentation
interactive sur <http://127.0.0.1:8000/api/docs>.

Dans un second terminal, l'interface :

```bash
cd frontend
```

```bash
npm install
```

```bash
npm run dev
```

Le panneau est alors accessible sur <http://localhost:5173>. Le serveur de
développement relaie `/api` et `/ws` vers le backend, de sorte que le cookie de
session fonctionne exactement comme en production.

Les tests :

```bash
pytest
```

## Licence

À définir.
