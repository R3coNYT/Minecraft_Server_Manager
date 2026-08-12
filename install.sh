#!/usr/bin/env bash
#
# Installation de Minecraft Server Manager sur un serveur Linux.
#
# Le script est **idempotent** : le relancer met à jour une installation
# existante sans écraser la configuration, la base de données ni les serveurs.
#
#   sudo ./install.sh
#   sudo ./install.sh --dir /srv/msm --servers-root /data/minecraft
#
set -euo pipefail

# --------------------------------------------------------------------------- #
#  Valeurs par défaut
# --------------------------------------------------------------------------- #
MSM_USER="msm"
MSM_GROUP="msm"
INSTALL_DIR="/opt/msm"
CONFIG_DIR="/etc/msm"
DATA_DIR="/var/lib/msm"
LOG_DIR="/var/log/msm"
SERVERS_ROOT="/data/minecraft"
SERVICE_NAME="minecraft-server-manager"
BIND_HOST="127.0.0.1"
BIND_PORT="8000"
SKIP_FRONTEND=0
SKIP_ADMIN=0

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# --------------------------------------------------------------------------- #
#  Affichage
# --------------------------------------------------------------------------- #
if [[ -t 1 ]]; then
  BOLD="\033[1m"; GREEN="\033[32m"; YELLOW="\033[33m"; RED="\033[31m"; RESET="\033[0m"
else
  BOLD=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

step()  { printf "\n${BOLD}▸ %s${RESET}\n" "$*"; }
info()  { printf "  %s\n" "$*"; }
ok()    { printf "  ${GREEN}✓${RESET} %s\n" "$*"; }
warn()  { printf "  ${YELLOW}!${RESET} %s\n" "$*"; }

# Toute erreur fatale explique la cause ET l'action corrective, comme le reste
# du panneau : un installateur qui dit seulement « échec » ne sert à rien.
fail() {
  printf "\n${RED}✗ %s${RESET}\n" "$1" >&2
  [[ $# -ge 2 ]] && printf "  Cause : %s\n" "$2" >&2
  [[ $# -ge 3 ]] && printf "  Action : %s\n" "$3" >&2
  exit 1
}

usage() {
  cat <<EOF
Installation de Minecraft Server Manager.

Options :
  --dir CHEMIN            Dossier d'installation      (défaut : ${INSTALL_DIR})
  --config CHEMIN         Dossier de configuration    (défaut : ${CONFIG_DIR})
  --data CHEMIN           Dossier de données          (défaut : ${DATA_DIR})
  --logs CHEMIN           Dossier de journaux         (défaut : ${LOG_DIR})
  --servers-root CHEMIN   Racine des serveurs         (défaut : ${SERVERS_ROOT})
  --user NOM              Utilisateur système         (défaut : ${MSM_USER})
  --host ADRESSE          Adresse d'écoute            (défaut : ${BIND_HOST})
  --port PORT             Port d'écoute               (défaut : ${BIND_PORT})
  --skip-frontend         Ne pas compiler l'interface
  --skip-admin            Ne pas créer de compte administrateur
  -h, --help              Afficher cette aide
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)          INSTALL_DIR="$2"; shift 2 ;;
    --config)       CONFIG_DIR="$2"; shift 2 ;;
    --data)         DATA_DIR="$2"; shift 2 ;;
    --logs)         LOG_DIR="$2"; shift 2 ;;
    --servers-root) SERVERS_ROOT="$2"; shift 2 ;;
    --user)         MSM_USER="$2"; MSM_GROUP="$2"; shift 2 ;;
    --host)         BIND_HOST="$2"; shift 2 ;;
    --port)         BIND_PORT="$2"; shift 2 ;;
    --skip-frontend) SKIP_FRONTEND=1; shift ;;
    --skip-admin)   SKIP_ADMIN=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    *) fail "Option inconnue : $1" "L'argument n'est pas reconnu." "Lancer ./install.sh --help" ;;
  esac
