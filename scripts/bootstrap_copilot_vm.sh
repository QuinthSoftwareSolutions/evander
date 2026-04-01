#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_copilot_vm.sh <vm-name> [vm-user]

Copy the host GitHub Copilot CLI into a guest VM and configure it to use the host
MCP gateway plus host-mediated GitHub authentication fallback.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH            Private key matching the guest authorized key.
  HOST_COPILOT_BIN        Host GitHub Copilot CLI binary. Default: `command -v copilot`
  ENABLE_GUEST_HEARTBEAT  Install and enable the guest heartbeat timer (0/1, default: 0).
  GUEST_HEARTBEAT_PROMPT  Prompt passed to `copilot --continue -p ...` from the guest timer.
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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/guest_ssh.sh
source "${SCRIPT_DIR}/lib/guest_ssh.sh"
# shellcheck source=lib/agent_provider_assets.sh
source "${SCRIPT_DIR}/lib/agent_provider_assets.sh"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

HOST_COPILOT_BIN="${HOST_COPILOT_BIN:-$(command -v copilot || true)}"
ENABLE_GUEST_HEARTBEAT="${ENABLE_GUEST_HEARTBEAT:-0}"
GUEST_HEARTBEAT_PROMPT="${GUEST_HEARTBEAT_PROMPT:-Resume work for this session. Check for pending feedback first, then continue the most recent task.}"
SSH_OPTS=(
  -i "${SSH_KEY_PATH}"
)
LEGACY_HEARTBEAT_DIR="${ROOT_DIR}/scripts/guest"
PROVIDER_RUNNER_LIB_NAME="$(agent_provider_runner_lib_name copilot)"
PROVIDER_RUNNER_LIB_PATH="$(agent_provider_runner_lib_path "${ROOT_DIR}/scripts/lib" copilot)"

if [[ -z "${HOST_COPILOT_BIN}" ]]; then
  echo "Missing host GitHub Copilot CLI binary." >&2
  echo "Install Copilot on the host first or set HOST_COPILOT_BIN." >&2
  exit 1
fi
HOST_COPILOT_BIN="$(readlink -f "${HOST_COPILOT_BIN}")"
if [[ ! -x "${HOST_COPILOT_BIN}" ]]; then
  echo "Host GitHub Copilot CLI binary is not executable: ${HOST_COPILOT_BIN}" >&2
  exit 1
fi

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_KEY_PATH}" >&2
  echo "Set SSH_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

if [[ ! -f "${PROVIDER_RUNNER_LIB_PATH}" ]]; then
  echo "Missing Copilot runner adapter: ${PROVIDER_RUNNER_LIB_PATH}" >&2
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

echo "Bootstrapping GitHub Copilot CLI into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  sudo mkdir -p /home/${VM_USER}
  sudo chown -R ${VM_USER}:${VM_USER} /home/${VM_USER}
  if [ -f /etc/agent-sandbox/session.env ]; then
    sudo chmod 755 /etc/agent-sandbox || true
    sudo chmod 644 /etc/agent-sandbox/session.env
  fi
  mkdir -p ~/bin ~/.copilot ~/.config/systemd/user ~/.config/agent-sandbox
"
scp "${SSH_OPTS[@]}" "${HOST_COPILOT_BIN}" "${VM_USER}@${VM_IP}:~/bin/copilot"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" \
  "VM_USER='${VM_USER}' bash -s" <<'EOF'
set -euo pipefail
chmod +x ~/bin/copilot
mkdir -p ~/.copilot
if [ -f ~/.agent/config.env ]; then
  # shellcheck disable=SC1091
  . ~/.agent/config.env
fi
if [ -f /etc/agent-sandbox/session.env ]; then
  # shellcheck disable=SC1091
  . /etc/agent-sandbox/session.env
fi
COPILOT_GATEWAY_URL="${AGENT_SANDBOX_GATEWAY_URL:-http://192.168.250.1:8000/mcp}"
if [ -n "${AGENT_SANDBOX_SESSION_ID:-}" ]; then
  COPILOT_GATEWAY_URL="${COPILOT_GATEWAY_URL}?session_id=${AGENT_SANDBOX_SESSION_ID}"
fi
COPILOT_GATEWAY_BASE_URL="$(python3 -c 'import os; from urllib.parse import urlsplit, urlunsplit; raw = os.environ.get("AGENT_SANDBOX_GATEWAY_URL", "http://192.168.250.1:8000/mcp"); parts = urlsplit(raw); print(urlunsplit((parts.scheme, parts.netloc, "", "", "")))')"
COPILOT_HOST_AUTH_URL="${COPILOT_GATEWAY_BASE_URL}/copilot-auth/token"
cat > ~/.copilot/config.json <<CONFIG
{
  "alt_screen": false
}
CONFIG
cat > ~/.copilot/mcp-config.json <<CONFIG
{
  "mcpServers": {
    "agent_sandbox": {
      "type": "http",
      "url": "${COPILOT_GATEWAY_URL}",
      "tools": ["*"]
    }
  }
}
CONFIG
cat > ~/bin/gh <<SHIM
#!/usr/bin/env bash
set -euo pipefail

