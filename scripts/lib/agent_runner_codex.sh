#!/usr/bin/env bash

agent_runner_codex_config_args() {
  if [[ -n "${AGENT_SANDBOX_MCP_GATEWAY_URL:-}" ]]; then
    printf '%s\0%s\0' \
      "-c" \
      "mcp_servers.agent_sandbox.url=\"${AGENT_SANDBOX_MCP_GATEWAY_URL}\""
  fi
}

agent_runner_codex_exec() {
  local config_args=()
  if [[ -n "${AGENT_SANDBOX_MCP_GATEWAY_URL:-}" ]]; then
    mapfile -d '' -t config_args < <(agent_runner_codex_config_args)
  fi
  codex exec "${config_args[@]}" "$@"
}

agent_runner_codex_resume_last() {
  local config_args=()
  if [[ -n "${AGENT_SANDBOX_MCP_GATEWAY_URL:-}" ]]; then
    mapfile -d '' -t config_args < <(agent_runner_codex_config_args)
  fi
  codex exec resume --last "${config_args[@]}" "$@"
}

agent_runner_codex_resume_thread() {
  local thread_id="$1"
  shift
  local config_args=()
  if [[ -n "${AGENT_SANDBOX_MCP_GATEWAY_URL:-}" ]]; then
    mapfile -d '' -t config_args < <(agent_runner_codex_config_args)
  fi
  codex exec resume "${config_args[@]}" "${thread_id}" "$@"
}

agent_runner_codex_binary_name() {
  printf 'codex\n'
}

agent_runner_codex_config_path() {
  printf '%s/.codex/config.toml\n' "${HOME}"
}
