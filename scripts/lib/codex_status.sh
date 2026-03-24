#!/usr/bin/env bash

vm_codex_status_from_ssh() {
  local vm_user="$1"
  local vm_ip="$2"
  local ssh_private_key_path="$3"
  local connect_timeout_seconds="$4"

  if ! ssh \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout="${connect_timeout_seconds}" \
    -i "${ssh_private_key_path}" \
    "${vm_user}@${vm_ip}" \
    "bash -s" <<'REMOTE_EOF'
set -euo pipefail

LOG_DIR="${HOME}/.agent-sandbox"

is_active_codex_pid() {
  local pid_path="$1"
  local pid
  local cmdline

  [[ -f "${pid_path}" ]] || return 1
  pid="$(tr -d '[:space:]' <"${pid_path}" || true)"
  [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1 || return 1
  cmdline="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  [[ -n "${cmdline}" ]] || return 1
  case "${cmdline}" in
    codex|*/codex|*" codex"|*" codex "*|*"/codex "*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

if [[ ! -d "${LOG_DIR}" ]]; then
  printf '%s\n' "not started"
  exit 0
fi

active_entries=()

read_state_value() {
  local state_path="$1"
  local key="$2"
  awk -F= -v target_key="${key}" '$1 == target_key {print substr($0, index($0, "=") + 1); exit}' "${state_path}" 2>/dev/null || true
}

shopt -s nullglob
for state_path in "${LOG_DIR}"/codex-*.state; do
  status="$(read_state_value "${state_path}" "status")"
  [[ "${status}" == "running" ]] || continue

  pid="$(read_state_value "${state_path}" "pid")"
  if [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" >/dev/null 2>&1; then
    name="$(basename "${state_path}")"
    name="${name#codex-}"
    name="${name%.state}"
    active_entries+=("${name}")
  fi
done
shopt -u nullglob

if [[ ${#active_entries[@]} -gt 0 ]]; then
  printf 'running (%s)\n' "$(IFS=', '; echo "${active_entries[*]}")"
  exit 0
fi

# Back-compat fallback for sessions that still publish PID files but not state.
shopt -s nullglob
for pid_path in "${LOG_DIR}"/codex-*.pid; do
  name="$(basename "${pid_path}")"
  name="${name#codex-}"
  name="${name%.pid}"
  if is_active_codex_pid "${pid_path}"; then
    active_entries+=("${name}")
  fi
done
shopt -u nullglob

if [[ ${#active_entries[@]} -eq 0 ]]; then
  printf '%s\n' "idle"
  exit 0
fi

printf 'running (%s)\n' "$(IFS=', '; echo "${active_entries[*]}")"
REMOTE_EOF
  then
    printf '%s\n' "unknown (SSH failed)"
    return 0
  fi
}

vm_codex_status_for_vm() {
  local vm_name="$1"
  local vm_user="$2"
  local ssh_private_key_path="$3"
  local connect_timeout_seconds="$4"

  # shellcheck source=lib/vm_network.sh
  source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/vm_network.sh"

  local vm_ip
  if ! vm_ip="$(vm_network_resolve_ip "${vm_name}" 2>/dev/null)"; then
    printf '%s\n' "unknown (no IP)"
    return 0
  fi

  vm_codex_status_from_ssh "${vm_user}" "${vm_ip}" "${ssh_private_key_path}" "${connect_timeout_seconds}"
}

vm_codex_is_busy_status() {
  local status="$1"
  [[ "${status}" == running* ]]
}
