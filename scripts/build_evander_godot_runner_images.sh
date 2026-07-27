#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'EOF'
Usage: build_evander_godot_runner_images.sh [--variant VARIANT] [--force]

Build optional, host-seeded Godot runner images. VARIANT is one of:
  all (default), godot, godot-native, godot-render

This command only creates local Docker images by default. It does not edit the
Evander image catalog, add images to the Ansible worker archive allowlist, or
produce archives consumed by CI/CD. Set SAVE_GODOT_RUNNER_ARCHIVES=1 only when
an operator explicitly needs manual-transfer tarballs under dist/optional/.

Flags:
  --variant VARIANT       Build all images or one named variant.
  --force                 Disable Docker's build cache.

Required host inputs:
  HOST_GODOT_BINARY_PATH
                          Godot Linux x86_64 executable. Defaults to godot4 or
                          godot resolved from PATH.
  HOST_GODOT_EXPORT_TEMPLATES_PATH
                          Matching export-template directory or .tpz archive.
                          Default:
                          ~/.local/share/godot/export_templates/4.7.1.stable

Environment:
  GODOT_VERSION           Godot release version. Default: 4.7.1
  GODOT_TEMPLATE_VERSION  Godot template directory version. Default: 4.7.1.stable
  EVANDER_RUNNER_BASE_IMAGE
                          Existing base runner image. Default: evander-runner:latest
  GODOT_RUNNER_IMAGE      Standard tag. Default: evander-runner-godot:4.7.1
  GODOT_NATIVE_RUNNER_IMAGE
                          Native-toolchain tag. Default: evander-runner-godot-native:4.7.1
  GODOT_RENDER_RUNNER_IMAGE
                          Software-render tag. Default: evander-runner-godot-render:4.7.1
  SAVE_GODOT_RUNNER_ARCHIVES
                          Save manual-transfer tarballs when true. Default: false
  GODOT_RUNNER_ARCHIVE_DIR
                          Optional archive directory. Default: dist/optional
  RUNNER_BUILD_NETWORK    Optional Docker build network. Example: host
  DOCKER                  Docker CLI path. Default: docker
EOF
}

variant="all"
force_rebuild=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
    --variant)
      if [[ "$#" -lt 2 ]]; then
        echo "Missing value for --variant." >&2
        exit 1
      fi
      variant="$2"
      shift 2
      ;;
    --force)
      force_rebuild=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      show_help >&2
      exit 1
      ;;
  esac
done

case "${variant}" in
  all|godot|godot-native|godot-render)
    ;;
  *)
    echo "Unsupported variant: ${variant}" >&2
    exit 1
    ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root_dir="$(cd "${script_dir}/.." && pwd)"
caller_user="${SUDO_USER:-${USER:-$(id -un)}}"
caller_home="$(getent passwd "${caller_user}" | cut -d: -f6)"

godot_version="${GODOT_VERSION:-4.7.1}"
godot_template_version="${GODOT_TEMPLATE_VERSION:-${godot_version}.stable}"
evander_runner_base_image="${EVANDER_RUNNER_BASE_IMAGE:-evander-runner:latest}"
godot_runner_image="${GODOT_RUNNER_IMAGE:-evander-runner-godot:${godot_version}}"
godot_native_runner_image="${GODOT_NATIVE_RUNNER_IMAGE:-evander-runner-godot-native:${godot_version}}"
godot_render_runner_image="${GODOT_RENDER_RUNNER_IMAGE:-evander-runner-godot-render:${godot_version}}"
godot_runner_archive_dir="${GODOT_RUNNER_ARCHIVE_DIR:-${root_dir}/dist/optional}"
runner_build_network="${RUNNER_BUILD_NETWORK:-}"
docker_cli="${DOCKER:-docker}"
save_archives=0
save_archives_setting="${SAVE_GODOT_RUNNER_ARCHIVES:-false}"

case "${save_archives_setting,,}" in
  1|true|yes|on)
    save_archives=1
    ;;
esac

host_godot_binary_path="${HOST_GODOT_BINARY_PATH:-}"
if [[ -z "${host_godot_binary_path}" ]]; then
  if command -v godot4 >/dev/null 2>&1; then
    host_godot_binary_path="$(command -v godot4)"
  elif command -v godot >/dev/null 2>&1; then
    host_godot_binary_path="$(command -v godot)"
  fi
fi
if [[ -z "${host_godot_binary_path}" || ! -x "${host_godot_binary_path}" ]]; then
  echo "Missing executable Godot binary; set HOST_GODOT_BINARY_PATH." >&2
  exit 1
fi
host_godot_binary_path="$(readlink -f "${host_godot_binary_path}")"

host_godot_export_templates_path="${HOST_GODOT_EXPORT_TEMPLATES_PATH:-${caller_home}/.local/share/godot/export_templates/${godot_template_version}}"
if [[ ! -e "${host_godot_export_templates_path}" ]]; then
  echo "Missing Godot export templates: ${host_godot_export_templates_path}" >&2
  echo "Set HOST_GODOT_EXPORT_TEMPLATES_PATH to the matching directory or .tpz archive." >&2
  exit 1
fi
if [[ -f "${host_godot_export_templates_path}" ]] && ! command -v unzip >/dev/null 2>&1; then
  echo "The unzip command is required for a Godot .tpz template archive." >&2
  exit 1
fi

if ! command -v "${docker_cli}" >/dev/null 2>&1; then
  echo "Missing Docker CLI: ${docker_cli}" >&2
  exit 1
fi
if ! "${docker_cli}" image inspect "${evander_runner_base_image}" >/dev/null 2>&1; then
  echo "Missing base image ${evander_runner_base_image}." >&2
  echo "Build it first with scripts/build_evander_runner_image.sh." >&2
  exit 1
