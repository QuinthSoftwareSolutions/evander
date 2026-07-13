from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import math
import os
import random
import signal
import time
from collections.abc import Mapping
from concurrent.futures import FIRST_COMPLETED, Future, wait
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import RLock, current_thread, main_thread
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

from evander.config import (
    GatewayConfig,
    OrchestrationCoreConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from evander.events.internal import (
    InternalEventBus,
    InternalEventSource,
    validate_internal_event_type,
)
from evander.events.scheduled import ScheduledInternalEventStore
from evander.events.models import (
    EffectDefinitionDiscriminated,
    FOR_EXECUTION_IDS_CONTEXT_KEY,
    GateDefinitionDiscriminated,
    materialize_node_definition,
    NODE_OUTPUTS_CONTEXT_KEY,
    QueryDefinitionDiscriminated,
    validate_output_mapping_keys,
    TriggerDefinitionDiscriminated,
    TriggerEvent,
)
from evander.worker.security import sanitize_text
from evander.gate_runtime import (
    GateRuntimeError,
    require_object_payload,
)
from evander.orchestration.host_polling import (
    HostFileLockManager,
    HostWakeSleeper,
    wake_socket_path_for_lock,
)
from evander.orchestration.adapters import (
    EffectCompletionPublisherAdapter,
    EffectDependencyResolverAdapter,
    EffectEventFetcherAdapter,
    EffectLoopExecution,
    EffectExecutionRecorderAdapter,
)
from evander.provider_config import ProviderConfigResolver
from evander.runtime import RuntimeContext
from evander.app_bootstrap import build_runtime
from evander.platform_integrations import PlatformServiceBundle, RuntimeAssemblyInterface
from evander.triggers.queue import TriggerQueueRecord
from evander.workflow.runtime_config import (
    FixedTriggerOrchestratorWorkerGlobalConfigReloader,
    TriggerOrchestratorWorkerGlobalConfigReloader,
    load_trigger_orchestrator_worker_global_config,
)
from evander.workflow.binding import (
    BindingResolutionError,
    binding_trace_to_payload,
    resolve_effect_config_bindings,
)
from evander.workflow.graph import (
    WorkflowGraphCycleError,
    build_downstream_index,
    build_inbound_edge_index,
    compute_downstream_depth,
    index_declared_outputs,
    would_form_cycle,
)
from evander.workflow.graph_metadata import (
    ResolvedWorkflowGraphMetadata,
    resolve_workflow_graph_metadata,
)
from evander.workflow.migrations import AGENT_PROMPT_ERROR_PORT
from evander.orchestration.agent_prompt_activity import AgentPromptActivityRuntime
from evander.workflow.engine.node_executors import (
    NodeExecutorRegistry,
    ProviderEffectNodeExecutor,
    ProviderQueryNodeExecutor,
)
from evander.workflow.engine import OrchestrationLoop
from evander.orchestration.kernels import PollingKernelConfig, PollingOrchestratorKernel
from evander.orchestration.wal_maintenance import OrchestrationWalCheckpointMaintenance
from evander.orchestration.wiring import (
    EffectWorkerStoreWiring,
    EffectWorkerWiring,
    build_effect_worker_store_wiring,
    build_effect_worker_wiring,
)
from evander.orchestration.wiring.effect import (
    EffectProviderRuntimeWiring,
    build_effect_provider_runtime_wiring,
)
from evander.orchestration.effect_extensions import SpecialEventResult
from evander.gates.providers.await_agent import (  # noqa: F401
    AwaitAgentPendingRecord,
    _await_agent_pending_target,
)
from evander.repositories.orchestration import (
    EffectExecutionRepository,
    GateExecutionRepository,
    JsonEffectCursorRepository,
    JsonExecutionRepository,
    SqliteExecutionRepository,
    SqliteGateExecutionRepository,
)

logger = logging.getLogger(__name__)

__all__ = [
    "AwaitAgentPendingRecord",
    "_await_agent_pending_target",
]

_DEFAULT_MAX_GRAPH_DEPTH = 16
_DEFAULT_MAX_EVENTS_PER_CYCLE = 200
_PHASE_LOG_THRESHOLD_SECONDS = 0.25
_MANAGED_GIT_RECONCILE_INTERVAL_SECONDS = 300.0
_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = FOR_EXECUTION_IDS_CONTEXT_KEY
COMPLETION_PENDING_STATUS = "completion_pending"
WorkflowNodeDefinition = (
    TriggerDefinitionDiscriminated
    | GateDefinitionDiscriminated
    | EffectDefinitionDiscriminated
    | QueryDefinitionDiscriminated
)
_ScheduledInternalEventStoreGetter = Callable[[], ScheduledInternalEventStore]
_ScheduledInternalEventStoreCache = tuple[Path, ScheduledInternalEventStore] | None


def _record_has_pending_completion(record: Any) -> bool:
    status = str(getattr(record, "status", "") or "").strip()
    return status in {"running", COMPLETION_PENDING_STATUS}


def _store_has_inflight_execution_for_event(store: Any, event_id: str) -> bool:
    has_running = getattr(store, "has_running_execution_for_event", None)
    if callable(has_running):
        return bool(has_running(event_id))
    list_executions = getattr(store, "list_executions", None)
    if not callable(list_executions):
        return False
    for record in list_executions():
        if getattr(record, "event_id", None) != event_id:
            continue
        if _record_has_pending_completion(record):
            return True
    return False


def _outputs_from_exception(exc: BaseException) -> dict[str, Any]:
    outputs = getattr(exc, "outputs", None)
    if isinstance(outputs, Mapping):
        return dict(outputs)
    return {}


_PROMPT_FAILURE_EFFECT_TYPES = frozenset(
    {"effect:agent:prompt", "effect:agent:enqueue-prompt"}
)
_PROMPT_FAILURE_DEFAULT_PHASE = "dispatch"


def _prompt_failure_outputs(exc: BaseException) -> dict[str, Any]:
    outputs = _outputs_from_exception(exc)
    outputs["success"] = False

    error_message = _normalize_optional_string(outputs.get("error_message"))
    if error_message is None:
        error_message = _normalize_optional_string(sanitize_text(str(exc)))
    if error_message is None:
        error_message = type(exc).__name__
    outputs["error_message"] = sanitize_text(error_message)

    error_class = _normalize_optional_string(outputs.get("error_class"))
    if error_class is None:
        error_class = type(exc).__name__
    outputs["error_class"] = sanitize_text(error_class)

    failed_phase = _normalize_optional_string(outputs.get("failed_phase"))
    if failed_phase is None:
        failed_phase = _PROMPT_FAILURE_DEFAULT_PHASE
    outputs["failed_phase"] = sanitize_text(failed_phase)
    return outputs


def _prompt_error_outbound_edge_exists(*, graph: ExecutionGraph, effect_id: str) -> bool:
    return any(
        _edge_source_port(edge) == AGENT_PROMPT_ERROR_PORT
        for edge in graph.outbound_index.get(effect_id, ())
    )


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
    completion_pending_status: str | None = None
    completion_event_branch_port: str | None = None
    error: str | None = None
    completion_event_enqueued_at: datetime | None = None
    binding_trace: dict[str, Any] | None = None
    binding_error: str | None = None
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
    completion_pending_status: str | None = None
    completion_event_branch_port: str | None = None
    completion_event_for_execution_ids: dict[str, str] | None = None
    error: str | None = None
    completion_event_enqueued_at: datetime | None = None
    binding_trace: dict[str, Any] | None = None
    binding_error: str | None = None
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
    node_graph_metadata_by_id: dict[str, ResolvedWorkflowGraphMetadata] = field(
        default_factory=dict
    )


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


class _WorkerAgentPromptActivity(AgentPromptActivityRuntime):
    def __init__(self, worker: "EffectOrchestratorWorker") -> None:
        super().__init__(
            runtime_dir=worker.worker_config.runtime_dir,
            execution_store=worker.execution_store,
        )


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


class EventScheduleEffectExecutor:
    def __init__(
        self,
        *,
        scheduled_internal_event_store: ScheduledInternalEventStore | None = None,
        scheduled_internal_event_store_getter: _ScheduledInternalEventStoreGetter | None = None,
    ) -> None:
        if scheduled_internal_event_store is None and scheduled_internal_event_store_getter is None:
            raise ValueError(
                "EventScheduleEffectExecutor requires a scheduled event store or getter"
            )
        self._scheduled_internal_event_store = scheduled_internal_event_store
        self._scheduled_internal_event_store_getter = scheduled_internal_event_store_getter

    @property
    def scheduled_internal_event_store(self) -> ScheduledInternalEventStore:
        if self._scheduled_internal_event_store_getter is not None:
            return self._scheduled_internal_event_store_getter()
        if self._scheduled_internal_event_store is None:
            raise RuntimeError("scheduled internal event store is not configured")
        return self._scheduled_internal_event_store

    def prepare_resolved_config(self, **kwargs: Any) -> dict[str, Any]:
        resolved_config = dict(kwargs.get("resolved_config") or {})
        started_at = _normalize_started_at(kwargs.get("started_at"))

        payload = resolved_config.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("effect:event:schedule payload must resolve to a JSON object")
        resolved_config["payload"] = payload

        resolved_config["event_type"] = validate_internal_event_type(
            resolved_config.get("event_type")
        )
        resolved_config["correlation_key"] = _normalize_optional_string(
            resolved_config.get("correlation_key")
        )
        resolved_config["idempotency_key"] = _normalize_optional_string(
            resolved_config.get("idempotency_key")
        )

        run_at_value = resolved_config.get("run_at")
        delay_seconds_value = resolved_config.get("delay_seconds")
        has_run_at = run_at_value is not None
        has_delay_seconds = delay_seconds_value is not None
        if has_run_at == has_delay_seconds:
            raise ValueError(
                "effect:event:schedule requires exactly one of run_at or delay_seconds"
            )
        if has_run_at:
            run_at = _normalize_datetime_value(run_at_value, field_name="run_at")
        else:
            delay_seconds = _normalize_delay_seconds(delay_seconds_value)
            if not _delay_seconds_within_datetime_range(started_at, delay_seconds):
                raise ValueError("delay_seconds is outside the supported datetime range")
            resolved_config["delay_seconds"] = delay_seconds
            run_at = started_at + timedelta(seconds=delay_seconds)
        resolved_config["run_at"] = run_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
        return resolved_config

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        resolved_config = dict(kwargs.get("resolved_config") or {})
        execution_context = dict(kwargs.get("execution_context") or {})

        payload = resolved_config.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("effect:event:schedule payload must resolve to a JSON object")
        event_type = validate_internal_event_type(resolved_config.get("event_type"))
        correlation_key = _normalize_optional_string(resolved_config.get("correlation_key"))
        run_at = _normalize_datetime_value(resolved_config.get("run_at"), field_name="run_at")
        idempotency_key = _normalize_optional_string(resolved_config.get("idempotency_key"))
        if idempotency_key is None:
            idempotency_key = _normalize_optional_string(execution_context.get("idempotency_key"))

        source = InternalEventSource(
            source_type="scheduled_internal_event",
            workflow_run_id=_normalize_optional_string(execution_context.get("workflow_run_id")),
            workflow_event_id=_normalize_optional_string(
                execution_context.get("workflow_event_id")
            ),
            workflow_trace_id=_normalize_optional_string(
                execution_context.get("workflow_trace_id")
            ),
            node_id=_normalize_optional_string(execution_context.get("node_id")),
            node_type=_normalize_optional_string(execution_context.get("node_type")),
        )
        scheduled_event, created = self.scheduled_internal_event_store.schedule_event(
            event_type=event_type,
            payload=payload,
            correlation_key=correlation_key,
            run_at=run_at,
            idempotency_key=idempotency_key,
            source=source,
        )
        return {
            "success": True,
            "created": created,
            "schedule_id": scheduled_event.schedule_id,
            "event_type": scheduled_event.event_type,
            "correlation_key": scheduled_event.correlation_key,
            "run_at": scheduled_event.run_at.isoformat().replace("+00:00", "Z"),
            "status": scheduled_event.status,
            "payload": dict(scheduled_event.payload),
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
        project_dir: Path | None = None,
    ) -> None:
        self.config = config
        self.project_dir = (
            Path(project_dir) if project_dir is not None else config.runtime_dir.parent
        )
        selected_config = core_config or worker_config
        if selected_config is None:
            raise ValueError(
                "EffectOrchestratorWorker requires either core_config or worker_config"
            )
        self.worker_config = (
            selected_config
            if isinstance(selected_config, TriggerOrchestratorWorkerGlobalConfig)
            else TriggerOrchestratorWorkerGlobalConfig.model_validate(
                selected_config.model_dump(mode="json")
            )
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
            addon_base_dir=self.project_dir,
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
        self.workflow_config_snapshot_store = resolved_wiring.workflow_config_snapshot_store
        self.queued_agent_prompt_store = resolved_wiring.queued_agent_prompt_store
        self._executor = resolved_wiring.executor
        self._inflight: dict[str, InflightExecution] = {}
        self._inflight_lock = RLock()
        self._last_graph_revision: str | None = None
        self._last_graph_node_count = 0
        self._last_graph_edge_count = 0
        self._last_snapshot_revision: str | None = None
        self._wal_checkpoint_maintenance = OrchestrationWalCheckpointMaintenance()
        self._scheduled_internal_event_store_cache: _ScheduledInternalEventStoreCache = None
        self.provider_factory = resolved_wiring.provider_factory
        self._managed_git_reconciler: Any | None = None
        self._managed_git_cleanups: list[Callable[[], Any]] = []
        self._configure_managed_git_runtime(self.provider_factory)
        self._last_managed_git_reconcile_at = 0.0
        self.query_provider_factory = resolved_wiring.query_provider_factory
        self.gate_provider_factory = resolved_wiring.gate_provider_factory
        self._agent_prompt_activity = _WorkerAgentPromptActivity(self)
        self._bind_provider_factory_runtime_ports()
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
        self._reconcile_managed_git_worktrees(force=True, strict=True)
        self._wake_sleeper = HostWakeSleeper(wake_socket_path_for_lock(self.lock_path))
        self._kernel = PollingOrchestratorKernel(
            config=PollingKernelConfig(
                poll_interval_seconds=self.worker_config.poll_interval_seconds,
                jitter_seconds=self.worker_config.jitter_seconds,
                lock_path=self.lock_path,
            ),
            lock_manager=HostFileLockManager(),
            sleeper=self._wake_sleeper,
            run_cycle=self.run_once,
            get_sleep_seconds=lambda result: result.next_sleep_seconds,
            on_start=self._wake_sleeper.start,
        )

    def _bind_provider_factory_runtime_ports(self) -> None:
        try:
            setattr(
                self.provider_factory,
                "agent_prompt_activity",
                self._agent_prompt_activity,
            )
        except Exception:
            return

    def _configure_managed_git_runtime(self, provider_factory: Any) -> None:
        admin_service = getattr(provider_factory, "admin_service", None)
        managed_git = getattr(admin_service, "managed_git_admin", None)
        preflight = getattr(managed_git, "preflight", None)
        if callable(preflight):
            preflight()
        cleanup = getattr(managed_git, "cleanup_active_worktrees", None)
        if callable(cleanup) and all(existing != cleanup for existing in self._managed_git_cleanups):
            self._managed_git_cleanups.append(cleanup)
        self._managed_git_reconciler = getattr(
            admin_service,
            "reconcile_managed_git_worktrees",
            None,
        )

    def _reconcile_managed_git_worktrees(
        self, *, force: bool = False, strict: bool = False
    ) -> None:
        if not callable(self._managed_git_reconciler):
            return
        now = time.monotonic()
        if (
            not force
            and now - self._last_managed_git_reconcile_at
            < _MANAGED_GIT_RECONCILE_INTERVAL_SECONDS
        ):
            return
        self._last_managed_git_reconcile_at = now
        try:
            self._managed_git_reconciler()
        except Exception:
            if strict:
                raise
            logger.exception("Managed Git worktree reconciliation failed")

    def run_forever(self) -> None:
        old_handlers = self._install_managed_git_cleanup_signal_handlers()
        try:
            self._kernel.run_forever()
        finally:
            self._cleanup_active_managed_git_worktrees()
            self._restore_signal_handlers(old_handlers)

    def _cleanup_active_managed_git_worktrees(self) -> None:
        for cleanup in list(self._managed_git_cleanups):
            try:
                cleanup()
            except Exception:
                logger.exception("Managed Git active worktree cleanup failed during shutdown")

    def _install_managed_git_cleanup_signal_handlers(self) -> dict[int, Any]:
        if current_thread() is not main_thread() or not self._managed_git_cleanups:
            return {}
        old_handlers: dict[int, Any] = {}
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous = signal.getsignal(signum)

            def handler(received: int, frame: Any, *, previous_handler: Any = previous) -> None:
                self._cleanup_active_managed_git_worktrees()
                if callable(previous_handler):
                    previous_handler(received, frame)
                    return
                if received == signal.SIGINT:
                    raise KeyboardInterrupt
                raise SystemExit(128 + received)

            signal.signal(signum, handler)
            old_handlers[signum] = previous
        return old_handlers

    @staticmethod
    def _restore_signal_handlers(old_handlers: Mapping[int, Any]) -> None:
        if current_thread() is not main_thread():
            return
        for signum, previous in old_handlers.items():
            signal.signal(signum, previous)

    def run_once_with_lock(self) -> EffectOrchestratorCycleResult:
        return self._kernel.run_once_with_lock()

    def force_complete_await_agent(self, dispatch_id: str) -> bool:
        return self._run_force_complete_hooks(dispatch_id=dispatch_id)

    def run_once(self) -> EffectOrchestratorCycleResult:
        self._reconcile_managed_git_worktrees()
        latest_config, _changed = self._config_reloader.get_latest()
        previous_config = self.worker_config
        active_config = previous_config
        config_changed = latest_config != previous_config
        early_completion_events_published = 0
        if config_changed:
            provider_changed = _provider_loader_signature(
                latest_config
            ) != _provider_loader_signature(previous_config)
            store_changed = _effect_store_binding_signature(
                latest_config
            ) != _effect_store_binding_signature(previous_config)
            store_wiring: EffectWorkerStoreWiring | None = None
            runtime_wiring: EffectProviderRuntimeWiring | None = None
            apply_config = True

            if store_changed:
                _completed_count, early_completion_events_published = self._reap_completed_futures()
                pending_existing_store_events = len(self.event_queue.list_pending())
                with self._inflight_lock:
                    inflight_count = len(self._inflight)
                if inflight_count > 0 or pending_existing_store_events > 0:
                    logger.info(
                        "Effect orchestrator store rebind deferred waiting for existing store "
                        "drain revision=%s inflight=%d pending_events=%d "
                        "completion_events_published=%d",
                        _config_revision_hash(latest_config),
                        inflight_count,
                        pending_existing_store_events,
                        early_completion_events_published,
                    )
                    apply_config = False
                else:
                    try:
                        store_wiring = build_effect_worker_store_wiring(
                            config=self.config,
                            worker_config=latest_config,
                            runtime=self.runtime,
                        )
                    except Exception:
                        logger.exception(
                            "Effect orchestrator store rebind failed revision=%s; "
                            "restart or fix configuration before retrying",
                            _config_revision_hash(latest_config),
                        )
                        apply_config = False
                    else:
                        if provider_changed:
                            try:
                                runtime_wiring = build_effect_provider_runtime_wiring(
                                    config=self.config,
                                    worker_config=latest_config,
                                    runtime=self.runtime,
                                    provider_config_resolver=self.provider_config_resolver,
                                    addon_base_dir=self.project_dir,
                                )
                            except Exception:
                                logger.exception(
                                    "Effect orchestrator provider_loader reload failed revision=%s; "
                                    "restart or fix configuration before retrying",
                                    _config_revision_hash(latest_config),
                                )
                                apply_config = False
            elif provider_changed:
                try:
                    runtime_wiring = build_effect_provider_runtime_wiring(
                        config=self.config,
                        worker_config=latest_config,
                        runtime=self.runtime,
                        provider_config_resolver=self.provider_config_resolver,
                        addon_base_dir=self.project_dir,
                    )
                except Exception:
                    logger.exception(
                        "Effect orchestrator provider_loader reload failed revision=%s; "
                        "restart or fix configuration before retrying",
                        _config_revision_hash(latest_config),
                    )
                    apply_config = False

            if apply_config:
                self.worker_config = latest_config
                active_config = latest_config
                if store_wiring is not None:
                    self.workflow_event_journal = store_wiring.workflow_event_journal
                    self.event_queue = store_wiring.event_queue
                    self.lock_path = store_wiring.lock_path
                    self.cursor_path = store_wiring.cursor_path
                    self.cursor_repository = store_wiring.effect_cursor_repository
                    self.execution_store_path = latest_config.effect_execution_store_path
                    self.execution_store = store_wiring.execution_store
                    self.gate_execution_store_path = latest_config.gate_execution_store_path
                    self.gate_execution_store = store_wiring.gate_execution_store
                    self.workflow_config_snapshot_store = (
                        store_wiring.workflow_config_snapshot_store
                    )
                    self._last_snapshot_revision = None
                    self.queued_agent_prompt_store = store_wiring.queued_agent_prompt_store
                    self._wake_sleeper.set_path(wake_socket_path_for_lock(self.lock_path))
                    self._kernel.config = PollingKernelConfig(
                        poll_interval_seconds=self.worker_config.poll_interval_seconds,
                        jitter_seconds=self.worker_config.jitter_seconds,
                        lock_path=self.lock_path,
                    )
                    self.event_queue.reset_inflight()
                if runtime_wiring is not None:
                    self._configure_managed_git_runtime(runtime_wiring.provider_factory)
                    self.provider_config_resolver = runtime_wiring.provider_config_resolver
                    self.provider_factory = runtime_wiring.provider_factory
                    self._bind_provider_factory_runtime_ports()
                    self.query_provider_factory = runtime_wiring.query_provider_factory
                    self.gate_provider_factory = runtime_wiring.gate_provider_factory
                    self.agent_run_status_port = runtime_wiring.agent_run_status_port
                    self.agent_prompt_dispatch_port = runtime_wiring.agent_prompt_dispatch_port
                    self._special_event_handlers = runtime_wiring.special_event_handlers
                    self._lifecycle_reconciliation_hooks = (
                        runtime_wiring.lifecycle_reconciliation_hooks
                    )
                    self._completion_dispatch_hooks = runtime_wiring.completion_dispatch_hooks
                    self._effect_provider_class_registry = getattr(
                        self.provider_factory, "registry", None
                    )
                    self._gate_providers.clear()
                    self._gate_provider_factory_cache_id = id(self.gate_provider_factory)
                    self.gate_executor_registry = NodeExecutorRegistry()
                    self.effect_executor_registry = NodeExecutorRegistry()
                    self.query_executor_registry = NodeExecutorRegistry()
                    self._register_default_node_executors()
                elif store_wiring is not None and (
                    latest_config.internal_event_store_path
                    != previous_config.internal_event_store_path
                    or latest_config.workflow_event_journal_path
                    != previous_config.workflow_event_journal_path
                ):
                    self._register_default_node_executors()
                logger.info(
                    "Effect orchestrator config revision changed revision=%s node_count=%d edge_count=%d",
                    _config_revision_hash(latest_config),
                    len(latest_config.triggers)
                    + len(latest_config.gates)
                    + len(latest_config.effects)
                    + len(latest_config.queries),
                    len(active_config.edges),
                )
        config_revision = _config_revision_hash(active_config)
        pending_events_scanned = 0
        events_processed = 0
        effect_executions_started = 0
        effect_executions_completed = 0
        completion_events_published = early_completion_events_published

        graph = _build_execution_graph(active_config)
        if self._last_snapshot_revision != config_revision:
            snapshot_captured_at = datetime.now(UTC)
            phase_started = time.perf_counter()
            try:
                self.workflow_config_snapshot_store.put_snapshot(
                    _build_workflow_config_snapshot(
                        active_config=active_config,
                        graph=graph,
                        config_revision=config_revision,
                        captured_at=snapshot_captured_at,
                    )
                )
                self._last_snapshot_revision = config_revision
            except Exception:  # noqa: BLE001
                logger.exception(
                    "Effect orchestrator failed to persist workflow config snapshot revision=%s",
                    config_revision,
                )
            self._log_phase_duration(
                "config_snapshot",
                phase_started,
                snapshot_written=True,
            )
        self._log_graph_revision_change(
            config_revision=config_revision,
            node_count=len(graph.nodes_by_id),
            edge_count=len(active_config.edges),
        )

        phase_started = time.perf_counter()
        _completed_count, published_count = self._reap_completed_futures()
        self._log_phase_duration(
            "reap_completed_futures",
            phase_started,
            completed_count=_completed_count,
            published_count=published_count,
        )
        completion_events_published += published_count

        phase_started = time.perf_counter()
        count = self._retry_pending_effect_completion_publications()
        self._log_phase_duration(
            "retry_pending_effect_completion_publications",
            phase_started,
            retried=count,
        )
        completion_events_published += count

        phase_started = time.perf_counter()
        count = self._retry_pending_gate_completion_publications(graph=graph)
        self._log_phase_duration(
            "retry_pending_gate_completion_publications",
            phase_started,
            retried=count,
        )
        completion_events_published += count

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
            phase_started = time.perf_counter()
            execution_count = self._process_event_record(
                queue_record,
                graph=resolved_graph,
                config_revision=resolved_config_revision,
            )
            self._log_phase_duration(
                "event_processing",
                phase_started,
                event_id=queue_record.event_id,
                executions_started=execution_count,
            )
            effect_executions_started += execution_count
            return EffectLoopExecution(
                queue_record=queue_record,
                execution_count=execution_count,
                published_count=0,
            )

        phase_started = time.perf_counter()
        loop_result = loop.run_once(
            max_events=max(
                1,
                int(getattr(latest_config, "max_events_per_cycle", _DEFAULT_MAX_EVENTS_PER_CYCLE)),
            ),
            resolve_and_execute=_resolve_and_execute,
        )
        pending_events_scanned = loop_result.events_scanned
        events_processed = loop_result.events_scanned
        self._log_phase_duration(
            "event_loop",
            phase_started,
            events_scanned=loop_result.events_scanned,
            executions_recorded=loop_result.executions_recorded,
        )

        # Give very short-running in-process effects a chance to settle before
        # the second reap so the same cycle can publish their completion state.
        self._wait_for_inflight_completion(timeout_seconds=0.01)

        # Reap futures again after dispatch so fast in-process effects do not
        # remain marked as running until an extra poll cycle.
        phase_started = time.perf_counter()
        _completed_count, published_count = self._reap_completed_futures()
        self._log_phase_duration(
            "post_dispatch_reap_completed_futures",
            phase_started,
            completed_count=_completed_count,
            published_count=published_count,
        )
        completion_events_published += published_count
        completion_events_published += self._run_post_event_maintenance(graph=graph)
        self._run_orchestration_wal_checkpoint_maintenance()

        next_sleep_seconds = _compute_effect_cycle_sleep_seconds(
            events_processed=events_processed,
            completion_events_published=completion_events_published,
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
        self.effect_executor_registry.register(
            "effect:event:schedule",
            EventScheduleEffectExecutor(
                scheduled_internal_event_store_getter=self._get_scheduled_internal_event_store
            ),
        )
        for query_type in self.query_provider_factory.registry.registered_types():
            self.query_executor_registry.register(query_type, provider_query_executor)

    def _get_scheduled_internal_event_store(self) -> ScheduledInternalEventStore:
        store_path = self.worker_config.scheduled_internal_event_store_path
        cached = self._scheduled_internal_event_store_cache
        if cached is None or cached[0] != store_path:
            cached = (store_path, ScheduledInternalEventStore(store_path))
            self._scheduled_internal_event_store_cache = cached
        return cached[1]

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

    def _log_phase_duration(self, phase: str, started_at: float, **details: Any) -> None:
        duration_seconds = time.perf_counter() - started_at
        if duration_seconds < _PHASE_LOG_THRESHOLD_SECONDS and not any(
            bool(value) for value in details.values()
        ):
            return
        logger.info(
            "Effect orchestrator phase completed phase=%s duration_ms=%.1f details=%s",
            phase,
            duration_seconds * 1000,
            {key: value for key, value in details.items() if value is not None},
        )

    def _run_post_event_maintenance(self, *, graph: ExecutionGraph) -> int:
        published = 0
        phase_started = time.perf_counter()
        count = self._reconcile_gate_providers(graph=graph)
        self._log_phase_duration("gate_reconciliation", phase_started, reconciled=count)
        published += count

        phase_started = time.perf_counter()
        count = self._run_waiting_reconciliation_hooks(graph=graph)
        self._log_phase_duration("waiting_reconciliation", phase_started, reconciled=count)
        published += count

        phase_started = time.perf_counter()
        count = self._run_post_completion_dispatch_hooks()
        self._log_phase_duration("post_completion_dispatch", phase_started, dispatched=count)
        published += count
        return published

    def _run_orchestration_wal_checkpoint_maintenance(self) -> None:
        phase_started = time.perf_counter()
        result = self._wal_checkpoint_maintenance.run_if_due(self.worker_config)
        if result is None:
            return
        self._log_phase_duration(
            "wal_checkpoint",
            phase_started,
            attempted=result.attempted,
            skipped_reason=result.skipped_reason,
            wal_size_bytes=result.wal_size_bytes,
            busy=result.busy,
            log_frames=result.log_frames,
            checkpointed_frames=result.checkpointed_frames,
            error=result.error,
        )

    def _run_waiting_reconciliation_hooks(self, *, graph: ExecutionGraph | None = None) -> int:
        reconciled = 0
        graph = graph or self._build_execution_graph()
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

    def _retry_pending_effect_completion_publications(self) -> int:
        retried = 0
        list_completion_pending = getattr(
            self.execution_store, "list_completion_pending_records", None
        )
        if callable(list_completion_pending):
            records = list(list_completion_pending())
        else:
            list_executions = getattr(self.execution_store, "list_executions", None)
            if callable(list_executions):
                records = [
                    record
                    for record in list_executions()
                    if str(getattr(record, "status", "") or "").strip() == COMPLETION_PENDING_STATUS
                ]
            else:
                read_all = getattr(self.execution_store, "read_all", None)
                if not callable(read_all):
                    return 0
                records = [
                    record
                    for record in read_all().values()
                    if str(getattr(record, "status", "") or "").strip() == COMPLETION_PENDING_STATUS
                ]

        for record in records:
            outputs = dict(getattr(record, "outputs", {}) or {})
            branch_port = getattr(record, "completion_event_branch_port", None)
            published_event_id = self._publish_effect_completion_event_or_mark_pending(
                record=record,
                outputs=outputs,
                branch_port=branch_port,
                pending_status=str(
                    getattr(record, "completion_pending_status", None) or "succeeded"
                ),
                finished_at=datetime.now(UTC),
            )
            if published_event_id is None:
                continue

            self._finalize_effect_completion_publication(
                record=record,
                outputs=outputs,
                published_event_id=published_event_id,
                finished_at=datetime.now(UTC),
                status=str(getattr(record, "completion_pending_status", None) or "succeeded"),
            )
            retried += 1
        return retried

    def _mark_effect_completion_publication_pending(
        self,
        *,
        record: EffectExecutionRecord,
        outputs: dict[str, Any],
        finished_at: datetime,
        error: BaseException,
        pending_status: str,
        branch_port: str | None = None,
    ) -> None:
        record.status = COMPLETION_PENDING_STATUS
        record.completion_pending_status = pending_status
        record.completion_event_branch_port = branch_port
        record.outputs = dict(outputs)
        record.published_event_id = None
        record.completion_event_enqueued_at = None
        record.error = str(error)
        record.finished_at = finished_at
        self.execution_store.record_execution(record)
        self._update_event_delivery_state(record.event_id)

    def _mark_effect_completion_publication_failed(
        self,
        *,
        record: EffectExecutionRecord,
        outputs: dict[str, Any],
        finished_at: datetime,
        error: BaseException,
    ) -> None:
        record.status = "failed"
        record.outputs = dict(outputs)
        record.published_event_id = None
        record.completion_event_enqueued_at = None
        record.error = str(error)
        record.finished_at = finished_at
        self.execution_store.record_execution(record)
        self._update_event_delivery_state(record.event_id)

    def _publish_effect_completion_event_or_mark_pending(
        self,
        *,
        record: EffectExecutionRecord,
        outputs: dict[str, Any],
        pending_status: str,
        branch_port: str | None = None,
        finished_at: datetime | None = None,
    ) -> str | None:
        resolved_finished_at = finished_at or datetime.now(UTC)
        try:
            completion_event, idempotency_key, completion_event_id = (
                self._build_completion_event_from_record(
                    record=record,
                    outputs=outputs,
                    branch_port=branch_port,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Effect completion event build failed "
                "event_id=%s trace_id=%s effect_id=%s effect_type=%s branch_port=%s",
                record.event_id,
                record.trace_id,
                record.effect_id,
                record.effect_type,
                branch_port,
            )
            self._mark_effect_completion_publication_failed(
                record=record,
                outputs=outputs,
                finished_at=resolved_finished_at,
                error=exc,
            )
            return None
        try:
            self.event_queue.publish(completion_event, idempotency_key=idempotency_key)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Effect completion publication failed "
                "event_id=%s trace_id=%s effect_id=%s effect_type=%s branch_port=%s",
                record.event_id,
                record.trace_id,
                record.effect_id,
                record.effect_type,
                branch_port,
            )
            self._mark_effect_completion_publication_pending(
                record=record,
                outputs=outputs,
                finished_at=resolved_finished_at,
                error=exc,
                pending_status=pending_status,
                branch_port=branch_port,
            )
            return None
        return completion_event_id

    def _publish_effect_completion_event_or_fail(
        self,
        *,
        record: EffectExecutionRecord,
        outputs: dict[str, Any],
        branch_port: str | None = None,
        finished_at: datetime | None = None,
    ) -> str | None:
        resolved_finished_at = finished_at or datetime.now(UTC)
        try:
            completion_event, idempotency_key, completion_event_id = (
                self._build_completion_event_from_record(
                    record=record,
                    outputs=outputs,
                    branch_port=branch_port,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Effect completion event build failed "
                "event_id=%s trace_id=%s effect_id=%s effect_type=%s branch_port=%s",
                record.event_id,
                record.trace_id,
                record.effect_id,
                record.effect_type,
                branch_port,
            )
            self._mark_effect_completion_publication_failed(
                record=record,
                outputs=outputs,
                finished_at=resolved_finished_at,
                error=exc,
            )
            return None
        try:
            self.event_queue.publish(completion_event, idempotency_key=idempotency_key)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Effect completion publication failed "
                "event_id=%s trace_id=%s effect_id=%s effect_type=%s branch_port=%s",
                record.event_id,
                record.trace_id,
                record.effect_id,
                record.effect_type,
                branch_port,
            )
            self._mark_effect_completion_publication_failed(
                record=record,
                outputs=outputs,
                finished_at=resolved_finished_at,
                error=exc,
            )
            return None
        return completion_event_id

    def _finalize_effect_completion_publication(
        self,
        *,
        record: EffectExecutionRecord,
        outputs: dict[str, Any],
        published_event_id: str,
        finished_at: datetime,
        status: str = "succeeded",
        branch_port: str | None = None,
    ) -> None:
        record.status = str(status or "succeeded").strip() or "succeeded"
        record.outputs = dict(outputs)
        record.published_event_id = published_event_id
        record.completion_event_enqueued_at = finished_at
        record.finished_at = finished_at
        record.error = None
        if branch_port is not None:
            record.completion_event_branch_port = branch_port
        if outputs.get("success") is False:
            error_message = _normalize_optional_string(outputs.get("error_message"))
            if error_message is None:
                error_message = _normalize_optional_string(outputs.get("message"))
            if error_message is not None:
                record.error = error_message
        self.execution_store.record_execution(record)
        self._update_event_delivery_state(record.event_id)

    def _retry_pending_gate_completion_publications(self, *, graph: ExecutionGraph) -> int:
        retried = 0
        list_completion_pending = getattr(
            self.gate_execution_store, "list_completion_pending_records", None
        )
        if callable(list_completion_pending):
            records = list(list_completion_pending())
        else:
            list_executions = getattr(self.gate_execution_store, "list_executions", None)
            if callable(list_executions):
                records = [
                    record
                    for record in list_executions()
                    if str(getattr(record, "status", "") or "").strip() == COMPLETION_PENDING_STATUS
                ]
            else:
                read_all = getattr(self.gate_execution_store, "read_all", None)
                if not callable(read_all):
                    return 0
                records = [
                    record
                    for record in read_all().values()
                    if str(getattr(record, "status", "") or "").strip() == COMPLETION_PENDING_STATUS
                ]

        for record in records:
            outputs = dict(getattr(record, "outputs", {}) or {})
            branch_port = getattr(record, "completion_event_branch_port", None)
            for_execution_ids = getattr(record, "completion_event_for_execution_ids", None)
            published_event_id = self._publish_gate_completion_event_or_mark_pending(
                record=record,
                outputs=outputs,
                branch_port=branch_port,
                for_execution_ids=for_execution_ids,
                preserved_context_node_ids=_gate_completion_preserved_context_node_ids(
                    graph=graph,
                    gate_id=getattr(record, "gate_id", ""),
                    gate_type=getattr(record, "gate_type", ""),
                ),
                pending_status=str(
                    getattr(record, "completion_pending_status", None) or "succeeded"
                ),
                finished_at=datetime.now(UTC),
            )
            if published_event_id is None:
                continue

            record.status = str(
                getattr(record, "completion_pending_status", None) or "succeeded"
            ).strip()
            record.outputs = outputs
            record.published_event_id = published_event_id
            record.completion_event_enqueued_at = datetime.now(UTC)
            record.error = None
            self.gate_execution_store.record_execution(record)
            self._update_event_delivery_state(record.event_id)
            retried += 1
        return retried

    def _mark_gate_completion_publication_failed(
        self,
        *,
        record: GateExecutionRecord,
        outputs: dict[str, Any],
        finished_at: datetime,
        error: BaseException,
    ) -> None:
        record.status = "failed"
        record.outputs = dict(outputs)
        record.published_event_id = None
        record.completion_event_enqueued_at = None
        record.error = str(error)
        record.finished_at = finished_at
        self.gate_execution_store.record_execution(record)
        self._update_event_delivery_state(record.event_id)

    def _publish_gate_completion_event_or_mark_pending(
        self,
        *,
        record: GateExecutionRecord,
        outputs: dict[str, Any],
        pending_status: str,
        branch_port: str | None = None,
        for_execution_ids: dict[str, str] | None = None,
        preserved_context_node_ids: set[str] | None = None,
        finished_at: datetime | None = None,
    ) -> str | None:
        resolved_finished_at = finished_at or datetime.now(UTC)
        try:
            completion_event, idempotency_key, completion_event_id = (
                self._build_gate_completion_event_from_record(
                    record=record,
                    outputs=outputs,
                    branch_port=branch_port,
                    for_execution_ids=for_execution_ids,
                    preserved_context_node_ids=preserved_context_node_ids,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Gate completion event build failed "
                "event_id=%s trace_id=%s gate_id=%s gate_type=%s branch_port=%s",
                record.event_id,
                record.trace_id,
                record.gate_id,
                record.gate_type,
                branch_port,
            )
            self._mark_gate_completion_publication_failed(
                record=record,
                outputs=outputs,
                finished_at=resolved_finished_at,
                error=exc,
            )
            return None
        try:
            self.event_queue.publish(completion_event, idempotency_key=idempotency_key)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Gate completion publication failed "
                "event_id=%s trace_id=%s gate_id=%s gate_type=%s branch_port=%s",
                record.event_id,
                record.trace_id,
                record.gate_id,
                record.gate_type,
                branch_port,
            )
            record.status = COMPLETION_PENDING_STATUS
            record.completion_pending_status = pending_status
            record.completion_event_branch_port = branch_port
            record.completion_event_for_execution_ids = for_execution_ids
            record.outputs = dict(outputs)
            record.published_event_id = None
            record.completion_event_enqueued_at = None
            record.error = str(exc)
            record.finished_at = resolved_finished_at
            self.gate_execution_store.record_execution(record)
            self._update_event_delivery_state(record.event_id)
            return None
        return completion_event_id

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

    @staticmethod
    def _guaranteed_context_node_ids_for(
        *, graph: ExecutionGraph, node_id: str
    ) -> set[str]:
        return _guaranteed_context_node_ids(graph=graph, node_id=node_id)

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
        graph_metadata = graph.node_graph_metadata_by_id.get(query.id)
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
                    graph_metadata=graph_metadata,
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
                binding_error=str(exc),
            )
            return False

        resolved_config = (
            resolved.resolved_config if isinstance(resolved.resolved_config, dict) else {}
        )
        binding_trace = binding_trace_to_payload(resolved.trace)
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
            binding_trace=binding_trace,
            config_revision=config_revision,
        )
        self.execution_store.record_execution(record)
        future = self._executor.submit(
            self._execute_query_provider,
            query=query,
            resolved_config=resolved_config,
            graph_metadata=graph_metadata,
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
        graph_metadata = graph.node_graph_metadata_by_id.get(effect.id)
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
                    graph_metadata=graph_metadata,
                    execution_context=_effect_execution_context(
                        idempotency_key=idempotency_key,
                        queue_record=queue_record,
                        effect=effect,
                        graph_metadata=graph_metadata,
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
                binding_error=str(exc),
            )
            return False

        resolved_config = (
            resolved.resolved_config if isinstance(resolved.resolved_config, dict) else {}
        )
        binding_trace = binding_trace_to_payload(resolved.trace)
        executor = self.effect_executor_registry.get(effect.type)
        if executor is None:
            self._write_failed_effect_record(
                queue_record=queue_record,
                effect=effect,
                started_at=started_at,
                resolved_config=resolved_config,
                config_revision=config_revision,
                error=f"no_effect_executor_registered:{effect.type}",
                binding_trace=binding_trace,
            )
            return False
        try:
            resolved_config = executor.prepare_resolved_config(
                effect=effect,
                resolved_config=resolved_config,
                graph_metadata=graph_metadata,
                upstream_payload=upstream_payload,
                declared_upstream_output_names=upstream_output_names,
                system_value_resolver=_system_value_resolver(
                    started_at=started_at,
                    trace_id=queue_record.trace_id,
                    event_id=queue_record.event_id,
                    node_id=effect.id,
                ),
                started_at=started_at,
            )
        except (BindingResolutionError, OSError, ValueError) as exc:
            self._write_failed_effect_record(
                queue_record=queue_record,
                effect=effect,
                started_at=started_at,
                resolved_config=resolved_config,
                config_revision=config_revision,
                error=str(exc),
                binding_trace=binding_trace,
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
            binding_trace=binding_trace,
            config_revision=config_revision,
        )
        self.execution_store.record_execution(record)
        future = self._executor.submit(
            self._execute_provider,
            effect=effect,
            resolved_config=resolved_config,
            graph_metadata=graph_metadata,
            execution_context=_effect_execution_context(
                idempotency_key=idempotency_key,
                queue_record=queue_record,
                effect=effect,
                graph_metadata=graph_metadata,
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
                binding_error=str(exc),
            )

    def _execute_provider(
        self,
        *,
        effect: EffectDefinitionDiscriminated,
        resolved_config: dict[str, Any],
        graph_metadata: ResolvedWorkflowGraphMetadata | None = None,
        execution_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        executor = self.effect_executor_registry.resolve(effect.type)
        return dict(
            executor.execute(
                effect=effect,
                resolved_config=resolved_config,
                graph_metadata=graph_metadata,
                execution_context=dict(execution_context or {}),
            )
            or {}
        )

    def _execute_query_provider(
        self,
        *,
        query: QueryDefinitionDiscriminated,
        resolved_config: dict[str, Any],
        graph_metadata: ResolvedWorkflowGraphMetadata | None = None,
    ) -> dict[str, Any]:
        executor = self.query_executor_registry.resolve(query.type)
        return dict(
            executor.execute(
                query=query,
                resolved_config=resolved_config,
                graph_metadata=graph_metadata,
            )
            or {}
        )

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
        graph_for_prompt_error_routing: ExecutionGraph | None = None
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
                    if provider_class is not None:
                        provider_class.on_execution_waiting(worker=self, record=record)
                    self._update_event_delivery_state(record.event_id)
                    continue
            except Exception as exc:  # noqa: BLE001
                if record.effect_type in _PROMPT_FAILURE_EFFECT_TYPES:
                    outputs = _prompt_failure_outputs(exc)
                    if graph_for_prompt_error_routing is None:
                        graph_for_prompt_error_routing = self._build_execution_graph()
                    if _prompt_error_outbound_edge_exists(
                        graph=graph_for_prompt_error_routing,
                        effect_id=record.effect_id,
                    ):
                        logger.info(
                            "Effect execution routed to error branch "
                            "event_id=%s trace_id=%s effect_id=%s effect_type=%s branch_port=%s",
                            record.event_id,
                            record.trace_id,
                            record.effect_id,
                            record.effect_type,
                            AGENT_PROMPT_ERROR_PORT,
                        )
                        published_event_id = (
                            self._publish_effect_completion_event_or_fail(
                                record=record,
                                outputs=outputs,
                                branch_port=AGENT_PROMPT_ERROR_PORT,
                                finished_at=finished_at,
                            )
                        )
                        if published_event_id is None:
                            continue
                        self._finalize_effect_completion_publication(
                            record=record,
                            outputs=outputs,
                            published_event_id=published_event_id,
                            finished_at=finished_at,
                            status="error_routed",
                            branch_port=AGENT_PROMPT_ERROR_PORT,
                        )
                        published_count += 1
                        continue
                    logger.exception(
                        "Effect execution failed event_id=%s trace_id=%s effect_id=%s effect_type=%s",
                        record.event_id,
                        record.trace_id,
                        record.effect_id,
                        record.effect_type,
                    )
                else:
                    outputs = _outputs_from_exception(exc)
                    logger.exception(
                        "Effect execution failed event_id=%s trace_id=%s effect_id=%s effect_type=%s",
                        record.event_id,
                        record.trace_id,
                        record.effect_id,
                        record.effect_type,
                    )
                record.status = "failed"
                record.error = str(exc)
                record.outputs = outputs
                record.finished_at = finished_at
                self.execution_store.record_execution(record)
                self._update_event_delivery_state(record.event_id)
                continue

            record.outputs = outputs
            published_event_id = self._publish_effect_completion_event_or_mark_pending(
                record=record,
                outputs=outputs,
                pending_status="succeeded",
                finished_at=finished_at,
            )
            if published_event_id is None:
                continue

            self._finalize_effect_completion_publication(
                record=record,
                outputs=outputs,
                published_event_id=published_event_id,
                finished_at=finished_at,
            )
            published_count += 1

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
        return _store_has_inflight_execution_for_event(
            self.execution_store, event_id
        ) or _store_has_inflight_execution_for_event(self.gate_execution_store, event_id)

    def _publish_completion_event_from_record(
        self,
        *,
        record: EffectExecutionRecord,
        outputs: dict[str, Any],
        branch_port: str | None = None,
    ) -> str:
        completion_event, idempotency_key, completion_event_id = (
            self._build_completion_event_from_record(
                record=record,
                outputs=outputs,
                branch_port=branch_port,
            )
        )
        self.event_queue.publish(completion_event, idempotency_key=idempotency_key)
        return completion_event_id

    def _build_completion_event_from_record(
        self,
        *,
        record: EffectExecutionRecord,
        outputs: dict[str, Any],
        branch_port: str | None = None,
    ) -> tuple[TriggerEvent, str, str]:
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
        return completion_event, idempotency_key, completion_event_id

    def _publish_gate_completion_event_from_record(
        self,
        *,
        record: GateExecutionRecord,
        outputs: dict[str, Any],
        branch_port: str | None = None,
        for_execution_ids: dict[str, str] | None = None,
        preserved_context_node_ids: set[str] | None = None,
    ) -> str:
        completion_event, idempotency_key, completion_event_id = (
            self._build_gate_completion_event_from_record(
                record=record,
                outputs=outputs,
                branch_port=branch_port,
                for_execution_ids=for_execution_ids,
                preserved_context_node_ids=preserved_context_node_ids,
            )
        )
        self.event_queue.publish(completion_event, idempotency_key=idempotency_key)
        return completion_event_id

    def _build_gate_completion_event_from_record(
        self,
        *,
        record: GateExecutionRecord,
        outputs: dict[str, Any],
        branch_port: str | None = None,
        for_execution_ids: dict[str, str] | None = None,
        preserved_context_node_ids: set[str] | None = None,
    ) -> tuple[TriggerEvent, str, str]:
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
                preserved_context_node_ids=preserved_context_node_ids,
                branch_port=branch_port,
                for_execution_ids=for_execution_ids,
            ),
        )
        return completion_event, record.idempotency_key, completion_event_id

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
        binding_trace: dict[str, Any] | None = None,
        binding_error: str | None = None,
    ) -> None:
        self._write_terminal_effect_record(
            queue_record=queue_record,
            effect=effect,
            status="failed",
            started_at=started_at,
            resolved_config=resolved_config,
            config_revision=config_revision,
            error=error,
            binding_trace=binding_trace,
            binding_error=binding_error,
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
        binding_trace: dict[str, Any] | None = None,
        binding_error: str | None = None,
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
            binding_trace=binding_trace,
            binding_error=binding_error,
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
        binding_trace: dict[str, Any] | None = None,
        binding_error: str | None = None,
        publish_completion_event: bool = False,
        branch_port: str | None = None,
        for_execution_ids: dict[str, str] | None = None,
        preserved_context_node_ids: set[str] | None = None,
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
            binding_trace=binding_trace,
            binding_error=binding_error,
            config_revision=config_revision,
        )
        if publish_completion_event:
            record.status = COMPLETION_PENDING_STATUS
            record.completion_pending_status = status
            record.completion_event_branch_port = branch_port
            record.completion_event_for_execution_ids = for_execution_ids
            self.gate_execution_store.record_execution(record)
            self._update_event_delivery_state(record.event_id)
            published_event_id = self._publish_gate_completion_event_or_mark_pending(
                record=record,
                outputs=outputs,
                branch_port=branch_port,
                for_execution_ids=for_execution_ids,
                preserved_context_node_ids=preserved_context_node_ids,
                pending_status=status,
                finished_at=finished_at,
            )
            if published_event_id is None:
                return
            record.published_event_id = published_event_id
            record.status = status
            record.error = error
            record.completion_event_enqueued_at = finished_at
        self.gate_execution_store.record_execution(record)
        if publish_completion_event:
            self._update_event_delivery_state(record.event_id)

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


class SqliteExecutionStore(SqliteExecutionRepository[EffectExecutionRecord]):
    def __init__(
        self,
        path: Path,
        *,
        journal: Any | None = None,
        retention_days: int | None = None,
    ) -> None:
        super().__init__(
            path,
            EffectExecutionRecord,
            table_name="effect_executions",
            node_id_field="effect_id",
            node_type_field="effect_type",
            journal=journal,
            source="effect_execution",
            retention_days=retention_days,
        )


class SqliteGateExecutionStore(SqliteGateExecutionRepository[GateExecutionRecord]):
    def __init__(
        self,
        path: Path,
        *,
        journal: Any | None = None,
        retention_days: int | None = None,
    ) -> None:
        super().__init__(
            path,
            GateExecutionRecord,
            table_name="gate_executions",
            node_id_field="gate_id",
            node_type_field="gate_type",
            journal=journal,
            source="gate_execution",
            retention_days=retention_days,
        )


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
        project_dir=resolved_project_dir,
    )


