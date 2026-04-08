from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import time
from copy import deepcopy
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

from .admin_service import AdminService
from .agent_completion_watcher import build_agent_run_completed_event
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
from .gate_runtime import (
    GateRuntimeError,
    filter_when_matches,
    filter_payload_matches,
    require_object_payload,
)
from .host_heartbeat_worker import _single_instance_lock
from .provider_config import ProviderConfigResolver
from .queries.registry import QueryProviderFactory
from .runtime import RuntimeContext, build_runtime
from .storage import JsonStore
from .triggers.queue import TriggerEventQueue, TriggerQueueRecord
from .workflow_binding import (
    BindingResolutionError,
    resolve_effect_config_bindings,
    resolve_gate_map_bindings,
    resolve_workflow_bindings,
)
from .workflow_graph import (
    WorkflowGraphCycleError,
    build_downstream_index,
    build_inbound_edge_index,
    compute_downstream_depth,
    index_declared_outputs,
    would_form_cycle,
)

logger = logging.getLogger(__name__)

_MAX_GRAPH_DEPTH = 16
_MAX_EVENTS_PER_CYCLE = 200
_AWAIT_AGENT_STALE_GRACE_SECONDS = 120
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


class AwaitAgentPendingRecord(BaseModel):
    gate_id: str = Field(min_length=1)
    dispatch_id: str = Field(min_length=1)
    prompt_event_id: str = Field(min_length=1)
    prompt_source_node_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    session_id: str | None = None
    vm_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    occurred_at: datetime


@dataclass(frozen=True)
class ExecutionGraph:
    nodes_by_id: dict[str, WorkflowNodeDefinition]
    gates_by_id: dict[str, GateDefinitionDiscriminated]
    effects_by_id: dict[str, EffectDefinitionDiscriminated]
    queries_by_id: dict[str, QueryDefinitionDiscriminated]
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


