from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from evander.admin.composition import build_admin_runtime
from evander.admin.services.managed_git_runtime_service import ManagedGitMaintenancePort
from evander.messaging.agent_prompt_queue import AgentPromptQueueStore
from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.effects.registry import EffectProviderFactory
from evander.provider_config import ProviderConfigResolver
from evander.provider_profiles import (
    is_pure_orchestration_core_profile,
    resolve_provider_module_names,
)
from evander.orchestration.internal_events import internal_event_bus_metadata
from evander.gates.registry import GateProviderFactory
from evander.queries.registry import QueryProviderFactory
from evander.runtime import RuntimeContext
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.workflow.events import SqliteWorkflowEventJournal
from evander.workflow.plugins import load_provider_plugins
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
from evander.repositories.orchestration import SqliteWorkflowConfigSnapshotRepository


@dataclass(frozen=True)
class EffectProviderRuntimeWiring:
    provider_config_resolver: ProviderConfigResolver
    provider_factory: EffectProviderFactory
    query_provider_factory: QueryProviderFactory
    gate_provider_factory: GateProviderFactory
    agent_run_status_port: object | None
    agent_prompt_dispatch_port: object | None
    special_event_handlers: tuple[SpecialEventHandler, ...]
    lifecycle_reconciliation_hooks: tuple[LifecycleReconciliationHook, ...]
    completion_dispatch_hooks: tuple[CompletionDispatchHook, ...]
    managed_git_maintenance: ManagedGitMaintenancePort


@dataclass(frozen=True)
class EffectWorkerStoreWiring:
    workflow_event_journal: SqliteWorkflowEventJournal
    event_queue: SqliteTriggerEventQueue
    lock_path: Path
    cursor_path: Path
    effect_cursor_repository: object
    execution_store: object
    gate_execution_store: object
    workflow_config_snapshot_store: object
    queued_agent_prompt_store: AgentPromptQueueStore


def build_effect_provider_runtime_wiring(
    *,
    config: GatewayConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    runtime: RuntimeContext,
    provider_config_resolver: ProviderConfigResolver | None = None,
    include_default_domain_handlers: bool = True,
    addon_base_dir: str | Path | None = None,
) -> EffectProviderRuntimeWiring:
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
    provider_factory = build_effect_provider_factory(
        runtime_dir=config.runtime_dir,
        provider_config_resolver=resolver,
        effect_providers=provider_catalog.effect_providers,
        env=os.environ,
        admin_runtime=admin_runtime,
    )
    query_provider_factory = build_query_provider_factory(
        runtime_dir=config.runtime_dir,
        provider_config_resolver=resolver,
        query_providers=provider_catalog.query_providers,
        env=os.environ,
        admin_runtime=admin_runtime,
    )
    gate_provider_factory = build_gate_provider_factory(
        runtime_dir=config.runtime_dir,
        provider_config_resolver=resolver,
        gate_providers=provider_catalog.gate_providers,
        env=os.environ,
        admin_runtime=admin_runtime,
    )
    provider_factory.metadata.update(internal_event_bus_metadata(worker_config))
    agent_run_status_port = None
    agent_prompt_dispatch_port = None
    special_event_handlers: tuple[SpecialEventHandler, ...] = ()
    lifecycle_reconciliation_hooks: tuple[LifecycleReconciliationHook, ...] = ()
    completion_dispatch_hooks: tuple[CompletionDispatchHook, ...] = ()
    if include_default_domain_handlers and not is_pure_orchestration_core_profile(
        worker_config.provider_profile
    ):
        agent_run_status_port = build_agent_run_status_port(
            runtime_dir=config.runtime_dir,
            admin_runtime=admin_runtime,
        )
        agent_prompt_dispatch_port = build_agent_prompt_dispatch_port(
            runtime_dir=config.runtime_dir,
            admin_runtime=admin_runtime,
        )

        from ..providers import build_agent_default_effect_handlers

        (
            special_event_handlers,
            lifecycle_reconciliation_hooks,
            completion_dispatch_hooks,
        ) = build_agent_default_effect_handlers(provider_factory=provider_factory)
    return EffectProviderRuntimeWiring(
        provider_config_resolver=resolver,
        provider_factory=provider_factory,
        query_provider_factory=query_provider_factory,
        gate_provider_factory=gate_provider_factory,
        agent_run_status_port=agent_run_status_port,
        agent_prompt_dispatch_port=agent_prompt_dispatch_port,
        special_event_handlers=special_event_handlers,
        lifecycle_reconciliation_hooks=lifecycle_reconciliation_hooks,
        completion_dispatch_hooks=completion_dispatch_hooks,
        managed_git_maintenance=admin_runtime.managed_git_maintenance,
    )


