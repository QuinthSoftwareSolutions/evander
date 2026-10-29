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
  VM_SSH_USER     Privileged guest SSH user used to wait for cloud-init and
                  repair guest home ownership. Default: agent-admin
  GUEST_BOOTSTRAP_WAIT_SECONDS
                  How long to wait for guest bootstrap readiness. Default: 180
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
# shellcheck source=lib/guest_ripgrep.sh
source "${SCRIPT_DIR}/lib/guest_ripgrep.sh"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

HOST_CODEX_ROOT="${HOST_CODEX_ROOT:-${CALLER_HOME}/.nvm/versions/node/v24.22.1/lib/node_modules/@openai/codex}"
HOST_CODEX_SKILLS_DIR="${HOST_CODEX_SKILLS_DIR:-${CALLER_HOME}/.codex/skills}"
HOST_REPO_CODEX_SKILLS_DIR="${HOST_REPO_CODEX_SKILLS_DIR:-${ROOT_DIR}/skills}"
HOST_CODEX_MODELS_CACHE_PATH="${HOST_CODEX_MODELS_CACHE_PATH:-${CALLER_HOME}/.codex/models_cache.json}"
VENDOR_DIR="${HOST_CODEX_ROOT}/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl"
LLM_BASE_URL="${LLM_BASE_URL:-}"
SSH_USER="${VM_SSH_USER:-agent-admin}"
GUEST_BOOTSTRAP_WAIT_SECONDS="${GUEST_BOOTSTRAP_WAIT_SECONDS:-180}"
SSH_OPTS=(
  -i "${SSH_KEY_PATH}"
)
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
if ! [[ "${GUEST_BOOTSTRAP_WAIT_SECONDS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "GUEST_BOOTSTRAP_WAIT_SECONDS must be a positive integer: ${GUEST_BOOTSTRAP_WAIT_SECONDS}" >&2
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

VM_IP="$(get_vm_ip)"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_KEY_PATH}" "${CALLER_HOME}")

echo "Bootstrapping Codex into ${VM_USER}@${VM_IP}"
prepare_guest_home
guest_ripgrep_seed "${VM_USER}" "${VM_IP}" "${SSH_OPTS[@]}"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
  set -euo pipefail
  mkdir -p ~/bin ~/codex-vendor ~/.config/systemd/user ~/.config/evander ~/.evander
  chmod 700 ~/.evander
"
scp "${SSH_OPTS[@]}" -r "${VENDOR_DIR}/." "${VM_USER}@${VM_IP}:~/codex-vendor/"

ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" \
  "VM_USER='${VM_USER}' LLM_BASE_URL='${LLM_BASE_URL}' bash -s" <<'EOF'
set -euo pipefail
if [[ -x ~/codex-vendor/codex/codex ]]; then
  cp ~/codex-vendor/codex/codex ~/bin/codex
elif [[ -x ~/codex-vendor/bin/codex ]]; then
  cp ~/codex-vendor/bin/codex ~/bin/codex
else
  echo "Missing Codex binary under ~/codex-vendor/codex/codex or ~/codex-vendor/bin/codex" >&2
  exit 1
fi
chmod +x ~/bin/codex
export CODEX_HOME="${CODEX_HOME:-$HOME/.config/evander/codex}"
mkdir -p ~/.codex "${CODEX_HOME}"
if [ -f ~/.agent/config.env ]; then
  # shellcheck disable=SC1091
  . ~/.agent/config.env
fi
if [ -f /etc/evander/session.env ]; then
  # shellcheck disable=SC1091
  . /etc/evander/session.env
fi
append_evander_mcp_query() {
  python3 - "$1" "$2" "${3:-}" <<'PY'
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import sys

url, session_id, token = sys.argv[1], sys.argv[2], sys.argv[3]
parts = urlsplit(url)
query = dict(parse_qsl(parts.query, keep_blank_values=True))
query["session_id"] = session_id
if token:
    query["mcp_session_token"] = token
print(urlunsplit(parts._replace(query=urlencode(query))))
PY
}
CODEX_LLM_BASE_URL="${LLM_BASE_URL:-${EVANDER_LLM_BASE_URL:-http://192.168.250.1:11434/v1}}"
CODEX_GATEWAY_URL="${EVANDER_GATEWAY_URL:-http://192.168.250.1:8000/mcp}"
CODEX_SESSION_ID="${EVANDER_SESSION_ID:-}"
CODEX_MCP_SESSION_TOKEN="${EVANDER_MCP_SESSION_TOKEN:-}"
if [ -n "${CODEX_SESSION_ID:-}" ]; then
  CODEX_LLM_BASE_URL="${CODEX_LLM_BASE_URL%/}/session/${CODEX_SESSION_ID}"
  CODEX_GATEWAY_URL="$(append_evander_mcp_query "${CODEX_GATEWAY_URL}" "${CODEX_SESSION_ID}" "${CODEX_MCP_SESSION_TOKEN}")"
fi
for CODEX_CONFIG_PATH in "${CODEX_HOME}/config.toml" "${HOME}/.codex/config.toml"; do
cat > "${CODEX_CONFIG_PATH}" <<CONFIG
model = "gpt-6-sol"
model_provider = "evander_openai_relay"
model_reasoning_effort = "high"
service_tier = "default"
personality = "pragmatic"
web_search = "disabled"

[model_providers.evander_openai_relay]
name = "Evander OpenAI Relay"
base_url = "${CODEX_LLM_BASE_URL}"
env_key = "OPENAI_API_KEY"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false

[mcp_servers.evander]
url = "${CODEX_GATEWAY_URL}"
CONFIG
cat >> "${CODEX_CONFIG_PATH}" <<CONFIG
enabled = true
required = true

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

def repair_missing_session_env_fi(profile_lines):
    out = []
    changed = False
    in_session_env = False
    indent = ""
    for line in profile_lines:
        stripped = line.strip()
        if in_session_env and stripped == "fi":
            in_session_env = False
        elif in_session_env and line == line.lstrip() and stripped.startswith("export "):
            out.append(f"{indent}fi")
            changed = True
            in_session_env = False
        out.append(line)
        if stripped in {
            "if [ -f /etc/evander/session.env ]; then",
            'if [ -f "$HOME/.claude/settings.json" ]; then',
        }:
            in_session_env = True
            indent = line[: len(line) - len(line.lstrip())]
    return out if changed else profile_lines

lines = repair_missing_session_env_fi(lines)
filtered = [
    line
    for line in lines
    if line.strip() not in {
        'export PATH="$HOME/bin:$PATH"',
        'export CODEX_HOME="${CODEX_HOME:-$HOME/.config/evander/codex}"',
    }
    and not (
        line.strip().startswith("export OPENAI_API_KEY=")
        and "relay-placeholder" in line
    )
]
filtered.append('export PATH="$HOME/bin:$PATH"')
filtered.append('export CODEX_HOME="${CODEX_HOME:-$HOME/.config/evander/codex}"')
target.write_text("\n".join(filtered) + "\n", encoding="utf-8")
PY
  if ! grep -q 'export OPENAI_API_KEY=' "${target}" 2>/dev/null; then
    if ! grep -q '/etc/evander/session.env' "${target}" 2>/dev/null; then
      cat >> "${target}" <<'PROFILE'
if [ -f /etc/evander/session.env ]; then
  set -a
  . /etc/evander/session.env
  set +a
fi
PROFILE
    fi
    cat >> "${target}" <<PROFILE
export OPENAI_API_KEY="\${OPENAI_API_KEY:-\${EVANDER_MCP_SESSION_TOKEN:-relay-placeholder}}"
PROFILE
  fi
}

