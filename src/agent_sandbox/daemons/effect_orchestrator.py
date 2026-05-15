from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import random
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_sandbox.config import (
    GatewayConfig,
    OrchestrationCoreConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from agent_sandbox.effects.registry import EffectProviderFactory, EffectProviderRegistry
from agent_sandbox.events.internal import InternalEventBus, InternalEventSource
from agent_sandbox.events.models import (
    EffectDefinitionDiscriminated,
    GateDefinitionDiscriminated,
    materialize_node_definition,
    NODE_OUTPUTS_CONTEXT_KEY,
    QueryDefinitionDiscriminated,
    validate_output_mapping_keys,
    TriggerDefinitionDiscriminated,
    TriggerEvent,
)
from agent_sandbox.gates.registry import GateProviderFactory, GateProviderRegistry
from agent_sandbox.gate_runtime import (
    GateRuntimeError,
    require_object_payload,
)
from agent_sandbox.messaging.wake_dispatch import single_instance_lock
from agent_sandbox.orchestration.adapters import (
    EffectCompletionPublisherAdapter,
    EffectDependencyResolverAdapter,
    EffectEventFetcherAdapter,
    EffectLoopExecution,
    EffectExecutionRecorderAdapter,
)
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.queries.registry import QueryProviderFactory, QueryProviderRegistry
from agent_sandbox.runtime import RuntimeContext
from agent_sandbox.app_bootstrap import build_runtime
from agent_sandbox.platform_integrations import PlatformServiceBundle, RuntimeAssemblyInterface
from agent_sandbox.triggers.queue import TriggerEventQueue, TriggerQueueRecord
from agent_sandbox.workflow.runtime_config import (
    FixedTriggerOrchestratorWorkerGlobalConfigReloader,
    TriggerOrchestratorWorkerGlobalConfigReloader,
    load_trigger_orchestrator_worker_global_config,
)
from agent_sandbox.workflow.binding import (
    BindingResolutionError,
    resolve_effect_config_bindings,
)
from agent_sandbox.workflow.graph import (
    WorkflowGraphCycleError,
    build_downstream_index,
    build_inbound_edge_index,
    compute_downstream_depth,
    index_declared_outputs,
    would_form_cycle,
)
from agent_sandbox.workflow.engine.node_executors import (
    NodeExecutorRegistry,
    ProviderEffectNodeExecutor,
    ProviderQueryNodeExecutor,
)
from agent_sandbox.workflow.engine import OrchestrationLoop
from agent_sandbox.workflow.plugins import load_provider_plugins
from agent_sandbox.orchestration.kernels import PollingKernelConfig, PollingOrchestratorKernel
from agent_sandbox.orchestration.wiring import EffectWorkerWiring, build_effect_worker_wiring
from agent_sandbox.orchestration.effect_extensions import SpecialEventResult
from agent_sandbox.gates.providers.await_agent import (
    AwaitAgentPendingRecord,
    _await_agent_pending_target,
)
from agent_sandbox.repositories.orchestration import (
    EffectExecutionRepository,
    GateExecutionRepository,
    JsonEffectCursorRepository,
    JsonExecutionRepository,
)

logger = logging.getLogger(__name__)

_DEFAULT_MAX_GRAPH_DEPTH = 16
_DEFAULT_MAX_EVENTS_PER_CYCLE = 200
_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"
WorkflowNodeDefinition = (
    TriggerDefinitionDiscriminated
    | GateDefinitionDiscriminated
    | EffectDefinitionDiscriminated
    | QueryDefinitionDiscriminated
)


class _LockManager:
    def lock(self, path: Path):
        return single_instance_lock(path)


class _Sleeper:
    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class EffectExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    execution_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    source_node_id: str = Field(min_length=1)
    source_trigger_type: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    effect_type: str = Field(min_length=1)
    status: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    resolved_config_hash: str = Field(min_length=1)
    resolved_config: dict[str, Any] = Field(default_factory=dict)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    published_event_id: str | None = None
    error: str | None = None
    completion_event_enqueued_at: datetime | None = None
    config_revision: str | None = None
    cleared_at: datetime | None = None
    clear_reason: str | None = None
    dismissed_at: datetime | None = None
    dismiss_reason: str | None = None


class GateExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    execution_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    source_node_id: str = Field(min_length=1)
    source_trigger_type: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)
    gate_type: str = Field(min_length=1)
    status: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    resolved_config_hash: str = Field(min_length=1)
    resolved_config: dict[str, Any] = Field(default_factory=dict)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    inputs_by_source: dict[str, dict[str, Any]] = Field(default_factory=dict)
    input_event_ids_by_source: dict[str, str] = Field(default_factory=dict)
    input_occurred_at_by_source: dict[str, datetime] = Field(default_factory=dict)
    published_event_id: str | None = None
    error: str | None = None
    completion_event_enqueued_at: datetime | None = None
    config_revision: str | None = None
    cleared_at: datetime | None = None
    clear_reason: str | None = None
    dismissed_at: datetime | None = None
    dismiss_reason: str | None = None


@dataclass(frozen=True)
class ExecutionGraph:
    nodes_by_id: dict[str, WorkflowNodeDefinition]
    gates_by_id: dict[str, GateDefinitionDiscriminated]
    effects_by_id: dict[str, EffectDefinitionDiscriminated]
    queries_by_id: dict[str, QueryDefinitionDiscriminated]
    outbound_index: dict[str, tuple[Any, ...]]
    downstream_index: dict[str, tuple[str, ...]]
    inbound_index: dict[str, tuple[Any, ...]]
    declared_outputs: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class EffectOrchestratorCycleResult:
    pending_events_scanned: int
    events_processed: int
    effect_executions: int
    completion_events_published: int
    next_sleep_seconds: float


@dataclass(frozen=True)
class InflightExecution:
    future: Future[dict[str, Any]]
    config_revision: str


class EventEmitEffectExecutor:
    def __init__(self, *, internal_event_bus: InternalEventBus) -> None:
        self.internal_event_bus = internal_event_bus

    def prepare_resolved_config(self, **kwargs: Any) -> dict[str, Any]:
        resolved_config = kwargs.get("resolved_config")
        config = dict(resolved_config) if isinstance(resolved_config, dict) else {}
        payload = config.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("effect:event:emit payload must resolve to a JSON object")
        config["payload"] = payload
        return config

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        resolved_config = dict(kwargs.get("resolved_config") or {})
        context = dict(kwargs.get("execution_context") or {})
        payload = resolved_config.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("effect:event:emit payload must resolve to a JSON object")
        event = self.internal_event_bus.publish(
            event_type=resolved_config.get("event_type"),
            payload=payload,
            correlation_key=resolved_config.get("correlation_key"),
            source=InternalEventSource(
                workflow_run_id=context.get("workflow_run_id"),
                workflow_event_id=context.get("workflow_event_id"),
                workflow_trace_id=context.get("workflow_trace_id"),
                node_id=context.get("node_id"),
                node_type=context.get("node_type"),
            ),
            idempotency_key=context.get("idempotency_key"),
        )
        return {
            "success": True,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "correlation_key": event.correlation_key,
            "created_at": event.created_at.isoformat().replace("+00:00", "Z"),
            "payload": dict(event.payload),
        }


class EffectOrchestratorWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        core_config: OrchestrationCoreConfig | None = None,
        worker_config: TriggerOrchestratorWorkerGlobalConfig | None = None,
        config_reloader: TriggerOrchestratorWorkerGlobalConfigReloader | None = None,
        runtime: RuntimeContext,
        provider_config_resolver: ProviderConfigResolver | None = None,
        wiring: EffectWorkerWiring | None = None,
    ) -> None:
        self.config = config
        selected_config = core_config or worker_config
        if selected_config is None:
            raise ValueError(
                "EffectOrchestratorWorker requires either core_config or worker_config"
            )
        self.worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
            selected_config.model_dump(mode="json")
        )
        self._config_reloader = (
            config_reloader
            or FixedTriggerOrchestratorWorkerGlobalConfigReloader(self.worker_config)
        )
        self.runtime = runtime
        resolved_wiring = wiring or build_effect_worker_wiring(
            config=config,
            worker_config=self.worker_config,
            runtime=runtime,
            provider_config_resolver=provider_config_resolver,
        )
        self.provider_config_resolver = resolved_wiring.provider_config_resolver
        self.workflow_event_journal = resolved_wiring.workflow_event_journal
        self.event_queue = resolved_wiring.event_queue
        self.lock_path = resolved_wiring.lock_path
        self.cursor_path = resolved_wiring.cursor_path
        self.cursor_repository = resolved_wiring.effect_cursor_repository
        self.execution_store_path = self.worker_config.effect_execution_store_path
        self.execution_store: EffectExecutionRepository = resolved_wiring.execution_store
        self.gate_execution_store_path = _resolve_worker_path(
            self.worker_config,
            "gate_execution_store_path",
            fallback=self.worker_config.runtime_dir / "gate-orchestrator-executions.json",
        )
        self.gate_execution_store: GateExecutionRepository = resolved_wiring.gate_execution_store
        self.queued_agent_prompt_store = resolved_wiring.queued_agent_prompt_store
        self._executor = resolved_wiring.executor
        self._inflight: dict[str, InflightExecution] = {}
        self._inflight_lock = RLock()
        self._last_graph_revision: str | None = None
        self._last_graph_node_count = 0
        self._last_graph_edge_count = 0
        self.provider_factory = resolved_wiring.provider_factory
        self.query_provider_factory = resolved_wiring.query_provider_factory
        self.gate_provider_factory = resolved_wiring.gate_provider_factory
        self.agent_run_status_port = getattr(resolved_wiring, "agent_run_status_port", None)
        self.agent_prompt_dispatch_port = getattr(
            resolved_wiring, "agent_prompt_dispatch_port", None
        )
        self._effect_provider_class_registry = getattr(self.provider_factory, "registry", None)
        self.gate_executor_registry = NodeExecutorRegistry()
        self.effect_executor_registry = NodeExecutorRegistry()
        self.query_executor_registry = NodeExecutorRegistry()
        self._register_default_node_executors()
        self._gate_providers: dict[str, Any] = {}
        self._gate_provider_factory_cache_id = id(self.gate_provider_factory)
        self._special_event_handlers = resolved_wiring.special_event_handlers
        self._lifecycle_reconciliation_hooks = resolved_wiring.lifecycle_reconciliation_hooks
        self._completion_dispatch_hooks = resolved_wiring.completion_dispatch_hooks
        self.event_queue.reset_inflight()
        self._kernel = PollingOrchestratorKernel(
            config=PollingKernelConfig(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                lock_path=self.lock_path,
            ),
            lock_manager=_LockManager(),
            sleeper=_Sleeper(),
            run_cycle=self.run_once,
            get_sleep_seconds=lambda result: result.next_sleep_seconds,
        )

    def run_forever(self) -> None:
        self._kernel.run_forever()

    def run_once_with_lock(self) -> EffectOrchestratorCycleResult:
        return self._kernel.run_once_with_lock()

    def force_complete_await_agent(self, dispatch_id: str) -> bool:
        return self._run_force_complete_hooks(dispatch_id=dispatch_id)

    def run_once(self) -> EffectOrchestratorCycleResult:
        latest_config, _changed = self._config_reloader.get_latest()
        self.worker_config = latest_config
        config_revision = _config_revision_hash(latest_config)
        pending_events_scanned = 0
        events_processed = 0
        effect_executions_started = 0
        effect_executions_completed = 0
        completion_events_published = 0

        graph = _build_execution_graph(self.worker_config)
        self._log_graph_revision_change(
            config_revision=config_revision,
            node_count=len(graph.nodes_by_id),
            edge_count=len(latest_config.edges),
        )
        completion_events_published += self._reconcile_gate_providers(graph=graph)
        completion_events_published += self._run_waiting_reconciliation_hooks()

        _completed_count, published_count = self._reap_completed_futures()
        completion_events_published += published_count
        completion_events_published += self._run_post_completion_dispatch_hooks()

        loop = OrchestrationLoop[
            TriggerQueueRecord,
            tuple[ExecutionGraph, str, dict[str, Any]],
            EffectLoopExecution,
        ](
            event_fetcher=EffectEventFetcherAdapter(
                fetch_pending=lambda: (self.event_queue.list_pending(limit=1) or [None])[0]
            ),
            dependency_resolver=EffectDependencyResolverAdapter(
                dependency_factory=lambda _queue_record: (graph, config_revision, {})
            ),
            execution_recorder=EffectExecutionRecorderAdapter(
                record_event_processed=lambda queue_record: (
                    self._update_event_delivery_state(queue_record.event_id),
                    self.cursor_repository.record_processed_event(
                        event_id=queue_record.event_id,
                        source_node_id=queue_record.trigger_id,
                    ),
                )
            ),
            completion_publisher=EffectCompletionPublisherAdapter(),
        )

        def _resolve_and_execute(
            queue_record: TriggerQueueRecord,
            dependency: tuple[ExecutionGraph, str, dict[str, Any]],
        ) -> EffectLoopExecution:
            nonlocal effect_executions_started
            resolved_graph, resolved_config_revision, _cursor = dependency
            execution_count = self._process_event_record(
                queue_record,
                graph=resolved_graph,
                config_revision=resolved_config_revision,
            )
            effect_executions_started += execution_count
            return EffectLoopExecution(
                queue_record=queue_record,
                execution_count=execution_count,
                published_count=0,
            )

        loop_result = loop.run_once(
            max_events=max(
                1,
                int(getattr(latest_config, "max_events_per_cycle", _DEFAULT_MAX_EVENTS_PER_CYCLE)),
            ),
            resolve_and_execute=_resolve_and_execute,
        )
        pending_events_scanned = loop_result.events_scanned
        events_processed = loop_result.events_scanned

        # Give very short-running in-process effects a chance to settle before
        # the second reap so the same cycle can publish their completion state.
        self._wait_for_inflight_completion(timeout_seconds=0.01)

        # Reap futures again after dispatch so fast in-process effects do not
        # remain marked as running until an extra poll cycle.
        _completed_count, published_count = self._reap_completed_futures()
        completion_events_published += published_count

        next_sleep_seconds = _compute_next_sleep_seconds(
            poll_interval_seconds=self.worker_config.poll_interval_seconds,
            jitter_seconds=self.worker_config.jitter_seconds,
        )

        return EffectOrchestratorCycleResult(
            pending_events_scanned=pending_events_scanned,
            events_processed=events_processed,
            effect_executions=effect_executions_started + effect_executions_completed,
            completion_events_published=completion_events_published,
            next_sleep_seconds=next_sleep_seconds,
        )

    def _process_event_record(
        self,
        queue_record: TriggerQueueRecord,
        *,
        graph: ExecutionGraph,
        config_revision: str,
    ) -> int:
        outbound_edges = _matching_outbound_edges(queue_record=queue_record, graph=graph)
        is_agent_completion_event = queue_record.trigger_type == "trigger:agent:run:completed"
        source_node_exists = queue_record.trigger_id in graph.nodes_by_id
        if not source_node_exists and self._run_special_event_handlers(
            queue_record=queue_record,
            graph=graph,
            phase="pre",
            source_node_exists=False,
        ):
            return 0

        if not source_node_exists:
            self._write_skipped_event_reference_record(
                queue_record=queue_record,
                node_id=queue_record.trigger_id,
                node_type=queue_record.trigger_type,
                config_revision=config_revision,
                reason=f"source_node_removed_from_graph:{queue_record.trigger_id}",
            )
            return 0
        logger.debug(
            "Processing workflow event event_id=%s trace_id=%s source_node_id=%s downstream_nodes=%d",
            queue_record.event_id,
            queue_record.trace_id,
            queue_record.trigger_id,
            len(outbound_edges),
        )
        execution_count = 0
        for edge in outbound_edges:
            target_node_id = _edge_target_id(edge)
            node = graph.nodes_by_id.get(target_node_id)
            if node is None:
                self._write_skipped_event_reference_record(
                    queue_record=queue_record,
                    node_id=target_node_id,
                    node_type="workflow:unknown",
                    config_revision=config_revision,
                    reason=f"edge_target_removed_from_graph:{queue_record.trigger_id}->{target_node_id}",
                )
                continue
            if not _edge_allows_cycle(edge) and would_form_cycle(
                queue_record.trigger_id, target_node_id, graph.downstream_index
            ):
                self._write_skipped_node_record(
                    queue_record=queue_record,
                    node=node,
                    config_revision=config_revision,
                    reason="cycle_detected",
                )
                continue

            max_graph_depth = max(
                1, int(getattr(self.worker_config, "max_graph_depth", _DEFAULT_MAX_GRAPH_DEPTH))
            )
            depth = _compute_depth(
                target_node_id, graph.downstream_index, max_graph_depth=max_graph_depth
            )
            if depth > max_graph_depth:
                self._write_skipped_node_record(
                    queue_record=queue_record,
                    node=node,
                    config_revision=config_revision,
                    reason=f"max_depth_exceeded:{depth}",
                )
                continue

            if target_node_id in graph.gates_by_id:
                self._execute_gate(
                    queue_record,
                    gate=graph.gates_by_id[target_node_id],
                    graph=graph,
                    config_revision=config_revision,
                )
                continue

            if target_node_id in graph.effects_by_id and self._dispatch_effect(
                queue_record,
                effect=graph.effects_by_id[target_node_id],
                graph=graph,
                config_revision=config_revision,
            ):
                execution_count += 1
                continue

            if target_node_id in graph.queries_by_id and self._dispatch_query(
                queue_record,
                query=graph.queries_by_id[target_node_id],
                graph=graph,
                config_revision=config_revision,
            ):
                execution_count += 1

        if is_agent_completion_event:
            self._run_special_event_handlers(
                queue_record=queue_record,
                graph=graph,
                phase="post",
                source_node_exists=True,
            )

        return execution_count

    def _build_execution_graph(self) -> ExecutionGraph:
        return _build_execution_graph(self.worker_config)

    def _get_gate_provider(self, gate_type: str) -> Any | None:
        if id(self.gate_provider_factory) != self._gate_provider_factory_cache_id:
            self._gate_providers.clear()
            self._gate_provider_factory_cache_id = id(self.gate_provider_factory)
        cached = self._gate_providers.get(gate_type)
        if cached is not None:
            return cached
        try:
            provider = self.gate_provider_factory.build(gate_type)  # type: ignore[arg-type]
        except KeyError:
            return None
        provider.load_state(worker=self)
        self._gate_providers[gate_type] = provider
        return provider

    def _register_default_node_executors(self) -> None:
        provider_effect_executor = ProviderEffectNodeExecutor(
            provider_factory_getter=lambda: self.provider_factory
        )
        provider_query_executor = ProviderQueryNodeExecutor(
            provider_factory_getter=lambda: self.query_provider_factory
        )

        for gate_type in self.gate_provider_factory.registry.registered_types():
            self.gate_executor_registry.register(gate_type, gate_type)
        for effect_type in self.provider_factory.registry.registered_types():
            self.effect_executor_registry.register(effect_type, provider_effect_executor)
        self.effect_executor_registry.register(
            "effect:event:emit",
            EventEmitEffectExecutor(
                internal_event_bus=InternalEventBus(
                    self.worker_config.internal_event_store_path,
                    journal_path=self.worker_config.workflow_event_journal_path,
                )
            ),
        )
        for query_type in self.query_provider_factory.registry.registered_types():
            self.query_executor_registry.register(query_type, provider_query_executor)

    def _effect_provider_class(self, effect_type: str) -> Any | None:
        registry = getattr(self.provider_factory, "registry", None)
        if registry is None:
            registry = self._effect_provider_class_registry
        if registry is None:
            return None
        try:
            return registry.resolve(effect_type)  # type: ignore[arg-type]
        except (AttributeError, KeyError):
            return None

    def _run_waiting_reconciliation_hooks(self) -> int:
        reconciled = 0
        graph = self._build_execution_graph()
        for hook in self._lifecycle_reconciliation_hooks:
            hook_name = hook.__class__.__name__
            try:
                reconciled += int(hook.reconcile(worker=self, graph=graph) or 0)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Effect orchestrator maintenance reconciliation failed "
                    "phase=waiting_reconciliation hook=%s",
                    hook_name,
                )
        return reconciled

    def _run_post_completion_dispatch_hooks(self) -> int:
        dispatched = 0
        for hook in self._completion_dispatch_hooks:
            hook_name = hook.__class__.__name__
            try:
                dispatched += int(hook.dispatch(worker=self) or 0)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Effect orchestrator maintenance reconciliation failed "
                    "phase=post_completion_dispatch hook=%s",
                    hook_name,
                )
        return dispatched

    def _run_special_event_handlers(
        self,
        *,
        queue_record: TriggerQueueRecord,
        graph: ExecutionGraph,
        phase: Literal["pre", "post"],
        source_node_exists: bool,
    ) -> bool:
        consumed = False
        for handler in self._special_event_handlers:
            result = handler.handle_event(
                worker=self,
                queue_record=queue_record,
                graph=graph,
                phase=phase,
                source_node_exists=source_node_exists,
            )
            normalized = result or SpecialEventResult()
            consumed = consumed or normalized.consume_event
        return consumed

    def _run_force_complete_hooks(self, *, dispatch_id: str) -> bool:
        for handler in self._special_event_handlers:
            if bool(handler.force_complete(worker=self, dispatch_id=dispatch_id)):
                return True
        return False

    def _dispatch_registered_gate(
        self,
        *,
        queue_record: TriggerQueueRecord,
        gate: GateDefinitionDiscriminated,
        graph: ExecutionGraph,
        started_at: datetime,
        config_revision: str,
        upstream_payload: dict[str, dict[str, Any]],
        upstream_output_names: dict[str, set[str]],
        inputs_by_source: dict[str, dict[str, Any]],
        input_event_ids_by_source: dict[str, str],
        input_occurred_at_by_source: dict[str, datetime],
    ) -> bool:
        if self.gate_executor_registry.get(gate.type) is None:
            return False
        provider = self._get_gate_provider(gate.type)
        if provider is None:
            return False
        materialized_gate = materialize_node_definition(gate)
        return bool(
            provider.execute(
                worker=self,
                queue_record=queue_record,
                gate=materialized_gate,
                graph=graph,
                started_at=started_at,
                config_revision=config_revision,
                upstream_payload=upstream_payload,
                upstream_output_names=upstream_output_names,
                inputs_by_source=inputs_by_source,
                input_event_ids_by_source=input_event_ids_by_source,
                input_occurred_at_by_source=input_occurred_at_by_source,
            )
        )

    def _handle_gate_completion_event(
        self, *, queue_record: TriggerQueueRecord, graph: ExecutionGraph
    ) -> int:
        completed = 0
        for gate_type in {gate.type for gate in graph.gates_by_id.values()}:
            provider = self._get_gate_provider(gate_type)
            if provider is None:
                continue
            completed += int(
                provider.handle_completion_event(
                    worker=self, queue_record=queue_record, graph=graph
                )
                or 0
            )
        return completed

    def _reconcile_gate_providers(self, *, graph: ExecutionGraph) -> int:
        reconciled = 0
        for gate_type in {gate.type for gate in graph.gates_by_id.values()}:
            provider: Any | None = None
            try:
                provider = self._get_gate_provider(gate_type)
                if provider is None:
                    continue
                reconciled += int(provider.reconcile(worker=self, graph=graph) or 0)
            except Exception:  # noqa: BLE001
                provider_name = provider.__class__.__name__ if provider is not None else None
                logger.exception(
                    "Effect orchestrator maintenance reconciliation failed "
                    "phase=gate_reconciliation gate_type=%s provider=%s",
                    gate_type,
                    provider_name,
                )
        return reconciled

    @staticmethod
    def _require_object_payload(value: Any, *, gate_type: str) -> dict[str, Any]:
        return require_object_payload(value, gate_type=gate_type)

    @staticmethod
    def _system_value_resolver_for(
        *, started_at: datetime, queue_record: TriggerQueueRecord, node_id: str
    ):
        return _system_value_resolver(
            started_at=started_at,
            trace_id=queue_record.trace_id,
            event_id=queue_record.event_id,
            node_id=node_id,
        )


    def claim_dedupe_key(
        self,
        *,
        gate_id: str,
        key: str,
        ttl: timedelta,
        now: datetime,
        event_id: str,
    ) -> bool:
        return _claim_dedupe_key(
            self.worker_config.runtime_dir / "gate-dedupe-keys.json",
            gate_id=gate_id,
            key=key,
            ttl=ttl,
            now=now,
            event_id=event_id,
        )

    def _dispatch_query(
        self,
        queue_record: TriggerQueueRecord,
        *,
        query: QueryDefinitionDiscriminated,
        graph: ExecutionGraph,
        config_revision: str,
    ) -> bool:
        idempotency_key = _execution_idempotency_key(queue_record.event_id, query.id)
        existing = self.execution_store.find_execution_by_idempotency_key(idempotency_key)
        if existing is not None:
            if existing.status == "running":
                with self._inflight_lock:
                    if idempotency_key in self._inflight:
                        return False
                future = self._executor.submit(
                    self._execute_query_provider,
                    query=query,
                    resolved_config=dict(existing.resolved_config),
                )
                with self._inflight_lock:
                    self._inflight[idempotency_key] = InflightExecution(
                        future=future, config_revision=config_revision
                    )
                return True
            return False
        with self._inflight_lock:
            if idempotency_key in self._inflight:
                return False

        started_at = datetime.now(UTC)
        upstream_payload = _node_output_context_from_payload(
            queue_record.payload,
            source_node_id=queue_record.trigger_id,
        )
        upstream_output_names = _declared_upstream_output_names(
            graph=graph, upstream_payload=upstream_payload
        )
        raw_query_config = _extract_query_config(query)
        try:
            resolved = resolve_effect_config_bindings(
                raw_query_config,
                upstream_payload=upstream_payload,
                declared_upstream_output_names=upstream_output_names,
                system_value_resolver=_system_value_resolver(
                    started_at=started_at,
                    trace_id=queue_record.trace_id,
                    event_id=queue_record.event_id,
                    node_id=query.id,
                ),
                strict_validation=True,
            )
        except BindingResolutionError as exc:
            self._write_failed_effect_record(
                queue_record=queue_record,
                effect=query,
                started_at=started_at,
                resolved_config={},
                config_revision=config_revision,
                error=str(exc),
            )
            return False

        resolved_config = (
            resolved.resolved_config if isinstance(resolved.resolved_config, dict) else {}
        )
        record = EffectExecutionRecord(
            execution_id=f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}",
            idempotency_key=idempotency_key,
            event_id=queue_record.event_id,
            trace_id=queue_record.trace_id,
            source_node_id=queue_record.trigger_id,
            source_trigger_type=queue_record.trigger_type,
            effect_id=query.id,
            effect_type=query.type,
            status="running",
            started_at=started_at,
            finished_at=started_at,
            resolved_config_hash=_json_sha256(resolved_config),
            resolved_config=resolved_config,
            input_payload=dict(queue_record.payload),
            outputs={},
            config_revision=config_revision,
        )
        self.execution_store.record_execution(record)
        future = self._executor.submit(
            self._execute_query_provider, query=query, resolved_config=resolved_config
        )
        with self._inflight_lock:
            self._inflight[idempotency_key] = InflightExecution(
                future=future, config_revision=config_revision
            )
        return True

    def _dispatch_effect(
        self,
        queue_record: TriggerQueueRecord,
        *,
        effect: EffectDefinitionDiscriminated,
        graph: ExecutionGraph,
        config_revision: str,
    ) -> bool:
        idempotency_key = _execution_idempotency_key(queue_record.event_id, effect.id)
        existing = self.execution_store.find_execution_by_idempotency_key(idempotency_key)
        if existing is not None:
            if existing.status == "running":
                with self._inflight_lock:
                    if idempotency_key in self._inflight:
                        return False
                future = self._executor.submit(
                    self._execute_provider,
                    effect=effect,
                    resolved_config=dict(existing.resolved_config),
                    execution_context=_effect_execution_context(
                        idempotency_key=idempotency_key,
                        queue_record=queue_record,
                        effect=effect,
                    ),
                )
                with self._inflight_lock:
                    self._inflight[idempotency_key] = InflightExecution(
                        future=future, config_revision=config_revision
                    )
                return True
            return False
        with self._inflight_lock:
            if idempotency_key in self._inflight:
                return False

        started_at = datetime.now(UTC)
        upstream_payload = _node_output_context_from_payload(
            queue_record.payload,
            source_node_id=queue_record.trigger_id,
        )
        upstream_output_names = _declared_upstream_output_names(
            graph=graph, upstream_payload=upstream_payload
        )
        raw_effect_config = _extract_effect_config(effect)
        try:
            resolved = resolve_effect_config_bindings(
                raw_effect_config,
                upstream_payload=upstream_payload,
                declared_upstream_output_names=upstream_output_names,
                system_value_resolver=_system_value_resolver(
                    started_at=started_at,
                    trace_id=queue_record.trace_id,
                    event_id=queue_record.event_id,
                    node_id=effect.id,
                ),
                strict_validation=True,
            )
        except BindingResolutionError as exc:
            self._write_failed_effect_record(
                queue_record=queue_record,
                effect=effect,
                started_at=started_at,
                resolved_config={},
                config_revision=config_revision,
                error=str(exc),
            )
            return False

        resolved_config = (
            resolved.resolved_config if isinstance(resolved.resolved_config, dict) else {}
        )
        executor = self.effect_executor_registry.get(effect.type)
        if executor is None:
            self._write_failed_effect_record(
                queue_record=queue_record,
                effect=effect,
                started_at=started_at,
                resolved_config=resolved_config,
                config_revision=config_revision,
                error=f"no_effect_executor_registered:{effect.type}",
            )
            return False
        try:
            resolved_config = executor.prepare_resolved_config(
                effect=effect,
                resolved_config=resolved_config,
                upstream_payload=upstream_payload,
                declared_upstream_output_names=upstream_output_names,
                system_value_resolver=_system_value_resolver(
                    started_at=started_at,
                    trace_id=queue_record.trace_id,
                    event_id=queue_record.event_id,
                    node_id=effect.id,
                ),
            )
        except (BindingResolutionError, OSError, ValueError) as exc:
            self._write_failed_effect_record(
                queue_record=queue_record,
                effect=effect,
                started_at=started_at,
                resolved_config=resolved_config,
                config_revision=config_revision,
                error=str(exc),
            )
            return False
        record = EffectExecutionRecord(
            execution_id=f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}",
            idempotency_key=idempotency_key,
            event_id=queue_record.event_id,
            trace_id=queue_record.trace_id,
            source_node_id=queue_record.trigger_id,
            source_trigger_type=queue_record.trigger_type,
            effect_id=effect.id,
            effect_type=effect.type,
            status="running",
            started_at=started_at,
            finished_at=started_at,
            resolved_config_hash=_json_sha256(resolved_config),
            resolved_config=resolved_config,
            input_payload=dict(queue_record.payload),
            outputs={},
            config_revision=config_revision,
        )
        self.execution_store.record_execution(record)
        future = self._executor.submit(
            self._execute_provider,
            effect=effect,
            resolved_config=resolved_config,
            execution_context=_effect_execution_context(
                idempotency_key=idempotency_key,
                queue_record=queue_record,
                effect=effect,
            ),
        )
        with self._inflight_lock:
            self._inflight[idempotency_key] = InflightExecution(
                future=future, config_revision=config_revision
            )
        return True

    def _execute_gate(
        self,
        queue_record: TriggerQueueRecord,
        *,
        gate: GateDefinitionDiscriminated,
        graph: ExecutionGraph,
        config_revision: str,
    ) -> None:
        idempotency_key = _execution_idempotency_key(queue_record.event_id, gate.id)
        if self.gate_execution_store.find_execution_by_idempotency_key(idempotency_key) is not None:
            return

        started_at = datetime.now(UTC)
        upstream_payload = _node_output_context_from_payload(
            queue_record.payload,
            source_node_id=queue_record.trigger_id,
        )
        upstream_output_names = _declared_upstream_output_names(
            graph=graph, upstream_payload=upstream_payload
        )
        inputs_by_source = {queue_record.trigger_id: dict(queue_record.payload)}
        input_event_ids_by_source = {queue_record.trigger_id: queue_record.event_id}
        input_occurred_at_by_source = {queue_record.trigger_id: queue_record.occurred_at}

        try:
            if self._dispatch_registered_gate(
                queue_record=queue_record,
                gate=gate,
                graph=graph,
                started_at=started_at,
                config_revision=config_revision,
                upstream_payload=upstream_payload,
                upstream_output_names=upstream_output_names,
                inputs_by_source=inputs_by_source,
                input_event_ids_by_source=input_event_ids_by_source,
                input_occurred_at_by_source=input_occurred_at_by_source,
            ):
                return

            self._write_gate_record(
                queue_record=queue_record,
                gate=gate,
                status="failed",
                started_at=started_at,
                resolved_config={},
                outputs={},
                inputs_by_source=inputs_by_source,
                input_event_ids_by_source=input_event_ids_by_source,
                input_occurred_at_by_source=input_occurred_at_by_source,
                config_revision=config_revision,
                error=f"no_gate_provider_registered:{gate.type}",
            )
        except (BindingResolutionError, GateRuntimeError) as exc:
            self._write_gate_record(
                queue_record=queue_record,
                gate=gate,
                status="failed",
                started_at=started_at,
                resolved_config={},
                outputs={},
                inputs_by_source=inputs_by_source,
                input_event_ids_by_source=input_event_ids_by_source,
                input_occurred_at_by_source=input_occurred_at_by_source,
                config_revision=config_revision,
                error=str(exc),
            )

    def _execute_provider(
        self,
        *,
        effect: EffectDefinitionDiscriminated,
        resolved_config: dict[str, Any],
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        executor = self.effect_executor_registry.resolve(effect.type)
        return dict(
            executor.execute(
                effect=effect,
                resolved_config=resolved_config,
                execution_context=dict(execution_context or {}),
            )
            or {}
        )

    def _execute_query_provider(
        self,
        *,
        query: QueryDefinitionDiscriminated,
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        executor = self.query_executor_registry.resolve(query.type)
        return dict(executor.execute(query=query, resolved_config=resolved_config) or {})

    def _list_vm_summaries_for_reconciliation(self) -> list[Any]:
        admin_service = getattr(self.provider_factory, "admin_service", None)
        if admin_service is None:
            return []
        return list(
            admin_service.list_vms(
                include_inactive=True,
                omit_state=False,
                vm_user="agent",
                directory_path=None,
                direct_only=False,
            )
        )

    def _list_vm_summaries_for_await_agent_reconciliation(self) -> list[Any]:
        return self._list_vm_summaries_for_reconciliation()

    def _reap_completed_futures(self) -> tuple[int, int]:
        completed_count = 0
        published_count = 0
        to_finalize: list[tuple[str, Future[dict[str, Any]]]] = []
        with self._inflight_lock:
            for idempotency_key, inflight in list(self._inflight.items()):
                if inflight.future.done():
                    to_finalize.append((idempotency_key, inflight.future))
                    self._inflight.pop(idempotency_key, None)

        for idempotency_key, future in to_finalize:
            completed_count += 1
            record = self.execution_store.find_execution_by_idempotency_key(idempotency_key)
            if record is None:
                continue
            finished_at = datetime.now(UTC)
            try:
                outputs = dict(future.result() or {})
                provider_class = self._effect_provider_class(record.effect_type)
                waits_for_completion = bool(
                    provider_class is not None
                    and provider_class.on_execution_started(record=record, outputs=outputs)
                )
                if waits_for_completion:
                    record.status = "waiting"
                    record.outputs = outputs
                    record.published_event_id = None
                    record.completion_event_enqueued_at = None
                    record.finished_at = finished_at
                    self.execution_store.record_execution(record)
                    self._update_event_delivery_state(record.event_id)
                    if provider_class is not None:
                        provider_class.on_execution_waiting(worker=self, record=record)
                    continue
                published_event_id = self._publish_completion_event_from_record(
                    record=record, outputs=outputs
                )
                record.status = "succeeded"
                record.outputs = outputs
                if outputs.get("success") is False:
                    message = outputs.get("message")
                    if isinstance(message, str) and message.strip():
                        record.error = message.strip()
                record.published_event_id = published_event_id
                record.completion_event_enqueued_at = finished_at
                published_count += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "Effect execution failed event_id=%s trace_id=%s effect_id=%s effect_type=%s",
                    record.event_id,
                    record.trace_id,
                    record.effect_id,
                    record.effect_type,
                )
                record.status = "failed"
                record.error = str(exc)
            record.finished_at = finished_at
            self.execution_store.record_execution(record)
            self._update_event_delivery_state(record.event_id)

        return completed_count, published_count

    def _wait_for_inflight_completion(self, *, timeout_seconds: float) -> None:
        with self._inflight_lock:
            futures = [inflight.future for inflight in self._inflight.values()]
        if not futures:
            return
        wait(futures, timeout=timeout_seconds, return_when=FIRST_COMPLETED)

    def _update_event_delivery_state(self, event_id: str) -> None:
        if self._event_has_inflight_effects(event_id):
            self.event_queue.mark_inflight(event_id)
            return
        self.event_queue.mark_delivered(event_id)

    def _event_has_inflight_effects(self, event_id: str) -> bool:
        for record in self.execution_store.list_executions():
            if record.event_id != event_id:
                continue
            if record.status != "running":
                continue
            return True
        return False

    def _publish_completion_event_from_record(
        self,
        *,
        record: EffectExecutionRecord,
        outputs: dict[str, Any],
        branch_port: str | None = None,
    ) -> str:
        event_prefix = "effect" if branch_port is None else f"effect:{branch_port}"
        completion_event_id = _completion_event_id(
            parent_event_id=record.event_id,
            node_id=record.effect_id,
            prefix=event_prefix,
        )
        completion_event = TriggerEvent(
            event_id=completion_event_id,
            trigger_id=record.effect_id,
            trigger_type=record.effect_type,
            occurred_at=datetime.now(UTC),
            trace_id=record.trace_id,
            payload=_payload_with_node_output_context(
                source_payload=record.input_payload,
                source_node_id=record.source_node_id,
                completed_node_id=record.effect_id,
                outputs=outputs,
                reset_context=False,
                branch_port=branch_port,
            ),
        )
        idempotency_key = (
            record.idempotency_key
            if branch_port is None
            else f"{record.idempotency_key}:{branch_port}"
        )
        self.event_queue.publish(completion_event, idempotency_key=idempotency_key)
        return completion_event_id

    def _publish_gate_completion_event_from_record(
        self,
        *,
        record: GateExecutionRecord,
        outputs: dict[str, Any],
        branch_port: str | None = None,
        for_execution_ids: dict[str, str] | None = None,
    ) -> str:
        completion_event_id = _completion_event_id(
            parent_event_id=record.event_id,
            node_id=record.gate_id,
            prefix="gate",
        )
        completion_event = TriggerEvent(
            event_id=completion_event_id,
            trigger_id=record.gate_id,
            trigger_type=record.gate_type,
            occurred_at=datetime.now(UTC),
            trace_id=record.trace_id,
            payload=_payload_with_node_output_context(
                source_payload=record.input_payload,
                source_node_id=record.source_node_id,
                completed_node_id=record.gate_id,
                outputs=outputs,
                reset_context=record.gate_type == "gate:merge",
                branch_port=branch_port,
                for_execution_ids=for_execution_ids,
            ),
        )
        self.event_queue.publish(completion_event, idempotency_key=record.idempotency_key)
        return completion_event_id

    def _write_skipped_node_record(
        self,
        *,
        queue_record: TriggerQueueRecord,
        node: WorkflowNodeDefinition,
        config_revision: str | None = None,
        reason: str,
    ) -> None:
        if _is_gate(node):
            self._write_gate_record(
                queue_record=queue_record,
                gate=node,
                status="skipped",
                started_at=datetime.now(UTC),
                resolved_config={},
                outputs={},
                inputs_by_source={queue_record.trigger_id: dict(queue_record.payload)},
                input_event_ids_by_source={queue_record.trigger_id: queue_record.event_id},
                input_occurred_at_by_source={queue_record.trigger_id: queue_record.occurred_at},
                config_revision=config_revision,
                error=reason,
            )
            return

        self._write_terminal_effect_record(
            queue_record=queue_record,
            effect=node,
            status="skipped",
            started_at=datetime.now(UTC),
            resolved_config={},
            config_revision=config_revision,
            error=reason,
        )

    def _write_skipped_event_reference_record(
        self,
        *,
        queue_record: TriggerQueueRecord,
        node_id: str,
        node_type: str,
        config_revision: str | None,
        reason: str,
    ) -> None:
        idempotency_key = _execution_idempotency_key(queue_record.event_id, node_id)
        if self.execution_store.find_execution_by_idempotency_key(idempotency_key) is not None:
            return
        now = datetime.now(UTC)
        self.execution_store.record_execution(
            EffectExecutionRecord(
                execution_id=f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}",
                idempotency_key=idempotency_key,
                event_id=queue_record.event_id,
                trace_id=queue_record.trace_id,
                source_node_id=queue_record.trigger_id,
                source_trigger_type=queue_record.trigger_type,
                effect_id=node_id,
                effect_type=node_type,
                status="skipped",
                started_at=now,
                finished_at=now,
                resolved_config_hash=_json_sha256({}),
                resolved_config={},
                input_payload=dict(queue_record.payload),
                outputs={},
                error=reason,
                config_revision=config_revision,
            )
        )

    def _write_failed_effect_record(
        self,
        *,
        queue_record: TriggerQueueRecord,
        effect: EffectDefinitionDiscriminated | QueryDefinitionDiscriminated,
        started_at: datetime,
        resolved_config: dict[str, Any],
        config_revision: str | None = None,
        error: str,
    ) -> None:
        self._write_terminal_effect_record(
            queue_record=queue_record,
            effect=effect,
            status="failed",
            started_at=started_at,
            resolved_config=resolved_config,
            config_revision=config_revision,
            error=error,
        )

    def _write_terminal_effect_record(
        self,
        *,
        queue_record: TriggerQueueRecord,
        effect: EffectDefinitionDiscriminated | QueryDefinitionDiscriminated,
        status: str,
        started_at: datetime,
        resolved_config: dict[str, Any],
        config_revision: str | None = None,
        error: str,
    ) -> None:
        idempotency_key = _execution_idempotency_key(queue_record.event_id, effect.id)
        if self.execution_store.find_execution_by_idempotency_key(idempotency_key) is not None:
            return
        finished_at = datetime.now(UTC)
        record = EffectExecutionRecord(
            execution_id=f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}",
            idempotency_key=idempotency_key,
            event_id=queue_record.event_id,
            trace_id=queue_record.trace_id,
            source_node_id=queue_record.trigger_id,
            source_trigger_type=queue_record.trigger_type,
            effect_id=effect.id,
            effect_type=effect.type,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            resolved_config_hash=_json_sha256(resolved_config),
            resolved_config=resolved_config,
            input_payload=dict(queue_record.payload),
            outputs={},
            error=error,
            config_revision=config_revision,
        )
        self.execution_store.record_execution(record)

    def _write_gate_record(
        self,
        *,
        queue_record: TriggerQueueRecord,
        gate: GateDefinitionDiscriminated,
        status: str,
        started_at: datetime,
        resolved_config: dict[str, Any],
        outputs: dict[str, Any],
        inputs_by_source: dict[str, dict[str, Any]],
        input_event_ids_by_source: dict[str, str],
        input_occurred_at_by_source: dict[str, datetime],
        config_revision: str | None = None,
        error: str | None = None,
        publish_completion_event: bool = False,
        branch_port: str | None = None,
        for_execution_ids: dict[str, str] | None = None,
    ) -> None:
        idempotency_key = _execution_idempotency_key(queue_record.event_id, gate.id)
        if self.gate_execution_store.find_execution_by_idempotency_key(idempotency_key) is not None:
            return
        finished_at = datetime.now(UTC)
        record = GateExecutionRecord(
            execution_id=f"gate-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}",
            idempotency_key=idempotency_key,
            event_id=queue_record.event_id,
            trace_id=queue_record.trace_id,
            source_node_id=queue_record.trigger_id,
            source_trigger_type=queue_record.trigger_type,
            gate_id=gate.id,
            gate_type=gate.type,
            status=status,
            started_at=started_at,
            finished_at=finished_at,
            resolved_config_hash=_json_sha256(resolved_config),
            resolved_config=resolved_config,
            input_payload=dict(queue_record.payload),
            outputs=outputs,
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            error=error,
            config_revision=config_revision,
        )
        if publish_completion_event:
            record.published_event_id = self._publish_gate_completion_event_from_record(
                record=record,
                outputs=outputs,
                branch_port=branch_port,
                for_execution_ids=for_execution_ids,
            )
            record.completion_event_enqueued_at = finished_at
        self.gate_execution_store.record_execution(record)

    def _log_graph_revision_change(
        self, *, config_revision: str, node_count: int, edge_count: int
    ) -> None:
        if self._last_graph_revision == config_revision:
            return
        logger.info(
            "Workflow graph revision changed revision=%s previous_revision=%s nodes=%d->%d edges=%d->%d",
            config_revision,
            self._last_graph_revision,
            self._last_graph_node_count,
            node_count,
            self._last_graph_edge_count,
            edge_count,
        )
        self._last_graph_revision = config_revision
        self._last_graph_node_count = node_count
        self._last_graph_edge_count = edge_count


