#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: build_agent_sandbox_runner_image.sh [--force]

Build the repo-local agent-sandbox runner image and save it as a Docker tarball.
The script writes a sidecar manifest next to the tarball and reuses the cached
archive when the manifest still matches the Dockerfile, build script, resolved
Codex binary, and package metadata. Moving base-image tags such as ubuntu:24.04
are part of that manifest, so use --force or RUNNER_FORCE_REBUILD=true to
refresh an upstream tag that changed without a Dockerfile text edit.

Flags:
  --force                  Force a rebuild even when the manifest matches.

Environment:
  HOST_CODEX_ROOT          Host Codex package root.
                           Default: ~/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex
  HOST_CODEX_BINARY_PATH   Direct path to the Codex binary to copy into the image.
  HOST_CODEX_PLATFORM_PACKAGE
                           Codex platform package to resolve. Default: @openai/codex-linux-x64
  CODEX_TARGET_TRIPLE      Codex target triple to copy. Default: x86_64-unknown-linux-musl
  RUNNER_IMAGE_NAME        Docker image tag. Default: agent-sandbox-runner:latest
  RUNNER_ARCHIVE_PATH      Output tarball. Default: dist/agent-sandbox-runner.tar
  RUNNER_MANIFEST_PATH     Sidecar manifest. Default: dist/agent-sandbox-runner.tar.manifest
  RUNNER_FORCE_REBUILD     Force a rebuild even when the manifest matches.
                           Accepted true values: 1, true, yes, on
  RUNNER_BUILD_NETWORK     Optional Docker build network, for hosts whose Docker
                           bridge cannot reach apt mirrors. Example: host
  DOCKER                   Docker CLI path. Default: docker
EOF
}

FORCE_REBUILD=0
for arg in "$@"; do
  case "${arg}" in
    -h|--help)
      show_help
      exit 0
      ;;
    --force)
      FORCE_REBUILD=1
      ;;
    *)
      echo "Unknown argument: ${arg}" >&2
      show_help >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CALLER_USER="${SUDO_USER:-${USER:-$(id -un)}}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"

HOST_CODEX_ROOT="${HOST_CODEX_ROOT:-${CALLER_HOME}/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex}"
HOST_CODEX_BINARY_PATH="${HOST_CODEX_BINARY_PATH:-}"
HOST_CODEX_PLATFORM_PACKAGE="${HOST_CODEX_PLATFORM_PACKAGE:-@openai/codex-linux-x64}"
CODEX_TARGET_TRIPLE="${CODEX_TARGET_TRIPLE:-x86_64-unknown-linux-musl}"
CODEX_BINARY_RELATIVE_PATH="vendor/${CODEX_TARGET_TRIPLE}/bin/codex"
CODEX_LEGACY_BINARY_RELATIVE_PATH="vendor/${CODEX_TARGET_TRIPLE}/codex/codex"
CODEX_BINARY_RELATIVE_PATHS=(
  "${CODEX_BINARY_RELATIVE_PATH}"
  "${CODEX_LEGACY_BINARY_RELATIVE_PATH}"
)
RUNNER_IMAGE_NAME="${RUNNER_IMAGE_NAME:-agent-sandbox-runner:latest}"
RUNNER_ARCHIVE_PATH="${RUNNER_ARCHIVE_PATH:-${ROOT_DIR}/dist/agent-sandbox-runner.tar}"
RUNNER_MANIFEST_PATH="${RUNNER_MANIFEST_PATH:-${RUNNER_ARCHIVE_PATH}.manifest}"
RUNNER_BUILD_NETWORK="${RUNNER_BUILD_NETWORK:-}"
DOCKER="${DOCKER:-docker}"
if [[ "${RUNNER_FORCE_REBUILD:-}" != "" ]]; then
  case "${RUNNER_FORCE_REBUILD,,}" in
    1|true|yes|on)
      FORCE_REBUILD=1
      ;;
  esac
fi

declare -a HOST_CODEX_BINARY_CANDIDATES=()
declare -A SEEN_PACKAGE_JSON_PATHS=()

add_codex_binary_candidates() {
  local package_root="$1"
  local relative_path

  for relative_path in "${CODEX_BINARY_RELATIVE_PATHS[@]}"; do
    HOST_CODEX_BINARY_CANDIDATES+=("${package_root}/${relative_path}")
  done
}

