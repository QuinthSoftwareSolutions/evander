#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: setup_issue_vm.sh [--dry-run] [mirror-options] <issue-number|issue-yaml> <project-or-solution> [repo-source] [session-id] [vm-user]

Run the common issue-VM bring-up flow in one command:
  1. prewarm the shared NuGet cache on the host
  2. launch the issue VM
  3. bootstrap Codex, .NET, and NuGet in the guest
  4. configure passwordless Git access and create the issue mirror
  5. clone the mirrored repo into the guest user's home directory
  6. write a task file into the repo root for long-running agent sessions
  7. start a Codex issue-resolution run inside the guest by default

Arguments:
  issue-number|issue-yaml
                       Issue identifier used to derive vm and repo names, or a YAML
                       file containing the issue metadata. In YAML mode, the script
                       reads the issue number from the file and copies the YAML into
                       the guest repo root as AGENT_TASK.yaml.
  project-or-solution  Host path used to prewarm the shared NuGet cache.
  repo-source          Host repo path or remote URL to mirror for guest Git access.
                       Default: /home/jonathan/Documents/Repos/ExampleIndustryAPP
  session-id           Session name passed to launch_agent_vm.sh. Default: agent-session
  vm-user              Guest user to configure. Default: agent

Environment:
  CODEX_MODEL                 Codex model to run inside the guest. Default: gpt-5.4
  VM_WAIT_IP_TIMEOUT_SECONDS   How long to wait for the guest to receive an IPv4 address.
                               Default: 180
  VM_WAIT_SSH_TIMEOUT_SECONDS  How long to wait for guest SSH to accept connections.
                               Default: 180

Mirror options (forwarded to setup_issue_git_vm.sh and mirror_issue_repo.sh):
  --sync-remote
  --no-sync-remote
  --remote-name NAME
  --refspec SPEC
  --branch NAME
  --force-local
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

START_AGENT=1
MIRROR_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      START_AGENT=0
      shift
      ;;
    --sync-remote|--no-sync-remote|--force-local)
      MIRROR_ARGS+=("$1")
      shift
      ;;
    --remote-name|--refspec|--branch)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      MIRROR_ARGS+=("$1" "$2")
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

