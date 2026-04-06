from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.effect_orchestrator import EffectOrchestratorWorker
from agent_sandbox.events.models import TriggerEvent


class _Provider:
    def __init__(self, output: dict[str, object], *, wait_on: Event | None = None) -> None:
        self.output = output
        self.wait_on = wait_on

    def execute(self, *, resolved_config=None):
        if self.wait_on is not None:
            self.wait_on.wait(timeout=2)
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


def _edge(source_id: str, target_id: str) -> dict[str, str]:
    return {"source_id": source_id, "target_id": target_id}


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
                    "template": {"vm_name": "vm-$(trigger-1.ticket_id)"},
                    "outputs": {"vm_name": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Provision",
                    "type": "effect:vm:create",
                    "vm_name": "$(gate-1.vm_name)",
                    "outputs": {"session_id": {"type": "string"}},
                },
                {
                    "id": "effect-2",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "$(effect-1.session_id)",
                    "prompt": "Continue $(effect-1.session_id)",
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

    for _ in range(6):
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
                    "session_id": "$(trigger-1.session_id)",
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
                    "vm_name": "vm-$(trigger-1.ticket_id)",
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
                    "prompt": "ticket $(trigger-1.ticket_id)",
                    "outputs": {"vm_name": {"type": "string"}},
                },
                {
                    "id": "effect-2",
                    "display_name": "Build session",
                    "type": "effect:agent:prompt",
                    "prompt": "vm $(effect-1.vm_name)",
                    "outputs": {"session_id": {"type": "string"}},
                },
                {
                    "id": "effect-3",
                    "display_name": "Cross-hop binding",
                    "type": "effect:agent:prompt",
                    "prompt": "ticket $(trigger-1.ticket_id) session $(effect-2.session_id)",
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
                    "prompt": "ticket $(trigger-1.ticket_id)",
                    "outputs": {"ticket_id": {"type": "integer"}},
                },
                {
                    "id": "effect-2",
                    "display_name": "Uses both values",
                    "type": "effect:agent:prompt",
                    "prompt": "original $(trigger-1.ticket_id) updated $(effect-1.ticket_id)",
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
                    "prompt": "missing $(trigger-1.session_id)",
                },
                {
                    "id": "effect-good",
                    "display_name": "Good binding",
                    "type": "effect:agent:prompt",
                    "prompt": "ticket $(trigger-1.ticket_id)",
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
                    "prompt": "state $(gate-filter.state)",
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
                    "template": {"session_id": "$(trigger-1.session_id)"},
                    "outputs": {"session_id": {"type": "string"}},
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "prompt": "session $(gate-map.session_id)",
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
                    "prompt": "shared $(gate-merge.shared)",
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
