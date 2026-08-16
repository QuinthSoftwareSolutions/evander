from __future__ import annotations

from config_helpers import canonical_worker_config

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.events.models import GateDedupeDefinition, TriggerEvent
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
        _edge("trigger-comment", "gate-dedupe"),
        _edge("gate-dedupe", "effect-prompt"),
    ]
    if include_dropped_edge:
        edges.append(_edge("gate-dedupe", "effect-prompt", source_port="dropped"))

    return canonical_worker_config(
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
                        "issue_id": {"type": "integer"},
                        "comment_id": {"type": "integer"},
                        "body": {"type": "string"},
                    },
                }
            ],
            "gates": [
                {
                    "id": "gate-dedupe",
                    "display_name": "Dedupe",
                    "type": "gate:dedupe",
                    "config": {
                        "key": (
                            "issue-comment:${{trigger-comment.issue_id}}:"
                            "${{trigger-comment.comment_id}}"
                        ),
                        "ttl": "P7D",
                    },
                    "outputs": {
                        "issue_id": {"type": "integer"},
                        "comment_id": {"type": "integer"},
                        "body": {"type": "string"},
                    },
                }
            ],
            "effects": [
                {
                    "id": "effect-prompt",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "prompt": "Process ${{gate-dedupe.body}}",
                }
            ],
            "edges": edges,
        }
    )


def _worker(tmp_path: Path, *, include_dropped_edge: bool = False) -> EffectOrchestratorWorker:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    return EffectOrchestratorWorker(
        config=config,
        worker_config=_worker_config(runtime_dir, include_dropped_edge=include_dropped_edge),
        runtime=None,  # type: ignore[arg-type]
    )


def _publish_comment(
    worker: EffectOrchestratorWorker, *, event_id: str, issue_id: int, comment_id: int
) -> None:
    worker.event_queue.publish(
        TriggerEvent(
            event_id=event_id,
            trigger_id="trigger-comment",
            trigger_type="trigger:cron",
            trace_id=f"trace-{event_id}",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={
                "issue_id": issue_id,
                "comment_id": comment_id,
                "body": f"comment-{comment_id}",
            },
        )
    )


def _run_cycles(worker: EffectOrchestratorWorker, count: int = 4) -> None:
    for _ in range(count):
        worker.run_once()


def _gate_records(worker: EffectOrchestratorWorker) -> list[dict[str, object]]:
    payload = json.loads(worker.worker_config.gate_execution_store_path.read_text(encoding="utf-8"))
    return list(payload.values())


def _completion_events(worker: EffectOrchestratorWorker) -> list[dict[str, object]]:
    payload = json.loads(worker.worker_config.event_queue_store_path.read_text(encoding="utf-8"))
    return [item for item in payload.values() if item.get("trigger_id") == "gate-dedupe"]


