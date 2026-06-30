from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from evander.addons import (
    AddonLoaderConfig,
    AddonRegistration,
    discover_addons,
    load_addons,
    validate_addon_id,
)
from evander.admin.payloads import AdminRevisionConflict
from evander.host_config import HostConfig, host_config_path, load_host_config, save_host_config
from evander.workflow.packs import load_workflow_pack

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
        configured_node_types, workflow_reference_error = self._configured_workflow_node_types(
            worker_payload.config
        )
        candidates = discover_addons(host_loader)
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
                    "has_workflow_providers": bool(registration and registration.workflow_providers),
                    "workflow_node_types": sorted(node_types),
                    "referenced_workflow_node_types": referenced_node_types,
                    "load_error": candidate.error,
                    "required_restart_services": list(
                        self._required_restart_services(has_mcp_tools=has_mcp_tools)
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
                    "has_workflow_providers": False,
                    "workflow_node_types": [],
                    "referenced_workflow_node_types": [],
                    "load_error": "Configured enabled add-on was not discovered.",
                    "required_restart_services": list(
                        self._required_restart_services(has_mcp_tools=False)
                    ),
                }
            )

        restart_required = any(item["restart_required"] for item in addons)
        has_mcp_change = any(
            item["restart_required"] and item["has_mcp_tools"] for item in addons
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
                self._required_restart_services(has_mcp_tools=has_mcp_change)
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

            candidates = discover_addons(host_config.addon_loader)
            known_ids = {
                candidate.registration.addon_id
                for candidate in candidates
                if candidate.registration is not None and candidate.error is None
            }
            unknown_ids = sorted(set(normalized_ids) - known_ids)
            if unknown_ids:
                raise ValueError(f"Unknown add-on id(s): {', '.join(unknown_ids)}")

            next_loader = AddonLoaderConfig(
                modules=list(host_config.addon_loader.modules),
                enabled_entry_points=host_config.addon_loader.enabled_entry_points,
                enabled_addon_ids=normalized_ids,
            )
            next_worker_config = dict(worker_payload.config)
            next_worker_config["addon_loader"] = next_loader.model_dump(mode="json")
            self.workflow_config_admin.write_worker_global_config(
                "trigger-orchestrator",
                next_worker_config,
                expected_revision=worker_payload.revision,
            )
            save_host_config(host_config.model_copy(update={"addon_loader": next_loader}))

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
            addon_id = candidate.registration.addon_id
            if addon_id in seen:
                continue
            seen.add(addon_id)
            enabled.append(addon_id)
        return enabled

    def _loaded_addon_ids(self) -> tuple[list[str], str | None]:
        try:
            catalog = load_addons(self.config.addon_loader)
        except Exception as exc:
            return [], str(exc)
        return [registration.addon_id for registration in catalog.registrations], None

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

    def _required_restart_services(self, *, has_mcp_tools: bool) -> tuple[str, ...]:
        if has_mcp_tools:
            return (*_RESTART_SERVICES_BASE, "evander-gateway")
        return _RESTART_SERVICES_BASE
