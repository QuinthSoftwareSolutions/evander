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
  5. clone the mirrored repo into the guest user's home directory

Arguments:
  issue-number         Issue identifier used to derive vm and repo names.
  project-or-solution  Host path used to prewarm the shared NuGet cache.
  repo-path            Host repo to mirror for guest Git access.
                       Default: /home/jonathan/Documents/Repos/ExampleIndustryAPP
  session-id           Session name passed to launch_agent_vm.sh. Default: agent-session
  vm-user              Guest user to configure. Default: agent

Environment:
  VM_IP_WAIT_TIMEOUT_SECONDS  How long to wait for the guest to receive an IPv4 address.
                              Default: 180
  VM_SSH_WAIT_TIMEOUT_SECONDS How long to wait for guest SSH to accept connections.
                              Default: 180
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
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE:-${CALLER_HOME}/.ssh/id_ed25519.pub}"
VM_IP_WAIT_TIMEOUT_SECONDS="${VM_IP_WAIT_TIMEOUT_SECONDS:-180}"
VM_SSH_WAIT_TIMEOUT_SECONDS="${VM_SSH_WAIT_TIMEOUT_SECONDS:-180}"
GUEST_REPO_NAME="$(basename "${REPO_PATH}")"
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"

if [[ ! -f "${SSH_PUBLIC_KEY_FILE}" ]]; then
  SSH_PUBLIC_KEY_FILE="${CALLER_HOME}/.ssh/id_rsa.pub"
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

if [[ ! -e "${PREWARM_TARGET}" ]]; then
  echo "Missing project or solution path: ${PREWARM_TARGET}" >&2
  exit 1
fi

if [[ ! -d "${REPO_PATH}" ]]; then
  echo "Missing repo path: ${REPO_PATH}" >&2
  exit 1
fi

if [[ ! -f "${SSH_PUBLIC_KEY_FILE}" ]]; then
  echo "Missing SSH public key: ${SSH_PUBLIC_KEY_FILE}" >&2
  echo "Set SSH_PUBLIC_KEY_FILE to a readable public key before running this helper." >&2
  exit 1
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_PRIVATE_KEY_PATH}" >&2
  echo "Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

if virsh dominfo "${VM_NAME}" >/dev/null 2>&1 || sudo -n virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
  echo "VM already exists: ${VM_NAME}" >&2
  echo "Destroy it first with: sudo ./scripts/destroy_agent_vm.sh ${VM_NAME}" >&2
  exit 1
fi

get_vm_ip() {
  local ip

  ip="$(
    virsh domifaddr "${VM_NAME}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  ip="$(
    sudo -n virsh domifaddr "${VM_NAME}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  return 1
}

wait_for_vm_ip() {
  local deadline now ip
  deadline=$((SECONDS + VM_IP_WAIT_TIMEOUT_SECONDS))

  while (( SECONDS < deadline )); do
    if ip="$(get_vm_ip)"; then
      printf '%s\n' "${ip}"
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for ${VM_NAME} to receive an IPv4 address." >&2
  echo "Check guest boot progress with: sudo virsh console ${VM_NAME}" >&2
  return 1
}

wait_for_vm_ssh() {
  local deadline
  deadline=$((SECONDS + VM_SSH_WAIT_TIMEOUT_SECONDS))

  while (( SECONDS < deadline )); do
    if ssh \
      -o StrictHostKeyChecking=no \
      -o UserKnownHostsFile=/dev/null \
      -o ConnectTimeout=5 \
      -i "${SSH_PRIVATE_KEY_PATH}" \
      "${VM_USER}@${VM_IP}" true >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for SSH on ${VM_USER}@${VM_IP}." >&2
  echo "Check guest boot progress with: sudo virsh console ${VM_NAME}" >&2
  return 1
}

"${SCRIPT_DIR}/prewarm_nuget_cache.sh" "${PREWARM_TARGET}"
sudo SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE}" "${SCRIPT_DIR}/launch_agent_vm.sh" "${VM_NAME}" "${SESSION_ID}"
VM_IP="$(wait_for_vm_ip)"
echo "Guest IPv4 detected: ${VM_IP}"
wait_for_vm_ssh
echo "Guest SSH is ready on ${VM_USER}@${VM_IP}"
"${SCRIPT_DIR}/bootstrap_codex_vm.sh" "${VM_NAME}" "${VM_USER}"
"${SCRIPT_DIR}/bootstrap_dotnet_vm.sh" "${VM_NAME}" "${VM_USER}"
"${SCRIPT_DIR}/bootstrap_nuget_vm.sh" "${VM_NAME}" "${VM_USER}"
"${SCRIPT_DIR}/setup_issue_git_vm.sh" "${VM_NAME}" "${ISSUE_NUMBER}" "${REPO_PATH}" "${VM_USER}"

ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -i "${SSH_PRIVATE_KEY_PATH}" \
  "${VM_USER}@${VM_IP}" \
  "GUEST_REPO_NAME='${GUEST_REPO_NAME}' ISSUE_NUMBER='${ISSUE_NUMBER}' SESSION_ID='${SESSION_ID}' bash -s" <<'EOF'
set -euo pipefail
. ~/.profile

TARGET_DIR="$HOME/${GUEST_REPO_NAME}"
REMOTE_URL="ssh://agent-sandbox-git-host/home/jonathan/agent-sandbox/git-remotes/${ISSUE_NUMBER}_repo.git"

if [[ -e "${TARGET_DIR}" ]]; then
  echo "Guest repo path already exists: ${TARGET_DIR}" >&2
  exit 1
fi

git clone "${REMOTE_URL}" "${TARGET_DIR}"
git -C "${TARGET_DIR}" config alias.push-agent '!f(){ git push agent HEAD:refs/heads/agents/${SESSION_ID:-manual}/$(git rev-parse --abbrev-ref HEAD); }; f'

cat > "$HOME/AGENTS.md" <<AGENTS
# Guest Notes

- Primary repo path: ${TARGET_DIR}
- Guest Git remote: ${REMOTE_URL}
- Recommended push command from inside the repo:
  \`git push-agent\`
- Equivalent explicit push pattern:
  \`git push agent HEAD:refs/heads/agents/\${SESSION_ID:-manual}/\$(git rev-parse --abbrev-ref HEAD)\`
- For restore, always use:
  \`dotnet restore --configfile ~/.nuget/NuGet/NuGet.Config\`
- Do not rely on a repo-local \`NuGet.Config\` for restore inside the guest.
- For build and test, prefer:
  \`dotnet build --no-restore\`
  \`dotnet test --no-restore\`
- The reason for \`--no-restore\` is to avoid accidentally triggering a slow restore with the wrong NuGet config.
- If shell changes were just bootstrapped, run:
  \`. ~/.profile\`
AGENTS
EOF

echo
echo "Issue VM setup complete."
echo "  vm:      ${VM_NAME}"
echo "  session: ${SESSION_ID}"
echo "  repo:    ssh://agent-sandbox-git-host/home/jonathan/agent-sandbox/git-remotes/${ISSUE_NUMBER}_repo.git"
echo "  guest:   /home/${VM_USER}/${GUEST_REPO_NAME}"
echo "  push:    git -C /home/${VM_USER}/${GUEST_REPO_NAME} push-agent"
