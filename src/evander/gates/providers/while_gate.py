from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from evander.gates.providers._state import provider_state_path as _provider_state_path
from evander.gate_runtime import GateRuntimeError, filter_when_matches
from evander.gates.providers._loop_graph import (
    _node_id as _node_id,
    _edge_source_id as _edge_source_id,
    _edge_target_id as _edge_target_id,
    _edge_allows_cycle as _edge_allows_cycle,
    _edge_source_port as _edge_source_port,
    _has_reachable_loopback as _has_reachable_loopback,
)
from evander.gates.providers.base import GateProviderBase
from evander.storage import JsonStore
from evander.workflow.binding import (
    binding_trace_to_payload,
    resolve_workflow_bindings,
)
from evander.workflow.lifecycle_ports import AGENT_PROMPT_ERROR_PORT
from evander.workflow.validation import NodeCapability, WorkflowValidationDiagnostic


class WhileGateStateRecord(BaseModel):
    key: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    while_execution_id: str = Field(min_length=1)
    next_iteration_index: int = Field(ge=0)
    max_iterations: int = Field(ge=1)
    updated_at: datetime


class _WhileGateStateStoreAdapter:
    def __init__(self, path: Path) -> None:
        self.store = JsonStore(path, WhileGateStateRecord)

    def get(self, key: str) -> WhileGateStateRecord | None:
        return self.store.get(key)

    def get_migrating_legacy_key(self, *, key: str, legacy_key: str) -> WhileGateStateRecord | None:
        if legacy_key == key:
            return self.get(key)

        snapshot = self.store.read_all()
        has_current = key in snapshot
        has_legacy = legacy_key in snapshot
        if has_current and not has_legacy:
            return snapshot[key]
        if not has_current and not has_legacy:
            return None

        def updater(items: dict[str, WhileGateStateRecord]) -> WhileGateStateRecord | None:
            current = items.get(key)
            if current is not None:
                items.pop(legacy_key, None)
                return current
            legacy = items.pop(legacy_key, None)
            if legacy is None:
                return None
            migrated = legacy.model_copy(update={"key": key})
            items[key] = migrated
            return migrated

        return self.store.update_all(updater)

    def put(self, key: str, record: WhileGateStateRecord) -> None:
        self.store.put(key, record)

    def delete(self, key: str) -> None:
        self.store.delete(key)


