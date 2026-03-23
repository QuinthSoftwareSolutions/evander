#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'USAGE'
Usage: bootstrap_mssql_tools_vm.sh <vm-name> [vm-user]

Copy host-installed Microsoft SQL Server client tooling into a guest VM and
configure the guest shell to use it offline.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH           Private key matching the guest authorized key.
  HOST_MSSQL_TOOLS_ROOT  Host SQL Server client tooling root.
                         Default: /opt/mssql-tools18
  HOST_MSSQL_ODBC_ROOT   Host Microsoft ODBC Driver 18 root.
                         Default: /opt/microsoft/msodbcsql18
  HOST_MSSQL_RUNTIME_LIBS
                         Space-delimited host library paths required by sqlcmd.
                         Default: libodbc.so.2, libodbcinst.so.2, libltdl.so.7
USAGE
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
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

HOST_MSSQL_TOOLS_ROOT="${HOST_MSSQL_TOOLS_ROOT:-/opt/mssql-tools18}"
HOST_MSSQL_ODBC_ROOT="${HOST_MSSQL_ODBC_ROOT:-/opt/microsoft/msodbcsql18}"
HOST_MSSQL_RUNTIME_LIBS=(${HOST_MSSQL_RUNTIME_LIBS:-/lib/x86_64-linux-gnu/libodbc.so.2 /lib/x86_64-linux-gnu/libodbcinst.so.2 /lib/x86_64-linux-gnu/libltdl.so.7})
SSH_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -i "${SSH_KEY_PATH}"
)

if [[ ! -d "${HOST_MSSQL_TOOLS_ROOT}" ]]; then
  echo "Missing host SQL Server tooling root: ${HOST_MSSQL_TOOLS_ROOT}" >&2
  exit 1
fi

if [[ ! -x "${HOST_MSSQL_TOOLS_ROOT}/bin/sqlcmd" ]]; then
  echo "Missing sqlcmd launcher under ${HOST_MSSQL_TOOLS_ROOT}/bin" >&2
  exit 1
fi

if [[ ! -d "${HOST_MSSQL_ODBC_ROOT}" ]]; then
  echo "Missing host ODBC driver root: ${HOST_MSSQL_ODBC_ROOT}" >&2
  exit 1
fi

if [[ ! -f "${HOST_MSSQL_ODBC_ROOT}/etc/odbcinst.ini" ]]; then
  echo "Missing ODBC driver registration under ${HOST_MSSQL_ODBC_ROOT}/etc/odbcinst.ini" >&2
  exit 1
fi

if [[ ! -d "${HOST_MSSQL_ODBC_ROOT}/lib64" ]]; then
  echo "Missing ODBC driver library directory under ${HOST_MSSQL_ODBC_ROOT}/lib64" >&2
  exit 1
fi

for runtime_lib in "${HOST_MSSQL_RUNTIME_LIBS[@]}"; do
  if [[ ! -e "${runtime_lib}" ]]; then
    echo "Missing required SQL Server runtime library: ${runtime_lib}" >&2
    exit 1
  fi
done

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_KEY_PATH}" >&2
  echo "Set SSH_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

get_vm_ip() {
  local ip

  ip="$(
    virsh domifaddr "${VM_NAME}" 2>/dev/null \
      | awk '/ipv4/ {split($4, a, "/"); print a[1]; exit}'
  )"
  if [[ -n "${ip}" ]]; then
    printf '%s\n' "${ip}"
    return 0
  fi

  ip="$(
    sudo -n virsh domifaddr "${VM_NAME}" 2>/dev/null \
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

