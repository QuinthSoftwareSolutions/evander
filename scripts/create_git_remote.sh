#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: create_git_remote.sh <repo-name> [hook-profile]

Internal initializer for Evander-owned bare repositories and deployment mirrors.
Operators should register managed repositories with `evander-cli git-repos register`
instead of invoking this script directly.

Arguments:
  repo-name     Bare repository name. ".git" suffix is optional.
  hook-profile  Reserved for the managed-repository service.

Configuration:
  EVANDER_GIT_ROOT     Internal root that owns the bare repository.
  EVANDER_GIT_USER     Host account that owns the repository.
  EVANDER_RUNTIME_DIR  Runtime root for post-receive event logs.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 1 ]]; then
  show_help >&2
  exit 1
fi

REPO_NAME="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/runtime_config.sh
source "${SCRIPT_DIR}/lib/runtime_config.sh"
load_issue_setup_config

RUNTIME_DIR="${EVANDER_RUNTIME_DIR:-${ROOT_DIR}/runtime}"
GIT_ROOT="${EVANDER_GIT_ROOT:-${RUNTIME_DIR}/managed-git}"
DEFAULT_GIT_USER="${USER:-}"
if [[ -z "${DEFAULT_GIT_USER}" ]]; then
  DEFAULT_GIT_USER="$(id -un 2>/dev/null || echo root)"
fi
GIT_USER="${EVANDER_GIT_USER:-${DEFAULT_GIT_USER}}"
TRIGGER_HOOK_SCRIPT="${ROOT_DIR}/scripts/handle_git_push_trigger.sh"

repair_git_remote_ownership_if_root() {
  local path="$1"
  local target_user="$2"
  local recursive="${3:-1}"
  local target_group

  if [[ "$(id -u)" != "0" || -z "${target_user}" || "${target_user}" == "root" ]]; then
    return 0
  fi
  if ! target_group="$(id -gn "${target_user}" 2>/dev/null)"; then
    echo "Cannot resolve group for Git remote owner '${target_user}'." >&2
    exit 1
  fi
  if [[ "${recursive}" == "1" ]]; then
    chown -R "${target_user}:${target_group}" "${path}"
  else
    chown "${target_user}:${target_group}" "${path}"
  fi
}

case "${REPO_NAME}" in
  *.git) REPO_DIR="${GIT_ROOT}/${REPO_NAME}" ;;
  *) REPO_DIR="${GIT_ROOT}/${REPO_NAME}.git" ;;
esac

mkdir -p "${GIT_ROOT}"
repair_git_remote_ownership_if_root "${GIT_ROOT}" "${GIT_USER}" 0

if [[ ! -d "${REPO_DIR}" ]]; then
  git init --bare "${REPO_DIR}" >/dev/null
  git --git-dir="${REPO_DIR}" config receive.denyCurrentBranch ignore
  git --git-dir="${REPO_DIR}" config gc.auto 0
  layout_marker="$(printf 'evander private ref layout v2\n' | git --git-dir="${REPO_DIR}" hash-object -w --stdin)"
  git --git-dir="${REPO_DIR}" update-ref refs/evander/layout/private-refs-v2 "${layout_marker}"
  touch "${REPO_DIR}/evander-private-refs.lock"
  chmod 644 "${REPO_DIR}/evander-private-refs.lock"
fi

cat > "${REPO_DIR}/hooks/pre-receive" <<'HOOK'
#!/usr/bin/env bash
set -euo pipefail

if [[ "${EVANDER_GIT_AUTH_ENFORCE:-0}" != "1" ]]; then
  cat >/dev/null
  exit 0
fi

decode_auth_value() {
  local plain_value="$1"
  local encoded_value="$2"
  local decoded_value

  if [[ -n "${plain_value}" ]]; then
    printf '%s\n' "${plain_value}"
    return 0
  fi

  if [[ -z "${encoded_value}" ]]; then
    return 0
  fi

  if ! decoded_value="$(printf '%s' "${encoded_value}" | base64 -d 2>/dev/null)"; then
    echo "Invalid base64-encoded Git SSH authorization metadata." >&2
    return 1
  fi
  printf '%s\n' "${decoded_value}"
}

AUTH_ACCESS="${EVANDER_GIT_AUTH_ACCESS:-read-only}"
AUTH_REPO_RAW="$(decode_auth_value "${EVANDER_GIT_AUTH_REPO:-}" "${EVANDER_GIT_AUTH_REPO_B64:-}")"
AUTH_SESSION_ID="$(
  decode_auth_value "${EVANDER_GIT_AUTH_SESSION_ID:-}" "${EVANDER_GIT_AUTH_SESSION_ID_B64:-}"
)"
AUTH_REF_PREFIX="$(
  decode_auth_value "${EVANDER_GIT_AUTH_REF_PREFIX:-}" "${EVANDER_GIT_AUTH_REF_PREFIX_B64:-}"
)"
AUTH_PR_NOTES_REF="$(
  decode_auth_value "${EVANDER_GIT_AUTH_PR_NOTES_REF:-}" "${EVANDER_GIT_AUTH_PR_NOTES_REF_B64:-}"
)"

