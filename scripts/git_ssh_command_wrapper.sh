#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: git_ssh_command_wrapper.sh

Forced-command wrapper used by host authorized_keys entries for guest Git SSH
access. It validates that the requested repository lives under the configured
Git root, auto-refreshes issue mirrors for guest read commands when mirror
metadata is available, and then hands the command to git-shell.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/runtime_config.sh
source "${SCRIPT_DIR}/lib/runtime_config.sh"
load_issue_setup_config

GIT_ROOT="${AGENT_SANDBOX_GIT_ROOT:-${ROOT_DIR}/git-remotes}"
ROOT_REAL="$(realpath -m -- "${GIT_ROOT}")"
ORIGINAL_COMMAND="${SSH_ORIGINAL_COMMAND:-}"
MIRROR_SCRIPT="${SCRIPT_DIR}/mirror_issue_repo.sh"
RESOLVE_REPO_SOURCE_SCRIPT="${SCRIPT_DIR}/resolve_repo_source.sh"

if [[ -z "${ORIGINAL_COMMAND}" ]]; then
  echo "Missing SSH_ORIGINAL_COMMAND." >&2
  exit 1
fi

case "${ORIGINAL_COMMAND}" in
  git-upload-pack\ *|git-receive-pack\ *|git-upload-archive\ *)
    ;;
  *)
    echo "Unsupported Git SSH command." >&2
    exit 1
    ;;
esac

REPO_PATH="${ORIGINAL_COMMAND#* }"

