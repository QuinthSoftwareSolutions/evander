from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.events.internal import InternalEventBus, internal_event_store_path
from evander.workflow.events import SqliteWorkflowEventJournal


def build_internal_event_bus(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> InternalEventBus:
    return InternalEventBus(
        worker_config.internal_event_store_path,
        journal=SqliteWorkflowEventJournal(
            worker_config.orchestration_store_path,
            retention_days=int(worker_config.orchestration_retention_days),
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


def internal_event_bus_metadata(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> dict[str, str | int]:
    return {
        "internal_event_store_path": str(worker_config.internal_event_store_path),
        "orchestration_store_path": str(worker_config.orchestration_store_path),
        "orchestration_retention_days": int(worker_config.orchestration_retention_days),
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
    return InternalEventBus(
        event_path,
        journal=SqliteWorkflowEventJournal(
            Path(values.get("orchestration_store_path") or Path(runtime_dir) / "orchestration.db"),
            retention_days=retention_days,
        ),
    )


__all__ = [
    "build_internal_event_bus",
    "build_internal_event_bus_for_runtime",
    "build_internal_event_bus_from_metadata",
    "internal_event_bus_metadata",
]
