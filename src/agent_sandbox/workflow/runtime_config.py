from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.directories import DirectoryStore
from agent_sandbox.messaging.targets import (
    communication_targets_db_path,
    known_communication_target_ids,
    validate_communication_target_refs,
)
from agent_sandbox.workflow.compiler import (
    compile_trigger_orchestrator_config,
    compile_trigger_orchestrator_config_with_diagnostics,
)
from agent_sandbox.workflow.migrations import migrate_agent_prompt_lifecycle_ports
from agent_sandbox.workflow.transforms import resolve_workflow_transforms
from agent_sandbox.workflow.packs import _pack_path, compose_workflow_ir

logger = logging.getLogger(__name__)

_SYSTEM_MANAGED_WORKFLOW_NODE_IDS = frozenset(
    {
        "system.chat_assistant",
        "system.chat_assistant.ensure_session",
        "system.chat_assistant.enqueue_prompt",
    }
)
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
            return None

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
            return _compile_worker_global_config(
                TriggerOrchestratorWorkerGlobalConfig.model_validate(self._base_payload)
            )
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"worker config file {self._path} must contain a JSON object")
        project_dir = self._path.parents[2]
        raw = _compose_configured_workflow_ir(project_dir=project_dir, payload=raw)
        runtime_dir = Path(self._base_payload.get("runtime_dir") or "runtime")
        _validate_communication_targets(
            base_config=GatewayConfig(runtime_dir=runtime_dir),
            payload=raw,
        )
        return _compile_worker_global_config(
            TriggerOrchestratorWorkerGlobalConfig.model_validate({**self._base_payload, **raw})
        )

    def _workflow_pack_paths(self) -> list[Path]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(raw, dict):
            return []
        configured_packs = raw.get("workflow_packs")
        if not isinstance(configured_packs, list):
            return []

        paths: list[Path] = []
        for pack_reference in configured_packs:
            try:
                paths.append(_pack_path(self._project_dir, str(pack_reference)))
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
) -> tuple[TriggerOrchestratorWorkerGlobalConfig, list[str]]:
    transforms = resolve_workflow_transforms(worker_config.workflow_transforms)
    compiled, diagnostics = compile_trigger_orchestrator_config_with_diagnostics(
        worker_config, transforms=transforms
    )
    return compiled, [item.message for item in diagnostics]


def _compose_configured_workflow_ir(
    *, project_dir: Path, payload: dict[str, Any]
) -> dict[str, Any]:
    payload = migrate_agent_prompt_lifecycle_ports(payload)
    configured_packs = payload.get("workflow_packs")
    inline_ir = payload.get("workflow_ir")
    has_configured_packs = isinstance(configured_packs, list) and bool(configured_packs)
    has_inline_ir = isinstance(inline_ir, dict)
    if not has_configured_packs and not has_inline_ir:
        return payload

    composition = compose_workflow_ir(
        project_dir=project_dir,
        workflow_packs=configured_packs if has_configured_packs else [],
        inline_ir=inline_ir if has_inline_ir else None,
    )
    merged = dict(payload)
    for collection_name in _WORKFLOW_NODE_COLLECTIONS:
        merged[collection_name] = _merge_system_managed_workflow_nodes(
            composed_entries=getattr(composition, collection_name),
            configured_entries=payload.get(collection_name),
        )
    merged["edges"] = _merge_system_managed_workflow_edges(
        composed_edges=composition.edges,
        configured_edges=payload.get("edges"),
        node_ids=_collect_workflow_node_ids(merged),
    )
    return migrate_agent_prompt_lifecycle_ports(merged)


def _merge_system_managed_workflow_nodes(
    *, composed_entries: list[dict[str, Any]], configured_entries: object
) -> list[dict[str, Any]]:
    system_entries: list[dict[str, Any]] = []
    if isinstance(configured_entries, list):
        system_entries = [
            dict(entry)
            for entry in configured_entries
            if isinstance(entry, dict)
            and _is_system_managed_workflow_node_id(entry.get("id"))
        ]
    if not system_entries:
        return composed_entries

    system_ids = {_workflow_node_id(entry) for entry in system_entries}
    merged = [entry for entry in composed_entries if _workflow_node_id(entry) not in system_ids]
    merged.extend(system_entries)
    return merged


def _merge_system_managed_workflow_edges(
    *, composed_edges: list[dict[str, Any]], configured_edges: object, node_ids: set[str]
) -> list[dict[str, Any]]:
    system_edges: list[dict[str, Any]] = []
    if isinstance(configured_edges, list):
        system_edges = [
            dict(edge)
            for edge in configured_edges
            if isinstance(edge, dict)
            and (
                _is_system_managed_workflow_node_id(edge.get("source_id"))
                or _is_system_managed_workflow_node_id(edge.get("target_id"))
            )
            and _workflow_edge_source_id(edge) in node_ids
            and _workflow_edge_target_id(edge) in node_ids
        ]
    if not system_edges:
        return composed_edges

    merged = [
        edge
        for edge in composed_edges
        if not (
            _is_system_managed_workflow_node_id(edge.get("source_id"))
            or _is_system_managed_workflow_node_id(edge.get("target_id"))
        )
    ]
    merged.extend(system_edges)
    return merged


def _collect_workflow_node_ids(payload: dict[str, Any]) -> set[str]:
    node_ids: set[str] = set()
    for collection_name in _WORKFLOW_NODE_COLLECTIONS:
        entries = payload.get(collection_name)
        if not isinstance(entries, list):
            continue
        node_ids.update(
            node_id
            for entry in entries
            if isinstance(entry, dict)
            for node_id in [_workflow_node_id(entry)]
            if node_id
        )
    return node_ids


def _workflow_node_id(entry: dict[str, Any]) -> str:
    return str(entry.get("id") or "").strip()


def _workflow_edge_source_id(entry: dict[str, Any]) -> str:
    return str(entry.get("source_id") or "").strip()


def _workflow_edge_target_id(entry: dict[str, Any]) -> str:
    return str(entry.get("target_id") or "").strip()


def _is_system_managed_workflow_node_id(node_id: object) -> bool:
    return str(node_id or "").strip() in _SYSTEM_MANAGED_WORKFLOW_NODE_IDS


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
    validate_communication_target_refs(
        payload=payload, known_target_ids=known_target_ids
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
