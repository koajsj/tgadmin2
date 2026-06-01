#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-tgadmin2}"
RUN_USER="${SUDO_USER:-${USER}}"
RUN_GROUP="$(id -gn "${RUN_USER}")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_FILE="${PROJECT_ROOT}/.env"
DB_PATH_DEFAULT="${PROJECT_ROOT}/data/bot.sqlite3"
BOT_TOKEN_VALUE="${BOT_TOKEN:-${1:-}}"

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

if [[ -z "${BOT_TOKEN_VALUE}" ]]; then
  printf "Please enter BOT_TOKEN: "
  read -rs BOT_TOKEN_VALUE
  printf "\n"
fi

if [[ -z "${BOT_TOKEN_VALUE}" ]]; then
  echo "BOT_TOKEN cannot be empty."
  exit 1
fi

echo "[1/7] Install system packages"
as_root apt-get update
as_root apt-get install -y git python3 python3-venv

echo "[2/7] Prepare project directories"
mkdir -p "${PROJECT_ROOT}/data"

echo "[3/7] Create virtual environment"
if [[ ! -d "${PROJECT_ROOT}/.venv" ]]; then
  "${PYTHON_BIN}" -m venv "${PROJECT_ROOT}/.venv"
fi

echo "[4/7] Install Python dependencies"
"${PROJECT_ROOT}/.venv/bin/python" -m pip install --upgrade pip
"${PROJECT_ROOT}/.venv/bin/python" -m pip install -r "${PROJECT_ROOT}/requirements.txt"

echo "[5/7] Write environment file"
cat > "${ENV_FILE}" <<EOF
BOT_TOKEN=${BOT_TOKEN_VALUE}
OWNER_ID=1095020773
DB_PATH=${DB_PATH_DEFAULT}
VERIFY_TIMEOUT_SECONDS=600
EXPIRE_ACTION=kick
MAX_FAILED_ATTEMPTS=3
LOG_LEVEL=INFO
GROUP_MESSAGE_AUTO_DELETE_SECONDS=0
SCHEDULER_INTERVAL_SECONDS=30
SYSTEMD_SERVICE_NAME=${SERVICE_NAME}
PM2_PROCESS_NAME=${SERVICE_NAME}
REDIS_URL=
EOF
chmod 600 "${ENV_FILE}"

echo "[6/7] Install systemd service"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
TMP_SERVICE="$(mktemp)"
cat > "${TMP_SERVICE}" <<EOF
[Unit]
Description=Telegram Group Verification Bot
After=network.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${PROJECT_ROOT}
EnvironmentFile=${ENV_FILE}
ExecStart=${PROJECT_ROOT}/.venv/bin/python -m bot.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
as_root mv "${TMP_SERVICE}" "${SERVICE_FILE}"
as_root systemctl daemon-reload
as_root systemctl enable --now "${SERVICE_NAME}"

echo "[7/7] Deployment complete"
echo "Service: ${SERVICE_NAME}"
echo "Status: sudo systemctl status ${SERVICE_NAME}"
echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
