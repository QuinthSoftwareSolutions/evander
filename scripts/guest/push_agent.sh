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
BRANCH_SYNC_REF=""
SUBMISSION_TMP=""
STRUCTURED=0
ERROR_CODE="transport_failure"

cleanup_notes_sync_ref() {
  if [[ -n "${NOTES_SYNC_REF}" ]]; then
    git update-ref -d "${NOTES_SYNC_REF}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${BRANCH_SYNC_REF}" ]]; then
    git update-ref -d "${BRANCH_SYNC_REF}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${SUBMISSION_TMP}" ]]; then rm -rf -- "${SUBMISSION_TMP}"; fi
}

trap cleanup_notes_sync_ref EXIT

die() {
  if [[ "${STRUCTURED}" == 1 ]]; then
    python3 - "${ERROR_CODE}" <<'PY'
import json, sys
code = sys.argv[1]
messages = {
    "checkout_unavailable": "The prepared checkout is unavailable. Reconcile clone or task preparation before retrying.",
    "head_moved": "HEAD changed. Review the current commit before submitting again.",
    "branch_mismatch": "Use the checkout's prepared branch before submitting.",
    "ancestry_blocked": "Integrate the required base or destination commits before submitting.",
    "non_fast_forward": "Integrate the managed branch's existing commits before submitting.",
    "summary_conflict": "Another writer changed this commit's summary. Review it before retrying.",
    "concurrent_update": "Publication refs changed concurrently. Retry the same request.",
}
print("EVANDER_GIT_SUBMISSION_RESULT=" + json.dumps({
    "success": False, "code": code,
    "message": messages.get(code, "Git submission failed. Retry the same request."),
    "retryable": code in {"transport_failure", "concurrent_update"},
}))
PY
    exit 1
  fi
  echo "$*" >&2
  exit 1
}

require_repo() {
  git rev-parse --show-toplevel >/dev/null 2>&1 || die "push-agent must run inside a Git worktree."
}

config_value() {
  if [[ "${STRUCTURED}" == 1 ]]; then
    case "$1" in
      evander.baseCommit) if [[ -n "${PREPARED_BASE}" ]]; then printf '%s\n' "${PREPARED_BASE}"; return; fi ;;
      evander.baseBranch) if [[ -n "${PREPARED_BASE_BRANCH}" ]]; then printf '%s\n' "${PREPARED_BASE_BRANCH}"; return; fi ;;
      evander.destinationBranch) if [[ -n "${PREPARED_DESTINATION}" ]]; then printf '%s\n' "${PREPARED_DESTINATION}"; return; fi ;;
    esac
  fi
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
  if [[ -n "${SUBMISSION_NOTES_REF:-}" ]]; then printf '%s\n' "${SUBMISSION_NOTES_REF}"; return; fi
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
  local remote_name="${SUBMISSION_REMOTE:-origin}"
  local tracking_ref="refs/remotes/origin/${branch_name}"
  local fetch_output merge_base_status counts remote_ahead_count local_ahead_count
  local remote_commit local_commit

  [[ -n "${branch_name}" ]] || return 0

  if [[ "${STRUCTURED}" != 1 ]] && ! git remote get-url "${remote_name}" >/dev/null 2>&1; then
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

  if git merge-base --is-ancestor "${tracking_ref}" "${SUBMISSION_COMMIT:-HEAD}" >/dev/null 2>&1; then
    return 0
  else
    merge_base_status=$?
  fi

  remote_commit="$(git rev-parse --verify "${tracking_ref}^{commit}" 2>/dev/null || true)"
  local_commit="$(git rev-parse --verify "${SUBMISSION_COMMIT:-HEAD}^{commit}" 2>/dev/null || true)"

  if [[ "${merge_base_status}" -ne 1 ]]; then
    die "Could not compare HEAD with ${label} ${remote_name}/${branch_name}; refusing agent push."
  fi

  counts="$(git rev-list --left-right --count "${tracking_ref}...${SUBMISSION_COMMIT:-HEAD}" 2>/dev/null || true)"
  remote_ahead_count="$(printf '%s\n' "${counts}" | awk '{print $1}')"
  local_ahead_count="$(printf '%s\n' "${counts}" | awk '{print $2}')"
  remote_ahead_count="${remote_ahead_count:-0}"
  local_ahead_count="${local_ahead_count:-0}"

  if [[ "${STRUCTURED}" == 1 ]]; then
    die "Required branch has commits missing from HEAD."
  fi

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
  if ! git merge-base --is-ancestor "${prepared_commit}" "${SUBMISSION_COMMIT:-HEAD}" >/dev/null 2>&1; then
    die "Refusing to push: HEAD does not contain configured prepared base commit ${prepared_commit}."
  fi
}

