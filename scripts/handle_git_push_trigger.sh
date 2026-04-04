#!/usr/bin/env bash
set -euo pipefail

show_help() {
  cat <<'USAGE'
Usage: handle_git_push_trigger.sh <bare-repo-path>

Read post-receive updates from stdin for a managed bare repository and enqueue
matching trigger:git:push events into the trigger orchestrator event queue.

Arguments:
  bare-repo-path  Absolute path to the bare repository receiving the push.

Configuration:
  AGENT_SANDBOX_TRIGGER_ORCHESTRATOR_CONFIG (optional)
    Path to trigger orchestrator config JSON.
    Default: <repo-root>/config/trigger-orchestrator/config.json
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  show_help
  exit 0
fi

if [[ $# -ne 1 ]]; then
  show_help >&2
  exit 1
fi

BARE_REPO_PATH="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONFIG_PATH="${AGENT_SANDBOX_TRIGGER_ORCHESTRATOR_CONFIG:-${ROOT_DIR}/config/trigger-orchestrator/config.json}"

if [[ ! -f "${CONFIG_PATH}" ]]; then
  exit 0
fi

UPDATES_FILE="$(mktemp)"
cat > "${UPDATES_FILE}"

PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

PYTHONPATH="${ROOT_DIR}/src${PYTHONPATH:+:${PYTHONPATH}}" \
  "${PYTHON_BIN}" - "${BARE_REPO_PATH}" "${CONFIG_PATH}" "${UPDATES_FILE}" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

def _resolve_repo(path_text: str) -> Path:
    return Path(path_text).expanduser().resolve()


def _load_config(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


def _resolve_config_path(value: object, *, config_path: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = config_path.parent / path
    return path.resolve()


def _branch_name(ref_name: str) -> str | None:
    normalized = ref_name.strip()
    prefix = "refs/heads/"
    if not normalized.startswith(prefix):
        return None
    branch = normalized.removeprefix(prefix).strip()
    return branch or None


def main() -> int:
    if len(sys.argv) != 4:
        return 1

    repo_path = _resolve_repo(sys.argv[1])
    config_path = Path(sys.argv[2]).expanduser().resolve()
    updates_path = Path(sys.argv[3]).expanduser().resolve()
    config = _load_config(config_path)
    runtime_dir = _resolve_config_path(config.get("runtime_dir", "runtime"), config_path=config_path)
    queue_path = config.get("event_queue_store_path")
    if queue_path:
        queue_store_path = _resolve_config_path(queue_path, config_path=config_path)
    else:
        queue_store_path = runtime_dir / "trigger-orchestrator-events.json"

    triggers = config.get("triggers", [])
    if not isinstance(triggers, list):
        triggers = []

    git_push_triggers = [
        trigger
        for trigger in triggers
        if isinstance(trigger, dict)
        and str(trigger.get("type", "")).strip() == "trigger:git:push"
        and str(trigger.get("id", "")).strip()
        and _resolve_repo(str(trigger.get("path_to_repo", ""))) == repo_path
    ]
    if not git_push_triggers:
        return 0

    queue_store_path.parent.mkdir(parents=True, exist_ok=True)
    if queue_store_path.exists():
        try:
            queue_payload = json.loads(queue_store_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            queue_payload = {}
        if not isinstance(queue_payload, dict):
            queue_payload = {}
    else:
        queue_payload = {}

    for raw_line in updates_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=2)
        if len(parts) != 3:
            continue
        old_rev, new_rev, ref_name = parts
        branch_name = _branch_name(ref_name)
        if branch_name is None:
            continue

        now = datetime.now(timezone.utc).isoformat()
        for trigger in git_push_triggers:
            trigger_id = str(trigger["id"]).strip()
            event_id = f"{trigger_id}:{branch_name}:{new_rev}"
            if event_id in queue_payload:
                continue
            queue_payload[event_id] = {
                "event_id": event_id,
                "trigger_id": trigger_id,
                "trigger_type": "trigger:git:push",
                "occurred_at": now,
                "payload": {
                    "branch_name": branch_name,
                    "commit_hash": new_rev,
                },
                "delivery_state": "pending",
                "delivered_at": None,
                "idempotency_key": None,
            }

    queue_store_path.write_text(json.dumps(queue_payload, indent=2, sort_keys=True), encoding="utf-8")

    return 0


raise SystemExit(main())
PY

rm -f "${UPDATES_FILE}"