done

# --------------------------------------------------------------------------- #
#  1. Vérification des prérequis
# --------------------------------------------------------------------------- #
step "Vérification des prérequis"

[[ $EUID -eq 0 ]] || fail \
  "Ce script doit être exécuté en tant que root." \
  "La création d'un utilisateur système et d'une unité systemd exige des droits root." \
  "Relancer avec : sudo ./install.sh"

command -v systemctl >/dev/null 2>&1 || fail \
  "systemd est introuvable." \
  "Ce script installe MSM comme service systemd." \
  "Sur un système sans systemd, lancer MSM manuellement : python -m msm.cli serve"

command -v runuser >/dev/null 2>&1 || fail \
  "La commande runuser est introuvable." \
  "Elle sert à exécuter les étapes d'initialisation sous l'utilisateur ${MSM_USER}." \
  "Installer le paquet util-linux : apt install util-linux"

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]:02d}")' 2>/dev/null || echo 0)"
    if [[ "$version" -ge 311 ]]; then PYTHON_BIN="$candidate"; break; fi
  fi
done

[[ -n "$PYTHON_BIN" ]] || fail \
  "Python 3.11 ou supérieur est introuvable." \
  "MSM utilise des fonctionnalités introduites en 3.11 (tomllib, typage moderne)." \
  "Installer python3 : apt install python3 python3-venv  /  dnf install python3"

ok "Python : $("$PYTHON_BIN" --version)"

"$PYTHON_BIN" -c "import venv" 2>/dev/null || fail \
  "Le module venv est absent." \
  "L'environnement virtuel ne peut pas être créé." \
  "Installer le paquet : apt install python3-venv"

if command -v java >/dev/null 2>&1; then
  ok "Java : $(java -version 2>&1 | head -n1)"
else
  warn "Java est absent — MSM s'installera, mais aucun serveur Minecraft ne pourra démarrer."
  warn "Installer par exemple : apt install openjdk-21-jre-headless"
fi

# --------------------------------------------------------------------------- #
#  2. Utilisateur système dédié
# --------------------------------------------------------------------------- #
step "Utilisateur système"

if id -u "$MSM_USER" >/dev/null 2>&1; then
  ok "L'utilisateur « $MSM_USER » existe déjà."
else
  # Compte système sans shell : MSM ne doit jamais tourner en root, et ce compte
  # ne doit pas pouvoir servir à ouvrir une session.
  useradd --system --create-home --home-dir "/var/lib/$MSM_USER" \
          --shell /usr/sbin/nologin "$MSM_USER"
  ok "Utilisateur système « $MSM_USER » créé (sans shell de connexion)."
fi

# --------------------------------------------------------------------------- #
#  3. Dossiers
# --------------------------------------------------------------------------- #
step "Préparation des dossiers"

install -d -o root -g "$MSM_GROUP" -m 750 "$CONFIG_DIR"
install -d -o "$MSM_USER" -g "$MSM_GROUP" -m 750 "$DATA_DIR" "$LOG_DIR"
# Les archives de sauvegarde vivent sous le dossier de données : c'est le seul
# emplacement déjà autorisé en écriture par le durcissement systemd.
install -d -o "$MSM_USER" -g "$MSM_GROUP" -m 750 "$DATA_DIR/backups"
install -d -o root -g root -m 755 "$INSTALL_DIR"

if [[ ! -d "$SERVERS_ROOT" ]]; then
  install -d -o "$MSM_USER" -g "$MSM_GROUP" -m 755 "$SERVERS_ROOT"
  ok "Racine des serveurs créée : $SERVERS_ROOT"
else
  ok "Racine des serveurs : $SERVERS_ROOT"
fi

# --------------------------------------------------------------------------- #
#  4. Copie du code
# --------------------------------------------------------------------------- #
step "Installation du code"

