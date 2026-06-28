#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'USAGE'
Usage: mirror_issue_repo.sh [options] <issue-number> [repo-source]

Create or refresh a host bare mirror for a repo using the issue-number naming
convention:
  bare repo:   <issue-number>_repo.git
  host remote: <issue-number>-mirror

Options:
  --sync-remote       Refresh local repo from its upstream before mirroring.
                      Default: on for remote URL input, and on for local path input
                      unless AGENT_SANDBOX_MIRROR_SYNC_REMOTE_DEFAULT=0.
  --no-sync-remote    Disable sync for local path input.
  --remote-name NAME  Upstream remote name for fetch/divergence checks. Default: origin
  --refspec SPEC      Mirror only the provided refspec (repeatable). Optional.
  --branch NAME       Convenience alias for --refspec refs/heads/<NAME>:refs/heads/<NAME>
  --force-local       Allow mirroring divergent local default branches without fast-forwarding.
  -h, --help          Show this help message.

Arguments:
  issue-number  Issue identifier used to derive the repo/remote names.
  repo-source   Working tree path or remote URL to mirror.
                Default: $AGENT_SANDBOX_DEFAULT_REPO_SOURCE

Environment:
  AGENT_SANDBOX_MIRROR_SYNC_REMOTE_DEFAULT
                Default local-source sync mode when no explicit flag is passed.
                Set to 1 (default) to sync local sources; set to 0 to opt out.
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 1 ]]; then
  show_help >&2
  exit 1
fi

SYNC_REMOTE_MODE="auto"
REMOTE_NAME="origin"
FORCE_LOCAL=0
REFSPECS=()

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

fast_forward_local_branch() {
  local repo_path="$1"
  local branch="$2"
  local local_ref="$3"
  local remote_ref="$4"
  local current_branch=""

  current_branch="$(git -C "${repo_path}" branch --show-current 2>/dev/null || true)"
  if [[ "${current_branch}" == "${branch}" ]]; then
    git -C "${repo_path}" merge --ff-only "${remote_ref}"
    return 0
  fi

  git -C "${repo_path}" update-ref "${local_ref}" "${remote_ref}" "${local_ref}"
}

default_base_branch_for_remote() {
  local repo_path="$1"
  local remote_name="$2"
  local preferred_branch="${3:-}"
  local branch=""

  if [[ -n "${preferred_branch}" ]] \
    && {
      git -C "${repo_path}" show-ref --verify --quiet "refs/remotes/${remote_name}/${preferred_branch}" \
        || git -C "${repo_path}" show-ref --verify --quiet "refs/heads/${preferred_branch}"
    }; then
    printf '%s\n' "${preferred_branch}"
    return 0
  fi

  branch="$(
    git -C "${repo_path}" symbolic-ref --quiet --short "refs/remotes/${remote_name}/HEAD" 2>/dev/null \
      | sed "s#^${remote_name}/##" || true
  )"
  if [[ -n "${branch}" ]]; then
    printf '%s\n' "${branch}"
    return 0
  fi

  branch="$(git -C "${repo_path}" branch --show-current 2>/dev/null || true)"
  if [[ -n "${branch}" ]] \
    && git -C "${repo_path}" show-ref --verify --quiet "refs/remotes/${remote_name}/${branch}"; then
    printf '%s\n' "${branch}"
    return 0
  fi

  git -C "${repo_path}" for-each-ref --format='%(refname:strip=3)' "refs/remotes/${remote_name}" \
    | awk '$0 != "HEAD" { print; exit }'
}

