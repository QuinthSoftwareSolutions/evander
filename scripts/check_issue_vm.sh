#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: check_issue_vm.sh <issue-number> [repo-name] [vm-user]

Show the current status of the issue VM and the background guest Codex run.

Arguments:
  issue-number  Issue identifier used to derive the VM name.
  repo-name     Repo basename used by setup_issue_vm.sh. Default: ExampleIndustryAPP
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
REPO_NAME="${2:-ExampleIndustryAPP}"
VM_USER="${3:-agent}"
VM_NAME="vm-${ISSUE_NUMBER}"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"

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

if ! virsh dominfo "${VM_NAME}" >/dev/null 2>&1 && ! sudo -n virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
  echo "VM not found: ${VM_NAME}" >&2
  exit 1
fi

VM_STATE="$(
  virsh dominfo "${VM_NAME}" 2>/dev/null | awk -F': *' '/^State:/ {print $2; exit}'
)"
if [[ -z "${VM_STATE}" ]]; then
  VM_STATE="$(
    sudo -n virsh dominfo "${VM_NAME}" 2>/dev/null | awk -F': *' '/^State:/ {print $2; exit}'
  )"
fi

VM_IP=""
if get_vm_ip >/dev/null 2>&1; then
  VM_IP="$(get_vm_ip)"
fi

echo "vm:    ${VM_NAME}"
echo "state: ${VM_STATE:-unknown}"
echo "ip:    ${VM_IP:-unavailable}"

if [[ -z "${VM_IP}" ]]; then
  echo "agent: unavailable (no guest IP yet)"
  exit 0
fi

ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o ConnectTimeout=5 \
  -i "${SSH_PRIVATE_KEY_PATH}" \
  "${VM_USER}@${VM_IP}" \
  "REPO_NAME='${REPO_NAME}' bash -s" <<'EOF'
set -euo pipefail

LOG_DIR="$HOME/.agent-sandbox"
LOG_PATH="${LOG_DIR}/codex-${REPO_NAME}.log"
PID_PATH="${LOG_DIR}/codex-${REPO_NAME}.pid"

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
