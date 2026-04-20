from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.effect_orchestrator import EffectOrchestratorWorker
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.orchestration.wiring import build_effect_worker_wiring


def _worker_config(runtime_dir: Path) -> TriggerOrchestratorWorkerGlobalConfig:
    return TriggerOrchestratorWorkerGlobalConfig.model_validate(
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


def _publish_agent_completion_event(worker: EffectOrchestratorWorker) -> None:
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-agent-complete-1",
            trigger_id="__await-agent-completion__",
            trigger_type="trigger:agent:run:completed",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            trace_id="trace-agent-complete-1",
            payload={"dispatch_id": "dispatch-1"},
        )
    )


def test_effect_worker_runs_without_domain_handlers_in_pure_mode(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = _worker_config(runtime_dir)
    wiring = build_effect_worker_wiring(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
        include_default_domain_handlers=False,
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None, wiring=wiring)  # type: ignore[arg-type]
    _publish_agent_completion_event(worker)

    worker.run_once()

    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    only = next(iter(execution_payload.values()))
    assert only["status"] == "skipped"
    assert only["error"] == "source_node_removed_from_graph:__await-agent-completion__"


def test_effect_worker_agent_default_handler_pack_consumes_agent_completion_event(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = _worker_config(runtime_dir)
    wiring = build_effect_worker_wiring(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
        include_default_domain_handlers=True,
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None, wiring=wiring)  # type: ignore[arg-type]
    _publish_agent_completion_event(worker)

    worker.run_once()

    execution_payload = json.loads((runtime_dir / "effect-orchestrator-executions.json").read_text(encoding="utf-8"))
    assert execution_payload == {}
