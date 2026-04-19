from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.effect_orchestrator import (
    AwaitAgentPendingRecord,
    EffectOrchestratorWorker,
    _await_agent_pending_target,
    build_effect_orchestrator_worker,
)
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.models import VmSummary


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


def _edge(source_id: str, target_id: str) -> dict[str, str]:
    return {"source_id": source_id, "target_id": target_id}


def _await_agent_store(worker: EffectOrchestratorWorker):
    provider = worker._get_gate_provider("gate:await-agent")
    assert provider is not None
    return provider.load_state()


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


def test_effect_orchestrator_logs_graph_revision_changes(tmp_path: Path, caplog) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    first_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {"id": "trigger-1", "display_name": "Tick", "type": "trigger:cron", "expression": "* * * * *"}
            ],
            "effects": [
                {"id": "effect-1", "display_name": "Do Work", "type": "effect:agent:prompt", "prompt": "hello"}
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
                {"id": "trigger-1", "display_name": "Tick", "type": "trigger:cron", "expression": "* * * * *"}
            ],
            "effects": [
                {"id": "effect-1", "display_name": "Do Work", "type": "effect:agent:prompt", "prompt": "hello"},
                {"id": "effect-2", "display_name": "Do More", "type": "effect:agent:prompt", "prompt": "more"},
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

    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    assert sorted(item["status"] for item in effect_payload.values()) == ["succeeded", "succeeded"]
    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
    only_gate = next(iter(gate_payload.values()))
    assert only_gate["status"] == "succeeded"
    assert only_gate["outputs"] == {"vm_name": "vm-123"}

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert any(item["trigger_id"] == "gate-1" for item in queue_payload.values())
    assert any(item["trigger_id"] == "effect-1" for item in queue_payload.values())
    assert any(item["trigger_id"] == "effect-2" for item in queue_payload.values())
    assert {item["trace_id"] for item in queue_payload.values()} == {"trace-1"}


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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    record = next(iter(effect_payload.values()))
    assert record["effect_id"] == "query-1"
    assert record["effect_type"] == "query:vm:run-state"
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"state_code": "running"}


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
    worker.query_provider_factory = _Factory({"query-1": {"issue_id": "root:issue-42", "metadata": {"steps": ["one", "two"]}}})

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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    record = next(iter(effect_payload.values()))
    assert record["effect_id"] == "query-1"
    assert record["effect_type"] == "query:issue:get"
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"issue_id": "root:issue-42", "metadata": {"steps": ["one", "two"]}}


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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    body_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-body"]
    done_records = [item for item in effect_payload.values() if item["effect_id"] == "effect-done"]

    assert len(body_records) == 3
    assert len(done_records) == 1
    assert [item["resolved_config"]["command"] for item in sorted(body_records, key=lambda entry: entry["resolved_config"]["command"])] == [
        "work 0:alpha",
        "work 1:beta",
        "work 2:gamma",
    ]
    assert done_records[0]["resolved_config"]["command"] == "done 3"

    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
    for_records = [item for item in gate_payload.values() if item["gate_id"] == "for-1"]
    assert len(for_records) == 4
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is False) == 3
    assert sum(1 for item in for_records if item["outputs"].get("is_done") is True) == 1


def test_for_gate_restarts_iteration_for_each_fresh_invocation_in_same_trace(tmp_path: Path) -> None:
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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
    assert sorted(item["resolved_config"]["command"] for item in done_records) == ["done 2", "done 2"]

    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    record = next(iter(effect_payload.values()))
    assert record["status"] == "failed"
    assert "reserved output name" in record["error"]

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert set(queue_payload.keys()) == {"event-1"}


