from __future__ import annotations

import json
from typing import Any, Literal

import yaml

from evander.gate_runtime import GateRuntimeError
from evander.gates.providers.base import GateProviderBase
from evander.workflow.binding import (
    binding_trace_to_payload,
    resolve_workflow_bindings,
)
from evander.workflow.validation import NodeCapability

SerializeFormat = Literal["json", "yaml"]


class SerializeGateProvider(GateProviderBase):
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
            {
                "value": gate.value,
                "format": getattr(gate, "format", "json"),
            },
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
            resolved.resolved_config, gate_type="gate:serialize"
        )
        format_name = _normalize_format(resolved_config.get("format"))
        content = _serialize_value(resolved_config.get("value"), format_name=format_name)
        outputs = {"content": content, "format": format_name}

        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config={"value": resolved_config.get("value"), "format": format_name},
            outputs=outputs,
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
            binding_trace=binding_trace_to_payload(resolved.trace),
            publish_completion_event=True,
        )
        return True


def _normalize_format(value: Any) -> SerializeFormat:
    text = str(value or "json").strip().lower()
    if text == "json":
        return "json"
    if text == "yaml":
        return "yaml"
    raise GateRuntimeError("gate:serialize format must be one of json or yaml")


def _serialize_value(value: Any, *, format_name: SerializeFormat) -> str:
    if format_name == "json":
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise GateRuntimeError("gate:serialize failed to serialize JSON") from exc
    try:
        return yaml.safe_dump(value, sort_keys=False, allow_unicode=True)
    except yaml.YAMLError as exc:
        raise GateRuntimeError("gate:serialize failed to serialize YAML") from exc
