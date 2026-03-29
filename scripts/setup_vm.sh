#!/usr/bin/env bash
set -Eeuo pipefail

CURRENT_STEP="startup"

log_step() {
  CURRENT_STEP="$1"
  echo "==> ${CURRENT_STEP}"
}

on_error() {
  local exit_code="$?"
  local line_no="${1:-?}"
  echo "ERROR: setup_vm.sh failed at step '${CURRENT_STEP}' (line ${line_no})" >&2
  echo "COMMAND: ${BASH_COMMAND}" >&2
  exit "${exit_code}"
}

trap 'on_error ${LINENO}' ERR

show_help() {
  cat <<'EOF'
Usage: setup_vm.sh [--dotnet] [--nuget] [--mssql [--mssql-init-root PATH]] [--playwright] <vm-name> <session-id> [vm-user]

Run the shared standalone-VM bootstrap flow:
  1. launch the VM
  2. wait for an IPv4 address
  3. wait for guest SSH readiness
  4. bootstrap Codex and selected optional guest toolchains

Arguments:
  vm-name     libvirt domain name.
  session-id  Session identifier passed through to launch_agent_vm.sh.
  vm-user     Guest user to configure. Default: agent

Environment:
  SSH_PUBLIC_KEY_FILE         Public key injected into the guest.
  SSH_PRIVATE_KEY_PATH        Private key matching the guest authorized key.
  VM_WAIT_IP_TIMEOUT_SECONDS  How long to wait for the guest to receive an IPv4 address.
                              Default: 180
  VM_WAIT_SSH_TIMEOUT_SECONDS How long to wait for guest SSH to accept connections.
                              Default: 180
  MSSQL_SA_PASSWORD           Required when --mssql is enabled.

Optional guest toolchains:
  --dotnet            Bootstrap the host .NET SDK into the guest.
  --nuget             Bootstrap NuGet relay configuration into the guest.
  --mssql             Bootstrap a guest-local Microsoft SQL Server instance into the guest.
  --mssql-init-root PATH
                      Host directory copied into /var/opt/mssql/init. If present,
                      init.sql from that directory is executed after SQL Server starts.
  --playwright        Bootstrap the host-seeded Node + Playwright runtime into the guest.
EOF
}

BOOTSTRAP_DOTNET=0
BOOTSTRAP_NUGET=0
BOOTSTRAP_MSSQL=0
BOOTSTRAP_PLAYWRIGHT=0
MSSQL_INIT_ROOT="${AGENT_SANDBOX_MSSQL_INIT_ROOT:-}"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dotnet)
      BOOTSTRAP_DOTNET=1
      shift
      ;;
    --nuget)
      BOOTSTRAP_NUGET=1
      shift
      ;;
    --mssql)
      BOOTSTRAP_MSSQL=1
      shift
      ;;
    --mssql-init-root)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      MSSQL_INIT_ROOT="$2"
      shift 2
      ;;
    --playwright)
      BOOTSTRAP_PLAYWRIGHT=1
      shift
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      show_help >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -lt 2 ]]; then
  show_help >&2
  exit 1
fi

VM_NAME="$1"
SESSION_ID="$2"
VM_USER="${3:-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CALLER_USER="${SUDO_USER:-}"
if [[ -z "${CALLER_USER}" && -n "${PKEXEC_UID:-}" ]]; then
  CALLER_USER="$(getent passwd "${PKEXEC_UID}" | cut -d: -f1)"
fi
CALLER_USER="${CALLER_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE:-${CALLER_HOME}/.ssh/id_ed25519.pub}"
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
VM_WAIT_IP_TIMEOUT_SECONDS="${VM_WAIT_IP_TIMEOUT_SECONDS:-180}"
VM_WAIT_SSH_TIMEOUT_SECONDS="${VM_WAIT_SSH_TIMEOUT_SECONDS:-180}"

if [[ ! -f "${SSH_PUBLIC_KEY_FILE}" ]]; then
  SSH_PUBLIC_KEY_FILE="${CALLER_HOME}/.ssh/id_rsa.pub"
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

if [[ ! -f "${SSH_PUBLIC_KEY_FILE}" ]]; then
  if [[ -f "${SSH_PRIVATE_KEY_PATH}" ]] && command -v ssh-keygen >/dev/null 2>&1; then
    DERIVED_PUBLIC_KEY_FILE="$(mktemp)"
    trap 'rm -f "${DERIVED_PUBLIC_KEY_FILE:-}"' EXIT
    ssh-keygen -y -f "${SSH_PRIVATE_KEY_PATH}" > "${DERIVED_PUBLIC_KEY_FILE}"
    SSH_PUBLIC_KEY_FILE="${DERIVED_PUBLIC_KEY_FILE}"
  else
    echo "Missing SSH public key: ${SSH_PUBLIC_KEY_FILE}" >&2
    echo "Set SSH_PUBLIC_KEY_FILE to a readable public key before running this helper." >&2
    echo "Alternatively set SSH_PRIVATE_KEY_PATH to derive the public key automatically." >&2
    exit 1
  fi
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_PRIVATE_KEY_PATH}" >&2
  echo "Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

