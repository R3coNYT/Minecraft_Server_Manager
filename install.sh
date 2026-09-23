#!/usr/bin/env bash
#
# Installs Minecraft Server Manager on a Linux server.
#
# The script is **idempotent**: running it again updates an existing
# installation without overwriting the configuration, the database or the
# servers. For an update, prefer update.sh: it backs up the database before
# migrating and rolls back on its own if something fails.
#
#   sudo ./install.sh
#   sudo ./install.sh --dir /srv/msm --servers-root /data/minecraft
#
set -euo pipefail

# --------------------------------------------------------------------------- #
#  Defaults
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
FROM_UPDATE=0

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

# Every fatal error explains the cause AND the fix, like the rest of the panel:
# an installer that only says "failed" is of no use.
fail() {
  printf "\n${RED}✗ %s${RESET}\n" "$1" >&2
  [[ $# -ge 2 ]] && printf "  Cause: %s\n" "$2" >&2
  [[ $# -ge 3 ]] && printf "  Fix: %s\n" "$3" >&2
  exit 1
}

usage() {
  cat <<EOF
Installs Minecraft Server Manager.

Options:
  --dir PATH              Installation directory     (default: ${INSTALL_DIR})
  --config PATH           Configuration directory    (default: ${CONFIG_DIR})
  --data PATH             Data directory             (default: ${DATA_DIR})
  --logs PATH             Log directory              (default: ${LOG_DIR})
  --servers-root PATH     Servers root               (default: ${SERVERS_ROOT})
  --user NAME             System user                (default: ${MSM_USER})
  --host ADDRESS          Listening address          (default: ${BIND_HOST})
  --port PORT             Listening port             (default: ${BIND_PORT})
  --skip-frontend         Do not build the web interface
  --skip-admin            Do not create an administrator account
  --from-update           Mode used by update.sh (no questions asked)
  -h, --help              Show this help
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
    # Called by update.sh: no questions, no final summary.
    --from-update)  FROM_UPDATE=1; SKIP_ADMIN=1; shift ;;
    -h|--help)      usage; exit 0 ;;
    *) fail "Unknown option: $1" "The argument is not recognised." "Run ./install.sh --help" ;;
  esac
done

# --------------------------------------------------------------------------- #
#  1. Prerequisites
# --------------------------------------------------------------------------- #
step "Checking prerequisites"

[[ $EUID -eq 0 ]] || fail \
  "This script must be run as root." \
  "Creating a system user and a systemd unit requires root privileges." \
  "Run it again with: sudo ./install.sh"

command -v systemctl >/dev/null 2>&1 || fail \
  "systemd was not found." \
  "This script installs MSM as a systemd service." \
  "On a system without systemd, run MSM by hand: python -m msm.cli serve"

command -v runuser >/dev/null 2>&1 || fail \
  "The runuser command was not found." \
  "It runs the initialisation steps as the ${MSM_USER} user." \
  "Install the util-linux package: apt install util-linux"

PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]:02d}")' 2>/dev/null || echo 0)"
    if [[ "$version" -ge 311 ]]; then PYTHON_BIN="$candidate"; break; fi
  fi
done

[[ -n "$PYTHON_BIN" ]] || fail \
  "Python 3.11 or later was not found." \
  "MSM relies on features introduced in 3.11 (tomllib, modern typing)." \
  "Install python3: apt install python3 python3-venv  /  dnf install python3"

ok "Python: $("$PYTHON_BIN" --version)"

"$PYTHON_BIN" -c "import venv" 2>/dev/null || fail \
  "The venv module is missing." \
  "The virtual environment cannot be created." \
  "Install the package: apt install python3-venv"

if command -v java >/dev/null 2>&1; then
  ok "Java: $(java -version 2>&1 | head -n1)"
else
  warn "Java is missing — MSM will install, but no Minecraft server will be able to start."
  warn "Install it, for example: apt install openjdk-21-jre-headless"
fi

# --------------------------------------------------------------------------- #
#  2. Dedicated system user
# --------------------------------------------------------------------------- #
step "System user"

