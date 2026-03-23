#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: list_running_vms.sh [vm-user]

List currently running libvirt VMs and show whether a guest Codex agent is
currently running inside each VM.

Arguments:
  vm-user  Guest user to inspect over SSH. Default: agent

Environment:
  SSH_PRIVATE_KEY_PATH                  Private key for guest SSH access.
                                        Default: ~/.ssh/id_ed25519, then ~/.ssh/id_rsa
  VM_AGENT_LIST_CONNECT_TIMEOUT_SECONDS SSH connect timeout per VM.
                                        Default: 5
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -gt 1 ]]; then
  show_help >&2
  exit 1
fi

VM_USER="${1:-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"

CALLER_USER="${SUDO_USER:-${USER:-}}"
CALLER_HOME="${HOME:-}"
if [[ -n "${CALLER_USER}" ]]; then
  CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6 || printf '%s\n' "${CALLER_HOME}")"
fi

SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
VM_AGENT_LIST_CONNECT_TIMEOUT_SECONDS="${VM_AGENT_LIST_CONNECT_TIMEOUT_SECONDS:-5}"

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

have_ssh_key=1
if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  have_ssh_key=0
fi

virsh_try() {
  if virsh "$@" 2>/dev/null; then
    return 0
  fi
  sudo -n virsh "$@" 2>/dev/null
}

list_running_vm_names() {
  virsh_try list --state-running --name | awk 'NF > 0'
}

dominfo_field() {
  local vm_name="$1"
  local field_name="$2"
  local dominfo_output

  dominfo_output="$(virsh_try dominfo "${vm_name}")"
  awk -F': *' -v field="${field_name}" '$1 == field {print $2; exit}' <<<"${dominfo_output}"
}

agent_status_for_vm() {
  local vm_ip="$1"

  if (( ! have_ssh_key )); then
    printf '%s\n' "unknown (missing SSH key)"
    return 0
  fi

  if ! ssh \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout="${VM_AGENT_LIST_CONNECT_TIMEOUT_SECONDS}" \
    -i "${SSH_PRIVATE_KEY_PATH}" \
    "${VM_USER}@${vm_ip}" \
    "bash -s" <<'EOF'
set -euo pipefail

LOG_DIR="${HOME}/.agent-sandbox"

is_active_codex_pid() {
  local pid_path="$1"
  local pid
  local cmdline

  [[ -f "${pid_path}" ]] || return 1
  pid="$(tr -d '[:space:]' <"${pid_path}" || true)"
  [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1 || return 1
  cmdline="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  [[ -n "${cmdline}" ]] || return 1
  case "${cmdline}" in
    codex|*/codex|*" codex"|*" codex "*|*"/codex "*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

if [[ ! -d "${LOG_DIR}" ]]; then
  printf '%s\n' "not started"
  exit 0
fi

active_entries=()

if is_active_codex_pid "${LOG_DIR}/codex-heartbeat.pid"; then
  active_entries+=("heartbeat")
fi

shopt -s nullglob
for pid_path in "${LOG_DIR}"/codex-*.pid; do
  name="$(basename "${pid_path}")"
  name="${name#codex-}"
  name="${name%.pid}"
  if [[ "${name}" == "heartbeat" ]]; then
    continue
  fi
  if is_active_codex_pid "${pid_path}"; then
    active_entries+=("${name}")
  fi
done
shopt -u nullglob

if [[ ${#active_entries[@]} -eq 0 ]]; then
  printf '%s\n' "idle"
  exit 0
fi

printf 'running (%s)\n' "$(IFS=', '; echo "${active_entries[*]}")"
EOF
  then
    printf '%s\n' "unknown (SSH failed)"
    return 0
  fi
}

printf '%-4s %-24s %-10s %-15s %s\n' "ID" "Name" "State" "IP" "Agent"

mapfile -t running_vms < <(list_running_vm_names)

if [[ ${#running_vms[@]} -eq 0 ]]; then
  exit 0
fi

for vm_name in "${running_vms[@]}"; do
  vm_id="$(dominfo_field "${vm_name}" "Id")"
  vm_state="$(dominfo_field "${vm_name}" "State")"

  if ! vm_ip="$(vm_network_resolve_ip "${vm_name}" 2>/dev/null)"; then
    vm_ip="unavailable"
    agent_status="unknown (no IP)"
  else
    agent_status="$(agent_status_for_vm "${vm_ip}")"
  fi

  printf '%-4s %-24s %-10s %-15s %s\n' \
    "${vm_id:--}" \
    "${vm_name}" \
    "${vm_state:-running}" \
    "${vm_ip}" \
    "${agent_status}"
done