atomic_publish() {
  local remote="$1" sha="$2" publish_ref="$3" notes_ref="$4" summary_file="$5"
  local branch_old notes_old note_object remote_note summary_blob initial_note="" attempt=1 output
  local changed=0
  local common_dir
  common_dir="$(git rev-parse --git-common-dir)"
  mkdir -p "${common_dir}/evander"
  exec 8>"${common_dir}/evander/submission.lock"
  flock --exclusive 8
  # Reuse the blob directly: -F applies stripspace and changes Markdown
  # hard breaks, blank lines, and other meaningful summary whitespace.
  summary_blob="$(git hash-object -w -- "${summary_file}")" || die "Could not store summary."
  BRANCH_SYNC_REF="${NOTES_SYNC_REF}-branch"
  while (( attempt <= 4 )); do
    ERROR_CODE="head_moved"
    [[ "$(git rev-parse HEAD)" == "${sha}" ]] || die "HEAD changed before publication."
    ERROR_CODE="transport_failure"
    output="$(git ls-remote "${remote}" "${publish_ref}" "${notes_ref}")" || die "Could not inspect publication refs."
    branch_old="$(printf '%s\n' "${output}" | awk -v ref="${publish_ref}" '$2 == ref {print $1}')"
    notes_old="$(printf '%s\n' "${output}" | awk -v ref="${notes_ref}" '$2 == ref {print $1}')"
    if [[ -n "${branch_old}" ]]; then
      git fetch --no-tags "${remote}" "+${publish_ref}:${BRANCH_SYNC_REF}" >/dev/null 2>&1 || die "Could not fetch managed branch."
      # Fetch may observe a newer tip; the lease must cover the actual compared tip.
      branch_old="$(git rev-parse "${BRANCH_SYNC_REF}")"
      ERROR_CODE="non_fast_forward"
      git merge-base --is-ancestor "${branch_old}" "${sha}" || die "Managed branch contains unpublished commits."
    fi
    ERROR_CODE="transport_failure"
    remote_note=""
    if [[ -n "${notes_old}" ]]; then
      git fetch --no-tags "${remote}" "+${notes_ref}:${NOTES_SYNC_REF}" >/dev/null 2>&1 || die "Could not fetch notes."
      notes_old="$(git rev-parse "${NOTES_SYNC_REF}")"
      remote_note="$(git notes --ref="${NOTES_SYNC_REF}" list "${sha}" 2>/dev/null || true)"
    fi
    if (( attempt > 1 )) && [[ "${remote_note}" != "${initial_note}" && "${remote_note}" != "${summary_blob}" ]]; then
      ERROR_CODE="summary_conflict"; die "Concurrent summary update."
    fi
    if [[ -n "${notes_old}" ]]; then
      if git rev-parse --verify "${notes_ref}" >/dev/null 2>&1; then
        git notes --ref="${notes_ref}" merge -s union "${NOTES_SYNC_REF}" >/dev/null 2>&1 || die "Could not merge notes."
      else
        git update-ref "${notes_ref}" "${notes_old}"
      fi
    fi
    if (( attempt == 1 )); then initial_note="${remote_note}"; fi
    if [[ "$(git notes --ref="${notes_ref}" list "${sha}" 2>/dev/null || true)" != "${summary_blob}" ]]; then
      git notes --ref="${notes_ref}" add -f -C "${summary_blob}" "${sha}" >/dev/null || die "Could not attach summary."
    fi
    note_object="$(git rev-parse "${notes_ref}")"
    if [[ "${branch_old}" == "${sha}" && "${notes_old}" == "${note_object}" ]]; then
      SUBMISSION_CHANGED="${changed}"; return
    fi
    if output="$(git push --atomic --porcelain \
      "--force-with-lease=${publish_ref}:${branch_old}" \
      "--force-with-lease=${notes_ref}:${notes_old}" \
      "${remote}" "${sha}:${publish_ref}" "${note_object}:${notes_ref}" 2>&1)"; then
      if [[ "${STRUCTURED}" != 1 ]]; then printf '%s\n' "${output}"; fi
      SUBMISSION_CHANGED=1; return
    fi
    if [[ "$(git ls-remote "${remote}" "${publish_ref}" "${notes_ref}")" == "$(printf '%s\t%s\n%s\t%s\n' "${branch_old}" "${publish_ref}" "${notes_old}" "${notes_ref}" | awk 'NF == 2' | sort -k2)" ]]; then
      ERROR_CODE="transport_failure"; die "Remote rejected atomic submission."
    fi
    # A failed atomic transaction changes neither ref. Refresh on a lease race.
    attempt=$((attempt + 1))
  done
  ERROR_CODE="concurrent_update"; die "Publication did not converge."
}