def test_dedupe_gate_first_occurrence_passes(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    _publish_comment(worker, event_id="event-1", issue_id=123, comment_id=456)

    _run_cycles(worker)

    records = _gate_records(worker)
    assert len(records) == 1
    assert records[0]["status"] == "succeeded"
    assert records[0]["resolved_config"]["allowed"] is True
    assert records[0]["outputs"]["body"] == "comment-456"
    assert len(_completion_events(worker)) == 1


def test_dedupe_gate_duplicate_occurrence_is_suppressed(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    _publish_comment(worker, event_id="event-1", issue_id=123, comment_id=456)
    _publish_comment(worker, event_id="event-2", issue_id=123, comment_id=456)

    _run_cycles(worker, count=8)

    records = sorted(_gate_records(worker), key=lambda item: str(item["event_id"]))
    assert [record["status"] for record in records] == ["succeeded", "dropped"]
    assert records[1]["resolved_config"]["allowed"] is False
    assert "duplicate" in records[1]["error"]
    assert len(_completion_events(worker)) == 1


def test_dedupe_gate_duplicate_publishes_dropped_branch_when_wired(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path, include_dropped_edge=True)
    graph = worker._build_execution_graph()
    gate = graph.gates_by_id["gate-dedupe"]

    for index in range(2):
        record = TriggerQueueRecord(
            event_id=f"event-{index}",
            trigger_id="trigger-comment",
            trigger_type="trigger:cron",
            trace_id=f"trace-{index}",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"issue_id": 123, "comment_id": 456, "body": "same"},
        )
        worker._execute_gate(record, gate=gate, graph=graph, config_revision="test")

    records = sorted(_gate_records(worker), key=lambda item: str(item["event_id"]))
    assert [record["status"] for record in records] == ["succeeded", "dropped"]
    assert records[1]["resolved_config"]["allowed"] is False
    assert "duplicate" in records[1]["error"]
    assert records[1]["outputs"]["body"] == "same"

    events = _completion_events(worker)
    assert len(events) == 2
    dropped_event = next(
        event for event in events if event["payload"].get("__branch_port") == "dropped"
    )
    assert dropped_event["payload"]["body"] == "same"


def test_dedupe_gate_distinct_keys_pass_independently(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    _publish_comment(worker, event_id="event-1", issue_id=123, comment_id=456)
    _publish_comment(worker, event_id="event-2", issue_id=123, comment_id=789)

    _run_cycles(worker, count=8)

    records = _gate_records(worker)
    assert sorted(record["status"] for record in records) == ["succeeded", "succeeded"]
    assert len(_completion_events(worker)) == 2


def test_dedupe_gate_expired_key_passes_again(tmp_path: Path) -> None:
    worker = _worker(tmp_path)
    _publish_comment(worker, event_id="event-1", issue_id=123, comment_id=456)
    _run_cycles(worker)

    store_path = worker.worker_config.runtime_dir / "gate-dedupe-keys.json"
    store_payload = json.loads(store_path.read_text(encoding="utf-8"))
    for entry in store_payload["entries"].values():
        entry["expires_at"] = "2026-04-01T00:00:00Z"
    store_path.write_text(json.dumps(store_payload), encoding="utf-8")

    _publish_comment(worker, event_id="event-2", issue_id=123, comment_id=456)
    _run_cycles(worker)

    records = sorted(_gate_records(worker), key=lambda item: str(item["event_id"]))
    assert [record["status"] for record in records] == ["succeeded", "succeeded"]
    assert len(_completion_events(worker)) == 2


def test_dedupe_gate_concurrent_duplicates_publish_one_downstream_activation(
    tmp_path: Path,
) -> None:
    worker = _worker(tmp_path)
    graph = worker._build_execution_graph()
    gate = graph.gates_by_id["gate-dedupe"]

    def execute(index: int) -> None:
        record = TriggerQueueRecord(
            event_id=f"event-{index}",
            trigger_id="trigger-comment",
            trigger_type="trigger:cron",
            trace_id=f"trace-{index}",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            payload={"issue_id": 123, "comment_id": 456, "body": "same"},
        )
        worker._execute_gate(record, gate=gate, graph=graph, config_revision="test")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(execute, range(8)))

    assert len(_completion_events(worker)) == 1
    statuses = [record["status"] for record in _gate_records(worker)]
    assert statuses.count("succeeded") == 1
    assert statuses.count("dropped") == 7


def test_dedupe_gate_missing_key_config_fails_clearly() -> None:
    with pytest.raises(ValidationError, match="key"):
        GateDedupeDefinition.model_validate(
            {"id": "gate-dedupe", "display_name": "Dedupe", "type": "gate:dedupe", "ttl": "P7D"}
        )


def test_dedupe_gate_invalid_empty_key_config_fails_clearly() -> None:
    with pytest.raises(ValidationError, match="key must be a non-empty string"):
        GateDedupeDefinition.model_validate(
            {
                "id": "gate-dedupe",
                "display_name": "Dedupe",
                "type": "gate:dedupe",
                "key": "  ",
                "ttl": "P7D",
            }
        )
