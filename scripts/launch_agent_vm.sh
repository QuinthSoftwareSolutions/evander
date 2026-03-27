#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: launch_agent_vm.sh <vm-name> <session-id>

Create an overlay disk and cloud-init seed, then boot an agent VM on the
isolated libvirt network.

Arguments:
  vm-name     libvirt domain name.
  session-id  Session manifest identifier embedded into guest config.

Environment:
  VM_STORAGE_DIR       Root directory for libvirt-accessible VM storage.
  GATEWAY_URL          Host MCP gateway URL exposed to the guest.
  SSH_PUBLIC_KEY_FILE  Public key injected into the guest.
  LLM_BASE_URL         Host OpenAI-compatible base URL exposed to the guest.
  SHARED_NUGET_CACHE_DIR  Host read-only NuGet package cache directory mounted into the guest.
  SHARED_NUGET_CACHE_TAG  Guest mount tag/path name for the shared NuGet cache. Default: agent-sandbox-nuget-cache
  SHARED_NUGET_CACHE_MODE Filesystem sharing mode: auto, virtiofs, or 9p. Default: auto
  RAM_MB               Guest memory in MB. Default: 4096
  VCPUS                Guest vCPU count. Default: 4
  DISK_SIZE_GB         Overlay virtual size in GB. Default: 20
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 2 ]]; then
  show_help >&2
  exit 1
fi

VM_NAME="$1"
SESSION_ID="$2"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${AGENT_SANDBOX_RUNTIME_DIR:-${ROOT_DIR}/runtime}"
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
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
DEFAULT_SSH_KEY_FILE="${CALLER_HOME}/.ssh/id_ed25519.pub"
if [[ ! -f "${DEFAULT_SSH_KEY_FILE}" ]]; then
  DEFAULT_SSH_KEY_FILE="${CALLER_HOME}/.ssh/id_rsa.pub"
fi
SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE:-${DEFAULT_SSH_KEY_FILE}}"
SHARED_NUGET_CACHE_DIR="${SHARED_NUGET_CACHE_DIR:-${VM_STORAGE_DIR}/nuget-cache/global-packages}"
SHARED_NUGET_CACHE_TAG="${SHARED_NUGET_CACHE_TAG:-agent-sandbox-nuget-cache}"
SHARED_NUGET_CACHE_MODE="${SHARED_NUGET_CACHE_MODE:-auto}"
GUEST_NUGET_FALLBACK_PACKAGES="/opt/agent-sandbox/nuget-cache-ro"
GUEST_NUGET_PACKAGES="/home/agent/.nuget/packages-local"
GUEST_NUGET_HTTP_CACHE_PATH="/home/agent/.nuget/http-cache"
RAM_MB="${RAM_MB:-4096}"
VCPUS="${VCPUS:-4}"
DISK_SIZE_GB="${DISK_SIZE_GB:-20}"

resolve_vm_defaults_exports() {
  local python_bin="${ROOT_DIR}/.venv/bin/python"
  if [[ ! -x "${python_bin}" ]]; then
    python_bin="python3"
  fi
  PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" \
    "${python_bin}" - "${RUNTIME_DIR}" "${SESSION_ID}" <<'PY'
import shlex
import sys

from agent_sandbox.bootstrap import load_runtime_gateway_config
from agent_sandbox.vm_defaults_config import VmDefaultsConfigResolver

runtime_dir, session_id = sys.argv[1], sys.argv[2]
resolver = VmDefaultsConfigResolver(load_runtime_gateway_config(runtime_dir=runtime_dir))
try:
    vm_defaults = resolver.resolve_for_session_id(session_id)
except ValueError:
    vm_defaults = resolver.resolve_for_directory("root")
for key, value in sorted(vm_defaults.guest_environment().items()):
    print(f"export {key}={shlex.quote(value)}")
PY
}

if [[ -z "${GATEWAY_URL:-}" || -z "${LLM_BASE_URL:-}" ]]; then
  if VM_DEFAULTS_EXPORTS="$(resolve_vm_defaults_exports 2>/dev/null)"; then
    eval "${VM_DEFAULTS_EXPORTS}"
  fi
fi

GATEWAY_URL="${GATEWAY_URL:-http://192.168.250.1:8000/mcp}"
LLM_BASE_URL="${LLM_BASE_URL:-http://192.168.250.1:11434/v1}"

mkdir -p "${VM_DIR}"
mkdir -p "${STATE_DIR}"
mkdir -p "${SHARED_NUGET_CACHE_DIR}"
chmod 0755 "${SHARED_NUGET_CACHE_DIR}"

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
ESCAPED_NUGET_FALLBACK_PACKAGES="${GUEST_NUGET_FALLBACK_PACKAGES//&/\\&}"
ESCAPED_NUGET_FALLBACK_PACKAGES="${ESCAPED_NUGET_FALLBACK_PACKAGES//|/\\|}"
ESCAPED_NUGET_PACKAGES="${GUEST_NUGET_PACKAGES//&/\\&}"
ESCAPED_NUGET_PACKAGES="${ESCAPED_NUGET_PACKAGES//|/\\|}"
ESCAPED_NUGET_HTTP_CACHE_PATH="${GUEST_NUGET_HTTP_CACHE_PATH//&/\\&}"
ESCAPED_NUGET_HTTP_CACHE_PATH="${ESCAPED_NUGET_HTTP_CACHE_PATH//|/\\|}"

