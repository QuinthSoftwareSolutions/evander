#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'USAGE'
Usage: bootstrap_mssql_vm.sh <vm-name> [vm-user]

Copy host-installed Microsoft SQL Server client tooling into a guest VM and
configure the guest shell to use it offline.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH           Private key matching the guest authorized key.
  HOST_MSSQL_TOOLS_ROOT  Host SQL Server client tooling root.
                         Default: /opt/mssql-tools18
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
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

HOST_MSSQL_TOOLS_ROOT="${HOST_MSSQL_TOOLS_ROOT:-/opt/mssql-tools18}"
SSH_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -i "${SSH_KEY_PATH}"
)

if [[ ! -d "${HOST_MSSQL_TOOLS_ROOT}" ]]; then
  echo "Missing host SQL Server tooling root: ${HOST_MSSQL_TOOLS_ROOT}" >&2
  exit 1
fi

if [[ ! -x "${HOST_MSSQL_TOOLS_ROOT}/bin/sqlcmd" ]]; then
  echo "Missing sqlcmd launcher under ${HOST_MSSQL_TOOLS_ROOT}/bin" >&2
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

echo "Bootstrapping SQL Server client tooling into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  sudo mkdir -p /home/${VM_USER}
  sudo chown -R ${VM_USER}:${VM_USER} /home/${VM_USER}
  mkdir -p ~/toolchains/mssql-tools
  rm -rf ~/toolchains/mssql-tools/*
"

tar -C "${HOST_MSSQL_TOOLS_ROOT}" -cf - . \
  | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "tar -C ~/toolchains/mssql-tools -xf -"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "bash -s" <<'EOF_VM'
set -euo pipefail

if [[ ! -x "$HOME/toolchains/mssql-tools/bin/sqlcmd" ]]; then
  echo "Expected sqlcmd launcher missing at $HOME/toolchains/mssql-tools/bin/sqlcmd" >&2
  exit 1
fi

if ! grep -q 'PATH="$HOME/toolchains/mssql-tools/bin:$PATH"' ~/.profile 2>/dev/null; then
  cat >> ~/.profile <<PROFILE
export PATH="$HOME/toolchains/mssql-tools/bin:$PATH"
PROFILE
fi

if ! grep -q 'PATH="$HOME/toolchains/mssql-tools/bin:$PATH"' ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc <<BASHRC
export PATH="$HOME/toolchains/mssql-tools/bin:$PATH"
BASHRC
fi

export PATH="$HOME/toolchains/mssql-tools/bin:$PATH"
sqlcmd -? >/dev/null
EOF_VM

echo "SQL Server bootstrap complete for ${VM_NAME} (${VM_IP})"