class JsonExecutionStore(JsonExecutionRepository[EffectExecutionRecord]):
    def __init__(self, path: Path, *, journal_path: Path | None = None) -> None:
        super().__init__(
            path, EffectExecutionRecord, journal_path=journal_path, source="effect_execution"
        )


class JsonGateExecutionStore(JsonExecutionRepository[GateExecutionRecord]):
    def __init__(self, path: Path, *, journal_path: Path | None = None) -> None:
        super().__init__(
            path, GateExecutionRecord, journal_path=journal_path, source="gate_execution"
        )

    def find_latest_by_gate_id(self, gate_id: str) -> GateExecutionRecord | None:
        return self.find_latest_execution_for_gate(gate_id)

    def find_latest_execution_for_gate(self, gate_id: str) -> GateExecutionRecord | None:
        latest: GateExecutionRecord | None = None
        for record in self.list_executions():
            if record.gate_id != gate_id:
                continue
            if latest is None or (record.finished_at, record.started_at, record.execution_id) > (
                latest.finished_at,
                latest.started_at,
                latest.execution_id,
            ):
                latest = record
        return latest


def build_effect_orchestrator_worker(
    config: GatewayConfig,
    *,
    project_dir: Path | None = None,
    services: RuntimeAssemblyInterface | None = None,
) -> EffectOrchestratorWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = load_trigger_orchestrator_worker_global_config(
        resolved_project_dir, base_config=config
    )
    config_reloader = TriggerOrchestratorWorkerGlobalConfigReloader(
        project_dir=resolved_project_dir,
        initial_config=worker_config,
        base_config=config,
    )
    effective_config = GatewayConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "runtime_dir": worker_config.runtime_dir,
        }
    )
    service_bundle = services or PlatformServiceBundle(runtime_builder=build_runtime)
    runtime = service_bundle.build_runtime(effective_config)
    return EffectOrchestratorWorker(
        config=effective_config,
        worker_config=worker_config,
        config_reloader=config_reloader,
        runtime=runtime,
    )


