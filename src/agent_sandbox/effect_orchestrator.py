from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from .agent_completion_watcher import build_agent_run_completed_event
from .admin_service import AdminService
from .agent_prompt_queue import AgentPromptQueueStore
from .config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from .effects.registry import EffectProviderFactory
from .events.models import (
    EffectDefinitionDiscriminated,
    GateDefinitionDiscriminated,
    NODE_OUTPUTS_CONTEXT_KEY,
    QueryDefinitionDiscriminated,
    validate_output_mapping_keys,
    TriggerDefinitionDiscriminated,
    TriggerEvent,
)
from .gates.registry import GateProviderFactory
from .gate_runtime import (
    GateRuntimeError,
    require_object_payload,
)
from .host_heartbeat_worker import _single_instance_lock
from .provider_config import ProviderConfigResolver
from .queries.registry import QueryProviderFactory
from .runtime import RuntimeContext
from .app_bootstrap import build_runtime
from .platform_integrations import PlatformServiceBundle, RuntimeAssemblyInterface
from .triggers.queue import TriggerEventQueue, TriggerQueueRecord
from .workflow_runtime_config import (
    FixedTriggerOrchestratorWorkerGlobalConfigReloader,
    TriggerOrchestratorWorkerGlobalConfigReloader,
    load_trigger_orchestrator_worker_global_config,
)
from .workflow_events import WorkflowEventJournal
from .workflow_binding import (
    BindingResolutionError,
    resolve_effect_config_bindings,
)
from .workflow_graph import (
    WorkflowGraphCycleError,
    build_downstream_index,
    build_inbound_edge_index,
    compute_downstream_depth,
    index_declared_outputs,
    would_form_cycle,
)
from .workflow_engine.node_executors import (
    NodeExecutorRegistry,
    ProviderEffectNodeExecutor,
    ProviderQueryNodeExecutor,
)

logger = logging.getLogger(__name__)

_MAX_GRAPH_DEPTH = 16
_MAX_EVENTS_PER_CYCLE = 200
_AWAIT_AGENT_STALE_GRACE_SECONDS = 120
_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"
WorkflowNodeDefinition = (
    TriggerDefinitionDiscriminated | GateDefinitionDiscriminated | EffectDefinitionDiscriminated | QueryDefinitionDiscriminated
)
TRecord = TypeVar("TRecord", bound=BaseModel)


class EffectExecutionRecord(BaseModel):
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


class GateExecutionRecord(BaseModel):
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


class EffectOrchestratorWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: TriggerOrchestratorWorkerGlobalConfig,
        config_reloader: TriggerOrchestratorWorkerGlobalConfigReloader | None = None,
        runtime: RuntimeContext,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self._config_reloader = config_reloader or FixedTriggerOrchestratorWorkerGlobalConfigReloader(worker_config)
        self.runtime = runtime
        self.workflow_event_journal = WorkflowEventJournal(worker_config.workflow_event_journal_path)
        self.event_queue = TriggerEventQueue(
            worker_config.event_queue_store_path,
            journal_path=worker_config.workflow_event_journal_path,
        )
        self.lock_path = _resolve_worker_path(
            worker_config,
            "gate_lock_path",
            fallback=worker_config.effect_lock_path or (worker_config.runtime_dir / "effect-orchestrator.lock"),
        )
        self.cursor_path = _resolve_worker_path(
            worker_config,
            "gate_cursor_store_path",
            fallback=worker_config.effect_cursor_store_path or (worker_config.runtime_dir / "effect-orchestrator-cursors.json"),
        )
        self.execution_store_path = worker_config.effect_execution_store_path
        self.execution_store = JsonExecutionStore(
            self.execution_store_path,
            journal_path=worker_config.workflow_event_journal_path,
        )
        self.gate_execution_store_path = _resolve_worker_path(
            worker_config,
            "gate_execution_store_path",
            fallback=worker_config.runtime_dir / "gate-orchestrator-executions.json",
        )
        self.gate_execution_store = JsonGateExecutionStore(
            self.gate_execution_store_path,
            journal_path=worker_config.workflow_event_journal_path,
        )
        self.queued_agent_prompt_store = AgentPromptQueueStore(worker_config.runtime_dir / "queued-agent-prompts.json")
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, min(32, max(1, (os.cpu_count() or 1) * 4))),
            thread_name_prefix="effect-orchestrator",
        )
        self._inflight: dict[str, InflightExecution] = {}
        self._inflight_lock = RLock()
        self._last_graph_revision: str | None = None
        self._last_graph_node_count = 0
        self._last_graph_edge_count = 0
        self.provider_factory = EffectProviderFactory(
            ProviderConfigResolver(config),
            admin_service=AdminService(config.runtime_dir),
            env=dict(os.environ),
        )
        self.query_provider_factory = QueryProviderFactory(
            ProviderConfigResolver(config),
            admin_service=AdminService(config.runtime_dir),
            env=dict(os.environ),
        )
        self.gate_provider_factory = GateProviderFactory(
            ProviderConfigResolver(config),
            admin_service=AdminService(config.runtime_dir),
            env=dict(os.environ),
        )
        self.gate_executor_registry = NodeExecutorRegistry()
        self.effect_executor_registry = NodeExecutorRegistry()
        self.query_executor_registry = NodeExecutorRegistry()
        self._register_default_node_executors()
        self._gate_providers: dict[str, Any] = {}
        self.event_queue.reset_inflight()

    def run_forever(self) -> None:
        with _single_instance_lock(self.lock_path):
            logger.info(
                "Workflow orchestrator started poll_interval=%.3f jitter=%.3f lock=%s",
                self.worker_config.poll_interval_seconds,
                self.worker_config.jitter_seconds,
                self.lock_path,
            )
            while True:
                try:
                    result = self.run_once()
                except Exception:
                    logger.exception("Workflow orchestrator cycle failed")
                    time.sleep(
                        _compute_backoff_sleep_seconds(
                            poll_interval_seconds=self.worker_config.poll_interval_seconds,
                            jitter_seconds=self.worker_config.jitter_seconds,
                        )
                    )
                    continue
                logger.debug(
                    "Workflow orchestrator cycle complete pending=%d processed=%d executed=%d published=%d sleep_seconds=%.3f",
                    result.pending_events_scanned,
                    result.events_processed,
                    result.effect_executions,
                    result.completion_events_published,
                    result.next_sleep_seconds,
                )
                time.sleep(result.next_sleep_seconds)

    def run_once_with_lock(self) -> EffectOrchestratorCycleResult:
        with _single_instance_lock(self.lock_path):
            return self.run_once()

    def force_complete_await_agent(self, dispatch_id: str) -> bool:
        graph = _build_execution_graph(self.worker_config)
        provider = self._get_gate_provider("gate:await-agent")
        if provider is None:
            return False
        return bool(provider.force_complete(worker=self, graph=graph, dispatch_id=dispatch_id))

    def run_once(self) -> EffectOrchestratorCycleResult:
        latest_config, _changed = self._config_reloader.get_latest()
        self.worker_config = latest_config
        config_revision = _config_revision_hash(latest_config)
        cursor_payload = _load_cursor_payload(self.cursor_path)
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
        completion_events_published += self._complete_waiting_agent_prompt_effects_directly()
        completion_events_published += self._dispatch_queued_agent_prompts_directly()

        completed_count, published_count = self._reap_completed_futures()
        effect_executions_completed += completed_count
        completion_events_published += published_count
        completion_events_published += self._dispatch_queued_agent_prompts_directly()

        while events_processed < _MAX_EVENTS_PER_CYCLE:
            pending = self.event_queue.list_pending(limit=1)
            if not pending:
                break
            pending_events_scanned += 1
            queue_record = pending[0]
            execution_count = self._process_event_record(queue_record, graph=graph, config_revision=config_revision)
            effect_executions_started += execution_count
            events_processed += 1
            self._update_event_delivery_state(queue_record.event_id)
            _update_cursor_payload(cursor_payload, queue_record)

        _save_cursor_payload(self.cursor_path, cursor_payload)
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
        if is_agent_completion_event and not source_node_exists:
            self._handle_agent_completion_event(queue_record=queue_record, graph=graph)
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
            if not _edge_allows_cycle(edge) and would_form_cycle(queue_record.trigger_id, target_node_id, graph.downstream_index):
                self._write_skipped_node_record(
                    queue_record=queue_record,
                    node=node,
                    config_revision=config_revision,
                    reason="cycle_detected",
                )
                continue

            depth = _compute_depth(target_node_id, graph.downstream_index)
            if depth > _MAX_GRAPH_DEPTH:
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
            self._handle_agent_completion_event(queue_record=queue_record, graph=graph)

        return execution_count

    def _handle_agent_completion_event(
        self,
        *,
        queue_record: TriggerQueueRecord,
        graph: ExecutionGraph,
    ) -> None:
        self._complete_waiting_agent_prompt_effects(queue_record=queue_record)
        self._handle_gate_completion_event(queue_record=queue_record, graph=graph)

    def _get_gate_provider(self, gate_type: str) -> Any | None:
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
        provider_effect_executor = ProviderEffectNodeExecutor(self.provider_factory)
        provider_query_executor = ProviderQueryNodeExecutor(self.query_provider_factory)

        for gate_type in ("gate:filter", "gate:map", "gate:merge", "gate:await-agent", "gate:for"):
            self.gate_executor_registry.register(gate_type, gate_type)
        for effect_type in (
            "effect:vm:create",
            "effect:vm:start",
            "effect:vm:stop",
            "effect:agent:prompt",
            "effect:agent:enqueue-prompt",
            "effect:bash:script",
            "effect:vm:exec",
            "effect:vm:copy-files",
            "effect:vm:delete-files",
            "effect:issue:update-state",
            "effect:session:update-metadata",
            "effect:git:promote-agent-branch",
            "effect:pr:create",
        ):
            self.effect_executor_registry.register(effect_type, provider_effect_executor)
        for query_type in ("query:vm:run-state", "query:git:pr-note", "query:session-context", "query:issue:get"):
            self.query_executor_registry.register(query_type, provider_query_executor)

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
        return bool(
            provider.execute(
                worker=self,
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
            )
        )

    def _handle_gate_completion_event(self, *, queue_record: TriggerQueueRecord, graph: ExecutionGraph) -> int:
        completed = 0
        for gate_type in {gate.type for gate in graph.gates_by_id.values()}:
            provider = self._get_gate_provider(gate_type)
            if provider is None:
                continue
            completed += int(provider.handle_completion_event(worker=self, queue_record=queue_record, graph=graph) or 0)
        return completed

    def _reconcile_gate_providers(self, *, graph: ExecutionGraph) -> int:
        reconciled = 0
        for gate_type in {gate.type for gate in graph.gates_by_id.values()}:
            provider = self._get_gate_provider(gate_type)
            if provider is None:
                continue
            reconciled += int(provider.reconcile(worker=self, graph=graph) or 0)
        return reconciled

    @staticmethod
    def _require_object_payload(value: Any, *, gate_type: str) -> dict[str, Any]:
        return require_object_payload(value, gate_type=gate_type)

    @staticmethod
    def _system_value_resolver_for(*, started_at: datetime, queue_record: TriggerQueueRecord, node_id: str):
        return _system_value_resolver(
            started_at=started_at,
            trace_id=queue_record.trace_id,
            event_id=queue_record.event_id,
            node_id=node_id,
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
        existing = self.execution_store.find_by_idempotency_key(idempotency_key)
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
                    self._inflight[idempotency_key] = InflightExecution(future=future, config_revision=config_revision)
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
        upstream_output_names = _declared_upstream_output_names(graph=graph, upstream_payload=upstream_payload)
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

        resolved_config = resolved.resolved_config if isinstance(resolved.resolved_config, dict) else {}
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
        self.execution_store.put(record)
        future = self._executor.submit(self._execute_query_provider, query=query, resolved_config=resolved_config)
        with self._inflight_lock:
            self._inflight[idempotency_key] = InflightExecution(future=future, config_revision=config_revision)
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
        existing = self.execution_store.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            if existing.status == "running":
                with self._inflight_lock:
                    if idempotency_key in self._inflight:
                        return False
                future = self._executor.submit(
                    self._execute_provider,
                    effect=effect,
                    resolved_config=dict(existing.resolved_config),
                )
                with self._inflight_lock:
                    self._inflight[idempotency_key] = InflightExecution(future=future, config_revision=config_revision)
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
        upstream_output_names = _declared_upstream_output_names(graph=graph, upstream_payload=upstream_payload)
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

        resolved_config = resolved.resolved_config if isinstance(resolved.resolved_config, dict) else {}
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
        self.execution_store.put(record)
        future = self._executor.submit(self._execute_provider, effect=effect, resolved_config=resolved_config)
        with self._inflight_lock:
            self._inflight[idempotency_key] = InflightExecution(future=future, config_revision=config_revision)
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
        if self.gate_execution_store.find_by_idempotency_key(idempotency_key) is not None:
            return

        started_at = datetime.now(UTC)
        upstream_payload = _node_output_context_from_payload(
            queue_record.payload,
            source_node_id=queue_record.trigger_id,
        )
        upstream_output_names = _declared_upstream_output_names(graph=graph, upstream_payload=upstream_payload)
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

    def _execute_provider(self, *, effect: EffectDefinitionDiscriminated, resolved_config: dict[str, Any]) -> dict[str, Any]:
        executor = self.effect_executor_registry.resolve(effect.type)
        return dict(executor.execute(effect=effect, resolved_config=resolved_config) or {})

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
            record = self.execution_store.find_by_idempotency_key(idempotency_key)
            if record is None:
                continue
            finished_at = datetime.now(UTC)
            try:
                outputs = dict(future.result() or {})
                if self._effect_waits_for_completion(record=record, outputs=outputs):
                    record.status = "waiting"
                    record.outputs = outputs
                    record.published_event_id = None
                    record.completion_event_enqueued_at = None
                    record.finished_at = finished_at
                    self.execution_store.put(record)
                    self._update_event_delivery_state(record.event_id)
                    continue
                published_event_id = self._publish_completion_event_from_record(record=record, outputs=outputs)
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
            self.execution_store.put(record)
            self._update_event_delivery_state(record.event_id)

        return completed_count, published_count

    @staticmethod
    def _effect_waits_for_completion(*, record: EffectExecutionRecord, outputs: dict[str, Any]) -> bool:
        if record.effect_type != "effect:agent:prompt":
            return False
        if outputs.get("success") is False:
            return False
        if str(outputs.get("output_mode") or "").strip() != "on_completion":
            return False
        return bool(str(outputs.get("dispatch_id") or "").strip())

    def _complete_waiting_agent_prompt_effects(self, *, queue_record: TriggerQueueRecord) -> int:
        dispatch_id = str(queue_record.payload.get("dispatch_id") or "").strip()
        if not dispatch_id:
            return 0

        finalized = 0
        for record in self.execution_store.read_all().values():
            if record.effect_type != "effect:agent:prompt" or record.status != "waiting":
                continue
            record_dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
            if record_dispatch_id != dispatch_id:
                continue

            finished_at = datetime.now(UTC)
            merged_outputs = {
                **record.outputs,
                **{
                    key: value
                    for key, value in queue_record.payload.items()
                    if key not in {NODE_OUTPUTS_CONTEXT_KEY, _BRANCH_PORT_PAYLOAD_KEY, _FOR_EXECUTION_IDS_PAYLOAD_KEY}
                },
            }
            published_event_id = self._publish_completion_event_from_record(record=record, outputs=merged_outputs)
            record.status = "succeeded"
            record.outputs = merged_outputs
            record.published_event_id = published_event_id
            record.completion_event_enqueued_at = finished_at
            record.finished_at = finished_at
            self.execution_store.put(record)
            self._update_event_delivery_state(record.event_id)
            finalized += 1
        return finalized

    def _complete_waiting_agent_prompt_effects_directly(self) -> int:
        waiting_records = [
            record
            for record in self.execution_store.read_all().values()
            if record.effect_type == "effect:agent:prompt" and record.status == "waiting"
        ]
        if not waiting_records:
            return 0

        try:
            summaries = self._list_vm_summaries_for_reconciliation()
        except Exception:
            logger.exception("Waiting agent-prompt reconciliation failed while listing VMs")
            return 0

        completed = 0
        for record in waiting_records:
            queue_record = self._build_completion_queue_record_for_waiting_agent_prompt(record=record, summaries=summaries)
            if queue_record is None:
                continue
            if self._complete_waiting_agent_prompt_effects(queue_record=queue_record):
                completed += 1
        return completed

    def _build_completion_queue_record_for_waiting_agent_prompt(
        self,
        *,
        record: EffectExecutionRecord,
        summaries: list[Any],
    ) -> TriggerQueueRecord | None:
        dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
        if not dispatch_id:
            return None

        session_id, vm_name = self._waiting_agent_prompt_target(record=record)
        matching_summary = self._match_waiting_agent_prompt_summary(
            summaries=summaries,
            dispatch_id=dispatch_id,
            session_id=session_id,
            vm_name=vm_name,
            record=record,
        )
        if matching_summary is None:
            return None
        if str(getattr(matching_summary, "agent_state", "") or "").strip() == "running":
            return None

        completion_event = build_agent_run_completed_event(
            trigger_id="__await-agent-completion__",
            dispatch_id=dispatch_id,
            vm_user=str(record.resolved_config.get("vm_user") or "agent"),
            summary=matching_summary,
            session_id=session_id,
            directory_path=(getattr(matching_summary, "directory_paths", None) or [None])[0],
        )
        return TriggerQueueRecord(
            event_id=completion_event.event_id,
            trigger_id=completion_event.trigger_id,
            trigger_type=completion_event.trigger_type,
            trace_id=completion_event.trace_id or record.trace_id,
            occurred_at=completion_event.occurred_at,
            payload=completion_event.payload,
            delivery_state="delivered",
            delivered_at=datetime.now(UTC),
            idempotency_key=f"__await-agent-completion__:{dispatch_id}",
        )

    @staticmethod
    def _waiting_agent_prompt_target(record: EffectExecutionRecord) -> tuple[str | None, str | None]:
        session_id = str(record.resolved_config.get("session_id") or "").strip() or None
        vm_name = str(record.resolved_config.get("vm_name") or "").strip() or None
        target = str(record.resolved_config.get("target") or "").strip() or None
        if target and vm_name is None and target.startswith("vm-"):
            vm_name = target
        if target and session_id is None and not target.startswith("vm-"):
            session_id = target
        return session_id, vm_name

    @staticmethod
    def _match_waiting_agent_prompt_summary(
        *,
        summaries: list[Any],
        dispatch_id: str,
        session_id: str | None,
        vm_name: str | None,
        record: EffectExecutionRecord,
    ) -> Any | None:
        exact_match = next(
            (
                summary
                for summary in summaries
                if str(getattr(summary, "dispatch_id", "") or "").strip() == dispatch_id
                and (vm_name is None or str(getattr(summary, "vm_name", "") or "").strip() == vm_name)
                and (
                    session_id is None
                    or session_id in {str(item or "").strip() for item in (getattr(summary, "session_ids", []) or [])}
                )
            ),
            None,
        )
        if exact_match is not None:
            return exact_match

        if (datetime.now(UTC) - record.finished_at).total_seconds() < _AWAIT_AGENT_STALE_GRACE_SECONDS:
            return None

        if vm_name is not None:
            vm_match = next(
                (
                    summary
                    for summary in summaries
                    if str(getattr(summary, "vm_name", "") or "").strip() == vm_name
                    and not str(getattr(summary, "dispatch_id", "") or "").strip()
                    and str(getattr(summary, "agent_state", "") or "").strip() != "running"
                ),
                None,
            )
            if vm_match is not None:
                return vm_match

        if session_id is None:
            return None

        return next(
            (
                summary
                for summary in summaries
                if session_id in {str(item or "").strip() for item in (getattr(summary, "session_ids", []) or [])}
                and not str(getattr(summary, "dispatch_id", "") or "").strip()
                and str(getattr(summary, "agent_state", "") or "").strip() != "running"
            ),
            None,
        )

    def _update_event_delivery_state(self, event_id: str) -> None:
        if self._event_has_inflight_effects(event_id):
            self.event_queue.mark_inflight(event_id)
            return
        self.event_queue.mark_delivered(event_id)

    def _event_has_inflight_effects(self, event_id: str) -> bool:
        for record in self.execution_store.read_all().values():
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
    ) -> str:
        completion_event_id = _completion_event_id(
            parent_event_id=record.event_id,
            node_id=record.effect_id,
            prefix="effect",
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
            ),
        )
        self.event_queue.publish(completion_event, idempotency_key=record.idempotency_key)
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
        if self.execution_store.find_by_idempotency_key(idempotency_key) is not None:
            return
        now = datetime.now(UTC)
        self.execution_store.put(
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
        if self.execution_store.find_by_idempotency_key(idempotency_key) is not None:
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
        self.execution_store.put(record)

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
        if self.gate_execution_store.find_by_idempotency_key(idempotency_key) is not None:
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
        self.gate_execution_store.put(record)


    def _log_graph_revision_change(self, *, config_revision: str, node_count: int, edge_count: int) -> None:
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


class _JsonRecordStore(Generic[TRecord]):
    def __init__(self, path: Path, model: type[TRecord], *, journal_path: Path | None = None, source: str | None = None) -> None:
        self.path = path
        self.model = model
        self._journal = WorkflowEventJournal(journal_path) if journal_path is not None else None
        self._journal_source = source
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def read_all(self) -> dict[str, TRecord]:
        with self._lock:
            payload = self._read_payload()
            return {key: self.model.model_validate(value) for key, value in payload.items()}

    def put(self, record: TRecord) -> None:
        with self._lock:
            payload = self._read_payload()
            payload[getattr(record, "execution_id")] = record.model_dump(mode="json")
            self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        if self._journal is not None and self._journal_source is not None:
            self._journal.append(
                "workflow.execution.updated",
                source=self._journal_source,
                subject_id=str(getattr(record, "execution_id")),
                payload={
                    "event_id": str(getattr(record, "event_id", "")),
                    "status": str(getattr(record, "status", "")),
                },
            )

    def find_by_idempotency_key(self, idempotency_key: str) -> TRecord | None:
        with self._lock:
            for value in self._read_payload().values():
                record = self.model.model_validate(value)
                if getattr(record, "idempotency_key") == idempotency_key:
                    return record
        return None

    def _read_payload(self) -> dict[str, Any]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        return payload


class JsonExecutionStore(_JsonRecordStore[EffectExecutionRecord]):
    def __init__(self, path: Path, *, journal_path: Path | None = None) -> None:
        super().__init__(path, EffectExecutionRecord, journal_path=journal_path, source="effect_execution")


class JsonGateExecutionStore(_JsonRecordStore[GateExecutionRecord]):
    def __init__(self, path: Path, *, journal_path: Path | None = None) -> None:
        super().__init__(path, GateExecutionRecord, journal_path=journal_path, source="gate_execution")

    def find_latest_by_gate_id(self, gate_id: str) -> GateExecutionRecord | None:
        latest: GateExecutionRecord | None = None
        for record in self.read_all().values():
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
    worker_config = load_trigger_orchestrator_worker_global_config(resolved_project_dir, base_config=config)
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

    from .bootstrap import load_runtime_gateway_config

    parser = argparse.ArgumentParser(description="Run the effect orchestrator worker.")
    parser.add_argument("--runtime-dir", default="runtime")
    parser.add_argument("--log-level", default=os.environ.get("EFFECT_ORCHESTRATOR_LOG_LEVEL", "INFO"))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    logging_level = getattr(logging, str(args.log_level).upper(), None)
    if not isinstance(logging_level, int):
        raise SystemExit(f"Invalid --log-level value: {args.log_level}")
    logging.basicConfig(level=logging_level, format="%(asctime)s %(levelname)s %(name)s %(message)s")

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
        for node in (*worker_config.triggers, *worker_config.gates, *worker_config.effects, *worker_config.queries)
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
    payload = effect.model_dump(mode="json")
    for field_name in ("id", "display_name", "type", "outputs"):
        payload.pop(field_name, None)
    return payload


def _extract_query_config(query: QueryDefinitionDiscriminated) -> dict[str, Any]:
    payload = query.model_dump(mode="json")
    for field_name in ("id", "display_name", "type", "outputs"):
        payload.pop(field_name, None)
    return payload


def _execution_idempotency_key(event_id: str, node_id: str) -> str:
    return f"{event_id}::{node_id}"


def _node_output_context_from_payload(payload: dict[str, Any], *, source_node_id: str) -> dict[str, dict[str, Any]]:
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
        if key not in {NODE_OUTPUTS_CONTEXT_KEY, _BRANCH_PORT_PAYLOAD_KEY, _FOR_EXECUTION_IDS_PAYLOAD_KEY}
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
            return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}-{random.randint(0, 999999):06d}"
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
    inherited_context = _node_output_context_from_payload(source_payload, source_node_id=source_node_id)
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
    merged_context = {node_id: dict(node_payload) for node_id, node_payload in inherited_context.items()}
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
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _compute_depth(node_id: str, downstream_index: dict[str, tuple[str, ...]]) -> int:
    try:
        return compute_downstream_depth(node_id, downstream_index)
    except WorkflowGraphCycleError:
        return _MAX_GRAPH_DEPTH + 1


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


def _matching_outbound_edges(*, queue_record: TriggerQueueRecord, graph: ExecutionGraph) -> tuple[Any, ...]:
    branch_port = _normalize_optional_string(queue_record.payload.get(_BRANCH_PORT_PAYLOAD_KEY))
    matched: list[Any] = []
    for edge in graph.outbound_index.get(queue_record.trigger_id, ()):
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

def _merged_for_execution_ids(*, source_payload: dict[str, Any], for_execution_ids: dict[str, str] | None) -> dict[str, str]:
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


def _resolve_worker_path(worker_config: TriggerOrchestratorWorkerGlobalConfig, attribute: str, *, fallback: Path) -> Path:
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


def _load_cursor_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unable to read effect orchestrator cursor state path=%s", path, exc_info=True)
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def _save_cursor_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _update_cursor_payload(payload: dict[str, Any], queue_record: TriggerQueueRecord) -> None:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    payload["last_processed_event_id"] = queue_record.event_id
    payload["last_processed_source_node_id"] = queue_record.trigger_id
    payload["last_processed_at"] = now
