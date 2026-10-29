from __future__ import annotations

import logging
from typing import Any

from evander.events.models import NODE_OUTPUTS_CONTEXT_KEY
from evander.gate_runtime import GateRuntimeError, parse_iso8601_duration
from evander.gates.providers.base import GateProviderBase
from evander.workflow.binding import binding_trace_to_payload, resolve_workflow_bindings
from evander.workflow.validation import NodeCapability

logger = logging.getLogger(__name__)

_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"


class CounterGateProvider(GateProviderBase):
    """Forward each activation with its keyed fixed-window count."""

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

        raw_key = getattr(gate, "key", None)
        raw_window = getattr(gate, "window", None)
        if raw_key is None:
            raise GateRuntimeError("gate:counter requires key")
        if raw_window is None:
            raise GateRuntimeError("gate:counter requires window")

        resolved = resolve_workflow_bindings(
            {"key": raw_key},
            upstream_payload,
            upstream_output_names,
            system_value_resolver=worker._system_value_resolver_for(
                started_at=started_at,
                queue_record=queue_record,
                node_id=gate.id,
            ),
            strict_validation=True,
        )
        resolved_payload = worker._require_object_payload(
            resolved.resolved_config, gate_type="gate:counter"
        )
        key = _coerce_non_empty_string(resolved_payload.get("key"), field_name="key")
        window_text = _coerce_non_empty_string(raw_window, field_name="window")
        window = parse_iso8601_duration(
            window_text, field_name="window", gate_type="gate:counter"
        )
        if window.total_seconds() <= 0:
            raise GateRuntimeError("gate:counter window must be greater than zero")

        claim = worker.claim_counter_window(
            gate_id=gate.id,
            key=key,
            window=window,
            window_text=window_text,
            now=started_at,
            event_id=queue_record.event_id,
        )
        count = int(claim["count"])
        resolved_config = {
            "key": key,
            "window": claim["window"],
            "window_opened_at": claim["window_opened_at"],
            "window_expires_at": claim["window_expires_at"],
            "count": count,
            "decision_replayed": bool(claim["decision_replayed"]),
            "diagnostic": "counted:within_window",
        }
        logger.info(
            "gate:counter counted activation gate_id=%s key=%s event_id=%s count=%s "
            "window_expires_at=%s",
            gate.id,
            key,
            queue_record.event_id,
            count,
            claim["window_expires_at"],
        )
        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config=resolved_config,
            outputs=_counter_outputs(queue_record.payload, count=count),
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
            binding_trace=binding_trace_to_payload(resolved.trace),
            publish_completion_event=True,
        )
        return True


def _counter_outputs(payload: dict[str, Any], *, count: int) -> dict[str, Any]:
    outputs = {
        item_key: value
        for item_key, value in payload.items()
        if item_key
        not in {
            NODE_OUTPUTS_CONTEXT_KEY,
            _BRANCH_PORT_PAYLOAD_KEY,
            _FOR_EXECUTION_IDS_PAYLOAD_KEY,
        }
    }
    outputs["count"] = count
    return outputs


def _coerce_non_empty_string(value: Any, *, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise GateRuntimeError(f"gate:counter {field_name} must resolve to a non-empty string")
    return text
