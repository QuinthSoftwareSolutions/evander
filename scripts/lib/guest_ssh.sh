#!/usr/bin/env bash

guest_ssh_known_hosts_path() {
  local caller_home="${1:-${HOME:-}}"
  local default_path="${caller_home}/.ssh/agent-sandbox-known_hosts"
  local default_parent
  local fallback_user
  local fallback_path

  if [[ -n "${AGENT_SANDBOX_GUEST_KNOWN_HOSTS_FILE:-}" ]]; then
    printf '%s\n' "${AGENT_SANDBOX_GUEST_KNOWN_HOSTS_FILE}"
    return 0
  fi

  default_parent="$(dirname "${default_path}")"
  if [[ -e "${default_path}" ]]; then
    if [[ -w "${default_path}" ]]; then
      printf '%s\n' "${default_path}"
      return 0
    fi
  elif [[ -d "${default_parent}" && -w "${default_parent}" ]]; then
    printf '%s\n' "${default_path}"
    return 0
  fi

  fallback_user="${SUDO_USER:-${USER:-agent}}"
  fallback_path="/tmp/agent-sandbox-known_hosts-${fallback_user}"
  printf '%s\n' "${fallback_path}"
}

guest_ssh_ensure_known_hosts_file() {
  local caller_home="${1:-${HOME:-}}"
  local known_hosts_path

  known_hosts_path="$(guest_ssh_known_hosts_path "${caller_home}")"
  mkdir -p "$(dirname "${known_hosts_path}")"
  touch "${known_hosts_path}"
  chmod 600 "${known_hosts_path}"
}

guest_ssh_remove_known_host() {
  local vm_name="$1"
  local caller_home="${2:-${HOME:-}}"
  local known_hosts_path

  known_hosts_path="$(guest_ssh_known_hosts_path "${caller_home}")"
  guest_ssh_ensure_known_hosts_file "${caller_home}"
  ssh-keygen -R "${vm_name}" -f "${known_hosts_path}" >/dev/null 2>&1 || true
}

guest_ssh_prepare_known_host() {
  local vm_name="$1"
  local vm_ip="$2"
  local caller_home="${3:-${HOME:-}}"
  local known_hosts_path
  local scan_timeout_seconds
  local scan_output
  local found_existing=1
  local mismatched_existing=0
  local scanned_host
  local key_type
  local key_value

  known_hosts_path="$(guest_ssh_known_hosts_path "${caller_home}")"
  guest_ssh_ensure_known_hosts_file "${caller_home}"

  if ! ssh-keygen -F "${vm_name}" -f "${known_hosts_path}" >/dev/null 2>&1; then
    found_existing=0
  fi

  if ! command -v ssh-keyscan >/dev/null 2>&1; then
    return "${found_existing}"
  fi

  scan_timeout_seconds="${AGENT_SANDBOX_GUEST_KEYSCAN_TIMEOUT_SECONDS:-5}"
  scan_output="$(
    ssh-keyscan -T "${scan_timeout_seconds}" "${vm_ip}" 2>/dev/null \
      | sed "s/^[^ ,][^ ]*/${vm_name}/"
  )"
  if [[ -z "${scan_output}" ]]; then
    return "${found_existing}"
  fi

  while read -r scanned_host key_type key_value; do
    [[ -n "${scanned_host}" && -n "${key_type}" && -n "${key_value}" ]] || continue
    if awk -v host="${scanned_host}" -v wanted_type="${key_type}" '
      $1 == host && $2 == wanted_type { found = 1; exit }
      END { exit(found ? 0 : 1) }
    ' "${known_hosts_path}" >/dev/null 2>&1; then
      if ! awk -v host="${scanned_host}" -v wanted_type="${key_type}" -v wanted_value="${key_value}" '
        $1 == host && $2 == wanted_type && $3 == wanted_value { found = 1; exit }
        END { exit(found ? 0 : 1) }
      ' "${known_hosts_path}" >/dev/null 2>&1; then
        mismatched_existing=1
        break
      fi
    fi
  done <<< "${scan_output}"

  if (( mismatched_existing )); then
    guest_ssh_remove_known_host "${vm_name}" "${caller_home}"
    found_existing=0
  fi

  while read -r scanned_host key_type key_value; do
    [[ -n "${scanned_host}" && -n "${key_type}" && -n "${key_value}" ]] || continue
    if awk -v host="${scanned_host}" -v wanted_type="${key_type}" '
      $1 == host && $2 == wanted_type { found = 1; exit }
      END { exit(found ? 0 : 1) }
    ' "${known_hosts_path}" >/dev/null 2>&1; then
      continue
    fi
    printf '%s %s %s\n' "${scanned_host}" "${key_type}" "${key_value}" >> "${known_hosts_path}"
    found_existing=0
  done <<< "${scan_output}"

  if ssh-keygen -F "${vm_name}" -f "${known_hosts_path}" >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

guest_ssh_options() {
  local vm_name="$1"
  local vm_ip="$2"
  local ssh_private_key_path="$3"
  local caller_home="${4:-${HOME:-}}"
  local known_hosts_path
  local strict_host_key_checking

  known_hosts_path="$(guest_ssh_known_hosts_path "${caller_home}")"
  guest_ssh_ensure_known_hosts_file "${caller_home}"

  strict_host_key_checking="yes"
  if ! guest_ssh_prepare_known_host "${vm_name}" "${vm_ip}" "${caller_home}"; then
    strict_host_key_checking="accept-new"
  fi

  printf '%s\n' \
    -o "StrictHostKeyChecking=${strict_host_key_checking}" \
    -o "UserKnownHostsFile=${known_hosts_path}" \
    -o "HostKeyAlias=${vm_name}" \
    -o "CheckHostIP=no" \
    -i "${ssh_private_key_path}"
}

guest_ssh_resolve_vm_ip() {
  local vm_name="$1"
  local ip

  ip="${AGENT_SANDBOX_VM_IP:-}"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  ip="$(
    sudo -n virsh domifaddr "${vm_name}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}' || true
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  ip="$(
    virsh domifaddr "${vm_name}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}' || true
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  if [[ "${AGENT_SANDBOX_ALLOW_PKEXEC_VIRSH:-0}" == "1" ]]; then
    ip="$(
      pkexec virsh domifaddr "${vm_name}" 2>/dev/null \
        | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}' || true
    )"
    if [[ -n "${ip}" ]]; then
      printf '%s\n' "${ip}"
      return 0
    fi
  fi

  return 1
}
