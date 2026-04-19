from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from ...admin_service import AdminService
from ...config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from ...provider_config import ProviderConfigResolver
from ...provider_profiles import resolve_provider_module_names
from ...runtime import RuntimeContext
from ...triggers.queue import TriggerEventQueue
from ...triggers.registry import TriggerProviderFactory, TriggerProviderRegistry
from ...workflow_plugins import load_provider_plugins


@dataclass(frozen=True)
class TriggerWorkerWiring:
    provider_config_resolver: ProviderConfigResolver
    event_queue: TriggerEventQueue
    provider_factory: TriggerProviderFactory


def build_trigger_worker_wiring(
    *,
    config: GatewayConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    runtime: RuntimeContext,
    provider_config_resolver: ProviderConfigResolver | None = None,
) -> TriggerWorkerWiring:
    runtime_provider_resolver = getattr(runtime, "provider_config", None)
    resolver = provider_config_resolver or runtime_provider_resolver or ProviderConfigResolver(config)
    event_queue = TriggerEventQueue(
        worker_config.event_queue_store_path,
        journal_path=worker_config.workflow_event_journal_path,
    )
    provider_catalog = load_provider_plugins(
        module_names=resolve_provider_module_names(
            profile_name=worker_config.provider_profile,
            extra_modules=worker_config.provider_modules,
        ),
        include_entry_points=worker_config.provider_entry_points_enabled,
        include_builtin=worker_config.provider_include_builtin,
    )
    provider_factory = TriggerProviderFactory(
        resolver,
        registry=TriggerProviderRegistry(initial=provider_catalog.trigger_providers),
        admin_service=AdminService(config.runtime_dir),
        env=dict(os.environ),
    )
    return TriggerWorkerWiring(
        provider_config_resolver=resolver,
        event_queue=event_queue,
        provider_factory=provider_factory,
    )
