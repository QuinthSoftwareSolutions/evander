#!/usr/bin/env bash

load_root_secrets_env() {
  local secrets_env="${ROOT_DIR}/secrets.env"

  [[ -f "${secrets_env}" ]] || return 0

  set -a
  # shellcheck source=/dev/null
  . "${secrets_env}"
  set +a
}

load_issue_setup_config() {
  local issue_setup_json="${ROOT_DIR}/runtime/directory-config/root/issue-setup.json"

  [[ -f "${issue_setup_json}" ]] || return 0

  eval "$(
    python3 - "${issue_setup_json}" <<'PY'
import json
import shlex
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)
if not isinstance(payload, dict):
    raise SystemExit(0)

fields = {
    "AGENT_SANDBOX_GIT_ROOT": payload.get("git_root"),
    "AGENT_SANDBOX_GIT_HOST": payload.get("git_host"),
    "AGENT_SANDBOX_GIT_USER": payload.get("git_user"),
    "AGENT_SANDBOX_GIT_SSH_ALIAS": payload.get("git_ssh_alias"),
    "ENABLE_GUEST_HEARTBEAT": "1" if payload.get("enable_guest_heartbeat") else "0",
    "GUEST_HEARTBEAT_PROMPT": payload.get("guest_heartbeat_prompt"),
    "AGENT_POST_EXEC_VERIFICATION_PROMPT": payload.get("post_exec_verification_prompt"),
    "AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV": payload.get("issue_setup_azure_devops_pr_pat_env"),
}

for key, value in fields.items():
    if value is None:
        continue
    text = str(value).strip()
    if not text and key != "ENABLE_GUEST_HEARTBEAT":
        continue
    print(f'if [[ -z "${{{key}:-}}" ]]; then export {key}={shlex.quote(text)}; fi')
PY
  )"
}

load_provider_config() {
  local providers_json="${ROOT_DIR}/runtime/directory-config/root/providers.json"

  [[ -f "${providers_json}" ]] || return 0

  eval "$(
    python3 - "${providers_json}" <<'PY'
import json
import shlex
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    payload = json.loads(path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    raise SystemExit(0)
if not isinstance(payload, dict):
    raise SystemExit(0)

azure_pat_env = payload.get("azure_devops_pr_pat_env")
if isinstance(azure_pat_env, str) and azure_pat_env.strip():
    print(f'if [[ -z "${{AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV:-}}" ]]; then export AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV={shlex.quote(azure_pat_env.strip())}; fi')
PY
  )"
}
