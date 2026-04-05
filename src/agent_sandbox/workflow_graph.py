from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from typing import Any


class WorkflowGraphError(ValueError):
    """Raised when workflow graph helpers encounter invalid input."""


class WorkflowGraphCycleError(WorkflowGraphError):
    """Raised when a downstream depth walk encounters a cycle."""


_NODE_ID_KEYS = ("id", "node_id")
_NODE_OUTPUTS_KEYS = ("outputs",)
_EDGE_SOURCE_KEYS = ("source_id",)
_EDGE_TARGET_KEYS = ("target_id",)


def build_downstream_index(edges: Iterable[Any]) -> dict[str, tuple[str, ...]]:
    """Return source -> ordered downstream node IDs from a flat edge collection."""

    downstream: dict[str, set[str]] = defaultdict(set)
    for edge in edges:
        source_id = _edge_endpoint(edge, _EDGE_SOURCE_KEYS)
        target_id = _edge_endpoint(edge, _EDGE_TARGET_KEYS)
        downstream[source_id].add(target_id)
    return {source_id: tuple(sorted(target_ids)) for source_id, target_ids in sorted(downstream.items())}


def build_inbound_edge_index(edges: Iterable[Any]) -> dict[str, tuple[Any, ...]]:
    """Return target -> ordered inbound edges from a flat edge collection."""

    inbound: dict[str, list[Any]] = defaultdict(list)
    for edge in edges:
        target_id = _edge_endpoint(edge, _EDGE_TARGET_KEYS)
        inbound[target_id].append(edge)
    return {
        target_id: tuple(sorted(edge_list, key=_edge_sort_key))
        for target_id, edge_list in sorted(inbound.items())
    }


