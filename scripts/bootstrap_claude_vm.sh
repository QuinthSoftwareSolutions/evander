#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_claude_vm.sh <vm-name> [vm-user]

Copies a host Claude Code CLI command into the guest and writes session-aware
Anthropic relay and MCP configuration.

Environment:
  HOST_CLAUDE_BIN       Host Claude Code binary. Default: `command -v claude`.
  ANTHROPIC_BASE_URL    Guest Anthropic relay base URL. Overrides guest session env when set.
  GATEWAY_URL           Guest MCP gateway URL. Default from guest session env.
  SSH_PRIVATE_KEY_PATH  SSH private key for guest access.
EOF
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 1 || $# -gt 2 ]]; then
  show_help >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"
# shellcheck source=lib/guest_ssh.sh
source "${SCRIPT_DIR}/lib/guest_ssh.sh"
# shellcheck source=lib/agent_provider_assets.sh
source "${SCRIPT_DIR}/lib/agent_provider_assets.sh"

VM_NAME="$1"
VM_USER="${2:-agent}"
CALLER_USER="${SUDO_USER:-${USER:-$(id -un)}}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
if [[ -z "${SSH_PRIVATE_KEY_PATH:-}" && -n "${SSH_KEY_PATH:-}" ]]; then
  SSH_PRIVATE_KEY_PATH="${SSH_KEY_PATH}"
fi
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" && -z "${SSH_KEY_PATH:-}" ]]; then
  SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi
HOST_CLAUDE_BIN="${HOST_CLAUDE_BIN:-$(command -v claude || true)}"
ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-}"
GATEWAY_URL="${GATEWAY_URL:-}"

if [[ -z "${HOST_CLAUDE_BIN}" || ! -f "${HOST_CLAUDE_BIN}" ]]; then
  echo "Missing host Claude Code binary. Install Claude Code or set HOST_CLAUDE_BIN." >&2
  exit 1
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_PRIVATE_KEY_PATH}" >&2
  echo "Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

if ! vm_network_vm_exists "${VM_NAME}"; then
  echo "VM not found: ${VM_NAME}" >&2
  exit 1
fi

VM_IP="$(vm_network_wait_for_ip "${VM_NAME}" "${VM_WAIT_IP_TIMEOUT_SECONDS:-180}")"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_PRIVATE_KEY_PATH}" "${CALLER_HOME}")

PROVIDER_RUNNER_LIB_NAME="$(agent_provider_runner_lib_name claude)"
PROVIDER_RUNNER_LIB_PATH="$(agent_provider_runner_lib_path "${ROOT_DIR}/scripts/lib" claude)"
if [[ ! -f "${PROVIDER_RUNNER_LIB_PATH}" ]]; then
  echo "Missing Claude runner adapter: ${PROVIDER_RUNNER_LIB_PATH}" >&2
  exit 1
fi

echo "Bootstrapping Claude Code into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  mkdir -p ~/bin ~/.claude ~/.config/agent-sandbox/agent-runner-lib ~/.agent-sandbox/lib
  chmod 700 ~/.claude ~/.agent-sandbox
"
scp "${SSH_OPTS[@]}" "${HOST_CLAUDE_BIN}" "${VM_USER}@${VM_IP}:~/bin/claude"
scp "${SSH_OPTS[@]}" \
  "${ROOT_DIR}/scripts/lib/agent_runner.sh" \
  "${PROVIDER_RUNNER_LIB_PATH}" \
  "${VM_USER}@${VM_IP}:~/.config/agent-sandbox/agent-runner-lib/"
scp "${SSH_OPTS[@]}" \
  "${ROOT_DIR}/scripts/lib/agent_runner.sh" \
  "${PROVIDER_RUNNER_LIB_PATH}" \
  "${VM_USER}@${VM_IP}:~/.agent-sandbox/lib/"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" \
  "ANTHROPIC_BASE_URL='${ANTHROPIC_BASE_URL}' GATEWAY_URL='${GATEWAY_URL}' bash -s" <<'EOF'
set -euo pipefail
chmod +x ~/bin/claude

if [ -f ~/.agent/config.env ]; then
  # shellcheck disable=SC1091
  . ~/.agent/config.env
fi
if [ -f /etc/agent-sandbox/session.env ]; then
  # shellcheck disable=SC1091
  . /etc/agent-sandbox/session.env
fi

CLAUDE_ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-${AGENT_SANDBOX_ANTHROPIC_BASE_URL:-http://192.168.250.1:11434}}"
CLAUDE_GATEWAY_URL="${GATEWAY_URL:-${AGENT_SANDBOX_GATEWAY_URL:-http://192.168.250.1:8000/mcp}}"
if [ -n "${AGENT_SANDBOX_SESSION_ID:-}" ]; then
  CLAUDE_ANTHROPIC_BASE_URL="${CLAUDE_ANTHROPIC_BASE_URL%/}/session/${AGENT_SANDBOX_SESSION_ID}"
  CLAUDE_GATEWAY_URL="${CLAUDE_GATEWAY_URL}?session_id=${AGENT_SANDBOX_SESSION_ID}"
fi

cat > ~/.claude/settings.json <<CONFIG
{
  "attribution": {
    "commit": ""
  },
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "relay-placeholder",
    "ANTHROPIC_BASE_URL": "${CLAUDE_ANTHROPIC_BASE_URL}",
    "CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY": "1",
    "DISABLE_TELEMETRY": "1"
  }
}
CONFIG

cat > ~/.claude/mcp-config.json <<CONFIG
{
  "mcpServers": {
    "agent_sandbox": {
      "type": "http",
      "url": "${CLAUDE_GATEWAY_URL}",
      "tools": ["*"]
    }
  }
}
CONFIG

append_shell_setup() {
  local target="$1"
  python3 - "${target}" <<'PY'
from pathlib import Path
import sys

target = Path(sys.argv[1]).expanduser()
target.parent.mkdir(parents=True, exist_ok=True)
lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
managed = {
    'export PATH="$HOME/bin:$PATH"',
    'export AGENT_PROVIDER="${AGENT_PROVIDER:-claude}"',
    'export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-relay-placeholder}"',
}
filtered = [line for line in lines if line.strip() not in managed]
filtered.extend(sorted(managed))
target.write_text("\n".join(filtered) + "\n", encoding="utf-8")
PY
  if ! grep -q '.claude/settings.json' "${target}" 2>/dev/null; then
    cat >> "${target}" <<'PROFILE'
if [ -f "$HOME/.claude/settings.json" ]; then
  export ANTHROPIC_BASE_URL="$(python3 -c 'import json, pathlib; p=pathlib.Path.home()/".claude/settings.json"; print((json.loads(p.read_text()).get("env") or {}).get("ANTHROPIC_BASE_URL", ""))' 2>/dev/null || true)"
fi
PROFILE
  fi
}

append_shell_setup ~/.profile
append_shell_setup ~/.bashrc

export PATH="$HOME/bin:$PATH"
claude --version
EOF
