from __future__ import annotations

from copy import deepcopy
from typing import Any

from evander.gates.providers.base import GateProviderBase
from evander.workflow.validation import NodeCapability


class MergeGateProvider(GateProviderBase):
    capability = NodeCapability(min_inbound=1)

    def execute(self, **kwargs: Any) -> bool:
        worker = kwargs["worker"]
        queue_record = kwargs["queue_record"]
        gate = kwargs["gate"]
        started_at = kwargs["started_at"]
        config_revision = kwargs["config_revision"]
        graph = kwargs["graph"]

        previous = worker.gate_execution_store.find_latest_execution_for_gate(gate.id)
        inputs_by_source = deepcopy(previous.inputs_by_source) if previous is not None else {}
        input_event_ids_by_source = (
            dict(previous.input_event_ids_by_source) if previous is not None else {}
        )
        input_occurred_at_by_source = (
            dict(previous.input_occurred_at_by_source) if previous is not None else {}
        )

        inputs_by_source[queue_record.trigger_id] = dict(queue_record.payload)
        input_event_ids_by_source[queue_record.trigger_id] = queue_record.event_id
        input_occurred_at_by_source[queue_record.trigger_id] = queue_record.occurred_at

        shared_output_keys = tuple(sorted((gate.outputs or {}).keys()))
        if not shared_output_keys and inputs_by_source:
            runtime_keys = {"__node_outputs", "__branch_port", "__for_execution_ids"}
            source_key_sets = [
                {key for key in payload if key not in runtime_keys}
                for payload in inputs_by_source.values()
            ]
            if source_key_sets:
                shared_output_keys = tuple(sorted(set.intersection(*source_key_sets)))
        resolved_config = {"shared_output_keys": list(shared_output_keys)}
        outputs = {
            key: queue_record.payload[key]
            for key in shared_output_keys
            if key in queue_record.payload
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
            preserved_context_node_ids=worker._guaranteed_context_node_ids_for(
                graph=graph,
                node_id=gate.id,
            ),
        )
        return True
