#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-tgadmin2}"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

echo "[1/4] Pull latest code"
git -C "${PROJECT_ROOT}" pull --ff-only

echo "[2/4] Update Python dependencies"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Virtualenv not found. Please run scripts/setup_debian.sh first."
  exit 1
fi
"${PYTHON_BIN}" -m pip install -r "${PROJECT_ROOT}/requirements.txt"

echo "[3/4] Restart service"
as_root systemctl restart "${SERVICE_NAME}"

echo "[4/4] Update complete"
echo "Status: sudo systemctl status ${SERVICE_NAME}"
echo "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
