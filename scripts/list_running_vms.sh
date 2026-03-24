#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: list_running_vms.sh [--all] [vm-user]

List currently running libvirt VMs and show the current guest agent state
inside each VM.

Arguments:
  vm-user  Guest user to inspect over SSH. Default: agent

Options:
  --all    Include inactive libvirt VMs in the output.
  --omit-state
           Skip SSH guest state inspection for faster output.

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

INCLUDE_ALL=0
OMIT_STATE=0
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
  case "${1}" in
    --all)
      INCLUDE_ALL=1
      shift
      ;;
    --omit-state)
      OMIT_STATE=1
      shift
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        POSITIONAL_ARGS+=("${1}")
        shift
      done
      ;;
    -*)
      show_help >&2
      exit 1
      ;;
    *)
      POSITIONAL_ARGS+=("${1}")
      shift
      ;;
  esac
done

if [[ ${#POSITIONAL_ARGS[@]} -gt 1 ]]; then
  show_help >&2
  exit 1
fi

VM_USER="${POSITIONAL_ARGS[0]:-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"
# shellcheck source=lib/agent_status.sh
source "${SCRIPT_DIR}/lib/agent_status.sh"

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

list_vm_names() {
  if (( INCLUDE_ALL )); then
    virsh_try list --all --name | awk 'NF > 0'
    return 0
  fi

  list_running_vm_names
}

dominfo_field() {
  local vm_name="$1"
  local field_name="$2"
  local dominfo_output

  dominfo_output="$(virsh_try dominfo "${vm_name}")"
  awk -F': *' -v field="${field_name}" '$1 == field {print $2; exit}' <<<"${dominfo_output}"
}

agent_status_for_vm() {
  local vm_name="$1"
  local vm_ip="$2"

  if (( OMIT_STATE )); then
    printf '%s\n' "omitted"
    return 0
  fi

  if (( ! have_ssh_key )); then
    printf '%s\n' "unknown (missing SSH key)"
    return 0
  fi

  vm_agent_status_from_ssh \
    "${VM_USER}" \
    "${vm_name}" \
    "${vm_ip}" \
    "${SSH_PRIVATE_KEY_PATH}" \
    "${VM_AGENT_LIST_CONNECT_TIMEOUT_SECONDS}"
}

printf '%-4s %-24s %-10s %-15s %s\n' "ID" "Name" "State" "IP" "Agent"

mapfile -t vms < <(list_vm_names)

if [[ ${#vms[@]} -eq 0 ]]; then
  exit 0
fi

for vm_name in "${vms[@]}"; do
  vm_id="$(dominfo_field "${vm_name}" "Id")"
  vm_state="$(dominfo_field "${vm_name}" "State")"

  if [[ "${vm_state}" != "running" ]]; then
    vm_ip="unavailable"
    agent_status="inactive VM"
  elif ! vm_ip="$(vm_network_resolve_ip "${vm_name}" 2>/dev/null)"; then
    vm_ip="unavailable"
    agent_status="unknown (no IP)"
  else
    agent_status="$(agent_status_for_vm "${vm_name}" "${vm_ip}")"
  fi

  printf '%-4s %-24s %-10s %-15s %s\n' \
    "${vm_id:--}" \
    "${vm_name}" \
    "${vm_state:-running}" \
    "${vm_ip}" \
    "${agent_status}"
done
