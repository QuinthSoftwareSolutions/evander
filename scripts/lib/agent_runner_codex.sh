#!/usr/bin/env bash

agent_runner_codex_exec() {
  codex exec "$@"
}

agent_runner_codex_resume_last() {
  codex exec resume --last "$@"
}

agent_runner_codex_binary_name() {
  printf 'codex\n'
}

agent_runner_codex_config_path() {
  printf '%s/.codex/config.toml\n' "${HOME}"
}