def main() -> None:
    import argparse

    from evander.bootstrap import load_runtime_gateway_config

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
    node_graph_metadata_by_id = resolve_workflow_graph_metadata(
        nodes_by_id=nodes_by_id,
        edges=edges,
        workflow_graphs=worker_config.workflow_graphs,
    )

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
        node_graph_metadata_by_id=node_graph_metadata_by_id,
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
    preserved_context_node_ids: set[str] | None = None,
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
        if preserved_context_node_ids is None:
            preserved_context: dict[str, dict[str, Any]] = {}
        else:
            preserved_context = {
                node_id: dict(node_payload)
                for node_id, node_payload in inherited_context.items()
                if node_id in preserved_context_node_ids
            }
        preserved_context[completed_node_id] = dict(outputs)
        payload[NODE_OUTPUTS_CONTEXT_KEY] = preserved_context
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


def _build_workflow_config_snapshot(
    *,
    active_config: TriggerOrchestratorWorkerGlobalConfig,
    graph: ExecutionGraph,
    config_revision: str,
    captured_at: datetime,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "revision": config_revision,
        "captured_at": captured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "snapshot_type": "compiled_runtime_graph",
        "source": "effect_orchestrator.run_once",
        "node_count": len(graph.nodes_by_id),
        "edge_count": len(active_config.edges),
        "triggers": [_snapshot_json_value(trigger) for trigger in active_config.triggers],
        "gates": [_snapshot_json_value(gate) for gate in active_config.gates],
        "effects": [_snapshot_json_value(effect) for effect in active_config.effects],
        "queries": [_snapshot_json_value(query) for query in active_config.queries],
        "edges": [_snapshot_json_value(edge) for edge in active_config.edges],
    }
    workflow_graphs = _active_workflow_graphs(active_config)
    if workflow_graphs is not None:
        snapshot["workflow_graphs"] = _snapshot_json_value(workflow_graphs)
    return snapshot


