#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: prewarm_container_worker_nuget_cache.sh --worker HOST --target PATH [options]

Restore one or more projects/solutions on the control plane, then additively
copy the resulting NuGet global-packages cache into container worker fallback
caches.

Options:
  --worker HOST       Worker SSH host to update. Repeat for multiple workers.
  --local             Update the fallback cache on this machine without SSH.
  --target PATH       .csproj/.sln/.slnx path to restore. Repeat for multiple targets.
  --cache-path PATH   Worker fallback cache path.
                      Default: /var/lib/agent-worker/toolchain-cache/nuget-cache-ro
  --dotnet PATH       dotnet executable to use. Default: dotnet
  --relay-base-url URL
                      Control-plane NuGet relay base URL.
                      Default: http://127.0.0.1:11666
  -h, --help          Show this help.

Environment:
  CONTAINER_WORKER_NUGET_CACHE_PATH  Default worker fallback cache path.
  DOTNET_COMMAND                     Default dotnet executable.
  NUGET_RELAY_BASE_URL               Default control-plane NuGet relay base URL.
EOF
}

WORKERS=()
TARGETS=()
LOCAL_UPDATE=false
CACHE_PATH="${CONTAINER_WORKER_NUGET_CACHE_PATH:-/var/lib/agent-worker/toolchain-cache/nuget-cache-ro}"
DOTNET_COMMAND="${DOTNET_COMMAND:-dotnet}"
NUGET_RELAY_BASE_URL="${NUGET_RELAY_BASE_URL:-http://127.0.0.1:11666}"
RESTORE_SUPPORT_PACKAGE_ID="Microsoft.Extensions.FileSystemGlobbing"
RESTORE_SUPPORT_PACKAGE_VERSION="6.0.0"

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --worker)
      if [[ -z "${2:-}" ]]; then
        echo "--worker requires a host." >&2
        exit 1
      fi
      WORKERS+=("$2")
      shift 2
      ;;
    --local)
      LOCAL_UPDATE=true
      shift
      ;;
    --target)
      if [[ -z "${2:-}" ]]; then
        echo "--target requires a project or solution path." >&2
        exit 1
      fi
      TARGETS+=("$2")
      shift 2
      ;;
    --cache-path)
      if [[ -z "${2:-}" ]]; then
        echo "--cache-path requires a path." >&2
        exit 1
      fi
      CACHE_PATH="$2"
      shift 2
      ;;
    --dotnet)
      if [[ -z "${2:-}" ]]; then
        echo "--dotnet requires a path." >&2
        exit 1
      fi
      DOTNET_COMMAND="$2"
      shift 2
      ;;
    --relay-base-url)
      if [[ -z "${2:-}" ]]; then
        echo "--relay-base-url requires a URL." >&2
        exit 1
      fi
      NUGET_RELAY_BASE_URL="${2%/}"
      shift 2
      ;;
    -h|--help)
      show_help
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      show_help >&2
      exit 1
      ;;
  esac
done