def test_effect_orchestrator_preserves_upstream_payload_across_multi_hop_chain(tmp_path: Path) -> None:
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

    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    by_effect = {item["effect_id"]: item for item in execution_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-3"]["status"] == "succeeded"
    assert by_effect["effect-3"]["resolved_config"]["prompt"] == "ticket 101 session session-101"


def test_effect_orchestrator_supports_disambiguated_references_for_same_key_name(tmp_path: Path) -> None:
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

    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    by_effect = {item["effect_id"]: item for item in execution_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-2"]["resolved_config"]["prompt"] == "original 101 updated 999"


def test_effect_orchestrator_depth_protection_skips_cyclic_graph(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "effects": [
                {"id": "effect-a", "display_name": "A", "type": "effect:agent:prompt", "prompt": "A"},
                {"id": "effect-b", "display_name": "B", "type": "effect:agent:prompt", "prompt": "B"},
            ],
            "edges": [
                _edge("effect-b", "effect-a"),
                _edge("effect-a", "effect-b"),
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({})

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-cycle-1",
            trigger_id="effect-a",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    worker.run_once()

    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    record = next(iter(execution_payload.values()))
    assert record["effect_id"] == "effect-b"
    assert record["status"] == "skipped"
    assert record["error"] == "cycle_detected"


def test_effect_orchestrator_dispatches_without_waiting_for_long_effect(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {"id": "trigger-1", "display_name": "Tick", "type": "trigger:cron", "expression": "* * * * *"}
            ],
            "effects": [
                {"id": "effect-1", "display_name": "Long", "type": "effect:agent:prompt", "prompt": "hello"}
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    block_event = Event()
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory({"effect-1": {"done": True}}, wait_on_by_effect_id={"effect-1": block_event})
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
    worker.run_once()
    elapsed = time.monotonic() - start

    assert elapsed < 0.5
    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    only = next(iter(execution_payload.values()))
    assert only["status"] == "running"
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-long-1"]["delivery_state"] == "inflight"

    block_event.set()
    only = None
    for _ in range(5):
        worker.run_once()
        execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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
                {"id": "trigger-1", "display_name": "Tick", "type": "trigger:cron", "expression": "* * * * *"}
            ],
            "effects": [
                {"id": "effect-1", "display_name": "Long", "type": "effect:agent:prompt", "prompt": "hello"}
            ],
            "edges": [_edge("trigger-1", "effect-1")],
        }
    )
    block_event = Event()
    first_worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    first_worker.provider_factory = _Factory({"effect-1": {"done": True}}, wait_on_by_effect_id={"effect-1": block_event})
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

    restarted_worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    restarted_worker.provider_factory = _Factory({"effect-1": {"done": True}})

    for _ in range(5):
        restarted_worker.run_once()
        execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
        only = next(iter(execution_payload.values()))
        if only["status"] == "succeeded":
            break
        time.sleep(0.01)

    block_event.set()
    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    only = next(iter(execution_payload.values()))
    assert only["status"] == "succeeded"
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-restart-1"]["delivery_state"] == "delivered"
    assert any(item["trigger_id"] == "effect-1" for item in queue_payload.values())


def test_effect_orchestrator_records_binding_resolution_failure_and_advances_queue(tmp_path: Path) -> None:
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

    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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

    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
    record = next(iter(gate_payload.values()))
    assert record["status"] == "succeeded"
    assert record["outputs"] == {"ticket": {"priority": "high", "id": 7}, "state": "Committed"}

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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

    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
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

    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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
                        "guest_repo_name": {"type": "string"},
                        "session_id": {"type": "string"},
                        "vm_name": {"type": "string"},
                        "labels": {"type": "object"},
                        "remote_url": {"type": "string"},
                        "success": {"type": "boolean"},
                        "target_dir": {"type": "string"},
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

    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "succeeded"
    assert gate_record["resolved_config"]["when"]["all"][0]["left"] is True
    assert gate_record["resolved_config"]["when"]["all"][1]["left"] == "session-123"

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}


def test_agent_prompt_on_completion_releases_downstream_effect_without_await_gate(tmp_path: Path) -> None:
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
                    "output_mode": "on_completion",
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
                _edge("effect-1", "effect-2"),
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
                "output_mode": "on_completion",
            },
            "effect-2": {
                "dispatch_id": "dispatch-effect-2-def456",
                "thread_id": "thread-2",
                "success": True,
                "output_mode": "immediate",
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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
                "scope": {"directory_path": "root/team-a", "vm_name": "vm-issue-42", "session_id": "session-42", "vm_user": "agent"},
                "ip_address": "192.0.2.10",
                "vm": {"vm_name": "vm-issue-42"},
            },
            trace_id="trace-on-completion",
        )
    )

    for _ in range(6):
        worker.run_once()

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-1"]["outputs"]["vm_name"] == "vm-issue-42"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-2"]["resolved_config"]["session_id"] == "session-42"
    assert by_effect["effect-2"]["resolved_config"]["prompt"] == "Write post-exec summary for vm-issue-42"


