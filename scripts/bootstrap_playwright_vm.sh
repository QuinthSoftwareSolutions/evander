#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_playwright_vm.sh <vm-name> [vm-user]

Copy a host-seeded Node + Playwright runtime into a guest VM and configure the
guest shell environment to use it offline.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH                 Private key matching the guest authorized key.
  HOST_NODE_ROOT               Host Node runtime to copy.
  HOST_PLAYWRIGHT_NODE_MODULES Host node_modules directory containing Playwright.
  HOST_PLAYWRIGHT_BROWSERS     Host Playwright browser cache.
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

HOST_NODE_ROOT="${HOST_NODE_ROOT:-${CALLER_HOME}/.nvm/versions/node/v24.14.0}"
HOST_PLAYWRIGHT_NODE_MODULES="${HOST_PLAYWRIGHT_NODE_MODULES:-${CALLER_HOME}/node_modules}"
HOST_PLAYWRIGHT_BROWSERS="${HOST_PLAYWRIGHT_BROWSERS:-${CALLER_HOME}/.cache/ms-playwright}"
SSH_OPTS=(
  -i "${SSH_KEY_PATH}"
)

if [[ ! -d "${HOST_NODE_ROOT}" ]]; then
  echo "Missing host Node runtime: ${HOST_NODE_ROOT}" >&2
  exit 1
fi

if [[ ! -d "${HOST_PLAYWRIGHT_NODE_MODULES}/playwright" ]]; then
  echo "Missing host Playwright package: ${HOST_PLAYWRIGHT_NODE_MODULES}/playwright" >&2
  exit 1
fi

if [[ ! -d "${HOST_PLAYWRIGHT_BROWSERS}" ]]; then
  echo "Missing host Playwright browser cache: ${HOST_PLAYWRIGHT_BROWSERS}" >&2
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

echo "Bootstrapping Playwright into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  mkdir -p ~/toolchains ~/playwright-runtime/node_modules ~/.cache ~/.agent-sandbox
  chmod 700 ~/.agent-sandbox
"

scp "${SSH_OPTS[@]}" -r "${HOST_NODE_ROOT}" "${VM_USER}@${VM_IP}:~/toolchains/node-v24.14.0"
scp "${SSH_OPTS[@]}" -r "${HOST_PLAYWRIGHT_NODE_MODULES}/playwright" "${VM_USER}@${VM_IP}:~/playwright-runtime/node_modules/"
scp "${SSH_OPTS[@]}" -r "${HOST_PLAYWRIGHT_NODE_MODULES}/playwright-core" "${VM_USER}@${VM_IP}:~/playwright-runtime/node_modules/"
scp "${SSH_OPTS[@]}" -r "${HOST_PLAYWRIGHT_BROWSERS}" "${VM_USER}@${VM_IP}:~/.cache/ms-playwright"

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

def repair_missing_profile_fi(profile_lines):
    out = []
    changed = False
    in_managed_block = False
    indent = ""
    for line in profile_lines:
        stripped = line.strip()
        if in_managed_block and stripped == "fi":
            in_managed_block = False
        elif in_managed_block and line == line.lstrip() and stripped.startswith("export "):
            out.append(f"{indent}fi")
            changed = True
            in_managed_block = False
        out.append(line)
        if stripped in {
            "if [ -f /etc/agent-sandbox/session.env ]; then",
            'if [ -f "$HOME/.claude/settings.json" ]; then',
        }:
            in_managed_block = True
            indent = line[: len(line) - len(line.lstrip())]
    return out if changed else profile_lines


def _is_if_start(stripped_line):
    return stripped_line.startswith("if ") and stripped_line.endswith("then")


playwright_block_start = 'if [ -z "${PLAYWRIGHT_MCP_EXECUTABLE_PATH:-}" ] && command -v node >/dev/null 2>&1; then'
managed_exports = {
    'export PATH="$HOME/toolchains/node-v24.14.0/bin:$PATH"',
    'export PATH="$HOME/bin:$HOME/toolchains/node-v24.14.0/bin:$PATH"',
    'export AGENT_SANDBOX_PLAYWRIGHT_RUNTIME="$HOME/playwright-runtime/node_modules"',
    'export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"',
    'export NODE_PATH="$AGENT_SANDBOX_PLAYWRIGHT_RUNTIME"',
    'export NODE_PATH="$HOME/playwright-runtime/node_modules"',
    'export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1"',
    'export PLAYWRIGHT_MCP_SANDBOX="0"',
}