preferred_branch_from_refspecs() {
  local refspec normalized source_ref destination_ref branch

  for refspec in "$@"; do
    [[ -n "${refspec}" ]] || continue
    normalized="${refspec#+}"
    if [[ "${normalized}" != *:* ]]; then
      continue
    fi
    source_ref="${normalized%%:*}"
    destination_ref="${normalized#*:}"
    if [[ "${source_ref}" == refs/heads/* ]]; then
      branch="${source_ref#refs/heads/}"
      if [[ "${destination_ref}" == "refs/heads/${branch}" ]]; then
        printf '%s\n' "${branch}"
        return 0
      fi
    fi
  done
}

resolve_canonical_origin_url() {
  local repo_path="$1"
  local remote_name="$2"
  local remote_url=""
  local normalized_url=""
  local mirror_origin_url=""

  if ! remote_url="$(git -C "${repo_path}" remote get-url "${remote_name}" 2>/dev/null)"; then
    return 1
  fi

  normalized_url="$(readlink -f "${remote_url}" 2>/dev/null || printf '%s' "${remote_url}")"
  if [[ "${normalized_url}" =~ /runtime/repo-cache/[^/]+\.git$ ]] \
    && [[ -d "${normalized_url}" ]] \
    && mirror_origin_url="$(git --git-dir="${normalized_url}" remote get-url origin 2>/dev/null)"; then
    printf '%s\n' "${mirror_origin_url}"
    return 0
  fi

  printf '%s\n' "${remote_url}"
}

reset_issue_remote_refs() {
  local bare_repo_path="$1"
  local -a refs=()

  mapfile -t refs < <(
    git --git-dir="${bare_repo_path}" for-each-ref --format='%(refname)' \
      refs/heads \
      refs/tags \
      refs/notes/agent-pr \
      refs/notes/agent-pr-cache
  )

  if (( ${#refs[@]} == 0 )); then
    return 0
  fi

  {
    for ref in "${refs[@]}"; do
      printf 'delete %s\n' "${ref}"
    done
  } | git --git-dir="${bare_repo_path}" update-ref --stdin
}

build_safe_default_refspecs() {
  local repo_path="$1"
  local default_base_branch="$2"
  local -a refspecs=()
  local -a remote_head_refspecs=()

  while IFS= read -r ref; do
    [[ -n "${ref}" ]] || continue
    local branch_name="${ref#refs/heads/}"
    case "${branch_name}" in
      agents/*|review/*)
        continue
        ;;
    esac
    refspecs+=("${ref}:${ref}")
  done < <(git -C "${repo_path}" for-each-ref --format='%(refname)' refs/heads)

  while IFS= read -r ref; do
    [[ -n "${ref}" ]] || continue
    refspecs+=("${ref}:${ref}")
    done < <(git -C "${repo_path}" for-each-ref --format='%(refname)' refs/tags)

  if (( ${#refspecs[@]} == 0 )); then
    while IFS= read -r ref; do
      [[ -n "${ref}" ]] || continue
      local remote_branch_name="${ref#refs/remotes/${REMOTE_NAME}/}"
      case "${remote_branch_name}" in
        HEAD|agents/*|review/*)
          continue
          ;;
      esac
      remote_head_refspecs+=("${ref}:refs/heads/${remote_branch_name}")
    done < <(git -C "${repo_path}" for-each-ref --format='%(refname)' "refs/remotes/${REMOTE_NAME}")
  fi

  if (( ${#refspecs[@]} == 0 && ${#remote_head_refspecs[@]} > 0 )); then
    refspecs=("${remote_head_refspecs[@]}")
  fi

  if (( ${#refspecs[@]} == 0 )); then
    if [[ -n "${default_base_branch}" ]] \
      && git -C "${repo_path}" show-ref --verify --quiet "refs/heads/${default_base_branch}"; then
      refspecs+=("refs/heads/${default_base_branch}:refs/heads/${default_base_branch}")
    elif [[ -n "${default_base_branch}" ]] \
      && git -C "${repo_path}" show-ref --verify --quiet "refs/remotes/${REMOTE_NAME}/${default_base_branch}"; then
      refspecs+=("refs/remotes/${REMOTE_NAME}/${default_base_branch}:refs/heads/${default_base_branch}")
    elif git -C "${repo_path}" rev-parse --verify HEAD >/dev/null 2>&1; then
      local head_branch
      head_branch="$(git -C "${repo_path}" branch --show-current)"
      if [[ -n "${head_branch}" ]]; then
        refspecs+=("refs/heads/${head_branch}:refs/heads/${head_branch}")
      fi
    fi
  fi

  printf '%s\n' "${refspecs[@]}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sync-remote)
      SYNC_REMOTE_MODE="on"
      shift
      ;;
    --no-sync-remote)
      SYNC_REMOTE_MODE="off"
      shift
      ;;
    --remote-name)
      REMOTE_NAME="${2:-}"
      if [[ -z "${REMOTE_NAME}" ]]; then
        echo "Missing value for --remote-name" >&2
        exit 1
      fi
      shift 2
      ;;
    --refspec)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for --refspec" >&2
        exit 1
      fi
      REFSPECS+=("$2")
      shift 2
      ;;
    --branch)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for --branch" >&2
        exit 1
      fi
      REFSPECS+=("refs/heads/$2:refs/heads/$2")
      shift 2
      ;;
    --force-local)
      FORCE_LOCAL=1
      shift
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option: $1" >&2
      show_help >&2
      exit 1
      ;;
    *)
      break
      ;;
  esac
done

if [[ $# -lt 1 || $# -gt 2 ]]; then
  show_help >&2
  exit 1
fi

ISSUE_NUMBER="$1"
REPO_SOURCE="${2:-${AGENT_SANDBOX_DEFAULT_REPO_SOURCE:-}}"
if [[ -z "${REPO_SOURCE}" ]]; then
  echo "Missing repo-source: pass [repo-source] or set AGENT_SANDBOX_DEFAULT_REPO_SOURCE." >&2
  exit 1
fi
SOURCE_MODE="local"
if is_remote_source "${REPO_SOURCE}"; then
  SOURCE_MODE="remote"
fi

LOCAL_SYNC_REMOTE_DEFAULT="${AGENT_SANDBOX_MIRROR_SYNC_REMOTE_DEFAULT:-1}"
if [[ "${LOCAL_SYNC_REMOTE_DEFAULT}" != "0" && "${LOCAL_SYNC_REMOTE_DEFAULT}" != "1" ]]; then
  echo "Invalid AGENT_SANDBOX_MIRROR_SYNC_REMOTE_DEFAULT='${LOCAL_SYNC_REMOTE_DEFAULT}'; expected 0 or 1." >&2
  exit 1
fi

SYNC_REMOTE=0
if [[ "${SOURCE_MODE}" == "remote" ]]; then
  SYNC_REMOTE=1
elif [[ "${SYNC_REMOTE_MODE}" == "on" ]]; then
  SYNC_REMOTE=1
elif [[ "${SYNC_REMOTE_MODE}" == "off" ]]; then
  SYNC_REMOTE=0
elif [[ "${LOCAL_SYNC_REMOTE_DEFAULT}" == "1" ]]; then
  SYNC_REMOTE=1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BARE_REPO_NAME="${ISSUE_NUMBER}_repo"
MIRROR_REMOTE_NAME="${ISSUE_NUMBER}-mirror"
BARE_REPO_PATH="${ROOT_DIR}/git-remotes/${BARE_REPO_NAME}.git"

RESOLVED_REPO_PATH="$(${SCRIPT_DIR}/resolve_repo_source.sh "${REPO_SOURCE}")"
PREFERRED_BASE_BRANCH="$(preferred_branch_from_refspecs "${REFSPECS[@]}")"

if (( SYNC_REMOTE )) && [[ "${SOURCE_MODE}" == "local" ]]; then
  if ! git -C "${RESOLVED_REPO_PATH}" remote get-url "${REMOTE_NAME}" >/dev/null 2>&1; then
    echo "Remote '${REMOTE_NAME}' not found in ${RESOLVED_REPO_PATH}; cannot --sync-remote." >&2
    exit 1
  fi

  echo "Syncing local source from '${REMOTE_NAME}'..."
  git -C "${RESOLVED_REPO_PATH}" fetch --prune "${REMOTE_NAME}"

  DEFAULT_REMOTE_BRANCH="$(
    default_base_branch_for_remote "${RESOLVED_REPO_PATH}" "${REMOTE_NAME}" "${PREFERRED_BASE_BRANCH}"
  )"
  if [[ -n "${DEFAULT_REMOTE_BRANCH}" ]]; then
    LOCAL_DEFAULT_REF="refs/heads/${DEFAULT_REMOTE_BRANCH}"
    REMOTE_DEFAULT_REF="refs/remotes/${REMOTE_NAME}/${DEFAULT_REMOTE_BRANCH}"

    if git -C "${RESOLVED_REPO_PATH}" show-ref --verify --quiet "${LOCAL_DEFAULT_REF}" \
      && git -C "${RESOLVED_REPO_PATH}" show-ref --verify --quiet "${REMOTE_DEFAULT_REF}"; then
      read -r LOCAL_AHEAD REMOTE_AHEAD < <(
        git -C "${RESOLVED_REPO_PATH}" rev-list --left-right --count \
          "${LOCAL_DEFAULT_REF}...${REMOTE_DEFAULT_REF}"
      )

      if (( REMOTE_AHEAD > 0 )); then
        echo "Divergence detected for ${DEFAULT_REMOTE_BRANCH}:" >&2
        echo "  local branch ${DEFAULT_REMOTE_BRANCH} is behind ${REMOTE_NAME}/${DEFAULT_REMOTE_BRANCH}" >&2
        echo "  ahead=${LOCAL_AHEAD} behind=${REMOTE_AHEAD}" >&2
        if (( FORCE_LOCAL == 0 && LOCAL_AHEAD == 0 )); then
          echo "Fast-forwarding local ${DEFAULT_REMOTE_BRANCH} from ${REMOTE_NAME}/${DEFAULT_REMOTE_BRANCH}..." >&2
          fast_forward_local_branch \
            "${RESOLVED_REPO_PATH}" \
            "${DEFAULT_REMOTE_BRANCH}" \
            "${LOCAL_DEFAULT_REF}" \
            "${REMOTE_DEFAULT_REF}"
        elif (( FORCE_LOCAL == 0 )); then
          echo "Refusing to mirror divergent local refs. Rebase/merge first or rerun with --force-local." >&2
          exit 1
        else
          echo "Continuing due to --force-local." >&2
        fi
      fi
    fi
  fi
fi

"${SCRIPT_DIR}/create_git_remote.sh" "${BARE_REPO_NAME}"
git --git-dir="${BARE_REPO_PATH}" config agent-sandbox.trustedRepoPath "${RESOLVED_REPO_PATH}"
git --git-dir="${BARE_REPO_PATH}" config agent-sandbox.mirrorSource "${REPO_SOURCE}"
git --git-dir="${BARE_REPO_PATH}" config agent-sandbox.mirrorRemoteName "${REMOTE_NAME}"
git --git-dir="${BARE_REPO_PATH}" config agent-sandbox.mirrorSyncRemote "${SYNC_REMOTE}"
git --git-dir="${BARE_REPO_PATH}" config agent-sandbox.mirrorForceLocal "${FORCE_LOCAL}"
git --git-dir="${BARE_REPO_PATH}" config --unset-all agent-sandbox.mirrorRefspec >/dev/null 2>&1 || true

CANONICAL_ORIGIN_URL=""
if [[ "${SOURCE_MODE}" == "remote" ]]; then
  CANONICAL_ORIGIN_URL="${REPO_SOURCE}"
elif CANONICAL_ORIGIN_URL="$(resolve_canonical_origin_url "${RESOLVED_REPO_PATH}" "${REMOTE_NAME}" 2>/dev/null)"; then
  :
fi
if [[ -n "${CANONICAL_ORIGIN_URL}" ]]; then
  git --git-dir="${BARE_REPO_PATH}" config agent-sandbox.canonicalOriginUrl "${CANONICAL_ORIGIN_URL}"
fi

if (( ${#REFSPECS[@]} > 0 )); then
  for refspec in "${REFSPECS[@]}"; do
    git --git-dir="${BARE_REPO_PATH}" config --add agent-sandbox.mirrorRefspec "${refspec}"
  done
fi

DEFAULT_BASE_BRANCH="$(
  default_base_branch_for_remote "${RESOLVED_REPO_PATH}" "${REMOTE_NAME}" "${PREFERRED_BASE_BRANCH}"
)"
if [[ -n "${DEFAULT_BASE_BRANCH}" ]]; then
  git --git-dir="${BARE_REPO_PATH}" config agent-sandbox.baseBranch "${DEFAULT_BASE_BRANCH}"
  git --git-dir="${BARE_REPO_PATH}" symbolic-ref HEAD "refs/heads/${DEFAULT_BASE_BRANCH}"
fi

if git -C "${RESOLVED_REPO_PATH}" remote get-url "${MIRROR_REMOTE_NAME}" >/dev/null 2>&1; then
  git -C "${RESOLVED_REPO_PATH}" remote set-url "${MIRROR_REMOTE_NAME}" "${BARE_REPO_PATH}"
else
  git -C "${RESOLVED_REPO_PATH}" remote add "${MIRROR_REMOTE_NAME}" "${BARE_REPO_PATH}"
fi

if (( ${#REFSPECS[@]} > 0 )); then
  git -C "${RESOLVED_REPO_PATH}" push --prune "${MIRROR_REMOTE_NAME}" "${REFSPECS[@]}"
else
  reset_issue_remote_refs "${BARE_REPO_PATH}"
  mapfile -t DEFAULT_REFSPECS < <(build_safe_default_refspecs "${RESOLVED_REPO_PATH}" "${DEFAULT_BASE_BRANCH}")
  FILTERED_DEFAULT_REFSPECS=()
  for refspec in "${DEFAULT_REFSPECS[@]}"; do
    [[ -n "${refspec}" ]] || continue
    FILTERED_DEFAULT_REFSPECS+=("${refspec}")
  done
  DEFAULT_REFSPECS=("${FILTERED_DEFAULT_REFSPECS[@]}")
  if (( ${#DEFAULT_REFSPECS[@]} == 0 )); then
    echo "Could not determine any safe refs to mirror from ${RESOLVED_REPO_PATH}." >&2
    exit 1
  fi
  git -C "${RESOLVED_REPO_PATH}" push --prune "${MIRROR_REMOTE_NAME}" "${DEFAULT_REFSPECS[@]}"
fi

SOURCE_COMMIT=""
if [[ -n "${DEFAULT_BASE_BRANCH}" ]] && git -C "${RESOLVED_REPO_PATH}" show-ref --verify --quiet "refs/heads/${DEFAULT_BASE_BRANCH}"; then
  SOURCE_COMMIT="$(git -C "${RESOLVED_REPO_PATH}" rev-parse "refs/heads/${DEFAULT_BASE_BRANCH}")"
elif [[ -n "${DEFAULT_BASE_BRANCH}" ]] && git -C "${RESOLVED_REPO_PATH}" show-ref --verify --quiet "refs/remotes/${REMOTE_NAME}/${DEFAULT_BASE_BRANCH}"; then
  SOURCE_COMMIT="$(git -C "${RESOLVED_REPO_PATH}" rev-parse "refs/remotes/${REMOTE_NAME}/${DEFAULT_BASE_BRANCH}")"
else
  SOURCE_COMMIT="$(git -C "${RESOLVED_REPO_PATH}" rev-parse --verify HEAD)"
fi

echo
echo "Host mirror ready:"
echo "  repo:   ${RESOLVED_REPO_PATH}"
echo "  source: ${REPO_SOURCE} (${SOURCE_MODE})"
echo "  remote: ${MIRROR_REMOTE_NAME}"
echo "  bare:   ${BARE_REPO_PATH}"
echo "  sync:   ${SYNC_REMOTE}"
echo "  base:   ${DEFAULT_BASE_BRANCH:-unknown}"
echo "  commit: ${SOURCE_COMMIT}"
