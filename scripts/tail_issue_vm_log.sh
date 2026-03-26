#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: tail_issue_vm_log.sh <issue-number> [lines] [repo-name] [vm-user]

Follow the background guest agent log for an issue VM from the host.

Arguments:
  issue-number  Issue identifier used to derive the VM name.
  lines         Number of log lines to print before following. Default: 10
  repo-name     Repo basename used by setup_issue_vm.sh. Default: auto-detect
  vm-user       Guest user to inspect. Default: agent
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
TAIL_LINES="${2:-10}"
REPO_NAME="${3:-}"
VM_USER="${4:-agent}"
VM_NAME="vm-${ISSUE_NUMBER}"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
# shellcheck source=lib/guest_ssh.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/guest_ssh.sh"
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
printf -v REPO_NAME_SSH %q "${REPO_NAME}"
printf -v TAIL_LINES_SSH %q "${TAIL_LINES}"

if ! [[ "${TAIL_LINES}" =~ ^[1-9][0-9]*$ ]]; then
  echo "lines must be a positive integer: ${TAIL_LINES}" >&2
  exit 1
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_PRIVATE_KEY_PATH}" >&2
  echo "Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

get_vm_ip() {
  local ip

  ip="$(
    sudo -n virsh domifaddr "${VM_NAME}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  ip="$(
    virsh domifaddr "${VM_NAME}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  return 1
}

if ! VM_IP="$(get_vm_ip)"; then
  echo "Could not determine an IPv4 address for VM: ${VM_NAME}" >&2
  exit 1
fi

mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_PRIVATE_KEY_PATH}" "${CALLER_HOME}")

ssh \
  "${SSH_OPTS[@]}" \
  "${VM_USER}@${VM_IP}" \
  "REPO_NAME=${REPO_NAME_SSH} TAIL_LINES=${TAIL_LINES_SSH} bash -s" <<'EOF'
set -euo pipefail

LOG_DIR="$HOME/.agent-sandbox"
RESOLVED_REPO_NAME="${REPO_NAME:-}"

if [[ -z "${RESOLVED_REPO_NAME}" ]]; then
  shopt -s nullglob
  candidates=()

  for path in "${LOG_DIR}"/agent-*.pid "${LOG_DIR}"/agent-*.log; do
    name="$(basename "${path}")"
    name="${name#agent-}"
    name="${name%.pid}"
    name="${name%.log}"
    if [[ -n "${name}" ]]; then
      candidates+=("${name}")
    fi
  done

  shopt -u nullglob

  if [[ ${#candidates[@]} -eq 0 ]]; then
    echo "No guest agent logs or pid files found under ${LOG_DIR}" >&2
    exit 0
  fi

  mapfile -t unique_candidates < <(printf '%s\n' "${candidates[@]}" | sort -u)
  if [[ ${#unique_candidates[@]} -ne 1 ]]; then
    printf 'Multiple guest agent logs found under %s; pass an explicit repo-name. Candidates:\n' "${LOG_DIR}" >&2
    printf '  %s\n' "${unique_candidates[@]}" >&2
    exit 1
  fi

  RESOLVED_REPO_NAME="${unique_candidates[0]}"
fi

LOG_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
if [[ ! -f "${LOG_PATH}" ]]; then
  echo "No guest agent log found at ${LOG_PATH}" >&2
  exit 0
fi

tail -n "${TAIL_LINES}" -f "${LOG_PATH}"
EOF
