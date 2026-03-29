#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: git_ssh_command_wrapper.sh

Forced-command wrapper used by host authorized_keys entries for guest Git SSH
access. It validates that the requested repository lives under the configured
Git root, auto-refreshes issue mirrors for guest read commands when mirror
metadata is available, and then hands the command to git-shell.
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
MIRROR_SCRIPT="${SCRIPT_DIR}/mirror_issue_repo.sh"

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

auto_refresh_issue_mirror() {
  local repo_real="$1"
  local original_command="$2"
  local repo_name issue_number mirror_source mirror_remote_name mirror_sync_remote mirror_force_local
  local -a mirror_args=()
  local -a mirror_refspecs=()

  case "${original_command}" in
    git-upload-pack\ *|git-upload-archive\ *)
      ;;
    *)
      return 0
      ;;
  esac

  repo_name="$(basename "${repo_real}")"
  case "${repo_name}" in
    *_repo.git)
      issue_number="${repo_name%_repo.git}"
      ;;
    *)
      return 0
      ;;
  esac

  mirror_source="$(git --git-dir="${repo_real}" config --get agent-sandbox.mirrorSource || true)"
  if [[ -z "${mirror_source}" ]]; then
    return 0
  fi

  mirror_remote_name="$(git --git-dir="${repo_real}" config --get agent-sandbox.mirrorRemoteName || true)"
  mirror_sync_remote="$(git --git-dir="${repo_real}" config --get agent-sandbox.mirrorSyncRemote || true)"
  mirror_force_local="$(git --git-dir="${repo_real}" config --get agent-sandbox.mirrorForceLocal || true)"
  mapfile -t mirror_refspecs < <(git --git-dir="${repo_real}" config --get-all agent-sandbox.mirrorRefspec || true)

  if [[ -n "${mirror_remote_name}" ]]; then
    mirror_args+=(--remote-name "${mirror_remote_name}")
  fi
  case "${mirror_sync_remote}" in
    1|true|yes|on)
      mirror_args+=(--sync-remote)
      ;;
    0|false|no|off)
      mirror_args+=(--no-sync-remote)
      ;;
  esac
  case "${mirror_force_local}" in
    1|true|yes|on)
      mirror_args+=(--force-local)
      ;;
  esac
  for refspec in "${mirror_refspecs[@]}"; do
    [[ -n "${refspec}" ]] || continue
    mirror_args+=(--refspec "${refspec}")
  done

  exec 9>"${repo_real}/agent-sandbox-sync.lock"
  flock 9

  "${MIRROR_SCRIPT}" "${mirror_args[@]}" "${issue_number}" "${mirror_source}" >/dev/null
}

auto_refresh_issue_mirror "${REPO_REAL}" "${ORIGINAL_COMMAND}"

exec git-shell -c "${ORIGINAL_COMMAND}"