class WhileGateProvider(GateProviderBase):
    capability = NodeCapability(
        min_inbound=1,
        non_cyclic_inbound_exact=1,
        allowed_source_ports=frozenset(
            {"body", "done", "exhausted", AGENT_PROMPT_ERROR_PORT}
        ),
        completion_semantics="loop",
    )

    def __init__(self) -> None:
        super().__init__()
        self._adapter: _WhileGateStateStoreAdapter | None = None

    def load_state(self, **kwargs: Any) -> _WhileGateStateStoreAdapter:
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
            namespace=("gates", "while", "state_store_path"),
            default=runtime_dir / "providers" / "gates" / "while" / "state.json",
        )
        self._adapter = _WhileGateStateStoreAdapter(state_path)
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
                    f"gate:while {gate_id} body branch must have an allow_cycle path back "
                    f"to {gate_id}"
                ),
                node_id=gate_id,
                node_type="gate:while",
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
            {"when": gate.when, "max_iterations": gate.max_iterations},
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
            resolved.resolved_config, gate_type="gate:while"
        )
        resolved_when = resolved_config.get("when")
        max_iterations = _coerce_max_iterations(resolved_config.get("max_iterations"))
        while_execution_id = _resolve_while_gate_execution_id(
            queue_record=queue_record, gate=gate, graph=graph
        )
        resolved_config = {
            "when": resolved_when,
            "max_iterations": max_iterations,
            "while_execution_id": while_execution_id,
        }
        state_key = _while_gate_state_key(
            gate_id=gate.id,
            trace_id=queue_record.trace_id,
            while_execution_id=while_execution_id,
        )
        legacy_state_key = _legacy_while_gate_state_key(
            gate_id=gate.id,
            trace_id=queue_record.trace_id,
            while_execution_id=while_execution_id,
        )
        adapter = self.load_state()
        state = adapter.get_migrating_legacy_key(key=state_key, legacy_key=legacy_state_key)
        if state is None:
            state = WhileGateStateRecord(
                key=state_key,
                gate_id=gate.id,
                trace_id=queue_record.trace_id,
                while_execution_id=while_execution_id,
                next_iteration_index=0,
                max_iterations=max_iterations,
                updated_at=datetime.now(UTC),
            )
        elif state.max_iterations != max_iterations:
            state.max_iterations = max_iterations

        outbound_ports = _while_gate_outbound_ports(graph=graph, gate_id=gate.id)
        condition_matches = filter_when_matches(resolved_when)
        if not condition_matches:
            outputs = _terminal_outputs(
                iteration_count=state.next_iteration_index,
                max_iterations=max_iterations,
                is_exhausted=False,
            )
            branch_port = "done"
            publish_completion_event = branch_port in outbound_ports
            adapter.delete(state_key)
        elif state.next_iteration_index >= max_iterations:
            outputs = _terminal_outputs(
                iteration_count=state.next_iteration_index,
                max_iterations=max_iterations,
                is_exhausted=True,
            )
            branch_port = "exhausted"
            publish_completion_event = branch_port in outbound_ports
            adapter.delete(state_key)
        else:
            iteration_index = state.next_iteration_index
            iteration_count = iteration_index + 1
            outputs = {
                "iteration_index": iteration_index,
                "iteration_count": iteration_count,
                "remaining_iterations": max(0, max_iterations - iteration_count),
                "is_done": False,
                "is_exhausted": False,
            }
            branch_port = "body"
            publish_completion_event = branch_port in outbound_ports
            if publish_completion_event:
                state.next_iteration_index = iteration_count
                state.updated_at = datetime.now(UTC)
                adapter.put(state_key, state)
            else:
                adapter.delete(state_key)

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
            publish_completion_event=publish_completion_event,
            branch_port=branch_port if publish_completion_event else None,
            # The orchestrator's loop id propagation is historically named for gate:for.
            for_execution_ids={gate.id: while_execution_id},
        )
        return True


def _terminal_outputs(
    *, iteration_count: int, max_iterations: int, is_exhausted: bool
) -> dict[str, Any]:
    return {
        "iteration_index": iteration_count,
        "iteration_count": iteration_count,
        "remaining_iterations": max(0, max_iterations - iteration_count),
        "is_done": True,
        "is_exhausted": is_exhausted,
    }


def _coerce_max_iterations(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GateRuntimeError("gate:while max_iterations must resolve to a positive integer")
    if value < 1:
        raise GateRuntimeError("gate:while max_iterations must resolve to a positive integer")
    return value


def _while_gate_outbound_ports(*, graph: Any, gate_id: str) -> set[str]:
    outbound_index = getattr(graph, "outbound_index", {})
    if not isinstance(outbound_index, dict):
        return set()
    ports: set[str] = set()
    for edge in outbound_index.get(gate_id, ()):
        port = _edge_source_port(edge)
        if port in {"body", "done", "exhausted"}:
            ports.add(port)
    return ports


def _resolve_while_gate_execution_id(*, queue_record: Any, gate: Any, graph: Any) -> str:
    inbound_edge = _matching_inbound_edge(
        queue_record=queue_record, target_node_id=gate.id, graph=graph
    )
    if inbound_edge is not None and bool(getattr(inbound_edge, "allow_cycle", False)):
        from_payload = _loop_execution_ids_from_payload(queue_record.payload).get(gate.id)
        if from_payload is not None:
            return from_payload
    return queue_record.event_id


def _matching_inbound_edge(*, queue_record: Any, target_node_id: str, graph: Any) -> Any | None:
    for edge in graph.inbound_index.get(target_node_id, ()):
        if str(getattr(edge, "source_id", "")) == queue_record.trigger_id:
            return edge
    return None


def _loop_execution_ids_from_payload(payload: dict[str, Any]) -> dict[str, str]:
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


def _while_gate_state_key(*, gate_id: str, trace_id: str, while_execution_id: str) -> str:
    del trace_id
    return f"{gate_id}:{while_execution_id}"


def _legacy_while_gate_state_key(*, gate_id: str, trace_id: str, while_execution_id: str) -> str:
    return f"{gate_id}:{trace_id}:{while_execution_id}"