def test_agent_enqueue_prompt_queues_when_target_agent_is_running(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {"id": "trigger-1", "display_name": "Tick", "type": "trigger:cron", "expression": "* * * * *"}
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
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("resume_agent should not be called while busy")),
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    only = next(iter(effect_payload.values()))
    assert only["status"] == "succeeded"
    assert only["outputs"]["queued"] is True
    queued_payload = json.loads((runtime_dir / "queued-agent-prompts.json").read_text(encoding="utf-8"))
    assert "vm-1" in queued_payload


def test_agent_enqueue_prompt_dispatches_queued_prompt_when_target_agent_becomes_idle(tmp_path: Path, monkeypatch) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {"id": "trigger-1", "display_name": "Tick", "type": "trigger:cron", "expression": "* * * * *"}
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
    summaries = [
        [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="running",
                session_ids=[],
            )
        ],
        [
            VmSummary(
                vm_name="vm-1",
                libvirt_state="running",
                agent_state="idle",
                session_ids=[],
            )
        ],
    ]
    monkeypatch.setattr(
        worker.provider_factory.admin_service,
        "list_vms",
        lambda **kwargs: summaries.pop(0) if summaries else [],
    )
    resume_calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        worker.provider_factory.admin_service,
        "resume_agent",
        lambda **kwargs: resume_calls.append(kwargs)
        or type("Result", (), {"success": True, "thread_id": "thread-queued"})(),
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

    worker.run_once()
    worker.run_once()
    worker.run_once()

    assert len(resume_calls) == 1
    assert resume_calls[0]["target"] == "vm-1"
    queued_store_path = runtime_dir / "queued-agent-prompts.json"
    queued_payload = json.loads(queued_store_path.read_text(encoding="utf-8")) if queued_store_path.exists() else {}
    assert queued_payload == {}


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
                        "guest_repo_name": {"type": "string"},
                        "session_id": {"type": "string"},
                        "vm_name": {"type": "string"},
                        "labels": {"type": "object"},
                        "remote_url": {"type": "string"},
                        "success": {"type": "boolean"},
                        "target_dir": {"type": "string"},
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

    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
    gate_record = next(iter(gate_payload.values()))
    assert gate_record["status"] == "dropped"

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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
    worker.provider_factory = _Factory(
        {
            "effect-1": {"session_id": "session-123", "vm_name": "vm-issue-14084", "success": True},
            "effect-2": {"success": True},
        }
    )

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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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
                _edge("effect-1", "await-agent-1"),
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
                "output_mode": "immediate",
            },
            "effect-2": {
                "dispatch_id": "dispatch-effect-2-def456",
                "thread_id": "thread-2",
                "success": True,
                "output_mode": "immediate",
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

    before_completion = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
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
                "scope": {"directory_path": "root/team-a", "vm_name": "vm-issue-42", "session_id": "session-42", "vm_user": "agent"},
                "ip_address": "192.0.2.10",
                "vm": {"vm_name": "vm-issue-42"},
            },
            trace_id="trace-await",
        )
    )

    for _ in range(6):
        worker.run_once()

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}

    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
    assert any(item["gate_id"] == "await-agent-1" and item["status"] == "succeeded" for item in gate_payload.values())


