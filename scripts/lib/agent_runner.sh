#!/usr/bin/env bash

agent_runner_provider() {
  printf '%s\n' "${AGENT_PROVIDER:-codex}"
}

agent_runner_load_provider() {
  if [[ "${_AGENT_RUNNER_PROVIDER_LOADED:-}" == "1" ]]; then
    return
  fi

  local provider provider_lib script_dir
  provider="$(agent_runner_provider)"
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  provider_lib="${script_dir}/agent_runner_${provider}.sh"

  if [[ ! -f "${provider_lib}" ]]; then
    echo "Unsupported AGENT_PROVIDER '${provider}': missing adapter ${provider_lib}" >&2
    return 1
  fi

  # shellcheck source=/dev/null
  source "${provider_lib}"
  _AGENT_RUNNER_PROVIDER_LOADED=1
}

agent_runner_exec() {
  agent_runner_load_provider || return 1
  agent_runner_"$(agent_runner_provider)"_exec "$@"
}

agent_runner_resume_last() {
  agent_runner_load_provider || return 1
  agent_runner_"$(agent_runner_provider)"_resume_last "$@"
}

agent_runner_binary_name() {
  agent_runner_load_provider || return 1
  agent_runner_"$(agent_runner_provider)"_binary_name
}

agent_runner_config_path() {
  agent_runner_load_provider || return 1
  agent_runner_"$(agent_runner_provider)"_config_path
}
