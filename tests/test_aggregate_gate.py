from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from evander.gate_runtime import GateRuntimeError
from evander.gates.providers.aggregate_gate import AggregateGateProvider


class _Worker:
    def __init__(self) -> None:
        self.record: dict[str, object] | None = None

    def _system_value_resolver_for(self, **_):
        def resolve(path: str) -> str:
            return f"system:{path}"

        return resolve

    def _require_object_payload(self, payload, *, gate_type: str):
        if not isinstance(payload, dict):
            raise GateRuntimeError(f"{gate_type} payload must resolve to an object")
        return dict(payload)

    def _write_gate_record(self, **kwargs) -> None:
        self.record = dict(kwargs)


def _execute_gate(*, items, metrics, upstream_payload=None, upstream_output_names=None):
    worker = _Worker()
    provider = AggregateGateProvider()
    provider.execute(
        worker=worker,
        queue_record=SimpleNamespace(trace_id="trace-1"),
        gate=SimpleNamespace(id="gate-aggregate", items=items, metrics=metrics),
        started_at=datetime(2026, 5, 31, 12, 0, tzinfo=timezone.utc),
        config_revision="revision-1",
        upstream_payload=upstream_payload or {},
        upstream_output_names=upstream_output_names or {},
        inputs_by_source={},
        input_event_ids_by_source={},
        input_occurred_at_by_source={},
    )
    assert worker.record is not None
    return worker.record["outputs"]


def test_aggregate_gate_computes_numeric_metrics_from_bound_items() -> None:
    outputs = _execute_gate(
        items="${{query-model-usage.items}}",
        metrics={
            "input_tokens": {"operation": "sum", "path": "input_tokens"},
            "total_tokens_min": {"operation": "min", "path": "total_tokens"},
            "total_tokens_max": {"operation": "max", "path": "total_tokens"},
            "total_tokens_avg": {"operation": "avg", "path": "total_tokens"},
        },
        upstream_payload={
            "query-model-usage": {
                "items": [
                    {"input_tokens": 10, "total_tokens": 12},
                    {"input_tokens": 20, "total_tokens": 30},
                    {"input_tokens": None, "total_tokens": None},
                    {},
                ]
            }
        },
        upstream_output_names={"query-model-usage": {"items"}},
    )

    assert outputs == {
        "count": 4,
        "input_tokens": 30,
        "total_tokens_min": 12,
        "total_tokens_max": 30,
        "total_tokens_avg": 21,
    }


def test_aggregate_gate_empty_numeric_values_return_sum_zero_and_null_statistics() -> None:
    outputs = _execute_gate(
        items=[{"other": 1}, {"value": None}],
        metrics={
            "total": {"operation": "sum", "path": "value"},
            "minimum": {"operation": "min", "path": "value"},
            "maximum": {"operation": "max", "path": "value"},
            "average": {"operation": "avg", "path": "value"},
        },
    )

    assert outputs == {
        "count": 2,
        "total": 0,
        "minimum": None,
        "maximum": None,
        "average": None,
    }


def test_aggregate_gate_rejects_non_array_items() -> None:
    with pytest.raises(GateRuntimeError, match="items must resolve to an array"):
        _execute_gate(
            items={"not": "an array"},
            metrics={"total": {"operation": "sum", "path": "value"}},
        )


def test_aggregate_gate_rejects_non_numeric_metric_values() -> None:
    with pytest.raises(GateRuntimeError, match="non-numeric value at item 1"):
        _execute_gate(
            items=[{"value": 1}, {"value": "2"}],
            metrics={"total": {"operation": "sum", "path": "value"}},
        )