def index_declared_outputs(*node_groups: Iterable[Any] | Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Return node ID -> declared output schema mapping for triggers, gates, and effects."""

    indexed: dict[str, dict[str, Any]] = {}
    for node in _iter_nodes(*node_groups):
        node_id = _node_id(node)
        indexed[node_id] = dict(_node_outputs(node))
    return indexed


def merge_declared_output_intersection(*node_groups: Iterable[Any] | Mapping[str, Any]) -> dict[str, Any]:
    """Return output schemas declared by every node in the supplied workflow graph slice."""

    if node_groups and all(isinstance(group, Mapping) and _looks_like_output_schema_map(group) for group in node_groups):
        return merge_output_schema_intersection(*node_groups)

    declared_outputs = [dict(_node_outputs(node)) for node in _iter_nodes(*node_groups)]
    if not declared_outputs:
        return {}

    shared_names = set(declared_outputs[0].keys())
    for outputs in declared_outputs[1:]:
        shared_names &= set(outputs.keys())

    merged: dict[str, Any] = {}
    for output_name in declared_outputs[0]:
        if output_name not in shared_names:
            continue
        schema = declared_outputs[0][output_name]
        if all(outputs[output_name] == schema for outputs in declared_outputs[1:]):
            merged[output_name] = schema
    return merged


def merge_output_schema_intersection(*declared_output_maps: Mapping[str, Any]) -> dict[str, Any]:
    """Return the shared output schema entries across raw declared output mappings."""

    normalized = [dict(output_map) for output_map in declared_output_maps]
    if not normalized:
        return {}

    shared_names = set(normalized[0].keys())
    for outputs in normalized[1:]:
        shared_names &= set(outputs.keys())

    merged: dict[str, Any] = {}
    for output_name in normalized[0]:
        if output_name not in shared_names:
            continue
        schema = normalized[0][output_name]
        if all(outputs[output_name] == schema for outputs in normalized[1:]):
            merged[output_name] = schema
    return merged


def reachable_downstream_nodes(node_id: str, downstream_index: Mapping[str, Iterable[str]]) -> tuple[str, ...]:
    """Return all nodes reachable by following downstream edges from `node_id`."""

    reachable: set[str] = set()
    queue: deque[str] = deque(downstream_index.get(node_id, ()))
    while queue:
        current = queue.popleft()
        if current in reachable:
            continue
        reachable.add(current)
        for child in downstream_index.get(current, ()):
            if child not in reachable:
                queue.append(child)
    return tuple(sorted(reachable))


def would_form_cycle(source_node_id: str, target_node_id: str, downstream_index: Mapping[str, Iterable[str]]) -> bool:
    """Return True when adding `source_node_id -> target_node_id` would close a cycle."""

    if source_node_id == target_node_id:
        return True
    return source_node_id in reachable_downstream_nodes(target_node_id, downstream_index)


def compute_downstream_depth(
    node_id: str,
    downstream_index: Mapping[str, Iterable[str]],
    *,
    _path: tuple[str, ...] | None = None,
) -> int:
    """Return the maximum downstream depth rooted at `node_id`.

    The depth is inclusive: a leaf node has depth 1.
    """

    path = tuple(_path or ())
    if node_id in path:
        raise WorkflowGraphCycleError(f"workflow graph contains cycle at {node_id!r}")
    children = tuple(downstream_index.get(node_id, ()))
    if not children:
        return 1
    next_path = path + (node_id,)
    return 1 + max(compute_downstream_depth(child, downstream_index, _path=next_path) for child in children)


index_downstream_nodes = build_downstream_index
index_inbound_edges = build_inbound_edge_index
merge_output_intersection = merge_output_schema_intersection
compute_depth = compute_downstream_depth


def _iter_nodes(*node_groups: Iterable[Any] | Mapping[str, Any]) -> Iterable[Any]:
    for node_group in node_groups:
        if node_group is None:
            continue
        if isinstance(node_group, Mapping):
            yield from node_group.values()
            continue
        yield from node_group


def _node_id(node: Any) -> str:
    if isinstance(node, Mapping):
        for key in _NODE_ID_KEYS:
            value = node.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
    for key in _NODE_ID_KEYS:
        value = getattr(node, key, None)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    raise WorkflowGraphError(f"workflow node is missing an ID: {node!r}")


def _node_outputs(node: Any) -> Mapping[str, Any]:
    if isinstance(node, Mapping):
        for key in _NODE_OUTPUTS_KEYS:
            value = node.get(key)
            if value is None:
                return {}
            if isinstance(value, Mapping):
                return value
            raise WorkflowGraphError(f"workflow node outputs must be a mapping: {node!r}")
        return {}
    for key in _NODE_OUTPUTS_KEYS:
        value = getattr(node, key, None)
        if value is None:
            return {}
        if isinstance(value, Mapping):
            return value
        raise WorkflowGraphError(f"workflow node outputs must be a mapping: {node!r}")
    return {}


def _edge_endpoint(edge: Any, keys: tuple[str, ...]) -> str:
    if isinstance(edge, tuple) and len(edge) == 2:
        source, target = edge
        if keys == _EDGE_SOURCE_KEYS:
            return str(source).strip()
        return str(target).strip()
    if isinstance(edge, list) and len(edge) == 2:
        source, target = edge
        if keys == _EDGE_SOURCE_KEYS:
            return str(source).strip()
        return str(target).strip()

    if isinstance(edge, Mapping):
        for key in keys:
            value = edge.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
    for key in keys:
        value = getattr(edge, key, None)
        if value is not None:
            text = str(value).strip()
            if text:
                return text
    raise WorkflowGraphError(f"workflow edge is missing an endpoint: {edge!r}")


def _edge_sort_key(edge: Any) -> tuple[str, str, str]:
    try:
        return (
            _edge_endpoint(edge, _EDGE_SOURCE_KEYS),
            _edge_endpoint(edge, _EDGE_TARGET_KEYS),
            repr(edge),
        )
    except WorkflowGraphError:
        return ("", "", repr(edge))


def _looks_like_output_schema_map(value: Mapping[str, Any]) -> bool:
    if not value:
        return True
    if not all(isinstance(item, Mapping) for item in value.values()):
        return False
    return not any(
        {"id", "node_id", "outputs"} & set(item.keys())
        for item in value.values()
    )


__all__ = [
    "WorkflowGraphError",
    "WorkflowGraphCycleError",
    "build_downstream_index",
    "index_downstream_nodes",
    "build_inbound_edge_index",
    "index_inbound_edges",
    "index_declared_outputs",
    "merge_declared_output_intersection",
    "merge_output_schema_intersection",
    "merge_output_intersection",
    "reachable_downstream_nodes",
    "would_form_cycle",
    "compute_downstream_depth",
    "compute_depth",
]
