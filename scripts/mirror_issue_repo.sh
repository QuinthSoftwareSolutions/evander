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
                      Default: on for remote URL input, off for local path input.
  --no-sync-remote    Disable sync for local path input.
  --remote-name NAME  Upstream remote name for fetch/divergence checks. Default: origin
  --refspec SPEC      Mirror only the provided refspec (repeatable). Optional.
  --branch NAME       Convenience alias for --refspec refs/heads/<NAME>:refs/heads/<NAME>
  --force-local       Allow mirroring even when local default branch is behind upstream.
  -h, --help          Show this help message.

Arguments:
  issue-number  Issue identifier used to derive the repo/remote names.
  repo-source   Working tree path or remote URL to mirror.
                Default: /home/jonathan/Documents/Repos/ExampleIndustryAPP
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
REPO_SOURCE="${2:-/home/jonathan/Documents/Repos/ExampleIndustryAPP}"
SOURCE_MODE="local"
if is_remote_source "${REPO_SOURCE}"; then
  SOURCE_MODE="remote"
fi

SYNC_REMOTE=0
if [[ "${SOURCE_MODE}" == "remote" ]]; then
  SYNC_REMOTE=1
elif [[ "${SYNC_REMOTE_MODE}" == "on" ]]; then
  SYNC_REMOTE=1
elif [[ "${SYNC_REMOTE_MODE}" == "off" ]]; then
  SYNC_REMOTE=0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
BARE_REPO_NAME="${ISSUE_NUMBER}_repo"
MIRROR_REMOTE_NAME="${ISSUE_NUMBER}-mirror"
BARE_REPO_PATH="${ROOT_DIR}/git-remotes/${BARE_REPO_NAME}.git"

RESOLVED_REPO_PATH="$(${SCRIPT_DIR}/resolve_repo_source.sh "${REPO_SOURCE}")"

if (( SYNC_REMOTE )) && [[ "${SOURCE_MODE}" == "local" ]]; then
  if ! git -C "${RESOLVED_REPO_PATH}" remote get-url "${REMOTE_NAME}" >/dev/null 2>&1; then
    echo "Remote '${REMOTE_NAME}' not found in ${RESOLVED_REPO_PATH}; cannot --sync-remote." >&2
    exit 1
  fi

  echo "Syncing local source from '${REMOTE_NAME}'..."
  git -C "${RESOLVED_REPO_PATH}" fetch --prune "${REMOTE_NAME}"

  DEFAULT_REMOTE_BRANCH="$(
    git -C "${RESOLVED_REPO_PATH}" symbolic-ref --quiet --short "refs/remotes/${REMOTE_NAME}/HEAD" 2>/dev/null \
      | sed "s#^${REMOTE_NAME}/##"
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
        if (( FORCE_LOCAL == 0 )); then
          echo "Refusing to mirror stale local refs. Rebase/merge first or rerun with --force-local." >&2
          exit 1
        fi
        echo "Continuing due to --force-local." >&2
      fi
    fi
  fi
fi

"${SCRIPT_DIR}/create_git_remote.sh" "${BARE_REPO_NAME}"

if git -C "${RESOLVED_REPO_PATH}" remote get-url "${MIRROR_REMOTE_NAME}" >/dev/null 2>&1; then
  git -C "${RESOLVED_REPO_PATH}" remote set-url "${MIRROR_REMOTE_NAME}" "${BARE_REPO_PATH}"
else
  git -C "${RESOLVED_REPO_PATH}" remote add "${MIRROR_REMOTE_NAME}" "${BARE_REPO_PATH}"
fi

if (( ${#REFSPECS[@]} > 0 )); then
  git -C "${RESOLVED_REPO_PATH}" push --prune "${MIRROR_REMOTE_NAME}" "${REFSPECS[@]}"
else
  git -C "${RESOLVED_REPO_PATH}" push --mirror "${MIRROR_REMOTE_NAME}"
fi
git --git-dir="${BARE_REPO_PATH}" config agent-sandbox.trustedRepoPath "${RESOLVED_REPO_PATH}"

DEFAULT_BASE_BRANCH="$(
  git -C "${RESOLVED_REPO_PATH}" symbolic-ref --quiet --short "refs/remotes/${REMOTE_NAME}/HEAD" 2>/dev/null \
    | sed "s#^${REMOTE_NAME}/##"
)"
if [[ -n "${DEFAULT_BASE_BRANCH}" ]]; then
  git --git-dir="${BARE_REPO_PATH}" config agent-sandbox.baseBranch "${DEFAULT_BASE_BRANCH}"
fi

SOURCE_COMMIT=""
if [[ -n "${DEFAULT_BASE_BRANCH}" ]] && git -C "${RESOLVED_REPO_PATH}" show-ref --verify --quiet "refs/heads/${DEFAULT_BASE_BRANCH}"; then
  SOURCE_COMMIT="$(git -C "${RESOLVED_REPO_PATH}" rev-parse "refs/heads/${DEFAULT_BASE_BRANCH}")"
else
  SOURCE_COMMIT="$(git -C "${RESOLVED_REPO_PATH}" rev-parse HEAD)"
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