export SUDO_USER="${CALLER_USER}"

if (( BOOTSTRAP_MSSQL )); then
  if [[ -z "${MSSQL_SA_PASSWORD:-}" ]]; then
    echo "MSSQL_SA_PASSWORD is required when --mssql is enabled." >&2
    exit 1
  fi
  if [[ -n "${MSSQL_INIT_ROOT}" && ! -d "${MSSQL_INIT_ROOT}" ]]; then
    echo "Missing MSSQL init root: ${MSSQL_INIT_ROOT}" >&2
    exit 1
  fi
fi

if sudo -n virsh dominfo "${VM_NAME}" >/dev/null 2>&1 || virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
  echo "VM already exists: ${VM_NAME}" >&2
  echo "Destroy it first with: sudo ./scripts/destroy_agent_vm.sh ${VM_NAME}" >&2
  exit 1
fi

# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"

run_launch_vm() {
  local -a env_args=(
    "SSH_PUBLIC_KEY_FILE=${SSH_PUBLIC_KEY_FILE}"
    "GATEWAY_URL=${GATEWAY_URL:-}"
    "LLM_BASE_URL=${LLM_BASE_URL:-}"
    "AGENT_SANDBOX_RUNTIME_DIR=${AGENT_SANDBOX_RUNTIME_DIR:-}"
    "VM_STORAGE_DIR=${VM_STORAGE_DIR:-}"
    "SHARED_NUGET_CACHE_DIR=${SHARED_NUGET_CACHE_DIR:-}"
    "SHARED_NUGET_CACHE_TAG=${SHARED_NUGET_CACHE_TAG:-}"
    "SHARED_NUGET_CACHE_MODE=${SHARED_NUGET_CACHE_MODE:-}"
    "RAM_MB=${RAM_MB:-}"
    "VCPUS=${VCPUS:-}"
    "DISK_SIZE_GB=${DISK_SIZE_GB:-}"
  )
  if [[ ${EUID} -eq 0 ]]; then
    env "${env_args[@]}" "${SCRIPT_DIR}/launch_agent_vm.sh" "${VM_NAME}" "${SESSION_ID}"
  else
    sudo env "${env_args[@]}" "${SCRIPT_DIR}/launch_agent_vm.sh" "${VM_NAME}" "${SESSION_ID}"
  fi
}

log_step "launch_vm"
run_launch_vm
log_step "wait_for_ip"
VM_IP="$(vm_network_wait_for_ip "${VM_NAME}" "${VM_WAIT_IP_TIMEOUT_SECONDS}")"
echo "Guest IPv4 detected: ${VM_IP}"
log_step "wait_for_ssh"
vm_network_wait_for_ssh "${VM_NAME}" "${VM_IP}" "${VM_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_WAIT_SSH_TIMEOUT_SECONDS}"
echo "Guest SSH is ready on ${VM_USER}@${VM_IP}"
log_step "bootstrap_codex"
"${SCRIPT_DIR}/bootstrap_codex_vm.sh" "${VM_NAME}" "${VM_USER}"
if (( BOOTSTRAP_DOTNET )); then
  log_step "bootstrap_dotnet"
  "${SCRIPT_DIR}/bootstrap_dotnet_vm.sh" "${VM_NAME}" "${VM_USER}"
fi
if (( BOOTSTRAP_NUGET )); then
  log_step "bootstrap_nuget"
  "${SCRIPT_DIR}/bootstrap_nuget_vm.sh" "${VM_NAME}" "${VM_USER}"
fi
if (( BOOTSTRAP_MSSQL )); then
  log_step "bootstrap_mssql"
  MSSQL_SA_PASSWORD="${MSSQL_SA_PASSWORD}" \
    MSSQL_INIT_ROOT="${MSSQL_INIT_ROOT}" \
    "${SCRIPT_DIR}/bootstrap_mssql_vm.sh" "${VM_NAME}" "${VM_USER}"
fi
if (( BOOTSTRAP_PLAYWRIGHT )); then
  log_step "bootstrap_playwright"
  "${SCRIPT_DIR}/bootstrap_playwright_vm.sh" "${VM_NAME}" "${VM_USER}"
fi

echo "Standalone VM setup complete for ${VM_NAME}"
