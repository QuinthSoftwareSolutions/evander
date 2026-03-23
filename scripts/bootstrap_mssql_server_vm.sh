#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'USAGE'
Usage: bootstrap_mssql_server_vm.sh <vm-name> [vm-user]

Copy a host-installed Microsoft SQL Server engine into a guest VM, initialize
it with a guest-local data directory, and start a local systemd service.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  MSSQL_SA_PASSWORD           Required SA password used for setup or password reset.
  MSSQL_PID                   Edition passed to mssql-conf. Default: Developer
  SSH_KEY_PATH                Private key matching the guest authorized key.
  HOST_MSSQL_SERVER_ROOT      Host SQL Server engine root. Default: /opt/mssql
  HOST_MSSQL_SERVICE_FILE     Host SQL Server systemd unit. Default: /usr/lib/systemd/system/mssql-server.service
  HOST_MSSQL_SERVER_RUNTIME_LIBS
                              Optional space-delimited runtime libraries to copy.
                              When omitted, they are derived from ldd on sqlservr.
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
MSSQL_SA_PASSWORD="${MSSQL_SA_PASSWORD:-}"
MSSQL_PID="${MSSQL_PID:-Developer}"
HOST_MSSQL_SERVER_ROOT="${HOST_MSSQL_SERVER_ROOT:-/opt/mssql}"
HOST_MSSQL_SERVICE_FILE="${HOST_MSSQL_SERVICE_FILE:-/usr/lib/systemd/system/mssql-server.service}"

if [[ -z "${MSSQL_SA_PASSWORD}" ]]; then
  echo "MSSQL_SA_PASSWORD is required to provision a guest-local SQL Server instance." >&2
  exit 1
fi

if [[ ! -d "${HOST_MSSQL_SERVER_ROOT}" ]]; then
  echo "Missing host SQL Server engine root: ${HOST_MSSQL_SERVER_ROOT}" >&2
  exit 1
fi

if [[ ! -x "${HOST_MSSQL_SERVER_ROOT}/bin/sqlservr" ]]; then
  echo "Missing sqlservr launcher under ${HOST_MSSQL_SERVER_ROOT}/bin" >&2
  exit 1
fi

if [[ ! -f "${HOST_MSSQL_SERVICE_FILE}" ]]; then
  echo "Missing host SQL Server service file: ${HOST_MSSQL_SERVICE_FILE}" >&2
  exit 1
fi

CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_KEY_PATH}" >&2
  echo "Set SSH_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

if [[ -n "${HOST_MSSQL_SERVER_RUNTIME_LIBS:-}" ]]; then
  # shellcheck disable=SC2206
  HOST_RUNTIME_LIBS=(${HOST_MSSQL_SERVER_RUNTIME_LIBS})
else
  mapfile -t HOST_RUNTIME_LIBS < <(
    ldd "${HOST_MSSQL_SERVER_ROOT}/bin/sqlservr" \
      | awk '/=> \// {print $3}' \
      | sort -u
  )
fi

for runtime_lib in "${HOST_RUNTIME_LIBS[@]}"; do
  if [[ ! -e "${runtime_lib}" ]]; then
    echo "Missing required SQL Server server runtime library: ${runtime_lib}" >&2
    exit 1
  fi
done

SSH_OPTS=(
  -o StrictHostKeyChecking=no
  -o UserKnownHostsFile=/dev/null
  -i "${SSH_KEY_PATH}"
)

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

echo "Bootstrapping SQL Server engine into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "sudo bash -s" <<'EOF_VM'
set -euo pipefail

if ! getent group mssql >/dev/null; then
  groupadd --system mssql
fi

if ! id -u mssql >/dev/null 2>&1; then
  useradd --system --gid mssql --home-dir /var/opt/mssql --shell /usr/sbin/nologin mssql
fi

install -d -m 0755 /opt/mssql /opt/mssql-host-libs
rm -rf /opt/mssql/*
rm -rf /opt/mssql-host-libs/*

install -d -o mssql -g mssql -m 0770 /var/opt/mssql
install -d -o mssql -g mssql -m 0770 /var/opt/mssql/data
install -d -o mssql -g mssql -m 0770 /var/opt/mssql/log
install -d -o mssql -g mssql -m 0770 /var/opt/mssql/secrets
EOF_VM

tar -C "${HOST_MSSQL_SERVER_ROOT}" -cf - . \
  | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "sudo tar -C /opt/mssql -xf -"

cat "${HOST_MSSQL_SERVICE_FILE}" \
  | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "sudo tee /etc/systemd/system/mssql-server.service >/dev/null"

for runtime_lib in "${HOST_RUNTIME_LIBS[@]}"; do
  runtime_name="$(basename "${runtime_lib}")"
  cat "${runtime_lib}" \
    | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "sudo tee /opt/mssql-host-libs/${runtime_name} >/dev/null"
done

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "sudo bash -s" <<'EOF_VM'
set -euo pipefail

install -d -m 0755 /etc/systemd/system/mssql-server.service.d
cat > /etc/systemd/system/mssql-server.service.d/override.conf <<'OVERRIDE'
[Service]
Environment=LD_LIBRARY_PATH=/opt/mssql/lib:/opt/mssql-host-libs
OVERRIDE

systemctl daemon-reload
systemctl stop mssql-server >/dev/null 2>&1 || true
EOF_VM

SA_PASSWORD_B64="$(printf '%s' "${MSSQL_SA_PASSWORD}" | base64 -w0)"
ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "sudo env SA_PASSWORD_B64='${SA_PASSWORD_B64}' MSSQL_PID_VALUE='${MSSQL_PID}' bash -s" <<'EOF_VM'
set -euo pipefail
sa_password="$(
  python3 - <<'PY'
import base64
import os

print(base64.b64decode(os.environ["SA_PASSWORD_B64"]).decode("utf-8"))
PY
)"

export LD_LIBRARY_PATH="/opt/mssql/lib:/opt/mssql-host-libs:${LD_LIBRARY_PATH:-}"

if [[ -f /var/opt/mssql/data/master.mdf ]]; then
  reset_only=1
  ACCEPT_EULA=Y MSSQL_SA_PASSWORD="${sa_password}" /opt/mssql/bin/mssql-conf -n set-sa-password
else
  reset_only=0
  ACCEPT_EULA=Y MSSQL_SA_PASSWORD="${sa_password}" MSSQL_PID="${MSSQL_PID_VALUE}" /opt/mssql/bin/mssql-conf -n setup
fi

systemctl enable mssql-server >/dev/null 2>&1 || true
if [[ "${reset_only}" == "1" ]]; then
  systemctl restart mssql-server
fi

for _ in $(seq 1 60); do
  if systemctl is-active --quiet mssql-server; then
    exit 0
  fi
  sleep 2
done

systemctl status --no-pager mssql-server >&2 || true
journalctl -u mssql-server --no-pager -n 100 >&2 || true
exit 1
EOF_VM

echo "SQL Server engine bootstrap complete for ${VM_NAME} (${VM_IP})"
