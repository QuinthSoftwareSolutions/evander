#!/usr/bin/env bash
set -euo pipefail

agent_home="${HOME:-/home/agent}"
data_home="${XDG_DATA_HOME:-${agent_home}/.local/share}"
template_parent="${data_home}/godot/export_templates"
template_link="${template_parent}/${GODOT_TEMPLATE_VERSION}"
template_source="${GODOT_EXPORT_TEMPLATES_DIR}"

if [[ ! -e "${template_link}" && ! -L "${template_link}" ]]; then
  mkdir -p "${template_parent}"
  ln -s "${template_source}" "${template_link}"
fi

exec "${GODOT_REAL_BINARY}" "$@"
