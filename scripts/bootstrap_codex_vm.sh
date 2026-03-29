#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_codex_vm.sh <vm-name> [vm-user]

Copy the host Codex binary into a guest VM and configure it to use the host
OpenAI relay.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH    Private key matching the guest authorized key.
  HOST_CODEX_ROOT Host Codex package root.
  LLM_BASE_URL    Guest OpenAI-compatible base URL. Overrides ~/.agent/config.env when set.
  ENABLE_GUEST_HEARTBEAT Install and enable the guest heartbeat timer (0/1, default: 0).
  GUEST_HEARTBEAT_PROMPT Prompt passed to `codex exec resume --last` from the guest timer.
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
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

HOST_CODEX_ROOT="${HOST_CODEX_ROOT:-${CALLER_HOME}/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex}"
VENDOR_DIR="${HOST_CODEX_ROOT}/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl"
LLM_BASE_URL="${LLM_BASE_URL:-}"
ENABLE_GUEST_HEARTBEAT="${ENABLE_GUEST_HEARTBEAT:-0}"
GUEST_HEARTBEAT_PROMPT="${GUEST_HEARTBEAT_PROMPT:-Resume work for this session. Check for pending feedback first, then continue the most recent task.}"
SSH_OPTS=(
  -i "${SSH_KEY_PATH}"
)
LEGACY_HEARTBEAT_DIR="${ROOT_DIR}/scripts/guest"
if [[ ! -d "${VENDOR_DIR}" ]]; then
  echo "Missing Codex vendor directory: ${VENDOR_DIR}" >&2
  echo "Set HOST_CODEX_ROOT if Codex is installed elsewhere on the host." >&2
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

echo "Bootstrapping Codex into ${VM_USER}@${VM_IP}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  sudo mkdir -p /home/${VM_USER}
  sudo chown -R ${VM_USER}:${VM_USER} /home/${VM_USER}
  if [ -f /etc/agent-sandbox/session.env ]; then
    sudo chmod 755 /etc/agent-sandbox || true
    sudo chmod 644 /etc/agent-sandbox/session.env
  fi
  mkdir -p ~/bin ~/codex-vendor ~/.config/systemd/user ~/.config/agent-sandbox
"
scp "${SSH_OPTS[@]}" -r "${VENDOR_DIR}/." "${VM_USER}@${VM_IP}:~/codex-vendor/"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" \
  "VM_USER='${VM_USER}' LLM_BASE_URL='${LLM_BASE_URL}' bash -s" <<'EOF'
set -euo pipefail
cp ~/codex-vendor/codex/codex ~/bin/codex
chmod +x ~/bin/codex
mkdir -p ~/.codex
if [ -f ~/.agent/config.env ]; then
  # shellcheck disable=SC1091
  . ~/.agent/config.env
fi
CODEX_LLM_BASE_URL="${LLM_BASE_URL:-${AGENT_SANDBOX_LLM_BASE_URL:-http://192.168.250.1:11434/v1}}"
CODEX_GATEWAY_URL="${AGENT_SANDBOX_GATEWAY_URL:-http://192.168.250.1:8000/mcp}"
cat > ~/.codex/config.toml <<CONFIG
model = "gpt-5.4"
model_provider = "openai"
model_reasoning_effort = "high"
personality = "pragmatic"
openai_base_url = "${CODEX_LLM_BASE_URL}"

[mcp_servers.agent_sandbox]
url = "${CODEX_GATEWAY_URL}"
CONFIG
cat >> ~/.codex/config.toml <<CONFIG
enabled = true

[projects."/home/${VM_USER}"]
trust_level = "trusted"
CONFIG

append_shell_setup() {
  local target="$1"
  if ! grep -q 'export PATH="$HOME/bin:$PATH"' "${target}" 2>/dev/null; then
    printf '\nexport PATH="$HOME/bin:$PATH"\n' >> "${target}"
  fi
  if ! grep -q 'relay-placeholder' "${target}" 2>/dev/null; then
    cat >> "${target}" <<PROFILE
if [ -f /etc/agent-sandbox/session.env ]; then
  set -a
  . /etc/agent-sandbox/session.env
  set +a
fi
export OPENAI_API_KEY="\${OPENAI_API_KEY:-relay-placeholder}"
PROFILE
  fi
}

append_shell_setup ~/.profile
append_shell_setup ~/.bashrc

export PATH="$HOME/bin:$HOME/codex-vendor/path:$PATH"
export OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"
codex --version

echo "Host heartbeat worker is now responsible for feedback wakeups by default."
EOF

GUEST_HEARTBEAT_PROMPT_B64="$(printf '%s' "${GUEST_HEARTBEAT_PROMPT}" | base64 -w0)"

if [[ "${ENABLE_GUEST_HEARTBEAT}" == "1" ]]; then
  echo "ENABLE_GUEST_HEARTBEAT=1 set; installing guest heartbeat service."
  if [[ -f "${LEGACY_HEARTBEAT_DIR}/heartbeat_cycle.sh" \
    && -f "${LEGACY_HEARTBEAT_DIR}/agent-heartbeat.service" \
    && -f "${LEGACY_HEARTBEAT_DIR}/agent-heartbeat.timer" \
    && -f "${ROOT_DIR}/scripts/lib/agent_runner.sh" \
    && -f "${ROOT_DIR}/scripts/lib/agent_runner_codex.sh" ]]; then
    scp "${SSH_OPTS[@]}" \
      "${LEGACY_HEARTBEAT_DIR}/heartbeat_cycle.sh" \
      "${LEGACY_HEARTBEAT_DIR}/agent-heartbeat.service" \
      "${LEGACY_HEARTBEAT_DIR}/agent-heartbeat.timer" \
      "${ROOT_DIR}/scripts/lib/agent_runner.sh" \
      "${ROOT_DIR}/scripts/lib/agent_runner_codex.sh" \
      "${VM_USER}@${VM_IP}:~/.config/agent-sandbox/"

    ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "GUEST_HEARTBEAT_PROMPT_B64='${GUEST_HEARTBEAT_PROMPT_B64}' bash -s" <<'EOF'
      set -euo pipefail
      mkdir -p ~/.config/systemd/user ~/.config/agent-sandbox/agent-runner-lib
      mv ~/.config/agent-sandbox/heartbeat_cycle.sh ~/.config/systemd/user/heartbeat_cycle.sh
      mv ~/.config/agent-sandbox/agent-heartbeat.service ~/.config/systemd/user/agent-heartbeat.service
      mv ~/.config/agent-sandbox/agent-heartbeat.timer ~/.config/systemd/user/agent-heartbeat.timer
      mv ~/.config/agent-sandbox/agent_runner.sh ~/.config/agent-sandbox/agent-runner-lib/agent_runner.sh
      mv ~/.config/agent-sandbox/agent_runner_codex.sh ~/.config/agent-sandbox/agent-runner-lib/agent_runner_codex.sh
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

echo "Codex bootstrap complete for ${VM_NAME} (${VM_IP})"
