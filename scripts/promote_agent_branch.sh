#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: promote_agent_branch.sh [options] <host-repo-path> <bare-repo> <agent-branch> [target-branch]

Fetch a quarantined agent branch from a host-local bare remote into a trusted
working clone and create a local target branch from it.

Arguments:
  host-repo-path  Path to a normal local clone of the trusted repository.
  bare-repo       Bare repo name or full path. ".git" suffix is optional.
  agent-branch    Branch name inside the bare repo, typically:
                  agents/<session-id>/<topic>
  target-branch   Local branch name to create. Default:
                  review/<session-id>/<topic>

Options:
  --remote-name <name>  Remote name to use in the trusted clone.
                        Default: agent-dropbox-auto
  --expected-commit-sha <sha>
                        Refuse to update the target branch unless the fetched
                        agent branch resolves to this commit.
  -h, --help            Show this help text.

Configuration:
  Reads root directory-scoped issue setup config when present.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

REMOTE_NAME="agent-dropbox-auto"
EXPECTED_COMMIT_SHA=""
POSITIONAL=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
    --remote-name)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --remote-name" >&2
        exit 1
      fi
      REMOTE_NAME="$2"
      shift 2
      ;;
    --expected-commit-sha)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --expected-commit-sha" >&2
        exit 1
      fi
      EXPECTED_COMMIT_SHA="$2"
      shift 2
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

if [[ $# -lt 3 || $# -gt 4 ]]; then
  show_help >&2
  exit 1
fi

HOST_REPO_PATH="$1"
BARE_REPO_INPUT="$2"
AGENT_BRANCH="$3"
REVIEW_BRANCH="${4:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/runtime_config.sh
source "${SCRIPT_DIR}/lib/runtime_config.sh"
load_issue_setup_config

GIT_ROOT="${EVANDER_GIT_ROOT:-${ROOT_DIR}/git-remotes}"

resolve_bare_repo() {
  local input="$1"
  case "${input}" in
    /*) printf '%s\n' "${input}" ;;
    *.git) printf '%s/%s\n' "${GIT_ROOT}" "${input}" ;;
    *) printf '%s/%s.git\n' "${GIT_ROOT}" "${input}" ;;
  esac
}

derive_review_branch() {
  local branch="$1"
  if [[ "${branch}" == agents/*/* ]]; then
    printf 'review/%s\n' "${branch#agents/}"
  else
    printf 'review/%s\n' "${branch##refs/heads/}"
  fi
}

BARE_REPO_PATH="$(resolve_bare_repo "${BARE_REPO_INPUT}")"

if [[ ! -d "${HOST_REPO_PATH}/.git" ]]; then
  echo "Host repo path is not a Git working tree: ${HOST_REPO_PATH}" >&2
  exit 1
fi

if [[ ! -d "${BARE_REPO_PATH}" ]]; then
  echo "Bare repo not found: ${BARE_REPO_PATH}" >&2
  exit 1
fi

if [[ -z "${REVIEW_BRANCH}" ]]; then
  REVIEW_BRANCH="$(derive_review_branch "${AGENT_BRANCH}")"
fi

LOCK_PATH="${HOST_REPO_PATH}/.git/evander-promote.lock"
exec 9>"${LOCK_PATH}"
flock 9

EXPECTED_URL="${BARE_REPO_PATH}"
if git -C "${HOST_REPO_PATH}" remote get-url "${REMOTE_NAME}" >/dev/null 2>&1; then
  CURRENT_URL="$(git -C "${HOST_REPO_PATH}" remote get-url "${REMOTE_NAME}")"
  if [[ "${CURRENT_URL}" != "${EXPECTED_URL}" ]]; then
    git -C "${HOST_REPO_PATH}" remote set-url "${REMOTE_NAME}" "${EXPECTED_URL}"
  fi
else
  git -C "${HOST_REPO_PATH}" remote add "${REMOTE_NAME}" "${EXPECTED_URL}"
fi

FETCH_REF="refs/heads/${AGENT_BRANCH}"
PROMOTION_REF="refs/evander/promotions/${AGENT_BRANCH}"

if ! git -C "${HOST_REPO_PATH}" fetch "${REMOTE_NAME}" "+${FETCH_REF}:${PROMOTION_REF}"; then
  echo "Failed to fetch agent branch ${AGENT_BRANCH} from ${REMOTE_NAME}" >&2
  exit 1
fi

if ! FETCHED_COMMIT="$(git -C "${HOST_REPO_PATH}" rev-parse --verify "${PROMOTION_REF}^{commit}")"; then
  echo "Failed to resolve fetched commit for ${AGENT_BRANCH}" >&2
  exit 1
fi

if [[ -n "${EXPECTED_COMMIT_SHA}" && "${FETCHED_COMMIT}" != "${EXPECTED_COMMIT_SHA}" ]]; then
  echo "Fetched commit ${FETCHED_COMMIT} for ${AGENT_BRANCH} does not match expected ${EXPECTED_COMMIT_SHA}" >&2
  exit 1
fi

if git -C "${HOST_REPO_PATH}" show-ref --verify --quiet "refs/heads/${REVIEW_BRANCH}"; then
  if ! git -C "${HOST_REPO_PATH}" switch -C "${REVIEW_BRANCH}" "${FETCHED_COMMIT}" >/dev/null; then
    echo "Failed to refresh local review branch: ${REVIEW_BRANCH}" >&2
    exit 1
  fi
  echo "Refreshed local review branch: ${REVIEW_BRANCH}"
else
  if ! git -C "${HOST_REPO_PATH}" switch -c "${REVIEW_BRANCH}" "${FETCHED_COMMIT}" >/dev/null; then
    echo "Failed to create local review branch: ${REVIEW_BRANCH}" >&2
    exit 1
  fi
  echo "Created local review branch: ${REVIEW_BRANCH}"
fi

echo "Fetched ${AGENT_BRANCH} from ${REMOTE_NAME}"
echo "Commit: ${FETCHED_COMMIT}"
echo
echo "Next steps:"
echo "  cd ${HOST_REPO_PATH}"
echo "  git status"
echo "  git log --oneline --decorate -n 5"
echo "  git diff origin/\$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')...HEAD"
