from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.common import (
    process_helpers,
    subprocess,
    vm_helpers,
)


class VmAgentLogSnapshotMixin:
    def get_agent_log_snapshot(
        self,
        target: str,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        vm_name = self._resolve_vm_name_from_target(target)
        if not vm_name:
            return "Agent log unavailable: no VM target selected."
        vm_state = vm_helpers.dominfo_field(vm_name, "State")
        if vm_state != "running":
            return f"Agent log unavailable: {vm_name} is not running."
        vm_ip = vm_helpers.resolve_vm_ip(vm_name)
        if not vm_ip:
            return f"Agent log unavailable: could not determine IP for {vm_name}."
        ssh_private_key_path = vm_helpers.resolve_private_key_path()
        if ssh_private_key_path is None:
            return "Agent log unavailable: missing SSH private key for guest access."

        remote_script = r"""
set -euo pipefail

AGENT_USER="${AGENT_USER:-agent}"
AGENT_HOME="$(getent passwd "${AGENT_USER}" | cut -d: -f6)"
if [[ -z "${AGENT_HOME}" ]]; then
  echo "Unknown agent user: ${AGENT_USER}" >&2
  exit 1
fi
LOG_DIR="${AGENT_HOME}/.agent-sandbox"
RUN_LOG_DIR="${AGENT_RUN_LOG_DIR:-/var/log/agent-sandbox/agent-runs}"
RESOLVED_REPO_NAME="${REPO_NAME:-}"
TAIL_LINES="${TAIL_LINES:-50}"

if [[ -z "${RESOLVED_REPO_NAME}" ]]; then
  shopt -s nullglob
  candidates=()
  for path in "${LOG_DIR}"/agent-*.pid "${LOG_DIR}"/agent-*.state; do
    name="$(basename "${path}")"
    name="${name#agent-}"
    name="${name%.pid}"
    name="${name%.state}"
    if [[ -n "${name}" ]]; then
      candidates+=("${name}")
    fi
  done
  if [[ -f "${LOG_DIR}/codex-resume-last.state" || -f "${LOG_DIR}/codex-heartbeat.pid" ]]; then
    candidates+=("codex-heartbeat")
  fi
  shopt -u nullglob

  if [[ ${#candidates[@]} -eq 0 ]]; then
    echo "No guest agent state or pid files found under ${LOG_DIR}"
    exit 0
  fi

  mapfile -t unique_candidates < <(printf '%s\n' "${candidates[@]}" | sort -u)
  if [[ ${#unique_candidates[@]} -ne 1 ]]; then
    echo "Multiple guest agent logs found under ${LOG_DIR}; pass an explicit repo name."
    printf 'Candidates:\n'
    printf '  %s\n' "${unique_candidates[@]}"
    exit 0
  fi

  RESOLVED_REPO_NAME="${unique_candidates[0]}"
fi

if [[ "${RESOLVED_REPO_NAME}" == "codex-heartbeat" ]]; then
  LOG_PATH="${RUN_LOG_DIR}/codex-heartbeat-run.log"
  LEGACY_LOG_PATH="${LOG_DIR}/codex-heartbeat-run.log"
else
  LOG_PATH="${RUN_LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
  LEGACY_LOG_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
fi
if [[ ! -f "${LOG_PATH}" ]]; then
  if [[ -f "${LEGACY_LOG_PATH}" ]]; then
    LOG_PATH="${LEGACY_LOG_PATH}"
  else
    echo "No guest agent log found at ${LOG_PATH} or ${LEGACY_LOG_PATH}"
    exit 0
  fi
fi

echo "Repo: ${RESOLVED_REPO_NAME}"
echo "Log: ${LOG_PATH}"
echo
TAIL_PATH="$(mktemp)"
trap 'rm -f "${TAIL_PATH}"' EXIT
tail -n "${TAIL_LINES}" "${LOG_PATH}" > "${TAIL_PATH}"
cat "${TAIL_PATH}"
python3 - "${TAIL_PATH}" "${AGENT_HOME}" <<'PY' || true
import json
import re
import sys
from pathlib import Path

tail_path = Path(sys.argv[1])
agent_home = Path(sys.argv[2])
try:
    tail_text = tail_path.read_text(encoding="utf-8", errors="replace")
except OSError:
    raise SystemExit(0)

matches = re.findall(r"(?im)^session id:\s*(\S+)\s*$", tail_text)
if not matches:
    raise SystemExit(0)
thread_id = matches[-1].strip()
if not thread_id:
    raise SystemExit(0)


def first_text(*values):
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, str):
                if part:
                    parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type and part_type not in {"text", "output_text", "assistant_message"}:
                continue
            text = first_text(part.get("text"), part.get("content"), part.get("message"))
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        return first_text(value.get("text"), value.get("content"), value.get("message"))
    return ""


def messages_from_event(event):
    if not isinstance(event, dict):
        return []
    event_type = str(event.get("type") or "")
    payload = event.get("payload")
    if event_type == "event_msg" and isinstance(payload, dict):
        payload_type = str(payload.get("type") or "")
        if payload_type == "agent_message":
            return [first_text(payload.get("message"))]
        if payload_type == "task_complete":
            return [first_text(payload.get("last_agent_message"))]
        return []
    if event_type == "response_item" and isinstance(payload, dict):
        if str(payload.get("type") or "") != "message":
            return []
        if str(payload.get("role") or "") != "assistant":
            return []
        return [
            first_text(
                payload.get("text"),
                payload.get("message"),
                content_text(payload.get("content")),
                content_text(payload.get("formatted")),
            )
        ]
    return []


def is_artifact(message):
    normalized = " ".join(message.strip().split()).casefold()
    return normalized in {"no response requested", "no response requested."}


roots = [
    agent_home / ".config" / "agent-sandbox" / "codex" / "sessions",
    agent_home / ".codex" / "sessions",
]
candidates = []
for root in roots:
    if not root.exists():
        continue
    try:
        candidates.extend(root.rglob(f"*{thread_id}*.jsonl"))
    except OSError:
        continue
if not candidates:
    raise SystemExit(0)

try:
    session_path = max(candidates, key=lambda path: path.stat().st_mtime)
except OSError:
    raise SystemExit(0)

messages = []
try:
    with session_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            try:
                event = json.loads(raw_line)
            except ValueError:
                continue
            messages.extend(messages_from_event(event))
except OSError:
    raise SystemExit(0)

seen = set()
for message in messages:
    if not message or is_artifact(message):
        continue
    if message in seen or message in tail_text:
        continue
    seen.add(message)
    print(message, flush=True)
PY
"""
        result: subprocess.CompletedProcess[str] | None = None
        attempted_errors: list[str] = []
        for ssh_user in self._control_ssh_users(vm_user):
            try:
                result = process_helpers.run_subprocess(
                    [
                        "ssh",
                        *vm_helpers.ssh_options(vm_name, vm_ip, ssh_private_key_path),
                        f"{ssh_user}@{vm_ip}",
                        "sudo",
                        "-n",
                        "env",
                        f"AGENT_USER={vm_user}",
                        "AGENT_RUN_LOG_DIR=/var/log/agent-sandbox/agent-runs",
                        f"REPO_NAME={repo_name or ''}",
                        f"TAIL_LINES={lines}",
                        "bash",
                        "-s",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20.0,
                    input=remote_script,
                )
            except subprocess.TimeoutExpired as exc:
                attempted_errors.append(
                    f"{ssh_user}@{vm_ip}: log read timed out after {exc.timeout:g} seconds"
                )
                result = None
                continue
            if result.returncode == 0:
                break
            attempted_errors.append((result.stderr or result.stdout or "").strip())
        if result is None:
            detail = next((candidate for candidate in reversed(attempted_errors) if candidate), "")
            return f"Agent log unavailable: {detail or 'no SSH users were configured.'}"
        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        if result.returncode != 0:
            detail = error or next(
                (candidate for candidate in reversed(attempted_errors) if candidate), ""
            )
            return f"Agent log unavailable: {detail or f'ssh exited with code {result.returncode}'}"
        return output or error or "Agent log is empty."
