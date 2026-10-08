#!/usr/bin/env bash

_agent_runner_codex_json_to_log() {
  if command -v python3 >/dev/null 2>&1; then
    python3 -u -c '
import json
import sys

TEXT_PART_TYPES = {"text", "output_text", "assistant_message"}
ASSISTANT_ITEM_TYPES = {"assistant_message", "message", "final_message"}
REASONING_PART_TYPES = {"summary_text", "text", "output_text"}
REASONING_SUMMARY_LIMIT = 800
TOOL_SUMMARY_LIMIT = 240
thread_id_printed = False
printed_messages = set()
active_tool_calls = {}


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


def summary_text(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts = []
    for part in value:
        if isinstance(part, str):
            if part:
                parts.append(part)
            continue
        if not isinstance(part, dict):
            continue
        part_type = str(part.get("type") or "")
        if part_type and part_type not in REASONING_PART_TYPES:
            continue
        text = first_text(part.get("text"), part.get("content"), part.get("message"))
        if text:
            parts.append(text)
    return "\n".join(parts)


def bounded_fragment(value, limit):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def json_object(value):
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def tool_summary(payload):
    name = first_string(
        payload.get("name"),
        payload.get("tool_name"),
        payload.get("function_name"),
    ) or "tool"
    arguments = json_object(payload.get("arguments"))
    command = first_string(
        payload.get("command"),
        arguments.get("cmd"),
        arguments.get("command"),
    )
    cwd = first_string(
        arguments.get("workdir"),
        arguments.get("cwd"),
        payload.get("cwd"),
    )
    parts = [name]
    if command:
        parts.append(bounded_fragment(command, TOOL_SUMMARY_LIMIT))
    if cwd:
        parts.append(f"cwd={cwd}")
    return " ".join(parts)


def reasoning_message(payload):
    text = first_text(
        payload.get("text"),
        payload.get("reasoning"),
        summary_text(payload.get("summary")),
        summary_text(payload.get("content")),
    )
    text = bounded_fragment(text, REASONING_SUMMARY_LIMIT)
    return f"reasoning: {text}" if text else ""


def start_tool(payload):
    call_id = first_string(payload.get("call_id"), payload.get("id"))
    summary = tool_summary(payload)
    if call_id:
        active_tool_calls[call_id] = summary
    return f"tool started: {summary}" if summary else ""


def complete_tool(payload):
    call_id = first_string(payload.get("call_id"), payload.get("id"))
    summary = active_tool_calls.pop(call_id, "") if call_id else ""
    return f"tool completed: {summary}" if summary else ""


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
            elif payload_type == "agent_reasoning":
                print_message(reasoning_message(payload))
            elif payload_type.endswith("_end"):
                print_message(complete_tool(payload))
        continue

    if event_type == "response_item":
        payload = event.get("payload")
        if isinstance(payload, dict):
            payload_type = str(payload.get("type") or "")
            if payload_type == "reasoning":
                print_message(reasoning_message(payload))
            elif payload_type == "function_call":
                print_message(start_tool(payload))
            elif payload_type in {"function_call_output", "tool_call_output"}:
                print_message(complete_tool(payload))
            else:
                print_message(text_from_response_item(event))
        continue

    if event_type in {"item.started", "item.completed"}:
        item = event.get("item")
        if isinstance(item, dict):
            item_type = str(item.get("type") or item.get("item_type") or "")
            if item_type == "reasoning":
                print_message(reasoning_message(item))
            elif item_type in {"command_execution", "function_call", "mcp_tool_call", "tool_call"}:
                if event_type == "item.started":
                    print_message(start_tool(item))
                else:
                    completed = complete_tool(item)
                    print_message(completed or f"tool completed: {tool_summary(item)}")
            elif event_type == "item.completed":
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
  case "${AGENT_FAST_MODE:-}" in
    ""|true|false|ultrafast) ;;
    *) echo "Unsupported AGENT_FAST_MODE '${AGENT_FAST_MODE}'." >&2; return 1 ;;
  esac
  case "${AGENT_REASONING_EFFORT:-}" in
    ""|none|minimal|low|medium|high|xhigh|max|ultra) ;;
    *) echo "Unsupported AGENT_REASONING_EFFORT '${AGENT_REASONING_EFFORT}'." >&2; return 1 ;;
  esac
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
  case "${AGENT_FAST_MODE:-false}" in
    ultrafast)
      printf '%s\0%s\0' "-c" 'service_tier="ultrafast"' "-c" 'features.fast_mode=true'
      ;;
    true)
      printf '%s\0%s\0' "-c" 'service_tier="fast"' "-c" 'features.fast_mode=true'
      ;;
    *)
      printf '%s\0%s\0' "-c" 'service_tier="default"'
      ;;
  esac
  printf '%s\0%s\0' "-c" "model_reasoning_effort=\"${AGENT_REASONING_EFFORT:-high}\""
  if [[ -n "${AGENT_REASONING_EFFORT:-}" && "${AGENT_REASONING_EFFORT_SOURCE:-}" != "default" ]]; then
    printf '%s\0%s\0' "-c" "model_supports_reasoning_summaries=true"
  fi
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
