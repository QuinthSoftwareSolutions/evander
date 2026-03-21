#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: setup_issue_vm.sh <issue-number> <project-or-solution> [repo-path] [session-id] [vm-user]

Run the common issue-VM bring-up flow in one command:
  1. prewarm the shared NuGet cache on the host
  2. launch the issue VM
  3. bootstrap Codex, .NET, and NuGet in the guest
  4. configure passwordless Git access and create the issue mirror

Arguments:
  issue-number         Issue identifier used to derive vm and repo names.
  project-or-solution  Host path used to prewarm the shared NuGet cache.
  repo-path            Host repo to mirror for guest Git access.
                       Default: /home/jonathan/Documents/Repos/ExampleIndustryAPP
  session-id           Session name passed to launch_agent_vm.sh. Default: agent-session
  vm-user              Guest user to configure. Default: agent
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

ISSUE_NUMBER="$1"
PREWARM_TARGET="$2"
REPO_PATH="${3:-/home/jonathan/Documents/Repos/ExampleIndustryAPP}"
SESSION_ID="${4:-agent-session}"
VM_USER="${5:-agent}"
VM_NAME="vm-${ISSUE_NUMBER}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -e "${PREWARM_TARGET}" ]]; then
  echo "Missing project or solution path: ${PREWARM_TARGET}" >&2
  exit 1
fi

if [[ ! -d "${REPO_PATH}" ]]; then
  echo "Missing repo path: ${REPO_PATH}" >&2
  exit 1
fi

if virsh dominfo "${VM_NAME}" >/dev/null 2>&1 || sudo -n virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
  echo "VM already exists: ${VM_NAME}" >&2
  echo "Destroy it first with: sudo ./scripts/destroy_agent_vm.sh ${VM_NAME}" >&2
  exit 1
fi

"${SCRIPT_DIR}/prewarm_nuget_cache.sh" "${PREWARM_TARGET}"
sudo "${SCRIPT_DIR}/launch_agent_vm.sh" "${VM_NAME}" "${SESSION_ID}"
"${SCRIPT_DIR}/bootstrap_codex_vm.sh" "${VM_NAME}" "${VM_USER}"
"${SCRIPT_DIR}/bootstrap_dotnet_vm.sh" "${VM_NAME}" "${VM_USER}"
"${SCRIPT_DIR}/bootstrap_nuget_vm.sh" "${VM_NAME}" "${VM_USER}"
"${SCRIPT_DIR}/setup_issue_git_vm.sh" "${VM_NAME}" "${ISSUE_NUMBER}" "${REPO_PATH}" "${VM_USER}"

echo
echo "Issue VM setup complete."
echo "  vm:      ${VM_NAME}"
echo "  session: ${SESSION_ID}"
echo "  repo:    ssh://agent-sandbox-git-host/home/jonathan/agent-sandbox/git-remotes/${ISSUE_NUMBER}_repo.git"