copilot_host_auth_token() {
  python3 - "${COPILOT_HOST_AUTH_URL}" "\${AGENT_SANDBOX_SESSION_ID:-}" <<'PY'
import sys
import urllib.error
import urllib.request

url = sys.argv[1]
session_id = sys.argv[2].strip()
if not session_id:
    print("AGENT_SANDBOX_SESSION_ID is not set for host-mediated Copilot auth.", file=sys.stderr)
    raise SystemExit(1)
request = urllib.request.Request(
    url,
    headers={
        "Accept": "text/plain",
        "X-Agent-Sandbox-Session-Id": session_id,
    },
)
try:
    with urllib.request.urlopen(request, timeout=30) as response:
        sys.stdout.write(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    body = exc.read().decode("utf-8", errors="replace").strip()
    if body:
        print(body, file=sys.stderr)
    else:
        print(f"Host-mediated Copilot auth failed with HTTP {exc.code}.", file=sys.stderr)
    raise SystemExit(1)
PY
}

if [[ "\${1:-}" == "auth" && "\${2:-}" == "token" ]]; then
  copilot_host_auth_token
  exit 0
fi

if [[ "\${1:-}" == "auth" && "\${2:-}" == "status" ]]; then
  if copilot_host_auth_token >/dev/null; then
    printf 'github.com\\n'
    printf '  ✓ Logged in to github.com via agent-sandbox host auth\\n'
    exit 0
  fi
  exit 1
fi

if [[ -x /usr/bin/gh ]]; then
  exec /usr/bin/gh "\$@"
fi

echo "This guest gh shim only supports 'gh auth token' and 'gh auth status' for Copilot auth fallback." >&2
exit 1
SHIM
chmod +x ~/bin/gh

append_shell_setup() {
  local target="$1"
  if ! grep -q 'export PATH="$HOME/bin:$PATH"' "${target}" 2>/dev/null; then
    printf '\nexport PATH="$HOME/bin:$PATH"\n' >> "${target}"
  fi
  if ! grep -q 'export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"' "${target}" 2>/dev/null; then
    printf 'export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"\n' >> "${target}"
  fi
}

append_shell_setup ~/.profile
append_shell_setup ~/.bashrc

export PATH="$HOME/bin:$PATH"
export COPILOT_HOME="${COPILOT_HOME:-$HOME/.copilot}"
copilot version

echo "Host-mediated Copilot auth is available through the guest gh shim and the host gateway."
echo "COPILOT_GITHUB_TOKEN, GH_TOKEN, GITHUB_TOKEN, or 'copilot login' still override the gh fallback if you set them intentionally."
echo "Host heartbeat worker is now responsible for feedback wakeups by default."
EOF

GUEST_HEARTBEAT_PROMPT_B64="$(printf '%s' "${GUEST_HEARTBEAT_PROMPT}" | base64 -w0)"

if [[ "${ENABLE_GUEST_HEARTBEAT}" == "1" ]]; then
  echo "ENABLE_GUEST_HEARTBEAT=1 set; installing guest heartbeat service."
  if [[ -f "${LEGACY_HEARTBEAT_DIR}/heartbeat_cycle.sh" \
    && -f "${LEGACY_HEARTBEAT_DIR}/agent-heartbeat.service" \
    && -f "${LEGACY_HEARTBEAT_DIR}/agent-heartbeat.timer" \
    && -f "${ROOT_DIR}/scripts/lib/agent_runner.sh" \
    && -f "${PROVIDER_RUNNER_LIB_PATH}" ]]; then
    scp "${SSH_OPTS[@]}" \
      "${LEGACY_HEARTBEAT_DIR}/heartbeat_cycle.sh" \
      "${LEGACY_HEARTBEAT_DIR}/agent-heartbeat.service" \
      "${LEGACY_HEARTBEAT_DIR}/agent-heartbeat.timer" \
      "${ROOT_DIR}/scripts/lib/agent_runner.sh" \
      "${PROVIDER_RUNNER_LIB_PATH}" \
      "${VM_USER}@${VM_IP}:~/.config/agent-sandbox/"

    ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "GUEST_HEARTBEAT_PROMPT_B64='${GUEST_HEARTBEAT_PROMPT_B64}' PROVIDER_RUNNER_LIB_NAME='${PROVIDER_RUNNER_LIB_NAME}' bash -s" <<'EOF'
      set -euo pipefail
      mkdir -p ~/.config/systemd/user ~/.config/agent-sandbox/agent-runner-lib
      mv ~/.config/agent-sandbox/heartbeat_cycle.sh ~/.config/systemd/user/heartbeat_cycle.sh
      mv ~/.config/agent-sandbox/agent-heartbeat.service ~/.config/systemd/user/agent-heartbeat.service
      mv ~/.config/agent-sandbox/agent-heartbeat.timer ~/.config/systemd/user/agent-heartbeat.timer
      mv ~/.config/agent-sandbox/agent_runner.sh ~/.config/agent-sandbox/agent-runner-lib/agent_runner.sh
      mv ~/.config/agent-sandbox/"${PROVIDER_RUNNER_LIB_NAME}" ~/.config/agent-sandbox/agent-runner-lib/"${PROVIDER_RUNNER_LIB_NAME}"
      chmod +x ~/.config/systemd/user/heartbeat_cycle.sh
      cat > ~/.config/agent-sandbox/guest-heartbeat.env <<HEARTBEAT_ENV
AGENT_SANDBOX_GUEST_HEARTBEAT_PROMPT=$(python3 -c 'import base64, os, shlex; print(shlex.quote(base64.b64decode(os.environ["GUEST_HEARTBEAT_PROMPT_B64"]).decode("utf-8")))')
HEARTBEAT_ENV
      systemctl --user daemon-reload
      systemctl --user enable --now agent-heartbeat.timer
EOF
  else
    echo "Warning: guest heartbeat assets are not present on host; skipping guest timer setup." >&2
  fi
else
  ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" '
    set -euo pipefail
    systemctl --user disable --now agent-heartbeat.timer >/dev/null 2>&1 || true
    systemctl --user daemon-reload || true
  '
fi

echo "GitHub Copilot CLI bootstrap complete for ${VM_NAME} (${VM_IP})"
