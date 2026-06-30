#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_LEVEL="${TEAMS_INGEST_LOG_LEVEL:-INFO}"
CLI_BIN="${ROOT_DIR}/.venv/bin/evander-cli"

if [[ ! -x "${CLI_BIN}" && -x "${ROOT_DIR}/.venv/bin/evander-cli" ]]; then
  CLI_BIN="${ROOT_DIR}/.venv/bin/evander-cli"
fi

exec "${CLI_BIN}" teams-ingest \
  --runtime-dir "${ROOT_DIR}/runtime" \
  --log-level "${LOG_LEVEL}"
