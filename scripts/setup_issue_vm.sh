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
Usage: setup_issue_vm.sh [--dry-run] [--python] [--dotnet] [--nuget] [--mssql [--mssql-init-root PATH]] [--playwright] [--frontend-deps --frontend-host-checkout PATH --frontend-install-root PATH ...] [mirror-options] <issue-number|issue-yaml> [project-or-solution-if-nuget] [repo-source] [session-id] [vm-user]

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
  AGENT_PROVIDER              Agent runner provider. Default: codex
  AGENT_MODEL                 Model to run inside the guest. Default: gpt-5.4
  AGENT_POST_EXEC_VERIFICATION_PROMPT
                              Optional prompt used for one follow-up
                              `agent_runner_resume_last` verification pass
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
  AGENT_SANDBOX_FRONTEND_HOST_CHECKOUT
                              Default host checkout used for frontend dependency snapshots
                              when the repo-source is remote.
  AGENT_SANDBOX_MSSQL_INIT_ROOT
                              Default host directory copied into /var/opt/mssql/init
                              when --mssql is enabled. If present, init.sql is executed.
  MSSQL_SA_PASSWORD          Required SA password used to initialize the guest-local
                              SQL Server instance when --mssql is enabled.

Mirror options (forwarded to setup_issue_git_vm.sh and mirror_issue_repo.sh):
  --sync-remote
  --no-sync-remote
  --remote-name NAME
  --refspec SPEC
  --branch NAME
  --force-local

Optional guest toolchains:
  --python            Bootstrap a guest-local Python virtualenv for Python-first repos.
  --dotnet            Bootstrap the host .NET SDK into the guest.
  --nuget             Bootstrap NuGet relay configuration (requires project-or-solution).
  --mssql             Bootstrap a guest-local Microsoft SQL Server instance into the guest.
  --mssql-init-root PATH
                      Host directory copied into /var/opt/mssql/init. If present,
                      init.sql from that directory is executed after SQL Server starts.
  --playwright        Bootstrap the host-seeded Node + Playwright runtime into the guest.
  --frontend-deps     Bootstrap host-seeded frontend install roots into the guest.
  --frontend-host-checkout PATH
                      Host repo checkout that owns the install roots. Optional for
                      local repo-source; required for remote repo-source.
  --frontend-install-root PATH
                      Install root inside the host checkout. Repeat for multiple roots.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

START_AGENT=1
BOOTSTRAP_PYTHON=0
BOOTSTRAP_DOTNET=0
BOOTSTRAP_NUGET=0
BOOTSTRAP_MSSQL=0
BOOTSTRAP_PLAYWRIGHT=0
BOOTSTRAP_FRONTEND_DEPS=0
MSSQL_INIT_ROOT="${AGENT_SANDBOX_MSSQL_INIT_ROOT:-}"
FRONTEND_HOST_CHECKOUT="${AGENT_SANDBOX_FRONTEND_HOST_CHECKOUT:-}"
FRONTEND_INSTALL_ROOTS=()
MIRROR_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      START_AGENT=0
      shift
      ;;
    --python)
      BOOTSTRAP_PYTHON=1
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
    --mssql-init-root)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      MSSQL_INIT_ROOT="$2"
      shift 2
      ;;
    --playwright)
      BOOTSTRAP_PLAYWRIGHT=1
      shift
      ;;
    --frontend-deps)
      BOOTSTRAP_FRONTEND_DEPS=1
      shift
      ;;
    --frontend-host-checkout)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      FRONTEND_HOST_CHECKOUT="$2"
      shift 2
      ;;
    --frontend-install-root)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      FRONTEND_INSTALL_ROOTS+=("$2")
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
AGENT_PROVIDER="${AGENT_PROVIDER:-codex}"
AGENT_MODEL="${AGENT_MODEL:-gpt-5.4}"
DEFAULT_POST_EXEC_VERIFICATION_PROMPT="Before concluding, verify whether you completed all required checklist items. Confirm (or explicitly list gaps for): (1) appropriate unit tests for your code changes, (2) documentation updates for behavioral or operational changes, and (3) a markdown PR summary maintained with 'git agent-pr-note'. If anything is missing, complete it now. If you make any changes during this checklist pass, push them before concluding. A successful 'git push-agent' refreshes and pushes refs/notes/agent-pr together with your latest work."
POST_EXEC_VERIFICATION_PROMPT="${AGENT_POST_EXEC_VERIFICATION_PROMPT:-${DEFAULT_POST_EXEC_VERIFICATION_PROMPT}}"
POST_EXEC_VERIFICATION_PROMPT_B64="$(printf '%s' "${POST_EXEC_VERIFICATION_PROMPT}" | python3 -c 'import base64,sys; print(base64.b64encode(sys.stdin.buffer.read()).decode())')"
AGENT_RUNNER_LIB_B64="$(base64 -w0 "${SCRIPT_DIR}/lib/agent_runner.sh")"
AGENT_RUNNER_CODEX_LIB_B64="$(base64 -w0 "${SCRIPT_DIR}/lib/agent_runner_codex.sh")"
AGENT_STATE_LIB_B64="$(base64 -w0 "${SCRIPT_DIR}/lib/agent_state.sh")"
AGENT_PR_NOTE_HELPER_B64="$(
  python3 - "${ROOT_DIR}/scripts/guest/agent_pr_note.sh" <<'PY'
