#!/usr/bin/env bash
set -euo pipefail

EXIT_USAGE=10
EXIT_MISSING_SSH_KEY=11
EXIT_VM_NOT_FOUND=12
EXIT_VM_START_FAILED=13
EXIT_VM_WAIT_IP_TIMEOUT=14
EXIT_VM_WAIT_SSH_TIMEOUT=15
EXIT_RESUME_FAILED=16

show_help() {
  cat <<'USAGE'
Usage:
  resume_issue_agent.sh <prompt> <issue-number> [vm-user] [--observe [lines]]
  resume_issue_agent.sh --prompt <prompt> (--issue-number <number> | --vm-name <name>) [options]

SSH into a VM and request a guest agent resume command from the guest user's home
directory.

Arguments (legacy positional mode):
  prompt        Prompt passed to the guest agent runner.
  issue-number  Issue identifier used to derive the VM name.
  vm-user       Guest user to connect as. Default: agent

Options:
  --prompt TEXT               Prompt passed to the guest agent runner.
  --issue-number NUMBER       Issue identifier used to derive VM name (`vm-<issue-number>`).
  --vm-name NAME              Explicit libvirt domain name.
  --session-id ID             Session identifier for host-worker logs/state.
  --dispatch-id ID            Correlation identifier written into the guest agent state file.
  --resume-thread-id ID       Resume a specific agent thread/session id instead of starting a new one.
  --mcp-gateway-url URL       Per-run MCP gateway URL override for the guest agent invocation.
  --vm-user USER              Guest user to connect as. Default: agent
  --ssh-user USER             Privileged guest SSH user for host control/log access.
                              Default: agent-admin, falling back to --vm-user when reachable.
  --agent-log-dir PATH        Root-owned guest log directory. Default:
                              /var/log/agent-sandbox/agent-runs
  --resume-mode MODE          Resume mode: same_context or new_context. Default: same_context
  --worker-mode               Disable TTY allocation and run non-interactive worker mode.
  --dispatch-only             In worker mode, dispatch the guest resume in the background and
                              return after launch instead of waiting for completion.
  --host-state-file PATH      Host JSON state file that stores wake attempt diagnostics.
  --host-control-state-path P Host JSON file that stores durable agent control state by VM.
  --observe [LINES]           Tail the guest Codex log after resuming instead of returning
                              immediately. Default lines: 10
  -h, --help                  Show this help text.

Environment:
  SSH_PRIVATE_KEY_PATH         Private key for guest SSH access.
                               Default: ~/.ssh/id_ed25519, then ~/.ssh/id_rsa
  VM_WAIT_IP_TIMEOUT_SECONDS   How long to wait for guest IPv4 address. Default: 180
  VM_WAIT_SSH_TIMEOUT_SECONDS  How long to wait for guest SSH to accept connections. Default: 180
  RESUME_COMMAND_TIMEOUT_SECONDS
                               End-to-end timeout for worker-mode resume command. Default: 900
  AGENT_PROVIDER               Agent runner provider. Default: codex
  AGENT_MODEL                  Model passed to guest agent runner commands. Required.
USAGE
}

iso_utc_now() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

