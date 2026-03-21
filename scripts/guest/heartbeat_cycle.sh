#!/usr/bin/env bash
set -euo pipefail

STATE_DIR="${HOME}/.agent-sandbox"
LOCK_FILE="${STATE_DIR}/heartbeat.lock"
LOG_FILE="${STATE_DIR}/heartbeat.log"
PID_FILE="${STATE_DIR}/codex-heartbeat.pid"
COOLDOWN_FILE="${STATE_DIR}/heartbeat.cooldown"
SESSION_FILE="/etc/agent-sandbox/session.env"
RUN_LOG_FILE="${STATE_DIR}/codex-heartbeat-run.log"
RESUME_HINT_FILE="${STATE_DIR}/codex-resume-args"

CODEX_MODEL="${CODEX_MODEL:-gpt-5.4}"
HEARTBEAT_COOLDOWN_SECONDS="${HEARTBEAT_COOLDOWN_SECONDS:-120}"
HEARTBEAT_ENABLE_RESUME="${HEARTBEAT_ENABLE_RESUME:-1}"
TARGET_DIR="${HEARTBEAT_TARGET_DIR:-${PWD}}"
TASK_FILE="${HEARTBEAT_TASK_FILE:-${TARGET_DIR}/AGENT_TASK.yaml}"

feedback_instruction() {
  printf '%s' "New human/provider feedback is pending for session ${SESSION_ID}. Fetch unread feedback using MCP tools (list_unread_feedback with your latest cursor), apply requested updates in ${TARGET_DIR}, then continue/finish the task from ${TASK_FILE}. Keep feedback handling idempotent, and only advance cursor state after durable handling. When restore is needed, use 'dotnet restore --configfile ~/.nuget/NuGet/NuGet.Config'. For build and test, prefer '--no-restore'. Commit and push with 'git push-agent' when ready."
}

mkdir -p "${STATE_DIR}"
touch "${LOG_FILE}"

log() {
  printf '%s %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$*" >>"${LOG_FILE}"
}

exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
  log "skip reason=lock-held"
  exit 0
fi

if [[ -f "${SESSION_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${SESSION_FILE}"
  set +a
fi

SESSION_ID="${AGENT_SANDBOX_SESSION_ID:-}"
GATEWAY_URL="${AGENT_SANDBOX_GATEWAY_URL:-http://192.168.250.1:8000/mcp}"

if [[ -z "${SESSION_ID}" ]]; then
  log "skip reason=missing-session-id session_file=${SESSION_FILE}"
  exit 0
fi

clean_pid_file() {
  if [[ ! -f "${PID_FILE}" ]]; then
    return 0
  fi

  local pid
  pid="$(tr -d '[:space:]' <"${PID_FILE}" || true)"
  if [[ -z "${pid}" || ! "${pid}" =~ ^[0-9]+$ ]]; then
    rm -f "${PID_FILE}"
    log "stale_pid reason=invalid"
    return 0
  fi

  if kill -0 "${pid}" >/dev/null 2>&1; then
    if ps -p "${pid}" -o args= 2>/dev/null | grep -qE 'codex( |$)'; then
      log "skip reason=codex-active pid=${pid}"
      exit 0
    fi
  fi

  rm -f "${PID_FILE}"
  log "stale_pid cleaned pid=${pid}"
}

cooldown_active() {
  [[ -f "${COOLDOWN_FILE}" ]] || return 1

  local next_epoch now_epoch
  next_epoch="$(tr -d '[:space:]' <"${COOLDOWN_FILE}" || true)"
  if [[ -z "${next_epoch}" || ! "${next_epoch}" =~ ^[0-9]+$ ]]; then
    rm -f "${COOLDOWN_FILE}"
    return 1
  fi

  now_epoch="$(date +%s)"
  if (( now_epoch < next_epoch )); then
    log "skip reason=cooldown-active wait_seconds=$((next_epoch - now_epoch))"
    return 0
  fi

  return 1
}

mcp_has_pending_feedback() {
  local payload response

  payload="$(python3 - "${SESSION_ID}" <<'PY'
import json
import sys

session_id = sys.argv[1]
print(json.dumps({
    "jsonrpc": "2.0",
    "id": "heartbeat-1",
    "method": "tools/call",
    "params": {
        "name": "has_pending_feedback",
        "arguments": {"session_id": session_id},
    },
}))
PY
)"

  if ! response="$(curl -fsS \
    -H 'content-type: application/json' \
    -H 'accept: application/json, text/event-stream' \
    --data "${payload}" \
    "${GATEWAY_URL}" 2>>"${LOG_FILE}")"; then
    log "mcp_error step=has_pending_feedback"
    return 2
  fi

  if python3 - "${response}" <<'PY'; then
import json
import sys

def find_pending(value):
    if isinstance(value, dict):
        if "pending" in value and isinstance(value["pending"], bool):
            return value["pending"]
        for nested in value.values():
            found = find_pending(nested)
            if found is not None:
                return found
    if isinstance(value, list):
        for nested in value:
            found = find_pending(nested)
            if found is not None:
                return found
    return None

payload = sys.argv[1]
try:
    parsed = json.loads(payload)
except json.JSONDecodeError:
    lowered = payload.lower()
    if '"pending":true' in lowered or '"pending": true' in lowered:
        sys.exit(0)
    sys.exit(1)

pending = find_pending(parsed)
if pending is True:
    sys.exit(0)
sys.exit(1)
PY
    return 0
  fi

  return 1
}

start_codex_exec_for_feedback() {
  local prompt
  prompt="$(feedback_instruction)"
  nohup codex exec \
    --model "${CODEX_MODEL}" \
    -s danger-full-access \
    --dangerously-bypass-approvals-and-sandbox \
    --skip-git-repo-check \
    -C "${TARGET_DIR}" \
    "${prompt}" \
    >>"${RUN_LOG_FILE}" 2>&1 < /dev/null &
}

start_codex_resume() {
  local -a resume_args
  local prompt
  if [[ -s "${RESUME_HINT_FILE}" ]]; then
    # shellcheck disable=SC2207
    resume_args=($(tr '\n' ' ' <"${RESUME_HINT_FILE}"))
  else
    resume_args=(--last)
  fi
  prompt="$(feedback_instruction)"
  nohup codex exec resume "${resume_args[@]}" "${prompt}" \
    >>"${RUN_LOG_FILE}" 2>&1 < /dev/null &
}

start_or_resume_codex() {
  if [[ "${HEARTBEAT_ENABLE_RESUME}" == "1" ]]; then
    if start_codex_resume; then
      log "codex_resume_started resume_args_file=${RESUME_HINT_FILE}"
    else
      log "codex_resume_failed fallback=exec"
      start_codex_exec_for_feedback
    fi
  else
    start_codex_exec_for_feedback
  fi

  local agent_pid
  agent_pid=$!
  printf '%s\n' "${agent_pid}" >"${PID_FILE}"
  printf '%s\n' "$(( $(date +%s) + HEARTBEAT_COOLDOWN_SECONDS ))" >"${COOLDOWN_FILE}"
  log "codex_started pid=${agent_pid} run_log=${RUN_LOG_FILE}"
}

clean_pid_file

if cooldown_active; then
  exit 0
fi

if mcp_has_pending_feedback; then
  log "pending_feedback=true session_id=${SESSION_ID}"
  start_or_resume_codex
elif [[ $? -eq 2 ]]; then
  log "pending_feedback=unknown session_id=${SESSION_ID}"
else
  log "pending_feedback=false session_id=${SESSION_ID}"
fi