class EffectOrchestratorWorker:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        worker_config: TriggerOrchestratorWorkerGlobalConfig,
        runtime: RuntimeContext,
    ) -> None:
        self.config = config
        self.worker_config = worker_config
        self.runtime = runtime
        self.event_queue = TriggerEventQueue(worker_config.event_queue_store_path)
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
        self.execution_store = JsonExecutionStore(self.execution_store_path)
        self.gate_execution_store_path = _resolve_worker_path(
            worker_config,
            "gate_execution_store_path",
            fallback=worker_config.runtime_dir / "gate-orchestrator-executions.json",
        )
        self.gate_execution_store = JsonGateExecutionStore(self.gate_execution_store_path)
        self.await_agent_store = JsonStore(worker_config.runtime_dir / "await-agent-pending.json", AwaitAgentPendingRecord)
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, min(32, max(1, (os.cpu_count() or 1) * 4))),
            thread_name_prefix="effect-orchestrator",
        )
        self._inflight: dict[str, Future[dict[str, Any]]] = {}
        self._inflight_lock = RLock()
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
        normalized_dispatch_id = str(dispatch_id or "").strip()
        if not normalized_dispatch_id:
            raise ValueError("dispatch_id is required")
        _key, pending = next(
            (
                (record_key, record)
                for record_key, record in self.await_agent_store.read_all().items()
                if record.dispatch_id == normalized_dispatch_id
            ),
            (None, None),
        )
        if pending is None:
            return False
        graph = _build_execution_graph(self.worker_config)
        completed = self._complete_pending_await_agent_directly(
            pending=pending,
            gate=graph.gates_by_id.get(pending.gate_id),
            log_skips=True,
            key=_key,
        )
        if completed:
            self.run_once_with_lock()
        return completed

    def run_once(self) -> EffectOrchestratorCycleResult:
        cursor_payload = _load_cursor_payload(self.cursor_path)
        pending_events_scanned = 0
        events_processed = 0
        effect_executions_started = 0
        effect_executions_completed = 0
        completion_events_published = 0

        graph = _build_execution_graph(self.worker_config)
        self._reconcile_await_agent_pending(graph=graph)
        completion_events_published += self._complete_pending_await_agent_gates_directly(graph=graph)
        completion_events_published += self._complete_waiting_agent_prompt_effects_directly()

        completed_count, published_count = self._reap_completed_futures()
        effect_executions_completed += completed_count
        completion_events_published += published_count

        while events_processed < _MAX_EVENTS_PER_CYCLE:
            pending = self.event_queue.list_pending(limit=1)
            if not pending:
                break
            pending_events_scanned += 1
            queue_record = pending[0]
            execution_count = self._process_event_record(queue_record, graph=graph)
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

    def _process_event_record(self, queue_record: TriggerQueueRecord, *, graph: ExecutionGraph) -> int:
        downstream_node_ids = graph.downstream_index.get(queue_record.trigger_id, ())
        logger.debug(
            "Processing workflow event event_id=%s trace_id=%s source_node_id=%s downstream_nodes=%d",
            queue_record.event_id,
            queue_record.trace_id,
            queue_record.trigger_id,
            len(downstream_node_ids),
        )
        execution_count = 0
        for target_node_id in downstream_node_ids:
            node = graph.nodes_by_id.get(target_node_id)
            if node is None:
                continue
            if would_form_cycle(queue_record.trigger_id, target_node_id, graph.downstream_index):
                self._write_skipped_node_record(queue_record=queue_record, node=node, reason="cycle_detected")
                continue

            depth = _compute_depth(target_node_id, graph.downstream_index)
            if depth > _MAX_GRAPH_DEPTH:
                self._write_skipped_node_record(
                    queue_record=queue_record,
                    node=node,
                    reason=f"max_depth_exceeded:{depth}",
                )
                continue

            if target_node_id in graph.gates_by_id:
                self._execute_gate(queue_record, gate=graph.gates_by_id[target_node_id], graph=graph)
                continue

            if target_node_id in graph.effects_by_id and self._dispatch_effect(
                queue_record,
                effect=graph.effects_by_id[target_node_id],
                graph=graph,
            ):
                execution_count += 1
                continue

            if target_node_id in graph.queries_by_id and self._dispatch_query(
                queue_record,
                query=graph.queries_by_id[target_node_id],
                graph=graph,
            ):
                execution_count += 1

        if queue_record.trigger_type == "trigger:agent:run:completed":
            self._complete_waiting_agent_prompt_effects(queue_record=queue_record)
            for gate in graph.gates_by_id.values():
                if getattr(gate, "type", "") != "gate:await-agent":
                    continue
                self._execute_await_agent_completion(queue_record=queue_record, gate=gate)

        return execution_count

    def _dispatch_query(
        self,
        queue_record: TriggerQueueRecord,
        *,
        query: QueryDefinitionDiscriminated,
        graph: ExecutionGraph,
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
                    self._inflight[idempotency_key] = future
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
        )
        self.execution_store.put(record)
        future = self._executor.submit(self._execute_query_provider, query=query, resolved_config=resolved_config)
        with self._inflight_lock:
            self._inflight[idempotency_key] = future
        return True

    def _dispatch_effect(
        self,
        queue_record: TriggerQueueRecord,
        *,
        effect: EffectDefinitionDiscriminated,
        graph: ExecutionGraph,
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
                    self._inflight[idempotency_key] = future
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
                error=str(exc),
            )
            return False

        resolved_config = resolved.resolved_config if isinstance(resolved.resolved_config, dict) else {}
        try:
            resolved_config = _prepare_effect_resolved_config(
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
        )
        self.execution_store.put(record)
        future = self._executor.submit(self._execute_provider, effect=effect, resolved_config=resolved_config)
        with self._inflight_lock:
            self._inflight[idempotency_key] = future
        return True

    def _execute_gate(
        self,
        queue_record: TriggerQueueRecord,
        *,
        gate: GateDefinitionDiscriminated,
        graph: ExecutionGraph,
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
            if gate.type == "gate:filter":
                raw_filter_config = gate.when if gate.when is not None else gate.match
                resolved = resolve_workflow_bindings(
                    raw_filter_config,
                    upstream_payload,
                    upstream_output_names,
                    system_value_resolver=_system_value_resolver(
                        started_at=started_at,
                        trace_id=queue_record.trace_id,
                        event_id=queue_record.event_id,
                        node_id=gate.id,
                    ),
                    strict_validation=True,
                )
                resolved_filter = resolved.resolved_config
                resolved_config = {"when": resolved_filter} if gate.when is not None else {"match": resolved_filter}
                matches = (
                    filter_when_matches(resolved_filter)
                    if gate.when is not None
                    else filter_payload_matches(resolved_filter, queue_record.payload)
                )
                if matches:
                    outputs = {
                        key: value for key, value in queue_record.payload.items() if key != NODE_OUTPUTS_CONTEXT_KEY
                    }
                    self._write_gate_record(
                        queue_record=queue_record,
                        gate=gate,
                        status="succeeded",
                        started_at=started_at,
                        resolved_config=resolved_config,
                        outputs=outputs,
                        inputs_by_source=inputs_by_source,
                        input_event_ids_by_source=input_event_ids_by_source,
                        input_occurred_at_by_source=input_occurred_at_by_source,
                        publish_completion_event=True,
                    )
                    return

                self._write_gate_record(
                    queue_record=queue_record,
                    gate=gate,
                    status="dropped",
                    started_at=started_at,
                    resolved_config=resolved_config,
                    outputs={},
                    inputs_by_source=inputs_by_source,
                    input_event_ids_by_source=input_event_ids_by_source,
                    input_occurred_at_by_source=input_occurred_at_by_source,
                )
                return

            if gate.type == "gate:map":
                resolved = resolve_gate_map_bindings(
                    gate.template,
                    upstream_payload,
                    upstream_output_names,
                    system_value_resolver=_system_value_resolver(
                        started_at=started_at,
                        trace_id=queue_record.trace_id,
                        event_id=queue_record.event_id,
                        node_id=gate.id,
                    ),
                    strict_validation=True,
                )
                outputs = require_object_payload(resolved.resolved_config, gate_type="gate:map")
                self._write_gate_record(
                    queue_record=queue_record,
                    gate=gate,
                    status="succeeded",
                    started_at=started_at,
                    resolved_config={"template": outputs},
                    outputs=outputs,
                    inputs_by_source=inputs_by_source,
                    input_event_ids_by_source=input_event_ids_by_source,
                    input_occurred_at_by_source=input_occurred_at_by_source,
                    publish_completion_event=True,
                )
                return

            if gate.type == "gate:await-agent":
                resolved = resolve_workflow_bindings(
                    {
                        "session_id": gate.session_id,
                        "vm_name": gate.vm_name,
                    },
                    upstream_payload,
                    upstream_output_names,
                    system_value_resolver=_system_value_resolver(
                        started_at=started_at,
                        trace_id=queue_record.trace_id,
                        event_id=queue_record.event_id,
                        node_id=gate.id,
                    ),
                    strict_validation=True,
                )
                self._register_await_agent_prompt(
                    queue_record=queue_record,
                    gate=gate,
                    resolved_config=require_object_payload(resolved.resolved_config, gate_type="gate:await-agent"),
                )
                return

            self._execute_merge_gate(
                queue_record=queue_record,
                gate=gate,
                graph=graph,
                started_at=started_at,
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
                error=str(exc),
            )

    def _execute_merge_gate(
        self,
        *,
        queue_record: TriggerQueueRecord,
        gate: GateDefinitionDiscriminated,
        graph: ExecutionGraph,
        started_at: datetime,
    ) -> None:
        previous = self.gate_execution_store.find_latest_by_gate_id(gate.id)
        inputs_by_source = deepcopy(previous.inputs_by_source) if previous is not None else {}
        input_event_ids_by_source = dict(previous.input_event_ids_by_source) if previous is not None else {}
        input_occurred_at_by_source = dict(previous.input_occurred_at_by_source) if previous is not None else {}

        inputs_by_source[queue_record.trigger_id] = dict(queue_record.payload)
        input_event_ids_by_source[queue_record.trigger_id] = queue_record.event_id
        input_occurred_at_by_source[queue_record.trigger_id] = queue_record.occurred_at

        required_source_ids = tuple(_edge_source_id(edge) for edge in graph.inbound_index.get(gate.id, ()))
        shared_output_keys = tuple(sorted((gate.outputs or {}).keys()))
        resolved_config = {"shared_output_keys": list(shared_output_keys)}
        outputs = {key: queue_record.payload[key] for key in shared_output_keys if key in queue_record.payload}
        self._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config=resolved_config,
            outputs=outputs,
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            publish_completion_event=True,
        )

    def _register_await_agent_prompt(
        self,
        *,
        queue_record: TriggerQueueRecord,
        gate: GateDefinitionDiscriminated,
        resolved_config: dict[str, Any],
    ) -> None:
        started_at = datetime.now(UTC)
        dispatch_id = str(queue_record.payload.get("dispatch_id") or "").strip()
        session_id = str(resolved_config.get("session_id") or "").strip() or None
        vm_name = str(resolved_config.get("vm_name") or "").strip() or None
        if not dispatch_id:
            self._write_gate_record(
                queue_record=queue_record,
                gate=gate,
                status="failed",
                started_at=started_at,
                resolved_config=resolved_config,
                outputs={},
                inputs_by_source={queue_record.trigger_id: dict(queue_record.payload)},
                input_event_ids_by_source={queue_record.trigger_id: queue_record.event_id},
                input_occurred_at_by_source={queue_record.trigger_id: queue_record.occurred_at},
                error="gate:await-agent requires upstream effect output dispatch_id",
            )
            return
        if session_id is None and vm_name is None:
            self._write_gate_record(
                queue_record=queue_record,
                gate=gate,
                status="failed",
                started_at=started_at,
                resolved_config=resolved_config,
                outputs={},
                inputs_by_source={queue_record.trigger_id: dict(queue_record.payload)},
                input_event_ids_by_source={queue_record.trigger_id: queue_record.event_id},
                input_occurred_at_by_source={queue_record.trigger_id: queue_record.occurred_at},
                error="gate:await-agent requires resolved session_id or vm_name",
            )
            return

        key = f"{gate.id}:{dispatch_id}"
        self.await_agent_store.put(
            key,
            AwaitAgentPendingRecord(
                gate_id=gate.id,
                dispatch_id=dispatch_id,
                prompt_event_id=queue_record.event_id,
                prompt_source_node_id=queue_record.trigger_id,
                trace_id=queue_record.trace_id,
                session_id=session_id,
                vm_name=vm_name,
                payload=dict(queue_record.payload),
                occurred_at=queue_record.occurred_at,
            ),
        )
        self._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="waiting",
            started_at=started_at,
            resolved_config={
                **resolved_config,
                "dispatch_id": dispatch_id,
                "completion_trigger_id": getattr(gate, "completion_trigger_id", None),
            },
            outputs={},
            inputs_by_source={queue_record.trigger_id: dict(queue_record.payload)},
            input_event_ids_by_source={queue_record.trigger_id: queue_record.event_id},
            input_occurred_at_by_source={queue_record.trigger_id: queue_record.occurred_at},
        )

    def _execute_await_agent_completion(
        self,
        *,
        queue_record: TriggerQueueRecord,
        gate: GateDefinitionDiscriminated,
    ) -> None:
        started_at = datetime.now(UTC)
        dispatch_id = str(queue_record.payload.get("dispatch_id") or "").strip()
        if not dispatch_id:
            return
        key = f"{gate.id}:{dispatch_id}"
        pending = self.await_agent_store.get(key)
        if pending is None:
            return

        outputs = {
            key_name: value
            for key_name, value in queue_record.payload.items()
            if key_name != NODE_OUTPUTS_CONTEXT_KEY
        }
        merged_payload = _payload_with_node_output_context(
            source_payload=pending.payload,
            source_node_id=pending.prompt_source_node_id,
            completed_node_id=queue_record.trigger_id,
            outputs=outputs,
            reset_context=False,
        )
        merged_queue_record = TriggerQueueRecord(
            event_id=queue_record.event_id,
            trigger_id=queue_record.trigger_id,
            trigger_type=queue_record.trigger_type,
            trace_id=queue_record.trace_id,
            occurred_at=queue_record.occurred_at,
            payload=merged_payload,
            delivery_state=queue_record.delivery_state,
            delivered_at=queue_record.delivered_at,
            idempotency_key=queue_record.idempotency_key,
        )
        self._write_gate_record(
            queue_record=merged_queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config={"dispatch_id": dispatch_id, "completion_trigger_id": getattr(gate, "completion_trigger_id", None)},
            outputs=outputs,
            inputs_by_source={
                pending.prompt_source_node_id: dict(pending.payload),
                queue_record.trigger_id: dict(queue_record.payload),
            },
            input_event_ids_by_source={
                pending.prompt_source_node_id: pending.prompt_event_id,
                queue_record.trigger_id: queue_record.event_id,
            },
            input_occurred_at_by_source={
                pending.prompt_source_node_id: pending.occurred_at,
                queue_record.trigger_id: queue_record.occurred_at,
            },
            publish_completion_event=True,
        )
        self.await_agent_store.delete(key)

    def _reconcile_await_agent_pending(self, *, graph: ExecutionGraph) -> None:
        pending_by_key = self.await_agent_store.read_all()
        if not pending_by_key:
            return

        try:
            summaries = self._list_vm_summaries_for_await_agent_reconciliation()
        except Exception:
            logger.exception("Await-agent reconciliation failed while listing VMs")
            return

        summaries_by_vm_name: dict[str, list[Any]] = {}
        summaries_by_session_id: dict[str, list[Any]] = {}
        for summary in summaries:
            vm_name = str(getattr(summary, "vm_name", "") or "").strip()
            if vm_name:
                summaries_by_vm_name.setdefault(vm_name, []).append(summary)
            for session_id in getattr(summary, "session_ids", []) or ():
                normalized_session_id = str(session_id or "").strip()
                if normalized_session_id:
                    summaries_by_session_id.setdefault(normalized_session_id, []).append(summary)

        now = datetime.now(UTC)
        for key, pending in pending_by_key.items():
            if pending.gate_id not in graph.gates_by_id:
                logger.info(
                    "Removing stale await-agent record with missing gate key=%s gate_id=%s dispatch_id=%s",
                    key,
                    pending.gate_id,
                    pending.dispatch_id,
                )
                self.await_agent_store.delete(key)
                continue

            if (now - pending.occurred_at).total_seconds() < _AWAIT_AGENT_STALE_GRACE_SECONDS:
                continue

            session_id, vm_name = _await_agent_pending_target(pending)
            candidate_summaries: list[Any] = []
            if session_id:
                candidate_summaries.extend(summaries_by_session_id.get(session_id, ()))
            if vm_name:
                candidate_summaries.extend(summaries_by_vm_name.get(vm_name, ()))
            session_id, vm_name = _await_agent_pending_target(pending, summaries=candidate_summaries)
            if not candidate_summaries:
                logger.info(
                    "Keeping await-agent record with no matching VM summary key=%s dispatch_id=%s session_id=%s vm_name=%s",
                    key,
                    pending.dispatch_id,
                    session_id,
                    vm_name,
                )
                continue

            matches_active_dispatch = any(
                str(getattr(summary, "dispatch_id", "") or "").strip() == pending.dispatch_id
                and str(getattr(summary, "agent_state", "") or "").strip() == "running"
                for summary in candidate_summaries
            )
            if matches_active_dispatch:
                continue

            has_replaced_or_unavailable_summary = any(
                (
                    str(getattr(summary, "dispatch_id", "") or "").strip()
                    and str(getattr(summary, "dispatch_id", "") or "").strip() != pending.dispatch_id
                )
                or str(getattr(summary, "libvirt_state", "") or "").strip() != "running"
                for summary in candidate_summaries
            )
            if has_replaced_or_unavailable_summary:
                logger.info(
                    "Removing stale await-agent record after replaced or unavailable VM state key=%s dispatch_id=%s session_id=%s vm_name=%s",
                    key,
                    pending.dispatch_id,
                    session_id,
                    vm_name,
                )
                self.await_agent_store.delete(key)

    def _complete_pending_await_agent_gates_directly(self, *, graph: ExecutionGraph) -> int:
        pending_by_key = self.await_agent_store.read_all()
        if not pending_by_key:
            return 0

        completed = 0
        for key, pending in pending_by_key.items():
            gate = graph.gates_by_id.get(pending.gate_id)
            if gate is None:
                continue
            if self._complete_pending_await_agent_directly(pending=pending, gate=gate, log_skips=True, key=key):
                completed += 1
        return completed

    def _complete_pending_await_agent_directly(
        self,
        *,
        pending: AwaitAgentPendingRecord,
        gate: GateDefinitionDiscriminated | None,
        log_skips: bool = False,
        key: str | None = None,
    ) -> bool:
        if gate is None or getattr(gate, "type", "") != "gate:await-agent":
            return False
        event = self._build_completion_event_for_pending(pending, log_skips=log_skips, key=key)
        if event is None:
            return False
        queue_record = TriggerQueueRecord(
            event_id=event.event_id,
            trigger_id=event.trigger_id,
            trigger_type=event.trigger_type,
            trace_id=event.trace_id or pending.trace_id,
            occurred_at=event.occurred_at,
            payload=event.payload,
            delivery_state="delivered",
            delivered_at=datetime.now(UTC),
            idempotency_key=f"__await-agent-completion__:{pending.dispatch_id}",
        )
        self._execute_await_agent_completion(queue_record=queue_record, gate=gate)
        return self.await_agent_store.get(f"{pending.gate_id}:{pending.dispatch_id}") is None

    def _build_completion_event_for_pending(
        self,
        pending: AwaitAgentPendingRecord,
        *,
        log_skips: bool = False,
        key: str | None = None,
    ) -> TriggerEvent | None:
        try:
            summaries = self._list_vm_summaries_for_await_agent_reconciliation()
        except Exception:
            logger.exception("Await-agent completion publish failed while listing VMs dispatch_id=%s", pending.dispatch_id)
            return None

        session_id, vm_name = _await_agent_pending_target(pending, summaries=summaries)
        if not vm_name:
            if log_skips:
                logger.info(
                    "Await-agent completion publish skipped missing_vm_name key=%s dispatch_id=%s session_id=%s",
                    key,
                    pending.dispatch_id,
                    session_id,
                )
            return None
        matching_summary = self._match_completion_summary(
            summaries=summaries,
            pending=pending,
            vm_name=vm_name,
            session_id=session_id,
        )
        if matching_summary is None:
            if log_skips:
                candidate_summaries = [
                    {
                        "vm_name": str(getattr(summary, "vm_name", "") or "").strip() or None,
                        "session_ids": list(getattr(summary, "session_ids", []) or []),
                        "agent_state": str(getattr(summary, "agent_state", "") or "").strip() or None,
                        "dispatch_id": str(getattr(summary, "dispatch_id", "") or "").strip() or None,
                        "libvirt_state": str(getattr(summary, "libvirt_state", "") or "").strip() or None,
                    }
                    for summary in summaries
                    if str(getattr(summary, "vm_name", "") or "").strip() == vm_name
                ]
                logger.info(
                    "Await-agent completion publish skipped no_matching_summary key=%s dispatch_id=%s vm_name=%s session_id=%s candidates=%s",
                    key,
                    pending.dispatch_id,
                    vm_name,
                    session_id,
                    candidate_summaries,
                )
            return None
        agent_state = str(getattr(matching_summary, "agent_state", "") or "").strip()
        if agent_state == "running":
            if log_skips:
                logger.info(
                    "Await-agent completion publish skipped still_running key=%s dispatch_id=%s vm_name=%s",
                    key,
                    pending.dispatch_id,
                    vm_name,
                )
            return None

        event = build_agent_run_completed_event(
            trigger_id="__await-agent-completion__",
            dispatch_id=pending.dispatch_id,
            vm_user="agent",
            summary=matching_summary,
            session_id=session_id,
            directory_path=(getattr(matching_summary, "directory_paths", None) or [None])[0],
        )
        if log_skips:
            logger.info(
                "Await-agent completion event prepared key=%s dispatch_id=%s vm_name=%s agent_state=%s event_id=%s",
                key,
                pending.dispatch_id,
                vm_name,
                agent_state,
                event.event_id,
            )
        return event

    @staticmethod
    def _match_completion_summary(
        *,
        summaries: list[Any],
        pending: AwaitAgentPendingRecord,
        vm_name: str,
        session_id: str | None,
    ) -> Any | None:
        exact_match = next(
            (
                summary
                for summary in summaries
                if str(getattr(summary, "vm_name", "") or "").strip() == vm_name
                and str(getattr(summary, "dispatch_id", "") or "").strip() == pending.dispatch_id
            ),
            None,
        )
        if exact_match is not None:
            return exact_match
        if not session_id:
            return None
        return next(
            (
                summary
                for summary in summaries
                if str(getattr(summary, "vm_name", "") or "").strip() == vm_name
                and session_id in {str(item or "").strip() for item in (getattr(summary, "session_ids", []) or [])}
                and not str(getattr(summary, "dispatch_id", "") or "").strip()
                and str(getattr(summary, "agent_state", "") or "").strip() != "running"
            ),
            None,
        )

    def _list_vm_summaries_for_await_agent_reconciliation(self) -> list[Any]:
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

    def _execute_provider(self, *, effect: EffectDefinitionDiscriminated, resolved_config: dict[str, Any]) -> dict[str, Any]:
        provider = self.provider_factory.build(effect)
        return dict(provider.execute(resolved_config=resolved_config) or {})

    def _execute_query_provider(
        self,
        *,
        query: QueryDefinitionDiscriminated,
        resolved_config: dict[str, Any],
    ) -> dict[str, Any]:
        provider = self.query_provider_factory.build(query)
        return dict(provider.execute(resolved_config=resolved_config) or {})

    def _reap_completed_futures(self) -> tuple[int, int]:
        completed_count = 0
        published_count = 0
        to_finalize: list[tuple[str, Future[dict[str, Any]]]] = []
        with self._inflight_lock:
            for idempotency_key, future in list(self._inflight.items()):
                if future.done():
                    to_finalize.append((idempotency_key, future))
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
                **{key: value for key, value in queue_record.payload.items() if key != NODE_OUTPUTS_CONTEXT_KEY},
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
            summaries = self._list_vm_summaries_for_await_agent_reconciliation()
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
            ),
        )
        self.event_queue.publish(completion_event, idempotency_key=record.idempotency_key)
        return completion_event_id

    def _write_skipped_node_record(
        self,
        *,
        queue_record: TriggerQueueRecord,
        node: WorkflowNodeDefinition,
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
                error=reason,
            )
            return

        self._write_terminal_effect_record(
            queue_record=queue_record,
            effect=node,
            status="skipped",
            started_at=datetime.now(UTC),
            resolved_config={},
            error=reason,
        )

    def _write_failed_effect_record(
        self,
        *,
        queue_record: TriggerQueueRecord,
        effect: EffectDefinitionDiscriminated | QueryDefinitionDiscriminated,
        started_at: datetime,
        resolved_config: dict[str, Any],
        error: str,
    ) -> None:
        self._write_terminal_effect_record(
            queue_record=queue_record,
            effect=effect,
            status="failed",
            started_at=started_at,
            resolved_config=resolved_config,
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
        error: str | None = None,
        publish_completion_event: bool = False,
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
        )
        if publish_completion_event:
            record.published_event_id = self._publish_gate_completion_event_from_record(record=record, outputs=outputs)
            record.completion_event_enqueued_at = finished_at
        self.gate_execution_store.put(record)