SHARED_FS_TYPE=""
SHARED_FS_MOUNT_OPTIONS=""
VIRT_INSTALL_FILESYSTEM_ARG=""
VIRT_INSTALL_MEMORYBACKING_ARGS=()
case "${SHARED_NUGET_CACHE_MODE}" in
  auto)
    if command -v virtiofsd >/dev/null 2>&1 || [[ -x /usr/libexec/virtiofsd ]] || [[ -x /usr/lib/qemu/virtiofsd ]]; then
      SHARED_FS_TYPE="virtiofs"
      SHARED_FS_MOUNT_OPTIONS="ro,nofail"
      VIRT_INSTALL_FILESYSTEM_ARG="source=${SHARED_NUGET_CACHE_DIR},target=${SHARED_NUGET_CACHE_TAG},driver.type=virtiofs"
      VIRT_INSTALL_MEMORYBACKING_ARGS=(--memorybacking access.mode=shared)
    else
      SHARED_FS_TYPE="9p"
      SHARED_FS_MOUNT_OPTIONS="ro,trans=virtio,version=9p2000.L,nofail"
      VIRT_INSTALL_FILESYSTEM_ARG="source=${SHARED_NUGET_CACHE_DIR},target=${SHARED_NUGET_CACHE_TAG},type=mount,accessmode=mapped"
    fi
    ;;
  virtiofs)
    SHARED_FS_TYPE="virtiofs"
    SHARED_FS_MOUNT_OPTIONS="ro,nofail"
    VIRT_INSTALL_FILESYSTEM_ARG="source=${SHARED_NUGET_CACHE_DIR},target=${SHARED_NUGET_CACHE_TAG},driver.type=virtiofs"
    VIRT_INSTALL_MEMORYBACKING_ARGS=(--memorybacking access.mode=shared)
    ;;
  9p)
    SHARED_FS_TYPE="9p"
    SHARED_FS_MOUNT_OPTIONS="ro,trans=virtio,version=9p2000.L,nofail"
    VIRT_INSTALL_FILESYSTEM_ARG="source=${SHARED_NUGET_CACHE_DIR},target=${SHARED_NUGET_CACHE_TAG},type=mount,accessmode=mapped"
    ;;
  *)
    echo "Unsupported SHARED_NUGET_CACHE_MODE: ${SHARED_NUGET_CACHE_MODE}" >&2
    exit 1
    ;;
esac

ESCAPED_NUGET_CACHE_TAG="${SHARED_NUGET_CACHE_TAG//&/\\&}"
ESCAPED_NUGET_CACHE_TAG="${ESCAPED_NUGET_CACHE_TAG//|/\\|}"
ESCAPED_NUGET_CACHE_FS_TYPE="${SHARED_FS_TYPE//&/\\&}"
ESCAPED_NUGET_CACHE_FS_TYPE="${ESCAPED_NUGET_CACHE_FS_TYPE//|/\\|}"
ESCAPED_NUGET_CACHE_MOUNT_OPTIONS="${SHARED_FS_MOUNT_OPTIONS//&/\\&}"
ESCAPED_NUGET_CACHE_MOUNT_OPTIONS="${ESCAPED_NUGET_CACHE_MOUNT_OPTIONS//|/\\|}"

sed \
  -e "s|__GATEWAY_URL__|${GATEWAY_URL}|g" \
  -e "s|__SESSION_ID__|${SESSION_ID}|g" \
  -e "s|__SSH_PUBLIC_KEY__|${ESCAPED_SSH_PUBLIC_KEY}|g" \
  -e "s|__LLM_BASE_URL__|${ESCAPED_LLM_BASE_URL}|g" \
  -e "s|__NUGET_FALLBACK_PACKAGES__|${ESCAPED_NUGET_FALLBACK_PACKAGES}|g" \
  -e "s|__NUGET_PACKAGES__|${ESCAPED_NUGET_PACKAGES}|g" \
  -e "s|__NUGET_HTTP_CACHE_PATH__|${ESCAPED_NUGET_HTTP_CACHE_PATH}|g" \
  -e "s|__NUGET_CACHE_TAG__|${ESCAPED_NUGET_CACHE_TAG}|g" \
  -e "s|__NUGET_CACHE_FS_TYPE__|${ESCAPED_NUGET_CACHE_FS_TYPE}|g" \
  -e "s|__NUGET_CACHE_MOUNT_OPTIONS__|${ESCAPED_NUGET_CACHE_MOUNT_OPTIONS}|g" \
  "${USER_DATA_TEMPLATE}" > "${USER_DATA_RENDERED}"

printf 'instance-id: %s\nlocal-hostname: %s\n' "${VM_NAME}" "${VM_NAME}" > "${META_DATA}"

qemu-img create -f qcow2 -F qcow2 -b "${BASE_IMAGE}" "${OVERLAY_IMAGE}" >/dev/null
qemu-img resize "${OVERLAY_IMAGE}" "${DISK_SIZE_GB}G" >/dev/null
cloud-localds "${SEED_IMAGE}" "${USER_DATA_RENDERED}" "${META_DATA}"

virt-install \
  --name "${VM_NAME}" \
  --memory "${RAM_MB}" \
  "${VIRT_INSTALL_MEMORYBACKING_ARGS[@]}" \
  --vcpus "${VCPUS}" \
  --import \
  --disk "path=${OVERLAY_IMAGE},format=qcow2,bus=virtio" \
  --disk "path=${SEED_IMAGE},device=cdrom" \
  --os-variant ubuntu24.04 \
  --network network=agent-sandbox,model=virtio \
  --filesystem "${VIRT_INSTALL_FILESYSTEM_ARG}" \
  --graphics none \
  --noautoconsole \
  --rng /dev/urandom

echo "Launched VM ${VM_NAME} for session ${SESSION_ID}"
