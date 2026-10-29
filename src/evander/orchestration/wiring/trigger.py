from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from evander.admin.composition import build_admin_runtime
from evander.admin.services.managed_git_runtime_service import ManagedGitMaintenancePort
from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.provider_config import ProviderConfigResolver
from evander.provider_profiles import resolve_provider_module_names
from evander.orchestration.internal_events import internal_event_bus_metadata
from evander.runtime import RuntimeContext
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.triggers.registry import TriggerProviderFactory
from evander.workflow.plugins import load_provider_plugins
from evander.repositories.orchestration import JsonGateCursorRepository
from ..adapters.provider_factories import build_trigger_provider_factory


@dataclass(frozen=True)
class TriggerProviderRuntimeWiring:
    provider_config_resolver: ProviderConfigResolver
    provider_factory: TriggerProviderFactory
    managed_git_maintenance: ManagedGitMaintenancePort


def build_trigger_provider_runtime_wiring(
    *,
    config: GatewayConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    runtime: RuntimeContext,
    provider_config_resolver: ProviderConfigResolver | None = None,
    addon_base_dir: str | Path | None = None,
) -> TriggerProviderRuntimeWiring:
    runtime_provider_resolver = getattr(runtime, "provider_config", None)
    resolver = (
        provider_config_resolver or runtime_provider_resolver or ProviderConfigResolver(config)
    )
    provider_catalog = load_provider_plugins(
        module_names=resolve_provider_module_names(
            profile_name=worker_config.provider_profile,
            extra_modules=worker_config.provider_modules,
        ),
        include_entry_points=worker_config.provider_entry_points_enabled,
        include_builtin=worker_config.provider_include_builtin,
        addon_loader=worker_config.addon_loader,
        addon_base_dir=addon_base_dir if addon_base_dir is not None else config.runtime_dir.parent,
    )
    admin_runtime = build_admin_runtime(config.runtime_dir)
    provider_factory = build_trigger_provider_factory(
        runtime_dir=config.runtime_dir,
        provider_config_resolver=resolver,
        trigger_providers=provider_catalog.trigger_providers,
        env=os.environ,
        admin_runtime=admin_runtime,
    )
    provider_factory.metadata.update(internal_event_bus_metadata(worker_config))
    return TriggerProviderRuntimeWiring(
        provider_config_resolver=resolver,
        provider_factory=provider_factory,
        managed_git_maintenance=admin_runtime.managed_git_maintenance,
    )


@dataclass(frozen=True)
class TriggerWorkerWiring:
    provider_config_resolver: ProviderConfigResolver
    event_queue: SqliteTriggerEventQueue
    provider_factory: TriggerProviderFactory
    gate_cursor_repository: JsonGateCursorRepository
    managed_git_maintenance: ManagedGitMaintenancePort


def build_trigger_worker_wiring(
    *,
    config: GatewayConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    runtime: RuntimeContext,
    provider_config_resolver: ProviderConfigResolver | None = None,
    addon_base_dir: str | Path | None = None,
) -> TriggerWorkerWiring:
    event_queue = SqliteTriggerEventQueue(
        worker_config.orchestration_store_path,
        retention_days=int(worker_config.orchestration_retention_days),
        compact_history_retention_days=int(
            worker_config.orchestration_compact_history_retention_days
        ),
    )
    gate_cursor_repository = JsonGateCursorRepository(worker_config.event_queue_cursor_store_path)
    runtime_wiring = build_trigger_provider_runtime_wiring(
        config=config,
        worker_config=worker_config,
        runtime=runtime,
        provider_config_resolver=provider_config_resolver,
        addon_base_dir=addon_base_dir,
    )
    return TriggerWorkerWiring(
        provider_config_resolver=runtime_wiring.provider_config_resolver,
        event_queue=event_queue,
        provider_factory=runtime_wiring.provider_factory,
        gate_cursor_repository=gate_cursor_repository,
        managed_git_maintenance=runtime_wiring.managed_git_maintenance,
    )
