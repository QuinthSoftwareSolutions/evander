from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ...agent_prompt_queue import AgentPromptQueueStore
from ...config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from ...effects.registry import EffectProviderFactory
from ...provider_config import ProviderConfigResolver
from ...provider_profiles import is_pure_orchestration_core_profile, resolve_provider_module_names
from ...gates.registry import GateProviderFactory
from ...queries.registry import QueryProviderFactory
from ...runtime import RuntimeContext
from ...triggers.queue import TriggerEventQueue
from ...workflow_events import WorkflowEventJournal
from ...workflow_plugins import load_provider_plugins
from ..adapters.provider_factories import (
    build_agent_prompt_dispatch_port,
    build_agent_run_status_port,
    build_effect_provider_factory,
    build_gate_provider_factory,
    build_query_provider_factory,
)
from ..effect_extensions import (
    CompletionDispatchHook,
    LifecycleReconciliationHook,
    SpecialEventHandler,
)


@dataclass(frozen=True)
class EffectWorkerWiring:
    provider_config_resolver: ProviderConfigResolver
    workflow_event_journal: WorkflowEventJournal
    event_queue: TriggerEventQueue
    lock_path: Path
    cursor_path: Path
    execution_store: object
    gate_execution_store: object
    queued_agent_prompt_store: AgentPromptQueueStore
    executor: ThreadPoolExecutor
    provider_factory: EffectProviderFactory
    query_provider_factory: QueryProviderFactory
    gate_provider_factory: GateProviderFactory
    agent_run_status_port: object | None
    agent_prompt_dispatch_port: object | None
    special_event_handlers: tuple[SpecialEventHandler, ...]
    lifecycle_reconciliation_hooks: tuple[LifecycleReconciliationHook, ...]
    completion_dispatch_hooks: tuple[CompletionDispatchHook, ...]


def build_effect_worker_wiring(
    *,
    config: GatewayConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    runtime: RuntimeContext,
    provider_config_resolver: ProviderConfigResolver | None = None,
    include_default_domain_handlers: bool = True,
) -> EffectWorkerWiring:
    from ...effect_orchestrator import JsonExecutionStore, JsonGateExecutionStore, _resolve_worker_path

    runtime_provider_resolver = getattr(runtime, "provider_config", None)
    resolver = provider_config_resolver or runtime_provider_resolver or ProviderConfigResolver(config)
    workflow_event_journal = WorkflowEventJournal(worker_config.workflow_event_journal_path)
    event_queue = TriggerEventQueue(
        worker_config.event_queue_store_path,
        journal_path=worker_config.workflow_event_journal_path,
    )
    lock_path = _resolve_worker_path(
        worker_config,
        "gate_lock_path",
        fallback=worker_config.effect_lock_path or (worker_config.runtime_dir / "effect-orchestrator.lock"),
    )
    cursor_path = _resolve_worker_path(
        worker_config,
        "gate_cursor_store_path",
        fallback=worker_config.effect_cursor_store_path or (worker_config.runtime_dir / "effect-orchestrator-cursors.json"),
    )
    execution_store = JsonExecutionStore(
        worker_config.effect_execution_store_path,
        journal_path=worker_config.workflow_event_journal_path,
    )
    gate_execution_store = JsonGateExecutionStore(
        _resolve_worker_path(
            worker_config,
            "gate_execution_store_path",
            fallback=worker_config.runtime_dir / "gate-orchestrator-executions.json",
        ),
        journal_path=worker_config.workflow_event_journal_path,
    )
    queued_agent_prompt_store = AgentPromptQueueStore(worker_config.runtime_dir / "queued-agent-prompts.json")
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(32, max(1, (os.cpu_count() or 1) * 4))),
        thread_name_prefix="effect-orchestrator",
    )
    provider_catalog = load_provider_plugins(
        module_names=resolve_provider_module_names(
            profile_name=worker_config.provider_profile,
            extra_modules=worker_config.provider_modules,
        ),
        include_entry_points=worker_config.provider_entry_points_enabled,
        include_builtin=worker_config.provider_include_builtin,
    )
    provider_factory = build_effect_provider_factory(
        runtime_dir=config.runtime_dir,
        provider_config_resolver=resolver,
        effect_providers=provider_catalog.effect_providers,
        env=os.environ,
    )
    query_provider_factory = build_query_provider_factory(
        runtime_dir=config.runtime_dir,
        provider_config_resolver=resolver,
        query_providers=provider_catalog.query_providers,
        env=os.environ,
    )
    gate_provider_factory = build_gate_provider_factory(
        runtime_dir=config.runtime_dir,
        provider_config_resolver=resolver,
        gate_providers=provider_catalog.gate_providers,
        env=os.environ,
    )
    agent_run_status_port = None
    agent_prompt_dispatch_port = None
    special_event_handlers: tuple[SpecialEventHandler, ...] = ()
    lifecycle_reconciliation_hooks: tuple[LifecycleReconciliationHook, ...] = ()
    completion_dispatch_hooks: tuple[CompletionDispatchHook, ...] = ()
    if include_default_domain_handlers and not is_pure_orchestration_core_profile(worker_config.provider_profile):
        agent_run_status_port = build_agent_run_status_port(runtime_dir=config.runtime_dir)
        agent_prompt_dispatch_port = build_agent_prompt_dispatch_port(runtime_dir=config.runtime_dir)
        from ..providers import build_agent_default_effect_handlers

        (
            special_event_handlers,
            lifecycle_reconciliation_hooks,
            completion_dispatch_hooks,
        ) = build_agent_default_effect_handlers(provider_factory=provider_factory)
    return EffectWorkerWiring(
        provider_config_resolver=resolver,
        workflow_event_journal=workflow_event_journal,
        event_queue=event_queue,
        lock_path=lock_path,
        cursor_path=cursor_path,
        execution_store=execution_store,
        gate_execution_store=gate_execution_store,
        queued_agent_prompt_store=queued_agent_prompt_store,
        executor=executor,
        provider_factory=provider_factory,
        query_provider_factory=query_provider_factory,
        gate_provider_factory=gate_provider_factory,
        agent_run_status_port=agent_run_status_port,
        agent_prompt_dispatch_port=agent_prompt_dispatch_port,
        special_event_handlers=special_event_handlers,
        lifecycle_reconciliation_hooks=lifecycle_reconciliation_hooks,
        completion_dispatch_hooks=completion_dispatch_hooks,
    )