if [[ -z "${HOST_CODEX_BINARY_PATH}" ]]; then
  add_codex_binary_candidates "${HOST_CODEX_ROOT}/node_modules/${HOST_CODEX_PLATFORM_PACKAGE}"
  add_codex_binary_candidates "${HOST_CODEX_ROOT}"

  HOST_CODEX_NODE_MODULES="$(cd "${HOST_CODEX_ROOT}/../.." 2>/dev/null && pwd || true)"
  if [[ -n "${HOST_CODEX_NODE_MODULES}" ]]; then
    HOISTED_CODEX_BINARY_ROOT="${HOST_CODEX_NODE_MODULES}/${HOST_CODEX_PLATFORM_PACKAGE}"
    add_codex_binary_candidates "${HOISTED_CODEX_BINARY_ROOT}"
  fi

  if command -v node >/dev/null 2>&1; then
    NODE_RESOLVED_CODEX_BINARY_ROOT="$(
      node -e '
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

const root = process.argv[1];
const platformPackage = process.argv[2];
const packageJsonPath = path.join(root, "package.json");
const requireBase = fs.existsSync(packageJsonPath) ? packageJsonPath : path.join(root, "noop.js");

try {
  const requireFromCodexRoot = createRequire(requireBase);
  const platformPackageJson = requireFromCodexRoot.resolve(`${platformPackage}/package.json`);
  process.stdout.write(path.dirname(platformPackageJson));
} catch {
  process.exit(0);
}
' "${HOST_CODEX_ROOT}" "${HOST_CODEX_PLATFORM_PACKAGE}" 2>/dev/null || true
    )"
    if [[ -n "${NODE_RESOLVED_CODEX_BINARY_ROOT}" ]]; then
      add_codex_binary_candidates "${NODE_RESOLVED_CODEX_BINARY_ROOT}"
    fi
  fi
else
  HOST_CODEX_BINARY_CANDIDATES+=("${HOST_CODEX_BINARY_PATH}")
fi

HOST_CODEX_BINARY_PATH=""
for candidate in "${HOST_CODEX_BINARY_CANDIDATES[@]}"; do
  if [[ -x "${candidate}" ]]; then
    HOST_CODEX_BINARY_PATH="${candidate}"
    break
  fi
done

if [[ ! -x "${HOST_CODEX_BINARY_PATH}" ]]; then
  echo "Missing executable Codex binary. Checked:" >&2
  for candidate in "${HOST_CODEX_BINARY_CANDIDATES[@]}"; do
    echo "  - ${candidate}" >&2
  done
  echo "Install @openai/codex with optional dependencies, or set HOST_CODEX_BINARY_PATH or HOST_CODEX_ROOT." >&2
  exit 1
fi

RESOLVED_CODEX_BINARY_PATH="$(readlink -f "${HOST_CODEX_BINARY_PATH}")"
DOCKERFILE_PATH="${ROOT_DIR}/docker/agent-sandbox-runner/Dockerfile"
BUILD_SCRIPT_PATH="${SCRIPT_DIR}/build_agent_sandbox_runner_image.sh"

if [[ ! -f "${DOCKERFILE_PATH}" ]]; then
  echo "Missing runner Dockerfile: ${DOCKERFILE_PATH}" >&2
  exit 1
fi

hash_file() {
  sha256sum "$1" | awk '{print $1}'
}

append_manifest_line() {
  manifest_lines+=("$1=$2")
}

append_package_json_metadata() {
  local label="$1"
  local package_json_path="$2"
  local package_json_sha

  if [[ ! -f "${package_json_path}" ]]; then
    return 0
  fi

  if [[ -n "${SEEN_PACKAGE_JSON_PATHS["${package_json_path}"]:-}" ]]; then
    return 0
  fi
  SEEN_PACKAGE_JSON_PATHS["${package_json_path}"]=1

  package_json_sha="$(hash_file "${package_json_path}")"
  append_manifest_line "${label}_package_json_sha256" "${package_json_sha}"

  if command -v node >/dev/null 2>&1; then
    while IFS= read -r line; do
      manifest_lines+=("${line}")
    done < <(
      node -e '
const fs = require("node:fs");
const packageJsonPath = process.argv[1];
const label = process.argv[2];
const data = JSON.parse(fs.readFileSync(packageJsonPath, "utf8"));
process.stdout.write(`${label}_package_name=${data.name ?? ""}\n`);
process.stdout.write(`${label}_package_version=${data.version ?? ""}\n`);
' "${package_json_path}" "${label}"
    )
  fi
}

