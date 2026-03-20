#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: install_systemd_units.sh [--help]

Render and install the agent-sandbox systemd unit files, then enable them.

Requirements:
  Run this script with sudo.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUN_USER="${SUDO_USER:-$USER}"
PROJECT_DIR="${ROOT_DIR}"
SYSTEMD_DIR="/etc/systemd/system"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run with sudo." >&2
  exit 1
fi

if [[ ! -f "${ROOT_DIR}/config/gateway.env" ]]; then
  cp "${ROOT_DIR}/config/gateway.env.example" "${ROOT_DIR}/config/gateway.env"
fi

if [[ ! -f "${ROOT_DIR}/config/openai-relay.env" ]]; then
  cp "${ROOT_DIR}/config/openai-relay.env.example" "${ROOT_DIR}/config/openai-relay.env"
fi

if [[ ! -f "${ROOT_DIR}/config/firewall.env" ]]; then
  cp "${ROOT_DIR}/config/firewall.env.example" "${ROOT_DIR}/config/firewall.env"
fi

render_unit() {
  local template="$1"
  local target="$2"
  sed \
    -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
    -e "s|__RUN_USER__|${RUN_USER}|g" \
    "${template}" > "${target}"
}

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-gateway.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-gateway.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-openai-relay.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-openai-relay.service"

render_unit \
  "${ROOT_DIR}/systemd/agent-sandbox-firewall.service.template" \
  "${SYSTEMD_DIR}/agent-sandbox-firewall.service"

systemctl daemon-reload
systemctl enable agent-sandbox-gateway.service
systemctl enable agent-sandbox-openai-relay.service
systemctl enable agent-sandbox-firewall.service

cat <<EOF
Installed systemd units:
  agent-sandbox-gateway.service
  agent-sandbox-openai-relay.service
  agent-sandbox-firewall.service

Next steps:
  1. Edit ${ROOT_DIR}/config/openai-relay.env and set OPENAI_API_KEY.
  2. Optionally edit ${ROOT_DIR}/config/firewall.env to change allowed host ports.
  3. Start services:
     sudo systemctl start agent-sandbox-firewall.service
     sudo systemctl start agent-sandbox-gateway.service
     sudo systemctl start agent-sandbox-openai-relay.service
EOF
