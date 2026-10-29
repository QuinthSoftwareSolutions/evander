#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_python_vm.sh [--vm-user USER] <vm-name> <host-checkout-root> <guest-repo-root>

Build a temporary host wheelhouse for a Python repo, copy it into a guest VM,
and create a guest-local virtualenv that installs the repo offline.

Arguments:
  vm-name            libvirt domain name.
  host-checkout-root Host checkout root containing pyproject.toml.
  guest-repo-root    Guest checkout path where the repo has already been cloned.

Options:
  --vm-user USER     Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH       Private key matching the guest authorized key.
  HOST_PYTHON_BIN    Host Python interpreter used to build the wheelhouse.
                     Default: python3
  GUEST_PYTHON_BIN   Guest Python interpreter used for the virtualenv.
                     Default: python3
  PYTHON_EXTRAS      Optional dependency group to install from pyproject.toml.
                     Default: dev
  GUEST_WHEELHOUSE_DIR
                     Guest wheelhouse destination. Default:
                     ~/.evander/python-wheelhouse/<repo-name>
EOF
}

VM_USER="agent"
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

if [[ $# -lt 3 ]]; then
  show_help >&2
  exit 1
fi

VM_NAME="$1"
HOST_CHECKOUT_ROOT="$(readlink -f "$2")"
GUEST_REPO_ROOT="$3"
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
PYTHON_EXTRAS="${PYTHON_EXTRAS:-dev}"
GUEST_REPO_BASENAME="$(basename "${GUEST_REPO_ROOT}")"
GUEST_WHEELHOUSE_DIR="${GUEST_WHEELHOUSE_DIR:-/home/${VM_USER}/.evander/python-wheelhouse/${GUEST_REPO_BASENAME}}"

if [[ ! -f "${HOST_CHECKOUT_ROOT}/pyproject.toml" ]]; then
  echo "Missing pyproject.toml under host checkout: ${HOST_CHECKOUT_ROOT}" >&2
  exit 1
fi

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

readarray -t PYTHON_BOOTSTRAP_METADATA < <(
  "${HOST_PYTHON_BIN}" - "${HOST_CHECKOUT_ROOT}/pyproject.toml" "${PYTHON_EXTRAS}" <<'PY'
import sys
import tomllib
from pathlib import Path

pyproject = Path(sys.argv[1])
extras_name = sys.argv[2]
data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

requires_python = data["project"].get("requires-python", "").strip()
dependencies = []
dependencies.extend(data.get("build-system", {}).get("requires", []))
dependencies.extend(data["project"].get("dependencies", []))
dependencies.extend(data["project"].get("optional-dependencies", {}).get(extras_name, []))

unique = []
seen = set()
for item in dependencies:
    if item not in seen:
        unique.append(item)
        seen.add(item)

if requires_python.startswith(">="):
    minimum = requires_python[2:].strip()
else:
    minimum = ""

print(f"REQUIRES_PYTHON_MIN={minimum}")
for item in unique:
    print(f"REQ={item}")
PY
)

REQUIRES_PYTHON_MIN=""
PACKAGE_REQUIREMENTS=()
for line in "${PYTHON_BOOTSTRAP_METADATA[@]}"; do
  case "${line}" in
    REQUIRES_PYTHON_MIN=*)
      REQUIRES_PYTHON_MIN="${line#REQUIRES_PYTHON_MIN=}"
      ;;
    REQ=*)
      PACKAGE_REQUIREMENTS+=("${line#REQ=}")
      ;;
  esac
done

