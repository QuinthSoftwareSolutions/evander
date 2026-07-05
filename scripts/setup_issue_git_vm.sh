#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: setup_issue_git_vm.sh [options] <vm-name> <issue-number> [repo-source] [vm-user]

Bootstrap passwordless Git access for a guest VM and create or refresh the
matching issue-numbered bare mirror on the host in one step.

Options (forwarded to mirror_issue_repo.sh):
  --sync-remote       Force upstream sync before mirroring local sources.
  --no-sync-remote    Disable upstream sync for local sources.
  --remote-name NAME
  --refspec SPEC
  --branch NAME
  --force-local

Arguments:
  vm-name       libvirt domain name.
  issue-number  Issue identifier used to derive the repo/remote names.
  repo-source   Working tree path or remote URL to mirror.
                Default: $EVANDER_DEFAULT_REPO_SOURCE
  vm-user       Guest user to configure. Default: agent
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -lt 2 ]]; then
  show_help >&2
  exit 1
fi

MIRROR_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --sync-remote|--no-sync-remote|--force-local)
      MIRROR_ARGS+=("$1")
      shift
      ;;
    --remote-name|--refspec|--branch)
      if [[ -z "${2:-}" ]]; then
        echo "Missing value for $1" >&2
        exit 1
      fi
      MIRROR_ARGS+=("$1" "$2")
      shift 2
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

if [[ $# -lt 2 || $# -gt 4 ]]; then
  show_help >&2
  exit 1
fi

VM_NAME="$1"
ISSUE_NUMBER="$2"
REPO_SOURCE="${3:-${EVANDER_DEFAULT_REPO_SOURCE:-}}"
if [[ -z "${REPO_SOURCE}" ]]; then
  echo "Missing repo-source: pass [repo-source] or set EVANDER_DEFAULT_REPO_SOURCE." >&2
  exit 1
fi
VM_USER="${4:-agent}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

EVANDER_GIT_REPO="${ISSUE_NUMBER}_repo" "${SCRIPT_DIR}/bootstrap_git_vm.sh" "${VM_NAME}" "${VM_USER}"
"${SCRIPT_DIR}/mirror_issue_repo.sh" "${MIRROR_ARGS[@]}" "${ISSUE_NUMBER}" "${REPO_SOURCE}"

echo
echo "One-command Git setup complete."
echo "Inside the VM, clone or set the remote to:"
echo "  ssh://evander-git-host${ROOT_DIR}/git-remotes/${ISSUE_NUMBER}_repo.git"
