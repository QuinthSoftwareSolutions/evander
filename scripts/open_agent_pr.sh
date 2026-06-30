#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: open_agent_pr.sh [options] <host-repo-path> [local-branch] [origin-branch]

Push a reviewed local branch from a trusted working clone to origin and,
optionally, open a pull request on the canonical remote.

Arguments:
  host-repo-path  Path to a normal local clone of the trusted repository.
  local-branch    Local branch to push. Default: current branch.
  origin-branch   Destination branch name on origin. Default: same as local branch.

Options:
  --create-pr         Open an upstream PR after pushing.
  --draft             Force Azure DevOps PR creation in draft mode.
  --no-draft          Force Azure DevOps PR creation as ready-for-review.
  --provider <name>   PR provider: auto, github, azure_devops. Default: auto.
  --project <name>    Azure DevOps project override for PR/review API operations.
  --base <branch>     Base branch for the PR. Default: repository default branch.
  --session-id <id>   Session id for mapping metadata. Defaults to branch-derived value.
  --work-item-id <id> Explicit Azure DevOps work item id to associate with the PR.
  --title <title>     Explicit PR title.
  --body-file <path>  Read PR body from a file.
  --body-artifact-id <id>
                      Attach a staged communication artifact to the Azure DevOps PR body.
                      May be passed multiple times.
  --no-fill           Do not pass --fill to gh pr create.
  -h, --help          Show this help text.

Configuration:
  Azure DevOps PR creation requires AZURE_DEVOPS_PR_PAT (or the value configured in
  EVANDER_AZURE_DEVOPS_PR_PAT_ENV).
  Directory-scoped ado_worker config may set create_prs_as_draft=true so new Azure DevOps
  PRs open as drafts for that session's directory.
  EVANDER_CREATE_PRS_AS_DRAFT can force draft behavior globally for this invocation.
  If a PR is created and a session id is available, the script also attaches the
  PR feedback stream to that session through evander-cli.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

CREATE_PR=0
BASE_BRANCH=""
TITLE=""
TITLE_EXPLICIT=0
BODY_FILE=""
BODY_ARTIFACT_IDS=()
USE_FILL=1
PROVIDER="auto"
PROJECT_OVERRIDE=""
SESSION_ID=""
WORK_ITEM_ID=""
DRAFT_OVERRIDE=""
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
    --create-pr)
      CREATE_PR=1
      shift
      ;;
    --draft)
      DRAFT_OVERRIDE="1"
      shift
      ;;
    --no-draft)
      DRAFT_OVERRIDE="0"
      shift
      ;;
    --provider)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --provider" >&2
        exit 1
      fi
      PROVIDER="$2"
      shift 2
      ;;
    --project)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --project" >&2
        exit 1
      fi
      PROJECT_OVERRIDE="$2"
      shift 2
      ;;
    --base)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --base" >&2
        exit 1
      fi
      BASE_BRANCH="$2"
      shift 2
      ;;
    --session-id)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --session-id" >&2
        exit 1
      fi
      SESSION_ID="$2"
      shift 2
      ;;
    --work-item-id)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --work-item-id" >&2
        exit 1
      fi
      WORK_ITEM_ID="$2"
      shift 2
      ;;
    --title)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --title" >&2
        exit 1
      fi
      TITLE="$2"
      TITLE_EXPLICIT=1
      shift 2
      ;;
    --body-file)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --body-file" >&2
        exit 1
      fi
      BODY_FILE="$2"
      shift 2
      ;;
    --body-artifact-id)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --body-artifact-id" >&2
        exit 1
      fi
      BODY_ARTIFACT_IDS+=("$2")
      shift 2
      ;;
    --no-fill)
      USE_FILL=0
      shift
      ;;
    --*)
      echo "Unknown option: $1" >&2
      show_help >&2
      exit 1
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

set -- "${POSITIONAL[@]}"

if [[ -n "${EVANDER_PR_BODY_ARTIFACT_IDS:-}" ]]; then
  while IFS= read -r artifact_id; do
    if [[ -n "${artifact_id}" ]]; then
      BODY_ARTIFACT_IDS+=("${artifact_id}")
    fi
  done < <(printf '%s\n' "${EVANDER_PR_BODY_ARTIFACT_IDS}" | tr ',;' '\n')
fi

