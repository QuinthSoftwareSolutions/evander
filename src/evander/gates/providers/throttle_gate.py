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
_DEFAULT_BRANCH_PORT = "default"
_DROPPED_BRANCH_PORT = "dropped"


class ThrottleGateProvider(GateProviderBase):
    """Route a bounded number of keyed activations through each fixed time window."""

    capability = NodeCapability(
        min_inbound=1,
        max_inbound=1,
        allowed_source_ports=frozenset(
            {None, _DEFAULT_BRANCH_PORT, _DROPPED_BRANCH_PORT}
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

        raw_key = getattr(gate, "key", None)
        raw_window = getattr(gate, "window", None)
        raw_limit = getattr(gate, "limit", None)
        if raw_key is None:
            raise GateRuntimeError("gate:throttle requires key")
        if raw_window is None:
            raise GateRuntimeError("gate:throttle requires window")
        if raw_limit is None:
            raise GateRuntimeError("gate:throttle requires limit")

        resolved = resolve_workflow_bindings(
            {"key": raw_key, "limit": raw_limit},
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
            resolved.resolved_config, gate_type="gate:throttle"
        )
        key = _coerce_non_empty_string(resolved_payload.get("key"), field_name="key")
        limit = _coerce_positive_int(resolved_payload.get("limit"))
        window_text = _coerce_non_empty_string(raw_window, field_name="window")
        window = parse_iso8601_duration(
            window_text, field_name="window", gate_type="gate:throttle"
        )
        if window.total_seconds() <= 0:
            raise GateRuntimeError("gate:throttle window must be greater than zero")

        claim = worker.claim_throttle_window(
            gate_id=gate.id,
            key=key,
            window=window,
            window_text=window_text,
            limit=limit,
            now=started_at,
            event_id=queue_record.event_id,
        )
        allowed = bool(claim["allowed"])
        resolved_config = {
            "key": key,
            "window": claim["window"],
            "limit": claim["limit"],
            "window_opened_at": claim["window_opened_at"],
            "window_expires_at": claim["window_expires_at"],
            "accepted_count": claim["accepted_count"],
            "allowed": allowed,
            "decision_replayed": bool(claim["decision_replayed"]),
            "diagnostic": (
                "allowed:within_window_limit"
                if allowed
                else "dropped:window_limit_exceeded"
            ),
        }
        binding_trace = binding_trace_to_payload(resolved.trace)
        if allowed:
            completion_branch_port = (
                _DEFAULT_BRANCH_PORT
                if _has_outbound_source_port(
                    graph=kwargs.get("graph"),
                    gate_id=gate.id,
                    source_port=_DEFAULT_BRANCH_PORT,
                )
                else None
            )
            logger.info(
                "gate:throttle allowed activation gate_id=%s key=%s event_id=%s "
                "accepted_count=%s limit=%s window_expires_at=%s",
                gate.id,
                key,
                queue_record.event_id,
                claim["accepted_count"],
                claim["limit"],
                claim["window_expires_at"],
            )
            worker._write_gate_record(
                queue_record=queue_record,
                gate=gate,
                status="succeeded",
                started_at=started_at,
                resolved_config=resolved_config,
                outputs=_passthrough_outputs(queue_record.payload),
                inputs_by_source=inputs_by_source,
                input_event_ids_by_source=input_event_ids_by_source,
                input_occurred_at_by_source=input_occurred_at_by_source,
                config_revision=config_revision,
                binding_trace=binding_trace,
                publish_completion_event=True,
                branch_port=completion_branch_port,
            )
            return True

        logger.info(
            "gate:throttle dropped activation gate_id=%s key=%s event_id=%s "
            "accepted_count=%s limit=%s window_expires_at=%s",
            gate.id,
            key,
            queue_record.event_id,
            claim["accepted_count"],
            claim["limit"],
            claim["window_expires_at"],
        )
        publish_dropped_event = _has_outbound_source_port(
            graph=kwargs.get("graph"),
            gate_id=gate.id,
            source_port=_DROPPED_BRANCH_PORT,
        )
        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="dropped",
            started_at=started_at,
            resolved_config=resolved_config,
            outputs=(_passthrough_outputs(queue_record.payload) if publish_dropped_event else {}),
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
            error="throttle limit reached within fixed window",
            binding_trace=binding_trace,
            publish_completion_event=publish_dropped_event,
            branch_port=_DROPPED_BRANCH_PORT if publish_dropped_event else None,
        )
        return True


def _passthrough_outputs(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        item_key: value
        for item_key, value in payload.items()
        if item_key
        not in {
            NODE_OUTPUTS_CONTEXT_KEY,
            _BRANCH_PORT_PAYLOAD_KEY,
            _FOR_EXECUTION_IDS_PAYLOAD_KEY,
        }
    }


def _has_outbound_source_port(*, graph: Any, gate_id: str, source_port: str) -> bool:
    outbound_index = getattr(graph, "outbound_index", {})
    if not isinstance(outbound_index, dict):
        return False
    return any(_edge_source_port(edge) == source_port for edge in outbound_index.get(gate_id, ()))


def _edge_source_port(edge: Any) -> str | None:
    value = edge.get("source_port") if isinstance(edge, dict) else getattr(edge, "source_port", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_non_empty_string(value: Any, *, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise GateRuntimeError(f"gate:throttle {field_name} must resolve to a non-empty string")
    return text


def _coerce_positive_int(value: Any) -> int:
    if isinstance(value, bool):
        raise GateRuntimeError("gate:throttle limit must resolve to a positive integer")
    if isinstance(value, int):
        limit = value
    elif isinstance(value, float) and value.is_integer():
        limit = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        limit = int(value.strip())
    else:
        raise GateRuntimeError("gate:throttle limit must resolve to a positive integer")
    if limit < 1:
        raise GateRuntimeError("gate:throttle limit must resolve to a positive integer")
    return limit