import base64
import sys
from pathlib import Path

print(base64.b64encode(Path(sys.argv[1]).read_bytes()).decode())
PY
)"

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
AGENT_SANDBOX_RUNTIME_DIR="${AGENT_SANDBOX_RUNTIME_DIR:-${ROOT_DIR}/runtime}"

resolve_vm_defaults_exports() {
  local python_bin="${ROOT_DIR}/.venv/bin/python"
  if [[ ! -x "${python_bin}" ]]; then
    python_bin="python3"
  fi
  PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" \
    "${python_bin}" - "${AGENT_SANDBOX_RUNTIME_DIR}" "${SESSION_ID}" <<'PY'
import shlex
import sys

from agent_sandbox.bootstrap import load_runtime_gateway_config
from agent_sandbox.vm_defaults_config import VmDefaultsConfigResolver

runtime_dir, session_id = sys.argv[1], sys.argv[2]
resolver = VmDefaultsConfigResolver(load_runtime_gateway_config(runtime_dir=runtime_dir))
try:
    vm_defaults = resolver.resolve_for_session_id(session_id)
except ValueError:
    vm_defaults = resolver.resolve_for_directory("root")
for key, value in sorted(vm_defaults.guest_environment().items()):
    print(f"export {key}={shlex.quote(value)}")
PY
}

if [[ -z "${GATEWAY_URL:-}" || -z "${LLM_BASE_URL:-}" || -z "${NUGET_RELAY_BASE_URL:-}" ]]; then
  if VM_DEFAULTS_EXPORTS="$(resolve_vm_defaults_exports 2>/dev/null)"; then
    eval "${VM_DEFAULTS_EXPORTS}"
  fi
fi

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