if [[ ${#TARGETS[@]} -eq 0 || ( "${LOCAL_UPDATE}" != "true" && ${#WORKERS[@]} -eq 0 ) ]]; then
  show_help >&2
  exit 1
fi

if ! command -v "${DOTNET_COMMAND}" >/dev/null 2>&1; then
  echo "${DOTNET_COMMAND} is required." >&2
  exit 1
fi

for command_name in tar; do
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "${command_name} is required." >&2
    exit 1
  fi
done

if [[ ${#WORKERS[@]} -gt 0 ]]; then
  for command_name in ssh scp; do
    if ! command -v "${command_name}" >/dev/null 2>&1; then
      echo "${command_name} is required." >&2
      exit 1
    fi
  done
fi

for target in "${TARGETS[@]}"; do
  if [[ ! -e "${target}" ]]; then
    echo "Restore target does not exist: ${target}" >&2
    exit 1
  fi
done

quote_remote() {
  printf "%q" "$1"
}

TMP_ROOT="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_ROOT}"
}
trap cleanup EXIT

STAGE_PACKAGES="${TMP_ROOT}/global-packages"
HTTP_CACHE="${TMP_ROOT}/http-cache"
PLUGIN_CACHE="${TMP_ROOT}/plugins-cache"
TEMP_DIR="${TMP_ROOT}/tmp"
CLI_HOME="${TMP_ROOT}/cli-home"
RESTORE_SUPPORT_DIR="${TMP_ROOT}/restore-support"
NUGET_CONFIG="${TMP_ROOT}/NuGet.Config"
ARCHIVE="${TMP_ROOT}/container-worker-nuget-cache.tar.gz"
MANIFEST="${TMP_ROOT}/package-manifest.txt"

mkdir -p \
  "${STAGE_PACKAGES}" \
  "${HTTP_CACHE}" \
  "${PLUGIN_CACHE}" \
  "${TEMP_DIR}" \
  "${CLI_HOME}" \
  "${RESTORE_SUPPORT_DIR}"

cat > "${NUGET_CONFIG}" <<CONFIG
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <config>
    <add key="globalPackagesFolder" value="${STAGE_PACKAGES}" />
    <add key="httpCacheFolder" value="${HTTP_CACHE}" />
  </config>
  <packageSources>
    <clear />
    <add key="sandbox-nuget" value="${NUGET_RELAY_BASE_URL}/nuget/nuget-org/v3/index.json" allowInsecureConnections="true" />
    <add key="sandbox-azure" value="${NUGET_RELAY_BASE_URL}/nuget/azure/v3/index.json" allowInsecureConnections="true" />
  </packageSources>
  <packageSourceMapping>
    <packageSource key="sandbox-nuget">
      <package pattern="*" />
    </packageSource>
    <packageSource key="sandbox-azure">
      <package pattern="ExampleIndustry.*" />
    </packageSource>
  </packageSourceMapping>
</configuration>
CONFIG

cat > "${RESTORE_SUPPORT_DIR}/restore-support.csproj" <<CONFIG
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
  </PropertyGroup>
  <ItemGroup>
    <PackageReference Include="${RESTORE_SUPPORT_PACKAGE_ID}" Version="${RESTORE_SUPPORT_PACKAGE_VERSION}" />
  </ItemGroup>
</Project>
CONFIG

restore_target() {
  local target="$1"
  echo "Prewarming NuGet fallback packages from ${target}"
  DOTNET_CLI_HOME="${CLI_HOME}" \
  DOTNET_CLI_TELEMETRY_OPTOUT=1 \
  DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1 \
  DOTNET_SKIP_WORKLOAD_MANIFEST_UPDATE=1 \
  DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=1 \
  DOTNET_NOLOGO=1 \
  NUGET_PACKAGES="${STAGE_PACKAGES}" \
  NUGET_HTTP_CACHE_PATH="${HTTP_CACHE}" \
  NUGET_PLUGINS_CACHE_PATH="${PLUGIN_CACHE}" \
  TMPDIR="${TEMP_DIR}" \
  "${DOTNET_COMMAND}" restore "${target}" --configfile "${NUGET_CONFIG}" -v minimal -p:NuGetAudit=false
}

for target in "${TARGETS[@]}"; do
  restore_target "${target}"
done
restore_target "${RESTORE_SUPPORT_DIR}/restore-support.csproj"

find "${STAGE_PACKAGES}" -mindepth 2 -maxdepth 2 -type d -printf "%P\n" | sort > "${MANIFEST}"
PACKAGE_VERSION_COUNT="$(wc -l < "${MANIFEST}")"
if [[ "${PACKAGE_VERSION_COUNT}" -eq 0 ]]; then
  echo "No package versions were staged." >&2
  exit 1
fi

tar -C "${STAGE_PACKAGES}" -czf "${ARCHIVE}" .
echo "Packed ${PACKAGE_VERSION_COUNT} package versions into ${ARCHIVE}"

if [[ "${LOCAL_UPDATE}" == "true" ]]; then
  echo "Updating local worker NuGet fallback cache: ${CACHE_PATH}"
  mkdir -p "${CACHE_PATH}"
  tar -C "${CACHE_PATH}" -xzf "${ARCHIVE}"
  missing=0
  while IFS= read -r package_version; do
    if [[ ! -d "${CACHE_PATH}/${package_version}" ]]; then
      echo "Missing after extraction: ${CACHE_PATH}/${package_version}" >&2
      missing=1
    fi
  done < "${MANIFEST}"
  if [[ "${missing}" -ne 0 ]]; then
    exit 1
  fi
  echo "Updated local cache ${CACHE_PATH} with ${PACKAGE_VERSION_COUNT} package versions"
fi

for worker in "${WORKERS[@]}"; do
  remote_base="/tmp/agent-sandbox-container-worker-nuget-prewarm-$RANDOM-$$"
  remote_archive="${remote_base}.tar.gz"
  remote_manifest="${remote_base}.manifest"

  echo "Copying NuGet fallback archive to ${worker}:${remote_archive}"
  scp "${ARCHIVE}" "${worker}:${remote_archive}"
  scp "${MANIFEST}" "${worker}:${remote_manifest}"

  cache_path_quoted="$(quote_remote "${CACHE_PATH}")"
  archive_quoted="$(quote_remote "${remote_archive}")"
  manifest_quoted="$(quote_remote "${remote_manifest}")"
  ssh "${worker}" \
    "CACHE_PATH=${cache_path_quoted} ARCHIVE=${archive_quoted} MANIFEST=${manifest_quoted} bash -s" <<'REMOTE'
set -euo pipefail
mkdir -p "${CACHE_PATH}"
tar -C "${CACHE_PATH}" -xzf "${ARCHIVE}"

missing=0
while IFS= read -r package_version; do
  if [[ ! -d "${CACHE_PATH}/${package_version}" ]]; then
    echo "Missing after extraction: ${CACHE_PATH}/${package_version}" >&2
    missing=1
  fi
done < "${MANIFEST}"

rm -f "${ARCHIVE}" "${MANIFEST}"
if [[ "${missing}" -ne 0 ]]; then
  exit 1
fi
REMOTE
  echo "Updated ${worker}:${CACHE_PATH} with ${PACKAGE_VERSION_COUNT} package versions"
done
