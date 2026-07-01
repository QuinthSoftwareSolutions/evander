#!/usr/bin/env bash

guest_ripgrep_resolve_host_binary() {
  local caller_home="${CALLER_HOME:-${HOME:-}}"
  local host_codex_root="${HOST_CODEX_ROOT:-}"
  local candidate

  if [[ -n "${HOST_RIPGREP_BIN:-}" ]]; then
    if [[ -x "${HOST_RIPGREP_BIN}" ]]; then
      readlink -f "${HOST_RIPGREP_BIN}"
      return 0
    fi
    echo "HOST_RIPGREP_BIN is not executable: ${HOST_RIPGREP_BIN}" >&2
    return 1
  fi

  candidate="$(command -v rg || true)"
  if [[ -n "${candidate}" && -x "${candidate}" ]]; then
    readlink -f "${candidate}"
    return 0
  fi

  if [[ -n "${host_codex_root}" ]]; then
    candidate="${host_codex_root}/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/codex-path/rg"
    if [[ -x "${candidate}" ]]; then
      readlink -f "${candidate}"
      return 0
    fi
  fi

  for candidate in \
    "${caller_home}"/.nvm/versions/node/*/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/codex-path/rg \
    /usr/local/bin/rg \
    /usr/bin/rg \
    /bin/rg; do
    if [[ -x "${candidate}" ]]; then
      readlink -f "${candidate}"
      return 0
    fi
  done

  return 1
}

guest_ripgrep_seed() {
  local vm_user="$1"
  local vm_ip="$2"
  local host_rg
  local remote_temp_name
  shift 2
  local -a ssh_opts=("$@")

  host_rg="$(guest_ripgrep_resolve_host_binary || true)"
  if [[ -z "${host_rg}" ]]; then
    echo "Host ripgrep binary not found; skipping guest rg seed." >&2
    echo "Install ripgrep on the host or set HOST_RIPGREP_BIN to seed existing VMs." >&2
    if [[ "${GUEST_RIPGREP_REQUIRED:-0}" == "1" ]]; then
      return 1
    fi
    return 0
  fi

  if ! ssh "${ssh_opts[@]}" "${vm_user}@${vm_ip}" 'set -euo pipefail; mkdir -p ~/bin'; then
    echo "Could not create ~/bin in guest for rg seed." >&2
    if [[ "${GUEST_RIPGREP_REQUIRED:-0}" == "1" ]]; then
      return 1
    fi
    return 0
  fi
  remote_temp_name=".rg.evander-bootstrap-$$"
  if ! scp "${ssh_opts[@]}" "${host_rg}" "${vm_user}@${vm_ip}:~/bin/${remote_temp_name}"; then
    echo "Could not copy host rg binary into guest." >&2
    ssh "${ssh_opts[@]}" "${vm_user}@${vm_ip}" \
      "rm -f \"\$HOME/bin/${remote_temp_name}\"" >/dev/null 2>&1 || true
    if [[ "${GUEST_RIPGREP_REQUIRED:-0}" == "1" ]]; then
      return 1
    fi
    return 0
  fi
  if ! ssh "${ssh_opts[@]}" "${vm_user}@${vm_ip}" \
    "REMOTE_RIPGREP_TEMP_NAME='${remote_temp_name}' bash -s" <<'EOF'
set -euo pipefail
rg_temp="${HOME}/bin/${REMOTE_RIPGREP_TEMP_NAME}"
chmod +x "${rg_temp}"
if "${rg_temp}" --version >/dev/null; then
  mv -f "${rg_temp}" "${HOME}/bin/rg"
else
  rm -f "${rg_temp}"
  exit 1
fi
EOF
  then
    echo "Seeded rg did not run successfully in guest." >&2
    if [[ "${GUEST_RIPGREP_REQUIRED:-0}" == "1" ]]; then
      return 1
    fi
    return 0
  fi
  echo "Seeded ripgrep into ${vm_user}@${vm_ip}:~/bin/rg"
}
