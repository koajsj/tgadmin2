#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-tgadmin2}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_FILE="${PROJECT_ROOT}/.env"
BOT_TOKEN_VALUE="${BOT_TOKEN:-${1:-}}"

source "${SCRIPT_DIR}/deploy_common.sh"

RUN_USER="$(resolve_service_user "${PROJECT_ROOT}")"
RUN_GROUP="$(resolve_service_group "${RUN_USER}")"

if [[ -z "${BOT_TOKEN_VALUE}" && -f "${ENV_FILE}" ]]; then
  BOT_TOKEN_VALUE="$(grep -E '^BOT_TOKEN=' "${ENV_FILE}" | head -n1 | cut -d= -f2- || true)"
fi

echo "[1/6] Pull latest code"
git -C "${PROJECT_ROOT}" pull --ff-only

echo "[2/6] Prepare runtime files"
mkdir -p "${PROJECT_ROOT}/data"
ensure_virtualenv "${PYTHON_BIN}" "${PROJECT_ROOT}/.venv"
if [[ ! -f "${ENV_FILE}" ]]; then
  if [[ -z "${BOT_TOKEN_VALUE}" ]]; then
    printf "Please enter BOT_TOKEN: "
    read -rs BOT_TOKEN_VALUE
    printf "\n"
  fi

  if [[ -z "${BOT_TOKEN_VALUE}" ]]; then
    echo "BOT_TOKEN cannot be empty."
    exit 1
  fi

  write_default_env_file "${ENV_FILE}" "${BOT_TOKEN_VALUE}" "${PROJECT_ROOT}" "${SERVICE_NAME}"
fi
chmod 600 "${ENV_FILE}"

echo "[3/6] Update Python dependencies"
install_python_dependencies "${PROJECT_ROOT}/.venv/bin/python" "${PROJECT_ROOT}/requirements.txt"

echo "[4/6] Refresh systemd service"
write_systemd_service "${SERVICE_NAME}" "${PROJECT_ROOT}" "${ENV_FILE}" "${RUN_USER}" "${RUN_GROUP}"
reload_systemd

echo "[5/6] Enable and restart service"
enable_systemd_service "${SERVICE_NAME}"

echo "[6/6] Update complete"
echo "Status: sudo systemctl status ${SERVICE_NAME}"
echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
