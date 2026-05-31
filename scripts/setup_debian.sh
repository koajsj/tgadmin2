#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "" && "${BOT_TOKEN:-}" == "" ]]; then
  echo "用法：BOT_TOKEN=123456:abc bash scripts/setup_debian.sh"
  echo "或者：bash scripts/setup_debian.sh 123456:abc"
  exit 1
fi

BOT_TOKEN_VALUE="${BOT_TOKEN:-${1:-}}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVICE_NAME="${SERVICE_NAME:-tgadmin2}"
RUN_USER="${SUDO_USER:-${USER}}"
RUN_GROUP="$(id -gn "${RUN_USER}")"
PYTHON_BIN="${PYTHON_BIN:-python3}"
ENV_FILE="${PROJECT_ROOT}/.env"
DB_PATH_DEFAULT="${PROJECT_ROOT}/data/bot.sqlite3"

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

echo "[1/7] 安装系统依赖"
as_root apt-get update
as_root apt-get install -y git python3 python3-venv

echo "[2/7] 创建运行目录"
mkdir -p "${PROJECT_ROOT}/data"

echo "[3/7] 创建虚拟环境"
if [[ ! -d "${PROJECT_ROOT}/.venv" ]]; then
  "${PYTHON_BIN}" -m venv "${PROJECT_ROOT}/.venv"
fi

echo "[4/7] 安装 Python 依赖"
"${PROJECT_ROOT}/.venv/bin/python" -m pip install --upgrade pip
"${PROJECT_ROOT}/.venv/bin/python" -m pip install -r "${PROJECT_ROOT}/requirements.txt"

echo "[5/7] 写入环境变量文件"
cat > "${ENV_FILE}" <<EOF
BOT_TOKEN=${BOT_TOKEN_VALUE}
DB_PATH=${DB_PATH_DEFAULT}
VERIFY_TIMEOUT_SECONDS=600
EXPIRE_ACTION=kick
LOG_LEVEL=INFO
GROUP_MESSAGE_AUTO_DELETE_SECONDS=0
SCHEDULER_INTERVAL_SECONDS=30
EOF
chmod 600 "${ENV_FILE}"

echo "[6/7] 写入 systemd 服务"
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

echo "[7/7] 部署完成"
echo "服务名: ${SERVICE_NAME}"
echo "查看状态: sudo systemctl status ${SERVICE_NAME}"
echo "查看日志: sudo journalctl -u ${SERVICE_NAME} -f"
