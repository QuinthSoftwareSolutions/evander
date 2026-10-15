#!/usr/bin/env bash
# Read cache metadata as data; never source a restored manifest.

runner_archive_cache_matches() {
  local archive_path="$1" manifest_path="$2" expected_digest="$3"
  local input_digest archive_digest
  [[ -s "$archive_path" && -s "$manifest_path" ]] || return 1
  input_digest="$(sed -n 's/^manifest_sha256=//p' "$manifest_path")"
  archive_digest="$(sed -n 's/^archive_sha256=//p' "$manifest_path")"
  [[ "$input_digest" == "$expected_digest" ]] || return 1
  [[ "$archive_digest" =~ ^[0-9a-f]{64}$ ]] || return 1
  [[ "$(sha256sum "$archive_path" | awk '{print $1}')" == "$archive_digest" ]]
}
