from __future__ import annotations

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

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.execution import ExecutionStatusKind
from agent_sandbox.effects.providers.agent_enqueue_prompt import AgentEnqueuePromptEffectProvider
from agent_sandbox.effects.providers.agent_prompt import AgentPromptEffectProvider
from agent_sandbox.effects.providers.lifecycle_hooks import AgentEnqueuePromptQueueDispatchHook
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.vm_copy_files import VmCopyFilesEffectProvider
from agent_sandbox.daemons.effect_orchestrator import (
    AwaitAgentPendingRecord,
    EffectOrchestratorWorker,
    ExecutionGraph,
    GateExecutionRecord,
    JsonExecutionStore,
    SqliteExecutionStore,
    SqliteGateExecutionStore,
    _await_agent_pending_target,
    _matching_outbound_edges,
    build_effect_orchestrator_worker,
    EffectExecutionRecord,
    EffectOrchestratorCycleResult,
    main,
)
from agent_sandbox.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
    QueuedAgentPromptRecord,
)
from agent_sandbox.orchestration.host_polling import HostFileLockManager
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.models import SessionManifest, ToolName, VmSummary
from agent_sandbox.repositories.orchestration import SqliteWorkflowConfigSnapshotRepository
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository
from agent_sandbox.triggers.queue import TriggerQueueRecord


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

    def build(self, effect):
        return _Provider(
            self.outputs_by_effect_id.get(effect.id, {}),
            wait_on=self.wait_on_by_effect_id.get(effect.id),
        )


class _CountingFactory:
    def __init__(self, outputs_by_effect_id: dict[str, dict[str, object]]) -> None:
        self.outputs_by_effect_id = outputs_by_effect_id
        self.execute_calls: list[dict[str, object]] = []

    def build(self, effect):
        return _CountingProvider(
            self.outputs_by_effect_id.get(effect.id, {}),
            self.execute_calls,
        )


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future: Future[object] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future


class _FailingFactory:
    def build(self, effect):
        return _FailingProvider({})


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

    def destroy(self, execution_id: str):
        self.destroyed.append(execution_id)


def _edge(
    source_id: str, target_id: str, *, source_port: str | None = None
) -> dict[str, str]:
    edge = {"source_id": source_id, "target_id": target_id}
    if source_port is not None:
        edge["source_port"] = source_port
    return edge


def _await_agent_store(worker: EffectOrchestratorWorker):
    provider = worker._get_gate_provider("gate:await-agent")
    assert provider is not None
    return provider.load_state()


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


def test_effect_orchestrator_main_help_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["agent-sandbox-effect-orchestrator", "--help"])

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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [],
        }
    )
    updated_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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


