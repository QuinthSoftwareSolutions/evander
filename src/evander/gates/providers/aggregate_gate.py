from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from evander.gate_runtime import GateRuntimeError
from evander.gates.providers.base import GateProviderBase
from evander.workflow.binding import (
    binding_trace_to_payload,
    resolve_workflow_bindings,
)
from evander.workflow.validation import NodeCapability


_MISSING = object()
_SUPPORTED_OPERATIONS = frozenset({"sum", "min", "max", "avg"})


class AggregateGateProvider(GateProviderBase):
    capability = NodeCapability(min_inbound=1, max_inbound=1)

    def execute(self, **kwargs: Any) -> bool:
        worker = kwargs["worker"]
        queue_record = kwargs["queue_record"]
        gate = kwargs["gate"]
        started_at = kwargs["started_at"]
        config_revision = kwargs["config_revision"]
        upstream_payload = kwargs["upstream_payload"]
        upstream_output_names = kwargs["upstream_output_names"]
        inputs_by_source = kwargs["inputs_by_source"]
        input_event_ids_by_source = kwargs["input_event_ids_by_source"]
        input_occurred_at_by_source = kwargs["input_occurred_at_by_source"]

        resolved = resolve_workflow_bindings(
            {"items": gate.items, "metrics": _metrics_to_mapping(gate.metrics)},
            upstream_payload,
            upstream_output_names,
            system_value_resolver=worker._system_value_resolver_for(
                started_at=started_at,
                queue_record=queue_record,
                node_id=gate.id,
            ),
            strict_validation=True,
        )
        resolved_config = worker._require_object_payload(
            resolved.resolved_config, gate_type="gate:aggregate"
        )
        items = resolved_config.get("items")
        if not isinstance(items, list):
            raise GateRuntimeError("gate:aggregate items must resolve to an array")
        metrics = _normalize_metrics(resolved_config.get("metrics"))

        outputs: dict[str, Any] = {"count": len(items)}
        for output_name, metric in metrics.items():
            outputs[output_name] = _aggregate_metric(
                items,
                output_name=output_name,
                operation=metric["operation"],
                path=metric["path"],
            )

        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config={"items": items, "metrics": metrics},
            outputs=outputs,
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
            binding_trace=binding_trace_to_payload(resolved.trace),
            publish_completion_event=True,
        )
        return True


def _metrics_to_mapping(raw_metrics: Any) -> dict[str, Any]:
    if not isinstance(raw_metrics, Mapping):
        raise GateRuntimeError("gate:aggregate metrics must be a JSON object")
    metrics: dict[str, Any] = {}
    for raw_name, raw_metric in raw_metrics.items():
        name = str(raw_name)
        model_dump = getattr(raw_metric, "model_dump", None)
        if callable(model_dump):
            metrics[name] = model_dump(mode="python")
        else:
            metrics[name] = raw_metric
    return metrics


def _normalize_metrics(raw_metrics: Any) -> dict[str, dict[str, str]]:
    if not isinstance(raw_metrics, Mapping) or not raw_metrics:
        raise GateRuntimeError("gate:aggregate metrics must resolve to a non-empty object")
    metrics: dict[str, dict[str, str]] = {}
    for raw_name, raw_metric in raw_metrics.items():
        name = str(raw_name).strip()
        if not name:
            raise GateRuntimeError("gate:aggregate metric names must be non-empty strings")
        if name == "count":
            raise GateRuntimeError("gate:aggregate metric name 'count' is reserved")
        if not isinstance(raw_metric, Mapping):
            raise GateRuntimeError(f"gate:aggregate metric {name} must be an object")
        operation = _normalize_operation(raw_metric.get("operation"), metric_name=name)
        path = _normalize_path(raw_metric.get("path"), metric_name=name)
        metrics[name] = {"operation": operation, "path": path}
    return metrics


def _normalize_operation(value: Any, *, metric_name: str) -> str:
    operation = "" if value is None else str(value).strip().lower()
    if operation not in _SUPPORTED_OPERATIONS:
        allowed = ", ".join(sorted(_SUPPORTED_OPERATIONS))
        raise GateRuntimeError(
            f"gate:aggregate metric {metric_name} operation must be one of {allowed}"
        )
    return operation


def _normalize_path(value: Any, *, metric_name: str) -> str:
    path = "" if value is None else str(value).strip()
    if not path or any(not segment for segment in path.split(".")):
        raise GateRuntimeError(
            f"gate:aggregate metric {metric_name} path must be a non-empty dotted object path"
        )
    return path


def _aggregate_metric(
    items: list[Any],
    *,
    output_name: str,
    operation: str,
    path: str,
) -> int | float | None:
    values: list[int | float] = []
    for index, item in enumerate(items):
        value = _resolve_item_path(item, path)
        if value is _MISSING or value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GateRuntimeError(
                f"gate:aggregate metric {output_name} path {path} resolved to "
                f"non-numeric value at item {index}"
            )
        values.append(value)

    if operation == "sum":
        return sum(values)
    if not values:
        return None
    if operation == "min":
        return min(values)
    if operation == "max":
        return max(values)
    if operation == "avg":
        return sum(values) / len(values)
    raise GateRuntimeError(f"gate:aggregate operation {operation} is not supported")


def _resolve_item_path(item: Any, path: str) -> Any:
    current = item
    for segment in path.split("."):
        if not isinstance(current, Mapping):
            return _MISSING
        if segment not in current:
            return _MISSING
        current = current[segment]
    return current
