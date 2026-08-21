from __future__ import annotations

from config_helpers import (
    canonical_worker_config,
    effect_execution_payload,
    gate_execution_payload,
    trigger_queue_payload,
    workflow_config_snapshot_payload,
    workflow_event_payload,
)

import json
import logging
import sys
import time
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
import yaml

from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.execution import ExecutionStatusKind
from evander.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from evander.effects.providers.agent_prompt import AgentPromptEffectProvider
from evander.effects.providers.lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook
from evander.effects.providers.base import (
    EffectProviderExecutionError,
    EffectRuntimeContext,
)
from evander.effects.providers.session_renew_if_expired import (
    SessionRenewIfExpiredEffectProvider,
)
from evander.effects.providers.vm_copy_files import VmCopyFilesEffectProvider
from evander.daemons.effect_orchestrator import (
    AwaitAgentPendingRecord,
    EffectOrchestratorWorker,
    ExecutionGraph,
    GateExecutionRecord,
    EventScheduleEffectExecutor,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
    _await_agent_pending_target,
    _build_execution_graph,
    _matching_outbound_edges,
    build_effect_orchestrator_worker,
    EffectExecutionRecord,
    EffectOrchestratorCycleResult,
    main,
)
from evander.events.scheduled import ScheduledInternalEventStore
from evander.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
    AgentPromptRetryLimitExceeded,
    QueuedAgentPromptRecord,
    agent_prompt_replay_queue_key,
)
from evander.messaging.agent_prompt_enqueue import enqueue_agent_prompt
from evander.messaging.agent_prompt_lock import AgentPromptLockStore, agent_prompt_lock_key
from evander.local_inference_capacity import (
    LOCAL_INFERENCE_QUEUE_REASON,
    LocalInferenceCapacityStore,
)
from evander.model_usage.runtime import model_usage_store
from evander.orchestration.host_polling import HostFileLockManager
from evander.events.models import TriggerEvent
from evander.messaging.wake_store import WakeRequestStore
from evander.models import (
    SessionManifest,
    ToolName,
    VmSummary,
    WakeDispatchRecord,
    WakeResumePromptPayload,
    WakeScheduleDefinition,
)
from evander.repositories.orchestration import SqliteWorkflowConfigSnapshotRepository
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.queries.registry import QueryProviderFactory
from evander.triggers.queue import SqliteTriggerEventQueue, TriggerQueueRecord


class _Provider:
    def __init__(self, output: dict[str, object], *, wait_on: Event | None = None) -> None:
        self.output = output
        self.wait_on = wait_on

    def execute(self, *, resolved_config=None):
        if self.wait_on is not None:
            self.wait_on.wait(timeout=2)
        return dict(self.output)


class _FailingProvider(_Provider):
    def execute(self, *, resolved_config=None):
        raise RuntimeError("provider boom")


class _FailingWithOutputsProvider(_Provider):
    def execute(self, *, resolved_config=None):
        raise EffectProviderExecutionError(
            "checked provider boom",
            outputs={
                "success": False,
                "status_code": 502,
                "body_text": "bad gateway",
            },
        )


class _CountingProvider:
    def __init__(
        self,
        output: dict[str, object],
        execute_calls: list[dict[str, object]],
    ) -> None:
        self.output = output
        self.execute_calls = execute_calls

    def execute(
        self,
        *,
        resolved_config=None,
        execution_context=None,
    ) -> dict[str, object]:
        self.execute_calls.append(
            {
                "resolved_config": dict(resolved_config or {}),
                "execution_context": dict(execution_context or {}),
            }
        )
        return dict(self.output)


class _Factory:
    def __init__(
        self,
        outputs_by_effect_id: dict[str, dict[str, object]],
        *,
        wait_on_by_effect_id: dict[str, Event] | None = None,
    ) -> None:
        self.outputs_by_effect_id = outputs_by_effect_id
        self.wait_on_by_effect_id = wait_on_by_effect_id or {}

    def build(self, effect, **kwargs):
        return _Provider(
            self.outputs_by_effect_id.get(effect.id, {}),
            wait_on=self.wait_on_by_effect_id.get(effect.id),
        )


class _CountingFactory:
    def __init__(self, outputs_by_effect_id: dict[str, dict[str, object]]) -> None:
        self.outputs_by_effect_id = outputs_by_effect_id
        self.execute_calls: list[dict[str, object]] = []
        self.build_calls: list[dict[str, object]] = []

    def build(self, effect, **kwargs):
        self.build_calls.append({"effect_id": effect.id, **dict(kwargs)})
        return _CountingProvider(
            self.outputs_by_effect_id.get(effect.id, {}),
            self.execute_calls,
        )


class _ProviderConfigResolver:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        del directory_path
        return self.config


class _SessionExpiryGuardProvider:
    def __init__(
        self,
        session_repository: JsonSessionManifestRepository,
        prompt_calls: list[dict[str, object]],
    ) -> None:
        self.session_repository = session_repository
        self.prompt_calls = prompt_calls

    def execute(self, *, resolved_config=None):
        payload = dict(resolved_config or {})
        session_id = str(payload["session_id"])
        session = self.session_repository.get_session(session_id)
        if session is None:
            raise RuntimeError(f"prompt guard could not find session {session_id}")
        if session.expires_at <= datetime.now(timezone.utc):
            raise RuntimeError(f"prompt guard saw expired session {session_id}")
        self.prompt_calls.append(
            {
                "session_id": session_id,
                "prompt": payload.get("prompt"),
                "expires_at": session.expires_at,
            }
        )
        return {"success": True, "thread_id": "thread-guarded"}


class _RenewThenPromptFactory:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        session_repository: JsonSessionManifestRepository,
        prompt_calls: list[dict[str, object]],
    ) -> None:
        self.config = config
        self.session_repository = session_repository
        self.prompt_calls = prompt_calls

    def build(self, effect, **kwargs):
        del kwargs
        if effect.type == "effect:session:renew-if-expired":
            provider = SessionRenewIfExpiredEffectProvider()
            provider.initialize(
                effect_definition=effect,
                runtime_context=EffectRuntimeContext(
                    resolved_config=self.config,
                    admin_service=SimpleNamespace(
                        runtime=SimpleNamespace(
                            session_repository=self.session_repository,
                            provider_config=_ProviderConfigResolver(self.config),
                        )
                    ),
                    env={},
                ),
            )
            return provider
        if effect.type == "effect:agent:prompt":
            return _SessionExpiryGuardProvider(self.session_repository, self.prompt_calls)
        raise KeyError(effect.type)


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future: Future[object] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future


class _ManualExecutor:
    def __init__(self) -> None:
        self.futures: list[Future[dict[str, object]]] = []

    def submit(self, fn, *args, **kwargs):
        del fn, args, kwargs
        future: Future[dict[str, object]] = Future()
        self.futures.append(future)
        return future


class _RecordingWakeNotifier:
    def __init__(self, *, before_notify=None) -> None:
        self.before_notify = before_notify
        self.calls = 0

    def notify(self) -> bool:
        self.calls += 1
        if self.before_notify is not None:
            self.before_notify()
        return True


class _FailingFactory:
    def build(self, effect, **kwargs):
        return _FailingProvider({})


class _FailingWithOutputsFactory:
    def build(self, effect):
        return _FailingWithOutputsProvider({})


class _GenericErrorRoutingFactory:
    def build(self, effect, **kwargs):
        del kwargs
        if effect.id == "effect-1":
            return _FailingProvider({})
        return _Provider({"success": True})


class _PromptErrorRoutingFactory:
    def __init__(self, outputs_by_effect_id: dict[str, dict[str, object]]) -> None:
        self.outputs_by_effect_id = outputs_by_effect_id

    def build(self, effect, **kwargs):
        del kwargs
        if effect.type == "effect:agent:prompt":
            return _FailingWithOutputsProvider({})
        return _Provider(self.outputs_by_effect_id.get(effect.id, {}))


class _StaticReloader:
    def __init__(self, config: TriggerOrchestratorWorkerGlobalConfig, *, changed: bool) -> None:
        self._config = config
        self._changed = changed

    def get_latest(self) -> tuple[TriggerOrchestratorWorkerGlobalConfig, bool]:
        changed = self._changed
        self._changed = False
        return self._config, changed


class _AgentRunStatusPortStub:
    def __init__(self, summaries: list[object]) -> None:
        self.summaries = summaries

    def list_vm_summaries(self) -> list[object]:
        return list(self.summaries)


class _FailingAgentRunStatusPortStub:
    def list_vm_summaries(self) -> list[object]:
        raise RuntimeError("virsh list failed")


class _AgentPromptDispatchPortStub:
    def __init__(self, resume_calls: list[dict[str, object]], result: object) -> None:
        self.resume_calls = resume_calls
        self.result = result

    def dispatch_agent_prompt(
        self,
        *,
        target: str,
        prompt: str,
        model: str | None = None,
        vm_user: str = "agent",
        dispatch_id: str | None = None,
        resume_thread_id: str | None = None,
        resume_mode: str = "new_context",
        persist_thread_id: bool = True,
        mcp_gateway_url: str | None = None,
    ) -> object:
        self.resume_calls.append(
            {
                "target": target,
                "prompt": prompt,
                "model": model,
                "vm_user": vm_user,
                "dispatch_id": dispatch_id,
                "resume_thread_id": resume_thread_id,
                "resume_mode": resume_mode,
                "persist_thread_id": persist_thread_id,
                "mcp_gateway_url": mcp_gateway_url,
            }
        )
        return self.result


class _DeliveryStateQueueStub:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def mark_inflight(self, event_id: str) -> None:
        self.calls.append(("inflight", event_id))

    def mark_delivered(self, event_id: str) -> None:
        self.calls.append(("delivered", event_id))


class _ContainerDispatchBackendStub:
    def __init__(self) -> None:
        self.repository = SimpleNamespace(read_all=lambda: {})
        self.prepared_specs: list[object] = []
        self.started_specs: list[object] = []
        self.stopped: list[str] = []
        self.interrupted: list[tuple[str, float | None, str]] = []
        self.destroyed: list[str] = []
        self.config: object | None = None
        self.status = SimpleNamespace(
            status=ExecutionStatusKind.STOPPED,
            message=None,
            metadata={},
        )
        self.logs = ""

    def prepare(self, spec):
        self.prepared_specs.append(spec)
        return spec

    def start(self, spec):
        self.started_specs.append(spec)
        return SimpleNamespace(execution_id=spec.target_name)

    def inspect(self, execution_id: str):
        del execution_id
        return self.status

    def get_log_snapshot(self, execution_id: str, *, lines: int = 50):
        del execution_id, lines
        return self.logs

    def stop(self, execution_id: str, *, timeout_seconds: int = 60):
        del timeout_seconds
        self.stopped.append(execution_id)

    def interrupt_active_tool(
        self,
        execution_id: str,
        *,
        max_elapsed_seconds: float | None = None,
        reason: str = "operator_requested",
    ) -> None:
        self.interrupted.append((execution_id, max_elapsed_seconds, reason))

    def destroy(self, execution_id: str):
        self.destroyed.append(execution_id)


def _edge(source_id: str, target_id: str, *, source_port: str | None = None) -> dict[str, str]:
    edge = {"source_id": source_id, "target_id": target_id}
    if source_port is not None:
        edge["source_port"] = source_port
    return edge


def _future_wake_worker_config(
    runtime_dir: Path,
    *,
    node_kind: str,
    include_downstream_effect: bool = False,
) -> TriggerOrchestratorWorkerGlobalConfig:
    trigger = {
        "id": "trigger-1",
        "display_name": "Tick",
        "type": "trigger:cron",
        "expression": "* * * * *",
    }
    effects: list[dict[str, object]] = []
    queries: list[dict[str, object]] = []
    if node_kind == "effect":
        effects.append(
            {
                "id": "effect-1",
                "display_name": "Create VM",
                "type": "effect:vm:create",
                "vm_name": "vm-1",
            }
        )
        if include_downstream_effect:
            effects.append(
                {
                    "id": "effect-2",
                    "display_name": "Create downstream VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-2",
                }
            )
    elif node_kind == "query":
        queries.append(
            {
                "id": "query-1",
                "display_name": "VM run state",
                "type": "query:vm:run-state",
                "session_id": "session-1",
            }
        )
    else:
        raise ValueError(f"unsupported node kind: {node_kind}")

    first_node_id = "effect-1" if node_kind == "effect" else "query-1"
    edges = [_edge("trigger-1", first_node_id)]
    if include_downstream_effect:
        edges.append(_edge("effect-1", "effect-2"))
    return canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [trigger],
            "effects": effects,
            "queries": queries,
            "edges": edges,
        }
    )


def _queued_prompt_execution_record(
    record: QueuedAgentPromptRecord,
    *,
    status: str,
) -> EffectExecutionRecord:
    now = datetime.now(timezone.utc)
    dispatch_id = str(record.dispatch_id or "")
    event_id = f"queued-agent-prompt:{record.queue_key}:{dispatch_id}"
    return EffectExecutionRecord(
        execution_id=f"exec-{dispatch_id}",
        idempotency_key=f"{event_id}::{record.effect_id}",
        event_id=event_id,
        trace_id=f"queued-agent-prompt:{record.queue_key}",
        source_node_id=record.effect_id,
        source_trigger_type="effect:agent:enqueue-prompt",
        effect_id=record.effect_id,
        effect_type="effect:agent:enqueue-prompt",
        status=status,
        started_at=now,
        finished_at=now,
        resolved_config_hash="queued-config-hash",
        resolved_config={"queue_key": record.queue_key},
        input_payload={"queue_key": record.queue_key},
        outputs={
            "queue_key": record.queue_key,
            "dispatch_id": dispatch_id,
            "session_id": record.session_id,
            "vm_name": record.vm_name,
        },
    )


def _await_agent_store(worker: EffectOrchestratorWorker):
    provider = worker._get_gate_provider("gate:await-agent")
    assert provider is not None
    return provider.load_state()


def test_agent_prompt_lock_store_allows_one_owner_until_release(tmp_path: Path) -> None:
    store = AgentPromptLockStore(tmp_path / "agent-prompt-locks.json")
    lock_key = agent_prompt_lock_key(
        target="session-42",
        vm_name="vm-1",
        session_id="session-42",
    )

    first = store.claim(
        lock_key=lock_key,
        owner_id="owner-1",
        target="session-42",
        vm_name="vm-1",
        session_id="session-42",
        dispatch_id="dispatch-1",
        effect_id="effect-1",
    )
    second = store.claim(
        lock_key=lock_key,
        owner_id="owner-2",
        target="session-42",
        vm_name="vm-1",
        session_id="session-42",
        dispatch_id="dispatch-2",
        effect_id="effect-1",
    )

    assert first.claimed is True
    assert first.record is not None
    assert first.record.lock_key == "session:session-42"
    assert second.claimed is False
    assert second.existing is not None
    assert second.existing.owner_id == "owner-1"
    assert store.release(lock_key=lock_key, owner_id="owner-2") is False
    assert store.release(lock_key=lock_key, owner_id="owner-1") is True

    third = store.claim(
        lock_key=lock_key,
        owner_id="owner-3",
        target="session-42",
        vm_name="vm-1",
        session_id="session-42",
        dispatch_id="dispatch-3",
        effect_id="effect-1",
    )

    assert third.claimed is True
    assert third.record is not None
    assert third.record.owner_id == "owner-3"


def test_matching_outbound_edges_honors_resume_target_node() -> None:
    queue_record = TriggerQueueRecord(
        event_id="evt-1",
        trigger_id="trigger-ticket",
        trigger_type="trigger:ticket:created",
        trace_id="trace-1",
        occurred_at=datetime.now(timezone.utc),
        payload={"__resume_target_node_id": "effect-b"},
    )
    graph = ExecutionGraph(
        nodes_by_id={},
        gates_by_id={},
        effects_by_id={},
        queries_by_id={},
        outbound_index={
            "trigger-ticket": (
                _edge("trigger-ticket", "effect-a"),
                _edge("trigger-ticket", "effect-b"),
            )
        },
        downstream_index={},
        inbound_index={},
        declared_outputs={},
    )

    matched = _matching_outbound_edges(queue_record=queue_record, graph=graph)

    assert [edge["target_id"] for edge in matched] == ["effect-b"]


def test_build_execution_graph_resolves_state_workflow_graph_metadata() -> None:
    worker_config = canonical_worker_config(
        {
            "runtime_dir": Path("runtime"),
            "triggers": [
                {
                    "id": "trigger-state",
                    "display_name": "State trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-state-update",
                    "display_name": "Update state",
                    "type": "effect:state:update",
                    "namespace": "workflow",
                    "key": "cursor",
                    "operation": "set",
                    "value": {"counter": 1},
                }
            ],
            "queries": [
                {
                    "id": "query-state-get",
                    "display_name": "Read state",
                    "type": "query:state:get",
                    "namespace": "workflow",
                    "key": "cursor",
                }
            ],
            "edges": [
                {"source_id": "trigger-state", "target_id": "effect-state-update"},
                {"source_id": "effect-state-update", "target_id": "query-state-get"},
            ],
            "workflow_graphs": [
                {
                    "id": "graph-state",
                    "name": "State graph",
                    "directory_path": "root/team-a",
                    "node_ids": [
                        "trigger-state",
                        "effect-state-update",
                        "query-state-get",
                    ],
                }
            ],
        }
    )

    graph = _build_execution_graph(worker_config)

    assert graph.node_graph_metadata_by_id["trigger-state"].graph_id == "graph-state"
    assert graph.node_graph_metadata_by_id["effect-state-update"].directory_path == "root/team-a"
    assert graph.node_graph_metadata_by_id["query-state-get"].graph_id == "graph-state"


def test_effect_orchestrator_executes_state_nodes_with_graph_isolation(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-alpha",
                    "display_name": "Alpha tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                },
                {
                    "id": "trigger-beta",
                    "display_name": "Beta tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                },
            ],
            "effects": [
                {
                    "id": "effect-alpha-state",
                    "display_name": "Store alpha state",
                    "type": "effect:state:update",
                    "namespace": "workflow",
                    "key": "cursor",
                    "operation": "set",
                    "value": {"counter": 1},
                },
                {
                    "id": "effect-beta-state",
                    "display_name": "Store beta state",
                    "type": "effect:state:update",
                    "namespace": "workflow",
                    "key": "cursor",
                    "operation": "set",
                    "value": {"counter": 2},
                },
            ],
            "queries": [
                {
                    "id": "query-alpha-state",
                    "display_name": "Read alpha state",
                    "type": "query:state:get",
                    "namespace": "workflow",
                    "key": "cursor",
                },
                {
                    "id": "query-beta-state",
                    "display_name": "Read beta state",
                    "type": "query:state:get",
                    "namespace": "workflow",
                    "key": "cursor",
                },
            ],
            "edges": [
                _edge("trigger-alpha", "effect-alpha-state"),
                _edge("effect-alpha-state", "query-alpha-state"),
                _edge("trigger-beta", "effect-beta-state"),
                _edge("effect-beta-state", "query-beta-state"),
            ],
            "workflow_graphs": [
                {
                    "id": "graph-alpha",
                    "name": "Alpha graph",
                    "directory_path": "root",
                    "node_ids": [
                        "trigger-alpha",
                        "effect-alpha-state",
                        "query-alpha-state",
                    ],
                },
                {
                    "id": "graph-beta",
                    "name": "Beta graph",
                    "directory_path": "root",
                    "node_ids": [
                        "trigger-beta",
                        "effect-beta-state",
                        "query-beta-state",
                    ],
                },
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-alpha",
            trigger_id="trigger-alpha",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-beta",
            trigger_id="trigger-beta",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 1, tzinfo=timezone.utc),
            payload={},
        )
    )

    for _ in range(8):
        worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    records_by_effect_id = {record["effect_id"]: record for record in execution_payload.values()}

    alpha_effect = records_by_effect_id["effect-alpha-state"]
    alpha_query = records_by_effect_id["query-alpha-state"]
    beta_effect = records_by_effect_id["effect-beta-state"]
    beta_query = records_by_effect_id["query-beta-state"]

    assert alpha_effect["outputs"]["graph_id"] == "graph-alpha"
    assert alpha_effect["outputs"]["value"] == {"counter": 1}
    assert alpha_effect["outputs"]["version"] == 1
    assert alpha_query["outputs"]["graph_id"] == "graph-alpha"
    assert alpha_query["outputs"]["value"] == {"counter": 1}
    assert alpha_query["outputs"]["found"] is True
    assert beta_effect["outputs"]["graph_id"] == "graph-beta"
    assert beta_effect["outputs"]["value"] == {"counter": 2}
    assert beta_effect["outputs"]["version"] == 1
    assert beta_query["outputs"]["graph_id"] == "graph-beta"
    assert beta_query["outputs"]["value"] == {"counter": 2}
    assert beta_query["outputs"]["found"] is True

    state_payload = json.loads((runtime_dir / "workflow-state.json").read_text(encoding="utf-8"))
    graph_state_records = state_payload["records"]["root"]
    assert graph_state_records["graph-alpha"]["workflow"]["cursor"]["value"] == {"counter": 1}
    assert graph_state_records["graph-beta"]["workflow"]["cursor"]["value"] == {"counter": 2}


def test_effect_orchestrator_rejects_state_nodes_without_persisted_graph_metadata(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    state_path = runtime_dir / "workflow-state.json"
    state_path.write_text('{"sentinel": true}', encoding="utf-8")

    config = GatewayConfig(runtime_dir=runtime_dir)
    valid_worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-state",
                    "display_name": "State trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-state-update",
                    "display_name": "Update state",
                    "type": "effect:state:update",
                    "namespace": "workflow",
                    "key": "cursor",
                    "operation": "set",
                    "value": {"counter": 1},
                }
            ],
            "queries": [
                {
                    "id": "query-state-get",
                    "display_name": "Read state",
                    "type": "query:state:get",
                    "namespace": "workflow",
                    "key": "cursor",
                }
            ],
            "edges": [
                {"source_id": "trigger-state", "target_id": "effect-state-update"},
                {"source_id": "effect-state-update", "target_id": "query-state-get"},
            ],
            "workflow_graphs": [
                {
                    "id": "graph-state",
                    "name": "State graph",
                    "directory_path": "root",
                    "node_ids": [
                        "trigger-state",
                        "effect-state-update",
                        "query-state-get",
                    ],
                }
            ],
        }
    )
    worker_config = valid_worker_config.model_copy(update={"workflow_graphs": []})
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-state-missing-1",
            trigger_id="trigger-state",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    with pytest.raises(ValueError, match="persisted workflow graph metadata"):
        worker.run_once()

    assert state_path.read_text(encoding="utf-8") == '{"sentinel": true}'


def test_effect_orchestrator_threads_graph_metadata_into_provider_contexts(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "prompt": "hello",
                }
            ],
            "queries": [
                {
                    "id": "query-1",
                    "display_name": "VM state",
                    "type": "query:vm:run-state",
                    "session_id": "session-1",
                }
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("trigger-1", "query-1"),
            ],
            "workflow_graphs": [
                {
                    "id": "graph-1",
                    "name": "Graph 1",
                    "directory_path": "root/team-a",
                    "node_ids": ["trigger-1", "effect-1", "query-1"],
                }
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    effect_factory = _CountingFactory({"effect-1": {"success": True}})
    query_factory = _CountingFactory({"query-1": {"state_code": "running"}})
    worker.provider_factory = effect_factory
    worker.query_provider_factory = query_factory

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-graph-metadata",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()

    assert effect_factory.build_calls
    assert all(
        call
        == {
            "effect_id": "effect-1",
            "directory_path": "root/team-a",
            "graph_id": "graph-1",
        }
        for call in effect_factory.build_calls
    )
    assert query_factory.build_calls == [
        {
            "effect_id": "query-1",
            "directory_path": "root/team-a",
            "graph_id": "graph-1",
        }
    ]
    assert effect_factory.execute_calls[0]["execution_context"]["graph_id"] == "graph-1"
    assert effect_factory.execute_calls[0]["execution_context"]["directory_path"] == "root/team-a"


def test_effect_orchestrator_main_help_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["evander-effect-orchestrator", "--help"])

    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0


def test_build_effect_orchestrator_worker_accepts_injected_services(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)

    class _InjectedServices:
        def __init__(self) -> None:
            self.called_with: GatewayConfig | None = None

        def build_runtime(self, cfg: GatewayConfig):
            self.called_with = cfg
            return object()

    services = _InjectedServices()
    worker = build_effect_orchestrator_worker(config, project_dir=tmp_path, services=services)

    assert services.called_with is not None
    assert services.called_with.runtime_dir == runtime_dir
    assert worker.runtime is not None


def test_effect_orchestrator_run_once_uses_reloaded_worker_config_for_sleep(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [],
        }
    )
    updated_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 13,
            "jitter_seconds": 0,
            "triggers": [],
        }
    )
    worker = EffectOrchestratorWorker(
        config=config,
        worker_config=worker_config,
        config_reloader=_StaticReloader(updated_config, changed=True),
        runtime=None,  # type: ignore[arg-type]
    )

    result = worker.run_once()

    assert result.next_sleep_seconds == 13


def test_event_schedule_effect_uses_reloaded_scheduled_store_path(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    old_store_path = runtime_dir / "old-scheduled-events.json"
    new_store_path = runtime_dir / "new-scheduled-events.json"
    config = GatewayConfig(runtime_dir=runtime_dir)
    base_worker_config = {
        "runtime_dir": runtime_dir,
        "poll_interval_seconds": 1,
        "jitter_seconds": 0,
        "triggers": [
            {
                "id": "trigger-1",
                "display_name": "Trigger",
                "type": "trigger:cron",
                "expression": "* * * * *",
            }
        ],
        "effects": [
            {
                "id": "schedule-plan",
                "display_name": "Schedule plan",
                "type": "effect:event:schedule",
                "event_type": "custom.operator_event",
                "payload": {"value": "ok"},
                "run_at": "2026-05-25T12:05:00Z",
            }
        ],
        "edges": [_edge("trigger-1", "schedule-plan")],
    }
    worker_config = canonical_worker_config(
        {
            **base_worker_config,
            "scheduled_internal_event_store_path": old_store_path,
        }
    )
    updated_config = canonical_worker_config(
        {
            **base_worker_config,
            "scheduled_internal_event_store_path": new_store_path,
        }
    )
    worker = EffectOrchestratorWorker(
        config=config,
        worker_config=worker_config,
        config_reloader=_StaticReloader(updated_config, changed=True),
        runtime=None,  # type: ignore[arg-type]
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="input-event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(timezone.utc),
            trace_id="trace-1",
            payload={},
        )
    )

    worker.run_once()
    worker._wait_for_inflight_completion(timeout_seconds=2)
    worker.run_once()

    assert not old_store_path.exists()
    scheduled_events = json.loads(new_store_path.read_text(encoding="utf-8"))
    scheduled = next(iter(scheduled_events.values()))
    assert scheduled["event_type"] == "custom.operator_event"
    assert scheduled["payload"] == {"value": "ok"}
    assert scheduled["run_at"] == "2026-05-25T12:05:00Z"


def test_effect_orchestrator_uses_shared_host_lock_manager(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [],
            "effects": [],
            "gates": [],
            "queries": [],
            "edges": [],
        }
    )
    worker = EffectOrchestratorWorker(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )

    assert isinstance(worker._kernel._lock_manager, HostFileLockManager)
    assert worker._kernel.config.lock_path == worker.lock_path

    sentinel = object()
    worker._kernel.run_once_with_lock = lambda: sentinel  # type: ignore[method-assign]

    assert worker.run_once_with_lock() is sentinel