def build_effect_worker_store_wiring(
    *,
    config: GatewayConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    runtime: RuntimeContext,
) -> EffectWorkerStoreWiring:
    from evander.daemons.effect_orchestrator import (
        SqliteExecutionStore,
        SqliteGateExecutionStore,
        _resolve_worker_path,
        build_effect_cursor_repository,
    )

    retention_days = int(worker_config.orchestration_retention_days)
    compact_history_retention_days = int(worker_config.orchestration_compact_history_retention_days)
    workflow_event_journal = SqliteWorkflowEventJournal(
        worker_config.orchestration_store_path,
        retention_days=retention_days,
        compact_history_retention_days=compact_history_retention_days,
    )
    event_queue = SqliteTriggerEventQueue(
        worker_config.orchestration_store_path,
        journal=workflow_event_journal,
        retention_days=retention_days,
        compact_history_retention_days=compact_history_retention_days,
    )
    execution_store = SqliteExecutionStore(
        worker_config.orchestration_store_path,
        journal=workflow_event_journal,
        retention_days=retention_days,
        compact_history_retention_days=compact_history_retention_days,
    )
    gate_execution_store = SqliteGateExecutionStore(
        worker_config.orchestration_store_path,
        journal=workflow_event_journal,
        retention_days=retention_days,
        compact_history_retention_days=compact_history_retention_days,
    )
    workflow_config_snapshot_store = SqliteWorkflowConfigSnapshotRepository(
        worker_config.orchestration_store_path
    )
    lock_path = _resolve_worker_path(
        worker_config,
        "effect_lock_path",
        fallback=worker_config.effect_lock_path
        or (worker_config.runtime_dir / "effect-orchestrator.lock"),
    )
    cursor_path = _resolve_worker_path(
        worker_config,
        "effect_cursor_store_path",
        fallback=worker_config.effect_cursor_store_path
        or (worker_config.runtime_dir / "effect-orchestrator-cursors.json"),
    )
    effect_cursor_repository = build_effect_cursor_repository(cursor_path)
    queued_agent_prompt_store = AgentPromptQueueStore(
        worker_config.runtime_dir / "queued-agent-prompts.json"
    )
    return EffectWorkerStoreWiring(
        workflow_event_journal=workflow_event_journal,
        event_queue=event_queue,
        lock_path=lock_path,
        cursor_path=cursor_path,
        effect_cursor_repository=effect_cursor_repository,
        execution_store=execution_store,
        gate_execution_store=gate_execution_store,
        workflow_config_snapshot_store=workflow_config_snapshot_store,
        queued_agent_prompt_store=queued_agent_prompt_store,
    )


@dataclass(frozen=True)
class EffectWorkerWiring:
    provider_config_resolver: ProviderConfigResolver
    workflow_event_journal: SqliteWorkflowEventJournal
    event_queue: SqliteTriggerEventQueue
    lock_path: Path
    cursor_path: Path
    effect_cursor_repository: object
    execution_store: object
    gate_execution_store: object
    workflow_config_snapshot_store: object
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
    managed_git_maintenance: ManagedGitMaintenancePort


def build_effect_worker_wiring(
    *,
    config: GatewayConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
    runtime: RuntimeContext,
    provider_config_resolver: ProviderConfigResolver | None = None,
    include_default_domain_handlers: bool = True,
    addon_base_dir: str | Path | None = None,
) -> EffectWorkerWiring:
    store_wiring = build_effect_worker_store_wiring(
        config=config,
        worker_config=worker_config,
        runtime=runtime,
    )
    executor = ThreadPoolExecutor(
        max_workers=max(1, min(32, max(1, (os.cpu_count() or 1) * 4))),
        thread_name_prefix="effect-orchestrator",
    )
    runtime_wiring = build_effect_provider_runtime_wiring(
        config=config,
        worker_config=worker_config,
        runtime=runtime,
        provider_config_resolver=provider_config_resolver,
        include_default_domain_handlers=include_default_domain_handlers,
        addon_base_dir=addon_base_dir,
    )
    return EffectWorkerWiring(
        provider_config_resolver=runtime_wiring.provider_config_resolver,
        workflow_event_journal=store_wiring.workflow_event_journal,
        event_queue=store_wiring.event_queue,
        lock_path=store_wiring.lock_path,
        cursor_path=store_wiring.cursor_path,
        effect_cursor_repository=store_wiring.effect_cursor_repository,
        execution_store=store_wiring.execution_store,
        gate_execution_store=store_wiring.gate_execution_store,
        workflow_config_snapshot_store=store_wiring.workflow_config_snapshot_store,
        queued_agent_prompt_store=store_wiring.queued_agent_prompt_store,
        executor=executor,
        provider_factory=runtime_wiring.provider_factory,
        query_provider_factory=runtime_wiring.query_provider_factory,
        gate_provider_factory=runtime_wiring.gate_provider_factory,
        agent_run_status_port=runtime_wiring.agent_run_status_port,
        agent_prompt_dispatch_port=runtime_wiring.agent_prompt_dispatch_port,
        special_event_handlers=runtime_wiring.special_event_handlers,
        lifecycle_reconciliation_hooks=runtime_wiring.lifecycle_reconciliation_hooks,
        completion_dispatch_hooks=runtime_wiring.completion_dispatch_hooks,
        managed_git_maintenance=runtime_wiring.managed_git_maintenance,
    )
