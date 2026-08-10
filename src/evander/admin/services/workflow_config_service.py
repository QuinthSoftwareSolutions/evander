from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ContextManager
from uuid import uuid4

from pydantic import ValidationError

from evander.addons import LoadedAddonCatalog, addon_mcp_tool_catalog_payloads, load_addons
from evander.admin.common import (
    _coerce_optional_datetime,
    _coerce_optional_string,
    _read_json_object,
    _resolve_path_setting,
    _safe_validation_error,
    _utc_now,
)
from evander.admin.payloads import (
    AdminRevisionConflict,
    EffectConfigMutationPayload,
    TriggerConfigMutationPayload,
    WorkerGlobalConfigPayload,
    WorkerGlobalConfigWritePayload,
    WorkflowEditorDocumentPayload,
    WorkflowEditorGraphMetadata,
    WorkflowEditorLayoutDocument,
    WorkflowEditorPackMutationPayload,
    WorkflowEditorSavePayload,
    WorkflowEditorValidationPayload,
    WorkflowEditorWorkflowPackDescriptor,
    WorkflowPackHistoryDetailPayload,
    WorkflowPackHistoryListPayload,
    WorkflowNodeFailuresDismissPayload,
    WorkflowRunClearPayload,
    WorkflowRunResumePayload,
)
from evander.admin.services.workflow_pack_history_service import (
    WorkflowPackChangeActor,
    WorkflowPackHistoryFileState,
    WorkflowPackHistoryService,
)
from evander.admin.worker_config import (
    _WORKER_GLOBAL_CONFIG_MODELS,
    _WORKER_GLOBAL_CONFIG_PATHS,
)
from evander.admin.workflow_catalog import (
    _EFFECT_DEFINITION_ADAPTER,
    _GATE_DEFINITION_ADAPTER,
    _QUERY_DEFINITION_ADAPTER,
    _TRIGGER_DEFINITION_ADAPTER,
    _WORKFLOW_EDITOR_NODE_CATALOG,
    _WORKFLOW_EDITOR_NODE_DESCRIPTIONS,
    WorkflowEditorFieldOption,
    WorkflowEditorNodeCatalogEntry,
    _communication_target_capabilities,
    _communication_target_option,
    _communication_target_shape,
    _field_option,
    _is_system_managed_workflow_node_id,
    _looks_like_declared_output_schema,
    _strip_default_edge_fields,
    _tool_field_options,
    _workflow_agent_create_diagnostics,
    _workflow_binding_diagnostics_for_graphs,
    _workflow_config_for_legacy_mutation_response,
)
from evander.config import TriggerOrchestratorWorkerGlobalConfig
from evander.events.internal import (
    EXTERNAL_WORKFLOW_EVENT_PREFIX,
    InternalEventBus,
    InternalEventSource,
    validate_internal_event_type,
)
from evander.events.models import TriggerEvent
from evander.host_directories.models import HostDirectoryDirectoryConfig
from evander.messaging.targets import COMMUNICATION_TARGETS_DOMAIN
from evander.orchestration.host_polling import notify_wake_socket_for_lock
from evander.repositories.orchestration import (
    JsonWorkflowConfigSnapshotRepository,
    SqliteWorkflowConfigSnapshotRepository,
)
from evander.repositories.skills_library import validate_skill_name
from evander.repositories.triggers import (
    JsonTriggerEventRepository,
    TriggerConfigDocument,
)
from evander.storage import JsonStore
from evander.triggers.providers._local_tickets import (
    seed_new_ticket_created_trigger_cursors,
)
from evander.triggers.providers._new_message_baselines import (
    seed_new_message_trigger_cursors,
)
from evander.triggers.queue import (
    SqliteTriggerEventQueue,
    TriggerEventQueue,
    TriggerQueueRecord,
)
from evander.workflow.catalog import (
    WorkflowEditorCatalogPayload,
    WorkflowInternalEventCatalogEntry,
)
from evander.workflow.events import (
    SqliteWorkflowEventJournal,
    WorkflowEventJournal,
    workflow_event_journal_path,
)
from evander.workflow.migrations import migrate_agent_prompt_lifecycle_ports
from evander.workflow.packs import WorkflowPack, compose_workflow_ir, load_workflow_pack
from evander.workflow.runtime_config import (
    _append_builtin_hidden_workflows,
    _compose_configured_workflow_ir,
    compile_worker_global_config_with_diagnostics,
)
from evander.workflow.validation import derive_workflow_output_schemas


def _internal_event_field_options(
    entries: list[WorkflowInternalEventCatalogEntry],
) -> list[WorkflowEditorFieldOption]:
    options: list[WorkflowEditorFieldOption] = []
    for entry in entries:
        metadata = dict(entry.metadata or {})
        if entry.source:
            metadata.setdefault("source", entry.source)
        options.append(
            _field_option(
                entry.event_type,
                entry.label,
                description=entry.description,
                metadata=metadata,
            )
        )
    return options


@dataclass(slots=True)
class WorkflowConfigAdminDependencies:
    project_dir: Path
    config: Any
    skill_library: Any
    directory_admin: Any
    admin_config_mutation_lock: Callable[[], ContextManager[None]]
    normalize_directory_reference: Callable[[str | None], str]
    workflow_reports_admin: Any | None = None
    addon_catalog: LoadedAddonCatalog | None = None


@dataclass(slots=True)
class WorkflowSkillReferenceSearchResult:
    skill_name: str
    references: list[dict[str, object]]


@dataclass(frozen=True, slots=True)
class WorkflowPackMigrationDocument:
    reference: str
    path: Path
    revision: str
    content: bytes


