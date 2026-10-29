#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_python_packages_vm.sh [--vm-user USER] [--venv PATH] <vm-name> <package-spec> [package-spec ...]

Build a temporary host wheelhouse for one or more Python package specs, copy it into a guest VM,
and install those packages offline into a guest-local tools virtualenv.

Arguments:
  vm-name             libvirt domain name.
  package-spec        One or more pip-compatible package specifiers, for example:
                      openai
                      openai>=1.0
                      pillow==11.2.1

Options:
  --vm-user USER      Guest user to configure. Default: agent
  --venv PATH         Guest virtualenv path to create/reuse. Default:
                      /home/<user>/.evander/python-tools-venv
  -h, --help          Show this help text.

Environment:
  SSH_KEY_PATH         Private key matching the guest authorized key.
  HOST_PYTHON_BIN      Host Python interpreter used to build the wheelhouse.
                       Default: python3
  GUEST_PYTHON_BIN     Guest Python interpreter used for the virtualenv.
                       Default: python3
  GUEST_WHEELHOUSE_DIR Guest wheelhouse destination. Default:
                       /home/<user>/.evander/python-wheelhouse/tools
EOF
}

VM_USER="agent"
GUEST_VENV_PATH=""

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vm-user)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      VM_USER="$2"
      shift 2
      ;;
    --venv)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      GUEST_VENV_PATH="$2"
      shift 2
      ;;
    -h|--help)
      show_help
      exit 0
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

if [[ $# -lt 2 ]]; then
  show_help >&2
  exit 1
fi

VM_NAME="$1"
shift
PACKAGE_SPECS=("$@")

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/guest_ssh.sh
source "${SCRIPT_DIR}/lib/guest_ssh.sh"

CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

HOST_PYTHON_BIN="${HOST_PYTHON_BIN:-python3}"
GUEST_PYTHON_BIN="${GUEST_PYTHON_BIN:-python3}"
GUEST_VENV_PATH="${GUEST_VENV_PATH:-/home/${VM_USER}/.evander/python-tools-venv}"
GUEST_WHEELHOUSE_DIR="${GUEST_WHEELHOUSE_DIR:-/home/${VM_USER}/.evander/python-wheelhouse/tools}"

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_KEY_PATH}" >&2
  echo "Set SSH_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

if ! "${HOST_PYTHON_BIN}" --version >/dev/null 2>&1; then
  echo "Host Python is not executable: ${HOST_PYTHON_BIN}" >&2
  exit 1
fi

if ! "${HOST_PYTHON_BIN}" -m pip --version >/dev/null 2>&1; then
  if ! "${HOST_PYTHON_BIN}" -m ensurepip --upgrade >/dev/null 2>&1; then
    echo "Host Python must provide pip: ${HOST_PYTHON_BIN}" >&2
    exit 1
  fi
  if ! "${HOST_PYTHON_BIN}" -m pip --version >/dev/null 2>&1; then
    echo "Host Python must provide pip: ${HOST_PYTHON_BIN}" >&2
    exit 1
  fi
fi

get_vm_ip() {
  if guest_ssh_resolve_vm_ip "${VM_NAME}"; then
    return 0
  fi

  echo "Could not determine an IPv4 address for VM ${VM_NAME}." >&2
  echo "Run this script with sudo, grant the current user libvirt access, or authorize the pkexec prompt." >&2
  return 1
}

BOOTSTRAP_REQUIREMENTS=(
  setuptools
  wheel
  virtualenv
)

TEMP_WHEELHOUSE="$(mktemp -d)"
TEMP_PACKAGE_SPECS_JSON="$(mktemp)"
trap 'rm -rf "${TEMP_WHEELHOUSE}"; rm -f "${TEMP_PACKAGE_SPECS_JSON}"' EXIT

echo "Building Python wheelhouse for guest tools: ${PACKAGE_SPECS[*]}"
"${HOST_PYTHON_BIN}" -m pip wheel \
  --wheel-dir "${TEMP_WHEELHOUSE}" \
  "${BOOTSTRAP_REQUIREMENTS[@]}" \
  "${PACKAGE_SPECS[@]}"

VM_IP="$(get_vm_ip)"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_KEY_PATH}" "${CALLER_HOME}")

echo "Bootstrapping Python packages into ${VM_USER}@${VM_IP}"
ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" \
  "GUEST_WHEELHOUSE_DIR='${GUEST_WHEELHOUSE_DIR}' GUEST_VENV_PATH='${GUEST_VENV_PATH}' bash -s" <<'EOF'
set -euo pipefail
mkdir -p "${GUEST_WHEELHOUSE_DIR}"
rm -rf "${GUEST_WHEELHOUSE_DIR:?}/"*
mkdir -p "$(dirname "${GUEST_VENV_PATH}")"
EOF

scp "${SSH_OPTS[@]}" -r "${TEMP_WHEELHOUSE}/." "${VM_USER}@${VM_IP}:${GUEST_WHEELHOUSE_DIR}/"

PACKAGE_SPECS_JSON="$("${HOST_PYTHON_BIN}" - "${PACKAGE_SPECS[@]}" <<'PY'
import json
import sys
print(json.dumps(sys.argv[1:]))
PY
)"
printf '%s' "${PACKAGE_SPECS_JSON}" > "${TEMP_PACKAGE_SPECS_JSON}"
GUEST_PACKAGE_SPECS_JSON="${GUEST_WHEELHOUSE_DIR}/package-specs.json"
scp "${SSH_OPTS[@]}" "${TEMP_PACKAGE_SPECS_JSON}" "${VM_USER}@${VM_IP}:${GUEST_PACKAGE_SPECS_JSON}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" \
  "GUEST_WHEELHOUSE_DIR='${GUEST_WHEELHOUSE_DIR}' GUEST_VENV_PATH='${GUEST_VENV_PATH}' GUEST_PYTHON_BIN='${GUEST_PYTHON_BIN}' GUEST_PACKAGE_SPECS_JSON='${GUEST_PACKAGE_SPECS_JSON}' bash -s" <<'EOF'
set -euo pipefail

if ! command -v "${GUEST_PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Guest Python interpreter is not available: ${GUEST_PYTHON_BIN}" >&2
  exit 1
fi

"${GUEST_PYTHON_BIN}" - "${GUEST_WHEELHOUSE_DIR}" "${GUEST_VENV_PATH}" <<'PY'
import os
import sys
from pathlib import Path

wheelhouse = Path(sys.argv[1])
venv_path = sys.argv[2]

for wheel in sorted(wheelhouse.glob("*.whl")):
    sys.path.insert(0, str(wheel))

from virtualenv import cli_run

cli_run([venv_path])
PY

"${GUEST_VENV_PATH}/bin/python" -m pip install \
  --no-index \
  --find-links "${GUEST_WHEELHOUSE_DIR}" \
  setuptools \
  wheel

"${GUEST_VENV_PATH}/bin/python" - "${GUEST_PACKAGE_SPECS_JSON}" <<'PY' | while IFS= read -r package_spec; do
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    specs = json.load(handle)
for spec in specs:
    print(spec)
PY
  [[ -n "${package_spec}" ]] || continue
  "${GUEST_VENV_PATH}/bin/python" -m pip install \
    --no-index \
    --find-links "${GUEST_WHEELHOUSE_DIR}" \
    "${package_spec}"
done
EOF

echo "Python package bootstrap complete for ${VM_NAME} (${VM_IP})"
echo "Guest virtualenv: ${GUEST_VENV_PATH}"
