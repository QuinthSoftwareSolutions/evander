#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'USAGE'
Usage: resolve_repo_source.sh [--print-env|--path-only|--name-only] <repo-source>

Resolve a repo source into a local working tree path and repository name.

Arguments:
  repo-source  Either a local repo path or a remote Git URL (https://, ssh://,
               or scp-style git@host:path).

Output modes:
  --path-only   Print only the resolved local path (default).
  --name-only   Print only the resolved repository name.
  --print-env   Print shell-safe assignments for:
                RESOLVED_REPO_PATH and RESOLVED_REPO_NAME

Remote behavior:
  - Mirrors are materialized under runtime/repo-cache/<hash>.git
  - A working tree checkout is maintained at runtime/repo-cache/<hash>-worktree
  - Each invocation refreshes from origin with fetch --prune
USAGE
}

OUTPUT_MODE="path"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  show_help
  exit 0
fi

if [[ "${1:-}" == "--print-env" ]]; then
  OUTPUT_MODE="env"
  shift
elif [[ "${1:-}" == "--name-only" ]]; then
  OUTPUT_MODE="name"
  shift
elif [[ "${1:-}" == "--path-only" ]]; then
  OUTPUT_MODE="path"
  shift
fi

if [[ $# -ne 1 ]]; then
  show_help >&2
  exit 1
fi

REPO_SOURCE="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CACHE_DIR="${ROOT_DIR}/runtime/repo-cache"
# shellcheck source=lib/runtime_config.sh
source "${SCRIPT_DIR}/lib/runtime_config.sh"
# shellcheck source=lib/git_provider_auth.sh
source "${SCRIPT_DIR}/lib/git_provider_auth.sh"
load_root_secrets_env
load_provider_config

is_remote_source() {
  local source="$1"
  if [[ "${source}" =~ ^[a-zA-Z][a-zA-Z0-9+.-]*:// ]]; then
    return 0
  fi
  if [[ "${source}" =~ ^[^/@:[:space:]]+@[^:[:space:]]+:.+ ]]; then
    return 0
  fi
  return 1
}

infer_repo_name() {
  local source="$1"
  local trimmed="${source%/}"
  local name

  if [[ "${trimmed}" =~ ^[^/@:[:space:]]+@[^:[:space:]]+:.+ ]]; then
    name="${trimmed##*:}"
  else
    name="${trimmed##*/}"
  fi

  name="${name##*/}"
  name="${name%.git}"
  if [[ -z "${name}" ]]; then
    echo "Could not infer repository name from source: ${source}" >&2
    exit 1
  fi

  printf '%s\n' "${name}"
}

checkout_default_branch() {
  local bare_path="$1"
  local worktree_path="$2"
  local default_branch

  default_branch="$(git --git-dir="${bare_path}" symbolic-ref --quiet --short HEAD 2>/dev/null || true)"

  if [[ -z "${default_branch}" ]]; then
    default_branch="$(git --git-dir="${bare_path}" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##' || true)"
  fi

  if [[ -z "${default_branch}" ]]; then
    default_branch="$(git --git-dir="${bare_path}" for-each-ref --format='%(refname:short)' refs/heads | head -n1)"
  fi

  if [[ -z "${default_branch}" ]]; then
    default_branch="$(git --git-dir="${bare_path}" for-each-ref --format='%(refname:short)' refs/remotes/origin | sed -n 's#^origin/##p' | head -n1)"
  fi

  if [[ -z "${default_branch}" ]]; then
    echo "Remote repository appears to have no branches: ${REPO_SOURCE}" >&2
    exit 1
  fi

  git -C "${worktree_path}" fetch --prune origin '+refs/heads/*:refs/remotes/origin/*' >/dev/null
  git -C "${worktree_path}" checkout -B "${default_branch}" "origin/${default_branch}" >/dev/null
  git -C "${worktree_path}" reset --hard "origin/${default_branch}" >/dev/null
  git -C "${worktree_path}" clean -fd >/dev/null
}

resolve_local_path() {
  local source="$1"
  if [[ ! -d "${source}" ]]; then
    echo "Missing repo path: ${source}" >&2
    exit 1
  fi

  git -C "${source}" rev-parse --is-inside-work-tree >/dev/null 2>&1 || {
    echo "Path is not a Git working tree: ${source}" >&2
    exit 1
  }

  readlink -f "${source}"
}

resolve_remote_path() {
  local source="$1"
  local hash
  local mirror_path
  local worktree_path
  local remote_url_for_git

  mkdir -p "${CACHE_DIR}"
  hash="$(printf '%s' "${source}" | sha256sum | awk '{print $1}')"
  mirror_path="${CACHE_DIR}/${hash}.git"
  worktree_path="${CACHE_DIR}/${hash}-worktree"
  git_provider_prepare_remote "${source}"
  remote_url_for_git="${GIT_PROVIDER_REMOTE_URL}"

  if [[ -d "${mirror_path}" ]]; then
    git --git-dir="${mirror_path}" remote set-url origin "${remote_url_for_git}"
    git_run_with_remote_auth "${source}" --git-dir="${mirror_path}" fetch --prune origin '+refs/*:refs/*'
  else
    git_run_with_remote_auth "${source}" clone --mirror "${remote_url_for_git}" "${mirror_path}" >/dev/null
  fi

  if [[ -d "${worktree_path}/.git" ]]; then
    git -C "${worktree_path}" remote set-url origin "${mirror_path}"
    git -C "${worktree_path}" fetch --prune origin
  else
    rm -rf "${worktree_path}"
    git clone "${mirror_path}" "${worktree_path}" >/dev/null
  fi

  checkout_default_branch "${mirror_path}" "${worktree_path}"
  printf '%s\n' "${worktree_path}"
}

if is_remote_source "${REPO_SOURCE}"; then
  RESOLVED_REPO_PATH="$(resolve_remote_path "${REPO_SOURCE}")"
  RESOLVED_REPO_NAME="$(infer_repo_name "${REPO_SOURCE}")"
else
  RESOLVED_REPO_PATH="$(resolve_local_path "${REPO_SOURCE}")"
  RESOLVED_REPO_NAME="$(basename "${RESOLVED_REPO_PATH%/}")"
fi

case "${OUTPUT_MODE}" in
  path)
    printf '%s\n' "${RESOLVED_REPO_PATH}"
    ;;
  name)
    printf '%s\n' "${RESOLVED_REPO_NAME}"
    ;;
  env)
    printf 'RESOLVED_REPO_PATH=%q\n' "${RESOLVED_REPO_PATH}"
    printf 'RESOLVED_REPO_NAME=%q\n' "${RESOLVED_REPO_NAME}"
    ;;
  *)
    echo "Unknown output mode: ${OUTPUT_MODE}" >&2
    exit 1
    ;;
esac
