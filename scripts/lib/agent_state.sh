#!/usr/bin/env bash

# Shared state-tracking helpers sourced inside guest agent runners.
#
# Required environment:
#   STATE_PATH             Path to the .state file.
#   AGENT_STATE_REPO_NAME  Repo identifier written into the state file.

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