authorized_pr_notes_ref() {
  local digest

  if [[ -n "${AUTH_PR_NOTES_REF}" ]]; then
    printf '%s\n' "${AUTH_PR_NOTES_REF}"
    return 0
  fi

  digest="$(printf '%s' "${AUTH_SESSION_ID}" | sha256sum | awk '{print $1}')"
  printf 'refs/notes/agent-pr/%s\n' "${digest:0:12}"
}

if [[ "${AUTH_ACCESS}" != "write" ]]; then
  echo "Git SSH key is read-only; repo/session metadata is required for pushes." >&2
  exit 1
fi

if [[ -z "${AUTH_REPO_RAW}" || -z "${AUTH_SESSION_ID}" || -z "${AUTH_REF_PREFIX}" ]]; then
  echo "Git write access requires authorized repo, session, and ref-prefix metadata." >&2
  exit 1
fi

current_git_dir="$(git rev-parse --git-dir)"
if [[ "${current_git_dir}" != /* ]]; then
  current_git_dir="$(pwd)/${current_git_dir}"
fi
current_repo_real="$(realpath -m -- "${current_git_dir}")"
auth_repo_real="$(realpath -m -- "${AUTH_REPO_RAW}")"

if [[ "${auth_repo_real}" != "${current_repo_real}" ]]; then
  echo "Git SSH key is not authorized for repository: ${current_repo_real}" >&2
  exit 1
fi

if [[ -z "${AUTH_SESSION_ID}" || "${AUTH_SESSION_ID}" == */* || "${AUTH_SESSION_ID}" == "." || "${AUTH_SESSION_ID}" == ".." ]]; then
  echo "Authorized Git session id must be a single non-empty ref path component." >&2
  exit 1
fi

expected_prefix="refs/evander/agents/${AUTH_SESSION_ID}/"
if [[ "${AUTH_REF_PREFIX}" == "refs/heads/agents/${AUTH_SESSION_ID}/" ]]; then
  AUTH_REF_PREFIX="${expected_prefix}"
fi
if [[ "${AUTH_REF_PREFIX}" != "${expected_prefix}" ]]; then
  echo "Authorized Git ref prefix must be ${expected_prefix}." >&2
  exit 1
fi

if ! git check-ref-format "${AUTH_REF_PREFIX}probe" >/dev/null 2>&1; then
  echo "Authorized Git ref prefix is not a valid branch namespace: ${AUTH_REF_PREFIX}" >&2
  exit 1
fi

auth_pr_notes_ref="$(authorized_pr_notes_ref)"
if ! git check-ref-format "${auth_pr_notes_ref}" >/dev/null 2>&1; then
  echo "Authorized Git PR notes ref is not valid: ${auth_pr_notes_ref}" >&2
  exit 1
fi

while read -r _oldrev _newrev refname; do
  [[ -n "${refname}" ]] || continue
  case "${refname}" in
    "${AUTH_REF_PREFIX}"*) ;;
    "${auth_pr_notes_ref}") ;;
    *)
      echo "Ref '${refname}' is outside authorized prefix '${AUTH_REF_PREFIX}' or PR notes ref '${auth_pr_notes_ref}'." >&2
      exit 1
      ;;
  esac
done
HOOK
chmod +x "${REPO_DIR}/hooks/pre-receive"

cat > "${REPO_DIR}/hooks/post-receive" <<HOOK
#!/usr/bin/env bash
set -euo pipefail
LOG_DIR="${RUNTIME_DIR}/hook-logs"
LOG_FILE="\${LOG_DIR}/$(basename "${REPO_DIR}").post-receive.log"
mkdir -p "\${LOG_DIR}"
{
  printf '=== %s post-receive start ===\n' "\$(date -Iseconds)"
  updates_file="\$(mktemp)"
  cat > "\${updates_file}"

  trigger_status=0
  if [[ -x "${TRIGGER_HOOK_SCRIPT}" ]]; then
    if cat "\${updates_file}" | "${TRIGGER_HOOK_SCRIPT}" "${REPO_DIR}"; then
      trigger_status=0
    else
      trigger_status=\$?
    fi
  fi

  status="\${trigger_status}"
  rm -f "\${updates_file}"
  printf '=== %s post-receive exit=%s ===\n' "\$(date -Iseconds)" "\${status}"
  exit "\${status}"
} >> "\${LOG_FILE}" 2>&1 || true
HOOK
chmod +x "${REPO_DIR}/hooks/post-receive"
repair_git_remote_ownership_if_root "${REPO_DIR}" "${GIT_USER}"

echo "Initialized Evander internal bare repository: ${REPO_DIR}"