if [[ ${#PACKAGE_REQUIREMENTS[@]} -eq 0 ]]; then
  echo "No Python requirements found in ${HOST_CHECKOUT_ROOT}/pyproject.toml" >&2
  exit 1
fi
BOOTSTRAP_REQUIREMENTS=(
  setuptools
  wheel
  virtualenv
)

TEMP_WHEELHOUSE="$(mktemp -d)"
trap 'rm -rf "${TEMP_WHEELHOUSE}"' EXIT

echo "Building Python wheelhouse from ${HOST_CHECKOUT_ROOT}"
"${HOST_PYTHON_BIN}" -m pip wheel \
  --wheel-dir "${TEMP_WHEELHOUSE}" \
  "${BOOTSTRAP_REQUIREMENTS[@]}" \
  "${PACKAGE_REQUIREMENTS[@]}"

VM_IP="$(get_vm_ip)"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_KEY_PATH}" "${CALLER_HOME}")

echo "Bootstrapping Python into ${VM_USER}@${VM_IP}"
ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "GUEST_REPO_ROOT='${GUEST_REPO_ROOT}' GUEST_WHEELHOUSE_DIR='${GUEST_WHEELHOUSE_DIR}' bash -s" <<'EOF'
set -euo pipefail
mkdir -p "${GUEST_WHEELHOUSE_DIR}"
rm -rf "${GUEST_WHEELHOUSE_DIR:?}/"*
if [[ ! -d "${GUEST_REPO_ROOT}" ]]; then
  echo "Guest repo path does not exist: ${GUEST_REPO_ROOT}" >&2
  exit 1
fi
EOF
scp "${SSH_OPTS[@]}" -r "${TEMP_WHEELHOUSE}/." "${VM_USER}@${VM_IP}:${GUEST_WHEELHOUSE_DIR}/"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" \
  "GUEST_REPO_ROOT='${GUEST_REPO_ROOT}' GUEST_WHEELHOUSE_DIR='${GUEST_WHEELHOUSE_DIR}' GUEST_PYTHON_BIN='${GUEST_PYTHON_BIN}' REQUIRES_PYTHON_MIN='${REQUIRES_PYTHON_MIN}' PYTHON_EXTRAS='${PYTHON_EXTRAS}' bash -s" <<'EOF'
set -euo pipefail

if ! command -v "${GUEST_PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Guest Python interpreter is not available: ${GUEST_PYTHON_BIN}" >&2
  exit 1
fi

if [[ -n "${REQUIRES_PYTHON_MIN}" ]]; then
  "${GUEST_PYTHON_BIN}" - "${REQUIRES_PYTHON_MIN}" <<'PY'
import sys

minimum = tuple(int(part) for part in sys.argv[1].split("."))
current = sys.version_info[: len(minimum)]
if current < minimum:
    raise SystemExit(
        f"Guest Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro} "
        f"does not satisfy requires-python>={sys.argv[1]}"
    )
PY
fi

if [[ ! -d "${GUEST_REPO_ROOT}" ]]; then
  echo "Guest repo path does not exist: ${GUEST_REPO_ROOT}" >&2
  exit 1
fi

"${GUEST_PYTHON_BIN}" - "${GUEST_WHEELHOUSE_DIR}" "${GUEST_REPO_ROOT}/.venv" <<'PY'
import sys
from pathlib import Path

wheelhouse = Path(sys.argv[1])
venv_path = sys.argv[2]

for wheel in sorted(wheelhouse.glob("*.whl")):
    sys.path.insert(0, str(wheel))

from virtualenv import cli_run

cli_run([venv_path])
PY
"${GUEST_REPO_ROOT}/.venv/bin/python" -m pip install \
  --no-index \
  --find-links "${GUEST_WHEELHOUSE_DIR}" \
  setuptools \
  wheel
"${GUEST_REPO_ROOT}/.venv/bin/python" -m pip install \
  --no-index \
  --find-links "${GUEST_WHEELHOUSE_DIR}" \
  --no-build-isolation \
  -e "${GUEST_REPO_ROOT}[${PYTHON_EXTRAS}]"
"${GUEST_REPO_ROOT}/.venv/bin/python" -m pytest -q --help >/dev/null
"${GUEST_REPO_ROOT}/.venv/bin/python" -m ruff check --help >/dev/null
EOF

echo "Python bootstrap complete for ${VM_NAME} (${VM_IP})"
