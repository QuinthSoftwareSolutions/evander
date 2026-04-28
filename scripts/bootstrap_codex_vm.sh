#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_codex_vm.sh [--skill NAME ...] <vm-name> [vm-user]

Copy the host Codex binary into a guest VM and configure it to use the host
OpenAI relay.

Arguments:
  --skill    Codex skill directory name to copy from the host skill root into
             the guest CODEX_HOME skills directory. Repeat for multiple skills.
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH    Private key matching the guest authorized key.
  HOST_CODEX_ROOT Host Codex package root.
  HOST_CODEX_SKILLS_DIR Host Codex skills root. Default: ~/.codex/skills for the caller.
  HOST_REPO_CODEX_SKILLS_DIR Repo-local Codex skills root. Default: <repo>/skills.
  HOST_CODEX_MODELS_CACHE_PATH Host Codex model cache. Default: ~/.codex/models_cache.json for the caller.
  LLM_BASE_URL    Guest OpenAI-compatible base URL. Overrides ~/.agent/config.env when set.
  AGENT_MODEL     Model persisted for guest heartbeat resume runs when enabled.
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

CODEX_SKILLS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skill)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      CODEX_SKILLS+=("$2")
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
HOST_CODEX_SKILLS_DIR="${HOST_CODEX_SKILLS_DIR:-${CALLER_HOME}/.codex/skills}"
HOST_REPO_CODEX_SKILLS_DIR="${HOST_REPO_CODEX_SKILLS_DIR:-${ROOT_DIR}/skills}"
HOST_CODEX_MODELS_CACHE_PATH="${HOST_CODEX_MODELS_CACHE_PATH:-${CALLER_HOME}/.codex/models_cache.json}"
VENDOR_DIR="${HOST_CODEX_ROOT}/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl"
LLM_BASE_URL="${LLM_BASE_URL:-}"
AGENT_MODEL="${AGENT_MODEL:-}"
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

