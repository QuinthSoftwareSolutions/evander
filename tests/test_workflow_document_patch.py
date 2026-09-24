from copy import deepcopy

import pytest

from evander.workflow.document_patch import apply_workflow_patch, apply_workflow_patch_layout


def documents(*, flat=False):
    trigger = {"id": "schedule", "type": "trigger:event", "config": {}}
    sink = {"id": "notify", "type": "effect:demo:echo", "config": {"recipients": ["existing"]}}
    original_edge = {"source_id": "schedule", "target_id": "notify"}
    config = {"max_graph_depth": 16}
    if flat:
        config.update(
            triggers=[trigger], effects=[sink], gates=[], queries=[], edges=[original_edge]
        )
    else:
        config["workflow_ir"] = {
            "nodes": [{"node": trigger}, {"node": sink}],
            "edges": [original_edge],
        }
    graphs = [{"id": "notifications", "node_ids": ["schedule", "notify"]}]
    patch = {
        "version": 1,
        "graph_anchor": "schedule",
        "required_node_ids": ["schedule", "notify"],
        "node_prefix": "digest-",
        "remove_edges": [original_edge],
        "nodes": [
            {"id": "digest-map", "type": "gate:map", "config": {"template": {"body": "Hello"}}}
        ],
        "edges": [
            {"source_id": "schedule", "target_id": "digest-map"},
            {"source_id": "digest-map", "target_id": "notify"},
        ],
        "append_config_lists": [
            {"node_id": "notify", "field": "recipients", "values": ["existing", "new"]}
        ],
    }
    return config, graphs, patch


@pytest.mark.parametrize("flat", [False, True])
def test_arbitrary_patch_is_idempotent_and_does_not_mutate_inputs(flat):
    config, graphs, patch = documents(flat=flat)
    original = deepcopy((config, graphs, patch))
    updated, next_graphs = apply_workflow_patch(config, graphs, patch)
    assert (config, graphs, patch) == original
    assert next_graphs[0]["node_ids"] == ["schedule", "notify", "digest-map"]
    nodes = (
        updated["effects"] if flat else [item["node"] for item in updated["workflow_ir"]["nodes"]]
    )
    sink = next(node for node in nodes if node["id"] == "notify")
    assert sink["config"]["recipients"] == ["existing", "new"]
    assert apply_workflow_patch(updated, next_graphs, patch) == (updated, next_graphs)


def test_patch_replacement_removes_old_namespaced_nodes_only():
    config, graphs, patch = documents()
    config, graphs = apply_workflow_patch(config, graphs, patch)
    patch["nodes"][0]["id"] = "digest-transform"
    patch["edges"] = [
        {"source_id": "schedule", "target_id": "digest-transform"},
        {"source_id": "digest-transform", "target_id": "notify"},
    ]
    updated, next_graphs = apply_workflow_patch(config, graphs, patch)
    assert next_graphs[0]["node_ids"] == ["schedule", "notify", "digest-transform"]
    assert updated["workflow_ir"]["edges"] == patch["edges"]


def test_prefix_cannot_replace_nodes_in_another_graph():
    config, graphs, patch = documents()
    config["workflow_ir"]["nodes"].append(
        {"node": {"id": "digest-other", "type": "gate:map", "config": {}}}
    )
    graphs.append({"id": "other", "node_ids": ["digest-other"]})
    with pytest.raises(ValueError, match="another graph"):
        apply_workflow_patch(config, graphs, patch)


@pytest.mark.parametrize(
    "edge",
    [
        {"source_id": "schedule", "target_id": "notify"},
        {"source_id": "digest-map", "target_id": "outside"},
    ],
)
def test_patch_cannot_add_unowned_or_cross_graph_edges(edge):
    config, graphs, patch = documents()
    patch["edges"].append(edge)
    with pytest.raises(ValueError, match="touch a patch node"):
        apply_workflow_patch(config, graphs, patch)


@pytest.mark.parametrize("optional", [False, True])
def test_optional_list_update_is_explicit(optional):
    config, graphs, patch = documents()
    patch["append_config_lists"][0].update(node_id="missing", optional=optional)
    if optional:
        apply_workflow_patch(config, graphs, patch)
    else:
        with pytest.raises(ValueError, match="List update node is missing"):
            apply_workflow_patch(config, graphs, patch)


def test_list_update_cannot_replace_a_dynamic_binding():
    config, graphs, patch = documents()
    config["workflow_ir"]["nodes"][1]["node"]["config"]["recipients"] = "${{schedule.recipients}}"
    with pytest.raises(ValueError, match="static arrays"):
        apply_workflow_patch(config, graphs, patch)


def test_missing_prerequisite_refuses_to_apply():
    config, graphs, patch = documents()
    patch["required_node_ids"].append("missing")
    with pytest.raises(ValueError, match="Required workflow node is missing"):
        apply_workflow_patch(config, graphs, patch)


def test_layout_replaces_only_the_patch_namespace():
    _config, _graphs, patch = documents()
    patch["layout"] = {"nodes": {"digest-map": {"x": 720, "y": 360}}}
    layout = {
        "nodes": {
            "schedule": {"x": 10, "y": 20},
            "digest-old": {"x": 30, "y": 40},
        }
    }

    updated = apply_workflow_patch_layout(layout, patch)

    assert updated == {
        "nodes": {
            "schedule": {"x": 10, "y": 20},
            "digest-map": {"x": 720.0, "y": 360.0},
        }
    }
    assert layout["nodes"]["digest-old"] == {"x": 30, "y": 40}


@pytest.mark.parametrize(
    "positions, message",
    [
        ({}, "every patch node"),
        ({"digest-map": {"x": float("inf"), "y": 0}}, "finite numbers"),
    ],
)
def test_layout_rejects_incomplete_or_invalid_positions(positions, message):
    _config, _graphs, patch = documents()
    patch["layout"] = {"nodes": positions}
    with pytest.raises(ValueError, match=message):
        apply_workflow_patch_layout({"nodes": {}}, patch)
