#!/usr/bin/env bash
set -euo pipefail

# Git invokes this fixed host-owned helper with a human-readable prompt. Secrets are supplied
# only through the child process environment and are never written to a URL or Git config.
prompt="${1:-}"
case "${prompt,,}" in
  *username*)
    printf '%s\n' "${EVANDER_GIT_USERNAME:-}"
    ;;
  *password*|*token*)
    printf '%s\n' "${EVANDER_GIT_PASSWORD:-}"
    ;;
  *)
    exit 1
    ;;
esac