@pytest.mark.parametrize("node_kind", ["effect", "query"])
def test_effect_orchestrator_notifies_once_when_new_future_completes(
    tmp_path: Path,
    node_kind: str,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = _future_wake_worker_config(runtime_dir, node_kind=node_kind)
    worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=runtime_dir),
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    executor = _ManualExecutor()
    notifier = _RecordingWakeNotifier()
    worker._executor = executor  # type: ignore[assignment]
    worker._wake_notifier = notifier  # type: ignore[assignment]
    worker.event_queue.publish(
        TriggerEvent(
            event_id=f"event-new-{node_kind}",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()

    assert len(executor.futures) == 1
    future = executor.futures[0]
    future.set_result({"success": True})

    assert notifier.calls == 1
    idempotency_key = f"event-new-{node_kind}::{node_kind}-1"
    assert idempotency_key in worker._inflight
    running_record = worker.execution_store.find_execution_by_idempotency_key(idempotency_key)
    assert running_record is not None
    assert running_record.status == "running"

    cycle = worker.run_once()

    completed_record = worker.execution_store.find_execution_by_idempotency_key(idempotency_key)
    assert completed_record is not None
    assert completed_record.status == "succeeded"
    assert cycle.completion_events_published == 1
    assert notifier.calls == 1


@pytest.mark.parametrize("node_kind", ["effect", "query"])
def test_effect_orchestrator_notifies_when_persisted_running_future_is_resubmitted(
    tmp_path: Path,
    node_kind: str,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = _future_wake_worker_config(runtime_dir, node_kind=node_kind)
    config = GatewayConfig(runtime_dir=runtime_dir)
    first_worker = EffectOrchestratorWorker(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    first_executor = _ManualExecutor()
    first_worker._executor = first_executor  # type: ignore[assignment]
    first_worker.event_queue.publish(
        TriggerEvent(
            event_id=f"event-recovered-{node_kind}",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )
    first_worker.run_once()
    assert len(first_executor.futures) == 1

    restarted_worker = EffectOrchestratorWorker(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    restarted_executor = _ManualExecutor()
    notifier = _RecordingWakeNotifier()
    restarted_worker._executor = restarted_executor  # type: ignore[assignment]
    restarted_worker._wake_notifier = notifier  # type: ignore[assignment]

    restarted_worker.run_once()

    assert len(restarted_executor.futures) == 1
    restarted_executor.futures[0].set_result({"success": True})
    assert notifier.calls == 1


def test_effect_orchestrator_registers_completed_future_before_wake_callback(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = _future_wake_worker_config(runtime_dir, node_kind="effect")
    worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=runtime_dir),
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    idempotency_key = "event-already-complete::effect-1"
    inflight_states: list[bool] = []
    notifier = _RecordingWakeNotifier(
        before_notify=lambda: inflight_states.append(idempotency_key in worker._inflight)
    )
    worker._wake_notifier = notifier  # type: ignore[assignment]
    future: Future[dict[str, object]] = Future()
    future.set_result({"success": True})

    worker._register_inflight_future(
        idempotency_key=idempotency_key,
        future=future,  # type: ignore[arg-type]
        config_revision="revision-1",
    )

    assert inflight_states == [True]
    assert notifier.calls == 1
    assert worker._inflight[idempotency_key].future is future


def test_effect_orchestrator_missing_wake_listener_falls_back_to_polling(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = _future_wake_worker_config(runtime_dir, node_kind="effect")
    worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=runtime_dir),
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    executor = _ManualExecutor()
    worker._executor = executor  # type: ignore[assignment]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-missing-listener",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()
    assert not worker._wake_notifier.path.exists()

    executor.futures[0].set_result({"success": True})

    idempotency_key = "event-missing-listener::effect-1"
    running_record = worker.execution_store.find_execution_by_idempotency_key(idempotency_key)
    assert running_record is not None
    assert running_record.status == "running"
    assert idempotency_key in worker._inflight

    cycle = worker.run_once()

    completed_record = worker.execution_store.find_execution_by_idempotency_key(idempotency_key)
    assert completed_record is not None
    assert completed_record.status == "succeeded"
    assert cycle.completion_events_published == 1


def test_effect_completion_callback_only_wakes_before_next_cycle_dispatches_downstream(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    worker_config = _future_wake_worker_config(
        runtime_dir,
        node_kind="effect",
        include_downstream_effect=True,
    )
    worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=runtime_dir),
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    executor = _ManualExecutor()
    notifier = _RecordingWakeNotifier()
    worker._executor = executor  # type: ignore[assignment]
    worker._wake_notifier = notifier  # type: ignore[assignment]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-downstream",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )
    worker.run_once()

    executor.futures[0].set_result({"success": True})

    first_record = worker.execution_store.find_execution_by_idempotency_key(
        "event-downstream::effect-1"
    )
    assert first_record is not None
    assert first_record.status == "running"
    assert len(executor.futures) == 1
    assert notifier.calls == 1

    worker.run_once()

    first_record = worker.execution_store.find_execution_by_idempotency_key(
        "event-downstream::effect-1"
    )
    assert first_record is not None
    assert first_record.status == "succeeded"
    assert len(executor.futures) == 2
    downstream_record = next(
        record
        for record in worker.execution_store.list_executions()
        if record.effect_id == "effect-2"
    )
    assert downstream_record.status == "running"
    assert notifier.calls == 1


def test_effect_orchestrator_holds_delivery_for_gate_completion_pending(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [],
            "effects": [],
            "gates": [],
            "queries": [],
            "edges": [],
        }
    )
    worker = EffectOrchestratorWorker(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    queue = _DeliveryStateQueueStub()
    worker.event_queue = queue

    db_path = tmp_path / "orchestration.db"
    gate_store = SqliteGateExecutionStore(db_path)
    gate_record = GateExecutionRecord(
        execution_id="gate-exec-1",
        idempotency_key="evt-1::gate-1",
        event_id="evt-1",
        trace_id="trace-1",
        source_node_id="trigger-1",
        source_trigger_type="trigger:cron",
        gate_id="gate-1",
        gate_type="gate:map",
        status="completion_pending",
        started_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc),
        resolved_config_hash="hash",
        resolved_config={},
        input_payload={},
        outputs={},
        inputs_by_source={},
        input_event_ids_by_source={},
        input_occurred_at_by_source={},
        completion_pending_status="succeeded",
        completion_event_branch_port="completed",
        completion_event_for_execution_ids={"effect-1": "exec-1"},
    )
    gate_store.record_execution(gate_record)

    worker.execution_store = SqliteExecutionStore(db_path)
    worker.gate_execution_store = gate_store

    worker._update_event_delivery_state("evt-1")

    assert queue.calls == [("inflight", "evt-1")]

    worker._update_event_delivery_state("evt-missing")

    assert queue.calls[-1] == ("delivered", "evt-missing")


def test_effect_orchestrator_logs_graph_revision_changes(tmp_path: Path, caplog) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    first_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Do Work",
                    "type": "effect:agent:prompt",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    second_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Do Work",
                    "type": "effect:agent:prompt",
                    "prompt": "hello",
                },
                {
                    "id": "effect-2",
                    "display_name": "Do More",
                    "type": "effect:agent:prompt",
                    "prompt": "more",
                },
            ],
            "edges": [_edge("trigger-1", "effect-1"), _edge("trigger-1", "effect-2")],
        }
    )

    class _SequenceReloader:
        def __init__(self):
            self._calls = 0

        def get_latest(self):
            self._calls += 1
            if self._calls == 1:
                return first_config, False
            if self._calls == 2:
                return second_config, True
            return second_config, False

    worker = EffectOrchestratorWorker(
        config=config,
        worker_config=first_config,
        config_reloader=_SequenceReloader(),
        runtime=None,  # type: ignore[arg-type]
    )

    with caplog.at_level(logging.INFO):
        worker.run_once()
        worker.run_once()

    assert "Workflow graph revision changed" in caplog.text
    assert "nodes=2->3 edges=1->2" in caplog.text


def test_effect_orchestrator_marks_removed_source_node_event_as_skipped(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [],
            "effects": [],
            "gates": [],
            "queries": [],
            "edges": [],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-orphan-1",
            trigger_id="effect-removed",
            trigger_type="effect:agent:prompt",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    only = next(iter(execution_payload.values()))
    assert only["status"] == "skipped"
    assert only["error"] == "source_node_removed_from_graph:effect-removed"
    assert only["config_revision"]


def test_effect_orchestrator_executes_gate_and_effect_chain(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"ticket_id": {"type": "integer"}},
                }
            ],
            "gates": [
                {
                    "id": "gate-1",
                    "display_name": "Transform",
                    "type": "gate:map",
                    "template": {"vm_name": "vm-${{trigger-1.ticket_id}}"},
                    "outputs": {"vm_name": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Provision",
                    "type": "effect:vm:create",
                    "vm_name": "${{gate-1.vm_name}}",
                    "outputs": {"session_id": {"type": "string"}},
                },
                {
                    "id": "effect-2",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "${{effect-1.session_id}}",
                    "prompt": "Continue ${{effect-1.session_id}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "gate-1"),
                _edge("gate-1", "effect-1"),
                _edge("effect-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {"session_id": "session-123"},
            "effect-2": {"thread_id": "thread-1", "success": True},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket_id": 123},
            trace_id="trace-1",
        )
    )

    for _ in range(12):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert sorted(item["status"] for item in effect_payload.values()) == ["succeeded", "succeeded"]
    assert by_effect["effect-1"]["binding_trace"]["entries"][0]["config_path"] == "$.vm_name"
    assert by_effect["effect-1"]["binding_trace"]["entries"][0]["reference"] == "gate-1.vm_name"
    assert by_effect["effect-2"]["binding_trace"]["entries"][0]["config_path"] == "$.session_id"
    assert (
        by_effect["effect-2"]["binding_trace"]["entries"][0]["reference"] == "effect-1.session_id"
    )
    assert by_effect["effect-2"]["binding_trace"]["entries"][1]["config_path"] == "$.prompt"
    assert (
        by_effect["effect-2"]["binding_trace"]["entries"][1]["reference"] == "effect-1.session_id"
    )
    gate_payload = gate_execution_payload(worker_config)
    only_gate = next(iter(gate_payload.values()))
    assert only_gate["status"] == "succeeded"
    assert only_gate["outputs"] == {"vm_name": "vm-123"}
    assert only_gate["binding_trace"]["entries"][0]["config_path"] == "$.vm_name"
    assert only_gate["binding_trace"]["entries"][0]["reference"] == "trigger-1.ticket_id"

    snapshot_payload = workflow_config_snapshot_payload(worker_config)
    only_snapshot = next(iter(snapshot_payload.values()))
    assert only_snapshot["snapshot_type"] == "compiled_runtime_graph"
    assert [item["id"] for item in only_snapshot["triggers"]] == ["trigger-1"]
    assert [item["id"] for item in only_snapshot["gates"]] == ["gate-1"]
    assert [item["id"] for item in only_snapshot["effects"]] == ["effect-1", "effect-2"]
    assert only_snapshot["edges"][0]["source_id"] == "trigger-1"

    queue_payload = trigger_queue_payload(worker_config)
    assert any(item["trigger_id"] == "gate-1" for item in queue_payload.values())
    assert any(item["trigger_id"] == "effect-1" for item in queue_payload.values())
    assert any(item["trigger_id"] == "effect-2" for item in queue_payload.values())
    assert {item["trace_id"] for item in queue_payload.values()} == {"trace-1"}


def test_effect_orchestrator_hot_drains_fast_completion_chain(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"ticket_id": {"type": "integer"}},
                }
            ],
            "gates": [
                {
                    "id": "gate-1",
                    "display_name": "Transform",
                    "type": "gate:map",
                    "template": {"vm_name": "vm-${{trigger-1.ticket_id}}"},
                    "outputs": {"vm_name": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Provision",
                    "type": "effect:vm:create",
                    "vm_name": "${{gate-1.vm_name}}",
                    "outputs": {"session_id": {"type": "string"}},
                },
                {
                    "id": "effect-2",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "${{effect-1.session_id}}",
                    "prompt": "Continue ${{effect-1.session_id}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "gate-1"),
                _edge("gate-1", "effect-1"),
                _edge("effect-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    worker.provider_factory = _Factory(
        {
            "effect-1": {"session_id": "session-123"},
            "effect-2": {"thread_id": "thread-1", "success": True},
        }
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-hot-drain-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket_id": 123},
        )
    )

    active_results = [worker.run_once(), worker.run_once(), worker.run_once()]
    idle_result = worker.run_once()

    assert [result.next_sleep_seconds for result in active_results] == [0.0, 0.0, 0.0]
    assert idle_result.next_sleep_seconds == 1
    effect_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"


def test_effect_orchestrator_executes_query_nodes(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"session_id": {"type": "string"}},
                }
            ],
            "queries": [
                {
                    "id": "query-1",
                    "display_name": "VM run state",
                    "type": "query:vm:run-state",
                    "session_id": "${{trigger-1.session_id}}",
                    "outputs": {"state_code": {"type": "string"}},
                }
            ],
            "edges": [
                _edge("trigger-1", "query-1"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.query_provider_factory = _Factory({"query-1": {"state_code": "running"}})

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-query-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"session_id": "session-123"},
        )
    )

    for _ in range(4):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    record = next(iter(effect_payload.values()))
    assert record["effect_id"] == "query-1"
    assert record["effect_type"] == "query:vm:run-state"
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"state_code": "running"}
    assert record["binding_trace"]["entries"][0]["config_path"] == "$.session_id"


def test_effect_orchestrator_session_list_query_applies_graph_directory_metadata(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    now = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)
    sessions = {
        "owner": SessionManifest(
            session_id="owner",
            vm_id="vm-owner",
            directory_path="root/team-a",
            model="gpt-5.4",
            allowed_tools=[],
            created_at=now,
            expires_at=now + timedelta(hours=1),
            metadata={"purpose": "spec"},
        ),
        "reviewer": SessionManifest(
            session_id="reviewer",
            vm_id="vm-reviewer",
            directory_path="root/team-a/reviews",
            model="gpt-5.4",
            allowed_tools=[],
            created_at=now + timedelta(minutes=1),
            expires_at=now + timedelta(hours=1),
            metadata={"purpose": "spec"},
        ),
        "other": SessionManifest(
            session_id="other",
            vm_id="vm-other",
            directory_path="root/team-b",
            model="gpt-5.4",
            allowed_tools=[],
            created_at=now + timedelta(minutes=2),
            expires_at=now + timedelta(hours=1),
            metadata={"purpose": "spec"},
        ),
    }
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "queries": [
                {
                    "id": "query-sessions",
                    "display_name": "Sessions",
                    "type": "query:session:list",
                    "metadata_equals": {"purpose": "spec"},
                    "outputs": {
                        "sessions": {"type": "array"},
                        "count": {"type": "integer"},
                    },
                }
            ],
            "edges": [
                _edge("trigger-1", "query-sessions"),
            ],
            "workflow_graphs": [
                {
                    "id": "graph-1",
                    "name": "Graph 1",
                    "directory_path": "root/team-a",
                    "node_ids": ["trigger-1", "query-sessions"],
                }
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    session_lookup = SimpleNamespace(read_all_sessions=lambda: sessions)
    worker.query_provider_factory = QueryProviderFactory(
        _ProviderConfigResolver(config),
        provider_runtime=SimpleNamespace(session_lookup=session_lookup),
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-query-sessions",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=now,
            payload={},
        )
    )

    for _ in range(4):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    record = next(iter(effect_payload.values()))

    assert record["effect_id"] == "query-sessions"
    assert record["effect_type"] == "query:session:list"
    assert record["status"] == "succeeded"
    assert record["outputs"]["count"] == 2
    assert [item["session_id"] for item in record["outputs"]["sessions"]] == [
        "owner",
        "reviewer",
    ]


def test_effect_orchestrator_executes_issue_get_query_nodes(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"issue_id": {"type": "string"}},
                }
            ],
            "queries": [
                {
                    "id": "query-1",
                    "display_name": "Issue get",
                    "type": "query:issue:get",
                    "issue_id": "${{trigger-1.issue_id}}",
                }
            ],
            "edges": [
                _edge("trigger-1", "query-1"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.query_provider_factory = _Factory(
        {"query-1": {"issue_id": "root:issue-42", "metadata": {"steps": ["one", "two"]}}}
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-query-issue-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"issue_id": "root:issue-42"},
        )
    )

    for _ in range(4):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    record = next(iter(effect_payload.values()))
    assert record["effect_id"] == "query-1"
    assert record["effect_type"] == "query:issue:get"
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"issue_id": "root:issue-42", "metadata": {"steps": ["one", "two"]}}
    assert record["binding_trace"]["entries"][0]["config_path"] == "$.issue_id"


def test_effect_orchestrator_persists_binding_error_on_failed_resolution(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "${{trigger-1.missing}}",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({})

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-binding-error-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-binding-error-1",
        )
    )

    for _ in range(2):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    record = next(iter(effect_payload.values()))
    assert record["status"] == "failed"
    assert "not declared" in record["binding_error"]
    assert record["binding_trace"] is None


def test_effect_orchestrator_persists_workflow_config_snapshot_in_sqlite_backend(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-1": {"success": True}})

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-sqlite-snapshot-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-sqlite-snapshot-1",
        )
    )

    for _ in range(4):
        worker.run_once()

    snapshot_repo = SqliteWorkflowConfigSnapshotRepository(runtime_dir / "orchestration.db")
    snapshots = snapshot_repo.list_snapshots()

    assert len(snapshots) == 1
    assert snapshots[0]["snapshot_type"] == "compiled_runtime_graph"
    assert [item["id"] for item in snapshots[0]["effects"]] == ["effect-1"]


def test_effect_orchestrator_persists_provider_reported_failure_message(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"ticket_id": {"type": "integer"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Provision",
                    "type": "effect:vm:create",
                    "vm_name": "vm-${{trigger-1.ticket_id}}",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "session_id": "session-123",
                "vm_name": "vm-123",
                "success": False,
                "message": "sudo: a password is required",
            }
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket_id": 123},
            trace_id="trace-1",
        )
    )

    for _ in range(4):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    record = next(iter(effect_payload.values()))
    assert record["status"] == "succeeded"
    assert record["error"] == "sudo: a password is required"
    assert record["outputs"]["message"] == "sudo: a password is required"


def test_effect_orchestrator_executes_issue_update_state_effect(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"issue_id": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-issue-state",
                    "display_name": "Update issue state",
                    "type": "effect:issue:update-state",
                    "issue_id": "${{trigger-1.issue_id}}",
                    "state": "In Progress",
                }
            ],
            "edges": [_edge("trigger-1", "effect-issue-state")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-issue-state": {
                "issue_number": "14084",
                "source_type": "local",
                "updated": True,
                "state": "In Progress",
                "success": True,
            }
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-issue-state-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"issue_id": "14084"},
        )
    )

    for _ in range(4):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    record = next(iter(effect_payload.values()))
    assert record["effect_type"] == "effect:issue:update-state"
    assert record["status"] == "succeeded"


def test_for_gate_iterates_body_then_routes_to_done(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"steps": {"type": "array"}},
                }
            ],
            "gates": [
                {
                    "id": "for-1",
                    "display_name": "Iterate steps",
                    "type": "gate:for",
                    "items": "${{trigger-1.steps}}",
                }
            ],
            "effects": [
                {
                    "id": "effect-body",
                    "display_name": "Body",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "work ${{for-1.current_index}}:${{for-1.current_element}}",
                },
                {
                    "id": "effect-done",
                    "display_name": "Done",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "done ${{for-1.item_count}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "for-1"),
                {"source_id": "for-1", "source_port": "body", "target_id": "effect-body"},
                {"source_id": "for-1", "source_port": "done", "target_id": "effect-done"},
                {"source_id": "effect-body", "target_id": "for-1", "allow_cycle": True},
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-body": {"success": True, "message": "body"},
            "effect-done": {"success": True, "message": "done"},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-for-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"steps": ["alpha", "beta", "gamma"]},
            trace_id="trace-for-1",
        )
    )

    for _ in range(20):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    body_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-body"]
    done_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-done"]

    assert len(body_records) == 3
    assert len(done_records) == 1
    assert [
        item["resolved_config"]["command"]
        for item in sorted(body_records, key=lambda entry: entry["resolved_config"]["command"])
    ] == [
        "work 0:alpha",
        "work 1:beta",
        "work 2:gamma",
    ]
    assert done_records[0]["resolved_config"]["command"] == "done 3"

    gate_payload = gate_execution_payload(worker_config)
    for_records = [item for item in gate_payload.values() if item["gate_id"] == "for-1"]
    assert len(for_records) == 4
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is False) == 3
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is True) == 1


def test_for_gate_resumes_legacy_trace_scoped_state_key(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"steps": {"type": "array"}},
                }
            ],
            "gates": [
                {
                    "id": "for-1",
                    "display_name": "Iterate steps",
                    "type": "gate:for",
                    "items": "${{trigger-1.steps}}",
                }
            ],
            "effects": [
                {
                    "id": "effect-body",
                    "display_name": "Body",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "work ${{for-1.current_index}}:${{for-1.current_element}}",
                },
                {
                    "id": "effect-done",
                    "display_name": "Done",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "done ${{for-1.item_count}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "for-1"),
                {"source_id": "for-1", "source_port": "body", "target_id": "effect-body"},
                {"source_id": "for-1", "source_port": "done", "target_id": "effect-done"},
                {"source_id": "effect-body", "target_id": "for-1", "allow_cycle": True},
            ],
        }
    )
    state_path = runtime_dir / "providers" / "gates" / "for" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_key = "for-1:trace-for-legacy:event-for-legacy"
    state_path.write_text(
        json.dumps(
            {
                legacy_key: {
                    "key": legacy_key,
                    "gate_id": "for-1",
                    "trace_id": "trace-for-legacy",
                    "for_execution_id": "event-for-legacy",
                    "next_index": 2,
                    "item_count": 3,
                    "updated_at": datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc).isoformat(),
                }
            }
        ),
        encoding="utf-8",
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-body": {"success": True, "message": "body"},
            "effect-done": {"success": True, "message": "done"},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-for-legacy",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"steps": ["alpha", "beta", "gamma"]},
            trace_id="trace-for-legacy",
        )
    )

    for _ in range(12):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    body_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-body"]
    done_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-done"]
    assert [item["resolved_config"]["command"] for item in body_records] == ["work 2:gamma"]
    assert [item["resolved_config"]["command"] for item in done_records] == ["done 3"]
    assert json.loads(state_path.read_text(encoding="utf-8")) == {}


def test_for_gate_without_body_edge_routes_immediately_to_done(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"steps": {"type": "array"}},
                }
            ],
            "gates": [
                {
                    "id": "for-1",
                    "display_name": "Iterate steps",
                    "type": "gate:for",
                    "items": "${{trigger-1.steps}}",
                }
            ],
            "effects": [
                {
                    "id": "effect-done",
                    "display_name": "Done",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "done ${{for-1.item_count}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "for-1"),
                {"source_id": "for-1", "source_port": "done", "target_id": "effect-done"},
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-done": {"success": True, "message": "done"}})

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-for-done-only-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"steps": ["alpha", "beta", "gamma"]},
            trace_id="trace-for-done-only-1",
        )
    )

    for _ in range(8):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    done_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-done"]
    assert len(done_records) == 1
    assert done_records[0]["resolved_config"]["command"] == "done 3"

    gate_payload = gate_execution_payload(worker_config)
    for_records = [item for item in gate_payload.values() if item["gate_id"] == "for-1"]
    assert len(for_records) == 1
    assert for_records[0]["outputs"] == {
        "current_index": 3,
        "current_element": None,
        "item_count": 3,
        "is_done": True,
    }


def test_for_gate_without_done_edge_terminates_after_body_iterations(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"steps": {"type": "array"}},
                }
            ],
            "gates": [
                {
                    "id": "for-1",
                    "display_name": "Iterate steps",
                    "type": "gate:for",
                    "items": "${{trigger-1.steps}}",
                }
            ],
            "effects": [
                {
                    "id": "effect-body",
                    "display_name": "Body",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "work ${{for-1.current_index}}:${{for-1.current_element}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "for-1"),
                {"source_id": "for-1", "source_port": "body", "target_id": "effect-body"},
                {"source_id": "effect-body", "target_id": "for-1", "allow_cycle": True},
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-body": {"success": True, "message": "body"}})

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-for-body-only-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"steps": ["alpha", "beta"]},
            trace_id="trace-for-body-only-1",
        )
    )

    for _ in range(12):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    body_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-body"]
    assert len(body_records) == 2
    assert sorted(item["resolved_config"]["command"] for item in body_records) == [
        "work 0:alpha",
        "work 1:beta",
    ]

    gate_payload = gate_execution_payload(worker_config)
    for_records = [item for item in gate_payload.values() if item["gate_id"] == "for-1"]
    assert len(for_records) == 3
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is False) == 2
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is True) == 1
    assert not any(
        item.get("published_event_id")
        for item in for_records
        if item["outputs"].get("is_done") is True
    )


def test_for_gate_without_outgoing_edges_terminates_immediately(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"steps": {"type": "array"}},
                }
            ],
            "gates": [
                {
                    "id": "for-1",
                    "display_name": "Iterate steps",
                    "type": "gate:for",
                    "items": "${{trigger-1.steps}}",
                }
            ],
            "edges": [_edge("trigger-1", "for-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({})

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-for-no-outgoing-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"steps": ["alpha", "beta"]},
            trace_id="trace-for-no-outgoing-1",
        )
    )

    for _ in range(5):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    assert effect_payload == {}

    gate_payload = gate_execution_payload(worker_config)
    for_records = [item for item in gate_payload.values() if item["gate_id"] == "for-1"]
    assert len(for_records) == 1
    assert for_records[0]["outputs"] == {
        "current_index": 2,
        "current_element": None,
        "item_count": 2,
        "is_done": True,
    }
    assert for_records[0].get("published_event_id") is None


def test_while_gate_false_condition_routes_to_done(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "while-1",
                    "display_name": "Retry while failing",
                    "type": "gate:while",
                    "when": {"left": 0, "greater_than": 0},
                    "max_iterations": 3,
                }
            ],
            "effects": [
                {
                    "id": "effect-done",
                    "display_name": "Done",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "done ${{while-1.iteration_count}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "while-1"),
                {"source_id": "while-1", "source_port": "done", "target_id": "effect-done"},
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-done": {"success": True, "message": "done"}})

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-while-done-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-while-done-1",
        )
    )

    for _ in range(8):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    done_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-done"]
    assert len(done_records) == 1
    assert done_records[0]["resolved_config"]["command"] == "done 0"

    gate_payload = gate_execution_payload(worker_config)
    while_records = [item for item in gate_payload.values() if item["gate_id"] == "while-1"]
    assert len(while_records) == 1
    assert while_records[0]["outputs"] == {
        "iteration_index": 0,
        "iteration_count": 0,
        "remaining_iterations": 3,
        "is_done": True,
        "is_exhausted": False,
    }


def test_while_gate_exhausts_after_max_iterations(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "while-1",
                    "display_name": "Retry while failing",
                    "type": "gate:while",
                    "when": {"left": 1, "equals": 1},
                    "max_iterations": 2,
                }
            ],
            "effects": [
                {
                    "id": "effect-body",
                    "display_name": "Body",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "body ${{while-1.iteration_index}}/${{while-1.remaining_iterations}}",
                },
                {
                    "id": "effect-exhausted",
                    "display_name": "Exhausted",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "exhausted ${{while-1.iteration_count}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "while-1"),
                {"source_id": "while-1", "source_port": "body", "target_id": "effect-body"},
                {
                    "source_id": "while-1",
                    "source_port": "exhausted",
                    "target_id": "effect-exhausted",
                },
                {"source_id": "effect-body", "target_id": "while-1", "allow_cycle": True},
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-body": {"success": True, "message": "body"},
            "effect-exhausted": {"success": True, "message": "exhausted"},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-while-exhaust-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-while-exhaust-1",
        )
    )

    for _ in range(16):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    body_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-body"]
    exhausted_records = [
        item for item in effect_payload.values() if item["effect_id"] == "effect-exhausted"
    ]
    assert len(body_records) == 2
    assert sorted(item["resolved_config"]["command"] for item in body_records) == [
        "body 0/1",
        "body 1/0",
    ]
    assert len(exhausted_records) == 1
    assert exhausted_records[0]["resolved_config"]["command"] == "exhausted 2"

    gate_payload = gate_execution_payload(worker_config)
    while_records = [item for item in gate_payload.values() if item["gate_id"] == "while-1"]
    assert len(while_records) == 3
    assert sum(1 for item in while_records if item["outputs"].get("is_done") is False) == 2
    exhausted = [item for item in while_records if item["outputs"].get("is_exhausted") is True]
    assert len(exhausted) == 1
    assert exhausted[0]["outputs"] == {
        "iteration_index": 2,
        "iteration_count": 2,
        "remaining_iterations": 0,
        "is_done": True,
        "is_exhausted": True,
    }


def test_while_gate_resumes_legacy_trace_scoped_state_key(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "while-1",
                    "display_name": "Retry while failing",
                    "type": "gate:while",
                    "when": {"left": 1, "equals": 1},
                    "max_iterations": 3,
                }
            ],
            "effects": [
                {
                    "id": "effect-body",
                    "display_name": "Body",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "body ${{while-1.iteration_index}}/${{while-1.remaining_iterations}}",
                },
                {
                    "id": "effect-exhausted",
                    "display_name": "Exhausted",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "exhausted ${{while-1.iteration_count}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "while-1"),
                {"source_id": "while-1", "source_port": "body", "target_id": "effect-body"},
                {
                    "source_id": "while-1",
                    "source_port": "exhausted",
                    "target_id": "effect-exhausted",
                },
                {"source_id": "effect-body", "target_id": "while-1", "allow_cycle": True},
            ],
        }
    )
    state_path = runtime_dir / "providers" / "gates" / "while" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_key = "while-1:trace-while-legacy:event-while-legacy"
    state_path.write_text(
        json.dumps(
            {
                legacy_key: {
                    "key": legacy_key,
                    "gate_id": "while-1",
                    "trace_id": "trace-while-legacy",
                    "while_execution_id": "event-while-legacy",
                    "next_iteration_index": 2,
                    "max_iterations": 3,
                    "updated_at": datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc).isoformat(),
                }
            }
        ),
        encoding="utf-8",
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-body": {"success": True, "message": "body"},
            "effect-exhausted": {"success": True, "message": "exhausted"},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-while-legacy",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-while-legacy",
        )
    )

    for _ in range(12):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    body_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-body"]
    exhausted_records = [
        item for item in effect_payload.values() if item["effect_id"] == "effect-exhausted"
    ]
    assert [item["resolved_config"]["command"] for item in body_records] == ["body 2/0"]
    assert [item["resolved_config"]["command"] for item in exhausted_records] == ["exhausted 3"]
    assert json.loads(state_path.read_text(encoding="utf-8")) == {}


def test_for_gate_restarts_iteration_for_each_fresh_invocation_in_same_trace(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "merge-1",
                    "display_name": "Merge",
                    "type": "gate:merge",
                },
                {
                    "id": "for-1",
                    "display_name": "Iterate steps",
                    "type": "gate:for",
                    "items": "${{merge-1.steps}}",
                },
            ],
            "effects": [
                {
                    "id": "effect-a",
                    "display_name": "Branch A",
                    "type": "effect:agent:prompt",
                    "prompt": "A",
                    "outputs": {"steps": {"type": "array"}},
                },
                {
                    "id": "effect-b",
                    "display_name": "Branch B",
                    "type": "effect:agent:prompt",
                    "prompt": "B",
                    "outputs": {"steps": {"type": "array"}},
                },
                {
                    "id": "effect-body",
                    "display_name": "Body",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "work ${{for-1.current_index}}:${{for-1.current_element}}",
                },
                {
                    "id": "effect-done",
                    "display_name": "Done",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "done ${{for-1.item_count}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-a"),
                _edge("trigger-1", "effect-b"),
                _edge("effect-a", "merge-1"),
                _edge("effect-b", "merge-1"),
                _edge("merge-1", "for-1"),
                {"source_id": "for-1", "source_port": "body", "target_id": "effect-body"},
                {"source_id": "for-1", "source_port": "done", "target_id": "effect-done"},
                {"source_id": "effect-body", "target_id": "for-1", "allow_cycle": True},
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-a": {"steps": ["alpha", "beta"]},
            "effect-b": {"steps": ["alpha", "beta"]},
            "effect-body": {"success": True, "message": "body"},
            "effect-done": {"success": True, "message": "done"},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-for-merge-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-for-merge-1",
        )
    )

    for _ in range(24):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    body_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-body"]
    done_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-done"]

    assert len(body_records) == 4
    assert len(done_records) == 2
    assert sorted(item["resolved_config"]["command"] for item in body_records) == [
        "work 0:alpha",
        "work 0:alpha",
        "work 1:beta",
        "work 1:beta",
    ]
    assert sorted(item["resolved_config"]["command"] for item in done_records) == [
        "done 2",
        "done 2",
    ]

    gate_payload = gate_execution_payload(worker_config)
    for_records = [item for item in gate_payload.values() if item["gate_id"] == "for-1"]
    assert len(for_records) == 6
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is False) == 4
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is True) == 2


def test_effect_orchestrator_rejects_reserved_runtime_output_name(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"ticket_id": {"type": "integer"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Provision",
                    "type": "effect:vm:create",
                    "vm_name": "vm-${{trigger-1.ticket_id}}",
                }
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-1": {"__node_outputs": "forbidden"}})

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket_id": 123},
            trace_id="trace-1",
        )
    )

    for _ in range(4):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    record = next(iter(effect_payload.values()))
    assert record["status"] == "failed"
    assert record["outputs"] == {"__node_outputs": "forbidden"}
    assert "reserved output name" in record["error"]

    queue_payload = trigger_queue_payload(worker_config)
    assert set(queue_payload.keys()) == {"event-1"}
    assert queue_payload["event-1"]["delivery_state"] == "delivered"


def test_effect_orchestrator_preserves_upstream_payload_across_multi_hop_chain(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"ticket_id": {"type": "integer"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Build vm name",
                    "type": "effect:agent:prompt",
                    "prompt": "ticket ${{trigger-1.ticket_id}}",
                    "outputs": {"vm_name": {"type": "string"}},
                },
                {
                    "id": "effect-2",
                    "display_name": "Build session",
                    "type": "effect:agent:prompt",
                    "prompt": "vm ${{effect-1.vm_name}}",
                    "outputs": {"session_id": {"type": "string"}},
                },
                {
                    "id": "effect-3",
                    "display_name": "Cross-hop binding",
                    "type": "effect:agent:prompt",
                    "prompt": "ticket ${{trigger-1.ticket_id}} session ${{effect-2.session_id}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "effect-2"),
                _edge("effect-2", "effect-3"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {"vm_name": "vm-101"},
            "effect-2": {"session_id": "session-101"},
            "effect-3": {"ok": True},
        }
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-chain-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket_id": 101},
        )
    )

    for _ in range(8):
        worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in execution_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-3"]["status"] == "succeeded"
    assert by_effect["effect-3"]["resolved_config"]["prompt"] == "ticket 101 session session-101"


def test_effect_orchestrator_supports_disambiguated_references_for_same_key_name(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"ticket_id": {"type": "integer"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Emit same key name",
                    "type": "effect:agent:prompt",
                    "prompt": "ticket ${{trigger-1.ticket_id}}",
                    "outputs": {"ticket_id": {"type": "integer"}},
                },
                {
                    "id": "effect-2",
                    "display_name": "Uses both values",
                    "type": "effect:agent:prompt",
                    "prompt": "original ${{trigger-1.ticket_id}} updated ${{effect-1.ticket_id}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {"ticket_id": 999},
            "effect-2": {"ok": True},
        }
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-disambiguated-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket_id": 101},
        )
    )

    for _ in range(6):
        worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in execution_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-2"]["resolved_config"]["prompt"] == "original 101 updated 999"


