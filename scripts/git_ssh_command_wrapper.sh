#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: git_ssh_command_wrapper.sh

Forced-command wrapper used by host authorized_keys entries for guest Git SSH
access. It validates that the requested repository lives under the configured
Git root and then hands the command to git-shell.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/runtime_config.sh
source "${SCRIPT_DIR}/lib/runtime_config.sh"
load_issue_setup_config

GIT_ROOT="${AGENT_SANDBOX_GIT_ROOT:-${ROOT_DIR}/git-remotes}"
ROOT_REAL="$(realpath -m -- "${GIT_ROOT}")"
ORIGINAL_COMMAND="${SSH_ORIGINAL_COMMAND:-}"

if [[ -z "${ORIGINAL_COMMAND}" ]]; then
  echo "Missing SSH_ORIGINAL_COMMAND." >&2
  exit 1
fi

case "${ORIGINAL_COMMAND}" in
  git-upload-pack\ *|git-receive-pack\ *|git-upload-archive\ *)
    ;;
  *)
    echo "Unsupported Git SSH command." >&2
    exit 1
    ;;
esac

REPO_PATH="${ORIGINAL_COMMAND#* }"

if [[ "${REPO_PATH}" == \'*\' ]]; then
  REPO_PATH="${REPO_PATH#\'}"
  REPO_PATH="${REPO_PATH%\'}"
elif [[ "${REPO_PATH}" == \"*\" ]]; then
  REPO_PATH="${REPO_PATH#\"}"
  REPO_PATH="${REPO_PATH%\"}"
fi

REPO_REAL="$(realpath -m -- "${REPO_PATH}")"

case "${REPO_REAL}" in
  "${ROOT_REAL}"/*) ;;
  *)
    echo "Repository path is outside the allowed Git root." >&2
    exit 1
    ;;
esac

if [[ ! -d "${REPO_REAL}" ]]; then
  echo "Repository does not exist: ${REPO_REAL}" >&2
  exit 1
fi

exec git-shell -c "${ORIGINAL_COMMAND}"
