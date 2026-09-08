from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.events.models import TriggerEvent
from evander.triggers.queue import SqliteTriggerEventQueue, TriggerQueueRecord
from evander.workflow.node_state import workflow_node_is_enabled


def publish_configured_agent_completions(
    *, queue: SqliteTriggerEventQueue, record: TriggerQueueRecord, graph: Any
) -> None:
    """Deliver an owned completion to existing trigger nodes without polling VMs.

    Forward only the canonical source so forwarded events cannot recursively fan
    out. Stable per-trigger/dispatch IDs make partial publication and redelivery
    safe across worker restarts.
    """
    if record.trigger_id != "__await-agent-completion__":
        return
    if record.trigger_type != "trigger:agent:run:completed":
        return
    scope = record.payload.get("scope")
    scope = scope if isinstance(scope, Mapping) else {}
    directories = _texts(record.payload.get("directory_paths"))
    directories.update(_texts(scope.get("directory_path")))
    directories.update(_texts(record.directory_path))
    sessions = _texts(record.payload.get("session_ids"))
    sessions.update(_texts(record.payload.get("session_id")))
    sessions.update(_texts(scope.get("session_id")))
    vm_name = _text(record.payload.get("vm_name") or scope.get("vm_name"))
    vm_user = _text(scope.get("vm_user")) or "agent"
    completion_id = str(record.payload.get("dispatch_id") or record.event_id)
    # A trigger starts an independent graph; do not carry routing directives or
    # upstream node outputs from the workflow that originally prompted the agent.
    payload = {key: value for key, value in record.payload.items() if not key.startswith("__")}
    for node in graph.nodes_by_id.values():
        if (
            node.type != record.trigger_type
            or node.id == record.trigger_id
            or not workflow_node_is_enabled(node)
        ):
            continue
        metadata = graph.node_graph_metadata_by_id.get(node.id)
        directory = _text(getattr(node, "directory_path", None)) or (
            metadata.directory_path if metadata is not None else "root"
        )
        if directory != "root" and directory not in directories:
            continue
        target_vm = _text(getattr(node, "vm_name", None))
        target_session = _text(getattr(node, "session_id", None))
        if target_vm and target_vm != vm_name:
            continue
        if target_session and target_session not in sessions:
            continue
        if (_text(getattr(node, "vm_user", None)) or "agent") != vm_user:
            continue
        event_id = f"{node.id}:agent-completion:{completion_id}"
        queue.publish(
            TriggerEvent(
                event_id=event_id,
                trigger_id=node.id,
                trigger_type=node.type,
                occurred_at=record.occurred_at,
                trace_id=record.trace_id,
                payload=payload,
            ),
            idempotency_key=event_id,
            config_revision=record.config_revision,
            directory_path=directory,
        )


def _texts(value: Any) -> set[str]:
    values = value if isinstance(value, list) else [value]
    return {item.strip() for item in values if isinstance(item, str) and item.strip()}


def _text(value: Any) -> str:
    return str(value or "").strip()