echo "Bootstrapping SQL Server client tooling into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  sudo mkdir -p /home/${VM_USER}
  sudo chown -R ${VM_USER}:${VM_USER} /home/${VM_USER}
  mkdir -p ~/toolchains/mssql-tools
  mkdir -p ~/toolchains/msodbcsql18
  rm -rf ~/toolchains/mssql-tools/*
  rm -rf ~/toolchains/msodbcsql18/*
  mkdir -p ~/toolchains/mssql-tools/lib
  mkdir -p ~/toolchains/mssql-tools/etc
"

tar -C "${HOST_MSSQL_TOOLS_ROOT}" -cf - . \
  | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "tar -C ~/toolchains/mssql-tools -xf -"

tar -C "${HOST_MSSQL_ODBC_ROOT}" -cf - . \
  | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "tar -C ~/toolchains/msodbcsql18 -xf -"

for runtime_lib in "${HOST_MSSQL_RUNTIME_LIBS[@]}"; do
  runtime_name="$(basename "${runtime_lib}")"
  cat "${runtime_lib}" \
    | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "cat > ~/toolchains/mssql-tools/lib/${runtime_name}"
done

ODBC_DRIVER_LIB_REL="$(
  python3 - "${HOST_MSSQL_ODBC_ROOT}" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
lib_candidates = sorted((root / "lib64").glob("libmsodbcsql-*.so*"))
if not lib_candidates:
    raise SystemExit("No msodbcsql shared library found under host ODBC root.")
print(lib_candidates[-1].relative_to(root).as_posix())
PY
)"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "ODBC_DRIVER_LIB_REL='${ODBC_DRIVER_LIB_REL}' bash -s" <<'EOF_VM'
set -euo pipefail

cat > "$HOME/toolchains/mssql-tools/etc/odbcinst.ini" <<ODBCINST
[ODBC Driver 18 for SQL Server]
Description=Microsoft ODBC Driver 18 for SQL Server
Driver=$HOME/toolchains/msodbcsql18/${ODBC_DRIVER_LIB_REL}
UsageCount=1
ODBCINST
EOF_VM

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "bash -s" <<'EOF_VM'
set -euo pipefail

if [[ ! -x "$HOME/toolchains/mssql-tools/bin/sqlcmd" ]]; then
  echo "Expected sqlcmd launcher missing at $HOME/toolchains/mssql-tools/bin/sqlcmd" >&2
  exit 1
fi

if [[ ! -f "$HOME/toolchains/mssql-tools/lib/libodbc.so.2" ]]; then
  echo "Expected libodbc runtime missing at $HOME/toolchains/mssql-tools/lib/libodbc.so.2" >&2
  exit 1
fi

if [[ ! -f "$HOME/toolchains/mssql-tools/etc/odbcinst.ini" ]]; then
  echo "Expected ODBC driver registration missing at $HOME/toolchains/mssql-tools/etc/odbcinst.ini" >&2
  exit 1
fi

if [[ ! -d "$HOME/toolchains/msodbcsql18/lib64" ]]; then
  echo "Expected ODBC driver payload missing at $HOME/toolchains/msodbcsql18/lib64" >&2
  exit 1
fi

if ! grep -q 'PATH="$HOME/toolchains/mssql-tools/bin:$PATH"' ~/.profile 2>/dev/null; then
  cat >> ~/.profile <<PROFILE
export PATH="$HOME/toolchains/mssql-tools/bin:$PATH"
PROFILE
fi

if ! grep -q 'LD_LIBRARY_PATH="$HOME/toolchains/mssql-tools/lib:$HOME/toolchains/msodbcsql18/lib64:${LD_LIBRARY_PATH:-}"' ~/.profile 2>/dev/null; then
  cat >> ~/.profile <<PROFILE
export LD_LIBRARY_PATH="$HOME/toolchains/mssql-tools/lib:$HOME/toolchains/msodbcsql18/lib64:${LD_LIBRARY_PATH:-}"
PROFILE
fi

if ! grep -q 'ODBCSYSINI="$HOME/toolchains/mssql-tools/etc"' ~/.profile 2>/dev/null; then
  cat >> ~/.profile <<PROFILE
export ODBCSYSINI="$HOME/toolchains/mssql-tools/etc"
PROFILE
fi

if ! grep -q 'PATH="$HOME/toolchains/mssql-tools/bin:$PATH"' ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc <<BASHRC
export PATH="$HOME/toolchains/mssql-tools/bin:$PATH"
BASHRC
fi

if ! grep -q 'LD_LIBRARY_PATH="$HOME/toolchains/mssql-tools/lib:$HOME/toolchains/msodbcsql18/lib64:${LD_LIBRARY_PATH:-}"' ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc <<BASHRC
export LD_LIBRARY_PATH="$HOME/toolchains/mssql-tools/lib:$HOME/toolchains/msodbcsql18/lib64:${LD_LIBRARY_PATH:-}"
BASHRC
fi

if ! grep -q 'ODBCSYSINI="$HOME/toolchains/mssql-tools/etc"' ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc <<BASHRC
export ODBCSYSINI="$HOME/toolchains/mssql-tools/etc"
BASHRC
fi

export PATH="$HOME/toolchains/mssql-tools/bin:$PATH"
export LD_LIBRARY_PATH="$HOME/toolchains/mssql-tools/lib:$HOME/toolchains/msodbcsql18/lib64:${LD_LIBRARY_PATH:-}"
export ODBCSYSINI="$HOME/toolchains/mssql-tools/etc"
sqlcmd -? >/dev/null
EOF_VM

echo "SQL Server tooling bootstrap complete for ${VM_NAME} (${VM_IP})"