write_host_state_attempt() {
  local host_state_file="$1"
  local session_id="$2"
  local outcome="$3"
  local error_message="${4:-}"
  local attempt_at

  [[ -n "${host_state_file}" ]] || return 0
  [[ -n "${session_id}" ]] || return 0

  attempt_at="$(iso_utc_now)"

  python3 - "${host_state_file}" "${session_id}" "${attempt_at}" "${outcome}" "${error_message}" <<'PY'
import json
import os
import tempfile
import sys

path = sys.argv[1]
session_id = sys.argv[2]
attempt_at = sys.argv[3]
outcome = sys.argv[4]
error = sys.argv[5]

payload = {}
if os.path.exists(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        payload = {}

if not isinstance(payload, dict):
    payload = {}

sessions = payload.get("sessions")
if not isinstance(sessions, dict):
    sessions = {}
    payload["sessions"] = sessions

entry = sessions.get(session_id)
if not isinstance(entry, dict):
    entry = {}

entry["last_attempt_at"] = attempt_at
entry["outcome"] = outcome
if error:
    entry["error"] = error
else:
    entry.pop("error", None)

sessions[session_id] = entry

os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
fd, tmp_path = tempfile.mkstemp(prefix="resume-state-", suffix=".tmp", dir=os.path.dirname(path) or ".")
os.close(fd)
with open(tmp_path, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
os.replace(tmp_path, path)
PY
}

read_host_control_state_status() {
  local host_control_state_path="$1"
  local vm_name="$2"

  [[ -n "${host_control_state_path}" ]] || return 0

  python3 - "${host_control_state_path}" "${vm_name}" <<'PY'
import json
import os
import sys

path = sys.argv[1]
vm_name = sys.argv[2]

if not os.path.exists(path):
    raise SystemExit(0)
try:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)
if not isinstance(payload, dict):
    raise SystemExit(0)
entry = payload.get(vm_name)
if not isinstance(entry, dict):
    raise SystemExit(0)
status = str(entry.get("status", "")).strip()
if status:
    print(status)
PY
}

resolve_private_key_path() {
  local caller_user

  caller_user="${SUDO_USER:-${USER:-}}"
  CALLER_HOME="${CALLER_HOME:-${HOME:-}}"
  if [[ -n "${caller_user}" ]]; then
    CALLER_HOME="$(getent passwd "${caller_user}" | cut -d: -f6 || printf '%s\n' "${CALLER_HOME}")"
  fi

  SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
  if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
    SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
  fi

  if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
    return 1
  fi
  return 0
}

parse_observe_args() {
  if [[ $# -eq 0 ]]; then
    return 0
  fi
  if [[ "${1:-}" != "--observe" ]]; then
    return 1
  fi
  OBSERVE=1
  shift
  if [[ $# -gt 0 ]]; then
    OBSERVE_LINES="$1"
    shift
  fi
  [[ $# -eq 0 ]]
}

parse_args() {
  PROMPT=""
  ISSUE_NUMBER=""
  VM_NAME=""
  SESSION_ID=""
  DISPATCH_ID=""
  RESUME_THREAD_ID=""
  MCP_GATEWAY_URL=""
  VM_USER="agent"
  SSH_USER="${VM_SSH_USER:-agent-admin}"
  AGENT_RUN_LOG_DIR="${AGENT_RUN_LOG_DIR:-/var/log/agent-sandbox/agent-runs}"
  RESUME_MODE="same_context"
  WORKER_MODE=0
  DISPATCH_ONLY=0
  HOST_STATE_FILE=""
  HOST_CONTROL_STATE_PATH=""
  OBSERVE=0
  OBSERVE_LINES=10

  if [[ $# -eq 0 ]]; then
    return 1
  fi

  if [[ "$1" != --* ]]; then
    if [[ $# -lt 2 ]]; then
      return 1
    fi
    PROMPT="$1"
    ISSUE_NUMBER="$2"
    shift 2
    if [[ $# -gt 0 && "${1:-}" != "--observe" && "${1:-}" != "--host-control-state-path" ]]; then
      VM_USER="$1"
      shift
    fi
    VM_NAME="vm-${ISSUE_NUMBER}"
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --observe)
          OBSERVE=1
          shift
          if [[ $# -gt 0 && "${1:-}" != --* ]]; then
            OBSERVE_LINES="$1"
            shift
          fi
          ;;
        --host-control-state-path)
          HOST_CONTROL_STATE_PATH="${2:-}"
          shift 2
          ;;
        *)
          return 1
          ;;
      esac
    done
  else
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --prompt)
          PROMPT="${2:-}"
          shift 2
          ;;
        --issue-number)
          ISSUE_NUMBER="${2:-}"
          shift 2
          ;;
        --vm-name)
          VM_NAME="${2:-}"
          shift 2
          ;;
        --session-id)
          SESSION_ID="${2:-}"
          shift 2
          ;;
        --dispatch-id)
          DISPATCH_ID="${2:-}"
          shift 2
          ;;
        --resume-thread-id)
          RESUME_THREAD_ID="${2:-}"
          shift 2
          ;;
        --mcp-gateway-url)
          MCP_GATEWAY_URL="${2:-}"
          shift 2
          ;;
        --vm-user)
          VM_USER="${2:-}"
          shift 2
          ;;
        --ssh-user)
          SSH_USER="${2:-}"
          shift 2
          ;;
        --agent-log-dir)
          AGENT_RUN_LOG_DIR="${2:-}"
          shift 2
          ;;
        --resume-mode)
          RESUME_MODE="${2:-}"
          shift 2
          ;;
        --worker-mode)
          WORKER_MODE=1
          shift
          ;;
        --dispatch-only)
          DISPATCH_ONLY=1
          shift
          ;;
        --host-state-file)
          HOST_STATE_FILE="${2:-}"
          shift 2
          ;;
        --host-control-state-path)
          HOST_CONTROL_STATE_PATH="${2:-}"
          shift 2
          ;;
        --observe)
          OBSERVE=1
          shift
          if [[ $# -gt 0 && "${1:-}" != --* ]]; then
            OBSERVE_LINES="$1"
            shift
          fi
          ;;
        -h|--help)
          show_help
          exit 0
          ;;
        *)
          echo "Unknown argument: $1" >&2
          return 1
          ;;
      esac
    done
    if [[ -z "${VM_NAME}" && -n "${ISSUE_NUMBER}" ]]; then
      VM_NAME="vm-${ISSUE_NUMBER}"
    fi
  fi

  if [[ -z "${PROMPT}" || -z "${VM_NAME}" ]]; then
    return 1
  fi
  if [[ -z "${VM_USER}" || -z "${SSH_USER}" || -z "${AGENT_RUN_LOG_DIR}" ]]; then
    return 1
  fi
  if [[ "${RESUME_MODE}" != "same_context" && "${RESUME_MODE}" != "new_context" ]]; then
    echo "resume mode must be same_context or new_context: ${RESUME_MODE}" >&2
    return 1
  fi
  if ! [[ "${OBSERVE_LINES}" =~ ^[1-9][0-9]*$ ]]; then
    echo "lines must be a positive integer: ${OBSERVE_LINES}" >&2
    return 1
  fi
  if (( WORKER_MODE )) && (( OBSERVE )); then
    echo "--observe is not supported with --worker-mode" >&2
    return 1
  fi
  if (( DISPATCH_ONLY )) && (( ! WORKER_MODE )); then
    echo "--dispatch-only requires --worker-mode" >&2
    return 1
  fi
  return 0
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"
# shellcheck source=lib/agent_provider_assets.sh
source "${SCRIPT_DIR}/lib/agent_provider_assets.sh"
AGENT_RUNNER_LIB_B64="$(base64 -w0 "${SCRIPT_DIR}/lib/agent_runner.sh")"
AGENT_STATE_LIB_B64="$(base64 -w0 "${SCRIPT_DIR}/lib/agent_state.sh")"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  show_help
  exit 0
fi

if ! parse_args "$@"; then
  show_help >&2
  exit "${EXIT_USAGE}"
fi

AGENT_PROVIDER="${AGENT_PROVIDER:-codex}"
AGENT_MODEL="${AGENT_MODEL:-}"
AGENT_RUNNER_PROVIDER_LIB_NAME="$(agent_provider_runner_lib_name "${AGENT_PROVIDER}")"
AGENT_RUNNER_PROVIDER_LIB_PATH="$(agent_provider_runner_lib_path "${SCRIPT_DIR}/lib" "${AGENT_PROVIDER}")"
if [[ ! -f "${AGENT_RUNNER_PROVIDER_LIB_PATH}" ]]; then
  echo "Unsupported AGENT_PROVIDER '${AGENT_PROVIDER}': missing runner adapter ${AGENT_RUNNER_PROVIDER_LIB_PATH}" >&2
  exit 1
fi
AGENT_RUNNER_PROVIDER_LIB_B64="$(base64 -w0 "${AGENT_RUNNER_PROVIDER_LIB_PATH}")"
VM_WAIT_IP_TIMEOUT_SECONDS="${VM_WAIT_IP_TIMEOUT_SECONDS:-180}"
VM_WAIT_SSH_TIMEOUT_SECONDS="${VM_WAIT_SSH_TIMEOUT_SECONDS:-180}"
RESUME_COMMAND_TIMEOUT_SECONDS="${RESUME_COMMAND_TIMEOUT_SECONDS:-900}"

if ! resolve_private_key_path; then
  echo "Missing SSH private key: ${SSH_PRIVATE_KEY_PATH:-unset}" >&2
  echo "Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key." >&2
  write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "missing-ssh-key"
  exit "${EXIT_MISSING_SSH_KEY}"
fi

if [[ "${AGENT_PROVIDER}" == "codex" && -z "${AGENT_MODEL:-}" ]]; then
  echo "AGENT_MODEL is required for session-bound Codex execution. Set AGENT_MODEL in the host invocation environment." >&2
  exit "${EXIT_USAGE}"