fi

reported_version="$("${host_godot_binary_path}" --headless --version | head -n1)"
if [[ "${reported_version}" != "${godot_version}."* && "${reported_version}" != "${godot_version}" ]]; then
  echo "Godot binary reports ${reported_version}; expected ${godot_version}." >&2
  exit 1
fi

declare -a temporary_paths=()
new_context_path=""
cleanup() {
  local temporary_path
  for temporary_path in "${temporary_paths[@]}"; do
    if [[ -d "${temporary_path}" ]]; then
      rm -rf "${temporary_path}"
    fi
  done
}
trap cleanup EXIT

new_context() {
  new_context_path="$(mktemp -d)"
  temporary_paths+=("${new_context_path}")
}

copy_export_templates() {
  local destination="$1"
  local extraction_path=""
  local source_path="${host_godot_export_templates_path}"
  local template_reported_version=""
  local version_file=""

  if [[ -f "${source_path}" ]]; then
    extraction_path="$(mktemp -d)"
    temporary_paths+=("${extraction_path}")
    unzip -q "${source_path}" -d "${extraction_path}"
    version_file="$(find "${extraction_path}" -maxdepth 3 -type f -name version.txt -print -quit)"
    if [[ -z "${version_file}" ]]; then
      echo "The template archive does not contain version.txt: ${source_path}" >&2
      exit 1
    fi
    source_path="$(dirname "${version_file}")"
  elif [[ -f "${source_path}/templates/version.txt" ]]; then
    source_path="${source_path}/templates"
  fi

  if [[ ! -f "${source_path}/version.txt" ]]; then
    echo "The export-template input does not contain version.txt: ${source_path}" >&2
    exit 1
  fi
  template_reported_version="$(tr -d '\r\n' < "${source_path}/version.txt")"
  if [[ "${template_reported_version}" != "${godot_template_version}" ]]; then
    echo "Export templates report ${template_reported_version}; expected ${godot_template_version}." >&2
    exit 1
  fi

  mkdir -p "${destination}"
  cp -a "${source_path}/." "${destination}/"
}

docker_build() {
  local image_name="$1"
  local context_path="$2"
  shift 2
  local -a build_args=(build --tag "${image_name}")

  if [[ -n "${runner_build_network}" ]]; then
    build_args+=(--network "${runner_build_network}")
  fi
  if [[ "${force_rebuild}" -eq 1 ]]; then
    build_args+=(--no-cache)
  fi
  build_args+=("$@" "${context_path}")
  "${docker_cli}" "${build_args[@]}"
}

save_image_if_requested() {
  local image_name="$1"
  local archive_basename="$2"
  local archive_path

  if [[ "${save_archives}" -eq 0 ]]; then
    return 0
  fi

  mkdir -p "${godot_runner_archive_dir}"
  archive_path="${godot_runner_archive_dir}/${archive_basename}.tar"
  "${docker_cli}" save --output "${archive_path}" "${image_name}"
  echo "Saved optional manual-transfer archive ${archive_path}"
}

build_godot_image() {
  local context_path
  new_context
  context_path="${new_context_path}"
  cp "${root_dir}/docker/evander-runner-godot/Dockerfile" "${context_path}/Dockerfile"
  cp "${root_dir}/docker/evander-runner-godot/godot-launcher.sh" "${context_path}/godot-launcher.sh"
  cp "${root_dir}/docker/evander-runner-godot/LICENSE.godot.txt" "${context_path}/LICENSE.godot.txt"
  cp "${host_godot_binary_path}" "${context_path}/godot"
  chmod 0755 "${context_path}/godot"
  copy_export_templates "${context_path}/export_templates"

  docker_build \
    "${godot_runner_image}" \
    "${context_path}" \
    --build-arg "EVANDER_RUNNER_BASE_IMAGE=${evander_runner_base_image}" \
    --build-arg "GODOT_VERSION=${godot_version}" \
    --build-arg "GODOT_TEMPLATE_VERSION=${godot_template_version}"
  save_image_if_requested "${godot_runner_image}" "evander-runner-godot-${godot_version}"
}

build_native_image() {
  local context_path
  new_context
  context_path="${new_context_path}"
  cp "${root_dir}/docker/evander-runner-godot-native/Dockerfile" "${context_path}/Dockerfile"
  docker_build \
    "${godot_native_runner_image}" \
    "${context_path}" \
    --build-arg "EVANDER_GODOT_BASE_IMAGE=${godot_runner_image}"
  save_image_if_requested "${godot_native_runner_image}" "evander-runner-godot-native-${godot_version}"
}

build_render_image() {
  local context_path
  new_context
  context_path="${new_context_path}"
  cp "${root_dir}/docker/evander-runner-godot-render/Dockerfile" "${context_path}/Dockerfile"
  cp \
    "${root_dir}/docker/evander-runner-godot-render/godot-render-launcher.sh" \
    "${context_path}/godot-render-launcher.sh"
  docker_build \
    "${godot_render_runner_image}" \
    "${context_path}" \
    --build-arg "EVANDER_GODOT_BASE_IMAGE=${godot_runner_image}"
  save_image_if_requested "${godot_render_runner_image}" "evander-runner-godot-render-${godot_version}"
}

build_godot_image
case "${variant}" in
  all)
    build_native_image
    build_render_image
    ;;
  godot-native)
    build_native_image
    ;;
  godot-render)
    build_render_image
    ;;
esac

echo "Built optional Godot runner variant: ${variant}"
echo "No deployment configuration was changed."