manifest_lines=()
append_manifest_line "runner_image_name" "${RUNNER_IMAGE_NAME}"
append_manifest_line "runner_archive_path" "${RUNNER_ARCHIVE_PATH}"
append_manifest_line "runner_manifest_path" "${RUNNER_MANIFEST_PATH}"
append_manifest_line "host_codex_platform_package" "${HOST_CODEX_PLATFORM_PACKAGE}"
append_manifest_line "codex_target_triple" "${CODEX_TARGET_TRIPLE}"
append_manifest_line "dockerfile_sha256" "$(hash_file "${DOCKERFILE_PATH}")"
append_manifest_line "build_script_sha256" "$(hash_file "${BUILD_SCRIPT_PATH}")"
append_manifest_line "codex_binary_realpath" "${RESOLVED_CODEX_BINARY_PATH}"
append_manifest_line "codex_binary_sha256" "$(hash_file "${RESOLVED_CODEX_BINARY_PATH}")"
# The Dockerfile may keep the same text while a moving base-image tag refreshes
# upstream, so the manifest records the exact FROM policy and operators can use
# --force or RUNNER_FORCE_REBUILD=true for an intentional refresh.
append_manifest_line "dockerfile_from_line" "$(grep -m1 '^FROM[[:space:]]' "${DOCKERFILE_PATH}")"

RESOLVED_CODEX_PACKAGE_ROOT="${RESOLVED_CODEX_BINARY_PATH}"
for _ in 1 2 3 4; do
  RESOLVED_CODEX_PACKAGE_ROOT="$(dirname "${RESOLVED_CODEX_PACKAGE_ROOT}")"
done

append_package_json_metadata "host_codex_root" "${HOST_CODEX_ROOT}/package.json"
append_package_json_metadata "resolved_codex_package_root" "${RESOLVED_CODEX_PACKAGE_ROOT}/package.json"

manifest_payload="$(printf '%s\n' "${manifest_lines[@]}")"
manifest_digest="$(printf '%s' "${manifest_payload}" | sha256sum | awk '{print $1}')"

if [[ "${FORCE_REBUILD}" -eq 0 && -s "${RUNNER_ARCHIVE_PATH}" && -s "${RUNNER_MANIFEST_PATH}" ]]; then
  existing_manifest_digest="$(sed -n 's/^manifest_sha256=//p' "${RUNNER_MANIFEST_PATH}" | head -n1)"
  if [[ "${existing_manifest_digest}" == "${manifest_digest}" ]]; then
    echo "Reused cached ${RUNNER_IMAGE_NAME} archive at ${RUNNER_ARCHIVE_PATH}"
    exit 0
  fi
fi

if ! command -v "${DOCKER}" >/dev/null 2>&1; then
  echo "Missing Docker CLI: ${DOCKER}" >&2
  exit 1
fi

BUILD_CONTEXT=""
MANIFEST_TMP_PATH=""
cleanup() {
  if [[ -n "${MANIFEST_TMP_PATH}" && -e "${MANIFEST_TMP_PATH}" ]]; then
    rm -f "${MANIFEST_TMP_PATH}"
  fi
  if [[ -n "${BUILD_CONTEXT}" && -d "${BUILD_CONTEXT}" ]]; then
    rm -rf "${BUILD_CONTEXT}"
  fi
}
trap cleanup EXIT

BUILD_CONTEXT="$(mktemp -d)"
cp "${ROOT_DIR}/docker/agent-sandbox-runner/Dockerfile" "${BUILD_CONTEXT}/Dockerfile"
cp "${HOST_CODEX_BINARY_PATH}" "${BUILD_CONTEXT}/codex"
chmod 0755 "${BUILD_CONTEXT}/codex"

build_args=(build -t "${RUNNER_IMAGE_NAME}")
if [[ -n "${RUNNER_BUILD_NETWORK}" ]]; then
  build_args+=(--network "${RUNNER_BUILD_NETWORK}")
fi
build_args+=("${BUILD_CONTEXT}")

"${DOCKER}" "${build_args[@]}"
mkdir -p "$(dirname "${RUNNER_ARCHIVE_PATH}")"
"${DOCKER}" save -o "${RUNNER_ARCHIVE_PATH}" "${RUNNER_IMAGE_NAME}"

mkdir -p "$(dirname "${RUNNER_MANIFEST_PATH}")"
MANIFEST_TMP_PATH="$(mktemp "$(dirname "${RUNNER_MANIFEST_PATH}")/.runner-manifest.XXXXXX")"
{
  printf 'manifest_sha256=%s\n' "${manifest_digest}"
  printf '%s\n' "${manifest_lines[@]}"
} > "${MANIFEST_TMP_PATH}"
mv -f "${MANIFEST_TMP_PATH}" "${RUNNER_MANIFEST_PATH}"
MANIFEST_TMP_PATH=""

echo "Built ${RUNNER_IMAGE_NAME}"
echo "Saved ${RUNNER_ARCHIVE_PATH}"
