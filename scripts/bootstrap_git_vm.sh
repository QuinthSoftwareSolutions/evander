#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: bootstrap_git_vm.sh <vm-name> [vm-user]

Internal guest provisioner for managed-repository SSH access. The control plane
invokes this after resolving a managed_repo_id to one exact bare repository.

Arguments:
  vm-name   libvirt domain name.
  vm-user   Guest user to configure. Default: agent

Environment:
  SSH_KEY_PATH                    Private key matching the guest authorized key.
  EVANDER_GIT_HOST          Host SSH endpoint for guest Git access.
  EVANDER_GIT_USER          Host user that owns the bare remotes.
  EVANDER_GIT_ROOT          Host directory containing bare remotes.
  EVANDER_GIT_SSH_ALIAS     Guest SSH host alias. Default: evander-git-host
  EVANDER_GIT_KEY_NAME      Guest key filename. Default: evander-git
  EVANDER_GIT_ALLOWED_FROM  Source CIDR allowed in the host authorized_keys entry.
  EVANDER_GIT_REPO          Bare repo path or name under EVANDER_GIT_ROOT to allow access.
  EVANDER_GIT_SESSION_ID    Session id allowed to push refs/evander/agents/<session-id>/...
                            Defaults to EVANDER_SESSION_ID from the guest session env.
  EVANDER_GIT_READ_ONLY     Set to 1 to force repo-scoped read-only Git SSH access.
  EVANDER_GIT_REMOVE_LEGACY_KEY
                            Set to 1 while replacing the pre-grant-model evander-git key.
  EVANDER_GIT_REQUIRE_WRITE_METADATA
                            Set to 1 to fail instead of installing read-only access
                            when EVANDER_GIT_REPO/session metadata is unavailable.
  This script is an internal implementation surface; operator clone commands use
  managed_repo_id and do not pass repository paths.
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

VM_NAME="$1"
VM_USER="${2:-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
# shellcheck source=lib/guest_ssh.sh
source "${SCRIPT_DIR}/lib/guest_ssh.sh"
# shellcheck source=lib/runtime_config.sh
source "${SCRIPT_DIR}/lib/runtime_config.sh"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"
SSH_KEY_PATH="${SSH_KEY_PATH:-${CALLER_HOME}/.ssh/id_ed25519}"

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  SSH_KEY_PATH="${CALLER_HOME}/.ssh/id_rsa"
fi

load_issue_setup_config

GIT_HOST="${EVANDER_GIT_HOST:-192.168.250.1}"
GIT_USER="${EVANDER_GIT_USER:-${CALLER_USER}}"
GIT_USER_HOME="$(getent passwd "${GIT_USER}" | cut -d: -f6)"
if [[ -z "${GIT_USER_HOME}" ]]; then
  echo "Cannot resolve home directory for Git user: ${GIT_USER}" >&2
  exit 1
fi
GIT_ROOT="${EVANDER_GIT_ROOT:-${ROOT_DIR}/runtime/managed-git}"
GIT_ROOT_REAL="$(realpath -m -- "${GIT_ROOT}")"
GIT_SSH_ALIAS="${EVANDER_GIT_SSH_ALIAS:-evander-git-host}"
GIT_KEY_NAME="${EVANDER_GIT_KEY_NAME:-evander-git}"
if [[ ! "${GIT_KEY_NAME}" =~ ^[A-Za-z0-9._-]+$ ]]; then
  echo "EVANDER_GIT_KEY_NAME contains unsupported characters." >&2
  exit 1
fi
GIT_ALLOWED_FROM="${EVANDER_GIT_ALLOWED_FROM:-192.168.250.0/24}"
WRAPPER_PATH="${ROOT_DIR}/scripts/git_ssh_command_wrapper.sh"
AUTHORIZED_KEYS_PATH="${GIT_USER_HOME}/.ssh/authorized_keys"
GIT_AUTH_REPO_INPUT="${EVANDER_GIT_REPO:-${EVANDER_GIT_AUTH_REPO:-}}"
GIT_SESSION_ID="${EVANDER_GIT_SESSION_ID:-${EVANDER_SESSION_ID:-}}"
GIT_REMOVE_LEGACY_KEY="${EVANDER_GIT_REMOVE_LEGACY_KEY:-0}"
case "${GIT_REMOVE_LEGACY_KEY}" in
  0|1) ;;
  *)
    echo "EVANDER_GIT_REMOVE_LEGACY_KEY must be 0 or 1." >&2
    exit 1
    ;;
