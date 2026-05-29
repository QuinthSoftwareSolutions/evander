#!/usr/bin/env bash

agent_runner_codex_validate_web_search_mode() {
  case "${AGENT_WEB_SEARCH_MODE:-}" in
    ""|disabled|cached|live)
      ;;
    *)
      echo "Unsupported AGENT_WEB_SEARCH_MODE '${AGENT_WEB_SEARCH_MODE}'." >&2
      return 1
      ;;
  esac
}

agent_runner_codex_config_args() {
  agent_runner_codex_validate_web_search_mode || return 1
  if [[ -n "${AGENT_WEB_SEARCH_MODE:-}" ]]; then
    printf '%s\0%s\0' \
      "-c" \
      "web_search=\"${AGENT_WEB_SEARCH_MODE}\""
  fi
  if [[ -n "${AGENT_SANDBOX_MCP_GATEWAY_URL:-}" ]]; then
    printf '%s\0%s\0' \
      "-c" \
      "mcp_servers.agent_sandbox.url=\"${AGENT_SANDBOX_MCP_GATEWAY_URL}\""
  fi
}

agent_runner_codex_exec() {
  local config_args=()
  agent_runner_codex_validate_web_search_mode || return 1
  mapfile -d '' -t config_args < <(agent_runner_codex_config_args)
  codex exec "${config_args[@]}" "$@"
}

agent_runner_codex_resume_last() {
  local config_args=()
  agent_runner_codex_validate_web_search_mode || return 1
  mapfile -d '' -t config_args < <(agent_runner_codex_config_args)
  codex exec resume --last "${config_args[@]}" "$@"
}

agent_runner_codex_resume_thread() {
  local thread_id="$1"
  shift
  local config_args=()
  agent_runner_codex_validate_web_search_mode || return 1
  mapfile -d '' -t config_args < <(agent_runner_codex_config_args)
  codex exec resume "${config_args[@]}" "${thread_id}" "$@"
}

agent_runner_codex_binary_name() {
  printf 'codex\n'
}

agent_runner_codex_config_path() {
  printf '%s/.codex/config.toml\n' "${HOME}"
}
