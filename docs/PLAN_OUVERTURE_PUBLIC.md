# Plan : ouvrir MSM au public

Rédigé et validé le 24/09/2026 (voir « Décisions » en fin de document). La
phase 7 (agents distants) reste au programme, après ce chantier.

## Objectif

MSM devient une plateforme où chacun peut créer un compte et héberger ses
propres serveurs Minecraft.

- L'interface actuelle devient l'**interface administrateur**. L'admin voit
  tout : tous les serveurs, tout le journal d'audit, tous les comptes, tous les
  réglages.
- Les autres comptes ont le rôle **user**. Ils ne voient que leurs serveurs et
  ceux qu'on leur a partagés, et peuvent en créer.
- Chaque serveur a un **propriétaire**. Il peut le partager avec d'autres
  comptes, en lecture seule ou comme administrateur **de ce serveur**.

| Étape | Contenu | État |
| --- | --- | --- |
| 1 | Modèle d'accès : rôles, propriétaires, membres, filtrage (backend) | à faire |
| 2 | Comptes : inscription, profil, pseudo, avatar, bannissement (backend) | à faire |
| 3 | Création par utilisateur : dossiers, quotas, ports (backend) | à faire |
| 4 | Interface : inscription, profil, dashboard user, membres, panel admin | à faire |
| 5 | Connexion avec Google | à faire |
| 6 | Isolation des serveurs, **préalable à l'ouverture publique** | à faire |
| — | E-mails (vérification, mot de passe oublié) | quand MSM aura un domaine |

Chaque étape est testée, commitée et poussée avant la suivante. L'inscription
reste **fermée** — ni ouverte, ni sur invitation — tant que **toutes** les
étapes ne sont pas terminées (voir « Sécurité »).

---

## Les rôles

### Rôles globaux (sur tout MSM)

| Rôle | Qui | Accès |
| --- | --- | --- |
| **Admin** | l'administrateur de la machine | tout MSM (détail plus bas) |
| **Moderator** | nommé par un admin | modère la plateforme : voit tout, arrête un serveur, bannit des users |
| **User** | tout compte créé par inscription | ses serveurs et ceux qu'on lui partage |

