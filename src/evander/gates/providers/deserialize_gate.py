from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Literal

import yaml

from evander.gate_runtime import GateRuntimeError
from evander.gates.providers.base import GateProviderBase
from evander.workflow.binding import (
    binding_trace_to_payload,
    resolve_workflow_bindings,
)
from evander.workflow.contracts import validate_output_mapping_keys
from evander.workflow.validation import NodeCapability

DeserializeFormat = Literal["auto", "json", "yaml"]


class DeserializeGateProvider(GateProviderBase):
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
                "content": gate.content,
                "format": getattr(gate, "format", "auto"),
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
            resolved.resolved_config, gate_type="gate:deserialize"
        )
        content = resolved_config.get("content")
        if not isinstance(content, str):
            raise GateRuntimeError("gate:deserialize content must resolve to a string")
        format_name = _normalize_format(resolved_config.get("format"))
        outputs = _deserialize_object(content, format_name=format_name)
        try:
            validate_output_mapping_keys(
                outputs,
                context=f"gate:deserialize {gate.id} runtime outputs",
            )
        except ValueError as exc:
            raise GateRuntimeError(str(exc)) from exc

        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config={"content": content, "format": format_name},
            outputs=outputs,
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
            binding_trace=binding_trace_to_payload(resolved.trace),
            publish_completion_event=True,
        )
        return True


def _normalize_format(value: Any) -> DeserializeFormat:
    text = str(value or "auto").strip().lower()
    if text == "auto":
        return "auto"
    if text == "json":
        return "json"
    if text == "yaml":
        return "yaml"
    raise GateRuntimeError("gate:deserialize format must be one of auto, json, or yaml")


def _deserialize_object(content: str, *, format_name: DeserializeFormat) -> dict[str, Any]:
    text = content.strip()
    if not text:
        raise GateRuntimeError("gate:deserialize content must not be empty")

    if format_name == "json":
        parsed = _parse_json(text)
    elif format_name == "yaml":
        parsed = _parse_yaml(text)
    else:
        try:
            parsed = _parse_json(text)
        except GateRuntimeError:
            parsed = _parse_yaml(text)

    if not isinstance(parsed, Mapping):
        raise GateRuntimeError("gate:deserialize content must decode to an object")

    outputs: dict[str, Any] = {}
    for key, value in parsed.items():
        if not isinstance(key, str) or not key.strip():
            raise GateRuntimeError(
                "gate:deserialize content must decode to an object with non-empty string keys"
            )
        outputs[key.strip()] = value
    return outputs


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise GateRuntimeError(f"gate:deserialize failed to parse JSON: {exc.msg}") from exc


def _parse_yaml(text: str) -> Any:
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise GateRuntimeError("gate:deserialize failed to parse YAML") from exc
