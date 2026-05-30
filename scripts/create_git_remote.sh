#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: create_git_remote.sh <repo-name> [branch-namespace]

Create a bare Git remote for agent output on the host-only network.

Arguments:
  repo-name         Bare repository name. ".git" suffix is optional.
  branch-namespace  Prefix used in the recommended push pattern.
                    Default: agents

Configuration:
  Reads root directory-scoped issue setup config when present.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 1 ]]; then
  show_help >&2
  exit 1
fi

REPO_NAME="$1"
BRANCH_NAMESPACE="${2:-agents}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/runtime_config.sh
source "${SCRIPT_DIR}/lib/runtime_config.sh"
load_issue_setup_config

GIT_ROOT="${AGENT_SANDBOX_GIT_ROOT:-${ROOT_DIR}/git-remotes}"
GIT_HOST="${AGENT_SANDBOX_GIT_HOST:-192.168.250.1}"
DEFAULT_GIT_USER="${USER:-}"
if [[ -z "${DEFAULT_GIT_USER}" ]]; then
  DEFAULT_GIT_USER="$(id -un 2>/dev/null || echo root)"
fi
GIT_USER="${AGENT_SANDBOX_GIT_USER:-${DEFAULT_GIT_USER}}"
GIT_SSH_ALIAS="${AGENT_SANDBOX_GIT_SSH_ALIAS:-agent-sandbox-git-host}"
TRIGGER_HOOK_SCRIPT="${ROOT_DIR}/scripts/handle_git_push_trigger.sh"

repair_git_remote_ownership_if_root() {
  local path="$1"
  local target_user="$2"
  local recursive="${3:-1}"
  local target_group

  if [[ "$(id -u)" != "0" || -z "${target_user}" || "${target_user}" == "root" ]]; then
    return 0
  fi
  if ! target_group="$(id -gn "${target_user}" 2>/dev/null)"; then
    echo "Cannot resolve group for Git remote owner '${target_user}'." >&2
    exit 1
  fi
  if [[ "${recursive}" == "1" ]]; then
    chown -R "${target_user}:${target_group}" "${path}"
  else
    chown "${target_user}:${target_group}" "${path}"
  fi
}

case "${REPO_NAME}" in
  *.git) REPO_DIR="${GIT_ROOT}/${REPO_NAME}" ;;
  *) REPO_DIR="${GIT_ROOT}/${REPO_NAME}.git" ;;
esac

mkdir -p "${GIT_ROOT}"
repair_git_remote_ownership_if_root "${GIT_ROOT}" "${GIT_USER}" 0

if [[ ! -d "${REPO_DIR}" ]]; then
  git init --bare "${REPO_DIR}" >/dev/null
  git --git-dir="${REPO_DIR}" config receive.denyCurrentBranch ignore
  git --git-dir="${REPO_DIR}" config gc.auto 0
fi

cat > "${REPO_DIR}/hooks/post-receive" <<HOOK
#!/usr/bin/env bash
set -euo pipefail
LOG_DIR="${ROOT_DIR}/runtime/hook-logs"
LOG_FILE="\${LOG_DIR}/$(basename "${REPO_DIR}").post-receive.log"
mkdir -p "\${LOG_DIR}"
{
  printf '=== %s post-receive start ===\n' "\$(date -Iseconds)"
  updates_file="\$(mktemp)"
  cat > "\${updates_file}"

  trigger_status=0
  if [[ -x "${TRIGGER_HOOK_SCRIPT}" ]]; then
    if cat "\${updates_file}" | "${TRIGGER_HOOK_SCRIPT}" "${REPO_DIR}"; then
      trigger_status=0
    else
      trigger_status=\$?
    fi
  fi

  status="\${trigger_status}"
  rm -f "\${updates_file}"
  printf '=== %s post-receive exit=%s ===\n' "\$(date -Iseconds)" "\${status}"
  exit "\${status}"
} >> "\${LOG_FILE}" 2>&1 || true
HOOK
chmod +x "${REPO_DIR}/hooks/post-receive"
repair_git_remote_ownership_if_root "${REPO_DIR}" "${GIT_USER}"

echo "Bare remote ready: ${REPO_DIR}"
echo
echo "Before first VM use:"
echo "  ./scripts/bootstrap_git_vm.sh <vm-name>"
echo
echo "Add from VM:"
echo "  git remote add agent ssh://${GIT_SSH_ALIAS}${REPO_DIR}"
echo
echo "Recommended push pattern:"
echo "  git push agent HEAD:refs/heads/${BRANCH_NAMESPACE}/\${SESSION_ID:-manual}/\$(git rev-parse --abbrev-ref HEAD)"
