"""Apply declarative, namespaced node/edge patches to operator workflow documents."""

from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

from evander.workflow.graph import compute_downstream_depth


def apply_workflow_patch(
    config: dict[str, Any], graphs: list[dict[str, Any]], patch: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    config, graphs = deepcopy(config), deepcopy(graphs)
    if patch.get("version") != 1:
        raise ValueError("Unsupported workflow patch version")
    prefix = patch.get("node_prefix")
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError("A nonempty node_prefix is required")
    flat = "workflow_ir" not in config
    ir = config.get("workflow_ir") or {
        "nodes": [
            node
            for collection in ("triggers", "gates", "effects", "queries")
            for node in config.get(collection, [])
        ],
        "edges": config.get("edges", []),
    }

    def body(node):
        return node.get("node", node)

    existing = {body(node)["id"]: body(node) for node in ir["nodes"]}
    for node_id in patch["required_node_ids"]:
        if node_id not in existing:
            raise ValueError(f"Required workflow node is missing: {node_id}")
    matching_graphs = [graph for graph in graphs if patch["graph_anchor"] in graph["node_ids"]]
    if len(matching_graphs) != 1:
        raise ValueError("graph_anchor must identify exactly one workflow graph")
    graph = matching_graphs[0]
    new_nodes = deepcopy(patch["nodes"])
    new_ids = [node["id"] for node in new_nodes]
    if (
        not new_ids
        or len(set(new_ids)) != len(new_ids)
        or any(not node_id.startswith(prefix) for node_id in new_ids)
    ):
        raise ValueError("Patch nodes must have unique IDs within node_prefix")
    replaced_ids = {node_id for node_id in existing if node_id.startswith(prefix)}
    if replaced_ids - set(graph["node_ids"]) or any(
        replaced_ids.intersection(other["node_ids"]) for other in graphs if other is not graph
    ):
        raise ValueError("node_prefix also owns nodes in another graph")
    expected_edges = patch.get("remove_edges", [])
    allowed_ids = set(graph["node_ids"]) - replaced_ids | set(new_ids)
    for edge in patch["edges"]:
        endpoints = {edge["source_id"], edge["target_id"]}
        if not endpoints.intersection(new_ids) or not endpoints.issubset(allowed_ids):
            raise ValueError(
                "Patch edges must touch a patch node and stay within the selected graph"
            )
    for edge in expected_edges:
        if not {edge["source_id"], edge["target_id"]}.issubset(graph["node_ids"]):
            raise ValueError("Removed edges must stay within the selected graph")

    def matches(left, right):
        return all(
            left.get(field) == right.get(field)
            for field in ("source_id", "target_id", "source_port")
        )

    for edge in expected_edges:
        count = sum(matches(item, edge) for item in ir["edges"])
        if count != 1 and not (count == 0 and replaced_ids):
            raise ValueError("Expected workflow routing changed; inspect before applying the patch")
    wrapped = any("node" in node for node in ir["nodes"])
    ir["nodes"] = [node for node in ir["nodes"] if body(node)["id"] not in replaced_ids]
    ir["nodes"].extend({"node": node} if wrapped else node for node in new_nodes)
    ir["edges"] = [
        edge
        for edge in ir["edges"]
        if edge["source_id"] not in replaced_ids
        and edge["target_id"] not in replaced_ids
        and not any(matches(edge, expected) for expected in expected_edges)
    ]
    ir["edges"].extend(deepcopy(patch["edges"]))
    graph["node_ids"] = [
        node_id for node_id in graph["node_ids"] if node_id not in replaced_ids
    ] + new_ids
    by_id = {body(node)["id"]: body(node) for node in ir["nodes"]}
    for update in patch.get("append_config_lists", []):
        node = by_id.get(update["node_id"])
        if node is None and update.get("optional", False):
            continue
        if node is None:
            raise ValueError(f"List update node is missing: {update['node_id']}")
        values = node["config"].get(update["field"], [])
        if not isinstance(values, list) or not isinstance(update["values"], list):
            raise ValueError(
                "List updates require static arrays; update a dynamic producer instead"
            )
        node["config"][update["field"]] = values + [
            value for value in update["values"] if value not in values
        ]
    downstream: dict[str, list[str]] = {}
    for edge in ir["edges"]:
        if not edge.get("allow_cycle"):
            downstream.setdefault(edge["source_id"], []).append(edge["target_id"])
    required_depth = compute_downstream_depth(patch["graph_anchor"], downstream)
    if int(config.get("max_graph_depth", 16)) < required_depth:
        raise ValueError(
            f"Worker max_graph_depth must be at least {required_depth}; update it through Admin first"
        )
    if flat:
        for kind, collection in (
            ("trigger:", "triggers"),
            ("gate:", "gates"),
            ("effect:", "effects"),
            ("query:", "queries"),
        ):
            config[collection] = [
                body(node) for node in ir["nodes"] if body(node)["type"].startswith(kind)
            ]
        config["edges"] = ir["edges"]
    else:
        config["workflow_ir"] = ir
    return config, graphs


def apply_workflow_patch_layout(layout: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    next_layout = deepcopy(layout)
    patch_layout = patch.get("layout")
    if patch_layout is None:
        return next_layout
    if not isinstance(patch_layout, dict) or not isinstance(patch_layout.get("nodes"), dict):
        raise ValueError("Patch layout must contain a nodes object")

    prefix = patch.get("node_prefix")
    patch_node_ids = {node["id"] for node in patch.get("nodes", [])}
    positions = patch_layout["nodes"]
    if set(positions) != patch_node_ids:
        raise ValueError("Patch layout must position every patch node exactly once")

    normalized: dict[str, dict[str, float]] = {}
    for node_id, position in positions.items():
        if not isinstance(node_id, str) or not node_id.startswith(prefix):
            raise ValueError("Patch layout may only position nodes in node_prefix")
        if not isinstance(position, dict):
            raise ValueError("Patch layout positions must be objects")
        x, y = position.get("x"), position.get("y")
        if (
            isinstance(x, bool)
            or isinstance(y, bool)
            or not isinstance(x, (int, float))
            or not isinstance(y, (int, float))
            or not math.isfinite(x)
            or not math.isfinite(y)
        ):
            raise ValueError("Patch layout coordinates must be finite numbers")
        normalized[node_id] = {"x": float(x), "y": float(y)}

    current_nodes = next_layout.get("nodes", {})
    if not isinstance(current_nodes, dict):
        raise ValueError("Workflow layout nodes must be an object")
    next_layout["nodes"] = {
        node_id: position
        for node_id, position in current_nodes.items()
        if not node_id.startswith(prefix)
    }
    next_layout["nodes"].update(normalized)
    return next_layout
