from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_sandbox.gate_runtime import GateRuntimeError
from agent_sandbox.gates.providers.base import GateProviderBase
from agent_sandbox.storage import JsonStore
from agent_sandbox.workflow.binding import (
    binding_trace_to_payload,
    resolve_workflow_bindings,
)
from agent_sandbox.workflow.validation import NodeCapability, WorkflowValidationDiagnostic


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
        completion_semantics="loop",
    )

    def __init__(self) -> None:
        super().__init__()
        self._adapter: _ForGateStateStoreAdapter | None = None

    def load_state(self, **kwargs: Any) -> _ForGateStateStoreAdapter:
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

    @classmethod
    def validate_graph_node(
        cls,
        *,
        node: Any,
        edges: list[Any] | None = None,
        **_: Any,
    ) -> list[WorkflowValidationDiagnostic]:
        gate_id = _node_id(node)
        if not gate_id or not edges:
            return []
        body_targets = [
            target_id
            for edge in edges
            if _edge_source_id(edge) == gate_id and _edge_source_port(edge) == "body"
            for target_id in (_edge_target_id(edge),)
            if target_id
        ]
        if not body_targets or _has_reachable_loopback(
            edges=edges, gate_id=gate_id, start_node_ids=body_targets
        ):
            return []
        return [
            WorkflowValidationDiagnostic(
                code="node_capability",
                message=(
                    f"gate:for {gate_id} body branch must have an allow_cycle path back "
                    f"to {gate_id}"
                ),
                node_id=gate_id,
                node_type="gate:for",
            )
        ]

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
        resolved_config = worker._require_object_payload(
            resolved.resolved_config, gate_type="gate:for"
        )
        items = resolved_config.get("items")
        if not isinstance(items, list):
            raise GateRuntimeError("gate:for items must resolve to an array")

        outbound_ports = _for_gate_outbound_ports(graph=graph, gate_id=gate.id)
        has_body_edge = "body" in outbound_ports
        has_done_edge = "done" in outbound_ports
        for_execution_id = _resolve_for_gate_execution_id(
            queue_record=queue_record, gate=gate, graph=graph
        )
        state_key = _for_gate_state_key(
            gate_id=gate.id, trace_id=queue_record.trace_id, for_execution_id=for_execution_id
        )
        adapter = self.load_state()

        if not has_body_edge:
            outputs = _terminal_outputs(item_count=len(items))
            adapter.delete(state_key)
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
                binding_trace=binding_trace_to_payload(resolved.trace),
                publish_completion_event=has_done_edge,
                branch_port="done" if has_done_edge else None,
                for_execution_ids={gate.id: for_execution_id},
            )
            return True

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
            outputs = _terminal_outputs(item_count=state.item_count)
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
            binding_trace=binding_trace_to_payload(resolved.trace),
            publish_completion_event=not is_done or has_done_edge,
            branch_port=branch_port if not is_done or has_done_edge else None,
            for_execution_ids={gate.id: for_execution_id},
        )
        return True


def _terminal_outputs(*, item_count: int) -> dict[str, Any]:
    return {
        "current_index": item_count,
        "current_element": None,
        "item_count": item_count,
        "is_done": True,
    }


def _for_gate_outbound_ports(*, graph: Any, gate_id: str) -> set[str]:
    outbound_index = getattr(graph, "outbound_index", {})
    if not isinstance(outbound_index, dict):
        return set()
    ports: set[str] = set()
    for edge in outbound_index.get(gate_id, ()):
        port = _edge_source_port(edge)
        if port in {"body", "done"}:
            ports.add(port)
    return ports


def _node_id(node: Any) -> str:
    if isinstance(node, dict):
        value = node.get("id")
    else:
        value = getattr(node, "id", None)
    if value is None:
        return ""
    return str(value).strip()


def _edge_source_id(edge: Any) -> str:
    if isinstance(edge, dict):
        value = edge.get("source_id")
    else:
        value = getattr(edge, "source_id", None)
    if value is None:
        return ""
    return str(value).strip()


def _edge_target_id(edge: Any) -> str:
    if isinstance(edge, dict):
        value = edge.get("target_id")
    else:
        value = getattr(edge, "target_id", None)
    if value is None:
        return ""
    return str(value).strip()


def _edge_allows_cycle(edge: Any) -> bool:
    if isinstance(edge, dict):
        return bool(edge.get("allow_cycle", False))
    return bool(getattr(edge, "allow_cycle", False))


def _edge_source_port(edge: Any) -> str | None:
    if isinstance(edge, dict):
        value = edge.get("source_port")
    else:
        value = getattr(edge, "source_port", None)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _has_reachable_loopback(
    *, edges: list[Any], gate_id: str, start_node_ids: list[str]
) -> bool:
    outbound_edges_by_source: dict[str, list[Any]] = {}
    for edge in edges:
        source_id = _edge_source_id(edge)
        target_id = _edge_target_id(edge)
        if source_id and target_id:
            outbound_edges_by_source.setdefault(source_id, []).append(edge)

    queue = list(start_node_ids)
    visited: set[str] = set()
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        for edge in outbound_edges_by_source.get(current, ()):
            target_id = _edge_target_id(edge)
            if target_id == gate_id and _edge_allows_cycle(edge):
                return True
            if target_id and target_id != gate_id and target_id not in visited:
                queue.append(target_id)
    return False


def _resolve_for_gate_execution_id(*, queue_record: Any, gate: Any, graph: Any) -> str:
    inbound_edge = _matching_inbound_edge(
        queue_record=queue_record, target_node_id=gate.id, graph=graph
    )
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
