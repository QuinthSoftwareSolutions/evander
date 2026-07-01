#!/usr/bin/env bash

_agent_runner_codex_json_to_log() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -u -c '
import json
import sys

TEXT_PART_TYPES = {"text", "output_text", "assistant_message"}
ASSISTANT_ITEM_TYPES = {"assistant_message", "message", "final_message"}
thread_id_printed = False
printed_messages = set()


def first_string(*values):
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def first_text(*values):
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, str):
                if part:
                    parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type and part_type not in TEXT_PART_TYPES:
                continue
            text = first_text(part.get("text"), part.get("content"), part.get("message"))
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        return first_text(value.get("text"), value.get("content"), value.get("message"))
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


def thread_id_from_event(event):
    thread = event.get("thread")
    payload = event.get("payload")
    return first_string(
        event.get("thread_id"),
        thread.get("id") if isinstance(thread, dict) else None,
        payload.get("id") if isinstance(payload, dict) else None,
    )


def text_from_response_item(event):
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return ""
    if str(payload.get("type") or "") != "message":
        return ""
    if str(payload.get("role") or "") != "assistant":
        return ""
    return first_text(
        payload.get("text"),
        payload.get("message"),
        content_text(payload.get("content")),
        content_text(payload.get("formatted")),
    )


def text_from_item_completed(event):
    item = event.get("item")
    if not isinstance(item, dict):
        return ""
    item_type = str(item.get("type") or item.get("item_type") or "")
    role = str(item.get("role") or "")
    if item_type and item_type not in ASSISTANT_ITEM_TYPES and role != "assistant":
        return ""
    return first_text(
        item.get("text"),
        item.get("message"),
        content_text(item.get("content")),
        content_text(item.get("formatted")),
    )


for raw_line in sys.stdin:
    raw_line = raw_line.rstrip("\n")
    try:
        event = json.loads(raw_line)
    except Exception:
        print(raw_line, flush=True)
        continue
    if not isinstance(event, dict):
        continue

    event_type = str(event.get("type") or "")
    if event_type == "session_meta":
        payload = event.get("payload")
        thread_id = first_string(payload.get("id") if isinstance(payload, dict) else None)
        if thread_id and not thread_id_printed:
            print(f"session id: {thread_id}", flush=True)
            thread_id_printed = True
        continue

    if event_type == "thread.started":
        thread_id = thread_id_from_event(event)
        if thread_id and not thread_id_printed:
            print(f"session id: {thread_id}", flush=True)
            thread_id_printed = True
        continue

    if event_type == "event_msg":
        payload = event.get("payload")
        if isinstance(payload, dict):
            payload_type = str(payload.get("type") or "")
            if payload_type == "agent_message":
                print_message(first_text(payload.get("message")))
            elif payload_type == "task_complete":
                print_message(first_text(payload.get("last_agent_message")))
        continue

    if event_type == "response_item":
        print_message(text_from_response_item(event))
        continue

    if event_type == "item.completed":
        print_message(text_from_item_completed(event))
        continue

    if event_type in {"error", "turn.failed"}:
        message = first_string(event.get("message"), event.get("error"))
        if message:
            print(f"codex {event_type}: {message}", flush=True)
'
  else
    cat
  fi
}

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

agent_runner_codex_supports_feature() {
  local feature="$1"
  codex features list 2>/dev/null \
    | awk -v feature="${feature}" '$1 == feature && $2 != "removed" { found=1 } END { exit found ? 0 : 1 }'
}

agent_runner_codex_config_args() {
  agent_runner_codex_validate_web_search_mode || return 1
  if [[ -n "${AGENT_WEB_SEARCH_MODE:-}" ]]; then
    printf '%s\0%s\0' \
      "-c" \
      "web_search=\"${AGENT_WEB_SEARCH_MODE}\""
  fi
  if [[ -n "${EVANDER_MCP_GATEWAY_URL:-}" ]]; then
    printf '%s\0%s\0' \
      "-c" \
      "mcp_servers.evander.url=\"${EVANDER_MCP_GATEWAY_URL}\""
    if agent_runner_codex_supports_feature "non_prefixed_mcp_tool_names"; then
      printf '%s\0%s\0' \
        "--enable" \
        "non_prefixed_mcp_tool_names"
    fi
  fi
}

agent_runner_codex_exec() {
  local config_args=()
  agent_runner_codex_validate_web_search_mode || return 1
  mapfile -d '' -t config_args < <(agent_runner_codex_config_args)
  codex exec "${config_args[@]}" --json "$@" | _agent_runner_codex_json_to_log
}

agent_runner_codex_resume_last() {
  local config_args=()
  agent_runner_codex_validate_web_search_mode || return 1
  mapfile -d '' -t config_args < <(agent_runner_codex_config_args)
  codex exec resume --last "${config_args[@]}" --json "$@" | _agent_runner_codex_json_to_log
}

agent_runner_codex_resume_thread() {
  local thread_id="$1"
  shift
  local config_args=()
  agent_runner_codex_validate_web_search_mode || return 1
  mapfile -d '' -t config_args < <(agent_runner_codex_config_args)
  codex exec resume "${config_args[@]}" --json "${thread_id}" "$@" | _agent_runner_codex_json_to_log
}

agent_runner_codex_binary_name() {
  printf 'codex\n'
}

agent_runner_codex_config_path() {
  printf '%s/.codex/config.toml\n' "${HOME}"
}
