#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
NETWORK_XML="${ROOT_DIR}/config/libvirt-network.xml"

if ! command -v virsh >/dev/null 2>&1; then
  echo "virsh is required" >&2
  exit 1
fi

if virsh net-info agent-sandbox >/dev/null 2>&1; then
  echo "libvirt network 'agent-sandbox' already exists"
  exit 0
fi

virsh net-define "${NETWORK_XML}"
virsh net-autostart agent-sandbox
virsh net-start agent-sandbox
echo "Created libvirt network 'agent-sandbox'"
