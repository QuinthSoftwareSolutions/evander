#!/usr/bin/env bash

# Shared state-tracking helpers sourced inside guest agent runners.
#
# Required environment:
#   STATE_PATH             Path to the .state file.
#   AGENT_STATE_REPO_NAME  Repo identifier written into the state file.

read_state_value() {
  local state_path="$1"
  local key="$2"

  [[ -f "${state_path}" ]] || return 1
  awk -F= -v target_key="${key}" '$1 == target_key {print substr($0, index($0, "=") + 1); exit}' "${state_path}" 2>/dev/null
}

agent_control_state_path() {
  local state_dir

  state_dir="$(dirname "${STATE_PATH}")"
  printf '%s\n' "${state_dir}/agent-control.state"
}

agent_is_disabled() {
  local control_path status

  control_path="$(agent_control_state_path)"
  status="$(read_state_value "${control_path}" "status" || true)"
  [[ "${status}" == "disabled" ]]
}

ensure_agent_enabled() {
  if agent_is_disabled; then
    printf 'Agent is disabled via %s\n' "$(agent_control_state_path)" >&2
    return 1
  fi
}

write_state() {
  local status="$1"
  local phase="$2"
  local exit_code="${3:-}"
  local now
  local tmp_path

  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  tmp_path="${STATE_PATH}.tmp"
  {
    printf "status=%s\n" "${status}"
    printf "phase=%s\n" "${phase}"
    printf "repo=%s\n" "${AGENT_STATE_REPO_NAME}"
    printf "pid=%s\n" "$$"
    printf "updated_at=%s\n" "${now}"
    if [[ -n "${exit_code}" ]]; then
      printf "exit_code=%s\n" "${exit_code}"
    fi
  } > "${tmp_path}"
  mv "${tmp_path}" "${STATE_PATH}"
}

on_exit_write_idle() {
  local exit_code="$?"
  write_state "idle" "complete" "${exit_code}"
}
