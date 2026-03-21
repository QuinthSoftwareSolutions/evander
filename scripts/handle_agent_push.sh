#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: handle_agent_push.sh <bare-repo-path>

Read post-receive updates from stdin for a bare agent repo, promote agent
branches into the trusted host clone, push the review branch to origin, create
an upstream PR, and emit a local notification.

Arguments:
  bare-repo-path  Absolute path to the host bare repository.

Configuration:
  Reads config/git-server.env when present.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -ne 1 ]]; then
  show_help >&2
  exit 1
fi

BARE_REPO_PATH="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ROOT_DIR}/config/git-server.env"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  . "${ENV_FILE}"
  set +a
fi

EVENT_LOG="${AGENT_SANDBOX_EVENT_LOG:-${ROOT_DIR}/runtime/agent-events.log}"
NOTIFY_COMMAND="${AGENT_SANDBOX_NOTIFY_COMMAND:-}"
AUTO_REMOTE_NAME="${AGENT_SANDBOX_AUTO_REMOTE_NAME:-agent-dropbox-auto}"
PR_NOTES_REF="${AGENT_SANDBOX_PR_NOTES_REF:-refs/notes/agent-pr}"
PR_BODY_DIR="${AGENT_SANDBOX_PR_BODY_DIR:-${ROOT_DIR}/runtime/pr-body}"

mkdir -p "$(dirname "${EVENT_LOG}")"
mkdir -p "${PR_BODY_DIR}"

notify_event() {
  local title="$1"
  local body="$2"
  printf '%s | %s | %s\n' "$(date -Iseconds)" "${title}" "${body}" >> "${EVENT_LOG}"
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "${title}" "${body}" >/dev/null 2>&1 || true
  fi
  if [[ -n "${NOTIFY_COMMAND}" ]]; then
    AGENT_NOTIFICATION_TITLE="${title}" AGENT_NOTIFICATION_BODY="${body}" \
      sh -lc "${NOTIFY_COMMAND}" >/dev/null 2>&1 || true
  fi
}

derive_review_branch() {
  local branch="$1"
  if [[ "${branch}" == agents/*/* ]]; then
    printf 'review/%s\n' "${branch#agents/}"
  else
    printf 'review/%s\n' "${branch##refs/heads/}"
  fi
}

extract_session_id() {
  local branch="$1"
  if [[ "${branch}" == agents/*/* ]]; then
    local remainder="${branch#agents/}"
    printf '%s\n' "${remainder%%/*}"
    return 0
  fi
  return 1
}

ensure_remote() {
  local host_repo_path="$1"
  local remote_name="$2"
  local expected_url="$3"
  if git -C "${host_repo_path}" remote get-url "${remote_name}" >/dev/null 2>&1; then
    local current_url
    current_url="$(git -C "${host_repo_path}" remote get-url "${remote_name}")"
    if [[ "${current_url}" != "${expected_url}" ]]; then
      git -C "${host_repo_path}" remote set-url "${remote_name}" "${expected_url}"
    fi
  else
    git -C "${host_repo_path}" remote add "${remote_name}" "${expected_url}"
  fi
}

promote_branch() {
  local host_repo_path="$1"
  local remote_name="$2"
  local agent_branch="$3"
  local review_branch="$4"
  local fetch_ref="refs/heads/${agent_branch}"
  local current_branch

  git -C "${host_repo_path}" fetch "${remote_name}" "${fetch_ref}" >/dev/null
  current_branch="$(git -C "${host_repo_path}" branch --show-current)"

  if git -C "${host_repo_path}" show-ref --verify --quiet "refs/heads/${review_branch}"; then
    if [[ "${current_branch}" == "${review_branch}" ]]; then
      git -C "${host_repo_path}" reset --hard FETCH_HEAD >/dev/null
    else
      git -C "${host_repo_path}" branch -f "${review_branch}" FETCH_HEAD >/dev/null
    fi
  else
    git -C "${host_repo_path}" branch "${review_branch}" FETCH_HEAD >/dev/null
  fi
}

if [[ ! -d "${BARE_REPO_PATH}" ]]; then
  echo "Bare repo not found: ${BARE_REPO_PATH}" >&2
  exit 1
fi

TRUSTED_REPO_PATH="$(git --git-dir="${BARE_REPO_PATH}" config --get agent-sandbox.trustedRepoPath || true)"
BASE_BRANCH="$(git --git-dir="${BARE_REPO_PATH}" config --get agent-sandbox.baseBranch || true)"