if (( BOOTSTRAP_FRONTEND_DEPS )); then
  if [[ ${#FRONTEND_INSTALL_ROOTS[@]} -eq 0 ]]; then
    echo "Missing frontend install roots: at least one --frontend-install-root is required with --frontend-deps" >&2
    exit 1
  fi
fi

if (( BOOTSTRAP_MSSQL )); then
  if [[ -z "${MSSQL_SA_PASSWORD:-}" ]]; then
    echo "MSSQL_SA_PASSWORD is required when --mssql is enabled." >&2
    exit 1
  fi
  if [[ -n "${MSSQL_INIT_ROOT}" && ! -d "${MSSQL_INIT_ROOT}" ]]; then
    echo "Missing MSSQL init root: ${MSSQL_INIT_ROOT}" >&2
    exit 1
  fi
fi

if ! RESOLVED_ENV="$("${SCRIPT_DIR}/resolve_repo_source.sh" --print-env "${REPO_SOURCE}")"; then
  echo "Failed to resolve repo source: ${REPO_SOURCE}" >&2
  exit 1
fi
eval "${RESOLVED_ENV}"

GUEST_REPO_NAME="${GUEST_REPO_NAME_OVERRIDE:-${RESOLVED_REPO_NAME}}"
FRONTEND_CHECKOUT_ROOT=""
FRONTEND_INSTALL_ROOTS_JSON="[]"

if (( BOOTSTRAP_FRONTEND_DEPS )); then
  if [[ -n "${FRONTEND_HOST_CHECKOUT}" ]]; then
    FRONTEND_CHECKOUT_ROOT="${FRONTEND_HOST_CHECKOUT}"
  elif [[ "${RESOLVED_REPO_TYPE}" == "local" ]]; then
    FRONTEND_CHECKOUT_ROOT="${RESOLVED_REPO_SOURCE}"
  else
    echo "--frontend-deps requires --frontend-host-checkout when repo-source is remote." >&2
    exit 1
  fi
  if ! FRONTEND_INSTALL_ROOTS_JSON="$(
    python3 - "${FRONTEND_CHECKOUT_ROOT}" "${FRONTEND_INSTALL_ROOTS[@]}" <<'PY'
import json
import sys
from pathlib import Path

checkout_root = Path(sys.argv[1]).resolve()
if not checkout_root.is_dir():
    print(f"Missing frontend host checkout: {checkout_root}", file=sys.stderr)
    sys.exit(1)

payload = []
for raw in sys.argv[2:]:
    root = Path(raw).resolve()
    try:
        relative = root.relative_to(checkout_root)
    except ValueError:
        print(f"Frontend install root must be inside host checkout: {root}", file=sys.stderr)
        sys.exit(1)
    if not (root / "package.json").is_file():
        print(f"Missing package.json under frontend install root: {root}", file=sys.stderr)
        sys.exit(1)
    if not (root / "node_modules").is_dir():
        print(f"Missing node_modules under frontend install root: {root}", file=sys.stderr)
        sys.exit(1)
    payload.append(relative.as_posix())

print(json.dumps(payload))
PY
  )"; then
    echo "Invalid frontend install roots for checkout: ${FRONTEND_CHECKOUT_ROOT}" >&2
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

if sudo -n virsh dominfo "${VM_NAME}" >/dev/null 2>&1 || virsh dominfo "${VM_NAME}" >/dev/null 2>&1; then
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
sudo \
  SSH_PUBLIC_KEY_FILE="${SSH_PUBLIC_KEY_FILE}" \
  GATEWAY_URL="${GATEWAY_URL:-}" \
  LLM_BASE_URL="${LLM_BASE_URL:-}" \
  "${SCRIPT_DIR}/launch_agent_vm.sh" "${VM_NAME}" "${SESSION_ID}"
log_step "wait_for_ip"
VM_IP="$(vm_network_wait_for_ip "${VM_NAME}" "${VM_WAIT_IP_TIMEOUT_SECONDS}")"
echo "Guest IPv4 detected: ${VM_IP}"
log_step "wait_for_ssh"
vm_network_wait_for_ssh "${VM_NAME}" "${VM_IP}" "${VM_USER}" "${SSH_PRIVATE_KEY_PATH}" "${VM_WAIT_SSH_TIMEOUT_SECONDS}"
echo "Guest SSH is ready on ${VM_USER}@${VM_IP}"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_PRIVATE_KEY_PATH}" "${CALLER_HOME}")
log_step "bootstrap_codex"
"${SCRIPT_DIR}/bootstrap_codex_vm.sh" "${VM_NAME}" "${VM_USER}"
if (( BOOTSTRAP_DOTNET )); then
  log_step "bootstrap_dotnet"
  "${SCRIPT_DIR}/bootstrap_dotnet_vm.sh" "${VM_NAME}" "${VM_USER}"
fi
if (( BOOTSTRAP_NUGET )); then
  log_step "bootstrap_nuget"
  NUGET_RELAY_BASE_URL="${NUGET_RELAY_BASE_URL:-}" "${SCRIPT_DIR}/bootstrap_nuget_vm.sh" "${VM_NAME}" "${VM_USER}"
fi
if (( BOOTSTRAP_MSSQL )); then
  log_step "bootstrap_mssql"
  MSSQL_SA_PASSWORD="${MSSQL_SA_PASSWORD}" \
    MSSQL_INIT_ROOT="${MSSQL_INIT_ROOT}" \
    "${SCRIPT_DIR}/bootstrap_mssql_vm.sh" "${VM_NAME}" "${VM_USER}"
fi
if (( BOOTSTRAP_PLAYWRIGHT )); then
  log_step "bootstrap_playwright"
  "${SCRIPT_DIR}/bootstrap_playwright_vm.sh" "${VM_NAME}" "${VM_USER}"
fi
if (( BOOTSTRAP_FRONTEND_DEPS )); then
  log_step "bootstrap_frontend_deps"
  "${SCRIPT_DIR}/bootstrap_frontend_deps_vm.sh" --vm-user "${VM_USER}" "${VM_NAME}" "${FRONTEND_CHECKOUT_ROOT}" "${FRONTEND_INSTALL_ROOTS[@]}"
fi
log_step "setup_issue_git_vm"
"${SCRIPT_DIR}/setup_issue_git_vm.sh" "${MIRROR_ARGS[@]}" "${VM_NAME}" "${ISSUE_NUMBER}" "${REPO_SOURCE}" "${VM_USER}"

log_step "guest_repo_clone_and_config"
TARGET_DIR="/home/${VM_USER}/${GUEST_REPO_NAME}"
PYTHON_INSTRUCTION=""
RESTORE_INSTRUCTION=""
if (( BOOTSTRAP_PYTHON )); then
  PYTHON_INSTRUCTION=$(cat <<'TXT'
- For Python work, use the repo-local virtualenv:
  `. .venv/bin/activate`
- For tests, prefer:
  `python -m pytest -q`
- For lint, prefer:
  `python -m ruff check`
- Treat in-guest Python checks as repo validation only; host-only libvirt, firewall,
  and systemd integration still needs host-side verification after promotion.
TXT
)
fi
if (( BOOTSTRAP_DOTNET )) && (( BOOTSTRAP_NUGET )); then
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

ssh \
  "${SSH_OPTS[@]}" \
  "${VM_USER}@${VM_IP}" \
  "GUEST_REPO_NAME='${GUEST_REPO_NAME}' ISSUE_NUMBER='${ISSUE_NUMBER}' SESSION_ID='${SESSION_ID}' REMOTE_URL='${REMOTE_URL}' AGENT_BRANCH_SUFFIX='${VM_NAME}' BOOTSTRAP_FRONTEND_DEPS='${BOOTSTRAP_FRONTEND_DEPS}' FRONTEND_INSTALL_ROOTS_JSON='${FRONTEND_INSTALL_ROOTS_JSON}' AGENT_PR_NOTE_HELPER_B64='${AGENT_PR_NOTE_HELPER_B64}' bash -s" <<'EOF'
set -euo pipefail
. ~/.profile

TARGET_DIR="$HOME/${GUEST_REPO_NAME}"

if [[ -e "${TARGET_DIR}" ]]; then
  echo "Guest repo path already exists: ${TARGET_DIR}" >&2
  exit 1
fi

git clone "${REMOTE_URL}" "${TARGET_DIR}"
git -C "${TARGET_DIR}" remote add agent "${REMOTE_URL}"
git -C "${TARGET_DIR}" config user.name "Truman Bot"
git -C "${TARGET_DIR}" config user.email "truman@quinth.se"
git -C "${TARGET_DIR}" config agent-sandbox.sessionId "${SESSION_ID}"
git -C "${TARGET_DIR}" config agent-sandbox.branchSuffix "${AGENT_BRANCH_SUFFIX}"
install -d -m 0755 "${TARGET_DIR}/.agent-sandbox/bin"
python3 - "${TARGET_DIR}/.agent-sandbox/bin/agent-pr-note" "${AGENT_PR_NOTE_HELPER_B64}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
git -C "${TARGET_DIR}" config alias.agent-pr-note '!f(){ repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.agent-sandbox/bin/agent-pr-note" "$@"; }; f'
git -C "${TARGET_DIR}" config alias.push-agent '!f(){ session_id=${SESSION_ID:-$(git config --get agent-sandbox.sessionId || true)}; if [ -z "${session_id}" ]; then echo "SESSION_ID is not set; refusing ambiguous agent push." >&2; return 1; fi; suffix_source=${AGENT_BRANCH_SUFFIX:-$(git config --get agent-sandbox.branchSuffix || true)}; if [ -z "${suffix_source}" ]; then suffix_source=${HOSTNAME:-}; fi; suffix=$(printf "%s" "${suffix_source}" | tr "[:upper:]" "[:lower:]" | tr -cs "a-z0-9._-" "-" | sed "s/^-*//;s/-*$//"); if [ -z "${suffix}" ]; then echo "AGENT_BRANCH_SUFFIX/HOSTNAME is empty; refusing ambiguous agent push." >&2; return 1; fi; repo_root=$(git rev-parse --show-toplevel); "${repo_root}/.agent-sandbox/bin/agent-pr-note" ensure-head; branch=$(git rev-parse --abbrev-ref HEAD); git push agent HEAD:refs/heads/agents/${session_id}/${branch}-${suffix} refs/notes/agent-pr; }; f'

if [[ "${BOOTSTRAP_FRONTEND_DEPS:-0}" == "1" ]]; then
  python3 - "$TARGET_DIR" "${FRONTEND_INSTALL_ROOTS_JSON:-[]}" <<'PY'
import json
import os
import shutil
import sys
from pathlib import Path

target_dir = Path(sys.argv[1])
install_roots = json.loads(sys.argv[2])
home = Path.home()
snapshot_root = home / "frontend-deps-runtime" / "install-roots"

if not install_roots:
    raise SystemExit("Missing frontend install-root mapping inside guest bootstrap flow.")

for relative in install_roots:
    source_root = snapshot_root / relative
    dest_root = target_dir / relative
    if not dest_root.exists():
        raise SystemExit(f"Expected frontend install root missing in guest clone: {dest_root}")
    if not source_root.exists():
        raise SystemExit(f"Expected frontend dependency snapshot missing in guest runtime: {source_root}")
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
EOF

if (( BOOTSTRAP_PYTHON )); then
  log_step "bootstrap_python"
  "${SCRIPT_DIR}/bootstrap_python_vm.sh" --vm-user "${VM_USER}" "${VM_NAME}" "${RESOLVED_REPO_PATH}" "/home/${VM_USER}/${GUEST_REPO_NAME}"
fi

if [[ -n "${ISSUE_FILE}" ]]; then
  log_step "copy_issue_file"
  scp \
    "${SSH_OPTS[@]}" \
    "${ISSUE_FILE}" \
    "${VM_USER}@${VM_IP}:/home/${VM_USER}/${GUEST_REPO_NAME}/AGENT_TASK.yaml" >/dev/null
else
  log_step "write_default_issue_file"
  ssh \
    "${SSH_OPTS[@]}" \
    "${VM_USER}@${VM_IP}" \
    "cat > /home/${VM_USER}/${GUEST_REPO_NAME}/AGENT_TASK.yaml" <<'GUEST_TASK_EOF'
issue_number: ${ISSUE_NUMBER}
name: "Not provided"
description: |
  No issue metadata file was provided during VM setup.
GUEST_TASK_EOF
fi

python3 - "${SCRIPT_DIR}/../templates/guest-AGENTS.md.tmpl" "${TARGET_DIR}" "${REMOTE_URL}" "${PYTHON_INSTRUCTION}" "${RESTORE_INSTRUCTION}" <<'PY' | ssh \
  "${SSH_OPTS[@]}" \
  "${VM_USER}@${VM_IP}" \
  "cat > \$HOME/AGENTS.md"
from pathlib import Path
import sys

template_path = Path(sys.argv[1]).resolve()
text = template_path.read_text(encoding="utf-8")
replacements = {
    "__TARGET_DIR__": sys.argv[2],
    "__REMOTE_URL__": sys.argv[3],
    "__PYTHON_INSTRUCTION__": sys.argv[4],
    "__RESTORE_INSTRUCTION__": sys.argv[5],
}
for placeholder, value in replacements.items():
    text = text.replace(placeholder, value)
print(text, end="")
PY

if (( START_AGENT )); then
  log_step "start_agent"
  ssh \
    "${SSH_OPTS[@]}" \
    "${VM_USER}@${VM_IP}" \
    "GUEST_REPO_NAME='${GUEST_REPO_NAME}' AGENT_PROVIDER='${AGENT_PROVIDER}' AGENT_MODEL='${AGENT_MODEL}' POST_EXEC_VERIFICATION_PROMPT_B64='${POST_EXEC_VERIFICATION_PROMPT_B64}' AGENT_RUNNER_LIB_B64='${AGENT_RUNNER_LIB_B64}' AGENT_RUNNER_CODEX_LIB_B64='${AGENT_RUNNER_CODEX_LIB_B64}' AGENT_STATE_LIB_B64='${AGENT_STATE_LIB_B64}' BOOTSTRAP_PYTHON='${BOOTSTRAP_PYTHON}' BOOTSTRAP_DOTNET='${BOOTSTRAP_DOTNET}' BOOTSTRAP_NUGET='${BOOTSTRAP_NUGET}' bash -s" <<'START_AGENT_EOF'
set -euo pipefail
. ~/.profile
AGENT_RUNNER_TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${AGENT_RUNNER_TMP_DIR}"' EXIT
printf '%s' "${AGENT_RUNNER_LIB_B64}" | base64 -d > "${AGENT_RUNNER_TMP_DIR}/agent_runner.sh"
printf '%s' "${AGENT_RUNNER_CODEX_LIB_B64}" | base64 -d > "${AGENT_RUNNER_TMP_DIR}/agent_runner_codex.sh"
# shellcheck source=/dev/null
source "${AGENT_RUNNER_TMP_DIR}/agent_runner.sh"

TARGET_DIR="$HOME/${GUEST_REPO_NAME}"
LOG_DIR="$HOME/.agent-sandbox"
mkdir -p "${LOG_DIR}/lib"
printf '%s' "${AGENT_RUNNER_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/agent_runner.sh"
printf '%s' "${AGENT_RUNNER_CODEX_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/agent_runner_codex.sh"
printf '%s' "${AGENT_STATE_LIB_B64}" | base64 -d > "${LOG_DIR}/lib/agent_state.sh"
LOG_PATH="${LOG_DIR}/agent-${GUEST_REPO_NAME}.log"
PID_PATH="${LOG_DIR}/agent-${GUEST_REPO_NAME}.pid"
STATE_PATH="${LOG_DIR}/agent-${GUEST_REPO_NAME}.state"
# shellcheck source=/dev/null
source "${LOG_DIR}/lib/agent_state.sh"
INITIAL_PROMPT="You have been issued the task described in ${TARGET_DIR}/AGENT_TASK.yaml. Read ${HOME}/AGENTS.md and ${TARGET_DIR}/AGENT_TASK.yaml before you begin. The repository is located at ${TARGET_DIR}. Do not ask for feedback or clarifications at this time, but work until the task is completed or until you determine that it is impossible to complete."
if [[ "${BOOTSTRAP_PYTHON:-0}" == "1" ]]; then
  INITIAL_PROMPT="${INITIAL_PROMPT} Use the repo-local virtualenv at ${TARGET_DIR}/.venv for Python work. Run tests with 'python -m pytest -q' from an activated venv and lint with 'python -m ruff check'. Guest validation does not replace host-side libvirt, firewall, or systemd verification."
fi
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

ensure_agent_enabled

nohup env \
  AGENT_PROVIDER="${AGENT_PROVIDER}" \
  AGENT_MODEL="${AGENT_MODEL}" \
  TARGET_DIR="${TARGET_DIR}" \
  GUEST_REPO_NAME="${GUEST_REPO_NAME}" \
  INITIAL_PROMPT="${INITIAL_PROMPT}" \
  POST_EXEC_VERIFICATION_PROMPT="${POST_EXEC_VERIFICATION_PROMPT}" \
  STATE_PATH="${STATE_PATH}" \
  AGENT_STATE_REPO_NAME="${GUEST_REPO_NAME}" \
  bash -lc '
. "$HOME/.agent-sandbox/lib/agent_runner.sh"
. "$HOME/.agent-sandbox/lib/agent_state.sh"

ensure_agent_enabled

write_state "running" "initial"
trap on_exit_write_idle EXIT

agent_runner_exec \
  --model "${AGENT_MODEL}" \
  -s danger-full-access \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  -C "${TARGET_DIR}" \
  "${INITIAL_PROMPT}"
exec_status=$?
write_state "running" "post_exec" "${exec_status}"
agent_runner_resume_last \
  --model "${AGENT_MODEL}" \
  --dangerously-bypass-approvals-and-sandbox \
  --skip-git-repo-check \
  "${POST_EXEC_VERIFICATION_PROMPT}" || true
exit "${exec_status}"
' >"${LOG_PATH}" 2>&1 < /dev/null &

AGENT_PID=$!
echo "${AGENT_PID}" > "${PID_PATH}"
printf 'pid=%s\nlog=%s\n' "${AGENT_PID}" "${LOG_PATH}"
START_AGENT_EOF
fi

log_step "complete"
echo
echo "Issue VM setup complete."
echo "  vm:      ${VM_NAME}"
echo "  session: ${SESSION_ID}"
echo "  repo:    ${REMOTE_URL}"
echo "  guest:   /home/${VM_USER}/${GUEST_REPO_NAME}"
echo "  task:    /home/${VM_USER}/${GUEST_REPO_NAME}/AGENT_TASK.yaml"
echo "  python:  $([[ ${BOOTSTRAP_PYTHON} -eq 1 ]] && echo enabled || echo disabled)"
echo "  dotnet:  $([[ ${BOOTSTRAP_DOTNET} -eq 1 ]] && echo enabled || echo disabled)"
echo "  nuget:   $([[ ${BOOTSTRAP_NUGET} -eq 1 ]] && echo enabled || echo disabled)"
echo "  mssql:   $([[ ${BOOTSTRAP_MSSQL} -eq 1 ]] && echo enabled || echo disabled)"
if (( BOOTSTRAP_MSSQL )) && [[ -n "${MSSQL_INIT_ROOT}" ]]; then
  echo "  mssql init root: ${MSSQL_INIT_ROOT}"
fi
echo "  playwright: $([[ ${BOOTSTRAP_PLAYWRIGHT} -eq 1 ]] && echo enabled || echo disabled)"
echo "  frontend deps: $([[ ${BOOTSTRAP_FRONTEND_DEPS} -eq 1 ]] && echo enabled || echo disabled)"
echo "  push:    git -C /home/${VM_USER}/${GUEST_REPO_NAME} push-agent"
if (( START_AGENT )); then
  echo "  agent:   started"
  echo "  log:     /home/${VM_USER}/.agent-sandbox/agent-${GUEST_REPO_NAME}.log"
  echo "  pid:     /home/${VM_USER}/.agent-sandbox/agent-${GUEST_REPO_NAME}.pid"
else
  echo "  agent:   not started (--dry-run)"
fi
