#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: check_issue_vm.sh <issue-number> [repo-name] [vm-user]

Show the current status of the issue VM and the background guest agent run.

Arguments:
  issue-number  Issue identifier used to derive the VM name.
  repo-name     Repo basename used by guest agent logs. Default: auto-detect
  vm-user       Guest user to inspect. Default: agent

Environment:
  VM_SSH_USER                   Privileged SSH user used to read root-owned logs.
                                Default: agent-admin
  VM_WAIT_IP_TIMEOUT_SECONDS   How long to wait for the guest to receive an IPv4 address.
                               Default: 180
  VM_WAIT_SSH_TIMEOUT_SECONDS  How long to wait for guest SSH to accept connections.
                               Default: 180
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

ISSUE_NUMBER="$1"
REPO_NAME="${2:-}"
VM_USER="${3:-agent}"
SSH_USER="${VM_SSH_USER:-agent-admin}"
VM_NAME="vm-${ISSUE_NUMBER}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
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

if ! sudo -n virsh dominfo "${VM_NAME}" >/dev/null 2>&1 && ! virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
  echo "VM not found: ${VM_NAME}" >&2
  exit 1
fi

DOMINFO_OUTPUT="$(sudo -n virsh dominfo "${VM_NAME}" 2>/dev/null || true)"
VM_STATE="$(awk -F': *' '/^State:/ {print $2; exit}' <<<"${DOMINFO_OUTPUT}")"
if [[ -z "${VM_STATE}" ]]; then
  DOMINFO_OUTPUT="$(virsh dominfo "${VM_NAME}" 2>/dev/null || true)"
  VM_STATE="$(awk -F': *' '/^State:/ {print $2; exit}' <<<"${DOMINFO_OUTPUT}")"
fi

VM_IP=""
if VM_IP="$(vm_network_wait_for_ip "${VM_NAME}" "${VM_WAIT_IP_TIMEOUT_SECONDS}" 2>/dev/null)"; then
  :
else
  VM_IP=""
fi

echo "vm:    ${VM_NAME}"
echo "state: ${VM_STATE:-unknown}"
echo "ip:    ${VM_IP:-unavailable}"

if [[ -z "${VM_IP}" ]]; then
  echo "agent: unavailable (no guest IP yet)"
  exit 0
fi

if ! vm_network_wait_for_ssh "${VM_NAME}" "${VM_IP}" "${SSH_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_WAIT_SSH_TIMEOUT_SECONDS}" >/dev/null 2>&1; then
  if [[ "${SSH_USER}" != "${VM_USER}" ]] \
    && vm_network_wait_for_ssh "${VM_NAME}" "${VM_IP}" "${VM_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_WAIT_SSH_TIMEOUT_SECONDS}" >/dev/null 2>&1; then
    SSH_USER="${VM_USER}"
  else
    echo "agent: unavailable (guest SSH not ready)"
    exit 0
  fi
fi

mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_PRIVATE_KEY_PATH}" "${CALLER_HOME}")
SSH_OPTS+=(-o ConnectTimeout=5)

check_remote() {
  local ssh_user="$1"

ssh \
  "${SSH_OPTS[@]}" \
  "${ssh_user}@${VM_IP}" \
  "sudo -n env AGENT_USER='${VM_USER}' REPO_NAME='${REPO_NAME}' AGENT_RUN_LOG_DIR=/var/log/evander/agent-runs bash -s" <<'EOF'
set -euo pipefail

AGENT_USER="${AGENT_USER:-agent}"
AGENT_HOME="$(getent passwd "${AGENT_USER}" | cut -d: -f6)"
if [[ -z "${AGENT_HOME}" ]]; then
  echo "agent: unavailable (unknown agent user ${AGENT_USER})"
  exit 0
fi
LOG_DIR="${AGENT_HOME}/.evander"
RUN_LOG_DIR="${AGENT_RUN_LOG_DIR:-/var/log/evander/agent-runs}"
RESOLVED_REPO_NAME="${REPO_NAME:-}"

if [[ -z "${RESOLVED_REPO_NAME}" ]]; then
  shopt -s nullglob
  candidates=()

  for path in "${LOG_DIR}"/agent-*.pid "${LOG_DIR}"/agent-*.state; do
    name="$(basename "${path}")"
    name="${name#agent-}"
    name="${name%.pid}"
    name="${name%.state}"
    if [[ -n "${name}" ]]; then
      candidates+=("${name}")
    fi
  done

  if [[ -f "${LOG_DIR}/codex-resume-last.state" || -f "${LOG_DIR}/codex-heartbeat.pid" ]]; then
    candidates+=("codex-heartbeat")
  fi

  shopt -u nullglob

  if [[ ${#candidates[@]} -eq 0 ]]; then
    echo "agent: not started"
    echo "repo:  unknown (no guest agent state or pid files found)"
    exit 0
  fi

  mapfile -t unique_candidates < <(printf '%s\n' "${candidates[@]}" | sort -u)
  if [[ ${#unique_candidates[@]} -ne 1 ]]; then
    echo "agent: ambiguous"
    echo "repo:  pass an explicit repo-name"
    printf 'candidates:\n'
    printf '  %s\n' "${unique_candidates[@]}"
    exit 0
  fi

  RESOLVED_REPO_NAME="${unique_candidates[0]}"
fi

if [[ "${RESOLVED_REPO_NAME}" == "codex-heartbeat" ]]; then
  LOG_PATH="${RUN_LOG_DIR}/codex-heartbeat-run.log"
  PID_PATH="${LOG_DIR}/codex-heartbeat.pid"
else
  LOG_PATH="${RUN_LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
  PID_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.pid"
fi
echo "repo:  ${RESOLVED_REPO_NAME}"

if [[ ! -f "${PID_PATH}" ]]; then
  echo "agent: not started"
  echo "pid:   missing (${PID_PATH})"
  echo "log:   ${LOG_PATH}"
  exit 0
fi

PID="$(cat "${PID_PATH}")"
echo "pid:   ${PID}"
echo "log:   ${LOG_PATH}"

if [[ -n "${PID}" ]] && kill -0 "${PID}" >/dev/null 2>&1; then
  echo "agent: running"
else
  echo "agent: not running"
fi

if [[ -f "${LOG_PATH}" ]]; then
  echo "last-log-lines:"
  tail -n 10 "${LOG_PATH}"
else
  echo "last-log-lines: missing (${LOG_PATH})"
fi
EOF
}

if ! check_remote "${SSH_USER}"; then
  if [[ "${SSH_USER}" != "${VM_USER}" ]]; then
    check_remote "${VM_USER}"
  else
    exit 1
  fi
fi
