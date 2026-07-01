#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: create_libvirt_network.sh [--help]

Define, autostart, and start the isolated libvirt network used by agent VMs.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
NETWORK_XML="${ROOT_DIR}/config/libvirt-network.xml"
NETWORK_NAME="evander"
LEGACY_NETWORK_NAME="agent-sandbox"

if ! command -v virsh >/dev/null 2>&1; then
  echo "virsh is required" >&2
  exit 1
fi

migrate_legacy_network_if_needed() {
  if ! virsh net-info "${LEGACY_NETWORK_NAME}" >/dev/null 2>&1; then
    return 0
  fi

  echo "Migrating legacy libvirt network '${LEGACY_NETWORK_NAME}' to '${NETWORK_NAME}'"
  if virsh net-info "${LEGACY_NETWORK_NAME}" | grep -Eq '^Active:[[:space:]]*yes$'; then
    virsh net-destroy "${LEGACY_NETWORK_NAME}"
  fi
  virsh net-undefine "${LEGACY_NETWORK_NAME}"
}

if virsh net-info "${NETWORK_NAME}" >/dev/null 2>&1; then
  echo "libvirt network '${NETWORK_NAME}' already exists"
  exit 0
fi

migrate_legacy_network_if_needed

virsh net-define "${NETWORK_XML}"
virsh net-autostart "${NETWORK_NAME}"
virsh net-start "${NETWORK_NAME}"
echo "Created libvirt network '${NETWORK_NAME}'"
