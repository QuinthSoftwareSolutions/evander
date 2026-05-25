from __future__ import annotations

from typing import Any

from agent_sandbox.events.models import NODE_OUTPUTS_CONTEXT_KEY
from agent_sandbox.gate_runtime import GateRuntimeError, filter_payload_matches, filter_when_matches
from agent_sandbox.gates.providers.base import GateProviderBase
from agent_sandbox.workflow.binding import binding_trace_to_payload, resolve_workflow_bindings
from agent_sandbox.workflow.validation import NodeCapability

_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"


class SwitchGateProvider(GateProviderBase):
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

        raw_config = {
            "cases": [_case_to_mapping(case) for case in gate.cases],
            "default_port": getattr(gate, "default_port", None),
        }
        resolved = resolve_workflow_bindings(
            raw_config,
            upstream_payload,
            upstream_output_names,
            system_value_resolver=worker._system_value_resolver_for(
                started_at=started_at,
                queue_record=queue_record,
                node_id=gate.id,
            ),
            strict_validation=True,
        )
        resolved_config = resolved.resolved_config if isinstance(resolved.resolved_config, dict) else {}
        resolved_cases = [
            case for case in resolved_config.get("cases", []) if isinstance(case, dict)
        ]
        resolved_default_port = resolved_config.get("default_port")

        selected_port: str | None = None
        matched_case_index: int | None = None
        matched_case_label: Any | None = None
        matched_by = "none"

        for index, case in enumerate(resolved_cases):
            case_port = _coerce_non_empty_string(case.get("port"), field_name="case port")
            case_label = case.get("label")
            case_when = case.get("when")
            case_match = case.get("match")
            if case_when is not None and filter_when_matches(case_when):
                selected_port = case_port
                matched_case_index = index
                matched_case_label = case_label
                matched_by = "when"
                break
            if case_match is not None and filter_payload_matches(case_match, queue_record.payload):
                selected_port = case_port
                matched_case_index = index
                matched_case_label = case_label
                matched_by = "match"
                break

        if selected_port is None and resolved_default_port is not None:
            selected_port = _coerce_non_empty_string(
                resolved_default_port, field_name="default_port"
            )
            matched_by = "default"

        final_resolved_config: dict[str, Any] = {
            "cases": resolved_cases,
            "selected_port": selected_port,
            "matched_case_index": matched_case_index,
            "matched_case_label": matched_case_label,
            "matched_by": matched_by,
        }
        if resolved_default_port is not None:
            final_resolved_config["default_port"] = resolved_default_port

        binding_trace = binding_trace_to_payload(resolved.trace)
        if selected_port is None:
            worker._write_gate_record(
                queue_record=queue_record,
                gate=gate,
                status="dropped",
                started_at=started_at,
                resolved_config=final_resolved_config,
                outputs={},
                inputs_by_source=inputs_by_source,
                input_event_ids_by_source=input_event_ids_by_source,
                input_occurred_at_by_source=input_occurred_at_by_source,
                config_revision=config_revision,
                binding_trace=binding_trace,
            )
            return True

        outputs = {
            key: value
            for key, value in queue_record.payload.items()
            if key
            not in {
                NODE_OUTPUTS_CONTEXT_KEY,
                _BRANCH_PORT_PAYLOAD_KEY,
                _FOR_EXECUTION_IDS_PAYLOAD_KEY,
            }
        }
        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config=final_resolved_config,
            outputs=outputs,
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
            binding_trace=binding_trace,
            publish_completion_event=True,
            branch_port=selected_port,
        )
        return True


def _case_to_mapping(case: Any) -> dict[str, Any]:
    model_dump = getattr(case, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="python"))
    if isinstance(case, dict):
        return dict(case)
    raise GateRuntimeError("gate:switch cases must be mappings")


def _coerce_non_empty_string(value: Any, *, field_name: str) -> str:
    if value is None:
        raise GateRuntimeError(f"gate:switch {field_name} must be a non-empty string")
    text = str(value).strip()
    if not text:
        raise GateRuntimeError(f"gate:switch {field_name} must be a non-empty string")
    return text
