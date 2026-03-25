#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: destroy_agent_vm.sh <vm-name>

Destroy and undefine a libvirt VM, then remove its overlay disk and local
runtime state.

Arguments:
  vm-name   libvirt domain name.

Environment:
  VM_STORAGE_DIR  Root directory for libvirt-accessible VM storage.
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

VM_NAME="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/guest_ssh.sh
source "${SCRIPT_DIR}/lib/guest_ssh.sh"
VM_STORAGE_DIR="${VM_STORAGE_DIR:-/var/lib/libvirt/images/agent-sandbox}"
VM_DIR="${VM_STORAGE_DIR}/vms/${VM_NAME}"
STATE_DIR="${ROOT_DIR}/runtime/vms/${VM_NAME}"
CALLER_USER="${SUDO_USER:-${USER:-}}"
CALLER_HOME="${HOME:-}"

if [[ -n "${CALLER_USER}" ]]; then
  CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6 || printf '%s\n' "${CALLER_HOME}")"
fi

rm_tree() {
  local path="$1"

  if [[ ! -e "${path}" ]]; then
    return 0
  fi
  if rm -rf "${path}" >/dev/null 2>&1; then
    return 0
  fi
  sudo -n rm -rf "${path}"
}

virsh destroy "${VM_NAME}" >/dev/null 2>&1 || true
virsh undefine "${VM_NAME}" --nvram >/dev/null 2>&1 || virsh undefine "${VM_NAME}" >/dev/null 2>&1 || true
guest_ssh_remove_known_host "${VM_NAME}" "${CALLER_HOME}"
rm_tree "${VM_DIR}"
rm_tree "${STATE_DIR}"

echo "Destroyed VM ${VM_NAME}"
