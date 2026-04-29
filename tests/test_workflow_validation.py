from __future__ import annotations

from agent_sandbox.workflow.validation import validate_workflow_graph


def test_validate_workflow_graph_runs_with_empty_capability_map() -> None:
    nodes = [
        type("Node", (), {"id": "trigger-1", "type": "trigger:cron"})(),
        type("Node", (), {"id": "effect-1", "type": "effect:vm:exec"})(),
    ]
    edges = [
        type(
            "Edge",
            (),
            {
                "source_id": "trigger-1",
                "target_id": "effect-1",
                "source_port": None,
                "allow_cycle": False,
            },
        )(),
    ]

    errors = validate_workflow_graph(
        nodes=nodes,
        edges=edges,
        trigger_node_ids=["trigger-1"],
        node_capabilities={},
    )

    assert errors == []
