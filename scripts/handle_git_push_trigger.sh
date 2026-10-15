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
  EVANDER_TRIGGER_ORCHESTRATOR_CONFIG (optional)
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
CONFIG_PATH="${EVANDER_TRIGGER_ORCHESTRATOR_CONFIG:-${ROOT_DIR}/config/trigger-orchestrator/config.json}"

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
import sys
from datetime import datetime, timezone
from pathlib import Path

from evander.events.models import TriggerEvent
from evander.orchestration.host_polling import HostWakeNotifier, wake_socket_path_for_lock
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.triggers.providers.git_push import (
    _load_managed_repo_metadata,
    build_git_push_payloads,
    git_push_trigger_matches_repo,
)
from evander.workflow.graph_metadata import resolve_workflow_graph_metadata
from evander.workflow.node_state import workflow_node_is_enabled
from evander.workflow.runtime_config import _compose_configured_workflow_ir

def _resolve_repo(path_text: str) -> Path:
    return Path(path_text).expanduser().resolve()


def _load_config(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        return payload
    return {}


def _resolve_git_push_triggers(*, config: dict, project_dir: Path) -> list[dict]:
    composed = _compose_configured_workflow_ir(
        project_dir=project_dir,
        payload=dict(config),
    )
    node_collections = (
        composed.get("triggers"),
        composed.get("gates"),
        composed.get("effects"),
        composed.get("queries"),
    )
    nodes_by_id = {
        str(node.get("id") or "").strip(): node
        for collection in node_collections
        if isinstance(collection, list)
        for node in collection
        if isinstance(node, dict) and str(node.get("id") or "").strip()
    }
    graph_metadata = resolve_workflow_graph_metadata(
        nodes_by_id=nodes_by_id,
        edges=composed.get("edges") if isinstance(composed.get("edges"), list) else [],
        workflow_graphs=(
            composed.get("workflow_graphs")
            if isinstance(composed.get("workflow_graphs"), list)
            else []
        ),
    )

    normalized_triggers: list[dict] = []
    triggers = composed.get("triggers")
    for trigger in triggers if isinstance(triggers, list) else []:
        if not isinstance(trigger, dict):
            continue
        normalized = dict(trigger)
        trigger_config = normalized.pop("config", None)
        if isinstance(trigger_config, dict):
            normalized.update(trigger_config)
        explicit_directory = str(normalized.get("directory_path") or "").strip()
        graph = graph_metadata.get(str(normalized.get("id") or "").strip())
        normalized["_evander_effective_directory_path"] = (
            explicit_directory
            or (graph.directory_path if graph is not None else "")
            or "root"
        )
        normalized_triggers.append(normalized)
    return normalized_triggers


def _trigger_can_access_repository(*, trigger: dict, owner_directory: str) -> bool:
    owner = str(owner_directory or "").strip()
    workflow_directory = str(
        trigger.get("_evander_effective_directory_path") or "root"
    ).strip() or "root"
    return bool(
        owner
        and (
            workflow_directory == owner
            or workflow_directory.startswith(f"{owner}/")
        )
    )


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


def _resolve_orchestration_store_path(
    config: dict, *, runtime_dir: Path, config_path: Path, project_dir: Path
) -> Path:
    store_path = config.get("orchestration_store_path")
    if store_path:
        return _resolve_config_path(store_path, config_path=config_path, project_dir=project_dir)
    return runtime_dir / "orchestration.db"


def _resolve_effect_lock_path(
    config: dict, *, runtime_dir: Path, config_path: Path, project_dir: Path
) -> Path:
    lock_path = config.get("effect_lock_path")
    if lock_path:
        return _resolve_config_path(lock_path, config_path=config_path, project_dir=project_dir)
    return runtime_dir / "effect-orchestrator.lock"


def _branch_name(ref_name: str) -> str | None:
    normalized = ref_name.strip()
    prefix = "refs/heads/"
    if not normalized.startswith(prefix):
        return None
    branch = normalized.removeprefix(prefix).strip()
    return branch or None


def _build_trigger_events(
    *,
    updates_path: Path,
    repo_path: Path,
    git_push_triggers: list[dict],
) -> list[TriggerEvent]:
    events: list[TriggerEvent] = []
    repo_metadata = _load_managed_repo_metadata(repo_path)
    owner_directory = str(repo_metadata.get("directory_path") or "").strip()
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

        occurred_at = datetime.now(timezone.utc)
        for payload in payloads:
            event_hash = str(payload.get("event_hash") or new_rev)
            for trigger in git_push_triggers:
                if not git_push_trigger_matches_repo(
                    trigger,
                    str(payload.get("managed_repo_id") or ""),
                ):
                    continue
                if not _trigger_can_access_repository(
                    trigger=trigger,
                    owner_directory=owner_directory,
                ):
                    continue
                trigger_id = str(trigger["id"]).strip()
                events.append(
                    TriggerEvent(
                        event_id=f"{trigger_id}:{payload['branch_name']}:{event_hash}",
                        trigger_id=trigger_id,
                        trigger_type="trigger:git:push",
                        occurred_at=occurred_at,
                        payload=payload,
                    )
                )
    # Atomic branch+notes transactions already have the summary available when
    # the branch event is consumed. Keep notes-only publications distinct.
    branch_updates = {
        (event.trigger_id, event.payload.get("branch_name"), event.payload.get("commit_hash"))
        for event in events if not event.payload.get("note_target_commit_hash")
    }
    return [event for event in events if not (
        event.payload.get("note_target_commit_hash") and
        (event.trigger_id, event.payload.get("branch_name"), event.payload.get("commit_hash"))
        in branch_updates
    )]


def _publish_sqlite_events(database_path: Path, events: list[TriggerEvent]) -> int:
    queue = SqliteTriggerEventQueue(database_path)
    published = 0
    for event in events:
        already_queued = queue.contains(event.event_id)
        record = queue.publish(event)
        if not already_queued and record.event_id == event.event_id:
            published += 1
    return published


def _notify_effect_worker(effect_lock_path: Path) -> None:
    HostWakeNotifier(wake_socket_path_for_lock(effect_lock_path)).notify()


def main() -> int:
    if len(sys.argv) != 5:
        return 1

    project_dir = Path(sys.argv[1]).expanduser().resolve()
    repo_path = _resolve_repo(sys.argv[2])
    config_path = Path(sys.argv[3]).expanduser().resolve()
    updates_path = Path(sys.argv[4]).expanduser().resolve()
    config = _load_config(config_path)
    runtime_dir = _resolve_config_path(config.get("runtime_dir", "runtime"), config_path=config_path, project_dir=project_dir)
    orchestration_store_path = _resolve_orchestration_store_path(
        config, runtime_dir=runtime_dir, config_path=config_path, project_dir=project_dir
    )
    effect_lock_path = _resolve_effect_lock_path(
        config, runtime_dir=runtime_dir, config_path=config_path, project_dir=project_dir
    )

    triggers = _resolve_git_push_triggers(config=config, project_dir=project_dir)

    git_push_triggers = [
        trigger
        for trigger in triggers
        if isinstance(trigger, dict)
        and workflow_node_is_enabled(trigger)
        and str(trigger.get("type", "")).strip() == "trigger:git:push"
        and str(trigger.get("id", "")).strip()
    ]
    if not git_push_triggers:
        return 0

    events = _build_trigger_events(
        updates_path=updates_path,
        repo_path=repo_path,
        git_push_triggers=git_push_triggers,
    )
    published = _publish_sqlite_events(orchestration_store_path, events)
    if published > 0:
        _notify_effect_worker(effect_lock_path)

    return 0


raise SystemExit(main())
PY

rm -f "${UPDATES_FILE}"