Le rôle **Viewer** disparaît (aucun compte ne l'a). Les modérateurs actuels
restent **Moderator**, avec la définition ci-dessus : ils n'ont plus d'accès
automatique à la console ou aux fichiers des serveurs. Pour les serveurs
actuels, l'admin les ajoute comme admins de ces serveurs.

### Rôles sur un serveur

| Rôle | Obtenu comment |
| --- | --- |
| **Propriétaire** | celui qui a créé le serveur ; un seul par serveur |
| **Admin du serveur** | donné par le propriétaire |
| **Membre** (lecteur) | par défaut quand on partage un serveur |

### Qui peut faire quoi sur un serveur

| Action | Propriétaire | Admin du serveur | Membre | Moderator non membre | Admin MSM non membre |
| --- | :-: | :-: | :-: | :-: | :-: |
| Voir la vue d'ensemble (overview) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Console (lire, envoyer des commandes) | ✅ | ✅ | — | — | — |
| Démarrer, redémarrer | ✅ | ✅ | — | — | — |
| Arrêter, forcer l'arrêt | ✅ | ✅ | — | ✅ | ✅ |
| Joueurs, fichiers, configs, server.properties | ✅ | ✅ | — | — | — |
| Événements, tâches programmées, sauvegardes | ✅ | ✅ | — | — | — |
| Notifications Discord, launcher | ✅ | ✅ | — | — | — |
| Paramètres du serveur (nom, mémoire…) | ✅ | ✅ | — | — | — |
| Gérer les membres | ✅ | — | — | — | — |
| Supprimer le serveur | ✅ | — | — | — | ✅ |
| Option « Start with MSM » | — | — | — | — | ✅ (tous les serveurs) |

L'arrêt par un modérateur ou un admin non membre sert à couper un serveur qui
pose problème sans le supprimer ni bannir son propriétaire. Il est journalisé,
et le propriétaire le voit dans la console et les notifications.

- Un membre ne voit que l'onglet **Overview**. Les autres onglets sont masqués,
  et l'API refuse de toute façon.
- Un admin MSM qui est aussi admin d'un serveur a les droits de la colonne
  « Admin du serveur » sur ce serveur.
- **« Start with MSM » est réservé aux admins MSM**, y compris sur leurs propres
  serveurs. Démarrer un serveur au lancement de MSM engage la machine, c'est
  une décision d'administrateur.

---

## Ce que voit chaque rôle

### Admin MSM

- **Dashboard** : ressources de la machine (CPU, mémoire, disque) et tous les
  serveurs. **Ses serveurs d'abord**, puis ceux des autres, regroupés par
  propriétaire, avec son pseudo affiché.
- **Barre latérale** : même ordre, ses serveurs puis ceux des autres.
- **Audit log** : tout MSM.
- **Users**, qui devient le **panel admin des comptes** :
  - la liste : admins en premier, puis les users, avec une recherche ;
  - la fiche d'un compte : pseudos successifs, e-mail, date d'inscription,
    dernière connexion, méthode de connexion (mot de passe, Google), serveurs
    possédés et partagés avec lui ;
  - les actions : **bannir** avec un motif, **débannir**, changer le rôle.
- **Settings** : langue, webhook global, inscriptions, quotas, plage de ports.

### Moderator

- **Dashboard et barre latérale** : tous les serveurs, comme l'admin, mais sans
  les ressources de la machine.
- **Audit log** : tout MSM.
- **Users** : la liste et les fiches. Il peut bannir et débannir des **users**,
  pas les admins ni les autres modérateurs, et ne change pas les rôles.
- **Settings** : uniquement la langue.
- Ses propres serveurs se gèrent comme ceux d'un user.

### User

- **Dashboard** : uniquement ses serveurs (possédés et partagés), et le bouton
  **Create a server**.
  - Pas de ressources de la machine : les cartes CPU, mémoire et disque sont
    remplacées par ses propres chiffres (serveurs en ligne, joueurs, mémoire
    utilisée par ses serveurs).
  - Pas de bouton « Add an existing server » (voir « Sécurité »).
- **Barre latérale** : ses serveurs. Ceux qu'on lui partage portent la mention
  **Shared**.
- **Pas d'Audit log, pas de Users.**
- **Settings** : uniquement la langue. Elle devient **propre à chaque compte**
  (aujourd'hui, c'est un réglage global de MSM).

### Tout le monde

- En haut à droite, un **clic sur le pseudo** mène au **profil** :
  - changer de pseudo ;
  - changer de mot de passe (ou en définir un pour un compte créé avec Google) ;
  - ajouter ou changer son avatar, affiché à côté du pseudo ;
  - lier ou délier son compte Google (étape 5).

---

## Étape 1 — Modèle d'accès (backend)

### Données

- `users.role` : `ADMIN`, `MODERATOR` ou `USER`.
- `users.storage_id` : identifiant court, aléatoire et **immuable** (10
  caractères `a-z0-9`), attribué à la création du compte. Il nomme le dossier de
  l'utilisateur (étape 3), pour qu'un changement de pseudo ne touche à rien sur
  le disque.
- `servers.owner_id` : le propriétaire, obligatoire.
- Nouvelle table `server_members` : `(server_id, user_id, role)`, avec pour
  `role` : `ADMIN` ou `VIEWER`. Le propriétaire n'y figure pas, il est dans
  `servers.owner_id`.
- L'ancienne table `server_permissions` (surcharges fines par permission) est
  supprimée. Les rôles de serveur la remplacent, plus simples à comprendre et à
  afficher.
- Le nom d'un serveur devient unique **par propriétaire**, et non plus sur tout
  MSM : deux users doivent pouvoir appeler leur serveur « Survie ».

### Calcul des droits

Les droits sur un serveur se déduisent du couple (rôle global, rôle sur le
serveur), selon le tableau ci-dessus. Les permissions atomiques existantes
(`console:write`, `file:upload`…) sont conservées : seule change la façon de
les attribuer. Toutes les routes serveur passent déjà par
`require_server_permission` et n'ont donc pas à changer une par une.

### Filtrage : rien ne doit fuiter

- `GET /servers` et le dashboard : seulement les serveurs visibles. Tri de
  l'admin : les siens, puis les autres.
- Un serveur invisible répond **404**, comme aujourd'hui, pour ne pas révéler
  qu'il existe.
- **WebSocket** : chaque connexion reçoit aujourd'hui tous les événements
  `system.*`, dont les créations et suppressions de serveurs, avec leurs noms.
  Ils seront réservés aux admins. Les abonnements à un serveur vérifient déjà
  les droits.
- Statistiques de la machine (`/system/stats` et leur flux temps réel) : admins
  uniquement.
- Audit, gestion des comptes, réglages globaux : admins uniquement.
- Toute action reste journalisée, y compris celles des users.

### Réglages réservés aux admins MSM

Ces réglages permettent d'exécuter n'importe quoi sur la machine. Ils sont
refusés aux users, même propriétaires :

- l'ajout d'un serveur existant, c'est-à-dire l'enregistrement d'un dossier
  quelconque ;
- le launcher « commande personnalisée », le chemin de Java, le script de
  démarrage, les arguments JVM libres et les variables d'environnement ;
- « Start with MSM ».

Les serveurs d'un user utilisent le lancement standard de leur type (JAR ou
`run.sh` pour NeoForge), avec la mémoire choisie dans les limites des quotas.

