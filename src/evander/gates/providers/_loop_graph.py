"""Graph access shared by the for and while workflow gates."""

from __future__ import annotations

from typing import Any


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


def _has_reachable_loopback(*, edges: list[Any], gate_id: str, start_node_ids: list[str]) -> bool:
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