structured_main() {
  STRUCTURED=1
  SUBMISSION_TMP="$(mktemp -d)"
  local request_file="${SUBMISSION_TMP}/request.json" summary_file="${SUBMISSION_TMP}/summary.md"
  python3 - "$1" "${request_file}" "${summary_file}" <<'PY'
import base64, json, pathlib, sys
request = json.loads(base64.b64decode(sys.argv[1], validate=True))
pathlib.Path(sys.argv[2]).write_text(json.dumps(request))
pathlib.Path(sys.argv[3]).write_text(request["summary_markdown"])
PY
  local -a fields
  mapfile -d '' -t fields < <(python3 - "${request_file}" <<'PY'
import json, sys
r = json.load(open(sys.argv[1]))
for k in ("session_id", "expected_commit_sha", "remote_url", "notes_ref", "fixed_branch", "prepared_base", "base_branch", "destination_branch", "guest_path"):
    v = r.get(k) or ""
    if "\0" in v: raise ValueError("invalid request")
    sys.stdout.write(v + "\0")
PY
  )
  [[ "${#fields[@]}" == 9 ]] || die "Invalid submission request."
  export SESSION_ID="${fields[0]}"
  local sha="${fields[1]}" remote="${fields[2]}" notes_ref="${fields[3]}" fixed_branch="${fields[4]}"
  SUBMISSION_COMMIT="${sha}"
  PREPARED_BASE="${fields[5]}" PREPARED_BASE_BRANCH="${fields[6]}" PREPARED_DESTINATION="${fields[7]}"
  SUBMISSION_REMOTE="${remote}" SUBMISSION_NOTES_REF="${notes_ref}"
  # Only the sandbox executes Git or guest code. Disable hooks for platform submission.
  git() { command git -c core.hooksPath=/dev/null -c core.fsmonitor=false -c protocol.ext.allow=never "$@"; }
  ERROR_CODE="checkout_unavailable"
  cd -- "${fields[8]}" || die "Prepared checkout is unavailable."
  require_repo
  local branch publish_ref git_dir cache suffix
  ERROR_CODE="branch_mismatch"
  branch="$(current_branch)" || die "A named prepared branch is required."
  ERROR_CODE="branch_mismatch"
  [[ -z "${fixed_branch}" || "${branch}" == "${fixed_branch}" ]] || die "Prepared branch changed."
  ERROR_CODE="head_moved"
  [[ "$(git rev-parse HEAD)" == "${sha}" ]] || die "HEAD changed."
  ERROR_CODE="branch_mismatch"
  suffix="$(branch_suffix)" || die "A branch suffix is unavailable."
  publish_ref="$(agent_branch_ref "${SESSION_ID}" "${branch}" "${suffix}")" || die "The branch belongs to another session."
  [[ "${notes_ref}" == refs/notes/agent-pr/* ]] || die "Invalid notes ref."
  ERROR_CODE="ancestry_blocked"
  verify_session_matches_issue "${SESSION_ID}" "$(config_value evander.issueNumber)"
  verify_publish_preconditions "$(expected_destination_branch "$(config_value evander.issueNumber)" "${SESSION_ID}")" "$(base_branch)" "$(prepared_base_commit)"
  ERROR_CODE="head_moved"
  [[ "$(git rev-parse HEAD)" == "${sha}" ]] || die "HEAD changed."
  git_dir="$(git rev-parse --git-dir)"
  cache="${git_dir}/evander/agent-pr-cache/${branch}.md"
  mkdir -p "$(dirname "${cache}")"
  cp "${summary_file}" "${cache}"
  # git notes --ref expands refs outside refs/notes; keep the temporary note
  # ref in the notes namespace so concurrent summaries are read accurately.
  NOTES_SYNC_REF="refs/notes/evander-submission-sync/$(printf '%s' "${SESSION_ID}:${branch}:$$" | git hash-object --stdin)"
  atomic_publish "${remote}" "${sha}" "${publish_ref}" "${notes_ref}" "${summary_file}"
  python3 - "${publish_ref}" "${SUBMISSION_CHANGED}" <<'PY'
import json, sys
print("EVANDER_GIT_SUBMISSION_RESULT=" + json.dumps({"success": True, "agent_ref": sys.argv[1], "changed": sys.argv[2] == "1"}))
PY
}

main() {
  if [[ "${1:-}" == "--submit-json" && $# == 2 ]]; then structured_main "$2"; return; fi
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
  SUBMISSION_COMMIT="$(git rev-parse HEAD)"
  publish_ref="$(agent_branch_ref "${session_id_value}" "${branch}" "${suffix}")"
  destination_branch="$(expected_destination_branch "${expected_issue_number}" "${session_id_value}")"
  base_branch_value="$(base_branch)"
  prepared_base_commit_value="$(prepared_base_commit)"

  verify_publish_preconditions "${destination_branch}" "${base_branch_value}" "${prepared_base_commit_value}"

  repo_root="$(git rev-parse --show-toplevel)"
  "${repo_root}/.evander/bin/agent-pr-note" ensure-head
  NOTES_SYNC_REF="refs/notes/evander-submission-sync/$(
    printf '%s\n' "${session_id_value}:${branch}:$$" | git hash-object --stdin
  )"
  SUBMISSION_TMP="$(mktemp -d)"
  git notes --ref="${notes_ref}" show HEAD >"${SUBMISSION_TMP}/summary.md"
  atomic_publish "agent" "${SUBMISSION_COMMIT}" "${publish_ref}" "${notes_ref}" "${SUBMISSION_TMP}/summary.md"
}

main "$@"
