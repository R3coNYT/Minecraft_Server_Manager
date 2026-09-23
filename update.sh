#!/usr/bin/env bash
#
# Updates Minecraft Server Manager without losing anything.
#
#   sudo ./update.sh              fetches the latest version and installs it
#   sudo ./update.sh --rollback   goes back to the version before the last update
#
# What is kept: configuration (/etc/msm/.env), database (accounts, servers,
# history, schedules…), backups, logs, and the Minecraft servers themselves —
# which keep running during the update.
#
# Steps:
#
#   1. fetches the new version (git pull of the repository holding this script);
#   2. sets the installed code aside, to be able to go back to it;
#   3. stops MSM, backs up the database and the configuration;
#   4. installs the new version (install.sh), applies the migrations;
#   5. checks that the panel answers.
#
# If step 4 or 5 fails, the previous version and the pre-update database are
# put back automatically.
#
set -euo pipefail

SERVICE_NAME="minecraft-server-manager"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
#: Database backups kept under <data>/update-backups.
KEEP_BACKUPS=5
#: In the copy of the previous version: path of the backup that goes with it.
ROLLBACK_MARKER=".msm-rollback"

NO_PULL=0
FORCE=0
ROLLBACK=0
ASSUME_YES=0
REF=""

# --------------------------------------------------------------------------- #
#  Output
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
  [[ $# -ge 2 ]] && printf "  Cause: %s\n" "$2" >&2
  [[ $# -ge 3 ]] && printf "  Fix: %s\n" "$3" >&2
  exit 1
}

usage() {
  cat <<EOF
Updates Minecraft Server Manager.

Usage: sudo ./update.sh [options]

Options:
  --ref REF      Install a specific branch or tag (default: the tracked branch)
  --no-pull      Do not query git: install the code as it is in this folder
  --force        Reinstall even if the installed version is already the latest
  --rollback     Go back to the version before the last update
  -y, --yes      Ask no questions
  -h, --help     Show this help
EOF
}

# --------------------------------------------------------------------------- #
#  Reading the existing installation
# --------------------------------------------------------------------------- #
# Everything is read back from the systemd unit and the .env: no option to
# repeat, and no risk of "updating" into other folders than the installed ones.
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
    "MSM is not installed on this machine." \
    "The unit ${UNIT_FILE} was not found." \
    "For a first installation: sudo ./install.sh"

  MSM_USER="$(unit_value User)"
  MSM_GROUP="$(unit_value Group)"
  INSTALL_DIR="$(unit_value WorkingDirectory)"
  ENV_FILE="$(unit_value EnvironmentFile)"
  CONFIG_DIR="$(dirname "$ENV_FILE")"

  [[ -n "$MSM_USER" && -d "$INSTALL_DIR" && -f "$ENV_FILE" ]] || fail \
    "Incomplete installation." \
    "The systemd unit does not point to an existing installation folder and .env file." \
    "Reinstall with: sudo ./install.sh (the configuration and the database are kept)"

  DATA_DIR="$(env_value MSM_DATA_DIR)"
  LOG_DIR="$(env_value MSM_LOG_DIR)"
  DATA_DIR="${DATA_DIR:-/var/lib/msm}"
  LOG_DIR="${LOG_DIR:-/var/log/msm}"
  # MSM_SERVER_ROOTS can list several roots (separated by ":"); install.sh only
  # needs the first one, the others stay in the unit.
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

  # Only an SQLite database is backed up by copying; the others have their own tools.
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

# Same principle as install.sh: the .env is read by the child process, never
# passed as an argument — the secret key appears on no command line.
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
  read -r -p "  $1 [y/N] " answer
  [[ "$answer" =~ ^[yY]$ ]]
}

# Schema revision, to be able to bring the database back to it on a rollback.
# Through the CLI if the installed version knows the command, otherwise straight
# from SQLite: versions older than update.sh have no `db-revision` command.
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
#  Backing up and restoring the database
# --------------------------------------------------------------------------- #
backup_state() {
  BACKUP_DIR="$BACKUP_ROOT/$(date +%Y%m%d-%H%M%S)"
  install -d -o root -g root -m 700 "$BACKUP_ROOT" "$BACKUP_DIR"

  cp -p "$ENV_FILE" "$BACKUP_DIR/.env"

  local revision="none"
  if [[ -n "$DB_FILE" && -f "$DB_FILE" ]]; then
    revision="$(db_revision)"
    # SQLite's backup API rather than a copy: it includes the WAL journal and
    # produces a consistent file in a single piece.
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
    ok "Database backed up: $BACKUP_DIR/msm.db"
  elif [[ -n "$DB_FILE" ]]; then
    warn "SQLite database not found ($DB_FILE): nothing to back up."
  else
    warn "Not an SQLite database: back it up with its own tools (pg_dump…) before going on."
    confirm "Continue without a database backup?" || fail "Update cancelled." \
      "No backup of the database was made." "Back up the database, then run the update again."
  fi

  cat > "$BACKUP_DIR/info" <<EOF
date=$(date -u +%Y-%m-%dT%H:%M:%SZ)
version=$(installed_version "$INSTALL_DIR")
commit=$(installed_commit "$INSTALL_DIR")
db_revision=${revision}
EOF
  ln -sfn "$BACKUP_DIR" "$BACKUP_ROOT/latest"
  ok "Configuration backed up."

  # The oldest ones go: one database per update eventually adds up.
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
  ok "Database restored from $backup"
}

restore_code() {
  [[ -d "$PREVIOUS_DIR" ]] || return 1
  local discarded="${INSTALL_DIR}.discarded"
  rm -rf "$discarded"
  mv "$INSTALL_DIR" "$discarded"
  mv "$PREVIOUS_DIR" "$INSTALL_DIR"
  rm -f "$INSTALL_DIR/$ROLLBACK_MARKER"
  rm -rf "$discarded"
  ok "Previous version put back in $INSTALL_DIR"
}

# Failure during the update: everything goes back to how it was. The service
# has been stopped since the backup, so the restored database loses nothing.
automatic_rollback() {
  printf "\n${RED}${BOLD}The update failed: going back to the previous version.${RESET}\n"
  systemctl stop "$SERVICE_NAME" 2>/dev/null || true
  restore_code || warn "No copy of the previous version: the code could not be restored."
  restore_database "$BACKUP_DIR"
  systemctl daemon-reload
  systemctl start "$SERVICE_NAME" || true

  if wait_healthy; then
    fail "Update cancelled — MSM is running version ${OLD_VERSION} again." \
         "$1" \
         "Check the details above and journalctl -u ${SERVICE_NAME} -n 100, then try again."
  fi
  fail "Update cancelled, but MSM does not answer after the rollback." \
       "$1" \
       "Check: journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
}

# --------------------------------------------------------------------------- #
#  Requested rollback
# --------------------------------------------------------------------------- #
manual_rollback() {
  [[ -d "$PREVIOUS_DIR" ]] || fail \
    "No previous version available." \
    "${PREVIOUS_DIR} does not exist: no update was made with this script, or the rollback already happened." \
    "To install a specific version: sudo ./update.sh --ref <tag or commit>"

  # The copy is only usable if the update that produced it got as far as the
  # backup: otherwise it is identical to the code in place, and the most recent
  # backup would belong to an earlier update.
  local backup
  backup="$(cat "$PREVIOUS_DIR/$ROLLBACK_MARKER" 2>/dev/null || true)"
  [[ -n "$backup" && -d "$backup" ]] || fail \
    "Nothing to undo." \
    "The last update stopped before changing the installation." \
    "MSM is still running its current version; no rollback is needed."
  local target_revision
  target_revision="$(grep -m1 '^db_revision=' "$backup/info" 2>/dev/null | cut -d= -f2 || true)"

  step "Rollback"
  local current_commit previous_commit
  current_commit="$(installed_commit "$INSTALL_DIR")"
  previous_commit="$(installed_commit "$PREVIOUS_DIR")"
  info "Current version:  $(installed_version "$INSTALL_DIR")${current_commit:+ (${current_commit:0:7})}"
  info "Previous version: $(installed_version "$PREVIOUS_DIR")${previous_commit:+ (${previous_commit:0:7})}"
  confirm "Go back to the previous version?" || { info "Nothing was changed."; exit 0; }

  systemctl stop "$SERVICE_NAME"
  ok "MSM stopped (the Minecraft servers keep running)."

  # The schema is brought back by the *current* code, the only one that knows
  # the migrations to undo. Data entered since the update is kept, except for
  # the features that disappear.
  if [[ -n "$target_revision" && "$target_revision" != "none" && "$target_revision" != "unknown" ]]; then
    local current
    current="$(db_revision)"
    if [[ "$current" != "$target_revision" ]]; then
      if run_as_msm "$VENV_PY" -m msm.cli downgrade "$target_revision"; then
        ok "Database schema brought back to $target_revision."
      else
        warn "The schema could not be brought back automatically."
        warn "The pre-update database can be restored ($backup):"
        warn "everything changed since would be lost."
        if confirm "Restore this backup?"; then
          restore_database "$backup"
        else
          systemctl start "$SERVICE_NAME"
          fail "Rollback interrupted." "The database schema could not be brought back." \
               "MSM was restarted in its current version; nothing changed."
        fi
      fi
    fi
  fi

  restore_code
  systemctl daemon-reload
  systemctl start "$SERVICE_NAME"
  wait_healthy || fail "MSM does not answer after the rollback." \
    "The service started but the probe ${HEALTH_URL} does not answer." \
    "Check: journalctl -u ${SERVICE_NAME} -n 100 --no-pager"

  printf "\n${GREEN}${BOLD}Rollback complete: MSM $(installed_version "$INSTALL_DIR").${RESET}\n\n"
}

# --------------------------------------------------------------------------- #
#  Fetching the new version
# --------------------------------------------------------------------------- #
# The repository belongs to whoever cloned it: git runs under that account, with
# its credentials (private repository) — never as root, which would leave behind
# files that account could no longer modify.
as_owner() {
  if [[ "$REPO_OWNER" == "root" ]]; then
    git -C "$SOURCE_DIR" "$@"
  else
    runuser -u "$REPO_OWNER" -- env HOME="$REPO_HOME" git -C "$SOURCE_DIR" "$@"
  fi
}

fetch_update() {
  step "Looking for a new version"

  if [[ "$NO_PULL" -eq 1 ]]; then
    [[ -f "$SOURCE_DIR/systemd/render-unit.sh" ]] || fail \
      "Version too old for an automatic update." \
      "The code in $SOURCE_DIR predates update.sh." \
      "Fetch a recent version of the repository, then run the update again."
    ok "Code taken as is from $SOURCE_DIR (--no-pull)."
    TARGET_COMMIT="$(git -c safe.directory="$SOURCE_DIR" -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
    return
  fi

  if ! git -c safe.directory="$SOURCE_DIR" -C "$SOURCE_DIR" rev-parse --git-dir >/dev/null 2>&1; then
    warn "$SOURCE_DIR is not a git repository: the code there will be installed as is."
    TARGET_COMMIT="unknown"
    return
  fi

  REPO_OWNER="$(stat -c %U "$SOURCE_DIR")"
  REPO_HOME="$(getent passwd "$REPO_OWNER" | cut -d: -f6)"

  as_owner diff --quiet HEAD -- || fail \
    "The repository has local changes." \
    "git would refuse to overwrite them, and installing them would mix two versions." \
    "Set them aside (git stash) or discard them (git checkout -- .), then run the update again."

  as_owner fetch --quiet --tags origin || fail \
    "Cannot reach the remote repository." \
    "git fetch failed as ${REPO_OWNER} (network, or credentials for a private repository)." \
    "Test with: sudo -u ${REPO_OWNER} git -C ${SOURCE_DIR} fetch — then, for GitHub: gh auth login && gh auth setup-git"

  # The target version is only *resolved* here: nothing is checked out before
  # making sure this script can install it.
  if [[ -n "$REF" ]]; then
    TARGET_COMMIT="$(as_owner rev-parse --verify --quiet "origin/${REF}^{commit}" \
      || as_owner rev-parse --verify --quiet "${REF}^{commit}")" || fail \
      "Version \"$REF\" not found." "No branch, tag or commit by that name." \
      "List the versions: git -C $SOURCE_DIR tag ; git -C $SOURCE_DIR branch -r"
  elif as_owner symbolic-ref -q HEAD >/dev/null; then
    TARGET_COMMIT="$(as_owner rev-parse '@{u}' 2>/dev/null)" || fail \
      "The current branch does not track any remote branch." \
      "git does not know where to fetch new versions from." \
      "Name the version you want: sudo ./update.sh --ref main"
  else
    TARGET_COMMIT="$(as_owner rev-parse HEAD)"
  fi

  as_owner cat-file -e "${TARGET_COMMIT}:systemd/render-unit.sh" 2>/dev/null || fail \
    "Version too old for an automatic update." \
    "${TARGET_COMMIT:0:7} predates update.sh, which would not know how to install it." \
    "To go back after an update: sudo ./update.sh --rollback"
  ok "Target version: ${TARGET_COMMIT:0:7}"
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
      "The local branch has diverged from the remote branch." \
      "Local commits prevent a simple fast-forward." \
      "Align the repository: git -C $SOURCE_DIR status, then run the update again."
  fi
  if [[ "$before" != "$TARGET_COMMIT" ]] && as_owner merge-base --is-ancestor "$before" "$TARGET_COMMIT"; then
    info "What's new:"
    as_owner log --oneline --no-decorate "${before}..${TARGET_COMMIT}" | head -n 20 | sed 's/^/    /'
  fi
}

# --------------------------------------------------------------------------- #
#  Update
# --------------------------------------------------------------------------- #
update() {
  OLD_VERSION="$(installed_version "$INSTALL_DIR")"
  local old_commit
  old_commit="$(installed_commit "$INSTALL_DIR")"
  info "Installed version: ${OLD_VERSION}${old_commit:+ (${old_commit:0:7})}"

  fetch_update

  if [[ "$FORCE" -eq 0 && -n "$old_commit" && "$old_commit" == "$TARGET_COMMIT" ]]; then
    printf "\n${GREEN}MSM is already up to date.${RESET} (--force to reinstall anyway)\n\n"
    return 0
  fi

  if [[ "$SOURCE_DIR" != "$INSTALL_DIR" ]]; then
    [[ -f "$SOURCE_DIR/install.sh" ]] || fail "install.sh not found in $SOURCE_DIR." \
      "update.sh must be run from the MSM repository." "cd into the repository, then sudo ./update.sh"
  fi

  # --- Copy of the previous version ------------------------------------------
  # Before any change, including the git pull: for an in-place installation
  # (repository = installation folder), the pull is what modifies the code.
  step "Copying the installed version"
  rm -rf "$PREVIOUS_DIR"
  cp -a "$INSTALL_DIR" "$PREVIOUS_DIR"
  rm -rf "$PREVIOUS_DIR/frontend/node_modules" "$PREVIOUS_DIR/$ROLLBACK_MARKER"
  ok "Version ${OLD_VERSION} kept in $PREVIOUS_DIR"

  apply_fetched

  # --- systemd unit ----------------------------------------------------------
  # Installed before stopping: the unit decides what stopping kills. An older
  # unit with `KillMode=mixed` would take the Minecraft servers down.
  step "systemd service"
  bash "$SOURCE_DIR/systemd/render-unit.sh" "$SOURCE_DIR/systemd/${SERVICE_NAME}.service" \
    "$UNIT_FILE" "$MSM_USER" "$MSM_GROUP" "$INSTALL_DIR" "$CONFIG_DIR" "$DATA_DIR" \
    "$LOG_DIR" "$SERVERS_ROOT"
  systemctl daemon-reload
  ok "Unit up to date."

  # --- Stop and backup -------------------------------------------------------
  step "Stopping MSM"
  systemctl stop "$SERVICE_NAME"
  ok "MSM stopped — the Minecraft servers keep running."

  step "Backup"
  backup_state
  # From here on, the copy of the previous version and this backup go together:
  # that pair is what `--rollback` puts back.
  echo "$BACKUP_DIR" > "$PREVIOUS_DIR/$ROLLBACK_MARKER"

  # --- Installation ----------------------------------------------------------
  step "Installing the new version"
  if ! bash "$SOURCE_DIR/install.sh" --from-update \
      --dir "$INSTALL_DIR" --config "$CONFIG_DIR" --data "$DATA_DIR" --logs "$LOG_DIR" \
      --servers-root "$SERVERS_ROOT" --user "$MSM_USER"; then
    automatic_rollback "Installing the new version failed."
  fi

  step "Checking"
  if ! wait_healthy; then
    automatic_rollback "The panel does not answer on ${HEALTH_URL} after the update."
  fi
  ok "The panel answers."

  local new_commit
  new_commit="$(installed_commit "$INSTALL_DIR")"
  printf "\n${GREEN}${BOLD}Update complete: %s → %s.${RESET}\n\n" \
    "${OLD_VERSION}${old_commit:+ (${old_commit:0:7})}" \
    "$(installed_version "$INSTALL_DIR")${new_commit:+ (${new_commit:0:7})}"
  cat <<EOF
  Pre-update backup                ${BACKUP_DIR}
  Go back to the previous version  sudo ./update.sh --rollback

EOF
}

main() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ref)       REF="${2:-}"; [[ -n "$REF" ]] || fail "--ref expects a value."; shift 2 ;;
      --no-pull)   NO_PULL=1; shift ;;
      --force)     FORCE=1; shift ;;
      --rollback)  ROLLBACK=1; shift ;;
      -y|--yes)    ASSUME_YES=1; shift ;;
      -h|--help)   usage; exit 0 ;;
      *) fail "Unknown option: $1" "The argument is not recognised." "Run ./update.sh --help" ;;
    esac
  done

  [[ $EUID -eq 0 ]] || fail \
    "This script must be run as root." \
    "It stops and restarts the service, and writes to MSM's folders." \
    "Run it again with: sudo ./update.sh"
  command -v systemctl >/dev/null 2>&1 || fail "systemd was not found." \
    "MSM is installed as a systemd service." "Update by hand: see docs/DEPLOY.md"

  load_installation

  if [[ "$ROLLBACK" -eq 1 ]]; then
    manual_rollback
  else
    update
  fi
}

# The whole script is read before it runs: the `git pull` can replace this file
# along the way without bash reading the modified remainder.
main "$@"; exit $?
