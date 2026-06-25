#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_ripgrep_vm.sh <vm-name> [vm-user]

Copy a host ripgrep binary into an existing guest VM as ~/bin/rg.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH       Private key matching the guest authorized key.
  HOST_RIPGREP_BIN   Host ripgrep binary to copy. Default: command -v rg,
                     then Codex vendored rg, then common system paths.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
  show_help >&2
  exit 1
fi

VM_NAME="$1"
VM_USER="${2:-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/guest_ssh.sh
source "${SCRIPT_DIR}/lib/guest_ssh.sh"
# shellcheck source=lib/guest_ripgrep.sh
source "${SCRIPT_DIR}/lib/guest_ripgrep.sh"

CALLER_USER="${SUDO_USER:-${USER:-$(id -un)}}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_KEY_PATH}" >&2
  echo "Set SSH_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

get_vm_ip() {
  if guest_ssh_resolve_vm_ip "${VM_NAME}"; then
    return 0
  fi

  echo "Could not determine an IPv4 address for VM ${VM_NAME}." >&2
  echo "Run this script with sudo, grant the current user libvirt access, or authorize the pkexec prompt." >&2
  return 1
}

VM_IP="$(get_vm_ip)"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_KEY_PATH}" "${CALLER_HOME}")

GUEST_RIPGREP_REQUIRED=1 guest_ripgrep_seed "${VM_USER}" "${VM_IP}" "${SSH_OPTS[@]}"
echo "Ripgrep bootstrap complete for ${VM_NAME} (${VM_IP})"
