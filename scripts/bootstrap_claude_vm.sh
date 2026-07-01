#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_claude_vm.sh <vm-name> [vm-user]

Copies a host Claude Code CLI command into the guest and writes session-aware
Anthropic relay and MCP configuration.

Environment:
  HOST_CLAUDE_BIN       Host Claude Code binary. Default: `command -v claude`,
                        then caller-local NVM installs.
  ANTHROPIC_BASE_URL    Guest Anthropic relay base URL. Overrides guest session env when set.
  GATEWAY_URL           Guest MCP gateway URL. Default from guest session env.
  SSH_PRIVATE_KEY_PATH  SSH private key for guest access.
  VM_SSH_USER           Privileged guest SSH user used to wait for cloud-init and
                        repair guest home ownership. Default: agent-admin
  GUEST_BOOTSTRAP_WAIT_SECONDS
                        How long to wait for guest bootstrap readiness. Default: 180
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
# shellcheck source=lib/guest_ripgrep.sh
source "${SCRIPT_DIR}/lib/guest_ripgrep.sh"
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
ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-}"
GATEWAY_URL="${GATEWAY_URL:-}"
SSH_USER="${VM_SSH_USER:-agent-admin}"
GUEST_BOOTSTRAP_WAIT_SECONDS="${GUEST_BOOTSTRAP_WAIT_SECONDS:-180}"

