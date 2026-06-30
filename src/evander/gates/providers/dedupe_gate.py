from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from evander.events.models import NODE_OUTPUTS_CONTEXT_KEY
from evander.gate_runtime import GateRuntimeError
from evander.gates.providers.base import GateProviderBase
from evander.workflow.binding import (
    binding_trace_to_payload,
    resolve_workflow_bindings,
)
from evander.workflow.validation import NodeCapability

_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"
_DROPPED_BRANCH_PORT = "dropped"

logger = logging.getLogger(__name__)


class DedupeGateProvider(GateProviderBase):
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
        if raw_key is None:
            raise GateRuntimeError("gate:dedupe requires key")
        raw_ttl = getattr(gate, "ttl", None)
        if raw_ttl is None:
            raise GateRuntimeError("gate:dedupe requires ttl")

        resolved = resolve_workflow_bindings(
            {"key": raw_key, "ttl": raw_ttl},
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
            resolved.resolved_config, gate_type="gate:dedupe"
        )
        key = _coerce_non_empty_string(resolved_payload.get("key"), field_name="key")
        ttl_text = _coerce_non_empty_string(resolved_payload.get("ttl"), field_name="ttl")
        ttl = parse_iso8601_duration(ttl_text)
        if ttl.total_seconds() <= 0:
            raise GateRuntimeError("gate:dedupe ttl must be greater than zero")

        allowed = worker.claim_dedupe_key(
            gate_id=gate.id,
            key=key,
            ttl=ttl,
            now=started_at,
            event_id=queue_record.event_id,
        )
        resolved_config = {
            "key": key,
            "ttl": ttl_text,
            "allowed": allowed,
            "diagnostic": "allowed:first_seen" if allowed else "suppressed:duplicate_within_ttl",
        }
        if allowed:
            logger.info(
                "gate:dedupe allowed activation gate_id=%s key=%s event_id=%s",
                gate.id,
                key,
                queue_record.event_id,
            )
            outputs = _passthrough_outputs(queue_record.payload)
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
                binding_trace=binding_trace_to_payload(resolved.trace),
                publish_completion_event=True,
            )
            return True

        logger.info(
            "gate:dedupe suppressed duplicate activation gate_id=%s key=%s event_id=%s",
            gate.id,
            key,
            queue_record.event_id,
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
            outputs=(
                _passthrough_outputs(queue_record.payload)
                if publish_dropped_event
                else {}
            ),
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
            error="duplicate key suppressed within ttl",
            binding_trace=binding_trace_to_payload(resolved.trace),
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


def _has_outbound_source_port(
    *, graph: Any, gate_id: str, source_port: str
) -> bool:
    outbound_index = getattr(graph, "outbound_index", {})
    if not isinstance(outbound_index, dict):
        return False
    return any(
        _edge_source_port(edge) == source_port
        for edge in outbound_index.get(gate_id, ())
    )


def _edge_source_port(edge: Any) -> str | None:
    if isinstance(edge, dict):
        value = edge.get("source_port")
    else:
        value = getattr(edge, "source_port", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_non_empty_string(value: Any, *, field_name: str) -> str:
    if value is None:
        raise GateRuntimeError(f"gate:dedupe requires {field_name}")
    text = str(value).strip()
    if not text:
        raise GateRuntimeError(f"gate:dedupe {field_name} must be a non-empty string")
    return text


def parse_iso8601_duration(value: str) -> timedelta:
    text = value.strip().upper()
    if not text.startswith("P"):
        raise GateRuntimeError("gate:dedupe ttl must be an ISO-8601 duration such as P7D or PT1H")
    days = hours = minutes = 0
    seconds = 0.0
    date_part = text[1:]
    time_part = ""
    if "T" in date_part:
        date_part, time_part = date_part.split("T", 1)
    if date_part:
        if not date_part.endswith("D") or date_part[:-1] == "":
            raise GateRuntimeError(
                "gate:dedupe ttl supports day/time ISO-8601 durations such as P7D or PT1H"
            )
        try:
            days = int(date_part[:-1])
        except ValueError as exc:
            raise GateRuntimeError("gate:dedupe ttl days must be an integer") from exc
    if time_part:
        import re

        pattern = re.compile(r"^(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?$")
        match = pattern.fullmatch(time_part)
        if match is None or not any(match.groups()):
            raise GateRuntimeError("gate:dedupe ttl supports time units H, M, and S")
        if match.group(1):
            hours = int(match.group(1))
        if match.group(2):
            minutes = int(match.group(2))
        if match.group(3):
            seconds = float(match.group(3))
    if not date_part and not time_part:
        raise GateRuntimeError("gate:dedupe ttl must include at least one duration unit")
    return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
