from __future__ import annotations

import os
from dataclasses import dataclass

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.provider_profiles import resolve_provider_module_names
from agent_sandbox.repositories.orchestration_db import maybe_checkpoint_orchestration_wal
from agent_sandbox.orchestration.wal_maintenance import sqlite_orchestration_store_enabled
from agent_sandbox.runtime import RuntimeContext
from agent_sandbox.triggers.queue import SqliteTriggerEventQueue, TriggerEventQueue
from agent_sandbox.triggers.registry import TriggerProviderFactory
from agent_sandbox.workflow.plugins import load_provider_plugins
from agent_sandbox.repositories.orchestration import JsonGateCursorRepository
from ..adapters.provider_factories import build_trigger_provider_factory


@dataclass(frozen=True)
class TriggerProviderRuntimeWiring:
    provider_config_resolver: ProviderConfigResolver
    provider_factory: TriggerProviderFactory


def build_trigger_provider_runtime_wiring(
    *,
    config: GatewayConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    runtime: RuntimeContext,
    provider_config_resolver: ProviderConfigResolver | None = None,
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
    )
    provider_factory = build_trigger_provider_factory(
        runtime_dir=config.runtime_dir,
        provider_config_resolver=resolver,
        trigger_providers=provider_catalog.trigger_providers,
        env=os.environ,
    )
    provider_factory.metadata.update(
        {
            "internal_event_store_path": str(worker_config.internal_event_store_path),
            "workflow_event_journal_path": str(worker_config.workflow_event_journal_path),
        }
    )
    return TriggerProviderRuntimeWiring(
        provider_config_resolver=resolver,
        provider_factory=provider_factory,
    )


@dataclass(frozen=True)
class TriggerWorkerWiring:
    provider_config_resolver: ProviderConfigResolver
    event_queue: TriggerEventQueue | SqliteTriggerEventQueue
    provider_factory: TriggerProviderFactory
    gate_cursor_repository: JsonGateCursorRepository


def build_trigger_worker_wiring(
    *,
    config: GatewayConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    runtime: RuntimeContext,
    provider_config_resolver: ProviderConfigResolver | None = None,
) -> TriggerWorkerWiring:
    if _sqlite_orchestration_store_enabled(worker_config):
        event_queue = SqliteTriggerEventQueue(
            worker_config.orchestration_store_path,
            retention_days=int(worker_config.orchestration_retention_days),
        )
        maybe_checkpoint_orchestration_wal(
            worker_config.orchestration_store_path,
            mode=worker_config.orchestration_wal_checkpoint_mode,
            min_bytes=worker_config.orchestration_wal_checkpoint_min_bytes,
            interval_seconds=worker_config.orchestration_wal_checkpoint_interval_seconds,
            busy_timeout_ms=worker_config.orchestration_wal_checkpoint_busy_timeout_ms,
        )
    else:
        event_queue = TriggerEventQueue(
            worker_config.event_queue_store_path,
            journal_path=worker_config.workflow_event_journal_path,
        )
    gate_cursor_repository = JsonGateCursorRepository(worker_config.event_queue_cursor_store_path)
    runtime_wiring = build_trigger_provider_runtime_wiring(
        config=config,
        worker_config=worker_config,
        runtime=runtime,
        provider_config_resolver=provider_config_resolver,
    )
    return TriggerWorkerWiring(
        provider_config_resolver=runtime_wiring.provider_config_resolver,
        event_queue=event_queue,
        provider_factory=runtime_wiring.provider_factory,
        gate_cursor_repository=gate_cursor_repository,
    )


def _sqlite_orchestration_store_enabled(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> bool:
    return sqlite_orchestration_store_enabled(worker_config)