resolve_host_claude_bin() {
  local candidate
  if [[ -n "${HOST_CLAUDE_BIN:-}" ]]; then
    printf '%s\n' "${HOST_CLAUDE_BIN}"
    return 0
  fi
  candidate="$(command -v claude || true)"
  if [[ -n "${candidate}" ]]; then
    printf '%s\n' "${candidate}"
    return 0
  fi
  for candidate in "${CALLER_HOME}"/.nvm/versions/node/*/bin/claude "${CALLER_HOME}"/.local/bin/claude; do
    if [[ -x "${candidate}" ]]; then
      printf '%s\n' "${candidate}"
      return 0
    fi
  done
  return 1
}

HOST_CLAUDE_BIN="$(resolve_host_claude_bin || true)"
if [[ -z "${HOST_CLAUDE_BIN}" ]]; then
  echo "Missing host Claude Code binary. Install Claude Code or set HOST_CLAUDE_BIN." >&2
  exit 1
fi
HOST_CLAUDE_BIN="$(readlink -f "${HOST_CLAUDE_BIN}")"
if [[ ! -x "${HOST_CLAUDE_BIN}" ]]; then
  echo "Host Claude Code binary is not executable: ${HOST_CLAUDE_BIN}" >&2
  exit 1
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_PRIVATE_KEY_PATH}" >&2
  echo "Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi
if ! [[ "${GUEST_BOOTSTRAP_WAIT_SECONDS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "GUEST_BOOTSTRAP_WAIT_SECONDS must be a positive integer: ${GUEST_BOOTSTRAP_WAIT_SECONDS}" >&2
  exit 1
fi

if ! vm_network_vm_exists "${VM_NAME}"; then
  echo "VM not found: ${VM_NAME}" >&2
  exit 1
fi

guest_user_home_writable() {
  ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" 'test -d "$HOME" && test -w "$HOME"' \
    >/dev/null 2>&1
}

repair_guest_home_with_user() {
  local ssh_user="$1"

  ssh "${SSH_OPTS[@]}" "${ssh_user}@${VM_IP}" \
    sudo -n env "VM_USER=${VM_USER}" "GUEST_BOOTSTRAP_WAIT_SECONDS=${GUEST_BOOTSTRAP_WAIT_SECONDS}" bash -s <<'EOF'
set -euo pipefail

if command -v cloud-init >/dev/null 2>&1; then
  if command -v timeout >/dev/null 2>&1; then
    cloud_init_status=0
    timeout "${GUEST_BOOTSTRAP_WAIT_SECONDS}" cloud-init status --wait >/dev/null 2>&1 || cloud_init_status=$?
    if [[ "${cloud_init_status}" == "124" ]]; then
      echo "Timed out waiting for guest cloud-init after ${GUEST_BOOTSTRAP_WAIT_SECONDS}s." >&2
      exit 1
    fi
    if [[ "${cloud_init_status}" != "0" ]]; then
      echo "cloud-init completed with a non-zero status; continuing with home ownership repair." >&2
    fi
  else
    cloud-init status --wait >/dev/null 2>&1 \
      || echo "cloud-init completed with a non-zero status; continuing with home ownership repair." >&2
  fi
fi

agent_home="$(getent passwd "${VM_USER}" | cut -d: -f6)"
if [[ -z "${agent_home}" ]]; then
  echo "Unknown guest user: ${VM_USER}" >&2
  exit 1
fi
agent_group="$(id -gn "${VM_USER}")"
install -d -m 0755 -o "${VM_USER}" -g "${agent_group}" "${agent_home}"
install -d -m 0775 -o "${VM_USER}" -g "${agent_group}" /artifacts
ln -sfn /artifacts "${agent_home}/artifacts"
chown -h "${VM_USER}:${agent_group}" "${agent_home}/artifacts"
chown -R "${VM_USER}:${agent_group}" "${agent_home}"
EOF
}

try_repair_guest_home() {
  local ssh_user="$1"

  [[ -n "${ssh_user}" ]] || return 1
  repair_guest_home_with_user "${ssh_user}" >/dev/null 2>&1
}

prepare_guest_home() {
  local deadline
  deadline=$((SECONDS + GUEST_BOOTSTRAP_WAIT_SECONDS))

  while (( SECONDS < deadline )); do
    if try_repair_guest_home "${SSH_USER}"; then
      return 0
    fi
    if [[ "${SSH_USER}" != "${VM_USER}" ]] && try_repair_guest_home "${VM_USER}"; then
      return 0
    fi
    if guest_user_home_writable; then
      return 0
    fi
    sleep 2
  done

  if repair_guest_home_with_user "${SSH_USER}"; then
    return 0
  fi
  if [[ "${SSH_USER}" != "${VM_USER}" ]] && repair_guest_home_with_user "${VM_USER}"; then
    return 0
  fi
  if guest_user_home_writable; then
    return 0
  fi

  echo "Timed out waiting for guest home to become writable for ${VM_USER}@${VM_IP}." >&2
  echo "Check cloud-init and guest user ownership, or set VM_SSH_USER to a sudo-capable guest user." >&2
  return 1
}

VM_IP="$(vm_network_wait_for_ip "${VM_NAME}" "${VM_WAIT_IP_TIMEOUT_SECONDS:-180}")"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_PRIVATE_KEY_PATH}" "${CALLER_HOME}")

PROVIDER_RUNNER_LIB_NAME="$(agent_provider_runner_lib_name claude)"
PROVIDER_RUNNER_LIB_PATH="$(agent_provider_runner_lib_path "${ROOT_DIR}/scripts/lib" claude)"
if [[ ! -f "${PROVIDER_RUNNER_LIB_PATH}" ]]; then
  echo "Missing Claude runner adapter: ${PROVIDER_RUNNER_LIB_PATH}" >&2
  exit 1
fi

echo "Bootstrapping Claude Code into ${VM_USER}@${VM_IP}"
prepare_guest_home
guest_ripgrep_seed "${VM_USER}" "${VM_IP}" "${SSH_OPTS[@]}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  mkdir -p ~/bin ~/.claude ~/.config/evander/agent-runner-lib ~/.evander/lib
  chmod 700 ~/.claude ~/.evander
"
scp "${SSH_OPTS[@]}" "${HOST_CLAUDE_BIN}" "${VM_USER}@${VM_IP}:~/bin/claude"
scp "${SSH_OPTS[@]}" \
  "${ROOT_DIR}/scripts/lib/agent_runner.sh" \
  "${PROVIDER_RUNNER_LIB_PATH}" \
  "${VM_USER}@${VM_IP}:~/.config/evander/agent-runner-lib/"
scp "${SSH_OPTS[@]}" \
  "${ROOT_DIR}/scripts/lib/agent_runner.sh" \
  "${PROVIDER_RUNNER_LIB_PATH}" \
  "${VM_USER}@${VM_IP}:~/.evander/lib/"

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
if [ -f /etc/evander/session.env ]; then
  # shellcheck disable=SC1091
  . /etc/evander/session.env
fi

CLAUDE_ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL:-${EVANDER_ANTHROPIC_BASE_URL:-${AGENT_SANDBOX_ANTHROPIC_BASE_URL:-http://192.168.250.1:11434}}}"
CLAUDE_GATEWAY_URL="${GATEWAY_URL:-${EVANDER_GATEWAY_URL:-${AGENT_SANDBOX_GATEWAY_URL:-http://192.168.250.1:8000/mcp}}}"
CLAUDE_SESSION_ID="${EVANDER_SESSION_ID:-${AGENT_SANDBOX_SESSION_ID:-}}"
if [ -n "${CLAUDE_SESSION_ID:-}" ]; then
  CLAUDE_ANTHROPIC_BASE_URL="${CLAUDE_ANTHROPIC_BASE_URL%/}/session/${CLAUDE_SESSION_ID}"
  CLAUDE_GATEWAY_URL="${CLAUDE_GATEWAY_URL}?session_id=${CLAUDE_SESSION_ID}"
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
    "evander": {
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
            "if [ -f /etc/evander/session.env ]; then",
            "if [ -f /etc/agent-sandbox/session.env ]; then",
            'if [ -f "$HOME/.claude/settings.json" ]; then',
        }:
            in_managed_block = True
            indent = line[: len(line) - len(line.lstrip())]
    return out if changed else profile_lines

managed = {
    'export PATH="$HOME/bin:$PATH"',
    'export AGENT_PROVIDER="${AGENT_PROVIDER:-claude}"',
    'export ANTHROPIC_AUTH_TOKEN="${ANTHROPIC_AUTH_TOKEN:-relay-placeholder}"',
}
lines = repair_missing_profile_fi(lines)
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
