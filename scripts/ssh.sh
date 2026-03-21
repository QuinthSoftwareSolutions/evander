#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <vm-name> [vm-user]" >&2
  exit 1
fi

VM_NAME="$1"
VM_USER="${2:-agent}"

VM_IP="$(
  virsh domifaddr "$VM_NAME" 2>/dev/null | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
)"

if [[ -z "$VM_IP" ]]; then
  echo "Could not determine an IPv4 address for VM: $VM_NAME" >&2
  exit 1
fi

ssh "$VM_USER@$VM_IP"
