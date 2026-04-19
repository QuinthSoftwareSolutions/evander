from __future__ import annotations

from typing import Any

from agent_sandbox.events.models import NODE_OUTPUTS_CONTEXT_KEY
from agent_sandbox.gate_runtime import filter_payload_matches, filter_when_matches
from agent_sandbox.gates.providers.base import GateProviderBase
from agent_sandbox.workflow_binding import resolve_workflow_bindings

_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"


class FilterGateProvider(GateProviderBase):
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

        raw_filter_config = gate.when if gate.when is not None else gate.match
        resolved = resolve_workflow_bindings(
            raw_filter_config,
            upstream_payload,
            upstream_output_names,
            system_value_resolver=worker._system_value_resolver_for(
                started_at=started_at,
                queue_record=queue_record,
                node_id=gate.id,
            ),
            strict_validation=True,
        )
        resolved_filter = resolved.resolved_config
        resolved_config = {"when": resolved_filter} if gate.when is not None else {"match": resolved_filter}
        matches = (
            filter_when_matches(resolved_filter)
            if gate.when is not None
            else filter_payload_matches(resolved_filter, queue_record.payload)
        )
        if matches:
            outputs = {
                key: value
                for key, value in queue_record.payload.items()
                if key not in {NODE_OUTPUTS_CONTEXT_KEY, _BRANCH_PORT_PAYLOAD_KEY, _FOR_EXECUTION_IDS_PAYLOAD_KEY}
            }
            worker._write_gate_record(
                queue_record=queue_record,
                gate=gate,
                status="succeeded",
                started_at=started_at,
                resolved_config=resolved_config,
                outputs=outputs,
                inputs_by_source=inputs_by_source,
                input_event_ids_by_source=input_event_ids_by_source,
                input_occurred_at_by_source=input_occurred_at_by_source,
                config_revision=config_revision,
                publish_completion_event=True,
            )
            return True

        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="dropped",
            started_at=started_at,
            resolved_config=resolved_config,
            outputs={},
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
        )
        return True
