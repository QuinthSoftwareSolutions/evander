from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from evander.events.models import NODE_OUTPUTS_CONTEXT_KEY
from evander.gate_runtime import GateRuntimeError, parse_iso8601_duration
from evander.gates.providers.base import GateProviderBase
from evander.storage import JsonStore
from evander.triggers.queue import TriggerQueueRecord
from evander.workflow.binding import (
    binding_trace_to_payload,
    resolve_workflow_bindings,
)
from evander.workflow.validation import NodeCapability

logger = logging.getLogger(__name__)

_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"


class BufferGateItem(BaseModel):
    event_id: str = Field(min_length=1)
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class BufferGateStateRecord(BaseModel):
    state_key: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)
    buffer_key: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    source_node_id: str = Field(min_length=1)
    source_trigger_type: str = Field(min_length=1)
    first_event_id: str = Field(min_length=1)
    latest_event_id: str = Field(min_length=1)
    first_seen_at: datetime
    last_seen_at: datetime
    scheduled_release_at: datetime | None = None
    count: int = Field(gt=0)
    timeout_seconds: float | None = Field(default=None, gt=0)
    items: list[BufferGateItem] = Field(default_factory=list)
    latest_event_payload: dict[str, Any] = Field(default_factory=dict)
    inputs_by_source: dict[str, dict[str, Any]] = Field(default_factory=dict)
    input_event_ids_by_source: dict[str, str] = Field(default_factory=dict)
    input_occurred_at_by_source: dict[str, datetime] = Field(default_factory=dict)
    seen_event_ids: list[str] = Field(default_factory=list)
    binding_trace: dict[str, Any] | None = None
    config_revision: str | None = None
    updated_at: datetime


class _BufferGateStateStoreAdapter:
    def __init__(self, path: Path) -> None:
        self.store = JsonStore(path, BufferGateStateRecord)

    def read_all(self) -> dict[str, BufferGateStateRecord]:
        return self.store.read_all()

    def get(self, key: str) -> BufferGateStateRecord | None:
        return self.store.get(key)

    def put(self, key: str, record: BufferGateStateRecord) -> None:
        self.store.put(key, record)

    def delete(self, key: str) -> None:
        self.store.delete(key)


