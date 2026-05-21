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
  HOST_CODEX_PLATFORM_PACKAGE
                           Codex platform package to resolve. Default: @openai/codex-linux-x64
  CODEX_TARGET_TRIPLE      Codex target triple to copy. Default: x86_64-unknown-linux-musl
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
HOST_CODEX_BINARY_PATH="${HOST_CODEX_BINARY_PATH:-}"
HOST_CODEX_PLATFORM_PACKAGE="${HOST_CODEX_PLATFORM_PACKAGE:-@openai/codex-linux-x64}"
CODEX_TARGET_TRIPLE="${CODEX_TARGET_TRIPLE:-x86_64-unknown-linux-musl}"
CODEX_BINARY_RELATIVE_PATH="vendor/${CODEX_TARGET_TRIPLE}/codex/codex"
RUNNER_IMAGE_NAME="${RUNNER_IMAGE_NAME:-agent-sandbox-runner:latest}"
RUNNER_ARCHIVE_PATH="${RUNNER_ARCHIVE_PATH:-${ROOT_DIR}/dist/agent-sandbox-runner.tar}"
RUNNER_BUILD_NETWORK="${RUNNER_BUILD_NETWORK:-}"
DOCKER="${DOCKER:-docker}"

declare -a HOST_CODEX_BINARY_CANDIDATES=()

if [[ -z "${HOST_CODEX_BINARY_PATH}" ]]; then
  HOST_CODEX_BINARY_CANDIDATES+=(
    "${HOST_CODEX_ROOT}/node_modules/${HOST_CODEX_PLATFORM_PACKAGE}/${CODEX_BINARY_RELATIVE_PATH}"
    "${HOST_CODEX_ROOT}/${CODEX_BINARY_RELATIVE_PATH}"
  )

  HOST_CODEX_NODE_MODULES="$(cd "${HOST_CODEX_ROOT}/../.." 2>/dev/null && pwd || true)"
  if [[ -n "${HOST_CODEX_NODE_MODULES}" ]]; then
    HOISTED_CODEX_BINARY="${HOST_CODEX_NODE_MODULES}/${HOST_CODEX_PLATFORM_PACKAGE}/${CODEX_BINARY_RELATIVE_PATH}"
    HOST_CODEX_BINARY_CANDIDATES+=("${HOISTED_CODEX_BINARY}")
  fi

  if command -v node >/dev/null 2>&1; then
    NODE_RESOLVED_CODEX_BINARY="$(
      node -e '
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

const root = process.argv[1];
const platformPackage = process.argv[2];
const relativeBinaryPath = process.argv[3];
const packageJsonPath = path.join(root, "package.json");
const requireBase = fs.existsSync(packageJsonPath) ? packageJsonPath : path.join(root, "noop.js");

try {
  const requireFromCodexRoot = createRequire(requireBase);
  const platformPackageJson = requireFromCodexRoot.resolve(`${platformPackage}/package.json`);
  process.stdout.write(path.join(path.dirname(platformPackageJson), relativeBinaryPath));
} catch {
  process.exit(0);
}
' "${HOST_CODEX_ROOT}" "${HOST_CODEX_PLATFORM_PACKAGE}" "${CODEX_BINARY_RELATIVE_PATH}" 2>/dev/null || true
    )"
    if [[ -n "${NODE_RESOLVED_CODEX_BINARY}" ]]; then
      HOST_CODEX_BINARY_CANDIDATES+=("${NODE_RESOLVED_CODEX_BINARY}")
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
