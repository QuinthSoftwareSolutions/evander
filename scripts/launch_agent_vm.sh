#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "usage: $0 <vm-name> <session-id>" >&2
  exit 1
fi

VM_NAME="$1"
SESSION_ID="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${ROOT_DIR}/runtime"
VM_STORAGE_DIR="${VM_STORAGE_DIR:-/var/lib/libvirt/images/agent-sandbox}"
IMAGES_DIR="${VM_STORAGE_DIR}/images"
VM_DIR="${VM_STORAGE_DIR}/vms/${VM_NAME}"
STATE_DIR="${RUNTIME_DIR}/vms/${VM_NAME}"
BASE_IMAGE="${IMAGES_DIR}/ubuntu-24.04-server-cloudimg-amd64.img"
OVERLAY_IMAGE="${VM_DIR}/disk.qcow2"
SEED_IMAGE="${VM_DIR}/seed.iso"
USER_DATA_TEMPLATE="${ROOT_DIR}/config/cloud-init-user-data.yaml"
USER_DATA_RENDERED="${STATE_DIR}/user-data"
META_DATA="${STATE_DIR}/meta-data"
GATEWAY_URL="${GATEWAY_URL:-http://192.168.250.1:8000/mcp}"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
DEFAULT_SSH_KEY_FILE="${CALLER_HOME}/.ssh/id_ed25519.pub"
if [[ ! -f "${DEFAULT_SSH_KEY_FILE}" ]]; then
  DEFAULT_SSH_KEY_FILE="${CALLER_HOME}/.ssh/id_rsa.pub"
fi
SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE:-${DEFAULT_SSH_KEY_FILE}}"
LLM_BASE_URL="${LLM_BASE_URL:-http://192.168.250.1:11434/v1}"
RAM_MB="${RAM_MB:-4096}"
VCPUS="${VCPUS:-4}"
DISK_SIZE_GB="${DISK_SIZE_GB:-20}"

mkdir -p "${VM_DIR}"
mkdir -p "${STATE_DIR}"

if ! command -v virt-install >/dev/null 2>&1; then
  echo "virt-install is required" >&2
  exit 1
fi

if [[ ! -f "${BASE_IMAGE}" ]]; then
  echo "Missing base image: ${BASE_IMAGE}" >&2
  exit 1
fi

if [[ ! -f "${SSH_PUBLIC_KEY_FILE}" ]]; then
  echo "Missing SSH public key: ${SSH_PUBLIC_KEY_FILE}" >&2
  echo "Set SSH_PUBLIC_KEY_FILE to a readable public key before launching the VM." >&2
  exit 1
fi

if [[ ! -w "${VM_DIR}" ]]; then
  echo "VM storage directory is not writable: ${VM_DIR}" >&2
  echo "Run this script with sudo, or set VM_STORAGE_DIR to a libvirt-accessible writable path." >&2
  exit 1
fi

SSH_PUBLIC_KEY="$(tr -d '\n' < "${SSH_PUBLIC_KEY_FILE}")"
ESCAPED_SSH_PUBLIC_KEY="${SSH_PUBLIC_KEY//&/\\&}"
ESCAPED_SSH_PUBLIC_KEY="${ESCAPED_SSH_PUBLIC_KEY//|/\\|}"
ESCAPED_LLM_BASE_URL="${LLM_BASE_URL//&/\\&}"
ESCAPED_LLM_BASE_URL="${ESCAPED_LLM_BASE_URL//|/\\|}"

sed \
  -e "s|__GATEWAY_URL__|${GATEWAY_URL}|g" \
  -e "s|__SESSION_ID__|${SESSION_ID}|g" \
  -e "s|__SSH_PUBLIC_KEY__|${ESCAPED_SSH_PUBLIC_KEY}|g" \
  -e "s|__LLM_BASE_URL__|${ESCAPED_LLM_BASE_URL}|g" \
  "${USER_DATA_TEMPLATE}" > "${USER_DATA_RENDERED}"

printf 'instance-id: %s\nlocal-hostname: %s\n' "${VM_NAME}" "${VM_NAME}" > "${META_DATA}"

qemu-img create -f qcow2 -F qcow2 -b "${BASE_IMAGE}" "${OVERLAY_IMAGE}" >/dev/null
qemu-img resize "${OVERLAY_IMAGE}" "${DISK_SIZE_GB}G" >/dev/null
cloud-localds "${SEED_IMAGE}" "${USER_DATA_RENDERED}" "${META_DATA}"

virt-install \
  --name "${VM_NAME}" \
  --memory "${RAM_MB}" \
  --vcpus "${VCPUS}" \
  --import \
  --disk "path=${OVERLAY_IMAGE},format=qcow2,bus=virtio" \
  --disk "path=${SEED_IMAGE},device=cdrom" \
  --os-variant ubuntu24.04 \
  --network network=agent-sandbox,model=virtio \
  --graphics none \
  --noautoconsole \
  --rng /dev/urandom

echo "Launched VM ${VM_NAME} for session ${SESSION_ID}"