if [[ -z "${TRUSTED_REPO_PATH}" || ! -d "${TRUSTED_REPO_PATH}/.git" ]]; then
  notify_event \
    "Agent branch received but no trusted clone is configured" \
    "bare=$(basename "${BARE_REPO_PATH}") trusted_repo=${TRUSTED_REPO_PATH:-unset}"
  exit 0
fi

while read -r old_sha new_sha ref_name; do
  [[ -n "${ref_name}" ]] || continue

  if [[ "${ref_name}" != refs/heads/agents/* ]]; then
    continue
  fi

  if [[ "${new_sha}" == 0000000000000000000000000000000000000000 ]]; then
    continue
  fi

  AGENT_BRANCH="${ref_name#refs/heads/}"
  REVIEW_BRANCH="$(derive_review_branch "${AGENT_BRANCH}")"
  SESSION_ID="$(extract_session_id "${AGENT_BRANCH}" || true)"

  ensure_remote "${TRUSTED_REPO_PATH}" "${AUTO_REMOTE_NAME}" "${BARE_REPO_PATH}"
  promote_branch "${TRUSTED_REPO_PATH}" "${AUTO_REMOTE_NAME}" "${AGENT_BRANCH}" "${REVIEW_BRANCH}"

  # AGENT_PR_NOTES: fetch the dedicated notes ref before PR automation so
  # host-side PR creation can consume agent-authored markdown summaries.
  if ! git -C "${TRUSTED_REPO_PATH}" fetch "${AUTO_REMOTE_NAME}" \
    "${PR_NOTES_REF}:${PR_NOTES_REF}" >/dev/null 2>&1; then
    notify_event \
      "AGENT_PR_NOTES fetch failed (non-fatal)" \
      "repo=$(basename "${TRUSTED_REPO_PATH}") notes_ref=${PR_NOTES_REF} branch=${REVIEW_BRANCH} session_id=${SESSION_ID:-unknown}"
  fi

  PR_BODY_FILE=""
  if RESOLVED_SHA="$(git -C "${TRUSTED_REPO_PATH}" rev-parse --verify "${new_sha}^{commit}" 2>/dev/null)"; then
    # AGENT_PR_NOTES: read git notes for the pushed commit and persist to runtime
    # for use as open_agent_pr.sh --body-file when the note is present.
    if NOTE_CONTENT="$(git -C "${TRUSTED_REPO_PATH}" notes --ref=agent-pr show "${RESOLVED_SHA}" 2>/dev/null)"; then
      if [[ -n "${NOTE_CONTENT}" ]]; then
        SAFE_SESSION_ID="${SESSION_ID:-unknown}"
        PR_BODY_FILE="${PR_BODY_DIR}/agent-note-${SAFE_SESSION_ID}-${RESOLVED_SHA}.md"
        printf '%s\n' "${NOTE_CONTENT}" > "${PR_BODY_FILE}"
      fi
    else
      notify_event \
        "AGENT_PR_NOTES read failed (non-fatal)" \
        "repo=$(basename "${TRUSTED_REPO_PATH}") sha=${RESOLVED_SHA} notes_ref=${PR_NOTES_REF} branch=${REVIEW_BRANCH} session_id=${SESSION_ID:-unknown}"
    fi
  else
    notify_event \
      "AGENT_PR_NOTES sha resolution failed (non-fatal)" \
      "repo=$(basename "${TRUSTED_REPO_PATH}") new_sha=${new_sha} branch=${REVIEW_BRANCH} session_id=${SESSION_ID:-unknown}"
  fi

  PR_ARGS=(--create-pr)
  if [[ -n "${BASE_BRANCH}" ]]; then
    PR_ARGS+=(--base "${BASE_BRANCH}")
  fi
  if [[ -n "${SESSION_ID}" ]]; then
    PR_ARGS+=(--session-id "${SESSION_ID}")
  fi
  if [[ -n "${PR_BODY_FILE}" ]]; then
    PR_ARGS+=(--body-file "${PR_BODY_FILE}")
  fi
  PR_ARGS+=("${TRUSTED_REPO_PATH}" "${REVIEW_BRANCH}")

  if PR_OUTPUT="$("${SCRIPT_DIR}/open_agent_pr.sh" "${PR_ARGS[@]}" 2>&1)"; then
    notify_event \
      "Agent branch promoted and PR prepared" \
      "repo=$(basename "${TRUSTED_REPO_PATH}") branch=${REVIEW_BRANCH} session_id=${SESSION_ID:-unknown} ${PR_OUTPUT}"
  else
    notify_event \
      "Agent branch promoted but PR automation failed" \
      "repo=$(basename "${TRUSTED_REPO_PATH}") branch=${REVIEW_BRANCH} session_id=${SESSION_ID:-unknown} error=${PR_OUTPUT}"
  fi
done
