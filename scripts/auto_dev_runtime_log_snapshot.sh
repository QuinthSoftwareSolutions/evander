#!/usr/bin/env bash
set -uo pipefail
umask 077

PROJECT_DIR="${1:-/opt/truman}"
RUNTIME_DIR="${PROJECT_DIR}/runtime"
SINCE="${AUTO_DEV_LOG_SINCE:-24 hours ago}"
OUT="${AUTO_DEV_LOG_OUTPUT:-$(mktemp -t truman-auto-dev-runtime-XXXXXX.log)}"
RAW_OUT="$(mktemp -t truman-auto-dev-runtime-raw-XXXXXX.log)"

cleanup() {
  rm -f "$RAW_OUT"
}
trap cleanup EXIT

redact_snapshot() {
  if ! command -v perl >/dev/null 2>&1; then
    printf '# Truman runtime log snapshot\n'
    printf 'redaction_error=perl_not_available\n'
    printf 'Raw snapshot was withheld because redaction could not run.\n'
    return 0
  fi

  perl -pe '
    s/(Authorization\s*[:=]\s*)(Bearer|Basic|Token)\s+[A-Za-z0-9._~+\/=-]+/${1}${2} <redacted>/ig;
    s/("(?:authorization|proxy-authorization)"\s*:\s*)"(?:Bearer|Basic|Token)\s+[^"]*"/${1}"<redacted>"/ig;
    s/((?:x-api-key|api-key|api_key|access_token|refresh_token|client_secret|id_token|password|passwd|pwd|pat|token|secret|apiKey|connection_string|connstr)\s*[=:]\s*)("[^"]*"|'\''[^'\'']*'\''|[^,\s;}]+)/${1}<redacted>/ig;
    s/("(?:[^"]*(?:key|token|secret|password|passwd|pwd|pat|connection_string|connstr)[^"]*)"\s*:\s*)("[^"]*"|null)/${1}"<redacted>"/ig;
    s/((?:Password|Pwd|User\s+Password)\s*=\s*)[^;,\s]+/${1}<redacted>/ig;
    s#(https?://)([^:/@\s]+):([^/@\s]+)@#${1}<redacted>:<redacted>@#ig;
    s/\bsk-[A-Za-z0-9_-]{20,}\b/sk-<redacted>/g;
    s/\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9_]{20,}\b/github-token-<redacted>/g;
    s/\bgithub_pat_[A-Za-z0-9_]{20,}\b/github-pat-<redacted>/g;
    s/\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/jwt-<redacted>/g;
    s/([?&](?:sig|signature|secrets?|tokens?|password|code)=)[^&\s\"},]+/${1}<redacted>/ig;
    s/(Set-Cookie\s*:\s*)[^\r\n]+/${1}<redacted>/ig;
    s/-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----/<redacted-private-key>/sg;
  '
}

{
  printf '# Truman runtime log snapshot\n'
  printf 'generated_at=%s\n' "$(date -Is)"
  printf 'project_dir=%s\n' "$PROJECT_DIR"
  printf 'runtime_dir=%s\n' "$RUNTIME_DIR"
  printf 'since=%s\n' "$SINCE"
  printf 'redaction=enabled\n\n'

  printf '## Failed systemd units\n'
  systemctl --no-pager --failed 2>&1 || true
  printf '\n'

  printf '## Selected service warnings and errors\n'
  for unit in \
    agent-sandbox-admin-web.service \
    agent-sandbox-trigger-orchestrator.service \
    agent-sandbox-effect-orchestrator.service \
    agent-sandbox-gateway.service \
    agent-sandbox-worker-daemon.service \
    agent-sandbox-ado-worker.service \
    agent-sandbox-teams-ingest.service; do
    printf '\n### %s\n' "$unit"
    journalctl --no-pager -u "$unit" --since "$SINCE" -p warning..alert -n 200 2>&1 || true
  done

  printf '\n## Runtime audit errors and denials\n'
  if [ -f "$RUNTIME_DIR/audit.jsonl" ]; then
    tail -n 800 "$RUNTIME_DIR/audit.jsonl" \
      | grep -E '"decision": "(error|denied)"|"error_class"' \
      | tail -n 160 || true
  else
    printf 'missing %s/audit.jsonl\n' "$RUNTIME_DIR"
  fi

  printf '\n## Recent orchestrator execution failures\n'
  for file in \
    "$RUNTIME_DIR/effect-orchestrator-executions.json" \
    "$RUNTIME_DIR/gate-orchestrator-executions.json" \
    "$RUNTIME_DIR/worker-jobs.json"; do
    printf '\n### %s\n' "$file"
    if [ -f "$file" ]; then
      tail -c 120000 "$file" \
        | grep -E '"success": false|"status": "(failed|error)"|"error"|"exit_code": [1-9]' \
        | tail -n 120 || true
    else
      printf 'missing\n'
    fi
  done
} > "$RAW_OUT"

redact_snapshot < "$RAW_OUT" > "$OUT"
rm -f "$RAW_OUT"

cat "$OUT"
if command -v jq >/dev/null 2>&1; then
  jq -n --arg snapshot_path "$OUT" --arg since "$SINCE" --arg redaction "enabled" \
    --argjson content_chars "$(wc -c < "$OUT" | tr -d ' ')" \
    '{snapshot_path:$snapshot_path,since:$since,redaction:$redaction,content_chars:$content_chars}'
fi
