from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.directories import DirectoryStore
from evander.messaging.targets import (
    communication_targets_db_path,
    known_communication_target_ids,
    validate_communication_new_message_trigger_targets,
    validate_communication_target_refs,
)
from evander.triggers.providers.schemas import CommunicationNewMessageTriggerDefinition
from evander.workflow.compiler import (
    compile_trigger_orchestrator_config,
    compile_trigger_orchestrator_config_with_diagnostics,
)
from evander.workflow.migrations import migrate_agent_prompt_lifecycle_ports
from evander.workflow.transforms import resolve_workflow_transforms
from evander.workflow.validation import WorkflowValidationDiagnostic
from evander.workflow.system_nodes import is_system_managed_workflow_node_id
from evander.workflow.packs import (
    BUILTIN_HIDDEN_WORKFLOW_PACKS,
    _pack_path,
    compose_workflow_ir,
    WorkflowPackGraph,
)

logger = logging.getLogger(__name__)

_WORKFLOW_NODE_COLLECTIONS = ("triggers", "gates", "effects", "queries")


def _worker_global_config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "trigger-orchestrator" / "config.json"


def _base_worker_global_payload(base_config: GatewayConfig | None) -> dict[str, object]:
    return TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": (
                base_config.runtime_dir if base_config is not None else Path("runtime")
            ),
        }
    ).model_dump(mode="json")


