from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.events.internal import (
    InternalEvent,
    InternalEventBus,
    InternalEventSource,
    internal_event_store_path,
)
from evander.orchestration.host_polling import notify_wake_socket_for_lock
from evander.workflow.events import SqliteWorkflowEventJournal


def build_internal_event_bus(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> InternalEventBus:
    return InternalEventBus(
        worker_config.internal_event_store_path,
        journal=SqliteWorkflowEventJournal(
            worker_config.orchestration_store_path,
            retention_days=int(worker_config.orchestration_retention_days),
            compact_history_retention_days=int(
                worker_config.orchestration_compact_history_retention_days
            ),
        ),
    )


def build_internal_event_bus_for_runtime(
    runtime_dir: Path | str,
    *,
    project_dir: Path | str | None = None,
    base_config: GatewayConfig | None = None,
) -> InternalEventBus:
    from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config

    resolved_runtime_dir = Path(runtime_dir)
    resolved_config = (
        base_config
        if isinstance(base_config, GatewayConfig)
        else GatewayConfig(runtime_dir=resolved_runtime_dir)
    )
    worker_config = load_trigger_orchestrator_worker_global_config(
        Path(project_dir) if project_dir is not None else resolved_runtime_dir.parent,
        base_config=resolved_config,
    )
    return build_internal_event_bus(worker_config)


def publish_internal_event_for_runtime(
    runtime_dir: Path | str,
    *,
    event_type: object,
    payload: Mapping[str, Any] | None = None,
    correlation_key: object | None = None,
    source: InternalEventSource | dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    project_dir: Path | str | None = None,
    base_config: GatewayConfig | None = None,
) -> InternalEvent:
    """Publish a durable event and promptly wake the trigger worker when it is running."""

    from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config

    resolved_runtime_dir = Path(runtime_dir)
    resolved_config = (
        base_config
        if isinstance(base_config, GatewayConfig)
        else GatewayConfig(runtime_dir=resolved_runtime_dir)
    )
    worker_config = load_trigger_orchestrator_worker_global_config(
        Path(project_dir) if project_dir is not None else resolved_runtime_dir.parent,
        base_config=resolved_config,
    )
    event = build_internal_event_bus(worker_config).publish(
        event_type=event_type,
        payload=dict(payload or {}),
        correlation_key=correlation_key,
        source=source,
        idempotency_key=idempotency_key,
    )
    notify_wake_socket_for_lock(worker_config.lock_path)
    return event


def internal_event_bus_metadata(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> dict[str, str | int]:
    return {
        "internal_event_store_path": str(worker_config.internal_event_store_path),
        "orchestration_store_path": str(worker_config.orchestration_store_path),
        "orchestration_retention_days": int(worker_config.orchestration_retention_days),
        "orchestration_compact_history_retention_days": int(
            worker_config.orchestration_compact_history_retention_days
        ),
    }


def build_internal_event_bus_from_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    runtime_dir: Path | str,
) -> InternalEventBus:
    values = metadata or {}
    event_path = Path(
        values.get("internal_event_store_path") or internal_event_store_path(runtime_dir)
    )
    retention_value = values.get("orchestration_retention_days")
    retention_days = int(retention_value) if retention_value is not None else None
    compact_retention_value = values.get("orchestration_compact_history_retention_days")
    compact_retention_days = (
        int(compact_retention_value) if compact_retention_value is not None else 365
    )
    return InternalEventBus(
        event_path,
        journal=SqliteWorkflowEventJournal(
            Path(values.get("orchestration_store_path") or Path(runtime_dir) / "orchestration.db"),
            retention_days=retention_days,
            compact_history_retention_days=compact_retention_days,
        ),
    )


__all__ = [
    "build_internal_event_bus",
    "build_internal_event_bus_for_runtime",
    "build_internal_event_bus_from_metadata",
    "internal_event_bus_metadata",
    "publish_internal_event_for_runtime",
]
