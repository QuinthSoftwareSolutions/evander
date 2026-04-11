#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_frontend_deps_vm.sh [--vm-user USER] <vm-name> <host-checkout-root> <install-root> [install-root ...]

Copy a host-seeded Node runtime plus one or more frontend install roots into a
guest VM so builds can run offline from the guest clone.

Arguments:
  vm-name             libvirt domain name.
  host-checkout-root  Host repo checkout that owns the frontend install roots.
  install-root        One or more install roots inside the host checkout. Each
                      must contain package.json and node_modules.
  --vm-user USER      Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH        Private key matching the guest authorized key.
  HOST_NODE_ROOT      Host Node runtime to copy.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

VM_USER="agent"
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
shift
HOST_CHECKOUT_ROOT="$1"
shift
INSTALL_ROOTS=("$@")

# shellcheck source=lib/guest_ssh.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/guest_ssh.sh"

if [[ ${#INSTALL_ROOTS[@]} -lt 1 ]]; then
  echo "At least one install-root is required." >&2
  exit 1
fi

CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

HOST_NODE_ROOT="${HOST_NODE_ROOT:-${CALLER_HOME}/.nvm/versions/node/v24.14.0}"
SSH_OPTS=(
  -i "${SSH_KEY_PATH}"
)

if [[ ! -d "${HOST_NODE_ROOT}" ]]; then
  echo "Missing host Node runtime: ${HOST_NODE_ROOT}" >&2
  exit 1
fi

if [[ ! -d "${HOST_CHECKOUT_ROOT}" ]]; then
  echo "Missing host checkout root: ${HOST_CHECKOUT_ROOT}" >&2
  exit 1
fi

HOST_CHECKOUT_ROOT="$(readlink -f "${HOST_CHECKOUT_ROOT}")"

INSTALL_ROOTS_JSON="$(
  python3 - "${HOST_CHECKOUT_ROOT}" "${INSTALL_ROOTS[@]}" <<'PY'
import json
import sys
from pathlib import Path

checkout = Path(sys.argv[1]).resolve()
payload = []
for raw in sys.argv[2:]:
    root = Path(raw).resolve()
    try:
        relative = root.relative_to(checkout)
    except ValueError:
        print(f"Install root must be inside host checkout: {raw}", file=sys.stderr)
        sys.exit(1)
    if not (root / "package.json").is_file():
        print(f"Missing package.json under install root: {root}", file=sys.stderr)
        sys.exit(1)
    if not (root / "node_modules").is_dir():
        print(f"Missing node_modules under install root: {root}", file=sys.stderr)
        sys.exit(1)
    payload.append(
        {
            "absolute": str(root),
            "relative": relative.as_posix(),
        }
    )
print(json.dumps(payload))
PY
)"

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

echo "Bootstrapping frontend dependencies into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  sudo mkdir -p /home/${VM_USER}
  sudo chown -R ${VM_USER}:${VM_USER} /home/${VM_USER}
  mkdir -p ~/toolchains ~/frontend-deps-runtime/install-roots ~/.cache
  rm -rf ~/frontend-deps-runtime/install-roots/*
  rm -rf ~/toolchains/node-v24.14.0
  mkdir -p ~/toolchains/node-v24.14.0
"

tar -C "${HOST_NODE_ROOT}" -cf - . \
  | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "tar -C \"\$HOME/toolchains/node-v24.14.0\" -xf -"

while IFS= read -r entry; do
  root_abs="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["absolute"])' "${entry}")"
  root_rel="$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["relative"])' "${entry}")"
  ssh -n "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "mkdir -p \"\$HOME/frontend-deps-runtime/install-roots/${root_rel}\""
  tar -C "${root_abs}" -cf - \
    package.json \
    node_modules \
    package-lock.json \
    npm-shrinkwrap.json \
    pnpm-lock.yaml \
    yarn.lock \
    .yarnrc.yml \
    .npmrc \
    angular.json \
    workspace.json \
    nx.json \
    --ignore-failed-read \
    | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "tar -C \"\$HOME/frontend-deps-runtime/install-roots/${root_rel}\" -xf -"
done < <(python3 -c 'import json,sys; [print(json.dumps(item)) for item in json.loads(sys.argv[1])]' "${INSTALL_ROOTS_JSON}")

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "bash -s" <<'EOF'
set -euo pipefail

append_node_path_setup() {
  local target="$1"
  python3 - "${target}" <<'PY'
from pathlib import Path
import sys

target = Path(sys.argv[1]).expanduser()
target.parent.mkdir(parents=True, exist_ok=True)
if target.exists():
    lines = target.read_text(encoding="utf-8").splitlines()
else:
    lines = []

managed = {
    'export PATH="$HOME/toolchains/node-v24.14.0/bin:$PATH"',
    'export PATH="$HOME/bin:$HOME/toolchains/node-v24.14.0/bin:$PATH"',
}
filtered = [line for line in lines if line.strip() not in managed]
filtered.append('export PATH="$HOME/bin:$HOME/toolchains/node-v24.14.0/bin:$PATH"')
target.write_text("\n".join(filtered) + "\n", encoding="utf-8")
PY
}

append_node_path_setup ~/.profile
append_node_path_setup ~/.bashrc

export PATH="$HOME/bin:$HOME/toolchains/node-v24.14.0/bin:$PATH"

node --version
npm --version
npx --version
test -d "$HOME/frontend-deps-runtime/install-roots"
EOF

echo "Frontend dependency bootstrap complete for ${VM_NAME} (${VM_IP})"