fi

if ! vm_network_vm_exists "${VM_NAME}"; then
  echo "VM not found: ${VM_NAME}" >&2
  write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "vm-not-found"
  exit "${EXIT_VM_NOT_FOUND}"
fi

HOST_CONTROL_STATUS="$(read_host_control_state_status "${HOST_CONTROL_STATE_PATH}" "${VM_NAME}" || true)"
if [[ "${HOST_CONTROL_STATUS}" == "disabled" ]]; then
  echo "Agent is disabled for ${VM_NAME}." >&2
  write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "agent-disabled"
  exit "${EXIT_RESUME_FAILED}"
fi

if [[ "${AGENT_PROVIDER}" == "codex" && -z "${AGENT_MODEL:-}" ]]; then
  echo "AGENT_MODEL is required for session-bound Codex execution. Set AGENT_MODEL in the host invocation environment." >&2
  exit "${EXIT_USAGE}"
fi

VM_STATE="$(vm_network_domain_state "${VM_NAME}")"
if [[ "${VM_STATE}" != "running" ]]; then
  if ! vm_network_start "${VM_NAME}"; then
    echo "Failed to start VM: ${VM_NAME}" >&2
    write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "vm-start-failed"
    exit "${EXIT_VM_START_FAILED}"
  fi
fi

if ! VM_IP="$(vm_network_wait_for_ip "${VM_NAME}" "${VM_WAIT_IP_TIMEOUT_SECONDS}")"; then
  write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "wait-ip-timeout"
  exit "${EXIT_VM_WAIT_IP_TIMEOUT}"
fi

if ! vm_network_wait_for_ssh "${VM_NAME}" "${VM_IP}" "${SSH_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_WAIT_SSH_TIMEOUT_SECONDS}" >/dev/null; then
  if [[ "${SSH_USER}" != "${VM_USER}" ]] \
    && vm_network_wait_for_ssh "${VM_NAME}" "${VM_IP}" "${VM_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_WAIT_SSH_TIMEOUT_SECONDS}" >/dev/null; then
    SSH_USER="${VM_USER}"
  else
    write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "wait-ssh-timeout"
    exit "${EXIT_VM_WAIT_SSH_TIMEOUT}"
  fi
fi

PROMPT_B64="$(printf '%s' "${PROMPT}" | base64 -w0)"
mapfile -t SSH_FLAGS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_PRIVATE_KEY_PATH}" "${CALLER_HOME:-${HOME:-}}")
SSH_FLAGS+=(-o ConnectTimeout=5)

if (( WORKER_MODE )); then
  if (( DISPATCH_ONLY )); then
    set +e
    ssh "${SSH_FLAGS[@]}" \
      "${SSH_USER}@${VM_IP}" \
      sudo -n env "AGENT_USER=${VM_USER}" "AGENT_RUN_LOG_DIR=${AGENT_RUN_LOG_DIR}" "PROMPT_B64=${PROMPT_B64}" "SESSION_ID=${SESSION_ID}" "DISPATCH_ID=${DISPATCH_ID}" "RESUME_THREAD_ID=${RESUME_THREAD_ID}" "AGENT_SANDBOX_MCP_GATEWAY_URL=${MCP_GATEWAY_URL}" "AGENT_BRANCH_SUFFIX=${VM_NAME}" "AGENT_PROVIDER=${AGENT_PROVIDER}" "AGENT_MODEL=${AGENT_MODEL}" "RESUME_MODE=${RESUME_MODE}" "HOST_CONTROL_STATUS=${HOST_CONTROL_STATUS}" "AGENT_RUNNER_LIB_B64=${AGENT_RUNNER_LIB_B64}" "AGENT_RUNNER_PROVIDER_LIB_NAME=${AGENT_RUNNER_PROVIDER_LIB_NAME}" "AGENT_RUNNER_PROVIDER_LIB_B64=${AGENT_RUNNER_PROVIDER_LIB_B64}" "AGENT_STATE_LIB_B64=${AGENT_STATE_LIB_B64}" bash -s <<'REMOTE'
set -euo pipefail

AGENT_USER="${AGENT_USER:-agent}"
AGENT_HOME="$(getent passwd "${AGENT_USER}" | cut -d: -f6)"
if [[ -z "${AGENT_HOME}" ]]; then
  echo "Unknown agent user: ${AGENT_USER}" >&2
  exit 1
fi
export HOME="${AGENT_HOME}" USER="${AGENT_USER}" LOGNAME="${AGENT_USER}"
cd "${AGENT_HOME}"
RUN_LOG_DIR="${AGENT_RUN_LOG_DIR:-/var/log/agent-sandbox/agent-runs}"
install -d -m 0700 -o "${AGENT_USER}" -g "${AGENT_USER}" "${AGENT_HOME}/.agent-sandbox"
install -d -m 0700 -o root -g root "${RUN_LOG_DIR}"
RUN_AS_AGENT=(runuser -u "${AGENT_USER}" -- env HOME="${AGENT_HOME}" USER="${AGENT_USER}" LOGNAME="${AGENT_USER}" AGENT_SANDBOX_MCP_GATEWAY_URL="${AGENT_SANDBOX_MCP_GATEWAY_URL:-}")

