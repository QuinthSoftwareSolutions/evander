#!/usr/bin/env bash
set -Eeuo pipefail

show_help() {
  cat <<'EOF'
Usage:
  push-agent

Validate that the current branch contains the configured destination and either
the server-prepared immutable base commit or the current base branch, refresh
the branch-local PR summary note onto HEAD, and push the agent branch plus the
configured PR notes ref to the agent remote. A current branch already owned by
this session under agents/<session-id>/ is pushed without adding another
session namespace. Concurrent PR-note updates are merged before publication and
retried without overwriting remote notes.
EOF
}

NOTES_SYNC_REF=""

cleanup_notes_sync_ref() {
  if [[ -n "${NOTES_SYNC_REF}" ]]; then
    git update-ref -d "${NOTES_SYNC_REF}" >/dev/null 2>&1 || true
  fi
}

trap cleanup_notes_sync_ref EXIT

die() {
  echo "$*" >&2
  exit 1
}

require_repo() {
  git rev-parse --show-toplevel >/dev/null 2>&1 || die "push-agent must run inside a Git worktree."
}

config_value() {
  git config --get "$1" 2>/dev/null || true
}

current_branch() {
  local branch
  if ! branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)"; then
    die "push-agent cannot push from detached HEAD. Switch to a named branch first."
  fi
  [[ -n "${branch}" ]] || die "push-agent could not determine the current branch."
  printf '%s\n' "${branch}"
}

