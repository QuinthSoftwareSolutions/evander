#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_dotnet_vm.sh <vm-name> [vm-user]

Copy a host-installed .NET SDK into a guest VM and configure the guest shell to
use it offline.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH      Private key matching the guest authorized key.
  HOST_DOTNET_ROOT  Host .NET installation root. Default: /usr/lib/dotnet
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
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

HOST_DOTNET_ROOT="${HOST_DOTNET_ROOT:-/usr/lib/dotnet}"
SSH_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -i "${SSH_KEY_PATH}"
)

if [[ ! -d "${HOST_DOTNET_ROOT}" ]]; then
  echo "Missing host .NET installation: ${HOST_DOTNET_ROOT}" >&2
  exit 1
fi

if [[ ! -x "${HOST_DOTNET_ROOT}/dotnet" ]]; then
  echo "Missing dotnet launcher under ${HOST_DOTNET_ROOT}" >&2
  exit 1
fi

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_KEY_PATH}" >&2
  echo "Set SSH_KEY_PATH to the private key that matches the VM's authorized key." >&2
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

echo "Bootstrapping .NET into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  sudo mkdir -p /home/${VM_USER}
  sudo chown -R ${VM_USER}:${VM_USER} /home/${VM_USER}
  mkdir -p ~/toolchains/dotnet ~/.dotnet-cli
  rm -rf ~/toolchains/dotnet/*
"

tar -C "${HOST_DOTNET_ROOT}" -cf - . \
  | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "tar -C ~/toolchains/dotnet -xf -"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "bash -s" <<'EOF'
set -euo pipefail

if [[ ! -x "$HOME/toolchains/dotnet/dotnet" ]]; then
  echo "Expected dotnet launcher missing at $HOME/toolchains/dotnet/dotnet" >&2
  exit 1
fi

if ! grep -q 'DOTNET_ROOT="$HOME/toolchains/dotnet"' ~/.profile 2>/dev/null; then
  cat >> ~/.profile <<PROFILE
export DOTNET_ROOT="$HOME/toolchains/dotnet"
export DOTNET_CLI_HOME="$HOME/.dotnet-cli"
export PATH="$HOME/toolchains/dotnet:$PATH"
PROFILE
fi

if ! grep -q 'DOTNET_ROOT="$HOME/toolchains/dotnet"' ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc <<BASHRC
export DOTNET_ROOT="$HOME/toolchains/dotnet"
export DOTNET_CLI_HOME="$HOME/.dotnet-cli"
export PATH="$HOME/toolchains/dotnet:$PATH"
BASHRC
fi

export DOTNET_ROOT="$HOME/toolchains/dotnet"
export DOTNET_CLI_HOME="$HOME/.dotnet-cli"
export PATH="$HOME/toolchains/dotnet:$PATH"

dotnet --info
EOF

echo ".NET bootstrap complete for ${VM_NAME} (${VM_IP})"
