from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.events.models import GateThrottleDefinition
from evander.triggers.queue import TriggerQueueRecord


def _edge(source_id: str, target_id: str, *, source_port: str | None = None) -> dict[str, object]:
    edge: dict[str, object] = {"source_id": source_id, "target_id": target_id}
    if source_port is not None:
        edge["source_port"] = source_port
    return edge


def _worker_config(
    runtime_dir: Path, *, include_dropped_edge: bool = False
) -> TriggerOrchestratorWorkerGlobalConfig:
    edges = [
        _edge("trigger-comment", "gate-throttle"),
        _edge("gate-throttle", "effect-prompt"),
    ]
    if include_dropped_edge:
        edges.append(_edge("gate-throttle", "effect-prompt", source_port="dropped"))
    return TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [
                {
                    "id": "trigger-comment",
                    "display_name": "Comment",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {
                        "group": {"type": "string"},
                        "quota": {"type": "integer"},
                        "body": {"type": "string"},
                    },
                }
            ],
            "gates": [
                {
                    "id": "gate-throttle",
                    "display_name": "Throttle",
                    "type": "gate:throttle",
                    "config": {
                        "key": "group:${{trigger-comment.group}}",
                        "window": "PT1H",
                        "limit": "${{trigger-comment.quota}}",
                    },
                }
            ],
            "effects": [
                {
                    "id": "effect-prompt",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "prompt": "Process ${{gate-throttle.body}}",
                }
            ],
            "edges": edges,
        }
    )


def _worker(tmp_path: Path, *, include_dropped_edge: bool = False) -> EffectOrchestratorWorker:
    runtime_dir = tmp_path / "runtime"
    return EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=runtime_dir),
        worker_config=_worker_config(runtime_dir, include_dropped_edge=include_dropped_edge),
        runtime=None,  # type: ignore[arg-type]
    )


def _execute(
    worker: EffectOrchestratorWorker,
    *,
    event_id: str,
    group: str = "a",
    quota: int = 2,
    body: str | None = None,
) -> None:
    graph = worker._build_execution_graph()
    worker._execute_gate(
        TriggerQueueRecord(
            event_id=event_id,
            trigger_id="trigger-comment",
            trigger_type="trigger:cron",
            trace_id=f"trace-{event_id}",
            occurred_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
            payload={"group": group, "quota": quota, "body": body or event_id},
        ),
        gate=graph.gates_by_id["gate-throttle"],
        graph=graph,
        config_revision="test",
    )


def _gate_records(worker: EffectOrchestratorWorker) -> list[dict[str, object]]:
    path = worker.worker_config.runtime_dir / "gate-orchestrator-executions.json"
    return list(json.loads(path.read_text(encoding="utf-8")).values())


