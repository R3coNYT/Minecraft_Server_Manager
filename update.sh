#!/usr/bin/env bash
#
# Mise à jour de Minecraft Server Manager, sans rien perdre.
#
#   sudo ./update.sh              récupère la dernière version et l'installe
#   sudo ./update.sh --rollback   revient à la version d'avant la dernière mise à jour
#
# Ce qui est conservé : configuration (/etc/msm/.env), base de données (comptes,
# serveurs, historique, planifications…), sauvegardes, journaux, et les serveurs
# Minecraft eux-mêmes — qui continuent de tourner pendant la mise à jour.
#
# Déroulement :
#
#   1. récupère la nouvelle version (git pull du dépôt où se trouve ce script) ;
#   2. met de côté le code installé, pour pouvoir y revenir ;
#   3. arrête MSM, sauvegarde la base et la configuration ;
#   4. installe la nouvelle version (install.sh), applique les migrations ;
#   5. vérifie que le panneau répond.
#
# Si l'étape 4 ou 5 échoue, l'ancienne version et la base d'avant la mise à jour
# sont remises en place automatiquement.
#
set -euo pipefail

SERVICE_NAME="minecraft-server-manager"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#: Sauvegardes de base gardées sous <données>/update-backups.
KEEP_BACKUPS=5
#: Dans la copie de l'ancienne version : chemin de la sauvegarde qui lui correspond.
ROLLBACK_MARKER=".msm-rollback"

NO_PULL=0
FORCE=0
ROLLBACK=0
ASSUME_YES=0
REF=""

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

