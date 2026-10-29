from __future__ import annotations

from dataclasses import replace
from typing import Any

from evander.events.models import NODE_OUTPUTS_CONTEXT_KEY
from evander.gate_runtime import GateRuntimeError, filter_payload_matches, filter_when_matches
from evander.gates.providers.base import GateProviderBase
from evander.workflow.binding import (
    BindingResolutionTrace,
    binding_trace_to_payload,
    resolve_workflow_bindings,
)
from evander.workflow.lifecycle_ports import AGENT_PROMPT_ERROR_PORT
from evander.workflow.validation import NodeCapability, WorkflowValidationDiagnostic

_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"


class SwitchGateProvider(GateProviderBase):
    capability = NodeCapability(min_inbound=1, max_inbound=1)

    @classmethod
    def validate_graph_node(
        cls,
        *,
        node: Any,
        edges: list[Any] | None = None,
        **_: Any,
    ) -> list[WorkflowValidationDiagnostic]:
        gate_id = _node_id(node)
        if not gate_id or not edges:
            return []

        valid_ports = _switch_valid_source_ports(node)
        missing_source_port = False
        invalid_source_ports: set[str] = set()
        for edge in edges:
            if _edge_source_id(edge) != gate_id:
                continue
            source_port = _edge_source_port(edge)
            if source_port is None:
                missing_source_port = True
                continue
            if source_port not in valid_ports:
                invalid_source_ports.add(source_port)

        diagnostics: list[WorkflowValidationDiagnostic] = []
        if missing_source_port:
            diagnostics.append(
                WorkflowValidationDiagnostic(
                    code="node_capability",
                    message=(f"gate:switch {gate_id} outbound edges require source_port"),
                    node_id=gate_id,
                    node_type="gate:switch",
                )
            )
        if invalid_source_ports:
            diagnostics.append(
                WorkflowValidationDiagnostic(
                    code="node_capability",
                    message=(
                        f"gate:switch {gate_id} uses invalid source_port value(s): "
                        f"{', '.join(sorted(invalid_source_ports))}"
                    ),
                    node_id=gate_id,
                    node_type="gate:switch",
                )
            )
        return diagnostics

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

        selected_port: str | None = None
        matched_case_index: int | None = None
        matched_case_label: Any | None = None
        matched_by = "none"
        resolved_cases: list[dict[str, Any]] = []
        resolved_default_port: Any | None = None
        trace_parts: list[BindingResolutionTrace] = []

        system_value_resolver = worker._system_value_resolver_for(
            started_at=started_at,
            queue_record=queue_record,
            node_id=gate.id,
        )

        for index, raw_case in enumerate(_case_to_mapping(case) for case in gate.cases):
            resolved = resolve_workflow_bindings(
                raw_case,
                upstream_payload,
                upstream_output_names,
                system_value_resolver=system_value_resolver,
                strict_validation=True,
            )
            trace_parts.append(_prefix_binding_trace(resolved.trace, f"$.cases[{index}]"))
            if not isinstance(resolved.resolved_config, dict):
                raise GateRuntimeError("gate:switch cases must resolve to mappings")
            case = resolved.resolved_config
            resolved_cases.append(case)
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

        raw_default_port = getattr(gate, "default_port", None)
        if selected_port is None and raw_default_port is not None:
            resolved_default = resolve_workflow_bindings(
                {"default_port": raw_default_port},
                upstream_payload,
                upstream_output_names,
                system_value_resolver=system_value_resolver,
                strict_validation=True,
            )
            trace_parts.append(resolved_default.trace)
            resolved_default_config = (
                resolved_default.resolved_config
                if isinstance(resolved_default.resolved_config, dict)
                else {}
            )
            resolved_default_port = resolved_default_config.get("default_port")
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

        binding_trace = binding_trace_to_payload(_merge_binding_traces(trace_parts))
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


def _prefix_binding_trace(
    trace: BindingResolutionTrace,
    config_path_prefix: str,
) -> BindingResolutionTrace:
    return BindingResolutionTrace(
        consumed_output_keys=trace.consumed_output_keys,
        entries=tuple(
            replace(
                entry,
                config_path=_join_config_path(config_path_prefix, entry.config_path),
            )
            for entry in trace.entries
        ),
    )


def _merge_binding_traces(
    trace_parts: list[BindingResolutionTrace],
) -> BindingResolutionTrace:
    consumed_output_keys = sorted(
        {output_key for trace in trace_parts for output_key in trace.consumed_output_keys}
    )
    entries = tuple(entry for trace in trace_parts for entry in trace.entries)
    return BindingResolutionTrace(
        consumed_output_keys=tuple(consumed_output_keys),
        entries=entries,
    )


def _join_config_path(prefix: str, path: str) -> str:
    if path == "$":
        return prefix
    if path.startswith("$."):
        return f"{prefix}.{path[2:]}"
    return f"{prefix}.{path}"


def _coerce_non_empty_string(value: Any, *, field_name: str) -> str:
    if value is None:
        raise GateRuntimeError(f"gate:switch {field_name} must be a non-empty string")
    text = str(value).strip()
    if not text:
        raise GateRuntimeError(f"gate:switch {field_name} must be a non-empty string")
    if text == AGENT_PROMPT_ERROR_PORT:
        raise GateRuntimeError(
            f"gate:switch {field_name} 'error' is reserved for lifecycle failures"
        )
    return text


def _switch_valid_source_ports(node: Any) -> set[str]:
    ports: set[str] = {AGENT_PROMPT_ERROR_PORT}
    cases = getattr(node, "cases", None)
    if cases is None and isinstance(node, dict):
        cases = node.get("cases")
    if isinstance(cases, list):
        for case in cases:
            port = _case_port(case)
            if port is not None:
                ports.add(port)
    default_port = getattr(node, "default_port", None)
    if default_port is None and isinstance(node, dict):
        default_port = node.get("default_port")
    if default_port is not None:
        text = str(default_port).strip()
        if text:
            ports.add(text)
    return ports


def _case_port(case: Any) -> str | None:
    if isinstance(case, dict):
        value = case.get("port")
    else:
        value = getattr(case, "port", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _node_id(node: Any) -> str:
    if isinstance(node, dict):
        value = node.get("id")
    else:
        value = getattr(node, "id", None)
    if value is None:
        return ""
    return str(value).strip()


def _edge_source_id(edge: Any) -> str:
    if isinstance(edge, dict):
        value = edge.get("source_id")
    else:
        value = getattr(edge, "source_id", None)
    if value is None:
        return ""
    return str(value).strip()


def _edge_source_port(edge: Any) -> str | None:
    if isinstance(edge, dict):
        value = edge.get("source_port")
    else:
        value = getattr(edge, "source_port", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None
