from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.events.models import GateCounterDefinition
from evander.triggers.queue import TriggerQueueRecord


def _worker_config(runtime_dir: Path) -> TriggerOrchestratorWorkerGlobalConfig:
    return TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-message",
                    "display_name": "Message",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {
                        "sender_name": {"type": "string"},
                        "body": {"type": "string"},
                    },
                }
            ],
            "gates": [
                {
                    "id": "gate-counter",
                    "display_name": "Counter",
                    "type": "gate:counter",
                    "config": {
                        "key": "user:${{trigger-message.sender_name}}",
                        "window": "PT1H",
                    },
                }
            ],
            "effects": [
                {
                    "id": "effect-prompt",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "prompt": "Message ${{gate-counter.body}} number ${{gate-counter.count}}",
                }
            ],
            "edges": [
                {"source_id": "trigger-message", "target_id": "gate-counter"},
                {"source_id": "gate-counter", "target_id": "effect-prompt"},
            ],
        }
    )


def _worker(tmp_path: Path) -> EffectOrchestratorWorker:
    runtime_dir = tmp_path / "runtime"
    return EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=runtime_dir),
        worker_config=_worker_config(runtime_dir),
        runtime=None,  # type: ignore[arg-type]
    )


def _execute(
    worker: EffectOrchestratorWorker,
    *,
    event_id: str,
    sender_name: str = "Ada",
    body: str | None = None,
    original_count: int | None = None,
) -> None:
    graph = worker._build_execution_graph()
    payload: dict[str, object] = {
        "sender_name": sender_name,
        "body": body or event_id,
    }
    if original_count is not None:
        payload["count"] = original_count
    worker._execute_gate(
        TriggerQueueRecord(
            event_id=event_id,
            trigger_id="trigger-message",
            trigger_type="trigger:cron",
            trace_id=f"trace-{event_id}",
            occurred_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
            payload=payload,
        ),
        gate=graph.gates_by_id["gate-counter"],
        graph=graph,
        config_revision="test",
    )


def _gate_records(worker: EffectOrchestratorWorker) -> list[dict[str, object]]:
    path = worker.worker_config.runtime_dir / "gate-orchestrator-executions.json"
    return list(json.loads(path.read_text(encoding="utf-8")).values())


def _completion_events(worker: EffectOrchestratorWorker) -> list[dict[str, object]]:
    payload = json.loads(worker.worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    return [item for item in payload.values() if item.get("trigger_id") == "gate-counter"]


def test_counter_forwards_each_event_with_its_keyed_fixed_window_count(tmp_path: Path) -> None:
    worker = _worker(tmp_path)

    _execute(worker, event_id="event-ada-1", original_count=99)
    _execute(worker, event_id="event-ben-1", sender_name="Ben")
    _execute(worker, event_id="event-ada-2")

    records = {str(record["event_id"]): record for record in _gate_records(worker)}
    assert all(record["status"] == "succeeded" for record in records.values())
    assert records["event-ada-1"]["outputs"] == {
        "sender_name": "Ada",
        "body": "event-ada-1",
        "count": 1,
    }
    assert records["event-ben-1"]["outputs"]["count"] == 1
    assert records["event-ada-2"]["outputs"]["count"] == 2
    assert len(_completion_events(worker)) == 3


def test_counter_opens_a_fresh_window_after_expiry(tmp_path: Path) -> None:
    worker = _worker(tmp_path)

    _execute(worker, event_id="event-1")
    store_path = worker.worker_config.runtime_dir / "gate-counter-windows.json"
    store = json.loads(store_path.read_text(encoding="utf-8"))
    only_entry = next(iter(store["entries"].values()))
    only_entry["window_expires_at"] = "2026-08-01T00:00:00Z"
    store_path.write_text(json.dumps(store), encoding="utf-8")

    _execute(worker, event_id="event-2")

    records = {str(record["event_id"]): record for record in _gate_records(worker)}
    assert records["event-2"]["outputs"]["count"] == 1

    opened_at = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    first_claim = worker.claim_counter_window(
        gate_id="gate-counter",
        key="boundary",
        window=timedelta(minutes=1),
        window_text="PT1M",
        now=opened_at,
        event_id="boundary-1",
    )
    expiry_claim = worker.claim_counter_window(
        gate_id="gate-counter",
        key="boundary",
        window=timedelta(minutes=1),
        window_text="PT1M",
        now=opened_at + timedelta(minutes=1),
        event_id="boundary-2",
    )
    assert first_claim["count"] == 1
    assert expiry_claim["count"] == 1
    assert expiry_claim["window_opened_at"] != first_claim["window_opened_at"]


def test_counter_is_atomic_and_replays_do_not_increment(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    graph = worker._build_execution_graph()
    gate = graph.gates_by_id["gate-counter"]

    def execute(index: int) -> None:
        worker._execute_gate(
            TriggerQueueRecord(
                event_id=f"event-{index}",
                trigger_id="trigger-message",
                trigger_type="trigger:cron",
                trace_id=f"trace-{index}",
                occurred_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
                payload={"sender_name": "Ada", "body": f"body-{index}"},
            ),
            gate=gate,
            graph=graph,
            config_revision="test",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(execute, range(8)))

    records = {str(record["event_id"]): record for record in _gate_records(worker)}
    counts = sorted(record["outputs"]["count"] for record in records.values())
    assert counts == list(range(1, 9))

    replayed_claim = worker.claim_counter_window(
        gate_id="gate-counter",
        key="user:Ada",
        window=timedelta(hours=1),
        window_text="PT1H",
        now=datetime.now(timezone.utc),
        event_id="event-0",
    )
    assert replayed_claim["count"] == records["event-0"]["outputs"]["count"]
    assert replayed_claim["decision_replayed"] is True


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"key": "", "window": "PT1M"}, "key"),
        ({"key": "user:ada", "window": "${{trigger.window}}"}, "window"),
        ({"key": "user:ada", "window": "PT0S"}, "greater than zero"),
    ],
)
def test_counter_definition_rejects_invalid_configuration(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        GateCounterDefinition.model_validate(
            {
                "id": "gate-counter",
                "display_name": "Counter",
                "type": "gate:counter",
                **payload,
            }
        )
