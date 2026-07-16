#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: git_ssh_command_wrapper.sh

Forced-command wrapper for Evander-managed Git repositories. Every key is
restricted to one exact bare repository. Write keys are additionally restricted
to one agent session branch namespace and its session-scoped notes ref.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

decode_base64_env_value() {
  local value_name="$1"
  local encoded_value="$2"
  local decoded_value

  if ! decoded_value="$(printf '%s' "${encoded_value}" | base64 -d 2>/dev/null)"; then
    echo "Invalid base64-encoded ${value_name}." >&2
    return 1
  fi
  printf '%s\n' "${decoded_value}"
}

decode_auth_value() {
  local plain_value="$1"
  local encoded_value="$2"

  if [[ -n "${plain_value}" ]]; then
    printf '%s\n' "${plain_value}"
  elif [[ -n "${encoded_value}" ]]; then
    decode_base64_env_value "Git SSH authorization metadata" "${encoded_value}"
  fi
}

if [[ -n "${EVANDER_GIT_ROOT_B64:-}" ]]; then
  GIT_ROOT="$(decode_base64_env_value "Git root" "${EVANDER_GIT_ROOT_B64}")"
else
  GIT_ROOT="${EVANDER_GIT_ROOT:-${ROOT_DIR}/runtime/managed-git}"
fi
ROOT_REAL="$(realpath -m -- "${GIT_ROOT}")"
ORIGINAL_COMMAND="${SSH_ORIGINAL_COMMAND:-}"

case "${ORIGINAL_COMMAND}" in
  git-upload-pack\ *|git-upload-archive\ *|git-receive-pack\ *) ;;
  "")
    echo "Missing SSH_ORIGINAL_COMMAND." >&2
    exit 1
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

AUTH_ACCESS="${EVANDER_GIT_AUTH_ACCESS:-read-only}"
AUTH_REPO_RAW="$(
  decode_auth_value "${EVANDER_GIT_AUTH_REPO:-}" "${EVANDER_GIT_AUTH_REPO_B64:-}"
)"
AUTH_SESSION_ID="$(
  decode_auth_value "${EVANDER_GIT_AUTH_SESSION_ID:-}" "${EVANDER_GIT_AUTH_SESSION_ID_B64:-}"
)"
AUTH_REF_PREFIX="$(
  decode_auth_value "${EVANDER_GIT_AUTH_REF_PREFIX:-}" "${EVANDER_GIT_AUTH_REF_PREFIX_B64:-}"
)"
AUTH_PR_NOTES_REF="$(
  decode_auth_value "${EVANDER_GIT_AUTH_PR_NOTES_REF:-}" "${EVANDER_GIT_AUTH_PR_NOTES_REF_B64:-}"
)"

if [[ -z "${AUTH_REPO_RAW}" ]]; then
  echo "Git SSH key is missing exact authorized repository metadata." >&2
  exit 1
fi
if [[ "${AUTH_REPO_RAW}" == /* ]]; then
  AUTH_REPO_REAL="$(realpath -m -- "${AUTH_REPO_RAW}")"
else
  case "${AUTH_REPO_RAW}" in
    *.git) AUTH_REPO_REAL="$(realpath -m -- "${GIT_ROOT}/${AUTH_REPO_RAW}")" ;;
    *) AUTH_REPO_REAL="$(realpath -m -- "${GIT_ROOT}/${AUTH_REPO_RAW}.git")" ;;
  esac
fi
if [[ "${AUTH_REPO_REAL}" != "${REPO_REAL}" ]]; then
  echo "Git SSH key is not authorized for repository: ${REPO_REAL}" >&2
  exit 1
fi

case "${ORIGINAL_COMMAND}" in
  git-upload-pack\ *|git-upload-archive\ *)
    case "${AUTH_ACCESS}" in
      read-only|write) ;;
      *)
        echo "Unsupported Git SSH auth access mode: ${AUTH_ACCESS}" >&2
        exit 1
        ;;
    esac
    ;;
  git-receive-pack\ *)
    if [[ "${AUTH_ACCESS}" != "write" ]]; then
      echo "Git SSH key is read-only; repo/session metadata is required for pushes." >&2
      exit 1
    fi
    if [[ -z "${AUTH_SESSION_ID}" || -z "${AUTH_REF_PREFIX}" || -z "${AUTH_PR_NOTES_REF}" ]]; then
      echo "Git write access requires session, ref-prefix, and notes-ref metadata." >&2
      exit 1
    fi
    if [[ "${AUTH_SESSION_ID}" == */* || "${AUTH_SESSION_ID}" == "." || "${AUTH_SESSION_ID}" == ".." ]]; then
      echo "Authorized Git session id must be a single non-empty ref path component." >&2
      exit 1
    fi
    EXPECTED_PREFIX="refs/heads/agents/${AUTH_SESSION_ID}/"
    if [[ "${AUTH_REF_PREFIX}" != "${EXPECTED_PREFIX}" ]]; then
      echo "Authorized Git ref prefix must be ${EXPECTED_PREFIX}." >&2
      exit 1
    fi
    if ! git check-ref-format "${AUTH_REF_PREFIX}probe" >/dev/null 2>&1 \
      || ! git check-ref-format "${AUTH_PR_NOTES_REF}" >/dev/null 2>&1; then
      echo "Authorized Git ref metadata is invalid." >&2
      exit 1
    fi
    HOOK_PATH="${REPO_REAL}/hooks/pre-receive"
    if [[ ! -x "${HOOK_PATH}" ]] || ! grep -q "EVANDER_GIT_AUTH_ENFORCE" "${HOOK_PATH}"; then
      echo "Managed repository is missing the Evander Git authorization hook." >&2
      exit 1
    fi
    export EVANDER_GIT_AUTH_ENFORCE=1
    export EVANDER_GIT_AUTH_ACCESS=write
    export EVANDER_GIT_AUTH_REPO="${AUTH_REPO_REAL}"
    export EVANDER_GIT_AUTH_SESSION_ID="${AUTH_SESSION_ID}"
    export EVANDER_GIT_AUTH_REF_PREFIX="${AUTH_REF_PREFIX}"
    export EVANDER_GIT_AUTH_PR_NOTES_REF="${AUTH_PR_NOTES_REF}"
    ;;
esac

exec git-shell -c "${ORIGINAL_COMMAND}"