if id -u "$MSM_USER" >/dev/null 2>&1; then
  ok "User \"$MSM_USER\" already exists."
else
  # System account without a shell: MSM must never run as root, and this
  # account must not be usable to open a session.
  useradd --system --create-home --home-dir "/var/lib/$MSM_USER" \
          --shell /usr/sbin/nologin "$MSM_USER"
  ok "System user \"$MSM_USER\" created (no login shell)."
fi

# --------------------------------------------------------------------------- #
#  3. Directories
# --------------------------------------------------------------------------- #
step "Preparing directories"

install -d -o root -g "$MSM_GROUP" -m 750 "$CONFIG_DIR"
install -d -o "$MSM_USER" -g "$MSM_GROUP" -m 750 "$DATA_DIR" "$LOG_DIR"
# Backup archives live under the data directory: it is the only location the
# systemd hardening already allows writing to.
install -d -o "$MSM_USER" -g "$MSM_GROUP" -m 750 "$DATA_DIR/backups"
install -d -o root -g root -m 755 "$INSTALL_DIR"

if [[ ! -d "$SERVERS_ROOT" ]]; then
  install -d -o "$MSM_USER" -g "$MSM_GROUP" -m 755 "$SERVERS_ROOT"
  ok "Servers root created: $SERVERS_ROOT"
else
  ok "Servers root: $SERVERS_ROOT"
fi

# --------------------------------------------------------------------------- #
#  4. Copying the code
# --------------------------------------------------------------------------- #
step "Installing the code"

if [[ "$SOURCE_DIR" != "$INSTALL_DIR" ]]; then
  # The development `data` folder and the dependencies are never copied: the
  # installation must not inherit a test database.
  for item in backend frontend migrations docs README.md; do
    [[ -e "$SOURCE_DIR/$item" ]] || continue
    rm -rf "${INSTALL_DIR:?}/$item"
    cp -r "$SOURCE_DIR/$item" "$INSTALL_DIR/"
  done
  rm -rf "$INSTALL_DIR/backend/.venv" "$INSTALL_DIR/backend/data" \
         "$INSTALL_DIR/frontend/node_modules"
  ok "Code copied to $INSTALL_DIR"
else
  ok "In-place installation in $INSTALL_DIR"
fi

# Installed version, read back by update.sh to know whether there is anything new.
# `safe.directory`: the repository often belongs to an account other than root,
# and git refuses to read it without this setting.
BUILD_COMMIT="$(git -c safe.directory="$SOURCE_DIR" -C "$SOURCE_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
printf 'commit=%s\ninstalled_at=%s\n' "$BUILD_COMMIT" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  > "$INSTALL_DIR/.msm-build"

# --------------------------------------------------------------------------- #
#  5. Python environment
# --------------------------------------------------------------------------- #
step "Python environment"

VENV="$INSTALL_DIR/backend/.venv"
if [[ ! -x "$VENV/bin/python" ]]; then
  "$PYTHON_BIN" -m venv "$VENV"
  ok "Virtual environment created."
else
  ok "Existing virtual environment reused."
fi

"$VENV/bin/pip" install --quiet --upgrade pip
info "Installing dependencies (this may take a minute)…"
"$VENV/bin/pip" install --quiet -e "$INSTALL_DIR/backend" \
  || fail "Installing the Python dependencies failed." \
          "pip could not install the msm package." \
          "Run it again with its output: $VENV/bin/pip install -e $INSTALL_DIR/backend"
ok "Python dependencies installed."

# --------------------------------------------------------------------------- #
#  6. Web interface
# --------------------------------------------------------------------------- #
step "Web interface"

if [[ "$SKIP_FRONTEND" -eq 1 ]]; then
  warn "Build skipped (--skip-frontend)."
elif command -v npm >/dev/null 2>&1; then
  info "Building the interface…"
  (cd "$INSTALL_DIR/frontend" && npm ci --silent && npm run build --silent) \
    || fail "Building the interface failed." \
            "npm could not produce the dist folder." \
            "Run it by hand: cd $INSTALL_DIR/frontend && npm ci && npm run build"
  ok "Interface built — MSM serves it itself."
