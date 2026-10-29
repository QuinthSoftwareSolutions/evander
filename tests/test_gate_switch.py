from __future__ import annotations

from config_helpers import (
    canonical_worker_config,
    effect_execution_payload,
    gate_execution_payload,
    trigger_queue_payload,
)

from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from evander.config import GatewayConfig
from evander.daemons.effect_orchestrator import EffectOrchestratorWorker
from evander.effects.providers.base import EffectProviderBase
from evander.events.models import TriggerEvent


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        future: Future[object] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future


class _RecordingEffectProvider(EffectProviderBase):
    def __init__(self, effect_id: str, calls: list[str]) -> None:
        super().__init__()
        self._effect_id = effect_id
        self._calls = calls

    def execute(
        self,
        *,
        resolved_config: dict[str, object] | None = None,
        execution_context: dict[str, object] | None = None,
    ) -> dict[str, object]:
        del resolved_config, execution_context
        self._calls.append(self._effect_id)
        return {"effect_id": self._effect_id}


def _edge(source_id: str, target_id: str, *, source_port: str | None = None) -> dict[str, str]:
    edge = {"source_id": source_id, "target_id": target_id}
    if source_port is not None:
        edge["source_port"] = source_port
    return edge


def _worker(
    tmp_path: Path,
    *,
    switch_gate: dict[str, object],
    effects: list[dict[str, object]],
    edges: list[dict[str, str]],
) -> tuple[EffectOrchestratorWorker, list[str]]:
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
                    "display_name": "Incoming",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {
                        "route": {"type": "string"},
                        "state": {"type": "string"},
                        "ticket_id": {"type": "integer"},
                        "pr": {"type": "object"},
                    },
                }
            ],
            "gates": [switch_gate],
            "effects": effects,
            "edges": [_edge("trigger-1", "gate-switch"), *edges],
        }
    )
    worker = EffectOrchestratorWorker(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )
    worker._executor = _ImmediateExecutor()  # type: ignore[assignment]
    executed_effect_ids: list[str] = []

    class _ProviderFactory:
        registry = SimpleNamespace(
            resolve=lambda effect_type: _RecordingEffectProvider,
            registered_types=lambda: ("effect:agent:prompt",),
        )

        def build(self, effect):
            return _RecordingEffectProvider(effect.id, executed_effect_ids)

    worker.provider_factory = _ProviderFactory()
    return worker, executed_effect_ids


def _publish(
    worker: EffectOrchestratorWorker,
    *,
    event_id: str,
    payload: dict[str, object],
    trace_id: str = "trace-1",
) -> None:
    worker.event_queue.publish(
        TriggerEvent(
            event_id=event_id,
            trigger_id="trigger-1",
            trigger_type="trigger:cron",
            occurred_at=datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc),
            trace_id=trace_id,
            payload=payload,
        )
    )


def _run_cycles(worker: EffectOrchestratorWorker, count: int = 5) -> None:
    for _ in range(count):
        worker.run_once()


def _gate_records(worker: EffectOrchestratorWorker) -> list[dict[str, object]]:
    payload = gate_execution_payload(worker.worker_config)
    return list(payload.values())


def _effect_records(worker: EffectOrchestratorWorker) -> list[dict[str, object]]:
    payload = effect_execution_payload(worker.worker_config)
    return list(payload.values())


def _queue_records(worker: EffectOrchestratorWorker) -> list[dict[str, object]]:
    payload = trigger_queue_payload(worker.worker_config)
    return list(payload.values())