fail() {
  printf "\n${RED}✗ %s${RESET}\n" "$1" >&2
  [[ $# -ge 2 ]] && printf "  Cause : %s\n" "$2" >&2
  [[ $# -ge 3 ]] && printf "  Action : %s\n" "$3" >&2
  exit 1
}

usage() {
  cat <<EOF
Mise à jour de Minecraft Server Manager.

Usage : sudo ./update.sh [options]

Options :
  --ref REF      Installer une branche ou un tag précis (défaut : la branche suivie)
  --no-pull      Ne pas interroger git : installer le code tel qu'il est dans ce dossier
  --force        Réinstaller même si la version installée est déjà la dernière
  --rollback     Revenir à la version d'avant la dernière mise à jour
  -y, --yes      Ne poser aucune question
  -h, --help     Afficher cette aide
EOF
}

# --------------------------------------------------------------------------- #
#  Lecture de l'installation existante
# --------------------------------------------------------------------------- #
# Tout est relu depuis l'unité systemd et le .env : aucune option à répéter, et
# aucun risque de « mettre à jour » vers d'autres dossiers que ceux installés.
unit_value() {
  grep -m1 "^$1=" "$UNIT_FILE" | cut -d= -f2- | sed 's/^-//'
}

env_value() {
  local value
  value="$(grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- || true)"
  value="${value%\"}"; value="${value#\"}"; value="${value%\'}"; value="${value#\'}"
  printf '%s' "$value"
}

load_installation() {
  [[ -f "$UNIT_FILE" ]] || fail \
    "MSM n'est pas installé sur cette machine." \
    "L'unité ${UNIT_FILE} est introuvable." \
    "Pour une première installation : sudo ./install.sh"

  MSM_USER="$(unit_value User)"
  MSM_GROUP="$(unit_value Group)"
  INSTALL_DIR="$(unit_value WorkingDirectory)"
  ENV_FILE="$(unit_value EnvironmentFile)"
  CONFIG_DIR="$(dirname "$ENV_FILE")"

  [[ -n "$MSM_USER" && -d "$INSTALL_DIR" && -f "$ENV_FILE" ]] || fail \
    "Installation incomplète." \
    "L'unité systemd ne désigne pas un dossier d'installation et un .env existants." \
    "Réinstaller avec : sudo ./install.sh (la configuration et la base sont conservées)"

  DATA_DIR="$(env_value MSM_DATA_DIR)"
  LOG_DIR="$(env_value MSM_LOG_DIR)"
  DATA_DIR="${DATA_DIR:-/var/lib/msm}"
  LOG_DIR="${LOG_DIR:-/var/log/msm}"
  # MSM_SERVER_ROOTS peut en lister plusieurs (séparées par « : ») ; install.sh
  # n'a besoin que de la première, les autres restent dans l'unité.
  local roots
  roots="$(env_value MSM_SERVER_ROOTS)"
  SERVERS_ROOT="${roots%%:*}"
  SERVERS_ROOT="${SERVERS_ROOT:-/data/minecraft}"

  local host port
  host="$(env_value MSM_HOST)"; port="$(env_value MSM_PORT)"
  case "${host:-127.0.0.1}" in
    0.0.0.0|::|"[::]") host="127.0.0.1" ;;
  esac
  HEALTH_URL="http://${host:-127.0.0.1}:${port:-8000}/api/v1/health"

  # Seule une base SQLite se sauvegarde par copie ; les autres ont leurs outils.
  local database_url
  database_url="$(env_value MSM_DATABASE_URL)"
  DB_FILE=""
  if [[ "$database_url" == sqlite* ]]; then
    DB_FILE="${database_url#*:///}"
  fi

  VENV_PY="$INSTALL_DIR/backend/.venv/bin/python"
  PREVIOUS_DIR="${INSTALL_DIR}.previous"
  BACKUP_ROOT="$DATA_DIR/update-backups"
}

installed_version() {
  grep -m1 '^__version__' "$1/backend/msm/__init__.py" 2>/dev/null | cut -d'"' -f2 || echo "?"
}

installed_commit() {
  grep -m1 '^commit=' "$1/.msm-build" 2>/dev/null | cut -d= -f2 || true
}

# Même principe que install.sh : le .env est lu par le processus fils, jamais
# passé en argument — la clé secrète n'apparaît dans aucune ligne de commande.
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

confirm() {
  [[ "$ASSUME_YES" -eq 1 ]] && return 0
  local answer
  read -r -p "  $1 [o/N] " answer
  [[ "$answer" =~ ^[oOyY]$ ]]
}

# Révision du schéma, pour pouvoir y ramener la base lors d'un retour arrière.
# Par la CLI si la version installée la connaît, sinon directement dans SQLite :
# les versions antérieures à update.sh n'ont pas la commande `db-revision`.
db_revision() {
  local revision
  revision="$(run_as_msm "$VENV_PY" -m msm.cli db-revision 2>/dev/null | tail -n1 || true)"
  if [[ -z "$revision" && -n "$DB_FILE" && -f "$DB_FILE" ]]; then
    revision="$("$VENV_PY" -c '
import sqlite3, sys
connection = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
try:
    row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    print(row[0] if row else "none")
except sqlite3.OperationalError:
    print("none")
' "$DB_FILE" 2>/dev/null || true)"
  fi
  printf '%s' "${revision:-unknown}"
}

wait_healthy() {
  local attempt
  for attempt in $(seq 1 30); do
    if "$VENV_PY" - "$HEALTH_URL" >/dev/null 2>&1 <<'PY'
import json, sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
    sys.exit(0 if json.load(response).get("status") == "ok" else 1)
PY
    then
      return 0
    fi
    sleep 1
  done
  return 1
}

# --------------------------------------------------------------------------- #
#  Sauvegarde et restauration de la base
# --------------------------------------------------------------------------- #
backup_state() {
  BACKUP_DIR="$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)"
  install -d -o root -g root -m 700 "$BACKUP_ROOT" "$BACKUP_DIR"

  cp -p "$ENV_FILE" "$BACKUP_DIR/.env"

  local revision="none"
  if [[ -n "$DB_FILE" && -f "$DB_FILE" ]]; then
    revision="$(db_revision)"
    # API de sauvegarde de SQLite plutôt qu'une copie : elle intègre le journal
    # WAL et produit un fichier cohérent en un seul morceau.
    "$VENV_PY" - "$DB_FILE" "$BACKUP_DIR/msm.db" <<'PY'
import sqlite3, sys
source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
with target:
    source.backup(target)
target.close()
source.close()
PY
    chmod 600 "$BACKUP_DIR/msm.db"
    ok "Base sauvegardée : $BACKUP_DIR/msm.db"
  elif [[ -n "$DB_FILE" ]]; then
    warn "Base SQLite introuvable ($DB_FILE) : rien à sauvegarder."
  else
    warn "Base non SQLite : la sauvegarder avec ses propres outils (pg_dump…) avant de continuer."
    confirm "Continuer sans sauvegarde de la base ?" || fail "Mise à jour annulée." \
      "Aucune sauvegarde de la base n'a été faite." "Sauvegarder la base, puis relancer."
  fi

  cat > "$BACKUP_DIR/info" <<EOF
date=$(date -u +%Y-%m-%dT%H:%M:%SZ)
version=$(installed_version "$INSTALL_DIR")
commit=$(installed_commit "$INSTALL_DIR")
db_revision=${revision}
EOF
  ln -sfn "$BACKUP_DIR" "$BACKUP_ROOT/latest"
  ok "Configuration sauvegardée."

  # Les plus anciennes partent : une base par mise à jour, cela finit par peser.
  local old
  for old in $(ls -1d "$BACKUP_ROOT"/2* 2>/dev/null | sort -r | tail -n +$((KEEP_BACKUPS + 1))); do
    rm -rf "$old"
  done
}

restore_database() {
  local backup="$1"
  [[ -n "$DB_FILE" && -f "$backup/msm.db" ]] || return 0
  rm -f "$DB_FILE-wal" "$DB_FILE-shm"
  cp "$backup/msm.db" "$DB_FILE"
  chown "$MSM_USER:$MSM_GROUP" "$DB_FILE"
  ok "Base restaurée depuis $backup"
}

restore_code() {
  [[ -d "$PREVIOUS_DIR" ]] || return 1
  local discarded="${INSTALL_DIR}.discarded"
  rm -rf "$discarded"
  mv "$INSTALL_DIR" "$discarded"
  mv "$PREVIOUS_DIR" "$INSTALL_DIR"
  rm -f "$INSTALL_DIR/$ROLLBACK_MARKER"
  rm -rf "$discarded"
  ok "Ancienne version remise en place dans $INSTALL_DIR"
}

# Échec pendant la mise à jour : tout revient à l'état d'avant. Le service était
# arrêté depuis la sauvegarde, la base restaurée ne perd donc rien.
automatic_rollback() {
  printf "\n${RED}${BOLD}La mise à jour a échoué : retour à la version précédente.${RESET}\n"
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  restore_code || warn "Aucune copie de l'ancienne version : le code n'a pas pu être restauré."
  restore_database "$BACKUP_DIR"
  systemctl daemon-reload
  systemctl start "$SERVICE_NAME" || true

  if wait_healthy; then
    fail "Mise à jour annulée — MSM tourne de nouveau en version ${OLD_VERSION}." \
         "$1" \
         "Consulter le détail ci-dessus et journalctl -u ${SERVICE_NAME} -n 100, puis réessayer."
  fi
  fail "Mise à jour annulée, mais MSM ne répond pas après le retour arrière." \
       "$1" \
       "Consulter : journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
}

# --------------------------------------------------------------------------- #
#  Retour arrière demandé
# --------------------------------------------------------------------------- #
manual_rollback() {
  [[ -d "$PREVIOUS_DIR" ]] || fail \
    "Aucune version précédente disponible." \
    "${PREVIOUS_DIR} n'existe pas : aucune mise à jour n'a été faite avec ce script, ou le retour arrière a déjà eu lieu." \
    "Pour installer une version précise : sudo ./update.sh --ref <tag ou commit>"

  # La copie ne sert que si la mise à jour qui l'a produite est allée jusqu'à la
  # sauvegarde : sinon elle est identique au code en place, et la sauvegarde la
  # plus récente appartiendrait à une mise à jour antérieure.
  local backup
  backup="$(cat "$PREVIOUS_DIR/$ROLLBACK_MARKER" 2>/dev/null || true)"
  [[ -n "$backup" && -d "$backup" ]] || fail     "Rien à défaire."     "La dernière mise à jour s'est arrêtée avant de modifier l'installation."     "MSM tourne toujours dans sa version actuelle ; aucun retour arrière n'est nécessaire."
  local target_revision
  target_revision="$(grep -m1 '^db_revision=' "$backup/info" 2>/dev/null | cut -d= -f2 || true)"

  step "Retour arrière"
  local current_commit previous_commit
  current_commit="$(installed_commit "$INSTALL_DIR")"
  previous_commit="$(installed_commit "$PREVIOUS_DIR")"
  info "Version actuelle   : $(installed_version "$INSTALL_DIR")${current_commit:+ (${current_commit:0:7})}"
  info "Version précédente : $(installed_version "$PREVIOUS_DIR")${previous_commit:+ (${previous_commit:0:7})}"
  confirm "Revenir à la version précédente ?" || { info "Rien n'a été modifié."; exit 0; }

  systemctl stop "$SERVICE_NAME"
  ok "MSM arrêté (les serveurs Minecraft continuent de tourner)."

  # Le schéma est ramené par le code *actuel*, le seul à connaître les
  # migrations à défaire. Les données saisies depuis la mise à jour sont
  # conservées, hormis celles des fonctionnalités qui disparaissent.
  if [[ -n "$target_revision" && "$target_revision" != "none" && "$target_revision" != "unknown" ]]; then
    local current
    current="$(db_revision)"
    if [[ "$current" != "$target_revision" ]]; then
      if run_as_msm "$VENV_PY" -m msm.cli downgrade "$target_revision"; then
        ok "Schéma de base ramené à $target_revision."
      else
        warn "Le schéma n'a pas pu être ramené automatiquement."
        warn "La base d'avant la mise à jour peut être restaurée ($backup) :"
        warn "tout ce qui a été modifié depuis serait perdu."
        if confirm "Restaurer cette sauvegarde ?"; then
          restore_database "$backup"
        else
          systemctl start "$SERVICE_NAME"
          fail "Retour arrière interrompu." "Le schéma de base n'a pas pu être ramené." \
               "MSM a été relancé dans sa version actuelle ; rien n'a changé."
        fi
      fi
    fi
  fi

  restore_code
  systemctl daemon-reload
  systemctl start "$SERVICE_NAME"
  wait_healthy || fail "MSM ne répond pas après le retour arrière." \
    "Le service a démarré mais la sonde ${HEALTH_URL} ne répond pas." \
    "Consulter : journalctl -u ${SERVICE_NAME} -n 100 --no-pager"

  printf "\n${GREEN}${BOLD}Retour arrière terminé : MSM $(installed_version "$INSTALL_DIR").${RESET}\n\n"
}

# --------------------------------------------------------------------------- #
#  Récupération de la nouvelle version
# --------------------------------------------------------------------------- #
# Le dépôt appartient à celui qui l'a cloné : git s'exécute sous ce compte, avec
# ses identifiants (dépôt privé) — jamais en root, qui laisserait derrière lui
# des fichiers que ce compte ne pourrait plus modifier.
as_owner() {
  if [[ "$REPO_OWNER" == "root" ]]; then
    git -C "$SOURCE_DIR" "$@"
  else
    runuser -u "$REPO_OWNER" -- env HOME="$REPO_HOME" git -C "$SOURCE_DIR" "$@"
  fi
}

fetch_update() {
  step "Recherche d'une nouvelle version"

  if [[ "$NO_PULL" -eq 1 ]]; then
    [[ -f "$SOURCE_DIR/systemd/render-unit.sh" ]] || fail \
      "Version trop ancienne pour une mise à jour automatique." \
      "Le code de $SOURCE_DIR est antérieur à update.sh." \
      "Récupérer une version récente du dépôt, puis relancer."
    ok "Code pris tel quel dans $SOURCE_DIR (--no-pull)."
    TARGET_COMMIT="$(git -c safe.directory="$SOURCE_DIR" -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
    return
  fi

  if ! git -c safe.directory="$SOURCE_DIR" -C "$SOURCE_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    warn "$SOURCE_DIR n'est pas un dépôt git : le code présent sera installé tel quel."
    TARGET_COMMIT="unknown"
    return
  fi

  REPO_OWNER="$(stat -c %U "$SOURCE_DIR")"
  REPO_HOME="$(getent passwd "$REPO_OWNER" | cut -d: -f6)"

  as_owner diff --quiet HEAD -- || fail \
    "Le dépôt contient des modifications locales." \
    "git refuserait de les écraser, et les installer mélangerait deux versions." \
    "Les mettre de côté (git stash) ou les annuler (git checkout -- .), puis relancer."

  as_owner fetch --quiet --tags origin || fail \
    "Impossible de contacter le dépôt distant." \
    "git fetch a échoué sous le compte ${REPO_OWNER} (réseau, ou identifiants d'un dépôt privé)." \
    "Tester : sudo -u ${REPO_OWNER} git -C ${SOURCE_DIR} fetch — puis, pour GitHub : gh auth login && gh auth setup-git"

  # La version visée est seulement *résolue* ici : rien n'est extrait avant
  # d'avoir vérifié qu'elle peut être installée par ce script.
  if [[ -n "$REF" ]]; then
    TARGET_COMMIT="$(as_owner rev-parse --verify --quiet "origin/${REF}^{commit}" \
      || as_owner rev-parse --verify --quiet "${REF}^{commit}")" || fail \
      "Version « $REF » introuvable." "Ni branche, ni tag, ni commit de ce nom." \
      "Lister les versions : git -C $SOURCE_DIR tag ; git -C $SOURCE_DIR branch -r"
  elif as_owner symbolic-ref -q HEAD >/dev/null; then
    TARGET_COMMIT="$(as_owner rev-parse '@{u}' 2>/dev/null)" || fail \
      "La branche courante ne suit aucune branche distante." \
      "git ne sait pas d'où récupérer les nouveautés." \
      "Préciser la version voulue : sudo ./update.sh --ref main"
  else
    TARGET_COMMIT="$(as_owner rev-parse HEAD)"
  fi

  as_owner cat-file -e "${TARGET_COMMIT}:systemd/render-unit.sh" 2>/dev/null || fail \
    "Version trop ancienne pour une mise à jour automatique." \
    "${TARGET_COMMIT:0:7} est antérieure à update.sh, qui ne saurait pas l'installer." \
    "Pour revenir en arrière après une mise à jour : sudo ./update.sh --rollback"
  ok "Version cible : ${TARGET_COMMIT:0:7}"
}

apply_fetched() {
  [[ "$NO_PULL" -eq 0 && -n "${REPO_OWNER:-}" ]] || return 0
  local before
  before="$(as_owner rev-parse HEAD)"
  if [[ -n "$REF" ]]; then
    as_owner checkout --quiet "$REF"
  fi
  if as_owner symbolic-ref -q HEAD >/dev/null; then
    as_owner merge --ff-only --quiet '@{u}' || fail \
      "La branche locale a divergé de la branche distante." \
      "Des commits locaux empêchent une simple avance rapide." \
      "Aligner le dépôt : git -C $SOURCE_DIR status, puis relancer."
  fi
  if [[ "$before" != "$TARGET_COMMIT" ]] && as_owner merge-base --is-ancestor "$before" "$TARGET_COMMIT"; then
    info "Nouveautés :"
    as_owner log --oneline --no-decorate "${before}..${TARGET_COMMIT}" | head -n 20 | sed 's/^/    /'
  fi
}

# --------------------------------------------------------------------------- #
#  Mise à jour
# --------------------------------------------------------------------------- #
update() {
  OLD_VERSION="$(installed_version "$INSTALL_DIR")"
  local old_commit
  old_commit="$(installed_commit "$INSTALL_DIR")"
  info "Version installée : ${OLD_VERSION}${old_commit:+ (${old_commit:0:7})}"

  fetch_update

  if [[ "$FORCE" -eq 0 && -n "$old_commit" && "$old_commit" == "$TARGET_COMMIT" ]]; then
    printf "\n${GREEN}MSM est déjà à jour.${RESET} (--force pour réinstaller quand même)\n\n"
    return 0
  fi

  if [[ "$SOURCE_DIR" != "$INSTALL_DIR" ]]; then
    [[ -f "$SOURCE_DIR/install.sh" ]] || fail "install.sh introuvable dans $SOURCE_DIR." \
      "update.sh doit être lancé depuis le dépôt MSM." "cd vers le dépôt, puis sudo ./update.sh"
  fi

  # --- Copie de l'ancienne version -------------------------------------------
  # Avant tout changement, y compris le git pull : pour une installation en place
  # (dépôt = dossier d'installation), c'est lui qui modifie le code.
  step "Copie de la version installée"
  rm -rf "$PREVIOUS_DIR"
  cp -a "$INSTALL_DIR" "$PREVIOUS_DIR"
  rm -rf "$PREVIOUS_DIR/frontend/node_modules" "$PREVIOUS_DIR/$ROLLBACK_MARKER"
  ok "Version ${OLD_VERSION} conservée dans $PREVIOUS_DIR"

  apply_fetched

  # --- Unité systemd ---------------------------------------------------------
  # Installée avant l'arrêt : c'est elle qui décide de ce que l'arrêt tue. Une
  # ancienne unité en `KillMode=mixed` couperait les serveurs Minecraft.
  step "Service systemd"
  bash "$SOURCE_DIR/systemd/render-unit.sh" "$SOURCE_DIR/systemd/${SERVICE_NAME}.service" \
    "$UNIT_FILE" "$MSM_USER" "$MSM_GROUP" "$INSTALL_DIR" "$CONFIG_DIR" "$DATA_DIR" \
    "$LOG_DIR" "$SERVERS_ROOT"
  systemctl daemon-reload
  ok "Unité à jour."

  # --- Arrêt et sauvegarde ---------------------------------------------------
  step "Arrêt de MSM"
  systemctl stop "$SERVICE_NAME"
  ok "MSM arrêté — les serveurs Minecraft continuent de tourner."

  step "Sauvegarde"
  backup_state
  # À partir d'ici, la copie de l'ancienne version et cette sauvegarde vont
  # ensemble : c'est ce couple que `--rollback` remettra en place.
  echo "$BACKUP_DIR" > "$PREVIOUS_DIR/$ROLLBACK_MARKER"

  # --- Installation ----------------------------------------------------------
  step "Installation de la nouvelle version"
  if ! bash "$SOURCE_DIR/install.sh" --from-update \
      --dir "$INSTALL_DIR" --config "$CONFIG_DIR" --data "$DATA_DIR" --logs "$LOG_DIR" \
      --servers-root "$SERVERS_ROOT" --user "$MSM_USER"; then
    automatic_rollback "L'installation de la nouvelle version a échoué."
  fi

  step "Vérification"
  if ! wait_healthy; then
    automatic_rollback "Le panneau ne répond pas sur ${HEALTH_URL} après la mise à jour."
  fi
  ok "Le panneau répond."

  local new_commit
  new_commit="$(installed_commit "$INSTALL_DIR")"
  printf "\n${GREEN}${BOLD}Mise à jour terminée : %s → %s.${RESET}\n\n" \
    "${OLD_VERSION}${old_commit:+ (${old_commit:0:7})}" \
    "$(installed_version "$INSTALL_DIR")${new_commit:+ (${new_commit:0:7})}"
  cat <<EOF
  Sauvegarde d'avant mise à jour   ${BACKUP_DIR}
  Revenir à la version précédente  sudo ./update.sh --rollback

EOF
}

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ref)       REF="${2:-}"; [[ -n "$REF" ]] || fail "--ref attend une valeur."; shift 2 ;;
      --no-pull)   NO_PULL=1; shift ;;
      --force)     FORCE=1; shift ;;
      --rollback)  ROLLBACK=1; shift ;;
      -y|--yes)    ASSUME_YES=1; shift ;;
      -h|--help)   usage; exit 0 ;;
      *) fail "Option inconnue : $1" "L'argument n'est pas reconnu." "Lancer ./update.sh --help" ;;
    esac
  done

  [[ $EUID -eq 0 ]] || fail \
    "Ce script doit être exécuté en tant que root." \
    "Il arrête et redémarre le service, et écrit dans les dossiers de MSM." \
    "Relancer avec : sudo ./update.sh"
  command -v systemctl >/dev/null 2>&1 || fail "systemd est introuvable." \
    "MSM est installé comme service systemd." "Mettre à jour à la main : voir docs/DEPLOY.md"

  load_installation

  if [[ "$ROLLBACK" -eq 1 ]]; then
    manual_rollback
  else
    update
  fi
}

# Tout le script est lu avant de s'exécuter : le `git pull` peut remplacer ce
# fichier en cours de route sans que bash n'en lise la suite modifiée.
main "$@"; exit $?
