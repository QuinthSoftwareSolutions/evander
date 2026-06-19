from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from agent_sandbox.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from agent_sandbox.daemons.effect_orchestrator import EffectOrchestratorWorker
from agent_sandbox.events.models import TriggerEvent


def _worker(
    tmp_path: Path,
    *,
    count: Any = 3,
    timeout: Any = "PT10M",
    key: Any = "group:${{trigger-1.group}}",
    include_downstream_map: bool = False,
) -> EffectOrchestratorWorker:
    runtime_dir = tmp_path / "runtime"
    gates: list[dict[str, Any]] = [
        {
            "id": "gate-buffer",
            "display_name": "Buffer",
            "type": "gate:buffer",
            "key": key,
            "count": count,
            "timeout": timeout,
        }
    ]
    edges: list[dict[str, str]] = [{"source_id": "trigger-1", "target_id": "gate-buffer"}]
    if include_downstream_map:
        gates.append(
            {
                "id": "gate-map",
                "display_name": "Map after buffer",
                "type": "gate:map",
                "template": {
                    "latest_value": "${{trigger-1.value}}",
                    "buffer_count": "${{gate-buffer.count}}",
                    "release_reason": "${{gate-buffer.release_reason}}",
                },
            }
        )
        edges.append({"source_id": "gate-buffer", "target_id": "gate-map"})

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
                    "outputs": {
                        "group": {"type": "string"},
                        "value": {"type": "integer"},
                        "desired_count": {"type": "string"},
                    },
                }
            ],
            "gates": gates,
            "edges": edges,
        }
    )
    return EffectOrchestratorWorker(
        config=config, worker_config=worker_config, runtime=None  # type: ignore[arg-type]
    )


def _publish(
    worker: EffectOrchestratorWorker,
    event_id: str,
    group: str,
    value: int,
    *,
    desired_count: str = "3",
    occurred_at: datetime | None = None,
) -> None:
    index = int(event_id.rsplit("-", 1)[-1]) if event_id.rsplit("-", 1)[-1].isdigit() else 0
    worker.event_queue.publish(
        TriggerEvent(
            event_id=event_id,
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            trace_id=f"trace-{group}",
            occurred_at=occurred_at or datetime(2026, 6, 19, 12, index, tzinfo=UTC),
            payload={
                "group": group,
                "value": value,
                "desired_count": desired_count,
            },
        )
    )


def _run_cycles(worker: EffectOrchestratorWorker, count: int = 4) -> None:
    for _ in range(count):
        worker.run_once()


def _buffer_provider(worker: EffectOrchestratorWorker):
    provider = worker._get_gate_provider("gate:buffer")
    assert provider is not None
    return provider


def _make_all_buffer_states_due(worker: EffectOrchestratorWorker) -> None:
    provider = _buffer_provider(worker)
    adapter = provider.load_state()
    now = datetime.now(UTC)
    for key, state in adapter.read_all().items():
        state.scheduled_release_at = now - timedelta(seconds=1)
        adapter.put(key, state)


def _gate_records(worker: EffectOrchestratorWorker, gate_id: str = "gate-buffer") -> list[dict[str, Any]]:
    path = worker.worker_config.gate_execution_store_path
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [record for record in payload.values() if record.get("gate_id") == gate_id]


def _succeeded_buffer_records(worker: EffectOrchestratorWorker) -> list[dict[str, Any]]:
    return [record for record in _gate_records(worker) if record["status"] == "succeeded"]