append_shell_setup ~/.profile
append_shell_setup ~/.bashrc

mkdir -p "${CODEX_HOME}/skills"
export PATH="$HOME/bin:$HOME/codex-vendor/path:$HOME/codex-vendor/codex-path:$PATH"
export CODEX_HOME
export OPENAI_API_KEY="${OPENAI_API_KEY:-${EVANDER_MCP_SESSION_TOKEN:-relay-placeholder}}"
codex --version

echo "Host-side wake workers are now responsible for feedback wakeups by default."
EOF

if [[ -f "${HOST_CODEX_MODELS_CACHE_PATH}" ]]; then
  scp "${SSH_OPTS[@]}" "${HOST_CODEX_MODELS_CACHE_PATH}" "${VM_USER}@${VM_IP}:~/codex-models-cache.json"
  ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" <<'EOF'
set -euo pipefail
export CODEX_HOME="${CODEX_HOME:-$HOME/.config/evander/codex}"
mkdir -p "${CODEX_HOME}" "${HOME}/.codex"
cp ~/codex-models-cache.json "${CODEX_HOME}/models_cache.json"
cp ~/codex-models-cache.json "${HOME}/.codex/models_cache.json"
rm -f ~/codex-models-cache.json
EOF
  echo "Copied host Codex model cache into guest."
else
  echo "Host Codex model cache not found at ${HOST_CODEX_MODELS_CACHE_PATH}; skipping model cache copy." >&2
fi

# Reconcile VMs that still have the retired guest timer installed.
ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" '
  set -euo pipefail
  systemctl --user disable --now agent-heartbeat.timer >/dev/null 2>&1 || true
  rm -f ~/.config/systemd/user/agent-heartbeat.service \
    ~/.config/systemd/user/agent-heartbeat.timer \
    ~/.config/systemd/user/heartbeat_cycle.sh ~/.config/evander/guest-heartbeat.env
  systemctl --user daemon-reload || true
'

if (( ${#CODEX_SKILLS[@]} > 0 )); then
  ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "
    set -euo pipefail
    export CODEX_HOME=\"\${CODEX_HOME:-\$HOME/.config/evander/codex}\"
    mkdir -p \"\${CODEX_HOME}/skills\" \"\${CODEX_HOME}/skill-bootstrap\"
  "
  for raw_skill in "${CODEX_SKILLS[@]}"; do
    skill_name="$(normalize_skill_name "${raw_skill}")"
    skill_path="$(resolve_skill_path "${skill_name}")"
    remote_skill_bootstrap_dir="$(
      ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" '
        set -euo pipefail
        export CODEX_HOME="${CODEX_HOME:-$HOME/.config/evander/codex}"
        printf "%s\n" "${CODEX_HOME}/skill-bootstrap"
      '
    )"
    scp "${SSH_OPTS[@]}" -r "${skill_path}" "${VM_USER}@${VM_IP}:${remote_skill_bootstrap_dir}/"
    ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" "SKILL_NAME='${skill_name}' bash -s" <<'EOF'
set -euo pipefail
export CODEX_HOME="${CODEX_HOME:-$HOME/.config/evander/codex}"
mkdir -p "${CODEX_HOME}/skills" "${CODEX_HOME}/skill-bootstrap"
rm -rf "${CODEX_HOME}/skills/${SKILL_NAME}"
mv "${CODEX_HOME}/skill-bootstrap/${SKILL_NAME}" "${CODEX_HOME}/skills/${SKILL_NAME}"
EOF
  done
  echo "Installed Codex skills into guest: ${CODEX_SKILLS[*]}"
fi

echo "Codex bootstrap complete for ${VM_NAME} (${VM_IP})"
