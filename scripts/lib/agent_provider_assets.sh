#!/usr/bin/env bash

agent_provider_name() {
  printf '%s\n' "${1:-${AGENT_PROVIDER:-codex}}"
}

agent_provider_validate_name() {
  local provider
  provider="$(agent_provider_name "${1:-}")"
  if [[ ! "${provider}" =~ ^[A-Za-z0-9_-]+$ ]]; then
    echo "Invalid AGENT_PROVIDER '${provider}'." >&2
    return 1
  fi
}

agent_provider_runner_lib_name() {
  local provider
  provider="$(agent_provider_name "${1:-}")"
  agent_provider_validate_name "${provider}" || return 1
  printf 'agent_runner_%s.sh\n' "${provider}"
}

agent_provider_runner_lib_path() {
  local base_dir="$1"
  local provider="${2:-}"
  printf '%s/%s\n' "${base_dir}" "$(agent_provider_runner_lib_name "${provider}")"
}

agent_provider_bootstrap_script_name() {
  local provider
  provider="$(agent_provider_name "${1:-}")"
  agent_provider_validate_name "${provider}" || return 1
  printf 'bootstrap_%s_vm.sh\n' "${provider}"
}

agent_provider_bootstrap_script_path() {
  local base_dir="$1"
  local provider="${2:-}"
  printf '%s/%s\n' "${base_dir}" "$(agent_provider_bootstrap_script_name "${provider}")"
}
