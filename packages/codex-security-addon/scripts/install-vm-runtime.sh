#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 2 ]]; then
  echo "Usage: install-vm-runtime.sh <setup-user@private-vm-address> <runtime.tar.gz>" >&2
  exit 2
fi
target="$1"
archive="$(realpath "$2")"
remote="$(ssh "${target}" 'mktemp -d /tmp/codex-security.XXXXXXXX')"
trap 'ssh "${target}" "rm -rf -- ${remote}"' EXIT
scp "${archive}" "${target}:${remote}/runtime.tar.gz"
ssh "${target}" "sudo install -d -m 0755 /opt/evander/codex-security && sudo tar -xzf ${remote}/runtime.tar.gz -C /opt/evander/codex-security && sudo chown -R root:root /opt/evander/codex-security && sudo chmod -R go-w /opt/evander/codex-security"