else
  warn "npm is missing: the interface will not be built."
  warn "The API stays usable. To add the interface later:"
  warn "  apt install nodejs npm && cd $INSTALL_DIR/frontend && npm ci && npm run build"
fi

# --------------------------------------------------------------------------- #
#  7. Configuration
# --------------------------------------------------------------------------- #
step "Configuration"

ENV_FILE="$CONFIG_DIR/.env"
if [[ -f "$ENV_FILE" ]]; then
  ok "Existing configuration kept: $ENV_FILE"
else
  SECRET="$("$VENV/bin/python" -c 'import secrets; print(secrets.token_urlsafe(64))')"
  cat > "$ENV_FILE" <<EOF
# Minecraft Server Manager configuration.
# Generated by install.sh — edit it, then: systemctl restart ${SERVICE_NAME}

MSM_ENVIRONMENT=production
MSM_HOST=${BIND_HOST}
MSM_PORT=${BIND_PORT}

# Changing this key invalidates every session and makes the secrets encrypted
# in the database (RCON passwords) unreadable.
MSM_SECRET_KEY=${SECRET}

# Set to true when MSM is served over HTTPS (directly or behind a proxy).
MSM_SESSION_COOKIE_SECURE=false

MSM_DATABASE_URL=sqlite+aiosqlite:///${DATA_DIR}/msm.db
MSM_DATA_DIR=${DATA_DIR}
MSM_LOG_DIR=${LOG_DIR}

# Server folders must live under this root.
MSM_SERVER_ROOTS=${SERVERS_ROOT}

# Backups: ${DATA_DIR}/backups by default. To write them elsewhere — another
# disk also protects against this one failing — set MSM_BACKUP_DIR AND add that
# path to ReadWritePaths in the systemd unit, otherwise the service will not be
# allowed to write there.
MSM_BACKUP_RETENTION=10

# The interface is served by MSM itself, so no third-party origin is needed.
MSM_CORS_ORIGINS=
MSM_LOG_FORMAT=json
EOF
  # The secret key must only be readable by MSM.
  chown root:"$MSM_GROUP" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  ok "Configuration written: $ENV_FILE (secret key generated)"
fi

# --------------------------------------------------------------------------- #
#  8. Database
# --------------------------------------------------------------------------- #
step "Database"

# Runs a command as the MSM user, with the configuration loaded.
#
# The .env file is read **by the child process**, never copied into arguments:
# the secret key therefore appears on no command line, and `ps` shows it to
# nobody. The terminal is kept, so interactive commands can ask for a password
# without echo.
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
  || fail "Initialising the database failed." \
          "The Alembic migrations could not be applied." \
          "Check the permissions on $DATA_DIR, then run install.sh again"
ok "Database schema applied."

# --------------------------------------------------------------------------- #
#  9. Administrator account
# --------------------------------------------------------------------------- #
step "Administrator account"

# `count-users` only writes its result to standard output; its logging goes to
# standard error, discarded here.
EXISTING_USERS="$(run_as_msm "$VENV/bin/python" -m msm.cli count-users 2>/dev/null | tail -n1)"

# Belt and braces: unexpected output must never suggest there are no accounts —
# the installer would then ask to create one on every update.
if ! [[ "$EXISTING_USERS" =~ ^[0-9]+$ ]]; then
  warn "The number of accounts could not be determined."
  warn "No account will be created; add one if needed with createadmin."
  EXISTING_USERS=1
fi

if [[ "$SKIP_ADMIN" -eq 1 ]]; then
  warn "Account creation skipped (--skip-admin)."
elif [[ "$EXISTING_USERS" -gt 0 ]]; then
  ok "${EXISTING_USERS} account(s) already present — no account created."
