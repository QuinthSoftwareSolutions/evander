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
  "${PYTHON_BIN}" - "${ROOT_DIR}" "${BARE_REPO_PATH}" "${CONFIG_PATH}" "${UPDATES_FILE}" <<'PY'
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
import fcntl

from agent_sandbox.triggers.providers.git_push import build_git_push_payloads, git_push_trigger_matches_repo

def _resolve_repo(path_text: str) -> Path:
    return Path(path_text).expanduser().resolve()


def _load_config(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


def _resolve_config_path(value: object, *, config_path: Path, project_dir: Path) -> Path:
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        try:
            config_path.relative_to(project_dir)
        except ValueError:
            base_dir = config_path.parent
        else:
            base_dir = project_dir
        path = base_dir / path
    return path.resolve()


def _branch_name(ref_name: str) -> str | None:
    normalized = ref_name.strip()
    prefix = "refs/heads/"
    if not normalized.startswith(prefix):
        return None
    branch = normalized.removeprefix(prefix).strip()
    return branch or None


@contextmanager
def _locked_queue(path: Path):
    lock_path = path.with_name(f"{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def main() -> int:
    if len(sys.argv) != 5:
        return 1

    project_dir = Path(sys.argv[1]).expanduser().resolve()
    repo_path = _resolve_repo(sys.argv[2])
    config_path = Path(sys.argv[3]).expanduser().resolve()
    updates_path = Path(sys.argv[4]).expanduser().resolve()
    config = _load_config(config_path)
    runtime_dir = _resolve_config_path(config.get("runtime_dir", "runtime"), config_path=config_path, project_dir=project_dir)
    queue_path = config.get("event_queue_store_path")
    if queue_path:
        queue_store_path = _resolve_config_path(queue_path, config_path=config_path, project_dir=project_dir)
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
        and git_push_trigger_matches_repo(trigger, str(repo_path))
    ]
    if not git_push_triggers:
        return 0

    queue_store_path.parent.mkdir(parents=True, exist_ok=True)
    with _locked_queue(queue_store_path):
        hold_seconds = float(os.environ.get("AGENT_SANDBOX_TRIGGER_QUEUE_LOCK_HOLD_SECONDS", "0") or "0")
        if hold_seconds > 0:
            time.sleep(hold_seconds)
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
            payloads = build_git_push_payloads(
                repo_path=str(repo_path),
                old_rev=old_rev,
                new_rev=new_rev,
                ref_name=ref_name,
            )
            if not payloads:
                continue

            now = datetime.now(timezone.utc).isoformat()
            for payload in payloads:
                event_hash = str(payload.get("event_hash") or new_rev)
                for trigger in git_push_triggers:
                    trigger_id = str(trigger["id"]).strip()
                    event_id = f"{trigger_id}:{payload['branch_name']}:{event_hash}"
                    if event_id in queue_payload:
                        continue
                    queue_payload[event_id] = {
                        "event_id": event_id,
                        "trigger_id": trigger_id,
                        "trigger_type": "trigger:git:push",
                        "occurred_at": now,
                        "payload": payload,
                        "delivery_state": "pending",
                        "inflight_at": None,
                        "delivered_at": None,
                        "idempotency_key": None,
                    }

        temp_fd, temp_path = tempfile.mkstemp(
            prefix=f"{queue_store_path.name}.",
            suffix=".tmp",
            dir=str(queue_store_path.parent),
        )
        try:
            with os.fdopen(temp_fd, "w", encoding="utf-8") as handle:
                json.dump(queue_payload, handle, indent=2, sort_keys=True)
            os.replace(temp_path, queue_store_path)
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass

    return 0


raise SystemExit(main())
PY

rm -f "${UPDATES_FILE}"