if [[ "$SOURCE_DIR" != "$INSTALL_DIR" ]]; then
  # Le dossier `data` de développement et les dépendances ne sont jamais copiés :
  # l'installation ne doit pas hériter d'une base de test.
  for item in backend frontend migrations docs README.md; do
    [[ -e "$SOURCE_DIR/$item" ]] || continue
    rm -rf "${INSTALL_DIR:?}/$item"
    cp -r "$SOURCE_DIR/$item" "$INSTALL_DIR/"
  done
  rm -rf "$INSTALL_DIR/backend/.venv" "$INSTALL_DIR/backend/data" \
         "$INSTALL_DIR/frontend/node_modules"
  ok "Code copié dans $INSTALL_DIR"
else
  ok "Installation en place dans $INSTALL_DIR"
fi

# --------------------------------------------------------------------------- #
#  5. Environnement Python
# --------------------------------------------------------------------------- #
step "Environnement Python"

VENV="$INSTALL_DIR/backend/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV"
  ok "Environnement virtuel créé."
else
  ok "Environnement virtuel existant réutilisé."
fi

"$VENV/bin/pip" install --quiet --upgrade pip
info "Installation des dépendances (peut prendre une minute)…"
"$VENV/bin/pip" install --quiet -e "$INSTALL_DIR/backend" \
  || fail "Échec de l'installation des dépendances Python." \
          "pip n'a pas pu installer le paquet msm." \
          "Relancer avec les journaux : $VENV/bin/pip install -e $INSTALL_DIR/backend"
ok "Dépendances Python installées."

# --------------------------------------------------------------------------- #
#  6. Interface web
# --------------------------------------------------------------------------- #
step "Interface web"

if [[ "$SKIP_FRONTEND" -eq 1 ]]; then
  warn "Compilation ignorée (--skip-frontend)."
elif command -v npm >/dev/null 2>&1; then
  info "Compilation de l'interface…"
  (cd "$INSTALL_DIR/frontend" && npm ci --silent && npm run build --silent) \
    || fail "Échec de la compilation de l'interface." \
            "npm n'a pas pu produire le dossier dist." \
            "Relancer manuellement : cd $INSTALL_DIR/frontend && npm ci && npm run build"
  ok "Interface compilée — MSM la servira lui-même."
else
  warn "npm est absent : l'interface ne sera pas compilée."
  warn "L'API restera utilisable. Pour ajouter l'interface plus tard :"
  warn "  apt install nodejs npm && cd $INSTALL_DIR/frontend && npm ci && npm run build"
fi

# --------------------------------------------------------------------------- #
#  7. Configuration
# --------------------------------------------------------------------------- #
step "Configuration"

ENV_FILE="$CONFIG_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  ok "Configuration existante conservée : $ENV_FILE"
else
  SECRET="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(64))')"
  cat > "$ENV_FILE" <<EOF
# Configuration de Minecraft Server Manager.
# Généré par install.sh — modifiable, puis : systemctl restart ${SERVICE_NAME}

MSM_ENVIRONMENT=production
MSM_HOST=${BIND_HOST}
MSM_PORT=${BIND_PORT}

# Change cette clé invalide toutes les sessions et rend illisibles les secrets
# chiffrés en base (mots de passe RCON).
MSM_SECRET_KEY=${SECRET}

# Mettre à true si MSM est servi en HTTPS (directement ou derrière un proxy).
MSM_SESSION_COOKIE_SECURE=false

MSM_DATABASE_URL=sqlite+aiosqlite:///${DATA_DIR}/msm.db
MSM_DATA_DIR=${DATA_DIR}
MSM_LOG_DIR=${LOG_DIR}

# Les dossiers de serveurs doivent se trouver sous cette racine.
MSM_SERVER_ROOTS=${SERVERS_ROOT}