class _JsonRecordStore(Generic[TRecord]):
    def __init__(self, path: Path, model: type[TRecord]) -> None:
        self.path = path
        self.model = model
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
    def __init__(self, path: Path) -> None:
        super().__init__(path, EffectExecutionRecord)


class JsonGateExecutionStore(_JsonRecordStore[GateExecutionRecord]):
    def __init__(self, path: Path) -> None:
        super().__init__(path, GateExecutionRecord)

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
) -> EffectOrchestratorWorker:
    resolved_project_dir = Path(project_dir) if project_dir is not None else Path.cwd()
    worker_config = _load_trigger_orchestrator_worker_global_config(resolved_project_dir, base_config=config)
    effective_config = GatewayConfig.model_validate(
        {
            **config.model_dump(mode="json"),
            "runtime_dir": worker_config.runtime_dir,
        }
    )
    runtime = build_runtime(effective_config)
    return EffectOrchestratorWorker(
        config=effective_config,
        worker_config=worker_config,
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

    return ExecutionGraph(
        nodes_by_id=nodes_by_id,
        gates_by_id={gate.id: gate for gate in worker_config.gates},
        effects_by_id={effect.id: effect for effect in worker_config.effects},
        queries_by_id={query.id: query for query in worker_config.queries},
        downstream_index=build_downstream_index(edges),
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

    flat_payload = {key: value for key, value in payload.items() if key != NODE_OUTPUTS_CONTEXT_KEY}
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
) -> dict[str, Any]:
    validate_output_mapping_keys(
        outputs,
        context=f"{completed_node_id} runtime outputs",
    )
    payload = dict(outputs)
    inherited_context = _node_output_context_from_payload(source_payload, source_node_id=source_node_id)
    if reset_context:
        payload[NODE_OUTPUTS_CONTEXT_KEY] = {completed_node_id: dict(outputs)}
        return payload
    merged_context = {node_id: dict(node_payload) for node_id, node_payload in inherited_context.items()}
    merged_context[completed_node_id] = dict(outputs)
    payload[NODE_OUTPUTS_CONTEXT_KEY] = merged_context
    return payload


def _completion_event_id(*, parent_event_id: str, node_id: str, prefix: str) -> str:
    return f"{prefix}:{node_id}:{hashlib.sha1(parent_event_id.encode('utf-8')).hexdigest()[:12]}"


def _prepare_effect_resolved_config(
    *,
    effect: EffectDefinitionDiscriminated,
    resolved_config: dict[str, Any],
    upstream_payload: dict[str, dict[str, Any]],
    declared_upstream_output_names: dict[str, set[str] | list[str] | tuple[str, ...]],
    system_value_resolver,
) -> dict[str, Any]:
    if effect.type != "effect:vm:copy-files":
        return resolved_config
    next_config = deepcopy(resolved_config)
    raw_files = next_config.get("files")
    if not isinstance(raw_files, list):
        return next_config
    for index, raw_entry in enumerate(raw_files):
        if not isinstance(raw_entry, dict):
            continue
        source_format = str(raw_entry.get("source_format") or "").strip().lower()
        if source_format not in {"", "file", "template"}:
            raise ValueError(f"effect:vm:copy-files files[{index}] source_format must be one of: file, template")
        if source_format != "template":
            continue
        source_path = str(raw_entry.get("source_path") or "").strip()
        if not source_path:
            raise ValueError(f"effect:vm:copy-files files[{index}] source_path is required for template sources")
        template_path = Path(source_path).expanduser().resolve()
        if not template_path.exists():
            raise ValueError(f"effect:vm:copy-files template source does not exist: {template_path}")
        if template_path.is_dir():
            raise ValueError(f"effect:vm:copy-files template source must be a file: {template_path}")
        template_text = template_path.read_text(encoding="utf-8")
        rendered = resolve_workflow_bindings(
            template_text,
            upstream_payload=upstream_payload,
            declared_upstream_output_names=declared_upstream_output_names,
            system_value_resolver=system_value_resolver,
            strict_validation=True,
        ).resolved_config
        if not isinstance(rendered, str):
            raise ValueError(f"effect:vm:copy-files template source must resolve to text: {template_path}")
        raw_entry["content"] = rendered
        raw_entry.pop("source_path", None)
    return next_config


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


def _load_trigger_orchestrator_worker_global_config(
    project_dir: Path,
    *,
    base_config: GatewayConfig | None = None,
) -> TriggerOrchestratorWorkerGlobalConfig:
    base_payload = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": (base_config.runtime_dir if base_config is not None else Path("runtime")),
        }
    ).model_dump(mode="json")
    path = project_dir / "config" / "trigger-orchestrator" / "config.json"
    if not path.exists():
        return TriggerOrchestratorWorkerGlobalConfig.model_validate(base_payload)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"worker config file {path} must contain a JSON object")
    return TriggerOrchestratorWorkerGlobalConfig.model_validate({**base_payload, **raw})


def _await_agent_pending_target(
    pending: AwaitAgentPendingRecord,
    *,
    summaries: list[Any] | None = None,
) -> tuple[str | None, str | None]:
    session_id = str(pending.session_id or "").strip() or None
    vm_name = str(pending.vm_name or "").strip() or None
    if not summaries:
        return session_id, vm_name

    if session_id is None and vm_name is not None:
        session_id = next(
            (
                str(session or "").strip()
                for summary in summaries
                if str(getattr(summary, "vm_name", "") or "").strip() == vm_name
                for session in (getattr(summary, "session_ids", []) or [])
                if str(session or "").strip()
            ),
            None,
        )
    if vm_name is None and session_id is not None:
        vm_name = next(
            (
                str(getattr(summary, "vm_name", "") or "").strip()
                for summary in summaries
                if session_id in {str(item or "").strip() for item in (getattr(summary, "session_ids", []) or [])}
                and str(getattr(summary, "vm_name", "") or "").strip()
            ),
            None,
        )
    return session_id, vm_name


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