def test_effect_orchestrator_uses_shared_host_lock_manager(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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


def test_effect_orchestrator_holds_delivery_for_gate_completion_pending(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    first_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    second_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    only = next(iter(execution_payload.values()))
    assert only["status"] == "skipped"
    assert only["error"] == "source_node_removed_from_graph:effect-removed"
    assert only["config_revision"]


def test_effect_orchestrator_executes_gate_and_effect_chain(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert sorted(item["status"] for item in effect_payload.values()) == ["succeeded", "succeeded"]
    assert by_effect["effect-1"]["binding_trace"]["entries"][0]["config_path"] == "$.vm_name"
    assert by_effect["effect-1"]["binding_trace"]["entries"][0]["reference"] == "gate-1.vm_name"
    assert by_effect["effect-2"]["binding_trace"]["entries"][0]["config_path"] == "$.session_id"
    assert by_effect["effect-2"]["binding_trace"]["entries"][0]["reference"] == "effect-1.session_id"
    assert by_effect["effect-2"]["binding_trace"]["entries"][1]["config_path"] == "$.prompt"
    assert by_effect["effect-2"]["binding_trace"]["entries"][1]["reference"] == "effect-1.session_id"
    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    only_gate = next(iter(gate_payload.values()))
    assert only_gate["status"] == "succeeded"
    assert only_gate["outputs"] == {"vm_name": "vm-123"}
    assert only_gate["binding_trace"]["entries"][0]["config_path"] == "$.vm_name"
    assert only_gate["binding_trace"]["entries"][0]["reference"] == "trigger-1.ticket_id"

    snapshot_payload = json.loads(
        (runtime_dir / "workflow-config-snapshots.json").read_text(encoding="utf-8")
    )
    only_snapshot = next(iter(snapshot_payload.values()))
    assert only_snapshot["snapshot_type"] == "compiled_runtime_graph"
    assert [item["id"] for item in only_snapshot["triggers"]] == ["trigger-1"]
    assert [item["id"] for item in only_snapshot["gates"]] == ["gate-1"]
    assert [item["id"] for item in only_snapshot["effects"]] == ["effect-1", "effect-2"]
    assert only_snapshot["edges"][0]["source_id"] == "trigger-1"

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert any(item["trigger_id"] == "gate-1" for item in queue_payload.values())
    assert any(item["trigger_id"] == "effect-1" for item in queue_payload.values())
    assert any(item["trigger_id"] == "effect-2" for item in queue_payload.values())
    assert {item["trace_id"] for item in queue_payload.values()} == {"trace-1"}


def test_effect_orchestrator_hot_drains_fast_completion_chain(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"


def test_effect_orchestrator_executes_query_nodes(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(effect_payload.values()))
    assert record["effect_id"] == "query-1"
    assert record["effect_type"] == "query:vm:run-state"
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"state_code": "running"}
    assert record["binding_trace"]["entries"][0]["config_path"] == "$.session_id"


def test_effect_orchestrator_executes_issue_get_query_nodes(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(effect_payload.values()))
    assert record["status"] == "failed"
    assert "not declared" in record["binding_error"]
    assert record["binding_trace"] is None


def test_effect_orchestrator_persists_workflow_config_snapshot_in_sqlite_backend(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "orchestration_store_backend": "sqlite",
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(effect_payload.values()))
    assert record["status"] == "succeeded"
    assert record["error"] == "sudo: a password is required"
    assert record["outputs"]["message"] == "sudo: a password is required"


def test_effect_orchestrator_executes_issue_update_state_effect(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(effect_payload.values()))
    assert record["effect_type"] == "effect:issue:update-state"
    assert record["status"] == "succeeded"


def test_for_gate_iterates_body_then_routes_to_done(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    for_records = [item for item in gate_payload.values() if item["gate_id"] == "for-1"]
    assert len(for_records) == 4
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is False) == 3
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is True) == 1


def test_for_gate_without_body_edge_routes_immediately_to_done(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    done_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-done"]
    assert len(done_records) == 1
    assert done_records[0]["resolved_config"]["command"] == "done 3"

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    body_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-body"]
    assert len(body_records) == 2
    assert sorted(item["resolved_config"]["command"] for item in body_records) == [
        "work 0:alpha",
        "work 1:beta",
    ]

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_path = runtime_dir / "effect-orchestrator-executions.json"
    effect_payload = (
        json.loads(effect_path.read_text(encoding="utf-8")) if effect_path.exists() else {}
    )
    assert effect_payload == {}

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    for_records = [item for item in gate_payload.values() if item["gate_id"] == "for-1"]
    assert len(for_records) == 1
    assert for_records[0]["outputs"] == {
        "current_index": 2,
        "current_element": None,
        "item_count": 2,
        "is_done": True,
    }
    assert for_records[0].get("published_event_id") is None


def test_for_gate_restarts_iteration_for_each_fresh_invocation_in_same_trace(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    for_records = [item for item in gate_payload.values() if item["gate_id"] == "for-1"]
    assert len(for_records) == 6
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is False) == 4
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is True) == 2


def test_effect_orchestrator_rejects_reserved_runtime_output_name(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(effect_payload.values()))
    assert record["status"] == "failed"
    assert record["outputs"] == {"__node_outputs": "forbidden"}
    assert "reserved output name" in record["error"]

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert set(queue_payload.keys()) == {"event-1"}
    assert queue_payload["event-1"]["delivery_state"] == "delivered"


def test_effect_orchestrator_preserves_upstream_payload_across_multi_hop_chain(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    by_effect = {item["effect_id"]: item for item in execution_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-2"]["resolved_config"]["prompt"] == "original 101 updated 999"


def test_effect_orchestrator_depth_protection_skips_cyclic_graph(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    with pytest.raises(ValueError, match="Non-cyclic edges cannot form a cycle"):
        TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    only = next(iter(execution_payload.values()))
    assert only["status"] == "running"
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-long-1"]["delivery_state"] == "inflight"

    idle_result = worker.run_once()
    assert idle_result.events_processed == 0
    assert idle_result.completion_events_published == 0
    assert idle_result.next_sleep_seconds == 1

    block_event.set()
    only = None
    for _ in range(5):
        worker.run_once()
        execution_payload = json.loads(
            (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
        )
        only = next(iter(execution_payload.values()))
        if only["status"] == "succeeded":
            break
        time.sleep(0.01)

    assert only is not None
    assert only["status"] == "succeeded"
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-long-1"]["delivery_state"] == "delivered"


def test_effect_orchestrator_retries_running_effect_after_restart(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-restart-1"]["delivery_state"] == "inflight"

    restarted_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    restarted_worker.provider_factory = _Factory({"effect-1": {"done": True}})

    for _ in range(5):
        restarted_worker.run_once()
        execution_payload = json.loads(
            (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
        )
        only = next(iter(execution_payload.values()))
        if only["status"] == "succeeded":
            break
        time.sleep(0.01)

    block_event.set()
    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    only = next(iter(execution_payload.values()))
    assert only["status"] == "succeeded"
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-restart-1"]["delivery_state"] == "delivered"
    assert any(item["trigger_id"] == "effect-1" for item in queue_payload.values())


def test_effect_orchestrator_marks_provider_failure_failed(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(execution_payload.values()))
    assert record["status"] == "failed"
    assert record["error"] == "provider boom"
    assert record["outputs"] == {}

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-provider-failure-1"]["delivery_state"] == "delivered"


def test_effect_orchestrator_marks_completion_publish_failure_pending(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(execution_payload.values()))
    assert record["status"] == "completion_pending"
    assert record["completion_pending_status"] == "succeeded"
    assert record["outputs"] == {"session_id": "session-123"}
    assert record["published_event_id"] is None
    assert record["completion_event_enqueued_at"] is None
    assert record["error"] == "completion publish boom"
    assert len(counting_factory.execute_calls) == 1

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-completion-publish-failure-1"]["delivery_state"] == "inflight"


def test_effect_orchestrator_retries_pending_completion_publish_after_restart(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(execution_payload.values()))
    assert record["status"] == "completion_pending"
    assert record["outputs"] == {"session_id": "session-123"}
    assert record["published_event_id"] is None
    assert record["completion_event_enqueued_at"] is None
    assert len(counting_factory.execute_calls) == 1

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-completion-retry-1"]["delivery_state"] == "inflight"
    assert all(item["trigger_id"] != "effect-1" for item in queue_payload.values())

    restarted_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    restarted_worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    restarted_worker.provider_factory = counting_factory

    restarted_worker.run_once()

    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(execution_payload.values()))
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"session_id": "session-123"}
    assert record["published_event_id"] is not None
    assert record["completion_event_enqueued_at"] is not None
    assert len(counting_factory.execute_calls) == 1

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-completion-retry-1"]["delivery_state"] == "delivered"
    assert sum(1 for item in queue_payload.values() if item["trigger_id"] == "effect-1") == 1


def test_effect_orchestrator_retries_pending_started_branch_publish_after_restart(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(execution_payload.values()))
    assert record["status"] == "completion_pending"
    assert record["completion_pending_status"] == "waiting"
    assert record["completion_event_branch_port"] == "started"
    assert record["outputs"]["dispatch_id"] == "dispatch-effect-1"
    assert record["published_event_id"] is None
    assert record["completion_event_enqueued_at"] is None
    assert record["error"] == "started branch publish boom"
    assert len(counting_factory.execute_calls) == 1

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-started-branch-retry-1"]["delivery_state"] == "inflight"
    assert all(item["trigger_id"] != "effect-1" for item in queue_payload.values())

    restarted_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    restarted_worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    restarted_worker.provider_factory = counting_factory

    restarted_worker.run_once()

    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(execution_payload.values()))
    assert record["status"] == "waiting"
    assert record["published_event_id"] is not None
    assert record["completion_event_enqueued_at"] is not None
    assert len(counting_factory.execute_calls) == 1

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
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

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    completion_records = [
        item for item in queue_payload.values() if item["trigger_id"] == "gate-map"
    ]
    assert len(completion_records) == 1
    assert completion_records[0]["delivery_state"] == "pending"
    assert queue_payload["event-gate-retry-1"]["delivery_state"] == "inflight"
    assert len(counting_factory.execute_calls) == 0

    worker.run_once()

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    gate_records = [item for item in gate_payload.values() if item["gate_id"] == "gate-map"]
    assert len(gate_records) == 1
    gate_record = gate_records[0]
    assert gate_record["status"] == "succeeded"
    assert gate_record["published_event_id"] is not None
    assert gate_record["completion_event_enqueued_at"] is not None
    assert gate_record["outputs"] == {"value": "hello"}
    assert gate_execute_calls == 1

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    effect_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-1"]
    assert len(effect_records) == 1
    assert effect_records[0]["status"] == "succeeded"
    assert len(counting_factory.execute_calls) == 1

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    execution_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    by_effect = {item["effect_id"]: item for item in execution_payload.values()}
    assert by_effect["effect-bad"]["status"] == "failed"
    assert by_effect["effect-good"]["status"] == "succeeded"
    assert "not declared by upstream node" in by_effect["effect-bad"]["error"]


def test_filter_gate_forwards_matching_payload_with_recursive_subset_match(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(gate_payload.values()))
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"ticket": {"priority": "high", "id": 7}, "state": "Committed"}

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    effect_record = next(iter(effect_payload.values()))
    assert effect_record["status"] == "succeeded"


def test_filter_gate_drops_non_matching_payload(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(gate_payload.values()))
    assert record["status"] == "dropped"
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert not any(item["trigger_id"] == "gate-filter" for item in queue_payload.values())


def test_filter_gate_when_supports_string_branch_operators(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Git push",
                    "type": "trigger:git:push",
                    "path_glob": str(tmp_path / "*.git"),
                }
            ],
            "gates": [
                {
                    "id": "gate-filter",
                    "display_name": "Agent branch only",
                    "type": "gate:filter",
                    "outputs": {
                        "branch_name": {"type": "string"},
                        "commit_hash": {"type": "string"},
                        "old_commit_hash": {"type": "string"},
                        "ref_name": {"type": "string"},
                        "repo_path": {"type": "string"},
                        "repo_name": {"type": "string"},
                        "session_id": {"type": "string"},
                        "review_branch": {"type": "string"},
                        "trusted_repo_path": {"type": "string"},
                        "base_branch": {"type": "string"},
                        "canonical_origin_url": {"type": "string"},
                    },
                    "when": {
                        "all": [
                            {"left": "${{trigger-1.branch_name}}", "starts_with": "agents/"},
                            {"left": "${{trigger-1.branch_name}}", "contains": "/"},
                            {"left": "${{trigger-1.repo_path}}", "ends_with": ".git"},
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
                "commit_hash": "a" * 40,
                "repo_path": str(tmp_path / "sample.git"),
            },
            trace_id="trace-git-1",
        )
    )

    for _ in range(4):
        worker.run_once()

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1"}


def test_filter_gate_when_can_match_ancestor_effect_outputs(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"
    assert gate_record["resolved_config"]["when"]["all"][0]["left"] is True
    assert gate_record["resolved_config"]["when"]["all"][1]["left"] == "session-123"

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}


def test_agent_prompt_on_completion_releases_downstream_effect_without_await_gate(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "waiting"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-2"]["resolved_config"]["metadata"] == {"thread_id": "thread-1"}


def test_agent_enqueue_prompt_completed_port_waits_for_agent_completion(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"


def test_agent_enqueue_prompt_queues_when_target_agent_is_running(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
        worker.provider_factory.admin_service,
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
        worker.provider_factory.admin_service,
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    only = next(iter(effect_payload.values()))
    assert only["status"] == "succeeded"
    assert only["outputs"]["queued"] is True
    queued_payload = json.loads(
        (runtime_dir / "queued-agent-prompts.json").read_text(encoding="utf-8")
    )
    assert "vm-1" in queued_payload


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
        }
    )

    assert without_context.workflow_context is None
    assert with_context.workflow_context == {
        "gate-map": {"ticket": {"issue_id": "ISSUE-1"}}
    }


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
        )
    )

    stored = queue_store.get("vm-1")
    assert stored is not None
    assert stored.created_at == first.created_at
    assert stored.coalesced_count == 2
    assert stored.prompt == "new prompt"
    assert stored.workflow_context == {"gate-map": {"ticket": {"issue_id": "ISSUE-2"}}}


def test_agent_enqueue_prompt_queued_completion_preserves_upstream_workflow_context(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
        worker.provider_factory.admin_service,
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
        worker.provider_factory.admin_service,
        "resume_agent",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("resume_agent should only be called by the queued dispatch port")
        ),
    )
    metadata_updates: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker.provider_factory.admin_service,
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
    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    effect_2_records = [
        record for record in effect_payload.values() if record["effect_id"] == "effect-2"
    ]
    assert len(effect_2_records) == 1
    assert effect_2_records[0]["status"] == "succeeded"


def test_agent_enqueue_prompt_dispatches_queued_prompt_when_target_agent_becomes_idle(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
        type("Result", (), {"success": True, "thread_id": "thread-queued"})(),
    )

    worker._run_post_completion_dispatch_hooks()

    assert len(resume_calls) == 1
    assert resume_calls[0]["target"] == "vm-1"
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
    assert waiting_records[0].outputs["thread_id"] == "thread-queued"


def test_agent_enqueue_prompt_dispatch_preserves_mcp_lease_for_queued_libvirt_prompt(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(
        runtime_dir=runtime_dir,
        gateway_url="http://gateway.test/mcp",
    )
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
            expires_at=datetime.now(timezone.utc),
            metadata={
                "agent_thread_ids": {
                    "teams:chat-1:agent_thread_id": "thread-existing"
                }
            },
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
    execution_store = JsonExecutionStore(runtime_dir / "effect-orchestrator-executions.json")
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
            expires_at=datetime.now(timezone.utc),
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
    execution_store = JsonExecutionStore(runtime_dir / "effect-orchestrator-executions.json")
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
            expires_at=datetime.now(timezone.utc),
            metadata={
                "agent_thread_ids": {
                    "teams:chat-1:agent_thread_id": "thread-existing"
                }
            },
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
    execution_store = JsonExecutionStore(runtime_dir / "effect-orchestrator-executions.json")
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
    execution_store = JsonExecutionStore(runtime_dir / "effect-orchestrator-executions.json")
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

    caplog.set_level(logging.ERROR, logger="agent_sandbox.effects.providers.lifecycle_hooks")

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
    execution_store = JsonExecutionStore(runtime_dir / "effect-orchestrator-executions.json")
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
    assert waiting_records[0].outputs["queue_key"] == "admin-agent-prompt:libvirt_vm:session-1"
    assert waiting_records[0].outputs["thread_id"] == "thread-dispatched"


def test_agent_enqueue_prompt_dispatch_does_not_publish_dispatch_id_as_thread_id(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
            expires_at=datetime.now(timezone.utc),
            vm_provider="container_worker_vm",
            vm_provider_id="truman",
            model="gpt-5.4-mini",
            metadata={
                "execution_backend": "container_worker_vm",
                "target_name": "truman",
                "agent_thread_ids": {
                    "teams:chat-1:agent_thread_id": "thread-existing"
                },
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
            resume_thread_id=None,
            resume_thread_metadata_key="teams:chat-1:agent_thread_id",
            effect_id="effect-agent-enqueue-prompt-1",
        )
    )
    backend = _ContainerDispatchBackendStub()
    backend.logs = "prompt complete\nsession id: 019de31a-8393-71d2-8990-e5f4fb0a4df2\n"
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.lifecycle_hooks.build_execution_backend",
        lambda config, admin_service: backend,
    )
    monkeypatch.setattr(
        "agent_sandbox.effects.providers.agent_prompt.build_execution_backend",
        lambda config, admin_service: backend,
    )
    execution_store = JsonExecutionStore(tmp_path / "effect-orchestrator-executions.json")
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
    assert "check teams" in str(backend.started_specs[0].command)
    assert "thread-existing" in str(backend.started_specs[0].command)
    waiting_records = list(execution_store.read_all().values())
    assert len(waiting_records) == 1
    assert waiting_records[0].status == "waiting"
    assert waiting_records[0].effect_type == "effect:agent:enqueue-prompt"
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
        "agent_sandbox.effects.providers.agent_prompt.build_execution_backend",
        lambda config, admin_service: backend,
    )
    execution_store = JsonExecutionStore(tmp_path / "effect-orchestrator-executions.json")
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
        worker.provider_factory.admin_service,
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    only = next(iter(effect_payload.values()))
    assert only["status"] == "failed"
    assert "queued prompt for retry" in only["error"]
    queued_payload = json.loads(
        (runtime_dir / "queued-agent-prompts.json").read_text(encoding="utf-8")
    )
    assert queued_payload["vm-1"]["prompt"] == "hello"


def test_agent_enqueue_prompt_queue_dispatch_hook_is_neutral_when_provider_absent(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    only = next(iter(effect_payload.values()))
    assert only["status"] == "failed"
    assert "resume_agent failed" in only["error"]


def test_filter_gate_when_drops_payload_when_predicate_is_false(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "dropped"

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1"}


def test_vm_copy_files_template_source_renders_bindings_before_dispatch(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    template_path = tmp_path / "guest-AGENTS.md.tmpl"
    template_path.write_text(
        "VM: ${{effect-1.vm_name}}\nIssue: ${{trigger-1.ticket.issue_number}}\nStarted: ${{system.execution.started_at_compact}}\n",
        encoding="utf-8",
    )
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    copy_record = next(item for item in effect_payload.values() if item["effect_id"] == "effect-2")
    rendered_file = copy_record["resolved_config"]["files"][0]
    assert "source_path" not in rendered_file
    assert rendered_file["content"].startswith("VM: vm-issue-14084\nIssue: 14084\nStarted: 2026")
    assert rendered_file["destination_path"] == "/home/agent/AGENTS.md"


def test_await_agent_gate_releases_follow_up_on_matching_completion(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    before_completion = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert any(
        item["gate_id"] == "await-agent-1" and item["status"] == "succeeded"
        for item in gate_payload.values()
    )


def test_await_agent_gate_releases_follow_up_from_in_process_vm_poll(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert any(
        item["gate_id"] == "await-agent-1" and item["status"] == "succeeded"
        for item in gate_payload.values()
    )


def test_await_agent_gate_preserves_upstream_node_outputs_for_follow_up_bindings(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}


def test_waiting_agent_prompt_reconciliation_completes_from_idle_matching_dispatch(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-1"]["published_event_id"] is not None
    assert by_effect["effect-2"]["status"] == "succeeded"


def test_waiting_agent_prompt_reconciliation_completes_idle_summary_without_dispatch_id(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"


def test_waiting_agent_prompt_reconciliation_skips_and_retries_when_inventory_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    execution_store = JsonExecutionStore(runtime_dir / "effect-orchestrator-executions.json")
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

    caplog.set_level(logging.ERROR, logger="agent_sandbox.effects.providers.agent_prompt")

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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}


def test_await_agent_reconciliation_completes_idle_pending_record_without_dispatch_id(
    tmp_path: Path, monkeypatch
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    caplog.set_level(logging.ERROR, logger="agent_sandbox.gates.providers.await_agent")

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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(gate_payload.values()))
    assert record["status"] == "failed"
    assert "not found in upstream payload" in record["error"]

    effect_path = runtime_dir / "effect-orchestrator-executions.json"
    if effect_path.exists():
        effect_payload = json.loads(effect_path.read_text(encoding="utf-8"))
        assert effect_payload == {}


def test_deserialize_gate_emits_parsed_object_outputs(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
                        "Review ${{gate-deserialize.title}} "
                        "${{gate-deserialize.metadata.priority}}"
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
                "payload_text": (
                    '{"title":"Fix relay","metadata":{"priority":"P1"},"count":2}'
                )
            },
        )
    )

    for _ in range(4):
        worker.run_once()

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"
    assert gate_record["outputs"] == {
        "title": "Fix relay",
        "metadata": {"priority": "P1"},
        "count": 2,
    }

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    effect_record = next(iter(effect_payload.values()))
    assert effect_record["resolved_config"]["prompt"] == "Review Fix relay P1"


def test_deserialize_gate_records_failed_status_when_content_is_not_object(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    record = next(iter(gate_payload.values()))
    assert record["status"] == "failed"
    assert record["error"] == "gate:deserialize content must decode to an object"


def test_serialize_gate_emits_json_content_for_downstream_bindings(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"
    assert gate_record["outputs"] == {
        "content": '{"title":"Fix relay","metadata":{"priority":"P1"}}',
        "format": "json",
    }

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    effect_record = next(iter(effect_payload.values()))
    assert (
        effect_record["resolved_config"]["prompt"]
        == 'Payload {"title":"Fix relay","metadata":{"priority":"P1"}}'
    )


def test_serialize_gate_emits_yaml_content(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    records = sorted(gate_payload.values(), key=lambda item: item["event_id"])
    assert records[0]["status"] == "succeeded"
    assert records[0]["outputs"] == {"shared": "one"}
    assert records[1]["status"] == "succeeded"
    assert records[1]["outputs"] == {"shared": "two"}

    effect_payload = json.loads(
        (runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    effect_record = next(iter(effect_payload.values()))
    assert effect_record["status"] == "succeeded"


def test_event_emit_effect_evaluates_payload_and_writes_internal_event(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
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
    journal = worker_config.workflow_event_journal_path.read_text(encoding="utf-8")
    assert "workflow.internal_event.published" in journal


def test_run_once_processes_unrelated_event_when_inventory_reconciliation_fails(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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

    caplog.set_level(logging.ERROR, logger="agent_sandbox.gates.providers.await_agent")

    result = worker.run_once()

    assert isinstance(result, EffectOrchestratorCycleResult)
    assert result.events_processed == 1
    assert _await_agent_store(worker).read_all() == {pending_key: pending}
    assert "AwaitAgentGateProvider" in caplog.text
    assert "virsh list failed" in caplog.text


def test_event_emit_effect_can_start_event_trigger_workflow(tmp_path: Path) -> None:
    from agent_sandbox.daemons.trigger_orchestrator import TriggerOrchestratorWorker

    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    event_trigger_records = [
        record for record in queue_payload.values() if record["trigger_id"] == "event-trigger"
    ]
    assert len(event_trigger_records) == 1
    assert event_trigger_records[0]["payload"]["payload"] == {"value": "ok"}


def test_event_emit_effect_invalid_event_type_fails_clearly(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
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
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
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
    worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
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
