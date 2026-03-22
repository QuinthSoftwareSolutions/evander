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
  --provider <name>   PR provider: auto, github, azure. Default: auto.
  --base <branch>     Base branch for the PR. Default: repository default branch.
  --session-id <id>   Session id for mapping metadata. Defaults to branch-derived value.
  --title <title>     Explicit PR title.
  --body-file <path>  Read PR body from a file.
  --no-fill           Do not pass --fill to gh pr create.
  -h, --help          Show this help text.

Configuration:
  Reads config/git-server.env when present.
  Azure DevOps PR creation requires AZURE_DEVOPS_PAT (or the value configured in
  AGENT_SANDBOX_AZURE_DEVOPS_PAT_ENV).
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

CREATE_PR=0
BASE_BRANCH=""
TITLE=""
BODY_FILE=""
USE_FILL=1
PROVIDER="auto"
SESSION_ID=""
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
    --title)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --title" >&2
        exit 1
      fi
      TITLE="$2"
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
ENV_FILE="${ROOT_DIR}/config/git-server.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  . "${ENV_FILE}"
  set +a
fi

AZURE_PAT_ENV="${AGENT_SANDBOX_AZURE_DEVOPS_PAT_ENV:-AZURE_DEVOPS_PAT}"

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

infer_provider_from_url() {
  local origin_url="$1"
  case "${origin_url}" in
    git@github.com:*|https://github.com/*)
      printf 'github\n'
      ;;
    git@ssh.dev.azure.com:*|https://dev.azure.com/*|https://*.visualstudio.com/*)
      printf 'azure\n'
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
    azure)
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
if [[ "${PUSH_PROVIDER}" == "azure" ]]; then
  AZURE_PAT_ENV_VALUE="${!AZURE_PAT_ENV:-}"
  if [[ -n "${AZURE_PAT_ENV_VALUE}" ]]; then
    HTTPS_ORIGIN_URL="$(azure_https_origin_url "${ORIGIN_URL}")"
    if [[ -n "${HTTPS_ORIGIN_URL}" ]]; then
      PUSH_REMOTE_URL="${HTTPS_ORIGIN_URL}"
    fi
  fi
fi

if [[ "${PR_REMOTE_NAME}" != "origin" ]]; then
  ensure_remote_url "${HOST_REPO_PATH}" "${PR_REMOTE_NAME}" "${PUSH_REMOTE_URL}"
fi

PUSH_ARGS=()
if [[ "${ORIGIN_BRANCH}" == review/* ]]; then
  PUSH_ARGS+=(--force-with-lease)
fi

if [[ "${PUSH_PROVIDER}" == "azure" && -n "${AZURE_PAT_ENV_VALUE}" ]]; then
  AZURE_AUTH_HEADER="Authorization: Basic $(printf ':%s' "${AZURE_PAT_ENV_VALUE}" | base64 | tr -d '\n')"
  git -C "${HOST_REPO_PATH}" -c http.extraHeader="${AZURE_AUTH_HEADER}" \
    push "${PUSH_ARGS[@]}" "${PR_REMOTE_NAME}" "${LOCAL_BRANCH}:refs/heads/${ORIGIN_BRANCH}"
else
  git -C "${HOST_REPO_PATH}" push "${PUSH_ARGS[@]}" "${PR_REMOTE_NAME}" "${LOCAL_BRANCH}:refs/heads/${ORIGIN_BRANCH}"
fi

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
  azure)
    AZURE_DEVOPS_PAT_VALUE="${!AZURE_PAT_ENV:-}"
    if [[ -z "${AZURE_DEVOPS_PAT_VALUE}" ]]; then
      echo "Azure DevOps PAT is required for --provider azure." >&2
      echo "Set ${AZURE_PAT_ENV} in the environment or config/git-server.env." >&2
      exit 1
    fi

    AZURE_RESULT="$(
    ORIGIN_URL="${ORIGIN_URL}" \
    AZURE_DEVOPS_PAT_VALUE="${AZURE_DEVOPS_PAT_VALUE}" \
    BASE_BRANCH="${BASE_BRANCH}" \
    ORIGIN_BRANCH="${ORIGIN_BRANCH}" \
    TITLE="${TITLE}" \
    BODY_CONTENT="${BODY_CONTENT}" \
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
body = os.environ["BODY_CONTENT"]
pat = os.environ["AZURE_DEVOPS_PAT_VALUE"]

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

query = urllib.parse.urlencode(
    {
        "searchCriteria.status": "active",
        "searchCriteria.sourceRefName": f"refs/heads/{origin_branch}",
        "searchCriteria.targetRefName": f"refs/heads/{base_branch}",
        "api-version": "7.1",
    }
)
existing_request = urllib.request.Request(f"{base_url}?{query}", headers=headers)
with urllib.request.urlopen(existing_request) as response:
    payload = json.load(response)

if payload.get("count", 0):
    pr = payload["value"][0]
    pr_id = str(pr.get("pullRequestId", ""))
    update_payload = json.dumps(
        {
            "title": title,
            "description": body,
        }
    ).encode("utf-8")
    update_request = urllib.request.Request(
        f"{base_url}/{urllib.parse.quote(pr_id)}?api-version=7.1",
        headers=headers,
        data=update_payload,
        method="PATCH",
    )
    with urllib.request.urlopen(update_request) as response:
        pr = json.load(response)
    web = pr.get("_links", {}).get("web", {}).get("href") or pr.get("url")
    pr_id = str(pr.get("pullRequestId", ""))
    print(json.dumps({"pr_url": web, "pr_id": pr_id}, sort_keys=True))
    sys.exit(0)

create_payload = json.dumps(
    {
        "sourceRefName": f"refs/heads/{origin_branch}",
        "targetRefName": f"refs/heads/{base_branch}",
        "title": title,
        "description": body,
    }
).encode("utf-8")
create_request = urllib.request.Request(
    f"{base_url}?api-version=7.1",
    headers=headers,
    data=create_payload,
    method="POST",
)
with urllib.request.urlopen(create_request) as response:
    pr = json.load(response)

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
fi

RESULT_JSON="$(
  PROVIDER="${PROVIDER}" \
  PR_URL="${PR_URL}" \
  PR_ID="${PR_ID}" \
  ORIGIN_BRANCH="${ORIGIN_BRANCH}" \
  BASE_BRANCH="${BASE_BRANCH}" \
  SESSION_ID="${SESSION_ID}" \
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
        },
        sort_keys=True,
    )
)
PY
)"
echo "AGENT_PR_RESULT ${RESULT_JSON}"
