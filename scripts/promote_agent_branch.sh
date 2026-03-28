#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: promote_agent_branch.sh [options] <host-repo-path> <bare-repo> <agent-branch> [review-branch]

Fetch a quarantined agent branch from a host-local bare remote into a trusted
working clone and create a local review branch from it.

Arguments:
  host-repo-path  Path to a normal local clone of the trusted repository.
  bare-repo       Bare repo name or full path. ".git" suffix is optional.
  agent-branch    Branch name inside the bare repo, typically:
                  agents/<session-id>/<topic>
  review-branch   Local branch name to create. Default:
                  review/<session-id>/<topic>

Options:
  --remote-name <name>  Remote name to use in the trusted clone.
                        Default: agent-dropbox
  -h, --help            Show this help text.

Configuration:
  Reads root directory-scoped issue setup config when present.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

REMOTE_NAME="agent-dropbox"
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

GIT_ROOT="${AGENT_SANDBOX_GIT_ROOT:-${ROOT_DIR}/git-remotes}"

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

EXPECTED_URL="${BARE_REPO_PATH}"
if git -C "${HOST_REPO_PATH}" remote get-url "${REMOTE_NAME}" >/dev/null 2>&1; then
  CURRENT_URL="$(git -C "${HOST_REPO_PATH}" remote get-url "${REMOTE_NAME}")"
  if [[ "${CURRENT_URL}" != "${EXPECTED_URL}" ]]; then
    echo "Remote ${REMOTE_NAME} already exists with a different URL:" >&2
    echo "  current:  ${CURRENT_URL}" >&2
    echo "  expected: ${EXPECTED_URL}" >&2
    echo "Use a different --remote-name or fix the remote manually." >&2
    exit 1
  fi
else
  git -C "${HOST_REPO_PATH}" remote add "${REMOTE_NAME}" "${EXPECTED_URL}"
fi

if git -C "${HOST_REPO_PATH}" show-ref --verify --quiet "refs/heads/${REVIEW_BRANCH}"; then
  echo "Local review branch already exists: ${REVIEW_BRANCH}" >&2
  echo "Choose a different review branch name or delete it manually after review." >&2
  exit 1
fi

FETCH_REF="refs/heads/${AGENT_BRANCH}"
git -C "${HOST_REPO_PATH}" fetch "${REMOTE_NAME}" "${FETCH_REF}"
git -C "${HOST_REPO_PATH}" switch -c "${REVIEW_BRANCH}" FETCH_HEAD >/dev/null

echo "Fetched ${AGENT_BRANCH} from ${REMOTE_NAME}"
echo "Created local review branch: ${REVIEW_BRANCH}"
echo
echo "Next steps:"
echo "  cd ${HOST_REPO_PATH}"
echo "  git status"
echo "  git log --oneline --decorate -n 5"
echo "  git diff origin/\$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')...HEAD"
