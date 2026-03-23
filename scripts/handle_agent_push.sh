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
LOCAL_PR_NOTES_REF=""
PR_BODY_DIR="${AGENT_SANDBOX_PR_BODY_DIR:-${ROOT_DIR}/runtime/pr-body}"
PR_FIXED_BODY_FILE="${AGENT_SANDBOX_PR_FIXED_BODY_FILE:-}"
PR_BODY_MAX_BYTES="${AGENT_SANDBOX_PR_BODY_MAX_BYTES:-60000}"

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

compose_pr_body_file() {
  local fixed_body_file="$1"
  local note_body_file="$2"
  local composed_body_file=""

  USED_FIXED_BODY=0
  USED_AGENT_NOTE=0
  COMPOSED_PR_BODY_FILE=""

  if [[ -n "${fixed_body_file}" && ! -f "${fixed_body_file}" ]]; then
    notify_event \
      "Configured PR fixed body file missing (non-fatal)" \
      "path=${fixed_body_file}"
    fixed_body_file=""
  fi

  if [[ -n "${fixed_body_file}" && -n "${note_body_file}" ]]; then
    USED_FIXED_BODY=1
    USED_AGENT_NOTE=1
    composed_body_file="$(mktemp "${PR_BODY_DIR}/composed-pr-body-XXXXXX.md")"
    cat "${fixed_body_file}" > "${composed_body_file}"
    printf '\n\n---\n\n## Agent Summary\n\n' >> "${composed_body_file}"
    cat "${note_body_file}" >> "${composed_body_file}"
    COMPOSED_PR_BODY_FILE="${composed_body_file}"
    return 0
  fi

  if [[ -n "${fixed_body_file}" ]]; then
    USED_FIXED_BODY=1
    COMPOSED_PR_BODY_FILE="${fixed_body_file}"
    return 0
  fi

  if [[ -n "${note_body_file}" ]]; then
    USED_AGENT_NOTE=1
    COMPOSED_PR_BODY_FILE="${note_body_file}"
    return 0
  fi

  return 1
}

enforce_pr_body_size_limit() {
  local body_file="$1"
  local max_bytes="$2"

  if [[ ! "${max_bytes}" =~ ^[0-9]+$ || "${max_bytes}" -le 0 ]]; then
    notify_event \
      "Invalid PR body max bytes; skipping size limit (non-fatal)" \
      "max_bytes=${max_bytes} body_file=${body_file}"
    return 0
  fi

  BODY_FILE_PATH="${body_file}" PR_BODY_MAX_BYTES_VALUE="${max_bytes}" python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["BODY_FILE_PATH"])
max_bytes = int(os.environ["PR_BODY_MAX_BYTES_VALUE"])
content = path.read_bytes()
if len(content) <= max_bytes:
    raise SystemExit(0)

notice = (
    b"\n\n---\n\n"
    + f"> \xe2\x9a\xa0\xef\xb8\x8f Truncated by handle_agent_push.sh after "
      f"{max_bytes} bytes.\n".encode("utf-8")
)
if len(notice) >= max_bytes:
    path.write_bytes(notice[:max_bytes])
    raise SystemExit(0)

keep = max_bytes - len(notice)
truncated = content[:keep]
while True:
    try:
        truncated.decode("utf-8")
        break
    except UnicodeDecodeError as exc:
        truncated = truncated[:exc.start]
path.write_bytes(truncated + notice)
PY
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

