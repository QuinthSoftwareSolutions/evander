#!/usr/bin/env bash

# shellcheck source=lib/guest_ssh.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/guest_ssh.sh"

vm_agent_status_from_ssh() {
  local vm_user="$1"
  local vm_name="$2"
  local vm_ip="$3"
  local ssh_private_key_path="$4"
  local connect_timeout_seconds="$5"
  local state_max_age_seconds
  local caller_user
  local caller_home
  local -a ssh_opts

  state_max_age_seconds="${VM_AGENT_STATE_MAX_AGE_SECONDS:-600}"
  caller_user="${SUDO_USER:-${USER:-}}"
  caller_home="${HOME:-}"
  if [[ -n "${caller_user}" ]]; then
    caller_home="$(getent passwd "${caller_user}" | cut -d: -f6 || printf '%s\n' "${caller_home}")"
  fi

  mapfile -t ssh_opts < <(guest_ssh_options "${vm_name}" "${vm_ip}" "${ssh_private_key_path}" "${caller_home}")
  ssh_opts+=(-o ConnectTimeout="${connect_timeout_seconds}")

  if ! ssh \
    "${ssh_opts[@]}" \
    "${vm_user}@${vm_ip}" \
    "VM_AGENT_STATE_MAX_AGE_SECONDS='${state_max_age_seconds}' bash -s" <<'REMOTE_EOF'
set -euo pipefail

LOG_DIR="${HOME}/.evander"
VM_AGENT_STATE_MAX_AGE_SECONDS="${VM_AGENT_STATE_MAX_AGE_SECONDS:-600}"
CONTROL_STATE_PATH="${LOG_DIR}/agent-control.state"

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
control_status=""

read_state_value() {
  local state_path="$1"
  local key="$2"
  awk -F= -v target_key="${key}" '$1 == target_key {print substr($0, index($0, "=") + 1); exit}' "${state_path}" 2>/dev/null || true
}

if [[ -f "${CONTROL_STATE_PATH}" ]]; then
  control_status="$(read_state_value "${CONTROL_STATE_PATH}" "status")"
  if [[ "${control_status}" == "disabled" ]]; then
    printf '%s\n' "disabled"
    exit 0
  fi
fi

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
codex_state_path="${LOG_DIR}/codex-resume-last.state"
if [[ -f "${codex_state_path}" ]]; then
  status="$(read_state_value "${codex_state_path}" "status")"
  if [[ "${status}" == "running" ]]; then
    updated_at="$(read_state_value "${codex_state_path}" "updated_at")"
    pid="$(read_state_value "${codex_state_path}" "pid")"
    if state_is_fresh "${updated_at}" || is_active_agent_pid "${pid}"; then
      active_entries+=("codex-heartbeat")
    fi
  fi
fi
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

  vm_agent_status_from_ssh "${vm_user}" "${vm_name}" "${vm_ip}" "${ssh_private_key_path}" "${connect_timeout_seconds}"
}

vm_agent_is_busy_status() {
  local status="$1"
  [[ "${status}" == running* ]]
}