class BufferGateProvider(GateProviderBase):
    capability = NodeCapability(min_inbound=1, max_inbound=1)

    def __init__(self) -> None:
        super().__init__()
        self._adapter: _BufferGateStateStoreAdapter | None = None

    def load_state(self, **kwargs: Any) -> _BufferGateStateStoreAdapter:
        del kwargs
        if self._adapter is not None:
            return self._adapter
        runtime_config = (
            self.runtime_context.resolved_config if self.runtime_context is not None else None
        )
        runtime_dir = Path(getattr(runtime_config, "runtime_dir", Path("runtime")))
        provider_state = (
            getattr(runtime_config, "provider_state", {}) if runtime_config is not None else {}
        )
        state_path = _provider_state_path(
            provider_state,
            namespace=("gates", "buffer", "state_store_path"),
            default=runtime_dir / "providers" / "gates" / "buffer" / "state.json",
        )
        self._adapter = _BufferGateStateStoreAdapter(state_path)
        return self._adapter

    def cleanup(self, **kwargs: Any) -> None:
        del kwargs
        self._adapter = None

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

        resolved = resolve_workflow_bindings(
            {
                "key": gate.key,
                "count": gate.count,
                "timeout": gate.timeout,
            },
            upstream_payload,
            upstream_output_names,
            system_value_resolver=worker._system_value_resolver_for(
                started_at=started_at,
                queue_record=queue_record,
                node_id=gate.id,
            ),
            strict_validation=True,
        )
        resolved_config = worker._require_object_payload(
            resolved.resolved_config, gate_type="gate:buffer"
        )
        buffer_key = _coerce_non_empty_string(resolved_config.get("key"), field_name="key")
        count = _coerce_positive_int(resolved_config.get("count"))
        timeout_seconds = _coerce_timeout_seconds(resolved_config.get("timeout"))

        event_occurred_at = _coerce_utc(queue_record.occurred_at)
        buffered_at = _coerce_utc(started_at)
        state_key = _buffer_state_key(gate_id=gate.id, buffer_key=buffer_key)
        adapter = self.load_state()
        state = adapter.get(state_key)
        if state is not None and (
            queue_record.event_id in state.seen_event_ids
            or any(item.event_id == queue_record.event_id for item in state.items)
        ):
            logger.info(
                "Ignoring duplicate buffer activation gate_id=%s key=%s event_id=%s "
                "item_count=%d first_seen_at=%s",
                gate.id,
                buffer_key,
                queue_record.event_id,
                len(state.items),
                state.first_seen_at.isoformat(),
            )
            return True

        if _state_is_due_for_timeout_release(state=state, now=buffered_at):
            self._release_buffer_state(
                worker=worker,
                gate=gate,
                state_key=state_key,
                state=state,
                adapter=adapter,
                reason="timeout",
                started_at=started_at,
            )
            state = None

        item = BufferGateItem(
            event_id=queue_record.event_id,
            occurred_at=event_occurred_at,
            payload=_forwardable_payload(queue_record.payload),
        )
        binding_trace = binding_trace_to_payload(resolved.trace)
        scheduled_release_at = (
            buffered_at + _seconds_to_timedelta(timeout_seconds)
            if timeout_seconds is not None
            else None
        )
        if state is None:
            state = BufferGateStateRecord(
                state_key=state_key,
                gate_id=gate.id,
                buffer_key=buffer_key,
                trace_id=queue_record.trace_id,
                source_node_id=queue_record.trigger_id,
                source_trigger_type=queue_record.trigger_type,
                first_event_id=queue_record.event_id,
                latest_event_id=queue_record.event_id,
                first_seen_at=buffered_at,
                last_seen_at=buffered_at,
                scheduled_release_at=scheduled_release_at,
                count=count,
                timeout_seconds=timeout_seconds,
                items=[item],
                latest_event_payload=dict(queue_record.payload),
                inputs_by_source=inputs_by_source,
                input_event_ids_by_source=input_event_ids_by_source,
                input_occurred_at_by_source=input_occurred_at_by_source,
                seen_event_ids=[queue_record.event_id],
                binding_trace=binding_trace,
                config_revision=config_revision,
                updated_at=datetime.now(UTC),
            )
        else:
            state.latest_event_id = queue_record.event_id
            state.last_seen_at = max(_coerce_utc(state.last_seen_at), buffered_at)
            state.count = count
            state.timeout_seconds = timeout_seconds
            state.scheduled_release_at = (
                _coerce_utc(state.first_seen_at) + _seconds_to_timedelta(timeout_seconds)
                if timeout_seconds is not None
                else None
            )
            state.items = [*state.items, item]
            state.latest_event_payload = dict(queue_record.payload)
            state.inputs_by_source = inputs_by_source
            state.input_event_ids_by_source = input_event_ids_by_source
            state.input_occurred_at_by_source = input_occurred_at_by_source
            state.config_revision = config_revision
            state.binding_trace = binding_trace
            state.updated_at = datetime.now(UTC)
            state.seen_event_ids = [*state.seen_event_ids, queue_record.event_id]

        if len(state.items) >= count:
            outputs = _release_outputs(state=state, reason="count")
            logger.info(
                "Releasing buffer by count gate_id=%s key=%s item_count=%d event_id=%s",
                gate.id,
                buffer_key,
                len(state.items),
                queue_record.event_id,
            )
            worker._write_gate_record(
                queue_record=queue_record,
                gate=gate,
                status="succeeded",
                started_at=started_at,
                resolved_config=_release_resolved_config(state=state, reason="count"),
                outputs=outputs,
                inputs_by_source=inputs_by_source,
                input_event_ids_by_source=input_event_ids_by_source,
                input_occurred_at_by_source=input_occurred_at_by_source,
                config_revision=config_revision,
                binding_trace=binding_trace,
                publish_completion_event=True,
            )
            adapter.delete(state_key)
            return True

        adapter.put(state_key, state)
        logger.info(
            "Buffered activation gate_id=%s key=%s item_count=%d count=%d "
            "scheduled_release_at=%s latest_event_id=%s",
            gate.id,
            buffer_key,
            len(state.items),
            count,
            state.scheduled_release_at.isoformat() if state.scheduled_release_at else None,
            state.latest_event_id,
        )
        return True

    def reconcile(self, **kwargs: Any) -> int:
        worker = kwargs["worker"]
        graph = kwargs["graph"]
        now = datetime.now(UTC)
        released = 0
        adapter = self.load_state()
        for state_key, state in list(adapter.read_all().items()):
            gate = graph.gates_by_id.get(state.gate_id)
            if gate is None or getattr(gate, "type", "") != "gate:buffer":
                continue
            if not _state_is_due_for_timeout_release(state=state, now=now):
                continue
            self._release_buffer_state(
                worker=worker,
                gate=gate,
                state_key=state_key,
                state=state,
                adapter=adapter,
                reason="timeout",
                started_at=now,
            )
            released += 1
        return released

    def _release_buffer_state(
        self,
        *,
        worker: Any,
        gate: Any,
        state_key: str,
        state: BufferGateStateRecord,
        adapter: _BufferGateStateStoreAdapter,
        reason: Literal["timeout"],
        started_at: datetime,
    ) -> None:
        queue_record = TriggerQueueRecord(
            event_id=state.latest_event_id,
            trigger_id=state.source_node_id,
            trigger_type=state.source_trigger_type,
            trace_id=state.trace_id,
            occurred_at=state.last_seen_at,
            payload=dict(state.latest_event_payload),
            delivery_state="delivered",
        )
        outputs = _release_outputs(state=state, reason=reason)
        scheduled_release_at = state.scheduled_release_at
        logger.info(
            "Releasing buffer by %s gate_id=%s key=%s item_count=%d "
            "first_seen_at=%s scheduled_release_at=%s latest_event_id=%s",
            reason,
            state.gate_id,
            state.buffer_key,
            len(state.items),
            state.first_seen_at.isoformat(),
            scheduled_release_at.isoformat() if scheduled_release_at is not None else None,
            state.latest_event_id,
        )
        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config=_release_resolved_config(state=state, reason=reason),
            outputs=outputs,
            inputs_by_source=state.inputs_by_source,
            input_event_ids_by_source=state.input_event_ids_by_source,
            input_occurred_at_by_source=state.input_occurred_at_by_source,
            config_revision=state.config_revision,
            binding_trace=state.binding_trace,
            publish_completion_event=True,
        )
        adapter.delete(state_key)