def _completion_events(worker: EffectOrchestratorWorker) -> list[dict[str, object]]:
    payload = json.loads(worker.worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    return [item for item in payload.values() if item.get("trigger_id") == "gate-throttle"]


def test_throttle_freezes_a_bound_limit_for_the_open_window(tmp_path: Path) -> None:
    worker = _worker(tmp_path)

    _execute(worker, event_id="event-1", quota=2)
    _execute(worker, event_id="event-2", quota=1)
    _execute(worker, event_id="event-3", quota=99)

    records = sorted(_gate_records(worker), key=lambda record: str(record["event_id"]))
    assert [record["status"] for record in records] == ["succeeded", "succeeded", "dropped"]
    assert [record["resolved_config"]["limit"] for record in records] == [2, 2, 2]
    assert records[2]["resolved_config"]["accepted_count"] == 2
    assert records[2]["resolved_config"]["diagnostic"] == "dropped:window_limit_exceeded"
    assert len(_completion_events(worker)) == 2


def test_throttle_routes_overflow_to_the_dropped_port_when_wired(tmp_path: Path) -> None:
    worker = _worker(tmp_path, include_dropped_edge=True)

    _execute(worker, event_id="event-1", quota=1, body="allowed")
    _execute(worker, event_id="event-2", quota=1, body="overflow")

    records = sorted(_gate_records(worker), key=lambda record: str(record["event_id"]))
    assert [record["status"] for record in records] == ["succeeded", "dropped"]
    assert records[1]["outputs"] == {"group": "a", "quota": 1, "body": "overflow"}
    dropped_event = next(
        event for event in _completion_events(worker) if event["payload"].get("__branch_port") == "dropped"
    )
    assert dropped_event["payload"]["body"] == "overflow"


def test_throttle_scopes_windows_by_key_and_opens_a_fresh_window_after_expiry(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)

    _execute(worker, event_id="event-a-1", group="a", quota=1)
    _execute(worker, event_id="event-b-1", group="b", quota=1)
    _execute(worker, event_id="event-a-2", group="a", quota=1)

    store_path = worker.worker_config.runtime_dir / "gate-throttle-windows.json"
    store = json.loads(store_path.read_text(encoding="utf-8"))
    a_entry = next(entry for entry in store["entries"].values() if entry["key"] == "group:a")
    a_entry["window_expires_at"] = "2026-08-01T00:00:00Z"
    store_path.write_text(json.dumps(store), encoding="utf-8")

    _execute(worker, event_id="event-a-3", group="a", quota=2)
    _execute(worker, event_id="event-a-4", group="a", quota=99)

    records = {str(record["event_id"]): record for record in _gate_records(worker)}
    assert records["event-a-1"]["status"] == "succeeded"
    assert records["event-b-1"]["status"] == "succeeded"
    assert records["event-a-2"]["status"] == "dropped"
    assert records["event-a-3"]["resolved_config"]["limit"] == 2
    assert records["event-a-4"]["status"] == "succeeded"
    assert records["event-a-4"]["resolved_config"]["limit"] == 2


def test_throttle_persists_across_workers_and_resets_at_the_expiry_boundary(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)
    _execute(worker, event_id="event-1", quota=1)

    restarted_worker = EffectOrchestratorWorker(
        config=GatewayConfig(runtime_dir=worker.worker_config.runtime_dir),
        worker_config=_worker_config(worker.worker_config.runtime_dir),
        runtime=None,  # type: ignore[arg-type]
    )
    _execute(restarted_worker, event_id="event-2", quota=1)

    persisted_record = next(
        record for record in _gate_records(restarted_worker) if record["event_id"] == "event-2"
    )
    assert persisted_record["status"] == "dropped"

    opened_at = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
    first_claim = restarted_worker.claim_throttle_window(
        gate_id="gate-throttle",
        key="boundary",
        window=timedelta(minutes=1),
        window_text="PT1M",
        limit=1,
        now=opened_at,
        event_id="boundary-1",
    )
    expiry_claim = restarted_worker.claim_throttle_window(
        gate_id="gate-throttle",
        key="boundary",
        window=timedelta(minutes=1),
        window_text="PT1M",
        limit=1,
        now=opened_at + timedelta(minutes=1),
        event_id="boundary-2",
    )

    assert first_claim["allowed"] is True
    assert expiry_claim["allowed"] is True
    assert expiry_claim["accepted_count"] == 1
    assert expiry_claim["window_opened_at"] != first_claim["window_opened_at"]


def test_throttle_is_atomic_under_concurrent_arrivals_and_replay_does_not_consume_quota(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)
    graph = worker._build_execution_graph()
    gate = graph.gates_by_id["gate-throttle"]

    def execute(index: int) -> None:
        record = TriggerQueueRecord(
            event_id=f"event-{index}",
            trigger_id="trigger-comment",
            trigger_type="trigger:cron",
            trace_id=f"trace-{index}",
            occurred_at=datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc),
            payload={"group": "a", "quota": 3, "body": f"body-{index}"},
        )
        worker._execute_gate(record, gate=gate, graph=graph, config_revision="test")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(execute, range(8)))

    records = _gate_records(worker)
    assert [record["status"] for record in records].count("succeeded") == 3
    assert [record["status"] for record in records].count("dropped") == 5

    _execute(worker, event_id="event-0", quota=3)
    assert len(_gate_records(worker)) == 8


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"key": "", "window": "PT1M", "limit": 1}, "key"),
        ({"key": "group:a", "window": "${{trigger.window}}", "limit": 1}, "window"),
        ({"key": "group:a", "window": "PT0S", "limit": 1}, "greater than zero"),
        ({"key": "group:a", "window": "PT1M", "limit": 0}, "limit"),
    ],
)
def test_throttle_definition_rejects_invalid_configuration(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        GateThrottleDefinition.model_validate(
            {
                "id": "gate-throttle",
                "display_name": "Throttle",
                "type": "gate:throttle",
                **payload,
            }
        )
