from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from evander.events.models import NODE_OUTPUTS_CONTEXT_KEY
from evander.gate_runtime import GateRuntimeError, parse_iso8601_duration
from evander.gates.providers.base import GateProviderBase
from evander.triggers.queue import TriggerQueueRecord
from evander.storage import JsonStore
from evander.workflow.binding import (
    resolve_workflow_bindings,
)
from evander.workflow.validation import NodeCapability

logger = logging.getLogger(__name__)

_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"


class DebounceGateStateRecord(BaseModel):
    state_key: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)
    debounce_key: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    source_node_id: str = Field(min_length=1)
    source_trigger_type: str = Field(min_length=1)
    first_event_id: str = Field(min_length=1)
    latest_event_id: str = Field(min_length=1)
    first_seen_at: datetime
    last_seen_at: datetime
    scheduled_release_at: datetime
    max_release_at: datetime
    quiet_period_seconds: float = Field(gt=0)
    max_delay_seconds: float = Field(gt=0)
    latest_payload: dict[str, Any] = Field(default_factory=dict)
    latest_event_payload: dict[str, Any] = Field(default_factory=dict)
    inputs_by_source: dict[str, dict[str, Any]] = Field(default_factory=dict)
    input_event_ids_by_source: dict[str, str] = Field(default_factory=dict)
    input_occurred_at_by_source: dict[str, datetime] = Field(default_factory=dict)
    seen_event_ids: list[str] = Field(default_factory=list)
    config_revision: str | None = None
    updated_at: datetime


class _DebounceGateStateStoreAdapter:
    def __init__(self, path: Path) -> None:
        self.store = JsonStore(path, DebounceGateStateRecord)

    def read_all(self) -> dict[str, DebounceGateStateRecord]:
        return self.store.read_all()

    def get(self, key: str) -> DebounceGateStateRecord | None:
        return self.store.get(key)

    def put(self, key: str, record: DebounceGateStateRecord) -> None:
        self.store.put(key, record)

    def delete(self, key: str) -> None:
        self.store.delete(key)


