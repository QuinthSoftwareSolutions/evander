#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: tail_issue_vm_log.sh <issue-number> [repo-name] [vm-user]

Follow the background guest Codex log for an issue VM from the host.

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

if ! VM_IP="$(get_vm_ip)"; then
  echo "Could not determine an IPv4 address for VM: ${VM_NAME}" >&2
  exit 1
fi

ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -i "${SSH_PRIVATE_KEY_PATH}" \
  "${VM_USER}@${VM_IP}" \
  "tail -f '$HOME/.agent-sandbox/codex-${REPO_NAME}.log'"
