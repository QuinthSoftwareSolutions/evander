#!/usr/bin/env bash
set -euo pipefail

EXIT_USAGE=10
EXIT_MISSING_SSH_KEY=11
EXIT_VM_NOT_FOUND=12
EXIT_VM_WAIT_IP_TIMEOUT=14
EXIT_VM_WAIT_SSH_TIMEOUT=15

show_help() {
  cat <<'USAGE'
Usage:
  set_issue_agent_state.sh <enable|disable> <issue-number> [vm-user]
  set_issue_agent_state.sh --state <enable|disable> (--issue-number <number> | --vm-name <name>) [options]

Set the guest agent control state for a VM. `disable` marks the agent unavailable
and stops any tracked guest agent processes. `enable` clears the disabled control
state back to `idle`.

Arguments (legacy positional mode):
  enable|disable  Desired control action.
  issue-number    Issue identifier used to derive the VM name.
  vm-user         Guest user to connect as. Default: agent

Options:
  --state ACTION        Desired action: enable or disable.
  --issue-number NUM    Issue identifier used to derive VM name (`vm-<issue-number>`).
  --vm-name NAME        Explicit libvirt domain name.
  --vm-user USER        Guest user to connect as. Default: agent
  --host-state-path P   Host JSON file used to persist agent control state by VM.
  -h, --help            Show this help text.

Environment:
  SSH_PRIVATE_KEY_PATH         Private key for guest SSH access.
                               Default: ~/.ssh/id_ed25519, then ~/.ssh/id_rsa
  VM_WAIT_IP_TIMEOUT_SECONDS   How long to wait for guest IPv4 address. Default: 180
  VM_WAIT_SSH_TIMEOUT_SECONDS  How long to wait for guest SSH to accept connections. Default: 180
USAGE
}

resolve_private_key_path() {
  local caller_user

  caller_user="${SUDO_USER:-${USER:-}}"
  CALLER_HOME="${HOME:-}"
  if [[ -n "${caller_user}" ]]; then
    CALLER_HOME="$(getent passwd "${caller_user}" | cut -d: -f6 || printf '%s\n' "${CALLER_HOME}")"
  fi

  SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
  if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
    SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
  fi

  [[ -f "${SSH_PRIVATE_KEY_PATH}" ]]
}