class WorkflowConfigAdminService:
    """Workflow configuration, editor, and runtime-store admin operations."""

    def __init__(self, dependencies: WorkflowConfigAdminDependencies) -> None:
        self.project_dir = dependencies.project_dir
        self.config = dependencies.config
        self.skill_library = dependencies.skill_library
        self.directory_admin = dependencies.directory_admin
        self._admin_config_mutation_lock = dependencies.admin_config_mutation_lock
        self._normalize_directory_reference = dependencies.normalize_directory_reference
        self._workflow_reports_admin = dependencies.workflow_reports_admin
        self._addon_catalog = dependencies.addon_catalog
        history_runtime_dir = _resolve_path_setting(
            base_dir=self.project_dir,
            value=self.config.runtime_dir,
            fallback=self.project_dir / "runtime",
        )
        self._workflow_pack_history = WorkflowPackHistoryService(
            runtime_dir=history_runtime_dir,
            stable_json_revision=self._stable_json_revision,
        )

    def set_workflow_reports_admin(self, workflow_reports_admin: Any) -> None:
        self._workflow_reports_admin = workflow_reports_admin

    def admin_config_mutation_lock(self) -> ContextManager[None]:
        return self._admin_config_mutation_lock()

    def stable_json_revision(self, payload: object) -> str:
        return self._stable_json_revision(payload)

    def _stable_json_revision(self, payload: object) -> str:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def write_json_file_atomic(self, path: Path, payload: dict[str, object]) -> None:
        self._write_json_file_atomic(path, payload)

    def read_worker_global_config(self, worker_id: str) -> WorkerGlobalConfigPayload:
        return self._read_worker_global_config(worker_id)

    def write_worker_global_config(
        self,
        worker_id: str,
        payload: dict[str, object],
        *,
        expected_revision: str | None = None,
    ) -> WorkerGlobalConfigWritePayload:
        return self._write_worker_global_config(
            worker_id, payload, expected_revision=expected_revision
        )

    def write_worker_global_config_unlocked(
        self, worker_id: str, payload: dict[str, object]
    ) -> WorkerGlobalConfigWritePayload:
        return self._write_worker_global_config_unlocked(worker_id, payload)

    def read_workflow_editor_document(
        self, workflow_pack: str | None = None
    ) -> WorkflowEditorDocumentPayload:
        return self._read_workflow_editor_document(workflow_pack=workflow_pack)

    def read_composed_workflow_editor_document(self) -> WorkflowEditorDocumentPayload:
        return self._read_composed_workflow_editor_document()

    def read_workflow_editor_catalog(
        self, directory_path: str | None = None
    ) -> WorkflowEditorCatalogPayload:
        return self._read_workflow_editor_catalog(directory_path=directory_path)

    def coerce_workflow_editor_graphs(
        self,
        raw_graphs: object,
        *,
        strict: bool = True,
    ) -> list[WorkflowEditorGraphMetadata]:
        return self._coerce_workflow_editor_graphs(raw_graphs, strict=strict)

    def validate_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        workflow_pack: str | None = None,
    ) -> WorkflowEditorValidationPayload:
        return self._validate_workflow_editor_document(
            config=config,
            layout=layout,
            graphs=graphs,
            workflow_pack=workflow_pack,
        )

    def save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        expected_revision: str | None = None,
        workflow_pack: str | None = None,
        actor: WorkflowPackChangeActor | None = None,
    ) -> WorkflowEditorSavePayload:
        return self._save_workflow_editor_document(
            config=config,
            layout=layout,
            graphs=graphs,
            expected_revision=expected_revision,
            workflow_pack=workflow_pack,
            actor=actor,
        )

    def read_workflow_pack_for_migration(
        self,
        *,
        workflow_pack: str,
    ) -> WorkflowPackMigrationDocument:
        """Read one configured pack without normalizing legacy workflow nodes."""
        with self.admin_config_mutation_lock():
            return self._read_workflow_pack_for_migration_unlocked(workflow_pack)

    def rewrite_workflow_pack_for_migration(
        self,
        *,
        workflow_pack: str,
        content: bytes,
        expected_revision: str,
        operation: str,
        actor: WorkflowPackChangeActor | None = None,
    ) -> WorkflowPackMigrationDocument:
        """CAS-rewrite one configured pack for the privileged migration path."""
        with self.admin_config_mutation_lock():
            return self._rewrite_workflow_pack_for_migration_unlocked(
                workflow_pack=workflow_pack,
                content=content,
                expected_revision=expected_revision,
                operation=operation,
                actor=actor,
            )

    def find_managed_codex_skill_references(
        self, skill_name: str
    ) -> WorkflowSkillReferenceSearchResult:
        normalized_skill_name = validate_skill_name(skill_name)
        with self.admin_config_mutation_lock():
            return self._find_managed_codex_skill_references_unlocked(normalized_skill_name)

    def create_workflow_pack(
        self,
        *,
        pack_id: str,
        pack_version: str | None = None,
        description: str | None = None,
        expected_revision: str | None = None,
        actor: WorkflowPackChangeActor | None = None,
    ) -> WorkflowEditorPackMutationPayload:
        return self._create_workflow_pack(
            pack_id=pack_id,
            pack_version=pack_version,
            description=description,
            expected_revision=expected_revision,
            actor=actor,
        )

    def update_workflow_pack_metadata(
        self,
        *,
        workflow_pack: str,
        pack_id: str,
        pack_version: str,
        description: str | None = None,
        expected_revision: str | None = None,
        actor: WorkflowPackChangeActor | None = None,
    ) -> WorkflowEditorPackMutationPayload:
        return self._update_workflow_pack_metadata(
            workflow_pack=workflow_pack,
            pack_id=pack_id,
            pack_version=pack_version,
            description=description,
            expected_revision=expected_revision,
            actor=actor,
        )

    def remove_workflow_pack(
        self,
        *,
        workflow_pack: str,
        expected_revision: str | None = None,
        actor: WorkflowPackChangeActor | None = None,
    ) -> WorkflowEditorPackMutationPayload:
        return self._remove_workflow_pack(
            workflow_pack=workflow_pack,
            expected_revision=expected_revision,
            actor=actor,
        )

    def list_workflow_pack_history(
        self,
        *,
        workflow_pack: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> WorkflowPackHistoryListPayload:
        return self._workflow_pack_history.list_history(
            workflow_pack=workflow_pack,
            limit=limit,
            cursor=cursor,
        )

    def get_workflow_pack_history_detail(
        self,
        change_id: str,
    ) -> WorkflowPackHistoryDetailPayload:
        return self._workflow_pack_history.get_history_detail(change_id)

    def read_workflow_run_snapshot(self):
        if self._workflow_reports_admin is None:
            raise RuntimeError("workflow reports service is not configured")
        return self._workflow_reports_admin._read_workflow_run_snapshot()

    def read_trigger_document(self) -> TriggerConfigDocument:
        return self._read_trigger_document()

    def write_trigger_document(self, config: dict[str, object]) -> TriggerConfigDocument:
        return self._write_trigger_document(config)

    def workflow_run_store_paths(self) -> dict[str, Any]:
        return self._workflow_run_store_paths()

    def workflow_uses_sqlite_store(self, stores: Mapping[str, Any]) -> bool:
        return self._workflow_uses_sqlite_store(stores)

    def workflow_run_store_bundle(self, stores: Mapping[str, Any]) -> tuple[Any, Any, Any]:
        return self._workflow_run_store_bundle(stores)

    def read_workflow_run_detail(self, **kwargs: Any):
        if self._workflow_reports_admin is None:
            raise RuntimeError("workflow reports service is not configured")
        return self._workflow_reports_admin._read_workflow_run_detail(**kwargs)

    def read_workflow_run_search(self, **kwargs: Any):
        if self._workflow_reports_admin is None:
            raise RuntimeError("workflow reports service is not configured")
        return self._workflow_reports_admin._read_workflow_run_search(**kwargs)

    def clear_workflow_run(self, event_id: str) -> WorkflowRunClearPayload:
        return self._clear_workflow_run(event_id)

    def dismiss_workflow_node_failures(self, node_id: str) -> WorkflowNodeFailuresDismissPayload:
        return self._dismiss_workflow_node_failures(node_id)

    def resume_workflow_run(self, event_id: str) -> WorkflowRunResumePayload:
        return self._resume_workflow_run(event_id)

    def workflow_event_journal(self) -> WorkflowEventJournal | SqliteWorkflowEventJournal:
        return self._workflow_event_journal()

    def upsert_trigger_config(
        self, trigger_id: str, payload: dict[str, object]
    ) -> TriggerConfigMutationPayload:
        return self._upsert_trigger_config(trigger_id, payload)

    def delete_trigger_config(self, trigger_id: str) -> TriggerConfigMutationPayload:
        return self._delete_trigger_config(trigger_id)

    def upsert_effect_config(
        self, effect_id: str, payload: dict[str, object]
    ) -> EffectConfigMutationPayload:
        return self._upsert_effect_config(effect_id, payload)

    def delete_effect_config(self, effect_id: str) -> EffectConfigMutationPayload:
        return self._delete_effect_config(effect_id)

    def _worker_global_config_path(self, worker_id: str) -> Path | None:
        relative = _WORKER_GLOBAL_CONFIG_PATHS.get(worker_id)
        if relative is None:
            return None
        return self.project_dir / relative

    def _worker_global_config_revision(self, config: dict[str, object]) -> str:
        return self._stable_json_revision(config)

    def _read_worker_global_config_unlocked(self, worker_id: str) -> WorkerGlobalConfigPayload:
        path = self._worker_global_config_path(worker_id)
        if path is None:
            raise ValueError(f"Unknown worker: {worker_id}")
        exists = path.exists()
        if exists:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"worker config file {path} must contain a JSON object")
        else:
            model = _WORKER_GLOBAL_CONFIG_MODELS[worker_id]
            raw = model.model_validate(self.config.model_dump(mode="json")).model_dump(
                mode="json", exclude_none=True
            )
        model = _WORKER_GLOBAL_CONFIG_MODELS[worker_id]
        config = self._validate_worker_global_config_payload(worker_id, raw)
        return WorkerGlobalConfigPayload(
            worker_id=worker_id,
            path=str(path),
            exists=exists,
            config=config,
            revision=self._worker_global_config_revision(config),
        )

    def _read_worker_global_config(self, worker_id: str) -> WorkerGlobalConfigPayload:
        return self._read_worker_global_config_unlocked(worker_id)

    def _read_worker_provider_loader_config(self, worker_id: str) -> dict[str, object]:
        path = self._worker_global_config_path(worker_id)
        if path is None:
            raise ValueError(f"Unknown worker: {worker_id}")
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError(f"worker config file {path} must contain a JSON object")
        else:
            raw = self.config.model_dump(mode="json", exclude_none=True)
        return {key: raw[key] for key in ("provider_loader", "addon_loader") if key in raw}

    def _write_worker_global_config_unlocked(
        self, worker_id: str, payload: dict[str, object]
    ) -> WorkerGlobalConfigWritePayload:
        path = self._worker_global_config_path(worker_id)
        if path is None:
            raise ValueError(f"Unknown worker: {worker_id}")
        config = self._validate_worker_global_config_payload(worker_id, payload)
        if worker_id == "trigger-orchestrator":
            previous_config: dict[str, object] = {}
            if path.exists():
                try:
                    raw_previous_config = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    raw_previous_config = None
                if isinstance(raw_previous_config, dict):
                    previous_config = raw_previous_config
            self._seed_new_trigger_cursors(
                previous_config=previous_config,
                next_config=config,
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
        return WorkerGlobalConfigWritePayload(
            worker_id=worker_id,
            path=str(path),
            config=config,
            revision=self._worker_global_config_revision(config),
        )

    def _write_worker_global_config(
        self,
        worker_id: str,
        payload: dict[str, object],
        *,
        expected_revision: str | None = None,
    ) -> WorkerGlobalConfigWritePayload:
        with self.admin_config_mutation_lock():
            if expected_revision is not None:
                current = self._read_worker_global_config_unlocked(worker_id)
                if current.revision != expected_revision:
                    raise AdminRevisionConflict(
                        "Worker configuration changed before save. Reload the worker settings before saving again.",
                        details={
                            "current_revision": current.revision,
                            "current": current.model_dump(mode="json"),
                        },
                    )
            return self._write_worker_global_config_unlocked(worker_id, payload)

    def _read_trigger_document(self) -> TriggerConfigDocument:
        payload = self._read_worker_global_config("trigger-orchestrator")
        return TriggerConfigDocument(path=payload.path, config=payload.config)

    def _write_trigger_document(self, config: dict[str, object]) -> TriggerConfigDocument:
        payload = self._write_worker_global_config("trigger-orchestrator", config)
        return TriggerConfigDocument(path=payload.path, config=payload.config)

    def _write_trigger_document_unlocked(self, config: dict[str, object]) -> TriggerConfigDocument:
        payload = self._write_worker_global_config_unlocked("trigger-orchestrator", config)
        return TriggerConfigDocument(path=payload.path, config=payload.config)

    def _validate_worker_global_config_payload(
        self, worker_id: str, payload: dict[str, object]
    ) -> dict[str, object]:
        model = _WORKER_GLOBAL_CONFIG_MODELS[worker_id]
        try:
            normalized = model.model_validate(payload).model_dump(mode="json", exclude_none=True)
            return _strip_default_edge_fields(normalized)
        except ValidationError as exc:
            raise _safe_validation_error(exc, context=f"Invalid {worker_id} worker config") from exc

    def _load_workflow_provider_catalog(self, payload: dict[str, object]):
        from evander.provider_profiles import resolve_provider_module_names
        from evander.workflow.plugins import load_provider_plugins

        worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(payload)
        return load_provider_plugins(
            module_names=resolve_provider_module_names(
                profile_name=worker_config.provider_profile,
                extra_modules=worker_config.provider_loader.provider_modules,
            ),
            include_entry_points=worker_config.provider_loader.provider_entry_points_enabled,
            include_builtin=worker_config.provider_include_builtin,
            addon_loader=worker_config.addon_loader,
        )

    def _load_workflow_provider_output_schemas(self, payload: dict[str, object]) -> None:
        self._load_workflow_provider_catalog(payload)

    def publish_external_workflow_event(
        self,
        *,
        event_type: object,
        payload: dict[str, Any],
        correlation_key: object | None = None,
        idempotency_key: str | None = None,
        source: InternalEventSource | dict[str, Any] | None = None,
    ) -> dict[str, object]:
        event_name = str(event_type or "").strip()
        if not event_name:
            raise ValueError("event_type is required")
        if event_name.startswith(EXTERNAL_WORKFLOW_EVENT_PREFIX):
            raise ValueError(
                f"event_type must not include the reserved {EXTERNAL_WORKFLOW_EVENT_PREFIX!r} prefix"
            )
        validate_internal_event_type(event_name)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")

        worker_config_payload = self._read_worker_global_config("trigger-orchestrator")
        worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
            worker_config_payload.config
        )
        bus = InternalEventBus(
            worker_config.internal_event_store_path,
            journal_path=worker_config.workflow_event_journal_path,
        )
        event = bus.publish(
            event_type=f"{EXTERNAL_WORKFLOW_EVENT_PREFIX}{event_name}",
            payload=payload,
            correlation_key=correlation_key,
            source=source,
            idempotency_key=idempotency_key,
        )
        notify_wake_socket_for_lock(worker_config.lock_path)
        return event.model_dump(mode="json")

    def _workflow_editor_default_layout_path(self) -> Path:
        return self.project_dir / "config" / "trigger-orchestrator" / "layout.json"

    def _workflow_editor_pack_layout_path(self, pack_reference: object) -> Path:
        return self._workflow_editor_pack_path(pack_reference).with_suffix(".layout.json")

    def _workflow_editor_pack_reference(self, pack_reference: object) -> str:
        text = str(pack_reference).strip()
        if not text:
            raise ValueError("workflow pack reference must be a non-empty string")
        normalized = text if text.endswith(".json") else f"{text}.json"
        relative = Path(normalized)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("workflow pack reference must stay under config/workflow-packs")
        return normalized

    def _workflow_editor_layout_path(self, raw_config: dict[str, object]) -> Path:
        workflow_packs = raw_config.get("workflow_packs")
        inline_ir = raw_config.get("workflow_ir")
        if (
            isinstance(workflow_packs, list)
            and len(workflow_packs) == 1
            and not isinstance(inline_ir, dict)
        ):
            return self._workflow_editor_pack_layout_path(workflow_packs[0])
        return self._workflow_editor_default_layout_path()

    def _workflow_editor_revision(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object],
        graphs: list[WorkflowEditorGraphMetadata],
    ) -> str:
        revision_config = dict(config)
        if graphs:
            revision_config.pop("workflow_graphs", None)
        return self._stable_json_revision(
            {
                "config": revision_config,
                "layout": layout,
                "graphs": [graph.model_dump(mode="json") for graph in graphs],
            }
        )

    def _workflow_editor_pack_path(self, pack_reference: object) -> Path:
        normalized = self._workflow_editor_pack_reference(pack_reference)
        relative = Path(normalized)
        return self.project_dir / "config" / "workflow-packs" / relative

    def _workflow_editor_configured_pack_references(
        self, raw_config: dict[str, object]
    ) -> list[str]:
        workflow_packs = raw_config.get("workflow_packs")
        if not isinstance(workflow_packs, list):
            return []
        return [self._workflow_editor_pack_reference(pack) for pack in workflow_packs]

    @staticmethod
    def _managed_codex_skill_reference_count(value: object, skill_name: str) -> int:
        if isinstance(value, list):
            return sum(1 for item in value if isinstance(item, str) and item.strip() == skill_name)
        if isinstance(value, str) and "${{" not in value and value.strip() == skill_name:
            return 1
        return 0

    @classmethod
    def _find_managed_codex_skill_references_in_node(
        cls,
        node: object,
        skill_name: str,
        *,
        scope: str,
        path: Path,
        workflow_pack: str | None = None,
    ) -> list[dict[str, object]]:
        if not isinstance(node, dict):
            return []
        node_type = str(node.get("type") or "").strip()
        if node_type not in {"effect:agent:create", "effect:vm:create"}:
            return []

        references: list[dict[str, object]] = []
        for container, field_path in (
            (node, "codex_skills"),
            (
                node.get("config") if isinstance(node.get("config"), dict) else None,
                "config.codex_skills",
            ),
        ):
            if not isinstance(container, dict) or "codex_skills" not in container:
                continue
            reference_count = cls._managed_codex_skill_reference_count(
                container["codex_skills"],
                skill_name,
            )
            if not reference_count:
                continue
            reference: dict[str, object] = {
                "scope": scope,
                "path": str(path),
                "node_id": str(node.get("id") or "").strip() or None,
                "node_type": node_type,
                "display_name": str(node.get("display_name") or "").strip() or None,
                "field_path": field_path,
                "reference_count": reference_count,
            }
            if workflow_pack is not None:
                reference["workflow_pack"] = workflow_pack
            references.append(reference)
        return references

    @classmethod
    def _find_managed_codex_skill_references_in_ir_payload(
        cls,
        ir_payload: object,
        skill_name: str,
        *,
        scope: str,
        path: Path,
        workflow_pack: str | None = None,
    ) -> list[dict[str, object]]:
        if not isinstance(ir_payload, dict):
            return []
        nodes = ir_payload.get("nodes")
        if not isinstance(nodes, list):
            return []

        references: list[dict[str, object]] = []
        for entry in nodes:
            node = entry.get("node") if isinstance(entry, dict) and "node" in entry else entry
            references.extend(
                cls._find_managed_codex_skill_references_in_node(
                    node,
                    skill_name,
                    scope=scope,
                    path=path,
                    workflow_pack=workflow_pack,
                )
            )
        return references

    @classmethod
    def _find_managed_codex_skill_references_in_workflow_config_payload(
        cls,
        payload: dict[str, object],
        skill_name: str,
        *,
        path: Path,
    ) -> list[dict[str, object]]:
        references: list[dict[str, object]] = []

        effects = payload.get("effects")
        if isinstance(effects, list):
            for effect in effects:
                references.extend(
                    cls._find_managed_codex_skill_references_in_node(
                        effect,
                        skill_name,
                        scope="inline",
                        path=path,
                    )
                )

        references.extend(
            cls._find_managed_codex_skill_references_in_ir_payload(
                payload.get("workflow_ir"),
                skill_name,
                scope="inline",
                path=path,
            )
        )
        return references

    def _find_managed_codex_skill_references_unlocked(
        self, skill_name: str
    ) -> WorkflowSkillReferenceSearchResult:
        config_path, _config_exists, raw_config = self._workflow_editor_read_raw_worker_config()
        references = self._find_managed_codex_skill_references_in_workflow_config_payload(
            raw_config,
            skill_name,
            path=config_path,
        )

        for pack_reference in self._workflow_editor_configured_pack_references(raw_config):
            pack_path = self._workflow_editor_pack_path(pack_reference)
            if not pack_path.exists():
                continue
            pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
            if not isinstance(pack_payload, dict):
                raise ValueError(f"workflow pack file {pack_path} must contain a JSON object")
            references.extend(
                self._find_managed_codex_skill_references_in_ir_payload(
                    pack_payload.get("ir"),
                    skill_name,
                    scope="pack",
                    path=pack_path,
                    workflow_pack=pack_reference,
                )
            )

        return WorkflowSkillReferenceSearchResult(
            skill_name=skill_name,
            references=references,
        )

    def _workflow_editor_read_raw_worker_config(self) -> tuple[Path, bool, dict[str, object]]:
        config_path = self._worker_global_config_path("trigger-orchestrator")
        if config_path is None:
            raise ValueError("Unknown worker: trigger-orchestrator")
        config_exists = config_path.exists()
        if config_exists:
            raw_config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(raw_config, dict):
                raise ValueError(f"worker config file {config_path} must contain a JSON object")
        else:
            model = _WORKER_GLOBAL_CONFIG_MODELS["trigger-orchestrator"]
            raw_config = model.model_validate(self.config.model_dump(mode="json")).model_dump(
                mode="json", exclude_none=True
            )
        return config_path, config_exists, raw_config

    def _workflow_editor_worker_config_revision(self, raw_config: dict[str, object]) -> str:
        return self._stable_json_revision(raw_config)

    def _check_workflow_editor_worker_config_revision(
        self,
        raw_config: dict[str, object],
        *,
        expected_revision: str | None,
    ) -> None:
        if expected_revision is None:
            return
        current_revision = self._workflow_editor_worker_config_revision(raw_config)
        if current_revision != expected_revision:
            raise AdminRevisionConflict(
                "Workflow pack list changed before save. Reload the workflow before saving again.",
                details={
                    "current_revision": current_revision,
                    "current": self._read_composed_workflow_editor_document().model_dump(
                        mode="json"
                    ),
                },
            )

    @staticmethod
    def _workflow_pack_slug(value: str, *, field_name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{field_name} is required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", normalized):
            raise ValueError(
                f"{field_name} must start with a letter or number and contain only letters, numbers, dots, hyphens, or underscores"
            )
        if normalized in {".", ".."}:
            raise ValueError(f"{field_name} is not valid")
        return normalized

    @staticmethod
    def _optional_workflow_pack_description(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None

    def _workflow_pack_reference_for_metadata(self, *, pack_id: str, pack_version: str) -> str:
        return self._workflow_editor_pack_reference(f"{pack_id}.{pack_version}.json")

    def _workflow_pack_mutation_payload(
        self,
        *,
        config_path: Path,
        selected_reference: str | None,
    ) -> WorkflowEditorPackMutationPayload:
        document = self._read_workflow_editor_document(workflow_pack=selected_reference)
        return WorkflowEditorPackMutationPayload(
            config_path=str(config_path),
            workflow_packs=document.workflow_packs,
            selected_workflow_pack=document.selected_workflow_pack,
            revision=document.revision,
            document=document,
        )

    def _workflow_pack_history_files(
        self,
        *,
        config_path: Path,
        pack_path: Path,
        layout_path: Path,
        tolerate_invalid: bool = False,
    ) -> list[WorkflowPackHistoryFileState]:
        return [
            self._workflow_pack_history.capture_file_state(
                "worker_config",
                config_path,
                tolerate_invalid=tolerate_invalid,
            ),
            self._workflow_pack_history.capture_file_state(
                "pack",
                pack_path,
                tolerate_invalid=tolerate_invalid,
            ),
            self._workflow_pack_history.capture_file_state(
                "layout",
                layout_path,
                tolerate_invalid=tolerate_invalid,
            ),
        ]

    def _workflow_pack_revision_from_history_files(
        self, files: list[WorkflowPackHistoryFileState]
    ) -> str | None:
        by_label = {file_state.label: file_state for file_state in files}
        pack = by_label.get("pack")
        if pack is None or not pack.exists or pack.content is None:
            return None
        layout = by_label.get("layout")
        return self._stable_json_revision(
            {
                "pack": pack.content,
                "layout_exists": bool(layout and layout.exists),
                "layout": layout.content if layout and layout.exists and layout.content else {},
            }
        )

    def _record_workflow_pack_history(
        self,
        *,
        operation: str,
        workflow_pack: str,
        actor: WorkflowPackChangeActor | None,
        before_files: list[WorkflowPackHistoryFileState],
        after_files: list[WorkflowPackHistoryFileState],
    ) -> None:
        self._workflow_pack_history.record_change(
            operation=operation,
            workflow_pack=workflow_pack,
            actor=actor,
            before_files=before_files,
            after_files=after_files,
            before_revision=self._workflow_pack_revision_from_history_files(before_files),
            after_revision=self._workflow_pack_revision_from_history_files(after_files),
        )

    def _read_workflow_pack_for_migration_unlocked(
        self,
        workflow_pack: str,
    ) -> WorkflowPackMigrationDocument:
        _config_path, _config_exists, raw_config = self._workflow_editor_read_raw_worker_config()
        selected_reference = self._workflow_editor_selected_pack_reference(
            raw_config,
            workflow_pack,
        )
        if selected_reference is None:
            raise ValueError("Workflow pack is required for managed Git migration")
        pack_path = self._workflow_editor_pack_path(selected_reference)
        if not pack_path.is_file():
            raise ValueError(f"workflow pack {selected_reference!r} does not exist at {pack_path}")
        content = pack_path.read_bytes()
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(
                f"workflow pack file {pack_path} must contain a UTF-8 JSON object"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(f"workflow pack file {pack_path} must contain a JSON object")
        return WorkflowPackMigrationDocument(
            reference=selected_reference,
            path=pack_path.resolve(strict=False),
            revision=hashlib.sha256(content).hexdigest(),
            content=content,
        )

    def _rewrite_workflow_pack_for_migration_unlocked(
        self,
        *,
        workflow_pack: str,
        content: bytes,
        expected_revision: str,
        operation: str,
        actor: WorkflowPackChangeActor | None,
    ) -> WorkflowPackMigrationDocument:
        allowed_operations = {
            "managed_git_migration_apply",
            "managed_git_migration_rollback",
        }
        if operation not in allowed_operations:
            raise ValueError("Unsupported workflow pack migration operation")
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Workflow pack migration content must be UTF-8 JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Workflow pack migration content must be a JSON object")

        current = self._read_workflow_pack_for_migration_unlocked(workflow_pack)
        normalized_expected = str(expected_revision or "").strip().lower()
        if current.revision != normalized_expected:
            raise AdminRevisionConflict(
                "Workflow pack changed before managed Git migration. Run the dry-run again.",
                details={
                    "workflow_pack": current.reference,
                    "current_revision": current.revision,
                },
            )
        config_path, _config_exists, _raw_config = self._workflow_editor_read_raw_worker_config()
        layout_path = self._workflow_editor_pack_layout_path(current.reference)
        before_files = self._workflow_pack_history_files(
            config_path=config_path,
            pack_path=current.path,
            layout_path=layout_path,
            tolerate_invalid=True,
        )
        try:
            self._write_file_bytes_atomic(current.path, content)
            after_files = self._workflow_pack_history_files(
                config_path=config_path,
                pack_path=current.path,
                layout_path=layout_path,
                tolerate_invalid=True,
            )
            self._record_workflow_pack_history(
                operation=operation,
                workflow_pack=current.reference,
                actor=actor,
                before_files=before_files,
                after_files=after_files,
            )
        except BaseException:
            if (
                current.path.is_file()
                and hashlib.sha256(current.path.read_bytes()).hexdigest()
                == hashlib.sha256(content).hexdigest()
            ):
                self._write_file_bytes_atomic(current.path, current.content)
            raise
        return WorkflowPackMigrationDocument(
            reference=current.reference,
            path=current.path,
            revision=hashlib.sha256(content).hexdigest(),
            content=content,
        )

    @staticmethod
    def _workflow_editor_has_inline_workflow(raw_config: dict[str, object]) -> bool:
        if isinstance(raw_config.get("workflow_ir"), dict):
            return True
        for key in ("triggers", "gates", "effects", "queries", "edges", "workflow_graphs"):
            value = raw_config.get(key)
            if isinstance(value, list) and bool(value):
                return True
        return False

    def _create_workflow_pack(
        self,
        *,
        pack_id: str,
        pack_version: str | None = None,
        description: str | None = None,
        expected_revision: str | None = None,
        actor: WorkflowPackChangeActor | None = None,
    ) -> WorkflowEditorPackMutationPayload:
        with self.admin_config_mutation_lock():
            config_path, _config_exists, raw_config = self._workflow_editor_read_raw_worker_config()
            self._check_workflow_editor_worker_config_revision(
                raw_config,
                expected_revision=expected_revision,
            )
            configured_references = self._workflow_editor_configured_pack_references(raw_config)
            if self._workflow_editor_has_inline_workflow(raw_config):
                raise ValueError(
                    "Workflow pack management cannot convert inline workflow configs yet."
                )
            normalized_pack_id = self._workflow_pack_slug(pack_id, field_name="pack_id")
            normalized_pack_version = self._workflow_pack_slug(
                pack_version or "v1",
                field_name="pack_version",
            )
            reference = self._workflow_pack_reference_for_metadata(
                pack_id=normalized_pack_id,
                pack_version=normalized_pack_version,
            )
            if reference in configured_references:
                raise ValueError(f"Workflow pack is already configured: {reference}")
            pack_path = self._workflow_editor_pack_path(reference)
            if pack_path.exists():
                raise ValueError(f"workflow pack file already exists at {pack_path}")
            layout_path = self._workflow_editor_pack_layout_path(reference)
            before_files = self._workflow_pack_history_files(
                config_path=config_path,
                pack_path=pack_path,
                layout_path=layout_path,
            )

            pack_payload: dict[str, object] = {
                "pack_id": normalized_pack_id,
                "pack_version": normalized_pack_version,
                "graphs": [],
                "ir": {
                    "version": "v1",
                    "nodes": [],
                    "edges": [],
                },
            }
            normalized_description = self._optional_workflow_pack_description(description)
            if normalized_description is not None:
                pack_payload["description"] = normalized_description

            wrapper_config = dict(raw_config)
            wrapper_config["workflow_packs"] = [*configured_references, reference]
            config_path.parent.mkdir(parents=True, exist_ok=True)
            pack_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json_file_atomic(pack_path, pack_payload)
            self._write_json_file_atomic(config_path, wrapper_config)
            after_files = self._workflow_pack_history_files(
                config_path=config_path,
                pack_path=pack_path,
                layout_path=layout_path,
            )
            self._record_workflow_pack_history(
                operation="create",
                workflow_pack=reference,
                actor=actor,
                before_files=before_files,
                after_files=after_files,
            )
            return self._workflow_pack_mutation_payload(
                config_path=config_path,
                selected_reference=reference,
            )

    def ensure_workflow_pack_payload_unlocked(
        self,
        *,
        raw_config: dict[str, object],
        pack_payload: dict[str, object],
        layout_payload: dict[str, object] | None = None,
        overwrite_existing: bool,
        operation: str,
        actor: WorkflowPackChangeActor | None = None,
    ) -> tuple[dict[str, object], str]:
        if self._workflow_editor_has_inline_workflow(raw_config):
            raise ValueError(
                "Workflow pack management cannot update configs that combine workflow packs with inline workflow definitions."
            )
        pack = WorkflowPack.model_validate(pack_payload)
        normalized_payload = pack.model_dump(mode="json")
        reference = self._workflow_pack_reference_for_metadata(
            pack_id=pack.pack_id,
            pack_version=pack.pack_version,
        )
        config_path, _config_exists, _current_raw_config = (
            self._workflow_editor_read_raw_worker_config()
        )
        pack_path = self._workflow_editor_pack_path(reference)
        layout_path = self._workflow_editor_pack_layout_path(reference)
        normalized_layout: dict[str, object] | None = None
        if layout_payload is not None:
            node_ids = {ir_node.node.id for ir_node in pack.ir.nodes}
            normalized_layout = self._validate_workflow_editor_layout(
                layout_payload,
                node_ids=node_ids,
            )
            missing_node_ids = sorted(node_ids.difference(normalized_layout["nodes"]))
            if missing_node_ids:
                raise ValueError(
                    "Workflow pack layout must position every node ID: "
                    + ", ".join(missing_node_ids)
                )
        configured_references = self._workflow_editor_configured_pack_references(raw_config)
        existing_pack = pack_path.exists()
        if existing_pack and not overwrite_existing:
            load_workflow_pack(self.project_dir, reference)

        before_files = self._workflow_pack_history_files(
            config_path=config_path,
            pack_path=pack_path,
            layout_path=layout_path,
            tolerate_invalid=overwrite_existing,
        )
        write_pack = overwrite_existing or not existing_pack
        if write_pack:
            pack_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json_file_atomic(pack_path, normalized_payload)
            if normalized_layout is not None:
                self._write_json_file_atomic(layout_path, normalized_layout)

        wrote_config = False
        next_config = dict(raw_config)
        if reference not in configured_references:
            next_config["workflow_packs"] = [*configured_references, reference]
            self._write_worker_global_config_unlocked("trigger-orchestrator", next_config)
            wrote_config = True

        if write_pack or wrote_config:
            after_files = self._workflow_pack_history_files(
                config_path=config_path,
                pack_path=pack_path,
                layout_path=layout_path,
                tolerate_invalid=overwrite_existing,
            )
            self._record_workflow_pack_history(
                operation=operation,
                workflow_pack=reference,
                actor=actor,
                before_files=before_files,
                after_files=after_files,
            )
        return next_config, reference

    def _update_workflow_pack_metadata(
        self,
        *,
        workflow_pack: str,
        pack_id: str,
        pack_version: str,
        description: str | None = None,
        expected_revision: str | None = None,
        actor: WorkflowPackChangeActor | None = None,
    ) -> WorkflowEditorPackMutationPayload:
        with self.admin_config_mutation_lock():
            config_path, _config_exists, raw_config = self._workflow_editor_read_raw_worker_config()
            selected_reference = self._workflow_editor_selected_pack_reference(
                raw_config,
                workflow_pack,
            )
            if selected_reference is None:
                raise ValueError("Workflow pack is required")
            current = self._read_workflow_editor_document(workflow_pack=selected_reference)
            current_pack = current.selected_workflow_pack
            if current_pack is None or not current_pack.exists:
                raise ValueError(f"Workflow pack is not editable: {selected_reference}")
            if expected_revision is not None and current_pack.revision != expected_revision:
                raise AdminRevisionConflict(
                    "Workflow pack metadata changed before save. Reload the workflow before saving again.",
                    details={
                        "current_revision": current_pack.revision,
                        "current": current.model_dump(mode="json"),
                    },
                )

            normalized_pack_id = self._workflow_pack_slug(pack_id, field_name="pack_id")
            normalized_pack_version = self._workflow_pack_slug(
                pack_version,
                field_name="pack_version",
            )
            pack_path = self._workflow_editor_pack_path(selected_reference)
            layout_path = self._workflow_editor_pack_layout_path(selected_reference)
            before_files = self._workflow_pack_history_files(
                config_path=config_path,
                pack_path=pack_path,
                layout_path=layout_path,
            )
            pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
            if not isinstance(pack_payload, dict):
                raise ValueError(f"workflow pack file {pack_path} must contain a JSON object")
            pack_payload["pack_id"] = normalized_pack_id
            pack_payload["pack_version"] = normalized_pack_version
            normalized_description = self._optional_workflow_pack_description(description)
            if normalized_description is None:
                pack_payload.pop("description", None)
            else:
                pack_payload["description"] = normalized_description
            self._write_json_file_atomic(pack_path, pack_payload)
            after_files = self._workflow_pack_history_files(
                config_path=config_path,
                pack_path=pack_path,
                layout_path=layout_path,
            )
            self._record_workflow_pack_history(
                operation="update_metadata",
                workflow_pack=selected_reference,
                actor=actor,
                before_files=before_files,
                after_files=after_files,
            )
            return self._workflow_pack_mutation_payload(
                config_path=config_path,
                selected_reference=selected_reference,
            )

    def _remove_workflow_pack(
        self,
        *,
        workflow_pack: str,
        expected_revision: str | None = None,
        actor: WorkflowPackChangeActor | None = None,
    ) -> WorkflowEditorPackMutationPayload:
        with self.admin_config_mutation_lock():
            config_path, _config_exists, raw_config = self._workflow_editor_read_raw_worker_config()
            self._check_workflow_editor_worker_config_revision(
                raw_config,
                expected_revision=expected_revision,
            )
            selected_reference = self._workflow_editor_selected_pack_reference(
                raw_config,
                workflow_pack,
            )
            if selected_reference is None:
                raise ValueError("Workflow pack is required")
            configured_references = self._workflow_editor_configured_pack_references(raw_config)
            if len(configured_references) <= 1:
                raise ValueError("Cannot remove the last configured workflow pack.")
            pack_path = self._workflow_editor_pack_path(selected_reference)
            layout_path = self._workflow_editor_pack_layout_path(selected_reference)
            before_files = self._workflow_pack_history_files(
                config_path=config_path,
                pack_path=pack_path,
                layout_path=layout_path,
                tolerate_invalid=True,
            )
            selected_index = configured_references.index(selected_reference)
            next_references = list(configured_references)
            next_references.pop(selected_index)
            next_selected_index = min(selected_index, len(next_references) - 1)
            next_selected_reference = next_references[next_selected_index]
            wrapper_config = dict(raw_config)
            wrapper_config["workflow_packs"] = next_references
            config_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json_file_atomic(config_path, wrapper_config)
            after_files = self._workflow_pack_history_files(
                config_path=config_path,
                pack_path=pack_path,
                layout_path=layout_path,
                tolerate_invalid=True,
            )
            self._record_workflow_pack_history(
                operation="remove",
                workflow_pack=selected_reference,
                actor=actor,
                before_files=before_files,
                after_files=after_files,
            )
            return self._workflow_pack_mutation_payload(
                config_path=config_path,
                selected_reference=next_selected_reference,
            )

    def _workflow_editor_selected_pack_reference(
        self,
        raw_config: dict[str, object],
        workflow_pack: str | None,
    ) -> str | None:
        pack_references = self._workflow_editor_configured_pack_references(raw_config)
        if not pack_references:
            if workflow_pack:
                raise ValueError(
                    "Workflow pack selection requires workflow_packs in the worker config."
                )
            return None
        if workflow_pack is None or not str(workflow_pack).strip():
            return None
        selected = self._workflow_editor_pack_reference(workflow_pack)
        if selected not in pack_references:
            raise ValueError(f"Workflow pack is not configured: {selected}")
        return selected

    def _workflow_editor_pack_descriptors(
        self,
        raw_config: dict[str, object],
        *,
        selected_reference: str | None = None,
    ) -> list[WorkflowEditorWorkflowPackDescriptor]:
        descriptors: list[WorkflowEditorWorkflowPackDescriptor] = []
        for reference in self._workflow_editor_configured_pack_references(raw_config):
            pack_path = self._workflow_editor_pack_path(reference)
            layout_path = self._workflow_editor_pack_layout_path(reference)
            exists = pack_path.exists()
            pack_id: str | None = None
            pack_version: str | None = None
            description: str | None = None
            graph_count = 0
            node_count = 0
            display_name = reference
            revision: str | None = None
            if exists:
                raw_pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
                if not isinstance(raw_pack_payload, dict):
                    raise ValueError(f"workflow pack file {pack_path} must contain a JSON object")
                pack = load_workflow_pack(self.project_dir, reference)
                pack_id = pack.pack_id
                pack_version = pack.pack_version
                description = pack.description
                graph_count = len(pack.graphs)
                node_count = len(pack.ir.nodes)
                display_name = pack.pack_id or reference
                if pack.pack_version:
                    display_name = f"{display_name} {pack.pack_version}"
                revision = self._stable_json_revision(
                    {
                        "pack": raw_pack_payload,
                        "layout_exists": layout_path.exists(),
                        "layout": (
                            json.loads(layout_path.read_text(encoding="utf-8"))
                            if layout_path.exists()
                            else {}
                        ),
                    }
                )
            descriptors.append(
                WorkflowEditorWorkflowPackDescriptor(
                    reference=reference,
                    path=str(pack_path),
                    layout_path=str(layout_path),
                    exists=exists,
                    editable=exists,
                    display_name=display_name,
                    revision=revision,
                    id=pack_id,
                    version=pack_version,
                    description=description,
                    graph_count=graph_count,
                    node_count=node_count,
                    selected=reference == selected_reference,
                )
            )
        return descriptors

    def _workflow_editor_config_for_pack_composition(
        self,
        *,
        raw_config: dict[str, object],
        composition: object,
    ) -> dict[str, object]:
        graph_keys = {
            "triggers",
            "gates",
            "effects",
            "queries",
            "edges",
            "workflow_ir",
            "workflow_graphs",
        }
        config = {key: value for key, value in raw_config.items() if key not in graph_keys}
        for collection_name in ("triggers", "gates", "effects", "queries"):
            config[collection_name] = list(getattr(composition, collection_name))
        config["edges"] = list(getattr(composition, "edges"))
        return config

    def _workflow_editor_pack_graphs(
        self,
        *,
        raw_config: dict[str, object],
        pack_reference: str,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        composition = compose_workflow_ir(
            project_dir=self.project_dir,
            workflow_packs=[pack_reference],
        )
        config = self._workflow_editor_config_for_pack_composition(
            raw_config=raw_config,
            composition=composition,
        )
        graphs = [graph.model_dump(mode="json") for graph in composition.graphs]
        return config, graphs

    def _workflow_editor_stored_graphs(
        self, raw_config: dict[str, object]
    ) -> list[dict[str, object]]:
        graphs: list[dict[str, object]] = []
        workflow_packs = raw_config.get("workflow_packs")
        if isinstance(workflow_packs, list):
            for pack_reference in workflow_packs:
                pack_path = self._workflow_editor_pack_path(pack_reference)
                if not pack_path.exists():
                    continue
                pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
                if not isinstance(pack_payload, dict):
                    continue
                pack_graphs = pack_payload.get("graphs")
                if isinstance(pack_graphs, list):
                    graphs.extend(item for item in pack_graphs if isinstance(item, dict))
        inline_graphs = raw_config.get("workflow_graphs")
        if isinstance(inline_graphs, list):
            graphs.extend(item for item in inline_graphs if isinstance(item, dict))
        return graphs

    def _workflow_editor_nodes_by_id(
        self, config: dict[str, object]
    ) -> dict[str, dict[str, object]]:
        nodes_by_id: dict[str, dict[str, object]] = {}
        for collection_name in ("triggers", "gates", "effects", "queries"):
            entries = config.get(collection_name)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                node_id = str(entry.get("id") or "").strip()
                if node_id:
                    nodes_by_id[node_id] = entry
        return nodes_by_id

    def _workflow_editor_connected_graph_node_ids(
        self, config: dict[str, object]
    ) -> list[list[str]]:
        nodes_by_id = self._workflow_editor_nodes_by_id(config)
        ordered_node_ids = list(nodes_by_id)
        node_ids = set(ordered_node_ids)
        neighbors: dict[str, set[str]] = {node_id: set() for node_id in ordered_node_ids}
        edges = config.get("edges")
        if isinstance(edges, list):
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                source_id = str(edge.get("source_id") or "").strip()
                target_id = str(edge.get("target_id") or "").strip()
                if source_id not in node_ids or target_id not in node_ids:
                    continue
                neighbors[source_id].add(target_id)
                neighbors[target_id].add(source_id)

        components: list[list[str]] = []
        visited: set[str] = set()
        order = {node_id: index for index, node_id in enumerate(ordered_node_ids)}
        for node_id in ordered_node_ids:
            if node_id in visited:
                continue
            stack = [node_id]
            component: set[str] = set()
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.add(current)
                stack.extend(sorted(neighbors[current] - visited, key=order.get, reverse=True))
            components.append(sorted(component, key=order.get))
        return components

    def _workflow_editor_default_graph_name(
        self,
        *,
        component: list[str],
        nodes_by_id: dict[str, dict[str, object]],
        index: int,
    ) -> str:
        trigger_node = next(
            (
                nodes_by_id[node_id]
                for node_id in component
                if str(nodes_by_id[node_id].get("type") or "").startswith("trigger:")
            ),
            None,
        )
        fallback_node = nodes_by_id.get(component[0]) if component else None
        source_node = trigger_node or fallback_node
        display_name = str(source_node.get("display_name") or "").strip() if source_node else ""
        return display_name or f"Graph {index + 1}"

    def _workflow_editor_graph_id_base(
        self,
        *,
        component: list[str],
        nodes_by_id: dict[str, dict[str, object]],
        index: int,
    ) -> str:
        trigger_id = next(
            (
                node_id
                for node_id in component
                if str(nodes_by_id[node_id].get("type") or "").startswith("trigger:")
            ),
            "",
        )
        source = trigger_id or (component[0] if component else f"graph-{index + 1}")
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", source).strip("-").lower()
        return f"graph-{slug or index + 1}"

    def _coerce_workflow_editor_graphs(
        self,
        graphs: object,
        *,
        strict: bool,
    ) -> list[WorkflowEditorGraphMetadata]:
        if graphs is None:
            return []
        if not isinstance(graphs, list):
            if strict:
                raise ValueError("Workflow graphs must be a JSON array.")
            return []
        normalized: list[WorkflowEditorGraphMetadata] = []
        for entry in graphs:
            if not isinstance(entry, dict):
                if strict:
                    raise ValueError("Workflow graph entries must be JSON objects.")
                continue
            try:
                graph = WorkflowEditorGraphMetadata.model_validate(entry)
            except ValidationError as exc:
                if strict:
                    raise _safe_validation_error(exc, context="Invalid workflow graph") from exc
                continue
            graph_id = graph.id.strip()
            graph_name = graph.name.strip()
            node_ids: list[str] = []
            seen_node_ids: set[str] = set()
            for raw_node_id in graph.node_ids:
                node_id = str(raw_node_id or "").strip()
                if not node_id or node_id in seen_node_ids:
                    continue
                seen_node_ids.add(node_id)
                node_ids.append(node_id)
            if not graph_id or not graph_name:
                if strict:
                    raise ValueError("Workflow graph id and name are required.")
                continue
            description = graph.description.strip() if graph.description else None
            user_ids: list[str] = []
            seen_user_ids: set[str] = set()
            for raw_user_id in graph.user_ids:
                user_id = str(raw_user_id or "").strip()
                if not user_id or user_id in seen_user_ids:
                    continue
                seen_user_ids.add(user_id)
                user_ids.append(user_id)
            normalized.append(
                WorkflowEditorGraphMetadata(
                    id=graph_id,
                    name=graph_name,
                    description=description or None,
                    directory_path=graph.directory_path,
                    user_ids=user_ids,
                    node_ids=node_ids,
                )
            )
        return normalized

    def _workflow_editor_graphs_for_config(
        self,
        *,
        config: dict[str, object],
        graphs: object,
        strict: bool = False,
    ) -> list[WorkflowEditorGraphMetadata]:
        components = self._workflow_editor_connected_graph_node_ids(config)
        component_keys = {frozenset(component) for component in components}
        node_ids = set(self._workflow_editor_nodes_by_id(config))
        stored_graphs = self._coerce_workflow_editor_graphs(graphs, strict=strict)
        seen_graph_ids: set[str] = set()
        seen_graph_keys: set[frozenset[str]] = set()
        valid_stored_graphs: list[WorkflowEditorGraphMetadata] = []
        for graph in stored_graphs:
            graph_key = frozenset(graph.node_ids)
            if graph.id in seen_graph_ids:
                if strict:
                    raise ValueError(f"Duplicate workflow graph id: {graph.id}")
                continue
            seen_graph_ids.add(graph.id)
            unknown_node_ids = sorted(
                node_id for node_id in graph.node_ids if node_id not in node_ids
            )
            if unknown_node_ids:
                if strict:
                    unknown_list = ", ".join(unknown_node_ids)
                    raise ValueError(
                        f"Workflow graph {graph.id} references unknown node IDs: {unknown_list}"
                    )
                continue
            if graph_key in seen_graph_keys:
                if strict:
                    raise ValueError(
                        f"Multiple workflow graphs describe the same connected graph: {graph.id}"
                    )
                continue
            seen_graph_keys.add(graph_key)
            if strict and graph_key not in component_keys:
                raise ValueError(
                    f"Workflow graph {graph.id} node_ids must match one connected graph."
                )
            valid_stored_graphs.append(graph)
        stored_graphs = valid_stored_graphs

        by_exact_key = {frozenset(graph.node_ids): graph for graph in stored_graphs}
        unmatched_graphs = [
            graph for graph in stored_graphs if frozenset(graph.node_ids) not in component_keys
        ]
        used_graph_ids: set[str] = set()
        used_result_ids: set[str] = set()
        result: list[WorkflowEditorGraphMetadata] = []
        nodes_by_id = self._workflow_editor_nodes_by_id(config)
        for index, component in enumerate(components):
            component_key = frozenset(component)
            matched = by_exact_key.get(component_key)
            if matched is None:
                matched = max(
                    (
                        graph
                        for graph in unmatched_graphs
                        if graph.id not in used_graph_ids
                        and component_key.intersection(graph.node_ids)
                    ),
                    key=lambda graph: len(component_key.intersection(graph.node_ids)),
                    default=None,
                )
            if matched is not None:
                used_graph_ids.add(matched.id)
                graph_id = matched.id
                graph_name = matched.name
                graph_description = matched.description
                graph_directory_path = matched.directory_path
                graph_user_ids = matched.user_ids
            else:
                graph_id = self._workflow_editor_graph_id_base(
                    component=component, nodes_by_id=nodes_by_id, index=index
                )
                graph_name = self._workflow_editor_default_graph_name(
                    component=component, nodes_by_id=nodes_by_id, index=index
                )
                graph_description = None
                graph_directory_path = "root"
                graph_user_ids = []
            unique_id = graph_id
            suffix = 2
            while unique_id in used_result_ids:
                unique_id = f"{graph_id}-{suffix}"
                suffix += 1
            used_result_ids.add(unique_id)
            result.append(
                WorkflowEditorGraphMetadata(
                    id=unique_id,
                    name=graph_name,
                    description=graph_description,
                    directory_path=graph_directory_path,
                    user_ids=graph_user_ids,
                    node_ids=component,
                )
            )
        return result

    def _workflow_editor_graph_payloads_to_persist(
        self,
        *,
        raw_graphs: list[dict[str, object]],
        normalized_graphs: list[WorkflowEditorGraphMetadata],
    ) -> list[dict[str, object]]:
        raw_graphs_by_id: dict[str, dict[str, object]] = {}
        normalized_raw_graphs_by_id: dict[str, dict[str, object]] = {}
        for raw_graph in raw_graphs:
            graph_id = str(raw_graph.get("id") or "").strip()
            if not graph_id or graph_id in raw_graphs_by_id:
                continue
            raw_graphs_by_id[graph_id] = raw_graph
            normalized_raw_graph = self._coerce_workflow_editor_graphs(
                [raw_graph],
                strict=False,
            )
            if normalized_raw_graph:
                normalized_raw_graphs_by_id[graph_id] = normalized_raw_graph[0].model_dump(
                    mode="json"
                )

        graph_payloads: list[dict[str, object]] = []
        for graph in normalized_graphs:
            graph_payload = graph.model_dump(mode="json")
            raw_graph = raw_graphs_by_id.get(graph.id)
            normalized_raw_graph = normalized_raw_graphs_by_id.get(graph.id)
            if raw_graph is not None and graph_payload == normalized_raw_graph:
                graph_payloads.append(json.loads(json.dumps(raw_graph)))
            else:
                graph_payloads.append(graph_payload)
        return graph_payloads

    def _compose_workflow_editor_config(self, payload: dict[str, object]) -> dict[str, object]:
        candidate = json.loads(json.dumps(payload))
        if not isinstance(candidate, dict):
            return payload
        return _compose_configured_workflow_ir(project_dir=self.project_dir, payload=candidate)

    def _workflow_editor_pack_backed_save_paths(
        self,
        raw_config: dict[str, object],
        workflow_pack: str | None = None,
    ) -> tuple[Path, list[object]] | None:
        workflow_packs = raw_config.get("workflow_packs")
        inline_ir = raw_config.get("workflow_ir")
        if not isinstance(workflow_packs, list) or not workflow_packs:
            return None
        if isinstance(inline_ir, dict):
            raise ValueError(
                "Workflow editor cannot save configs that combine workflow_packs with inline workflow_ir; "
                "edit the pack files directly."
            )
        if workflow_pack is None or not str(workflow_pack).strip():
            if len(workflow_packs) != 1:
                raise ValueError(
                    "Workflow editor save requires workflow_pack when multiple workflow packs are configured."
                )
            selected_reference = self._workflow_editor_pack_reference(workflow_packs[0])
        else:
            selected_reference = self._workflow_editor_selected_pack_reference(
                raw_config,
                workflow_pack,
            )
        if selected_reference is None:
            return None
        return self._workflow_editor_pack_path(selected_reference), workflow_packs

    def _workflow_editor_pack_payload_to_persist(
        self,
        *,
        pack_path: Path,
        validated_config: dict[str, object],
        graph_payloads: list[dict[str, object]] | None,
    ) -> dict[str, object]:
        if pack_path.exists():
            pack_payload = json.loads(pack_path.read_text(encoding="utf-8"))
            if not isinstance(pack_payload, dict):
                raise ValueError(f"workflow pack file {pack_path} must contain a JSON object")
        else:
            raise ValueError(f"workflow pack file {pack_path} does not exist")

        existing_ir = pack_payload.get("ir")
        ir_version = "v1"
        if isinstance(existing_ir, dict):
            ir_version = str(existing_ir.get("version") or "v1")
        nodes: list[object] = []
        for key in ("triggers", "gates", "effects", "queries"):
            values = validated_config.get(key, [])
            if isinstance(values, list):
                nodes.extend(values)
        pack_payload["ir"] = {
            "version": ir_version,
            "nodes": nodes,
            "edges": validated_config.get("edges", []),
        }
        if graph_payloads is not None:
            pack_payload["graphs"] = graph_payloads
        return pack_payload

    def _write_workflow_editor_pack_config(
        self,
        *,
        config_path: Path,
        pack_path: Path,
        workflow_packs: list[object],
        validated_config: dict[str, object],
        graph_payloads: list[dict[str, object]] | None,
    ) -> None:
        pack_payload = self._workflow_editor_pack_payload_to_persist(
            pack_path=pack_path,
            validated_config=validated_config,
            graph_payloads=graph_payloads,
        )

        graph_keys = {"triggers", "gates", "effects", "queries", "edges"}
        wrapper_config = {
            key: value for key, value in validated_config.items() if key not in graph_keys
        }
        wrapper_config["workflow_packs"] = workflow_packs
        wrapper_config.pop("workflow_ir", None)
        wrapper_config.pop("workflow_graphs", None)

        self._write_json_file_atomic(config_path, wrapper_config)
        self._write_json_file_atomic(pack_path, pack_payload)

    def _validate_selected_workflow_pack_runtime_config(
        self,
        *,
        raw_config: dict[str, object],
        workflow_packs: list[object],
        selected_reference: str,
        selected_validation: WorkflowEditorValidationPayload,
    ) -> None:
        graph_keys = {
            "triggers",
            "gates",
            "effects",
            "queries",
            "edges",
            "workflow_ir",
            "workflow_graphs",
        }
        full_config = {key: value for key, value in raw_config.items() if key not in graph_keys}
        full_config["workflow_packs"] = workflow_packs
        combined_graphs: list[dict[str, object]] = []
        for collection_name in ("triggers", "gates", "effects", "queries"):
            full_config[collection_name] = []
        full_config["edges"] = []

        for pack_reference in workflow_packs:
            normalized_reference = self._workflow_editor_pack_reference(pack_reference)
            if normalized_reference == selected_reference:
                pack_config = selected_validation.config
                for collection_name in ("triggers", "gates", "effects", "queries"):
                    entries = pack_config.get(collection_name, [])
                    if isinstance(entries, list):
                        full_config[collection_name].extend(
                            entry for entry in entries if isinstance(entry, dict)
                        )
                edges = pack_config.get("edges", [])
                if isinstance(edges, list):
                    full_config["edges"].extend(edge for edge in edges if isinstance(edge, dict))
                combined_graphs.extend(
                    graph.model_dump(mode="json") for graph in selected_validation.graphs
                )
                continue

            composition = compose_workflow_ir(
                project_dir=self.project_dir,
                workflow_packs=[normalized_reference],
            )
            pack_config = self._workflow_editor_config_for_pack_composition(
                raw_config=raw_config,
                composition=composition,
            )
            for collection_name in ("triggers", "gates", "effects", "queries"):
                full_config[collection_name].extend(
                    dict(entry) for entry in getattr(composition, collection_name)
                )
            full_config["edges"].extend(dict(edge) for edge in composition.edges)
            combined_graphs.extend(
                graph.model_dump(mode="json")
                for graph in self._workflow_editor_graphs_for_config(
                    config=pack_config,
                    graphs=[graph.model_dump(mode="json") for graph in composition.graphs],
                )
            )

        self._validate_workflow_editor_document(
            config=full_config,
            layout={},
            graphs=combined_graphs,
            base_config={},
        )

    def _write_file_bytes_atomic(self, path: Path, encoded: bytes) -> None:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(encoded)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        try:
            os.replace(temp_path, path)
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _write_json_file_atomic(self, path: Path, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        self._write_file_bytes_atomic(path, encoded)

    def _read_composed_workflow_editor_document(self) -> WorkflowEditorDocumentPayload:
        config_path = self._worker_global_config_path("trigger-orchestrator")
        if config_path is None:
            raise ValueError("Unknown worker: trigger-orchestrator")
        config_exists = config_path.exists()
        if config_exists:
            raw_config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(raw_config, dict):
                raise ValueError(f"worker config file {config_path} must contain a JSON object")
        else:
            model = _WORKER_GLOBAL_CONFIG_MODELS["trigger-orchestrator"]
            raw_config = model.model_validate(self.config.model_dump(mode="json")).model_dump(
                mode="json", exclude_none=True
            )
        composed_config = self._compose_workflow_editor_config(raw_config)
        composed_config = self._strip_system_managed_workflows(composed_config)
        normalized_candidate = self._normalize_workflow_editor_config(composed_config)
        normalized_config = self._validate_worker_global_config_payload(
            "trigger-orchestrator", normalized_candidate
        )
        layout_path = self._workflow_editor_layout_path(raw_config)
        layout_exists = layout_path.exists()
        raw_layout: dict[str, object]
        if layout_exists:
            loaded_layout = json.loads(layout_path.read_text(encoding="utf-8"))
            if not isinstance(loaded_layout, dict):
                raise ValueError(f"workflow layout file {layout_path} must contain a JSON object")
            raw_layout = loaded_layout
        else:
            raw_layout = {}
        layout = self._validate_workflow_editor_layout(
            raw_layout,
            node_ids=self._collect_workflow_node_ids(normalized_config),
            allow_unknown_nodes=True,
        )
        graphs = self._workflow_editor_graphs_for_config(
            config=normalized_config,
            graphs=self._workflow_editor_stored_graphs(raw_config),
        )
        self._load_workflow_provider_output_schemas(normalized_config)
        output_schemas = derive_workflow_output_schemas(normalized_config)
        saved_at = (
            datetime.fromtimestamp(config_path.stat().st_mtime, tz=UTC) if config_exists else None
        )
        revision = self._workflow_editor_revision(
            config=normalized_config,
            layout=layout,
            graphs=graphs,
        )
        workflow_packs = self._workflow_editor_pack_descriptors(raw_config)
        document_scope = "composed" if workflow_packs else "inline"
        return WorkflowEditorDocumentPayload(
            config_path=str(config_path),
            layout_path=str(layout_path),
            config_exists=config_exists,
            layout_exists=layout_exists,
            document_scope=document_scope,
            workflow_packs=workflow_packs,
            selected_workflow_pack=None,
            config=normalized_config,
            layout=layout,
            graphs=graphs,
            output_schemas=output_schemas,
            saved_at=saved_at,
            revision=revision,
            pack_list_revision=self._workflow_editor_worker_config_revision(raw_config),
        )

    def _read_workflow_editor_document(
        self,
        workflow_pack: str | None = None,
    ) -> WorkflowEditorDocumentPayload:
        config_path = self._worker_global_config_path("trigger-orchestrator")
        if config_path is None:
            raise ValueError("Unknown worker: trigger-orchestrator")
        config_exists = config_path.exists()
        if config_exists:
            raw_config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(raw_config, dict):
                raise ValueError(f"worker config file {config_path} must contain a JSON object")
        else:
            model = _WORKER_GLOBAL_CONFIG_MODELS["trigger-orchestrator"]
            raw_config = model.model_validate(self.config.model_dump(mode="json")).model_dump(
                mode="json", exclude_none=True
            )

        selected_reference = self._workflow_editor_selected_pack_reference(
            raw_config,
            workflow_pack,
        )
        if selected_reference is None:
            return self._read_composed_workflow_editor_document()
        if isinstance(raw_config.get("workflow_ir"), dict):
            raise ValueError(
                "Workflow editor cannot edit configs that combine workflow_packs with inline workflow_ir; "
                "edit the pack files directly."
            )

        workflow_packs = self._workflow_editor_pack_descriptors(
            raw_config,
            selected_reference=selected_reference,
        )
        selected_pack = next(
            (
                descriptor
                for descriptor in workflow_packs
                if descriptor.reference == selected_reference
            ),
            None,
        )
        if selected_pack is None:
            raise ValueError(f"Workflow pack is not configured: {selected_reference}")
        if not selected_pack.exists:
            raise ValueError(
                f"workflow pack {selected_reference!r} does not exist at {selected_pack.path}"
            )

        pack_config, pack_graphs = self._workflow_editor_pack_graphs(
            raw_config=raw_config,
            pack_reference=selected_reference,
        )
        normalized_candidate = self._normalize_workflow_editor_config(pack_config)
        normalized_config = self._validate_worker_global_config_payload(
            "trigger-orchestrator", normalized_candidate
        )
        layout_path = Path(selected_pack.layout_path)
        layout_exists = layout_path.exists()
        raw_layout: dict[str, object]
        if layout_exists:
            loaded_layout = json.loads(layout_path.read_text(encoding="utf-8"))
            if not isinstance(loaded_layout, dict):
                raise ValueError(f"workflow layout file {layout_path} must contain a JSON object")
            raw_layout = loaded_layout
        else:
            raw_layout = {}
        layout = self._validate_workflow_editor_layout(
            raw_layout,
            node_ids=self._collect_workflow_node_ids(normalized_config),
            allow_unknown_nodes=True,
        )
        graphs = self._workflow_editor_graphs_for_config(
            config=normalized_config,
            graphs=pack_graphs,
        )
        self._load_workflow_provider_output_schemas(normalized_config)
        output_schemas = derive_workflow_output_schemas(normalized_config)
        pack_path = Path(selected_pack.path)
        saved_at = (
            datetime.fromtimestamp(pack_path.stat().st_mtime, tz=UTC)
            if pack_path.exists()
            else None
        )
        revision = self._workflow_editor_revision(
            config=normalized_config,
            layout=layout,
            graphs=graphs,
        )
        return WorkflowEditorDocumentPayload(
            config_path=str(config_path),
            layout_path=str(layout_path),
            config_exists=config_exists,
            layout_exists=layout_exists,
            document_scope="pack",
            workflow_packs=workflow_packs,
            selected_workflow_pack=selected_pack,
            config=normalized_config,
            layout=layout,
            graphs=graphs,
            output_schemas=output_schemas,
            saved_at=saved_at,
            revision=revision,
            pack_list_revision=self._workflow_editor_worker_config_revision(raw_config),
        )

    def _communication_target_options(
        self, directory_path: str | None, *, purpose: str
    ) -> list[WorkflowEditorFieldOption]:
        normalized_directory = self._normalize_directory_reference(directory_path)
        result = self.directory_admin.compute_effective_config(
            normalized_directory,
            domain=COMMUNICATION_TARGETS_DOMAIN,
        )
        if not result.ok or result.data is None:
            messages = [error.message for error in result.errors]
            raise ValueError(
                "; ".join(messages)
                or f"Communication targets could not be resolved for {normalized_directory!r}."
            )
        targets = result.data.resolution.effective_config.get("targets")
        if not isinstance(targets, list):
            return []

        options: list[WorkflowEditorFieldOption] = []
        for target in targets:
            if not isinstance(target, dict):
                continue
            provider = str(target.get("provider") or "").strip()
            capabilities = set(_communication_target_capabilities(target))
            if purpose == "send-message":
                if provider == "teams" and not capabilities.intersection({"write", "reply"}):
                    continue
                if provider == "discord" and "write" not in capabilities:
                    continue
                if provider == "email" and "write" not in capabilities:
                    continue
                if provider not in {"teams", "discord", "email"}:
                    continue
            elif purpose == "notification":
                if provider not in {"teams", "discord", "email"}:
                    continue
                if "notify" not in capabilities:
                    continue
            elif purpose == "read-message":
                if provider not in {"teams", "discord", "email"}:
                    continue
                if "read" not in capabilities:
                    continue
                if _communication_target_shape(target) == "teams_user":
                    continue
            elif purpose == "team-chat-bridge":
                if provider not in {"teams", "discord", "email"}:
                    continue
                if "read" not in capabilities:
                    continue
                if provider == "discord" and "write" not in capabilities:
                    continue
                if provider in {"teams", "email"} and not capabilities.intersection(
                    {"write", "reply"}
                ):
                    continue
                if _communication_target_shape(target) == "teams_user":
                    continue
            elif purpose == "agent-session":
                if provider not in {"teams", "discord", "email"}:
                    continue
                policy = target.get("policy")
                if (
                    provider == "email"
                    and not str(
                        policy.get("auth_profile_ref") if isinstance(policy, dict) else ""
                    ).strip()
                ):
                    continue
                if provider == "email":
                    if "write" not in capabilities:
                        continue
                elif not capabilities.intersection({"read", "write", "reply"}):
                    continue
                if (
                    _communication_target_shape(target) == "teams_user"
                    and "write" not in capabilities
                ):
                    continue
            else:
                continue
            option = _communication_target_option(target, purpose=purpose)
            if option is not None:
                options.append(option)
        return options

    def _communication_send_target_options(
        self, directory_path: str | None
    ) -> list[WorkflowEditorFieldOption]:
        return self._communication_target_options(directory_path, purpose="send-message")

    def _communication_notification_target_options(
        self, directory_path: str | None
    ) -> list[WorkflowEditorFieldOption]:
        return self._communication_target_options(directory_path, purpose="notification")

    def _communication_read_target_options(
        self, directory_path: str | None
    ) -> list[WorkflowEditorFieldOption]:
        return self._communication_target_options(directory_path, purpose="read-message")

    def _team_chat_bridge_target_options(
        self, directory_path: str | None
    ) -> list[WorkflowEditorFieldOption]:
        return self._communication_target_options(directory_path, purpose="team-chat-bridge")

    def _agent_communication_target_options(
        self, directory_path: str | None
    ) -> list[WorkflowEditorFieldOption]:
        return self._communication_target_options(directory_path, purpose="agent-session")

    def _directory_resource_trigger_options(
        self, directory_path: str | None
    ) -> list[WorkflowEditorFieldOption]:
        normalized_directory = self._normalize_directory_reference(directory_path)
        result = self.directory_admin.compute_effective_config(
            normalized_directory,
            domain="host_directories",
        )
        if not result.ok or result.data is None:
            messages = [error.message for error in result.errors]
            raise ValueError(
                "; ".join(messages)
                or f"Host directories could not be resolved for {normalized_directory!r}."
            )
        configured = HostDirectoryDirectoryConfig.model_validate(
            result.data.resolution.effective_config
        )
        return [
            _field_option(
                directory_id,
                definition.display_name,
                description=definition.description,
            )
            for directory_id, definition in sorted(configured.host_directories.items())
            if not definition.disabled
        ]

    def _read_workflow_editor_catalog(
        self, directory_path: str | None = None
    ) -> WorkflowEditorCatalogPayload:
        skill_options = [_field_option(skill.name) for skill in self.skill_library.list_skills()]
        addon_catalog = self._addon_catalog or load_addons(
            self.config.addon_loader, base_dir=self.project_dir
        )
        tool_options = _tool_field_options(addon_mcp_tool_catalog_payloads(addon_catalog))
        communication_send_target_options = self._communication_send_target_options(directory_path)
        communication_notification_target_options = self._communication_notification_target_options(
            directory_path
        )
        communication_read_target_options = self._communication_read_target_options(directory_path)
        team_chat_bridge_target_options = self._team_chat_bridge_target_options(directory_path)
        agent_communication_target_options = self._agent_communication_target_options(
            directory_path
        )
        host_directory_trigger_options = self._directory_resource_trigger_options(directory_path)
        provider_catalog = self._load_workflow_provider_catalog(
            self._read_worker_provider_loader_config("trigger-orchestrator")
        )
        internal_event_catalog_entries = sorted(
            (
                entry.model_copy(deep=True)
                for entry in provider_catalog.internal_event_catalog_entries
            ),
            key=lambda entry: (entry.source or "", entry.event_type),
        )
        internal_event_options = _internal_event_field_options(internal_event_catalog_entries)
        nodes: list[WorkflowEditorNodeCatalogEntry] = []
        seen_node_types: set[str] = set()
        for entry in _WORKFLOW_EDITOR_NODE_CATALOG:
            node = entry.model_copy(
                deep=True,
                update={
                    "description": (
                        entry.description or _WORKFLOW_EDITOR_NODE_DESCRIPTIONS.get(entry.type)
                    )
                },
            )
            node.fields = [
                field.model_copy(update={"options": tool_options})
                if field.key in {"allowed_tools", "tool_argument_policies", "tools"}
                else field
                for field in node.fields
            ]
            if node.type in {"effect:vm:create", "effect:agent:create"}:
                node.fields = [
                    field.model_copy(update={"options": skill_options})
                    if field.key == "codex_skills"
                    else field.model_copy(update={"options": tool_options})
                    if field.key == "allowed_tools"
                    else field.model_copy(update={"options": agent_communication_target_options})
                    if node.type == "effect:agent:create"
                    and field.key == "communication_target_ids"
                    else field
                    for field in node.fields
                ]
            if node.type == "effect:communication:send-message":
                node.fields = [
                    field.model_copy(update={"options": communication_send_target_options})
                    if field.key == "communication_target_id"
                    else field
                    for field in node.fields
                ]
            if node.type == "effect:notification:send":
                node.fields = [
                    field.model_copy(update={"options": communication_notification_target_options})
                    if field.key == "communication_target_id"
                    else field
                    for field in node.fields
                ]
            if node.type == "effect:session:approve-mcp-tools-for-run":
                node.fields = [
                    field.model_copy(update={"options": communication_notification_target_options})
                    if field.key == "approval_notification_target_ids"
                    else field
                    for field in node.fields
                ]
            if node.type == "query:communication:messages":
                node.fields = [
                    field.model_copy(update={"options": communication_read_target_options})
                    if field.key == "communication_target_id"
                    else field
                    for field in node.fields
                ]
            if node.type == "trigger:communication:new-message":
                node.fields = [
                    field.model_copy(update={"options": communication_read_target_options})
                    if field.key == "communication_target_ids"
                    else field
                    for field in node.fields
                ]
            if node.type == "trigger:host-directory:file-added":
                node.fields = [
                    field.model_copy(update={"options": host_directory_trigger_options})
                    if field.key == "directory_id"
                    else field
                    for field in node.fields
                ]
            if node.type == "trigger:event":
                node.fields = [
                    field.model_copy(update={"options": internal_event_options})
                    if field.key == "event_type"
                    else field
                    for field in node.fields
                ]
            if node.type in seen_node_types:
                raise ValueError(f"Duplicate workflow editor catalog entry: {node.type}")
            seen_node_types.add(node.type)
            nodes.append(node)
        for entry in provider_catalog.workflow_editor_catalog_entries:
            node = entry.model_copy(deep=True)
            if node.type == "effect:team:enqueue-operator-chat-message":
                node.fields = [
                    field.model_copy(update={"options": team_chat_bridge_target_options})
                    if field.key == "communication_target_id"
                    else field
                    for field in node.fields
                ]
            if node.type in seen_node_types:
                raise ValueError(
                    f"Provider workflow editor catalog entry duplicates core node type: {node.type}"
                )
            seen_node_types.add(node.type)
            nodes.append(node)
        return WorkflowEditorCatalogPayload(
            nodes=nodes,
            internal_events=internal_event_catalog_entries,
        )

    def _validate_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        base_config: dict[str, object] | None = None,
        workflow_pack: str | None = None,
    ) -> WorkflowEditorValidationPayload:
        merged_config = self._merge_system_managed_workflows(
            editable_config=config,
            base_config=base_config,
        )
        runtime_validation_config = _append_builtin_hidden_workflows(
            project_dir=self.project_dir,
            payload=merged_config,
        )
        normalized_candidate = self._normalize_workflow_editor_config(runtime_validation_config)
        normalized_config = self._validate_worker_global_config_payload(
            "trigger-orchestrator", normalized_candidate
        )
        compiled_config, diagnostics = compile_worker_global_config_with_diagnostics(
            TriggerOrchestratorWorkerGlobalConfig.model_validate(normalized_config),
            project_dir=self.project_dir,
        )
        normalized_config = _strip_default_edge_fields(
            compiled_config.model_dump(mode="json", exclude_none=True)
        )
        editable_normalized_config = self._strip_system_managed_workflows(normalized_config)
        normalized_layout = self._validate_workflow_editor_layout(
            layout or {},
            node_ids=self._collect_workflow_node_ids(editable_normalized_config),
        )
        graph_source: object = graphs
        if graph_source is None and isinstance(base_config, dict):
            graph_source = self._workflow_editor_stored_graphs(base_config)
        normalized_graphs = self._workflow_editor_graphs_for_config(
            config=editable_normalized_config,
            graphs=graph_source,
            strict=graphs is not None,
        )
        self._load_workflow_provider_output_schemas(editable_normalized_config)
        output_schemas = derive_workflow_output_schemas(editable_normalized_config)
        binding_diagnostics = _workflow_binding_diagnostics_for_graphs(
            editable_normalized_config,
            normalized_graphs,
        )
        agent_create_diagnostics = _workflow_agent_create_diagnostics(
            editable_normalized_config,
        )
        validation = WorkflowEditorValidationPayload(
            config=editable_normalized_config,
            layout=normalized_layout,
            graphs=normalized_graphs,
            output_schemas=output_schemas,
            validation_diagnostics=[
                diagnostic.model_dump(mode="json", exclude_none=True) for diagnostic in diagnostics
            ]
            + [
                diagnostic.model_dump(mode="json", exclude_none=True)
                for diagnostic in binding_diagnostics
            ]
            + [
                diagnostic.model_dump(mode="json", exclude_none=True)
                for diagnostic in agent_create_diagnostics
            ],
        )
        if workflow_pack is not None and str(workflow_pack).strip():
            config_path = self._worker_global_config_path("trigger-orchestrator")
            if config_path is None:
                raise ValueError("Unknown worker: trigger-orchestrator")
            raw_config: dict[str, object] = {}
            if config_path.exists():
                loaded_config = json.loads(config_path.read_text(encoding="utf-8"))
                if not isinstance(loaded_config, dict):
                    raise ValueError(f"worker config file {config_path} must contain a JSON object")
                raw_config = loaded_config
            selected_reference = self._workflow_editor_selected_pack_reference(
                raw_config,
                workflow_pack,
            )
            workflow_packs = raw_config.get("workflow_packs")
            if selected_reference is not None and isinstance(workflow_packs, list):
                self._validate_selected_workflow_pack_runtime_config(
                    raw_config=raw_config,
                    workflow_packs=workflow_packs,
                    selected_reference=selected_reference,
                    selected_validation=validation,
                )
        return validation

    def _save_workflow_editor_document_unlocked(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        workflow_pack: str | None = None,
        actor: WorkflowPackChangeActor | None = None,
    ) -> WorkflowEditorSavePayload:
        config_path = self._worker_global_config_path("trigger-orchestrator")
        if config_path is None:
            raise ValueError("Unknown worker: trigger-orchestrator")
        raw_config: dict[str, object] = {}
        if config_path.exists():
            loaded_config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(loaded_config, dict):
                raise ValueError(f"worker config file {config_path} must contain a JSON object")
            raw_config = loaded_config

        selected_reference = self._workflow_editor_selected_pack_reference(
            raw_config,
            workflow_pack,
        )
        pack_save = self._workflow_editor_pack_backed_save_paths(raw_config, workflow_pack)
        if selected_reference is None and pack_save is not None:
            _, configured_workflow_packs = pack_save
            if configured_workflow_packs:
                selected_reference = self._workflow_editor_pack_reference(
                    configured_workflow_packs[0]
                )
        workflow_packs = self._workflow_editor_pack_descriptors(
            raw_config,
            selected_reference=selected_reference,
        )
        selected_pack = (
            next(
                (
                    descriptor
                    for descriptor in workflow_packs
                    if descriptor.reference == selected_reference
                ),
                None,
            )
            if selected_reference is not None
            else None
        )
        validation = self._validate_workflow_editor_document(
            config=config,
            layout=layout,
            graphs=graphs,
            base_config=None if selected_reference is not None else raw_config,
        )
        merged_config = self._merge_system_managed_workflows(
            editable_config=validation.config,
            base_config=raw_config,
        )
        if selected_reference is not None:
            existing_editable_config = self._read_workflow_editor_document(
                workflow_pack=selected_reference,
            ).config
        else:
            existing_editable_config = self._strip_system_managed_workflows(
                self._compose_workflow_editor_config(raw_config)
            )
        self._seed_new_trigger_cursors(
            previous_config=existing_editable_config,
            next_config=validation.config,
        )
        layout_path = (
            self._workflow_editor_pack_layout_path(selected_reference)
            if selected_reference is not None
            else self._workflow_editor_layout_path(raw_config)
        )
        config_path.parent.mkdir(parents=True, exist_ok=True)
        layout_path.parent.mkdir(parents=True, exist_ok=True)
        if selected_reference is not None:
            _, stored_graphs = self._workflow_editor_pack_graphs(
                raw_config=raw_config,
                pack_reference=selected_reference,
            )
        else:
            stored_graphs = self._workflow_editor_stored_graphs(raw_config)
        graphs_to_persist = validation.graphs if graphs is not None or stored_graphs else None
        graph_payloads_to_persist = (
            self._workflow_editor_graph_payloads_to_persist(
                raw_graphs=stored_graphs,
                normalized_graphs=graphs_to_persist,
            )
            if graphs_to_persist is not None
            else None
        )
        history_before_files: list[WorkflowPackHistoryFileState] | None = None
        if pack_save is None:
            if graph_payloads_to_persist is not None:
                merged_config["workflow_graphs"] = graph_payloads_to_persist
            else:
                merged_config.pop("workflow_graphs", None)
            self._write_json_file_atomic(config_path, merged_config)
        else:
            pack_path, workflow_packs = pack_save
            pack_path.parent.mkdir(parents=True, exist_ok=True)
            if selected_reference is None:
                selected_reference = self._workflow_editor_pack_reference(workflow_packs[0])
            self._validate_selected_workflow_pack_runtime_config(
                raw_config=raw_config,
                workflow_packs=workflow_packs,
                selected_reference=selected_reference,
                selected_validation=validation,
            )
            history_before_files = self._workflow_pack_history_files(
                config_path=config_path,
                pack_path=pack_path,
                layout_path=layout_path,
            )
            pack_payload = self._workflow_editor_pack_payload_to_persist(
                pack_path=pack_path,
                validated_config=merged_config,
                graph_payloads=graph_payloads_to_persist,
            )
            self._write_json_file_atomic(pack_path, pack_payload)
        self._write_json_file_atomic(layout_path, validation.layout)
        if (
            pack_save is not None
            and selected_reference is not None
            and history_before_files is not None
        ):
            history_after_files = self._workflow_pack_history_files(
                config_path=config_path,
                pack_path=pack_path,
                layout_path=layout_path,
            )
            self._record_workflow_pack_history(
                operation="save",
                workflow_pack=selected_reference,
                actor=actor,
                before_files=history_before_files,
                after_files=history_after_files,
            )
        saved_path = pack_path if pack_save is not None else config_path
        saved_at = datetime.fromtimestamp(saved_path.stat().st_mtime, tz=UTC)
        revision = self._workflow_editor_revision(
            config=validation.config,
            layout=validation.layout,
            graphs=validation.graphs,
        )
        pack_list_revision = self._workflow_editor_worker_config_revision(
            merged_config if pack_save is None else raw_config
        )
        if selected_reference is not None:
            workflow_packs = self._workflow_editor_pack_descriptors(
                raw_config,
                selected_reference=selected_reference,
            )
            selected_pack = next(
                (
                    descriptor
                    for descriptor in workflow_packs
                    if descriptor.reference == selected_reference
                ),
                selected_pack,
            )
        return WorkflowEditorSavePayload(
            config_path=str(config_path),
            layout_path=str(layout_path),
            document_scope="pack" if selected_reference is not None else "inline",
            workflow_packs=workflow_packs if selected_reference is not None else [],
            selected_workflow_pack=selected_pack,
            config=validation.config,
            layout=validation.layout,
            graphs=validation.graphs,
            output_schemas=validation.output_schemas,
            validation_diagnostics=validation.validation_diagnostics,
            saved_at=saved_at,
            revision=revision,
            pack_list_revision=pack_list_revision,
        )

    def _save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        expected_revision: str | None = None,
        workflow_pack: str | None = None,
        actor: WorkflowPackChangeActor | None = None,
    ) -> WorkflowEditorSavePayload:
        with self.admin_config_mutation_lock():
            if expected_revision is not None:
                current = self._read_workflow_editor_document(workflow_pack=workflow_pack)
                if current.revision != expected_revision:
                    raise AdminRevisionConflict(
                        "Workflow editor document changed before save. Reload the workflow before saving again.",
                        details={
                            "current_revision": current.revision,
                            "current": current.model_dump(mode="json"),
                        },
                    )
            return self._save_workflow_editor_document_unlocked(
                config=config,
                layout=layout,
                graphs=graphs,
                workflow_pack=workflow_pack,
                actor=actor,
            )

    def _seed_new_ticket_created_trigger_cursors(
        self, *, previous_config: dict[str, object], next_config: dict[str, object]
    ) -> None:
        runtime_dir = _resolve_path_setting(
            base_dir=self.project_dir,
            value=next_config.get("runtime_dir"),
            fallback=self.config.runtime_dir,
        )
        seed_new_ticket_created_trigger_cursors(
            runtime_dir=runtime_dir,
            previous_config=previous_config,
            next_config=next_config,
        )

    def _seed_new_trigger_cursors(
        self, *, previous_config: dict[str, object], next_config: dict[str, object]
    ) -> None:
        self._seed_new_ticket_created_trigger_cursors(
            previous_config=previous_config,
            next_config=next_config,
        )
        self._seed_new_message_trigger_cursors(
            previous_config=previous_config,
            next_config=next_config,
        )

    def _seed_new_message_trigger_cursors(
        self, *, previous_config: dict[str, object], next_config: dict[str, object]
    ) -> None:
        runtime_dir = _resolve_path_setting(
            base_dir=self.project_dir,
            value=next_config.get("runtime_dir"),
            fallback=self.config.runtime_dir,
        )
        seed_new_message_trigger_cursors(
            runtime_dir=runtime_dir,
            previous_config=previous_config,
            next_config=next_config,
            teams_meeting_store_path=_resolve_path_setting(
                base_dir=self.project_dir,
                value=next_config.get("teams_meeting_store_path"),
                fallback=self.config.teams_meeting_store_path,
            ),
        )

    def _clear_workflow_run(self, event_id: str) -> WorkflowRunClearPayload:
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            raise ValueError("Workflow run event_id is required.")

        stores = self._workflow_run_store_paths()
        event_queue_store_path = stores["event_queue_store_path"]
        effect_execution_store_path = stores["effect_execution_store_path"]
        gate_execution_store_path = stores["gate_execution_store_path"]
        await_agent_store_path = stores["await_agent_store_path"]
        legacy_await_agent_store_path = stores["legacy_await_agent_store_path"]

        cleared_at = _utc_now()
        clear_reason = "manual_clear_stale_workflow_runs"
        clear_error = "Manually cleared stale workflow run from editor state."

        if self._workflow_uses_sqlite_store(stores):
            queue_store, effect_store, gate_store = self._workflow_sqlite_store_bundle(stores)
            queue_records_updated = queue_store.mark_event_cleared(
                normalized_event_id,
                cleared_at=cleared_at,
                clear_reason=clear_reason,
                error=clear_error,
            )
            effect_records_updated = effect_store.clear_event_records(
                normalized_event_id,
                cleared_at=cleared_at,
                clear_reason=clear_reason,
                clear_error=clear_error,
            )
            gate_records_updated = gate_store.clear_event_records(
                normalized_event_id,
                cleared_at=cleared_at,
                clear_reason=clear_reason,
                clear_error=clear_error,
            )
        else:

            def _clear_queue_records(records: dict[str, TriggerQueueRecord]) -> int:
                updated = 0
                for record_key, record in records.items():
                    record_event_id = str(record.event_id or record_key).strip()
                    if record_event_id != normalized_event_id:
                        continue
                    if record.delivery_state == "delivered":
                        continue
                    record.delivery_state = "delivered"
                    record.delivered_at = cleared_at
                    record.clear_reason = clear_reason
                    record.error = clear_error
                    records[record_key] = record
                    updated += 1
                return updated

            from evander.daemons.effect_orchestrator import (
                JsonExecutionStore,
                JsonGateExecutionStore,
            )

            queue_records_updated = JsonStore(
                event_queue_store_path, TriggerQueueRecord
            ).update_all(_clear_queue_records)

            def _clear_execution_records(records: dict[str, Any]) -> int:
                updated = 0
                for execution_id, record in records.items():
                    record_event_id = str(getattr(record, "event_id", execution_id) or "").strip()
                    if record_event_id != normalized_event_id:
                        continue
                    status = str(getattr(record, "status", "") or "").strip()
                    if status == "failed":
                        record.dismissed_at = cleared_at
                        record.dismiss_reason = "manual_dismiss_errored_workflow_run"
                        records[execution_id] = record
                        updated += 1
                        continue
                    if status not in {"running", "waiting", "completion_pending"}:
                        continue
                    record.status = "failed"
                    record.finished_at = cleared_at
                    record.cleared_at = cleared_at
                    record.clear_reason = clear_reason
                    record.error = clear_error
                    record.dismissed_at = cleared_at
                    record.dismiss_reason = "manual_clear_stale_workflow_runs"
                    records[execution_id] = record
                    updated += 1
                return updated

            effect_records_updated = JsonExecutionStore(effect_execution_store_path).update_all(
                _clear_execution_records
            )
            gate_records_updated = JsonGateExecutionStore(gate_execution_store_path).update_all(
                _clear_execution_records
            )

        await_agent_records_removed = 0
        await_agent_payload = _read_json_object(await_agent_store_path)
        matching_await_keys = [
            record_key
            for record_key, raw_record in await_agent_payload.items()
            if isinstance(raw_record, dict)
            and (
                (_coerce_optional_string(raw_record.get("prompt_event_id")) or record_key)
                == normalized_event_id
            )
        ]
        for record_key in matching_await_keys:
            del await_agent_payload[record_key]
            await_agent_records_removed += 1

        legacy_await_agent_payload = _read_json_object(legacy_await_agent_store_path)
        legacy_matching_await_keys = [
            record_key
            for record_key, raw_record in legacy_await_agent_payload.items()
            if isinstance(raw_record, dict)
            and (
                (_coerce_optional_string(raw_record.get("prompt_event_id")) or record_key)
                == normalized_event_id
            )
        ]
        for record_key in legacy_matching_await_keys:
            del legacy_await_agent_payload[record_key]
            await_agent_records_removed += 1

        if (
            queue_records_updated == 0
            and effect_records_updated == 0
            and gate_records_updated == 0
            and await_agent_records_removed == 0
        ):
            raise ValueError(f"Workflow run not found or already inactive: {normalized_event_id}")

        await_agent_store_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json_file_atomic(await_agent_store_path, await_agent_payload)
        if legacy_await_agent_store_path.exists() or legacy_await_agent_payload:
            legacy_await_agent_store_path.parent.mkdir(parents=True, exist_ok=True)
            self._write_json_file_atomic(legacy_await_agent_store_path, legacy_await_agent_payload)
        self._workflow_event_journal().append(
            "workflow.run.cleared",
            source="workflow_editor",
            subject_id=normalized_event_id,
            payload={
                "queue_records_updated": queue_records_updated,
                "effect_records_updated": effect_records_updated,
                "gate_records_updated": gate_records_updated,
                "await_agent_records_removed": await_agent_records_removed,
            },
        )
        return WorkflowRunClearPayload(
            event_id=normalized_event_id,
            cleared_at=cleared_at,
            queue_records_updated=queue_records_updated,
            effect_records_updated=effect_records_updated,
            gate_records_updated=gate_records_updated,
            await_agent_records_removed=await_agent_records_removed,
        )

    def _dismiss_workflow_node_failures(self, node_id: str) -> WorkflowNodeFailuresDismissPayload:
        normalized_node_id = str(node_id or "").strip()
        if not normalized_node_id:
            raise ValueError("Workflow node_id is required.")

        stores = self._workflow_run_store_paths()
        dismissed_at = _utc_now()
        dismiss_reason = "manual_dismiss_node_failures"

        def _dismiss_failed_records(records: dict[str, Any], *, node_id_field: str) -> int:
            updated = 0
            for execution_id, record in records.items():
                record_node_id = str(getattr(record, node_id_field, "") or "").strip()
                if record_node_id != normalized_node_id:
                    continue
                status = str(getattr(record, "status", "") or "").strip()
                if status != "failed" or getattr(record, "dismissed_at", None):
                    continue
                setattr(record, "dismissed_at", dismissed_at)
                setattr(record, "dismiss_reason", dismiss_reason)
                records[execution_id] = record
                updated += 1
            return updated

        def _dismiss_store(store: Any, *, node_id_field: str) -> int:
            dismiss_failed_records_for_node = getattr(
                store, "dismiss_failed_records_for_node", None
            )
            if callable(dismiss_failed_records_for_node):
                return dismiss_failed_records_for_node(
                    normalized_node_id,
                    dismissed_at=dismissed_at,
                    dismiss_reason=dismiss_reason,
                )
            return store.update_all(
                lambda records: _dismiss_failed_records(records, node_id_field=node_id_field)
            )

        if self._workflow_uses_sqlite_store(stores):
            _, effect_store, gate_store = self._workflow_sqlite_store_bundle(stores)
        else:
            from evander.daemons.effect_orchestrator import (
                JsonExecutionStore,
                JsonGateExecutionStore,
            )

            effect_store = JsonExecutionStore(stores["effect_execution_store_path"])
            gate_store = JsonGateExecutionStore(stores["gate_execution_store_path"])

        effect_records_updated = _dismiss_store(effect_store, node_id_field="effect_id")
        gate_records_updated = _dismiss_store(gate_store, node_id_field="gate_id")

        if effect_records_updated or gate_records_updated:
            self._workflow_event_journal().append(
                "workflow.node_failures.dismissed",
                source="workflow_editor",
                subject_id=normalized_node_id,
                payload={
                    "effect_records_updated": effect_records_updated,
                    "gate_records_updated": gate_records_updated,
                },
            )

        return WorkflowNodeFailuresDismissPayload(
            node_id=normalized_node_id,
            dismissed_at=dismissed_at,
            effect_records_updated=effect_records_updated,
            gate_records_updated=gate_records_updated,
        )

    def _resume_workflow_run(self, event_id: str) -> WorkflowRunResumePayload:
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            raise ValueError("Workflow run event_id is required.")

        stores = self._workflow_run_store_paths()
        event_queue_store_path = stores["event_queue_store_path"]
        effect_execution_store_path = stores["effect_execution_store_path"]
        gate_execution_store_path = stores["gate_execution_store_path"]

        queue_store = None
        if self._workflow_uses_sqlite_store(stores):
            queue_store, effect_store, gate_store = self._workflow_sqlite_store_bundle(stores)
            failed_record = self._latest_resumable_failed_workflow_record(
                event_id=normalized_event_id,
                effect_payload={
                    record.execution_id: record.model_dump(mode="json")
                    for record in effect_store.list_records_for_event(
                        normalized_event_id, statuses={"failed"}
                    )
                },
                gate_payload={
                    record.execution_id: record.model_dump(mode="json")
                    for record in gate_store.list_records_for_event(
                        normalized_event_id, statuses={"failed"}
                    )
                },
            )
        else:
            failed_record = self._latest_resumable_failed_workflow_record(
                event_id=normalized_event_id,
                effect_payload=_read_json_object(effect_execution_store_path),
                gate_payload=_read_json_object(gate_execution_store_path),
            )
        if failed_record is None:
            raise ValueError(f"Workflow run is not resumable: {normalized_event_id}")

        resumed_at = _utc_now()
        source_node_id = str(failed_record["source_node_id"])
        source_trigger_type = str(failed_record["source_trigger_type"])
        target_node_id = str(failed_record["node_id"])
        input_payload = dict(failed_record["input_payload"])
        input_payload["__resume_target_node_id"] = target_node_id
        input_payload["__resume_source_event_id"] = normalized_event_id
        resume_event_id = f"{normalized_event_id}:resume:{uuid4().hex[:12]}"

        resume_event = TriggerEvent(
            event_id=resume_event_id,
            trigger_id=source_node_id,
            trigger_type=source_trigger_type,
            trace_id=str(failed_record["trace_id"]),
            occurred_at=resumed_at,
            payload=input_payload,
        )
        if queue_store is not None:
            queue_store.publish(resume_event)
        else:
            TriggerEventQueue(event_queue_store_path).publish(resume_event)
        notify_wake_socket_for_lock(stores["effect_lock_path"])
        self._workflow_event_journal().append(
            "workflow.run.resumed",
            source="workflow_editor",
            subject_id=normalized_event_id,
            payload={
                "resume_event_id": resume_event_id,
                "source_node_id": source_node_id,
                "target_node_id": target_node_id,
            },
        )
        return WorkflowRunResumePayload(
            event_id=normalized_event_id,
            resumed_at=resumed_at,
            resume_event_id=resume_event_id,
            source_node_id=source_node_id,
            target_node_id=target_node_id,
        )

    def _workflow_run_store_paths(self) -> dict[str, Any]:
        worker_payload = self._read_worker_global_config("trigger-orchestrator")
        worker_config = worker_payload.config
        runtime_dir = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("runtime_dir"),
            fallback=self.config.runtime_dir,
        )
        backend = str(worker_config.get("orchestration_store_backend") or "sqlite").strip().lower()
        if backend not in {"sqlite", "json"}:
            backend = "sqlite"
        backend_explicit = False
        try:
            worker_config_path = Path(worker_payload.path)
            if worker_config_path.exists():
                raw_worker_config = json.loads(worker_config_path.read_text(encoding="utf-8"))
                backend_explicit = (
                    isinstance(raw_worker_config, dict)
                    and "orchestration_store_backend" in raw_worker_config
                )
        except (OSError, json.JSONDecodeError):
            backend_explicit = False
        orchestration_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("orchestration_store_path"),
            fallback=runtime_dir / "orchestration.db",
        )
        event_queue_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("event_queue_store_path"),
            fallback=runtime_dir / "trigger-orchestrator-events.json",
        )
        effect_execution_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("effect_execution_store_path"),
            fallback=runtime_dir / "effect-orchestrator-executions.json",
        )
        gate_execution_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("gate_execution_store_path"),
            fallback=runtime_dir / "gate-orchestrator-executions.json",
        )
        workflow_config_snapshot_store_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("workflow_config_snapshot_store_path"),
            fallback=runtime_dir / "workflow-config-snapshots.json",
        )
        effect_lock_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("effect_lock_path"),
            fallback=runtime_dir / "effect-orchestrator.lock",
        )
        if (
            backend == "sqlite"
            and not backend_explicit
            and not orchestration_store_path.exists()
            and (
                event_queue_store_path.exists()
                or effect_execution_store_path.exists()
                or gate_execution_store_path.exists()
            )
        ):
            backend = "json"
        return {
            "runtime_dir": runtime_dir,
            "orchestration_store_backend": backend,
            "orchestration_store_path": orchestration_store_path,
            "orchestration_retention_days": int(
                worker_config.get("orchestration_retention_days")
                if worker_config.get("orchestration_retention_days") is not None
                else 30
            ),
            "event_queue_store_path": event_queue_store_path,
            "effect_execution_store_path": effect_execution_store_path,
            "gate_execution_store_path": gate_execution_store_path,
            "effect_lock_path": effect_lock_path,
            "workflow_config_snapshot_store_path": workflow_config_snapshot_store_path,
            "await_agent_store_path": runtime_dir
            / "providers"
            / "gates"
            / "await-agent"
            / "pending.json",
            "legacy_await_agent_store_path": runtime_dir / "await-agent-pending.json",
        }

    def _workflow_uses_sqlite_store(self, stores: Mapping[str, Any]) -> bool:
        return str(stores.get("orchestration_store_backend") or "").lower() == "sqlite"

    def _workflow_sqlite_store_bundle(self, stores: Mapping[str, Any]) -> tuple[Any, Any, Any]:
        from evander.daemons.effect_orchestrator import (
            SqliteExecutionStore,
            SqliteGateExecutionStore,
        )

        db_path = Path(stores["orchestration_store_path"])
        raw_retention_days = stores.get("orchestration_retention_days")
        retention_days = int(raw_retention_days if raw_retention_days is not None else 30)
        journal = SqliteWorkflowEventJournal(db_path, retention_days=retention_days)
        return (
            SqliteTriggerEventQueue(db_path, journal=journal, retention_days=retention_days),
            SqliteExecutionStore(db_path, journal=journal, retention_days=retention_days),
            SqliteGateExecutionStore(db_path, journal=journal, retention_days=retention_days),
        )

    def _latest_resumable_failed_workflow_record(
        self,
        *,
        event_id: str,
        effect_payload: dict[str, object],
        gate_payload: dict[str, object],
    ) -> dict[str, object] | None:
        candidates: list[dict[str, object]] = []

        def collect(records: dict[str, object], *, node_id_field: str) -> None:
            for record_key, raw_record in records.items():
                if not isinstance(raw_record, dict):
                    continue
                if raw_record.get("dismissed_at"):
                    continue
                if str(raw_record.get("event_id") or record_key).strip() != event_id:
                    continue
                if str(raw_record.get("status") or "").strip() != "failed":
                    continue
                source_node_id = _coerce_optional_string(raw_record.get("source_node_id"))
                source_trigger_type = _coerce_optional_string(raw_record.get("source_trigger_type"))
                node_id = _coerce_optional_string(raw_record.get(node_id_field))
                trace_id = _coerce_optional_string(raw_record.get("trace_id"))
                input_payload = raw_record.get("input_payload")
                if (
                    source_node_id is None
                    or source_trigger_type is None
                    or node_id is None
                    or trace_id is None
                    or not isinstance(input_payload, dict)
                ):
                    continue
                finished_at = _coerce_optional_datetime(raw_record.get("finished_at"))
                started_at = _coerce_optional_datetime(raw_record.get("started_at"))
                candidates.append(
                    {
                        "node_id": node_id,
                        "source_node_id": source_node_id,
                        "source_trigger_type": source_trigger_type,
                        "trace_id": trace_id,
                        "input_payload": dict(input_payload),
                        "finished_at": finished_at,
                        "started_at": started_at,
                    }
                )

        collect(effect_payload, node_id_field="effect_id")
        collect(gate_payload, node_id_field="gate_id")
        if not candidates:
            return None
        candidates.sort(
            key=lambda item: (
                item.get("finished_at")
                or item.get("started_at")
                or datetime.min.replace(tzinfo=UTC),
                str(item.get("node_id") or ""),
            ),
            reverse=True,
        )
        return candidates[0]

    def _workflow_run_store_bundle(self, stores: Mapping[str, Any]) -> tuple[Any, Any, Any]:
        if self._workflow_uses_sqlite_store(stores):
            return self._workflow_sqlite_store_bundle(stores)
        from evander.daemons.effect_orchestrator import (
            JsonExecutionStore,
            JsonGateExecutionStore,
        )

        return (
            TriggerEventQueue(Path(stores["event_queue_store_path"])),
            JsonExecutionStore(Path(stores["effect_execution_store_path"])),
            JsonGateExecutionStore(Path(stores["gate_execution_store_path"])),
        )

    def _workflow_run_event_records(self, stores: Mapping[str, Any]) -> dict[str, Any]:
        if self._workflow_uses_sqlite_store(stores):
            queue_store, _, _ = self._workflow_sqlite_store_bundle(stores)
            return {
                record.event_id: record.model_dump(mode="json")
                for record in queue_store.read_all().values()
            }
        return JsonTriggerEventRepository().read_events(stores["event_queue_store_path"])

    def _workflow_config_snapshot_repository(
        self, stores: Mapping[str, Any]
    ) -> JsonWorkflowConfigSnapshotRepository | SqliteWorkflowConfigSnapshotRepository:
        if self._workflow_uses_sqlite_store(stores):
            return SqliteWorkflowConfigSnapshotRepository(Path(stores["orchestration_store_path"]))
        return JsonWorkflowConfigSnapshotRepository(
            Path(stores["workflow_config_snapshot_store_path"])
        )

    def _workflow_event_journal(self) -> WorkflowEventJournal | SqliteWorkflowEventJournal:
        stores = self._workflow_run_store_paths()
        if self._workflow_uses_sqlite_store(stores):
            raw_retention_days = stores.get("orchestration_retention_days")
            return SqliteWorkflowEventJournal(
                stores["orchestration_store_path"],
                retention_days=int(raw_retention_days if raw_retention_days is not None else 30),
            )
        worker_config = self._read_worker_global_config("trigger-orchestrator").config
        runtime_dir = stores["runtime_dir"]
        journal_path = _resolve_path_setting(
            base_dir=self.project_dir,
            value=worker_config.get("workflow_event_journal_path"),
            fallback=workflow_event_journal_path(runtime_dir),
        )
        return WorkflowEventJournal(journal_path)

    def _collect_workflow_node_ids(self, config: dict[str, object]) -> set[str]:
        node_ids: set[str] = set()
        for collection_name, adapter in (
            ("triggers", _TRIGGER_DEFINITION_ADAPTER),
            ("gates", _GATE_DEFINITION_ADAPTER),
            ("effects", _EFFECT_DEFINITION_ADAPTER),
            ("queries", _QUERY_DEFINITION_ADAPTER),
        ):
            entries = config.get(collection_name, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                node = adapter.validate_python(entry)
                node_ids.add(node.id)
        return node_ids

    def _normalize_workflow_editor_config(self, payload: dict[str, object]) -> dict[str, object]:
        candidate = json.loads(json.dumps(payload))
        if not isinstance(candidate, dict):
            return payload
        candidate = migrate_agent_prompt_lifecycle_ports(candidate)

        triggers = candidate.get("triggers")
        gates = candidate.get("gates")
        effects = candidate.get("effects")
        queries = candidate.get("queries")
        edges = candidate.get("edges")
        if triggers is None:
            triggers = []
            candidate["triggers"] = triggers
        if gates is None:
            gates = []
            candidate["gates"] = gates
        if effects is None:
            effects = []
            candidate["effects"] = effects
        if queries is None:
            queries = []
            candidate["queries"] = queries
        if edges is None:
            edges = []
            candidate["edges"] = edges
        if (
            not isinstance(triggers, list)
            or not isinstance(gates, list)
            or not isinstance(effects, list)
            or not isinstance(queries, list)
        ):
            return candidate
        if not isinstance(edges, list):
            return candidate

        self._nest_runtime_outputs_config(candidate)
        self._load_workflow_provider_output_schemas(candidate)
        output_schemas = derive_workflow_output_schemas(candidate)
        for gate in gates:
            if not isinstance(gate, dict):
                continue
            gate_type = str(gate.get("type") or "").strip()
            gate_id = str(gate.get("id") or "").strip()
            if gate_type == "gate:await-agent":
                gate.pop("completion_trigger_id", None)
            if gate_type in {
                "gate:filter",
                "gate:debounce",
                "gate:buffer",
                "gate:deserialize",
                "gate:serialize",
                "gate:map",
                "gate:aggregate",
                "gate:dedupe",
                "gate:switch",
                "gate:await-agent",
                "gate:for",
                "gate:while",
                "gate:merge",
            }:
                gate["outputs"] = output_schemas.get(gate_id, {})

        return candidate

    def _nest_runtime_outputs_config(self, config: dict[str, object]) -> None:
        for collection_name in ("triggers", "effects", "queries"):
            entries = config.get(collection_name)
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or "outputs" not in entry:
                    continue
                outputs = entry.get("outputs")
                if _looks_like_declared_output_schema(outputs):
                    continue
                nested_config = entry.get("config")
                if not isinstance(nested_config, dict):
                    nested_config = {}
                    entry["config"] = nested_config
                nested_config.setdefault("outputs", outputs)
                entry.pop("outputs", None)

    def _validate_workflow_editor_layout(
        self,
        payload: dict[str, object],
        *,
        node_ids: set[str],
        allow_unknown_nodes: bool = False,
    ) -> dict[str, object]:
        candidate = dict(payload)
        if "nodes" not in candidate:
            candidate = {"nodes": candidate.get("nodes", {}) if payload else {}}
        try:
            document = WorkflowEditorLayoutDocument.model_validate(candidate)
        except ValidationError as exc:
            raise _safe_validation_error(exc, context="Invalid workflow editor layout") from exc
        normalized_nodes: dict[str, dict[str, float]] = {}
        unknown_node_ids: list[str] = []
        for node_id, position in document.nodes.items():
            if node_id not in node_ids:
                if allow_unknown_nodes:
                    continue
                unknown_node_ids.append(node_id)
                continue
            normalized_nodes[node_id] = {"x": position.x, "y": position.y}
        if unknown_node_ids:
            unknown_list = ", ".join(sorted(unknown_node_ids))
            raise ValueError(f"Workflow layout references unknown node IDs: {unknown_list}")
        return {"nodes": normalized_nodes}

    def _strip_system_managed_workflows(self, config: dict[str, object]) -> dict[str, object]:
        candidate = json.loads(json.dumps(config))
        if not isinstance(candidate, dict):
            return config
        system_ids: set[str] = set()
        for collection_name in ("triggers", "gates", "effects", "queries"):
            entries = candidate.get(collection_name)
            if not isinstance(entries, list):
                continue
            candidate[collection_name] = [
                entry
                for entry in entries
                if not (
                    isinstance(entry, dict)
                    and _is_system_managed_workflow_node_id(str(entry.get("id") or "").strip())
                )
            ]
            system_ids.update(
                str(entry.get("id") or "").strip()
                for entry in entries
                if isinstance(entry, dict)
                and _is_system_managed_workflow_node_id(str(entry.get("id") or "").strip())
            )
        if isinstance(candidate.get("edges"), list) and system_ids:
            candidate["edges"] = [
                edge
                for edge in candidate["edges"]
                if not (
                    isinstance(edge, dict)
                    and (
                        str(edge.get("source_id") or "").strip() in system_ids
                        or str(edge.get("target_id") or "").strip() in system_ids
                    )
                )
            ]
        return candidate

    def _merge_system_managed_workflows(
        self, *, editable_config: dict[str, object], base_config: dict[str, object] | None
    ) -> dict[str, object]:
        del base_config
        return self._strip_system_managed_workflows(editable_config)

    def _upsert_trigger_config(
        self, trigger_id: str, payload: dict[str, object]
    ) -> TriggerConfigMutationPayload:
        normalized_trigger_id = str(trigger_id).strip()
        if not normalized_trigger_id:
            raise ValueError("trigger_id is required")
        if _is_system_managed_workflow_node_id(normalized_trigger_id):
            raise ValueError(
                f"Trigger is system-managed and cannot be mutated: {normalized_trigger_id}"
            )
        normalized_payload = dict(payload)
        payload_trigger_id = str(normalized_payload.get("id", "")).strip()
        if payload_trigger_id and payload_trigger_id != normalized_trigger_id:
            raise ValueError("trigger_id must match payload.id when payload.id is provided")
        normalized_payload["id"] = normalized_trigger_id
        try:
            validated_trigger = _TRIGGER_DEFINITION_ADAPTER.validate_python(normalized_payload)
        except ValidationError as exc:
            raise _safe_validation_error(exc, context="Invalid trigger config") from exc
        normalized_payload = validated_trigger.model_dump(mode="json", exclude_none=True)

        with self.admin_config_mutation_lock():
            current = self._read_trigger_document()
            config = dict(current.config)
            existing_triggers_raw = config.get("triggers", [])
            if not isinstance(existing_triggers_raw, list):
                raise ValueError(
                    "Invalid trigger-orchestrator config: triggers must be a JSON array."
                )
            existing_triggers = [
                entry for entry in existing_triggers_raw if isinstance(entry, dict)
            ]
            replacing = False
            for index, existing in enumerate(existing_triggers):
                existing_id = str(existing.get("id", "")).strip()
                if existing_id == normalized_trigger_id:
                    existing_triggers[index] = normalized_payload
                    replacing = True
                    break
            if not replacing:
                existing_triggers.append(normalized_payload)
            config["triggers"] = existing_triggers
            write_result = self._write_trigger_document_unlocked(config)
        return TriggerConfigMutationPayload(
            trigger_id=normalized_trigger_id,
            operation="updated" if replacing else "added",
            path=write_result.path,
            config=_workflow_config_for_legacy_mutation_response(write_result.config),
        )

    def _delete_trigger_config(self, trigger_id: str) -> TriggerConfigMutationPayload:
        normalized_trigger_id = str(trigger_id).strip()
        if not normalized_trigger_id:
            raise ValueError("trigger_id is required")
        if _is_system_managed_workflow_node_id(normalized_trigger_id):
            raise ValueError(
                f"Trigger is system-managed and cannot be mutated: {normalized_trigger_id}"
            )
        with self.admin_config_mutation_lock():
            current = self._read_trigger_document()
            config = dict(current.config)
            triggers_raw = config.get("triggers", [])
            if not isinstance(triggers_raw, list):
                raise ValueError(
                    "Invalid trigger-orchestrator config: triggers must be a JSON array."
                )
            filtered = []
            removed = False
            for entry in triggers_raw:
                if not isinstance(entry, dict):
                    continue
                entry_id = str(entry.get("id", "")).strip()
                if entry_id == normalized_trigger_id:
                    removed = True
                    continue
                filtered.append(entry)
            if not removed:
                raise ValueError(f"Trigger not found: {normalized_trigger_id}")
            config["triggers"] = filtered
            write_result = self._write_trigger_document_unlocked(config)
        return TriggerConfigMutationPayload(
            trigger_id=normalized_trigger_id,
            operation="deleted",
            path=write_result.path,
            config=_workflow_config_for_legacy_mutation_response(write_result.config),
        )

    def _upsert_effect_config(
        self, effect_id: str, payload: dict[str, object]
    ) -> EffectConfigMutationPayload:
        normalized_effect_id = str(effect_id).strip()
        if not normalized_effect_id:
            raise ValueError("effect_id is required")
        if _is_system_managed_workflow_node_id(normalized_effect_id):
            raise ValueError(
                f"Effect is system-managed and cannot be mutated: {normalized_effect_id}"
            )
        normalized_payload = dict(payload)
        normalized_source_id = str(normalized_payload.pop("source_id", "")).strip()
        payload_effect_id = str(normalized_payload.get("id", "")).strip()
        if payload_effect_id and payload_effect_id != normalized_effect_id:
            raise ValueError("effect_id must match payload.id when payload.id is provided")
        normalized_payload["id"] = normalized_effect_id
        try:
            validated_effect = _EFFECT_DEFINITION_ADAPTER.validate_python(normalized_payload)
        except ValidationError as exc:
            raise _safe_validation_error(exc, context="Invalid effect config") from exc
        normalized_payload = validated_effect.model_dump(mode="json", exclude_none=True)

        with self.admin_config_mutation_lock():
            current = self._read_worker_global_config_unlocked("trigger-orchestrator")
            config = dict(current.config)
            existing_effects_raw = config.get("effects", [])
            if not isinstance(existing_effects_raw, list):
                raise ValueError(
                    "Invalid trigger-orchestrator config: effects must be a JSON array."
                )
            existing_effects = [entry for entry in existing_effects_raw if isinstance(entry, dict)]
            existing_edges_raw = config.get("edges", [])
            if not isinstance(existing_edges_raw, list):
                existing_edges_raw = []
            existing_edges = [entry for entry in existing_edges_raw if isinstance(entry, dict)]
            replacing = False
            for index, existing in enumerate(existing_effects):
                existing_id = str(existing.get("id", "")).strip()
                if existing_id == normalized_effect_id:
                    existing_effects[index] = normalized_payload
                    replacing = True
                    break
            if not replacing:
                existing_effects.append(normalized_payload)

            filtered_edges: list[dict[str, object]] = []
            for edge in existing_edges:
                target_id = str(edge.get("target_id", "")).strip()
                if target_id == normalized_effect_id:
                    continue
                filtered_edges.append(edge)
            if normalized_source_id:
                filtered_edges.append(
                    {"source_id": normalized_source_id, "target_id": normalized_effect_id}
                )
            config["effects"] = existing_effects
            config["edges"] = filtered_edges
            write_result = self._write_worker_global_config_unlocked("trigger-orchestrator", config)
        return EffectConfigMutationPayload(
            effect_id=normalized_effect_id,
            operation="updated" if replacing else "added",
            path=write_result.path,
            config=_workflow_config_for_legacy_mutation_response(write_result.config),
        )

    def _delete_effect_config(self, effect_id: str) -> EffectConfigMutationPayload:
        normalized_effect_id = str(effect_id).strip()
        if not normalized_effect_id:
            raise ValueError("effect_id is required")
        if _is_system_managed_workflow_node_id(normalized_effect_id):
            raise ValueError(
                f"Effect is system-managed and cannot be mutated: {normalized_effect_id}"
            )
        with self.admin_config_mutation_lock():
            current = self._read_worker_global_config_unlocked("trigger-orchestrator")
            config = dict(current.config)
            effects_raw = config.get("effects", [])
            if not isinstance(effects_raw, list):
                raise ValueError(
                    "Invalid trigger-orchestrator config: effects must be a JSON array."
                )
            filtered = []
            removed = False
            for entry in effects_raw:
                if not isinstance(entry, dict):
                    continue
                entry_id = str(entry.get("id", "")).strip()
                if entry_id == normalized_effect_id:
                    removed = True
                    continue
                filtered.append(entry)
            if not removed:
                raise ValueError(f"Effect not found: {normalized_effect_id}")
            edges_raw = config.get("edges", [])
            filtered_edges = []
            if isinstance(edges_raw, list):
                for entry in edges_raw:
                    if not isinstance(entry, dict):
                        continue
                    source_id = str(entry.get("source_id", "")).strip()
                    target_id = str(entry.get("target_id", "")).strip()
                    if source_id == normalized_effect_id or target_id == normalized_effect_id:
                        continue
                    filtered_edges.append(entry)
            config["effects"] = filtered
            config["edges"] = filtered_edges
            write_result = self._write_worker_global_config_unlocked("trigger-orchestrator", config)
        return EffectConfigMutationPayload(
            effect_id=normalized_effect_id,
            operation="deleted",
            path=write_result.path,
            config=_workflow_config_for_legacy_mutation_response(write_result.config),
        )
