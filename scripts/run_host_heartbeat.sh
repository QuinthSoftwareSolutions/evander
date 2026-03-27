#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_PATH="${1:-${ROOT_DIR}/config/gateway.yaml}"
LOG_LEVEL="${HOST_HEARTBEAT_LOG_LEVEL:-INFO}"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  echo "Host heartbeat config not found: ${CONFIG_PATH}" >&2
  exit 1
fi

exec "${ROOT_DIR}/.venv/bin/agent-sandbox-cli" host-heartbeat \
  --config "${CONFIG_PATH}" \
  --log-level "${LOG_LEVEL}"
