from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import Any

from evander.addons import (
    AddonLoaderConfig,
    AddonRegistration,
    discover_addons,
    load_addons,
    validate_addon_id,
)
from evander.admin.payloads import AdminRevisionConflict
from evander.admin.services.workflow_pack_history_service import WorkflowPackChangeActor
from evander.host_config import HostConfig, host_config_path, load_host_config, save_host_config
from evander.workflow.packs import WorkflowPack, load_workflow_pack

_PROVIDER_COLLECTIONS = (
    "trigger_providers",
    "gate_providers",
    "effect_providers",
    "query_providers",
    "node_definition_resolvers",
)
_RESTART_SERVICES_BASE = (
    "evander-admin-web",
    "evander-trigger-orchestrator",
    "evander-effect-orchestrator",
)


@dataclass(slots=True)
class AddonAdminDependencies:
    config: Any
    project_dir: Any
    workflow_config_admin: Any


@dataclass(slots=True)
class RuntimeWorkflowPackPayload:
    source: str
    reference: str
    payload: dict[str, object]


class AddonAdminService:
    """Admin operations for restart-gated add-on enablement."""

    def __init__(self, dependencies: AddonAdminDependencies) -> None:
        self.config = dependencies.config
        self.project_dir = dependencies.project_dir
        self.workflow_config_admin = dependencies.workflow_config_admin

    def read_addon_state(self) -> dict[str, Any]:
        host_config = self._read_host_config()
        worker_payload = self.workflow_config_admin.read_worker_global_config("trigger-orchestrator")
        host_loader = host_config.addon_loader
        worker_loader = self._worker_addon_loader(worker_payload.config)
        worker_runtime_addon_ids, worker_runtime_loader_error = (
            self._runtime_addon_ids_for_worker_loader(worker_loader)
        )
        configured_node_types, workflow_reference_error = self._configured_workflow_node_types(
            worker_payload.config
        )
        candidates = discover_addons(host_loader, base_dir=self.project_dir)
        loaded_addon_ids, catalog_error = self._loaded_addon_ids()
        enabled_addon_ids = self._explicit_enabled_addon_ids(host_loader, candidates)
        enabled_set = set(enabled_addon_ids)
        loaded_set = set(loaded_addon_ids)

        addons: list[dict[str, Any]] = []
        for candidate in candidates:
            registration = candidate.registration
            addon_id = registration.addon_id if registration is not None else None
            node_types = self._workflow_node_types(registration)
            referenced_node_types = sorted(configured_node_types.intersection(node_types))
            enabled = bool(addon_id and addon_id in enabled_set)
            loaded = bool(addon_id and addon_id in loaded_set)
            pending_enable = enabled and not loaded
            pending_disable = loaded and not enabled
            has_mcp_tools = bool(registration and registration.mcp_tools is not None)
            has_connector_drivers = bool(registration and registration.connector_drivers)
            addons.append(
                {
                    "candidate_key": candidate.source,
                    "addon_id": addon_id,
                    "display_name": registration.display_name if registration else candidate.source,
                    "source": candidate.source,
                    "source_type": candidate.source_type,
                    "entry_point_name": candidate.entry_point_name,
                    "enabled": enabled,
                    "loaded": loaded,
                    "pending_enable": pending_enable,
                    "pending_disable": pending_disable,
                    "restart_required": pending_enable or pending_disable,
                    "configurable": bool(addon_id and candidate.error is None),
                    "has_admin_ui": bool(registration and registration.admin_ui_routes),
                    "has_admin_api": bool(registration and registration.admin_api_routes),
                    "has_mcp_tools": has_mcp_tools,
                    "has_connector_drivers": has_connector_drivers,
                    "has_workflow_providers": bool(registration and registration.workflow_providers),
                    "workflow_node_types": sorted(node_types),
                    "referenced_workflow_node_types": referenced_node_types,
                    "runtime_workflow_pack": self._runtime_workflow_pack_status(
                        registration,
                        worker_payload.config,
                        addon_runtime_enabled=bool(
                            addon_id and addon_id in worker_runtime_addon_ids
                        ),
                        addon_runtime_loader_error=worker_runtime_loader_error,
                    ),
                    "load_error": candidate.error,
                    "required_restart_services": list(
                        self._required_restart_services(
                            has_mcp_tools=has_mcp_tools or has_connector_drivers
                        )
                    ),
                }
            )
        discovered_ids = {
            candidate.registration.addon_id
            for candidate in candidates
            if candidate.registration is not None
        }
        for addon_id in enabled_addon_ids:
            if addon_id in discovered_ids:
                continue
            addons.append(
                {
                    "candidate_key": f"configured:{addon_id}",
                    "addon_id": addon_id,
                    "display_name": addon_id,
                    "source": "enabled_addon_ids",
                    "source_type": "configured",
                    "entry_point_name": None,
                    "enabled": True,
                    "loaded": addon_id in loaded_set,
                    "pending_enable": addon_id not in loaded_set,
                    "pending_disable": False,
                    "restart_required": addon_id not in loaded_set,
                    "configurable": True,
                    "has_admin_ui": False,
                    "has_admin_api": False,
                    "has_mcp_tools": False,
                    "has_connector_drivers": False,
                    "has_workflow_providers": False,
                    "workflow_node_types": [],
                    "referenced_workflow_node_types": [],
                    "runtime_workflow_pack": None,
                    "load_error": "Configured enabled add-on was not discovered.",
                    "required_restart_services": list(
                        self._required_restart_services(has_mcp_tools=False)
                    ),
                }
            )

        restart_required = any(item["restart_required"] for item in addons)
        has_gateway_change = any(
            item["restart_required"]
            and (item["has_mcp_tools"] or item["has_connector_drivers"])
            for item in addons
        )
        return {
            "addon_loader": host_loader.model_dump(mode="json"),
            "worker_addon_loader": worker_loader.model_dump(mode="json"),
            "explicit_enabled_addon_ids": enabled_addon_ids,
            "loaded_addon_ids": loaded_addon_ids,
            "addons": addons,
            "revision": self._revision(host_loader, worker_loader),
            "restart_required": restart_required,
            "required_restart_services": list(
                self._required_restart_services(has_mcp_tools=has_gateway_change)
            )
            if restart_required
            else [],
            "catalog_error": catalog_error,
            "workflow_reference_error": workflow_reference_error,
            "config_mismatch": host_loader.model_dump(mode="json")
            != worker_loader.model_dump(mode="json"),
            "config_paths": {
                "host": str(host_config_path(host_config.runtime_dir)),
                "trigger_orchestrator": worker_payload.path,
            },
        }

    def update_enabled_addons(
        self,
        enabled_addon_ids: list[object],
        *,
        expected_revision: str,
        actor: WorkflowPackChangeActor | None = None,
    ) -> dict[str, Any]:
        normalized_ids = self._normalize_enabled_addon_ids(enabled_addon_ids)
        with self.workflow_config_admin.admin_config_mutation_lock():
            host_config = self._read_host_config()
            worker_payload = self.workflow_config_admin.read_worker_global_config(
                "trigger-orchestrator"
            )
            worker_loader = self._worker_addon_loader(worker_payload.config)
            current_revision = self._revision(host_config.addon_loader, worker_loader)
            if current_revision != expected_revision:
                raise AdminRevisionConflict(
                    "Add-on configuration changed before save. Reload add-ons before saving again.",
                    details={"current_revision": current_revision},
                )

            candidates = discover_addons(host_config.addon_loader, base_dir=self.project_dir)
            known_ids = {
                candidate.registration.addon_id
                for candidate in candidates
                if candidate.registration is not None and candidate.error is None
            }
            unknown_ids = sorted(set(normalized_ids) - known_ids)
            if unknown_ids:
                raise ValueError(f"Unknown add-on id(s): {', '.join(unknown_ids)}")

            registrations_by_id = {
                candidate.registration.addon_id: candidate.registration
                for candidate in candidates
                if candidate.registration is not None and candidate.error is None
            }
            next_loader = AddonLoaderConfig(
                modules=list(host_config.addon_loader.modules),
                package_dirs=list(host_config.addon_loader.package_dirs),
                enabled_entry_points=host_config.addon_loader.enabled_entry_points,
                enabled_addon_ids=normalized_ids,
            )
            next_worker_config = dict(worker_payload.config)
            next_worker_config["addon_loader"] = next_loader.model_dump(mode="json")
            runtime_packs: list[RuntimeWorkflowPackPayload] = []
            for addon_id in normalized_ids:
                registration = registrations_by_id.get(addon_id)
                runtime_pack = self._runtime_workflow_pack_payload(registration)
                if runtime_pack is None:
                    continue
                runtime_packs.append(runtime_pack)
            disabled_runtime_pack_references: set[str] = set()
            enabled_addon_id_set = set(normalized_ids)
            for addon_id, registration in registrations_by_id.items():
                if addon_id in enabled_addon_id_set:
                    continue
                runtime_pack = self._runtime_workflow_pack_payload(registration)
                if runtime_pack is not None:
                    disabled_runtime_pack_references.add(runtime_pack.reference)
            next_worker_config = self._unlist_runtime_workflow_pack_references(
                next_worker_config,
                disabled_runtime_pack_references,
            )
            self._validate_runtime_workflow_pack_materializations(
                next_worker_config,
                runtime_packs,
                overwrite_existing=False,
            )
            for runtime_pack in runtime_packs:
                next_worker_config, _reference = (
                    self.workflow_config_admin.ensure_workflow_pack_payload_unlocked(
                        raw_config=next_worker_config,
                        pack_payload=runtime_pack.payload,
                        overwrite_existing=False,
                        operation=(
                            "runtime_pack_configure"
                            if self.workflow_config_admin._workflow_editor_pack_path(
                                runtime_pack.reference
                            ).exists()
                            else "runtime_pack_create"
                        ),
                        actor=actor,
                    )
                )
            self.workflow_config_admin.write_worker_global_config_unlocked(
                "trigger-orchestrator",
                next_worker_config,
            )
            save_host_config(host_config.model_copy(update={"addon_loader": next_loader}))

        return self.read_addon_state()

    def refresh_runtime_workflow_pack(
        self,
        addon_id: str,
        *,
        expected_revision: str,
        actor: WorkflowPackChangeActor | None = None,
    ) -> dict[str, Any]:
        normalized_addon_id = validate_addon_id(addon_id)
        with self.workflow_config_admin.admin_config_mutation_lock():
            host_config = self._read_host_config()
            worker_payload = self.workflow_config_admin.read_worker_global_config(
                "trigger-orchestrator"
            )
            worker_loader = self._worker_addon_loader(worker_payload.config)
            current_revision = self._revision(host_config.addon_loader, worker_loader)
            if current_revision != expected_revision:
                raise AdminRevisionConflict(
                    "Add-on configuration changed before refresh. Reload add-ons before trying again.",
                    details={"current_revision": current_revision},
                )
            candidates = discover_addons(host_config.addon_loader, base_dir=self.project_dir)
            registration = next(
                (
                    candidate.registration
                    for candidate in candidates
                    if candidate.registration is not None
                    and candidate.error is None
                    and candidate.registration.addon_id == normalized_addon_id
                ),
                None,
            )
            if registration is None:
                raise ValueError(f"Unknown add-on id: {normalized_addon_id}")
            runtime_pack = self._runtime_workflow_pack_payload(registration)
            if runtime_pack is None:
                raise ValueError(
                    f"Add-on {normalized_addon_id} does not define a runtime workflow pack."
                )
            worker_runtime_addon_ids, worker_runtime_loader_error = (
                self._runtime_addon_ids_for_worker_loader(worker_loader)
            )
            if normalized_addon_id not in worker_runtime_addon_ids:
                message = (
                    f"Cannot refresh runtime workflow pack for add-on {normalized_addon_id} "
                    "because trigger-orchestrator addon_loader does not load that add-on. "
                    "Enable the add-on first, then refresh the runtime workflow pack."
                )
                if worker_runtime_loader_error:
                    message = (
                        f"{message} trigger-orchestrator addon_loader error: "
                        f"{worker_runtime_loader_error}"
                    )
                raise ValueError(message)
            next_worker_config, _reference = (
                self.workflow_config_admin.ensure_workflow_pack_payload_unlocked(
                    raw_config=dict(worker_payload.config),
                    pack_payload=runtime_pack.payload,
                    overwrite_existing=True,
                    operation="runtime_pack_refresh",
                    actor=actor,
                )
            )
            self.workflow_config_admin.write_worker_global_config_unlocked(
                "trigger-orchestrator",
                next_worker_config,
            )
        return self.read_addon_state()

    def _read_host_config(self) -> HostConfig:
        path = host_config_path(self.config.runtime_dir)
        if path.exists():
            return load_host_config(self.config.runtime_dir)
        return HostConfig.from_legacy_gateway_config(self.config)

    def _worker_addon_loader(self, worker_config: Mapping[str, object]) -> AddonLoaderConfig:
        return AddonLoaderConfig.model_validate(worker_config.get("addon_loader") or {})

    def _revision(self, host_loader: AddonLoaderConfig, worker_loader: AddonLoaderConfig) -> str:
        return self.workflow_config_admin.stable_json_revision(
            {
                "host_addon_loader": host_loader.model_dump(mode="json"),
                "worker_addon_loader": worker_loader.model_dump(mode="json"),
            }
        )

    def _explicit_enabled_addon_ids(self, loader: AddonLoaderConfig, candidates: Any) -> list[str]:
        if loader.enabled_addon_ids is not None:
            return list(loader.enabled_addon_ids)
        enabled: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate.registration is None:
                continue
            if loader.enabled_addon_ids is None and candidate.source_type == "package":
                continue
            addon_id = candidate.registration.addon_id
            if addon_id in seen:
                continue
            seen.add(addon_id)
            enabled.append(addon_id)
        return enabled

    def _loaded_addon_ids(self) -> tuple[list[str], str | None]:
        try:
            catalog = load_addons(self.config.addon_loader, base_dir=self.project_dir)
        except Exception as exc:
            return [], str(exc)
        return [registration.addon_id for registration in catalog.registrations], None

    def _runtime_addon_ids_for_worker_loader(
        self, worker_loader: AddonLoaderConfig
    ) -> tuple[set[str], str | None]:
        try:
            catalog = load_addons(worker_loader, base_dir=self.project_dir)
        except Exception as exc:
            return set(), str(exc)
        return {registration.addon_id for registration in catalog.registrations}, None

    def _normalize_enabled_addon_ids(self, values: list[object]) -> list[str]:
        if not isinstance(values, list):
            raise ValueError("enabled_addon_ids must be a JSON array")
        normalized: list[str] = []
        seen: set[str] = set()
        for value in values:
            addon_id = validate_addon_id(str(value or "").strip())
            if addon_id in seen:
                continue
            seen.add(addon_id)
            normalized.append(addon_id)
        return normalized

    def _configured_workflow_node_types(
        self, worker_config: Mapping[str, object]
    ) -> tuple[set[str], str | None]:
        configured: set[str] = set()
        workflow_packs = worker_config.get("workflow_packs")
        if not isinstance(workflow_packs, list):
            return configured, None
        try:
            for raw_reference in workflow_packs:
                reference = str(raw_reference or "").strip()
                if not reference:
                    continue
                pack = load_workflow_pack(self.project_dir, reference)
                for item in pack.ir.nodes:
                    node_type = str(getattr(item.node, "type", "") or "").strip()
                    if node_type:
                        configured.add(node_type)
        except Exception as exc:
            return configured, str(exc)
        return configured, None

    def _workflow_node_types(self, registration: AddonRegistration | None) -> set[str]:
        if registration is None or registration.workflow_providers is None:
            return set()
        value = registration.workflow_providers
        if callable(value):
            value = value()
        register = getattr(value, "register_providers", None)
        if callable(register):
            value = register()
        node_types: set[str] = set()
        for collection_name in _PROVIDER_COLLECTIONS:
            collection = self._mapping_value(value, collection_name)
            if collection is None:
                continue
            for raw_type in collection.keys():
                node_type = str(raw_type or "").strip()
                if node_type:
                    node_types.add(node_type)
        entries = self._sequence_value(value, "workflow_editor_catalog_entries")
        for entry in entries or ():
            if isinstance(entry, Mapping):
                raw_type = entry.get("type")
            else:
                raw_type = getattr(entry, "type", None)
            node_type = str(raw_type or "").strip()
            if node_type:
                node_types.add(node_type)
        return node_types

    def _mapping_value(self, value: object, name: str) -> Mapping[Any, Any] | None:
        if isinstance(value, Mapping):
            candidate = value.get(name)
        else:
            candidate = getattr(value, name, None)
        return candidate if isinstance(candidate, Mapping) else None

    def _sequence_value(self, value: object, name: str) -> Any:
        if isinstance(value, Mapping):
            return value.get(name)
        return getattr(value, name, None)

    def _runtime_workflow_pack_source(self, registration: AddonRegistration | None) -> str | None:
        if registration is None:
            return None
        source = registration.metadata.get("runtime_workflow_pack")
        if source is None:
            return None
        if callable(source):
            return f"{source.__module__}.{source.__qualname__}"
        text = str(source).strip()
        return text or None

    def _runtime_workflow_pack_payload(
        self, registration: AddonRegistration | None
    ) -> RuntimeWorkflowPackPayload | None:
        source = self._runtime_workflow_pack_source(registration)
        if source is None:
            return None
        factory = self._runtime_workflow_pack_factory(registration, source)
        payload = factory()
        if not isinstance(payload, dict):
            raise ValueError(f"runtime workflow pack {source} must return a JSON object")
        pack = WorkflowPack.model_validate(payload)
        reference = self.workflow_config_admin._workflow_pack_reference_for_metadata(
            pack_id=pack.pack_id,
            pack_version=pack.pack_version,
        )
        return RuntimeWorkflowPackPayload(
            source=source,
            reference=reference,
            payload=pack.model_dump(mode="json"),
        )

    def _runtime_workflow_pack_factory(
        self,
        registration: AddonRegistration | None,
        source: str,
    ):
        raw_source = registration.metadata.get("runtime_workflow_pack") if registration else None
        if callable(raw_source):
            return raw_source
        module_name, separator, attribute_name = source.rpartition(".")
        if not separator or not module_name or not attribute_name:
            raise ValueError(
                f"runtime workflow pack source {source!r} must be a dotted callable path"
            )
        value: object = import_module(module_name)
        for part in attribute_name.split("."):
            value = getattr(value, part)
        if not callable(value):
            raise ValueError(f"runtime workflow pack source {source!r} is not callable")
        return value

    def _runtime_workflow_pack_status(
        self,
        registration: AddonRegistration | None,
        worker_config: Mapping[str, object],
        *,
        addon_runtime_enabled: bool,
        addon_runtime_loader_error: str | None,
    ) -> dict[str, object] | None:
        source = self._runtime_workflow_pack_source(registration)
        if source is None:
            return None
        status: dict[str, object] = {
            "source": source,
            "reference": None,
            "path": None,
            "exists": False,
            "configured": False,
            "refresh_available": False,
            "error": None,
        }
        try:
            runtime_pack = self._runtime_workflow_pack_payload(registration)
            if runtime_pack is None:
                return None
            pack_path = self.workflow_config_admin._workflow_editor_pack_path(
                runtime_pack.reference
            )
            configured_references = (
                self.workflow_config_admin._workflow_editor_configured_pack_references(
                    dict(worker_config)
                )
            )
            status.update(
                {
                    "reference": runtime_pack.reference,
                    "path": str(pack_path),
                    "exists": pack_path.exists(),
                    "configured": runtime_pack.reference in configured_references,
                    "refresh_available": addon_runtime_enabled,
                }
            )
            if not addon_runtime_enabled and addon_runtime_loader_error:
                status["error"] = (
                    "trigger-orchestrator addon_loader cannot load runtime workflow "
                    f"providers: {addon_runtime_loader_error}"
                )
        except Exception as exc:
            status["error"] = str(exc)
        return status

    def _validate_runtime_workflow_pack_materializations(
        self,
        worker_config: Mapping[str, object],
        runtime_packs: list[RuntimeWorkflowPackPayload],
        *,
        overwrite_existing: bool,
    ) -> None:
        if not runtime_packs:
            return
        if self.workflow_config_admin._workflow_editor_has_inline_workflow(dict(worker_config)):
            raise ValueError(
                "Workflow pack management cannot update configs that combine workflow packs with inline workflow definitions."
            )
        if overwrite_existing:
            return
        for runtime_pack in runtime_packs:
            pack_path = self.workflow_config_admin._workflow_editor_pack_path(
                runtime_pack.reference
            )
            if pack_path.exists():
                load_workflow_pack(self.project_dir, runtime_pack.reference)

    def _unlist_runtime_workflow_pack_references(
        self,
        worker_config: dict[str, object],
        references: set[str],
    ) -> dict[str, object]:
        if not references:
            return worker_config
        workflow_packs = worker_config.get("workflow_packs")
        if not isinstance(workflow_packs, list):
            return worker_config
        remaining = [
            item
            for item in workflow_packs
            if str(item or "").strip() not in references
        ]
        if len(remaining) == len(workflow_packs):
            return worker_config
        updated = dict(worker_config)
        updated["workflow_packs"] = remaining
        return updated

    def _required_restart_services(self, *, has_mcp_tools: bool) -> tuple[str, ...]:
        if has_mcp_tools:
            return (*_RESTART_SERVICES_BASE, "evander-gateway")
        return _RESTART_SERVICES_BASE
