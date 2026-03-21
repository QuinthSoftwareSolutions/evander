#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: prewarm_nuget_cache.sh <project-or-solution> [more paths...]

Restore one or more projects/solutions on the host into the shared NuGet cache.

Arguments:
  project-or-solution  One or more .csproj/.sln/.slnx paths to restore.

Environment:
  SHARED_NUGET_CACHE_DIR  Host shared global-packages cache directory.
                          Default: /var/lib/libvirt/images/agent-sandbox/nuget-cache/global-packages
  NUGET_CONFIG_FILE       NuGet.Config path used for host prewarm restores.
                          Default: /home/jonathan/agent-sandbox/runtime/host-prewarm/NuGet.Config
  NUGET_HTTP_CACHE_DIR    Host HTTP cache directory.
                          Default: /home/jonathan/agent-sandbox/runtime/host-prewarm/http-cache
  NUGET_TEMP_DIR          Host temp directory.
                          Default: /home/jonathan/agent-sandbox/runtime/host-prewarm/tmp
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

SHARED_NUGET_CACHE_DIR="${SHARED_NUGET_CACHE_DIR:-/var/lib/libvirt/images/agent-sandbox/nuget-cache/global-packages}"
HOST_PREWARM_DIR="${ROOT_DIR}/runtime/host-prewarm"
NUGET_CONFIG_FILE="${NUGET_CONFIG_FILE:-${HOST_PREWARM_DIR}/NuGet.Config}"
NUGET_HTTP_CACHE_DIR="${NUGET_HTTP_CACHE_DIR:-${HOST_PREWARM_DIR}/http-cache}"
NUGET_TEMP_DIR="${NUGET_TEMP_DIR:-${HOST_PREWARM_DIR}/tmp}"
NUGET_PLUGIN_CACHE_DIR="${HOST_PREWARM_DIR}/plugins-cache"

if ! command -v dotnet >/dev/null 2>&1; then
  echo "dotnet is required on the host." >&2
  exit 1
fi

mkdir -p "${SHARED_NUGET_CACHE_DIR}" "${HOST_PREWARM_DIR}" "${NUGET_HTTP_CACHE_DIR}" "${NUGET_TEMP_DIR}" "${NUGET_PLUGIN_CACHE_DIR}"

if [[ ! -f "${NUGET_CONFIG_FILE}" ]]; then
  cat > "${NUGET_CONFIG_FILE}" <<'CONFIG'
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="sandbox-nuget" value="http://127.0.0.1:11666/nuget/nuget-org/v3/index.json" allowInsecureConnections="true" />
    <add key="sandbox-azure" value="http://127.0.0.1:11666/nuget/azure/v3/index.json" allowInsecureConnections="true" />
  </packageSources>
  <packageSourceMapping>
    <packageSource key="sandbox-azure">
      <package pattern="ExampleIndustry.*" />
      <package pattern="TrueMes.*" />
    </packageSource>
    <packageSource key="sandbox-nuget">
      <package pattern="*" />
    </packageSource>
  </packageSourceMapping>
</configuration>
CONFIG
fi

for target in "$@"; do
  echo "Prewarming NuGet cache from ${target}"
  NUGET_PACKAGES="${SHARED_NUGET_CACHE_DIR}" \
  NUGET_HTTP_CACHE_PATH="${NUGET_HTTP_CACHE_DIR}" \
  NUGET_PLUGINS_CACHE_PATH="${NUGET_PLUGIN_CACHE_DIR}" \
  TMPDIR="${NUGET_TEMP_DIR}" \
  dotnet restore "${target}" --configfile "${NUGET_CONFIG_FILE}" -v minimal
done

echo "Prewarmed shared NuGet cache at ${SHARED_NUGET_CACHE_DIR}"