esac
SSH_OPTS=(
  -i "${SSH_KEY_PATH}"
)

is_truthy() {
  case "${1:-}" in
    1|true|yes|on) return 0 ;;
    *) return 1 ;;
  esac
}

one_line_base64() {
  printf '%s' "$1" | base64 | tr -d '\n'
}

agent_pr_notes_ref_for_session() {
  local session_id="$1"
  local digest
  digest="$(printf '%s' "${session_id}" | sha256sum | awk '{print $1}')"
  printf 'refs/notes/agent-pr/%s\n' "${digest:0:12}"
}

resolve_git_auth_repo() {
  local repo_value="$1"
  local repo_path

  if [[ "${repo_value}" == /* ]]; then
    repo_path="${repo_value}"
  else
    case "${repo_value}" in
      *.git) repo_path="${GIT_ROOT}/${repo_value}" ;;
      *) repo_path="${GIT_ROOT}/${repo_value}.git" ;;
    esac
  fi

  realpath -m -- "${repo_path}"
}

validate_git_session_id() {
  local session_id="$1"

  if [[ -z "${session_id}" || "${session_id}" == */* || "${session_id}" == "." || "${session_id}" == ".." ]]; then
    return 1
  fi

  git check-ref-format "refs/evander/agents/${session_id}/probe" >/dev/null 2>&1
}

escape_authorized_keys_command() {
  local command_value="$1"
  command_value="${command_value//\\/\\\\}"
  command_value="${command_value//\"/\\\"}"
  printf '%s' "${command_value}"
}

validate_guest_public_key() {
  local public_key="$1"
  local key_type key_body
  local temp_key

  read -r key_type key_body _ <<<"${public_key}" || true
  case "${key_type}" in
    ssh-rsa|ssh-ed25519|ecdsa-sha2-nistp*|sk-ssh-ed25519@openssh.com|sk-ecdsa-sha2-nistp256@openssh.com)
      ;;
    *)
      echo "Guest Git SSH public key has an unsupported or missing key type." >&2
      return 1
      ;;
  esac

  if [[ -z "${key_body}" ]]; then
    echo "Guest Git SSH public key is missing key material." >&2
    return 1
  fi

  temp_key="$(mktemp)"
  printf '%s\n' "${public_key}" > "${temp_key}"
  if ! ssh-keygen -l -f "${temp_key}" >/dev/null 2>&1; then
    rm -f "${temp_key}"
    echo "Guest Git SSH public key is not a valid OpenSSH public key." >&2
    return 1
  fi
  rm -f "${temp_key}"

  printf '%s\n' "${key_body}"
}

replace_authorized_keys_entry() {
  local authorized_keys_path="$1"
  local public_key_body="$2"
  local authorized_keys_entry="$3"
  local temp_path

  temp_path="$(mktemp "${authorized_keys_path}.tmp.XXXXXX")"
  chmod 600 "${temp_path}"
  if python3 - "${authorized_keys_path}" "${temp_path}" "${public_key_body}" "${authorized_keys_entry}" <<'PY'
from pathlib import Path
import os
import sys

path = Path(sys.argv[1])
temp_path = Path(sys.argv[2])
needle = sys.argv[3]
entry = sys.argv[4]

if not needle:
    raise SystemExit("refusing authorized_keys update with empty public key body")

lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
filtered = [line for line in lines if needle not in line]
filtered.append(entry)
temp_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")
if path.exists():
    existing_stat = path.stat()
    os.chown(temp_path, existing_stat.st_uid, existing_stat.st_gid)
os.replace(temp_path, path)
PY
  then
    return 0
  fi

  rm -f "${temp_path}"
  return 1
}