normalize_skill_name() {
  local name="${1:-}"
  if [[ ! "${name}" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "Invalid Codex skill name: ${name}" >&2
    return 1
  fi
  printf '%s\n' "${name}"
}

resolve_skill_path() {
  local skill_name="$1"
  local repo_local_path="${HOST_REPO_CODEX_SKILLS_DIR}/${skill_name}"
  local user_skill_path="${HOST_CODEX_SKILLS_DIR}/${skill_name}"

  if [[ -d "${repo_local_path}" ]]; then
    printf '%s\n' "${repo_local_path}"
    return 0
  fi
  if [[ -d "${user_skill_path}" ]]; then
    printf '%s\n' "${user_skill_path}"
    return 0
  fi

  echo "Missing host Codex skill directory: ${repo_local_path} or ${user_skill_path}" >&2
  echo "Set HOST_REPO_CODEX_SKILLS_DIR or HOST_CODEX_SKILLS_DIR if your skills live elsewhere." >&2
  return 1
}

for raw_skill in "${CODEX_SKILLS[@]}"; do
  skill_name="$(normalize_skill_name "${raw_skill}")"
  skill_path="$(resolve_skill_path "${skill_name}")"
  if [[ ! -f "${skill_path}/SKILL.md" ]]; then
    echo "Invalid Codex skill '${skill_name}': missing ${skill_path}/SKILL.md" >&2
    exit 1
  fi
done

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
  mkdir -p ~/bin ~/codex-vendor ~/.config/systemd/user ~/.config/agent-sandbox ~/.agent-sandbox
  chmod 700 ~/.agent-sandbox
"
scp "${SSH_OPTS[@]}" -r "${VENDOR_DIR}/." "${VM_USER}@${VM_IP}:~/codex-vendor/"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" \
  "VM_USER='${VM_USER}' LLM_BASE_URL='${LLM_BASE_URL}' bash -s" <<'EOF'
set -euo pipefail
cp ~/codex-vendor/codex/codex ~/bin/codex
chmod +x ~/bin/codex
export CODEX_HOME="${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"
mkdir -p ~/.codex "${CODEX_HOME}"
if [ -f ~/.agent/config.env ]; then
  # shellcheck disable=SC1091
  . ~/.agent/config.env
fi
if [ -f /etc/agent-sandbox/session.env ]; then
  # shellcheck disable=SC1091
  . /etc/agent-sandbox/session.env
fi
CODEX_LLM_BASE_URL="${LLM_BASE_URL:-${AGENT_SANDBOX_LLM_BASE_URL:-http://192.168.250.1:11434/v1}}"
CODEX_GATEWAY_URL="${AGENT_SANDBOX_GATEWAY_URL:-http://192.168.250.1:8000/mcp}"
if [ -n "${AGENT_SANDBOX_SESSION_ID:-}" ]; then
  CODEX_LLM_BASE_URL="${CODEX_LLM_BASE_URL%/}/session/${AGENT_SANDBOX_SESSION_ID}"
  CODEX_GATEWAY_URL="${CODEX_GATEWAY_URL}?session_id=${AGENT_SANDBOX_SESSION_ID}"
fi
for CODEX_CONFIG_PATH in "${CODEX_HOME}/config.toml" "${HOME}/.codex/config.toml"; do
cat > "${CODEX_CONFIG_PATH}" <<CONFIG
model = "gpt-5.4"
model_provider = "openai"
model_reasoning_effort = "high"
personality = "pragmatic"
openai_base_url = "${CODEX_LLM_BASE_URL}"

[mcp_servers.agent_sandbox]
url = "${CODEX_GATEWAY_URL}"
CONFIG
cat >> "${CODEX_CONFIG_PATH}" <<CONFIG
enabled = true

[projects."/home/${VM_USER}"]
trust_level = "trusted"
CONFIG
done

append_shell_setup() {
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

filtered = [
    line
    for line in lines
    if line.strip() not in {
        'export PATH="$HOME/bin:$PATH"',
        'export CODEX_HOME="${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"',
    }
]
filtered.append('export PATH="$HOME/bin:$PATH"')
filtered.append('export CODEX_HOME="${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"')
target.write_text("\n".join(filtered) + "\n", encoding="utf-8")
PY
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

mkdir -p "${CODEX_HOME}/skills"
export PATH="$HOME/bin:$HOME/codex-vendor/path:$PATH"
export CODEX_HOME
export OPENAI_API_KEY="${OPENAI_API_KEY:-relay-placeholder}"
codex --version

echo "Host-side wake workers are now responsible for feedback wakeups by default."
EOF

if [[ -f "${HOST_CODEX_MODELS_CACHE_PATH}" ]]; then
  scp "${SSH_OPTS[@]}" "${HOST_CODEX_MODELS_CACHE_PATH}" "${VM_USER}@${VM_IP}:~/codex-models-cache.json"
  ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" <<'EOF'
set -euo pipefail
export CODEX_HOME="${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"
mkdir -p "${CODEX_HOME}" "${HOME}/.codex"
cp ~/codex-models-cache.json "${CODEX_HOME}/models_cache.json"
cp ~/codex-models-cache.json "${HOME}/.codex/models_cache.json"
rm -f ~/codex-models-cache.json
EOF
  echo "Copied host Codex model cache into guest."
else
  echo "Host Codex model cache not found at ${HOST_CODEX_MODELS_CACHE_PATH}; skipping model cache copy." >&2
fi

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

    ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "GUEST_HEARTBEAT_PROMPT_B64='${GUEST_HEARTBEAT_PROMPT_B64}' AGENT_MODEL='${AGENT_MODEL}' bash -s" <<'EOF'
      set -euo pipefail
      mkdir -p ~/.config/systemd/user ~/.config/agent-sandbox/agent-runner-lib
      mv ~/.config/agent-sandbox/heartbeat_cycle.sh ~/.config/systemd/user/heartbeat_cycle.sh
      mv ~/.config/agent-sandbox/agent-heartbeat.service ~/.config/systemd/user/agent-heartbeat.service
      mv ~/.config/agent-sandbox/agent-heartbeat.timer ~/.config/systemd/user/agent-heartbeat.timer
      mv ~/.config/agent-sandbox/agent_runner.sh ~/.config/agent-sandbox/agent-runner-lib/agent_runner.sh
      mv ~/.config/agent-sandbox/agent_runner_codex.sh ~/.config/agent-sandbox/agent-runner-lib/agent_runner_codex.sh
      chmod +x ~/.config/systemd/user/heartbeat_cycle.sh
      cat > ~/.config/agent-sandbox/guest-heartbeat.env <<HEARTBEAT_ENV
AGENT_MODEL=$(python3 -c 'import os, shlex; print(shlex.quote(os.environ.get("AGENT_MODEL", "")))')
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

if (( ${#CODEX_SKILLS[@]} > 0 )); then
  ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
    set -euo pipefail
    export CODEX_HOME=\"\${CODEX_HOME:-\$HOME/.config/agent-sandbox/codex}\"
    mkdir -p \"\${CODEX_HOME}/skills\" \"\${CODEX_HOME}/skill-bootstrap\"
  "
  for raw_skill in "${CODEX_SKILLS[@]}"; do
    skill_name="$(normalize_skill_name "${raw_skill}")"
    skill_path="$(resolve_skill_path "${skill_name}")"
    remote_skill_bootstrap_dir="$(
      ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" '
        set -euo pipefail
        export CODEX_HOME="${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"
        printf "%s\n" "${CODEX_HOME}/skill-bootstrap"
      '
    )"
    scp "${SSH_OPTS[@]}" -r "${skill_path}" "${VM_USER}@${VM_IP}:${remote_skill_bootstrap_dir}/"
    ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "SKILL_NAME='${skill_name}' bash -s" <<'EOF'
set -euo pipefail
export CODEX_HOME="${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"
mkdir -p "${CODEX_HOME}/skills" "${CODEX_HOME}/skill-bootstrap"
rm -rf "${CODEX_HOME}/skills/${SKILL_NAME}"
mv "${CODEX_HOME}/skill-bootstrap/${SKILL_NAME}" "${CODEX_HOME}/skills/${SKILL_NAME}"
EOF
  done
  echo "Installed Codex skills into guest: ${CODEX_SKILLS[*]}"
fi

echo "Codex bootstrap complete for ${VM_NAME} (${VM_IP})"
