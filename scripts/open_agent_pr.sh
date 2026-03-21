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

if [[ -z "${ORIGIN_BRANCH}" ]]; then
  ORIGIN_BRANCH="${LOCAL_BRANCH}"
fi

git -C "${HOST_REPO_PATH}" rev-parse --verify "${LOCAL_BRANCH}^{commit}" >/dev/null
git -C "${HOST_REPO_PATH}" push origin "${LOCAL_BRANCH}:refs/heads/${ORIGIN_BRANCH}"

echo "Pushed ${LOCAL_BRANCH} to origin/${ORIGIN_BRANCH}"

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
    git -C "${HOST_REPO_PATH}" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null \
      | sed 's#^origin/##'
  )"
fi

if [[ -z "${BASE_BRANCH}" ]]; then
  echo "Could not determine origin default branch. Pass --base explicitly." >&2
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

ORIGIN_URL="$(git -C "${HOST_REPO_PATH}" remote get-url origin)"

if [[ "${PROVIDER}" == "auto" ]]; then
  case "${ORIGIN_URL}" in
    git@github.com:*|https://github.com/*) PROVIDER="github" ;;
    git@ssh.dev.azure.com:*|https://dev.azure.com/*|https://*.visualstudio.com/*) PROVIDER="azure" ;;
    *)
      echo "Could not infer PR provider from origin URL: ${ORIGIN_URL}" >&2
      echo "Pass --provider explicitly." >&2
      exit 1
      ;;
  esac
fi

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

    (
      cd "${HOST_REPO_PATH}"
      gh "${GH_ARGS[@]}"
    )
    ;;
  azure)
    AZURE_DEVOPS_PAT_VALUE="${!AZURE_PAT_ENV:-}"
    if [[ -z "${AZURE_DEVOPS_PAT_VALUE}" ]]; then
      echo "Azure DevOps PAT is required for --provider azure." >&2
      echo "Set ${AZURE_PAT_ENV} in the environment or config/git-server.env." >&2
      exit 1
    fi

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
    web = pr.get("_links", {}).get("web", {}).get("href") or pr.get("url")
    print(web)
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
print(web)
PY
    ;;
  *)
    echo "Unsupported provider: ${PROVIDER}" >&2
    exit 1
    ;;
esac
