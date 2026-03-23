#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: list_unassigned_ado_work_items.sh

Load config/ado-worker.env when present and print Azure DevOps work items that:
- match the same worker query as agent-sandbox-ado-worker
- have not yet been picked up by agents according to ADO_PROCESSED_STORE_PATH

Output format:
  <work-item-id><TAB><changed-date><TAB><state><TAB><title>

Requirements:
- A populated config/ado-worker.env
- The PAT environment variable named by ADO_PAT_ENV_VAR_NAME
- A working .venv with agent-sandbox installed
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/config/ado-worker.env"
CLI_BIN="${ROOT_DIR}/.venv/bin/agent-sandbox-cli"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  . "${ENV_FILE}"
  set +a
fi

if [[ ! -x "${CLI_BIN}" ]]; then
  echo "Missing CLI entry point: ${CLI_BIN}" >&2
  echo "Install the project into .venv first, for example: uv pip install -e '.[dev]'" >&2
  exit 1
fi

exec "${CLI_BIN}" poll-ado-backlog --list-unprocessed "$@"