### Migration des données existantes

- Les admins restent admins, les modérateurs restent modérateurs (avec la
  nouvelle définition). Il n'y a aucun Viewer à migrer ; s'il en restait un, il
  deviendrait User.
- Chaque serveur existant reçoit pour propriétaire le premier admin (ton
  compte). Les dossiers existants **ne bougent pas**.
- Aucun membre n'est ajouté automatiquement : l'admin partage ensuite ses
  serveurs, depuis l'onglet Members, avec qui il veut.

### Tests

Une matrice de tests couvre chaque couple (rôle global, rôle sur le serveur) et
chaque famille de routes (autorisé ou refusé), plus la non-fuite : listes, 404,
WebSocket, statistiques.

---

## Étape 2 — Comptes (backend)

### Inscription

- Champs : **e-mail**, **pseudo**, **mot de passe** et sa confirmation, case
  d'acceptation des règles de l'instance.
- Pseudo : 3 à 24 caractères parmi `a-z 0-9 _ -`, unique sans tenir compte des
  majuscules. Certains pseudos sont réservés (`admin`, `root`, `msm`,
  `minecraft`…). Le pseudo sert aussi de nom de dossier (étape 3), d'où ces
  règles strictes.
- E-mail unique, stocké en minuscules.
- Mot de passe : les règles actuelles (argon2id, longueur minimale).
- Anti-abus : limite d'inscriptions par IP et par heure. Un CAPTCHA
  (Cloudflare Turnstile ou hCaptcha) est possible en option si un bot
  s'acharne.
- **Mode d'inscription**, réglé par l'admin :
  - **fermée** (par défaut) : seul l'admin crée des comptes, comme aujourd'hui ;
  - **sur invitation** : l'admin génère des liens à usage unique ;
  - **ouverte** : n'importe qui peut s'inscrire.

### Profil

- **Changer de pseudo** : l'ancien est conservé dans `username_history`, que
  l'admin consulte. Un ancien pseudo reste réservé à son titulaire : personne
  d'autre ne peut le prendre pour usurper son identité. Rien ne change sur le
  disque : le dossier porte l'identifiant immuable du compte, pas son pseudo.
- **Changer de mot de passe** : l'ancien est exigé, les autres sessions sont
  fermées.
- **Avatar** : PNG, JPEG ou WebP de 2 Mo au plus. Il est **réencodé** en WebP
  256×256, ce qui retire les métadonnées et neutralise un fichier piégé. Il est
  stocké dans le dossier de données de MSM et servi par l'API.
- **Langue** : propre à chaque compte, avec la langue globale en valeur par
  défaut.

### Bannissement

- Champs : `banned_at`, `banned_by` et `ban_reason`.
- Effets immédiats :
  - les sessions sont fermées ;
  - la connexion est refusée avec le motif ;
  - **les serveurs du compte sont arrêtés proprement** et ne peuvent plus
    démarrer.
