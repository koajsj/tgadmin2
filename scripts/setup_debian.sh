#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-tgadmin2}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_FILE="${PROJECT_ROOT}/.env"
DB_PATH_DEFAULT="${PROJECT_ROOT}/data/bot.sqlite3"
BOT_TOKEN_VALUE="${BOT_TOKEN:-${1:-}}"

source "${SCRIPT_DIR}/deploy_common.sh"

RUN_USER="$(resolve_service_user "${PROJECT_ROOT}")"
RUN_GROUP="$(resolve_service_group "${RUN_USER}")"

if [[ -z "${BOT_TOKEN_VALUE}" && -f "${ENV_FILE}" ]]; then
  BOT_TOKEN_VALUE="$(grep -E '^BOT_TOKEN=' "${ENV_FILE}" | head -n1 | cut -d= -f2- || true)"
fi

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
install_python_dependencies "${PROJECT_ROOT}/.venv/bin/python" "${PROJECT_ROOT}/requirements.txt"

echo "[5/7] Write environment file"
if [[ ! -f "${ENV_FILE}" ]]; then
  write_default_env_file "${ENV_FILE}" "${BOT_TOKEN_VALUE}" "${PROJECT_ROOT}" "${SERVICE_NAME}"
else
  echo "Keeping existing ${ENV_FILE}"
fi
chmod 600 "${ENV_FILE}"

echo "[6/7] Install systemd service"
write_systemd_service "${SERVICE_NAME}" "${PROJECT_ROOT}" "${ENV_FILE}" "${RUN_USER}" "${RUN_GROUP}"
reload_systemd
enable_systemd_service "${SERVICE_NAME}"

echo "[7/7] Deployment complete"
echo "Service: ${SERVICE_NAME}"
echo "Status: sudo systemctl status ${SERVICE_NAME}"
echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
