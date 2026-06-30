#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: build_evander_claude_runner_image.sh [--force]

Build the repo-local Claude evander runner image and save it as a Docker tarball.
The script writes a sidecar manifest next to the tarball and reuses the cached
archive when the manifest still matches the Dockerfile, build script, resolved
Claude executable, and image metadata. Moving base-image tags such as ubuntu:24.04
are part of that manifest, so use --force or RUNNER_FORCE_REBUILD=true to
refresh an upstream tag that changed without a Dockerfile text edit.

Flags:
  --force                  Force a rebuild even when the manifest matches.

Environment:
  HOST_CLAUDE_BIN          Host Claude Code executable. Default: command -v claude
  RUNNER_IMAGE_NAME        Docker image tag. Default: evander-runner-claude:latest
  RUNNER_ARCHIVE_PATH      Output tarball. Default: dist/evander-runner-claude.tar
  RUNNER_MANIFEST_PATH     Sidecar manifest. Default: dist/evander-runner-claude.tar.manifest
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

HOST_CLAUDE_BIN="${HOST_CLAUDE_BIN:-}"
if [[ -z "${HOST_CLAUDE_BIN}" ]]; then
  HOST_CLAUDE_BIN="$(command -v claude || true)"
fi
RUNNER_IMAGE_NAME="${RUNNER_IMAGE_NAME:-evander-runner-claude:latest}"
RUNNER_ARCHIVE_PATH="${RUNNER_ARCHIVE_PATH:-${ROOT_DIR}/dist/evander-runner-claude.tar}"
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

if [[ -z "${HOST_CLAUDE_BIN}" || ! -x "${HOST_CLAUDE_BIN}" ]]; then
  echo "Missing executable Claude Code command. Install Claude Code or set HOST_CLAUDE_BIN." >&2
  exit 1
fi

RESOLVED_CLAUDE_BIN="$(readlink -f "${HOST_CLAUDE_BIN}")"
DOCKERFILE_PATH="${ROOT_DIR}/docker/evander-runner-claude/Dockerfile"
BUILD_SCRIPT_PATH="${SCRIPT_DIR}/build_evander_claude_runner_image.sh"

if [[ ! -f "${DOCKERFILE_PATH}" ]]; then
  echo "Missing Claude runner Dockerfile: ${DOCKERFILE_PATH}" >&2
  exit 1
fi

hash_file() {
  sha256sum "$1" | awk '{print $1}'
}

append_manifest_line() {
  manifest_lines+=("$1=$2")
}

manifest_lines=()
append_manifest_line "runner_image_name" "${RUNNER_IMAGE_NAME}"
append_manifest_line "runner_archive_path" "${RUNNER_ARCHIVE_PATH}"
append_manifest_line "runner_manifest_path" "${RUNNER_MANIFEST_PATH}"
append_manifest_line "dockerfile_sha256" "$(hash_file "${DOCKERFILE_PATH}")"
append_manifest_line "build_script_sha256" "$(hash_file "${BUILD_SCRIPT_PATH}")"
append_manifest_line "claude_bin_realpath" "${RESOLVED_CLAUDE_BIN}"
append_manifest_line "claude_bin_sha256" "$(hash_file "${RESOLVED_CLAUDE_BIN}")"
# The Dockerfile may keep the same text while a moving base-image tag refreshes
# upstream, so the manifest records the exact FROM policy and operators can use
# --force or RUNNER_FORCE_REBUILD=true for an intentional refresh.
append_manifest_line "dockerfile_from_line" "$(grep -m1 '^FROM[[:space:]]' "${DOCKERFILE_PATH}")"

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
RUNNER_ARCHIVE_TMP_PATH=""
MANIFEST_TMP_PATH=""
cleanup() {
  if [[ -n "${RUNNER_ARCHIVE_TMP_PATH}" && -e "${RUNNER_ARCHIVE_TMP_PATH}" ]]; then
    rm -f "${RUNNER_ARCHIVE_TMP_PATH}"
  fi
  if [[ -n "${MANIFEST_TMP_PATH}" && -e "${MANIFEST_TMP_PATH}" ]]; then
    rm -f "${MANIFEST_TMP_PATH}"
  fi
  if [[ -n "${BUILD_CONTEXT}" && -d "${BUILD_CONTEXT}" ]]; then
    rm -rf "${BUILD_CONTEXT}"
  fi
}
trap cleanup EXIT

BUILD_CONTEXT="$(mktemp -d)"
cp "${ROOT_DIR}/docker/evander-runner-claude/Dockerfile" "${BUILD_CONTEXT}/Dockerfile"
cp "${RESOLVED_CLAUDE_BIN}" "${BUILD_CONTEXT}/claude"
chmod 0755 "${BUILD_CONTEXT}/claude"

build_args=(build -t "${RUNNER_IMAGE_NAME}")
if [[ -n "${RUNNER_BUILD_NETWORK}" ]]; then
  build_args+=(--network "${RUNNER_BUILD_NETWORK}")
fi
if [[ "${FORCE_REBUILD}" -eq 1 ]]; then
  build_args+=(--pull)
fi
build_args+=("${BUILD_CONTEXT}")

"${DOCKER}" "${build_args[@]}"
mkdir -p "$(dirname "${RUNNER_ARCHIVE_PATH}")"
RUNNER_ARCHIVE_TMP_PATH="$(mktemp "$(dirname "${RUNNER_ARCHIVE_PATH}")/.runner-archive.XXXXXX.tar")"
"${DOCKER}" save -o "${RUNNER_ARCHIVE_TMP_PATH}" "${RUNNER_IMAGE_NAME}"
mv -f "${RUNNER_ARCHIVE_TMP_PATH}" "${RUNNER_ARCHIVE_PATH}"
RUNNER_ARCHIVE_TMP_PATH=""

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
