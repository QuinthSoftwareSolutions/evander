from __future__ import annotations


from agent_sandbox.models import utc_now
from collections.abc import (
    Mapping,
    Sequence,
)
from typing import Any
from uuid import uuid4
from .types import TeamCommandApplyResult
from ...models import TeamOutboxRecord
from ..domain.common import (
    _optional_text,
    _required_text,
)
from ..domain.ephemeral import (
    EPHEMERAL_COMPLETED_EVENT_TYPE,
    EPHEMERAL_REQUESTED_EVENT_TYPE,
)
from ..domain.git_common import GIT_PROMOTION_EVENT_TYPE
from ..domain.release_packages import GIT_RELEASE_RECOVERY_EVENT_TYPE

def _team_event_outbox_records(
    events: Sequence[Mapping[str, Any]],
) -> list[TeamOutboxRecord]:
    records: list[TeamOutboxRecord] = []
    now = utc_now()
    for event in events:
        event_type = _required_text(event.get("event_type"), "event_type")
        payload = event.get("payload")
        payload_mapping = dict(payload) if isinstance(payload, Mapping) else {}
        aggregate_type = "team_event"
        aggregate_id = _optional_text(event.get("correlation_key"))
        if event_type == GIT_PROMOTION_EVENT_TYPE:
            aggregate_type = "git_promotion"
            aggregate_id = _optional_text(payload_mapping.get("promotion_id")) or aggregate_id
        elif event_type == GIT_RELEASE_RECOVERY_EVENT_TYPE:
            aggregate_type = "git_release_recovery"
            aggregate_id = _optional_text(payload_mapping.get("release_package_id")) or aggregate_id
        elif event_type in {
            EPHEMERAL_REQUESTED_EVENT_TYPE,
            EPHEMERAL_COMPLETED_EVENT_TYPE,
        }:
            aggregate_type = "ephemeral_assignment"
            aggregate_id = _optional_text(payload_mapping.get("assignment_id")) or aggregate_id
        outbox_payload: dict[str, Any] = {
            "event_type": event_type,
            "correlation_key": _optional_text(event.get("correlation_key")),
            "payload": payload_mapping,
        }
        if "delay_seconds" in event:
            outbox_payload["delay_seconds"] = event.get("delay_seconds")
        records.append(
            TeamOutboxRecord(
                id=f"outbox-{uuid4().hex}",
                intent_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                idempotency_key=_optional_text(event.get("idempotency_key")),
                payload=outbox_payload,
                created_at=now,
                updated_at=now,
            )
        )
    return records

def _team_command_outbox_records(
    apply_result: TeamCommandApplyResult,
) -> list[TeamOutboxRecord]:
    records = [TeamOutboxRecord.model_validate(record) for record in apply_result.outbox_records]
    events = _response_event_mappings(apply_result.response_payload)
    if not events:
        return records
    seen = {_outbox_record_dedupe_key(record) for record in records}
    for record in _team_event_outbox_records(events):
        key = _outbox_record_dedupe_key(record)
        if key in seen:
            continue
        records.append(record)
        seen.add(key)
    return records

def _response_event_mappings(response_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    events = response_payload.get("events")
    if not isinstance(events, Sequence) or isinstance(events, (str, bytes, bytearray)):
        return []
    return [event for event in events if isinstance(event, Mapping)]

def _outbox_record_dedupe_key(record: TeamOutboxRecord) -> tuple[Any, ...]:
    if record.idempotency_key is not None:
        return ("idempotency", record.idempotency_key)
    return (
        "aggregate",
        record.intent_type,
        record.aggregate_type,
        record.aggregate_id,
    )

def _append_response_outbox_record_ids(
    response: dict[str, Any],
    outbox_records: Sequence[TeamOutboxRecord],
) -> None:
    if not outbox_records:
        return
    raw_ids = response.get("outbox_record_ids")
    if isinstance(raw_ids, Sequence) and not isinstance(raw_ids, (str, bytes, bytearray)):
        outbox_ids = [str(item) for item in raw_ids]
    else:
        outbox_ids = []
    for record in outbox_records:
        if record.id not in outbox_ids:
            outbox_ids.append(record.id)
    response["outbox_record_ids"] = outbox_ids

__all__ = [
    '_team_event_outbox_records',
    '_team_command_outbox_records',
    '_response_event_mappings',
    '_outbox_record_dedupe_key',
    '_append_response_outbox_record_ids',
]