def test_effect_orchestrator_depth_protection_skips_cyclic_graph(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    with pytest.raises(ValueError, match="Non-cyclic edges cannot form a cycle"):
        canonical_worker_config(
            {
                "runtime_dir": runtime_dir,
                "poll_interval_seconds": 1,
                "jitter_seconds": 0,
                "effects": [
                    {
                        "id": "effect-a",
                        "display_name": "A",
                        "type": "effect:agent:prompt",
                        "prompt": "A",
                    },
                    {
                        "id": "effect-b",
                        "display_name": "B",
                        "type": "effect:agent:prompt",
                        "prompt": "B",
                    },
                ],
                "edges": [
                    _edge("effect-b", "effect-a"),
                    _edge("effect-a", "effect-b"),
                ],
            }
        )


def test_effect_orchestrator_dispatches_without_waiting_for_long_effect(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Long",
                    "type": "effect:agent:prompt",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    block_event = Event()
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {"effect-1": {"done": True}}, wait_on_by_effect_id={"effect-1": block_event}
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-long-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    start = time.monotonic()
    first_result = worker.run_once()
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
    assert first_result.next_sleep_seconds == 0.0
    execution_payload = effect_execution_payload(worker_config)
    only = next(iter(execution_payload.values()))
    assert only["status"] == "running"
    queue_payload = trigger_queue_payload(worker_config)
    assert queue_payload["event-long-1"]["delivery_state"] == "inflight"

    idle_result = worker.run_once()
    assert idle_result.events_processed == 0
    assert idle_result.completion_events_published == 0
    assert idle_result.next_sleep_seconds == 1

    block_event.set()
    only = None
    for _ in range(5):
        worker.run_once()
        execution_payload = effect_execution_payload(worker_config)
        only = next(iter(execution_payload.values()))
        if only["status"] == "succeeded":
            break
        time.sleep(0.01)

    assert only is not None
    assert only["status"] == "succeeded"
    queue_payload = trigger_queue_payload(worker_config)
    assert queue_payload["event-long-1"]["delivery_state"] == "delivered"


def test_effect_orchestrator_retries_running_effect_after_restart(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Long",
                    "type": "effect:agent:prompt",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    block_event = Event()
    first_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    first_worker.provider_factory = _Factory(
        {"effect-1": {"done": True}}, wait_on_by_effect_id={"effect-1": block_event}
    )
    first_worker.event_queue.publish(
        TriggerEvent(
            event_id="event-restart-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    first_worker.run_once()
    queue_payload = trigger_queue_payload(worker_config)
    assert queue_payload["event-restart-1"]["delivery_state"] == "inflight"

    restarted_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    restarted_worker.provider_factory = _Factory({"effect-1": {"done": True}})

    for _ in range(5):
        restarted_worker.run_once()
        execution_payload = effect_execution_payload(worker_config)
        only = next(iter(execution_payload.values()))
        if only["status"] == "succeeded":
            break
        time.sleep(0.01)

    block_event.set()
    execution_payload = effect_execution_payload(worker_config)
    only = next(iter(execution_payload.values()))
    assert only["status"] == "succeeded"
    queue_payload = trigger_queue_payload(worker_config)
    assert queue_payload["event-restart-1"]["delivery_state"] == "delivered"
    assert any(item["trigger_id"] == "effect-1" for item in queue_payload.values())


def test_effect_orchestrator_marks_provider_failure_failed(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Create VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-1",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    worker.provider_factory = _FailingFactory()
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-provider-failure-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    record = next(iter(execution_payload.values()))
    assert record["status"] == "failed"
    assert record["error"] == "provider boom"
    assert record["outputs"] == {}

    queue_payload = trigger_queue_payload(worker_config)
    assert queue_payload["event-provider-failure-1"]["delivery_state"] == "delivered"


def test_effect_orchestrator_records_provider_failure_outputs(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Create VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-1",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    worker.provider_factory = _FailingWithOutputsFactory()
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-provider-failure-outputs-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    record = next(iter(execution_payload.values()))
    assert record["status"] == "failed"
    assert record["error"] == "checked provider boom"
    assert record["outputs"] == {
        "success": False,
        "status_code": 502,
        "body_text": "bad gateway",
    }


def test_effect_orchestrator_routes_generic_provider_failure_to_error_branch(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Send",
                    "type": "effect:communication:send-message",
                    "communication_target_id": "ops-chat",
                    "message": "hello",
                },
                {
                    "id": "effect-error",
                    "display_name": "Record failure",
                    "type": "effect:session:update-metadata",
                    "target": "session-1",
                    "metadata": {"handled": True},
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "effect-error", source_port="error"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    worker.provider_factory = _GenericErrorRoutingFactory()
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-provider-error-route-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    for _ in range(3):
        worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in execution_payload.values()}
    failed_send = by_effect["effect-1"]
    recorder = by_effect["effect-error"]

    assert failed_send["status"] == "error_routed"
    assert failed_send["completion_event_branch_port"] == "error"
    assert failed_send["error"] == "provider boom"
    assert failed_send["outputs"] == {
        "success": False,
        "error_message": "provider boom",
        "error_class": "RuntimeError",
    }
    assert recorder["status"] == "succeeded"


def test_effect_orchestrator_marks_completion_publish_failure_pending(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Create VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-1",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    counting_factory = _CountingFactory({"effect-1": {"session_id": "session-123"}})
    worker.provider_factory = counting_factory
    original_publish = worker.event_queue.publish

    def publish(event: TriggerEvent, *, idempotency_key: str | None = None):
        if event.trigger_id == "effect-1":
            raise RuntimeError("completion publish boom")
        return original_publish(event, idempotency_key=idempotency_key)

    worker.event_queue.publish = publish  # type: ignore[assignment]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-completion-publish-failure-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    record = next(iter(execution_payload.values()))
    assert record["status"] == "completion_pending"
    assert record["completion_pending_status"] == "succeeded"
    assert record["outputs"] == {"session_id": "session-123"}
    assert record["published_event_id"] is None
    assert record["completion_event_enqueued_at"] is None
    assert record["error"] == "completion publish boom"
    assert len(counting_factory.execute_calls) == 1

    queue_payload = trigger_queue_payload(worker_config)
    assert queue_payload["event-completion-publish-failure-1"]["delivery_state"] == "inflight"


def test_effect_orchestrator_retries_pending_completion_publish_after_restart(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Create VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-1",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    counting_factory = _CountingFactory({"effect-1": {"session_id": "session-123"}})

    first_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    first_worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    first_worker.provider_factory = counting_factory
    original_publish = first_worker.event_queue.publish

    def publish(event: TriggerEvent, *, idempotency_key: str | None = None):
        if event.trigger_id == "effect-1":
            raise RuntimeError("completion publish boom")
        return original_publish(event, idempotency_key=idempotency_key)

    first_worker.event_queue.publish = publish  # type: ignore[assignment]
    first_worker.event_queue.publish(
        TriggerEvent(
            event_id="event-completion-retry-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    first_worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    record = next(iter(execution_payload.values()))
    assert record["status"] == "completion_pending"
    assert record["outputs"] == {"session_id": "session-123"}
    assert record["published_event_id"] is None
    assert record["completion_event_enqueued_at"] is None
    assert len(counting_factory.execute_calls) == 1

    queue_payload = trigger_queue_payload(worker_config)
    assert queue_payload["event-completion-retry-1"]["delivery_state"] == "inflight"
    assert all(item["trigger_id"] != "effect-1" for item in queue_payload.values())

    restarted_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    restarted_worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    restarted_worker.provider_factory = counting_factory

    restarted_worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    record = next(iter(execution_payload.values()))
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"session_id": "session-123"}
    assert record["published_event_id"] is not None
    assert record["completion_event_enqueued_at"] is not None
    assert len(counting_factory.execute_calls) == 1

    queue_payload = trigger_queue_payload(worker_config)
    assert queue_payload["event-completion-retry-1"]["delivery_state"] == "delivered"
    assert sum(1 for item in queue_payload.values() if item["trigger_id"] == "effect-1") == 1


def test_effect_orchestrator_retries_pending_started_branch_publish_after_restart(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "vm_name": "vm-1",
                    "prompt": "continue",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    counting_factory = _CountingFactory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1",
                "thread_id": "thread-1",
                "success": True,
            }
        }
    )

    first_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    first_worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    first_worker.provider_factory = counting_factory
    original_publish = first_worker.event_queue.publish

    def publish(event: TriggerEvent, *, idempotency_key: str | None = None):
        if event.trigger_id == "effect-1":
            raise RuntimeError("started branch publish boom")
        return original_publish(event, idempotency_key=idempotency_key)

    first_worker.event_queue.publish = publish  # type: ignore[assignment]
    first_worker.event_queue.publish(
        TriggerEvent(
            event_id="event-started-branch-retry-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    first_worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    record = next(iter(execution_payload.values()))
    assert record["status"] == "completion_pending"
    assert record["completion_pending_status"] == "waiting"
    assert record["completion_event_branch_port"] == "started"
    assert record["outputs"]["dispatch_id"] == "dispatch-effect-1"
    assert record["published_event_id"] is None
    assert record["completion_event_enqueued_at"] is None
    assert record["error"] == "started branch publish boom"
    assert len(counting_factory.execute_calls) == 1

    queue_payload = trigger_queue_payload(worker_config)
    assert queue_payload["event-started-branch-retry-1"]["delivery_state"] == "inflight"
    assert all(item["trigger_id"] != "effect-1" for item in queue_payload.values())

    restarted_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    restarted_worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    restarted_worker.provider_factory = counting_factory

    restarted_worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    record = next(iter(execution_payload.values()))
    assert record["status"] == "waiting"
    assert record["published_event_id"] is not None
    assert record["completion_event_enqueued_at"] is not None
    assert len(counting_factory.execute_calls) == 1

    queue_payload = trigger_queue_payload(worker_config)
    assert queue_payload["event-started-branch-retry-1"]["delivery_state"] == "delivered"
    started_events = [
        item
        for item in queue_payload.values()
        if item["trigger_id"] == "effect-1" and item["idempotency_key"].endswith(":started")
    ]
    assert len(started_events) == 1
    assert started_events[0]["payload"]["__branch_port"] == "started"


def test_effect_orchestrator_retries_partially_published_completion_publish_once(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Create VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-1",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    counting_factory = _CountingFactory({"effect-1": {"session_id": "session-123"}})

    first_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    first_worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    first_worker.provider_factory = counting_factory
    original_publish = first_worker.event_queue.publish

    def publish(event: TriggerEvent, *, idempotency_key: str | None = None):
        record = original_publish(event, idempotency_key=idempotency_key)
        if event.trigger_id == "effect-1":
            raise RuntimeError("completion publish boom after write")
        return record

    first_worker.event_queue.publish = publish  # type: ignore[assignment]
    first_worker.event_queue.publish(
        TriggerEvent(
            event_id="event-completion-retry-2",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    first_worker.run_once()

    queue_payload = trigger_queue_payload(worker_config)
    completion_records = [
        item
        for item in queue_payload.values()
        if item["idempotency_key"] == "event-completion-retry-2::effect-1"
    ]
    assert len(completion_records) == 1
    assert completion_records[0]["delivery_state"] == "pending"

    restarted_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    restarted_worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    restarted_worker.provider_factory = counting_factory

    restarted_worker.run_once()

    queue_payload = trigger_queue_payload(worker_config)
    completion_records = [
        item
        for item in queue_payload.values()
        if item["idempotency_key"] == "event-completion-retry-2::effect-1"
    ]
    assert len(completion_records) == 1
    assert completion_records[0]["delivery_state"] == "delivered"
    assert sum(1 for item in queue_payload.values() if item["trigger_id"] == "effect-1") == 1
    assert len(counting_factory.execute_calls) == 1


def test_effect_orchestrator_retries_partially_published_gate_completion_publish_once(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "max_events_per_cycle": 1,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"value": {"type": "string"}},
                }
            ],
            "gates": [
                {
                    "id": "gate-map",
                    "display_name": "Map",
                    "type": "gate:map",
                    "template": {"value": "${{trigger-1.value}}"},
                    "outputs": {"value": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "prompt": "value ${{gate-map.value}}",
                }
            ],
            "edges": [
                _edge("trigger-1", "gate-map"),
                _edge("gate-map", "effect-1"),
            ],
        }
    )
    counting_factory = _CountingFactory({"effect-1": {"session_id": "session-123"}})

    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    worker.provider_factory = counting_factory

    gate_execute_calls = 0

    def _wrap_gate_execute(target_worker: EffectOrchestratorWorker) -> None:
        nonlocal gate_execute_calls
        provider = target_worker._get_gate_provider("gate:map")
        assert provider is not None
        original_execute = provider.execute

        def execute(**kwargs):
            nonlocal gate_execute_calls
            gate_execute_calls += 1
            return original_execute(**kwargs)

        provider.execute = execute  # type: ignore[method-assign]

    _wrap_gate_execute(worker)

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-gate-retry-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"value": "hello"},
        )
    )

    original_publish = worker.event_queue.publish
    gate_publish_should_fail = True

    def publish(event: TriggerEvent, *, idempotency_key: str | None = None):
        nonlocal gate_publish_should_fail
        record = original_publish(event, idempotency_key=idempotency_key)
        if gate_publish_should_fail and event.trigger_id == "gate-map":
            gate_publish_should_fail = False
            raise RuntimeError("gate completion publish boom after write")
        return record

    worker.event_queue.publish = publish  # type: ignore[assignment]

    worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    gate_records = [item for item in gate_payload.values() if item["gate_id"] == "gate-map"]
    assert len(gate_records) == 1
    gate_record = gate_records[0]
    assert gate_record["status"] == "completion_pending"
    assert gate_record["completion_pending_status"] == "succeeded"
    assert gate_record["outputs"] == {"value": "hello"}
    assert gate_record["published_event_id"] is None
    assert gate_record["completion_event_enqueued_at"] is None
    assert gate_record["error"] == "gate completion publish boom after write"
    assert gate_execute_calls == 1

    queue_payload = trigger_queue_payload(worker_config)
    completion_records = [
        item for item in queue_payload.values() if item["trigger_id"] == "gate-map"
    ]
    assert len(completion_records) == 1
    assert completion_records[0]["delivery_state"] == "pending"
    assert queue_payload["event-gate-retry-1"]["delivery_state"] == "inflight"
    assert len(counting_factory.execute_calls) == 0

    worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    gate_records = [item for item in gate_payload.values() if item["gate_id"] == "gate-map"]
    assert len(gate_records) == 1
    gate_record = gate_records[0]
    assert gate_record["status"] == "succeeded"
    assert gate_record["published_event_id"] is not None
    assert gate_record["completion_event_enqueued_at"] is not None
    assert gate_record["outputs"] == {"value": "hello"}
    assert gate_execute_calls == 1

    effect_payload = effect_execution_payload(worker_config)
    effect_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-1"]
    assert len(effect_records) == 1
    assert effect_records[0]["status"] == "succeeded"
    assert len(counting_factory.execute_calls) == 1

    queue_payload = trigger_queue_payload(worker_config)
    completion_records = [
        item for item in queue_payload.values() if item["trigger_id"] == "gate-map"
    ]
    assert len(completion_records) == 1
    assert completion_records[0]["delivery_state"] == "delivered"
    assert queue_payload["event-gate-retry-1"]["delivery_state"] == "delivered"


def test_effect_orchestrator_records_binding_resolution_failure_and_advances_queue(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"ticket_id": {"type": "integer"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-bad",
                    "display_name": "Bad binding",
                    "type": "effect:agent:prompt",
                    "prompt": "missing ${{trigger-1.session_id}}",
                },
                {
                    "id": "effect-good",
                    "display_name": "Good binding",
                    "type": "effect:agent:prompt",
                    "prompt": "ticket ${{trigger-1.ticket_id}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-bad"),
                _edge("trigger-1", "effect-good"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-good": {"ok": True}})

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-bind-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket_id": 5},
        )
    )

    for _ in range(5):
        worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in execution_payload.values()}
    assert by_effect["effect-bad"]["status"] == "failed"
    assert by_effect["effect-good"]["status"] == "succeeded"
    assert "not declared by upstream node" in by_effect["effect-bad"]["error"]


def test_filter_gate_forwards_matching_payload_with_recursive_subset_match(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {
                        "ticket": {"type": "object"},
                        "state": {"type": "string"},
                    },
                }
            ],
            "gates": [
                {
                    "id": "gate-filter",
                    "display_name": "High priority only",
                    "type": "gate:filter",
                    "match": {"ticket": {"priority": "high"}},
                    "outputs": {
                        "ticket": {"type": "object"},
                        "state": {"type": "string"},
                    },
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "prompt": "state ${{gate-filter.state}}",
                }
            ],
            "edges": [
                _edge("trigger-1", "gate-filter"),
                _edge("gate-filter", "effect-1"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-1": {"ok": True}})
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-filter-pass",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket": {"priority": "high", "id": 7}, "state": "Committed"},
        )
    )

    for _ in range(5):
        worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    record = next(iter(gate_payload.values()))
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"ticket": {"priority": "high", "id": 7}, "state": "Committed"}

    effect_payload = effect_execution_payload(worker_config)
    effect_record = next(iter(effect_payload.values()))
    assert effect_record["status"] == "succeeded"


def test_filter_gate_drops_non_matching_payload(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"state": {"type": "string"}, "ticket_id": {"type": "integer"}},
                }
            ],
            "gates": [
                {
                    "id": "gate-filter",
                    "display_name": "Committed only",
                    "type": "gate:filter",
                    "match": {"state": "Committed"},
                    "outputs": {"state": {"type": "string"}, "ticket_id": {"type": "integer"}},
                }
            ],
            "edges": [_edge("trigger-1", "gate-filter")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-filter-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"state": "Open", "ticket_id": 7},
        )
    )

    worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    record = next(iter(gate_payload.values()))
    assert record["status"] == "dropped"
    queue_payload = trigger_queue_payload(worker_config)
    assert not any(item["trigger_id"] == "gate-filter" for item in queue_payload.values())


def test_filter_gate_when_supports_string_branch_operators(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Git push",
                    "type": "trigger:git:push",
                    "managed_repo_id_glob": "mr_*",
                }
            ],
            "gates": [
                {
                    "id": "gate-filter",
                    "display_name": "Agent branch only",
                    "type": "gate:filter",
                    "outputs": {
                        "branch_name": {"type": "string"},
                        "managed_repo_id": {"type": "string"},
                        "commit_hash": {"type": "string"},
                        "new_commit_hash": {"type": "string"},
                        "old_commit_hash": {"type": "string"},
                        "ref_name": {"type": "string"},
                        "session_id": {"type": "string"},
                        "review_branch": {"type": "string"},
                        "notes_ref": {"type": "string"},
                        "default_branch": {"type": "string"},
                    },
                    "when": {
                        "all": [
                            {"left": "${{trigger-1.branch_name}}", "starts_with": "agents/"},
                            {"left": "${{trigger-1.branch_name}}", "contains": "/"},
                            {"left": "${{trigger-1.managed_repo_id}}", "starts_with": "mr_"},
                        ]
                    },
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "continue",
                }
            ],
            "edges": [
                _edge("trigger-1", "gate-filter"),
                _edge("gate-filter", "effect-1"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-1": {"thread_id": "thread-1", "success": True}})

    worker.event_queue.publish(
        TriggerEvent(
            event_id="git-event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:git:push",
            occurred_at=datetime(2026, 4, 7, 12, 0, tzinfo=timezone.utc),
            payload={
                "branch_name": "agents/session-1/topic",
                "managed_repo_id": "mr_0123456789abcdefabcd",
                "commit_hash": "a" * 40,
                "new_commit_hash": "a" * 40,
                "old_commit_hash": "0" * 40,
                "ref_name": "refs/heads/agents/session-1/topic",
                "default_branch": "main",
            },
            trace_id="trace-git-1",
        )
    )

    for _ in range(4):
        worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"

    effect_payload = effect_execution_payload(worker_config)
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1"}


def test_filter_gate_when_can_match_ancestor_effect_outputs(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "gate-filter",
                    "display_name": "Create succeeded",
                    "type": "gate:filter",
                    "outputs": {
                        "session_id": {"type": "string"},
                        "vm_name": {"type": "string"},
                        "labels": {"type": "object"},
                        "success": {"type": "boolean"},
                        "message": {"type": "string"},
                    },
                    "when": {
                        "all": [
                            {"left": "${{effect-1.success}}", "equals": True},
                            {"left": "${{effect-1.session_id}}", "is_not_null": True},
                        ]
                    },
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Create VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-1",
                },
                {
                    "id": "effect-2",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "${{effect-1.session_id}}",
                    "prompt": "Continue ${{effect-1.session_id}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "gate-filter"),
                _edge("gate-filter", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {"session_id": "session-123", "success": True},
            "effect-2": {"thread_id": "thread-1", "success": True},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-when-pass",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-when-pass",
        )
    )

    for _ in range(6):
        worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"
    assert gate_record["resolved_config"]["when"]["all"][0]["left"] is True
    assert gate_record["resolved_config"]["when"]["all"][1]["left"] == "session-123"

    effect_payload = effect_execution_payload(worker_config)
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}


def test_agent_prompt_on_completion_releases_downstream_effect_without_await_gate(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-2",
                    "display_name": "Post exec",
                    "type": "effect:agent:prompt",
                    "session_id": "${{effect-1.scope.session_id}}",
                    "prompt": "Write post-exec summary for ${{effect-1.vm_name}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "effect-2", source_port="completed"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1-abc123",
                "thread_id": "thread-1",
                "success": True,
            },
            "effect-2": {
                "thread_id": "thread-2",
                "success": True,
            },
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-on-completion-start",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-on-completion",
        )
    )

    for _ in range(4):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "waiting"
    assert "effect-2" not in by_effect

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-on-completion-finish",
            trigger_id="__await-agent-completion__",
            trigger_type="trigger:agent:run:completed",
            occurred_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
            payload={
                "dispatch_id": "dispatch-effect-1-abc123",
                "vm_name": "vm-issue-42",
                "session_ids": ["session-42"],
                "labels": {},
                "directory_paths": ["root/team-a"],
                "agent_repo_names": ["repo-a"],
                "libvirt_state": "running",
                "agent_state": "idle",
                "previous_agent_state": "running",
                "transition": {"from": "running", "to": "idle"},
                "last_state_update_at": "2026-04-04T12:05:00Z",
                "scope": {
                    "directory_path": "root/team-a",
                    "vm_name": "vm-issue-42",
                    "session_id": "session-42",
                    "vm_user": "agent",
                },
                "ip_address": "192.0.2.10",
                "vm": {"vm_name": "vm-issue-42"},
            },
            trace_id="trace-on-completion",
        )
    )

    for _ in range(6):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-1"]["outputs"]["vm_name"] == "vm-issue-42"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-2"]["resolved_config"]["session_id"] == "session-42"
    assert (
        by_effect["effect-2"]["resolved_config"]["prompt"]
        == "Write post-exec summary for vm-issue-42"
    )


def test_agent_enqueue_prompt_started_port_releases_downstream_after_dispatch(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-2",
                    "display_name": "Store metadata",
                    "type": "effect:session:update-metadata",
                    "target": "session-42",
                    "metadata": {"thread_id": "${{effect-1.thread_id}}"},
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "effect-2", source_port="started"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1-abc123",
                "thread_id": "thread-1",
                "success": True,
                "queued": False,
            },
            "effect-2": {"success": True},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-enqueue-started",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-enqueue-started",
        )
    )

    for _ in range(4):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "waiting"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-2"]["resolved_config"]["metadata"] == {"thread_id": "thread-1"}


def test_agent_enqueue_prompt_completed_port_waits_for_agent_completion(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-2",
                    "display_name": "Post completion",
                    "type": "effect:session:update-metadata",
                    "target": "session-42",
                    "metadata": {"completed": True},
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "effect-2", source_port="completed"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1-abc123",
                "thread_id": "thread-1",
                "success": True,
                "queued": False,
            },
            "effect-2": {"success": True},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-enqueue-completed-start",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-enqueue-completed",
        )
    )

    for _ in range(4):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "waiting"
    assert "effect-2" not in by_effect

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-enqueue-completed-finish",
            trigger_id="__await-agent-completion__",
            trigger_type="trigger:agent:run:completed",
            occurred_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
            payload={
                "dispatch_id": "dispatch-effect-1-abc123",
                "vm_name": "vm-issue-42",
                "session_ids": ["session-42"],
                "agent_state": "idle",
                "previous_agent_state": "running",
                "scope": {
                    "directory_path": "root",
                    "vm_name": "vm-issue-42",
                    "session_id": "session-42",
                    "vm_user": "agent",
                },
                "vm": {"vm_name": "vm-issue-42"},
            },
            trace_id="trace-enqueue-completed",
        )
    )

    for _ in range(6):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"


def test_agent_enqueue_prompt_queues_when_target_agent_is_running(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-1",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "list_vms",
        lambda include_inactive=True, vm_user="agent": [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="running",
                session_ids=[],
            )
        ],
    )
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "resume_agent",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("resume_agent should not be called while busy")
        ),
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-enqueue-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-enqueue-1",
        )
    )

    for _ in range(3):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    only = next(iter(effect_payload.values()))
    assert only["status"] == "succeeded"
    assert only["outputs"]["queued"] is True
    queued_payload = json.loads(
        (runtime_dir / "queued-agent-prompts.json").read_text(encoding="utf-8")
    )
    assert "vm-1" in queued_payload


def test_agent_enqueue_prompt_applies_local_capacity_after_model_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        model_provider_mode="local_openai",
        local_openai_api_base_url="http://127.0.0.1:11435/v1",
        local_openai_max_concurrency=1,
    )
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "First local prompt",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-1",
                    "prompt": "first",
                    "model": "resolved-local-model-a",
                },
                {
                    "id": "effect-2",
                    "display_name": "Second local prompt",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-2",
                    "prompt": "second",
                    "model": "resolved-local-model-b",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("trigger-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory.admin_service.runtime.provider_config = _ProviderConfigResolver(config)
    resume_calls: list[dict[str, object]] = []
    running_vms: set[str] = set()

    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "list_vms",
        lambda **_kwargs: [
            VmSummary(
                vm_name=vm_name,
                libvirt_state="running",
                agent_state="running" if vm_name in running_vms else "idle",
                session_ids=[],
            )
            for vm_name in ("vm-1", "vm-2")
        ],
    )

    def _resume_agent(**kwargs: object) -> object:
        resume_calls.append(dict(kwargs))
        running_vms.add(str(kwargs["target"]))
        return SimpleNamespace(
            success=True,
            thread_id=f"thread-{len(resume_calls)}",
        )

    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "resume_agent",
        _resume_agent,
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-local-capacity",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-local-capacity",
        )
    )

    for _ in range(4):
        worker.run_once()

    assert [call["model"] for call in resume_calls] == ["resolved-local-model-a"]
    executions = worker.execution_store.read_all().values()
    by_effect = {record.effect_id: record for record in executions}
    assert by_effect["effect-1"].status == "waiting"
    assert by_effect["effect-2"].status == "succeeded"
    assert by_effect["effect-2"].outputs["queued"] is True
    assert by_effect["effect-2"].outputs["queue_reason"] == LOCAL_INFERENCE_QUEUE_REASON
    queued_records = worker.queued_agent_prompt_store.read_all()
    assert len(queued_records) == 1
    [queued] = queued_records.values()
    assert queued.queue_key.startswith("vm-2:local-inference:")
    assert queued.model == "resolved-local-model-b"
    assert queued.model_provider_mode == "local_openai"
    assert queued.queue_reason == LOCAL_INFERENCE_QUEUE_REASON
    assert len(LocalInferenceCapacityStore(runtime_dir).read_all()) == 1

    first_dispatch_id = str(by_effect["effect-1"].outputs["dispatch_id"])
    running_vms.discard("vm-1")
    finalized = AgentEnqueuePromptEffectProvider.on_completion_event(
        worker=worker,
        queue_record=TriggerQueueRecord(
            event_id="event-local-capacity-completed",
            trigger_id="__await-agent-completion__",
            trigger_type="trigger:agent:run:completed",
            occurred_at=datetime(2026, 8, 9, 12, 5, tzinfo=timezone.utc),
            payload={
                "dispatch_id": first_dispatch_id,
                "vm_name": "vm-1",
                "agent_state": "idle",
            },
            trace_id="trace-local-capacity",
        ),
    )
    assert finalized == 1

    worker._run_post_completion_dispatch_hooks()

    assert [call["model"] for call in resume_calls] == [
        "resolved-local-model-a",
        "resolved-local-model-b",
    ]
    assert worker.queued_agent_prompt_store.read_all() == {}
    slots = LocalInferenceCapacityStore(runtime_dir).read_all()
    assert len(slots) == 1
    assert next(iter(slots.values())).model == "resolved-local-model-b"


def test_local_capacity_reconciles_terminal_vm_lease_before_new_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        model_provider_mode="local_openai",
        local_openai_api_base_url="http://127.0.0.1:11435/v1",
        local_openai_max_concurrency=1,
    )
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Local prompt",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-new",
                    "prompt": "continue",
                    "model": "resolved-local-model",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory.admin_service.runtime.provider_config = _ProviderConfigResolver(config)
    capacity_store = LocalInferenceCapacityStore(runtime_dir)
    orphaned = capacity_store.claim(
        max_concurrency=1,
        owner_id="orphaned-run",
        model="old-local-model",
        dispatch_id="old-dispatch",
        execution_backend="libvirt_vm",
        target_name="vm-old",
    )
    assert (
        capacity_store.mark_running(
            orphaned.slot_id,
            target_name="vm-old",
            execution_id="vm-old",
            now=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        is True
    )
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "list_vms",
        lambda **_kwargs: [
            VmSummary(
                vm_name="vm-old",
                libvirt_state="running",
                agent_state="idle",
                session_ids=[],
            ),
            VmSummary(
                vm_name="vm-new",
                libvirt_state="running",
                agent_state="idle",
                session_ids=[],
            ),
        ],
    )
    resume_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "resume_agent",
        lambda **kwargs: (
            resume_calls.append(dict(kwargs))
            or SimpleNamespace(success=True, thread_id="thread-new")
        ),
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-reconcile-capacity",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-reconcile-capacity",
        )
    )

    for _ in range(3):
        worker.run_once()

    assert [call["target"] for call in resume_calls] == ["vm-new"]
    remaining = capacity_store.read_all()
    assert orphaned.slot_id not in remaining
    assert len(remaining) == 1
    assert next(iter(remaining.values())).target_name == "vm-new"


