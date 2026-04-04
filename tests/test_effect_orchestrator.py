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


def test_effect_orchestrator_executes_effect_chain(tmp_path: Path) -> None:
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
                    "upstream_id": "trigger-1",
                    "vm_name": "vm-$(ticket_id)",
                    "outputs": {"session_id": {"type": "string"}},
                },
                {
                    "id": "effect-2",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "upstream_id": "effect-1",
                    "prompt": "Continue $(session_id)",
                },
            ],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.provider_factory = _Factory(
        {
            "effect-1": {"session_id": "session-123"},
            "effect-2": {"thread_id": "thread-1", "succeeded": True},
        }
    )

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"ticket_id": 123},
        )
    )

    first = worker.run_once()
    for _ in range(4):
        worker.run_once()

    assert first.events_processed >= 1
    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    statuses = sorted(item["status"] for item in execution_payload.values())
    assert statuses == ["succeeded", "succeeded"]

    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert any(item["trigger_id"] == "effect-1" for item in queue_payload.values())
    assert any(item["trigger_id"] == "effect-2" for item in queue_payload.values())


def test_effect_orchestrator_depth_protection_skips_cyclic_graph(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "effects": [
                {
                    "id": "effect-a",
                    "display_name": "A",
                    "type": "effect:agent:prompt",
                    "upstream_id": "effect-b",
                    "prompt": "A",
                },
                {
                    "id": "effect-b",
                    "display_name": "B",
                    "type": "effect:agent:prompt",
                    "upstream_id": "effect-a",
                    "prompt": "B",
                },
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
    assert len(execution_payload) == 1
    record = next(iter(execution_payload.values()))
    assert record["effect_id"] == "effect-b"
    assert record["status"] == "skipped"


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
                    "upstream_id": "trigger-1",
                    "prompt": "hello",
                }
            ],
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

    # Worker should schedule the effect asynchronously and return quickly.
    assert elapsed < 0.5
    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    only = next(iter(execution_payload.values()))
    assert only["status"] == "running"

    block_event.set()
    worker.run_once()
    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    only = next(iter(execution_payload.values()))
    assert only["status"] == "succeeded"