def test_await_agent_gate_releases_follow_up_from_in_process_vm_poll(tmp_path: Path, monkeypatch) -> None:
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
                _edge("effect-1", "await-agent-1"),
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
                "output_mode": "immediate",
            },
            "effect-2": {
                "dispatch_id": "dispatch-effect-2-def456",
                "thread_id": "thread-2",
                "success": True,
                "output_mode": "immediate",
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}

    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
    assert any(item["gate_id"] == "await-agent-1" and item["status"] == "succeeded" for item in gate_payload.values())


def test_await_agent_gate_preserves_upstream_node_outputs_for_follow_up_bindings(tmp_path: Path, monkeypatch) -> None:
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
                _edge("effect-1", "await-agent-1"),
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
                "output_mode": "immediate",
            },
            "effect-2": {
                "dispatch_id": "dispatch-effect-2-def456",
                "thread_id": "thread-2",
                "success": True,
                "output_mode": "immediate",
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"
    assert by_effect["effect-2"]["resolved_config"]["session_id"] == "session-42"
    assert by_effect["effect-2"]["resolved_config"]["prompt"] == "Write post-exec summary for vm-issue-42"


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
                _edge("effect-1", "await-agent-1"),
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
                "output_mode": "immediate",
            },
            "effect-2": {
                "dispatch_id": "dispatch-effect-2-def456",
                "thread_id": "thread-2",
                "success": True,
                "output_mode": "immediate",
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}


def test_waiting_agent_prompt_reconciliation_completes_from_idle_matching_dispatch(tmp_path: Path, monkeypatch) -> None:
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
                    "output_mode": "on_completion",
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
                _edge("effect-1", "effect-2"),
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
                "output_mode": "on_completion",
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-1"]["published_event_id"] is not None
    assert by_effect["effect-2"]["status"] == "succeeded"


def test_waiting_agent_prompt_reconciliation_completes_idle_summary_without_dispatch_id(tmp_path: Path, monkeypatch) -> None:
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
                    "output_mode": "on_completion",
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
                _edge("effect-1", "effect-2"),
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
                "output_mode": "on_completion",
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    by_effect = {item["effect_id"]: item for item in effect_payload.values()}
    assert by_effect["effect-1"]["status"] == "succeeded"
    assert by_effect["effect-2"]["status"] == "succeeded"


def test_await_agent_gate_releases_when_settled_summary_lacks_dispatch_id(tmp_path: Path, monkeypatch) -> None:
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
                _edge("effect-1", "await-agent-1"),
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
                "output_mode": "immediate",
            },
            "effect-2": {
                "dispatch_id": "dispatch-effect-2-def456",
                "thread_id": "thread-2",
                "success": True,
                "output_mode": "immediate",
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

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    assert {item["effect_id"] for item in effect_payload.values()} == {"effect-1", "effect-2"}


def test_await_agent_reconciliation_completes_idle_pending_record_without_dispatch_id(tmp_path: Path, monkeypatch) -> None:
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
                _edge("effect-1", "await-agent-1"),
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


def test_await_agent_reconciliation_keeps_pending_record_without_matching_summary(tmp_path: Path, monkeypatch) -> None:
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
                _edge("effect-1", "await-agent-1"),
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


def test_await_agent_reconciliation_keeps_settled_current_dispatch_record(tmp_path: Path, monkeypatch) -> None:
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
                _edge("effect-1", "await-agent-1"),
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

    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
    record = next(iter(gate_payload.values()))
    assert record["status"] == "failed"
    assert "not found in upstream payload" in record["error"]

    effect_path = runtime_dir / "effect-orchestrator-executions.json"
    if effect_path.exists():
        effect_payload = json.loads(effect_path.read_text(encoding="utf-8"))
        assert effect_payload == {}


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

    gate_payload = json.loads((runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8"))
    records = sorted(gate_payload.values(), key=lambda item: item["event_id"])
    assert records[0]["status"] == "succeeded"
    assert records[0]["outputs"] == {"shared": "one"}
    assert records[1]["status"] == "succeeded"
    assert records[1]["outputs"] == {"shared": "two"}

    effect_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    effect_record = next(iter(effect_payload.values()))
    assert effect_record["status"] == "succeeded"