def test_queued_dispatch_uses_current_provider_mode_for_capacity_admission(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        model_provider_mode="local_openai",
        local_openai_api_base_url="http://127.0.0.1:11435/v1",
        local_openai_max_concurrency=1,
    )
    worker_config = canonical_worker_config(
        {"runtime_dir": runtime_dir, "triggers": [], "effects": [], "edges": []}
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    capacity_store = LocalInferenceCapacityStore(runtime_dir)
    occupied = capacity_store.claim(
        max_concurrency=1,
        owner_id="active-local-run",
        model="local-model",
        execution_backend="container_worker_vm",
    )
    assert capacity_store.mark_running(occupied.slot_id) is True
    record = QueuedAgentPromptRecord(
        queue_key="vm-1",
        target="vm-1",
        vm_name="vm-1",
        prompt="continue",
        model="local-model",
        model_provider_mode="openai_public",
        effect_id="effect-1",
    )

    admission = AgentEnqueuePromptQueueDispatchHook()._claim_local_inference_capacity_for_record(
        worker=worker,
        record=record,
        config=config,
        queue_key=record.queue_key,
        dispatch_id="queued-dispatch",
        execution_backend="libvirt_vm",
    )

    assert admission.enabled is True
    assert admission.admitted is False


def test_agent_enqueue_prompt_queues_racing_prompt_while_dispatch_is_inflight(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "max_events_per_cycle": 2,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"prompt": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-1",
                    "prompt": "${{trigger-1.prompt}}",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    resume_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "list_vms",
        lambda include_inactive=True, vm_user="agent": [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="idle",
                session_ids=[],
            )
        ],
    )

    def _resume_agent(**kwargs: object) -> object:
        resume_calls.append(dict(kwargs))
        return type(
            "Result",
            (),
            {"success": True, "thread_id": f"thread-{len(resume_calls)}"},
        )()

    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "resume_agent",
        _resume_agent,
    )

    for index, prompt in enumerate(("first", "second"), start=1):
        worker.event_queue.publish(
            TriggerEvent(
                event_id=f"event-{index}",
                trigger_id="trigger-1",
                trigger_type="trigger:cron",
                occurred_at=datetime(2026, 4, 4, 12, index, tzinfo=timezone.utc),
                payload={"prompt": prompt},
                trace_id="trace-racing-enqueue",
            )
        )

    for _ in range(4):
        worker.run_once()

    assert [call["prompt"] for call in resume_calls] == ["first"]
    queued_payload = json.loads(
        (runtime_dir / "queued-agent-prompts.json").read_text(encoding="utf-8")
    )
    assert queued_payload["vm-1"]["prompt"] == "second"

    effect_payload = effect_execution_payload(worker_config)
    by_event = {item["event_id"]: item for item in effect_payload.values()}
    assert by_event["event-1"]["status"] == "waiting"
    assert by_event["event-1"]["outputs"]["queued"] is False
    assert by_event["event-2"]["status"] == "succeeded"
    assert by_event["event-2"]["outputs"]["queued"] is True
    lock_store_path = runtime_dir / "agent-prompt-locks.json"
    lock_payload = json.loads(lock_store_path.read_text(encoding="utf-8"))
    assert list(lock_payload) == ["vm:vm-1"]
    assert lock_payload["vm:vm-1"]["owner_id"] == by_event["event-1"]["idempotency_key"]
    assert by_event["event-1"]["outputs"]["prompt_lock_key"] == "vm:vm-1"
    assert (
        by_event["event-1"]["outputs"]["prompt_lock_owner_id"]
        == by_event["event-1"]["idempotency_key"]
    )

    finalized = AgentEnqueuePromptEffectProvider.on_completion_event(
        worker=worker,
        queue_record=TriggerQueueRecord(
            event_id="agent-completion-1",
            trigger_id="__await-agent-completion__",
            trigger_type="trigger:agent:run:completed",
            occurred_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
            payload={
                "dispatch_id": by_event["event-1"]["outputs"]["dispatch_id"],
                "vm_name": "vm-1",
                "agent_state": "idle",
            },
            trace_id="trace-racing-enqueue",
        ),
    )

    assert finalized == 1
    assert json.loads(lock_store_path.read_text(encoding="utf-8")) == {}


def test_queued_agent_prompt_record_accepts_optional_workflow_context() -> None:
    base_payload = {
        "queue_key": "vm-1",
        "target": "vm-1",
        "vm_name": "vm-1",
        "prompt": "hello",
        "effect_id": "effect-1",
    }

    without_context = QueuedAgentPromptRecord.model_validate(base_payload)
    with_context = QueuedAgentPromptRecord.model_validate(
        {
            **base_payload,
            "workflow_context": {
                "gate-map": {"ticket": {"issue_id": "ISSUE-1"}},
                "ignored": "not-a-node-payload",
            },
            "workflow_for_execution_ids": {
                "for-1": "event-for-1",
                " ": "ignored",
                "for-empty": "",
            },
        }
    )

    assert without_context.workflow_context is None
    assert without_context.workflow_for_execution_ids is None
    assert with_context.workflow_context == {"gate-map": {"ticket": {"issue_id": "ISSUE-1"}}}
    assert with_context.workflow_for_execution_ids == {"for-1": "event-for-1"}


def test_agent_prompt_queue_coalescing_keeps_latest_workflow_context(tmp_path: Path) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    first = queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt="old prompt",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            effect_id="effect-1",
            workflow_context={"gate-map": {"ticket": {"issue_id": "ISSUE-1"}}},
            workflow_for_execution_ids={"for-1": "event-old"},
        )
    )

    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt="new prompt",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            effect_id="effect-1",
            workflow_context={"gate-map": {"ticket": {"issue_id": "ISSUE-2"}}},
            workflow_for_execution_ids={"for-1": "event-new"},
        )
    )

    stored = queue_store.get("vm-1")
    assert stored is not None
    assert stored.created_at == first.created_at
    assert stored.coalesced_count == 2
    assert stored.prompt == "new prompt"
    assert stored.workflow_context == {"gate-map": {"ticket": {"issue_id": "ISSUE-2"}}}
    assert stored.workflow_for_execution_ids == {"for-1": "event-new"}


def test_agent_prompt_enqueue_persists_generated_dispatch_id(tmp_path: Path) -> None:
    queued = enqueue_agent_prompt(
        runtime_dir=tmp_path,
        target="vm-1",
        vm_name="vm-1",
        session_id=None,
        prompt="continue",
        model=None,
        vm_user="agent",
        dispatch_id=None,
        resume_thread_id=None,
        effect_id="effect-1",
    )

    assert str(queued.dispatch_id).startswith("dispatch-queued-")
    stored = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json").get(queued.queue_key)
    assert stored is not None
    assert stored.dispatch_id == queued.dispatch_id


@pytest.mark.parametrize(
    ("queue_key", "dispatch_id"),
    [
        ("scheduled-wake:request-1", "wake-dispatch-1"),
        ("admin-agent-prompt:libvirt_vm:session-1:dispatch:team-dispatch-1", "team-dispatch-1"),
        ("wake-prompt:wake-dispatch-2", "wake-dispatch-2"),
        ("effect-1:local-inference:capacity-dispatch-1", "capacity-dispatch-1"),
    ],
)
def test_agent_prompt_replay_preserves_dispatch_scoped_queue_keys(
    queue_key: str,
    dispatch_id: str,
) -> None:
    record = QueuedAgentPromptRecord(
        queue_key=queue_key,
        target="vm-1",
        vm_name="vm-1",
        prompt="continue",
        dispatch_id=dispatch_id,
        effect_id="effect-1",
    )

    assert agent_prompt_replay_queue_key(record) == queue_key


def test_agent_prompt_queue_compare_delete_keeps_replacement_dispatch(tmp_path: Path) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="durable-key",
            target="vm-1",
            vm_name="vm-1",
            prompt="prompt A",
            dispatch_id="dispatch-a",
            effect_id="effect-1",
        )
    )
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="durable-key",
            target="vm-1",
            vm_name="vm-1",
            prompt="prompt B",
            dispatch_id="dispatch-b",
            effect_id="effect-1",
        )
    )

    assert queue_store.delete_if_dispatch_id("durable-key", "dispatch-a") is False
    replacement = queue_store.get("durable-key")
    assert replacement is not None
    assert replacement.dispatch_id == "dispatch-b"
    assert replacement.prompt == "prompt B"


def test_agent_prompt_queue_replay_claim_preserves_fifo_and_retry_metadata(
    tmp_path: Path,
) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    created_at = datetime(2026, 8, 10, 9, 0, tzinfo=timezone.utc)
    original = queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            prompt="prompt A",
            dispatch_id="dispatch-a",
            effect_id="effect-1",
            created_at=created_at,
            coalesced_count=3,
        )
    )
    claimed_key = agent_prompt_replay_queue_key(original)

    claimed = queue_store.claim_for_replay(
        "vm-1",
        "dispatch-a",
        claimed_queue_key=claimed_key,
    )

    assert claimed is not None
    assert claimed.queue_key == claimed_key
    assert claimed.created_at == created_at
    assert claimed.coalesced_count == 3
    assert claimed.retry_key == original.retry_key
    assert claimed.retry_count == original.retry_count
    assert claimed.max_retries == original.max_retries
    assert queue_store.get("vm-1") is None


def test_agent_prompt_queue_retry_ledger_caps_recreated_prompt(tmp_path: Path) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")

    for _ in range(3):
        queue_store.put(
            QueuedAgentPromptRecord(
                queue_key="pr-feedback:session-1:feedback-1",
                target="session-1",
                vm_name="worker-1",
                session_id="session-1",
                prompt="check feedback",
                model=None,
                vm_user="agent",
                resume_thread_id=None,
                effect_id="effect-1",
            ),
            max_retries=3,
        )
        queue_store.delete("pr-feedback:session-1:feedback-1")

    with pytest.raises(AgentPromptRetryLimitExceeded) as exc_info:
        queue_store.put(
            QueuedAgentPromptRecord(
                queue_key="pr-feedback:session-1:feedback-1",
                target="session-1",
                vm_name="worker-1",
                session_id="session-1",
                prompt="check feedback",
                model=None,
                vm_user="agent",
                resume_thread_id=None,
                effect_id="effect-1",
            ),
            max_retries=3,
        )

    assert exc_info.value.retry_count == 4
    assert exc_info.value.max_retries == 3
    assert queue_store.get("pr-feedback:session-1:feedback-1") is None
    ledger_payload = json.loads(
        (tmp_path / "agent-prompt-retry-ledger.json").read_text(encoding="utf-8")
    )
    [ledger_record] = ledger_payload.values()
    assert ledger_record["retry_count"] == 4
    assert ledger_record["max_retries"] == 3
    assert ledger_record["exhausted_at"] is not None


def test_agent_prompt_queue_retry_ledger_allows_distinct_prompt_for_same_key(
    tmp_path: Path,
) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="admin-agent-prompt:container_worker_vm:session-1",
            target="session-1",
            vm_name="worker-1",
            session_id="session-1",
            prompt="first prompt",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            effect_id="admin-agent-prompt:container_worker_vm:session-1",
        ),
        max_retries=1,
    )
    queue_store.delete("admin-agent-prompt:container_worker_vm:session-1")

    queued = queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="admin-agent-prompt:container_worker_vm:session-1",
            target="session-1",
            vm_name="worker-1",
            session_id="session-1",
            prompt="second prompt",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            effect_id="admin-agent-prompt:container_worker_vm:session-1",
        ),
        max_retries=1,
    )

    assert queued.retry_count == 1
    assert len(json.loads((tmp_path / "agent-prompt-retry-ledger.json").read_text())) == 2


def test_agent_enqueue_prompt_queued_completion_preserves_upstream_workflow_context(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"issue_id": {"type": "string"}},
                }
            ],
            "gates": [
                {
                    "id": "gate-map",
                    "display_name": "Map",
                    "type": "gate:map",
                    "template": {"ticket": {"issue_id": "${{trigger-1.issue_id}}"}},
                    "outputs": {"ticket": {"type": "object"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-1",
                    "prompt": "Handle ${{gate-map.ticket.issue_id}}",
                },
                {
                    "id": "effect-2",
                    "display_name": "Record completion",
                    "type": "effect:session:update-metadata",
                    "target": "session-1",
                    "metadata": {
                        "issue_id": "${{gate-map.ticket.issue_id}}",
                        "agent_done": True,
                    },
                },
            ],
            "edges": [
                _edge("trigger-1", "gate-map"),
                _edge("gate-map", "effect-1"),
                _edge("effect-1", "effect-2", source_port="completed"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "list_vms",
        lambda **kwargs: [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="running",
                session_ids=["session-1"],
            )
        ],
    )
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "resume_agent",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("resume_agent should only be called by the queued dispatch port")
        ),
    )
    metadata_updates: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker.provider_factory.admin_service.issue_git_admin,
        "session_update_metadata",
        lambda **kwargs: metadata_updates.append(kwargs) or {"success": True},
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-queued-context-start",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"issue_id": "ISSUE-123"},
            trace_id="trace-queued-context",
        )
    )

    for _ in range(6):
        worker.run_once()

    queued_payload = json.loads(
        (runtime_dir / "queued-agent-prompts.json").read_text(encoding="utf-8")
    )
    assert queued_payload["vm-1"]["workflow_context"]["gate-map"] == {
        "ticket": {"issue_id": "ISSUE-123"}
    }

    resume_calls: list[dict[str, object]] = []
    worker.agent_run_status_port = _AgentRunStatusPortStub(
        [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="idle",
                session_ids=["session-1"],
            )
        ]
    )
    worker.agent_prompt_dispatch_port = _AgentPromptDispatchPortStub(
        resume_calls,
        type("Result", (), {"success": True, "thread_id": "thread-queued"})(),
    )

    worker.run_once()

    assert len(resume_calls) == 1
    waiting_records = [
        record
        for record in worker.execution_store.read_all().values()
        if record.effect_id == "effect-1" and record.status == "waiting"
    ]
    assert len(waiting_records) == 1
    waiting_record = waiting_records[0]
    assert waiting_record.input_payload["__node_outputs"]["gate-map"] == {
        "ticket": {"issue_id": "ISSUE-123"}
    }
    dispatch_id = str(waiting_record.outputs["dispatch_id"])

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-queued-context-complete",
            trigger_id="__await-agent-completion__",
            trigger_type="trigger:agent:run:completed",
            occurred_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
            payload={
                "dispatch_id": dispatch_id,
                "thread_id": "thread-queued",
                "session_id": "session-1",
                "success": True,
                "agent_state": "idle",
            },
            trace_id="trace-queued-context",
        )
    )

    for _ in range(8):
        worker.run_once()

    assert metadata_updates == [
        {
            "target": "session-1",
            "metadata": {"issue_id": "ISSUE-123", "agent_done": True},
        }
    ]
    effect_payload = effect_execution_payload(worker_config)
    effect_2_records = [
        record for record in effect_payload.values() if record["effect_id"] == "effect-2"
    ]
    assert len(effect_2_records) == 1
    assert effect_2_records[0]["status"] == "succeeded"


def test_agent_enqueue_prompt_queued_started_loopback_preserves_for_gate_state(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"sessions": {"type": "array"}},
                }
            ],
            "gates": [
                {
                    "id": "for-1",
                    "display_name": "For each session",
                    "type": "gate:for",
                    "items": "${{trigger-1.sessions}}",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "${{for-1.current_element.vm_name}}",
                    "prompt": "Handle ${{for-1.current_element.session_id}}",
                },
                {
                    "id": "effect-done",
                    "display_name": "Done",
                    "type": "effect:session:update-metadata",
                    "target": "session-1",
                    "metadata": {"for_done": True},
                },
            ],
            "edges": [
                _edge("trigger-1", "for-1"),
                {"source_id": "for-1", "source_port": "body", "target_id": "effect-1"},
                {"source_id": "for-1", "source_port": "done", "target_id": "effect-done"},
                {
                    "source_id": "effect-1",
                    "source_port": "started",
                    "target_id": "for-1",
                    "allow_cycle": True,
                },
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "list_vms",
        lambda **kwargs: [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="running",
                session_ids=["session-1"],
            )
        ],
    )
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "resume_agent",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("resume_agent should only be called by queued dispatch")
        ),
    )
    metadata_updates: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker.provider_factory.admin_service.issue_git_admin,
        "session_update_metadata",
        lambda **kwargs: metadata_updates.append(kwargs) or {"success": True},
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-for-queued-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"sessions": [{"session_id": "session-1", "vm_name": "vm-1"}]},
            trace_id="trace-for-queued",
        )
    )

    for _ in range(6):
        worker.run_once()

    queued_payload = json.loads(
        (runtime_dir / "queued-agent-prompts.json").read_text(encoding="utf-8")
    )
    assert queued_payload["vm-1"]["workflow_for_execution_ids"] == {"for-1": "event-for-queued-1"}

    resume_calls: list[dict[str, object]] = []
    worker.agent_run_status_port = _AgentRunStatusPortStub(
        [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="idle",
                session_ids=["session-1"],
            )
        ]
    )
    worker.agent_prompt_dispatch_port = _AgentPromptDispatchPortStub(
        resume_calls,
        type("Result", (), {"success": True, "thread_id": "thread-queued"})(),
    )

    for _ in range(10):
        worker.run_once()

    assert len(resume_calls) == 1
    queued_store_path = runtime_dir / "queued-agent-prompts.json"
    queued_after_dispatch = (
        json.loads(queued_store_path.read_text(encoding="utf-8"))
        if queued_store_path.exists()
        else {}
    )
    assert queued_after_dispatch == {}
    assert metadata_updates == [
        {"target": "session-1", "metadata": {"for_done": True}},
    ]

    gate_payload = gate_execution_payload(worker_config)
    for_records = [item for item in gate_payload.values() if item["gate_id"] == "for-1"]
    assert len(for_records) == 2
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is False) == 1
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is True) == 1
    assert [item["resolved_config"]["for_execution_id"] for item in for_records] == [
        "event-for-queued-1",
        "event-for-queued-1",
    ]


def test_agent_enqueue_prompt_dispatches_queued_prompt_when_target_agent_becomes_idle(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-1",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    resume_calls: list[dict[str, object]] = []
    worker.queued_agent_prompt_store.put(
        QueuedAgentPromptRecord(
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt="hello",
            model=None,
            vm_user="agent",
            dispatch_id="caller-dispatch-1",
            resume_thread_id=None,
            resume_mode="same_context",
            effect_id="effect-1",
            metadata={
                "workflow_run_id": "workflow-run-1",
                "mcp_tool_approval_lease_id": "approval-lease-1",
            },
        )
    )
    worker.agent_run_status_port = _AgentRunStatusPortStub(
        [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="idle",
                session_ids=[],
            )
        ]
    )
    worker.agent_prompt_dispatch_port = _AgentPromptDispatchPortStub(
        resume_calls,
        type("Result", (), {"success": True, "thread_id": "thread-queued"})(),
    )

    worker._run_post_completion_dispatch_hooks()

    assert len(resume_calls) == 1
    assert resume_calls[0]["target"] == "vm-1"
    assert resume_calls[0]["dispatch_id"] == "caller-dispatch-1"
    assert resume_calls[0]["resume_mode"] == "same_context"
    queued_store_path = runtime_dir / "queued-agent-prompts.json"
    queued_payload = (
        json.loads(queued_store_path.read_text(encoding="utf-8"))
        if queued_store_path.exists()
        else {}
    )
    assert queued_payload == {}
    waiting_records = list(worker.execution_store.read_all().values())
    assert len(waiting_records) == 1
    assert waiting_records[0].status == "waiting"
    assert waiting_records[0].effect_type == "effect:agent:enqueue-prompt"
    assert waiting_records[0].outputs["dispatch_id"] == "caller-dispatch-1"
    assert waiting_records[0].outputs["thread_id"] == "thread-queued"
    assert waiting_records[0].resolved_config["resume_mode"] == "same_context"
    assert waiting_records[0].input_payload["resume_mode"] == "same_context"
    for payload in (
        waiting_records[0].resolved_config,
        waiting_records[0].input_payload,
        waiting_records[0].outputs,
    ):
        assert payload["workflow_run_id"] == "workflow-run-1"
        assert payload["mcp_tool_approval_lease_id"] == "approval-lease-1"


def test_agent_enqueue_prompt_dispatch_keeps_queued_prompt_when_lock_is_held(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-1",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    resume_calls: list[dict[str, object]] = []
    worker.queued_agent_prompt_store.put(
        QueuedAgentPromptRecord(
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt="hello",
            model=None,
            vm_user="agent",
            dispatch_id="caller-dispatch-1",
            resume_thread_id=None,
            effect_id="effect-1",
        )
    )
    worker.agent_run_status_port = _AgentRunStatusPortStub(
        [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="idle",
                session_ids=[],
            )
        ]
    )
    worker.agent_prompt_dispatch_port = _AgentPromptDispatchPortStub(
        resume_calls,
        type("Result", (), {"success": True, "thread_id": "thread-queued"})(),
    )
    claim = worker._agent_prompt_activity.claim_enqueue_prompt(
        target="vm-1",
        vm_name="vm-1",
        session_id=None,
        owner_id="other-owner",
        dispatch_id="other-dispatch",
        effect_id="effect-1",
    )

    worker._run_post_completion_dispatch_hooks()

    for dispatch_id, prompt in (
        ("caller-dispatch-2", "follow-up B"),
        ("caller-dispatch-3", "follow-up C"),
    ):
        enqueue_agent_prompt(
            runtime_dir=runtime_dir,
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt=prompt,
            model=None,
            vm_user="agent",
            dispatch_id=dispatch_id,
            resume_thread_id=None,
            effect_id="effect-1",
        )
        worker._run_post_completion_dispatch_hooks()

    assert claim.claimed is True
    assert resume_calls == []
    queued_payload = json.loads(
        (runtime_dir / "queued-agent-prompts.json").read_text(encoding="utf-8")
    )
    assert list(queued_payload) == ["vm-1"]
    assert queued_payload["vm-1"]["dispatch_id"] == "caller-dispatch-3"
    assert queued_payload["vm-1"]["prompt"] == "follow-up C"
    assert queued_payload["vm-1"]["coalesced_count"] == 3
    assert worker.execution_store.read_all() == {}


def test_agent_enqueue_prompt_capacity_denial_keeps_one_coalescing_row(
    tmp_path: Path,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        model_provider_mode="local_openai",
        local_openai_api_base_url="http://127.0.0.1:11435/v1",
        local_openai_max_concurrency=1,
    )
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    enqueue_agent_prompt(
        runtime_dir=tmp_path,
        queue_key="vm-1",
        target="vm-1",
        vm_name="vm-1",
        session_id=None,
        prompt="prompt A",
        model="local-model",
        vm_user="agent",
        dispatch_id="dispatch-a",
        resume_thread_id=None,
        effect_id="effect-1",
    )
    occupied = LocalInferenceCapacityStore(tmp_path).claim(
        max_concurrency=1,
        owner_id="other-owner",
        model="local-model",
    )
    assert occupied.admitted is True
    resume_calls: list[dict[str, object]] = []
    worker = SimpleNamespace(
        config=config,
        queued_agent_prompt_store=queue_store,
        execution_store=SqliteExecutionStore(tmp_path / "orchestration.db"),
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=[],
                )
            ]
        ),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            resume_calls,
            SimpleNamespace(success=True, thread_id="unexpected"),
        ),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={"effect-1": SimpleNamespace(type="effect:agent:enqueue-prompt")}
        ),
    )
    hook = AgentEnqueuePromptQueueDispatchHook()

    assert hook.on_post_completion_dispatch(worker=worker) == 0
    for dispatch_id, prompt in (("dispatch-b", "prompt B"), ("dispatch-c", "prompt C")):
        enqueue_agent_prompt(
            runtime_dir=tmp_path,
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt=prompt,
            model="local-model",
            vm_user="agent",
            dispatch_id=dispatch_id,
            resume_thread_id=None,
            effect_id="effect-1",
        )
        assert hook.on_post_completion_dispatch(worker=worker) == 0

    assert resume_calls == []
    assert list(queue_store.read_all()) == ["vm-1"]
    queued = queue_store.get("vm-1")
    assert queued is not None
    assert queued.dispatch_id == "dispatch-c"
    assert queued.prompt == "prompt C"
    assert queued.coalesced_count == 3


@pytest.mark.parametrize("claim_failure", ["mismatch", "exception"])
def test_agent_enqueue_prompt_releases_lock_and_capacity_when_replay_cas_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    claim_failure: str,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        model_provider_mode="local_openai",
        local_openai_api_base_url="http://127.0.0.1:11435/v1",
        local_openai_max_concurrency=1,
    )
    worker_config = canonical_worker_config(
        {
            "runtime_dir": tmp_path,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-1",
                    "prompt": "prompt A",
                    "model": "local-model",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory.admin_service.runtime.provider_config = _ProviderConfigResolver(config)
    enqueue_agent_prompt(
        runtime_dir=tmp_path,
        queue_key="vm-1",
        target="vm-1",
        vm_name="vm-1",
        session_id=None,
        prompt="prompt A",
        model="local-model",
        vm_user="agent",
        dispatch_id="dispatch-a",
        resume_thread_id=None,
        effect_id="effect-1",
    )
    worker.agent_run_status_port = _AgentRunStatusPortStub(
        [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="idle",
                session_ids=[],
            )
        ]
    )
    resume_calls: list[dict[str, object]] = []
    worker.agent_prompt_dispatch_port = _AgentPromptDispatchPortStub(
        resume_calls,
        SimpleNamespace(success=True, thread_id="unexpected"),
    )
    original_claim = worker.queued_agent_prompt_store.claim_for_replay

    def lose_replay_claim(queue_key, dispatch_id, *, claimed_queue_key):
        if claim_failure == "exception":
            raise OSError("queue store unavailable")
        enqueue_agent_prompt(
            runtime_dir=tmp_path,
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt="replacement B",
            model="local-model",
            vm_user="agent",
            dispatch_id="dispatch-b",
            resume_thread_id=None,
            effect_id="effect-1",
        )
        return original_claim(
            queue_key,
            dispatch_id,
            claimed_queue_key=claimed_queue_key,
        )

    monkeypatch.setattr(
        worker.queued_agent_prompt_store,
        "claim_for_replay",
        lose_replay_claim,
    )

    worker._run_post_completion_dispatch_hooks()

    assert resume_calls == []
    replacement = worker.queued_agent_prompt_store.get("vm-1")
    assert replacement is not None
    assert replacement.dispatch_id == (
        "dispatch-b" if claim_failure == "mismatch" else "dispatch-a"
    )
    assert LocalInferenceCapacityStore(tmp_path).read_all() == {}
    follow_up_lock = worker._agent_prompt_activity.claim_enqueue_prompt(
        target="vm-1",
        vm_name="vm-1",
        session_id=None,
        owner_id="follow-up-owner",
        dispatch_id="follow-up-dispatch",
        effect_id="effect-1",
    )
    assert follow_up_lock.claimed is True


def test_agent_enqueue_prompt_replay_discards_libvirt_row_after_waiting_record_crash(
    tmp_path: Path,
) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queued = QueuedAgentPromptRecord(
        queue_key="libvirt-crash-window",
        target="vm-1",
        vm_name="vm-1",
        prompt="continue",
        dispatch_id="dispatch-libvirt-crash",
        effect_id="effect-libvirt",
    )
    queue_store.put(queued)
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    execution_store.put(_queued_prompt_execution_record(queued, status="waiting"))
    resume_calls: list[dict[str, object]] = []
    published_started: list[tuple[str | None, str]] = []
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        execution_store=execution_store,
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=[],
                )
            ]
        ),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            resume_calls,
            SimpleNamespace(success=True, thread_id="duplicate-thread"),
        ),
        _publish_completion_event_from_record=(
            lambda record, outputs, branch_port=None: (
                published_started.append((branch_port, str(outputs.get("dispatch_id") or "")))
                or "started-event"
            )
        ),
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 0
    assert resume_calls == []
    assert published_started == [("started", "dispatch-libvirt-crash")]
    assert queue_store.get(queued.queue_key) is None
    assert (
        execution_store.find_execution_by_idempotency_key(
            _queued_prompt_execution_record(queued, status="waiting").idempotency_key
        )
        is not None
    )


def test_agent_enqueue_prompt_replay_discards_container_row_after_terminal_record_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="worker-agent",
            metadata={"execution_backend": "container_worker_vm"},
        )
    )
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queued = QueuedAgentPromptRecord(
        queue_key="container-crash-window",
        target="session-1",
        vm_name="worker-agent",
        session_id="session-1",
        prompt="continue",
        dispatch_id="dispatch-container-crash",
        effect_id="effect-container",
    )
    queue_store.put(queued)
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    execution_store.put(_queued_prompt_execution_record(queued, status="succeeded"))
    backend_calls: list[object] = []
    published_started: list[tuple[str | None, str]] = []
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend_calls.append(config),
    )
    worker = SimpleNamespace(
        config=config,
        queued_agent_prompt_store=queue_store,
        execution_store=execution_store,
        agent_prompt_dispatch_port=object(),
        provider_config_resolver=SimpleNamespace(resolve_for_session_id=lambda _id: config),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        _publish_completion_event_from_record=(
            lambda record, outputs, branch_port=None: (
                published_started.append((branch_port, str(outputs.get("dispatch_id") or "")))
                or "started-event"
            )
        ),
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 0
    assert backend_calls == []
    assert published_started == [("started", "dispatch-container-crash")]
    assert queue_store.get(queued.queue_key) is None


def test_agent_enqueue_prompt_replay_retries_terminal_started_event_before_deleting_row(
    tmp_path: Path,
) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queued = QueuedAgentPromptRecord(
        queue_key="terminal-started-retry",
        target="vm-1",
        vm_name="vm-1",
        prompt="continue",
        dispatch_id="dispatch-terminal-retry",
        effect_id="effect-terminal-retry",
    )
    queue_store.put(queued)
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    terminal = _queued_prompt_execution_record(queued, status="succeeded")
    execution_store.put(terminal)
    publish_attempts: list[str] = []

    def publish_started_event(*, record, outputs, branch_port=None):
        publish_attempts.append(str(branch_port or ""))
        if len(publish_attempts) == 1:
            raise RuntimeError("event queue unavailable")
        return "started-event"

    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        execution_store=execution_store,
        agent_run_status_port=_AgentRunStatusPortStub([]),
        agent_prompt_dispatch_port=object(),
        _publish_completion_event_from_record=publish_started_event,
    )
    hook = AgentEnqueuePromptQueueDispatchHook()

    assert hook.on_post_completion_dispatch(worker=worker) == 0
    assert queue_store.get(queued.queue_key) is not None
    unchanged = execution_store.find_execution_by_idempotency_key(terminal.idempotency_key)
    assert unchanged is not None
    assert unchanged.status == "succeeded"

    assert hook.on_post_completion_dispatch(worker=worker) == 0
    assert publish_attempts == ["started", "started"]
    assert queue_store.get(queued.queue_key) is None


def test_agent_enqueue_prompt_reconcile_publishes_queued_started_before_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queued = QueuedAgentPromptRecord(
        queue_key="queued-container-reconcile",
        target="session-1",
        vm_name="worker-agent",
        session_id="session-1",
        prompt="continue",
        dispatch_id="dispatch-container-reconcile",
        effect_id="effect-container-reconcile",
    )
    waiting = _queued_prompt_execution_record(queued, status="waiting")
    waiting.outputs.update(
        {
            "dispatched_from_queue": True,
            "execution_backend": "container_worker_vm",
            "execution_id": "worker-execution-1",
        }
    )
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    execution_store.put(waiting)
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queue_store.put(queued)
    publish_attempts: list[str] = []
    reconciliation_calls: list[str] = []

    def publish_started_event(*, record, outputs, branch_port=None):
        publish_attempts.append(str(branch_port or ""))
        if len(publish_attempts) == 1:
            raise RuntimeError("event queue unavailable")
        return "started-event"

    def reconcile_container(cls, *, worker, record):
        reconciliation_calls.append(str(record.outputs.get("dispatch_id") or ""))
        record.status = "succeeded"
        worker.execution_store.record_execution(record)
        return 1

    monkeypatch.setattr(
        AgentPromptEffectProvider,
        "_reconcile_container_waiting_record",
        classmethod(reconcile_container),
    )
    worker = SimpleNamespace(
        execution_store=execution_store,
        queued_agent_prompt_store=queue_store,
        _publish_completion_event_from_record=publish_started_event,
    )

    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker) == 0
    after_failure = execution_store.find_execution_by_idempotency_key(waiting.idempotency_key)
    assert after_failure is not None
    assert after_failure.status == "waiting"
    assert reconciliation_calls == []

    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker) == 1
    assert publish_attempts == ["started", "started"]
    assert reconciliation_calls == ["dispatch-container-reconcile"]
    after_success = execution_store.find_execution_by_idempotency_key(waiting.idempotency_key)
    assert after_success is not None
    assert after_success.status == "succeeded"


