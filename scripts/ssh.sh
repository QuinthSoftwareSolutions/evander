#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <vm-name> [vm-user]" >&2
  exit 1
fi

VM_NAME="$1"
VM_USER="${2:-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/guest_ssh.sh
source "${SCRIPT_DIR}/lib/guest_ssh.sh"
CALLER_USER="${SUDO_USER:-${USER:-}}"
CALLER_HOME="${HOME:-}"

if [[ -n "${CALLER_USER}" ]]; then
  CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6 || printf '%s\n' "${CALLER_HOME}")"
fi

SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_PRIVATE_KEY_PATH}" >&2
  exit 1
fi

VM_IP="$(
  sudo -n virsh domifaddr "$VM_NAME" 2>/dev/null | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
)"

if [[ -z "$VM_IP" ]]; then
  VM_IP="$(
    virsh domifaddr "$VM_NAME" 2>/dev/null | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
fi

if [[ -z "$VM_IP" ]]; then
  echo "Could not determine an IPv4 address for VM: $VM_NAME" >&2
  exit 1
fi

mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_PRIVATE_KEY_PATH}" "${CALLER_HOME}")
exec ssh "${SSH_OPTS[@]}" "$VM_USER@$VM_IP"