if [[ $# -lt 2 ]]; then
  show_help >&2
  exit 1
fi

ISSUE_INPUT="$1"
PREWARM_TARGET="$2"
REPO_SOURCE="${3:-/home/jonathan/Documents/Repos/ExampleIndustryAPP}"
SESSION_ID="${4:-agent-session}"
VM_USER="${5:-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE:-${CALLER_HOME}/.ssh/id_ed25519.pub}"
VM_WAIT_IP_TIMEOUT_SECONDS="${VM_WAIT_IP_TIMEOUT_SECONDS:-180}"
VM_WAIT_SSH_TIMEOUT_SECONDS="${VM_WAIT_SSH_TIMEOUT_SECONDS:-180}"
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
ISSUE_FILE=""
CODEX_MODEL="${CODEX_MODEL:-gpt-5.4}"

read_issue_number_from_yaml() {
  python3 - "$1" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
patterns = [
    r"(?m)^\s*issue_number\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
    r"(?m)^\s*number\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
    r"(?m)^\s*id\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
]
for pattern in patterns:
    match = re.search(pattern, text)
    if match:
        print(match.group(1))
        sys.exit(0)
sys.exit(1)
PY
}

if [[ -f "${ISSUE_INPUT}" ]]; then
  ISSUE_FILE="$(readlink -f "${ISSUE_INPUT}")"
  if ! ISSUE_NUMBER="$(read_issue_number_from_yaml "${ISSUE_FILE}")"; then
    echo "Could not read issue number from YAML file: ${ISSUE_FILE}" >&2
    echo "Expected one of: issue_number:, number:, or id:" >&2
    exit 1
  fi
else
  ISSUE_NUMBER="${ISSUE_INPUT}"
fi

VM_NAME="vm-${ISSUE_NUMBER}"

if [[ ! -f "${SSH_PUBLIC_KEY_FILE}" ]]; then
  SSH_PUBLIC_KEY_FILE="${CALLER_HOME}/.ssh/id_rsa.pub"
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  SSH_PRIVATE_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

if [[ ! -e "${PREWARM_TARGET}" ]]; then
  echo "Missing project or solution path: ${PREWARM_TARGET}" >&2
  exit 1
fi

eval "$("${SCRIPT_DIR}/resolve_repo_source.sh" --print-env "${REPO_SOURCE}")"

GUEST_REPO_NAME="${GUEST_REPO_NAME_OVERRIDE:-${RESOLVED_REPO_NAME}}"

if [[ ! -f "${SSH_PUBLIC_KEY_FILE}" ]]; then
  echo "Missing SSH public key: ${SSH_PUBLIC_KEY_FILE}" >&2
  echo "Set SSH_PUBLIC_KEY_FILE to a readable public key before running this helper." >&2
  exit 1
fi

if [[ ! -f "${SSH_PRIVATE_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_PRIVATE_KEY_PATH}" >&2
  echo "Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

if virsh dominfo "${VM_NAME}" >/dev/null 2>&1 || sudo -n virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
  echo "VM already exists: ${VM_NAME}" >&2
  echo "Destroy it first with: sudo ./scripts/destroy_agent_vm.sh ${VM_NAME}" >&2
  exit 1
fi

"${SCRIPT_DIR}/prewarm_nuget_cache.sh" "${PREWARM_TARGET}"
sudo SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE}" "${SCRIPT_DIR}/launch_agent_vm.sh" "${VM_NAME}" "${SESSION_ID}"
VM_IP="$(vm_network_wait_for_ip "${VM_NAME}" "${VM_WAIT_IP_TIMEOUT_SECONDS}")"
echo "Guest IPv4 detected: ${VM_IP}"
vm_network_wait_for_ssh "${VM_NAME}" "${VM_IP}" "${VM_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_WAIT_SSH_TIMEOUT_SECONDS}"
echo "Guest SSH is ready on ${VM_USER}@${VM_IP}"
"${SCRIPT_DIR}/bootstrap_codex_vm.sh" "${VM_NAME}" "${VM_USER}"
"${SCRIPT_DIR}/bootstrap_dotnet_vm.sh" "${VM_NAME}" "${VM_USER}"
"${SCRIPT_DIR}/bootstrap_nuget_vm.sh" "${VM_NAME}" "${VM_USER}"
"${SCRIPT_DIR}/setup_issue_git_vm.sh" "${MIRROR_ARGS[@]}" "${VM_NAME}" "${ISSUE_NUMBER}" "${REPO_SOURCE}" "${VM_USER}"

ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -i "${SSH_PRIVATE_KEY_PATH}" \
  "${VM_USER}@${VM_IP}" \
  "GUEST_REPO_NAME='${GUEST_REPO_NAME}' ISSUE_NUMBER='${ISSUE_NUMBER}' SESSION_ID='${SESSION_ID}' bash -s" <<'EOF'
set -euo pipefail
. ~/.profile

TARGET_DIR="$HOME/${GUEST_REPO_NAME}"
REMOTE_URL="ssh://agent-sandbox-git-host/home/jonathan/agent-sandbox/git-remotes/${ISSUE_NUMBER}_repo.git"

if [[ -e "${TARGET_DIR}" ]]; then
  echo "Guest repo path already exists: ${TARGET_DIR}" >&2
  exit 1
fi

git clone "${REMOTE_URL}" "${TARGET_DIR}"
git -C "${TARGET_DIR}" config alias.push-agent '!f(){ git push agent HEAD:refs/heads/agents/${SESSION_ID:-manual}/$(git rev-parse --abbrev-ref HEAD); }; f'

cat > "$HOME/AGENTS.md" <<AGENTS
# Guest Notes

- Primary repo path: ${TARGET_DIR}
- Task file in repo root: ${TARGET_DIR}/AGENT_TASK.yaml
- Guest Git remote: ${REMOTE_URL}
- Recommended push command from inside the repo:
  \`git push-agent\`
- Equivalent explicit push pattern:
  \`git push agent HEAD:refs/heads/agents/\${SESSION_ID:-manual}/\$(git rev-parse --abbrev-ref HEAD)\`
- For restore, always use:
  \`dotnet restore --configfile ~/.nuget/NuGet/NuGet.Config\`
- Do not rely on a repo-local \`NuGet.Config\` for restore inside the guest.
- For build and test, prefer:
  \`dotnet build --no-restore\`
  \`dotnet test --no-restore\`
- The reason for \`--no-restore\` is to avoid accidentally triggering a slow restore with the wrong NuGet config.
- If shell changes were just bootstrapped, run:
  \`. ~/.profile\`

## PR Summary Requirement

Agents must provide a markdown summary of changes and attach it to the tip commit
with Git notes.

- Required notes ref: \`agent-pr\`
- The markdown summary must be attached via:
  \`git notes --ref=agent-pr add -f -m "<markdown summary>"\`
- The notes ref must be pushed together with the branch push.

Copy/paste examples (replace placeholders):

\`\`\`bash
# Add or replace the markdown summary note on the current tip commit.
git notes --ref=agent-pr add -f -m "<markdown summary in Markdown>"

# Push the current branch and the notes ref together.
git push agent \
  HEAD:refs/heads/agents/\${SESSION_ID:-manual}/\$(git rev-parse --abbrev-ref HEAD) \
  refs/notes/agent-pr
\`\`\`

### Troubleshooting

- Note added but not pushed:
  run the push command above and make sure \`refs/notes/agent-pr\` is included.
- Wrong ref name:
  use exactly \`--ref=agent-pr\` (this maps to \`refs/notes/agent-pr\`).
- Note attached to the wrong commit:
  checkout the correct tip commit and re-run
  \`git notes --ref=agent-pr add -f -m "<markdown summary>"\`.
AGENTS
EOF

if [[ -n "${ISSUE_FILE}" ]]; then
  scp \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -i "${SSH_PRIVATE_KEY_PATH}" \
    "${ISSUE_FILE}" \
    "${VM_USER}@${VM_IP}:/home/${VM_USER}/${GUEST_REPO_NAME}/AGENT_TASK.yaml" >/dev/null
else
  ssh \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -i "${SSH_PRIVATE_KEY_PATH}" \
    "${VM_USER}@${VM_IP}" \
    "cat > /home/${VM_USER}/${GUEST_REPO_NAME}/AGENT_TASK.yaml" <<EOF
issue_number: ${ISSUE_NUMBER}
name: "Not provided"
description: |
  No issue metadata file was provided during VM setup.
EOF
fi

if (( START_AGENT )); then
  ssh \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -i "${SSH_PRIVATE_KEY_PATH}" \
    "${VM_USER}@${VM_IP}" \
    "GUEST_REPO_NAME='${GUEST_REPO_NAME}' CODEX_MODEL='${CODEX_MODEL}' bash -s" <<'EOF'
set -euo pipefail
. ~/.profile

TARGET_DIR="$HOME/${GUEST_REPO_NAME}"
LOG_DIR="$HOME/.agent-sandbox"
LOG_PATH="${LOG_DIR}/codex-${GUEST_REPO_NAME}.log"
PID_PATH="${LOG_DIR}/codex-${GUEST_REPO_NAME}.pid"

mkdir -p "${LOG_DIR}"

if [[ -f "${PID_PATH}" ]]; then
  EXISTING_PID="$(cat "${PID_PATH}")"
  if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" >/dev/null 2>&1; then
    echo "Codex already running in guest with PID ${EXISTING_PID}" >&2
    exit 1
  fi
fi

nohup codex exec \
  --model "${CODEX_MODEL}" \
  -s danger-full-access \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  -C "${TARGET_DIR}" \
  "You have been issued the task described in ${TARGET_DIR}/AGENT_TASK.yaml. Read ${HOME}/AGENTS.md and ${TARGET_DIR}/AGENT_TASK.yaml before you begin. The repository is located at ${TARGET_DIR}. Do not ask for feedback or clarifications, but work until the task is completed or until you determine that it is impossible to complete. When restore is needed, use 'dotnet restore --configfile ~/.nuget/NuGet/NuGet.Config'. For build and test, prefer '--no-restore'. When you have finished, commit and push your work using 'git push-agent'." \
  >"${LOG_PATH}" 2>&1 < /dev/null &

AGENT_PID=$!
echo "${AGENT_PID}" > "${PID_PATH}"
printf 'pid=%s\nlog=%s\n' "${AGENT_PID}" "${LOG_PATH}"
EOF
fi

echo
echo "Issue VM setup complete."
echo "  vm:      ${VM_NAME}"
echo "  session: ${SESSION_ID}"
echo "  repo:    ssh://agent-sandbox-git-host/home/jonathan/agent-sandbox/git-remotes/${ISSUE_NUMBER}_repo.git"
echo "  guest:   /home/${VM_USER}/${GUEST_REPO_NAME}"
echo "  task:    /home/${VM_USER}/${GUEST_REPO_NAME}/AGENT_TASK.yaml"
echo "  push:    git -C /home/${VM_USER}/${GUEST_REPO_NAME} push-agent"
if (( START_AGENT )); then
  echo "  agent:   started"
  echo "  log:     /home/${VM_USER}/.agent-sandbox/codex-${GUEST_REPO_NAME}.log"
  echo "  pid:     /home/${VM_USER}/.agent-sandbox/codex-${GUEST_REPO_NAME}.pid"
else
  echo "  agent:   not started (--dry-run)"
fi
