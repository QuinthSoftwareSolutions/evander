#!/usr/bin/env bash

_agent_runner_copilot_common_args() {
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
        echo "Unsupported ${mode} option for copilot provider: $1" >&2
        return 1
        ;;
      *)
        prompt="$1"
        shift
        if [[ $# -gt 0 ]]; then
          echo "Unexpected extra arguments for copilot provider: $*" >&2
          return 1
        fi
        break
        ;;
    esac
  done

  if [[ $# -gt 0 ]]; then
    echo "Unexpected extra arguments for copilot provider: $*" >&2
    return 1
  fi

  if [[ -z "${prompt}" ]]; then
    echo "Missing prompt for copilot provider." >&2
    return 1
  fi

  dest_ref=(
    --no-alt-screen
    --allow-all
    --no-ask-user
    --autopilot
  )
  if [[ "${mode}" == "resume" ]]; then
    dest_ref+=(--continue)
  fi
  if [[ -n "${model}" ]]; then
    dest_ref+=(--model "${model}")
  fi
  dest_ref+=(-p "${prompt}")

  AGENT_RUNNER_COPILOT_CWD="${cwd}"
}

_agent_runner_copilot_run() {
  local mode="$1"
  shift

  local -a copilot_args=()
  local cwd=""

  _agent_runner_copilot_common_args copilot_args "${mode}" "$@" || return 1
  cwd="${AGENT_RUNNER_COPILOT_CWD:-}"

  if [[ -n "${cwd}" ]]; then
    (
      cd "${cwd}" &&
      copilot "${copilot_args[@]}"
    )
    return
  fi

  copilot "${copilot_args[@]}"
}

agent_runner_copilot_exec() {
  _agent_runner_copilot_run exec "$@"
}

agent_runner_copilot_resume_last() {
  _agent_runner_copilot_run resume "$@"
}

agent_runner_copilot_binary_name() {
  printf 'copilot\n'
}

agent_runner_copilot_config_path() {
  printf '%s/.copilot/config.json\n' "${HOME}"
}