parse_args() {
  DESIRED_ACTION=""
  ISSUE_NUMBER=""
  VM_NAME=""
  VM_USER="agent"
  HOST_STATE_PATH=""

  if [[ $# -eq 0 ]]; then
    return 1
  fi

  if [[ "$1" != --* ]]; then
    if [[ $# -lt 2 ]]; then
      return 1
    fi
    DESIRED_ACTION="$1"
    ISSUE_NUMBER="$2"
    VM_NAME="vm-${ISSUE_NUMBER}"
    shift 2
    if [[ $# -gt 0 && "${1:-}" != --* ]]; then
      VM_USER="$1"
      shift
    fi
    while [[ $# -gt 0 ]]; do
      case "$1" in
        --host-state-path)
          HOST_STATE_PATH="${2:-}"
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
        --state)
          DESIRED_ACTION="${2:-}"
          shift 2
          ;;
        --issue-number)
          ISSUE_NUMBER="${2:-}"
          VM_NAME="vm-${ISSUE_NUMBER}"
          shift 2
          ;;
        --vm-name)
          VM_NAME="${2:-}"
          shift 2
          ;;
        --vm-user)
          VM_USER="${2:-}"
          shift 2
          ;;
        --host-state-path)
          HOST_STATE_PATH="${2:-}"
          shift 2
          ;;
        -h|--help)
          show_help
          exit 0
          ;;
        *)
          return 1
          ;;
      esac
    done
  fi

  [[ "${DESIRED_ACTION}" == "enable" || "${DESIRED_ACTION}" == "disable" ]] || return 1
  [[ -n "${VM_NAME}" ]] || return 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"

write_host_control_state() {
  local path="$1"
  local vm_name="$2"
  local status="$3"
  local phase="$4"

  [[ -n "${path}" ]] || return 0

  python3 - "${path}" "${vm_name}" "${status}" "${phase}" <<'PY'
import json
import os
import sys
import tempfile
import fcntl
from datetime import datetime, timezone

path, vm_name, status, phase = sys.argv[1:5]
os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
lock_path = f"{path}.lock" if os.path.splitext(path)[1] else f"{path}.lock"
lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    payload = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            payload = {}

    if not isinstance(payload, dict):
        payload = {}

    payload[vm_name] = {
        "vm_name": vm_name,
        "status": status,
        "phase": phase,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    fd, tmp_path = tempfile.mkstemp(prefix="agent-control-", suffix=".tmp", dir=os.path.dirname(path) or ".")
    os.close(fd)
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.replace(tmp_path, path)
finally:
    fcntl.flock(lock_fd, fcntl.LOCK_UN)
    os.close(lock_fd)
PY
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if ! parse_args "$@"; then
  show_help >&2
  exit "${EXIT_USAGE}"
fi

if ! resolve_private_key_path; then
  echo "Missing SSH private key for guest access." >&2
  exit "${EXIT_MISSING_SSH_KEY}"
fi

VM_WAIT_IP_TIMEOUT_SECONDS="${VM_WAIT_IP_TIMEOUT_SECONDS:-180}"
VM_WAIT_SSH_TIMEOUT_SECONDS="${VM_WAIT_SSH_TIMEOUT_SECONDS:-180}"

if ! sudo -n virsh dominfo "${VM_NAME}" >/dev/null 2>&1 && ! virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
  echo "VM not found: ${VM_NAME}" >&2
  exit "${EXIT_VM_NOT_FOUND}"
fi

REMOTE_STATUS="idle"
REMOTE_PHASE="operator-enable"
if [[ "${DESIRED_ACTION}" == "disable" ]]; then
  REMOTE_STATUS="disabled"
  REMOTE_PHASE="operator-disable"
fi

write_host_control_state "${HOST_STATE_PATH}" "${VM_NAME}" "${REMOTE_STATUS}" "${REMOTE_PHASE}"

VM_STATE="$(vm_network_domain_state "${VM_NAME}")"
if [[ "${VM_STATE}" != "running" ]]; then
  printf 'state=%s path=%s guest_sync=skipped vm_state=%s\n' "${REMOTE_STATUS}" "${HOST_STATE_PATH:-<unset>}" "${VM_STATE}"
  exit 0
fi

if ! vm_network_wait_for_ip "${VM_NAME}" "${VM_WAIT_IP_TIMEOUT_SECONDS}" >/dev/null; then
  echo "Timed out waiting for IP on ${VM_NAME} after ${VM_WAIT_IP_TIMEOUT_SECONDS}s." >&2
  exit "${EXIT_VM_WAIT_IP_TIMEOUT}"
fi
VM_IP="$(vm_network_resolve_ip "${VM_NAME}")"

mapfile -t SSH_FLAGS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_PRIVATE_KEY_PATH}" "${CALLER_HOME}")
SSH_FLAGS+=(-o ConnectTimeout=5)

if ! timeout --foreground "${VM_WAIT_SSH_TIMEOUT_SECONDS}s" \
  ssh "${SSH_FLAGS[@]}" "${VM_USER}@${VM_IP}" true >/dev/null 2>&1; then
  echo "Timed out waiting for SSH on ${VM_NAME} after ${VM_WAIT_SSH_TIMEOUT_SECONDS}s." >&2
  exit "${EXIT_VM_WAIT_SSH_TIMEOUT}"
fi

ssh "${SSH_FLAGS[@]}" \
  "${VM_USER}@${VM_IP}" \
  "REMOTE_STATUS='${REMOTE_STATUS}' REMOTE_PHASE='${REMOTE_PHASE}' bash -s" <<'REMOTE'
set -euo pipefail

LOG_DIR="${HOME}/.agent-sandbox"
CONTROL_STATE_PATH="${LOG_DIR}/agent-control.state"

write_control_state() {
  local status="$1"
  local phase="$2"
  local now tmp_path

  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  tmp_path="${CONTROL_STATE_PATH}.tmp"
  {
    printf "status=%s\n" "${status}"
    printf "phase=%s\n" "${phase}"
    printf "repo=%s\n" "control"
    printf "pid=%s\n" "$$"
    printf "updated_at=%s\n" "${now}"
  } > "${tmp_path}"
  mv "${tmp_path}" "${CONTROL_STATE_PATH}"
}

terminate_pid_file() {
  local pid_file="$1"
  local pid

  [[ -f "${pid_file}" ]] || return 0
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" >/dev/null 2>&1; then
    kill "${pid}" >/dev/null 2>&1 || true
    sleep 1
    if kill -0 "${pid}" >/dev/null 2>&1; then
      kill -9 "${pid}" >/dev/null 2>&1 || true
    fi
  fi
  rm -f "${pid_file}"
}

mkdir -p "${LOG_DIR}"
write_control_state "${REMOTE_STATUS}" "${REMOTE_PHASE}"

if [[ "${REMOTE_STATUS}" == "disabled" ]]; then
  shopt -s nullglob
  for pid_file in "${LOG_DIR}"/agent-*.pid "${LOG_DIR}/codex-heartbeat.pid"; do
    terminate_pid_file "${pid_file}"
  done
  shopt -u nullglob
fi

printf 'state=%s path=%s\n' "${REMOTE_STATUS}" "${CONTROL_STATE_PATH}"
REMOTE
