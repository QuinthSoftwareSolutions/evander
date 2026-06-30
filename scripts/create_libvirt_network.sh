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

if ! command -v virsh >/dev/null 2>&1; then
  echo "virsh is required" >&2
  exit 1
fi

if virsh net-info evander >/dev/null 2>&1; then
  echo "libvirt network 'evander' already exists"
  exit 0
fi

virsh net-define "${NETWORK_XML}"
virsh net-autostart evander
virsh net-start evander
echo "Created libvirt network 'evander'"