class DebounceGateProvider(GateProviderBase):
    capability = NodeCapability(min_inbound=1, max_inbound=1)

    def __init__(self) -> None:
        super().__init__()
        self._adapter: _DebounceGateStateStoreAdapter | None = None

    def load_state(self, **kwargs: Any) -> _DebounceGateStateStoreAdapter:
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
            namespace=("gates", "debounce", "state_store_path"),
            default=runtime_dir / "providers" / "gates" / "debounce" / "state.json",
        )
        self._adapter = _DebounceGateStateStoreAdapter(state_path)
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
                "quiet_period": gate.quiet_period,
                "max_delay": gate.max_delay,
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
            resolved.resolved_config, gate_type="gate:debounce"
        )
        debounce_key = str(resolved_config.get("key") or "").strip()
        if not debounce_key:
            raise GateRuntimeError("gate:debounce key must resolve to a non-empty string")
        quiet_period = parse_iso8601_duration(
            resolved_config.get("quiet_period"), field_name="quiet_period"
        )
        max_delay = parse_iso8601_duration(resolved_config.get("max_delay"), field_name="max_delay")
        if max_delay < quiet_period:
            raise GateRuntimeError(
                "gate:debounce max_delay must be greater than or equal to quiet_period"
            )

        now = _coerce_utc(queue_record.occurred_at)
        state_key = _debounce_state_key(gate_id=gate.id, debounce_key=debounce_key)
        adapter = self.load_state()
        state = adapter.get(state_key)
        if state is not None and queue_record.event_id in state.seen_event_ids:
            logger.info(
                "Ignoring duplicate debounce activation gate_id=%s key=%s event_id=%s "
                "first_seen_at=%s last_seen_at=%s scheduled_release_at=%s",
                gate.id,
                debounce_key,
                queue_record.event_id,
                state.first_seen_at.isoformat(),
                state.last_seen_at.isoformat(),
                state.scheduled_release_at.isoformat(),
            )
            return True

        latest_payload = _forwardable_payload(queue_record.payload)
        latest_event_payload = dict(queue_record.payload)
        if state is None:
            first_seen_at = now
            max_release_at = first_seen_at + max_delay
            state = DebounceGateStateRecord(
                state_key=state_key,
                gate_id=gate.id,
                debounce_key=debounce_key,
                trace_id=queue_record.trace_id,
                source_node_id=queue_record.trigger_id,
                source_trigger_type=queue_record.trigger_type,
                first_event_id=queue_record.event_id,
                latest_event_id=queue_record.event_id,
                first_seen_at=first_seen_at,
                last_seen_at=now,
                scheduled_release_at=min(now + quiet_period, max_release_at),
                max_release_at=max_release_at,
                quiet_period_seconds=quiet_period.total_seconds(),
                max_delay_seconds=max_delay.total_seconds(),
                latest_payload=latest_payload,
                latest_event_payload=latest_event_payload,
                inputs_by_source=inputs_by_source,
                input_event_ids_by_source=input_event_ids_by_source,
                input_occurred_at_by_source=input_occurred_at_by_source,
                seen_event_ids=[queue_record.event_id],
                config_revision=config_revision,
                updated_at=datetime.now(UTC),
            )
        else:
            state.latest_event_id = queue_record.event_id
            state.last_seen_at = max(state.last_seen_at, now)
            state.scheduled_release_at = min(
                state.last_seen_at + quiet_period, state.max_release_at
            )
            state.quiet_period_seconds = quiet_period.total_seconds()
            state.max_delay_seconds = max_delay.total_seconds()
            state.latest_payload = latest_payload
            state.latest_event_payload = latest_event_payload
            state.inputs_by_source = inputs_by_source
            state.input_event_ids_by_source = input_event_ids_by_source
            state.input_occurred_at_by_source = input_occurred_at_by_source
            state.config_revision = config_revision
            state.updated_at = datetime.now(UTC)
            state.seen_event_ids = [*state.seen_event_ids, queue_record.event_id][-100:]
        adapter.put(state_key, state)
        logger.info(
            "Scheduled debounce gate_id=%s key=%s first_seen_at=%s last_seen_at=%s "
            "scheduled_release_at=%s max_release_at=%s latest_event_id=%s",
            gate.id,
            debounce_key,
            state.first_seen_at.isoformat(),
            state.last_seen_at.isoformat(),
            state.scheduled_release_at.isoformat(),
            state.max_release_at.isoformat(),
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
            if gate is None or getattr(gate, "type", "") != "gate:debounce":
                continue
            reason = _release_reason(state=state, now=now)
            if reason is None:
                continue
            queue_record = TriggerQueueRecord(
                event_id=state.latest_event_id,
                trigger_id=state.source_node_id,
                trigger_type=state.source_trigger_type,
                trace_id=state.trace_id,
                occurred_at=state.last_seen_at,
                payload=dict(state.latest_event_payload or state.latest_payload),
                delivery_state="delivered",
            )
            resolved_config = {
                "key": state.debounce_key,
                "quiet_period": _format_seconds(state.quiet_period_seconds),
                "max_delay": _format_seconds(state.max_delay_seconds),
                "first_seen_at": state.first_seen_at.isoformat(),
                "last_seen_at": state.last_seen_at.isoformat(),
                "scheduled_release_at": state.scheduled_release_at.isoformat(),
                "release_reason": reason,
            }
            logger.info(
                "Releasing debounce gate_id=%s key=%s first_seen_at=%s last_seen_at=%s "
                "scheduled_release_at=%s reason=%s latest_event_id=%s",
                state.gate_id,
                state.debounce_key,
                state.first_seen_at.isoformat(),
                state.last_seen_at.isoformat(),
                state.scheduled_release_at.isoformat(),
                reason,
                state.latest_event_id,
            )
            worker._write_gate_record(
                queue_record=queue_record,
                gate=gate,
                status="succeeded",
                started_at=now,
                resolved_config=resolved_config,
                outputs=dict(state.latest_payload),
                inputs_by_source=state.inputs_by_source,
                input_event_ids_by_source=state.input_event_ids_by_source,
                input_occurred_at_by_source=state.input_occurred_at_by_source,
                config_revision=state.config_revision,
                publish_completion_event=True,
            )
            adapter.delete(state_key)
            released += 1
        return released

    def next_reconcile_at(self, **kwargs: Any) -> datetime | None:
        graph = kwargs.get("graph")
        active_gate_ids = (
            {
                gate_id
                for gate_id, gate in graph.gates_by_id.items()
                if getattr(gate, "type", "") == "gate:debounce"
            }
            if graph is not None
            else None
        )
        deadlines = [
            min(_coerce_utc(state.scheduled_release_at), _coerce_utc(state.max_release_at))
            for state in self.load_state().read_all().values()
            if active_gate_ids is None or state.gate_id in active_gate_ids
        ]
        return min(deadlines) if deadlines else None


def _release_reason(
    *, state: DebounceGateStateRecord, now: datetime
) -> Literal["quiet_period", "max_delay"] | None:
    normalized_now = _coerce_utc(now)
    if normalized_now >= _coerce_utc(state.max_release_at):
        return "max_delay"
    if normalized_now >= _coerce_utc(state.scheduled_release_at):
        return "quiet_period"
    return None


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


def _debounce_state_key(*, gate_id: str, debounce_key: str) -> str:
    digest = hashlib.sha256(debounce_key.encode("utf-8")).hexdigest()[:24]
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


def _format_seconds(seconds: float) -> str:
    if float(seconds).is_integer():
        return f"PT{int(seconds)}S"
    return f"PT{seconds}S"
