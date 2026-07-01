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
  Uses environment variables and root config-derived defaults.
  Directory-scoped ado_worker config supports:
    - create_prs_automatically (default true)
    - create_prs_as_draft (default false)
  Per-session metadata may also override both settings.
  Optional host-wide overrides:
    - EVANDER_AUTO_CREATE_PRS
    - EVANDER_CREATE_PRS_AS_DRAFT
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
# shellcheck source=lib/runtime_config.sh
source "${SCRIPT_DIR}/lib/runtime_config.sh"
load_root_secrets_env
load_provider_config

EVENT_LOG="${EVANDER_EVENT_LOG:-${ROOT_DIR}/runtime/agent-events.log}"
NOTIFY_COMMAND="${EVANDER_NOTIFY_COMMAND:-}"
AUTO_REMOTE_NAME="${EVANDER_AUTO_REMOTE_NAME:-agent-dropbox-auto}"
PR_NOTES_REF="${EVANDER_PR_NOTES_REF:-}"
PR_NOTES_REF_OVERRIDE_SET=0
if [[ -v EVANDER_PR_NOTES_REF ]]; then
  PR_NOTES_REF_OVERRIDE_SET=1
  if [[ -z "${PR_NOTES_REF}" ]]; then
    PR_NOTES_REF="refs/notes/agent-pr"
  fi
fi
PUSHED_AGENT_NOTES_REFS=()
LOCAL_PR_NOTES_REF=""
RUNTIME_DIR="${EVANDER_RUNTIME_DIR:-${ROOT_DIR}/runtime}"
PR_BODY_DIR="${EVANDER_PR_BODY_DIR:-${ROOT_DIR}/runtime/pr-body}"
PR_FIXED_BODY_FILE="${EVANDER_PR_FIXED_BODY_FILE:-}"
PR_BODY_MAX_BYTES="${EVANDER_PR_BODY_MAX_BYTES:-60000}"
PR_TEMPLATE_LEGACY_FILE="${ROOT_DIR}/config/pr-template.md"
AUTO_CREATE_PRS_OVERRIDE=""
AUTO_CREATE_PRS_OVERRIDE_SET=0
if [[ -v EVANDER_AUTO_CREATE_PRS ]]; then
  AUTO_CREATE_PRS_OVERRIDE="${EVANDER_AUTO_CREATE_PRS}"
  AUTO_CREATE_PRS_OVERRIDE_SET=1
fi
DRAFT_PRS_OVERRIDE=""
DRAFT_PRS_OVERRIDE_SET=0
if [[ -v EVANDER_CREATE_PRS_AS_DRAFT ]]; then
  DRAFT_PRS_OVERRIDE="${EVANDER_CREATE_PRS_AS_DRAFT}"
  DRAFT_PRS_OVERRIDE_SET=1
fi

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