else
  read -r -p "  Administrator account name [admin]: " ADMIN_NAME
  ADMIN_NAME="${ADMIN_NAME:-admin}"

  # The password is asked for by the command itself, without echo: it never
  # goes through a script variable or a command line.
  if run_as_msm "$VENV/bin/python" -m msm.cli createadmin "$ADMIN_NAME"; then
    ok "Administrator account \"$ADMIN_NAME\" created."
  else
    warn "The account could not be created."
    warn "Try again: sudo -u $MSM_USER $VENV/bin/python -m msm.cli createadmin NAME"
  fi
fi

# --------------------------------------------------------------------------- #
#  10. Permissions
# --------------------------------------------------------------------------- #
step "Permissions"

chown -R "$MSM_USER":"$MSM_GROUP" "$DATA_DIR" "$LOG_DIR"
chown -R root:root "$INSTALL_DIR"
# MSM reads its code but has no reason to be able to modify it.
chmod -R go-w "$INSTALL_DIR"
ok "The code is read-only for the service."

# --------------------------------------------------------------------------- #
#  11. systemd service
# --------------------------------------------------------------------------- #
step "systemd service"

UNIT_SOURCE="$SOURCE_DIR/systemd/${SERVICE_NAME}.service"
[[ -f "$UNIT_SOURCE" ]] || fail \
  "systemd unit template not found." \
  "The file ${UNIT_SOURCE} is missing." \
  "Run the script from the root of the repository."

bash "$SOURCE_DIR/systemd/render-unit.sh" "$UNIT_SOURCE" "/etc/systemd/system/${SERVICE_NAME}.service" \
  "$MSM_USER" "$MSM_GROUP" "$INSTALL_DIR" "$CONFIG_DIR" "$DATA_DIR" "$LOG_DIR" "$SERVERS_ROOT"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" >/dev/null 2>&1

# Enabling is *checked*, not assumed: without it, MSM installs, starts, and never
# comes back after the machine reboots — a failure only discovered at the first
# power cut.
systemctl is-enabled --quiet "${SERVICE_NAME}" || fail \
  "The service could not be enabled at boot." \
  "systemctl enable ${SERVICE_NAME} failed: MSM would not come back after a reboot." \
  "Check: systemctl status ${SERVICE_NAME} ; then run: systemctl enable ${SERVICE_NAME}"

ok "Service installed and enabled at boot."

# --------------------------------------------------------------------------- #
#  12. Start
# --------------------------------------------------------------------------- #
step "Starting"

systemctl restart "${SERVICE_NAME}"
sleep 3

if systemctl is-active --quiet "${SERVICE_NAME}"; then
  ok "Service started."
else
  fail "The service did not start." \
       "systemd reports a failure at start-up." \
       "Check the logs: journalctl -u ${SERVICE_NAME} -n 50 --no-pager"
fi

# update.sh prints its own summary.
[[ "$FROM_UPDATE" -eq 1 ]] && exit 0

printf "\n${GREEN}${BOLD}Installation complete.${RESET}\n\n"
cat <<EOF
  Panel          http://${BIND_HOST}:${BIND_PORT}
  Configuration  ${CONFIG_DIR}/.env
  Data           ${DATA_DIR}
  Backups        ${DATA_DIR}/backups
  Logs           ${LOG_DIR}/msm.log  ·  journalctl -u ${SERVICE_NAME} -f
  Servers        ${SERVERS_ROOT}

  Service status     systemctl status ${SERVICE_NAME}
  Restart            systemctl restart ${SERVICE_NAME}
  Create an account  sudo -u ${MSM_USER} ${VENV}/bin/python -m msm.cli createadmin NAME

  MSM restarts automatically with the machine. For your Minecraft servers to
  come back too, tick "Start with MSM" in their settings.

EOF

if [[ "$BIND_HOST" == "127.0.0.1" ]]; then
  cat <<EOF
  The panel only listens on the local machine. To reach it remotely, put an
  HTTPS reverse proxy in front of it (recommended), then set
  MSM_SESSION_COOKIE_SECURE=true in ${CONFIG_DIR}/.env.

  Exposing MSM directly on the Internet without HTTPS would send the session
  cookie in clear text.

EOF
fi