def test_switch_gate_first_match_wins_and_preserves_outputs(tmp_path: Path) -> None:
    worker, executed_effect_ids = _worker(
        tmp_path,
        switch_gate={
            "id": "gate-switch",
            "display_name": "Route by state",
            "type": "gate:switch",
            "default_port": "fallback",
            "cases": [
                {
                    "port": "ready",
                    "label": "Ready path",
                    "when": {
                        "left": "${{trigger-1.state}}",
                        "equals": "ready",
                    },
                },
                {
                    "port": "blocked",
                    "label": "Blocked path",
                    "match": {"route": "${{trigger-1.route}}"},
                },
            ],
        },
        effects=[
            {
                "id": "effect-ready",
                "display_name": "Ready effect",
                "type": "effect:agent:prompt",
                "prompt": "branch ${{gate-switch.state}}",
            },
            {
                "id": "effect-blocked",
                "display_name": "Blocked effect",
                "type": "effect:agent:prompt",
                "prompt": "branch ${{gate-switch.state}}",
            },
        ],
        edges=[
            _edge("gate-switch", "effect-ready", source_port="ready"),
            _edge("gate-switch", "effect-blocked", source_port="blocked"),
        ],
    )
    _publish(
        worker,
        event_id="event-ready",
        payload={
            "route": "ready",
            "state": "ready",
            "ticket_id": 7,
            "__for_execution_ids": {"gate-legacy": "exec-legacy"},
        },
    )

    _run_cycles(worker)

    gate_record = _gate_records(worker)[0]
    assert gate_record["status"] == "succeeded"
    assert gate_record["resolved_config"]["selected_port"] == "ready"
    assert gate_record["resolved_config"]["matched_case_index"] == 0
    assert gate_record["resolved_config"]["matched_case_label"] == "Ready path"
    assert gate_record["resolved_config"]["matched_by"] == "when"
    assert gate_record["resolved_config"]["cases"][0]["when"]["left"] == "ready"
    assert len(gate_record["resolved_config"]["cases"]) == 1
    assert gate_record["outputs"] == {
        "route": "ready",
        "state": "ready",
        "ticket_id": 7,
    }
    assert gate_record["completion_event_branch_port"] == "ready"
    binding_paths = {entry["config_path"] for entry in gate_record["binding_trace"]["entries"]}
    assert "$.cases[0].when.left" in binding_paths
    assert "$.cases[1].match.route" not in binding_paths
    assert executed_effect_ids == ["effect-ready"]

    effect_record = _effect_records(worker)[0]
    assert effect_record["effect_id"] == "effect-ready"
    assert effect_record["resolved_config"]["prompt"] == "branch ready"


def test_switch_gate_bound_business_port_cannot_resolve_to_error(tmp_path: Path) -> None:
    worker, executed_effect_ids = _worker(
        tmp_path,
        switch_gate={
            "id": "gate-switch",
            "display_name": "Route by state",
            "type": "gate:switch",
            "cases": [
                {
                    "port": "${{trigger-1.route}}",
                    "when": {
                        "left": "${{trigger-1.state}}",
                        "equals": "ready",
                    },
                }
            ],
        },
        effects=[
            {
                "id": "effect-error",
                "display_name": "Lifecycle error handler",
                "type": "effect:agent:prompt",
                "prompt": "repair ${{gate-switch.error_message}}",
            }
        ],
        edges=[_edge("gate-switch", "effect-error", source_port="error")],
    )
    _publish(
        worker,
        event_id="event-reserved-port",
        payload={"route": "error", "state": "ready", "ticket_id": 7},
    )

    _run_cycles(worker)

    gate_record = _gate_records(worker)[0]
    assert gate_record["status"] == "error_routed"
    assert gate_record["completion_event_branch_port"] == "error"
    assert "reserved for lifecycle failures" in str(gate_record["error"])
    assert executed_effect_ids == ["effect-error"]


def test_switch_gate_does_not_resolve_later_cases_or_default_after_match(
    tmp_path: Path,
) -> None:
    worker, executed_effect_ids = _worker(
        tmp_path,
        switch_gate={
            "id": "gate-switch",
            "display_name": "Route by state",
            "type": "gate:switch",
            "default_port": "${{trigger-1.pr.id}}",
            "cases": [
                {
                    "port": "ready",
                    "label": "Ready path",
                    "when": {
                        "left": "${{trigger-1.state}}",
                        "equals": "ready",
                    },
                },
                {
                    "port": "blocked",
                    "label": "PR path",
                    "when": {
                        "left": "${{trigger-1.pr.id}}",
                        "equals": 42,
                    },
                },
            ],
        },
        effects=[
            {
                "id": "effect-ready",
                "display_name": "Ready effect",
                "type": "effect:agent:prompt",
                "prompt": "branch ${{gate-switch.state}}",
            }
        ],
        edges=[_edge("gate-switch", "effect-ready", source_port="ready")],
    )
    _publish(
        worker,
        event_id="event-ready",
        payload={"route": "ready", "state": "ready", "ticket_id": 7},
    )

    _run_cycles(worker)

    gate_record = _gate_records(worker)[0]
    assert gate_record["status"] == "succeeded"
    assert gate_record["resolved_config"]["selected_port"] == "ready"
    assert gate_record["resolved_config"]["matched_case_index"] == 0
    assert gate_record["resolved_config"]["matched_case_label"] == "Ready path"
    assert gate_record["resolved_config"]["matched_by"] == "when"
    assert gate_record["resolved_config"]["cases"] == [
        {
            "port": "ready",
            "label": "Ready path",
            "when": {"left": "ready", "equals": "ready"},
            "match": None,
        }
    ]
    assert "default_port" not in gate_record["resolved_config"]
    binding_paths = {entry["config_path"] for entry in gate_record["binding_trace"]["entries"]}
    assert binding_paths == {"$.cases[0].when.left"}
    assert executed_effect_ids == ["effect-ready"]


