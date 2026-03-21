#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: open_agent_pr.sh [options] <host-repo-path> [local-branch] [origin-branch]

Push a reviewed local branch from a trusted working clone to origin and,
optionally, open a GitHub pull request.

Arguments:
  host-repo-path  Path to a normal local clone of the trusted repository.
  local-branch    Local branch to push. Default: current branch.
  origin-branch   Destination branch name on origin. Default: same as local branch.

Options:
  --create-pr         Open a GitHub PR after pushing.
  --base <branch>     Base branch for the PR. Default: repository default branch.
  --title <title>     Explicit PR title.
  --body-file <path>  Read PR body from a file.
  --no-fill           Do not pass --fill to gh pr create.
  -h, --help          Show this help text.
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

if ! command -v gh >/dev/null 2>&1; then
  echo "GitHub CLI (gh) is required for --create-pr." >&2
  exit 1
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

GH_ARGS=(pr create --repo "$(git -C "${HOST_REPO_PATH}" remote get-url origin)" --base "${BASE_BRANCH}" --head "${ORIGIN_BRANCH}")

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
