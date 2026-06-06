#!/usr/bin/env bash

as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

resolve_service_user() {
  local project_root="$1"

  stat -c '%U' "${project_root}"
}

resolve_service_group() {
  local service_user="$1"

  id -gn "${service_user}"
}

ensure_virtualenv() {
  local python_bin="$1"
  local venv_path="$2"

  if [[ ! -d "${venv_path}" ]]; then
    "${python_bin}" -m venv "${venv_path}"
  fi
}

install_python_dependencies() {
  local venv_python="$1"
  local requirements_file="$2"

  "${venv_python}" -m pip install --upgrade pip
  "${venv_python}" -m pip install -r "${requirements_file}"
}

write_default_env_file() {
  local env_file="$1"
  local bot_token="$2"
  local project_root="$3"
  local service_name="$4"

  cat > "${env_file}" <<EOF
BOT_TOKEN=${bot_token}
OWNER_ID=1095020773
DB_PATH=${project_root}/data/bot.sqlite3
VERIFY_TIMEOUT_SECONDS=600
EXPIRE_ACTION=kick
MAX_FAILED_ATTEMPTS=3
LOG_LEVEL=INFO
GROUP_MESSAGE_AUTO_DELETE_SECONDS=0
SCHEDULER_INTERVAL_SECONDS=30
SYSTEMD_SERVICE_NAME=${service_name}
PM2_PROCESS_NAME=${service_name}
REDIS_URL=
EOF
}

write_systemd_service() {
  local service_name="$1"
  local project_root="$2"
  local env_file="$3"
  local run_user="$4"
  local run_group="$5"
  local service_file="/etc/systemd/system/${service_name}.service"
  local tmp_service

  tmp_service="$(mktemp)"
  cat > "${tmp_service}" <<EOF
[Unit]
Description=Telegram Group Verification Bot
After=network.target

[Service]
Type=simple
User=${run_user}
Group=${run_group}
WorkingDirectory=${project_root}
EnvironmentFile=${env_file}
ExecStart=${project_root}/.venv/bin/python -m bot.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

  as_root install -m 0644 "${tmp_service}" "${service_file}"
  rm -f "${tmp_service}"
}

reload_systemd() {
  as_root systemctl daemon-reload
}

enable_systemd_service() {
  local service_name="$1"

  as_root systemctl enable --now "${service_name}"
}

restart_systemd_service() {
  local service_name="$1"

  as_root systemctl restart "${service_name}"
}
