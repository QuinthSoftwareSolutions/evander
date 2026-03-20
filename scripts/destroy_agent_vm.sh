#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <vm-name>" >&2
  exit 1
fi

VM_NAME="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VM_STORAGE_DIR="${VM_STORAGE_DIR:-/var/lib/libvirt/images/agent-sandbox}"
VM_DIR="${VM_STORAGE_DIR}/vms/${VM_NAME}"
STATE_DIR="${ROOT_DIR}/runtime/vms/${VM_NAME}"

virsh destroy "${VM_NAME}" >/dev/null 2>&1 || true
virsh undefine "${VM_NAME}" --nvram >/dev/null 2>&1 || virsh undefine "${VM_NAME}" >/dev/null 2>&1 || true
rm -rf "${VM_DIR}"
rm -rf "${STATE_DIR}"

echo "Destroyed VM ${VM_NAME}"