bare_config_value_with_legacy() {
  local current_key="$1"
  local legacy_key="$2"
  local value
  value="$(git --git-dir="${BARE_REPO_PATH}" config --get "${current_key}" || true)"
  if [[ -n "${value}" ]]; then
    printf '%s\n' "${value}"
    return 0
  fi

  value="$(git --git-dir="${BARE_REPO_PATH}" config --get "${legacy_key}" || true)"
  if [[ -n "${value}" ]]; then
    git --git-dir="${BARE_REPO_PATH}" config "${current_key}" "${value}"
  fi
  printf '%s\n' "${value}"
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

resolve_pr_template_body_file() {
  local session_id="$1"
  local legacy_template_file="$2"
  local python_bin="${ROOT_DIR}/.venv/bin/python"
  if [[ ! -x "${python_bin}" ]]; then
    python_bin="python3"
  fi

  RUNTIME_DIR_PATH="${RUNTIME_DIR}" SESSION_ID_VALUE="${session_id}" LEGACY_TEMPLATE_FILE="${legacy_template_file}" PR_BODY_DIR_PATH="${PR_BODY_DIR}" \
    "${python_bin}" - <<'PY'
import json
import os
import tempfile
from pathlib import Path

runtime_dir = Path(os.environ["RUNTIME_DIR_PATH"])
session_id = os.environ.get("SESSION_ID_VALUE", "").strip()
legacy_template_file = Path(os.environ["LEGACY_TEMPLATE_FILE"])
pr_body_dir = Path(os.environ["PR_BODY_DIR_PATH"])
pr_body_dir.mkdir(parents=True, exist_ok=True)

directory_path = "root"
sessions_path = runtime_dir / "sessions.json"
if session_id and sessions_path.exists():
    try:
        sessions_payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sessions_payload = {}
    if isinstance(sessions_payload, dict):
        session_payload = sessions_payload.get(session_id)
        if isinstance(session_payload, dict):
            candidate_directory_path = str(session_payload.get("directory_path", "")).strip()
            if candidate_directory_path:
                directory_path = candidate_directory_path

template_text = None
segments = [segment for segment in Path(directory_path).parts if segment]
if not segments or segments[0] != "root":
    segments = ["root"]
directory_config_root = runtime_dir / "directory-config"
for index in range(1, len(segments) + 1):
    candidate = directory_config_root.joinpath(*segments[:index], "pr-template.json")
    if not candidate.exists():
        continue
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    if isinstance(payload, dict):
        value = payload.get("pr_template_markdown")
        if isinstance(value, str):
            template_text = value

if template_text is None:
    if legacy_template_file.exists():
        template_text = legacy_template_file.read_text(encoding="utf-8")
    else:
        raise SystemExit(1)

if not template_text.strip():
    raise SystemExit(1)

with tempfile.NamedTemporaryFile(
    "w",
    encoding="utf-8",
    dir=str(pr_body_dir),
    prefix="resolved-pr-template-",
    suffix=".md",
    delete=False,
) as handle:
    handle.write(template_text)
    print(handle.name)
PY
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

resolve_pr_automation_settings() {
  local session_id="$1"
  local python_bin="${ROOT_DIR}/.venv/bin/python"
  if [[ ! -x "${python_bin}" ]]; then
    python_bin="python3"
  fi

  RUNTIME_DIR_PATH="${RUNTIME_DIR}" \
  SESSION_ID_VALUE="${session_id}" \
  AUTO_CREATE_OVERRIDE_IS_SET="${AUTO_CREATE_PRS_OVERRIDE_SET}" \
  AUTO_CREATE_OVERRIDE_VALUE="${AUTO_CREATE_PRS_OVERRIDE}" \
  DRAFT_OVERRIDE_IS_SET="${DRAFT_PRS_OVERRIDE_SET}" \
  DRAFT_OVERRIDE_VALUE="${DRAFT_PRS_OVERRIDE}" \
    "${python_bin}" - <<'PY'
import json
import os
from pathlib import Path, PurePosixPath

runtime_dir = Path(os.environ["RUNTIME_DIR_PATH"])
session_id = os.environ.get("SESSION_ID_VALUE", "").strip()
auto_create_override = None
if os.environ.get("AUTO_CREATE_OVERRIDE_IS_SET") == "1":
    auto_create_override = os.environ.get("AUTO_CREATE_OVERRIDE_VALUE", "").strip()
draft_override = None
if os.environ.get("DRAFT_OVERRIDE_IS_SET") == "1":
    draft_override = os.environ.get("DRAFT_OVERRIDE_VALUE", "").strip()

directory_path = "root"
session_metadata: dict[str, object] = {}
sessions_path = runtime_dir / "sessions.json"
if session_id and sessions_path.exists():
    try:
        sessions_payload = json.loads(sessions_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        sessions_payload = {}
    if isinstance(sessions_payload, dict):
        session_payload = sessions_payload.get(session_id)
        if isinstance(session_payload, dict):
            candidate_directory_path = str(session_payload.get("directory_path") or "").strip()
            if candidate_directory_path:
                directory_path = candidate_directory_path
            raw_metadata = session_payload.get("metadata")
            if isinstance(raw_metadata, dict):
                session_metadata = raw_metadata

segments = [segment for segment in PurePosixPath(directory_path).parts if segment not in {"", "."}]
if not segments or segments[0] != "root":
    segments = ["root"]

def parse_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return None

create_prs_automatically = True
create_prs_as_draft = False

directory_config_root = runtime_dir / "directory-config"
for index in range(1, len(segments) + 1):
    candidate = directory_config_root.joinpath(*segments[:index], "ado-worker.json")
    if not candidate.exists():
        continue
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    if not isinstance(payload, dict):
        continue
    auto_value = parse_bool(payload.get("create_prs_automatically"))
    if auto_value is not None:
        create_prs_automatically = auto_value
    draft_value = parse_bool(payload.get("create_prs_as_draft"))
    if draft_value is not None:
        create_prs_as_draft = draft_value

session_auto_value = parse_bool(session_metadata.get("create_prs_automatically"))
if session_auto_value is not None:
    create_prs_automatically = session_auto_value
session_draft_value = parse_bool(session_metadata.get("create_prs_as_draft"))
if session_draft_value is not None:
    create_prs_as_draft = session_draft_value
automation_payload = session_metadata.get("pr_automation")
if isinstance(automation_payload, dict):
    nested_auto = parse_bool(automation_payload.get("create_prs_automatically"))
    if nested_auto is not None:
        create_prs_automatically = nested_auto
    nested_draft = parse_bool(automation_payload.get("create_prs_as_draft"))
    if nested_draft is not None:
        create_prs_as_draft = nested_draft

override_auto = parse_bool(auto_create_override)
if override_auto is not None:
    create_prs_automatically = override_auto
override_draft = parse_bool(draft_override)
if override_draft is not None:
    create_prs_as_draft = override_draft

print(f"{1 if create_prs_automatically else 0} {1 if create_prs_as_draft else 0}", end="")
PY
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

derive_pr_notes_ref_for_session() {
  local session_id="$1"
  if [[ -z "${session_id}" ]]; then
    printf 'refs/notes/agent-pr\n'
    return
  fi
  PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" python3 - "${session_id}" <<'PY'
try:
    from evander.git_pr_notes import agent_pr_notes_ref_for_session
except Exception:
    import hashlib

    def agent_pr_notes_ref_for_session(session_id: str) -> str:
        session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
        return f"refs/notes/agent-pr/{session_hash}"

import sys

print(agent_pr_notes_ref_for_session(sys.argv[1]), end="")
PY
}

is_agent_pr_notes_ref() {
  local ref_name="$1"
  [[ "${ref_name}" == refs/notes/agent-pr || "${ref_name}" == refs/notes/agent-pr/* ]]
}

select_pushed_notes_ref() {
  local session_id="$1"
  local derived_ref=""
  local candidate
  if (( PR_NOTES_REF_OVERRIDE_SET == 1 )); then
    printf '%s\n' "${PR_NOTES_REF}"
    return
  fi
  if [[ -n "${session_id}" ]]; then
    derived_ref="$(derive_pr_notes_ref_for_session "${session_id}")"
    for candidate in "${PUSHED_AGENT_NOTES_REFS[@]}"; do
      if [[ "${candidate}" == "${derived_ref}" ]]; then
        printf '%s\n' "${candidate}"
        return
      fi
    done
  fi
  for candidate in "${PUSHED_AGENT_NOTES_REFS[@]}"; do
    if [[ "${candidate}" == refs/notes/agent-pr ]]; then
      printf '%s\n' "${candidate}"
      return
    fi
  done
  if [[ -n "${derived_ref}" ]]; then
    printf '%s\n' "${derived_ref}"
    return
  fi
  printf 'refs/notes/agent-pr\n'
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
  local pushed_notes_ref="${3:-}"
  local review_branch
  local session_id
  local pr_notes_ref
  local note_body_file
  local resolved_template_body_file
  local resolved_sha
  local pr_body_file
  local body_truncated
  local body_size_bytes
  local pr_output
  local pr_automation
  local create_prs_automatically
  local create_prs_as_draft

  review_branch="$(derive_review_branch "${agent_branch}")"
  session_id="$(extract_session_id "${agent_branch}" || true)"
  if [[ -n "${pushed_notes_ref}" ]]; then
    pr_notes_ref="${pushed_notes_ref}"
  else
    pr_notes_ref="$(select_pushed_notes_ref "${session_id:-}")"
  fi

  ensure_remote "${TRUSTED_REPO_PATH}" "${AUTO_REMOTE_NAME}" "${BARE_REPO_PATH}"
  promote_branch "${TRUSTED_REPO_PATH}" "${AUTO_REMOTE_NAME}" "${agent_branch}" "${review_branch}"

  if ! git -C "${TRUSTED_REPO_PATH}" fetch "${AUTO_REMOTE_NAME}" \
    "+${pr_notes_ref}:${LOCAL_PR_NOTES_REF}" >/dev/null 2>&1; then
    notify_event \
      "AGENT_PR_NOTES fetch failed (non-fatal)" \
      "repo=$(basename "${TRUSTED_REPO_PATH}") notes_ref=${pr_notes_ref} local_notes_ref=${LOCAL_PR_NOTES_REF} branch=${review_branch} session_id=${session_id:-unknown}"
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
        "repo=$(basename "${TRUSTED_REPO_PATH}") sha=${resolved_sha} notes_ref=${pr_notes_ref} local_notes_ref=${LOCAL_PR_NOTES_REF} branch=${review_branch} session_id=${session_id:-unknown}"
    fi
  else
    notify_event \
      "AGENT_PR_NOTES sha resolution failed (non-fatal)" \
      "repo=$(basename "${TRUSTED_REPO_PATH}") new_sha=${new_sha} branch=${review_branch} session_id=${session_id:-unknown}"
  fi

  resolved_template_body_file=""
  if [[ -z "${PR_FIXED_BODY_FILE}" ]]; then
    resolved_template_body_file="$(resolve_pr_template_body_file "${session_id:-}" "${PR_TEMPLATE_LEGACY_FILE}" 2>/dev/null || true)"
  fi

  USED_FIXED_BODY=0
  USED_AGENT_NOTE=0
  COMPOSED_PR_BODY_FILE=""
  if compose_pr_body_file "${PR_FIXED_BODY_FILE:-${resolved_template_body_file}}" "${note_body_file}"; then
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

  pr_automation="$(resolve_pr_automation_settings "${session_id:-}" 2>/dev/null || printf '1 0')"
  create_prs_automatically="$(printf '%s' "${pr_automation}" | awk '{print $1}')"
  create_prs_as_draft="$(printf '%s' "${pr_automation}" | awk '{print $2}')"

  PR_ARGS=()
  if [[ "${create_prs_automatically}" == "1" ]]; then
    PR_ARGS+=(--create-pr)
  fi
  if [[ "${create_prs_as_draft}" == "1" ]]; then
    PR_ARGS+=(--draft)
  else
    PR_ARGS+=(--no-draft)
  fi
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

  if pr_output="$(EVANDER_CANONICAL_ORIGIN_URL="${CANONICAL_ORIGIN_URL}" "${SCRIPT_DIR}/open_agent_pr.sh" "${PR_ARGS[@]}" 2>&1)"; then
    if [[ "${create_prs_automatically}" == "1" ]]; then
      notify_event \
        "Agent branch promoted and PR prepared" \
        "repo=$(basename "${TRUSTED_REPO_PATH}") branch=${review_branch} session_id=${session_id:-unknown} create_prs_as_draft=${create_prs_as_draft} used_fixed_body=${USED_FIXED_BODY} used_agent_note=${USED_AGENT_NOTE} body_truncated=${body_truncated} ${pr_output}"
    else
      notify_event \
        "Agent branch promoted (PR creation disabled)" \
        "repo=$(basename "${TRUSTED_REPO_PATH}") branch=${review_branch} session_id=${session_id:-unknown} create_prs_as_draft=${create_prs_as_draft} used_fixed_body=${USED_FIXED_BODY} used_agent_note=${USED_AGENT_NOTE} body_truncated=${body_truncated} ${pr_output}"
    fi
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

TRUSTED_REPO_PATH="$(bare_config_value_with_legacy evander.trustedRepoPath agent-sandbox.trustedRepoPath)"
BASE_BRANCH="$(bare_config_value_with_legacy evander.baseBranch agent-sandbox.baseBranch)"
CANONICAL_ORIGIN_URL="$(bare_config_value_with_legacy evander.canonicalOriginUrl agent-sandbox.canonicalOriginUrl)"
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
  if is_agent_pr_notes_ref "${ref_name}"; then
    HAS_AGENT_NOTES_UPDATE=1
    PUSHED_AGENT_NOTES_REFS+=("${ref_name}")
  fi
done < "${UPDATES_FILE}"

while read -r old_sha new_sha ref_name; do
  [[ -n "${ref_name}" ]] || continue

  if is_agent_pr_notes_ref "${ref_name}"; then
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
  pushed_notes_ref=""
  if (( ${#PUSHED_AGENT_NOTES_REFS[@]} == 1 )); then
    pushed_notes_ref="${PUSHED_AGENT_NOTES_REFS[0]}"
  fi
  while read -r agent_branch current_sha; do
    [[ -n "${agent_branch}" && -n "${current_sha}" ]] || continue
    process_agent_branch "${agent_branch}" "${current_sha}" "${pushed_notes_ref}"
  done < <(
    git --git-dir="${BARE_REPO_PATH}" for-each-ref \
      --format='%(refname:lstrip=2) %(objectname)' refs/heads/agents
  )
fi