def test_agent_enqueue_prompt_restart_repairs_claimed_row_then_respects_retention(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "orchestration.db"
    started_event_id = "effect:started:effect-retained:deadbeef0001"
    event_queue = SqliteTriggerEventQueue(database_path)
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queued = queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            prompt="prompt A",
            dispatch_id="dispatch-a",
            effect_id="effect-1",
            created_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    claimed_key = agent_prompt_replay_queue_key(queued)
    claimed = queue_store.claim_for_replay(
        queued.queue_key,
        "dispatch-a",
        claimed_queue_key=claimed_key,
    )
    assert claimed is not None
    for dispatch_id, prompt in (("dispatch-b", "prompt B"), ("dispatch-c", "prompt C")):
        enqueue_agent_prompt(
            runtime_dir=tmp_path,
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt=prompt,
            model=None,
            vm_user="agent",
            dispatch_id=dispatch_id,
            resume_thread_id=None,
            effect_id="effect-1",
        )

    waiting = _queued_prompt_execution_record(claimed, status="waiting")
    waiting.outputs["dispatched_from_queue"] = True
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    execution_store.put(waiting)
    publish_calls: list[str] = []

    def publish_started_event(*, record, outputs, branch_port=None):
        publish_calls.append(str(branch_port or ""))
        event_queue.publish(
            TriggerEvent(
                event_id=started_event_id,
                trigger_id=record.effect_id,
                trigger_type=record.effect_type,
                occurred_at=datetime.now(timezone.utc),
                payload=dict(outputs),
            ),
            idempotency_key=f"{record.idempotency_key}:{branch_port}",
        )
        return started_event_id

    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        execution_store=execution_store,
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=[],
                )
            ]
        ),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            [],
            SimpleNamespace(success=True, thread_id="unexpected"),
        ),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={"effect-1": SimpleNamespace(type="effect:agent:enqueue-prompt")}
        ),
        _publish_completion_event_from_record=publish_started_event,
    )

    assert AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker) == 0
    assert publish_calls == ["started"]
    assert event_queue.contains(started_event_id)
    assert queue_store.get(claimed_key) is None
    replacement = queue_store.get("vm-1")
    assert replacement is not None
    assert replacement.dispatch_id == "dispatch-c"

    assert event_queue.mark_delivered(
        started_event_id,
        delivered_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    restarted_event_queue = SqliteTriggerEventQueue(
        database_path,
        retention_days=0,
        compact_history_retention_days=365,
    )
    assert not restarted_event_queue.contains(started_event_id)

    restarted_worker = SimpleNamespace(
        execution_store=execution_store,
        queued_agent_prompt_store=queue_store,
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="running",
                    session_ids=[],
                )
            ]
        ),
        _publish_completion_event_from_record=publish_started_event,
    )

    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=restarted_worker) == 0
    assert publish_calls == ["started"]
    assert not restarted_event_queue.contains(started_event_id)
    assert queue_store.get("vm-1") is not None


def test_agent_enqueue_prompt_replay_retains_row_for_different_active_dispatch(
    tmp_path: Path,
) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queued = QueuedAgentPromptRecord(
        queue_key="queued-next",
        target="session-1",
        vm_name="vm-1",
        session_id="session-1",
        prompt="next prompt",
        dispatch_id="dispatch-next",
        effect_id="effect-next",
    )
    queue_store.put(queued)
    active = QueuedAgentPromptRecord(
        queue_key="already-running",
        target="session-1",
        vm_name="vm-1",
        session_id="session-1",
        prompt="active prompt",
        dispatch_id="dispatch-active",
        effect_id="effect-active",
    )
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    execution_store.put(_queued_prompt_execution_record(active, status="waiting"))
    resume_calls: list[dict[str, object]] = []
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        execution_store=execution_store,
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=["session-1"],
                )
            ]
        ),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            resume_calls,
            SimpleNamespace(success=True, thread_id="duplicate-thread"),
        ),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={"effect-next": SimpleNamespace(type="effect:agent:enqueue-prompt")}
        ),
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 0
    assert resume_calls == []
    assert queue_store.get(queued.queue_key) is not None


def test_agent_enqueue_prompt_libvirt_replay_keeps_prompts_arriving_during_launch(
    tmp_path: Path,
) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    first = enqueue_agent_prompt(
        runtime_dir=tmp_path,
        queue_key="vm-1",
        target="vm-1",
        vm_name="vm-1",
        session_id=None,
        prompt="prompt A",
        model=None,
        vm_user="agent",
        dispatch_id="dispatch-a",
        resume_thread_id=None,
        effect_id="effect-1",
    )
    resume_calls: list[dict[str, object]] = []

    class InterleavingDispatchPort(_AgentPromptDispatchPortStub):
        def dispatch_agent_prompt(self, **kwargs):
            result = super().dispatch_agent_prompt(**kwargs)
            for dispatch_id, prompt in (
                ("dispatch-b", "prompt B"),
                ("dispatch-c", "prompt C"),
            ):
                enqueue_agent_prompt(
                    runtime_dir=tmp_path,
                    queue_key="vm-1",
                    target="vm-1",
                    vm_name="vm-1",
                    session_id=None,
                    prompt=prompt,
                    model=None,
                    vm_user="agent",
                    dispatch_id=dispatch_id,
                    resume_thread_id=None,
                    effect_id="effect-1",
                )
            return result

    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        execution_store=execution_store,
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=[],
                )
            ]
        ),
        agent_prompt_dispatch_port=InterleavingDispatchPort(
            resume_calls,
            SimpleNamespace(success=True, thread_id="thread-a"),
        ),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={"effect-1": SimpleNamespace(type="effect:agent:enqueue-prompt")}
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: (
            "started-event"
        ),
    )

    assert AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker) == 1

    assert [call["dispatch_id"] for call in resume_calls] == ["dispatch-a"]
    replacement = queue_store.get("vm-1")
    assert replacement is not None
    assert replacement.dispatch_id == "dispatch-c"
    assert replacement.prompt == "prompt C"
    assert replacement.coalesced_count == 2
    assert replacement.created_at > first.created_at
    assert queue_store.get("vm-1:replay:dispatch-a") is None
    [waiting] = execution_store.read_all().values()
    assert waiting.outputs["queue_key"] == "vm-1:replay:dispatch-a"
    assert waiting.outputs["dispatch_id"] == "dispatch-a"


def test_agent_enqueue_prompt_failed_claimed_replay_keeps_newer_prompts_coalescing(
    tmp_path: Path,
) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    enqueue_agent_prompt(
        runtime_dir=tmp_path,
        queue_key="vm-1",
        target="vm-1",
        vm_name="vm-1",
        session_id=None,
        prompt="prompt A",
        model=None,
        vm_user="agent",
        dispatch_id="dispatch-a",
        resume_thread_id=None,
        effect_id="effect-1",
    )
    resume_calls: list[dict[str, object]] = []
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        execution_store=SqliteExecutionStore(tmp_path / "orchestration.db"),
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=[],
                )
            ]
        ),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            resume_calls,
            SimpleNamespace(success=False, thread_id=None),
        ),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={"effect-1": SimpleNamespace(type="effect:agent:enqueue-prompt")}
        ),
    )
    hook = AgentEnqueuePromptQueueDispatchHook()

    assert hook.on_post_completion_dispatch(worker=worker) == 0
    for dispatch_id, prompt in (("dispatch-b", "prompt B"), ("dispatch-c", "prompt C")):
        enqueue_agent_prompt(
            runtime_dir=tmp_path,
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt=prompt,
            model=None,
            vm_user="agent",
            dispatch_id=dispatch_id,
            resume_thread_id=None,
            effect_id="effect-1",
        )
        assert hook.on_post_completion_dispatch(worker=worker) == 0

    assert [call["dispatch_id"] for call in resume_calls] == [
        "dispatch-a",
        "dispatch-a",
        "dispatch-a",
    ]
    assert set(queue_store.read_all()) == {"vm-1:replay:dispatch-a", "vm-1"}
    replacement = queue_store.get("vm-1")
    assert replacement is not None
    assert replacement.dispatch_id == "dispatch-c"
    assert replacement.coalesced_count == 2


def test_agent_enqueue_prompt_container_replay_keeps_prompts_arriving_during_launch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="worker-agent",
            metadata={"execution_backend": "container_worker_vm"},
        )
    )
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    first = enqueue_agent_prompt(
        runtime_dir=tmp_path,
        queue_key="session-1",
        target="session-1",
        vm_name="worker-agent",
        session_id="session-1",
        prompt="prompt A",
        model=None,
        vm_user="agent",
        dispatch_id="dispatch-a",
        resume_thread_id=None,
        effect_id="effect-1",
    )
    backend = _ContainerDispatchBackendStub()
    original_start = backend.start

    def interleaving_start(spec):
        for dispatch_id, prompt in (
            ("dispatch-b", "prompt B"),
            ("dispatch-c", "prompt C"),
        ):
            enqueue_agent_prompt(
                runtime_dir=tmp_path,
                queue_key="session-1",
                target="session-1",
                vm_name="worker-agent",
                session_id="session-1",
                prompt=prompt,
                model=None,
                vm_user="agent",
                dispatch_id=dispatch_id,
                resume_thread_id=None,
                effect_id="effect-1",
            )
        return original_start(spec)

    backend.start = interleaving_start
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    worker = SimpleNamespace(
        config=config,
        queued_agent_prompt_store=queue_store,
        execution_store=execution_store,
        agent_prompt_dispatch_port=object(),
        provider_config_resolver=SimpleNamespace(resolve_for_session_id=lambda _id: config),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: (
            "started-event"
        ),
    )

    assert AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker) == 1

    replacement = queue_store.get("session-1")
    assert replacement is not None
    assert replacement.dispatch_id == "dispatch-c"
    assert replacement.prompt == "prompt C"
    assert replacement.coalesced_count == 2
    assert replacement.created_at > first.created_at
    assert queue_store.get("session-1:replay:dispatch-a") is None
    [waiting] = execution_store.read_all().values()
    assert waiting.outputs["queue_key"] == "session-1:replay:dispatch-a"
    assert waiting.outputs["dispatch_id"] == "dispatch-a"


@pytest.mark.parametrize("backend_state", ["unavailable", "busy"])
def test_agent_enqueue_prompt_container_ineligible_replay_keeps_one_coalescing_row(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend_state: str,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="worker-agent",
            metadata={"execution_backend": "container_worker_vm"},
        )
    )
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    enqueue_agent_prompt(
        runtime_dir=tmp_path,
        queue_key="session-1",
        target="session-1",
        vm_name="worker-agent",
        session_id="session-1",
        prompt="prompt A",
        model=None,
        vm_user="agent",
        dispatch_id="dispatch-a",
        resume_thread_id=None,
        effect_id="effect-1",
    )
    backend = _ContainerDispatchBackendStub()
    if backend_state == "busy":
        backend.repository = SimpleNamespace(
            list_active_by_session=lambda _session_id, _statuses: [
                SimpleNamespace(execution_id="active-execution", last_status="running")
            ]
        )
        backend.status = SimpleNamespace(
            status=ExecutionStatusKind.RUNNING,
            message=None,
            metadata={},
        )

    def build_backend(config, admin_service):
        del config, admin_service
        if backend_state == "unavailable":
            raise RuntimeError("container backend unavailable")
        return backend

    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        build_backend,
    )
    worker = SimpleNamespace(
        config=config,
        queued_agent_prompt_store=queue_store,
        execution_store=SqliteExecutionStore(tmp_path / "orchestration.db"),
        agent_prompt_dispatch_port=object(),
        provider_config_resolver=SimpleNamespace(resolve_for_session_id=lambda _id: config),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
    )
    hook = AgentEnqueuePromptQueueDispatchHook()

    assert hook.on_post_completion_dispatch(worker=worker) == 0
    for dispatch_id, prompt in (("dispatch-b", "prompt B"), ("dispatch-c", "prompt C")):
        enqueue_agent_prompt(
            runtime_dir=tmp_path,
            queue_key="session-1",
            target="session-1",
            vm_name="worker-agent",
            session_id="session-1",
            prompt=prompt,
            model=None,
            vm_user="agent",
            dispatch_id=dispatch_id,
            resume_thread_id=None,
            effect_id="effect-1",
        )
        assert hook.on_post_completion_dispatch(worker=worker) == 0

    assert list(queue_store.read_all()) == ["session-1"]
    queued = queue_store.get("session-1")
    assert queued is not None
    assert queued.dispatch_id == "dispatch-c"
    assert queued.prompt == "prompt C"
    assert queued.coalesced_count == 3
    assert backend.started_specs == []


def test_agent_enqueue_prompt_dispatch_keeps_libvirt_behind_older_busy_container_capacity_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    created_at = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)
    for queue_key, created_delta in (("container-oldest", 0), ("libvirt-newer", 1)):
        queue_store.put(
            QueuedAgentPromptRecord(
                queue_key=queue_key,
                target=queue_key,
                vm_name=queue_key,
                prompt=queue_key,
                model="local-model",
                queue_reason=LOCAL_INFERENCE_QUEUE_REASON,
                effect_id=f"effect-{queue_key}",
                created_at=created_at + timedelta(seconds=created_delta),
            )
        )
    resume_calls: list[dict[str, object]] = []
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        execution_store=SqliteExecutionStore(tmp_path / "orchestration.db"),
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-newer",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=[],
                )
            ]
        ),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            resume_calls,
            SimpleNamespace(success=True, thread_id="thread-newer"),
        ),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={
                "effect-container-oldest": SimpleNamespace(type="effect:agent:enqueue-prompt"),
                "effect-libvirt-newer": SimpleNamespace(type="effect:agent:enqueue-prompt"),
            }
        ),
    )
    hook = AgentEnqueuePromptQueueDispatchHook()
    monkeypatch.setattr(
        hook,
        "_container_queue_keys",
        lambda **_kwargs: {"container-oldest"},
    )
    monkeypatch.setattr(
        hook,
        "_dispatch_container_record_if_idle",
        lambda **_kwargs: False,
    )

    dispatched = hook.on_post_completion_dispatch(worker=worker)

    assert dispatched == 0
    assert resume_calls == []
    assert sorted(queue_store.read_all()) == ["container-oldest", "libvirt-newer"]


def test_agent_enqueue_prompt_dispatch_prunes_removed_effect_before_capacity_fifo_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    created_at = datetime(2026, 8, 9, 10, 0, tzinfo=timezone.utc)
    for queue_key, effect_id, created_delta in (
        ("removed-effect", "effect-removed", 0),
        ("current-effect", "effect-current", 1),
    ):
        queue_store.put(
            QueuedAgentPromptRecord(
                queue_key=queue_key,
                target=queue_key,
                vm_name=queue_key,
                prompt=queue_key,
                model="local-model",
                queue_reason=LOCAL_INFERENCE_QUEUE_REASON,
                dispatch_id=f"dispatch-{queue_key}",
                effect_id=effect_id,
                created_at=created_at + timedelta(seconds=created_delta),
            )
        )
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        agent_prompt_dispatch_port=object(),
        agent_run_status_port=_AgentRunStatusPortStub([]),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={"effect-current": SimpleNamespace(type="effect:agent:enqueue-prompt")}
        ),
    )
    hook = AgentEnqueuePromptQueueDispatchHook()
    monkeypatch.setattr(hook, "_container_queue_keys", lambda **_kwargs: set())
    monkeypatch.setattr(hook, "_has_waiting_enqueue_prompt_for_record", lambda **_kwargs: True)

    assert hook.on_post_completion_dispatch(worker=worker) == 0

    assert queue_store.get("removed-effect") is None
    assert queue_store.get("current-effect") is not None
    admission = LocalInferenceCapacityStore(tmp_path).claim(
        max_concurrency=1,
        owner_id="current-replay",
        model="local-model",
        queue_key="current-effect",
    )
    assert admission.admitted is True


def test_agent_enqueue_prompt_dispatch_skips_expired_libvirt_queued_prompt(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )
    queue_store = AgentPromptQueueStore(runtime_dir / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="vm-1",
            target="session-1",
            vm_name="vm-1",
            session_id="session-1",
            prompt="hello",
            model=None,
            queue_reason=LOCAL_INFERENCE_QUEUE_REASON,
            vm_user="agent",
            resume_thread_id=None,
            effect_id="effect-1",
        )
    )
    resume_calls: list[dict[str, object]] = []
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=["session-1"],
                )
            ]
        ),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            resume_calls,
            type("Result", (), {"success": True, "thread_id": "thread-queued"})(),
        ),
        execution_store=SqliteExecutionStore(runtime_dir / "orchestration.db"),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={"effect-1": SimpleNamespace(type="effect:agent:enqueue-prompt")}
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: None,
        _update_event_delivery_state=lambda event_id: None,
    )

    caplog.set_level(logging.WARNING, logger="evander.effects.providers.lifecycle_hooks")

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 0
    assert resume_calls == []
    assert queue_store.get("vm-1") is None
    admission = LocalInferenceCapacityStore(runtime_dir).claim(
        max_concurrency=1,
        owner_id="new-arrival",
        model="local-model",
    )
    assert admission.admitted is True
    assert "queue_key=vm-1" in caplog.text
    assert "session_id=session-1" in caplog.text
    assert "expires_at=" in caplog.text


def test_agent_enqueue_prompt_dispatch_skips_expired_container_queued_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={"execution_backend": "container_worker_vm", "target_name": "truman"},
        )
    )
    queue_store = AgentPromptQueueStore(runtime_dir / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="session-1",
            target="session-1",
            vm_name="truman",
            session_id="session-1",
            prompt="hello",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            effect_id="effect-1",
        )
    )
    backend_calls: list[tuple[object, object]] = []
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: (
            backend_calls.append((config, admin_service)) or _ContainerDispatchBackendStub()
        ),
    )
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        agent_prompt_dispatch_port=object(),
        execution_store=SqliteExecutionStore(runtime_dir / "orchestration.db"),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        provider_config_resolver=SimpleNamespace(
            resolve_for_session_id=lambda session_id: config,
            resolve_for_directory=lambda directory_path: config,
        ),
    )

    caplog.set_level(logging.WARNING, logger="evander.effects.providers.lifecycle_hooks")

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 0
    assert backend_calls == []
    assert queue_store.get("session-1") is None
    assert "queue_key=session-1" in caplog.text
    assert "session_id=session-1" in caplog.text
    assert "expires_at=" in caplog.text


def test_agent_enqueue_prompt_dispatch_preserves_mcp_lease_for_queued_libvirt_prompt(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        gateway_url="http://gateway.test/mcp",
    )
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-1",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    resume_calls: list[dict[str, object]] = []
    worker.queued_agent_prompt_store.put(
        QueuedAgentPromptRecord(
            queue_key="vm-1",
            target="session-1",
            vm_name="vm-1",
            session_id="session-1",
            prompt="hello",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            effect_id="effect-1",
            metadata={
                "workflow_run_id": "run-1",
                "mcp_tool_approval_lease_id": "lease-1",
            },
        )
    )
    worker.agent_run_status_port = _AgentRunStatusPortStub(
        [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="idle",
                session_ids=["session-1"],
            )
        ]
    )
    worker.agent_prompt_dispatch_port = _AgentPromptDispatchPortStub(
        resume_calls,
        type("Result", (), {"success": True, "thread_id": "thread-queued"})(),
    )

    worker._run_post_completion_dispatch_hooks()

    assert len(resume_calls) == 1
    assert resume_calls[0]["mcp_gateway_url"] == (
        "http://gateway.test/mcp?"
        "session_id=session-1&workflow_run_id=run-1&mcp_tool_approval_lease_id=lease-1"
    )


def test_agent_enqueue_prompt_dispatch_reads_and_updates_resume_thread_metadata_key(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            metadata={"agent_thread_ids": {"teams:chat-1:agent_thread_id": "thread-existing"}},
        )
    )
    queue_store = AgentPromptQueueStore(runtime_dir / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="teams:chat-1:msg-1",
            target="session-1",
            vm_name="vm-1",
            session_id="session-1",
            prompt="hello",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            resume_thread_metadata_key="teams:chat-1:agent_thread_id",
            effect_id="effect-1",
        )
    )
    execution_store = SqliteExecutionStore(runtime_dir / "orchestration.db")
    resume_calls: list[dict[str, object]] = []
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=["session-1"],
                )
            ]
        ),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            resume_calls,
            type("Result", (), {"success": True, "thread_id": "thread-updated"})(),
        ),
        execution_store=execution_store,
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={
                "effect-1": SimpleNamespace(type="effect:agent:enqueue-prompt"),
            }
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: None,
        _update_event_delivery_state=lambda event_id: None,
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 1
    assert resume_calls[0]["resume_thread_id"] == "thread-existing"
    assert resume_calls[0]["persist_thread_id"] is False
    updated = session_repository.get_session("session-1")
    assert updated is not None
    assert updated.metadata["agent_thread_ids"]["teams:chat-1:agent_thread_id"] == (
        "thread-updated"
    )
    assert "teams:chat-1:agent_thread_id" not in updated.metadata
    waiting_record = next(iter(execution_store.read_all().values()))
    assert waiting_record.resolved_config["resume_thread_metadata_key"] == (
        "teams:chat-1:agent_thread_id"
    )
    assert waiting_record.resolved_config["resume_thread_metadata_collection"] == (
        "agent_thread_ids"
    )


def test_agent_enqueue_prompt_dispatch_does_not_fallback_to_default_thread_for_new_key(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            metadata={
                "agent_thread_id": "thread-default",
                "agent_thread_ids": {
                    "teams:chat-1:agent_thread_id": "thread-existing",
                },
            },
        )
    )
    queue_store = AgentPromptQueueStore(runtime_dir / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="teams:chat-2:msg-1",
            target="session-1",
            vm_name="vm-1",
            session_id="session-1",
            prompt="hello",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            resume_thread_metadata_key="teams:chat-2:agent_thread_id",
            effect_id="effect-1",
        )
    )
    execution_store = SqliteExecutionStore(runtime_dir / "orchestration.db")
    resume_calls: list[dict[str, object]] = []
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=["session-1"],
                )
            ]
        ),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            resume_calls,
            type("Result", (), {"success": True, "thread_id": "thread-new-key"})(),
        ),
        execution_store=execution_store,
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={
                "effect-1": SimpleNamespace(type="effect:agent:enqueue-prompt"),
            }
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: None,
        _update_event_delivery_state=lambda event_id: None,
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 1
    assert resume_calls[0]["resume_thread_id"] is None
    assert resume_calls[0]["persist_thread_id"] is False
    updated = session_repository.get_session("session-1")
    assert updated is not None
    assert updated.metadata["agent_thread_id"] == "thread-default"
    assert updated.metadata["agent_thread_ids"] == {
        "teams:chat-1:agent_thread_id": "thread-existing",
        "teams:chat-2:agent_thread_id": "thread-new-key",
    }


@pytest.mark.parametrize(
    ("expires_delta", "renew_if_expires_within", "renewal_reason"),
    [
        (timedelta(minutes=-5), None, "expired"),
        (timedelta(minutes=10), "30m", "within_window"),
    ],
)
def test_session_renew_if_expired_runs_before_prompting(
    tmp_path: Path,
    expires_delta: timedelta,
    renew_if_expires_within: str | None,
    renewal_reason: str,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(timezone.utc) + expires_delta,
        )
    )
    renew_effect = {
        "id": "effect-renew",
        "display_name": "Renew session",
        "type": "effect:session:renew-if-expired",
        "session_id": "session-1",
        "extend_by": "6h",
    }
    if renew_if_expires_within is not None:
        renew_effect["renew_if_expires_within"] = renew_if_expires_within
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                renew_effect,
                {
                    "id": "effect-prompt",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "${{effect-renew.session_id}}",
                    "prompt": "Continue ${{effect-renew.session_id}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-renew"),
                _edge("effect-renew", "effect-prompt"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    prompt_calls: list[dict[str, object]] = []
    worker.provider_factory = _RenewThenPromptFactory(
        config=config,
        session_repository=session_repository,
        prompt_calls=prompt_calls,
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-renew-before-prompt",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    for _ in range(6):
        worker.run_once()

    renewed_session = session_repository.get_session("session-1")
    assert renewed_session is not None
    execution_payload = effect_execution_payload(worker_config)
    records_by_effect = {record["effect_id"]: record for record in execution_payload.values()}
    assert set(records_by_effect) == {"effect-renew", "effect-prompt"}
    assert records_by_effect["effect-renew"]["status"] == "succeeded"
    assert records_by_effect["effect-renew"]["outputs"]["renewal_reason"] == renewal_reason
    assert records_by_effect["effect-renew"]["outputs"]["renewed"] is True
    assert records_by_effect["effect-prompt"]["status"] == "succeeded"
    assert prompt_calls == [
        {
            "session_id": "session-1",
            "prompt": "Continue session-1",
            "expires_at": renewed_session.expires_at,
        }
    ]


def test_session_prompt_without_renewal_still_fails_for_expired_session(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
    )
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-prompt",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "Continue session-1",
                }
            ],
            "edges": [_edge("trigger-1", "effect-prompt")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    prompt_calls: list[dict[str, object]] = []
    worker.provider_factory = _RenewThenPromptFactory(
        config=config,
        session_repository=session_repository,
        prompt_calls=prompt_calls,
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-prompt-only-expired",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    record = next(iter(execution_payload.values()))
    assert record["status"] == "failed"
    assert "prompt guard saw expired session session-1" in record["error"]
    assert prompt_calls == []
    unchanged = session_repository.get_session("session-1")
    assert unchanged is not None
    assert unchanged.expires_at <= datetime.now(timezone.utc)


def test_agent_enqueue_prompt_dispatch_serializes_distinct_queue_keys_for_same_session(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            allowed_tools=[ToolName.GET_AGENT_STATUS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            metadata={"agent_thread_ids": {"teams:chat-1:agent_thread_id": "thread-existing"}},
        )
    )
    queue_store = AgentPromptQueueStore(runtime_dir / "queued-agent-prompts.json")
    for message_id in ("msg-1", "msg-2"):
        queue_store.put(
            QueuedAgentPromptRecord(
                queue_key=f"teams:chat-1:{message_id}",
                target="session-1",
                vm_name="vm-1",
                session_id="session-1",
                prompt=f"hello {message_id}",
                model=None,
                vm_user="agent",
                resume_thread_id=None,
                resume_thread_metadata_key="teams:chat-1:agent_thread_id",
                effect_id="effect-1",
            )
        )
    execution_store = SqliteExecutionStore(runtime_dir / "orchestration.db")
    resume_calls: list[dict[str, object]] = []
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=["session-1"],
                )
            ]
        ),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            resume_calls,
            type("Result", (), {"success": True, "thread_id": "thread-updated"})(),
        ),
        execution_store=execution_store,
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={
                "effect-1": SimpleNamespace(type="effect:agent:enqueue-prompt"),
            }
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: None,
        _update_event_delivery_state=lambda event_id: None,
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 1
    assert [call["prompt"] for call in resume_calls] == ["hello msg-1"]
    assert sorted(queue_store.read_all()) == ["teams:chat-1:msg-2"]
    waiting_records = list(execution_store.read_all().values())
    assert len(waiting_records) == 1
    assert waiting_records[0].status == "waiting"
    assert waiting_records[0].outputs["session_id"] == "session-1"


def test_agent_enqueue_prompt_queue_dispatch_skips_and_retries_when_inventory_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runtime_dir = tmp_path / "runtime"
    queue_store = AgentPromptQueueStore(runtime_dir / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt="hello",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            effect_id="effect-1",
        )
    )
    execution_store = SqliteExecutionStore(runtime_dir / "orchestration.db")
    resume_calls: list[dict[str, object]] = []
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        agent_run_status_port=_FailingAgentRunStatusPortStub(),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            resume_calls,
            type("Result", (), {"success": True, "thread_id": "thread-queued"})(),
        ),
        execution_store=execution_store,
        _build_execution_graph=lambda: SimpleNamespace(
            effects_by_id={
                "effect-1": SimpleNamespace(type="effect:agent:enqueue-prompt"),
            }
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: None,
        _update_event_delivery_state=lambda event_id: None,
    )

    caplog.set_level(logging.ERROR, logger="evander.effects.providers.lifecycle_hooks")

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 0
    assert resume_calls == []
    queued_store_path = runtime_dir / "queued-agent-prompts.json"
    queued_payload = json.loads(queued_store_path.read_text(encoding="utf-8"))
    assert queued_payload["vm-1"]["prompt"] == "hello"
    assert "AgentEnqueuePromptQueueDispatchHook" in caplog.text
    assert "queued_non_container_records=1" in caplog.text
    assert "virsh list failed" in caplog.text

    caplog.clear()
    worker.agent_run_status_port = _AgentRunStatusPortStub(
        [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="idle",
                session_ids=[],
            )
        ]
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 1
    assert len(resume_calls) == 1
    assert resume_calls[0]["target"] == "vm-1"
    assert queue_store.read_all() == {}
    waiting_records = list(execution_store.read_all().values())
    assert len(waiting_records) == 1
    assert waiting_records[0].status == "waiting"
    assert waiting_records[0].effect_type == "effect:agent:enqueue-prompt"


