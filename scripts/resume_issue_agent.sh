#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: resume_issue_agent.sh <prompt> <issue-number> [vm-user]

SSH into an issue VM and run a guest Codex resume command from the guest user's
home directory.

Arguments:
  prompt        Prompt passed to `codex exec resume --last`.
  issue-number  Issue identifier used to derive the VM name.
  vm-user       Guest user to connect as. Default: agent

Environment:
  SSH_PRIVATE_KEY_PATH       Private key for guest SSH access.
                             Default: ~/.ssh/id_ed25519, then ~/.ssh/id_rsa
  VM_WAIT_IP_TIMEOUT_SECONDS How long to wait for the guest to receive an IPv4 address.
                             Default: 180
  VM_WAIT_SSH_TIMEOUT_SECONDS How long to wait for guest SSH to accept connections.
                              Default: 180
  CODEX_MODEL               Model passed to `codex exec resume`.
                            Default: gpt-5.4
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 2 || $# -gt 3 ]]; then
  show_help >&2
  exit 1
fi

PROMPT="$1"
ISSUE_NUMBER="$2"
VM_USER="${3:-agent}"
VM_NAME="vm-${ISSUE_NUMBER}"
CODEX_MODEL="${CODEX_MODEL:-gpt-5.4}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"

CALLER_USER="${SUDO_USER:-${USER:-}}"
CALLER_HOME="${HOME:-}"
if [[ -n "${CALLER_USER}" ]]; then
  CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6 || printf '%s\n' "${CALLER_HOME}")"
fi

SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
VM_WAIT_IP_TIMEOUT_SECONDS="${VM_WAIT_IP_TIMEOUT_SECONDS:-180}"
VM_WAIT_SSH_TIMEOUT_SECONDS="${VM_WAIT_SSH_TIMEOUT_SECONDS:-180}"

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_PRIVATE_KEY_PATH}" >&2
  echo "Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

if ! virsh dominfo "${VM_NAME}" >/dev/null 2>&1 && ! sudo -n virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
  echo "VM not found: ${VM_NAME}" >&2
  exit 1
fi

VM_IP="$(vm_network_wait_for_ip "${VM_NAME}" "${VM_WAIT_IP_TIMEOUT_SECONDS}")"
vm_network_wait_for_ssh "${VM_NAME}" "${VM_IP}" "${VM_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_WAIT_SSH_TIMEOUT_SECONDS}" >/dev/null

PROMPT_B64="$(printf '%s' "${PROMPT}" | base64 -w0)"

exec ssh \
  -t \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o ConnectTimeout=5 \
  -i "${SSH_PRIVATE_KEY_PATH}" \
  "${VM_USER}@${VM_IP}" \
  "PROMPT_B64='${PROMPT_B64}' CODEX_MODEL='${CODEX_MODEL}' bash -s" <<'EOF'
set -euo pipefail

cd "${HOME}"
. "${HOME}/.profile" >/dev/null 2>&1 || true

LOG_DIR="${HOME}/.agent-sandbox"
STATE_PATH="${LOG_DIR}/codex-resume-last.state"
PROMPT="$(python3 -c 'import base64, os; print(base64.b64decode(os.environ["PROMPT_B64"]).decode("utf-8"))')"
mkdir -p "${LOG_DIR}"

write_state() {
  local status="$1"
  local phase="$2"
  local exit_code="${3:-}"
  local now
  local tmp_path

  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  tmp_path="${STATE_PATH}.tmp"
  {
    printf "status=%s\n" "${status}"
    printf "phase=%s\n" "${phase}"
    printf "repo=%s\n" "resume-last"
    printf "pid=%s\n" "$$"
    printf "updated_at=%s\n" "${now}"
    if [[ -n "${exit_code}" ]]; then
      printf "exit_code=%s\n" "${exit_code}"
    fi
  } > "${tmp_path}"
  mv "${tmp_path}" "${STATE_PATH}"
}

on_exit() {
  local exit_code="$?"
  write_state "idle" "complete" "${exit_code}"
}

write_state "running" "resume"
trap on_exit EXIT

codex exec resume \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  --model "${CODEX_MODEL}" \
  --last \
  "${PROMPT}"
EOF
