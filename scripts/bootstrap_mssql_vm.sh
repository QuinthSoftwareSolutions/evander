#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'USAGE'
Usage: bootstrap_mssql_vm.sh [--init-root PATH] [--pid EDITION] <vm-name> [vm-user]

Provision a guest-local Microsoft SQL Server instance inside a VM using
host-installed SQL Server artifacts, then optionally apply an init bundle.

Arguments:
  vm-name        libvirt domain name.
  vm-user        Guest user to configure. Default: agent

Options:
  --init-root PATH  Host directory copied into /var/opt/mssql/init inside the guest.
                    If present, init.sql from that directory is executed after the
                    local SQL Server instance is available.
  --pid EDITION     MSSQL edition passed to mssql-conf setup. Default: Developer

Environment:
  MSSQL_SA_PASSWORD  Required SA password used to initialize or reset the guest instance.
  SSH_KEY_PATH       Private key matching the guest authorized key.
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

INIT_ROOT="${MSSQL_INIT_ROOT:-}"
MSSQL_PID="${MSSQL_PID:-Developer}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --init-root)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      INIT_ROOT="$2"
      shift 2
      ;;
    --pid)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      MSSQL_PID="$2"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      show_help >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -lt 1 ]]; then
  show_help >&2
  exit 1
fi

VM_NAME="$1"
VM_USER="${2:-agent}"
SSH_USER="${VM_SSH_USER:-agent-admin}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/guest_ssh.sh
source "${SCRIPT_DIR}/lib/guest_ssh.sh"

MSSQL_SA_PASSWORD="${MSSQL_SA_PASSWORD:-}"
if [[ -z "${MSSQL_SA_PASSWORD}" ]]; then
  echo "MSSQL_SA_PASSWORD is required to provision a guest-local SQL Server instance." >&2
  exit 1
fi

if [[ -n "${INIT_ROOT}" ]]; then
  if [[ ! -d "${INIT_ROOT}" ]]; then
    echo "Missing MSSQL init root: ${INIT_ROOT}" >&2
    exit 1
  fi
  if [[ ! -f "${INIT_ROOT}/init.sql" ]]; then
    echo "Missing init.sql under MSSQL init root: ${INIT_ROOT}" >&2
    exit 1
  fi
fi

CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi
SSH_OPTS=(
  -i "${SSH_KEY_PATH}"
)

get_vm_ip() {
  if guest_ssh_resolve_vm_ip "${VM_NAME}"; then
    return 0
  fi

  echo "Could not determine an IPv4 address for VM ${VM_NAME}." >&2
  echo "Run this script with sudo, grant the current user libvirt access, or authorize the pkexec prompt." >&2
  return 1
}

VM_IP="$(get_vm_ip)"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_KEY_PATH}" "${CALLER_HOME}")

"${SCRIPT_DIR}/bootstrap_mssql_tools_vm.sh" "${VM_NAME}" "${VM_USER}"
MSSQL_SA_PASSWORD="${MSSQL_SA_PASSWORD}" MSSQL_PID="${MSSQL_PID}" \
  "${SCRIPT_DIR}/bootstrap_mssql_server_vm.sh" "${VM_NAME}" "${VM_USER}"

if [[ -n "${INIT_ROOT}" ]]; then
  echo "Applying MSSQL init bundle from ${INIT_ROOT} into ${SSH_USER}@${VM_IP}"
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_IP}" "sudo -n bash -s" <<'EOF_VM'
set -euo pipefail
install -d -o mssql -g mssql -m 0770 /var/opt/mssql/init
rm -rf /var/opt/mssql/init/*
EOF_VM
  tar -C "${INIT_ROOT}" -cf - . \
    | ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_IP}" "sudo -n tar -C /var/opt/mssql/init -xf -"
  ssh "${SSH_OPTS[@]}" "${SSH_USER}@${VM_IP}" "sudo -n bash -s" <<'EOF_VM'
set -euo pipefail
chown -R mssql:mssql /var/opt/mssql/init
chmod -R u+rwX,go+rX /var/opt/mssql/init
EOF_VM
fi

SA_PASSWORD_B64="$(printf '%s' "${MSSQL_SA_PASSWORD}" | base64 -w0)"
ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "SA_PASSWORD_B64='${SA_PASSWORD_B64}' APPLY_INIT='$([[ -n "${INIT_ROOT}" ]] && echo 1 || echo 0)' bash -s" <<'EOF_VM'
set -euo pipefail
sa_password="$(
  python3 - <<'PY'
import base64
import os

print(base64.b64decode(os.environ["SA_PASSWORD_B64"]).decode("utf-8"))
PY
)"

export PATH="$HOME/toolchains/mssql-tools/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/toolchains/mssql-tools/lib:${LD_LIBRARY_PATH:-}"

for _ in $(seq 1 60); do
  if sqlcmd -S localhost,1433 -U sa -P "${sa_password}" -C -Q "SELECT 1" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

sqlcmd -S localhost,1433 -U sa -P "${sa_password}" -C -Q "SELECT 1" >/dev/null

if [[ "${APPLY_INIT}" == "1" ]]; then
  sqlcmd -S localhost,1433 -U sa -P "${sa_password}" -C -i /var/opt/mssql/init/init.sql
fi
EOF_VM

echo "SQL Server bootstrap complete for ${VM_NAME} (${VM_IP})"
