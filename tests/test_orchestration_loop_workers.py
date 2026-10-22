from __future__ import annotations

from config_helpers import canonical_worker_config, trigger_queue_payload

import json
from datetime import datetime, timezone
from pathlib import Path

from evander.config import GatewayConfig
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.events.models import TriggerEvent
from evander.orchestration import wal_maintenance
from evander.orchestration.wiring import (
    build_effect_worker_wiring,
    build_trigger_worker_wiring,
)
from evander.repositories.orchestration_db import OrchestrationWalCheckpointResult


def test_effect_queue_delivery_semantics_are_preserved(tmp_path: Path) -> None:
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
    assert result.next_sleep_seconds == 0.0
    queue_payload = trigger_queue_payload(worker_config)
    assert queue_payload["event-1"]["delivery_state"] == "delivered"


def test_effect_worker_wiring_uses_effect_cursor_path_for_processed_checkpoint(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    gate_cursor_path = runtime_dir / "custom-gate-cursors.json"
    effect_cursor_path = runtime_dir / "custom-effect-cursors.json"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [],
            "gates": [],
            "effects": [],
            "queries": [],
            "edges": [],
            "gate_cursor_store_path": gate_cursor_path,
            "effect_cursor_store_path": effect_cursor_path,
        }
    )

    wiring = build_effect_worker_wiring(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
        include_default_domain_handlers=False,
    )

    assert wiring.cursor_path == effect_cursor_path

    wiring.effect_cursor_repository.record_processed_event(
        event_id="event-1",
        source_node_id="trigger-1",
    )

    assert effect_cursor_path.exists()
    effect_cursor_payload = json.loads(effect_cursor_path.read_text(encoding="utf-8"))
    assert effect_cursor_payload["last_processed_event_id"] == "event-1"
    assert not gate_cursor_path.exists()


def test_effect_worker_runs_wal_checkpoint_maintenance_from_worker_cycle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "orchestration_wal_checkpoint_min_bytes": 0,
            "orchestration_wal_checkpoint_interval_seconds": 300,
            "triggers": [],
            "gates": [],
            "effects": [],
            "queries": [],
            "edges": [],
        }
    )
    from threading import Event, get_ident

    completed = Event()
    cycle_calls = []

    def maintenance(config, *, run_garbage_collection):
        assert run_garbage_collection is True
        cycle_calls.append((config.orchestration_store_path, get_ident()))
        completed.set()
        return OrchestrationWalCheckpointResult(attempted=True, wal_size_bytes=128)

    monkeypatch.setattr(wal_maintenance, "run_storage_maintenance", maintenance)
    worker = EffectOrchestratorWorker(config=config, worker_config=worker_config, runtime=None)
    worker._wal_checkpoint_maintenance.clock = lambda: 0.0
    try:
        assert cycle_calls == []
        worker.run_once()
        assert completed.wait(1)
        worker.run_once()
        assert len(cycle_calls) == 1
        assert cycle_calls[0][0] == worker_config.orchestration_store_path
        assert cycle_calls[0][1] != get_ident()
    finally:
        worker.close()


def test_core_minimal_profile_smoke_builds_trigger_and_effect_workers_without_builtin_providers(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = canonical_worker_config(
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
