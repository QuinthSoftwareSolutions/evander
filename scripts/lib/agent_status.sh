#!/usr/bin/env bash

vm_agent_status_from_ssh() {
  local vm_user="$1"
  local vm_ip="$2"
  local ssh_private_key_path="$3"
  local connect_timeout_seconds="$4"
  local state_max_age_seconds

  state_max_age_seconds="${VM_AGENT_STATE_MAX_AGE_SECONDS:-600}"

  if ! ssh \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout="${connect_timeout_seconds}" \
    -i "${ssh_private_key_path}" \
    "${vm_user}@${vm_ip}" \
    "VM_AGENT_STATE_MAX_AGE_SECONDS='${state_max_age_seconds}' bash -s" <<'REMOTE_EOF'
set -euo pipefail

LOG_DIR="${HOME}/.agent-sandbox"
VM_AGENT_STATE_MAX_AGE_SECONDS="${VM_AGENT_STATE_MAX_AGE_SECONDS:-600}"

is_active_agent_pid() {
  local pid="$1"
  local cmdline_path="/proc/${pid}/cmdline"
  local cmdline=""

  [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1 || return 1
  [[ -r "${cmdline_path}" ]] || return 1

  cmdline="$(tr '\000' ' ' < "${cmdline_path}" 2>/dev/null || true)"
  [[ -n "${cmdline}" ]] || return 1

  [[ "${cmdline}" == *"agent_runner_exec"* || "${cmdline}" == *"agent_runner_resume_last"* ]]
}

iso_to_epoch() {
  local raw="$1"

  [[ -n "${raw}" ]] || return 1
  date -u -d "${raw}" +%s 2>/dev/null
}

state_is_fresh() {
  local updated_at="$1"
  local updated_at_epoch now

  updated_at_epoch="$(iso_to_epoch "${updated_at}")" || return 1
  now="$(date -u +%s)"
  (( now - updated_at_epoch <= VM_AGENT_STATE_MAX_AGE_SECONDS ))
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
for state_path in "${LOG_DIR}"/agent-*.state; do
  status="$(read_state_value "${state_path}" "status")"
  [[ "${status}" == "running" ]] || continue

  updated_at="$(read_state_value "${state_path}" "updated_at")"
  pid="$(read_state_value "${state_path}" "pid")"
  if ! state_is_fresh "${updated_at}" && ! is_active_agent_pid "${pid}"; then
    continue
  fi

  name="$(basename "${state_path}")"
  name="${name#agent-}"
  name="${name%.state}"
  active_entries+=("${name}")
done
shopt -u nullglob

if [[ ${#active_entries[@]} -gt 0 ]]; then
  printf 'running (%s)\n' "$(IFS=', '; echo "${active_entries[*]}")"
  exit 0
fi

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

vm_agent_status_for_vm() {
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

  vm_agent_status_from_ssh "${vm_user}" "${vm_ip}" "${ssh_private_key_path}" "${connect_timeout_seconds}"
}

vm_agent_is_busy_status() {
  local status="$1"
  [[ "${status}" == running* ]]
}
