#!/usr/bin/env bash
set -Eeuo pipefail

CURRENT_STEP="startup"

log_step() {
  CURRENT_STEP="$1"
  echo "==> ${CURRENT_STEP}"
}

on_error() {
  local exit_code="$?"
  local line_no="${1:-?}"
  echo "ERROR: setup_issue_vm.sh failed at step '${CURRENT_STEP}' (line ${line_no})" >&2
  echo "COMMAND: ${BASH_COMMAND}" >&2
  exit "${exit_code}"
}

trap 'on_error ${LINENO}' ERR

show_help() {
  cat <<'EOF'
Usage: setup_issue_vm.sh [--dry-run] [--dotnet] [--nuget] [--mssql] [--playwright] [--browser-tests --browser-tests-host-checkout PATH --browser-tests-install-root PATH ...] [mirror-options] <issue-number|issue-yaml> [project-or-solution-if-nuget] [repo-source] [session-id] [vm-user]

Run the common issue-VM bring-up flow in one command:
  1. optionally prewarm the shared NuGet cache on the host
  2. launch the issue VM
  3. bootstrap Codex and selected optional guest toolchains
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
  project-or-solution-if-nuget
                       Host path used to prewarm the shared NuGet cache when --nuget
                       is requested. When --nuget is omitted, the next positional
                       argument is treated as repo-source instead.
  repo-source          Host repo path or remote URL to mirror for guest Git access.
                       Default: $AGENT_SANDBOX_DEFAULT_REPO_SOURCE or repo root
  session-id           Session name passed to launch_agent_vm.sh. Default:
                       issue-<issue-number>-<utc timestamp>-<pid>
  vm-user              Guest user to configure. Default: agent

Environment:
  CODEX_MODEL                 Codex model to run inside the guest. Default: gpt-5.4
  AGENT_SANDBOX_CODEX_POST_EXEC_VERIFICATION_PROMPT
                              Optional prompt used for one follow-up
                              `codex exec resume --last` verification pass
                              after the initial exec run exits.
  VM_WAIT_IP_TIMEOUT_SECONDS   How long to wait for the guest to receive an IPv4 address.
                               Default: 180
  VM_WAIT_SSH_TIMEOUT_SECONDS  How long to wait for guest SSH to accept connections.
                               Default: 180
  AGENT_SANDBOX_DEFAULT_REPO_SOURCE
                              Default repo-source when argument is omitted.
                              Default: repository root
  AGENT_SANDBOX_GIT_ROOT      Host path containing issue bare remotes.
                              Default: <repo-root>/git-remotes
  AGENT_SANDBOX_GIT_SSH_ALIAS Host SSH alias used inside guest clone URL.
                              Default: agent-sandbox-git-host
  AGENT_SANDBOX_BROWSER_TESTS_HOST_CHECKOUT
                              Default host checkout used for browser-test snapshots
                              when the repo-source is remote.

Mirror options (forwarded to setup_issue_git_vm.sh and mirror_issue_repo.sh):
  --sync-remote
  --no-sync-remote
  --remote-name NAME
  --refspec SPEC
  --branch NAME
  --force-local

Optional guest toolchains:
  --dotnet            Bootstrap the host .NET SDK into the guest.
  --nuget             Bootstrap NuGet relay configuration (requires project-or-solution).
  --mssql             Bootstrap Microsoft SQL Server client tooling into the guest.
  --playwright        Bootstrap the host-seeded Node + Playwright runtime into the guest.
  --browser-tests     Bootstrap host-seeded frontend install roots into the guest.
  --browser-tests-host-checkout PATH
                      Host repo checkout that owns the install roots. Optional for
                      local repo-source; required for remote repo-source.
  --browser-tests-install-root PATH
                      Install root inside the host checkout. Repeat for multiple roots.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

START_AGENT=1
BOOTSTRAP_DOTNET=0
BOOTSTRAP_NUGET=0
BOOTSTRAP_MSSQL=0
BOOTSTRAP_PLAYWRIGHT=0
BOOTSTRAP_BROWSER_TESTS=0
BROWSER_TESTS_HOST_CHECKOUT="${AGENT_SANDBOX_BROWSER_TESTS_HOST_CHECKOUT:-}"
BROWSER_TESTS_INSTALL_ROOTS=()
MIRROR_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      START_AGENT=0
      shift
      ;;
    --dotnet)
      BOOTSTRAP_DOTNET=1
      shift
      ;;
    --nuget)
      BOOTSTRAP_NUGET=1
      shift
      ;;
    --mssql)
      BOOTSTRAP_MSSQL=1
      shift
      ;;
    --playwright)
      BOOTSTRAP_PLAYWRIGHT=1
      shift
      ;;
    --browser-tests)
      BOOTSTRAP_BROWSER_TESTS=1
      shift
      ;;
    --browser-tests-host-checkout)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      BROWSER_TESTS_HOST_CHECKOUT="$2"
      shift 2
      ;;
    --browser-tests-install-root)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      BROWSER_TESTS_INSTALL_ROOTS+=("$2")
      shift 2
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

