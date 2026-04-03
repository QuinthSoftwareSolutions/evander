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
GIT_USER="${AGENT_SANDBOX_GIT_USER:-${USER}}"
GIT_SSH_ALIAS="${AGENT_SANDBOX_GIT_SSH_ALIAS:-agent-sandbox-git-host}"
HOOK_SCRIPT="${ROOT_DIR}/scripts/handle_agent_push.sh"
TRIGGER_HOOK_SCRIPT="${ROOT_DIR}/scripts/handle_git_push_trigger.sh"

case "${REPO_NAME}" in
  *.git) REPO_DIR="${GIT_ROOT}/${REPO_NAME}" ;;
  *) REPO_DIR="${GIT_ROOT}/${REPO_NAME}.git" ;;
esac

mkdir -p "${GIT_ROOT}"

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

  if cat "\${updates_file}" | "${HOOK_SCRIPT}" "${REPO_DIR}"; then
    status=0
  else
    status=\$?
  fi
  rm -f "\${updates_file}"
  if [[ "\${trigger_status}" -ne 0 ]]; then
    status="\${trigger_status}"
  fi
  printf '=== %s post-receive exit=%s ===\n' "\$(date -Iseconds)" "\${status}"
  exit "\${status}"
} >> "\${LOG_FILE}" 2>&1 || true
HOOK
chmod +x "${REPO_DIR}/hooks/post-receive"

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
