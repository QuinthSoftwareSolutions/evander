from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.events.models import TriggerEvent
from evander.gates.registry import DEFAULT_GATE_PROVIDER_REGISTRY, GateProviderRegistry


def _edge(source_id: str, target_id: str) -> dict[str, str]:
    return {"source_id": source_id, "target_id": target_id}


def test_effect_orchestrator_runs_without_await_agent_provider(tmp_path: Path) -> None:
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
                    "id": "await-agent-1",
                    "display_name": "Await Agent",
                    "type": "gate:await-agent",
                    "session_id": "session-1",
                    "outputs": {"dispatch_id": {"type": "string"}},
                }
            ],
            "edges": [_edge("trigger-1", "await-agent-1")],
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.gate_provider_factory.registry = GateProviderRegistry(initial={})
    worker._gate_providers.clear()

    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
            payload={"dispatch_id": "dispatch-1"},
        )
    )

    result = worker.run_once()

    assert result.events_processed == 1
    gate_payload = json.loads(
        (runtime_dir / "gate-orchestrator-executions.json").read_text(encoding="utf-8")
    )
    only = next(iter(gate_payload.values()))
    assert only["status"] == "failed"
    assert only["error"] == "no_gate_provider_registered:gate:await-agent"


def test_default_registry_includes_all_known_gate_types() -> None:
    assert set(DEFAULT_GATE_PROVIDER_REGISTRY) == {
        "gate:aggregate",
        "gate:await-agent",
        "gate:buffer",
        "gate:counter",
        "gate:debounce",
        "gate:deserialize",
        "gate:dedupe",
        "gate:filter",
        "gate:for",
        "gate:map",
        "gate:merge",
        "gate:serialize",
        "gate:switch",
        "gate:throttle",
        "gate:while",
    }
