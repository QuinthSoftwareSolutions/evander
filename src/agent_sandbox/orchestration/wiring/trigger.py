from __future__ import annotations

import os
from dataclasses import dataclass

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.provider_profiles import resolve_provider_module_names
from agent_sandbox.runtime import RuntimeContext
from agent_sandbox.triggers.queue import TriggerEventQueue
from agent_sandbox.triggers.registry import TriggerProviderFactory
from agent_sandbox.workflow.plugins import load_provider_plugins
from agent_sandbox.repositories.orchestration import JsonGateCursorRepository
from ..adapters.provider_factories import build_trigger_provider_factory


@dataclass(frozen=True)
class TriggerWorkerWiring:
    provider_config_resolver: ProviderConfigResolver
    event_queue: TriggerEventQueue
    provider_factory: TriggerProviderFactory
    gate_cursor_repository: JsonGateCursorRepository


def build_trigger_worker_wiring(
    *,
    config: GatewayConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    runtime: RuntimeContext,
    provider_config_resolver: ProviderConfigResolver | None = None,
) -> TriggerWorkerWiring:
    runtime_provider_resolver = getattr(runtime, "provider_config", None)
    resolver = (
        provider_config_resolver or runtime_provider_resolver or ProviderConfigResolver(config)
    )
    event_queue = TriggerEventQueue(
        worker_config.event_queue_store_path,
        journal_path=worker_config.workflow_event_journal_path,
    )
    gate_cursor_repository = JsonGateCursorRepository(worker_config.event_queue_cursor_store_path)
    provider_catalog = load_provider_plugins(
        module_names=resolve_provider_module_names(
            profile_name=worker_config.provider_profile,
            extra_modules=worker_config.provider_modules,
        ),
        include_entry_points=worker_config.provider_entry_points_enabled,
        include_builtin=worker_config.provider_include_builtin,
    )
    provider_factory = build_trigger_provider_factory(
        runtime_dir=config.runtime_dir,
        provider_config_resolver=resolver,
        trigger_providers=provider_catalog.trigger_providers,
        env=os.environ,
    )
    return TriggerWorkerWiring(
        provider_config_resolver=resolver,
        event_queue=event_queue,
        provider_factory=provider_factory,
        gate_cursor_repository=gate_cursor_repository,
    )
