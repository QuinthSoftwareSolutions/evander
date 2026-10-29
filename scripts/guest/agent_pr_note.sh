#!/usr/bin/env bash
set -Eeuo pipefail

show_help() {
  cat <<'EOF'
Usage:
  agent-pr-note set (-m TEXT | -F FILE)
  agent-pr-note append (-m TEXT | -F FILE)
  agent-pr-note show
  agent-pr-note ensure-head

Maintains an additive PR-summary note for the current branch and attaches the
full summary to HEAD on the configured evander.prNotesRef notes ref.
EOF
}

TEMP_FILES=()

cleanup_temp_files() {
  if (( ${#TEMP_FILES[@]} == 0 )); then
    return
  fi
  rm -f "${TEMP_FILES[@]}"
}

trap cleanup_temp_files EXIT

make_temp_file() {
  local temp_file
  temp_file="$(mktemp)"
  TEMP_FILES+=("${temp_file}")
  printf '%s\n' "${temp_file}"
}

die() {
  echo "$*" >&2
  exit 1
}

require_repo() {
  git rev-parse --show-toplevel >/dev/null 2>&1 || die "agent-pr-note must run inside a Git worktree."
}

branch_name() {
  local branch
  if branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null)"; then
    printf '%s\n' "${branch}"
    return
  fi

  git rev-parse --short HEAD
}

cache_file_for_current_branch() {
  local git_dir
  local branch

  git_dir="$(git rev-parse --git-dir)"
  branch="$(branch_name)"
  printf '%s/evander/agent-pr-cache/%s.md\n' "${git_dir}" "${branch}"
}

pr_notes_ref() {
  local notes_ref
  notes_ref="$(git config --get evander.prNotesRef 2>/dev/null || true)"
  if [[ -z "${notes_ref}" ]]; then
    notes_ref="refs/notes/agent-pr"
  fi
  printf '%s\n' "${notes_ref}"
}

read_head_note() {
  git notes --ref="$(pr_notes_ref)" show HEAD 2>/dev/null || true
}

read_cached_or_head_note() {
  local cache_file
  cache_file="$(cache_file_for_current_branch)"

  if [[ -f "${cache_file}" ]]; then
    cat "${cache_file}"
    return
  fi

  read_head_note
}

write_current_note() {
  local content_file="$1"
  local cache_file

  cache_file="$(cache_file_for_current_branch)"
  mkdir -p "$(dirname "${cache_file}")"
  cp "${content_file}" "${cache_file}"
  git notes --ref="$(pr_notes_ref)" add -f -F "${cache_file}" HEAD >/dev/null
}

read_input_to_file() {
  local output_file="$1"
  shift

  local mode=""
  local payload=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      -m|--message)
        [[ -z "${mode}" ]] || die "Specify only one of -m/--message or -F/--file."
        mode="message"
        payload="${2:-}"
        [[ -n "${payload}" ]] || die "Missing value for $1."
        shift 2
        ;;
      -F|--file)
        [[ -z "${mode}" ]] || die "Specify only one of -m/--message or -F/--file."
        mode="file"
        payload="${2:-}"
        [[ -n "${payload}" ]] || die "Missing value for $1."
        [[ -f "${payload}" ]] || die "Input file not found: ${payload}"
        shift 2
        ;;
      -h|--help)
        show_help
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
  done

  [[ -n "${mode}" ]] || die "Provide note content with -m/--message or -F/--file."

  if [[ "${mode}" == "message" ]]; then
    printf '%s\n' "${payload}" > "${output_file}"
    return
  fi

  cp "${payload}" "${output_file}"
}

command_set() {
  local input_file
  input_file="$(make_temp_file)"

  read_input_to_file "${input_file}" "$@"
  write_current_note "${input_file}"
}

command_append() {
  local input_file
  local aggregate_file
  local existing

  input_file="$(make_temp_file)"
  aggregate_file="$(make_temp_file)"

  read_input_to_file "${input_file}" "$@"
  existing="$(read_cached_or_head_note)"
  if [[ -n "${existing}" ]]; then
    printf '%s\n\n' "${existing}" > "${aggregate_file}"
  fi
  cat "${input_file}" >> "${aggregate_file}"
  write_current_note "${aggregate_file}"
}

command_show() {
  local note
  note="$(read_cached_or_head_note)"
  [[ -n "${note}" ]] || die "No cached agent PR note exists for this branch."
  printf '%s\n' "${note}"
}

command_ensure_head() {
  local cache_file
  local head_note

  cache_file="$(cache_file_for_current_branch)"
  if [[ -f "${cache_file}" ]]; then
    git notes --ref="$(pr_notes_ref)" add -f -F "${cache_file}" HEAD >/dev/null
    return
  fi

  head_note="$(read_head_note)"
  if [[ -n "${head_note}" ]]; then
    mkdir -p "$(dirname "${cache_file}")"
    printf '%s\n' "${head_note}" > "${cache_file}"
    return
  fi

  die "No agent PR note found for this branch. Run 'git agent-pr-note set -m \"...\"' first."
}

main() {
  require_repo

  local command="${1:-}"
  shift || true

  case "${command}" in
    set)
      command_set "$@"
      ;;
    append)
      command_append "$@"
      ;;
    show)
      command_show "$@"
      ;;
    ensure-head)
      command_ensure_head "$@"
      ;;
    -h|--help|"")
      show_help
      ;;
    *)
      die "Unknown command: ${command}"
      ;;
  esac
}

main "$@"