def test_switch_gate_match_case_can_select_branch(tmp_path: Path) -> None:
    worker, executed_effect_ids = _worker(
        tmp_path,
        switch_gate={
            "id": "gate-switch",
            "display_name": "Route by state",
            "type": "gate:switch",
            "cases": [
                {
                    "port": "blocked",
                    "label": "Blocked path",
                    "match": {"route": "${{trigger-1.route}}"},
                }
            ],
        },
        effects=[
            {
                "id": "effect-blocked",
                "display_name": "Blocked effect",
                "type": "effect:agent:prompt",
                "prompt": "branch ${{gate-switch.state}}",
            }
        ],
        edges=[_edge("gate-switch", "effect-blocked", source_port="blocked")],
    )
    _publish(
        worker,
        event_id="event-blocked",
        payload={"route": "blocked", "state": "blocked", "ticket_id": 8},
    )

    _run_cycles(worker)

    gate_record = _gate_records(worker)[0]
    assert gate_record["status"] == "succeeded"
    assert gate_record["resolved_config"]["selected_port"] == "blocked"
    assert gate_record["resolved_config"]["matched_case_index"] == 0
    assert gate_record["resolved_config"]["matched_case_label"] == "Blocked path"
    assert gate_record["resolved_config"]["matched_by"] == "match"
    assert gate_record["resolved_config"]["cases"][0]["match"]["route"] == "blocked"
    assert gate_record["completion_event_branch_port"] == "blocked"
    assert executed_effect_ids == ["effect-blocked"]
    assert _effect_records(worker)[0]["resolved_config"]["prompt"] == "branch blocked"


def test_switch_gate_default_port_routes_when_no_case_matches(tmp_path: Path) -> None:
    worker, executed_effect_ids = _worker(
        tmp_path,
        switch_gate={
            "id": "gate-switch",
            "display_name": "Route by state",
            "type": "gate:switch",
            "default_port": "fallback",
            "cases": [
                {
                    "port": "ready",
                    "when": {"left": "${{trigger-1.state}}", "equals": "ready"},
                }
            ],
        },
        effects=[
            {
                "id": "effect-fallback",
                "display_name": "Fallback effect",
                "type": "effect:agent:prompt",
                "prompt": "branch ${{gate-switch.state}}",
            }
        ],
        edges=[_edge("gate-switch", "effect-fallback", source_port="fallback")],
    )
    _publish(
        worker,
        event_id="event-default",
        payload={"route": "other", "state": "other", "ticket_id": 9},
    )

    _run_cycles(worker)

    gate_record = _gate_records(worker)[0]
    assert gate_record["status"] == "succeeded"
    assert gate_record["resolved_config"]["selected_port"] == "fallback"
    assert gate_record["resolved_config"]["matched_case_index"] is None
    assert gate_record["resolved_config"]["matched_case_label"] is None
    assert gate_record["resolved_config"]["matched_by"] == "default"
    assert gate_record["completion_event_branch_port"] == "fallback"
    assert executed_effect_ids == ["effect-fallback"]
    assert _effect_records(worker)[0]["resolved_config"]["prompt"] == "branch other"


def test_switch_gate_drops_when_no_case_matches_and_no_default_port(tmp_path: Path) -> None:
    worker, executed_effect_ids = _worker(
        tmp_path,
        switch_gate={
            "id": "gate-switch",
            "display_name": "Route by state",
            "type": "gate:switch",
            "cases": [
                {
                    "port": "ready",
                    "when": {"left": "${{trigger-1.state}}", "equals": "ready"},
                }
            ],
        },
        effects=[
            {
                "id": "effect-ready",
                "display_name": "Ready effect",
                "type": "effect:agent:prompt",
                "prompt": "branch ${{gate-switch.state}}",
            }
        ],
        edges=[_edge("gate-switch", "effect-ready", source_port="ready")],
    )
    _publish(
        worker,
        event_id="event-drop",
        payload={"route": "other", "state": "other", "ticket_id": 10},
    )

    _run_cycles(worker)

    gate_record = _gate_records(worker)[0]
    assert gate_record["status"] == "dropped"
    assert gate_record["outputs"] == {}
    assert gate_record["resolved_config"]["selected_port"] is None
    assert gate_record["resolved_config"]["matched_case_index"] is None
    assert gate_record["resolved_config"]["matched_case_label"] is None
    assert gate_record["resolved_config"]["matched_by"] == "none"
    assert gate_record["completion_event_branch_port"] is None
    assert executed_effect_ids == []
    assert _effect_records(worker) == []
    assert all(record["trigger_id"] != "gate-switch" for record in _queue_records(worker))
