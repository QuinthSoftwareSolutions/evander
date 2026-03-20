#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <repo-name> [branch-namespace]" >&2
  exit 1
fi

REPO_NAME="$1"
BRANCH_NAMESPACE="${2:-agents}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/config/git-server.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  . "${ENV_FILE}"
  set +a
fi

GIT_ROOT="${AGENT_SANDBOX_GIT_ROOT:-${ROOT_DIR}/git-remotes}"
GIT_HOST="${AGENT_SANDBOX_GIT_HOST:-192.168.250.1}"
GIT_USER="${AGENT_SANDBOX_GIT_USER:-${USER}}"

case "${REPO_NAME}" in
  *.git) REPO_DIR="${GIT_ROOT}/${REPO_NAME}" ;;
  *) REPO_DIR="${GIT_ROOT}/${REPO_NAME}.git" ;;
esac

mkdir -p "${GIT_ROOT}"

if [[ ! -d "${REPO_DIR}" ]]; then
  git init --bare "${REPO_DIR}" >/dev/null
  git --git-dir="${REPO_DIR}" config receive.denyCurrentBranch ignore
  git --git-dir="${REPO_DIR}" config gc.auto 0
fi

echo "Bare remote ready: ${REPO_DIR}"
echo
echo "Add from VM:"
echo "  git remote add agent ssh://${GIT_USER}@${GIT_HOST}${REPO_DIR}"
echo
echo "Recommended push pattern:"
echo "  git push agent HEAD:refs/heads/${BRANCH_NAMESPACE}/\${SESSION_ID:-manual}/\$(git rev-parse --abbrev-ref HEAD)"