def test_agent_enqueue_prompt_dispatches_admin_prompt_without_graph_effect(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    queue_store = AgentPromptQueueStore(runtime_dir / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="admin-agent-prompt:libvirt_vm:session-1",
            target="session-1",
            vm_name="vm-1",
            session_id="session-1",
            prompt="manual admin prompt",
            model=None,
            vm_user="agent",
            resume_thread_id="thread-1",
            effect_id="admin-agent-prompt:libvirt_vm:session-1",
        )
    )
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="orphan-prompt",
            target="vm-2",
            vm_name="vm-2",
            session_id=None,
            prompt="unknown orphan prompt",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            effect_id="orphan-prompt",
        )
    )
    execution_store = SqliteExecutionStore(runtime_dir / "orchestration.db")
    resume_calls: list[dict[str, object]] = []
    worker = SimpleNamespace(
        queued_agent_prompt_store=queue_store,
        agent_run_status_port=_AgentRunStatusPortStub(
            [
                VmSummary(
                    vm_name="vm-1",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=["session-1"],
                ),
                VmSummary(
                    vm_name="vm-2",
                    libvirt_state="running",
                    agent_state="idle",
                    session_ids=[],
                ),
            ]
        ),
        agent_prompt_dispatch_port=_AgentPromptDispatchPortStub(
            resume_calls,
            type("Result", (), {"success": True, "thread_id": "thread-dispatched"})(),
        ),
        execution_store=execution_store,
        _build_execution_graph=lambda: SimpleNamespace(effects_by_id={}),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: None,
        _update_event_delivery_state=lambda event_id: None,
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 1
    assert len(resume_calls) == 1
    assert resume_calls[0]["target"] == "session-1"
    assert resume_calls[0]["prompt"] == "manual admin prompt"
    assert resume_calls[0]["resume_thread_id"] == "thread-1"
    assert queue_store.get("admin-agent-prompt:libvirt_vm:session-1") is None
    assert queue_store.get("orphan-prompt") is not None
    waiting_records = list(execution_store.read_all().values())
    assert len(waiting_records) == 1
    assert waiting_records[0].status == "waiting"
    assert waiting_records[0].effect_type == "effect:agent:enqueue-prompt"
    assert (
        waiting_records[0]
        .outputs["queue_key"]
        .startswith("admin-agent-prompt:libvirt_vm:session-1:replay:")
    )
    assert waiting_records[0].outputs["thread_id"] == "thread-dispatched"


def test_agent_enqueue_prompt_dispatch_does_not_publish_dispatch_id_as_thread_id(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-1",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    resume_calls: list[dict[str, object]] = []
    worker.queued_agent_prompt_store.put(
        QueuedAgentPromptRecord(
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt="hello",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            effect_id="effect-1",
        )
    )
    worker.agent_run_status_port = _AgentRunStatusPortStub(
        [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="idle",
                session_ids=[],
            )
        ]
    )
    worker.agent_prompt_dispatch_port = _AgentPromptDispatchPortStub(
        resume_calls,
        type("Result", (), {"success": True, "thread_id": ""})(),
    )

    worker._run_post_completion_dispatch_hooks()

    waiting_records = list(worker.execution_store.read_all().values())
    assert len(waiting_records) == 1
    outputs = waiting_records[0].outputs
    assert str(outputs["dispatch_id"]).startswith("dispatch-effect-1-")
    assert "thread_id" not in outputs


def test_agent_enqueue_prompt_dispatch_hook_tracks_container_queue_until_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "truman",
                "container_worker": {"image": "runner:queue-image"},
                "agent_thread_ids": {"teams:chat-1:agent_thread_id": "thread-existing"},
            },
        )
    )
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="session-1",
            target="session-1",
            vm_name="truman",
            session_id="session-1",
            prompt="check teams",
            model=None,
            vm_user="agent",
            dispatch_id="caller-dispatch-2",
            resume_thread_id=None,
            resume_thread_metadata_key="teams:chat-1:agent_thread_id",
            effect_id="effect-agent-enqueue-prompt-1",
        )
    )
    backend = _ContainerDispatchBackendStub()
    backend.logs = "prompt complete\nsession id: 019de31a-8393-71d2-8990-e5f4fb0a4df2\n"
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
    monkeypatch.setattr(
        "evander.effects.providers.agent_prompt.build_execution_backend",
        lambda config, admin_service: backend,
    )
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    published_events: list[tuple[str | None, dict[str, object]]] = []
    worker = SimpleNamespace(
        config=config,
        execution_store=execution_store,
        queued_agent_prompt_store=queue_store,
        agent_prompt_dispatch_port=object(),
        provider_config_resolver=SimpleNamespace(resolve_for_session_id=lambda session_id: config),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: (
            published_events.append((branch_port, outputs)) or "effect-completed"
        ),
        _update_event_delivery_state=lambda event_id: None,
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 1
    assert queue_store.read_all() == {}
    assert backend.started_specs[0].session_id == "session-1"
    assert backend.started_specs[0].metadata["dispatch_id"] == "caller-dispatch-2"
    assert backend.started_specs[0].image == "runner:queue-image"
    assert backend.started_specs[0].codex_turn is not None
    assert str(backend.started_specs[0].codex_turn.mcp_gateway_url).startswith(
        "http://192.168.250.1:8000/mcp?session_id=session-1"
    )
    assert "check teams" in str(backend.started_specs[0].command)
    assert "mcp_servers.evander.url" in str(backend.started_specs[0].command)
    assert "thread-existing" in str(backend.started_specs[0].command)
    waiting_records = list(execution_store.read_all().values())
    assert len(waiting_records) == 1
    assert waiting_records[0].status == "waiting"
    assert waiting_records[0].effect_type == "effect:agent:enqueue-prompt"
    assert waiting_records[0].outputs["dispatch_id"] == "caller-dispatch-2"
    execution_id = str(waiting_records[0].outputs["execution_id"])
    assert execution_id == backend.started_specs[0].target_name
    assert published_events[0][0] == "started"

    backend.status = SimpleNamespace(
        status=ExecutionStatusKind.RUNNING,
        message=None,
        metadata={},
    )
    reconciled = AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker)

    assert reconciled == 0
    running_record = list(execution_store.read_all().values())[0]
    assert running_record.status == "waiting"
    assert running_record.outputs["thread_id"] == "thread-existing"
    refreshed_session = session_repository.get_session("session-1")
    assert refreshed_session is not None
    assert refreshed_session.metadata["agent_thread_ids"]["teams:chat-1:agent_thread_id"] == (
        "thread-existing"
    )
    assert "teams:chat-1:agent_thread_id" not in refreshed_session.metadata

    backend.status = SimpleNamespace(
        status=ExecutionStatusKind.STOPPED,
        message=None,
        metadata={},
    )
    reconciled = AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker)

    assert reconciled == 1
    assert backend.destroyed == [execution_id]
    final_records = list(execution_store.read_all().values())
    assert final_records[0].status == "succeeded"
    assert published_events[-1][0] == "completed"
    assert published_events[-1][1]["worker_status"] == "stopped"
    assert published_events[-1][1]["thread_id"] == "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    refreshed_session = session_repository.get_session("session-1")
    assert refreshed_session is not None
    assert "agent_thread_id" not in refreshed_session.metadata
    assert (
        refreshed_session.metadata["agent_thread_ids"]["teams:chat-1:agent_thread_id"]
        == "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    )
    assert "teams:chat-1:agent_thread_id" not in refreshed_session.metadata


def test_container_prompt_completion_without_agent_output_fails_without_persisting_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "truman",
                "agent_thread_ids": {"teams:chat-1:agent_thread_id": "thread-existing"},
            },
        )
    )
    backend = _ContainerDispatchBackendStub()
    backend.logs = (
        "session id: 019de31a-8393-71d2-8990-e5f4fb0a4df2\n"
        "codex failed: no agent message was emitted\n"
    )
    backend.status = SimpleNamespace(
        status=ExecutionStatusKind.STOPPED,
        message=None,
        metadata={},
    )
    monkeypatch.setattr(
        "evander.effects.providers.agent_prompt.build_execution_backend",
        lambda config, admin_service: backend,
    )
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    published_events: list[tuple[str | None, dict[str, object]]] = []
    started_at = datetime.now(timezone.utc)
    record = EffectExecutionRecord(
        execution_id="effect-exec-1",
        idempotency_key="event-1::effect-agent-enqueue-prompt-1",
        event_id="event-1",
        trace_id="trace-1",
        source_node_id="trigger-1",
        source_trigger_type="trigger:teams:direct-message",
        effect_id="effect-agent-enqueue-prompt-1",
        effect_type="effect:agent:enqueue-prompt",
        status="waiting",
        started_at=started_at,
        finished_at=started_at,
        resolved_config_hash="hash",
        resolved_config={"directory_path": "root", "vm_user": "agent"},
        input_payload={},
        outputs={
            "dispatch_id": "caller-dispatch-2",
            "execution_backend": "container_worker_vm",
            "execution_id": "agent-session-1",
            "session_id": "session-1",
            "resume_thread_metadata_key": "teams:chat-1:agent_thread_id",
        },
    )
    execution_store.put(record)
    worker = SimpleNamespace(
        config=config,
        execution_store=execution_store,
        provider_config_resolver=SimpleNamespace(resolve_for_session_id=lambda session_id: config),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: (
            published_events.append((branch_port, outputs)) or "effect-completed"
        ),
        _update_event_delivery_state=lambda event_id: None,
    )

    backend.status = SimpleNamespace(
        status=ExecutionStatusKind.RUNNING,
        message=None,
        metadata={},
    )
    reconciled = AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker)

    assert reconciled == 0
    running_record = list(execution_store.read_all().values())[0]
    assert "thread_id" not in running_record.outputs
    refreshed_session = session_repository.get_session("session-1")
    assert refreshed_session is not None
    assert refreshed_session.metadata["agent_thread_ids"]["teams:chat-1:agent_thread_id"] == (
        "thread-existing"
    )

    backend.status = SimpleNamespace(
        status=ExecutionStatusKind.STOPPED,
        message=None,
        metadata={},
    )
    reconciled = AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker)

    assert reconciled == 1
    assert backend.destroyed == ["agent-session-1"]
    final_record = list(execution_store.read_all().values())[0]
    assert final_record.status == "succeeded"
    assert final_record.outputs["success"] is False
    assert final_record.outputs["agent_state"] == "failed"
    assert final_record.outputs["message"] == (
        "container worker job completed without an agent response"
    )
    assert final_record.outputs["codex_outcome"]["reason"] == "codex_no_final_message"
    assert published_events[-1][1]["success"] is False
    refreshed_session = session_repository.get_session("session-1")
    assert refreshed_session is not None
    assert refreshed_session.metadata["agent_thread_ids"]["teams:chat-1:agent_thread_id"] == (
        "thread-existing"
    )


def test_agent_enqueue_prompt_dispatch_hook_uses_queued_container_resume_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
        gateway_url="http://gateway.test/mcp",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={"execution_backend": "container_worker_vm", "target_name": "truman"},
        )
    )
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="scheduled-wake:wake-1",
            target="session-1",
            vm_name="truman",
            session_id="session-1",
            prompt="resume scheduled work",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            resume_mode="same_context",
            effect_id="scheduled-wake:wake-1",
            metadata={
                "workflow_run_id": "run-1",
                "mcp_tool_approval_lease_id": "lease-1",
                "reduced_context_prompt": "continue from the compact handoff",
                "agent_context_retry_enabled": "false",
                "agent_context_retry_max_attempts": "0",
                "agent_context_retry_target_chars": "4096",
            },
        )
    )
    backend = _ContainerDispatchBackendStub()
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    published_events: list[tuple[str | None, dict[str, object]]] = []
    worker = SimpleNamespace(
        config=config,
        execution_store=execution_store,
        queued_agent_prompt_store=queue_store,
        agent_prompt_dispatch_port=object(),
        provider_config_resolver=SimpleNamespace(resolve_for_session_id=lambda session_id: config),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: (
            published_events.append((branch_port, outputs)) or "effect-completed"
        ),
        _update_event_delivery_state=lambda event_id: None,
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 1
    assert queue_store.read_all() == {}
    command = str(backend.started_specs[0].command)
    assert "codex exec resume --last" in command
    assert "resume scheduled work" in command
    assert "mcp_servers.evander.url" in command
    assert "workflow_run_id=run-1" in command
    assert "mcp_tool_approval_lease_id=lease-1" in command
    waiting_records = list(execution_store.read_all().values())
    assert waiting_records[0].resolved_config["resume_mode"] == "same_context"
    assert waiting_records[0].input_payload["resume_mode"] == "same_context"
    assert waiting_records[0].resolved_config["workflow_run_id"] == "run-1"
    assert waiting_records[0].resolved_config["mcp_tool_approval_lease_id"] == "lease-1"
    assert waiting_records[0].input_payload["workflow_run_id"] == "run-1"
    assert waiting_records[0].input_payload["mcp_tool_approval_lease_id"] == "lease-1"
    assert waiting_records[0].outputs["workflow_run_id"] == "run-1"
    assert waiting_records[0].outputs["mcp_tool_approval_lease_id"] == "lease-1"
    assert (
        waiting_records[0].resolved_config["reduced_context_prompt"]
        == "continue from the compact handoff"
    )
    assert waiting_records[0].resolved_config["agent_context_retry_enabled"] == "false"
    assert waiting_records[0].resolved_config["agent_context_retry_max_attempts"] == "0"
    assert waiting_records[0].resolved_config["agent_context_retry_target_chars"] == "4096"
    assert (
        waiting_records[0].input_payload["reduced_context_prompt"]
        == "continue from the compact handoff"
    )
    assert waiting_records[0].input_payload["agent_context_retry_enabled"] == "false"
    assert waiting_records[0].input_payload["agent_context_retry_max_attempts"] == "0"
    assert waiting_records[0].input_payload["agent_context_retry_target_chars"] == "4096"
    assert AgentPromptEffectProvider._context_retry_mcp_gateway_url(
        record=waiting_records[0],
        config=config,
        session_id="session-1",
    ) == (
        "http://gateway.test/mcp?"
        "session_id=session-1&workflow_run_id=run-1&mcp_tool_approval_lease_id=lease-1"
    )
    assert published_events[0][0] == "started"


def test_agent_enqueue_prompt_dispatch_hook_discards_cancelled_scheduled_wake_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={"execution_backend": "container_worker_vm", "target_name": "truman"},
        )
    )
    wake_store = WakeRequestStore(tmp_path / "wake-requests-store.json")
    wake_store.schedule_resume_request(
        session_id="session-1",
        prompt="resume scheduled work",
        schedule=WakeScheduleDefinition(
            resume_at=datetime.now(timezone.utc) + timedelta(minutes=5)
        ),
        request_id="wake-1",
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="dispatch-queued",
            request_id="wake-1",
            session_id="session-1",
            status="queued",
            resume_prompt=WakeResumePromptPayload(prompt="resume scheduled work"),
            resume_mode="same_context",
            dispatched_at=datetime.now(timezone.utc),
        )
    )
    cancellation = wake_store.cancel_request(
        "wake-1",
        caller_session_id="session-1",
        reason="obsolete wake",
    )
    assert cancellation.status == "cancelled"

    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="scheduled-wake:wake-1",
            target="session-1",
            vm_name="truman",
            session_id="session-1",
            prompt="resume scheduled work",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            resume_mode="same_context",
            effect_id="scheduled-wake:wake-1",
            metadata={"request_id": "wake-1", "wake_dispatch_id": "dispatch-queued"},
        )
    )
    backend = _ContainerDispatchBackendStub()
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    worker = SimpleNamespace(
        config=config,
        execution_store=execution_store,
        queued_agent_prompt_store=queue_store,
        agent_prompt_dispatch_port=object(),
        provider_config_resolver=SimpleNamespace(resolve_for_session_id=lambda session_id: config),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(
                    session_repository=session_repository,
                    wake_store=wake_store,
                )
            )
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: "done",
        _update_event_delivery_state=lambda event_id: None,
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 0
    assert queue_store.read_all() == {}
    assert backend.started_specs == []
    assert execution_store.read_all() == {}


def test_agent_enqueue_prompt_dispatch_hook_preserves_queued_container_agent_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id=None,
            allowed_tools=[ToolName.LIST_PENDING_AGENT_PROMPTS],
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "truman",
                "agent_provider": "claude",
                "wake_dispatch_id": "wake-dispatch-1",
            },
        )
    )
    queue_store = AgentPromptQueueStore(tmp_path / "queued-agent-prompts.json")
    queue_store.put(
        QueuedAgentPromptRecord(
            queue_key="pr-feedback:session-1:feedback-1",
            target="session-1",
            vm_name="truman",
            session_id="session-1",
            prompt="handle PR feedback",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            resume_mode="same_context",
            effect_id="effect-agent-enqueue-prompt-1",
            metadata={"wake_dispatch_id": "wake-dispatch-1"},
        )
    )
    backend = _ContainerDispatchBackendStub()
    monkeypatch.setattr(
        "evander.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    worker = SimpleNamespace(
        config=config,
        execution_store=execution_store,
        queued_agent_prompt_store=queue_store,
        agent_prompt_dispatch_port=object(),
        provider_config_resolver=SimpleNamespace(resolve_for_session_id=lambda session_id: config),
        provider_factory=SimpleNamespace(
            admin_service=SimpleNamespace(
                runtime=SimpleNamespace(session_repository=session_repository)
            )
        ),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: "started",
        _update_event_delivery_state=lambda event_id: None,
    )

    dispatched = AgentEnqueuePromptQueueDispatchHook().on_post_completion_dispatch(worker=worker)

    assert dispatched == 1
    command = str(backend.started_specs[0].command)
    assert "claude --continue -p" in command
    assert "claude-sonnet-4-6" in command
    assert "codex exec" not in command
    waiting_records = list(execution_store.read_all().values())
    assert waiting_records[0].resolved_config["agent_provider"] == "claude"
    assert waiting_records[0].resolved_config["model"] == "claude-sonnet-4-6"
    assert waiting_records[0].resolved_config["wake_dispatch_id"] == "wake-dispatch-1"
    assert waiting_records[0].outputs["wake_dispatch_id"] == "wake-dispatch-1"
    context = model_usage_store(tmp_path).get_active_correlation_context(
        session_id="session-1",
        dispatch_id=waiting_records[0].outputs["dispatch_id"],
    )
    assert context is not None
    assert context.agent_harness == "claude"


def test_agent_prompt_finalize_waiting_record_marks_wake_completion_from_wake_dispatch_id(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    wake_store = WakeRequestStore(runtime_dir / "wake-requests-store.json")
    completed_at = datetime.now(timezone.utc).replace(microsecond=0)
    scheduled_for = completed_at - timedelta(minutes=5)
    wake_store.schedule_resume_request(
        session_id="session-1",
        prompt="Resume work.",
        schedule=WakeScheduleDefinition(resume_at=scheduled_for),
        request_id="wake-1",
        source="test",
        resume_mode="same_context",
        enabled=True,
        metadata={},
    )
    wake_store.record_dispatch(
        WakeDispatchRecord(
            dispatch_id="wake-dispatch-1",
            request_id="wake-1",
            session_id="session-1",
            status="dispatched",
            resume_prompt=WakeResumePromptPayload(prompt="Resume work."),
            resume_mode="same_context",
            scheduled_for=scheduled_for,
            dispatched_at=scheduled_for + timedelta(seconds=5),
            source="scheduled_once",
        )
    )
    published_events: list[tuple[object, object]] = []
    worker = SimpleNamespace(
        config=GatewayConfig(runtime_dir=runtime_dir),
        runtime=SimpleNamespace(wake_store=wake_store),
        execution_store=SimpleNamespace(put=lambda record: None),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: (
            published_events.append((branch_port, outputs)) or "effect-completed"
        ),
        _update_event_delivery_state=lambda event_id: None,
    )
    record = SimpleNamespace(
        event_id="queued-agent-prompt:wake-dispatch-1",
        effect_type="effect:agent:enqueue-prompt",
        effect_id="effect-agent-enqueue-prompt-1",
        trace_id="trace-1",
        outputs={
            "dispatch_id": "effect-dispatch-123",
            "wake_dispatch_id": "wake-dispatch-1",
            "session_id": "session-1",
            "vm_name": "truman",
            "execution_backend": "container_worker_vm",
        },
    )
    queue_record = TriggerQueueRecord(
        event_id=(
            "__await-agent-completion__:effect-dispatch-123:"
            f"{completed_at.isoformat().replace('+00:00', 'Z')}"
        ),
        trigger_id="__await-agent-completion__",
        trigger_type="trigger:agent:run:completed",
        trace_id="trace-1",
        occurred_at=completed_at,
        payload={
            "dispatch_id": "effect-dispatch-123",
            "wake_dispatch_id": "wake-dispatch-1",
            "session_id": "session-1",
            "agent_state": "idle",
            "scope": {
                "directory_path": "root/team-a",
                "session_id": "session-1",
                "vm_name": None,
                "vm_user": "agent",
            },
        },
        delivery_state="delivered",
        delivered_at=completed_at,
        idempotency_key="__await-agent-completion__:effect-dispatch-123",
    )

    AgentPromptEffectProvider._finalize_waiting_record(
        worker=worker,
        record=record,
        queue_record=queue_record,
    )

    completed = wake_store.get_request("wake-1")
    assert completed is not None
    assert completed.completed_at is not None
    assert completed.completion_evidence["dispatch_id"] == "wake-dispatch-1"
    assert completed.completion_evidence["trigger_event_id"] == queue_record.event_id
    assert completed.completion_evidence["trigger_type"] == queue_record.trigger_type
    assert published_events
    assert published_events[0][1]["wake_dispatch_id"] == "wake-dispatch-1"


def test_agent_prompt_container_active_tool_watchdog_interrupts_only_stalled_tool(
    tmp_path: Path,
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
        container_worker_active_tool_timeout_seconds=10,
    )
    backend = _ContainerDispatchBackendStub()
    backend.config = config
    backend.status = SimpleNamespace(
        metadata={
            "active_tool": {
                "summary": "exec_command timeout --preserve-status 300 dotnet test",
                "started_at": (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat(),
            }
        }
    )

    AgentPromptEffectProvider._interrupt_stalled_container_active_tool_if_expired(
        backend=backend,
        status=backend.status,
        execution_id="agent-session-1-stalled-test",
    )

    assert backend.interrupted == [("agent-session-1-stalled-test", 10.0, "active_tool_timeout")]
    assert backend.stopped == []
    assert backend.destroyed == []


def test_agent_prompt_container_reconciliation_times_out_running_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
        container_worker_job_timeout_seconds=10,
    )
    backend = _ContainerDispatchBackendStub()
    backend.config = config
    execution_id = "agent-session-1-timeout"
    started_at = datetime.now(timezone.utc) - timedelta(seconds=45)
    backend.status = SimpleNamespace(
        status=ExecutionStatusKind.RUNNING,
        message=None,
        metadata={},
        started_at=started_at,
    )
    backend.logs = (
        "session id: 019de31a-8393-71d2-8990-e5f4fb0a4df2\n"
        "checking repo\n"
        "still waiting on dotnet test\n"
    )
    monkeypatch.setattr(
        "evander.effects.providers.agent_prompt.build_execution_backend",
        lambda config, admin_service: backend,
    )
    execution_store = SqliteExecutionStore(tmp_path / "orchestration.db")
    record = EffectExecutionRecord(
        execution_id="exec-timeout",
        idempotency_key="event-1::effect-1",
        event_id="event-1",
        trace_id="trace-1",
        source_node_id="trigger-1",
        source_trigger_type="trigger:cron",
        effect_id="effect-1",
        effect_type="effect:agent:prompt",
        status="waiting",
        started_at=started_at,
        finished_at=started_at,
        resolved_config_hash="hash",
        resolved_config={
            "session_id": "session-1",
            "directory_path": "root/team-a",
            "vm_user": "agent",
        },
        input_payload={},
        outputs={
            "dispatch_id": "dispatch-effect-1-abc123",
            "thread_id": "dispatch-effect-1-abc123",
            "success": True,
            "execution_backend": "container_worker_vm",
            "execution_id": execution_id,
            "session_id": "session-1",
        },
    )
    execution_store.put(record)
    published_outputs: list[dict[str, object]] = []
    delivery_updates: list[str] = []
    worker = SimpleNamespace(
        config=config,
        execution_store=execution_store,
        provider_config_resolver=SimpleNamespace(resolve_for_session_id=lambda session_id: config),
        provider_factory=SimpleNamespace(admin_service=None),
        _publish_completion_event_from_record=lambda record, outputs, branch_port=None: (
            published_outputs.append(outputs) or "effect-completed"
        ),
        _update_event_delivery_state=lambda event_id: delivery_updates.append(event_id),
    )

    reconciled = AgentPromptEffectProvider.reconcile_waiting(worker=worker)

    assert reconciled == 1
    assert backend.stopped == [execution_id]
    assert backend.destroyed == [execution_id]
    final_record = execution_store.get_execution("exec-timeout")
    assert final_record is not None
    assert final_record.status == "failed"
    assert final_record.published_event_id is None
    assert final_record.outputs["success"] is False
    assert final_record.outputs["worker_status"] == "timeout"
    assert "execution_id=agent-session-1-timeout" in str(final_record.error)
    assert "elapsed_seconds=" in str(final_record.error)
    assert "timeout_seconds=10.0" in str(final_record.error)
    assert "still waiting on dotnet test" in str(final_record.error)
    assert "still waiting on dotnet test" in final_record.outputs["logs"]
    assert final_record.outputs["thread_id"] == "019de31a-8393-71d2-8990-e5f4fb0a4df2"
    assert published_outputs == []
    assert delivery_updates == ["event-1"]


def test_agent_enqueue_prompt_queues_and_fails_when_immediate_resume_fails(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-1",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "list_vms",
        lambda include_inactive=True, vm_user="agent": [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="shut off",
                agent_state="stopped",
                session_ids=[],
            )
        ],
    )
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "resume_agent",
        lambda **kwargs: type(
            "Result",
            (),
            {"success": False, "thread_id": "thread-old", "message": "vm-start-failed"},
        )(),
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-enqueue-fail-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-enqueue-fail-1",
        )
    )

    for _ in range(3):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    only = next(iter(effect_payload.values()))
    assert only["status"] == "failed"
    assert "queued prompt for retry" in only["error"]
    assert only["outputs"]["success"] is False
    assert only["outputs"]["queued"] is True
    assert only["outputs"]["queue_key"] == "vm-1"
    assert only["outputs"]["vm_name"] == "vm-1"
    assert only["outputs"]["coalesced_count"] == 1
    assert only["outputs"]["error_class"] == "RuntimeError"
    assert only["outputs"]["failed_phase"] == "dispatch"
    assert "queued prompt for retry" in only["outputs"]["error_message"]
    queued_payload = json.loads(
        (runtime_dir / "queued-agent-prompts.json").read_text(encoding="utf-8")
    )
    assert queued_payload["vm-1"]["prompt"] == "hello"


def test_agent_enqueue_prompt_routes_retry_context_to_error_branch(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Enqueue",
                    "type": "effect:agent:enqueue-prompt",
                    "vm_name": "vm-1",
                    "prompt": "hello",
                },
                {
                    "id": "effect-error",
                    "display_name": "Publish retry context",
                    "type": "effect:event:emit",
                    "event_type": "prompt.error",
                    "payload": {
                        "queue_key": "${{effect-1.queue_key}}",
                        "coalesced_count": "${{effect-1.coalesced_count}}",
                        "queued": "${{effect-1.queued}}",
                    },
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "effect-error", source_port="error"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "list_vms",
        lambda include_inactive=True, vm_user="agent": [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="shut off",
                agent_state="stopped",
                session_ids=[],
            )
        ],
    )
    monkeypatch.setattr(
        worker.provider_factory.admin_service.vm_agent_admin,
        "resume_agent",
        lambda **kwargs: type(
            "Result",
            (),
            {"success": False, "thread_id": "thread-old", "message": "vm-start-failed"},
        )(),
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-enqueue-error-route-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 7, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-enqueue-error-route-1",
        )
    )

    for _ in range(4):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    enqueue_record = by_effect["effect-1"]
    downstream_record = by_effect["effect-error"]

    assert enqueue_record["status"] == "error_routed"
    assert enqueue_record["completion_event_branch_port"] == "error"
    assert enqueue_record["outputs"]["success"] is False
    assert enqueue_record["outputs"]["queued"] is True
    assert enqueue_record["outputs"]["queue_key"] == "vm-1"
    assert enqueue_record["outputs"]["coalesced_count"] == 1
    assert enqueue_record["outputs"]["vm_name"] == "vm-1"
    assert enqueue_record["outputs"]["error_class"] == "RuntimeError"
    assert enqueue_record["outputs"]["failed_phase"] == "dispatch"
    assert "queued prompt for retry" in enqueue_record["outputs"]["error_message"]

    assert downstream_record["status"] == "succeeded"
    assert downstream_record["resolved_config"]["payload"] == {
        "queue_key": "vm-1",
        "coalesced_count": 1,
        "queued": True,
    }


def test_agent_enqueue_prompt_queue_dispatch_hook_is_neutral_when_provider_absent(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "vm_name": "vm-1",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]

    worker.queued_agent_prompt_store.put(
        QueuedAgentPromptRecord(
            queue_key="vm-1",
            target="vm-1",
            vm_name="vm-1",
            session_id=None,
            prompt="queued",
            model=None,
            vm_user="agent",
            resume_thread_id=None,
            effect_id="effect-enqueue",
        )
    )
    resume_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker.provider_factory.admin_service,
        "resume_agent",
        lambda **kwargs: (
            resume_calls.append(kwargs)
            or type("Result", (), {"success": True, "thread_id": "thread-queued"})()
        ),
    )

    worker.run_once()

    assert resume_calls == []
    queued_payload = json.loads(
        (runtime_dir / "queued-agent-prompts.json").read_text(encoding="utf-8")
    )
    assert "vm-1" in queued_payload


def test_agent_prompt_fails_when_resume_agent_reports_unsuccessful_result(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "vm_name": "vm-1",
                    "prompt": "hello",
                }
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        worker.provider_factory.admin_service,
        "resume_agent",
        lambda **kwargs: type(
            "Result",
            (),
            {"success": False, "thread_id": "thread-old", "message": "vm-start-failed"},
        )(),
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-prompt-fail-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 10, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-prompt-fail-1",
        )
    )

    for _ in range(3):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    only = next(iter(effect_payload.values()))
    assert only["status"] == "failed"
    assert "resume_agent failed" in only["error"]
    assert only["outputs"]["success"] is False
    assert only["outputs"]["dispatch_id"].startswith("dispatch-effect-1-")
    assert only["outputs"]["vm_name"] == "vm-1"
    assert only["outputs"]["error_class"] == "RuntimeError"
    assert only["outputs"]["failed_phase"] == "dispatch"
    assert "resume_agent failed" in only["outputs"]["error_message"]

    queue_payload = trigger_queue_payload(worker_config)
    assert all(item["trigger_id"] != "effect-1" for item in queue_payload.values())


def test_agent_prompt_routes_failed_outputs_to_error_branch(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-prompt",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "vm_name": "vm-1",
                    "prompt": "hello",
                },
                {
                    "id": "effect-error",
                    "display_name": "Error route",
                    "type": "effect:session:update-metadata",
                    "target": "session-1",
                    "metadata": {"handled": True},
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-prompt"),
                _edge("effect-prompt", "effect-error", source_port="error"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    worker.provider_factory = _PromptErrorRoutingFactory(
        {"effect-error": {"success": True, "handled": True}}
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-prompt-error-route-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 10, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-prompt-error-route-1",
        )
    )

    for _ in range(4):
        worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in execution_payload.values()}

    prompt_record = by_effect["effect-prompt"]
    downstream_record = by_effect["effect-error"]

    assert prompt_record["status"] == "error_routed"
    assert prompt_record["completion_event_branch_port"] == "error"
    assert prompt_record["published_event_id"] is not None
    assert prompt_record["completion_event_enqueued_at"] is not None
    assert prompt_record["error"] == "checked provider boom"
    assert prompt_record["outputs"]["success"] is False
    assert prompt_record["outputs"]["status_code"] == 502
    assert prompt_record["outputs"]["body_text"] == "bad gateway"
    assert prompt_record["outputs"]["error_message"] == "checked provider boom"
    assert prompt_record["outputs"]["error_class"] == "EffectProviderExecutionError"
    assert prompt_record["outputs"]["failed_phase"] == "dispatch"

    assert downstream_record["status"] == "succeeded"
    assert downstream_record["outputs"]["success"] is True
    assert downstream_record["outputs"]["handled"] is True

    queue_payload = trigger_queue_payload(worker_config)
    error_branch_events = [
        item for item in queue_payload.values() if item["trigger_id"] == "effect-prompt"
    ]
    assert len(error_branch_events) == 1
    assert error_branch_events[0]["payload"]["__branch_port"] == "error"


def test_agent_prompt_error_branch_publish_failure_fails_loudly(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-prompt",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "vm_name": "vm-1",
                    "prompt": "hello",
                },
                {
                    "id": "effect-error",
                    "display_name": "Error route",
                    "type": "effect:session:update-metadata",
                    "target": "session-1",
                    "metadata": {"handled": True},
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-prompt"),
                _edge("effect-prompt", "effect-error", source_port="error"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    worker.provider_factory = _PromptErrorRoutingFactory(
        {"effect-error": {"success": True, "handled": True}}
    )
    original_publish = worker.event_queue.publish

    def publish(event: TriggerEvent, *, idempotency_key: str | None = None):
        if event.trigger_id == "effect-prompt":
            raise RuntimeError("error branch publish boom")
        return original_publish(event, idempotency_key=idempotency_key)

    worker.event_queue.publish = publish  # type: ignore[assignment]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-prompt-error-publish-failure-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 15, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-prompt-error-publish-failure-1",
        )
    )

    worker.run_once()

    execution_payload = effect_execution_payload(worker_config)
    prompt_record = next(
        record for record in execution_payload.values() if record["effect_id"] == "effect-prompt"
    )

    assert prompt_record["status"] == "failed"
    assert prompt_record["completion_pending_status"] is None
    assert prompt_record["completion_event_branch_port"] is None
    assert prompt_record["published_event_id"] is None
    assert prompt_record["completion_event_enqueued_at"] is None
    assert prompt_record["error"] == "error branch publish boom"
    assert prompt_record["outputs"]["success"] is False
    assert prompt_record["outputs"]["error_message"] == "checked provider boom"
    assert prompt_record["outputs"]["error_class"] == "EffectProviderExecutionError"

    queue_payload = trigger_queue_payload(worker_config)
    assert all(item["trigger_id"] != "effect-prompt" for item in queue_payload.values())


def test_filter_gate_when_drops_payload_when_predicate_is_false(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "gate-filter",
                    "display_name": "Create succeeded",
                    "type": "gate:filter",
                    "outputs": {
                        "session_id": {"type": "string"},
                        "vm_name": {"type": "string"},
                        "labels": {"type": "object"},
                        "success": {"type": "boolean"},
                        "message": {"type": "string"},
                    },
                    "when": {"all": [{"left": "${{effect-1.success}}", "equals": True}]},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Create VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-1",
                },
                {
                    "id": "effect-2",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "${{effect-1.session_id}}",
                    "prompt": "Continue ${{effect-1.session_id}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "gate-filter"),
                _edge("gate-filter", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {"session_id": "session-123", "success": False},
            "effect-2": {"thread_id": "thread-1", "success": True},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-when-drop",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-when-drop",
        )
    )

    for _ in range(6):
        worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "dropped"

    effect_payload = effect_execution_payload(worker_config)
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1"}