derive_local_notes_ref() {
  local bare_repo_path="$1"
  local repo_name
  repo_name="$(basename "${bare_repo_path}")"
  repo_name="${repo_name%.git}"
  repo_name="$(printf '%s' "${repo_name}" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9._-' '-')"
  repo_name="${repo_name#-}"
  repo_name="${repo_name%-}"
  if [[ -z "${repo_name}" ]]; then
    repo_name="bare-repo"
  fi
  printf 'refs/notes/agent-pr-cache/%s\n' "${repo_name}"
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

process_agent_branch() {
  local agent_branch="$1"
  local new_sha="$2"
  local review_branch
  local session_id
  local note_body_file
  local resolved_sha
  local pr_body_file
  local body_truncated
  local body_size_bytes
  local pr_output

  review_branch="$(derive_review_branch "${agent_branch}")"
  session_id="$(extract_session_id "${agent_branch}" || true)"

  ensure_remote "${TRUSTED_REPO_PATH}" "${AUTO_REMOTE_NAME}" "${BARE_REPO_PATH}"
  promote_branch "${TRUSTED_REPO_PATH}" "${AUTO_REMOTE_NAME}" "${agent_branch}" "${review_branch}"

  if ! git -C "${TRUSTED_REPO_PATH}" fetch "${AUTO_REMOTE_NAME}" \
    "${PR_NOTES_REF}:${LOCAL_PR_NOTES_REF}" >/dev/null 2>&1; then
    notify_event \
      "AGENT_PR_NOTES fetch failed (non-fatal)" \
      "repo=$(basename "${TRUSTED_REPO_PATH}") notes_ref=${PR_NOTES_REF} local_notes_ref=${LOCAL_PR_NOTES_REF} branch=${review_branch} session_id=${session_id:-unknown}"
  fi

  note_body_file=""
  if resolved_sha="$(git -C "${TRUSTED_REPO_PATH}" rev-parse --verify "${new_sha}^{commit}" 2>/dev/null)"; then
    if NOTE_CONTENT="$(git -C "${TRUSTED_REPO_PATH}" notes --ref="${LOCAL_PR_NOTES_REF}" show "${resolved_sha}" 2>/dev/null)"; then
      if [[ -n "${NOTE_CONTENT}" ]]; then
        SAFE_SESSION_ID="${session_id:-unknown}"
        note_body_file="${PR_BODY_DIR}/agent-note-${SAFE_SESSION_ID}-${resolved_sha}.md"
        printf '%s\n' "${NOTE_CONTENT}" > "${note_body_file}"
      fi
    else
      notify_event \
        "AGENT_PR_NOTES read failed (non-fatal)" \
        "repo=$(basename "${TRUSTED_REPO_PATH}") sha=${resolved_sha} notes_ref=${PR_NOTES_REF} local_notes_ref=${LOCAL_PR_NOTES_REF} branch=${review_branch} session_id=${session_id:-unknown}"
    fi
  else
    notify_event \
      "AGENT_PR_NOTES sha resolution failed (non-fatal)" \
      "repo=$(basename "${TRUSTED_REPO_PATH}") new_sha=${new_sha} branch=${review_branch} session_id=${session_id:-unknown}"
  fi

  USED_FIXED_BODY=0
  USED_AGENT_NOTE=0
  COMPOSED_PR_BODY_FILE=""
  if compose_pr_body_file "${PR_FIXED_BODY_FILE}" "${note_body_file}"; then
    pr_body_file="${COMPOSED_PR_BODY_FILE}"
  else
    pr_body_file=""
  fi
  body_truncated=0
  if [[ -n "${pr_body_file}" ]]; then
    if [[ "${PR_BODY_MAX_BYTES}" =~ ^[0-9]+$ && "${PR_BODY_MAX_BYTES}" -gt 0 ]]; then
      body_size_bytes="$(wc -c < "${pr_body_file}")"
      if [[ "${body_size_bytes}" -gt "${PR_BODY_MAX_BYTES}" ]]; then
        body_truncated=1
      fi
    fi
    enforce_pr_body_size_limit "${pr_body_file}" "${PR_BODY_MAX_BYTES}"
  fi

  PR_ARGS=(--create-pr)
  if [[ -n "${BASE_BRANCH}" ]]; then
    PR_ARGS+=(--base "${BASE_BRANCH}")
  fi
  if [[ -n "${session_id}" ]]; then
    PR_ARGS+=(--session-id "${session_id}")
  fi
  if [[ -n "${pr_body_file}" ]]; then
    PR_ARGS+=(--body-file "${pr_body_file}")
  fi
  PR_ARGS+=("${TRUSTED_REPO_PATH}" "${review_branch}")

  if pr_output="$(AGENT_SANDBOX_CANONICAL_ORIGIN_URL="${CANONICAL_ORIGIN_URL}" "${SCRIPT_DIR}/open_agent_pr.sh" "${PR_ARGS[@]}" 2>&1)"; then
    notify_event \
      "Agent branch promoted and PR prepared" \
      "repo=$(basename "${TRUSTED_REPO_PATH}") branch=${review_branch} session_id=${session_id:-unknown} used_fixed_body=${USED_FIXED_BODY} used_agent_note=${USED_AGENT_NOTE} body_truncated=${body_truncated} ${pr_output}"
  else
    notify_event \
      "Agent branch promoted but PR automation failed" \
      "repo=$(basename "${TRUSTED_REPO_PATH}") branch=${review_branch} session_id=${session_id:-unknown} used_fixed_body=${USED_FIXED_BODY} used_agent_note=${USED_AGENT_NOTE} body_truncated=${body_truncated} error=${pr_output}"
  fi
}

if [[ ! -d "${BARE_REPO_PATH}" ]]; then
  echo "Bare repo not found: ${BARE_REPO_PATH}" >&2
  exit 1
fi

# Git hook processes inherit repo-local environment such as GIT_DIR=., which
# breaks subsequent git commands aimed at the trusted worktree unless we clear
# it first.
while IFS= read -r git_env_var; do
  unset "${git_env_var}"
done < <(git --git-dir="${BARE_REPO_PATH}" rev-parse --local-env-vars)

TRUSTED_REPO_PATH="$(git --git-dir="${BARE_REPO_PATH}" config --get agent-sandbox.trustedRepoPath || true)"
BASE_BRANCH="$(git --git-dir="${BARE_REPO_PATH}" config --get agent-sandbox.baseBranch || true)"
CANONICAL_ORIGIN_URL="$(git --git-dir="${BARE_REPO_PATH}" config --get agent-sandbox.canonicalOriginUrl || true)"
LOCAL_PR_NOTES_REF="$(derive_local_notes_ref "${BARE_REPO_PATH}")"

if [[ -z "${TRUSTED_REPO_PATH}" || ! -d "${TRUSTED_REPO_PATH}/.git" ]]; then
  notify_event \
    "Agent branch received but no trusted clone is configured" \
    "bare=$(basename "${BARE_REPO_PATH}") trusted_repo=${TRUSTED_REPO_PATH:-unset}"
  exit 0
fi

UPDATES_FILE="$(mktemp)"
cat > "${UPDATES_FILE}"
trap 'rm -f "${UPDATES_FILE}"' EXIT

HAS_AGENT_BRANCH_UPDATES=0
HAS_AGENT_NOTES_UPDATE=0

while read -r old_sha new_sha ref_name; do
  [[ -n "${ref_name}" ]] || continue

  if [[ "${ref_name}" == refs/notes/agent-pr ]]; then
    HAS_AGENT_NOTES_UPDATE=1
    continue
  fi

  if [[ "${ref_name}" != refs/heads/agents/* ]]; then
    continue
  fi

  if [[ "${new_sha}" == 0000000000000000000000000000000000000000 ]]; then
    continue
  fi

  HAS_AGENT_BRANCH_UPDATES=1
  process_agent_branch "${ref_name#refs/heads/}" "${new_sha}"
done < "${UPDATES_FILE}"

if (( HAS_AGENT_BRANCH_UPDATES == 0 )) && (( HAS_AGENT_NOTES_UPDATE == 1 )); then
  while read -r agent_branch current_sha; do
    [[ -n "${agent_branch}" && -n "${current_sha}" ]] || continue
    process_agent_branch "${agent_branch}" "${current_sha}"
  done < <(
    git --git-dir="${BARE_REPO_PATH}" for-each-ref \
      --format='%(refname:lstrip=2) %(objectname)' refs/heads/agents
  )
fi
