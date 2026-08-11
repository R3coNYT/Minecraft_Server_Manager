# Déploiement sur un serveur Linux

## Installation

Depuis une copie du dépôt sur la machine cible :

```bash
sudo ./install.sh
```

Le script est **idempotent** : le relancer met à jour le code et les dépendances
sans toucher à la configuration, à la base de données ni aux serveurs Minecraft.

### Options

```bash
sudo ./install.sh --servers-root /srv/minecraft --port 8080
```

| Option | Rôle | Défaut |
|---|---|---|
| `--dir` | Dossier d'installation | `/opt/msm` |
| `--config` | Dossier de configuration | `/etc/msm` |
| `--data` | Base de données et caches | `/var/lib/msm` |
| `--logs` | Journaux de MSM | `/var/log/msm` |
| `--servers-root` | Racine des serveurs Minecraft | `/data/minecraft` |
| `--user` | Utilisateur système | `msm` |
| `--host` / `--port` | Adresse d'écoute | `127.0.0.1:8000` |
| `--skip-frontend` | Ne pas compiler l'interface | — |
| `--skip-admin` | Ne pas créer de compte | — |

### Ce que fait le script

1. vérifie Python ≥ 3.11, systemd, `runuser`, et signale l'absence de Java ;
2. crée l'utilisateur système `msm`, **sans shell de connexion** ;
3. prépare les dossiers avec des droits restreints ;
4. copie le code, crée l'environnement virtuel, installe les dépendances ;
5. compile l'interface si `npm` est disponible ;
6. génère `/etc/msm/.env` avec une clé secrète aléatoire, en `640 root:msm` ;
7. applique les migrations de base de données ;
8. demande la création d'un compte administrateur ;
9. installe et démarre l'unité systemd.

Le mot de passe administrateur est saisi directement par la commande `createadmin`,
sans écho : il ne transite ni par une variable du script, ni par une ligne de
commande visible dans `ps`.

## Après l'installation

```bash
systemctl status minecraft-server-manager
```

```bash
journalctl -u minecraft-server-manager -f
```

Créer un compte supplémentaire :

```bash
sudo -u msm /opt/msm/backend/.venv/bin/python -m msm.cli createadmin flavien
```

## Exposition sur le réseau

Par défaut, MSM n'écoute que sur `127.0.0.1`. **Ne pas l'exposer directement sur
Internet sans HTTPS** : le cookie de session circulerait en clair.

La marche à suivre est un reverse proxy TLS. Exemple nginx :

```nginx
server {
    listen 443 ssl http2;
    server_name msm.exemple.fr;

    ssl_certificate     /etc/letsencrypt/live/msm.exemple.fr/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/msm.exemple.fr/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # Indispensable : sans ces deux en-têtes, la console temps réel ne
        # s'établit pas et l'interface reste figée sur « Reconnexion ».
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
    }
}
```

Puis, dans `/etc/msm/.env` :

```bash
MSM_SESSION_COOKIE_SECURE=true
```

et redémarrer le service.

## Interface web

MSM sert lui-même l'interface compilée, sur le même port que l'API. Ce n'est pas
qu'une commodité : le panneau et son API partagent alors la même origine, donc le
cookie de session fonctionne sans `SameSite=None`, sans CORS, et sans proxy
obligatoire.

Si `npm` était absent à l'installation, l'ajouter puis compiler :

```bash
cd /opt/msm/frontend && npm ci && npm run build
```

## Redémarrages et serveurs Minecraft

**Redémarrer MSM ne coupe aucun serveur Minecraft.** Le panneau est un outil
d'administration : son arrêt ne doit pas déconnecter les joueurs.

Au démarrage suivant, MSM **réadopte** les serveurs encore vivants : il retrouve
leur processus par son PID et sa date de création, reprend la lecture de
`logs/latest.log`, et permet de les arrêter. Leur état est affiché `Indéterminé`
et leur console passe en lecture seule — les tubes d'entrée ont disparu avec le
processus précédent, et MSM ne peut plus leur transmettre de commande.

Pour retrouver une console pleinement fonctionnelle, redémarrer le serveur depuis
le panneau.

## Sauvegarde

### Depuis le panneau

Chaque serveur a un onglet **Sauvegardes** : l'archive emporte les mondes et les
configurations, et **inventorie** les mods et plugins installés sans les
embarquer — ils se retéléchargent, les mondes non. Un serveur démarré est
sauvegardé à chaud, sans déconnecter les joueurs.

Les archives sont écrites dans `/var/lib/msm/backups`, et les dix dernières par
serveur sont conservées (`MSM_BACKUP_RETENTION`).

> Une sauvegarde sur le disque qu'elle protège ne protège pas d'une panne de ce
> disque. Pour l'écrire ailleurs, renseigner `MSM_BACKUP_DIR` **et** ajouter ce
> chemin à `ReadWritePaths=` dans l'unité systemd : le durcissement interdit
> sinon toute écriture hors des dossiers déclarés.

### Automatiser

L'onglet **Planification** de chaque serveur programme la sauvegarde — « chaque
jour à 4 h » — ainsi que redémarrages, événements et commandes. Les heures sont
locales au fuseau choisi, changement d'heure compris.

Si MSM était arrêté au moment prévu, l'exécution est rattrapée tant que le retard
reste sous `MSM_SCHEDULER_GRACE_MINUTES` (60 par défaut) ; au-delà elle est
marquée « manquée » et l'occurrence suivante est visée.

### Ce que le panneau ne sauvegarde pas

| Quoi | Où | Pourquoi |
|---|---|---|
| `/etc/msm/.env` | configuration | contient la clé secrète ; sans elle, les secrets chiffrés (webhook Discord, mots de passe RCON) deviennent illisibles |
| `/var/lib/msm/msm.db` | base | comptes, serveurs, audit, historique des joueurs, tâches programmées |
| Les JAR et les mods | dossiers de serveurs | volumineux et re-téléchargeables ; leur liste figure dans chaque archive |

## Accès sortants

MSM n'a besoin d'Internet que pour trois choses, toutes facultatives :

| Vers | Pourquoi |
|---|---|
| `api.mojang.com`, `sessionserver.mojang.com`, `textures.minecraft.net` | pseudos et skins des joueurs |
| `launchermeta.mojang.com`, `piston-*.mojang.com`, `api.papermc.io`, `api.purpurmc.org` | catalogue de versions et téléchargement des JAR |
| `discord.com` | notifications, si un webhook est configuré |

Aucune autre destination n'est possible : les hôtes sont codés en dur et
revérifiés avant chaque requête.

## Mise à jour

```bash
git pull && sudo ./install.sh
```

Le script réinstalle le code et les dépendances, applique les nouvelles
migrations, et redémarre le service. La configuration et la base sont conservées.

## Désinstallation

```bash
sudo systemctl disable --now minecraft-server-manager
sudo rm /etc/systemd/system/minecraft-server-manager.service
sudo systemctl daemon-reload
sudo rm -rf /opt/msm
```

Les données (`/var/lib/msm`), la configuration (`/etc/msm`) et les serveurs
Minecraft ne sont **pas** supprimés — à retirer manuellement si souhaité.
