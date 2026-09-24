#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: build_evander_runner_base_image.sh [--force]

Build the shared operating-system layer used by the Codex and Claude runner
images. A missing or intentionally refreshed upstream image is pulled with a
bounded timeout and retries before Docker starts the build.

Flags:
  --force                         Refresh the upstream image before building.

Environment:
  RUNNER_BASE_IMAGE               Shared image tag. Default: evander-runner-base:latest
  RUNNER_OS_BASE_IMAGE            Upstream image. Default: ubuntu:24.04
  RUNNER_BASE_PULL_ATTEMPTS       Pull attempts. Default: 3
  RUNNER_BASE_PULL_TIMEOUT_SECONDS
                                  Timeout for each pull. Default: 120
  RUNNER_BASE_PULL_RETRY_DELAY_SECONDS
                                  Delay between pulls. Default: 5
  RUNNER_BASE_BUILD_TIMEOUT_SECONDS
                                  Overall base build timeout. Default: 900
  RUNNER_BUILD_NETWORK            Optional Docker build network. Example: host
  DOCKER                          Docker CLI path. Default: docker
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
DOCKERFILE_PATH="${ROOT_DIR}/docker/evander-runner-base/Dockerfile"
RUNNER_BASE_IMAGE="${RUNNER_BASE_IMAGE:-evander-runner-base:latest}"
RUNNER_OS_BASE_IMAGE="${RUNNER_OS_BASE_IMAGE:-ubuntu:24.04}"
RUNNER_BASE_PULL_ATTEMPTS="${RUNNER_BASE_PULL_ATTEMPTS:-3}"
RUNNER_BASE_PULL_TIMEOUT_SECONDS="${RUNNER_BASE_PULL_TIMEOUT_SECONDS:-120}"
RUNNER_BASE_PULL_RETRY_DELAY_SECONDS="${RUNNER_BASE_PULL_RETRY_DELAY_SECONDS:-5}"
RUNNER_BASE_BUILD_TIMEOUT_SECONDS="${RUNNER_BASE_BUILD_TIMEOUT_SECONDS:-900}"
RUNNER_BUILD_NETWORK="${RUNNER_BUILD_NETWORK:-}"
DOCKER="${DOCKER:-docker}"

if [[ "${RUNNER_FORCE_REBUILD:-}" != "" ]]; then
  case "${RUNNER_FORCE_REBUILD,,}" in
    1|true|yes|on)
      FORCE_REBUILD=1
      ;;
  esac
fi

require_nonnegative_integer() {
  local name="$1"
  local value="$2"
  if [[ ! "${value}" =~ ^[0-9]+$ ]]; then
    echo "${name} must be a non-negative integer; got ${value}." >&2
    exit 2
  fi
}

require_positive_integer() {
  local name="$1"
  local value="$2"
  require_nonnegative_integer "${name}" "${value}"
  if [[ "${value}" -eq 0 ]]; then
    echo "${name} must be greater than zero." >&2
    exit 2
  fi
}

require_positive_integer RUNNER_BASE_PULL_ATTEMPTS "${RUNNER_BASE_PULL_ATTEMPTS}"
require_positive_integer RUNNER_BASE_PULL_TIMEOUT_SECONDS "${RUNNER_BASE_PULL_TIMEOUT_SECONDS}"
require_nonnegative_integer RUNNER_BASE_PULL_RETRY_DELAY_SECONDS "${RUNNER_BASE_PULL_RETRY_DELAY_SECONDS}"
require_positive_integer RUNNER_BASE_BUILD_TIMEOUT_SECONDS "${RUNNER_BASE_BUILD_TIMEOUT_SECONDS}"

if [[ ! -f "${DOCKERFILE_PATH}" ]]; then
  echo "Missing shared runner base Dockerfile: ${DOCKERFILE_PATH}" >&2
  exit 1
fi
if ! command -v "${DOCKER}" >/dev/null 2>&1; then
  echo "Missing Docker CLI: ${DOCKER}" >&2
  exit 1
fi
if ! command -v timeout >/dev/null 2>&1; then
  echo "Missing timeout command; install GNU coreutils." >&2
  exit 1
fi

BASE_DOCKERFILE_SHA256="$(sha256sum "${DOCKERFILE_PATH}" | awk '{print $1}')"
BASE_INPUT_SHA256="$(printf '%s\n%s\n' "${BASE_DOCKERFILE_SHA256}" "${RUNNER_OS_BASE_IMAGE}" | sha256sum | awk '{print $1}')"
if [[ "${FORCE_REBUILD}" -ne 1 ]]; then
  EXISTING_BASE_INPUT_SHA256="$(
    "${DOCKER}" image inspect \
      --format '{{ index .Config.Labels "io.evander.runner.base-input-sha256" }}' \
      "${RUNNER_BASE_IMAGE}" 2>/dev/null || true
  )"
  if [[ "${EXISTING_BASE_INPUT_SHA256}" == "${BASE_INPUT_SHA256}" ]]; then
    echo "Reused local ${RUNNER_BASE_IMAGE} shared base." >&2
    exit 0
  fi
fi

if [[ "${FORCE_REBUILD}" -eq 1 ]] || ! "${DOCKER}" image inspect "${RUNNER_OS_BASE_IMAGE}" >/dev/null 2>&1; then
  pulled=0
  for ((attempt = 1; attempt <= RUNNER_BASE_PULL_ATTEMPTS; attempt++)); do
    echo "Pulling ${RUNNER_OS_BASE_IMAGE} (attempt ${attempt}/${RUNNER_BASE_PULL_ATTEMPTS}, timeout ${RUNNER_BASE_PULL_TIMEOUT_SECONDS}s)" >&2
    if timeout --foreground --kill-after=10s "${RUNNER_BASE_PULL_TIMEOUT_SECONDS}s" \
      "${DOCKER}" pull "${RUNNER_OS_BASE_IMAGE}"; then
      pulled=1
      break
    fi
    if [[ "${attempt}" -lt "${RUNNER_BASE_PULL_ATTEMPTS}" && "${RUNNER_BASE_PULL_RETRY_DELAY_SECONDS}" -gt 0 ]]; then
      sleep "${RUNNER_BASE_PULL_RETRY_DELAY_SECONDS}"
    fi
  done
  if [[ "${pulled}" -ne 1 ]]; then
    echo "Failed to pull ${RUNNER_OS_BASE_IMAGE} after ${RUNNER_BASE_PULL_ATTEMPTS} bounded attempts." >&2
    exit 1
  fi
fi

build_args=(
  build
  --build-arg "RUNNER_OS_BASE_IMAGE=${RUNNER_OS_BASE_IMAGE}"
  --build-arg "RUNNER_BASE_INPUT_SHA256=${BASE_INPUT_SHA256}"
  -t "${RUNNER_BASE_IMAGE}"
)
if [[ -n "${RUNNER_BUILD_NETWORK}" ]]; then
  build_args+=(--network "${RUNNER_BUILD_NETWORK}")
fi
build_args+=("${ROOT_DIR}/docker/evander-runner-base")

echo "Building ${RUNNER_BASE_IMAGE} (timeout ${RUNNER_BASE_BUILD_TIMEOUT_SECONDS}s)" >&2
if ! timeout --foreground --kill-after=30s "${RUNNER_BASE_BUILD_TIMEOUT_SECONDS}s" \
  "${DOCKER}" "${build_args[@]}"; then
  echo "Failed to build ${RUNNER_BASE_IMAGE} or exceeded the bounded build window." >&2
  exit 1
fi
