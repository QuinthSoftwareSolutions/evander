from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from evander.events.models import NODE_OUTPUTS_CONTEXT_KEY
from evander.gate_runtime import GateRuntimeError, filter_when_matches
from evander.gates.providers.base import GateProviderBase
from evander.workflow.binding import (
    BindingResolutionTrace,
    binding_trace_to_payload,
    resolve_workflow_bindings,
)
from evander.workflow.lifecycle_ports import AGENT_PROMPT_ERROR_PORT
from evander.workflow.validation import NodeCapability

_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"
_VALID_BRANCH_PORT = "valid"
_INVALID_BRANCH_PORT = "invalid"


class ValidateGateProvider(GateProviderBase):
    """Evaluate every named predicate and emit ordered validation failures."""

    capability = NodeCapability(
        min_inbound=1,
        max_inbound=1,
        allowed_source_ports=frozenset(
            {_VALID_BRANCH_PORT, _INVALID_BRANCH_PORT, AGENT_PROMPT_ERROR_PORT}
        ),
    )

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

        system_value_resolver = worker._system_value_resolver_for(
            started_at=started_at,
            queue_record=queue_record,
            node_id=gate.id,
        )
        resolved_checks: list[dict[str, Any]] = []
        failures: list[dict[str, str]] = []
        trace_parts: list[BindingResolutionTrace] = []

        for index, check in enumerate(gate.checks):
            code = _check_text(check, "code")
            message = _check_text(check, "message")
            raw_when = _check_value(check, "when")
            resolved = resolve_workflow_bindings(
                raw_when,
                upstream_payload,
                upstream_output_names,
                system_value_resolver=system_value_resolver,
                strict_validation=True,
            )
            trace_parts.append(_prefix_binding_trace(resolved.trace, f"$.checks[{index}].when"))
            resolved_when = resolved.resolved_config
            if not isinstance(resolved_when, Mapping):
                raise GateRuntimeError(
                    f"gate:validate check {code!r} when must resolve to a JSON object"
                )
            try:
                passed = filter_when_matches(resolved_when)
            except GateRuntimeError as exc:
                raise GateRuntimeError(
                    f"gate:validate check {code!r} could not be evaluated: {exc}"
                ) from exc
            resolved_checks.append(
                {
                    "code": code,
                    "message": message,
                    "when": dict(resolved_when),
                    "passed": passed,
                }
            )
            if not passed:
                failures.append({"code": code, "message": message})

        branch_port = _VALID_BRANCH_PORT if not failures else _INVALID_BRANCH_PORT
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
        outputs.update(
            {
                "valid": not failures,
                "failure_count": len(failures),
                "failed_codes": [failure["code"] for failure in failures],
                "failures": failures,
            }
        )
        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config={
                "checks": resolved_checks,
                "selected_port": branch_port,
            },
            outputs=outputs,
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
            binding_trace=binding_trace_to_payload(_merge_binding_traces(trace_parts)),
            publish_completion_event=True,
            branch_port=branch_port,
        )
        return True


def _check_value(check: Any, field_name: str) -> Any:
    if isinstance(check, Mapping):
        return check.get(field_name)
    return getattr(check, field_name, None)


def _check_text(check: Any, field_name: str) -> str:
    value = _check_value(check, field_name)
    text = str(value or "").strip()
    if not text:
        raise GateRuntimeError(f"gate:validate check {field_name} must be a non-empty string")
    return text


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
    return BindingResolutionTrace(
        consumed_output_keys=tuple(
            sorted(
                {output_key for trace in trace_parts for output_key in trace.consumed_output_keys}
            )
        ),
        entries=tuple(entry for trace in trace_parts for entry in trace.entries),
    )


def _join_config_path(prefix: str, path: str) -> str:
    if path == "$":
        return prefix
    if path.startswith("$."):
        return f"{prefix}.{path[2:]}"
    return f"{prefix}.{path}"
