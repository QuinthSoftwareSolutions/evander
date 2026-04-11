#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_LEVEL="${TEAMS_INGEST_LOG_LEVEL:-INFO}"

exec "${ROOT_DIR}/.venv/bin/agent-sandbox-cli" teams-ingest \
  --runtime-dir "${ROOT_DIR}/runtime" \
  --log-level "${LOG_LEVEL}"
