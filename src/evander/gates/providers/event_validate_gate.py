from __future__ import annotations

from typing import Any

from evander.events.internal import validate_internal_event_type
from evander.events.models import NODE_OUTPUTS_CONTEXT_KEY
from evander.gate_runtime import GateRuntimeError
from evander.gates.providers.base import GateProviderBase
from evander.workflow.binding import binding_trace_to_payload, resolve_workflow_bindings
from evander.workflow.lifecycle_ports import AGENT_PROMPT_ERROR_PORT
from evander.workflow.validation import NodeCapability


class EventValidateGateProvider(GateProviderBase):
    capability = NodeCapability(
        min_inbound=1,
        max_inbound=1,
        allowed_source_ports=frozenset({"valid", "invalid", AGENT_PROMPT_ERROR_PORT}),
    )

    def execute(self, **kwargs: Any) -> bool:
        worker, record, gate = kwargs["worker"], kwargs["queue_record"], kwargs["gate"]
        resolved = resolve_workflow_bindings(
            {"event_type": gate.event_type, "payload": gate.payload},
            kwargs["upstream_payload"],
            kwargs["upstream_output_names"],
            system_value_resolver=worker._system_value_resolver_for(
                started_at=kwargs["started_at"], queue_record=record, node_id=gate.id
            ),
            strict_validation=True,
        )
        config = resolved.resolved_config
        name = config["event_type"]
        if not isinstance(name, str):
            raise GateRuntimeError("Event type must resolve to a string")
        validate_internal_event_type(name)
        schemas = self.runtime_context.event_schemas if self.runtime_context else None
        if schemas is None:
            raise GateRuntimeError("Event schema service is unavailable")
        metadata = kwargs["graph"].node_graph_metadata_by_id.get(gate.id)
        if metadata is None:
            raise GateRuntimeError(
                "Event validation requires the workflow's persisted directory metadata"
            )
        directory = metadata.directory_path
        result = schemas.validate(directory, name, config["payload"])
        port = "valid" if result["valid"] else "invalid"
        outputs = {
            key: value
            for key, value in record.payload.items()
            if key not in {NODE_OUTPUTS_CONTEXT_KEY, "__branch_port", "__for_execution_ids"}
        }
        outputs.update(result)
        worker._write_gate_record(
            queue_record=record,
            gate=gate,
            status="succeeded",
            started_at=kwargs["started_at"],
            resolved_config={
                **config,
                "directory_path": directory,
                "schema_revision": result["schema_revision"],
                "schema_source": result["schema_source"],
                "selected_port": port,
            },
            outputs=outputs,
            inputs_by_source=kwargs["inputs_by_source"],
            input_event_ids_by_source=kwargs["input_event_ids_by_source"],
            input_occurred_at_by_source=kwargs["input_occurred_at_by_source"],
            config_revision=kwargs["config_revision"],
            binding_trace=binding_trace_to_payload(resolved.trace),
            publish_completion_event=True,
            branch_port=port,
        )
        return True