if [[ "${REPO_PATH}" == \'*\' ]]; then
  REPO_PATH="${REPO_PATH#\'}"
  REPO_PATH="${REPO_PATH%\'}"
elif [[ "${REPO_PATH}" == \"*\" ]]; then
  REPO_PATH="${REPO_PATH#\"}"
  REPO_PATH="${REPO_PATH%\"}"
fi

REPO_REAL="$(realpath -m -- "${REPO_PATH}")"

case "${REPO_REAL}" in
  "${ROOT_REAL}"/*) ;;
  *)
    echo "Repository path is outside the allowed Git root." >&2
    exit 1
    ;;
esac

if [[ ! -d "${REPO_REAL}" ]]; then
  echo "Repository does not exist: ${REPO_REAL}" >&2
  exit 1
fi

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

reset_mirrored_repo_refs() {
  local bare_repo_path="$1"
  local -a refs=()

  mapfile -t refs < <(
    git --git-dir="${bare_repo_path}" for-each-ref --format='%(refname)' \
      refs/heads \
      refs/tags
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

clear_staged_mirror_refs() {
  local bare_repo_path="$1"
  local stage_namespace="$2"
  local -a refs=()

  mapfile -t refs < <(
    git --git-dir="${bare_repo_path}" for-each-ref --format='%(refname)' "${stage_namespace}"
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

stage_mirror_fetch_refspec() {
  local refspec="$1"
  local stage_namespace="$2"
  local force_prefix=""
  local source_ref
  local destination_ref

  if [[ "${refspec}" == +* ]]; then
    force_prefix="+"
    refspec="${refspec#+}"
  fi

  source_ref="${refspec%%:*}"
  destination_ref="${refspec#*:}"

  if [[ -z "${source_ref}" || -z "${destination_ref}" || "${source_ref}" == "${destination_ref}" ]]; then
    echo "Unsupported mirror refspec: ${force_prefix}${refspec}" >&2
    exit 1
  fi

  destination_ref="${destination_ref#refs/}"
  printf '%s%s:%s/%s\n' "${force_prefix}" "${source_ref}" "${stage_namespace}" "${destination_ref}"
}

apply_staged_mirror_refs() {
  local bare_repo_path="$1"
  local stage_namespace="$2"
  local -A current_refs=()
  local -A staged_refs=()
  local ref
  local oid
  local live_ref

  while read -r ref oid; do
    [[ -n "${ref}" && -n "${oid}" ]] || continue
    current_refs["${ref}"]="${oid}"
  done < <(
    git --git-dir="${bare_repo_path}" for-each-ref --format='%(refname) %(objectname)' refs/heads refs/tags
  )

  while read -r ref oid; do
    [[ -n "${ref}" && -n "${oid}" ]] || continue
    live_ref="refs/${ref#"${stage_namespace}/"}"
    staged_refs["${live_ref}"]="${oid}"
  done < <(
    git --git-dir="${bare_repo_path}" for-each-ref --format='%(refname) %(objectname)' "${stage_namespace}"
  )

  {
    for ref in "${!current_refs[@]}"; do
      if [[ -z "${staged_refs[${ref}]+x}" ]]; then
        printf 'delete %s %s\n' "${ref}" "${current_refs[${ref}]}"
      fi
    done

    for ref in "${!staged_refs[@]}"; do
      if [[ -n "${current_refs[${ref}]+x}" ]]; then
        printf 'update %s %s %s\n' "${ref}" "${staged_refs[${ref}]}" "${current_refs[${ref}]}"
      else
        printf 'create %s %s\n' "${ref}" "${staged_refs[${ref}]}"
      fi
    done
  } | git --git-dir="${bare_repo_path}" update-ref --stdin
}

build_safe_default_refspecs() {
  local repo_path="$1"
  local remote_name="$2"
  local default_base_branch="$3"
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
      local remote_branch_name="${ref#refs/remotes/${remote_name}/}"
      case "${remote_branch_name}" in
        HEAD|agents/*|review/*)
          continue
          ;;
      esac
      remote_head_refspecs+=("${ref}:refs/heads/${remote_branch_name}")
    done < <(git -C "${repo_path}" for-each-ref --format='%(refname)' "refs/remotes/${remote_name}")
  fi

  if (( ${#refspecs[@]} == 0 && ${#remote_head_refspecs[@]} > 0 )); then
    refspecs=("${remote_head_refspecs[@]}")
  fi

  if (( ${#refspecs[@]} == 0 )); then
    if [[ -n "${default_base_branch}" ]] \
      && git -C "${repo_path}" show-ref --verify --quiet "refs/heads/${default_base_branch}"; then
      refspecs+=("refs/heads/${default_base_branch}:refs/heads/${default_base_branch}")
    elif [[ -n "${default_base_branch}" ]] \
      && git -C "${repo_path}" show-ref --verify --quiet "refs/remotes/${remote_name}/${default_base_branch}"; then
      refspecs+=("refs/remotes/${remote_name}/${default_base_branch}:refs/heads/${default_base_branch}")
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

build_remote_tracking_refspecs() {
  local repo_path="$1"
  local remote_name="$2"
  local -a refspecs=()

  while IFS= read -r ref; do
    [[ -n "${ref}" ]] || continue
    local remote_branch_name="${ref#refs/remotes/${remote_name}/}"
    case "${remote_branch_name}" in
      HEAD|agents/*|review/*)
        continue
        ;;
    esac
    refspecs+=("${ref}:refs/heads/${remote_branch_name}")
  done < <(git -C "${repo_path}" for-each-ref --format='%(refname)' "refs/remotes/${remote_name}")

  printf '%s\n' "${refspecs[@]}"
}

auto_refresh_mirror() {
  local repo_real="$1"
  local original_command="$2"
  local mirror_source mirror_remote_name mirror_sync_remote mirror_force_local source_mode resolved_repo_path
  local default_base_branch local_default_ref remote_default_ref
  local local_ahead remote_ahead
  local sync_failed=0
  local stage_namespace="refs/agent-sandbox/staged-mirror"
  local -a mirror_args=()
  local -a mirror_refspecs=()
  local -a fetch_refspecs=()
  local -a staged_fetch_refspecs=()

  case "${original_command}" in
    git-upload-pack\ *|git-upload-archive\ *)
      ;;
    *)
      return 0
      ;;
  esac

  mirror_source="$(git --git-dir="${repo_real}" config --get agent-sandbox.mirrorSource || true)"
  if [[ -z "${mirror_source}" ]]; then
    return 0
  fi

  mirror_remote_name="$(git --git-dir="${repo_real}" config --get agent-sandbox.mirrorRemoteName || true)"
  mirror_sync_remote="$(git --git-dir="${repo_real}" config --get agent-sandbox.mirrorSyncRemote || true)"
  mirror_force_local="$(git --git-dir="${repo_real}" config --get agent-sandbox.mirrorForceLocal || true)"
  mapfile -t mirror_refspecs < <(git --git-dir="${repo_real}" config --get-all agent-sandbox.mirrorRefspec || true)

  if [[ -n "${mirror_remote_name}" ]]; then
    mirror_args+=(--remote-name "${mirror_remote_name}")
  fi
  case "${mirror_sync_remote}" in
    1|true|yes|on)
      mirror_args+=(--sync-remote)
      ;;
    0|false|no|off)
      mirror_args+=(--no-sync-remote)
      ;;
  esac
  case "${mirror_force_local}" in
    1|true|yes|on)
      mirror_args+=(--force-local)
      ;;
  esac
  for refspec in "${mirror_refspecs[@]}"; do
    [[ -n "${refspec}" ]] || continue
    mirror_args+=(--refspec "${refspec}")
  done

  source_mode="local"
  if is_remote_source "${mirror_source}"; then
    source_mode="remote"
  fi

  exec 9>"${repo_real}/agent-sandbox-sync.lock"
  flock 9

  if [[ "${repo_real}" == *_repo.git ]]; then
    local repo_name issue_number
    repo_name="$(basename "${repo_real}")"
    issue_number="${repo_name%_repo.git}"
    "${MIRROR_SCRIPT}" "${mirror_args[@]}" "${issue_number}" "${mirror_source}" >/dev/null
    return 0
  fi

  if [[ ! -x "${RESOLVE_REPO_SOURCE_SCRIPT}" ]]; then
    echo "Missing repo refresh helper: ${RESOLVE_REPO_SOURCE_SCRIPT}" >&2
    exit 1
  fi

  resolved_repo_path="$("${RESOLVE_REPO_SOURCE_SCRIPT}" "${mirror_source}")"

  if [[ "${source_mode}" == "local" ]] && [[ "${mirror_sync_remote}" =~ ^(1|true|yes|on)$ ]]; then
    if ! git -C "${resolved_repo_path}" remote get-url "${mirror_remote_name}" >/dev/null 2>&1; then
      echo "Remote '${mirror_remote_name}' not found in ${resolved_repo_path}; cannot refresh shared mirror." >&2
      exit 1
    fi

    if ! git -C "${resolved_repo_path}" fetch --prune "${mirror_remote_name}" >/dev/null 2>&1; then
      sync_failed=1
    fi

    default_base_branch="$(
      git -C "${resolved_repo_path}" symbolic-ref --quiet --short "refs/remotes/${mirror_remote_name}/HEAD" 2>/dev/null \
        | sed "s#^${mirror_remote_name}/##" || true
    )"
    if (( sync_failed == 0 )) && [[ -n "${default_base_branch}" ]]; then
      local_default_ref="refs/heads/${default_base_branch}"
      remote_default_ref="refs/remotes/${mirror_remote_name}/${default_base_branch}"
      if git -C "${resolved_repo_path}" show-ref --verify --quiet "${local_default_ref}" \
        && git -C "${resolved_repo_path}" show-ref --verify --quiet "${remote_default_ref}"; then
        read -r local_ahead remote_ahead < <(
          git -C "${resolved_repo_path}" rev-list --left-right --count "${local_default_ref}...${remote_default_ref}"
        )
        if (( remote_ahead > 0 )) && [[ ! "${mirror_force_local}" =~ ^(1|true|yes|on)$ ]]; then
          echo "Refusing to mirror stale local refs from ${resolved_repo_path}; local ${default_base_branch} is behind ${mirror_remote_name}/${default_base_branch}." >&2
          exit 1
        fi
      fi
    fi
  else
    default_base_branch="$(
      git -C "${resolved_repo_path}" symbolic-ref --quiet --short "refs/remotes/${mirror_remote_name}/HEAD" 2>/dev/null \
        | sed "s#^${mirror_remote_name}/##" || true
    )"
  fi

  if (( ${#mirror_refspecs[@]} > 0 )); then
    fetch_refspecs=("${mirror_refspecs[@]}")
  elif [[ "${source_mode}" == "local" ]] && [[ "${mirror_sync_remote}" =~ ^(1|true|yes|on)$ ]]; then
    mapfile -t fetch_refspecs < <(build_remote_tracking_refspecs "${resolved_repo_path}" "${mirror_remote_name}")
    if (( ${#fetch_refspecs[@]} == 0 )); then
      mapfile -t fetch_refspecs < <(
        build_safe_default_refspecs "${resolved_repo_path}" "${mirror_remote_name}" "${default_base_branch}"
      )
    fi
  else
    mapfile -t fetch_refspecs < <(
      build_safe_default_refspecs "${resolved_repo_path}" "${mirror_remote_name}" "${default_base_branch}"
    )
  fi

  if (( ${#fetch_refspecs[@]} == 0 )); then
    echo "No safe refs found to mirror from ${resolved_repo_path}." >&2
    exit 1
  fi

  clear_staged_mirror_refs "${repo_real}" "${stage_namespace}"
  for refspec in "${fetch_refspecs[@]}"; do
    staged_fetch_refspecs+=("$(stage_mirror_fetch_refspec "${refspec}" "${stage_namespace}")")
  done
  git --git-dir="${repo_real}" fetch --prune "${resolved_repo_path}" "${staged_fetch_refspecs[@]}" >/dev/null
  apply_staged_mirror_refs "${repo_real}" "${stage_namespace}"
  clear_staged_mirror_refs "${repo_real}" "${stage_namespace}"

  if [[ -n "${default_base_branch}" ]]; then
    git --git-dir="${repo_real}" config agent-sandbox.baseBranch "${default_base_branch}"
    git --git-dir="${repo_real}" symbolic-ref HEAD "refs/heads/${default_base_branch}" >/dev/null
  fi
}

auto_refresh_mirror "${REPO_REAL}" "${ORIGINAL_COMMAND}"

exec git-shell -c "${ORIGINAL_COMMAND}"
