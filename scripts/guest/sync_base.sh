#!/usr/bin/env bash
set -Eeuo pipefail

show_help() {
  cat <<'EOF'
Usage:
  sync-base [branch]

Fetch and merge the configured base branch into the current work branch.
When [branch] is omitted, reads evander.baseBranch from repo config.
EOF
}

die() {
  echo "$*" >&2
  exit 1
}

require_repo() {
  git rev-parse --show-toplevel >/dev/null 2>&1 || die "sync-base must run inside a Git worktree."
}

config_value() {
  git config --get "$1" 2>/dev/null || true
}

current_branch() {
  local branch
  branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
  [[ -n "${branch}" ]] || die "sync-base cannot run from detached HEAD. Switch to the work branch first."
  printf '%s\n' "${branch}"
}

base_branch() {
  local explicit_branch="${1:-}"
  local configured_branch
  if [[ -n "${explicit_branch}" ]]; then
    printf '%s\n' "${explicit_branch}"
    return
  fi

  configured_branch="$(config_value evander.baseBranch)"
  [[ -n "${configured_branch}" ]] || die "No base branch configured. Set evander.baseBranch or run: git sync-base <branch>"
  printf '%s\n' "${configured_branch}"
}

require_clean_worktree() {
  if ! git diff --quiet --ignore-submodules --; then
    die "Working tree has uncommitted changes. Commit or stash them before running git sync-base."
  fi
  if ! git diff --cached --quiet --ignore-submodules --; then
    die "Index has staged changes. Commit or unstage them before running git sync-base."
  fi
}

fetch_base_branch() {
  local remote_name="$1"
  local branch_name="$2"
  local tracking_ref="$3"
  local output status

  set +e
  output="$(git ls-remote --exit-code "${remote_name}" "refs/heads/${branch_name}" 2>&1)"
  status=$?
  set -e
  if [[ "${status}" -eq 2 ]]; then
    die "Base branch ${remote_name}/${branch_name} does not exist."
  fi
  if [[ "${status}" -ne 0 ]]; then
    die "Could not verify base branch ${remote_name}/${branch_name}: ${output}"
  fi

  if ! output="$(git fetch "${remote_name}" "+refs/heads/${branch_name}:${tracking_ref}" 2>&1)"; then
    die "Could not fetch base branch ${remote_name}/${branch_name}: ${output}"
  fi
}

merge_base_branch() {
  local remote_name="$1"
  local branch_name="$2"
  local tracking_ref="$3"
  local output status

  if git merge-base --is-ancestor "${tracking_ref}" HEAD >/dev/null 2>&1; then
    echo "Current branch already contains ${remote_name}/${branch_name}."
    return 0
  fi

  set +e
  output="$(git merge --no-edit "${tracking_ref}" 2>&1)"
  status=$?
  set -e
  if [[ -n "${output}" ]]; then
    printf '%s\n' "${output}"
  fi
  if [[ "${status}" -eq 0 ]]; then
    echo "Merged ${remote_name}/${branch_name} into $(current_branch)."
    return 0
  fi

  cat >&2 <<EOF
Merge from ${remote_name}/${branch_name} stopped before completion.

Resolve conflicts, then finish the merge:
  git status
  git add <resolved files>
  git commit
  git agent-pr-note append -m "Merged ${branch_name} and resolved conflicts."
  git push-agent
EOF
  exit "${status}"
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    show_help
    exit 0
  fi
  if [[ $# -gt 1 ]]; then
    die "Usage: sync-base [branch]"
  fi

  require_repo
  require_clean_worktree

  local remote_name="origin"
  local branch_name tracking_ref
  branch_name="$(base_branch "${1:-}")"
  tracking_ref="refs/remotes/${remote_name}/${branch_name}"

  git remote get-url "${remote_name}" >/dev/null 2>&1 || die "Remote ${remote_name} is not configured."
  fetch_base_branch "${remote_name}" "${branch_name}" "${tracking_ref}"
  merge_base_branch "${remote_name}" "${branch_name}" "${tracking_ref}"
}

main "$@"
