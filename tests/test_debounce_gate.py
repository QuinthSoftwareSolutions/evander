from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.daemons.effect_orchestrator import EffectOrchestratorWorker
from agent_sandbox.events.models import TriggerEvent


def _worker(tmp_path: Path) -> EffectOrchestratorWorker:
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
                    "display_name": "Incoming",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"group": {"type": "string"}, "value": {"type": "integer"}},
                }
            ],
            "gates": [
                {
                    "id": "debounce-1",
                    "display_name": "Debounce",
                    "type": "gate:debounce",
                    "key": "group:${{trigger-1.group}}",
                    "quiet_period": "PT1M",
                    "max_delay": "PT10M",
                }
            ],
            "edges": [{"source_id": "trigger-1", "target_id": "debounce-1"}],
        }
    )
    return EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None  # type: ignore[arg-type]
    )


def _publish(worker: EffectOrchestratorWorker, event_id: str, group: str, value: int) -> None:
    worker.event_queue.publish(
        TriggerEvent(
            event_id=event_id,
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime.now(UTC),
            payload={"group": group, "value": value},
        )
    )


def _debounce_provider(worker: EffectOrchestratorWorker):
    provider = worker._get_gate_provider("gate:debounce")
    assert provider is not None
    return provider


def _make_all_states_due(worker: EffectOrchestratorWorker, *, reason: str = "quiet_period") -> None:
    provider = _debounce_provider(worker)
    adapter = provider.load_state()
    now = datetime.now(UTC)
    for key, state in adapter.read_all().items():
        if reason == "max_delay":
            state.first_seen_at = now - timedelta(minutes=11)
            state.max_release_at = now - timedelta(seconds=1)
            state.scheduled_release_at = now + timedelta(minutes=5)
        else:
            state.last_seen_at = now - timedelta(minutes=2)
            state.scheduled_release_at = now - timedelta(minutes=1)
            state.max_release_at = now + timedelta(minutes=8)
        adapter.put(key, state)


def _succeeded_gate_records(worker: EffectOrchestratorWorker) -> list[dict[str, object]]:
    path = worker.worker_config.runtime_dir / "gate-orchestrator-executions.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [record for record in payload.values() if record["status"] == "succeeded"]


def test_debounce_single_event_releases_after_quiet_period(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    _publish(worker, "event-1", "a", 1)

    worker.run_once()
    assert _succeeded_gate_records(worker) == []

    _make_all_states_due(worker)
    worker.run_once()

    records = _succeeded_gate_records(worker)
    assert len(records) == 1
    assert records[0]["event_id"] == "event-1"
    assert records[0]["outputs"] == {"group": "a", "value": 1}
    assert records[0]["resolved_config"]["release_reason"] == "quiet_period"


def test_debounce_multiple_events_with_same_key_release_once_with_latest_payload(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)
    _publish(worker, "event-1", "a", 1)
    _publish(worker, "event-2", "a", 2)

    worker.run_once()
    _make_all_states_due(worker)
    worker.run_once()

    records = _succeeded_gate_records(worker)
    assert len(records) == 1
    assert records[0]["event_id"] == "event-2"
    assert records[0]["outputs"] == {"group": "a", "value": 2}


def test_debounce_different_keys_release_independently(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    _publish(worker, "event-1", "a", 1)
    _publish(worker, "event-2", "b", 2)

    worker.run_once()
    provider = _debounce_provider(worker)
    assert len(provider.load_state().read_all()) == 2

    _make_all_states_due(worker)
    worker.run_once()

    records = sorted(_succeeded_gate_records(worker), key=lambda record: str(record["event_id"]))
    assert [record["event_id"] for record in records] == ["event-1", "event-2"]
    assert [record["outputs"] for record in records] == [
        {"group": "a", "value": 1},
        {"group": "b", "value": 2},
    ]


def test_debounce_max_delay_forces_release(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    _publish(worker, "event-1", "a", 1)
    _publish(worker, "event-2", "a", 2)

    worker.run_once()
    _make_all_states_due(worker, reason="max_delay")
    worker.run_once()

    records = _succeeded_gate_records(worker)
    assert len(records) == 1
    assert records[0]["event_id"] == "event-2"
    assert records[0]["resolved_config"]["release_reason"] == "max_delay"


def test_debounce_duplicate_event_does_not_emit_twice(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    _publish(worker, "event-1", "a", 1)
    worker.run_once()

    worker.event_queue.mark_delivered("event-1")
    record = worker.event_queue._store.get("event-1")
    assert record is not None
    record.delivery_state = "pending"
    worker.event_queue._store.put("event-1", record)
    worker.run_once()

    provider = _debounce_provider(worker)
    states = list(provider.load_state().read_all().values())
    assert len(states) == 1
    assert states[0].seen_event_ids == ["event-1"]

    _make_all_states_due(worker)
    worker.run_once()
    worker.run_once()

    records = _succeeded_gate_records(worker)
    assert len(records) == 1
    assert records[0]["event_id"] == "event-1"


def test_debounce_invalid_config_is_rejected_clearly(tmp_path: Path) -> None:
    with pytest.raises(
        ValidationError,
        match="gate:debounce max_delay must be greater than or equal to quiet_period",
    ):
        TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {
                "runtime_dir": tmp_path / "runtime",
                "triggers": [
                    {
                        "id": "trigger-1",
                        "display_name": "Incoming",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    }
                ],
                "gates": [
                    {
                        "id": "debounce-1",
                        "display_name": "Debounce",
                        "type": "gate:debounce",
                        "key": "issue:1",
                        "quiet_period": "PT10M",
                        "max_delay": "PT1M",
                    }
                ],
            }
        )
