from __future__ import annotations

from typing import Any

from agent_sandbox.gates.providers.base import GateProviderBase
from agent_sandbox.workflow_validation import NodeCapability
from agent_sandbox.workflow_binding import resolve_gate_map_bindings


class MapGateProvider(GateProviderBase):
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

        resolved = resolve_gate_map_bindings(
            gate.template,
            upstream_payload,
            upstream_output_names,
            system_value_resolver=worker._system_value_resolver_for(
                started_at=started_at,
                queue_record=queue_record,
                node_id=gate.id,
            ),
            strict_validation=True,
        )
        outputs = worker._require_object_payload(resolved.resolved_config, gate_type="gate:map")
        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config={"template": outputs},
            outputs=outputs,
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
            publish_completion_event=True,
        )
        return True
