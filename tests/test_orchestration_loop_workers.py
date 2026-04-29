from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.daemons.effect_orchestrator import EffectOrchestratorWorker
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.orchestration.wiring import (
    build_effect_worker_wiring,
    build_trigger_worker_wiring,
)
from agent_sandbox.daemons.trigger_orchestrator import TriggerOrchestratorWorker
from agent_sandbox.workflow.engine import OrchestrationLoop


class _StaticProvider:
    def __init__(self, events: list[TriggerEvent]) -> None:
        self._events = events

    def poll(self) -> list[TriggerEvent]:
        return list(self._events)


def test_workers_delegate_run_once_to_shared_orchestration_loop(
    monkeypatch, tmp_path: Path
) -> None:
    calls: list[int] = []
    original_run_once = OrchestrationLoop.run_once

    def _spy_run_once(self, *, max_events: int, resolve_and_execute):  # type: ignore[no-untyped-def]
        calls.append(max_events)
        return original_run_once(
            self, max_events=max_events, resolve_and_execute=resolve_and_execute
        )

    monkeypatch.setattr(OrchestrationLoop, "run_once", _spy_run_once)

    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "cron-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
        }
    )

    trigger_worker = TriggerOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    trigger_worker.provider_factory.build = lambda _trigger: _StaticProvider([])  # type: ignore[assignment]
    trigger_worker.run_once()

    effect_worker = EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None
    )  # type: ignore[arg-type]
    effect_worker.run_once()

    assert calls == [1, 200]


def test_effect_queue_delivery_semantics_are_preserved(tmp_path: Path) -> None:
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
        }
    )
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)  # type: ignore[arg-type]
    worker.event_queue.publish(
        TriggerEvent(
            event_id="event-1",
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={},
        )
    )

    result = worker.run_once()

    assert result.pending_events_scanned == 1
    assert result.events_processed == 1
    queue_payload = json.loads(worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    assert queue_payload["event-1"]["delivery_state"] == "delivered"


def test_core_minimal_profile_smoke_builds_trigger_and_effect_workers_without_builtin_providers(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "provider_loader": {
                "provider_profile": "profiles/core-minimal",
                "provider_entry_points_enabled": False,
            },
            "triggers": [],
            "gates": [],
            "effects": [],
            "queries": [],
            "edges": [],
        }
    )

    trigger_wiring = build_trigger_worker_wiring(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    effect_wiring = build_effect_worker_wiring(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )

    assert trigger_wiring.provider_factory.registry.registered_types() == ()
    assert effect_wiring.provider_factory.registry.registered_types() == ()
    assert effect_wiring.gate_provider_factory.registry.registered_types() == ()
    assert effect_wiring.query_provider_factory.registry.registered_types() == ()
    assert effect_wiring.special_event_handlers == ()
    assert effect_wiring.lifecycle_reconciliation_hooks == ()
    assert effect_wiring.completion_dispatch_hooks == ()