def load_trigger_orchestrator_worker_global_config(
    project_dir: Path,
    *,
    base_config: GatewayConfig | None = None,
) -> TriggerOrchestratorWorkerGlobalConfig:
    base_payload = _base_worker_global_payload(base_config)
    path = _worker_global_config_path(project_dir)
    if not path.exists():
        base_payload = _compose_configured_workflow_ir(
            project_dir=project_dir, payload=dict(base_payload)
        )
        return _compile_worker_global_config(
            TriggerOrchestratorWorkerGlobalConfig.model_validate(base_payload)
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    raw = _compose_configured_workflow_ir(project_dir=project_dir, payload=raw)
    _validate_communication_targets(base_config=base_config, payload=raw)
    return _compile_worker_global_config(
        TriggerOrchestratorWorkerGlobalConfig.model_validate({**base_payload, **raw})
    )


class TriggerOrchestratorWorkerGlobalConfigReloader:
    def __init__(
        self,
        *,
        project_dir: Path,
        initial_config: TriggerOrchestratorWorkerGlobalConfig,
        base_config: GatewayConfig | None = None,
    ) -> None:
        self._path = _worker_global_config_path(project_dir)
        self._project_dir = project_dir
        self._base_payload = _base_worker_global_payload(base_config)
        self._base_config = base_config or GatewayConfig(
            runtime_dir=Path(self._base_payload.get("runtime_dir") or "runtime")
        )
        self._current_config = initial_config
        self._last_fingerprint = self._compute_fingerprint()

    def get_latest(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        return self._reload(force=False)

    def reload_now(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        return self._reload(force=True)

    def _reload(self, *, force: bool) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        fingerprint = self._compute_fingerprint()
        if not force and fingerprint == self._last_fingerprint:
            return self._current_config, False

        try:
            next_config = self._load_from_path()
        except Exception:
            self._last_fingerprint = fingerprint
            logger.error(
                "Failed to reload trigger orchestrator worker config path=%s; keeping last-known-good",
                self._path,
                exc_info=True,
            )
            return self._current_config, False

        changed = next_config != self._current_config
        self._current_config = next_config
        self._last_fingerprint = fingerprint
        return self._current_config, changed

    def _compute_fingerprint(self) -> tuple[tuple[int, int, int, str], ...] | None:
        base_fingerprint = _file_fingerprint(self._path)
        if base_fingerprint is None:
            base_fingerprint = (0, 0, 0, f"missing:{self._path}")

        fingerprints = [base_fingerprint]
        runtime_dir = Path(self._base_payload.get("runtime_dir") or "runtime")
        communication_targets_path = communication_targets_db_path(runtime_dir)
        fingerprints.extend(_sqlite_fingerprints(communication_targets_path))

        for pack_path in self._workflow_pack_paths():
            pack_fingerprint = _file_fingerprint(pack_path)
            if pack_fingerprint is None:
                fingerprints.append((0, 0, 0, f"missing:{pack_path}"))
            else:
                fingerprints.append(pack_fingerprint)
        return tuple(fingerprints)

    def _load_from_path(self) -> TriggerOrchestratorWorkerGlobalConfig:
        if not self._path.exists():
            payload = _compose_configured_workflow_ir(
                project_dir=self._project_dir,
                payload=dict(self._base_payload),
            )
            return _compile_worker_global_config(
                TriggerOrchestratorWorkerGlobalConfig.model_validate(payload)
            )
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"worker config file {self._path} must contain a JSON object")
        raw = _compose_configured_workflow_ir(project_dir=self._project_dir, payload=raw)
        _validate_communication_targets(
            base_config=self._base_config,
            payload=raw,
        )
        return _compile_worker_global_config(
            TriggerOrchestratorWorkerGlobalConfig.model_validate({**self._base_payload, **raw})
        )

    def _workflow_pack_paths(self) -> list[Path]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        configured_packs = raw.get("workflow_packs")
        paths: list[Path] = []
        if isinstance(configured_packs, list):
            for pack_reference in configured_packs:
                try:
                    paths.append(_pack_path(self._project_dir, str(pack_reference)))
                except Exception:
                    continue
        for pack_reference in BUILTIN_HIDDEN_WORKFLOW_PACKS:
            try:
                paths.append(_pack_path(self._project_dir, pack_reference))
            except Exception:
                continue
        return paths


def _compile_worker_global_config(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> TriggerOrchestratorWorkerGlobalConfig:
    transforms = resolve_workflow_transforms(worker_config.workflow_transforms)
    return compile_trigger_orchestrator_config(worker_config, transforms=transforms)


def compile_worker_global_config_with_diagnostics(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    *,
    project_dir: Path | None = None,
) -> tuple[TriggerOrchestratorWorkerGlobalConfig, list[WorkflowValidationDiagnostic]]:
    transforms = resolve_workflow_transforms(worker_config.workflow_transforms)
    compiled, diagnostics = compile_trigger_orchestrator_config_with_diagnostics(
        worker_config,
        transforms=transforms,
        addon_base_dir=project_dir,
    )
    return compiled, diagnostics


def _compose_configured_workflow_ir(
    *, project_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    payload = migrate_agent_prompt_lifecycle_ports(payload)
    configured_packs = payload.get("workflow_packs")
    inline_graphs = payload.get("workflow_graphs")
    inline_ir = payload.get("workflow_ir")
    has_configured_packs = isinstance(configured_packs, list) and bool(configured_packs)
    has_inline_ir = isinstance(inline_ir, dict)
    if not has_configured_packs and not has_inline_ir:
        return _append_builtin_hidden_workflows(project_dir=project_dir, payload=payload)

    composition = compose_workflow_ir(
        project_dir=project_dir,
        workflow_packs=configured_packs if has_configured_packs else [],
        inline_ir=inline_ir if has_inline_ir else None,
    )
    merged_graphs = _merge_workflow_graphs(inline_graphs, composition.graphs)
    if merged_graphs:
        payload = dict(payload)
        payload["workflow_graphs"] = merged_graphs
    return _append_builtin_hidden_workflows(
        project_dir=project_dir,
        payload=payload,
        editable_collections={
            collection_name: getattr(composition, collection_name)
            for collection_name in _WORKFLOW_NODE_COLLECTIONS
        },
        editable_edges=composition.edges,
    )


def _append_builtin_hidden_workflows(
    *,
    project_dir: Path,
    payload: dict[str, Any],
    editable_collections: dict[str, list[dict[str, Any]]] | None = None,
    editable_edges: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = migrate_agent_prompt_lifecycle_ports(payload)
    composition = compose_workflow_ir(
        project_dir=project_dir,
        workflow_packs=[],
        built_in_workflow_packs=BUILTIN_HIDDEN_WORKFLOW_PACKS,
    )
    merged = dict(payload)
    hidden_node_ids: set[str] = set()
    for collection_name in _WORKFLOW_NODE_COLLECTIONS:
        configured_entries = (
            editable_collections.get(collection_name)
            if editable_collections is not None
            else merged.get(collection_name)
        )
        editable_entries = (
            [
                dict(entry)
                for entry in configured_entries
                if isinstance(entry, dict)
                and not _is_system_managed_workflow_node_id(entry.get("id"))
            ]
            if isinstance(configured_entries, list)
            else []
        )
        hidden_entries = getattr(composition, collection_name)
        hidden_node_ids.update(_workflow_node_id(entry) for entry in hidden_entries)
        editable_entries.extend(hidden_entries)
        merged[collection_name] = editable_entries

    configured_edges = editable_edges if editable_edges is not None else merged.get("edges")
    editable_edges = (
        [
            dict(edge)
            for edge in configured_edges
            if isinstance(edge, dict)
            and _workflow_edge_source_id(edge) not in hidden_node_ids
            and _workflow_edge_target_id(edge) not in hidden_node_ids
        ]
        if isinstance(configured_edges, list)
        else []
    )
    editable_edges.extend(composition.edges)
    merged["edges"] = editable_edges
    return migrate_agent_prompt_lifecycle_ports(merged)


def _merge_workflow_graphs(
    existing_graphs: object, additional_graphs: list[WorkflowPackGraph]
) -> list[dict[str, Any]]:
    merged_graphs: list[dict[str, Any]] = []
    if isinstance(existing_graphs, list):
        for graph in existing_graphs:
            validated_graph = WorkflowPackGraph.model_validate(graph)
            merged_graphs.append(validated_graph.model_dump(mode="json"))
    merged_graphs.extend(graph.model_dump(mode="json") for graph in additional_graphs)
    return merged_graphs


def _workflow_node_id(entry: dict[str, Any]) -> str:
    return str(entry.get("id") or "").strip()


def _workflow_edge_source_id(entry: dict[str, Any]) -> str:
    return str(entry.get("source_id") or "").strip()


def _workflow_edge_target_id(entry: dict[str, Any]) -> str:
    return str(entry.get("target_id") or "").strip()


def _is_system_managed_workflow_node_id(node_id: object) -> bool:
    return is_system_managed_workflow_node_id(node_id)


def _validate_communication_targets(
    *, base_config: GatewayConfig | None, payload: dict[str, Any]
) -> None:
    known_target_ids: set[str] = set()
    if base_config is not None:
        directory_store = DirectoryStore(base_config.directories_store_path)
        known_target_ids = known_communication_target_ids(
            runtime_dir=base_config.runtime_dir,
            directory_store=directory_store,
        )
    validate_communication_target_refs(payload=payload, known_target_ids=known_target_ids)
    if base_config is None:
        return

    directory_store = DirectoryStore(base_config.directories_store_path)
    triggers = payload.get("triggers")
    if not isinstance(triggers, list):
        return
    for entry in triggers:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("type") or "").strip() != "trigger:communication:new-message":
            continue
        trigger_payload = dict(entry)
        config = trigger_payload.pop("config", None)
        if isinstance(config, dict):
            for key, value in config.items():
                trigger_payload.setdefault(key, value)
        trigger = CommunicationNewMessageTriggerDefinition.model_validate(trigger_payload)
        validate_communication_new_message_trigger_targets(
            trigger,
            runtime_dir=base_config.runtime_dir,
            directory_store=directory_store,
        )


class FixedTriggerOrchestratorWorkerGlobalConfigReloader:
    def __init__(self, config: TriggerOrchestratorWorkerGlobalConfig) -> None:
        self._config = config

    def get_latest(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        return self._config, False

    def reload_now(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        return self._config, False


def _file_fingerprint(path: Path) -> tuple[int, int, int, str] | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    try:
        payload = path.read_bytes()
    except OSError:
        return None
    digest = hashlib.sha256(payload).hexdigest()
    return stat.st_mtime_ns, stat.st_ino, stat.st_size, digest


def _sqlite_fingerprints(path: Path) -> list[tuple[int, int, int, str]]:
    fingerprints: list[tuple[int, int, int, str]] = []
    for candidate in (path, path.with_name(f"{path.name}-wal"), path.with_name(f"{path.name}-shm")):
        fingerprint = _file_fingerprint(candidate)
        if fingerprint is None:
            fingerprints.append((0, 0, 0, f"missing:{candidate}"))
        else:
            fingerprints.append(fingerprint)
    return fingerprints