def test_vm_copy_files_template_source_renders_bindings_before_dispatch(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    template_path = tmp_path / "guest-AGENTS.md.tmpl"
    template_path.write_text(
        "VM: ${{effect-1.vm_name}}\nIssue: ${{trigger-1.ticket.issue_number}}\nStarted: ${{system.execution.started_at_compact}}\n",
        encoding="utf-8",
    )
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Ticket created",
                    "type": "trigger:ticket:created",
                    "directories": ["root/team-a"],
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Create VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-issue-${{trigger-1.ticket.issue_number}}",
                },
                {
                    "id": "effect-2",
                    "display_name": "Copy files",
                    "type": "effect:vm:copy-files",
                    "target": "${{effect-1.session_id}}",
                    "allowed_source_roots": [str(tmp_path)],
                    "files": [
                        {
                            "source_path": str(template_path),
                            "source_format": "template",
                            "destination_path": "/home/agent/AGENTS.md",
                            "overwrite": True,
                        }
                    ],
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]

    class _VmFileCopierStub:
        def vm_copy_files(self, **kwargs):
            return {"success": True, "action": "vm_copy_files", **kwargs}

    class _TemplateAwareFactory:
        def build(self, effect):
            if effect.id == "effect-2":
                provider = VmCopyFilesEffectProvider()
                provider.initialize(
                    effect_definition=effect,
                    runtime_context=EffectRuntimeContext(
                        vm_file_copier=_VmFileCopierStub(),
                        metadata={"directory_path": "root/team-a"},
                    ),
                )
                return provider
            return _Provider(
                {"session_id": "session-123", "vm_name": "vm-issue-14084", "success": True}
            )

    worker.provider_factory = _TemplateAwareFactory()

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-template-copy",
            trigger_id="trigger-1",
            trigger_type="trigger:ticket:created",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket": {"issue_number": "14084"}},
            trace_id="trace-template-copy",
        )
    )

    for _ in range(6):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    copy_record = next(item for item in effect_payload.values() if item["effect_id"] == "effect-2")
    rendered_file = copy_record["resolved_config"]["files"][0]
    assert "source_path" not in rendered_file
    assert rendered_file["content"].startswith("VM: vm-issue-14084\nIssue: 14084\nStarted: 2026")
    assert rendered_file["destination_path"] == "/home/agent/AGENTS.md"


def test_await_agent_gate_releases_follow_up_on_matching_completion(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                },
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-2",
                    "display_name": "Post exec",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Write post-exec summary for ${{await-agent-1.vm_name}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
                _edge("await-agent-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1-abc123",
                "thread_id": "thread-1",
                "success": True,
            },
            "effect-2": {
                "thread_id": "thread-2",
                "success": True,
            },
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-await-start",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-await",
        )
    )

    for _ in range(4):
        worker.run_once()

    before_completion = effect_execution_payload(worker_config)
    assert {item["effect_id"] for item in before_completion.values()} == {"effect-1"}

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-agent-complete",
            trigger_id="agent-complete",
            trigger_type="trigger:agent:run:completed",
            occurred_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
            payload={
                "dispatch_id": "dispatch-effect-1-abc123",
                "vm_name": "vm-issue-42",
                "session_ids": ["session-42"],
                "labels": {},
                "directory_paths": ["root/team-a"],
                "agent_repo_names": ["repo-a"],
                "libvirt_state": "running",
                "agent_state": "idle",
                "previous_agent_state": "running",
                "transition": {"from": "running", "to": "idle"},
                "last_state_update_at": "2026-04-04T12:05:00Z",
                "scope": {
                    "directory_path": "root/team-a",
                    "vm_name": "vm-issue-42",
                    "session_id": "session-42",
                    "vm_user": "agent",
                },
                "ip_address": "192.0.2.10",
                "vm": {"vm_name": "vm-issue-42"},
            },
            trace_id="trace-await",
        )
    )

    for _ in range(6):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}

    gate_payload = gate_execution_payload(worker_config)
    assert any(
        item["gate_id"] == "await-agent-1" and item["status"] == "succeeded"
        for item in gate_payload.values()
    )


def test_await_agent_recorded_failure_routes_to_error_branch(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-prompt",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-recover",
                    "display_name": "Record await failure",
                    "type": "effect:session:update-metadata",
                    "target": "session-42",
                    "metadata": {"await_failure_handled": True},
                },
            ],
            "edges": [
                _edge("effect-prompt", "await-agent-1", source_port="started"),
                _edge("await-agent-1", "effect-recover", source_port="error"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(  # type: ignore[arg-type]
        config=config,
        worker_config=worker_config,
        runtime=None,
    )
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    worker.provider_factory = _Factory({"effect-recover": {"success": True}})
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-await-missing-dispatch",
            trigger_id="effect-prompt",
            trigger_type="effect:agent:prompt",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"__branch_port": "started"},
            trace_id="trace-await-missing-dispatch",
        )
    )

    for _ in range(3):
        worker.run_once()

    gate_record = next(iter(gate_execution_payload(worker_config).values()))
    assert gate_record["status"] == "error_routed"
    assert gate_record["completion_event_branch_port"] == "error"
    assert gate_record["error"] == ("gate:await-agent requires upstream effect output dispatch_id")
    assert gate_record["outputs"] == {
        "success": False,
        "error_message": "gate:await-agent requires upstream effect output dispatch_id",
        "error_class": "GateRuntimeError",
    }
    recovery_record = next(iter(effect_execution_payload(worker_config).values()))
    assert recovery_record["effect_id"] == "effect-recover"
    assert recovery_record["status"] == "succeeded"


def test_await_agent_recorded_failure_routes_to_error_branch_on_replay(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-prompt",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-recover",
                    "display_name": "Record await failure",
                    "type": "effect:session:update-metadata",
                    "target": "session-42",
                    "metadata": {"await_failure_handled": True},
                },
            ],
            "edges": [
                _edge("effect-prompt", "await-agent-1", source_port="started"),
                _edge("await-agent-1", "effect-recover", source_port="error"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(  # type: ignore[arg-type]
        config=config,
        worker_config=worker_config,
        runtime=None,
    )
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    worker.provider_factory = _Factory({"effect-recover": {"success": True}})
    occurred_at = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-await-failed-before-route",
            trigger_id="effect-prompt",
            trigger_type="effect:agent:prompt",
            occurred_at=occurred_at,
            payload={"__branch_port": "started"},
            trace_id="trace-await-failed-before-route",
        )
    )
    idempotency_key = "event-await-failed-before-route::await-agent-1"
    worker.gate_execution_store.record_execution(
        GateExecutionRecord(
            execution_id="gate-await-failed-before-route",
            idempotency_key=idempotency_key,
            event_id="event-await-failed-before-route",
            trace_id="trace-await-failed-before-route",
            source_node_id="effect-prompt",
            source_trigger_type="effect:agent:prompt",
            gate_id="await-agent-1",
            gate_type="gate:await-agent",
            status="failed",
            started_at=occurred_at,
            finished_at=occurred_at,
            resolved_config_hash="failed-await-config",
            resolved_config={"session_id": "session-42"},
            input_payload={"__branch_port": "started"},
            outputs={},
            inputs_by_source={"effect-prompt": {"__branch_port": "started"}},
            input_event_ids_by_source={"effect-prompt": "event-await-failed-before-route"},
            input_occurred_at_by_source={"effect-prompt": occurred_at},
            error="gate:await-agent requires upstream effect output dispatch_id",
        )
    )
    original_publish = worker.event_queue.publish
    persisted_statuses: list[str] = []

    def publish(event: TriggerEvent, *, idempotency_key: str | None = None):
        if event.trigger_id == "await-agent-1":
            persisted = worker.gate_execution_store.find_execution_by_idempotency_key(
                "event-await-failed-before-route::await-agent-1"
            )
            assert persisted is not None
            persisted_statuses.append(persisted.status)
        return original_publish(event, idempotency_key=idempotency_key)

    worker.event_queue.publish = publish  # type: ignore[assignment]

    for _ in range(3):
        worker.run_once()

    assert persisted_statuses == ["completion_pending"]
    gate_record = worker.gate_execution_store.find_execution_by_idempotency_key(idempotency_key)
    assert gate_record is not None
    assert gate_record.status == "error_routed"
    assert gate_record.completion_event_branch_port == "error"
    assert gate_record.outputs == {
        "success": False,
        "error_message": "gate:await-agent requires upstream effect output dispatch_id",
        "error_class": "GateRuntimeError",
    }
    recovery_record = next(iter(effect_execution_payload(worker_config).values()))
    assert recovery_record["effect_id"] == "effect-recover"
    assert recovery_record["status"] == "succeeded"


def test_await_agent_gate_releases_follow_up_from_in_process_vm_poll(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                },
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-2",
                    "display_name": "Post exec",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Write post-exec summary for ${{await-agent-1.vm_name}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
                _edge("await-agent-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1-abc123",
                "thread_id": "thread-1",
                "success": True,
            },
            "effect-2": {
                "thread_id": "thread-2",
                "success": True,
            },
        }
    )
    worker.agent_run_status_port = _AgentRunStatusPortStub([])

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-await-start",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-await",
        )
    )

    for _ in range(4):
        worker.run_once()

    pending = _await_agent_store(worker).get("await-agent-1:dispatch-effect-1-abc123")
    assert pending is not None
    pending.session_id = "session-42"
    pending.vm_name = "vm-issue-42"
    pending.payload["__node_outputs"]["effect-vm-create-1"] = {
        "session_id": "session-42",
        "vm_name": "vm-issue-42",
    }
    _await_agent_store(worker).put("await-agent-1:dispatch-effect-1-abc123", pending)

    worker.agent_run_status_port.summaries = [
        VmSummary(
            vm_name="vm-issue-42",
            session_ids=["session-42"],
            labels={},
            directory_paths=["root/team-a"],
            libvirt_state="running",
            ip_address="192.0.2.10",
            agent_state="idle",
            agent_repo_names=["repo-a"],
            last_state_update_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
            dispatch_id="dispatch-effect-1-abc123",
        )
    ]

    for _ in range(6):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}

    gate_payload = gate_execution_payload(worker_config)
    assert any(
        item["gate_id"] == "await-agent-1" and item["status"] == "succeeded"
        for item in gate_payload.values()
    )


@pytest.mark.parametrize("libvirt_state", ["shut off", "stopped"])
def test_await_agent_gate_releases_follow_up_from_in_process_vm_poll_for_same_dispatch_idle_non_running_vm(
    tmp_path: Path, monkeypatch, libvirt_state: str
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                },
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-2",
                    "display_name": "Post exec",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Write post-exec summary for ${{await-agent-1.vm_name}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
                _edge("await-agent-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1-abc123",
                "thread_id": "thread-1",
                "success": True,
            },
            "effect-2": {
                "thread_id": "thread-2",
                "success": True,
            },
        }
    )
    worker.agent_run_status_port = _AgentRunStatusPortStub([])

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-await-start",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-await",
        )
    )

    for _ in range(4):
        worker.run_once()

    pending_key = "await-agent-1:dispatch-effect-1-abc123"
    pending = _await_agent_store(worker).get(pending_key)
    assert pending is not None
    pending.session_id = "session-42"
    pending.vm_name = "vm-issue-42"
    pending.payload["__node_outputs"]["effect-vm-create-1"] = {
        "session_id": "session-42",
        "vm_name": "vm-issue-42",
    }
    _await_agent_store(worker).put(pending_key, pending)

    worker.agent_run_status_port.summaries = [
        VmSummary(
            vm_name="vm-issue-42",
            session_ids=["session-42"],
            labels={},
            directory_paths=["root/team-a"],
            libvirt_state=libvirt_state,
            ip_address="192.0.2.10",
            agent_state="idle",
            agent_repo_names=["repo-a"],
            last_state_update_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
            dispatch_id="dispatch-effect-1-abc123",
        )
    ]

    for _ in range(6):
        worker.run_once()

    assert pending_key not in _await_agent_store(worker).read_all()

    effect_payload = effect_execution_payload(worker_config)
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}

    gate_payload = gate_execution_payload(worker_config)
    assert any(
        item["gate_id"] == "await-agent-1" and item["status"] == "succeeded"
        for item in gate_payload.values()
    )


def test_await_agent_gate_preserves_upstream_node_outputs_for_follow_up_bindings(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                },
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-2",
                    "display_name": "Post exec",
                    "type": "effect:agent:prompt",
                    "session_id": "${{effect-vm-create-1.session_id}}",
                    "prompt": "Write post-exec summary for ${{effect-vm-create-1.vm_name}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
                _edge("await-agent-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1-abc123",
                "thread_id": "thread-1",
                "success": True,
            },
            "effect-2": {
                "thread_id": "thread-2",
                "success": True,
            },
        }
    )
    monkeypatch.setattr(worker, "_list_vm_summaries_for_await_agent_reconciliation", lambda: [])

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-await-start",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-await",
        )
    )

    for _ in range(4):
        worker.run_once()

    pending = _await_agent_store(worker).get("await-agent-1:dispatch-effect-1-abc123")
    assert pending is not None
    pending.session_id = "session-42"
    pending.vm_name = "vm-issue-42"
    pending.payload["__node_outputs"]["effect-vm-create-1"] = {
        "session_id": "session-42",
        "vm_name": "vm-issue-42",
    }
    _await_agent_store(worker).put("await-agent-1:dispatch-effect-1-abc123", pending)

    monkeypatch.setattr(
        worker,
        "_list_vm_summaries_for_await_agent_reconciliation",
        lambda: [
            VmSummary(
                vm_name="vm-issue-42",
                session_ids=["session-42"],
                labels={},
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.10",
                agent_state="idle",
                agent_repo_names=["repo-a"],
                last_state_update_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
                dispatch_id="dispatch-effect-1-abc123",
            )
        ],
    )

    for _ in range(6):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-2"]["resolved_config"]["session_id"] == "session-42"
    assert (
        by_effect["effect-2"]["resolved_config"]["prompt"]
        == "Write post-exec summary for vm-issue-42"
    )


def test_force_complete_await_agent_releases_follow_up(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                },
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-2",
                    "display_name": "Post exec",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Write post-exec summary for ${{await-agent-1.vm_name}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
                _edge("await-agent-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1-abc123",
                "thread_id": "thread-1",
                "success": True,
            },
            "effect-2": {
                "thread_id": "thread-2",
                "success": True,
            },
        }
    )
    monkeypatch.setattr(worker, "_list_vm_summaries_for_await_agent_reconciliation", lambda: [])

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-await-start",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-await",
        )
    )

    for _ in range(4):
        worker.run_once()

    pending = _await_agent_store(worker).get("await-agent-1:dispatch-effect-1-abc123")
    assert pending is not None
    pending.session_id = "session-42"
    pending.vm_name = "vm-issue-42"
    _await_agent_store(worker).put("await-agent-1:dispatch-effect-1-abc123", pending)
    monkeypatch.setattr(
        worker,
        "_list_vm_summaries_for_await_agent_reconciliation",
        lambda: [
            VmSummary(
                vm_name="vm-issue-42",
                session_ids=["session-42"],
                labels={},
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.10",
                agent_state="idle",
                agent_repo_names=["repo-a"],
                last_state_update_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
                dispatch_id="dispatch-effect-1-abc123",
            )
        ],
    )

    assert worker.force_complete_await_agent("dispatch-effect-1-abc123") is True

    effect_payload = effect_execution_payload(worker_config)
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}


class _IndexedOnlyExecutionStore:
    def __init__(self, records: list[object]) -> None:
        self.records = records

    def list_waiting_records(self, effect_type: str | None = None) -> list[object]:
        normalized_type = str(effect_type or "").strip()
        return [
            record
            for record in self.records
            if str(getattr(record, "status", "") or "").strip() == "waiting"
            and (
                not normalized_type
                or str(getattr(record, "effect_type", "") or "").strip() == normalized_type
            )
        ]

    def read_all(self) -> dict[str, object]:
        raise AssertionError("hot-path reconciliation should not call read_all")


class _ActiveIndexedExecutionStore(_IndexedOnlyExecutionStore):
    def __init__(self, records: list[object]) -> None:
        super().__init__(records)
        self.active_list_calls = 0

    def list_active_records(self) -> list[object]:
        self.active_list_calls += 1
        return list(self.records)

    def list_executions(self) -> list[object]:
        raise AssertionError("queued-prompt activity lookup should not scan full history")


def test_agent_prompt_waiting_reconciliation_uses_indexed_store_method() -> None:
    worker = SimpleNamespace(execution_store=_IndexedOnlyExecutionStore([]))

    assert AgentPromptEffectProvider.reconcile_waiting(worker=worker) == 0
    assert AgentEnqueuePromptEffectProvider.reconcile_waiting(worker=worker) == 0


def test_enqueue_prompt_queue_dispatch_checks_waiting_records_without_full_scan() -> None:
    waiting_record = SimpleNamespace(
        effect_type="effect:agent:enqueue-prompt",
        status="waiting",
        outputs={"session_id": "session-1"},
        resolved_config={},
        input_payload={},
    )
    worker = SimpleNamespace(
        execution_store=_IndexedOnlyExecutionStore([waiting_record]),
    )
    queued_record = SimpleNamespace(session_id="session-1", vm_name="", target="")

    assert (
        AgentEnqueuePromptQueueDispatchHook()._has_waiting_enqueue_prompt_for_record(
            worker=worker,
            record=queued_record,
        )
        is True
    )


def test_enqueue_prompt_queue_dispatch_prefers_active_records_over_full_history() -> None:
    waiting_record = SimpleNamespace(
        effect_type="effect:agent:enqueue-prompt",
        status="waiting",
        outputs={"session_id": "session-1"},
        resolved_config={},
        input_payload={},
    )
    execution_store = _ActiveIndexedExecutionStore([waiting_record])
    worker = SimpleNamespace(execution_store=execution_store)
    queued_record = SimpleNamespace(session_id="session-1", vm_name="", target="")

    assert (
        AgentEnqueuePromptQueueDispatchHook()._has_waiting_enqueue_prompt_for_record(
            worker=worker,
            record=queued_record,
        )
        is True
    )
    assert execution_store.active_list_calls == 1


def test_waiting_agent_prompt_reconciliation_completes_from_idle_matching_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-2",
                    "display_name": "Post exec",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Write post-exec summary",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "effect-2", source_port="completed"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1-abc123",
                "thread_id": "thread-1",
                "success": True,
            },
            "effect-2": {
                "thread_id": "thread-2",
                "success": True,
            },
        }
    )
    worker.agent_run_status_port = _AgentRunStatusPortStub([])

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-waiting-start",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-waiting",
        )
    )

    for _ in range(4):
        worker.run_once()

    worker.agent_run_status_port.summaries = [
        VmSummary(
            vm_name="vm-issue-42",
            session_ids=["session-42"],
            labels={},
            directory_paths=["root/team-a"],
            libvirt_state="running",
            ip_address="192.0.2.10",
            agent_state="idle",
            agent_repo_names=["repo-a"],
            last_state_update_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
            dispatch_id="dispatch-effect-1-abc123",
        )
    ]

    for _ in range(6):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-1"]["published_event_id"] is not None
    assert by_effect["effect-2"]["status"] == "succeeded"


def test_waiting_agent_prompt_reconciliation_completes_idle_summary_without_dispatch_id(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-2",
                    "display_name": "Post exec",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Write post-exec summary",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "effect-2", source_port="completed"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1-abc123",
                "thread_id": "thread-1",
                "success": True,
            },
            "effect-2": {
                "thread_id": "thread-2",
                "success": True,
            },
        }
    )
    monkeypatch.setattr(worker, "_list_vm_summaries_for_await_agent_reconciliation", lambda: [])

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-waiting-start",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-waiting",
        )
    )

    for _ in range(4):
        worker.run_once()

    effect_record = next(
        record
        for record in worker.execution_store.read_all().values()
        if record.effect_id == "effect-1"
    )
    effect_record.finished_at = datetime(2026, 4, 4, 11, 50, tzinfo=timezone.utc)
    worker.execution_store.put(effect_record)

    monkeypatch.setattr(
        worker,
        "_list_vm_summaries_for_await_agent_reconciliation",
        lambda: [
            VmSummary(
                vm_name="vm-issue-42",
                session_ids=["session-42"],
                labels={},
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.10",
                agent_state="idle",
                agent_repo_names=["repo-a"],
                last_state_update_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
                dispatch_id=None,
            )
        ],
    )

    for _ in range(6):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"


def test_waiting_agent_prompt_reconciliation_skips_and_retries_when_inventory_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    execution_store = SqliteExecutionStore(runtime_dir / "orchestration.db")
    record = EffectExecutionRecord(
        execution_id="exec-waiting",
        idempotency_key="event-1::effect-1",
        event_id="event-1",
        trace_id="trace-1",
        source_node_id="trigger-1",
        source_trigger_type="trigger:cron",
        effect_id="effect-1",
        effect_type="effect:agent:prompt",
        status="waiting",
        started_at=datetime(2026, 4, 4, 11, 55, tzinfo=timezone.utc),
        finished_at=datetime(2026, 4, 4, 11, 55, tzinfo=timezone.utc),
        resolved_config_hash="hash",
        resolved_config={
            "session_id": "session-42",
            "directory_path": "root/team-a",
            "vm_user": "agent",
        },
        input_payload={},
        outputs={
            "dispatch_id": "dispatch-effect-1-abc123",
            "thread_id": "thread-1",
            "success": True,
        },
    )
    execution_store.put(record)
    worker = SimpleNamespace(
        execution_store=execution_store,
        agent_run_status_port=_FailingAgentRunStatusPortStub(),
    )

    caplog.set_level(logging.ERROR, logger="evander.effects.providers.agent_prompt")

    reconciled = AgentPromptEffectProvider.reconcile_waiting(worker=worker)

    assert reconciled == 0
    stored_record = execution_store.get_execution("exec-waiting")
    assert stored_record is not None
    assert stored_record.status == "waiting"
    assert stored_record.outputs["dispatch_id"] == "dispatch-effect-1-abc123"
    assert "AgentPromptEffectProvider" in caplog.text
    assert "phase=waiting_reconciliation" in caplog.text
    assert "waiting_vm_records=1" in caplog.text
    assert "inventory reconciliation skipped" in caplog.text
    assert "virsh list failed" in caplog.text


def test_await_agent_gate_releases_when_settled_summary_lacks_dispatch_id(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                },
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                },
                {
                    "id": "effect-2",
                    "display_name": "Post exec",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Write post-exec summary for ${{await-agent-1.vm_name}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
                _edge("await-agent-1", "effect-2"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {
                "dispatch_id": "dispatch-effect-1-abc123",
                "thread_id": "thread-1",
                "success": True,
            },
            "effect-2": {
                "thread_id": "thread-2",
                "success": True,
            },
        }
    )
    monkeypatch.setattr(worker, "_list_vm_summaries_for_await_agent_reconciliation", lambda: [])

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-await-start",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
            trace_id="trace-await",
        )
    )

    for _ in range(4):
        worker.run_once()

    pending = _await_agent_store(worker).get("await-agent-1:dispatch-effect-1-abc123")
    assert pending is not None
    pending.session_id = "session-42"
    pending.vm_name = "vm-issue-42"
    _await_agent_store(worker).put("await-agent-1:dispatch-effect-1-abc123", pending)
    monkeypatch.setattr(
        worker,
        "_list_vm_summaries_for_await_agent_reconciliation",
        lambda: [
            VmSummary(
                vm_name="vm-issue-42",
                session_ids=["session-42"],
                labels={},
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.10",
                agent_state="idle",
                agent_repo_names=["repo-a"],
                last_state_update_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
                dispatch_id=None,
            )
        ],
    )

    for _ in range(6):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}


def test_await_agent_reconciliation_completes_idle_pending_record_without_dispatch_id(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                }
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    pending = AwaitAgentPendingRecord(
        gate_id="await-agent-1",
        dispatch_id="dispatch-old",
        prompt_event_id="effect:effect-agent-prompt-1:old",
        prompt_source_node_id="effect-agent-prompt-1",
        trace_id="trace-old",
        session_id="session-42",
        vm_name="vm-issue-42",
        payload={},
        occurred_at=datetime(2026, 4, 4, 11, 50, tzinfo=timezone.utc),
    )
    _await_agent_store(worker).put("await-agent-1:dispatch-old", pending)
    monkeypatch.setattr(
        worker,
        "_list_vm_summaries_for_await_agent_reconciliation",
        lambda: [
            VmSummary(
                vm_name="vm-issue-42",
                session_ids=["session-42"],
                labels={},
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.10",
                agent_state="idle",
                last_state_update_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
                dispatch_id=None,
            )
        ],
    )

    worker.run_once()

    assert _await_agent_store(worker).read_all() == {}


def test_await_agent_reconciliation_keeps_pending_record_without_matching_summary(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-missing",
                    "vm_name": "vm-issue-missing",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-missing",
                    "prompt": "Continue main task",
                }
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    pending = AwaitAgentPendingRecord(
        gate_id="await-agent-1",
        dispatch_id="dispatch-missing",
        prompt_event_id="effect:effect-agent-prompt-1:missing",
        prompt_source_node_id="effect-agent-prompt-1",
        trace_id="trace-missing",
        session_id="session-missing",
        vm_name="vm-issue-missing",
        payload={},
        occurred_at=datetime(2026, 4, 4, 11, 50, tzinfo=timezone.utc),
    )
    _await_agent_store(worker).put("await-agent-1:dispatch-missing", pending)
    monkeypatch.setattr(worker, "_list_vm_summaries_for_await_agent_reconciliation", lambda: [])

    worker.run_once()

    assert _await_agent_store(worker).read_all() == {"await-agent-1:dispatch-missing": pending}


def test_await_agent_reconciliation_deletes_pending_record_for_replaced_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                }
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    pending = AwaitAgentPendingRecord(
        gate_id="await-agent-1",
        dispatch_id="dispatch-old",
        prompt_event_id="effect:effect-agent-prompt-1:old",
        prompt_source_node_id="effect-agent-prompt-1",
        trace_id="trace-old",
        session_id="session-42",
        vm_name="vm-issue-42",
        payload={},
        occurred_at=datetime(2026, 4, 4, 11, 50, tzinfo=timezone.utc),
    )
    pending_key = "await-agent-1:dispatch-old"
    _await_agent_store(worker).put(pending_key, pending)
    monkeypatch.setattr(
        worker,
        "_list_vm_summaries_for_await_agent_reconciliation",
        lambda: [
            VmSummary(
                vm_name="vm-issue-42",
                session_ids=["session-42"],
                labels={},
                directory_paths=["root/team-a"],
                libvirt_state="shut off",
                ip_address="192.0.2.10",
                agent_state="running",
                agent_repo_names=["repo-a"],
                last_state_update_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
                dispatch_id="dispatch-new",
            )
        ],
    )

    worker.run_once()

    assert pending_key not in _await_agent_store(worker).read_all()


def test_await_agent_reconciliation_keeps_pending_record_when_unavailable_vm_has_no_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                }
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    pending = AwaitAgentPendingRecord(
        gate_id="await-agent-1",
        dispatch_id="dispatch-stale",
        prompt_event_id="effect:effect-agent-prompt-1:stale",
        prompt_source_node_id="effect-agent-prompt-1",
        trace_id="trace-stale",
        session_id="session-42",
        vm_name="vm-issue-42",
        payload={},
        occurred_at=datetime(2026, 4, 4, 11, 50, tzinfo=timezone.utc),
    )
    pending_key = "await-agent-1:dispatch-stale"
    _await_agent_store(worker).put(pending_key, pending)
    monkeypatch.setattr(
        worker,
        "_list_vm_summaries_for_await_agent_reconciliation",
        lambda: [
            VmSummary(
                vm_name="vm-issue-42",
                session_ids=["session-42"],
                labels={},
                directory_paths=["root/team-a"],
                libvirt_state="shut off",
                ip_address="192.0.2.10",
                agent_state="running",
                agent_repo_names=["repo-a"],
                last_state_update_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
                dispatch_id=None,
            )
        ],
    )

    worker.run_once()

    assert _await_agent_store(worker).read_all() == {pending_key: pending}


def test_await_agent_reconciliation_skips_and_retries_when_inventory_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                }
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    pending = AwaitAgentPendingRecord(
        gate_id="await-agent-1",
        dispatch_id="dispatch-failing",
        prompt_event_id="effect:effect-agent-prompt-1:failing",
        prompt_source_node_id="effect-agent-prompt-1",
        trace_id="trace-failing",
        session_id="session-42",
        vm_name="vm-issue-42",
        payload={},
        occurred_at=datetime(2026, 4, 4, 11, 50, tzinfo=timezone.utc),
    )
    pending_key = "await-agent-1:dispatch-failing"
    _await_agent_store(worker).put(pending_key, pending)
    worker.agent_run_status_port = _FailingAgentRunStatusPortStub()

    caplog.set_level(logging.ERROR, logger="evander.gates.providers.await_agent")

    worker.run_once()

    assert _await_agent_store(worker).read_all() == {pending_key: pending}
    assert "AwaitAgentGateProvider" in caplog.text
    assert "phase=stale_pending_cleanup" in caplog.text
    assert "pending_records=1" in caplog.text
    assert "phase=completion_event_build" in caplog.text
    assert f"key={pending_key}" in caplog.text
    assert f"dispatch_id={pending.dispatch_id}" in caplog.text
    assert "virsh list failed" in caplog.text

    caplog.clear()
    worker.agent_run_status_port = _AgentRunStatusPortStub(
        [
            VmSummary(
                vm_name="vm-issue-42",
                session_ids=["session-42"],
                labels={},
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.10",
                agent_state="idle",
                last_state_update_at=datetime(2026, 4, 4, 12, 5, tzinfo=timezone.utc),
                dispatch_id=None,
            )
        ]
    )

    worker.run_once()

    assert _await_agent_store(worker).read_all() == {}


def test_await_agent_reconciliation_keeps_settled_current_dispatch_record(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                }
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    pending = AwaitAgentPendingRecord(
        gate_id="await-agent-1",
        dispatch_id="dispatch-current",
        prompt_event_id="effect:effect-agent-prompt-1:current",
        prompt_source_node_id="effect-agent-prompt-1",
        trace_id="trace-current",
        session_id="session-42",
        vm_name="vm-issue-42",
        payload={},
        occurred_at=datetime(2026, 4, 4, 11, 50, tzinfo=timezone.utc),
    )
    _await_agent_store(worker).put("await-agent-1:dispatch-current", pending)
    monkeypatch.setattr(
        worker,
        "_list_vm_summaries_for_await_agent_reconciliation",
        lambda: [
            VmSummary(
                vm_name="vm-issue-42",
                session_ids=["session-42"],
                labels={},
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.10",
                agent_state="idle",
                last_state_update_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
                dispatch_id="dispatch-current",
            )
        ],
    )

    worker.run_once()

    assert _await_agent_store(worker).read_all() == {}