if [[ ! -f "${SSH_KEY_PATH}" ]]; then
  echo "Missing SSH private key: ${SSH_KEY_PATH}" >&2
  echo "Set SSH_KEY_PATH to the private key that matches the VM's authorized key." >&2
  exit 1
fi

if [[ ! -x "${WRAPPER_PATH}" ]]; then
  echo "Missing or non-executable Git SSH wrapper: ${WRAPPER_PATH}" >&2
  exit 1
fi

get_vm_ip() {
  if guest_ssh_resolve_vm_ip "${VM_NAME}"; then
    return 0
  fi

  echo "Could not determine an IPv4 address for VM ${VM_NAME}." >&2
  echo "Run this script with sudo, grant the current user libvirt access, or authorize the pkexec prompt." >&2
  return 1
}

VM_IP="$(get_vm_ip)"
mapfile -t SSH_OPTS < <(guest_ssh_options "${VM_NAME}" "${VM_IP}" "${SSH_KEY_PATH}" "${CALLER_HOME}")

echo "Bootstrapping Git SSH into ${VM_USER}@${VM_IP}"

GUEST_GIT_INFO="$(
  ssh "${SSH_OPTS[@]}" "${VM_USER}@${VM_IP}" \
    "VM_NAME='${VM_NAME}' GIT_HOST='${GIT_HOST}' GIT_USER='${GIT_USER}' GIT_SSH_ALIAS='${GIT_SSH_ALIAS}' GIT_KEY_NAME='${GIT_KEY_NAME}' GIT_REMOVE_LEGACY_KEY='${GIT_REMOVE_LEGACY_KEY}' bash -s" <<'EOF'
set -euo pipefail
mkdir -p ~/.ssh
chmod 700 ~/.ssh

KEY_PATH="$HOME/.ssh/${GIT_KEY_NAME}"
CONFIG_PATH="$HOME/.ssh/config"
KNOWN_HOSTS_PATH="$HOME/.ssh/known_hosts"

if [[ "${GIT_REMOVE_LEGACY_KEY}" == "1" ]]; then
  rm -f "$HOME/.ssh/evander-git" "$HOME/.ssh/evander-git.pub"
fi

if [[ ! -f "${KEY_PATH}" ]]; then
  ssh-keygen -q -t ed25519 -N "" -C "evander-git@${VM_NAME}" -f "${KEY_PATH}"
fi

touch "${CONFIG_PATH}" "${KNOWN_HOSTS_PATH}"
chmod 600 "${CONFIG_PATH}" "${KNOWN_HOSTS_PATH}"

python3 - "${CONFIG_PATH}" "${GIT_KEY_NAME}" "${GIT_REMOVE_LEGACY_KEY}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
key_name = sys.argv[2]
remove_legacy = sys.argv[3] == "1"
text = path.read_text(encoding="utf-8") if path.exists() else ""
markers = [(f"# BEGIN evander git grant {key_name}", f"# END evander git grant {key_name}")]
if remove_legacy:
    markers.extend(
        [
            ("# BEGIN evander git host", "# END evander git host"),
            ("# BEGIN evander git write host", "# END evander git write host"),
        ]
    )
for begin, end in markers:
    while begin in text and end in text:
        start = text.index(begin)
        stop = text.index(end, start) + len(end)
        suffix = "\n" if stop < len(text) and text[stop] == "\n" else ""
        text = text[:start].rstrip() + "\n"
        text += text[stop + len(suffix):].lstrip("\n")
path.write_text(text, encoding="utf-8")
PY

cat >> "${CONFIG_PATH}" <<CONFIG
# BEGIN evander git grant ${GIT_KEY_NAME}
Host ${GIT_SSH_ALIAS}
  HostName ${GIT_HOST}
  User ${GIT_USER}
  IdentityFile ~/.ssh/${GIT_KEY_NAME}
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
# END evander git grant ${GIT_KEY_NAME}
CONFIG

ssh-keyscan -H "${GIT_HOST}" >> "${KNOWN_HOSTS_PATH}" 2>/dev/null || true
detected_session_id=""
if [[ -r /etc/evander/session.env ]]; then
  detected_session_id="$(
    set +u
    # shellcheck disable=SC1091
    . /etc/evander/session.env >/dev/null 2>&1 || true
    printf '%s' "${EVANDER_SESSION_ID:-${SESSION_ID:-}}"
  )"
