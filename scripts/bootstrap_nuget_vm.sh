#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'USAGE'
Usage: bootstrap_nuget_vm.sh <vm-name> [vm-user]

Write a NuGet.Config into a guest VM that points restores at host-side relays.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH           Private key matching the guest authorized key.
  NUGET_RELAY_BASE_URL   Host relay base URL. Default: http://192.168.250.1:11666
  NUGET_FALLBACK_PACKAGES  Guest read-only fallback package folder. Default: /opt/agent-sandbox/nuget-cache-ro
  NUGET_PACKAGES_DIR       Guest writable global-packages folder. Default: /home/agent/.nuget/packages-local
  NUGET_HTTP_CACHE_DIR     Guest writable HTTP cache folder. Default: /home/agent/.nuget/http-cache
USAGE
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
VM_USER="${2:-agent}"
SSH_USER="${VM_SSH_USER:-agent-admin}"
# shellcheck source=lib/guest_ssh.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/guest_ssh.sh"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi
NUGET_RELAY_BASE_URL="${NUGET_RELAY_BASE_URL:-http://192.168.250.1:11666}"
NUGET_FALLBACK_PACKAGES="${NUGET_FALLBACK_PACKAGES:-/opt/agent-sandbox/nuget-cache-ro}"
NUGET_PACKAGES_DIR="${NUGET_PACKAGES_DIR:-/home/agent/.nuget/packages-local}"
NUGET_HTTP_CACHE_DIR="${NUGET_HTTP_CACHE_DIR:-/home/agent/.nuget/http-cache}"

SSH_OPTS=(
  -i "${SSH_KEY_PATH}"
)

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_KEY_PATH}" >&2
  echo "Set SSH_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

get_vm_ip() {
  if guest_ssh_resolve_vm_ip "${VM_NAME}"; then
    return 0
  fi

  echo "Could not determine an IPv4 address for VM ${VM_NAME}." >&2
  echo "Run this script with sudo, grant the current user libvirt access, or authorize the pkexec prompt." >&2
  return 1
}

VM_IP="$(get_vm_ip)"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_KEY_PATH}" "${CALLER_HOME}")

echo "Bootstrapping NuGet config into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_IP}" "sudo -n env NUGET_FALLBACK_PACKAGES='${NUGET_FALLBACK_PACKAGES}' bash -s" <<'EOF_VM'
set -euo pipefail

mkdir -p "${NUGET_FALLBACK_PACKAGES}"

if ! mountpoint -q "${NUGET_FALLBACK_PACKAGES}"; then
  mount "${NUGET_FALLBACK_PACKAGES}" >/dev/null 2>&1 || true
fi

if ! mountpoint -q "${NUGET_FALLBACK_PACKAGES}"; then
  for _ in {1..10}; do
    sleep 1
    mount "${NUGET_FALLBACK_PACKAGES}" >/dev/null 2>&1 || true
    if mountpoint -q "${NUGET_FALLBACK_PACKAGES}"; then
      break
    fi
  done
fi

if ! mountpoint -q "${NUGET_FALLBACK_PACKAGES}"; then
  echo "Warning: shared NuGet fallback folder is not mounted at ${NUGET_FALLBACK_PACKAGES}; restores will use relay sources only." >&2
fi
EOF_VM

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "bash -s" <<EOF_VM
set -euo pipefail

mkdir -p ~/.nuget/NuGet
mkdir -p "${NUGET_PACKAGES_DIR}"
mkdir -p "${NUGET_HTTP_CACHE_DIR}"

cat > ~/.nuget/NuGet/NuGet.Config <<CONFIG
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <config>
    <add key="globalPackagesFolder" value="${NUGET_PACKAGES_DIR}" />
    <add key="httpCacheFolder" value="${NUGET_HTTP_CACHE_DIR}" />
  </config>
  <packageSources>
    <clear />
    <add key="sandbox-nuget" value="${NUGET_RELAY_BASE_URL}/nuget/nuget-org/v3/index.json" allowInsecureConnections="true" />
    <add key="sandbox-azure" value="${NUGET_RELAY_BASE_URL}/nuget/azure/v3/index.json" allowInsecureConnections="true" />
  </packageSources>
  <fallbackPackageFolders>
    <add key="shared-readonly" value="${NUGET_FALLBACK_PACKAGES}" />
  </fallbackPackageFolders>
</configuration>
CONFIG

if command -v dotnet >/dev/null 2>&1; then
  dotnet nuget list source --configfile ~/.nuget/NuGet/NuGet.Config
fi
EOF_VM

echo "NuGet bootstrap complete for ${VM_NAME} (${VM_IP})"
