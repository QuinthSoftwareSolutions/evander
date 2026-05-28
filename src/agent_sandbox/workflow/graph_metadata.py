from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from collections.abc import Iterable, Mapping
from typing import Any

from agent_sandbox.workflow.packs import WorkflowPackGraph

STATE_NODE_TYPES = frozenset({"effect:state:update", "query:state:get"})


@dataclass(frozen=True)
class ResolvedWorkflowGraphMetadata:
    graph_id: str
    directory_path: str


def resolve_workflow_graph_metadata(
    *,
    nodes_by_id: Mapping[str, Any],
    edges: Iterable[Any],
    workflow_graphs: Iterable[Any] | None,
) -> dict[str, ResolvedWorkflowGraphMetadata]:
    """Resolve persisted graph metadata for runtime nodes.

    Exact graph metadata is preserved for any connected component, but only state-node
    components are required to have persisted metadata.
    """

    normalized_nodes = _normalize_node_map(nodes_by_id)
    components = _connected_components(normalized_nodes, edges)
    component_by_node_id = {
        node_id: component for component in components for node_id in component
    }

    exact_metadata_by_component: dict[frozenset[str], ResolvedWorkflowGraphMetadata] = {}
    normalized_graphs = [_normalize_workflow_graph(graph) for graph in workflow_graphs or []]
    _validate_unique_graph_ids(normalized_graphs)
    for graph in normalized_graphs:
        graph_node_ids = _normalized_node_id_set(graph.node_ids)
        if not graph_node_ids:
            continue
        component = component_by_node_id.get(next(iter(graph_node_ids)), None)
        if component is None:
            continue
        component_key = frozenset(component)
        if graph_node_ids != component_key:
            continue
        exact_metadata_by_component[component_key] = ResolvedWorkflowGraphMetadata(
            graph_id=graph.id,
            directory_path=_first_directory_path(graph),
        )

    node_graph_metadata_by_id: dict[str, ResolvedWorkflowGraphMetadata] = {}
    for component in components:
        component_key = frozenset(component)
        state_node_ids = [
            node_id
            for node_id in component
            if _node_type(normalized_nodes[node_id]) in STATE_NODE_TYPES
        ]
        if not state_node_ids:
            continue

        overlapping_graphs = _graphs_overlapping_component(normalized_graphs, component_key)
        invalid_overlaps = [
            graph
            for graph in overlapping_graphs
            if _normalized_node_id_set(graph.node_ids) != component_key
        ]
        state_node_id = state_node_ids[0]
        if invalid_overlaps:
            invalid_graph = invalid_overlaps[0]
            graph_node_ids = _normalized_node_id_set(invalid_graph.node_ids)
            unknown_node_ids = sorted(
                node_id for node_id in graph_node_ids if node_id not in normalized_nodes
            )
            if unknown_node_ids:
                unknown_list = ", ".join(unknown_node_ids)
                raise ValueError(
                    f"Workflow graph {invalid_graph.id} references unknown node IDs: {unknown_list}; "
                    f"persisted workflow graph metadata is required for workflow node {state_node_id}."
                )
            raise ValueError(
                f"Workflow graph {invalid_graph.id} node_ids must exactly match the connected component "
                f"for workflow node {state_node_id}; persisted workflow graph metadata is required."
            )

        metadata = exact_metadata_by_component.get(component_key)
        if metadata is None:
            raise ValueError(
                "Workflow node "
                f"{state_node_id} requires persisted workflow graph metadata for its connected component."
            )

        for node_id in component:
            node_graph_metadata_by_id[node_id] = metadata

    for component, metadata in exact_metadata_by_component.items():
        for node_id in component:
            node_graph_metadata_by_id.setdefault(node_id, metadata)

    return node_graph_metadata_by_id


def _normalize_node_map(nodes_by_id: Mapping[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for raw_node_id, node in nodes_by_id.items():
        node_id = _normalize_text(raw_node_id)
        if not node_id:
            continue
        normalized[node_id] = node
    return normalized


def _connected_components(
    nodes_by_id: Mapping[str, Any], edges: Iterable[Any]
) -> list[tuple[str, ...]]:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes_by_id}
    for edge in edges:
        source_id = _edge_endpoint(edge, "source_id")
        target_id = _edge_endpoint(edge, "target_id")
        if source_id not in adjacency or target_id not in adjacency:
            raise ValueError(
                f"workflow edge references unknown node IDs: {source_id!r} -> {target_id!r}"
            )
        adjacency[source_id].add(target_id)
        adjacency[target_id].add(source_id)

    components: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for node_id in sorted(adjacency):
        if node_id in seen:
            continue
        component: list[str] = []
        queue: deque[str] = deque([node_id])
        while queue:
            current = queue.popleft()
            if current in seen:
                continue
            seen.add(current)
            component.append(current)
            for neighbor in sorted(adjacency[current]):
                if neighbor not in seen:
                    queue.append(neighbor)
        components.append(tuple(sorted(component)))
    return components


def _normalize_workflow_graph(graph: Any) -> WorkflowPackGraph:
    if isinstance(graph, WorkflowPackGraph):
        return graph
    return WorkflowPackGraph.model_validate(graph)


def _validate_unique_graph_ids(graphs: Iterable[WorkflowPackGraph]) -> None:
    seen_graph_ids: set[str] = set()
    duplicate_graph_ids: set[str] = set()
    for graph in graphs:
        graph_id = _normalize_text(graph.id)
        if not graph_id:
            raise ValueError("Workflow graph id must be a non-empty string.")
        if graph_id in seen_graph_ids:
            duplicate_graph_ids.add(graph_id)
        seen_graph_ids.add(graph_id)
    if duplicate_graph_ids:
        duplicate_list = ", ".join(sorted(duplicate_graph_ids))
        raise ValueError(f"Duplicate workflow graph id: {duplicate_list}")


def _first_directory_path(graph: WorkflowPackGraph) -> str:
    text = _normalize_text(graph.directory_path)
    return text or "root"


def _graphs_overlapping_component(
    graphs: list[WorkflowPackGraph], component: frozenset[str]
) -> list[WorkflowPackGraph]:
    component_set = set(component)
    overlapping: list[WorkflowPackGraph] = []
    for graph in graphs:
        graph_node_ids = _normalized_node_id_set(graph.node_ids)
        if graph_node_ids & component_set:
            overlapping.append(graph)
    return overlapping


def _normalized_node_id_set(node_ids: Iterable[Any]) -> frozenset[str]:
    return frozenset(
        node_id
        for node_id in (_normalize_text(node_id) for node_id in node_ids)
        if node_id
    )


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _edge_endpoint(edge: Any, key: str) -> str:
    if isinstance(edge, Mapping):
        return _normalize_text(edge.get(key))
    return _normalize_text(getattr(edge, key, None))


def _node_type(node: Any) -> str:
    if isinstance(node, Mapping):
        return _normalize_text(node.get("type"))
    return _normalize_text(getattr(node, "type", None))


__all__ = [
    "ResolvedWorkflowGraphMetadata",
    "STATE_NODE_TYPES",
    "resolve_workflow_graph_metadata",
]
