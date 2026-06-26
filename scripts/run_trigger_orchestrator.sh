#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_LEVEL="${TRIGGER_ORCHESTRATOR_LOG_LEVEL:-INFO}"
CLI_BIN="${ROOT_DIR}/.venv/bin/evander-cli"

if [[ ! -x "${CLI_BIN}" && -x "${ROOT_DIR}/.venv/bin/agent-sandbox-cli" ]]; then
  CLI_BIN="${ROOT_DIR}/.venv/bin/agent-sandbox-cli"
fi

exec "${CLI_BIN}" trigger-orchestrator \
  --runtime-dir "${ROOT_DIR}/runtime" \
  --log-level "${LOG_LEVEL}"