def _release_outputs(
    *, state: BufferGateStateRecord, reason: Literal["count", "timeout"]
) -> dict[str, Any]:
    items = [dict(item.payload) for item in state.items]
    return {
        "items": items,
        "count": len(items),
        "release_reason": reason,
    }


def _release_resolved_config(
    *, state: BufferGateStateRecord, reason: Literal["count", "timeout"]
) -> dict[str, Any]:
    return {
        "key": state.buffer_key,
        "count": state.count,
        "timeout": _format_seconds(state.timeout_seconds)
        if state.timeout_seconds is not None
        else None,
        "first_event_id": state.first_event_id,
        "latest_event_id": state.latest_event_id,
        "first_seen_at": state.first_seen_at.isoformat(),
        "last_seen_at": state.last_seen_at.isoformat(),
        "scheduled_release_at": (
            state.scheduled_release_at.isoformat() if state.scheduled_release_at else None
        ),
        "release_reason": reason,
    }


def _forwardable_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in payload.items()
        if key
        not in {
            NODE_OUTPUTS_CONTEXT_KEY,
            _BRANCH_PORT_PAYLOAD_KEY,
            _FOR_EXECUTION_IDS_PAYLOAD_KEY,
        }
    }


def _state_is_due_for_timeout_release(
    *, state: BufferGateStateRecord | None, now: datetime
) -> bool:
    if state is None or state.scheduled_release_at is None:
        return False
    return _coerce_utc(now) >= _coerce_utc(state.scheduled_release_at)


def _coerce_non_empty_string(value: Any, *, field_name: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise GateRuntimeError(f"gate:buffer {field_name} must resolve to a non-empty string")
    return text


def _coerce_positive_int(value: Any) -> int:
    if isinstance(value, bool):
        raise GateRuntimeError("gate:buffer count must resolve to a positive integer")
    if isinstance(value, int):
        count = value
    elif isinstance(value, float) and value.is_integer():
        count = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        count = int(value.strip())
    else:
        raise GateRuntimeError("gate:buffer count must resolve to a positive integer")
    if count < 1:
        raise GateRuntimeError("gate:buffer count must resolve to a positive integer")
    return count


def _coerce_timeout_seconds(value: Any) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    timeout = parse_iso8601_duration(value, field_name="timeout", gate_type="gate:buffer")
    return timeout.total_seconds()


def _buffer_state_key(*, gate_id: str, buffer_key: str) -> str:
    digest = hashlib.sha256(buffer_key.encode("utf-8")).hexdigest()[:24]
    return f"{gate_id}:{digest}"


def _provider_state_path(provider_state: Any, *, namespace: tuple[str, ...], default: Path) -> Path:
    cursor: Any = provider_state if isinstance(provider_state, dict) else {}
    for key in namespace:
        if not isinstance(cursor, dict):
            return default
        cursor = cursor.get(key)
    if cursor is None:
        return default
    text = str(cursor).strip()
    return Path(text) if text else default


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _seconds_to_timedelta(seconds: float) -> timedelta:
    return timedelta(seconds=seconds)


def _format_seconds(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    if float(seconds).is_integer():
        return f"PT{int(seconds)}S"
    return f"PT{seconds}S"