if [[ $# -lt 1 ]]; then
  show_help >&2
  exit 1
fi

ISSUE_INPUT="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/config/git-server.env"
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  . "${ENV_FILE}"
  set +a
fi

DEFAULT_REPO_SOURCE="${AGENT_SANDBOX_DEFAULT_REPO_SOURCE:-${ROOT_DIR}}"
GIT_REMOTE_ROOT="${AGENT_SANDBOX_GIT_ROOT:-${ROOT_DIR}/git-remotes}"
GIT_SSH_ALIAS="${AGENT_SANDBOX_GIT_SSH_ALIAS:-agent-sandbox-git-host}"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE:-${CALLER_HOME}/.ssh/id_ed25519.pub}"
VM_WAIT_IP_TIMEOUT_SECONDS="${VM_WAIT_IP_TIMEOUT_SECONDS:-180}"
VM_WAIT_SSH_TIMEOUT_SECONDS="${VM_WAIT_SSH_TIMEOUT_SECONDS:-180}"
SSH_PRIVATE_KEY_PATH="${SSH_PRIVATE_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"
ISSUE_FILE=""
CODEX_MODEL="${CODEX_MODEL:-gpt-5.4}"
DEFAULT_POST_EXEC_VERIFICATION_PROMPT="Before concluding, verify whether you completed all required checklist items. Confirm (or explicitly list gaps for): (1) appropriate unit tests for your code changes, (2) documentation updates for behavioral or operational changes, and (3) a markdown PR summary maintained with 'git agent-pr-note'. If anything is missing, complete it now. If you make any changes during this checklist pass, push them before concluding. A successful 'git push-agent' refreshes and pushes refs/notes/agent-pr together with your latest work."
POST_EXEC_VERIFICATION_PROMPT="${AGENT_SANDBOX_CODEX_POST_EXEC_VERIFICATION_PROMPT:-${DEFAULT_POST_EXEC_VERIFICATION_PROMPT}}"
POST_EXEC_VERIFICATION_PROMPT_B64="$(printf '%s' "${POST_EXEC_VERIFICATION_PROMPT}" | python3 -c 'import base64,sys; print(base64.b64encode(sys.stdin.buffer.read()).decode())')"

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

default_session_id() {
  local issue_number="$1"
  printf 'issue-%s-%s-%s\n' "${issue_number}" "$(date -u +%Y%m%dT%H%M%SZ)" "$$"
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

if (( BOOTSTRAP_NUGET )); then
  PREWARM_TARGET="${2:-}"
  REPO_SOURCE_ARG="${3:-}"
  SESSION_ID="${4:-}"
  VM_USER="${5:-agent}"
else
  PREWARM_TARGET=""
  REPO_SOURCE_ARG="${2:-}"
  SESSION_ID="${3:-}"
  VM_USER="${4:-agent}"
fi

if [[ -z "${SESSION_ID}" ]]; then
  SESSION_ID="$(default_session_id "${ISSUE_NUMBER}")"
fi

REPO_SOURCE="${REPO_SOURCE_ARG:-${DEFAULT_REPO_SOURCE}}"

if (( BOOTSTRAP_NUGET )); then
  if [[ -z "${PREWARM_TARGET}" ]]; then
    echo "Missing project or solution path: project-or-solution is required with --nuget" >&2
    exit 1
  fi
  if [[ ! -e "${PREWARM_TARGET}" ]]; then
    echo "Missing project or solution path: ${PREWARM_TARGET}" >&2
    exit 1
  fi
fi

if (( BOOTSTRAP_BROWSER_TESTS )); then
  if [[ ${#BROWSER_TESTS_INSTALL_ROOTS[@]} -eq 0 ]]; then
    echo "Missing browser test install roots: at least one --browser-tests-install-root is required with --browser-tests" >&2
    exit 1
  fi
fi

if ! RESOLVED_ENV="$("${SCRIPT_DIR}/resolve_repo_source.sh" --print-env "${REPO_SOURCE}")"; then
  echo "Failed to resolve repo source: ${REPO_SOURCE}" >&2
  exit 1
fi
eval "${RESOLVED_ENV}"

GUEST_REPO_NAME="${GUEST_REPO_NAME_OVERRIDE:-${RESOLVED_REPO_NAME}}"
BROWSER_TESTS_CHECKOUT_ROOT=""
BROWSER_TESTS_INSTALL_ROOTS_JSON="[]"

if (( BOOTSTRAP_BROWSER_TESTS )); then
  if [[ -n "${BROWSER_TESTS_HOST_CHECKOUT}" ]]; then
    BROWSER_TESTS_CHECKOUT_ROOT="${BROWSER_TESTS_HOST_CHECKOUT}"
  elif [[ "${RESOLVED_REPO_TYPE}" == "local" ]]; then
    BROWSER_TESTS_CHECKOUT_ROOT="${RESOLVED_REPO_SOURCE}"
  else
    echo "--browser-tests requires --browser-tests-host-checkout when repo-source is remote." >&2
    exit 1
  fi
  if ! BROWSER_TESTS_INSTALL_ROOTS_JSON="$(
    python3 - "${BROWSER_TESTS_CHECKOUT_ROOT}" "${BROWSER_TESTS_INSTALL_ROOTS[@]}" <<'PY'
import json
import sys
from pathlib import Path

checkout_root = Path(sys.argv[1]).resolve()
if not checkout_root.is_dir():
    print(f"Missing browser test host checkout: {checkout_root}", file=sys.stderr)
    sys.exit(1)

payload = []
for raw in sys.argv[2:]:
    root = Path(raw).resolve()
    try:
        relative = root.relative_to(checkout_root)
    except ValueError:
        print(f"Browser test install root must be inside host checkout: {root}", file=sys.stderr)
        sys.exit(1)
    if not (root / "package.json").is_file():
        print(f"Missing package.json under browser test install root: {root}", file=sys.stderr)
        sys.exit(1)
    if not (root / "node_modules").is_dir():
        print(f"Missing node_modules under browser test install root: {root}", file=sys.stderr)
        sys.exit(1)
    payload.append(relative.as_posix())

print(json.dumps(payload))
PY
  )"; then
    echo "Invalid browser test install roots for checkout: ${BROWSER_TESTS_CHECKOUT_ROOT}" >&2
    exit 1
  fi
fi

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

REMOTE_URL="ssh://${GIT_SSH_ALIAS}${GIT_REMOTE_ROOT}/${ISSUE_NUMBER}_repo.git"

if [[ ! -d "${GIT_REMOTE_ROOT}" ]]; then
  echo "Configured AGENT_SANDBOX_GIT_ROOT does not exist: ${GIT_REMOTE_ROOT}" >&2
  echo "Create it first or set AGENT_SANDBOX_GIT_ROOT to the host bare-remote root." >&2
  exit 1
fi

if virsh dominfo "${VM_NAME}" >/dev/null 2>&1 || sudo -n virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
  echo "VM already exists: ${VM_NAME}" >&2
  echo "Destroy it first with: sudo ./scripts/destroy_agent_vm.sh ${VM_NAME}" >&2
  exit 1
fi

# shellcheck source=lib/vm_network.sh
source "${SCRIPT_DIR}/lib/vm_network.sh"

if (( BOOTSTRAP_NUGET )); then
  log_step "prewarm_nuget_cache"
  "${SCRIPT_DIR}/prewarm_nuget_cache.sh" "${PREWARM_TARGET}"
fi
log_step "launch_vm"
sudo SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE}" "${SCRIPT_DIR}/launch_agent_vm.sh" "${VM_NAME}" "${SESSION_ID}"
log_step "wait_for_ip"
VM_IP="$(vm_network_wait_for_ip "${VM_NAME}" "${VM_WAIT_IP_TIMEOUT_SECONDS}")"
echo "Guest IPv4 detected: ${VM_IP}"
log_step "wait_for_ssh"
vm_network_wait_for_ssh "${VM_NAME}" "${VM_IP}" "${VM_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_WAIT_SSH_TIMEOUT_SECONDS}"
echo "Guest SSH is ready on ${VM_USER}@${VM_IP}"
log_step "bootstrap_codex"
"${SCRIPT_DIR}/bootstrap_codex_vm.sh" "${VM_NAME}" "${VM_USER}"
if (( BOOTSTRAP_DOTNET )); then
  log_step "bootstrap_dotnet"
  "${SCRIPT_DIR}/bootstrap_dotnet_vm.sh" "${VM_NAME}" "${VM_USER}"
fi
if (( BOOTSTRAP_NUGET )); then
  log_step "bootstrap_nuget"
  "${SCRIPT_DIR}/bootstrap_nuget_vm.sh" "${VM_NAME}" "${VM_USER}"
fi
if (( BOOTSTRAP_MSSQL )); then
  log_step "bootstrap_mssql"
  "${SCRIPT_DIR}/bootstrap_mssql_vm.sh" "${VM_NAME}" "${VM_USER}"
fi
if (( BOOTSTRAP_PLAYWRIGHT )); then
  log_step "bootstrap_playwright"
  "${SCRIPT_DIR}/bootstrap_playwright_vm.sh" "${VM_NAME}" "${VM_USER}"
fi
if (( BOOTSTRAP_BROWSER_TESTS )); then
  log_step "bootstrap_browser_tests"
  "${SCRIPT_DIR}/bootstrap_browser_tests_vm.sh" --vm-user "${VM_USER}" "${VM_NAME}" "${BROWSER_TESTS_CHECKOUT_ROOT}" "${BROWSER_TESTS_INSTALL_ROOTS[@]}"
fi
log_step "setup_issue_git_vm"
"${SCRIPT_DIR}/setup_issue_git_vm.sh" "${MIRROR_ARGS[@]}" "${VM_NAME}" "${ISSUE_NUMBER}" "${REPO_SOURCE}" "${VM_USER}"

log_step "guest_repo_clone_and_config"
ssh \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -i "${SSH_PRIVATE_KEY_PATH}" \
  "${VM_USER}@${VM_IP}" \
  "GUEST_REPO_NAME='${GUEST_REPO_NAME}' ISSUE_NUMBER='${ISSUE_NUMBER}' SESSION_ID='${SESSION_ID}' REMOTE_URL='${REMOTE_URL}' AGENT_BRANCH_SUFFIX='${VM_NAME}' BOOTSTRAP_DOTNET='${BOOTSTRAP_DOTNET}' BOOTSTRAP_NUGET='${BOOTSTRAP_NUGET}' BOOTSTRAP_BROWSER_TESTS='${BOOTSTRAP_BROWSER_TESTS}' BROWSER_TESTS_INSTALL_ROOTS_JSON='${BROWSER_TESTS_INSTALL_ROOTS_JSON}' bash -s" <<'EOF'
set -euo pipefail
. ~/.profile

TARGET_DIR="$HOME/${GUEST_REPO_NAME}"
RESTORE_INSTRUCTION=""
if [[ "${BOOTSTRAP_DOTNET:-0}" == "1" && "${BOOTSTRAP_NUGET:-0}" == "1" ]]; then
  RESTORE_INSTRUCTION=$(cat <<'TXT'
- For restore, always use:
  `dotnet restore --configfile ~/.nuget/NuGet/NuGet.Config`
- Do not rely on a repo-local `NuGet.Config` for restore inside the guest.
- For build and test, prefer:
  `dotnet build --no-restore`
  `dotnet test --no-restore`
- The reason for `--no-restore` is to avoid accidentally triggering a slow restore with the wrong NuGet config.
TXT
)
fi

if [[ -e "${TARGET_DIR}" ]]; then
  echo "Guest repo path already exists: ${TARGET_DIR}" >&2
  exit 1
fi

git clone "${REMOTE_URL}" "${TARGET_DIR}"
git -C "${TARGET_DIR}" remote add agent "${REMOTE_URL}"
git -C "${TARGET_DIR}" config user.name "Truman Bot"
git -C "${TARGET_DIR}" config user.email "truman@quinth.se"
install -d -m 0755 "${TARGET_DIR}/.agent-sandbox/bin"
install -m 0755 "${ROOT_DIR}/scripts/guest/agent_pr_note.sh" "${TARGET_DIR}/.agent-sandbox/bin/agent-pr-note"
git -C "${TARGET_DIR}" config alias.agent-pr-note '!f(){ repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.agent-sandbox/bin/agent-pr-note" "$@"; }; f'
git -C "${TARGET_DIR}" config alias.push-agent '!f(){ session_id=${SESSION_ID:-}; if [[ -z "${session_id}" ]]; then echo "SESSION_ID is not set; refusing ambiguous agent push." >&2; return 1; fi; suffix_source=${AGENT_BRANCH_SUFFIX:-${HOSTNAME:-}}; suffix=$(printf "%s" "${suffix_source}" | tr "[:upper:]" "[:lower:]" | tr -cs "a-z0-9._-" "-" | sed "s/^-*//;s/-*$//"); if [[ -z "${suffix}" ]]; then echo "AGENT_BRANCH_SUFFIX/HOSTNAME is empty; refusing ambiguous agent push." >&2; return 1; fi; repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.agent-sandbox/bin/agent-pr-note" ensure-head; branch=$(git rev-parse --abbrev-ref HEAD); git push agent HEAD:refs/heads/agents/${session_id}/${branch}-${suffix} refs/notes/agent-pr; }; f'

if [[ "${BOOTSTRAP_BROWSER_TESTS:-0}" == "1" ]]; then
  python3 - "$TARGET_DIR" "${BROWSER_TESTS_INSTALL_ROOTS_JSON:-[]}" <<'PY'
import json
import os
import shutil
import sys
from pathlib import Path

target_dir = Path(sys.argv[1])
install_roots = json.loads(sys.argv[2])
home = Path.home()
snapshot_root = home / "browser-test-runtime" / "install-roots"

if not install_roots:
    raise SystemExit("Missing browser test install-root mapping inside guest bootstrap flow.")

for relative in install_roots:
    source_root = snapshot_root / relative
    dest_root = target_dir / relative
    if not dest_root.exists():
        raise SystemExit(f"Expected browser test install root missing in guest clone: {dest_root}")
    if not source_root.exists():
        raise SystemExit(f"Expected browser test snapshot missing in guest runtime: {source_root}")
    for child_name in (
        "node_modules",
        "package.json",
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        ".yarnrc.yml",
        ".npmrc",
        "angular.json",
        "workspace.json",
        "nx.json",
    ):
        source_child = source_root / child_name
        dest_child = dest_root / child_name
        if not source_child.exists():
            continue
        if dest_child.is_symlink() or dest_child.is_file():
            dest_child.unlink()
        elif dest_child.is_dir():
            shutil.rmtree(dest_child)
        if source_child.is_dir():
            shutil.copytree(source_child, dest_child, symlinks=True)
        else:
            dest_child.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_child, dest_child)
PY
fi

cat > "$HOME/AGENTS.md" <<AGENTS
# Guest Notes

- Primary repo path: ${TARGET_DIR}
- Task file in repo root: ${TARGET_DIR}/AGENT_TASK.yaml
- Guest Git remote: ${REMOTE_URL}
- Recommended push command from inside the repo:
  \`git push-agent\`
- PR note helper from inside the repo:
  \`git agent-pr-note\`
- Equivalent explicit push pattern:
  \`git push agent HEAD:refs/heads/agents/\${SESSION_ID}/\$(git rev-parse --abbrev-ref HEAD)-\${AGENT_BRANCH_SUFFIX:-\${HOSTNAME}} refs/notes/agent-pr\`
- Branch suffix defaults to \`${AGENT_BRANCH_SUFFIX}\` (set to the VM name by setup); fallback is \`${HOSTNAME}\`.
- `push-agent` refuses to run when `SESSION_ID` is missing or the suffix resolves empty, because that would cause branch collisions.
- `push-agent` also refreshes the current branch's cached PR summary onto \`HEAD\` and pushes \`refs/notes/agent-pr\` automatically.
${RESTORE_INSTRUCTION}
- If shell changes were just bootstrapped, run:
  \`. ~/.profile\`

## PR Summary Requirement

Agents must provide a markdown summary of changes and attach it to the tip commit
with Git notes.

- Required notes ref: \`agent-pr\`
- Use \`git agent-pr-note set\` to create or replace the full PR summary snapshot for the current branch.
- Use \`git agent-pr-note append\` for follow-up commits so the summary stays additive.
- \`git push-agent\` reattaches the cached summary to the current \`HEAD\` and pushes the notes ref automatically.

Copy/paste examples (replace placeholders):

\`\`\`bash
# Create the initial full PR summary for this branch.
git agent-pr-note set -m "<markdown summary in Markdown>"

# Append a later update without losing the earlier summary.
git agent-pr-note append -m "<follow-up markdown update>"

# Push the current branch and refreshed notes ref together.
git push-agent
\`\`\`

### Troubleshooting

- Summary missing on the latest commit:
  run \`git agent-pr-note ensure-head\` or just \`git push-agent\` from the branch tip.
- Wrong ref name:
  use exactly \`--ref=agent-pr\` (this maps to \`refs/notes/agent-pr\`).
- Note attached to the wrong commit:
  checkout the correct tip commit and re-run
  \`git agent-pr-note ensure-head\`.
AGENTS
EOF

if [[ -n "${ISSUE_FILE}" ]]; then
  log_step "copy_issue_file"
  scp \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -i "${SSH_PRIVATE_KEY_PATH}" \
    "${ISSUE_FILE}" \
    "${VM_USER}@${VM_IP}:/home/${VM_USER}/${GUEST_REPO_NAME}/AGENT_TASK.yaml" >/dev/null
else
  log_step "write_default_issue_file"
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
  log_step "start_agent"
  ssh \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -i "${SSH_PRIVATE_KEY_PATH}" \
    "${VM_USER}@${VM_IP}" \
    "GUEST_REPO_NAME='${GUEST_REPO_NAME}' CODEX_MODEL='${CODEX_MODEL}' POST_EXEC_VERIFICATION_PROMPT_B64='${POST_EXEC_VERIFICATION_PROMPT_B64}' BOOTSTRAP_DOTNET='${BOOTSTRAP_DOTNET}' BOOTSTRAP_NUGET='${BOOTSTRAP_NUGET}' bash -s" <<'EOF'
set -euo pipefail
. ~/.profile

TARGET_DIR="$HOME/${GUEST_REPO_NAME}"
LOG_DIR="$HOME/.agent-sandbox"
LOG_PATH="${LOG_DIR}/codex-${GUEST_REPO_NAME}.log"
PID_PATH="${LOG_DIR}/codex-${GUEST_REPO_NAME}.pid"
INITIAL_PROMPT="You have been issued the task described in ${TARGET_DIR}/AGENT_TASK.yaml. Read ${HOME}/AGENTS.md and ${TARGET_DIR}/AGENT_TASK.yaml before you begin. The repository is located at ${TARGET_DIR}. Do not ask for feedback or clarifications, but work until the task is completed or until you determine that it is impossible to complete."
if [[ "${BOOTSTRAP_DOTNET:-0}" == "1" && "${BOOTSTRAP_NUGET:-0}" == "1" ]]; then
  INITIAL_PROMPT="${INITIAL_PROMPT} When restore is needed, use 'dotnet restore --configfile ~/.nuget/NuGet/NuGet.Config'. For build and test, prefer '--no-restore'."
fi
INITIAL_PROMPT="${INITIAL_PROMPT} When you have finished, commit and push your work using 'git push-agent'."
POST_EXEC_VERIFICATION_PROMPT="$(python3 -c 'import base64, os; print(base64.b64decode(os.environ["POST_EXEC_VERIFICATION_PROMPT_B64"]).decode("utf-8"))')"

mkdir -p "${LOG_DIR}"

if [[ -f "${PID_PATH}" ]]; then
  EXISTING_PID="$(cat "${PID_PATH}")"
  if [[ -n "${EXISTING_PID}" ]] && kill -0 "${EXISTING_PID}" >/dev/null 2>&1; then
    echo "Codex already running in guest with PID ${EXISTING_PID}" >&2
    exit 1
  fi
fi

nohup env \
  CODEX_MODEL="${CODEX_MODEL}" \
  TARGET_DIR="${TARGET_DIR}" \
  INITIAL_PROMPT="${INITIAL_PROMPT}" \
  POST_EXEC_VERIFICATION_PROMPT="${POST_EXEC_VERIFICATION_PROMPT}" \
  bash -lc '
codex exec \
  --model "${CODEX_MODEL}" \
  -s danger-full-access \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  -C "${TARGET_DIR}" \
  "${INITIAL_PROMPT}"
exec_status=$?
codex exec resume \
  --last \
  --model "${CODEX_MODEL}" \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  "${POST_EXEC_VERIFICATION_PROMPT}" || true
exit "${exec_status}"
' >"${LOG_PATH}" 2>&1 < /dev/null &

AGENT_PID=$!
echo "${AGENT_PID}" > "${PID_PATH}"
printf 'pid=%s\nlog=%s\n' "${AGENT_PID}" "${LOG_PATH}"
EOF
fi

log_step "complete"
echo
echo "Issue VM setup complete."
echo "  vm:      ${VM_NAME}"
echo "  session: ${SESSION_ID}"
echo "  repo:    ${REMOTE_URL}"
echo "  guest:   /home/${VM_USER}/${GUEST_REPO_NAME}"
echo "  task:    /home/${VM_USER}/${GUEST_REPO_NAME}/AGENT_TASK.yaml"
echo "  dotnet:  $([[ ${BOOTSTRAP_DOTNET} -eq 1 ]] && echo enabled || echo disabled)"
echo "  nuget:   $([[ ${BOOTSTRAP_NUGET} -eq 1 ]] && echo enabled || echo disabled)"
echo "  mssql:   $([[ ${BOOTSTRAP_MSSQL} -eq 1 ]] && echo enabled || echo disabled)"
echo "  playwright: $([[ ${BOOTSTRAP_PLAYWRIGHT} -eq 1 ]] && echo enabled || echo disabled)"
echo "  browser tests: $([[ ${BOOTSTRAP_BROWSER_TESTS} -eq 1 ]] && echo enabled || echo disabled)"
echo "  push:    git -C /home/${VM_USER}/${GUEST_REPO_NAME} push-agent"
if (( START_AGENT )); then
  echo "  agent:   started"
  echo "  log:     /home/${VM_USER}/.agent-sandbox/codex-${GUEST_REPO_NAME}.log"
  echo "  pid:     /home/${VM_USER}/.agent-sandbox/codex-${GUEST_REPO_NAME}.pid"
else
  echo "  agent:   not started (--dry-run)"
fi