- Débannir rend l'accès. Les serveurs ne redémarrent pas d'eux-mêmes.
- On ne peut pas se réinscrire avec le même e-mail ou le même compte Google.
- Un admin ne peut ni se bannir lui-même, ni bannir le dernier admin. Un
  modérateur ne bannit que des users.

### E-mails : plus tard

MSM n'a pas encore de nom de domaine : les e-mails attendront. En attendant, pas
de vérification d'adresse, et un mot de passe oublié se réinitialise par
l'admin. Une fois le domaine et le serveur SMTP en place, on ajoutera :

- la vérification de l'adresse à l'inscription ;
- « mot de passe oublié » ;
- une alerte quand l'e-mail ou le mot de passe change.

---

## Étape 3 — Création par utilisateur (backend)

### Dossiers

- Un serveur créé par un compte va dans
  `{racine des users}/{identifiant du compte}/{serveur}`. La racine est un
  réglage admin, qui vaut par défaut la première racine autorisée. Avec `/data`
  comme racine : `/data/k7x2m9qd4a/survie`.
- La fiche d'un compte, dans le panel admin, affiche son identifiant et le
  chemin de son dossier, pour s'y retrouver sur la machine.
- Le dossier de l'user est créé à sa première création de serveur.
- Un user ne choisit plus le dossier : il ne donne que le nom du serveur. Le
  choix libre reste possible pour un admin.
- Changer de pseudo ne touche pas aux dossiers.
- Les serveurs existants restent où ils sont.

### Quotas (réglages admin, avec une valeur par défaut et une surcharge par compte)

- Nombre maximal de serveurs possédés.
- Mémoire maximale par serveur, et totale sur ses serveurs **en ligne** :
  vérifiée au démarrage, avec un refus clair.
- Espace disque, mesuré périodiquement. Au-delà, plus de nouvelle sauvegarde
  ni de nouvel envoi de fichiers.
- Les admins MSM n'ont pas de quota.

### Ports

- L'admin définit une plage, par exemple 25565 à 25664. Chaque serveur reçoit
  un port libre de cette plage à sa création.
- Le user ne choisit pas son port, et `server-port` dans `server.properties`
  est **réimposé à chaque démarrage**. Sinon, un user pourrait prendre le port
  d'un autre, ou celui d'un service de la machine.
- RCON et query : désactivés par défaut sur les serveurs des users.
- Il faut ouvrir cette plage dans le pare-feu de la machine ; la doc de
  déploiement l'expliquera.

---

## Étape 4 — Interface

- **Pages publiques** :
  - **Sign up**, liée depuis la connexion, et visible seulement si
    l'inscription est ouverte ou si on arrive avec une invitation ;
  - le **choix du pseudo** après Google (étape 5).
- **Profil** (clic sur le pseudo) : pseudo, mot de passe, avatar, langue, et le
  compte Google lié.
- **Navigation selon le rôle** : pour un user, Audit log et Users sont masqués,
  et Settings ne garde que la langue.
- **Dashboard** : la version user décrite plus haut ; pour l'admin, le tri
  « les miens puis les autres » avec le pseudo du propriétaire.
- **Onglet serveur « Members »** (propriétaire uniquement) :
  - ajouter un compte par son pseudo, avec une recherche par autocomplétion ;
  - choisir son rôle : membre ou admin du serveur ;
  - retirer un membre.
- **Mention « Shared »** sur les serveurs partagés, et **pseudo du
  propriétaire** sur les serveurs des autres pour l'admin.
- **« Start with MSM »** : visible des admins MSM uniquement.
- **Panel admin des comptes** : liste, fiche, bannir et débannir, pseudos
  successifs, serveurs possédés et partagés.
- **Réglages admin** : mode d'inscription et invitations, quotas, plage de
  ports, racine des dossiers users.
- Toutes les nouvelles chaînes en anglais et en français.

---

## Étape 5 — Connexion avec Google

- Protocole OpenID Connect, avec la bibliothèque `authlib`.
- À configurer par l'admin :
  - un identifiant client Google (Google Cloud Console) ;
  - `MSM_GOOGLE_CLIENT_ID` et `MSM_GOOGLE_CLIENT_SECRET` ;
  - une adresse publique **en HTTPS**, obligatoire pour Google.
  Sans configuration, le bouton n'apparaît pas. Comme pour les e-mails, il
  faudra un nom de domaine. Le développement et les tests se font sur
  `http://localhost`, que Google accepte.