def _provider_loader_signature(worker_config: TriggerOrchestratorWorkerGlobalConfig) -> str:
    payload = {
        "provider_loader": worker_config.provider_loader.model_dump(mode="json"),
        "addon_loader": worker_config.addon_loader.model_dump(mode="json"),
        "provider_include_builtin": worker_config.provider_include_builtin,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _effect_store_binding_signature(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> str:
    payload = {
        "runtime_dir": str(worker_config.runtime_dir),
        "orchestration_store_backend": worker_config.orchestration_store_backend,
        "orchestration_store_backend_explicit": "orchestration_store_backend"
        in worker_config.model_fields_set,
        "orchestration_store_path": str(worker_config.orchestration_store_path)
        if worker_config.orchestration_store_path is not None
        else None,
        "orchestration_retention_days": worker_config.orchestration_retention_days,
        "event_queue_store_path": str(worker_config.event_queue_store_path)
        if worker_config.event_queue_store_path is not None
        else None,
        "workflow_event_journal_path": str(worker_config.workflow_event_journal_path)
        if worker_config.workflow_event_journal_path is not None
        else None,
        "effect_execution_store_path": str(worker_config.effect_execution_store_path)
        if worker_config.effect_execution_store_path is not None
        else None,
        "gate_execution_store_path": str(worker_config.gate_execution_store_path)
        if worker_config.gate_execution_store_path is not None
        else None,
        "workflow_config_snapshot_store_path": str(
            worker_config.workflow_config_snapshot_store_path
        )
        if worker_config.workflow_config_snapshot_store_path is not None
        else None,
        "effect_cursor_store_path": str(worker_config.effect_cursor_store_path)
        if worker_config.effect_cursor_store_path is not None
        else None,
        "effect_lock_path": str(worker_config.effect_lock_path)
        if worker_config.effect_lock_path is not None
        else None,
        "internal_event_store_path": str(worker_config.internal_event_store_path)
        if worker_config.internal_event_store_path is not None
        else None,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _active_workflow_graphs(
    worker_config: TriggerOrchestratorWorkerGlobalConfig,
) -> Any | None:
    extra = getattr(worker_config, "model_extra", None)
    if isinstance(extra, dict) and "workflow_graphs" in extra:
        return extra["workflow_graphs"]
    if hasattr(worker_config, "workflow_graphs"):
        return getattr(worker_config, "workflow_graphs")
    return None


def _snapshot_json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _snapshot_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_snapshot_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_snapshot_json_value(item) for item in value]
    if isinstance(value, set):
        return [_snapshot_json_value(item) for item in sorted(value, key=repr)]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return value


def _compute_depth(
    node_id: str, downstream_index: dict[str, tuple[str, ...]], *, max_graph_depth: int
) -> int:
    try:
        return compute_downstream_depth(node_id, downstream_index)
    except WorkflowGraphCycleError:
        return max(1, int(max_graph_depth)) + 1


def _is_gate(node: WorkflowNodeDefinition) -> bool:
    return str(getattr(node, "type", "")).startswith("gate:")


def _gate_completion_preserved_context_node_ids(
    *, graph: ExecutionGraph, gate_id: str, gate_type: str
) -> set[str] | None:
    if str(gate_type or "").strip() != "gate:merge":
        return None
    return _guaranteed_context_node_ids(graph=graph, node_id=str(gate_id or "").strip())


def _guaranteed_context_node_ids(*, graph: ExecutionGraph, node_id: str) -> set[str]:
    node_ids = set(graph.nodes_by_id)
    if node_id not in node_ids:
        return set()
    predecessors: dict[str, set[str]] = {current_node_id: set() for current_node_id in node_ids}
    for inbound_edges in graph.inbound_index.values():
        for edge in inbound_edges:
            if _edge_allows_cycle(edge):
                continue
            source_id = _edge_source_id(edge)
            target_id = _edge_target_id(edge)
            if source_id in node_ids and target_id in node_ids:
                predecessors[target_id].add(source_id)

    dominators: dict[str, set[str]] = {}
    for current_node_id in node_ids:
        dominators[current_node_id] = (
            {current_node_id} if not predecessors[current_node_id] else set(node_ids)
        )

    changed = True
    while changed:
        changed = False
        for current_node_id in sorted(node_ids):
            preds = predecessors[current_node_id]
            if not preds:
                next_dominators = {current_node_id}
            else:
                shared = set(node_ids)
                for predecessor_id in preds:
                    shared &= dominators.get(predecessor_id, {predecessor_id})
                next_dominators = shared | {current_node_id}
            if next_dominators != dominators[current_node_id]:
                dominators[current_node_id] = next_dominators
                changed = True
    return dominators.get(node_id, {node_id}) - {node_id}


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


def _normalize_started_at(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return datetime.now(UTC)


def _normalize_datetime_value(value: Any, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    text = _normalize_optional_string(value)
    if text is None:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_delay_seconds(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("delay_seconds must be a number")
    try:
        if isinstance(value, (int, float)):
            delay_seconds = float(value)
        else:
            text = _normalize_optional_string(value)
            if text is None:
                raise ValueError("delay_seconds must be a number")
            delay_seconds = float(text)
    except OverflowError as exc:
        raise ValueError("delay_seconds must be a finite number") from exc
    except ValueError as exc:
        raise ValueError("delay_seconds must be a number") from exc
    if not math.isfinite(delay_seconds):
        raise ValueError("delay_seconds must be a finite number")
    return delay_seconds


def _delay_seconds_within_datetime_range(started_at: datetime, delay_seconds: float) -> bool:
    started_at = _normalize_started_at(started_at)
    if delay_seconds >= 0:
        max_delay_seconds = (datetime.max.replace(tzinfo=UTC) - started_at).total_seconds()
        return delay_seconds <= max_delay_seconds
    min_delay_seconds = (started_at - datetime.min.replace(tzinfo=UTC)).total_seconds()
    return -delay_seconds <= min_delay_seconds


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


def _compute_effect_cycle_sleep_seconds(
    *,
    events_processed: int,
    completion_events_published: int,
    poll_interval_seconds: float,
    jitter_seconds: float,
) -> float:
    if events_processed > 0 or completion_events_published > 0:
        return 0.0
    return _compute_next_sleep_seconds(
        poll_interval_seconds=poll_interval_seconds,
        jitter_seconds=jitter_seconds,
    )


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
    graph_metadata: ResolvedWorkflowGraphMetadata | None = None,
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
    if graph_metadata is not None:
        context["graph_id"] = graph_metadata.graph_id
        context["directory_path"] = graph_metadata.directory_path
        context["graph_metadata"] = {
            "graph_id": graph_metadata.graph_id,
            "directory_path": graph_metadata.directory_path,
        }
    if isinstance(input_payload.get(NODE_OUTPUTS_CONTEXT_KEY), dict):
        context[NODE_OUTPUTS_CONTEXT_KEY] = input_payload[NODE_OUTPUTS_CONTEXT_KEY]
    return context
