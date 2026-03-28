#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_git_vm.sh <vm-name> [vm-user]

Configure a guest VM for passwordless Git pushes back to host bare remotes
using a dedicated guest SSH key and a host-side forced-command entry.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH                    Private key matching the guest authorized key.
  AGENT_SANDBOX_GIT_HOST          Host SSH endpoint for guest Git access.
  AGENT_SANDBOX_GIT_USER          Host user that owns the bare remotes.
  AGENT_SANDBOX_GIT_ROOT          Host directory containing bare remotes.
  AGENT_SANDBOX_GIT_SSH_ALIAS     Guest SSH host alias. Default: agent-sandbox-git-host
  AGENT_SANDBOX_GIT_ALLOWED_FROM  Source CIDR allowed in the host authorized_keys entry.
  Values can also come from root directory-scoped issue setup config.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 1 ]]; then
  show_help >&2
  exit 1
fi

VM_NAME="$1"
VM_USER="${2:-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/guest_ssh.sh
source "${SCRIPT_DIR}/lib/guest_ssh.sh"
# shellcheck source=lib/runtime_config.sh
source "${SCRIPT_DIR}/lib/runtime_config.sh"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

load_issue_setup_config

GIT_HOST="${AGENT_SANDBOX_GIT_HOST:-192.168.250.1}"
GIT_USER="${AGENT_SANDBOX_GIT_USER:-${CALLER_USER}}"
GIT_ROOT="${AGENT_SANDBOX_GIT_ROOT:-${ROOT_DIR}/git-remotes}"
GIT_SSH_ALIAS="${AGENT_SANDBOX_GIT_SSH_ALIAS:-agent-sandbox-git-host}"
GIT_ALLOWED_FROM="${AGENT_SANDBOX_GIT_ALLOWED_FROM:-192.168.250.0/24}"
WRAPPER_PATH="${ROOT_DIR}/scripts/git_ssh_command_wrapper.sh"
AUTHORIZED_KEYS_PATH="${CALLER_HOME}/.ssh/authorized_keys"
SSH_OPTS=(
  -i "${SSH_KEY_PATH}"
)

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_KEY_PATH}" >&2
  echo "Set SSH_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

if [[ ! -x "${WRAPPER_PATH}" ]]; then
  echo "Missing or non-executable Git SSH wrapper: ${WRAPPER_PATH}" >&2
  exit 1
fi

get_vm_ip() {
  local ip

  ip="$(
    sudo -n virsh domifaddr "${VM_NAME}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  ip="$(
    virsh domifaddr "${VM_NAME}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  echo "Could not determine an IPv4 address for VM ${VM_NAME}." >&2
  echo "Run this script with sudo, or grant the current user libvirt access." >&2
  return 1
}

VM_IP="$(get_vm_ip)"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_KEY_PATH}" "${CALLER_HOME}")

echo "Bootstrapping Git SSH into ${VM_USER}@${VM_IP}"

PUBLIC_KEY="$(
  ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" \
    "VM_NAME='${VM_NAME}' GIT_HOST='${GIT_HOST}' GIT_USER='${GIT_USER}' GIT_SSH_ALIAS='${GIT_SSH_ALIAS}' bash -s" <<'EOF'
set -euo pipefail
mkdir -p ~/.ssh
chmod 700 ~/.ssh

KEY_PATH="$HOME/.ssh/agent-sandbox-git"
CONFIG_PATH="$HOME/.ssh/config"
KNOWN_HOSTS_PATH="$HOME/.ssh/known_hosts"

if [[ ! -f "${KEY_PATH}" ]]; then
  ssh-keygen -q -t ed25519 -N "" -C "agent-sandbox-git@${VM_NAME}" -f "${KEY_PATH}"
fi

touch "${CONFIG_PATH}" "${KNOWN_HOSTS_PATH}"
chmod 600 "${CONFIG_PATH}" "${KNOWN_HOSTS_PATH}"

python3 - "${CONFIG_PATH}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8") if path.exists() else ""
begin = "# BEGIN agent-sandbox git host"
end = "# END agent-sandbox git host"
while begin in text and end in text:
    start = text.index(begin)
    stop = text.index(end, start) + len(end)
    suffix = "\n" if stop < len(text) and text[stop] == "\n" else ""
    text = text[:start].rstrip() + "\n"
    text += text[stop + len(suffix):].lstrip("\n")
path.write_text(text, encoding="utf-8")
PY

cat >> "${CONFIG_PATH}" <<CONFIG
# BEGIN agent-sandbox git host
Host ${GIT_SSH_ALIAS}
  HostName ${GIT_HOST}
  User ${GIT_USER}
  IdentityFile ~/.ssh/agent-sandbox-git
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
# END agent-sandbox git host
CONFIG

ssh-keyscan -H "${GIT_HOST}" >> "${KNOWN_HOSTS_PATH}" 2>/dev/null || true
cat "${KEY_PATH}.pub"
EOF
)"

if [[ -z "${PUBLIC_KEY}" ]]; then
  echo "Failed to retrieve guest Git SSH public key." >&2
  exit 1
fi

mkdir -p "${CALLER_HOME}/.ssh"
chmod 700 "${CALLER_HOME}/.ssh"
touch "${AUTHORIZED_KEYS_PATH}"
chmod 600 "${AUTHORIZED_KEYS_PATH}"

PUBLIC_KEY_BODY="$(awk '{print $2}' <<<"${PUBLIC_KEY}")"
python3 - "${AUTHORIZED_KEYS_PATH}" "${PUBLIC_KEY_BODY}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
needle = sys.argv[2]
lines = path.read_text(encoding="utf-8").splitlines()
filtered = [line for line in lines if needle not in line]
path.write_text(("\n".join(filtered) + "\n") if filtered else "", encoding="utf-8")
PY

cat >> "${AUTHORIZED_KEYS_PATH}" <<EOF
from="${GIT_ALLOWED_FROM}",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding,command="${WRAPPER_PATH}" ${PUBLIC_KEY}
EOF

echo "Git SSH bootstrap complete for ${VM_NAME} (${VM_IP})"
echo
echo "Guest host alias:"
echo "  ${GIT_SSH_ALIAS}"
echo
echo "Use this remote shape inside the VM:"
echo "  ssh://${GIT_SSH_ALIAS}${GIT_ROOT}/<repo>.git"
