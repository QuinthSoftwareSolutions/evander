#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: setup_issue_git_vm.sh <vm-name> <issue-number> [repo-path] [vm-user]

Bootstrap passwordless Git access for a guest VM and create or refresh the
matching issue-numbered bare mirror on the host in one step.

Arguments:
  vm-name       libvirt domain name.
  issue-number  Issue identifier used to derive the repo/remote names.
  repo-path     Working tree to mirror.
                Default: /home/jonathan/Documents/Repos/ExampleIndustryAPP
  vm-user       Guest user to configure. Default: agent
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 2 ]]; then
  show_help >&2
  exit 1
fi

VM_NAME="$1"
ISSUE_NUMBER="$2"
REPO_PATH="${3:-/home/jonathan/Documents/Repos/ExampleIndustryAPP}"
VM_USER="${4:-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ ! -d "${REPO_PATH}" ]]; then
  echo "Missing repo path: ${REPO_PATH}" >&2
  exit 1
fi

"${SCRIPT_DIR}/bootstrap_git_vm.sh" "${VM_NAME}" "${VM_USER}"
"${SCRIPT_DIR}/mirror_issue_repo.sh" "${ISSUE_NUMBER}" "${REPO_PATH}"

echo
echo "One-command Git setup complete."
echo "Inside the VM, clone or set the remote to:"
echo "  ssh://agent-sandbox-git-host${ROOT_DIR}/git-remotes/${ISSUE_NUMBER}_repo.git"
