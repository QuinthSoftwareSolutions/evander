#!/usr/bin/env bash
set -Eeuo pipefail

show_help() {
  cat <<'EOF'
Usage:
  push_existing_pr_branch.sh <open-agent-pr args...>

Run open_agent_pr.sh in push-only mode and convert its output into a final JSON
object for effect:bash:script. Preflight failures emitted by open_agent_pr.sh are
treated as failed script executions so workflow gates can resume the agent.

Configuration:
  OPEN_AGENT_PR may override the open_agent_pr.sh path. By default this script
  uses a sibling scripts/open_agent_pr.sh when present, then falls back to the
  deployed Truman path /opt/truman/scripts/open_agent_pr.sh.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

default_open_agent_pr() {
  if [[ -x "${script_dir}/open_agent_pr.sh" ]]; then
    printf '%s\n' "${script_dir}/open_agent_pr.sh"
    return
  fi
  if [[ -x "/opt/truman/scripts/open_agent_pr.sh" ]]; then
    printf '%s\n' "/opt/truman/scripts/open_agent_pr.sh"
    return
  fi
  printf '%s\n' "${script_dir}/open_agent_pr.sh"
}

OPEN_AGENT_PR="${OPEN_AGENT_PR:-$(default_open_agent_pr)}"

output=""
status=0
if output="$("${OPEN_AGENT_PR}" "$@" 2>&1)"; then
  status=0
else
  status=$?
fi

if [[ -n "${output}" ]]; then
  printf '%s\n' "${output}" >&2
fi

OUTPUT="${output}" STATUS="${status}" python3 - <<'PY'
import json
import os
import sys

output = os.environ.get("OUTPUT", "")
try:
    status = int(os.environ.get("STATUS", "1") or "1")
except ValueError:
    status = 1

payload = None
for line in output.splitlines():
    if not line.startswith("AGENT_PR_RESULT "):
        continue
    try:
        parsed = json.loads(line.removeprefix("AGENT_PR_RESULT ").strip())
    except json.JSONDecodeError:
        continue
    if isinstance(parsed, dict):
        payload = parsed
        break

message = output.strip() or f"open_agent_pr.sh exited with code {status}"
if payload is None:
    payload = {
        "success": status == 0,
        "action": "push_existing_pr_branch",
        "message": message,
    }
else:
    payload = dict(payload)
    payload["action"] = "push_existing_pr_branch"
    payload.setdefault("message", message)
    payload.setdefault("success", status == 0)

print(json.dumps(payload, sort_keys=True))
if payload.get("success") is False:
    sys.exit(status if status else 20)
sys.exit(status)
PY
