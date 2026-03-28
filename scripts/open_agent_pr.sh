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
  --provider <name>   PR provider: auto, github, azure_devops. Default: auto.
  --base <branch>     Base branch for the PR. Default: repository default branch.
  --session-id <id>   Session id for mapping metadata. Defaults to branch-derived value.
  --work-item-id <id> Explicit Azure DevOps work item id to associate with the PR.
  --title <title>     Explicit PR title.
  --body-file <path>  Read PR body from a file.
  --no-fill           Do not pass --fill to gh pr create.
  -h, --help          Show this help text.

Configuration:
  Azure DevOps PR creation requires AZURE_DEVOPS_PR_PAT (or the value configured in
  AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV).
  If a PR is created and a session id is available, the script also attaches the
  PR feedback stream to that session through agent-sandbox-cli.
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
USE_FILL=1
PROVIDER="auto"
SESSION_ID=""
WORK_ITEM_ID=""
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
    --provider)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --provider" >&2
        exit 1
      fi
      PROVIDER="$2"
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
load_provider_config

if [[ -z "${HOME:-}" ]]; then
  CURRENT_USER="$(id -un)"
  HOME_FROM_PASSWD="$(getent passwd "${CURRENT_USER}" | cut -d: -f6)"
  if [[ -n "${HOME_FROM_PASSWD}" ]]; then
    export HOME="${HOME_FROM_PASSWD}"
  fi
fi

AZURE_PR_PAT_ENV="${AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV:-AZURE_DEVOPS_PR_PAT}"

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

refresh_remote_branch_tracking_ref() {
  local repo_path="$1"
  local remote_name="$2"
  local branch_name="$3"

  if git_with_push_auth "${repo_path}" ls-remote --exit-code "${remote_name}" "refs/heads/${branch_name}" >/dev/null 2>&1; then
    git_with_push_auth "${repo_path}" fetch "${remote_name}" \
      "refs/heads/${branch_name}:refs/remotes/${remote_name}/${branch_name}" >/dev/null
  fi
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
  case "${provider}" in
    github)
      local trimmed="${origin_url%.git}"
      trimmed="${trimmed#git@github.com:}"
      trimmed="${trimmed#https://github.com/}"
      printf '%s\n' "${trimmed}"
      ;;
    azure_devops)
      ORIGIN_URL="${origin_url}" python3 - <<'PY'
import os
import re
import urllib.parse

origin_url = os.environ["ORIGIN_URL"]
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
    print(f"{organization}/{project}/{repo}", end="")
PY
      ;;
    *)
      printf '%s\n' ""
      ;;
  esac
}

attach_pr_feedback_stream() {
  local cli_path="${AGENT_SANDBOX_CLI_BIN:-}"
  local session_id="$1"
  local provider="$2"
  local repo_slug="$3"
  local pr_id="$4"

  if [[ -z "${session_id}" || -z "${provider}" || -z "${repo_slug}" || -z "${pr_id}" ]]; then
    return 0
  fi

  if [[ -z "${cli_path}" ]]; then
    if [[ -x "${ROOT_DIR}/.venv/bin/agent-sandbox-cli" ]]; then
      cli_path="${ROOT_DIR}/.venv/bin/agent-sandbox-cli"
    else
      cli_path="agent-sandbox-cli"
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
import json
import os
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["MAP_PATH"])
if path.exists():
    payload = json.loads(path.read_text(encoding="utf-8"))
else:
    payload = {"version": 1, "entries": {}}

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
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

if [[ -z "${ORIGIN_BRANCH}" ]]; then
  ORIGIN_BRANCH="${LOCAL_BRANCH}"
fi

git -C "${HOST_REPO_PATH}" rev-parse --verify "${LOCAL_BRANCH}^{commit}" >/dev/null
PR_REMOTE_NAME="origin"
ORIGIN_URL="${AGENT_SANDBOX_CANONICAL_ORIGIN_URL:-}"
if [[ -n "${ORIGIN_URL}" ]]; then
  PR_REMOTE_NAME="agent-sandbox-pr-origin"
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

PR_MAP_PATH="${AGENT_SANDBOX_PR_MAP_PATH:-${ROOT_DIR}/runtime/pr-map.json}"

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

if [[ -n "${WORK_ITEM_ID}" && ! "${WORK_ITEM_ID}" =~ ^[0-9]+$ ]]; then
  echo "Work item id must be numeric: ${WORK_ITEM_ID}" >&2
  exit 1
fi

REPO_SLUG="$(infer_repo_slug "${ORIGIN_URL}" "${PROVIDER}")"
if [[ -z "${REPO_SLUG}" ]]; then
  REPO_SLUG="$(basename "${HOST_REPO_PATH}")"
fi

PR_URL=""
PR_ID=""

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
    AZURE_DEVOPS_PR_PAT_VALUE="${AZURE_DEVOPS_PR_PAT_VALUE}" \
    BASE_BRANCH="${BASE_BRANCH}" \
    ORIGIN_BRANCH="${ORIGIN_BRANCH}" \
    TITLE="${TITLE}" \
    TITLE_EXPLICIT="${TITLE_EXPLICIT}" \
    BODY_CONTENT="${BODY_CONTENT}" \
    WORK_ITEM_ID="${WORK_ITEM_ID}" \
    python3 - <<'PY'
import base64
import json
import os
import re
import sys
import urllib.parse
import urllib.request

origin_url = os.environ["ORIGIN_URL"]
base_branch = os.environ["BASE_BRANCH"]
origin_branch = os.environ["ORIGIN_BRANCH"]
title = os.environ["TITLE"]
title_explicit = os.environ.get("TITLE_EXPLICIT", "0") == "1"
body = os.environ["BODY_CONTENT"]
pat = os.environ["AZURE_DEVOPS_PR_PAT_VALUE"]
work_item_id = os.environ.get("WORK_ITEM_ID", "").strip()

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

project = urllib.parse.unquote(project)
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
    update_doc = {"description": body}
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
    print(json.dumps({"pr_url": web, "pr_id": pr_id}, sort_keys=True))
    sys.exit(0)

pr = request_json(
    f"{base_url}?api-version=7.1",
    payload={
        "sourceRefName": f"refs/heads/{origin_branch}",
        "targetRefName": f"refs/heads/{base_branch}",
        "title": title,
        "description": body,
    },
    method="POST",
)
ensure_work_item_link(pr)

web = pr.get("_links", {}).get("web", {}).get("href") or pr.get("url")
pr_id = str(pr.get("pullRequestId", ""))
print(json.dumps({"pr_url": web, "pr_id": pr_id}, sort_keys=True))
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
  python3 - <<'PY'
import json
import os
print(
    json.dumps(
        {
            "provider": os.environ["PROVIDER"],
            "pr_url": os.environ["PR_URL"],
            "pr_id": os.environ["PR_ID"],
            "origin_branch": os.environ["ORIGIN_BRANCH"],
            "base_branch": os.environ["BASE_BRANCH"],
            "session_id": os.environ["SESSION_ID"],
            "work_item_id": os.environ["WORK_ITEM_ID"],
        },
        sort_keys=True,
    )
)
PY
)"
echo "AGENT_PR_RESULT ${RESULT_JSON}"
