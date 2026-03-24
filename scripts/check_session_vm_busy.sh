#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'USAGE'
Usage: check_session_vm_busy.sh <session-id> <vm-name> [vm-user]

Inspect a session VM and report whether an agent process is active.

Arguments:
  session-id  Session identifier for logging context.
  vm-name     Libvirt domain name.
  vm-user     Guest SSH user. Default: agent

Environment:
  SSH_PRIVATE_KEY_PATH                     Private key for guest SSH access.
                                           Default: ~/.ssh/id_ed25519, then ~/.ssh/id_rsa
  VM_AGENT_BUSY_CONNECT_TIMEOUT_SECONDS    SSH connect timeout in seconds.
                                           Default: 5
  VM_AGENT_STATE_MAX_AGE_SECONDS           Maximum age for a running state file before
                                           a secondary agent liveness check is required.
                                           Default: 600
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 2 || $# -gt 3 ]]; then
  show_help >&2
  exit 1
fi

SESSION_ID="$1"
VM_NAME="$2"
VM_USER="${3:-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/agent_status.sh
source "${SCRIPT_DIR}/lib/agent_status.sh"

CALLER_USER="${SUDO_USER:-${USER:-}}"
CALLER_HOME="${HOME:-}"
if [[ -n "${CALLER_USER}" ]]; then
  CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6 || printf '%s\n' "${CALLER_HOME}")"
fi

SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
VM_AGENT_BUSY_CONNECT_TIMEOUT_SECONDS="${VM_AGENT_BUSY_CONNECT_TIMEOUT_SECONDS:-5}"

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  printf 'status=unknown reason=missing-ssh-key session=%s vm=%s\n' "${SESSION_ID}" "${VM_NAME}"
  exit 2
fi

status="$(vm_agent_status_for_vm "${VM_NAME}" "${VM_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_AGENT_BUSY_CONNECT_TIMEOUT_SECONDS}")"

if vm_agent_is_busy_status "${status}"; then
  printf 'status=busy reason=agent-busy session=%s vm=%s detail=%q\n' "${SESSION_ID}" "${VM_NAME}" "${status}"
  exit 0
fi

printf 'status=idle session=%s vm=%s detail=%q\n' "${SESSION_ID}" "${VM_NAME}" "${status}"
exit 1
