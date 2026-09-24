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
        # L'adresse réelle du visiteur : sans elle, l'audit et la limite
        # d'inscriptions par adresse verraient tout le monde comme 127.0.0.1.
        # Uvicorn ne la croit que si elle vient de la machine elle-même.
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

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

## Serveurs des comptes : ports et pare-feu

Chaque serveur créé par un compte reçoit un port d'une plage réservée, réglée
dans **Settings → Hosting** (25565 à 25664 par défaut). Le compte ne choisit pas
son port : MSM le réimpose dans `server.properties` à chaque démarrage, et y
désactive RCON et query, qui ouvriraient d'autres ports.

Pour que les joueurs puissent se connecter, cette plage doit être ouverte dans
le pare-feu de la machine (et redirigée par la box si la machine est derrière) :

```bash
sudo ufw allow 25565:25664/tcp
```

Les dossiers des comptes sont créés sous la même page de réglages (par défaut,
la première des racines autorisées), un dossier par compte, nommé d'après son
identifiant immuable — un changement de pseudo ne déplace rien.

## Connexion avec Google

Facultative : sans configuration, le bouton « Continue with Google » n'apparaît
pas. Il faut une adresse publique **en HTTPS** (Google refuse une adresse IP ou
du HTTP, sauf `http://localhost` pour essayer).

1. Dans la [console Google Cloud](https://console.cloud.google.com/apis/credentials),
   créer un projet, configurer l'écran de consentement (type « Externe »,
   champs d'application `openid`, `email`, `profile`), puis créer un
   **ID client OAuth** de type « Application Web ».
2. Y déclarer l'URI de redirection autorisée :
   `https://msm.exemple.fr/api/v1/auth/google/callback`.
3. Dans `/etc/msm/.env` :

   ```bash
   MSM_PUBLIC_URL=https://msm.exemple.fr
   MSM_GOOGLE_CLIENT_ID=123456-abc.apps.googleusercontent.com
   MSM_GOOGLE_CLIENT_SECRET=GOCSPX-...
   ```

4. Redémarrer le service.

Un compte MSM est lié à l'identifiant Google, jamais à l'adresse : un compte
existant se lie depuis son profil, après s'être connecté avec son mot de passe.
La première connexion d'un inconnu suit le mode d'inscription (fermée, sur
invitation, ouverte) et lui fait choisir un pseudo.


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

## Redémarrage de la machine

`install.sh` active le service : **MSM repart tout seul au démarrage de la
machine**, et le script vérifie que l'activation a bien pris — sans quoi la panne
ne se découvrirait qu'à la première coupure de courant.

Que MSM redémarre ne relance pas pour autant les serveurs Minecraft. Deux cas
distincts :

| Situation | Ce qui se passe |
|---|---|
| Redémarrage de **MSM seul** (mise à jour, `systemctl restart`) | Les serveurs Minecraft continuent de tourner et sont **réadoptés** : les joueurs ne sont pas déconnectés. |
| Redémarrage de **la machine** | Tout est éteint. Au retour, MSM relance les serveurs dont l'option **« Démarrer avec MSM »** est cochée (onglet Aperçu du serveur). |

L'option est décochée par défaut : rien ne démarre sans qu'on l'ait demandé. Un
serveur dont le démarrage échoue — dossier sur un disque non remonté, par
exemple — est signalé sans empêcher les autres de repartir.

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

MSM n'a besoin d'Internet que pour ces usages, tous facultatifs :

| Vers | Pourquoi |
|---|---|
| `api.mojang.com`, `sessionserver.mojang.com`, `textures.minecraft.net` | pseudos et skins des joueurs |
| `launchermeta.mojang.com`, `piston-*.mojang.com`, `fill.papermc.io`, `fill-data.papermc.io`, `api.purpurmc.org`, `meta.fabricmc.net`, `maven.neoforged.net`, `api.mohistmc.com` | catalogue de versions, création de serveurs et téléchargement des JAR |
| `discord.com` | notifications, si un webhook est configuré |
| le serveur de fichiers d'un launcher | synchronisation des mods, si une liaison est configurée |

Aucune autre destination n'est possible pour MSM : les hôtes sont codés en dur
et revérifiés avant chaque requête.

Deux exceptions, qui ne passent pas par MSM : **l'installeur de NeoForge**,
exécuté à la création d'un serveur NeoForge, télécharge lui-même Minecraft et
ses bibliothèques (hôtes de Mojang et de NeoForged) ; **Mohist et Youer**
récupèrent leurs bibliothèques au premier démarrage du serveur.

## Mise à jour

Depuis le dossier cloné :

```bash
sudo ./update.sh
```

Le script relit l'installation existante (unité systemd et `.env`) : aucune option à
répéter. Il :

1. récupère la nouvelle version (`git pull`, exécuté sous le compte propriétaire du
   dépôt, avec ses identifiants — utile pour un dépôt privé) ;
2. conserve une copie de la version installée dans `/opt/msm.previous` ;
3. installe la nouvelle unité systemd **avant** d'arrêter MSM, puis l'arrête — les
   serveurs Minecraft continuent de tourner ;
4. sauvegarde la base (API de sauvegarde SQLite) et le `.env` dans
   `/var/lib/msm/update-backups/` (les 5 dernières sont gardées) ;
5. installe la nouvelle version via `install.sh`, applique les migrations,
   redémarre MSM et vérifie que `/api/v1/health` répond.

Si l'étape 5 échoue, l'ancienne version et la base d'avant la mise à jour sont
remises en place automatiquement : rien n'a été écrit en base entre-temps, le
service était arrêté.

| Commande | Effet |
| --- | --- |
| `sudo ./update.sh --rollback` | Revenir à la version d'avant la dernière mise à jour. Le schéma de base est ramené par les migrations inverses : les données saisies depuis sont conservées. |
| `sudo ./update.sh --ref v2.1.0` | Installer une branche, un tag ou un commit précis. |
| `sudo ./update.sh --no-pull` | Installer le code tel qu'il est dans le dossier, sans interroger git. |
| `sudo ./update.sh --force` | Réinstaller même si la version est déjà la dernière. |

Pour une installation antérieure à `update.sh` : lancer une fois `git pull` dans le
dossier cloné, puis `sudo ./update.sh`. Ces installations utilisaient
`KillMode=mixed`, qui faisait tuer les serveurs Minecraft par systemd à chaque
redémarrage de MSM ; la mise à jour installe l'unité corrigée (`KillMode=process`)
avant d'arrêter quoi que ce soit.

`sudo ./install.sh` reste utilisable pour réinstaller : il est idempotent et
conserve configuration et base, mais ne fait ni sauvegarde ni retour arrière.

## Désinstallation

```bash
sudo systemctl disable --now minecraft-server-manager
sudo rm /etc/systemd/system/minecraft-server-manager.service
sudo systemctl daemon-reload
sudo rm -rf /opt/msm
```

Les données (`/var/lib/msm`), la configuration (`/etc/msm`) et les serveurs
Minecraft ne sont **pas** supprimés — à retirer manuellement si souhaité.