- Première connexion : Google donne l'e-mail et l'avatar. MSM demande ensuite
  de **choisir un pseudo** avant de créer le compte avec le rôle User.
- Le compte est lié à l'identifiant Google stable (`sub`), pas à l'e-mail, qui
  peut changer.
- Sur le profil :
  - lier Google à un compte existant ;
  - délier Google, à condition d'avoir défini un mot de passe, pour ne pas
    s'enfermer dehors.
- Un compte banni reste banni, même via Google.

---

## Étape 6 — Isolation des serveurs

### Pourquoi c'est indispensable

Un mod ou un plugin est du **code Java exécuté sur la machine**. Aujourd'hui,
tous les serveurs tournent sous le compte système de MSM. Un user qui envoie
un mod piégé pourrait alors :

- lire la base de MSM, sa clé secrète et les sessions (et donc prendre la main
  sur MSM) ;
- lire et modifier les serveurs des autres ;
- consommer toute la mémoire ou tout le processeur de la machine.

Entre amis de confiance, ce risque est acceptable. **Pour des inconnus, il ne
l'est pas.** D'où l'inscription fermée par défaut jusqu'à cette étape.

### Proposition

Chaque serveur est lancé comme une **unité systemd transitoire**
(`systemd-run`) :

- sous un compte système propre à son propriétaire, sans accès à `/opt/msm` ni
  aux dossiers des autres ;
- avec des limites dans le noyau (cgroups) : mémoire (`MemoryMax`), part de
  processeur (`CPUQuota`) et nombre de processus (`TasksMax`) ;
- avec un système de fichiers en lecture seule, hors de son propre dossier
  (`ProtectSystem`, `ReadWritePaths`).

**Retenu : systemd.** MSM obtient le droit de lancer ces unités, et seulement celui-là, par une règle
polkit ou sudoers installée par `install.sh`. Bonus : systemd suit lui-même les
processus, ce qui fiabilise la réadoption après un redémarrage de MSM.

L'alternative Docker (un conteneur par serveur) isole encore mieux, mais ajoute
une dépendance lourde et complique les chemins et les mods. Elle est à écarter
sauf besoin particulier.

---

## Sécurité : récapitulatif

| Risque | Parade | Étape |
| --- | --- | --- |
| Voir les serveurs, l'audit ou la machine des autres | filtrage partout, 404, WebSocket | 1 |
| Exécuter une commande sur la machine via le launcher | commande perso, Java, JVM et env réservés aux admins | 1 |
| Enregistrer un dossier quelconque de la machine | « Add existing server » réservé aux admins | 1 |
| Inscriptions en masse | mode fermé ou invitations, limite par IP, CAPTCHA en option | 2 |
| Avatar piégé | réencodage systématique en WebP | 2 |
| Voler le port d'un autre ou d'un service | plage de ports, port réimposé au démarrage | 3 |
| Saturer la mémoire ou le disque | quotas | 3 et 6 |
| Mod malveillant | isolation par serveur | 6 |

---

## Décisions (validées le 24/09/2026)

1. **Admin MSM sur le serveur d'un autre** : vue d'ensemble, **arrêt et arrêt
   forcé**, suppression, « Start with MSM ».
2. **Membres** : seul le propriétaire les gère. On pourra l'ouvrir aux admins
   du serveur plus tard si le besoin se présente.
3. **Dossiers** : nommés d'après un **identifiant immuable** du compte, pas son
   pseudo. Changer de pseudo ne touche pas au disque.
4. **Moderator** : le rôle est conservé, redéfini en modérateur de la
   plateforme (voir « Les rôles »). Les modérateurs actuels le gardent ; un
   seul admin, aucun Viewer.
5. **E-mails** : plus tard, quand MSM aura un nom de domaine et un serveur SMTP.
6. **Isolation** : systemd. L'inscription (ouverte ou sur invitation) n'est
   activée qu'une fois **toutes** les étapes terminées.
