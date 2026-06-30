from __future__ import annotations

import pytest

from evander.workflow.graph import (
    WorkflowGraphCycleError,
    build_downstream_index,
    build_inbound_edge_index,
    compute_downstream_depth,
    index_declared_outputs,
    merge_declared_output_intersection,
    merge_output_schema_intersection,
    reachable_downstream_nodes,
    would_form_cycle,
)


def test_build_indices_and_cycle_helpers_handle_canonical_edge_shape() -> None:
    edges = [
        {"source_id": "trigger-1", "target_id": "gate-1"},
        {"source_id": "gate-1", "target_id": "effect-1"},
        ("effect-1", "effect-2"),
    ]

    downstream = build_downstream_index(edges)
    inbound = build_inbound_edge_index(edges)

    assert downstream == {
        "effect-1": ("effect-2",),
        "gate-1": ("effect-1",),
        "trigger-1": ("gate-1",),
    }
    assert inbound["effect-1"][0] == {"source_id": "gate-1", "target_id": "effect-1"}
    assert reachable_downstream_nodes("trigger-1", downstream) == ("effect-1", "effect-2", "gate-1")
    assert would_form_cycle("effect-2", "trigger-1", downstream) is True
    assert would_form_cycle("trigger-1", "effect-2", downstream) is False
    assert compute_downstream_depth("trigger-1", downstream) == 4


def test_compute_downstream_depth_raises_on_cycle() -> None:
    downstream = {"a": ("b",), "b": ("a",)}

    with pytest.raises(WorkflowGraphCycleError, match="cycle"):
        compute_downstream_depth("a", downstream)


def test_declared_output_index_and_schema_intersection_cover_nodes_and_raw_maps() -> None:
    nodes = [
        {
            "id": "trigger-1",
            "outputs": {"ticket_id": {"type": "integer"}, "shared": {"type": "string"}},
        },
        {
            "id": "effect-1",
            "outputs": {"shared": {"type": "string"}, "session_id": {"type": "string"}},
        },
        {"id": "gate-1", "outputs": {"shared": {"type": "string"}, "route": {"type": "string"}}},
    ]

    indexed = index_declared_outputs(nodes)
    assert indexed["trigger-1"]["ticket_id"] == {"type": "integer"}
    assert indexed["gate-1"]["route"] == {"type": "string"}

    assert merge_declared_output_intersection(nodes) == {"shared": {"type": "string"}}
    assert merge_output_schema_intersection(
        {"shared": {"type": "string"}, "ticket_id": {"type": "integer"}},
        {"shared": {"type": "string"}, "session_id": {"type": "string"}},
        {"shared": {"type": "string"}, "route": {"type": "string"}},
    ) == {"shared": {"type": "string"}}


def test_declared_output_index_includes_intrinsic_and_derived_outputs() -> None:
    nodes = [
        {"id": "trigger-1", "type": "trigger:ticket:created", "directories": ["root"]},
        {
            "id": "gate-1",
            "type": "gate:map",
            "template": {"vm_name": "vm-${{trigger-1.ticket}}", "count": 1},
        },
        {"id": "effect-1", "type": "effect:vm:create"},
        {"id": "query-1", "type": "query:vm:run-state"},
    ]

    indexed = index_declared_outputs(nodes)

    assert indexed["trigger-1"]["ticket"]["type"] == "object"
    assert indexed["trigger-1"]["ticket"]["properties"]["issue_number"] == {"type": "string"}
    assert indexed["gate-1"] == {"vm_name": {"type": "string"}, "count": {"type": "integer"}}
    assert indexed["effect-1"]["session_id"] == {"type": "string"}
    assert indexed["query-1"]["state_code"] == {"type": "string"}
