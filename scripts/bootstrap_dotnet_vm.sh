#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_dotnet_vm.sh <vm-name> [vm-user]

Copy a host-installed .NET SDK plus host-seeded dotnet tools into a guest VM
and configure the guest shell to use them offline.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH              Private key matching the guest authorized key.
  HOST_DOTNET_ROOT          Host .NET installation root. Default: /usr/lib/dotnet
  HOST_DOTNET_TOOLS_ROOT    Host dotnet global tools root. Default: ~/.dotnet
  HOST_NUGET_PACKAGES_ROOT  Host NuGet package cache. Default: ~/.nuget/packages
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
# shellcheck source=lib/guest_ssh.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/guest_ssh.sh"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

HOST_DOTNET_ROOT="${HOST_DOTNET_ROOT:-/usr/lib/dotnet}"
HOST_DOTNET_TOOLS_ROOT="${HOST_DOTNET_TOOLS_ROOT:-${CALLER_HOME}/.dotnet}"
HOST_NUGET_PACKAGES_ROOT="${HOST_NUGET_PACKAGES_ROOT:-${CALLER_HOME}/.nuget/packages}"
SSH_OPTS=(
  -i "${SSH_KEY_PATH}"
)

if [[ ! -d "${HOST_DOTNET_ROOT}" ]]; then
  echo "Missing host .NET installation: ${HOST_DOTNET_ROOT}" >&2
  exit 1
fi

if [[ ! -x "${HOST_DOTNET_ROOT}/dotnet" ]]; then
  echo "Missing dotnet launcher under ${HOST_DOTNET_ROOT}" >&2
  exit 1
fi

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_KEY_PATH}" >&2
  echo "Set SSH_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

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

echo "Bootstrapping .NET into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  mkdir -p ~/toolchains/dotnet ~/.dotnet-cli ~/.dotnet ~/.nuget/packages ~/.evander
  chmod 700 ~/.evander
  rm -rf ~/toolchains/dotnet/*
"

tar -C "${HOST_DOTNET_ROOT}" -cf - . \
  | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "tar -C ~/toolchains/dotnet -xf -"

if [[ -d "${HOST_DOTNET_TOOLS_ROOT}/tools" ]]; then
  ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "mkdir -p ~/.dotnet/tools && rm -rf ~/.dotnet/tools/*"
  tar -C "${HOST_DOTNET_TOOLS_ROOT}/tools" -cf - . \
    | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "tar -C ~/.dotnet/tools -xf -"
fi

if [[ -d "${HOST_DOTNET_TOOLS_ROOT}/toolResolverCache" ]]; then
  ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "mkdir -p ~/.dotnet/toolResolverCache && rm -rf ~/.dotnet/toolResolverCache/*"
  tar -C "${HOST_DOTNET_TOOLS_ROOT}/toolResolverCache" -cf - . \
    | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "tar -C ~/.dotnet/toolResolverCache -xf -"
fi

TOOLS_PACKAGE_PATHS="$(
  python3 - "${HOST_DOTNET_TOOLS_ROOT}" "${HOST_NUGET_PACKAGES_ROOT}" <<'PY'
import json
import sys
from pathlib import Path

tools_root = Path(sys.argv[1]).expanduser()
packages_root = Path(sys.argv[2]).expanduser().resolve()
cache_root = tools_root / "toolResolverCache"
seen = set()
for path in cache_root.rglob("*"):
    if not path.is_file():
        continue
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        continue
    if not isinstance(payload, list):
        continue
    for entry in payload:
        executable = entry.get("PathToExecutable")
        if not executable:
          continue
        exe_path = Path(executable).expanduser().resolve()
        try:
            relative = exe_path.relative_to(packages_root)
        except ValueError:
            continue
        if len(relative.parts) < 2:
            continue
        seen.add("/".join(relative.parts[:2]))
for item in sorted(seen):
    print(item)
PY
)"

while IFS= read -r package_path; do
  [[ -z "${package_path}" ]] && continue
  if [[ ! -d "${HOST_NUGET_PACKAGES_ROOT}/${package_path}" ]]; then
    continue
  fi
  ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "mkdir -p \"\$HOME/.nuget/packages/${package_path%/*}\""
  tar -C "${HOST_NUGET_PACKAGES_ROOT}" -cf - "${package_path}" \
    | ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "tar -C ~/.nuget/packages -xf -"
done <<< "${TOOLS_PACKAGE_PATHS}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "bash -s" <<'EOF'
set -euo pipefail

if [[ ! -x "$HOME/toolchains/dotnet/dotnet" ]]; then
  echo "Expected dotnet launcher missing at $HOME/toolchains/dotnet/dotnet" >&2
  exit 1
fi

if ! grep -q 'DOTNET_ROOT="$HOME/toolchains/dotnet"' ~/.profile 2>/dev/null; then
  cat >> ~/.profile <<PROFILE
export DOTNET_ROOT="$HOME/toolchains/dotnet"
export DOTNET_CLI_HOME="$HOME/.dotnet-cli"
export PATH="$HOME/toolchains/dotnet:$HOME/.dotnet/tools:$PATH"
PROFILE
fi

if ! grep -q 'DOTNET_ROOT="$HOME/toolchains/dotnet"' ~/.bashrc 2>/dev/null; then
  cat >> ~/.bashrc <<BASHRC
export DOTNET_ROOT="$HOME/toolchains/dotnet"
export DOTNET_CLI_HOME="$HOME/.dotnet-cli"
export PATH="$HOME/toolchains/dotnet:$HOME/.dotnet/tools:$PATH"
BASHRC
fi

export DOTNET_ROOT="$HOME/toolchains/dotnet"
export DOTNET_CLI_HOME="$HOME/.dotnet-cli"
export PATH="$HOME/toolchains/dotnet:$HOME/.dotnet/tools:$PATH"

if [[ ! -x "$HOME/.dotnet/tools/dotnet-ef" && -f "$HOME/.nuget/packages/dotnet-ef/3.1.8/tools/netcoreapp3.1/any/dotnet-ef.dll" ]]; then
  cat > "$HOME/.dotnet/tools/dotnet-ef" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
exec "$HOME/toolchains/dotnet/dotnet" "$HOME/.nuget/packages/dotnet-ef/3.1.8/tools/netcoreapp3.1/any/dotnet-ef.dll" "\$@"
WRAPPER
  chmod +x "$HOME/.dotnet/tools/dotnet-ef"
fi

dotnet --info
if command -v dotnet-ef >/dev/null 2>&1; then
  dotnet-ef --version
fi
EOF

echo ".NET bootstrap complete for ${VM_NAME} (${VM_IP})"
