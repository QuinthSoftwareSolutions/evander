#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: inspect_nuget_cache.sh [package-id] [version]

Inspect the host shared NuGet cache.

Arguments:
  package-id  Optional package ID to check.
  version     Optional package version to check. Requires package-id.

Environment:
  SHARED_NUGET_CACHE_DIR  Host shared global-packages cache directory.
                          Default: /var/lib/libvirt/images/agent-sandbox/nuget-cache/global-packages
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

SHARED_NUGET_CACHE_DIR="${SHARED_NUGET_CACHE_DIR:-/var/lib/libvirt/images/agent-sandbox/nuget-cache/global-packages}"

if [[ ! -d "${SHARED_NUGET_CACHE_DIR}" ]]; then
  echo "Shared cache directory does not exist: ${SHARED_NUGET_CACHE_DIR}" >&2
  exit 1
fi

echo "Shared NuGet cache: ${SHARED_NUGET_CACHE_DIR}"
du -sh "${SHARED_NUGET_CACHE_DIR}"
echo "Last updated: $(stat -c '%y' "${SHARED_NUGET_CACHE_DIR}")"

if [[ $# -ge 1 ]]; then
  package_id="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  package_dir="${SHARED_NUGET_CACHE_DIR}/${package_id}"
  if [[ $# -ge 2 ]]; then
    package_dir="${package_dir}/$2"
  fi

  if [[ -e "${package_dir}" ]]; then
    echo "Present: ${package_dir}"
  else
    echo "Missing: ${package_dir}"
    exit 2
  fi
fi
