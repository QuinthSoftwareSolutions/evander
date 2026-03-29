#!/usr/bin/env bash
set -euo pipefail

. "${HOME}/.profile" >/dev/null 2>&1 || true

AGENT_PROVIDER="${AGENT_PROVIDER:-codex}"
AGENT_MODEL="${AGENT_MODEL:-gpt-5.4}"
AGENT_SANDBOX_GUEST_HEARTBEAT_PROMPT="${AGENT_SANDBOX_GUEST_HEARTBEAT_PROMPT:-Resume work for this session. Check for pending feedback first, then continue the most recent task.}"

LIB_DIR="${HOME}/.config/agent-sandbox/agent-runner-lib"

if [[ ! -f "${LIB_DIR}/agent_runner.sh" ]]; then
  echo "Missing agent runner library: ${LIB_DIR}/agent_runner.sh" >&2
  exit 1
fi

# shellcheck source=/dev/null
source "${LIB_DIR}/agent_runner.sh"

LOG_DIR="${HOME}/.agent-sandbox"
RUN_LOG_FILE="${LOG_DIR}/codex-heartbeat-run.log"
PID_FILE="${LOG_DIR}/codex-heartbeat.pid"
RESUME_HINT_FILE="${LOG_DIR}/resume-args.txt"

mkdir -p "${LOG_DIR}"

is_active_agent_pid() {
  local pid="$1"
  local cmdline_path="/proc/${pid}/cmdline"
  local cmdline=""

  [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1 || return 1
  [[ -r "${cmdline_path}" ]] || return 1

  cmdline="$(tr '\000' ' ' < "${cmdline_path}" 2>/dev/null || true)"
  [[ -n "${cmdline}" ]] || return 1

  [[ "${cmdline}" == *"agent_runner_exec"* || "${cmdline}" == *"agent_runner_resume_last"* ]]
}

if [[ -f "${PID_FILE}" ]]; then
  EXISTING_PID="$(cat "${PID_FILE}")"
  if is_active_agent_pid "${EXISTING_PID}"; then
    printf 'Guest heartbeat skipped: active resume process pid=%s\n' "${EXISTING_PID}"
    exit 0
  fi
fi

shopt -s nullglob
for state_path in "${LOG_DIR}"/agent-*.state; do
  status="$(awk -F= '$1 == "status" {print substr($0, index($0, "=") + 1); exit}' "${state_path}" 2>/dev/null || true)"
  if [[ "${status}" == "running" ]]; then
    printf 'Guest heartbeat skipped: active agent state=%s\n' "$(basename "${state_path}")"
    exit 0
  fi
done
if [[ -f "${LOG_DIR}/codex-resume-last.state" ]]; then
  status="$(awk -F= '$1 == "status" {print substr($0, index($0, "=") + 1); exit}' "${LOG_DIR}/codex-resume-last.state" 2>/dev/null || true)"
  if [[ "${status}" == "running" ]]; then
    printf 'Guest heartbeat skipped: active agent state=%s\n' "codex-resume-last.state"
    exit 0
  fi
fi
shopt -u nullglob

printf '%s\n' "--last" > "${RESUME_HINT_FILE}"

nohup agent_runner_resume_last \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  --model "${AGENT_MODEL}" \
  "${AGENT_SANDBOX_GUEST_HEARTBEAT_PROMPT}" \
  >>"${RUN_LOG_FILE}" 2>&1 < /dev/null &

AGENT_PID=$!
printf '%s\n' "${AGENT_PID}" > "${PID_FILE}"
printf 'Guest heartbeat resumed agent pid=%s log=%s\n' "${AGENT_PID}" "${RUN_LOG_FILE}"
