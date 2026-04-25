#!/usr/bin/env bash
set -Eeuo pipefail

show_help() {
  cat <<'EOF'
Usage:
  setup_review_repo.sh --base-ref REF --head-ref REF [options]

Prepare a Git working tree for review by resolving explicit base and head refs,
checking out the head commit, and writing a durable review-context file.

Options:
  --repo-path PATH      Path to the Git working tree. Default: current directory.
  --base-ref REF        Base branch/ref to review against.
  --head-ref REF        Head branch/ref to review.
  --base-commit SHA     Explicit base commit SHA. Overrides --base-ref lookup.
  --head-commit SHA     Explicit head commit SHA. Overrides --head-ref lookup.
  --context-file PATH   Path to write shell-safe review context. Default:
                        .agent-sandbox/review-context.env in the repo root.
  -h, --help            Show this help output.
EOF
}

die() {
  echo "$*" >&2
  exit 1
}

require_repo() {
  git -C "${REPO_PATH}" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Not a Git working tree: ${REPO_PATH}"
}

normalize_fetch_source() {
  local ref="$1"
  case "${ref}" in
    refs/remotes/origin/*)
      printf '%s\n' "${ref#refs/remotes/origin/}"
      ;;
    refs/heads/*)
      printf '%s\n' "${ref#refs/heads/}"
      ;;
    *)
      printf '%s\n' "${ref}"
      ;;
  esac
}

resolve_fetch_remote() {
  if git -C "${REPO_PATH}" remote get-url agent >/dev/null 2>&1; then
    printf 'agent\n'
    return
  fi
  if git -C "${REPO_PATH}" remote get-url origin >/dev/null 2>&1; then
    printf 'origin\n'
    return
  fi
  die "Missing agent/origin remote; cannot fetch review refs"
}

fetch_ref_into_local_ref() {
  local ref="$1"
  local local_ref="$2"
  local remote_name="$3"

  local fetch_source
  fetch_source="$(normalize_fetch_source "${ref}")"
  git -C "${REPO_PATH}" fetch --no-tags "${remote_name}" "${fetch_source}:${local_ref}" >/dev/null
}

resolve_commit() {
  local ref="$1"
  local explicit_commit="${2:-}"
  local local_ref="$3"
  local remote_name="$4"

  if [[ -n "${explicit_commit}" ]]; then
    if ! git -C "${REPO_PATH}" rev-parse --verify "${explicit_commit}^{commit}" >/dev/null 2>&1; then
      fetch_ref_into_local_ref "${ref}" "${local_ref}" "${remote_name}"
      if ! git -C "${REPO_PATH}" rev-parse --verify "${explicit_commit}^{commit}" >/dev/null 2>&1; then
        die "Explicit commit ${explicit_commit} for ${ref} was not present after fetching ${remote_name}"
      fi
    fi
    git -C "${REPO_PATH}" rev-parse --verify "${explicit_commit}^{commit}" >/dev/null
    git -C "${REPO_PATH}" update-ref "${local_ref}" "${explicit_commit}"
    git -C "${REPO_PATH}" rev-parse --verify "${explicit_commit}^{commit}"
    return
  fi

  if git -C "${REPO_PATH}" rev-parse --verify "${ref}^{commit}" >/dev/null 2>&1; then
    local resolved
    resolved="$(git -C "${REPO_PATH}" rev-parse --verify "${ref}^{commit}")"
    git -C "${REPO_PATH}" update-ref "${local_ref}" "${resolved}"
    printf '%s\n' "${resolved}"
    return
  fi

  fetch_ref_into_local_ref "${ref}" "${local_ref}" "${remote_name}"
  git -C "${REPO_PATH}" rev-parse --verify "${local_ref}^{commit}"
}

write_context_file() {
  local context_path="$1"
  local base_ref="$2"
  local base_commit="$3"
  local head_ref="$4"
  local head_commit="$5"

  mkdir -p "$(dirname "${context_path}")"
  {
    printf 'REVIEW_BASE_REF=%q\n' "${base_ref}"
    printf 'REVIEW_BASE_COMMIT=%q\n' "${base_commit}"
    printf 'REVIEW_HEAD_REF=%q\n' "${head_ref}"
    printf 'REVIEW_HEAD_COMMIT=%q\n' "${head_commit}"
    printf 'REVIEW_PREPARED_AT=%q\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    printf 'REVIEW_REPO_PATH=%q\n' "${REPO_PATH}"
  } >"${context_path}"
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

REPO_PATH="$(pwd -P)"
BASE_REF=""
HEAD_REF=""
BASE_COMMIT=""
HEAD_COMMIT=""
CONTEXT_FILE=".agent-sandbox/review-context.env"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-path)
      [[ -n "${2:-}" ]] || die "Missing value for $1"
      REPO_PATH="$(cd "$2" && pwd -P)"
      shift 2
      ;;
    --base-ref)
      [[ -n "${2:-}" ]] || die "Missing value for $1"
      BASE_REF="$2"
      shift 2
      ;;
    --head-ref)
      [[ -n "${2:-}" ]] || die "Missing value for $1"
      HEAD_REF="$2"
      shift 2
      ;;
    --base-commit)
      [[ -n "${2:-}" ]] || die "Missing value for $1"
      BASE_COMMIT="$2"
      shift 2
      ;;
    --head-commit)
      [[ -n "${2:-}" ]] || die "Missing value for $1"
      HEAD_COMMIT="$2"
      shift 2
      ;;
    --context-file)
      [[ -n "${2:-}" ]] || die "Missing value for $1"
      CONTEXT_FILE="$2"
      shift 2
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[[ -n "${BASE_REF}" ]] || die "Missing required argument: --base-ref"
[[ -n "${HEAD_REF}" ]] || die "Missing required argument: --head-ref"

require_repo

BASE_LOCAL_REF="refs/agent-sandbox/review/base"
HEAD_LOCAL_REF="refs/agent-sandbox/review/head"
FETCH_REMOTE="$(resolve_fetch_remote)"

BASE_COMMIT="$(resolve_commit "${BASE_REF}" "${BASE_COMMIT}" "${BASE_LOCAL_REF}" "${FETCH_REMOTE}")"
HEAD_COMMIT="$(resolve_commit "${HEAD_REF}" "${HEAD_COMMIT}" "${HEAD_LOCAL_REF}" "${FETCH_REMOTE}")"

git -C "${REPO_PATH}" checkout --detach "${HEAD_COMMIT}" >/dev/null
git -C "${REPO_PATH}" reset --hard "${HEAD_COMMIT}" >/dev/null
git -C "${REPO_PATH}" clean -fd >/dev/null

write_context_file "${CONTEXT_FILE}" "${BASE_REF}" "${BASE_COMMIT}" "${HEAD_REF}" "${HEAD_COMMIT}"

printf 'REVIEW_BASE_REF=%s\n' "${BASE_REF}"
printf 'REVIEW_BASE_COMMIT=%s\n' "${BASE_COMMIT}"
printf 'REVIEW_HEAD_REF=%s\n' "${HEAD_REF}"
printf 'REVIEW_HEAD_COMMIT=%s\n' "${HEAD_COMMIT}"
printf 'REVIEW_CONTEXT_FILE=%s\n' "${CONTEXT_FILE}"