if [[ $# -lt 1 || $# -gt 3 ]]; then
  show_help >&2
  exit 1
fi

HOST_REPO_PATH="$1"
LOCAL_BRANCH="${2:-}"
ORIGIN_BRANCH="${3:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/runtime_config.sh
source "${SCRIPT_DIR}/lib/runtime_config.sh"
load_root_secrets_env
load_provider_config

if [[ -z "${HOME:-}" ]]; then
  CURRENT_USER="$(id -un)"
  HOME_FROM_PASSWD="$(getent passwd "${CURRENT_USER}" | cut -d: -f6)"
  if [[ -n "${HOME_FROM_PASSWD}" ]]; then
    export HOME="${HOME_FROM_PASSWD}"
  fi
fi

AZURE_PR_PAT_ENV="${EVANDER_AZURE_DEVOPS_PR_PAT_ENV:-AZURE_DEVOPS_PR_PAT}"

if [[ ! -d "${HOST_REPO_PATH}/.git" ]]; then
  echo "Host repo path is not a Git working tree: ${HOST_REPO_PATH}" >&2
  exit 1
fi

if [[ -z "${LOCAL_BRANCH}" ]]; then
  LOCAL_BRANCH="$(git -C "${HOST_REPO_PATH}" branch --show-current)"
fi

if [[ -z "${LOCAL_BRANCH}" ]]; then
  echo "Could not determine the current branch. Pass [local-branch] explicitly." >&2
  exit 1
fi

derive_session_id() {
  local branch="$1"
  if [[ "${branch}" == agents/*/* || "${branch}" == review/*/* ]]; then
    local remainder="${branch#*/}"
    printf '%s\n' "${remainder%%/*}"
    return 0
  fi
  return 1
}

extract_issue_number() {
  local value="${1:-}"
  [[ -n "${value}" ]] || return 1
  if [[ "${value}" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "${value}"
    return 0
  fi
  if [[ "${value}" =~ ^vm-([0-9]+)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "${value}" =~ ^issue-([0-9]+)(-|$) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

derive_origin_branch() {
  local explicit_origin_branch="$1"
  local local_branch="$2"
  local session_id="$3"
  local work_item_id="$4"
  local issue_number=""

  if [[ -n "${explicit_origin_branch}" ]]; then
    printf '%s\n' "${explicit_origin_branch}"
    return 0
  fi

  if [[ -n "${work_item_id}" ]]; then
    issue_number="$(extract_issue_number "${work_item_id}" || true)"
  fi

  if [[ -z "${issue_number}" && -n "${session_id}" ]]; then
    issue_number="$(extract_issue_number "${session_id}" || true)"
  fi

  if [[ -n "${issue_number}" ]]; then
    printf 'feature/issue-%s\n' "${issue_number}"
    return 0
  fi

  printf '%s\n' "${local_branch}"
}

infer_provider_from_url() {
  local origin_url="$1"
  case "${origin_url}" in
    git@github.com:*|https://github.com/*)
      printf 'github\n'
      ;;
    git@ssh.dev.azure.com:*|https://dev.azure.com/*|https://*.visualstudio.com/*)
      printf 'azure_devops\n'
      ;;
    *)
      printf '\n'
      ;;
  esac
}

ensure_remote_url() {
  local repo_path="$1"
  local remote_name="$2"
  local remote_url="$3"

  if git -C "${repo_path}" remote get-url "${remote_name}" >/dev/null 2>&1; then
    local current_url
    current_url="$(git -C "${repo_path}" remote get-url "${remote_name}")"
    if [[ "${current_url}" != "${remote_url}" ]]; then
      git -C "${repo_path}" remote set-url "${remote_name}" "${remote_url}"
    fi
  else
    git -C "${repo_path}" remote add "${remote_name}" "${remote_url}"
  fi
}

git_with_push_auth() {
  local repo_path="$1"
  shift

  if [[ "${PUSH_PROVIDER}" == "azure_devops" && -n "${AZURE_PAT_ENV_VALUE}" ]]; then
    git -C "${repo_path}" -c http.extraHeader="${AZURE_AUTH_HEADER}" "$@"
  elif [[ "${PUSH_PROVIDER}" == "github" && "${USE_GH_GIT_CREDENTIAL_HELPER:-0}" == "1" ]]; then
    git -C "${repo_path}" -c credential.https://github.com.helper='!/usr/bin/gh auth git-credential' "$@"
  else
    git -C "${repo_path}" "$@"
  fi
}

remote_url_or_empty() {
  local repo_path="$1"
  local remote_name="$2"
  git -C "${repo_path}" remote get-url "${remote_name}" 2>/dev/null || true
}

refresh_remote_branch_tracking_ref() {
  local repo_path="$1"
  local remote_name="$2"
  local branch_name="$3"

  if git_with_push_auth "${repo_path}" ls-remote --exit-code "${remote_name}" "refs/heads/${branch_name}" >/dev/null 2>&1; then
    git_with_push_auth "${repo_path}" fetch "${remote_name}" \
      "refs/heads/${branch_name}:refs/remotes/${remote_name}/${branch_name}" >/dev/null
  fi
}

emit_preflight_result() {
  local reason="$1"
  local message="$2"
  local destination_commit="${3:-}"
  local local_commit="${4:-}"
  local destination_ahead_count="${5:-0}"
  local local_ahead_count="${6:-0}"
  local destination_ref="refs/heads/${ORIGIN_BRANCH}"
  local destination_tracking_ref="refs/remotes/${PR_REMOTE_NAME}/${ORIGIN_BRANCH}"

  REASON="${reason}" \
  MESSAGE="${message}" \
  LOCAL_BRANCH_VALUE="${LOCAL_BRANCH}" \
  ORIGIN_BRANCH_VALUE="${ORIGIN_BRANCH}" \
  BASE_BRANCH_VALUE="${BASE_BRANCH}" \
  SESSION_ID_VALUE="${SESSION_ID}" \
  WORK_ITEM_ID_VALUE="${WORK_ITEM_ID}" \
  PR_REMOTE_NAME_VALUE="${PR_REMOTE_NAME}" \
  DESTINATION_REF_VALUE="${destination_ref}" \
  DESTINATION_TRACKING_REF_VALUE="${destination_tracking_ref}" \
  DESTINATION_COMMIT_VALUE="${destination_commit}" \
  LOCAL_COMMIT_VALUE="${local_commit}" \
  DESTINATION_AHEAD_COUNT_VALUE="${destination_ahead_count}" \
  LOCAL_AHEAD_COUNT_VALUE="${local_ahead_count}" \
  python3 - <<'PY'
import json
import os

def int_value(name: str) -> int:
    try:
        return int(os.environ.get(name, "0") or "0")
    except ValueError:
        return 0

payload = {
    "success": False,
    "action": "pr_create",
    "preflight_success": False,
    "reason": os.environ["REASON"],
    "message": os.environ["MESSAGE"],
    "local_branch": os.environ["LOCAL_BRANCH_VALUE"],
    "origin_branch": os.environ["ORIGIN_BRANCH_VALUE"],
    "base_branch": os.environ["BASE_BRANCH_VALUE"],
    "session_id": os.environ["SESSION_ID_VALUE"],
    "work_item_id": os.environ["WORK_ITEM_ID_VALUE"],
    "remote_name": os.environ["PR_REMOTE_NAME_VALUE"],
    "destination_ref": os.environ["DESTINATION_REF_VALUE"],
    "destination_tracking_ref": os.environ["DESTINATION_TRACKING_REF_VALUE"],
    "destination_commit": os.environ["DESTINATION_COMMIT_VALUE"],
    "local_commit": os.environ["LOCAL_COMMIT_VALUE"],
    "destination_ahead_count": int_value("DESTINATION_AHEAD_COUNT_VALUE"),
    "local_ahead_count": int_value("LOCAL_AHEAD_COUNT_VALUE"),
}
print(f"AGENT_PR_RESULT {json.dumps(payload, sort_keys=True)}")
PY
}

preflight_destination_branch() {
  local repo_path="$1"
  local remote_name="$2"
  local branch_name="$3"
  local ls_output fetch_output merge_base_status counts destination_ahead_count local_ahead_count
  local destination_tracking_ref="refs/remotes/${remote_name}/${branch_name}"
  local destination_commit=""
  local local_commit=""

  if ls_output="$(git_with_push_auth "${repo_path}" ls-remote --exit-code "${remote_name}" "refs/heads/${branch_name}" 2>&1)"; then
    :
  else
    local ls_status=$?
    if [[ "${ls_status}" -eq 2 ]]; then
      return 0
    fi
    emit_preflight_result \
      "preflight_failed" \
      "Could not verify destination branch ${remote_name}/${branch_name}: ${ls_output}" \
      "" \
      "$(git -C "${repo_path}" rev-parse --verify "${LOCAL_BRANCH}^{commit}" 2>/dev/null || true)" \
      0 \
      0
    exit 0
  fi

  if ! fetch_output="$(git_with_push_auth "${repo_path}" fetch "${remote_name}" "+refs/heads/${branch_name}:${destination_tracking_ref}" 2>&1)"; then
    emit_preflight_result \
      "preflight_failed" \
      "Could not refresh destination branch ${remote_name}/${branch_name}: ${fetch_output}" \
      "" \
      "$(git -C "${repo_path}" rev-parse --verify "${LOCAL_BRANCH}^{commit}" 2>/dev/null || true)" \
      0 \
      0
    exit 0
  fi

  destination_commit="$(git -C "${repo_path}" rev-parse --verify "${destination_tracking_ref}^{commit}" 2>/dev/null || true)"
  local_commit="$(git -C "${repo_path}" rev-parse --verify "${LOCAL_BRANCH}^{commit}" 2>/dev/null || true)"

  if git -C "${repo_path}" merge-base --is-ancestor "${destination_tracking_ref}" "${LOCAL_BRANCH}" >/dev/null 2>&1; then
    return 0
  else
    merge_base_status=$?
  fi
  if [[ "${merge_base_status}" -ne 1 ]]; then
    emit_preflight_result \
      "preflight_failed" \
      "Could not compare ${LOCAL_BRANCH} with ${remote_name}/${branch_name}." \
      "${destination_commit}" \
      "${local_commit}" \
      0 \
      0
    exit 0
  fi

  counts="$(git -C "${repo_path}" rev-list --left-right --count "${destination_tracking_ref}...${LOCAL_BRANCH}" 2>/dev/null || true)"
  destination_ahead_count="$(printf '%s\n' "${counts}" | awk '{print $1}')"
  local_ahead_count="$(printf '%s\n' "${counts}" | awk '{print $2}')"
  destination_ahead_count="${destination_ahead_count:-0}"
  local_ahead_count="${local_ahead_count:-0}"
  emit_preflight_result \
    "destination_branch_ahead" \
    "Destination branch ${remote_name}/${branch_name} has commits that are not contained in ${LOCAL_BRANCH}; merge the destination branch before pushing." \
    "${destination_commit}" \
    "${local_commit}" \
    "${destination_ahead_count}" \
    "${local_ahead_count}"
  exit 0
}

resolve_create_prs_as_draft() {
  local session_id="$1"
  local draft_override="${2:-}"
  local runtime_dir="${EVANDER_RUNTIME_DIR:-${ROOT_DIR}/runtime}"
  local env_override="${EVANDER_CREATE_PRS_AS_DRAFT:-}"

  RUNTIME_DIR_PATH="${runtime_dir}" \
  SESSION_ID_VALUE="${session_id}" \
  DRAFT_OVERRIDE_VALUE="${draft_override}" \
  DRAFT_OVERRIDE_ENV_VALUE="${env_override}" \
  python3 - <<'PY'
import json
import os
from pathlib import Path, PurePosixPath

runtime_dir = Path(os.environ["RUNTIME_DIR_PATH"])
session_id = os.environ.get("SESSION_ID_VALUE", "").strip()
draft_override = os.environ.get("DRAFT_OVERRIDE_VALUE", "").strip()
env_override = os.environ.get("DRAFT_OVERRIDE_ENV_VALUE", "").strip()
directory_path = "root"
session_metadata: dict[str, object] = {}
sessions_path = runtime_dir / "sessions.json"

if session_id and sessions_path.exists():
    try:
        sessions_payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sessions_payload = {}
    if isinstance(sessions_payload, dict):
        session_payload = sessions_payload.get(session_id)
        if isinstance(session_payload, dict):
            candidate_directory_path = str(session_payload.get("directory_path") or "").strip()
            if candidate_directory_path:
                directory_path = candidate_directory_path
            raw_metadata = session_payload.get("metadata")
            if isinstance(raw_metadata, dict):
                session_metadata = raw_metadata

segments = [segment for segment in PurePosixPath(directory_path).parts if segment not in {"", "."}]
if not segments or segments[0] != "root":
    segments = ["root"]

create_prs_as_draft = False

for override in (draft_override, env_override):
    if not override:
        continue
    normalized = override.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        print("1", end="")
        raise SystemExit(0)
    if normalized in {"0", "false", "no", "off", ""}:
        print("0", end="")
        raise SystemExit(0)

directory_config_root = runtime_dir / "directory-config"
for index in range(1, len(segments) + 1):
    candidate = directory_config_root.joinpath(*segments[:index], "ado-worker.json")
    if not candidate.exists():
        continue
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    if not isinstance(payload, dict) or "create_prs_as_draft" not in payload:
        continue
    value = payload.get("create_prs_as_draft")
    if isinstance(value, bool):
        create_prs_as_draft = value
        continue
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            create_prs_as_draft = True
        elif normalized in {"0", "false", "no", "off", ""}:
            create_prs_as_draft = False

def parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return None

session_draft_value = parse_bool(session_metadata.get("create_prs_as_draft"))
if session_draft_value is not None:
    create_prs_as_draft = session_draft_value
automation_payload = session_metadata.get("pr_automation")
if isinstance(automation_payload, dict):
    nested_draft = parse_bool(automation_payload.get("create_prs_as_draft"))
    if nested_draft is not None:
        create_prs_as_draft = nested_draft

print("1" if create_prs_as_draft else "0", end="")
PY
}

azure_https_origin_url() {
  local origin_url="$1"
  ORIGIN_URL_INPUT="${origin_url}" python3 - <<'PY'
import os
import re
import urllib.parse

origin_url = os.environ["ORIGIN_URL_INPUT"]
ssh_match = re.match(r"^git@ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/]+)$", origin_url)
https_match = re.match(
    r"^https://(?:[^@/]+@)?dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/]+)$",
    origin_url,
)
vs_match = re.match(
    r"^https://(?:[^@/]+@)?([^.]+)\.visualstudio\.com/([^/]+)/_git/([^/]+)$",
    origin_url,
)

match = ssh_match or https_match or vs_match
if not match:
    print("", end="")
else:
    organization, project, repo = match.groups()
    organization = urllib.parse.unquote(organization)
    project = urllib.parse.unquote(project)
    repo = urllib.parse.unquote(repo)
    print(
        "https://dev.azure.com/"
        f"{urllib.parse.quote(organization, safe='')}/"
        f"{urllib.parse.quote(project, safe='')}/_git/"
        f"{urllib.parse.quote(repo, safe='')}",
        end="",
    )
PY
}

github_https_origin_url() {
  local origin_url="$1"
  ORIGIN_URL_INPUT="${origin_url}" python3 - <<'PY'
import os
import re

origin_url = os.environ["ORIGIN_URL_INPUT"]
ssh_match = re.match(r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$", origin_url)
https_match = re.match(r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?$", origin_url)

match = ssh_match or https_match
if not match:
    print("", end="")
else:
    print(f"https://github.com/{match.group(1)}.git", end="")
PY
}

github_auth_available() {
  command -v gh >/dev/null 2>&1 || return 1
  gh auth status >/dev/null 2>&1
}

infer_repo_slug() {
  local origin_url="$1"
  local provider="$2"
  local project_override="${3:-}"
  case "${provider}" in
    github)
      local trimmed="${origin_url%.git}"
      trimmed="${trimmed#git@github.com:}"
      trimmed="${trimmed#https://github.com/}"
      printf '%s\n' "${trimmed}"
      ;;
    azure_devops)
      ORIGIN_URL="${origin_url}" PROJECT_OVERRIDE="${project_override}" python3 - <<'PY'
import os
import re
import urllib.parse

origin_url = os.environ["ORIGIN_URL"]
project_override = os.environ.get("PROJECT_OVERRIDE", "").strip()
ssh_match = re.match(r"^git@ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/]+)$", origin_url)
https_match = re.match(
    r"^https://(?:[^@/]+@)?dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/]+)$",
    origin_url,
)
vs_match = re.match(
    r"^https://(?:[^@/]+@)?([^.]+)\.visualstudio\.com/([^/]+)/_git/([^/]+)$",
    origin_url,
)

match = ssh_match or https_match or vs_match
if not match:
    print("", end="")
else:
    organization, project, repo = match.groups()
    organization = urllib.parse.unquote(organization)
    project = project_override or urllib.parse.unquote(project)
    repo = urllib.parse.unquote(repo)
    print(f"{organization}/{project}/{repo}", end="")
PY
      ;;
    *)
      printf '%s\n' ""
      ;;
  esac
}

attach_pr_feedback_stream() {
  local cli_path="${EVANDER_CLI_BIN:-${EVANDER_CLI_BIN:-}}"
  local session_id="$1"
  local provider="$2"
  local repo_slug="$3"
  local pr_id="$4"

  if [[ -z "${session_id}" || -z "${provider}" || -z "${repo_slug}" || -z "${pr_id}" ]]; then
    return 0
  fi

  if [[ -z "${cli_path}" ]]; then
    if [[ -x "${ROOT_DIR}/.venv/bin/evander-cli" ]]; then
      cli_path="${ROOT_DIR}/.venv/bin/evander-cli"
    elif [[ -x "${ROOT_DIR}/.venv/bin/evander-cli" ]]; then
      cli_path="${ROOT_DIR}/.venv/bin/evander-cli"
    else
      cli_path="evander-cli"
    fi
  fi

  "${cli_path}" attach-pr-feedback \
    --runtime-dir "${ROOT_DIR}/runtime" \
    --provider "${provider}" \
    --repo "${repo_slug}" \
    --pr-id "${pr_id}" \
    "${session_id}"
}

write_pr_mapping() {
  local map_path="$1"
  local provider="$2"
  local repo_slug="$3"
  local pr_id="$4"
  local pr_url="$5"
  local origin_branch="$6"
  local base_branch="$7"
  local session_id="$8"
  local local_branch="$9"

  mkdir -p "$(dirname "${map_path}")"
  MAP_PATH="${map_path}" \
  PROVIDER="${provider}" \
  REPO_SLUG="${repo_slug}" \
  PR_ID="${pr_id}" \
  PR_URL="${pr_url}" \
  ORIGIN_BRANCH="${origin_branch}" \
  BASE_BRANCH="${base_branch}" \
  SESSION_ID="${session_id}" \
  LOCAL_BRANCH="${local_branch}" \
  python3 - <<'PY'
import fcntl
import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["MAP_PATH"])
path.parent.mkdir(parents=True, exist_ok=True)
lock_path = path.with_name(f".{path.name}.lock")


def _load_payload() -> dict:
    if not path.exists():
        return {"version": 1, "entries": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {"version": 1, "entries": {}}


def _umask_respecting_default_mode() -> int:
    current_umask = os.umask(0)
    try:
        return 0o666 & ~current_umask
    finally:
        os.umask(current_umask)


def _write_payload(payload: dict) -> None:
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    owner_source = path if path.exists() else path.parent
    owner_stat = owner_source.stat()
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else _umask_respecting_default_mode()
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        if os.geteuid() == 0:
            os.chown(temp_path, owner_stat.st_uid, owner_stat.st_gid)
        os.replace(temp_path, path)
        dir_fd = os.open(path.parent, getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()


with lock_path.open("a+", encoding="utf-8") as lock_file:
    if os.geteuid() == 0:
        owner_stat = (path if path.exists() else path.parent).stat()
        os.fchown(lock_file.fileno(), owner_stat.st_uid, owner_stat.st_gid)
    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
    try:
        payload = _load_payload()
        entries = payload.setdefault("entries", {})
        timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        provider = os.environ["PROVIDER"]
        repo_slug = os.environ["REPO_SLUG"]
        pr_id = os.environ["PR_ID"]
        key = f"{provider}:{repo_slug}:{pr_id}"
        entries[key] = {
            "provider": provider,
            "repo": repo_slug,
            "pr_id": pr_id,
            "pr_url": os.environ["PR_URL"],
            "origin_branch": os.environ["ORIGIN_BRANCH"],
            "base_branch": os.environ["BASE_BRANCH"],
            "session_id": os.environ["SESSION_ID"],
            "local_branch": os.environ["LOCAL_BRANCH"],
            "updated_at": timestamp,
        }
        payload["updated_at"] = timestamp
        _write_payload(payload)
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
PY
}

if [[ -z "${SESSION_ID}" ]]; then
  SESSION_ID="$(derive_session_id "${LOCAL_BRANCH}" || true)"
fi

if [[ -z "${WORK_ITEM_ID}" && -n "${SESSION_ID}" ]]; then
  WORK_ITEM_ID="$(extract_issue_number "${SESSION_ID}" || true)"
fi

if [[ -z "${ORIGIN_BRANCH}" ]]; then
  ORIGIN_BRANCH="$(derive_origin_branch "${ORIGIN_BRANCH}" "${LOCAL_BRANCH}" "${SESSION_ID}" "${WORK_ITEM_ID}")"
fi

git -C "${HOST_REPO_PATH}" rev-parse --verify "${LOCAL_BRANCH}^{commit}" >/dev/null
PR_REMOTE_NAME="origin"
ORIGIN_URL="${EVANDER_CANONICAL_ORIGIN_URL:-}"
if [[ -n "${ORIGIN_URL}" ]]; then
  PR_REMOTE_NAME="evander-pr-origin"
else
  ORIGIN_URL="$(git -C "${HOST_REPO_PATH}" remote get-url origin)"
fi
PUSH_PROVIDER="${PROVIDER}"
if [[ "${PUSH_PROVIDER}" == "auto" ]]; then
  PUSH_PROVIDER="$(infer_provider_from_url "${ORIGIN_URL}")"
fi

PUSH_REMOTE_URL="${ORIGIN_URL}"
AZURE_PAT_ENV_VALUE=""
AZURE_AUTH_HEADER=""
USE_GH_GIT_CREDENTIAL_HELPER=0
if [[ "${PUSH_PROVIDER}" == "azure_devops" ]]; then
  AZURE_PAT_ENV_VALUE="${!AZURE_PR_PAT_ENV:-}"
  if [[ -n "${AZURE_PAT_ENV_VALUE}" ]]; then
    HTTPS_ORIGIN_URL="$(azure_https_origin_url "${ORIGIN_URL}")"
    if [[ -n "${HTTPS_ORIGIN_URL}" ]]; then
      PUSH_REMOTE_URL="${HTTPS_ORIGIN_URL}"
      if [[ "${PR_REMOTE_NAME}" == "origin" ]]; then
        EXISTING_PR_REMOTE_URL="$(remote_url_or_empty "${HOST_REPO_PATH}" "evander-pr-origin")"
        if [[ "${EXISTING_PR_REMOTE_URL}" == "${HTTPS_ORIGIN_URL}" ]]; then
          PR_REMOTE_NAME="evander-pr-origin"
        fi
      fi
    fi
    AZURE_AUTH_HEADER="Authorization: Basic $(printf ':%s' "${AZURE_PAT_ENV_VALUE}" | base64 | tr -d '\n')"
  fi
elif [[ "${PUSH_PROVIDER}" == "github" ]]; then
  if github_auth_available; then
    HTTPS_ORIGIN_URL="$(github_https_origin_url "${ORIGIN_URL}")"
    if [[ -n "${HTTPS_ORIGIN_URL}" ]]; then
      PUSH_REMOTE_URL="${HTTPS_ORIGIN_URL}"
      USE_GH_GIT_CREDENTIAL_HELPER=1
    fi
  fi
fi

if [[ "${PR_REMOTE_NAME}" != "origin" ]]; then
  ensure_remote_url "${HOST_REPO_PATH}" "${PR_REMOTE_NAME}" "${PUSH_REMOTE_URL}"
fi

preflight_destination_branch "${HOST_REPO_PATH}" "${PR_REMOTE_NAME}" "${ORIGIN_BRANCH}"

PUSH_ARGS=()
if [[ "${ORIGIN_BRANCH}" == review/* ]]; then
  refresh_remote_branch_tracking_ref "${HOST_REPO_PATH}" "${PR_REMOTE_NAME}" "${ORIGIN_BRANCH}"
  PUSH_ARGS+=(--force-with-lease)
fi

git_with_push_auth "${HOST_REPO_PATH}" push "${PUSH_ARGS[@]}" "${PR_REMOTE_NAME}" "${LOCAL_BRANCH}:refs/heads/${ORIGIN_BRANCH}"

echo "Pushed ${LOCAL_BRANCH} to ${PR_REMOTE_NAME}/${ORIGIN_BRANCH}"

if [[ "${CREATE_PR}" -eq 0 ]]; then
  echo
  echo "Open a PR manually or rerun with --create-pr."
  exit 0
fi

if [[ -n "${BODY_FILE}" && ! -f "${BODY_FILE}" ]]; then
  echo "PR body file not found: ${BODY_FILE}" >&2
  exit 1
fi

if [[ -z "${BASE_BRANCH}" ]]; then
  BASE_BRANCH="$(
    git -C "${HOST_REPO_PATH}" symbolic-ref --quiet --short "refs/remotes/${PR_REMOTE_NAME}/HEAD" 2>/dev/null \
      | sed "s#^${PR_REMOTE_NAME}/##"
  )"
fi

if [[ -z "${BASE_BRANCH}" ]]; then
  echo "Could not determine ${PR_REMOTE_NAME} default branch. Pass --base explicitly." >&2
  exit 1
fi

if [[ -z "${TITLE}" ]]; then
  TITLE="$(git -C "${HOST_REPO_PATH}" log -1 --format=%s "${LOCAL_BRANCH}")"
fi

BODY_CONTENT=""
if [[ -n "${BODY_FILE}" ]]; then
  BODY_CONTENT="$(cat "${BODY_FILE}")"
else
  BODY_CONTENT="Automated review branch promotion for ${ORIGIN_BRANCH}."
fi

PR_MAP_PATH="${EVANDER_PR_MAP_PATH:-${ROOT_DIR}/runtime/pr-map.json}"

if [[ "${PROVIDER}" == "auto" ]]; then
  PROVIDER="$(infer_provider_from_url "${ORIGIN_URL}")"
  if [[ -z "${PROVIDER}" ]]; then
    echo "Could not infer PR provider from origin URL: ${ORIGIN_URL}" >&2
    echo "Pass --provider explicitly." >&2
    exit 1
  fi
fi

if [[ -z "${SESSION_ID}" ]]; then
  SESSION_ID="$(derive_session_id "${ORIGIN_BRANCH}" || derive_session_id "${LOCAL_BRANCH}" || true)"
fi

if [[ -z "${WORK_ITEM_ID}" && -n "${SESSION_ID}" ]]; then
  WORK_ITEM_ID="$(extract_issue_number "${SESSION_ID}" || true)"
fi

CREATE_PRS_AS_DRAFT="$(resolve_create_prs_as_draft "${SESSION_ID}" "${DRAFT_OVERRIDE}")"
if [[ -n "${WORK_ITEM_ID}" && ! "${WORK_ITEM_ID}" =~ ^[0-9]+$ ]]; then
  echo "Work item id must be numeric: ${WORK_ITEM_ID}" >&2
  exit 1
fi

REPO_SLUG="$(infer_repo_slug "${ORIGIN_URL}" "${PROVIDER}" "${PROJECT_OVERRIDE}")"
if [[ -z "${REPO_SLUG}" ]]; then
  REPO_SLUG="$(basename "${HOST_REPO_PATH}")"
fi

if [[ "${#BODY_ARTIFACT_IDS[@]}" -gt 0 && "${PROVIDER}" != "azure_devops" ]]; then
  echo "--body-artifact-id is currently supported only for --provider azure_devops." >&2
  exit 1
fi

PR_URL=""
PR_ID=""
PR_UPLOADED_ATTACHMENTS_JSON="[]"
BODY_ARTIFACT_IDS_TEXT=""
if [[ "${#BODY_ARTIFACT_IDS[@]}" -gt 0 ]]; then
  BODY_ARTIFACT_IDS_TEXT="$(printf '%s\n' "${BODY_ARTIFACT_IDS[@]}")"
fi

case "${PROVIDER}" in
  github)
    if ! command -v gh >/dev/null 2>&1; then
      echo "GitHub CLI (gh) is required for --provider github." >&2
      exit 1
    fi

    if [[ -z "${BODY_FILE}" && "${USE_FILL}" -eq 1 ]]; then
      refresh_remote_branch_tracking_ref "${HOST_REPO_PATH}" "${PR_REMOTE_NAME}" "${BASE_BRANCH}"
    fi

    GH_ARGS=(pr create --repo "${ORIGIN_URL}" --base "${BASE_BRANCH}" --head "${ORIGIN_BRANCH}")

    if [[ -n "${TITLE}" ]]; then
      GH_ARGS+=(--title "${TITLE}")
    fi

    if [[ -n "${BODY_FILE}" ]]; then
      GH_ARGS+=(--body-file "${BODY_FILE}")
    elif [[ "${USE_FILL}" -eq 1 ]]; then
      GH_ARGS+=(--fill)
    fi

    PR_URL="$(
      (
      cd "${HOST_REPO_PATH}"
      gh "${GH_ARGS[@]}"
      )
    )"
    PR_URL="$(printf '%s\n' "${PR_URL}" | tail -n 1)"
    PR_ID="$(printf '%s\n' "${PR_URL}" | sed -nE 's#.*/pull/([0-9]+)$#\1#p')"
    ;;
  azure_devops)
    AZURE_DEVOPS_PR_PAT_VALUE="${!AZURE_PR_PAT_ENV:-}"
    if [[ -z "${AZURE_DEVOPS_PR_PAT_VALUE}" ]]; then
      echo "Azure DevOps PR PAT is required for --provider azure_devops." >&2
      echo "Set ${AZURE_PR_PAT_ENV} in the environment or the root providers config." >&2
      exit 1
    fi

    AZURE_RESULT="$(
    ORIGIN_URL="${ORIGIN_URL}" \
    PROJECT_OVERRIDE="${PROJECT_OVERRIDE}" \
    AZURE_DEVOPS_PR_PAT_VALUE="${AZURE_DEVOPS_PR_PAT_VALUE}" \
    BASE_BRANCH="${BASE_BRANCH}" \
    ORIGIN_BRANCH="${ORIGIN_BRANCH}" \
    TITLE="${TITLE}" \
    TITLE_EXPLICIT="${TITLE_EXPLICIT}" \
    BODY_CONTENT="${BODY_CONTENT}" \
    PR_BODY_ARTIFACT_IDS="${BODY_ARTIFACT_IDS_TEXT}" \
    RUNTIME_DIR="${EVANDER_RUNTIME_DIR:-${ROOT_DIR}/runtime}" \
    SESSION_ID="${SESSION_ID}" \
    WORK_ITEM_ID="${WORK_ITEM_ID}" \
    CREATE_PRS_AS_DRAFT="${CREATE_PRS_AS_DRAFT}" \
    python3 - <<'PY'
import base64
import json
import mimetypes
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

origin_url = os.environ["ORIGIN_URL"]
project_override = os.environ.get("PROJECT_OVERRIDE", "").strip()
base_branch = os.environ["BASE_BRANCH"]
origin_branch = os.environ["ORIGIN_BRANCH"]
title = os.environ["TITLE"]
title_explicit = os.environ.get("TITLE_EXPLICIT", "0") == "1"
body = os.environ["BODY_CONTENT"]
body_artifact_ids = [
    item.strip()
    for item in os.environ.get("PR_BODY_ARTIFACT_IDS", "").splitlines()
    if item.strip()
]
runtime_dir = Path(os.environ.get("RUNTIME_DIR", "runtime"))
session_id = os.environ.get("SESSION_ID", "").strip()
pat = os.environ["AZURE_DEVOPS_PR_PAT_VALUE"]
work_item_id = os.environ.get("WORK_ITEM_ID", "").strip()
create_prs_as_draft = os.environ.get("CREATE_PRS_AS_DRAFT", "").strip() in {"1", "true", "yes", "on"}
azure_pr_description_max_chars = 4000

ssh_match = re.match(r"^git@ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/]+)$", origin_url)
https_match = re.match(
    r"^https://(?:[^@/]+@)?dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/]+)$",
    origin_url,
)
vs_match = re.match(
    r"^https://(?:[^@/]+@)?([^.]+)\.visualstudio\.com/([^/]+)/_git/([^/]+)$",
    origin_url,
)

if ssh_match:
    organization, project, repo = ssh_match.groups()
elif https_match:
    organization, project, repo = https_match.groups()
elif vs_match:
    organization, project, repo = vs_match.groups()
else:
    print(f"Unsupported Azure DevOps origin URL: {origin_url}", file=sys.stderr)
    sys.exit(1)

project = project_override or urllib.parse.unquote(project)
repo = urllib.parse.unquote(repo)

base_url = (
    f"https://dev.azure.com/{urllib.parse.quote(organization, safe='')}/"
    f"{urllib.parse.quote(project, safe='')}/_apis/git/repositories/"
    f"{urllib.parse.quote(repo, safe='')}/pullrequests"
)

auth_header = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
headers = {
    "Authorization": f"Basic {auth_header}",
    "Content-Type": "application/json",
}
attachment_upload_headers = {
    "Authorization": f"Basic {auth_header}",
    "Content-Type": "application/octet-stream",
}
artifact_id_re = re.compile(r"^comm_[0-9a-f]{32}$")


def request_json(url: str, *, method: str = "GET", payload: dict | None = None, content_type: str = "application/json") -> dict:
    data = None
    request_headers = dict(headers)
    if payload is not None:
      data = json.dumps(payload).encode("utf-8")
      request_headers["Content-Type"] = content_type
    request = urllib.request.Request(
        url,
        headers=request_headers,
        data=data,
        method=method,
    )
    with urllib.request.urlopen(request) as response:
        return json.load(response)


def safe_artifact_filename(value: object, fallback: str) -> str:
    raw = str(value or fallback).replace("\x00", "")
    name = Path(raw.replace("\\", "/")).name
    if not name or name in {".", ".."}:
        name = fallback
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-")[:255] or "attachment"


def load_body_artifacts() -> list[dict]:
    artifacts: list[dict] = []
    if not body_artifact_ids:
        return artifacts
    artifact_root = runtime_dir / "communication-artifacts"
    for artifact_id in body_artifact_ids:
        if not artifact_id_re.fullmatch(artifact_id):
            print(f"Invalid body artifact id: {artifact_id}", file=sys.stderr)
            sys.exit(1)
        metadata_path = artifact_root / f"{artifact_id}.json"
        blob_path = artifact_root / f"{artifact_id}.bin"
        if not metadata_path.is_file() or not blob_path.is_file():
            print(f"Unknown staged body artifact: {artifact_id}", file=sys.stderr)
            sys.exit(1)
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"Could not read staged body artifact metadata {artifact_id}: {exc}", file=sys.stderr)
            sys.exit(1)
        artifact_session_id = str(metadata.get("session_id") or "").strip()
        if session_id and artifact_session_id and artifact_session_id != session_id:
            print(
                f"Body artifact {artifact_id} is owned by session {artifact_session_id}, not {session_id}",
                file=sys.stderr,
            )
            sys.exit(1)
        filename = safe_artifact_filename(
            metadata.get("normalized_safe_filename")
            or metadata.get("filename")
            or metadata.get("original_filename"),
            f"{artifact_id}.bin",
        )
        mime_type = str(metadata.get("mime_type") or mimetypes.guess_type(filename)[0] or "")
        artifacts.append(
            {
                "artifact_id": artifact_id,
                "filename": filename,
                "mime_type": mime_type,
                "byte_count": int(metadata.get("byte_count") or blob_path.stat().st_size),
                "path": blob_path,
            }
        )
    return artifacts


def upload_pr_body_attachments(pr_id: str, artifacts: list[dict]) -> list[dict]:
    uploaded: list[dict] = []
    if not artifacts:
        return uploaded
    for artifact in artifacts:
        filename = artifact["filename"]
        upload_url = (
            f"{base_url}/{urllib.parse.quote(pr_id, safe='')}/attachments/"
            f"{urllib.parse.quote(filename, safe='')}?api-version=7.1"
        )
        request = urllib.request.Request(
            upload_url,
            headers=attachment_upload_headers,
            data=artifact["path"].read_bytes(),
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            payload = json.load(response)
        uploaded_url = str(payload.get("url") or upload_url.split("?", 1)[0])
        uploaded.append(
            {
                "artifact_id": artifact["artifact_id"],
                "filename": filename,
                "url": uploaded_url,
                "mime_type": artifact.get("mime_type") or None,
                "byte_count": artifact["byte_count"],
            }
        )
    return uploaded


def append_markdown_attachment_links(text: str, attachments: list[dict]) -> str:
    if not attachments:
        return text
    lines: list[str] = []
    for attachment in attachments:
        filename = str(attachment.get("filename") or "attachment")
        url = str(attachment.get("url") or "")
        if not url:
            continue
        label = filename.replace("[", "\\[").replace("]", "\\]")
        mime_type = str(attachment.get("mime_type") or "")
        if mime_type.startswith("image/"):
            lines.append(f"![{label}]({url})")
        else:
            lines.append(f"[{label}]({url})")
    if not lines:
        return text
    if text.strip():
        return f"{text.rstrip()}\n\n" + "\n".join(lines)
    return "\n".join(lines)


def truncate_pr_description(text: str) -> str:
    if len(text) <= azure_pr_description_max_chars:
        return text
    print(
        f"Warning: Azure DevOps PR description truncated from {len(text)} "
        f"to {azure_pr_description_max_chars} characters.",
        file=sys.stderr,
    )
    return text[:azure_pr_description_max_chars]


body_artifacts = load_body_artifacts()


def ensure_work_item_link(pr: dict) -> None:
    if not work_item_id:
        return
    repository = pr.get("repository") or {}
    repository_id = str(repository.get("id") or "").strip()
    project_info = repository.get("project") or {}
    project_id = str(project_info.get("id") or "").strip()
    pr_id = str(pr.get("pullRequestId") or "").strip()
    if not repository_id or not project_id or not pr_id:
        return

    artifact_url = f"vstfs:///Git/PullRequestId/{project_id}%2F{repository_id}%2F{pr_id}"
    work_item_url = (
        f"https://dev.azure.com/{urllib.parse.quote(organization, safe='')}/"
        f"{urllib.parse.quote(project, safe='')}/_apis/wit/workitems/{urllib.parse.quote(work_item_id, safe='')}"
    )
    try:
        work_item = request_json(f"{work_item_url}?$expand=relations&api-version=7.1")
        for relation in work_item.get("relations") or []:
            if relation.get("rel") == "ArtifactLink" and relation.get("url") == artifact_url:
                return

        patch_doc = [
            {
                "op": "add",
                "path": "/relations/-",
                "value": {
                    "rel": "ArtifactLink",
                    "url": artifact_url,
                    "attributes": {"name": "Pull Request"},
                },
            }
        ]
        request = urllib.request.Request(
            f"{work_item_url}?api-version=7.1",
            headers={**headers, "Content-Type": "application/json-patch+json"},
            data=json.dumps(patch_doc).encode("utf-8"),
            method="PATCH",
        )
        with urllib.request.urlopen(request) as response:
            json.load(response)
    except Exception as exc:
        print(
            f"Warning: failed to associate Azure DevOps PR {pr_id} with work item {work_item_id}: {exc}",
            file=sys.stderr,
        )

query = urllib.parse.urlencode(
    {
        "searchCriteria.status": "active",
        "searchCriteria.sourceRefName": f"refs/heads/{origin_branch}",
        "searchCriteria.targetRefName": f"refs/heads/{base_branch}",
        "api-version": "7.1",
    }
)
payload = request_json(f"{base_url}?{query}")

if payload.get("count", 0):
    pr = payload["value"][0]
    pr_id = str(pr.get("pullRequestId", ""))
    uploaded_attachments = upload_pr_body_attachments(pr_id, body_artifacts)
    update_doc = {
        "description": truncate_pr_description(
            append_markdown_attachment_links(body, uploaded_attachments)
        )
    }
    if title_explicit:
        update_doc["title"] = title
    pr = request_json(
        f"{base_url}/{urllib.parse.quote(pr_id)}?api-version=7.1",
        payload=update_doc,
        method="PATCH",
    )
    ensure_work_item_link(pr)
    web = pr.get("_links", {}).get("web", {}).get("href") or pr.get("url")
    pr_id = str(pr.get("pullRequestId", ""))
    print(
        json.dumps(
            {
                "pr_url": web,
                "pr_id": pr_id,
                "uploaded_attachments": uploaded_attachments,
            },
            sort_keys=True,
        )
    )
    sys.exit(0)

pr = request_json(
    f"{base_url}?api-version=7.1",
    payload={
        "sourceRefName": f"refs/heads/{origin_branch}",
        "targetRefName": f"refs/heads/{base_branch}",
        "title": title,
        "description": truncate_pr_description(body),
        "isDraft": create_prs_as_draft,
    },
    method="POST",
)
pr_id = str(pr.get("pullRequestId") or "").strip()
uploaded_attachments = upload_pr_body_attachments(pr_id, body_artifacts)
if uploaded_attachments:
    pr = request_json(
        f"{base_url}/{urllib.parse.quote(pr_id)}?api-version=7.1",
        payload={
            "description": truncate_pr_description(
                append_markdown_attachment_links(body, uploaded_attachments)
            )
        },
        method="PATCH",
    )
ensure_work_item_link(pr)

web = pr.get("_links", {}).get("web", {}).get("href") or pr.get("url")
pr_id = str(pr.get("pullRequestId", ""))
print(
    json.dumps(
        {
            "pr_url": web,
            "pr_id": pr_id,
            "uploaded_attachments": uploaded_attachments,
        },
        sort_keys=True,
    )
)
PY
    )"
    PR_URL="$(
      AZURE_RESULT="${AZURE_RESULT}" python3 - <<'PY'
import json
import os
payload = json.loads(os.environ["AZURE_RESULT"])
print(payload.get("pr_url", ""), end="")
PY
    )"
    PR_ID="$(
      AZURE_RESULT="${AZURE_RESULT}" python3 - <<'PY'
import json
import os
payload = json.loads(os.environ["AZURE_RESULT"])
print(payload.get("pr_id", ""), end="")
PY
    )"
    PR_UPLOADED_ATTACHMENTS_JSON="$(
      AZURE_RESULT="${AZURE_RESULT}" python3 - <<'PY'
import json
import os
payload = json.loads(os.environ["AZURE_RESULT"])
print(json.dumps(payload.get("uploaded_attachments") or []), end="")
PY
    )"
    ;;
  *)
    echo "Unsupported provider: ${PROVIDER}" >&2
    exit 1
    ;;
esac

if [[ -z "${PR_ID}" ]]; then
  PR_ID="$(printf '%s\n' "${PR_URL}" | sed -nE 's#.*/pull/([0-9]+).*#\1#p')"
fi

if [[ -n "${PR_ID}" ]]; then
  write_pr_mapping \
    "${PR_MAP_PATH}" \
    "${PROVIDER}" \
    "${REPO_SLUG}" \
    "${PR_ID}" \
    "${PR_URL}" \
    "${ORIGIN_BRANCH}" \
    "${BASE_BRANCH}" \
    "${SESSION_ID}" \
    "${LOCAL_BRANCH}"

  if [[ -n "${SESSION_ID}" ]]; then
    attach_pr_feedback_stream "${SESSION_ID}" "${PROVIDER}" "${REPO_SLUG}" "${PR_ID}"
  fi
fi

RESULT_JSON="$(
  PROVIDER="${PROVIDER}" \
  PR_URL="${PR_URL}" \
  PR_ID="${PR_ID}" \
  ORIGIN_BRANCH="${ORIGIN_BRANCH}" \
  BASE_BRANCH="${BASE_BRANCH}" \
  SESSION_ID="${SESSION_ID}" \
  WORK_ITEM_ID="${WORK_ITEM_ID}" \
  PR_UPLOADED_ATTACHMENTS_JSON="${PR_UPLOADED_ATTACHMENTS_JSON}" \
  python3 - <<'PY'
import json
import os
uploaded_attachments = json.loads(os.environ.get("PR_UPLOADED_ATTACHMENTS_JSON") or "[]")
payload = {
    "provider": os.environ["PROVIDER"],
    "pr_url": os.environ["PR_URL"],
    "pr_id": os.environ["PR_ID"],
    "origin_branch": os.environ["ORIGIN_BRANCH"],
    "base_branch": os.environ["BASE_BRANCH"],
    "session_id": os.environ["SESSION_ID"],
    "work_item_id": os.environ["WORK_ITEM_ID"],
}
if uploaded_attachments:
    payload["uploaded_attachments"] = uploaded_attachments
print(
    json.dumps(payload, sort_keys=True)
)
PY
)"
echo "AGENT_PR_RESULT ${RESULT_JSON}"
