#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: create_base_vm.sh [--help]

Download the Ubuntu cloud image used as the read-only base for guest overlays.

Environment:
  VM_STORAGE_DIR  Root directory for libvirt-accessible VM storage.
  DOWNLOAD_URL    Cloud image URL to download.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VM_STORAGE_DIR="${VM_STORAGE_DIR:-/var/lib/libvirt/images/agent-sandbox}"
IMAGES_DIR="${VM_STORAGE_DIR}/images"
BASE_IMAGE="${IMAGES_DIR}/ubuntu-24.04-server-cloudimg-amd64.img"
DOWNLOAD_URL="${DOWNLOAD_URL:-https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img}"

mkdir -p "${IMAGES_DIR}"

if [[ -f "${BASE_IMAGE}" ]]; then
  echo "Base image already present: ${BASE_IMAGE}"
  exit 0
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "curl is required" >&2
  exit 1
fi

curl -L "${DOWNLOAD_URL}" -o "${BASE_IMAGE}"
qemu-img info "${BASE_IMAGE}" >/dev/null
echo "Downloaded base image to ${BASE_IMAGE}"