resolve_primary_agent_paths() {
  local log_dir="$1"
  local resolved_repo_name="${GUEST_REPO_NAME:-}"
  local candidate_names=()
  local path
  local name

  if [[ -n "${resolved_repo_name}" ]]; then
    printf '%s\n' "${resolved_repo_name}"
    return 0
  fi

  shopt -s nullglob
  for path in "${log_dir}"/agent-*.pid "${log_dir}"/agent-*.state; do
    name="$(basename "${path}")"
    name="${name#agent-}"
    name="${name%.pid}"
    name="${name%.state}"
    if [[ -n "${name}" ]]; then
      candidate_names+=("${name}")
    fi
  done
  shopt -u nullglob

  if [[ ${#candidate_names[@]} -eq 0 ]]; then
    return 1
  fi

  mapfile -t candidate_names < <(printf '%s\n' "${candidate_names[@]}" | sort -u)
  if [[ ${#candidate_names[@]} -ne 1 ]]; then
    return 1
  fi

  printf '%s\n' "${candidate_names[0]}"
}

AGENT_RUNNER_TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${AGENT_RUNNER_TMP_DIR}"' EXIT
printf '%s' "${AGENT_RUNNER_LIB_B64}" | base64 -d > "${AGENT_RUNNER_TMP_DIR}/agent_runner.sh"
printf '%s' "${AGENT_RUNNER_PROVIDER_LIB_B64}" | base64 -d > "${AGENT_RUNNER_TMP_DIR}/${AGENT_RUNNER_PROVIDER_LIB_NAME}"
printf '%s' "${AGENT_STATE_LIB_B64}" | base64 -d > "${AGENT_RUNNER_TMP_DIR}/agent_state.sh"
# shellcheck source=/dev/null
source "${AGENT_RUNNER_TMP_DIR}/agent_runner.sh"
source "${AGENT_RUNNER_TMP_DIR}/agent_state.sh"

LOG_DIR="${HOME}/.agent-sandbox"
RESOLVED_REPO_NAME="$(resolve_primary_agent_paths "${LOG_DIR}" || true)"
if [[ -n "${RESOLVED_REPO_NAME}" ]]; then
  RUN_LOG_FILE="${RUN_LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
  PID_FILE="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.pid"
  STATE_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.state"
  AGENT_STATE_REPO_NAME="${RESOLVED_REPO_NAME}"
else
  RUN_LOG_FILE="${RUN_LOG_DIR}/codex-heartbeat-run.log"
  PID_FILE="${LOG_DIR}/codex-heartbeat.pid"
  STATE_PATH="${LOG_DIR}/codex-resume-last.state"
  AGENT_STATE_REPO_NAME="resume-last"
fi
PROMPT="$(python3 -c 'import base64, os; print(base64.b64decode(os.environ["PROMPT_B64"]).decode("utf-8"))')"

install -d -m 0700 -o "${AGENT_USER}" -g "${AGENT_USER}" "${LOG_DIR}/lib"
printf '%s' "${AGENT_RUNNER_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/agent_runner.sh"
printf '%s' "${AGENT_RUNNER_PROVIDER_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/${AGENT_RUNNER_PROVIDER_LIB_NAME}"
printf '%s' "${AGENT_STATE_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/agent_state.sh"
chown "${AGENT_USER}:${AGENT_USER}" "${LOG_DIR}/lib/agent_runner.sh" "${LOG_DIR}/lib/${AGENT_RUNNER_PROVIDER_LIB_NAME}" "${LOG_DIR}/lib/agent_state.sh"
chmod 0644 "${LOG_DIR}/lib/agent_runner.sh" "${LOG_DIR}/lib/${AGENT_RUNNER_PROVIDER_LIB_NAME}" "${LOG_DIR}/lib/agent_state.sh"
touch "${RUN_LOG_FILE}"
chown root:root "${RUN_LOG_FILE}"
chmod 0600 "${RUN_LOG_FILE}"

if [[ -n "${HOST_CONTROL_STATUS}" ]]; then
  write_agent_control_state "${HOST_CONTROL_STATUS}" "host-sync"
  chown "${AGENT_USER}:${AGENT_USER}" "${LOG_DIR}/agent-control.state"
  chmod 0600 "${LOG_DIR}/agent-control.state"
fi

ensure_agent_enabled

write_state() {
  local status="$1"
  local phase="$2"
  local exit_code="${3:-}"
  local now
  local tmp_path

  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  tmp_path="${STATE_PATH}.tmp"
  {
    printf "status=%s\n" "${status}"
    printf "phase=%s\n" "${phase}"
    printf "repo=%s\n" "resume-last"
    printf "pid=%s\n" "$$"
    printf "updated_at=%s\n" "${now}"
    if [[ -n "${exit_code}" ]]; then
      printf "exit_code=%s\n" "${exit_code}"
    fi
  } > "${tmp_path}"
  mv "${tmp_path}" "${STATE_PATH}"
}

if [[ -f "${PID_FILE}" ]]; then
  EXISTING_PID="$(cat "${PID_FILE}")"
  if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" >/dev/null 2>&1; then
    exit 0
  fi
fi

nohup "${RUN_AS_AGENT[@]}" \
  SESSION_ID="${SESSION_ID}" \
  AGENT_DISPATCH_ID="${DISPATCH_ID}" \
  AGENT_BRANCH_SUFFIX="${AGENT_BRANCH_SUFFIX}" \
  AGENT_PROVIDER="${AGENT_PROVIDER}" \
  AGENT_MODEL="${AGENT_MODEL}" \
  AGENT_SANDBOX_MCP_GATEWAY_URL="${AGENT_SANDBOX_MCP_GATEWAY_URL}" \
  RESUME_MODE="${RESUME_MODE}" \
  RESUME_THREAD_ID="${RESUME_THREAD_ID}" \
  PROMPT="${PROMPT}" \
  RUN_LOG_FILE="${RUN_LOG_FILE}" \
  STATE_PATH="${STATE_PATH}" \
  AGENT_STATE_REPO_NAME="${AGENT_STATE_REPO_NAME}" \
bash -lc '
set -euo pipefail
. "$HOME/.profile" >/dev/null 2>&1 || true
. "$HOME/.agent-sandbox/lib/agent_runner.sh"
. "$HOME/.agent-sandbox/lib/agent_state.sh"

ensure_agent_enabled

write_state "running" "resume"
trap on_exit_write_idle EXIT

if [[ -n "${RESUME_THREAD_ID:-}" ]]; then
  agent_runner_resume_thread \
    "${RESUME_THREAD_ID}" \
    --dangerously-bypass-approvals-and-sandbox \
    --skip-git-repo-check \
    --model "${AGENT_MODEL}" \
    "${PROMPT}"
elif [[ "${RESUME_MODE}" == "new_context" ]]; then
  TARGET_DIR="${HOME}"
  if [[ -n "${AGENT_STATE_REPO_NAME}" && "${AGENT_STATE_REPO_NAME}" != "resume-last" && -d "${HOME}/${AGENT_STATE_REPO_NAME}" ]]; then
    TARGET_DIR="${HOME}/${AGENT_STATE_REPO_NAME}"
  fi
  agent_runner_exec \
    -s danger-full-access \
    --dangerously-bypass-approvals-and-sandbox \
    --skip-git-repo-check \
    --model "${AGENT_MODEL}" \
    -C "${TARGET_DIR}" \
    "${PROMPT}"
else
  agent_runner_resume_last \
    --dangerously-bypass-approvals-and-sandbox \
    --skip-git-repo-check \
    --model "${AGENT_MODEL}" \
    "${PROMPT}"
fi
' >>"${RUN_LOG_FILE}" 2>&1 < /dev/null &
AGENT_PID=$!
printf '%s\n' "${AGENT_PID}" >"${PID_FILE}"
chown "${AGENT_USER}:${AGENT_USER}" "${PID_FILE}"
chmod 0600 "${PID_FILE}"
REMOTE
    resume_exit_code="$?"
    set -e

    if [[ "${resume_exit_code}" -eq 0 ]]; then
      write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "ok" ""
      exit 0
    fi
    write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "resume-exit-${resume_exit_code}"
    echo "Resume dispatch failed for ${VM_NAME} with exit code ${resume_exit_code}." >&2
    exit "${EXIT_RESUME_FAILED}"
  fi
  set +e
  timeout --foreground "${RESUME_COMMAND_TIMEOUT_SECONDS}s" \
    ssh "${SSH_FLAGS[@]}" \
    "${SSH_USER}@${VM_IP}" \
    sudo -n env "AGENT_USER=${VM_USER}" "AGENT_RUN_LOG_DIR=${AGENT_RUN_LOG_DIR}" "PROMPT_B64=${PROMPT_B64}" "SESSION_ID=${SESSION_ID}" "DISPATCH_ID=${DISPATCH_ID}" "RESUME_THREAD_ID=${RESUME_THREAD_ID}" "AGENT_SANDBOX_MCP_GATEWAY_URL=${MCP_GATEWAY_URL}" "AGENT_BRANCH_SUFFIX=${VM_NAME}" "AGENT_PROVIDER=${AGENT_PROVIDER}" "AGENT_MODEL=${AGENT_MODEL}" "RESUME_MODE=${RESUME_MODE}" "HOST_CONTROL_STATUS=${HOST_CONTROL_STATUS}" "AGENT_RUNNER_LIB_B64=${AGENT_RUNNER_LIB_B64}" "AGENT_RUNNER_PROVIDER_LIB_NAME=${AGENT_RUNNER_PROVIDER_LIB_NAME}" "AGENT_RUNNER_PROVIDER_LIB_B64=${AGENT_RUNNER_PROVIDER_LIB_B64}" "AGENT_STATE_LIB_B64=${AGENT_STATE_LIB_B64}" bash -s <<'REMOTE'
set -euo pipefail

AGENT_USER="${AGENT_USER:-agent}"
AGENT_HOME="$(getent passwd "${AGENT_USER}" | cut -d: -f6)"
if [[ -z "${AGENT_HOME}" ]]; then
  echo "Unknown agent user: ${AGENT_USER}" >&2
  exit 1
fi
export HOME="${AGENT_HOME}" USER="${AGENT_USER}" LOGNAME="${AGENT_USER}"
cd "${AGENT_HOME}"
RUN_LOG_DIR="${AGENT_RUN_LOG_DIR:-/var/log/agent-sandbox/agent-runs}"
install -d -m 0700 -o "${AGENT_USER}" -g "${AGENT_USER}" "${AGENT_HOME}/.agent-sandbox"
install -d -m 0700 -o root -g root "${RUN_LOG_DIR}"
RUN_AS_AGENT=(runuser -u "${AGENT_USER}" -- env HOME="${AGENT_HOME}" USER="${AGENT_USER}" LOGNAME="${AGENT_USER}" AGENT_SANDBOX_MCP_GATEWAY_URL="${AGENT_SANDBOX_MCP_GATEWAY_URL:-}")

resolve_primary_agent_paths() {
  local log_dir="$1"
  local resolved_repo_name="${GUEST_REPO_NAME:-}"
  local candidate_names=()
  local path
  local name

  if [[ -n "${resolved_repo_name}" ]]; then
    printf '%s\n' "${resolved_repo_name}"
    return 0
  fi

  shopt -s nullglob
  for path in "${log_dir}"/agent-*.pid "${log_dir}"/agent-*.state; do
    name="$(basename "${path}")"
    name="${name#agent-}"
    name="${name%.pid}"
    name="${name%.state}"
    if [[ -n "${name}" ]]; then
      candidate_names+=("${name}")
    fi
  done
  shopt -u nullglob

  if [[ ${#candidate_names[@]} -eq 0 ]]; then
    return 1
  fi

  mapfile -t candidate_names < <(printf '%s\n' "${candidate_names[@]}" | sort -u)
  if [[ ${#candidate_names[@]} -ne 1 ]]; then
    return 1
  fi

  printf '%s\n' "${candidate_names[0]}"
}

AGENT_RUNNER_TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${AGENT_RUNNER_TMP_DIR}"' EXIT
printf '%s' "${AGENT_RUNNER_LIB_B64}" | base64 -d > "${AGENT_RUNNER_TMP_DIR}/agent_runner.sh"
printf '%s' "${AGENT_RUNNER_PROVIDER_LIB_B64}" | base64 -d > "${AGENT_RUNNER_TMP_DIR}/${AGENT_RUNNER_PROVIDER_LIB_NAME}"
printf '%s' "${AGENT_STATE_LIB_B64}" | base64 -d > "${AGENT_RUNNER_TMP_DIR}/agent_state.sh"
# shellcheck source=/dev/null
source "${AGENT_RUNNER_TMP_DIR}/agent_runner.sh"
source "${AGENT_RUNNER_TMP_DIR}/agent_state.sh"

LOG_DIR="${HOME}/.agent-sandbox"
RESOLVED_REPO_NAME="$(resolve_primary_agent_paths "${LOG_DIR}" || true)"
if [[ -n "${RESOLVED_REPO_NAME}" ]]; then
  RUN_LOG_FILE="${RUN_LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
  STATE_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.state"
  AGENT_STATE_REPO_NAME="${RESOLVED_REPO_NAME}"
else
  RUN_LOG_FILE="${RUN_LOG_DIR}/codex-heartbeat-run.log"
  STATE_PATH="${LOG_DIR}/codex-resume-last.state"
  AGENT_STATE_REPO_NAME="resume-last"
fi
AGENT_DISPATCH_ID="${DISPATCH_ID:-}"
PROMPT="$(python3 -c 'import base64, os; print(base64.b64decode(os.environ["PROMPT_B64"]).decode("utf-8"))')"
install -d -m 0700 -o "${AGENT_USER}" -g "${AGENT_USER}" "${LOG_DIR}/lib"
printf '%s' "${AGENT_RUNNER_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/agent_runner.sh"
printf '%s' "${AGENT_RUNNER_PROVIDER_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/${AGENT_RUNNER_PROVIDER_LIB_NAME}"
printf '%s' "${AGENT_STATE_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/agent_state.sh"
chown "${AGENT_USER}:${AGENT_USER}" "${LOG_DIR}/lib/agent_runner.sh" "${LOG_DIR}/lib/${AGENT_RUNNER_PROVIDER_LIB_NAME}" "${LOG_DIR}/lib/agent_state.sh"
chmod 0644 "${LOG_DIR}/lib/agent_runner.sh" "${LOG_DIR}/lib/${AGENT_RUNNER_PROVIDER_LIB_NAME}" "${LOG_DIR}/lib/agent_state.sh"
touch "${RUN_LOG_FILE}"
chown root:root "${RUN_LOG_FILE}"
chmod 0600 "${RUN_LOG_FILE}"

if [[ -n "${HOST_CONTROL_STATUS}" ]]; then
  write_agent_control_state "${HOST_CONTROL_STATUS}" "host-sync"
  chown "${AGENT_USER}:${AGENT_USER}" "${LOG_DIR}/agent-control.state"
  chmod 0600 "${LOG_DIR}/agent-control.state"
fi

ensure_agent_enabled

"${RUN_AS_AGENT[@]}" \
  SESSION_ID="${SESSION_ID}" \
  AGENT_DISPATCH_ID="${DISPATCH_ID}" \
  RESUME_THREAD_ID="${RESUME_THREAD_ID}" \
  AGENT_BRANCH_SUFFIX="${AGENT_BRANCH_SUFFIX}" \
  AGENT_PROVIDER="${AGENT_PROVIDER}" \
  AGENT_MODEL="${AGENT_MODEL}" \
  AGENT_SANDBOX_MCP_GATEWAY_URL="${AGENT_SANDBOX_MCP_GATEWAY_URL}" \
  RESUME_MODE="${RESUME_MODE}" \
  PROMPT="${PROMPT}" \
  STATE_PATH="${STATE_PATH}" \
  AGENT_STATE_REPO_NAME="${AGENT_STATE_REPO_NAME}" \
bash -lc '
set -euo pipefail
. "$HOME/.profile" >/dev/null 2>&1 || true
. "$HOME/.agent-sandbox/lib/agent_runner.sh"
. "$HOME/.agent-sandbox/lib/agent_state.sh"

ensure_agent_enabled

write_state "running" "resume"
trap on_exit_write_idle EXIT

if [[ -n "${RESUME_THREAD_ID:-}" ]]; then
  agent_runner_resume_thread \
    "${RESUME_THREAD_ID}" \
    --dangerously-bypass-approvals-and-sandbox \
    --skip-git-repo-check \
    --model "${AGENT_MODEL}" \
    "${PROMPT}"
elif [[ "${RESUME_MODE}" == "new_context" ]]; then
  TARGET_DIR="${HOME}"
  if [[ -n "${AGENT_STATE_REPO_NAME}" && "${AGENT_STATE_REPO_NAME}" != "resume-last" && -d "${HOME}/${AGENT_STATE_REPO_NAME}" ]]; then
    TARGET_DIR="${HOME}/${AGENT_STATE_REPO_NAME}"
  fi
  agent_runner_exec \
    -s danger-full-access \
    --dangerously-bypass-approvals-and-sandbox \
    --skip-git-repo-check \
    --model "${AGENT_MODEL}" \
    -C "${TARGET_DIR}" \
    "${PROMPT}"
else
  agent_runner_resume_last \
    --dangerously-bypass-approvals-and-sandbox \
    --skip-git-repo-check \
    --model "${AGENT_MODEL}" \
    "${PROMPT}"
fi
' 2>&1 | tee -a "${RUN_LOG_FILE}"
REMOTE
  resume_exit_code="$?"
  set -e

  if [[ "${resume_exit_code}" -eq 0 ]]; then
    write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "ok" ""
    exit 0
  fi
  if [[ "${resume_exit_code}" -eq 124 ]]; then
    write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "resume-timeout"
    echo "Resume command timed out after ${RESUME_COMMAND_TIMEOUT_SECONDS}s for ${VM_NAME}." >&2
    exit "${EXIT_RESUME_FAILED}"
  fi
  write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "resume-exit-${resume_exit_code}"
  echo "Resume command failed for ${VM_NAME} with exit code ${resume_exit_code}." >&2
  exit "${EXIT_RESUME_FAILED}"
fi

resume_output="$(
  ssh "${SSH_FLAGS[@]}" \
    "${SSH_USER}@${VM_IP}" \
    sudo -n env "AGENT_USER=${VM_USER}" "AGENT_RUN_LOG_DIR=${AGENT_RUN_LOG_DIR}" "PROMPT_B64=${PROMPT_B64}" "SESSION_ID=${SESSION_ID}" "DISPATCH_ID=${DISPATCH_ID}" "RESUME_THREAD_ID=${RESUME_THREAD_ID}" "AGENT_SANDBOX_MCP_GATEWAY_URL=${MCP_GATEWAY_URL}" "AGENT_BRANCH_SUFFIX=${VM_NAME}" "AGENT_PROVIDER=${AGENT_PROVIDER}" "AGENT_MODEL=${AGENT_MODEL}" "RESUME_MODE=${RESUME_MODE}" "HOST_CONTROL_STATUS=${HOST_CONTROL_STATUS}" "AGENT_RUNNER_LIB_B64=${AGENT_RUNNER_LIB_B64}" "AGENT_RUNNER_PROVIDER_LIB_NAME=${AGENT_RUNNER_PROVIDER_LIB_NAME}" "AGENT_RUNNER_PROVIDER_LIB_B64=${AGENT_RUNNER_PROVIDER_LIB_B64}" "AGENT_STATE_LIB_B64=${AGENT_STATE_LIB_B64}" bash -s <<'REMOTE'
set -euo pipefail

AGENT_USER="${AGENT_USER:-agent}"
AGENT_HOME="$(getent passwd "${AGENT_USER}" | cut -d: -f6)"
if [[ -z "${AGENT_HOME}" ]]; then
  echo "Unknown agent user: ${AGENT_USER}" >&2
  exit 1
fi
export HOME="${AGENT_HOME}" USER="${AGENT_USER}" LOGNAME="${AGENT_USER}"
cd "${AGENT_HOME}"
RUN_LOG_DIR="${AGENT_RUN_LOG_DIR:-/var/log/agent-sandbox/agent-runs}"
install -d -m 0700 -o "${AGENT_USER}" -g "${AGENT_USER}" "${AGENT_HOME}/.agent-sandbox"
install -d -m 0700 -o root -g root "${RUN_LOG_DIR}"
RUN_AS_AGENT=(runuser -u "${AGENT_USER}" -- env HOME="${AGENT_HOME}" USER="${AGENT_USER}" LOGNAME="${AGENT_USER}" AGENT_SANDBOX_MCP_GATEWAY_URL="${AGENT_SANDBOX_MCP_GATEWAY_URL:-}")

resolve_primary_agent_paths() {
  local log_dir="$1"
  local resolved_repo_name="${GUEST_REPO_NAME:-}"
  local candidate_names=()
  local path
  local name

  if [[ -n "${resolved_repo_name}" ]]; then
    printf '%s\n' "${resolved_repo_name}"
    return 0
  fi

  shopt -s nullglob
  for path in "${log_dir}"/agent-*.pid "${log_dir}"/agent-*.state; do
    name="$(basename "${path}")"
    name="${name#agent-}"
    name="${name%.pid}"
    name="${name%.state}"
    if [[ -n "${name}" ]]; then
      candidate_names+=("${name}")
    fi
  done
  shopt -u nullglob

  if [[ ${#candidate_names[@]} -eq 0 ]]; then
    return 1
  fi

  mapfile -t candidate_names < <(printf '%s\n' "${candidate_names[@]}" | sort -u)
  if [[ ${#candidate_names[@]} -ne 1 ]]; then
    return 1
  fi

  printf '%s\n' "${candidate_names[0]}"
}

AGENT_RUNNER_TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${AGENT_RUNNER_TMP_DIR}"' EXIT
printf '%s' "${AGENT_RUNNER_LIB_B64}" | base64 -d > "${AGENT_RUNNER_TMP_DIR}/agent_runner.sh"
printf '%s' "${AGENT_RUNNER_PROVIDER_LIB_B64}" | base64 -d > "${AGENT_RUNNER_TMP_DIR}/${AGENT_RUNNER_PROVIDER_LIB_NAME}"
printf '%s' "${AGENT_STATE_LIB_B64}" | base64 -d > "${AGENT_RUNNER_TMP_DIR}/agent_state.sh"
# shellcheck source=/dev/null
source "${AGENT_RUNNER_TMP_DIR}/agent_runner.sh"
source "${AGENT_RUNNER_TMP_DIR}/agent_state.sh"

LOG_DIR="${HOME}/.agent-sandbox"
RESOLVED_REPO_NAME="$(resolve_primary_agent_paths "${LOG_DIR}" || true)"
if [[ -n "${RESOLVED_REPO_NAME}" ]]; then
  RUN_LOG_FILE="${RUN_LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
  PID_FILE="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.pid"
  STATE_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.state"
  AGENT_STATE_REPO_NAME="${RESOLVED_REPO_NAME}"
else
  RUN_LOG_FILE="${RUN_LOG_DIR}/codex-heartbeat-run.log"
  PID_FILE="${LOG_DIR}/codex-heartbeat.pid"
  STATE_PATH="${LOG_DIR}/codex-resume-last.state"
  AGENT_STATE_REPO_NAME="resume-last"
fi
AGENT_DISPATCH_ID="${DISPATCH_ID:-}"
RESUME_HINT_FILE="${LOG_DIR}/resume-args.txt"
PROMPT="$(python3 -c 'import base64, os; print(base64.b64decode(os.environ["PROMPT_B64"]).decode("utf-8"))')"

install -d -m 0700 -o "${AGENT_USER}" -g "${AGENT_USER}" "${LOG_DIR}/lib"
printf '%s' "${AGENT_RUNNER_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/agent_runner.sh"
printf '%s' "${AGENT_RUNNER_PROVIDER_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/${AGENT_RUNNER_PROVIDER_LIB_NAME}"
printf '%s' "${AGENT_STATE_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/agent_state.sh"
chown "${AGENT_USER}:${AGENT_USER}" "${LOG_DIR}/lib/agent_runner.sh" "${LOG_DIR}/lib/${AGENT_RUNNER_PROVIDER_LIB_NAME}" "${LOG_DIR}/lib/agent_state.sh"
chmod 0644 "${LOG_DIR}/lib/agent_runner.sh" "${LOG_DIR}/lib/${AGENT_RUNNER_PROVIDER_LIB_NAME}" "${LOG_DIR}/lib/agent_state.sh"
touch "${RUN_LOG_FILE}"
chown root:root "${RUN_LOG_FILE}"
chmod 0600 "${RUN_LOG_FILE}"

if [[ -n "${HOST_CONTROL_STATUS}" ]]; then
  write_agent_control_state "${HOST_CONTROL_STATUS}" "host-sync"
  chown "${AGENT_USER}:${AGENT_USER}" "${LOG_DIR}/agent-control.state"
  chmod 0600 "${LOG_DIR}/agent-control.state"
fi

ensure_agent_enabled

write_state() {
  local status="$1"
  local phase="$2"
  local exit_code="${3:-}"
  local now
  local tmp_path

  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  tmp_path="${STATE_PATH}.tmp"
  {
    printf "status=%s\n" "${status}"
    printf "phase=%s\n" "${phase}"
    printf "repo=%s\n" "resume-last"
    printf "pid=%s\n" "$$"
    printf "updated_at=%s\n" "${now}"
    if [[ -n "${exit_code}" ]]; then
      printf "exit_code=%s\n" "${exit_code}"
    fi
  } > "${tmp_path}"
  mv "${tmp_path}" "${STATE_PATH}"
}

if [[ -f "${PID_FILE}" ]]; then
  EXISTING_PID="$(cat "${PID_FILE}")"
  if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" >/dev/null 2>&1; then
    echo "Agent already running in guest with PID ${EXISTING_PID}" >&2
    exit 1
  fi
fi

printf '%s\n' "--last" >"${RESUME_HINT_FILE}"

RUN_LOG_START_SIZE=0
if [[ -f "${RUN_LOG_FILE}" ]]; then
  RUN_LOG_START_SIZE="$(wc -c < "${RUN_LOG_FILE}" | tr -d '[:space:]')"
fi

nohup "${RUN_AS_AGENT[@]}" \
  SESSION_ID="${SESSION_ID}" \
  AGENT_DISPATCH_ID="${DISPATCH_ID}" \
  RESUME_THREAD_ID="${RESUME_THREAD_ID}" \
  AGENT_BRANCH_SUFFIX="${AGENT_BRANCH_SUFFIX}" \
  AGENT_PROVIDER="${AGENT_PROVIDER}" \
  AGENT_MODEL="${AGENT_MODEL}" \
  RESUME_MODE="${RESUME_MODE}" \
  PROMPT="${PROMPT}" \
  RUN_LOG_FILE="${RUN_LOG_FILE}" \
  STATE_PATH="${STATE_PATH}" \
  AGENT_STATE_REPO_NAME="${AGENT_STATE_REPO_NAME}" \
bash -lc '
set -euo pipefail
. "$HOME/.profile" >/dev/null 2>&1 || true
. "$HOME/.agent-sandbox/lib/agent_runner.sh"
. "$HOME/.agent-sandbox/lib/agent_state.sh"

ensure_agent_enabled

write_state "running" "resume"
trap on_exit_write_idle EXIT

if [[ -n "${RESUME_THREAD_ID:-}" ]]; then
  agent_runner_resume_thread \
    "${RESUME_THREAD_ID}" \
    --dangerously-bypass-approvals-and-sandbox \
    --skip-git-repo-check \
    --model "${AGENT_MODEL}" \
    "${PROMPT}"
elif [[ "${RESUME_MODE}" == "new_context" ]]; then
  TARGET_DIR="${HOME}"
  if [[ -n "${AGENT_STATE_REPO_NAME}" && "${AGENT_STATE_REPO_NAME}" != "resume-last" && -d "${HOME}/${AGENT_STATE_REPO_NAME}" ]]; then
    TARGET_DIR="${HOME}/${AGENT_STATE_REPO_NAME}"
  fi
  agent_runner_exec \
    -s danger-full-access \
    --dangerously-bypass-approvals-and-sandbox \
    --skip-git-repo-check \
    --model "${AGENT_MODEL}" \
    -C "${TARGET_DIR}" \
    "${PROMPT}"
else
  agent_runner_resume_last \
    --dangerously-bypass-approvals-and-sandbox \
    --skip-git-repo-check \
    --model "${AGENT_MODEL}" \
    "${PROMPT}"
fi
' >>"${RUN_LOG_FILE}" 2>&1 < /dev/null &

AGENT_PID=$!
printf '%s\n' "${AGENT_PID}" >"${PID_FILE}"
chown "${AGENT_USER}:${AGENT_USER}" "${PID_FILE}"
chmod 0600 "${PID_FILE}"
THREAD_ID_RESULT="${RESUME_THREAD_ID:-}"
if [[ -z "${THREAD_ID_RESULT}" && "${AGENT_PROVIDER}" == "codex" && "${RESUME_MODE}" == "new_context" ]]; then
  for _ in $(seq 1 150); do
    if [[ -f "${RUN_LOG_FILE}" ]]; then
      THREAD_ID_RESULT="$(
        tail -c "+$((RUN_LOG_START_SIZE + 1))" "${RUN_LOG_FILE}" 2>/dev/null \
          | awk -F': ' '/^session id: / {thread_id=$2} END {print thread_id}' \
          | tr -d '\r' || true
      )"
      if [[ -n "${THREAD_ID_RESULT}" ]]; then
        break
      fi
    fi
    sleep 0.1
  done
fi
if [[ -n "${THREAD_ID_RESULT}" ]]; then
  printf 'thread_id=%s\n' "${THREAD_ID_RESULT}"
fi
printf 'resumed pid=%s log=%s\n' "${AGENT_PID}" "${RUN_LOG_FILE}"
REMOTE
)"

THREAD_ID_OUTPUT=""
RESUME_SUMMARY=""
while IFS= read -r line; do
  [[ -z "${line}" ]] && continue
  if [[ "${line}" == thread_id=* ]]; then
    THREAD_ID_OUTPUT="${line#thread_id=}"
    continue
  fi
  if [[ -z "${RESUME_SUMMARY}" ]]; then
    RESUME_SUMMARY="${line}"
  else
    RESUME_SUMMARY="${RESUME_SUMMARY}; ${line}"
  fi
done <<< "${resume_output}"

if [[ -n "${THREAD_ID_OUTPUT}" ]]; then
  printf 'thread_id=%s\n' "${THREAD_ID_OUTPUT}"
fi

if (( OBSERVE )); then
  exec ssh "${SSH_FLAGS[@]}" \
    "${SSH_USER}@${VM_IP}" \
    sudo -n env "AGENT_USER=${VM_USER}" "AGENT_RUN_LOG_DIR=${AGENT_RUN_LOG_DIR}" "LINES=${OBSERVE_LINES}" bash -s <<'REMOTE'
set -euo pipefail

AGENT_USER="${AGENT_USER:-agent}"
AGENT_HOME="$(getent passwd "${AGENT_USER}" | cut -d: -f6)"
if [[ -z "${AGENT_HOME}" ]]; then
  echo "Unknown agent user: ${AGENT_USER}" >&2
  exit 1
fi
LOG_DIR="${AGENT_HOME}/.agent-sandbox"
RUN_LOG_DIR="${AGENT_RUN_LOG_DIR:-/var/log/agent-sandbox/agent-runs}"

resolve_primary_agent_paths() {
  local log_dir="$1"
  local candidate_names=()
  local path
  local name

  shopt -s nullglob
  for path in "${log_dir}"/agent-*.pid "${log_dir}"/agent-*.state; do
    name="$(basename "${path}")"
    name="${name#agent-}"
    name="${name%.pid}"
    name="${name%.state}"
    if [[ -n "${name}" ]]; then
      candidate_names+=("${name}")
    fi
  done
  shopt -u nullglob

  if [[ ${#candidate_names[@]} -eq 0 ]]; then
    return 1
  fi

  mapfile -t candidate_names < <(printf '%s\n' "${candidate_names[@]}" | sort -u)
  if [[ ${#candidate_names[@]} -ne 1 ]]; then
    return 1
  fi

  printf '%s\n' "${candidate_names[0]}"
}

RESOLVED_REPO_NAME="$(resolve_primary_agent_paths "${LOG_DIR}" || true)"
if [[ -n "${RESOLVED_REPO_NAME}" ]]; then
  LOG_PATH="${RUN_LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
else
  LOG_PATH="${RUN_LOG_DIR}/codex-heartbeat-run.log"
fi

if [[ ! -f "${LOG_PATH}" ]]; then
  echo "No guest Codex resume log found at ${LOG_PATH}" >&2
  exit 0
fi
tail -n "${LINES}" -f "${LOG_PATH}"
REMOTE
fi

printf 'Agent resumed for %s' "${VM_NAME}"
if [[ -n "${RESUME_SUMMARY}" ]]; then
  printf ' (%s)' "${RESUME_SUMMARY}"
fi
printf '.\n'
