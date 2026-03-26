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
  HOST_AGENT_CONTROL_STATE_PATH  JSON file storing host-side agent control state.
                                 Default: runtime/host-agent-control-state.json
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
HOST_AGENT_CONTROL_STATE_PATH="${HOST_AGENT_CONTROL_STATE_PATH:-${ROOT_DIR}/runtime/host-agent-control-state.json}"
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

clear_host_control_state() {
  local path="$1"
  local vm_name="$2"

  [[ -f "${path}" ]] || return 0

  python3 - "${path}" "${vm_name}" <<'PY'
import json
import os
import sys
import tempfile
import fcntl

path, vm_name = sys.argv[1:3]
lock_path = f"{path}.lock" if os.path.splitext(path)[1] else f"{path}.lock"
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    if payload.pop(vm_name, None) is None:
        sys.exit(0)

    fd, tmp_path = tempfile.mkstemp(prefix="agent-control-", suffix=".tmp", dir=os.path.dirname(path) or ".")
    os.close(fd)
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.replace(tmp_path, path)
finally:
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
PY
}

sudo -n virsh destroy "${VM_NAME}" >/dev/null 2>&1 || virsh destroy "${VM_NAME}" >/dev/null 2>&1 || true
sudo -n virsh undefine "${VM_NAME}" --nvram >/dev/null 2>&1 \
  || sudo -n virsh undefine "${VM_NAME}" >/dev/null 2>&1 \
  || virsh undefine "${VM_NAME}" --nvram >/dev/null 2>&1 \
  || virsh undefine "${VM_NAME}" >/dev/null 2>&1 \
  || true
guest_ssh_remove_known_host "${VM_NAME}" "${CALLER_HOME}"
rm_tree "${VM_DIR}"
rm_tree "${STATE_DIR}"
clear_host_control_state "${HOST_AGENT_CONTROL_STATE_PATH}" "${VM_NAME}"

echo "Destroyed VM ${VM_NAME}"