# Sauvegardes : ${DATA_DIR}/backups par défaut. Pour les écrire ailleurs — un
# autre disque protège aussi d'une panne de celui-ci — renseigner MSM_BACKUP_DIR
# ET ajouter ce chemin à ReadWritePaths dans l'unité systemd, sans quoi le
# service n'aura pas le droit d'y écrire.
MSM_BACKUP_RETENTION=10

# L'interface étant servie par MSM, aucune origine tierce n'est nécessaire.
MSM_CORS_ORIGINS=
MSM_LOG_FORMAT=json
EOF
  # La clé secrète ne doit être lisible que par MSM.
  chown root:"$MSM_GROUP" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  ok "Configuration écrite : $ENV_FILE (clé secrète générée)"
fi

# --------------------------------------------------------------------------- #
#  8. Base de données
# --------------------------------------------------------------------------- #
step "Base de données"

# Exécute une commande sous l'utilisateur MSM, avec la configuration chargée.
#
# Le fichier .env est lu **par le processus fils**, jamais recopié en arguments :
# la clé secrète n'apparaît donc dans aucune ligne de commande, et `ps` ne la
# montre à personne. Le terminal est conservé, ce qui permet aux commandes
# interactives de demander un mot de passe sans écho.
run_as_msm() {
  runuser -u "$MSM_USER" -- bash -c '
    set -a
    . "$1"
    set +a
    shift
    cd "$1" || exit 1
    shift
    exec "$@"
  ' _ "$ENV_FILE" "$INSTALL_DIR/backend" "$@"
}

run_as_msm "$VENV/bin/python" -m msm.cli migrate \
  || fail "Échec de l'initialisation de la base de données." \
          "Les migrations Alembic n'ont pas pu être appliquées." \
          "Vérifier les droits sur $DATA_DIR, puis relancer install.sh"
ok "Schéma de base appliqué."

# --------------------------------------------------------------------------- #
#  9. Compte administrateur
# --------------------------------------------------------------------------- #
step "Compte administrateur"

# `count-users` n'écrit que son résultat sur la sortie standard ; sa
# journalisation part sur la sortie d'erreur, écartée ici.
EXISTING_USERS="$(run_as_msm "$VENV/bin/python" -m msm.cli count-users 2>/dev/null | tail -n1)"

# Ceinture et bretelles : une sortie inattendue ne doit jamais faire croire qu'il
# n'y a aucun compte — on redemanderait d'en créer un à chaque mise à jour.
if ! [[ "$EXISTING_USERS" =~ ^[0-9]+$ ]]; then
  warn "Le nombre de comptes n'a pas pu être déterminé."
  warn "Aucun compte ne sera créé ; en ajouter un au besoin avec createadmin."
  EXISTING_USERS=1
fi

if [[ "$SKIP_ADMIN" -eq 1 ]]; then
  warn "Création du compte ignorée (--skip-admin)."
elif [[ "$EXISTING_USERS" -gt 0 ]]; then
  ok "${EXISTING_USERS} compte(s) déjà présent(s) — aucun compte créé."
else
  read -r -p "  Nom du compte administrateur [admin] : " ADMIN_NAME
  ADMIN_NAME="${ADMIN_NAME:-admin}"

  # Le mot de passe est demandé par la commande elle-même, sans écho : il ne
  # transite ni par une variable du script, ni par une ligne de commande.
  if run_as_msm "$VENV/bin/python" -m msm.cli createadmin "$ADMIN_NAME"; then
    ok "Compte administrateur « $ADMIN_NAME » créé."
  else
    warn "Le compte n'a pas pu être créé."
    warn "Réessayer : sudo -u $MSM_USER $VENV/bin/python -m msm.cli createadmin NOM"
  fi
fi

# --------------------------------------------------------------------------- #
#  10. Droits
# --------------------------------------------------------------------------- #
step "Droits d'accès"

