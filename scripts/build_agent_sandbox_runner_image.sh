#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: build_agent_sandbox_runner_image.sh

Build the repo-local agent-sandbox runner image and save it as a Docker tarball.

Environment:
  HOST_CODEX_ROOT          Host Codex package root.
                           Default: ~/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex
  HOST_CODEX_BINARY_PATH   Direct path to the Codex binary to copy into the image.
  RUNNER_IMAGE_NAME        Docker image tag. Default: agent-sandbox-runner:latest
  RUNNER_ARCHIVE_PATH      Output tarball. Default: dist/agent-sandbox-runner.tar
  RUNNER_BUILD_NETWORK     Optional Docker build network, for hosts whose Docker
                           bridge cannot reach apt mirrors. Example: host
  DOCKER                   Docker CLI path. Default: docker
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CALLER_USER="${SUDO_USER:-$USER}"
CALLER_HOME="$(getent passwd "${CALLER_USER}" | cut -d: -f6)"

HOST_CODEX_ROOT="${HOST_CODEX_ROOT:-${CALLER_HOME}/.nvm/versions/node/v24.14.0/lib/node_modules/@openai/codex}"
DEFAULT_CODEX_BINARY="${HOST_CODEX_ROOT}/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/codex/codex"
HOST_CODEX_BINARY_PATH="${HOST_CODEX_BINARY_PATH:-${DEFAULT_CODEX_BINARY}}"
RUNNER_IMAGE_NAME="${RUNNER_IMAGE_NAME:-agent-sandbox-runner:latest}"
RUNNER_ARCHIVE_PATH="${RUNNER_ARCHIVE_PATH:-${ROOT_DIR}/dist/agent-sandbox-runner.tar}"
RUNNER_BUILD_NETWORK="${RUNNER_BUILD_NETWORK:-}"
DOCKER="${DOCKER:-docker}"

if [[ ! -x "${HOST_CODEX_BINARY_PATH}" ]]; then
  echo "Missing executable Codex binary: ${HOST_CODEX_BINARY_PATH}" >&2
  echo "Set HOST_CODEX_BINARY_PATH or HOST_CODEX_ROOT to the installed host Codex package." >&2
  exit 1
fi

if ! command -v "${DOCKER}" >/dev/null 2>&1; then
  echo "Missing Docker CLI: ${DOCKER}" >&2
  exit 1
fi

BUILD_CONTEXT="$(mktemp -d)"
cleanup() {
  rm -rf "${BUILD_CONTEXT}"
}
trap cleanup EXIT

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

echo "Built ${RUNNER_IMAGE_NAME}"
echo "Saved ${RUNNER_ARCHIVE_PATH}"
