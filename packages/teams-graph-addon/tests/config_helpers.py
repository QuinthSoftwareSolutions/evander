from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.directory_config import (
    DIRECTORY_CONFIG_DOMAIN_FIELDS,
    DirectoryConfigStore,
    get_domain_base_config,
)
from evander.directories import DirectoryStore
from evander.host_config import HostConfig, save_host_config
from evander.daemons.effect_orchestrator import SqliteExecutionStore, SqliteGateExecutionStore
from evander.repositories.orchestration import SqliteWorkflowConfigSnapshotRepository
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.workflow.events import SqliteWorkflowEventJournal

_WORKFLOW_NODE_COLLECTIONS = ("triggers", "gates", "effects", "queries")
_WORKFLOW_NODE_ENVELOPE_FIELDS = frozenset({"id", "display_name", "type", "config", "outputs"})


def trigger_queue_payload(
    config: TriggerOrchestratorWorkerGlobalConfig,
) -> dict[str, dict[str, Any]]:
    return {
        event_id: record.model_dump(mode="json")
        for event_id, record in SqliteTriggerEventQueue(config.orchestration_store_path)
        .read_all()
        .items()
    }


def effect_execution_payload(
    config: TriggerOrchestratorWorkerGlobalConfig,
) -> dict[str, dict[str, Any]]:
    return {
        execution_id: record.model_dump(mode="json")
        for execution_id, record in SqliteExecutionStore(config.orchestration_store_path)
        .read_all()
        .items()
    }


def gate_execution_payload(
    config: TriggerOrchestratorWorkerGlobalConfig,
) -> dict[str, dict[str, Any]]:
    return {
        execution_id: record.model_dump(mode="json")
        for execution_id, record in SqliteGateExecutionStore(config.orchestration_store_path)
        .read_all()
        .items()
    }


def workflow_event_payload(
    config: TriggerOrchestratorWorkerGlobalConfig,
) -> list[dict[str, Any]]:
    events, _ = SqliteWorkflowEventJournal(config.orchestration_store_path).read_after(0)
    return [event.model_dump(mode="json") for event in events]


def workflow_config_snapshot_payload(
    config: TriggerOrchestratorWorkerGlobalConfig,
) -> dict[str, dict[str, Any]]:
    snapshots = SqliteWorkflowConfigSnapshotRepository(
        config.orchestration_store_path
    ).list_snapshots()
    return {str(snapshot["revision"]): snapshot for snapshot in snapshots}


def canonical_workflow_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert concise test fixtures to the persisted nested node envelope."""

    normalized = copy.deepcopy(payload)
    _canonicalize_workflow_container(normalized)
    if isinstance(normalized.get("nodes"), list):
        _canonicalize_workflow_ir(normalized)
    for key in ("workflow_ir", "ir"):
        nested = normalized.get(key)
        if isinstance(nested, dict):
            _canonicalize_workflow_ir(nested)
    return normalized


def canonical_workflow_node(payload: dict[str, Any]) -> dict[str, Any]:
    """Build one canonical workflow node envelope for programmatic tests."""

    normalized = copy.deepcopy(payload)
    _canonicalize_workflow_node(normalized)
    return normalized


def canonical_worker_config(
    payload: dict[str, Any],
) -> TriggerOrchestratorWorkerGlobalConfig:
    """Build a worker config from concise fixtures using the canonical envelope."""

    return TriggerOrchestratorWorkerGlobalConfig.model_validate(canonical_workflow_payload(payload))


def canonicalize_workflow_files(project_dir: Path) -> None:
    """Rewrite concise workflow test fixtures to the canonical persisted shape."""

    paths = [project_dir / "config" / "trigger-orchestrator" / "config.json"]
    packs_dir = project_dir / "config" / "workflow-packs"
    if packs_dir.exists():
        paths.extend(packs_dir.glob("*.json"))
    for path in paths:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        normalized = canonical_workflow_payload(payload)
        if normalized != payload:
            path.write_text(json.dumps(normalized), encoding="utf-8")


def _canonicalize_workflow_container(payload: dict[str, Any]) -> None:
    for collection_name in _WORKFLOW_NODE_COLLECTIONS:
        entries = payload.get(collection_name)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                _canonicalize_workflow_node(entry)


def _canonicalize_workflow_ir(payload: dict[str, Any]) -> None:
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return
    for entry in nodes:
        if not isinstance(entry, dict):
            continue
        node = entry.get("node") if isinstance(entry.get("node"), dict) else entry
        _canonicalize_workflow_node(node)


def _canonicalize_workflow_node(node: dict[str, Any]) -> None:
    config = dict(node.get("config") or {})
    for key in list(node):
        if key not in _WORKFLOW_NODE_ENVELOPE_FIELDS:
            config[key] = node.pop(key)
    node["config"] = config


def write_runtime_config(base_dir: Path, **overrides: Any) -> Path:
    """Materialize canonical host and root-directory config for a test runtime."""

    runtime_dir = Path(overrides.pop("runtime_dir", base_dir / "runtime"))
    overridden_fields = set(overrides)
    config = GatewayConfig(runtime_dir=runtime_dir, **overrides)
    save_host_config(HostConfig.from_gateway_config(config))

    directory_store = DirectoryStore(config.directories_store_path)
    directory_store.ensure_root()
    config_store = DirectoryConfigStore(directory_store, runtime_dir=config.runtime_dir)
    config_store.ensure_root()
    for domain, domain_fields in DIRECTORY_CONFIG_DOMAIN_FIELDS.items():
        if overridden_fields.isdisjoint(domain_fields):
            continue
        config_store.write(
            "root",
            domain=domain,
            payload=get_domain_base_config(config, domain),
        )
    return runtime_dir