def _completion_events(worker: EffectOrchestratorWorker) -> list[dict[str, Any]]:
    payload = json.loads(worker.worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    return [event for event in payload.values() if event.get("trigger_id") == "gate-buffer"]


def test_buffer_below_threshold_does_not_publish_downstream(tmp_path: Path) -> None:
    worker = _worker(tmp_path, count=3)
    _publish(worker, "event-1", "a", 1)
    _publish(worker, "event-2", "a", 2)

    _run_cycles(worker)

    assert _succeeded_buffer_records(worker) == []
    assert _completion_events(worker) == []
    states = list(_buffer_provider(worker).load_state().read_all().values())
    assert len(states) == 1
    assert [item.payload["value"] for item in states[0].items] == [1, 2]


def test_buffer_releases_one_batch_when_count_is_reached(tmp_path: Path) -> None:
    worker = _worker(tmp_path, count=3)
    _publish(worker, "event-1", "a", 1)
    _publish(worker, "event-2", "a", 2)
    _publish(worker, "event-3", "a", 3)

    _run_cycles(worker)

    records = _succeeded_buffer_records(worker)
    assert len(records) == 1
    assert records[0]["event_id"] == "event-3"
    assert records[0]["outputs"] == {
        "items": [
            {"group": "a", "value": 1, "desired_count": "3"},
            {"group": "a", "value": 2, "desired_count": "3"},
            {"group": "a", "value": 3, "desired_count": "3"},
        ],
        "count": 3,
        "release_reason": "count",
    }

    events = _completion_events(worker)
    assert len(events) == 1
    assert events[0]["payload"]["count"] == 3
    assert events[0]["payload"]["release_reason"] == "count"


def test_buffer_partitions_by_resolved_key(tmp_path: Path) -> None:
    worker = _worker(tmp_path, count=2)
    _publish(worker, "event-1", "a", 1)
    _publish(worker, "event-2", "b", 10)
    _publish(worker, "event-3", "a", 2)

    _run_cycles(worker)

    records = _succeeded_buffer_records(worker)
    assert len(records) == 1
    assert records[0]["resolved_config"]["key"] == "group:a"
    assert [item["value"] for item in records[0]["outputs"]["items"]] == [1, 2]

    remaining_states = list(_buffer_provider(worker).load_state().read_all().values())
    assert len(remaining_states) == 1
    assert remaining_states[0].buffer_key == "group:b"
    assert [item.payload["value"] for item in remaining_states[0].items] == [10]


def test_buffer_timeout_reconciliation_releases_partial_batch(tmp_path: Path) -> None:
    worker = _worker(tmp_path, count=5, timeout="PT10M")
    _publish(worker, "event-1", "a", 1)
    _publish(worker, "event-2", "a", 2)

    _run_cycles(worker)
    _make_all_buffer_states_due(worker)
    worker.run_once()

    records = _succeeded_buffer_records(worker)
    assert len(records) == 1
    assert records[0]["event_id"] == "event-2"
    assert records[0]["outputs"]["count"] == 2
    assert records[0]["outputs"]["release_reason"] == "timeout"
    assert [item["value"] for item in records[0]["outputs"]["items"]] == [1, 2]


def test_buffer_releases_overdue_timeout_batch_before_appending_late_activation(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path, count=5, timeout="PT10M")
    now = datetime.now(UTC).replace(microsecond=0)
    _publish(worker, "event-1", "a", 1, occurred_at=now)
    worker.run_once()

    provider = _buffer_provider(worker)
    adapter = provider.load_state()
    state_key, state = next(iter(adapter.read_all().items()))
    state.scheduled_release_at = now + timedelta(minutes=1)
    adapter.put(state_key, state)

    _publish(worker, "event-2", "a", 2, occurred_at=now + timedelta(minutes=2))
    worker.run_once()

    records = _succeeded_buffer_records(worker)
    assert len(records) == 1
    assert records[0]["event_id"] == "event-1"
    assert records[0]["outputs"] == {
        "items": [{"group": "a", "value": 1, "desired_count": "3"}],
        "count": 1,
        "release_reason": "timeout",
    }

    remaining_states = list(provider.load_state().read_all().values())
    assert len(remaining_states) == 1
    assert [item.payload["value"] for item in remaining_states[0].items] == [2]
    assert remaining_states[0].first_event_id == "event-2"


def test_buffer_duplicate_event_id_does_not_double_count(tmp_path: Path) -> None:
    worker = _worker(tmp_path, count=2)
    _publish(worker, "event-1", "a", 1)
    worker.run_once()

    worker.event_queue.mark_delivered("event-1")
    record = worker.event_queue._store.get("event-1")
    assert record is not None
    record.delivery_state = "pending"
    worker.event_queue._store.put("event-1", record)
    worker.run_once()

    states = list(_buffer_provider(worker).load_state().read_all().values())
    assert len(states) == 1
    assert len(states[0].items) == 1
    assert states[0].seen_event_ids == ["event-1"]

    _publish(worker, "event-2", "a", 2)
    _run_cycles(worker)

    records = _succeeded_buffer_records(worker)
    assert len(records) == 1
    assert records[0]["outputs"]["count"] == 2
    assert [item["value"] for item in records[0]["outputs"]["items"]] == [1, 2]


def test_buffer_timeout_release_preserves_upstream_binding_context(tmp_path: Path) -> None:
    worker = _worker(tmp_path, count=5, timeout="PT10M", include_downstream_map=True)
    _publish(worker, "event-1", "a", 7)
    worker.run_once()

    _make_all_buffer_states_due(worker)
    worker.run_once()
    worker.run_once()

    map_records = _gate_records(worker, gate_id="gate-map")
    assert len(map_records) == 1
    assert map_records[0]["status"] == "succeeded"
    assert map_records[0]["outputs"] == {
        "latest_value": 7,
        "buffer_count": 1,
        "release_reason": "timeout",
    }


def test_buffer_invalid_config_is_rejected_clearly(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="gate:buffer count must be a positive integer"):
        _worker(tmp_path, count=0)

    with pytest.raises(ValidationError, match="gate:buffer key must be a non-empty string"):
        _worker(tmp_path, key="")

    with pytest.raises(ValidationError, match="gate:buffer timeout must be an ISO-8601 duration"):
        _worker(tmp_path, timeout="not-a-duration")


def test_buffer_empty_timeout_is_count_only(tmp_path: Path) -> None:
    worker = _worker(tmp_path, count=2, timeout="")
    _publish(worker, "event-1", "a", 1)
    worker.run_once()

    state = next(iter(_buffer_provider(worker).load_state().read_all().values()))
    assert state.timeout_seconds is None
    assert state.scheduled_release_at is None

    _publish(worker, "event-2", "a", 2)
    _run_cycles(worker)

    records = _succeeded_buffer_records(worker)
    assert len(records) == 1
    assert records[0]["outputs"]["release_reason"] == "count"


def test_buffer_runtime_rejects_count_binding_that_resolves_non_integer(tmp_path: Path) -> None:
    worker = _worker(tmp_path, count="${{trigger-1.desired_count}}")
    _publish(worker, "event-1", "a", 1, desired_count="many")

    worker.run_once()

    records = _gate_records(worker)
    assert len(records) == 1
    assert records[0]["status"] == "failed"
    assert records[0]["error"] == "gate:buffer count must resolve to a positive integer"