@pytest.mark.parametrize("libvirt_state", ["shut off", "stopped"])
def test_await_agent_reconciliation_completes_settled_current_dispatch_record_for_non_running_vm(
    tmp_path: Path, monkeypatch, libvirt_state: str
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-42",
                    "prompt": "Continue main task",
                }
            ],
            "edges": [
                _edge("trigger-1", "effect-1"),
                _edge("effect-1", "await-agent-1", source_port="started"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    pending = AwaitAgentPendingRecord(
        gate_id="await-agent-1",
        dispatch_id="dispatch-current",
        prompt_event_id="effect:effect-agent-prompt-1:current",
        prompt_source_node_id="effect-agent-prompt-1",
        trace_id="trace-current",
        session_id="session-42",
        vm_name="vm-issue-42",
        payload={},
        occurred_at=datetime(2026, 4, 4, 11, 50, tzinfo=timezone.utc),
    )
    pending_key = "await-agent-1:dispatch-current"
    _await_agent_store(worker).put(pending_key, pending)
    monkeypatch.setattr(
        worker,
        "_list_vm_summaries_for_await_agent_reconciliation",
        lambda: [
            VmSummary(
                vm_name="vm-issue-42",
                session_ids=["session-42"],
                labels={},
                directory_paths=["root/team-a"],
                libvirt_state=libvirt_state,
                ip_address="192.0.2.10",
                agent_state="idle",
                last_state_update_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
                dispatch_id="dispatch-current",
            )
        ],
    )

    worker.run_once()

    assert pending_key not in _await_agent_store(worker).read_all()

    gate_payload = gate_execution_payload(worker_config)
    assert any(
        item["gate_id"] == "await-agent-1" and item["status"] == "succeeded"
        for item in gate_payload.values()
    )


def test_await_agent_pending_target_can_fill_missing_vm_name_from_session_summary() -> None:
    pending = AwaitAgentPendingRecord(
        gate_id="await-agent-1",
        dispatch_id="dispatch-current",
        prompt_event_id="effect:effect-agent-prompt-1:current",
        prompt_source_node_id="effect-agent-prompt-1",
        trace_id="trace-current",
        session_id="session-42",
        vm_name=None,
        payload={},
        occurred_at=datetime(2026, 4, 4, 11, 50, tzinfo=timezone.utc),
    )

    session_id, vm_name = _await_agent_pending_target(
        pending,
        summaries=[
            VmSummary(
                vm_name="vm-issue-42",
                session_ids=["session-42"],
                labels={},
                directory_paths=["root/team-a"],
                libvirt_state="running",
                ip_address="192.0.2.10",
                agent_state="idle",
                last_state_update_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
                dispatch_id=None,
            )
        ],
    )

    assert session_id == "session-42"
    assert vm_name == "vm-issue-42"


def test_map_gate_records_failed_status_on_unresolved_reference(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"session_id": {"type": "string"}},
                }
            ],
            "gates": [
                {
                    "id": "gate-map",
                    "display_name": "Map",
                    "type": "gate:map",
                    "template": {"session_id": "${{trigger-1.session_id}}"},
                    "outputs": {"session_id": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "prompt": "session ${{gate-map.session_id}}",
                }
            ],
            "edges": [
                _edge("trigger-1", "gate-map"),
                _edge("gate-map", "effect-1"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-1": {"ok": True}})
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-map-fail",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    record = next(iter(gate_payload.values()))
    assert record["status"] == "failed"
    assert "not found in upstream payload" in record["error"]

    assert effect_execution_payload(worker_config) == {}


def test_gate_binding_failure_routes_to_error_branch(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "gate-map",
                    "display_name": "Map",
                    "type": "gate:map",
                    "template": {"session_id": "${{trigger-1.session_id}}"},
                    "outputs": {
                        "session_id": {"type": "string"},
                        "success": {"type": "boolean"},
                        "error_message": {"type": "string"},
                        "error_class": {"type": "string"},
                    },
                }
            ],
            "effects": [
                {
                    "id": "effect-recover",
                    "display_name": "Recover",
                    "type": "effect:agent:prompt",
                    "prompt": "Repair the missing field",
                }
            ],
            "edges": [
                _edge("trigger-1", "gate-map"),
                _edge("gate-map", "effect-recover", source_port="error"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(  # type: ignore[arg-type]
        config=config,
        worker_config=worker_config,
        runtime=None,
    )
    worker.provider_factory = _Factory({"effect-recover": {"ok": True}})
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-map-error-route",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    for _ in range(4):
        worker.run_once()

    gate_record = next(iter(gate_execution_payload(worker_config).values()))
    assert gate_record["status"] == "error_routed"
    assert gate_record["completion_event_branch_port"] == "error"
    assert gate_record["outputs"]["success"] is False
    assert gate_record["outputs"]["error_class"] == "BindingResolutionError"
    assert "session_id" in gate_record["outputs"]["error_message"]

    effect_record = next(iter(effect_execution_payload(worker_config).values()))
    assert effect_record["effect_id"] == "effect-recover"
    assert effect_record["status"] == "succeeded"


def test_deserialize_gate_emits_parsed_object_outputs(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"payload_text": {"type": "string"}},
                }
            ],
            "gates": [
                {
                    "id": "gate-deserialize",
                    "display_name": "Deserialize",
                    "type": "gate:deserialize",
                    "content": "${{trigger-1.payload_text}}",
                    "format": "auto",
                    "outputs": {
                        "title": {"type": "string"},
                        "metadata": {"type": "object"},
                        "count": {"type": "integer"},
                    },
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": (
                        "Review ${{gate-deserialize.title}} ${{gate-deserialize.metadata.priority}}"
                    ),
                }
            ],
            "edges": [
                _edge("trigger-1", "gate-deserialize"),
                _edge("gate-deserialize", "effect-1"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(  # type: ignore[arg-type]
        config=config,
        worker_config=worker_config,
        runtime=None,
    )
    worker.provider_factory = _Factory({"effect-1": {"ok": True}})
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-deserialize",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={
                "payload_text": ('{"title":"Fix relay","metadata":{"priority":"P1"},"count":2}')
            },
        )
    )

    for _ in range(4):
        worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"
    assert gate_record["outputs"] == {
        "title": "Fix relay",
        "metadata": {"priority": "P1"},
        "count": 2,
    }

    effect_payload = effect_execution_payload(worker_config)
    effect_record = next(iter(effect_payload.values()))
    assert effect_record["resolved_config"]["prompt"] == "Review Fix relay P1"


def test_deserialize_gate_records_failed_status_when_content_is_not_object(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "gate-deserialize",
                    "display_name": "Deserialize",
                    "type": "gate:deserialize",
                    "content": "['not', 'an', 'object']",
                    "format": "yaml",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "prompt": "Review ${{gate-deserialize.title}}",
                }
            ],
            "edges": [
                _edge("trigger-1", "gate-deserialize"),
                _edge("gate-deserialize", "effect-1"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(  # type: ignore[arg-type]
        config=config,
        worker_config=worker_config,
        runtime=None,
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-deserialize-fail",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    record = next(iter(gate_payload.values()))
    assert record["status"] == "failed"
    assert record["error"] == "gate:deserialize content must decode to an object"


def test_deserialize_gate_routes_parse_failure_to_error_branch(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "gate-deserialize",
                    "display_name": "Deserialize",
                    "type": "gate:deserialize",
                    "content": "{not-json",
                    "format": "json",
                }
            ],
            "effects": [
                {
                    "id": "effect-recover",
                    "display_name": "Recover",
                    "type": "effect:agent:prompt",
                    "prompt": "Repair the invalid document",
                }
            ],
            "edges": [
                _edge("trigger-1", "gate-deserialize"),
                _edge("gate-deserialize", "effect-recover", source_port="error"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(  # type: ignore[arg-type]
        config=config,
        worker_config=worker_config,
        runtime=None,
    )
    worker.provider_factory = _Factory({"effect-recover": {"ok": True}})
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-deserialize-error-route",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    for _ in range(4):
        worker.run_once()

    gate_record = next(iter(gate_execution_payload(worker_config).values()))
    assert gate_record["status"] == "error_routed"
    assert gate_record["completion_event_branch_port"] == "error"
    assert gate_record["outputs"] == {
        "success": False,
        "error_message": "gate:deserialize failed to parse JSON: Expecting property name enclosed in double quotes",
        "error_class": "GateRuntimeError",
    }
    assert gate_record["error"] == gate_record["outputs"]["error_message"]

    effect_record = next(iter(effect_execution_payload(worker_config).values()))
    assert effect_record["effect_id"] == "effect-recover"
    assert effect_record["status"] == "succeeded"


def test_serialize_gate_emits_json_content_for_downstream_bindings(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {
                        "title": {"type": "string"},
                        "priority": {"type": "string"},
                    },
                }
            ],
            "gates": [
                {
                    "id": "gate-serialize",
                    "display_name": "Serialize",
                    "type": "gate:serialize",
                    "value": {
                        "title": "${{trigger-1.title}}",
                        "metadata": {"priority": "${{trigger-1.priority}}"},
                    },
                    "format": "json",
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "Payload ${{gate-serialize.content}}",
                }
            ],
            "edges": [
                _edge("trigger-1", "gate-serialize"),
                _edge("gate-serialize", "effect-1"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(  # type: ignore[arg-type]
        config=config,
        worker_config=worker_config,
        runtime=None,
    )
    worker.provider_factory = _Factory({"effect-1": {"ok": True}})
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-serialize",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"title": "Fix relay", "priority": "P1"},
        )
    )

    for _ in range(4):
        worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"
    assert gate_record["outputs"] == {
        "content": '{"title":"Fix relay","metadata":{"priority":"P1"}}',
        "format": "json",
    }

    effect_payload = effect_execution_payload(worker_config)
    effect_record = next(iter(effect_payload.values()))
    assert (
        effect_record["resolved_config"]["prompt"]
        == 'Payload {"title":"Fix relay","metadata":{"priority":"P1"}}'
    )


def test_aggregate_gate_emits_totals_for_downstream_bindings(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"items": {"type": "array"}},
                }
            ],
            "gates": [
                {
                    "id": "gate-aggregate",
                    "display_name": "Aggregate usage",
                    "type": "gate:aggregate",
                    "items": "${{trigger-1.items}}",
                    "metrics": {
                        "input_tokens": {"operation": "sum", "path": "input_tokens"},
                        "output_tokens": {"operation": "sum", "path": "output_tokens"},
                        "total_tokens": {"operation": "sum", "path": "total_tokens"},
                        "average_tokens": {"operation": "avg", "path": "total_tokens"},
                    },
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": (
                        "Tokens ${{gate-aggregate.total_tokens}} "
                        "across ${{gate-aggregate.count}} event(s); "
                        "average ${{gate-aggregate.average_tokens}}."
                    ),
                }
            ],
            "edges": [
                _edge("trigger-1", "gate-aggregate"),
                _edge("gate-aggregate", "effect-1"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(  # type: ignore[arg-type]
        config=config,
        worker_config=worker_config,
        runtime=None,
    )
    worker.provider_factory = _Factory({"effect-1": {"ok": True}})
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-aggregate",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
            payload={
                "items": [
                    {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
                    {"input_tokens": 20, "output_tokens": 8, "total_tokens": 28},
                    {"input_tokens": None, "output_tokens": None, "total_tokens": None},
                ]
            },
        )
    )

    for _ in range(4):
        worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"
    assert gate_record["outputs"] == {
        "count": 3,
        "input_tokens": 30,
        "output_tokens": 13,
        "total_tokens": 43,
        "average_tokens": 21.5,
    }

    effect_payload = effect_execution_payload(worker_config)
    effect_record = next(iter(effect_payload.values()))
    assert (
        effect_record["resolved_config"]["prompt"] == "Tokens 43 across 3 event(s); average 21.5."
    )


def test_serialize_gate_emits_yaml_content(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"ticket": {"type": "object"}},
                }
            ],
            "gates": [
                {
                    "id": "gate-serialize",
                    "display_name": "Serialize",
                    "type": "gate:serialize",
                    "value": "${{trigger-1.ticket}}",
                    "format": "yaml",
                }
            ],
            "edges": [_edge("trigger-1", "gate-serialize")],
        }
    )
    worker = EffectOrchestratorWorker(  # type: ignore[arg-type]
        config=config,
        worker_config=worker_config,
        runtime=None,
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-serialize-yaml",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket": {"title": "Fix relay", "metadata": {"priority": "P1"}}},
        )
    )

    worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"
    assert yaml.safe_load(gate_record["outputs"]["content"]) == {
        "title": "Fix relay",
        "metadata": {"priority": "P1"},
    }
    assert gate_record["outputs"]["format"] == "yaml"


def test_merge_gate_re_emits_shared_payload_keys(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-a",
                    "display_name": "A",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"shared": {"type": "string"}, "a_only": {"type": "integer"}},
                },
                {
                    "id": "trigger-b",
                    "display_name": "B",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"shared": {"type": "string"}, "b_only": {"type": "integer"}},
                },
            ],
            "gates": [
                {
                    "id": "gate-merge",
                    "display_name": "Merge",
                    "type": "gate:merge",
                    "outputs": {"shared": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "prompt": "shared ${{gate-merge.shared}}",
                }
            ],
            "edges": [
                _edge("trigger-a", "gate-merge"),
                _edge("trigger-b", "gate-merge"),
                _edge("gate-merge", "effect-1"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-1": {"ok": True}})
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-merge-1",
            trigger_id="trigger-a",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"shared": "one", "a_only": 1},
        )
    )
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-merge-2",
            trigger_id="trigger-b",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, 1, tzinfo=timezone.utc),
            payload={"shared": "two", "b_only": 2},
        )
    )

    for _ in range(6):
        worker.run_once()

    gate_payload = gate_execution_payload(worker_config)
    records = sorted(gate_payload.values(), key=lambda item: item["event_id"])
    assert records[0]["status"] == "succeeded"
    assert records[0]["outputs"] == {"shared": "one"}
    assert records[1]["status"] == "succeeded"
    assert records[1]["outputs"] == {"shared": "two"}

    effect_payload = effect_execution_payload(worker_config)
    effect_record = next(iter(effect_payload.values()))
    assert effect_record["status"] == "succeeded"


def test_merge_gate_preserves_guaranteed_pre_branch_context(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"ticket_id": {"type": "string"}},
                }
            ],
            "gates": [
                {
                    "id": "gate-merge",
                    "display_name": "Merge",
                    "type": "gate:merge",
                    "outputs": {"shared": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-pre",
                    "display_name": "Prepare",
                    "type": "effect:agent:prompt",
                    "prompt": "prepare",
                    "outputs": {"session_id": {"type": "string"}},
                },
                {
                    "id": "effect-a",
                    "display_name": "A",
                    "type": "effect:agent:prompt",
                    "prompt": "a",
                    "outputs": {"shared": {"type": "string"}},
                },
                {
                    "id": "effect-b",
                    "display_name": "B",
                    "type": "effect:agent:prompt",
                    "prompt": "b",
                    "outputs": {"shared": {"type": "string"}},
                },
                {
                    "id": "effect-after",
                    "display_name": "After",
                    "type": "effect:agent:prompt",
                    "session_id": "${{effect-pre.session_id}}",
                    "prompt": "shared ${{gate-merge.shared}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-pre"),
                _edge("effect-pre", "effect-a"),
                _edge("effect-pre", "effect-b"),
                _edge("effect-a", "gate-merge"),
                _edge("effect-b", "gate-merge"),
                _edge("gate-merge", "effect-after"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-pre": {"session_id": "session-pre", "success": True},
            "effect-a": {"shared": "a", "success": True},
            "effect-b": {"shared": "b", "success": True},
            "effect-after": {"success": True},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-merge-context-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket_id": "42"},
        )
    )

    for _ in range(8):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    after_records = [
        item for item in effect_payload.values() if item["effect_id"] == "effect-after"
    ]
    assert after_records
    assert all(record["status"] == "succeeded" for record in after_records)
    assert {record["resolved_config"]["session_id"] for record in after_records} == {"session-pre"}


def test_merge_gate_drops_branch_local_context(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "gate-merge",
                    "display_name": "Merge",
                    "type": "gate:merge",
                    "outputs": {"shared": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-a",
                    "display_name": "A",
                    "type": "effect:agent:prompt",
                    "prompt": "a",
                    "outputs": {"shared": {"type": "string"}},
                },
                {
                    "id": "effect-b",
                    "display_name": "B",
                    "type": "effect:agent:prompt",
                    "prompt": "b",
                    "outputs": {"shared": {"type": "string"}},
                },
                {
                    "id": "effect-after",
                    "display_name": "After",
                    "type": "effect:agent:prompt",
                    "prompt": "branch ${{effect-a.shared}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-a"),
                _edge("trigger-1", "effect-b"),
                _edge("effect-a", "gate-merge"),
                _edge("effect-b", "gate-merge"),
                _edge("gate-merge", "effect-after"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-a": {"shared": "a"},
            "effect-b": {"shared": "b"},
            "effect-after": {"success": True},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-merge-branch-context-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    for _ in range(8):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    after_records = [
        item for item in effect_payload.values() if item["effect_id"] == "effect-after"
    ]
    assert after_records
    assert all(record["status"] == "failed" for record in after_records)
    assert {record["binding_error"] for record in after_records} == {
        "Output reference node 'effect-a' is not available in this upstream path"
    }


def test_debounce_gate_preserves_context_for_delayed_release(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "gate-debounce",
                    "display_name": "Debounce",
                    "type": "gate:debounce",
                    "key": "fixed",
                    "quiet_period": "PT0.001S",
                    "max_delay": "PT0.001S",
                    "outputs": {"message": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-pre",
                    "display_name": "Prepare",
                    "type": "effect:agent:prompt",
                    "prompt": "prepare",
                    "outputs": {"session_id": {"type": "string"}},
                },
                {
                    "id": "effect-after",
                    "display_name": "After",
                    "type": "effect:agent:prompt",
                    "session_id": "${{effect-pre.session_id}}",
                    "prompt": "${{gate-debounce.message}}",
                },
            ],
            "edges": [
                _edge("trigger-1", "effect-pre"),
                _edge("effect-pre", "gate-debounce"),
                _edge("gate-debounce", "effect-after"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-pre": {"session_id": "session-pre", "message": "hello"},
            "effect-after": {"success": True},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-debounce-context-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    for _ in range(6):
        worker.run_once()

    effect_payload = effect_execution_payload(worker_config)
    after_record = next(
        item for item in effect_payload.values() if item["effect_id"] == "effect-after"
    )
    assert after_record["status"] == "succeeded"
    assert after_record["resolved_config"] == {
        "prompt": "hello",
        "session_id": "session-pre",
    }


def test_event_emit_effect_evaluates_payload_and_writes_internal_event(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"issue_id": {"type": "string"}, "metadata": {"type": "object"}},
                }
            ],
            "effects": [
                {
                    "id": "emit-plan",
                    "display_name": "Emit plan",
                    "type": "effect:event:emit",
                    "event_type": "issue.plan_committed",
                    "correlation_key": "issue:${{trigger-1.issue_id}}",
                    "payload": {
                        "issue_id": "${{trigger-1.issue_id}}",
                        "plan_version": "${{trigger-1.metadata.plan.version}}",
                    },
                }
            ],
            "edges": [_edge("trigger-1", "emit-plan")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="input-event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(timezone.utc),
            trace_id="trace-1",
            payload={"issue_id": "ISSUE-1", "metadata": {"plan": {"version": 7}}},
        )
    )

    worker.run_once()
    worker._wait_for_inflight_completion(timeout_seconds=2)
    worker.run_once()

    events = json.loads(worker_config.internal_event_store_path.read_text(encoding="utf-8"))
    assert len(events) == 1
    emitted = next(iter(events.values()))
    assert emitted["event_type"] == "issue.plan_committed"
    assert emitted["payload"] == {"issue_id": "ISSUE-1", "plan_version": 7}
    assert emitted["correlation_key"] == "issue:ISSUE-1"
    assert emitted["source"]["workflow_run_id"] == "trace-1"
    assert emitted["source"]["node_id"] == "emit-plan"
    journal = workflow_event_payload(worker_config)
    assert "workflow.internal_event.published" in {event["event_type"] for event in journal}


def test_run_once_processes_unrelated_event_when_inventory_reconciliation_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"issue_id": {"type": "string"}, "metadata": {"type": "object"}},
                }
            ],
            "effects": [
                {
                    "id": "emit-plan",
                    "display_name": "Emit plan",
                    "type": "effect:event:emit",
                    "event_type": "issue.plan_committed",
                    "correlation_key": "issue:${{trigger-1.issue_id}}",
                    "payload": {
                        "issue_id": "${{trigger-1.issue_id}}",
                        "plan_version": "${{trigger-1.metadata.plan.version}}",
                    },
                }
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "session_id": "session-42",
                    "vm_name": "vm-issue-42",
                    "outputs": {},
                }
            ],
            "edges": [
                _edge("trigger-1", "emit-plan"),
                _edge("emit-plan", "await-agent-1", source_port="started"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    pending = AwaitAgentPendingRecord(
        gate_id="await-agent-1",
        dispatch_id="dispatch-cycle-failure",
        prompt_event_id="effect:effect-agent-prompt-1:cycle-failure",
        prompt_source_node_id="effect-agent-prompt-1",
        trace_id="trace-cycle-failure",
        session_id="session-42",
        vm_name="vm-issue-42",
        payload={},
        occurred_at=datetime(2026, 4, 4, 11, 50, tzinfo=timezone.utc),
    )
    pending_key = "await-agent-1:dispatch-cycle-failure"
    _await_agent_store(worker).put(pending_key, pending)
    worker.agent_run_status_port = _FailingAgentRunStatusPortStub()
    worker.event_queue.publish(
        TriggerEvent(
            event_id="input-event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(timezone.utc),
            trace_id="trace-cycle-failure",
            payload={"issue_id": "ISSUE-1", "metadata": {"plan": {"version": 7}}},
        )
    )

    caplog.set_level(logging.ERROR, logger="evander.gates.providers.await_agent")

    result = worker.run_once()

    assert isinstance(result, EffectOrchestratorCycleResult)
    assert result.events_processed == 1
    assert _await_agent_store(worker).read_all() == {pending_key: pending}
    assert "AwaitAgentGateProvider" in caplog.text
    assert "virsh list failed" in caplog.text


def test_event_emit_effect_can_start_event_trigger_workflow(tmp_path: Path) -> None:
    from evander.daemons.trigger_orchestrator import TriggerOrchestratorWorker

    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "source-trigger",
                    "display_name": "Source",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                },
                {
                    "id": "event-trigger",
                    "display_name": "Custom event",
                    "type": "trigger:event",
                    "event_type": "custom.operator_event",
                },
            ],
            "effects": [
                {
                    "id": "emit-custom",
                    "display_name": "Emit custom",
                    "type": "effect:event:emit",
                    "event_type": "custom.operator_event",
                    "payload": {"value": "ok"},
                }
            ],
            "edges": [_edge("source-trigger", "emit-custom")],
        }
    )
    effect_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    effect_worker.event_queue.publish(
        TriggerEvent(
            event_id="source-event-1",
            trigger_id="source-trigger",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(timezone.utc),
            trace_id="trace-1",
            payload={},
        )
    )
    effect_worker.run_once()
    effect_worker._wait_for_inflight_completion(timeout_seconds=2)
    effect_worker.run_once()

    trigger_worker = TriggerOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    result = trigger_worker.run_once()

    assert result.events_published >= 1
    queue_payload = trigger_queue_payload(worker_config)
    event_trigger_records = [
        record for record in queue_payload.values() if record["trigger_id"] == "event-trigger"
    ]
    assert len(event_trigger_records) == 1
    assert event_trigger_records[0]["payload"]["payload"] == {"value": "ok"}


def test_event_emit_effect_invalid_event_type_fails_clearly(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"event_type": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "emit-invalid",
                    "display_name": "Emit invalid",
                    "type": "effect:event:emit",
                    "event_type": "${{trigger-1.event_type}}",
                    "payload": {},
                }
            ],
            "edges": [_edge("trigger-1", "emit-invalid")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="input-event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(timezone.utc),
            trace_id="trace-1",
            payload={"event_type": "bad event type"},
        )
    )

    worker.run_once()
    worker._wait_for_inflight_completion(timeout_seconds=2)
    worker.run_once()

    executions = [
        record
        for record in worker.execution_store.list_executions()
        if record.effect_id == "emit-invalid"
    ]
    assert executions[-1].status == "failed"
    assert "event_type" in (executions[-1].error or "")
    assert json.loads(worker_config.internal_event_store_path.read_text(encoding="utf-8")) == {}


def test_event_emit_effect_retry_uses_idempotency_key(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "effects": [
                {
                    "id": "emit-once",
                    "display_name": "Emit once",
                    "type": "effect:event:emit",
                    "event_type": "custom.operator_event",
                    "payload": {"value": "ok"},
                }
            ],
            "edges": [_edge("trigger-1", "emit-once")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="input-event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(timezone.utc),
            trace_id="trace-1",
            payload={},
        )
    )

    worker.run_once()
    worker._wait_for_inflight_completion(timeout_seconds=2)
    first_worker_events = json.loads(
        worker_config.internal_event_store_path.read_text(encoding="utf-8")
    )
    assert len(first_worker_events) == 1
    worker_retry = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    worker_retry.run_once()
    worker_retry._wait_for_inflight_completion(timeout_seconds=2)
    worker_retry.run_once()

    events = json.loads(worker_config.internal_event_store_path.read_text(encoding="utf-8"))
    assert len(events) == 1


def test_event_schedule_effect_resolves_bindings_and_writes_pending_record(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {
                        "event_type": {"type": "string"},
                        "case_id": {"type": "string"},
                        "summary": {"type": "string"},
                        "scheduled_for": {"type": "string"},
                    },
                }
            ],
            "effects": [
                {
                    "id": "schedule-plan",
                    "display_name": "Schedule plan",
                    "type": "effect:event:schedule",
                    "event_type": "${{trigger-1.event_type}}",
                    "correlation_key": "case:${{trigger-1.case_id}}",
                    "payload": {
                        "case_id": "${{trigger-1.case_id}}",
                        "summary": "${{trigger-1.summary}}",
                    },
                    "run_at": "${{trigger-1.scheduled_for}}",
                    "idempotency_key": "schedule:${{trigger-1.case_id}}",
                }
            ],
            "edges": [_edge("trigger-1", "schedule-plan")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="input-event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(timezone.utc),
            trace_id="trace-1",
            payload={
                "event_type": "custom.operator_event",
                "case_id": "CASE-1",
                "summary": "ready",
                "scheduled_for": "2026-05-25T12:05:00Z",
            },
        )
    )

    worker.run_once()
    worker._wait_for_inflight_completion(timeout_seconds=2)
    worker.run_once()

    executions = [
        record
        for record in worker.execution_store.list_executions()
        if record.effect_id == "schedule-plan"
    ]
    assert executions[-1].status == "succeeded"
    assert executions[-1].outputs["created"] is True
    assert executions[-1].outputs["run_at"] == "2026-05-25T12:05:00Z"

    scheduled_events = json.loads(
        worker_config.scheduled_internal_event_store_path.read_text(encoding="utf-8")
    )
    assert len(scheduled_events) == 1
    scheduled = next(iter(scheduled_events.values()))
    assert scheduled["event_type"] == "custom.operator_event"
    assert scheduled["payload"] == {"case_id": "CASE-1", "summary": "ready"}
    assert scheduled["correlation_key"] == "case:CASE-1"
    assert scheduled["run_at"] == "2026-05-25T12:05:00Z"
    assert scheduled["status"] == "pending"
    assert scheduled["source"]["source_type"] == "scheduled_internal_event"
    assert scheduled["source"]["workflow_run_id"] == "trace-1"
    assert scheduled["source"]["workflow_event_id"] == "input-event-1"
    assert scheduled["source"]["workflow_trace_id"] == "trace-1"
    assert scheduled["source"]["node_id"] == "schedule-plan"
    assert scheduled["source"]["node_type"] == "effect:event:schedule"
    assert json.loads(worker_config.internal_event_store_path.read_text(encoding="utf-8")) == {}


def test_event_schedule_effect_duplicate_idempotency_returns_original_record_without_mutation(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Trigger",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {
                        "event_type": {"type": "string"},
                        "case_id": {"type": "string"},
                    },
                }
            ],
            "effects": [
                {
                    "id": "schedule-once",
                    "display_name": "Schedule once",
                    "type": "effect:event:schedule",
                    "event_type": "${{trigger-1.event_type}}",
                    "payload": {"case_id": "${{trigger-1.case_id}}"},
                    "run_at": "2026-05-25T12:05:00Z",
                    "idempotency_key": "schedule:${{trigger-1.case_id}}",
                }
            ],
            "edges": [_edge("trigger-1", "schedule-once")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]

    worker.event_queue.publish(
        TriggerEvent(
            event_id="input-event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(timezone.utc),
            trace_id="trace-1",
            payload={"event_type": "custom.operator_event", "case_id": "CASE-1"},
        )
    )
    worker.run_once()
    worker._wait_for_inflight_completion(timeout_seconds=2)
    worker.run_once()

    first_schedule = json.loads(
        worker_config.scheduled_internal_event_store_path.read_text(encoding="utf-8")
    )
    first_schedule_record = next(iter(first_schedule.values()))

    worker.event_queue.publish(
        TriggerEvent(
            event_id="input-event-2",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(timezone.utc),
            trace_id="trace-2",
            payload={"event_type": "custom.operator_event", "case_id": "CASE-1"},
        )
    )
    worker.run_once()
    worker._wait_for_inflight_completion(timeout_seconds=2)
    worker.run_once()

    second_schedule = json.loads(
        worker_config.scheduled_internal_event_store_path.read_text(encoding="utf-8")
    )
    second_schedule_record = next(iter(second_schedule.values()))

    executions = [
        record
        for record in worker.execution_store.list_executions()
        if record.effect_id == "schedule-once"
    ]
    assert len(executions) == 2
    assert sorted(record.outputs["created"] for record in executions) == [False, True]
    assert first_schedule == second_schedule
    assert second_schedule_record == first_schedule_record
    assert json.loads(worker_config.internal_event_store_path.read_text(encoding="utf-8")) == {}


def test_event_schedule_effect_prepare_resolved_config_normalizes_delay_seconds(
    tmp_path: Path,
) -> None:
    executor = EventScheduleEffectExecutor(
        scheduled_internal_event_store=ScheduledInternalEventStore(
            tmp_path / "scheduled-internal-events.json"
        )
    )

    resolved = executor.prepare_resolved_config(
        resolved_config={
            "event_type": "custom.operator_event",
            "payload": {"value": "ok"},
            "delay_seconds": "90",
        },
        started_at=datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc),
    )

    assert resolved["event_type"] == "custom.operator_event"
    assert resolved["payload"] == {"value": "ok"}
    assert resolved["delay_seconds"] == 90.0
    assert resolved["run_at"] == "2026-05-25T12:01:30Z"


@pytest.mark.parametrize(
    ("resolved_config", "message"),
    [
        (
            {
                "event_type": "bad event type",
                "payload": {},
                "run_at": "2026-05-25T12:05:00Z",
            },
            "event_type",
        ),
        (
            {
                "event_type": "custom.operator_event",
                "payload": "not-an-object",
                "run_at": "2026-05-25T12:05:00Z",
            },
            "payload must resolve to a JSON object",
        ),
        (
            {
                "event_type": "custom.operator_event",
                "payload": {},
                "run_at": "not-a-timestamp",
            },
            "run_at must be an ISO-8601 timestamp",
        ),
        (
            {
                "event_type": "custom.operator_event",
                "payload": {},
                "delay_seconds": "not-a-number",
            },
            "delay_seconds must be a number",
        ),
        (
            {
                "event_type": "custom.operator_event",
                "payload": {},
                "delay_seconds": "1e309",
            },
            "delay_seconds must be a finite number",
        ),
        (
            {
                "event_type": "custom.operator_event",
                "payload": {},
                "delay_seconds": "nan",
            },
            "delay_seconds must be a finite number",
        ),
        (
            {
                "event_type": "custom.operator_event",
                "payload": {},
                "delay_seconds": "1e20",
            },
            "delay_seconds is outside the supported datetime range",
        ),
        (
            {
                "event_type": "custom.operator_event",
                "payload": {},
            },
            "requires exactly one of run_at or delay_seconds",
        ),
    ],
)
def test_event_schedule_effect_prepare_resolved_config_rejects_invalid_values(
    tmp_path: Path, resolved_config: dict[str, object], message: str
) -> None:
    executor = EventScheduleEffectExecutor(
        scheduled_internal_event_store=ScheduledInternalEventStore(
            tmp_path / "scheduled-internal-events.json"
        )
    )

    with pytest.raises(ValueError, match=message):
        executor.prepare_resolved_config(
            resolved_config=resolved_config,
            started_at=datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc),
        )
