#!/usr/bin/env bash

# shellcheck source=lib/guest_ssh.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/guest_ssh.sh"

vm_network_domain_state() {
  local vm_name="$1"
  local state

  state="$(
    sudo -n virsh dominfo "${vm_name}" 2>/dev/null \
      | awk -F': *' '/^State:/ {print $2; exit}'
  )"
  if [[ -n "${state}" ]]; then
    printf '%s\n' "${state}"
    return 0
  fi

  state="$(
    virsh dominfo "${vm_name}" 2>/dev/null \
      | awk -F': *' '/^State:/ {print $2; exit}'
  )"
  if [[ -n "${state}" ]]; then
    printf '%s\n' "${state}"
    return 0
  fi

  return 1
}

vm_network_vm_exists() {
  local vm_name="$1"

  if vm_network_domain_state "${vm_name}" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

vm_network_start() {
  local vm_name="$1"

  if sudo -n virsh start "${vm_name}" >/dev/null 2>&1; then
    return 0
  fi

  if virsh start "${vm_name}" >/dev/null 2>&1; then
    return 0
  fi

  return 1
}

vm_network_resolve_ip() {
  local vm_name="$1"
  local ip

  ip="$(
    sudo -n virsh domifaddr "${vm_name}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  ip="$(
    virsh domifaddr "${vm_name}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  return 1
}

vm_network_wait_for_ip() {
  local vm_name="$1"
  local timeout_seconds="$2"
  local deadline ip

  deadline=$((SECONDS + timeout_seconds))

  while (( SECONDS < deadline )); do
    if ip="$(vm_network_resolve_ip "${vm_name}")"; then
      printf '%s\n' "${ip}"
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for VM '${vm_name}' to receive an IPv4 address after ${timeout_seconds}s." >&2
  echo "Check guest boot progress with: sudo virsh console ${vm_name}" >&2
  return 1
}

vm_network_wait_for_ssh() {
  local vm_name="$1"
  local vm_ip="$2"
  local vm_user="$3"
  local ssh_private_key_path="$4"
  local timeout_seconds="$5"
  local deadline
  local caller_user
  local caller_home
  local -a ssh_opts

  caller_user="${SUDO_USER:-${USER:-}}"
  caller_home="${HOME:-}"
  if [[ -n "${caller_user}" ]]; then
    caller_home="$(getent passwd "${caller_user}" | cut -d: -f6 || printf '%s\n' "${caller_home}")"
  fi

  deadline=$((SECONDS + timeout_seconds))

  while (( SECONDS < deadline )); do
    mapfile -t ssh_opts < <(guest_ssh_options "${vm_name}" "${vm_ip}" "${ssh_private_key_path}" "${caller_home}")
    ssh_opts+=(-o ConnectTimeout=5)
    if ssh \
      "${ssh_opts[@]}" \
      "${vm_user}@${vm_ip}" true >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  echo "Timed out waiting for SSH on '${vm_user}@${vm_ip}' after ${timeout_seconds}s (VM '${vm_name}')." >&2
  echo "Check guest boot progress with: sudo virsh console ${vm_name}" >&2
  return 1
}