def main() -> None:
    import argparse

    from agent_sandbox.bootstrap import load_runtime_gateway_config

    parser = argparse.ArgumentParser(description="Run the effect orchestrator worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument(
        "--log-level", default=os.environ.get("EFFECT_ORCHESTRATOR_LOG_LEVEL", "INFO")
    )
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logging_level = getattr(logging, str(args.log_level).upper(), None)
    if not isinstance(logging_level, int):
        raise SystemExit(f"Invalid --log-level value: {args.log_level}")
    logging.basicConfig(
        level=logging_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )

    runtime_dir = Path(args.runtime_dir).expanduser().resolve()
    config = load_runtime_gateway_config(runtime_dir=runtime_dir)
    worker = build_effect_orchestrator_worker(config, project_dir=runtime_dir.parent)
    if args.once:
        result = worker.run_once_with_lock()
        print(
            f"Effect orchestrator cycle complete pending={result.pending_events_scanned} "
            f"processed={result.events_processed} executed={result.effect_executions} "
            f"published={result.completion_events_published} sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return
    worker.run_forever()


def _build_execution_graph(worker_config: TriggerOrchestratorWorkerGlobalConfig) -> ExecutionGraph:
    nodes_by_id: dict[str, WorkflowNodeDefinition] = {
        node.id: node
        for node in (
            *worker_config.triggers,
            *worker_config.gates,
            *worker_config.effects,
            *worker_config.queries,
        )
    }
    edges = list(worker_config.edges)
    acyclic_edges = [edge for edge in edges if not _edge_allows_cycle(edge)]

    return ExecutionGraph(
        nodes_by_id=nodes_by_id,
        gates_by_id={gate.id: gate for gate in worker_config.gates},
        effects_by_id={effect.id: effect for effect in worker_config.effects},
        queries_by_id={query.id: query for query in worker_config.queries},
        outbound_index=_build_outbound_edge_index(edges),
        downstream_index=build_downstream_index(acyclic_edges),
        inbound_index=build_inbound_edge_index(edges),
        declared_outputs=index_declared_outputs(
            worker_config.triggers,
            worker_config.gates,
            worker_config.effects,
            worker_config.queries,
        ),
    )


def _extract_effect_config(effect: EffectDefinitionDiscriminated) -> dict[str, Any]:
    return dict(effect.config)


def _extract_query_config(query: QueryDefinitionDiscriminated) -> dict[str, Any]:
    return dict(query.config)


def _claim_dedupe_key(
    store_path: Path,
    *,
    gate_id: str,
    key: str,
    ttl: timedelta,
    now: datetime,
    event_id: str,
) -> bool:
    now_utc = _ensure_utc(now)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = store_path.with_suffix(f"{store_path.suffix}.lock")
    lock_path.touch(exist_ok=True)
    with lock_path.open("r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            payload = _read_dedupe_payload(store_path)
            entries = payload.setdefault("entries", {})
            if not isinstance(entries, dict):
                entries = {}
                payload["entries"] = entries
            _prune_expired_dedupe_entries(entries, now=now_utc)
            entry_id = _dedupe_entry_id(gate_id=gate_id, key=key)
            existing = entries.get(entry_id)
            if isinstance(existing, dict):
                expires_at = _parse_dedupe_timestamp(existing.get("expires_at"))
                if expires_at is not None and expires_at > now_utc:
                    _write_dedupe_payload(store_path, payload)
                    return False
            expires_at = now_utc + ttl
            entries[entry_id] = {
                "gate_id": gate_id,
                "key": key,
                "first_event_id": event_id,
                "seen_at": _format_dedupe_timestamp(now_utc),
                "expires_at": _format_dedupe_timestamp(expires_at),
            }
            _write_dedupe_payload(store_path, payload)
            return True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_dedupe_payload(store_path: Path) -> dict[str, Any]:
    if not store_path.exists():
        return {"version": 1, "entries": {}}
    try:
        payload = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unable to read gate dedupe state path=%s", store_path, exc_info=True)
        return {"version": 1, "entries": {}}
    if not isinstance(payload, dict):
        return {"version": 1, "entries": {}}
    payload.setdefault("version", 1)
    payload.setdefault("entries", {})
    return payload


def _write_dedupe_payload(store_path: Path, payload: dict[str, Any]) -> None:
    tmp_path = store_path.with_suffix(f"{store_path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(store_path)


def _prune_expired_dedupe_entries(entries: dict[str, Any], *, now: datetime) -> None:
    expired = []
    for entry_id, entry in entries.items():
        if not isinstance(entry, dict):
            expired.append(entry_id)
            continue
        expires_at = _parse_dedupe_timestamp(entry.get("expires_at"))
        if expires_at is None or expires_at <= now:
            expired.append(entry_id)
    for entry_id in expired:
        entries.pop(entry_id, None)


def _dedupe_entry_id(*, gate_id: str, key: str) -> str:
    digest = hashlib.sha256(f"{gate_id}\0{key}".encode("utf-8")).hexdigest()
    return digest[:32]


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_dedupe_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return _ensure_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _format_dedupe_timestamp(value: datetime) -> str:
    return _ensure_utc(value).isoformat().replace("+00:00", "Z")


def _execution_idempotency_key(event_id: str, node_id: str) -> str:
    return f"{event_id}::{node_id}"


def _node_output_context_from_payload(
    payload: dict[str, Any], *, source_node_id: str
) -> dict[str, dict[str, Any]]:
    raw_context = payload.get(NODE_OUTPUTS_CONTEXT_KEY)
    if isinstance(raw_context, dict):
        context: dict[str, dict[str, Any]] = {}
        for node_id, node_payload in raw_context.items():
            if not isinstance(node_id, str) or not isinstance(node_payload, dict):
                continue
            context[node_id] = dict(node_payload)
        if context:
            return context

    flat_payload = {
        key: value
        for key, value in payload.items()
        if key
        not in {NODE_OUTPUTS_CONTEXT_KEY, _BRANCH_PORT_PAYLOAD_KEY, _FOR_EXECUTION_IDS_PAYLOAD_KEY}
    }
    return {source_node_id: flat_payload}


def _declared_upstream_output_names(
    *,
    graph: ExecutionGraph,
    upstream_payload: dict[str, dict[str, Any]],
) -> dict[str, set[str]]:
    declared: dict[str, set[str]] = {}
    for node_id, node_payload in upstream_payload.items():
        declared_keys = set(graph.declared_outputs.get(node_id, {}).keys())
        declared[node_id] = declared_keys | set(node_payload.keys())
    return declared


def _system_value_resolver(
    *,
    started_at: datetime,
    trace_id: str | None,
    event_id: str,
    node_id: str,
):
    def _resolve(path: str) -> Any:
        normalized = str(path).strip()
        if normalized == "now.utc_timestamp":
            return datetime.now(UTC).isoformat().replace("+00:00", "Z")
        if normalized == "now.utc_timestamp_compact":
            return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        if normalized == "now.unix_seconds":
            return int(time.time())
        if normalized == "execution.trace_id":
            return trace_id or ""
        if normalized == "execution.event_id":
            return event_id
        if normalized == "execution.node_id":
            return node_id
        if normalized == "execution.started_at":
            return started_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        if normalized == "execution.started_at_compact":
            return started_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        if normalized == "unique.compact":
            return (
                f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{random.randint(0, 999999):06d}"
            )
        raise BindingResolutionError(f"Unknown system binding {normalized!r}")

    return _resolve


def _payload_with_node_output_context(
    *,
    source_payload: dict[str, Any],
    source_node_id: str,
    completed_node_id: str,
    outputs: dict[str, Any],
    reset_context: bool,
    branch_port: str | None = None,
    for_execution_ids: dict[str, str] | None = None,
) -> dict[str, Any]:
    validate_output_mapping_keys(
        outputs,
        context=f"{completed_node_id} runtime outputs",
    )
    payload = dict(outputs)
    inherited_context = _node_output_context_from_payload(
        source_payload, source_node_id=source_node_id
    )
    merged_for_execution_ids = _merged_for_execution_ids(
        source_payload=source_payload,
        for_execution_ids=for_execution_ids,
    )
    if reset_context:
        payload[NODE_OUTPUTS_CONTEXT_KEY] = {completed_node_id: dict(outputs)}
        if branch_port is not None:
            payload[_BRANCH_PORT_PAYLOAD_KEY] = branch_port
        if merged_for_execution_ids:
            payload[_FOR_EXECUTION_IDS_PAYLOAD_KEY] = merged_for_execution_ids
        return payload
    merged_context = {
        node_id: dict(node_payload) for node_id, node_payload in inherited_context.items()
    }
    merged_context[completed_node_id] = dict(outputs)
    payload[NODE_OUTPUTS_CONTEXT_KEY] = merged_context
    if branch_port is not None:
        payload[_BRANCH_PORT_PAYLOAD_KEY] = branch_port
    if merged_for_execution_ids:
        payload[_FOR_EXECUTION_IDS_PAYLOAD_KEY] = merged_for_execution_ids
    return payload


def _completion_event_id(*, parent_event_id: str, node_id: str, prefix: str) -> str:
    return f"{prefix}:{node_id}:{hashlib.sha1(parent_event_id.encode('utf-8')).hexdigest()[:12]}"


def _config_revision_hash(worker_config: TriggerOrchestratorWorkerGlobalConfig) -> str:
    payload = worker_config.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _compute_depth(
    node_id: str, downstream_index: dict[str, tuple[str, ...]], *, max_graph_depth: int
) -> int:
    try:
        return compute_downstream_depth(node_id, downstream_index)
    except WorkflowGraphCycleError:
        return max(1, int(max_graph_depth)) + 1


def _is_gate(node: WorkflowNodeDefinition) -> bool:
    return str(getattr(node, "type", "")).startswith("gate:")


def _edge_source_id(edge: Any) -> str:
    for field_name in ("source_id",):
        if isinstance(edge, dict):
            value = edge.get(field_name)
        else:
            value = getattr(edge, field_name, None)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    raise ValueError(f"workflow edge is missing a source id: {edge!r}")


def _edge_target_id(edge: Any) -> str:
    for field_name in ("target_id",):
        if isinstance(edge, dict):
            value = edge.get(field_name)
        else:
            value = getattr(edge, field_name, None)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    raise ValueError(f"workflow edge is missing a target id: {edge!r}")


def _edge_source_port(edge: Any) -> str | None:
    if isinstance(edge, dict):
        value = edge.get("source_port")
    else:
        value = getattr(edge, "source_port", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _edge_allows_cycle(edge: Any) -> bool:
    if isinstance(edge, dict):
        return bool(edge.get("allow_cycle", False))
    return bool(getattr(edge, "allow_cycle", False))


def _build_outbound_edge_index(edges: list[Any]) -> dict[str, tuple[Any, ...]]:
    outbound: dict[str, list[Any]] = {}
    for edge in edges:
        source_id = _edge_source_id(edge)
        outbound.setdefault(source_id, []).append(edge)
    return {source_id: tuple(values) for source_id, values in sorted(outbound.items())}


def _matching_outbound_edges(
    *, queue_record: TriggerQueueRecord, graph: ExecutionGraph
) -> tuple[Any, ...]:
    branch_port = _normalize_optional_string(queue_record.payload.get(_BRANCH_PORT_PAYLOAD_KEY))
    resume_target_node_id = _normalize_optional_string(
        queue_record.payload.get("__resume_target_node_id")
    )
    matched: list[Any] = []
    for edge in graph.outbound_index.get(queue_record.trigger_id, ()):
        if resume_target_node_id is not None and _edge_target_id(edge) != resume_target_node_id:
            continue
        edge_port = _edge_source_port(edge)
        if branch_port is None:
            if edge_port is not None:
                continue
        elif edge_port != branch_port:
            continue
        matched.append(edge)
    return tuple(matched)


def _for_execution_ids_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    raw_value = payload.get(_FOR_EXECUTION_IDS_PAYLOAD_KEY)
    if not isinstance(raw_value, dict):
        return {}
    normalized: dict[str, str] = {}
    for gate_id, execution_id in raw_value.items():
        if not isinstance(gate_id, str):
            continue
        normalized_gate_id = gate_id.strip()
        normalized_execution_id = _normalize_optional_string(execution_id)
        if normalized_gate_id and normalized_execution_id is not None:
            normalized[normalized_gate_id] = normalized_execution_id
    return normalized


def _merged_for_execution_ids(
    *, source_payload: dict[str, Any], for_execution_ids: dict[str, str] | None
) -> dict[str, str]:
    merged = _for_execution_ids_from_payload(source_payload)
    if for_execution_ids:
        merged.update(
            {
                gate_id.strip(): execution_id
                for gate_id, execution_id in for_execution_ids.items()
                if gate_id.strip() and _normalize_optional_string(execution_id) is not None
            }
        )
    return merged


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_worker_path(
    worker_config: TriggerOrchestratorWorkerGlobalConfig, attribute: str, *, fallback: Path
) -> Path:
    value = getattr(worker_config, attribute, None)
    return Path(value) if value is not None else Path(fallback)


def _compute_next_sleep_seconds(*, poll_interval_seconds: float, jitter_seconds: float) -> float:
    delay = poll_interval_seconds
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    return max(0.0, delay)


def _compute_backoff_sleep_seconds(*, poll_interval_seconds: float, jitter_seconds: float) -> float:
    delay = max(1.0, poll_interval_seconds)
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    return max(1.0, delay)


def build_effect_cursor_repository(path: Path) -> JsonEffectCursorRepository:
    return JsonEffectCursorRepository(path)


def _effect_execution_context(
    *,
    idempotency_key: str,
    queue_record: TriggerQueueRecord,
    effect: EffectDefinitionDiscriminated,
) -> dict[str, Any]:
    input_payload = dict(queue_record.payload)
    context = {
        "idempotency_key": idempotency_key,
        "workflow_run_id": queue_record.trace_id,
        "workflow_event_id": queue_record.event_id,
        "workflow_trace_id": queue_record.trace_id,
        "node_id": effect.id,
        "node_type": effect.type,
        "input_payload": input_payload,
    }
    if isinstance(input_payload.get(NODE_OUTPUTS_CONTEXT_KEY), dict):
        context[NODE_OUTPUTS_CONTEXT_KEY] = input_payload[NODE_OUTPUTS_CONTEXT_KEY]
    return context