fi

printf 'PUBLIC_KEY=%s\n' "$(cat "${KEY_PATH}.pub")"
printf 'SESSION_ID=%s\n' "${detected_session_id}"
EOF
)"

PUBLIC_KEY=""
DETECTED_SESSION_ID=""
while IFS= read -r line; do
  case "${line}" in
    PUBLIC_KEY=*) PUBLIC_KEY="${line#PUBLIC_KEY=}" ;;
    SESSION_ID=*) DETECTED_SESSION_ID="${line#SESSION_ID=}" ;;
  esac
done <<<"${GUEST_GIT_INFO}"

if [[ -z "${PUBLIC_KEY}" ]]; then
  echo "Failed to retrieve guest Git SSH public key." >&2
  exit 1
fi

if ! PUBLIC_KEY_BODY="$(validate_guest_public_key "${PUBLIC_KEY}")"; then
  exit 1
fi

if [[ -z "${GIT_SESSION_ID}" && -n "${DETECTED_SESSION_ID}" ]]; then
  GIT_SESSION_ID="${DETECTED_SESSION_ID}"
fi

AUTH_ACCESS="read-only"
AUTH_REPO_REAL=""
AUTH_REF_PREFIX=""
AUTH_PR_NOTES_REF=""
if [[ -n "${GIT_AUTH_REPO_INPUT}" ]]; then
  AUTH_REPO_REAL="$(resolve_git_auth_repo "${GIT_AUTH_REPO_INPUT}")"
  case "${AUTH_REPO_REAL}" in
    "${GIT_ROOT_REAL}"/*) ;;
    *)
      echo "EVANDER_GIT_REPO must resolve under EVANDER_GIT_ROOT: ${GIT_ROOT}" >&2
      exit 1
      ;;
  esac
fi

if is_truthy "${EVANDER_GIT_READ_ONLY:-0}"; then
  if [[ -z "${AUTH_REPO_REAL}" ]]; then
    echo "Cannot install read-only Git SSH access without EVANDER_GIT_REPO metadata." >&2
    echo "Set EVANDER_GIT_REPO to the bare repo path or name that the guest may read." >&2
    exit 1
  fi
  AUTH_ACCESS="read-only"
elif [[ -n "${AUTH_REPO_REAL}" && -n "${GIT_SESSION_ID}" ]]; then
  if ! validate_git_session_id "${GIT_SESSION_ID}"; then
    echo "Invalid EVANDER_GIT_SESSION_ID: ${GIT_SESSION_ID}" >&2
    echo "Session ids used for Git writes must be a single valid ref path component." >&2
    exit 1
  fi
  AUTH_REF_PREFIX="refs/evander/agents/${GIT_SESSION_ID}/"
  AUTH_PR_NOTES_REF="$(agent_pr_notes_ref_for_session "${GIT_SESSION_ID}")"
  AUTH_ACCESS="write"
elif is_truthy "${EVANDER_GIT_REQUIRE_WRITE_METADATA:-0}"; then
  echo "Cannot install writable Git SSH access without repo/session metadata." >&2
  echo "Set EVANDER_GIT_REPO and EVANDER_GIT_SESSION_ID, or set EVANDER_GIT_REPO with EVANDER_GIT_READ_ONLY=1." >&2
  exit 1
elif [[ -n "${AUTH_REPO_REAL}" ]]; then
  echo "Session metadata unavailable; installing read-only Git SSH access scoped to ${AUTH_REPO_REAL}." >&2
  echo "Set EVANDER_GIT_SESSION_ID to allow pushes to refs/evander/agents/<session-id>/..." >&2
else
  echo "Cannot install Git SSH access without EVANDER_GIT_REPO metadata." >&2
  echo "Set EVANDER_GIT_REPO for repo-scoped read-only access, or set EVANDER_GIT_REPO and EVANDER_GIT_SESSION_ID for writable access." >&2
  exit 1
fi

if [[ "${AUTH_ACCESS}" == "write" ]]; then
  FORCED_COMMAND="EVANDER_GIT_ROOT_B64=$(one_line_base64 "${GIT_ROOT_REAL}")"
  FORCED_COMMAND+=" EVANDER_GIT_AUTH_ACCESS=write"
  FORCED_COMMAND+=" EVANDER_GIT_REFRESH_REQUIRED=1"
  FORCED_COMMAND+=" EVANDER_GIT_AUTH_REPO_B64=$(one_line_base64 "${AUTH_REPO_REAL}")"
  FORCED_COMMAND+=" EVANDER_GIT_AUTH_SESSION_ID_B64=$(one_line_base64 "${GIT_SESSION_ID}")"
  FORCED_COMMAND+=" EVANDER_GIT_AUTH_REF_PREFIX_B64=$(one_line_base64 "${AUTH_REF_PREFIX}")"
  FORCED_COMMAND+=" EVANDER_GIT_AUTH_PR_NOTES_REF_B64=$(one_line_base64 "${AUTH_PR_NOTES_REF}")"
  FORCED_COMMAND+=" ${WRAPPER_PATH}"
else
  FORCED_COMMAND="EVANDER_GIT_ROOT_B64=$(one_line_base64 "${GIT_ROOT_REAL}")"
  FORCED_COMMAND+=" EVANDER_GIT_AUTH_ACCESS=read-only"
  FORCED_COMMAND+=" EVANDER_GIT_REFRESH_REQUIRED=1"
  FORCED_COMMAND+=" EVANDER_GIT_AUTH_REPO_B64=$(one_line_base64 "${AUTH_REPO_REAL}")"
  FORCED_COMMAND+=" ${WRAPPER_PATH}"
fi
FORCED_COMMAND_ESCAPED="$(escape_authorized_keys_command "${FORCED_COMMAND}")"
AUTHORIZED_KEYS_ENTRY="from=\"${GIT_ALLOWED_FROM}\",no-agent-forwarding,no-port-forwarding,no-pty,no-user-rc,no-X11-forwarding,command=\"${FORCED_COMMAND_ESCAPED}\" ${PUBLIC_KEY}"

mkdir -p "${GIT_USER_HOME}/.ssh"
chmod 700 "${GIT_USER_HOME}/.ssh"
touch "${AUTHORIZED_KEYS_PATH}"
chmod 600 "${AUTHORIZED_KEYS_PATH}"
if [[ "$(id -u)" == "0" ]]; then
  GIT_USER_GROUP="$(id -gn "${GIT_USER}")"
  chown "${GIT_USER}:${GIT_USER_GROUP}" "${GIT_USER_HOME}/.ssh" "${AUTHORIZED_KEYS_PATH}"
fi
replace_authorized_keys_entry "${AUTHORIZED_KEYS_PATH}" "${PUBLIC_KEY_BODY}" "${AUTHORIZED_KEYS_ENTRY}"

# Machine-readable, secret-free lifecycle metadata consumed by the control plane.
printf 'EVANDER_GIT_PUBLIC_KEY_BODY=%s\n' "${PUBLIC_KEY_BODY}"
printf 'EVANDER_GIT_AUTHORIZED_KEYS_PATH=%s\n' "${AUTHORIZED_KEYS_PATH}"
printf 'EVANDER_GIT_AUTH_ACCESS=%s\n' "${AUTH_ACCESS}"

echo "Git SSH bootstrap complete for ${VM_NAME} (${VM_IP})"
if [[ "${AUTH_ACCESS}" == "write" ]]; then
  echo "Write access is limited to:"
  echo "  repo: ${AUTH_REPO_REAL}"
  echo "  ref prefix: ${AUTH_REF_PREFIX}"
  echo "  PR notes ref: ${AUTH_PR_NOTES_REF}"
else
  echo "Read-only access is limited to:"
  echo "  repo: ${AUTH_REPO_REAL}"
  echo "Pushes require EVANDER_GIT_REPO and EVANDER_GIT_SESSION_ID."
fi
echo
echo "Guest host alias:"
echo "  ${GIT_SSH_ALIAS}"
