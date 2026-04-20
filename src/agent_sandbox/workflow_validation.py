from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from .workflow_graph import build_inbound_edge_index, would_form_cycle


@dataclass(frozen=True)
class NodeCapability:
    min_inbound: int | None = None
    max_inbound: int | None = None
    non_cyclic_inbound_exact: int | None = None
    allowed_source_ports: frozenset[str] | None = None
    required_source_ports: frozenset[str] = field(default_factory=frozenset)
    completion_semantics: str | None = None


class WorkflowValidationDiagnostic(BaseModel):
    code: str
    message: str
    node_id: str | None = None
    node_type: str | None = None


def validate_workflow_graph(
    *,
    nodes: Iterable[Any],
    edges: Iterable[Any],
    trigger_node_ids: Iterable[str],
    node_capabilities: Mapping[str, NodeCapability] | None = None,
) -> list[str]:
    """Validate workflow graph structure and optional node capability rules."""
    nodes_by_id = {str(node.id): node for node in nodes}
    edges_list = list(edges)
    trigger_ids = {str(node_id) for node_id in trigger_node_ids}
    inbound_edges = build_inbound_edge_index(edges_list)
    downstream_non_cyclic: dict[str, list[str]] = {}
    outbound_edges_by_source: dict[str, list[Any]] = {}

    errors: list[str] = []
    for edge in edges_list:
        outbound_edges_by_source.setdefault(edge.source_id, []).append(edge)
        if bool(getattr(edge, "allow_cycle", False)):
            continue
        if would_form_cycle(edge.source_id, edge.target_id, downstream_non_cyclic):
            errors.append(f"Non-cyclic edges cannot form a cycle: {edge.source_id} -> {edge.target_id}")
            continue
        downstream_non_cyclic.setdefault(edge.source_id, []).append(edge.target_id)

    missing_endpoints: set[str] = set()
    self_edges: set[str] = set()
    for edge in edges_list:
        source_known = edge.source_id in nodes_by_id
        target_known = edge.target_id in nodes_by_id
        if edge.source_id == edge.target_id:
            self_edges.add(edge.source_id)
        if not source_known:
            missing_endpoints.add(edge.source_id)
        if not target_known:
            missing_endpoints.add(edge.target_id)

    if missing_endpoints:
        missing_list = ", ".join(sorted(missing_endpoints))
        errors.append(f"Edge endpoints must reference existing node IDs: {missing_list}")

    if self_edges:
        self_edge_list = ", ".join(sorted(self_edges))
        errors.append(f"Edges cannot connect a node to itself: {self_edge_list}")

    trigger_inbound_errors: list[str] = []
    for trigger_node_id in sorted(trigger_ids):
        inbound_count = len(inbound_edges.get(trigger_node_id, ()))
        if inbound_count != 0:
            trigger_inbound_errors.append(f"trigger {trigger_node_id} must have 0 inbound edges, found {inbound_count}")
    errors.extend(trigger_inbound_errors)

    if not node_capabilities:
        return errors

    reachable_node_ids = set(trigger_ids)
    stack = list(trigger_ids)
    while stack:
        current = stack.pop()
        for edge in outbound_edges_by_source.get(current, ()):
            if edge.target_id in reachable_node_ids:
                continue
            reachable_node_ids.add(edge.target_id)
            stack.append(edge.target_id)

    capability_errors: list[str] = []
    for node in nodes_by_id.values():
        if node.id in trigger_ids or node.id not in reachable_node_ids:
            continue
        capability = node_capabilities.get(node.type)
        if capability is None:
            continue
        inbound = list(inbound_edges.get(node.id, ()))
        inbound_count = len(inbound)
        if capability.min_inbound is not None and inbound_count < capability.min_inbound:
            capability_errors.append(
                f"{node.type} {node.id} must have at least {capability.min_inbound} inbound edge(s), found {inbound_count}"
            )
        if capability.max_inbound is not None and inbound_count > capability.max_inbound:
            capability_errors.append(
                f"{node.type} {node.id} must have at most {capability.max_inbound} inbound edge(s), found {inbound_count}"
            )
        if capability.non_cyclic_inbound_exact is not None:
            non_cyclic_count = sum(1 for edge in inbound if not bool(getattr(edge, "allow_cycle", False)))
            if non_cyclic_count != capability.non_cyclic_inbound_exact:
                capability_errors.append(
                    f"{node.type} {node.id} must have exactly {capability.non_cyclic_inbound_exact} non-cyclic inbound edge(s), found {non_cyclic_count}"
                )
        if capability.allowed_source_ports is not None:
            outbound_ports = [edge.source_port for edge in outbound_edges_by_source.get(node.id, ())]
            invalid_ports = [port for port in outbound_ports if port not in capability.allowed_source_ports]
            if invalid_ports:
                capability_errors.append(
                    f"{node.type} {node.id} uses invalid source_port value(s): {', '.join(sorted({str(port) for port in invalid_ports}))}"
                )
        for required_port in sorted(capability.required_source_ports):
            if not any(edge.source_port == required_port for edge in outbound_edges_by_source.get(node.id, ())):
                capability_errors.append(f"{node.type} {node.id} must have an outbound edge with source_port={required_port}")
    errors.extend(capability_errors)
    return errors
