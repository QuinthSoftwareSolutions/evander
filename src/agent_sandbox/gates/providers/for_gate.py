from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_sandbox.gate_runtime import GateRuntimeError
from agent_sandbox.gates.providers.base import GateProviderBase
from agent_sandbox.storage import JsonStore
from agent_sandbox.workflow_binding import resolve_workflow_bindings
from agent_sandbox.workflow_validation import NodeCapability


class ForGateStateRecord(BaseModel):
    key: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    for_execution_id: str = Field(min_length=1)
    next_index: int = Field(ge=0)
    item_count: int = Field(ge=0)
    updated_at: datetime


class _ForGateStateStoreAdapter:
    def __init__(self, path: Path) -> None:
        self.store = JsonStore(path, ForGateStateRecord)

    def get(self, key: str) -> ForGateStateRecord | None:
        return self.store.get(key)

    def put(self, key: str, record: ForGateStateRecord) -> None:
        self.store.put(key, record)

    def delete(self, key: str) -> None:
        self.store.delete(key)


class ForGateProvider(GateProviderBase):
    capability = NodeCapability(
        min_inbound=1,
        non_cyclic_inbound_exact=1,
        allowed_source_ports=frozenset({"body", "done"}),
        required_source_ports=frozenset({"body", "done"}),
        completion_semantics="loop",
    )

    def __init__(self) -> None:
        super().__init__()
        self._adapter: _ForGateStateStoreAdapter | None = None

    def load_state(self, **kwargs: Any) -> _ForGateStateStoreAdapter:
        if self._adapter is not None:
            return self._adapter
        runtime_config = self.runtime_context.resolved_config if self.runtime_context is not None else None
        runtime_dir = Path(getattr(runtime_config, "runtime_dir", Path("runtime")))
        provider_state = getattr(runtime_config, "provider_state", {}) if runtime_config is not None else {}
        state_path = _provider_state_path(
            provider_state,
            namespace=("gates", "for", "state_store_path"),
            default=runtime_dir / "providers" / "gates" / "for" / "state.json",
        )
        legacy_path = runtime_dir / "for-gate-state.json"
        _migrate_legacy_store(legacy_path=legacy_path, provider_owned_path=state_path)
        self._adapter = _ForGateStateStoreAdapter(state_path)
        return self._adapter

    def cleanup(self, **kwargs: Any) -> None:
        del kwargs
        self._adapter = None

    def execute(self, **kwargs: Any) -> bool:
        worker = kwargs["worker"]
        queue_record = kwargs["queue_record"]
        gate = kwargs["gate"]
        graph = kwargs["graph"]
        started_at = kwargs["started_at"]
        config_revision = kwargs["config_revision"]
        upstream_payload = kwargs["upstream_payload"]
        upstream_output_names = kwargs["upstream_output_names"]
        inputs_by_source = kwargs["inputs_by_source"]
        input_event_ids_by_source = kwargs["input_event_ids_by_source"]
        input_occurred_at_by_source = kwargs["input_occurred_at_by_source"]

        resolved = resolve_workflow_bindings(
            {"items": gate.items},
            upstream_payload,
            upstream_output_names,
            system_value_resolver=worker._system_value_resolver_for(
                started_at=started_at,
                queue_record=queue_record,
                node_id=gate.id,
            ),
            strict_validation=True,
        )
        resolved_config = worker._require_object_payload(resolved.resolved_config, gate_type="gate:for")
        items = resolved_config.get("items")
        if not isinstance(items, list):
            raise GateRuntimeError("gate:for items must resolve to an array")

        for_execution_id = _resolve_for_gate_execution_id(queue_record=queue_record, gate=gate, graph=graph)
        state_key = _for_gate_state_key(gate_id=gate.id, trace_id=queue_record.trace_id, for_execution_id=for_execution_id)
        adapter = self.load_state()
        state = adapter.get(state_key)
        if state is None:
            state = ForGateStateRecord(
                key=state_key,
                gate_id=gate.id,
                trace_id=queue_record.trace_id,
                for_execution_id=for_execution_id,
                next_index=0,
                item_count=len(items),
                updated_at=datetime.now(UTC),
            )

        is_done = state.next_index >= state.item_count
        branch_port = "done" if is_done else "body"
        if is_done:
            outputs = {
                "current_index": state.item_count,
                "current_element": None,
                "item_count": state.item_count,
                "is_done": True,
            }
            adapter.delete(state_key)
        else:
            current_index = state.next_index
            outputs = {
                "current_index": current_index,
                "current_element": items[current_index] if current_index < len(items) else None,
                "item_count": state.item_count,
                "is_done": False,
            }
            state.next_index = current_index + 1
            state.updated_at = datetime.now(UTC)
            adapter.put(state_key, state)

        worker._write_gate_record(
            queue_record=queue_record,
            gate=gate,
            status="succeeded",
            started_at=started_at,
            resolved_config={"items": items, "for_execution_id": for_execution_id},
            outputs=outputs,
            inputs_by_source=inputs_by_source,
            input_event_ids_by_source=input_event_ids_by_source,
            input_occurred_at_by_source=input_occurred_at_by_source,
            config_revision=config_revision,
            publish_completion_event=True,
            branch_port=branch_port,
            for_execution_ids={gate.id: for_execution_id},
        )
        return True


def _resolve_for_gate_execution_id(*, queue_record: Any, gate: Any, graph: Any) -> str:
    inbound_edge = _matching_inbound_edge(queue_record=queue_record, target_node_id=gate.id, graph=graph)
    if inbound_edge is not None and bool(getattr(inbound_edge, "allow_cycle", False)):
        from_payload = _for_execution_ids_from_payload(queue_record.payload).get(gate.id)
        if from_payload is not None:
            return from_payload
    return queue_record.event_id


def _matching_inbound_edge(*, queue_record: Any, target_node_id: str, graph: Any) -> Any | None:
    for edge in graph.inbound_index.get(target_node_id, ()):
        if str(getattr(edge, "source_id", "")) == queue_record.trigger_id:
            return edge
    return None


def _for_execution_ids_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    raw_value = payload.get("__for_execution_ids")
    if not isinstance(raw_value, dict):
        return {}
    normalized: dict[str, str] = {}
    for gate_id, execution_id in raw_value.items():
        gate_text = str(gate_id).strip() if isinstance(gate_id, str) else ""
        execution_text = str(execution_id).strip() if execution_id is not None else ""
        if gate_text and execution_text:
            normalized[gate_text] = execution_text
    return normalized


def _for_gate_state_key(*, gate_id: str, trace_id: str, for_execution_id: str) -> str:
    return f"{gate_id}:{trace_id}:{for_execution_id}"


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


def _migrate_legacy_store(*, legacy_path: Path, provider_owned_path: Path) -> None:
    if not legacy_path.exists() or provider_owned_path.exists():
        return
    provider_owned_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.replace(provider_owned_path)
