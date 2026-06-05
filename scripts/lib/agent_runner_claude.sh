#!/usr/bin/env bash

_agent_runner_claude_common_args() {
  local -n dest_ref="$1"
  local mode="$2"
  shift 2

  local prompt=""
  local model=""
  local cwd=""

  dest_ref=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --model)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --model" >&2
          return 1
        fi
        model="$2"
        shift 2
        ;;
      --model=*)
        model="${1#--model=}"
        shift
        ;;
      -C)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for -C" >&2
          return 1
        fi
        cwd="$2"
        shift 2
        ;;
      -C*)
        cwd="${1#-C}"
        shift
        ;;
      -s)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for -s" >&2
          return 1
        fi
        shift 2
        ;;
      --dangerously-bypass-approvals-and-sandbox|--skip-git-repo-check)
        shift
        ;;
      --)
        shift
        break
        ;;
      -*)
        echo "Unsupported ${mode} option for claude provider: $1" >&2
        return 1
        ;;
      *)
        prompt="$1"
        shift
        if [[ $# -gt 0 ]]; then
          echo "Unexpected extra arguments for claude provider: $*" >&2
          return 1
        fi
        break
        ;;
    esac
  done

  if [[ $# -gt 0 ]]; then
    echo "Unexpected extra arguments for claude provider: $*" >&2
    return 1
  fi
  if [[ -z "${prompt}" ]]; then
    echo "Missing prompt for claude provider." >&2
    return 1
  fi

  dest_ref=(
    -p
    --dangerously-skip-permissions
  )
  if [[ -n "${model}" ]]; then
    dest_ref+=(--model "${model}")
  fi
  if [[ -f "${HOME}/.claude/mcp-config.json" ]]; then
    dest_ref+=(--mcp-config "${HOME}/.claude/mcp-config.json")
  fi
  if [[ "${mode}" == "resume-last" ]]; then
    dest_ref+=(--continue)
  fi
  dest_ref+=("${prompt}")

  AGENT_RUNNER_CLAUDE_CWD="${cwd}"
}

_agent_runner_claude_run() {
  local mode="$1"
  shift

  local -a claude_args=()
  local cwd=""

  _agent_runner_claude_common_args claude_args "${mode}" "$@" || return 1
  cwd="${AGENT_RUNNER_CLAUDE_CWD:-}"

  if [[ -n "${cwd}" ]]; then
    (
      cd "${cwd}" &&
      claude "${claude_args[@]}"
    )
    return
  fi

  claude "${claude_args[@]}"
}

agent_runner_claude_exec() {
  _agent_runner_claude_run exec "$@"
}

agent_runner_claude_resume_last() {
  _agent_runner_claude_run resume-last "$@"
}

agent_runner_claude_resume_thread() {
  local thread_id="${1:-}"
  shift || true
  if [[ -z "${thread_id}" ]]; then
    echo "Missing thread id for claude provider resume." >&2
    return 1
  fi

  local -a claude_args=()
  local cwd=""
  _agent_runner_claude_common_args claude_args resume-thread "$@" || return 1
  cwd="${AGENT_RUNNER_CLAUDE_CWD:-}"
  claude_args=(--resume "${thread_id}" "${claude_args[@]}")

  if [[ -n "${cwd}" ]]; then
    (
      cd "${cwd}" &&
      claude "${claude_args[@]}"
    )
    return
  fi

  claude "${claude_args[@]}"
}

agent_runner_claude_binary_name() {
  printf 'claude\n'
}

agent_runner_claude_config_path() {
  printf '%s/.claude/settings.json\n' "${HOME}"
}