chown -R "$MSM_USER":"$MSM_GROUP" "$DATA_DIR" "$LOG_DIR"
chown -R root:root "$INSTALL_DIR"
# MSM lit son code mais n'a aucune raison de pouvoir le modifier.
chmod -R go-w "$INSTALL_DIR"
ok "Le code est en lecture seule pour le service."

# --------------------------------------------------------------------------- #
#  11. Service systemd
# --------------------------------------------------------------------------- #
step "Service systemd"

UNIT_SOURCE="$SOURCE_DIR/systemd/${SERVICE_NAME}.service"
[[ -f "$UNIT_SOURCE" ]] || fail \
  "Modèle d'unité systemd introuvable." \
  "Le fichier ${UNIT_SOURCE} est absent." \
  "Relancer le script depuis la racine du dépôt."

sed -e "s|__MSM_USER__|${MSM_USER}|g" \
    -e "s|__MSM_GROUP__|${MSM_GROUP}|g" \
    -e "s|__MSM_HOME__|${INSTALL_DIR}|g" \
    -e "s|__MSM_CONFIG__|${CONFIG_DIR}|g" \
    -e "s|__MSM_DATA__|${DATA_DIR}|g" \
    -e "s|__MSM_LOGS__|${LOG_DIR}|g" \
    -e "s|__MSM_SERVERS__|${SERVERS_ROOT}|g" \
    "$UNIT_SOURCE" > "/etc/systemd/system/${SERVICE_NAME}.service"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null 2>&1

# L'activation est *vérifiée*, pas supposée : sans elle, MSM s'installe, démarre,
# et ne revient jamais après un redémarrage de la machine — panne qui ne se
# découvrirait qu'à la première coupure de courant.
systemctl is-enabled --quiet "${SERVICE_NAME}" || fail \
  "Le service n'a pas pu être activé au démarrage." \
  "systemctl enable ${SERVICE_NAME} a échoué : MSM ne redémarrerait pas après un reboot." \
  "Consulter : systemctl status ${SERVICE_NAME} ; puis relancer : systemctl enable ${SERVICE_NAME}"

ok "Service installé et activé au démarrage de la machine."

# --------------------------------------------------------------------------- #
#  12. Démarrage
# --------------------------------------------------------------------------- #
step "Démarrage"

systemctl restart "${SERVICE_NAME}"
sleep 3

if systemctl is-active --quiet "${SERVICE_NAME}"; then
  ok "Service démarré."
else
  fail "Le service n'a pas démarré." \
       "systemd signale un échec au lancement." \
       "Consulter les journaux : journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
fi

printf "\n${GREEN}${BOLD}Installation terminée.${RESET}\n\n"
cat <<EOF
  Panneau        http://${BIND_HOST}:${BIND_PORT}
  Configuration  ${CONFIG_DIR}/.env
  Données        ${DATA_DIR}
  Sauvegardes    ${DATA_DIR}/backups
  Journaux       ${LOG_DIR}/msm.log  ·  journalctl -u ${SERVICE_NAME} -f
  Serveurs       ${SERVERS_ROOT}

  État du service    systemctl status ${SERVICE_NAME}
  Redémarrer         systemctl restart ${SERVICE_NAME}
  Créer un compte    sudo -u ${MSM_USER} ${VENV}/bin/python -m msm.cli createadmin NOM

  MSM redémarre automatiquement avec la machine. Pour que vos serveurs
  Minecraft repartent aussi, cocher « Démarrer avec MSM » dans leurs réglages.

EOF

if [[ "$BIND_HOST" == "127.0.0.1" ]]; then
  cat <<EOF
  Le panneau n'écoute que sur la machine locale. Pour y accéder à distance,
  placer un reverse proxy HTTPS devant (recommandé), puis passer
  MSM_SESSION_COOKIE_SECURE=true dans ${CONFIG_DIR}/.env.

  Exposer directement MSM sur Internet sans HTTPS ferait circuler le cookie de
  session en clair.

EOF
fi
