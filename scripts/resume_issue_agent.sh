#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: resume_issue_agent.sh <prompt> <issue-number> [vm-user] [--observe [lines]]

SSH into an issue VM and request a guest Codex resume command from the guest
user's home directory.

Arguments:
  prompt        Prompt passed to `codex exec resume --last`.
  issue-number  Issue identifier used to derive the VM name.
  vm-user       Guest user to connect as. Default: agent
  --observe     Tail the guest Codex log after resuming instead of returning
                immediately.
  lines         Number of log lines to print before following. Default: 10

Environment:
  SSH_PRIVATE_KEY_PATH       Private key for guest SSH access.
                             Default: ~/.ssh/id_ed25519, then ~/.ssh/id_rsa
  VM_WAIT_IP_TIMEOUT_SECONDS How long to wait for the guest to receive an IPv4 address.
                             Default: 180
  VM_WAIT_SSH_TIMEOUT_SECONDS How long to wait for guest SSH to accept connections.
                              Default: 180
  CODEX_MODEL               Model passed to `codex exec resume`.
                            Default: gpt-5.4
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 2 ]]; then
  show_help >&2
  exit 1
fi

PROMPT="$1"
ISSUE_NUMBER="$2"
if [[ $# -ge 3 && "${3:-}" != "--observe" ]]; then
  VM_USER="$3"
  shift 3
else
  VM_USER="agent"
  shift 2
fi

OBSERVE=0
OBSERVE_LINES=10
if [[ $# -gt 0 ]]; then
  if [[ "${1:-}" != "--observe" ]]; then
    show_help >&2
    exit 1
  fi
  OBSERVE=1
  shift
  if [[ $# -gt 0 ]]; then
    OBSERVE_LINES="$1"
    shift
  fi
  if [[ $# -gt 0 ]]; then
    show_help >&2
    exit 1
  fi
fi

VM_NAME="vm-${ISSUE_NUMBER}"
CODEX_MODEL="${CODEX_MODEL:-gpt-5.4}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"

CALLER_USER="${SUDO_USER:-${USER:-}}"
CALLER_HOME="${HOME:-}"
if [[ -n "${CALLER_USER}" ]]; then
  CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6 || printf '%s\n' "${CALLER_HOME}")"
fi

SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
VM_WAIT_IP_TIMEOUT_SECONDS="${VM_WAIT_IP_TIMEOUT_SECONDS:-180}"
VM_WAIT_SSH_TIMEOUT_SECONDS="${VM_WAIT_SSH_TIMEOUT_SECONDS:-180}"

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_PRIVATE_KEY_PATH}" >&2
  echo "Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

if ! [[ "${OBSERVE_LINES}" =~ ^[1-9][0-9]*$ ]]; then
  echo "lines must be a positive integer: ${OBSERVE_LINES}" >&2
  exit 1
fi

if ! virsh dominfo "${VM_NAME}" >/dev/null 2>&1 && ! sudo -n virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
  echo "VM not found: ${VM_NAME}" >&2
  exit 1
fi

VM_IP="$(vm_network_wait_for_ip "${VM_NAME}" "${VM_WAIT_IP_TIMEOUT_SECONDS}")"
vm_network_wait_for_ssh "${VM_NAME}" "${VM_IP}" "${VM_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_WAIT_SSH_TIMEOUT_SECONDS}" >/dev/null

PROMPT_B64="$(printf '%s' "${PROMPT}" | base64 -w0)"

resume_output="$(
ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o ConnectTimeout=5 \
  -i "${SSH_PRIVATE_KEY_PATH}" \
  "${VM_USER}@${VM_IP}" \
  "PROMPT_B64='${PROMPT_B64}' CODEX_MODEL='${CODEX_MODEL}' bash -s" <<'EOF'
set -euo pipefail

cd "${HOME}"
. "${HOME}/.profile" >/dev/null 2>&1 || true

PROMPT="$(python3 -c 'import base64, os; print(base64.b64decode(os.environ["PROMPT_B64"]).decode("utf-8"))')"
LOG_DIR="${HOME}/.agent-sandbox"
RUN_LOG_FILE="${LOG_DIR}/codex-heartbeat-run.log"
RESUME_HINT_FILE="${LOG_DIR}/resume-args.txt"
PID_FILE="${LOG_DIR}/codex-heartbeat.pid"

mkdir -p "${LOG_DIR}"

if [[ -f "${PID_FILE}" ]]; then
  EXISTING_PID="$(cat "${PID_FILE}")"
  if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" >/dev/null 2>&1; then
    echo "Codex already running in guest with PID ${EXISTING_PID}" >&2
    exit 1
  fi
fi

printf '%s\n' "--last" >"${RESUME_HINT_FILE}"

nohup codex exec resume \
  --last \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  --model "${CODEX_MODEL}" \
  "${PROMPT}" \
  >>"${RUN_LOG_FILE}" 2>&1 < /dev/null &

AGENT_PID=$!
printf '%s\n' "${AGENT_PID}" >"${PID_FILE}"
printf 'resumed pid=%s log=%s\n' "${AGENT_PID}" "${RUN_LOG_FILE}"
EOF
)"

if (( OBSERVE )); then
  exec ssh \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -i "${SSH_PRIVATE_KEY_PATH}" \
    "${VM_USER}@${VM_IP}" \
    "LINES='${OBSERVE_LINES}' bash -s" <<'EOF'
set -euo pipefail
LOG_PATH="${HOME}/.agent-sandbox/codex-heartbeat-run.log"
if [[ ! -f "${LOG_PATH}" ]]; then
  echo "No guest Codex resume log found at ${LOG_PATH}" >&2
  exit 0
fi
tail -n "${LINES}" -f "${LOG_PATH}"
EOF
fi

printf 'Agent resumed for %s' "${VM_NAME}"
if [[ -n "${resume_output}" ]]; then
  printf ' (%s)' "${resume_output}"
fi
printf '.\n'
