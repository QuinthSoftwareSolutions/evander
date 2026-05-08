from __future__ import annotations

from agent_sandbox.workflow.validation import (
    derive_workflow_output_schemas,
    validate_workflow_binding_diagnostics,
    validate_workflow_graph,
)


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


def test_binding_diagnostics_accept_filter_derived_outputs() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-ticket-created-main",
                "display_name": "Ticket created",
                "type": "trigger:ticket:created",
                "directories": ["root"],
            }
        ],
        "gates": [
            {
                "id": "gate-filter-1",
                "display_name": "Filter ticket",
                "type": "gate:filter",
                "when": {
                    "all": [
                        {
                            "left": "${{trigger-ticket-created-main.ticket.directory_path}}",
                            "equals": "root",
                        }
                    ]
                },
            }
        ],
        "effects": [
            {
                "id": "prompt-agent",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "session_id": "session-1",
                "prompt": "Review ${{gate-filter-1.ticket.body}}",
            }
        ],
        "edges": [
            {"source_id": "trigger-ticket-created-main", "target_id": "gate-filter-1"},
            {"source_id": "gate-filter-1", "target_id": "prompt-agent"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["gate-filter-1"]["ticket"]["properties"]["body"] == {
        "type": "string"
    }
    assert diagnostics == []


def test_binding_diagnostics_accept_merge_derived_outputs() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-ticket-created-main",
                "display_name": "Ticket created",
                "type": "trigger:ticket:created",
                "directories": ["root/one"],
            },
            {
                "id": "trigger-ticket-changed-main",
                "display_name": "Ticket changed",
                "type": "trigger:ticket:changed",
                "directories": ["root/two"],
            },
        ],
        "gates": [
            {
                "id": "gate-map-1",
                "display_name": "Map created ticket",
                "type": "gate:map",
                "template": {
                    "ticket": {"body": "${{trigger-ticket-created-main.ticket.body}}"},
                    "route": "created",
                },
            },
            {
                "id": "gate-map-2",
                "display_name": "Map changed ticket",
                "type": "gate:map",
                "template": {
                    "ticket": {"body": "${{trigger-ticket-changed-main.ticket.body}}"},
                    "route": "changed",
                },
            },
            {
                "id": "gate-merge-1",
                "display_name": "Merge tickets",
                "type": "gate:merge",
            },
        ],
        "effects": [
            {
                "id": "prompt-agent",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "session_id": "session-1",
                "prompt": "Review ${{gate-merge-1.ticket.body}} via ${{gate-merge-1.route}}",
            }
        ],
        "edges": [
            {"source_id": "trigger-ticket-created-main", "target_id": "gate-map-1"},
            {"source_id": "trigger-ticket-changed-main", "target_id": "gate-map-2"},
            {"source_id": "gate-map-1", "target_id": "gate-merge-1"},
            {"source_id": "gate-map-2", "target_id": "gate-merge-1"},
            {"source_id": "gate-merge-1", "target_id": "prompt-agent"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["gate-merge-1"] == {
        "ticket": {"type": "object"},
        "route": {"type": "string"},
    }
    assert diagnostics == []


def test_output_schema_derivation_handles_nested_node_config() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-ticket-created-main",
                "display_name": "Ticket created",
                "type": "trigger:ticket:created",
                "config": {"directories": ["root"]},
            }
        ],
        "gates": [
            {
                "id": "gate-map-1",
                "display_name": "Map ticket",
                "type": "gate:map",
                "config": {
                    "template": {
                        "ticket": {"body": "${{trigger-ticket-created-main.ticket.body}}"},
                        "route": "created",
                    },
                },
            },
            {
                "id": "gate-filter-1",
                "display_name": "Filter mapped ticket",
                "type": "gate:filter",
                "config": {
                    "when": {
                        "all": [
                            {
                                "left": "${{gate-map-1.route}}",
                                "equals": "created",
                            }
                        ]
                    },
                },
            },
        ],
        "effects": [
            {
                "id": "prompt-agent",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "config": {
                    "session_id": "session-1",
                    "prompt": "Review ${{gate-filter-1.ticket.body}}",
                },
            }
        ],
        "edges": [
            {"source_id": "trigger-ticket-created-main", "target_id": "gate-map-1"},
            {"source_id": "gate-map-1", "target_id": "gate-filter-1"},
            {"source_id": "gate-filter-1", "target_id": "prompt-agent"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["gate-map-1"] == {
        "ticket": {"type": "object"},
        "route": {"type": "string"},
    }
    assert output_schemas["gate-filter-1"] == output_schemas["gate-map-1"]
    assert diagnostics == []
