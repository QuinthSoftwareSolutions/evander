#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: mirror_issue_repo.sh <issue-number> [repo-path]

Create or refresh a host bare mirror for a repo using the issue-number naming
convention:
  bare repo:   <issue-number>_repo.git
  host remote: <issue-number>-mirror

Arguments:
  issue-number  Issue identifier used to derive the repo/remote names.
  repo-path     Working tree to mirror. Default: /home/jonathan/Documents/Repos/ExampleIndustryAPP
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 1 ]]; then
  show_help >&2
  exit 1
fi

ISSUE_NUMBER="$1"
REPO_PATH="${2:-/home/jonathan/Documents/Repos/ExampleIndustryAPP}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BARE_REPO_NAME="${ISSUE_NUMBER}_repo"
MIRROR_REMOTE_NAME="${ISSUE_NUMBER}-mirror"
BARE_REPO_PATH="${ROOT_DIR}/git-remotes/${BARE_REPO_NAME}.git"

if [[ ! -d "${REPO_PATH}" ]]; then
  echo "Missing repo path: ${REPO_PATH}" >&2
  exit 1
fi

"${SCRIPT_DIR}/create_git_remote.sh" "${BARE_REPO_NAME}"

if git -C "${REPO_PATH}" remote get-url "${MIRROR_REMOTE_NAME}" >/dev/null 2>&1; then
  git -C "${REPO_PATH}" remote set-url "${MIRROR_REMOTE_NAME}" "${BARE_REPO_PATH}"
else
  git -C "${REPO_PATH}" remote add "${MIRROR_REMOTE_NAME}" "${BARE_REPO_PATH}"
fi

git -C "${REPO_PATH}" push --mirror "${MIRROR_REMOTE_NAME}"
git --git-dir="${BARE_REPO_PATH}" config agent-sandbox.trustedRepoPath "${REPO_PATH}"

DEFAULT_BASE_BRANCH="$(
  git -C "${REPO_PATH}" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null \
    | sed 's#^origin/##'
)"
if [[ -n "${DEFAULT_BASE_BRANCH}" ]]; then
  git --git-dir="${BARE_REPO_PATH}" config agent-sandbox.baseBranch "${DEFAULT_BASE_BRANCH}"
fi

echo
echo "Host mirror ready:"
echo "  repo:   ${REPO_PATH}"
echo "  remote: ${MIRROR_REMOTE_NAME}"
echo "  bare:   ${BARE_REPO_PATH}"
