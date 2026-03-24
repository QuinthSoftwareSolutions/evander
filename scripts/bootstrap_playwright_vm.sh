#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_playwright_vm.sh <vm-name> [vm-user]

Copy a host-seeded Node + Playwright runtime into a guest VM and configure the
guest shell environment to use it offline.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH                 Private key matching the guest authorized key.
  HOST_NODE_ROOT               Host Node runtime to copy.
  HOST_PLAYWRIGHT_NODE_MODULES Host node_modules directory containing Playwright.
  HOST_PLAYWRIGHT_BROWSERS     Host Playwright browser cache.
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
VM_USER="${2:-agent}"
# shellcheck source=lib/guest_ssh.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/guest_ssh.sh"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

HOST_NODE_ROOT="${HOST_NODE_ROOT:-${CALLER_HOME}/.nvm/versions/node/v24.14.0}"
HOST_PLAYWRIGHT_NODE_MODULES="${HOST_PLAYWRIGHT_NODE_MODULES:-${CALLER_HOME}/node_modules}"
HOST_PLAYWRIGHT_BROWSERS="${HOST_PLAYWRIGHT_BROWSERS:-${CALLER_HOME}/.cache/ms-playwright}"
SSH_OPTS=(
  -i "${SSH_KEY_PATH}"
)

if [[ ! -d "${HOST_NODE_ROOT}" ]]; then
  echo "Missing host Node runtime: ${HOST_NODE_ROOT}" >&2
  exit 1
fi

if [[ ! -d "${HOST_PLAYWRIGHT_NODE_MODULES}/playwright" ]]; then
  echo "Missing host Playwright package: ${HOST_PLAYWRIGHT_NODE_MODULES}/playwright" >&2
  exit 1
fi

if [[ ! -d "${HOST_PLAYWRIGHT_BROWSERS}" ]]; then
  echo "Missing host Playwright browser cache: ${HOST_PLAYWRIGHT_BROWSERS}" >&2
  exit 1
fi

get_vm_ip() {
  local ip

  ip="$(
    virsh domifaddr "${VM_NAME}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  ip="$(
    sudo -n virsh domifaddr "${VM_NAME}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  echo "Could not determine an IPv4 address for VM ${VM_NAME}." >&2
  echo "Run this script with sudo, or grant the current user libvirt access." >&2
  return 1
}

VM_IP="$(get_vm_ip)"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_KEY_PATH}" "${CALLER_HOME}")

echo "Bootstrapping Playwright into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  sudo mkdir -p /home/${VM_USER}
  sudo chown -R ${VM_USER}:${VM_USER} /home/${VM_USER}
  mkdir -p ~/toolchains ~/playwright-runtime/node_modules ~/.cache
"

scp "${SSH_OPTS[@]}" -r "${HOST_NODE_ROOT}" "${VM_USER}@${VM_IP}:~/toolchains/node-v24.14.0"
scp "${SSH_OPTS[@]}" -r "${HOST_PLAYWRIGHT_NODE_MODULES}/playwright" "${VM_USER}@${VM_IP}:~/playwright-runtime/node_modules/"
scp "${SSH_OPTS[@]}" -r "${HOST_PLAYWRIGHT_NODE_MODULES}/playwright-core" "${VM_USER}@${VM_IP}:~/playwright-runtime/node_modules/"
scp "${SSH_OPTS[@]}" -r "${HOST_PLAYWRIGHT_BROWSERS}" "${VM_USER}@${VM_IP}:~/.cache/ms-playwright"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "bash -s" <<'EOF'
set -euo pipefail

if ! grep -q 'toolchains/node-v24.14.0/bin' ~/.profile 2>/dev/null; then
  cat >> ~/.profile <<PROFILE
export PATH="$HOME/toolchains/node-v24.14.0/bin:$PATH"
export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"
export NODE_PATH="$HOME/playwright-runtime/node_modules"
PROFILE
fi

export PATH="$HOME/toolchains/node-v24.14.0/bin:$PATH"
export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"
export NODE_PATH="$HOME/playwright-runtime/node_modules"

node --version
node -e 'const { chromium } = require("playwright"); console.log(chromium.executablePath());'
EOF

echo "Playwright bootstrap complete for ${VM_NAME} (${VM_IP})"
