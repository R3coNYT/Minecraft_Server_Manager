# Minecraft Server Manager (MSM) 2.0

Panneau de contrôle web pour administrer **plusieurs serveurs Minecraft** depuis une seule
interface : console temps réel, joueurs, mods, plugins, configurations et événements.

> Réécriture complète. La version 1 (script Flask mono-serveur) reste consultable sur la
> branche `main` et le tag `v1.0-legacy`.

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
- [ ] **Phase 1** — cœur : process manager, console, WebSocket, auth, frontend *(en cours)*
- [ ] **Phase 2** — Minecraft : détection, EULA, joueurs, skins, actions
- [ ] **Phase 3** — fichiers : mods, plugins, configs, server.properties
- [ ] **Phase 4** — événements
- [ ] **Phase 5** — administration : audit, utilisateurs, monitoring, sauvegardes
- [ ] **Phase 6** — extensions

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — conception complète, décisions techniques et justifications
- [Développement](docs/DEVELOPMENT.md) — installation locale, tests, conventions

## Démarrage rapide (développement)

```bash
cd backend
python -m venv .venv
# Linux/macOS : source .venv/bin/activate
# Windows     : .venv\Scripts\activate
pip install -e ".[dev]"
pytest
```

## Licence

À définir.
