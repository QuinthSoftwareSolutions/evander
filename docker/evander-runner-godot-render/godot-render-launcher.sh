#!/usr/bin/env bash
set -euo pipefail

rendering_method="${GODOT_RENDERING_METHOD:-gl_compatibility}"
screen="${GODOT_RENDER_SCREEN:-1920x1080x24}"
declare -a audio_args=(--audio-driver Dummy)
declare -a display_args=(--display-driver x11)
declare -a rendering_args=(--rendering-method "${rendering_method}")

for arg in "$@"; do
  case "${arg}" in
    --audio-driver|--audio-driver=*)
      audio_args=()
      ;;
    --display-driver|--display-driver=*)
      display_args=()
      ;;
    --rendering-method|--rendering-method=*)
      rendering_args=()
      ;;
  esac
done

export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
export GALLIUM_DRIVER="${GALLIUM_DRIVER:-llvmpipe}"

exec xvfb-run \
  --auto-servernum \
  --server-args="-screen 0 ${screen} -nolisten tcp" \
  godot "${display_args[@]}" "${audio_args[@]}" "${rendering_args[@]}" "$@"
