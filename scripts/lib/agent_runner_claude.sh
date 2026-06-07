#!/usr/bin/env bash

_agent_runner_claude_json_to_log() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -u -c '
import json
import sys

thread_id_printed = False
printed_messages = set()


def first_string(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                if item:
                    parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            text = first_string(item.get("text"), item.get("content"), item.get("message"))
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        return first_string(value.get("text"), value.get("content"), value.get("message"))
    return ""


def print_message(message):
    if not isinstance(message, str) or not message:
        return
    if message in printed_messages:
        return
    printed_messages.add(message)
    if message.endswith("\n"):
        print(message, end="", flush=True)
    else:
        print(message, flush=True)


for line in sys.stdin:
    stripped = line.strip()
    if not stripped:
        continue
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        print(stripped, flush=True)
        continue
    if not isinstance(event, dict):
        continue

    thread_id = first_string(
        event.get("session_id"),
        event.get("sessionId"),
        event.get("conversation_id"),
        event.get("conversationId"),
    )
    if thread_id and not thread_id_printed:
        print(f"session id: {thread_id}", flush=True)
        thread_id_printed = True

    event_type = first_string(event.get("type"))
    if event_type in {"assistant", "assistant_message", "message"}:
        message = event.get("message")
        if isinstance(message, dict):
            print_message(content_text(message.get("content")))
        else:
            print_message(content_text(event.get("content")))
        continue
    if event_type in {"result", "final"}:
        print_message(first_string(event.get("result"), event.get("text"), event.get("content")))
        continue
    if event_type in {"error", "failed"}:
        message = event.get("error")
        if isinstance(message, dict):
            message = first_string(message.get("message"), message.get("type"))
        print_message("claude error: " + first_string(message, event.get("message")))
'
  else
    cat
  fi
}

_agent_runner_claude_json_string() {
  local value="$1"
  case "${value}" in
    *$'\n'*|*$'\r'*)
      echo "Claude MCP gateway URL cannot contain newlines." >&2
      return 1
      ;;
  esac
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  printf '"%s"' "${value}"
}

_agent_runner_claude_mcp_config_path() {
  if [[ -n "${AGENT_SANDBOX_MCP_GATEWAY_URL:-}" ]]; then
    local config_path="${HOME}/.claude/agent-sandbox-mcp-config.json"
    local escaped_url
    escaped_url="$(_agent_runner_claude_json_string "${AGENT_SANDBOX_MCP_GATEWAY_URL}")" || return 1
    mkdir -p "${HOME}/.claude"
    cat > "${config_path}" <<CONFIG
{
  "mcpServers": {
    "agent_sandbox": {
      "tools": ["*"],
      "type": "http",
      "url": ${escaped_url}
    }
  }
}
CONFIG
    printf '%s\n' "${config_path}"
    return 0
  fi

  if [[ -f "${HOME}/.claude/mcp-config.json" ]]; then
    printf '%s\n' "${HOME}/.claude/mcp-config.json"
  fi
}

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
    --output-format
    stream-json
    --verbose
  )
  if [[ -n "${model}" ]]; then
    dest_ref+=(--model "${model}")
  fi
  local mcp_config_path
  mcp_config_path="$(_agent_runner_claude_mcp_config_path)" || return 1
  if [[ -n "${mcp_config_path}" ]]; then
    dest_ref+=(--mcp-config "${mcp_config_path}")
  fi
  if [[ "${mode}" == "resume-last" ]]; then
    dest_ref+=(--continue)
  fi

  AGENT_RUNNER_CLAUDE_CWD="${cwd}"
  AGENT_RUNNER_CLAUDE_PROMPT="${prompt}"
}

_agent_runner_claude_invoke() {
  local prompt="$1"
  shift
  printf '%s' "${prompt}" | claude "$@" | _agent_runner_claude_json_to_log
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
      _agent_runner_claude_invoke "${AGENT_RUNNER_CLAUDE_PROMPT:-}" "${claude_args[@]}"
    )
    return
  fi

  _agent_runner_claude_invoke "${AGENT_RUNNER_CLAUDE_PROMPT:-}" "${claude_args[@]}"
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
      _agent_runner_claude_invoke "${AGENT_RUNNER_CLAUDE_PROMPT:-}" "${claude_args[@]}"
    )
    return
  fi

  _agent_runner_claude_invoke "${AGENT_RUNNER_CLAUDE_PROMPT:-}" "${claude_args[@]}"
}

agent_runner_claude_binary_name() {
  printf 'claude\n'
}

agent_runner_claude_config_path() {
  printf '%s/.claude/settings.json\n' "${HOME}"
}
