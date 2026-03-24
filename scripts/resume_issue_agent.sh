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
  resume_issue_agent.sh <prompt> <issue-number> [vm-user]
  resume_issue_agent.sh --prompt <prompt> --vm-name <vm-name> --session-id <session-id> [options]

SSH into a VM and run a guest Codex resume command from the guest user's home directory.

Arguments (legacy positional mode):
  prompt        Prompt passed to `codex exec resume --last`.
  issue-number  Issue identifier used to derive the VM name.
  vm-user       Guest user to connect as. Default: agent

Options:
  --prompt TEXT               Prompt passed to `codex exec resume --last`.
  --issue-number NUMBER       Issue identifier used to derive VM name (`vm-<issue-number>`).
  --vm-name NAME              Explicit libvirt domain name.
  --session-id ID             Session identifier for host-worker logs/state.
  --vm-user USER              Guest user to connect as. Default: agent
  --worker-mode               Disable TTY allocation and run non-interactive SSH mode.
  --host-state-file PATH      Host JSON state file that stores wake attempt diagnostics.
  -h, --help                  Show this help text.

Environment:
  SSH_PRIVATE_KEY_PATH         Private key for guest SSH access.
                               Default: ~/.ssh/id_ed25519, then ~/.ssh/id_rsa
  VM_WAIT_IP_TIMEOUT_SECONDS   How long to wait for guest IPv4 address. Default: 180
  VM_WAIT_SSH_TIMEOUT_SECONDS  How long to wait for guest SSH to accept connections. Default: 180
  RESUME_COMMAND_TIMEOUT_SECONDS
                               End-to-end timeout for resume SSH command. Default: 900
  CODEX_MODEL                  Model passed to `codex exec resume`. Default: gpt-5.4
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

resolve_private_key_path() {
  local caller_user caller_home

  caller_user="${SUDO_USER:-${USER:-}}"
  caller_home="${HOME:-}"
  if [[ -n "${caller_user}" ]]; then
    caller_home="$(getent passwd "${caller_user}" | cut -d: -f6 || printf '%s\n' "${caller_home}")"
  fi

  SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${caller_home}/.ssh/id_ed25519}"
  if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
    SSH_PRIVATE_KEY_PATH="${caller_home}/.ssh/id_rsa"
  fi

  if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
    return 1
  fi
  return 0
}

parse_args() {
  PROMPT=""
  ISSUE_NUMBER=""
  VM_NAME=""
  SESSION_ID=""
  VM_USER="agent"
  WORKER_MODE=0
  HOST_STATE_FILE=""

  if [[ $# -eq 0 ]]; then
    return 1
  fi

  if [[ "$1" != --* ]]; then
    if [[ $# -lt 2 || $# -gt 3 ]]; then
      return 1
    fi
    PROMPT="$1"
    ISSUE_NUMBER="$2"
    VM_USER="${3:-agent}"
    VM_NAME="vm-${ISSUE_NUMBER}"
    return 0
  fi

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
      --vm-user)
        VM_USER="${2:-}"
        shift 2
        ;;
      --worker-mode)
        WORKER_MODE=1
        shift
        ;;
      --host-state-file)
        HOST_STATE_FILE="${2:-}"
        shift 2
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

  if [[ -z "${PROMPT}" || -z "${VM_NAME}" ]]; then
    return 1
  fi

  return 0
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"

if ! parse_args "$@"; then
  show_help >&2
  exit "${EXIT_USAGE}"
fi

CODEX_MODEL="${CODEX_MODEL:-gpt-5.4}"
VM_WAIT_IP_TIMEOUT_SECONDS="${VM_WAIT_IP_TIMEOUT_SECONDS:-180}"
VM_WAIT_SSH_TIMEOUT_SECONDS="${VM_WAIT_SSH_TIMEOUT_SECONDS:-180}"
RESUME_COMMAND_TIMEOUT_SECONDS="${RESUME_COMMAND_TIMEOUT_SECONDS:-900}"

if ! resolve_private_key_path; then
  echo "Missing SSH private key: ${SSH_PRIVATE_KEY_PATH:-unset}" >&2
  echo "Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key." >&2
  write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "missing-ssh-key"
  exit "${EXIT_MISSING_SSH_KEY}"
fi

if ! vm_network_vm_exists "${VM_NAME}"; then
  echo "VM not found: ${VM_NAME}" >&2
  write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "vm-not-found"
  exit "${EXIT_VM_NOT_FOUND}"
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

if ! vm_network_wait_for_ssh "${VM_NAME}" "${VM_IP}" "${VM_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_WAIT_SSH_TIMEOUT_SECONDS}" >/dev/null; then
  write_host_state_attempt "${HOST_STATE_FILE}" "${SESSION_ID}" "error" "wait-ssh-timeout"
  exit "${EXIT_VM_WAIT_SSH_TIMEOUT}"
fi

PROMPT_B64="$(printf '%s' "${PROMPT}" | base64 -w0)"
SSH_FLAGS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -o ConnectTimeout=5
  -i "${SSH_PRIVATE_KEY_PATH}"
)
if (( WORKER_MODE == 0 )); then
  SSH_FLAGS=(-t "${SSH_FLAGS[@]}")
fi

set +e

timeout --foreground "${RESUME_COMMAND_TIMEOUT_SECONDS}s" \
  ssh "${SSH_FLAGS[@]}" \
  "${VM_USER}@${VM_IP}" \
  "PROMPT_B64='${PROMPT_B64}' CODEX_MODEL='${CODEX_MODEL}' bash -s" <<'REMOTE'
set -euo pipefail

cd "${HOME}"
. "${HOME}/.profile" >/dev/null 2>&1 || true

LOG_DIR="${HOME}/.agent-sandbox"
STATE_PATH="${LOG_DIR}/codex-resume-last.state"
PROMPT="$(python3 -c 'import base64, os; print(base64.b64decode(os.environ["PROMPT_B64"]).decode("utf-8"))')"
mkdir -p "${LOG_DIR}"

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

on_exit() {
  local exit_code="$?"
  write_state "idle" "complete" "${exit_code}"
}

write_state "running" "resume"
trap on_exit EXIT

codex exec resume \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  --model "${CODEX_MODEL}" \
  --last \
  "${PROMPT}"
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