def remove_managed_playwright_lines(profile_lines):
    filtered_lines = []
    i = 0
    while i < len(profile_lines):
        stripped = profile_lines[i].strip()
        if stripped == playwright_block_start:
            depth = 0
            while i < len(profile_lines):
                current = profile_lines[i].strip()
                if _is_if_start(current):
                    depth += 1
                elif current == "fi":
                    depth -= 1
                    i += 1
                    if depth <= 0:
                        break
                    continue
                i += 1
            continue
        if stripped in managed_exports:
            i += 1
            continue
        filtered_lines.append(profile_lines[i])
        i += 1
    return filtered_lines


lines = repair_missing_profile_fi(lines)
filtered = remove_managed_playwright_lines(lines)
filtered.extend(
    [
        'export PATH="$HOME/bin:$HOME/toolchains/node-v24.14.0/bin:$PATH"',
        'export AGENT_SANDBOX_PLAYWRIGHT_RUNTIME="$HOME/playwright-runtime/node_modules"',
        'export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"',
        'export NODE_PATH="$AGENT_SANDBOX_PLAYWRIGHT_RUNTIME"',
        'export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1"',
        'export PLAYWRIGHT_MCP_SANDBOX="0"',
        'if [ -z "${PLAYWRIGHT_MCP_EXECUTABLE_PATH:-}" ] && command -v node >/dev/null 2>&1; then',
        '  if _agent_sandbox_playwright_chromium="$(node -e \'const fs = require("fs"); const path = require("path"); const runtime = process.env.AGENT_SANDBOX_PLAYWRIGHT_RUNTIME; const { chromium } = require(path.join(runtime, "playwright")); const executable = chromium.executablePath(); if (!fs.existsSync(executable)) process.exit(1); process.stdout.write(executable);\' 2>/dev/null)" && [ -n "$_agent_sandbox_playwright_chromium" ]; then',
        '    export PLAYWRIGHT_MCP_EXECUTABLE_PATH="$_agent_sandbox_playwright_chromium"',
        '  fi',
        '  unset _agent_sandbox_playwright_chromium',
        'fi',
    ]
)
target.write_text("\n".join(filtered) + "\n", encoding="utf-8")
PY
}

append_node_path_setup ~/.profile

export PATH="$HOME/bin:$HOME/toolchains/node-v24.14.0/bin:$PATH"
export AGENT_SANDBOX_PLAYWRIGHT_RUNTIME="$HOME/playwright-runtime/node_modules"
export PLAYWRIGHT_BROWSERS_PATH="$HOME/.cache/ms-playwright"
export NODE_PATH="$AGENT_SANDBOX_PLAYWRIGHT_RUNTIME"
export PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD="1"
export PLAYWRIGHT_MCP_SANDBOX="0"
if _agent_sandbox_playwright_chromium="$(node -e 'const fs = require("fs"); const path = require("path"); const runtime = process.env.AGENT_SANDBOX_PLAYWRIGHT_RUNTIME; const { chromium } = require(path.join(runtime, "playwright")); const executable = chromium.executablePath(); if (!fs.existsSync(executable)) process.exit(1); process.stdout.write(executable);' 2>/dev/null)" && [[ -n "${_agent_sandbox_playwright_chromium}" ]]; then
  export PLAYWRIGHT_MCP_EXECUTABLE_PATH="${_agent_sandbox_playwright_chromium}"
else
  echo "Could not resolve seeded Chromium executable from ${AGENT_SANDBOX_PLAYWRIGHT_RUNTIME}" >&2
  exit 1
fi
unset _agent_sandbox_playwright_chromium

node --version
node -e 'const fs = require("fs"); const path = require("path"); const runtime = process.env.AGENT_SANDBOX_PLAYWRIGHT_RUNTIME; const { chromium } = require(path.join(runtime, "playwright")); const executable = chromium.executablePath(); console.log(executable); if (!fs.existsSync(executable)) process.exit(3);'
printf 'PLAYWRIGHT_MCP_EXECUTABLE_PATH=%s\n' "${PLAYWRIGHT_MCP_EXECUTABLE_PATH}"
EOF

echo "Playwright bootstrap complete for ${VM_NAME} (${VM_IP})"