agent_branch_ref() {
  local session_id_value="$1"
  local branch="$2"
  local suffix="$3"

  if [[ "${branch}" == agents/"${session_id_value}"/* ]]; then
    printf 'refs/evander/%s\n' "${branch}"
    return 0
  fi
  if [[ "${branch}" == agents/* ]]; then
    die "Current branch ${branch} belongs to a different agent session; refusing agent push."
  fi
  printf 'refs/evander/agents/%s/%s-%s\n' "${session_id_value}" "${branch}" "${suffix}"
}

sanitize_suffix() {
  local suffix_source="$1"
  printf '%s' "${suffix_source}" \
    | tr '[:upper:]' '[:lower:]' \
    | tr -cs 'a-z0-9._-' '-' \
    | sed 's/^-*//;s/-*$//'
}

session_id() {
  local value="${SESSION_ID:-}"
  if [[ -z "${value}" ]]; then
    value="$(config_value evander.sessionId)"
  fi
  [[ -n "${value}" ]] || die "SESSION_ID is not set; refusing ambiguous agent push."
  printf '%s\n' "${value}"
}

branch_suffix() {
  local suffix_source="${AGENT_BRANCH_SUFFIX:-}"
  local suffix
  if [[ -z "${suffix_source}" ]]; then
    suffix_source="$(config_value evander.branchSuffix)"
  fi
  if [[ -z "${suffix_source}" ]]; then
    suffix_source="${HOSTNAME:-}"
  fi
  suffix="$(sanitize_suffix "${suffix_source}")"
  [[ -n "${suffix}" ]] || die "AGENT_BRANCH_SUFFIX/HOSTNAME is empty; refusing ambiguous agent push."
  printf '%s\n' "${suffix}"
}

pr_notes_ref() {
  local notes_ref
  notes_ref="$(config_value evander.prNotesRef)"
  if [[ -z "${notes_ref}" ]]; then
    notes_ref="refs/notes/agent-pr"
  fi
  printf '%s\n' "${notes_ref}"
}

expected_destination_branch() {
  local expected_issue_number="$1"
  local session_id_value="$2"
  local destination_branch
  destination_branch="$(config_value evander.destinationBranch)"
  if [[ -z "${expected_issue_number}" ]]; then
    expected_issue_number="$(issue_number_from_value "${session_id_value}" || true)"
  fi
  if [[ -z "${destination_branch}" && -n "${expected_issue_number}" ]]; then
    destination_branch="feature/issue-${expected_issue_number}"
  fi
  printf '%s\n' "${destination_branch}"
}

base_branch() {
  config_value evander.baseBranch
}

prepared_base_commit() {
  config_value evander.baseCommit
}

issue_number_from_value() {
  local value="${1:-}"
  [[ -n "${value}" ]] || return 1
  if [[ "${value}" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "${value}"
    return 0
  fi
  if [[ "${value}" =~ ^vm-([0-9]+)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "${value}" =~ ^issue-([0-9]+)(-|$) ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

verify_session_matches_issue() {
  local session_id_value="$1"
  local expected_issue_number="$2"
  if [[ -z "${expected_issue_number}" ]]; then
    return 0
  fi
  case "${session_id_value}" in
    issue-${expected_issue_number}|issue-${expected_issue_number}-*)
      ;;
    *)
      die "SESSION_ID (${session_id_value}) does not match expected issue ${expected_issue_number}; refusing agent push."
      ;;
  esac
}

remote_branch_exists() {
  local remote_name="$1"
  local branch_name="$2"
  local label="$3"
  local output status

  set +e
  output="$(git ls-remote --exit-code "${remote_name}" "refs/heads/${branch_name}" 2>&1)"
  status=$?
  set -e

  if [[ "${status}" -eq 0 ]]; then
    return 0
  fi
  if [[ "${status}" -eq 2 ]]; then
    return 1
  fi

  die "Could not verify ${label} ${remote_name}/${branch_name}: ${output}"
}

verify_remote_branch_contained() {
  local branch_name="$1"
  local label="$2"
  local missing_ok="$3"
  local remote_name="origin"
  local tracking_ref="refs/remotes/${remote_name}/${branch_name}"
  local fetch_output merge_base_status counts remote_ahead_count local_ahead_count
  local remote_commit local_commit

  [[ -n "${branch_name}" ]] || return 0

  if ! git remote get-url "${remote_name}" >/dev/null 2>&1; then
    die "Cannot verify ${label} ${remote_name}/${branch_name}: remote ${remote_name} is not configured."
  fi

  if ! remote_branch_exists "${remote_name}" "${branch_name}" "${label}"; then
    if [[ "${missing_ok}" == "1" ]]; then
      return 0
    fi
    die "Configured ${label} ${remote_name}/${branch_name} does not exist."
  fi

  if ! fetch_output="$(git fetch "${remote_name}" "+refs/heads/${branch_name}:${tracking_ref}" 2>&1)"; then
    die "Could not refresh ${label} ${remote_name}/${branch_name}: ${fetch_output}"
  fi

  if git merge-base --is-ancestor "${tracking_ref}" HEAD >/dev/null 2>&1; then
    return 0
  else
    merge_base_status=$?
  fi

  remote_commit="$(git rev-parse --verify "${tracking_ref}^{commit}" 2>/dev/null || true)"
  local_commit="$(git rev-parse --verify "HEAD^{commit}" 2>/dev/null || true)"

  if [[ "${merge_base_status}" -ne 1 ]]; then
    die "Could not compare HEAD with ${label} ${remote_name}/${branch_name}; refusing agent push."
  fi

  counts="$(git rev-list --left-right --count "${tracking_ref}...HEAD" 2>/dev/null || true)"
  remote_ahead_count="$(printf '%s\n' "${counts}" | awk '{print $1}')"
  local_ahead_count="$(printf '%s\n' "${counts}" | awk '{print $2}')"
  remote_ahead_count="${remote_ahead_count:-0}"
  local_ahead_count="${local_ahead_count:-0}"

  cat >&2 <<EOF
Refusing to push: ${label} ${remote_name}/${branch_name} has ${remote_ahead_count} commit(s) not contained in HEAD.
Local HEAD: ${local_commit:-unknown} (${local_ahead_count} commit(s) not on ${remote_name}/${branch_name})
Remote branch: ${remote_commit:-unknown}
EOF

  if [[ "${label}" == "base branch" ]]; then
    cat >&2 <<EOF

Sync the base branch before pushing:
  git sync-base
  git push-agent
EOF
  else
    cat >&2 <<EOF

Fetch and merge the destination branch before pushing:
  git fetch ${remote_name} ${branch_name}
  git merge ${remote_name}/${branch_name}
  git agent-pr-note append -m "Merged ${branch_name} before pushing updated work."
  git push-agent
EOF
  fi
  exit 1
}

verify_publish_preconditions() {
  local destination_branch="$1"
  local base_branch="$2"
  local prepared_base_commit="$3"

  if [[ -n "${destination_branch}" ]]; then
    verify_remote_branch_contained "${destination_branch}" "destination branch" "1"
  fi
  if [[ -n "${prepared_base_commit}" ]]; then
    verify_prepared_base_contained "${prepared_base_commit}"
  elif [[ -n "${base_branch}" && "${base_branch}" != "${destination_branch}" ]]; then
    verify_remote_branch_contained "${base_branch}" "base branch" "0"
  fi
}

verify_prepared_base_contained() {
  local prepared_commit="$1"

  if ! git rev-parse --verify "${prepared_commit}^{commit}" >/dev/null 2>&1; then
    die "Configured prepared base commit ${prepared_commit} is not available locally."
  fi
  if ! git merge-base --is-ancestor "${prepared_commit}" HEAD >/dev/null 2>&1; then
    die "Refusing to push: HEAD does not contain configured prepared base commit ${prepared_commit}."
  fi
}

sync_remote_notes_ref() {
  local remote_name="$1"
  local notes_ref="$2"
  local output status

  set +e
  output="$(git ls-remote --exit-code "${remote_name}" "${notes_ref}" 2>&1)"
  status=$?
  set -e

  if [[ "${status}" -eq 2 ]]; then
    return 0
  fi
  if [[ "${status}" -ne 0 ]]; then
    die "Could not inspect managed PR notes ${remote_name}/${notes_ref}: ${output}"
  fi

  if ! output="$(git fetch "${remote_name}" "+${notes_ref}:${NOTES_SYNC_REF}" 2>&1)"; then
    die "Could not refresh managed PR notes ${remote_name}/${notes_ref}: ${output}"
  fi
  if ! output="$(git notes --ref="${notes_ref}" merge -s union "${NOTES_SYNC_REF}" 2>&1)"; then
    die "Could not merge concurrent managed PR notes from ${remote_name}/${notes_ref}: ${output}"
  fi
}

push_notes_ref_with_retry() {
  local remote_name="$1"
  local notes_ref="$2"
  local max_attempts=4
  local attempt=1
  local output

  while (( attempt <= max_attempts )); do
    sync_remote_notes_ref "${remote_name}" "${notes_ref}"
    if output="$(git push "${remote_name}" "${notes_ref}:${notes_ref}" 2>&1)"; then
      [[ -z "${output}" ]] || printf '%s\n' "${output}"
      return 0
    fi
    if (( attempt == max_attempts )); then
      die "Could not publish managed PR notes ${remote_name}/${notes_ref} after ${max_attempts} synchronized attempts: ${output}"
    fi
    printf 'Managed PR notes changed during publication; synchronizing and retrying (%d/%d).\n' \
      "${attempt}" "${max_attempts}" >&2
    attempt=$((attempt + 1))
  done
}

main() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    show_help
    exit 0
  fi
  if [[ $# -ne 0 ]]; then
    die "Unknown argument: $1"
  fi

  require_repo

  local session_id_value expected_issue_number suffix notes_ref repo_root branch destination_branch base_branch_value prepared_base_commit_value publish_ref
  session_id_value="$(session_id)"
  expected_issue_number="$(config_value evander.issueNumber)"
  verify_session_matches_issue "${session_id_value}" "${expected_issue_number}"
  suffix="$(branch_suffix)"
  notes_ref="$(pr_notes_ref)"
  branch="$(current_branch)"
  publish_ref="$(agent_branch_ref "${session_id_value}" "${branch}" "${suffix}")"
  destination_branch="$(expected_destination_branch "${expected_issue_number}" "${session_id_value}")"
  base_branch_value="$(base_branch)"
  prepared_base_commit_value="$(prepared_base_commit)"

  verify_publish_preconditions "${destination_branch}" "${base_branch_value}" "${prepared_base_commit_value}"

  repo_root="$(git rev-parse --show-toplevel)"
  "${repo_root}/.evander/bin/agent-pr-note" ensure-head
  NOTES_SYNC_REF="refs/evander/agent-pr-sync/$(
    printf '%s\n' "${session_id_value}:${branch}:$$" | git hash-object --stdin
  )"
  git push agent "HEAD:${publish_ref}"
  push_notes_ref_with_retry "agent" "${notes_ref}"
}

main "$@"
