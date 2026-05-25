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


def test_semantic_management_outputs_are_bindable() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-cron-1",
                "display_name": "Cron",
                "type": "trigger:cron",
                "expression": "*/15 * * * *",
            }
        ],
        "effects": [
            {
                "id": "semantic-ensure-1",
                "display_name": "Ensure semantic collection",
                "type": "effect:semantic:ensure-collection",
                "collection_name": "agent_memory",
            },
            {
                "id": "semantic-upsert-1",
                "display_name": "Semantic upsert",
                "type": "effect:semantic:upsert",
                "collection_name": "${{semantic-ensure-1.collection_name}}",
                "point_id": "00000000-0000-4000-8000-000000000001",
                "text": "remember this",
                "payload": {"source": "test"},
            },
            {
                "id": "semantic-delete-1",
                "display_name": "Semantic delete",
                "type": "effect:semantic:delete",
                "collection_name": "${{semantic-ensure-1.collection_name}}",
                "point_ids": ["00000000-0000-4000-8000-000000000001"],
            }
        ],
        "queries": [
            {
                "id": "semantic-status-1",
                "display_name": "Semantic status",
                "type": "query:semantic:store-status",
                "collection_name": "${{semantic-ensure-1.collection_name}}",
            },
            {
                "id": "semantic-search-1",
                "display_name": "Semantic search",
                "type": "query:semantic:search",
                "collection_name": "${{semantic-upsert-1.collection_name}}",
                "query_text": "find it",
                "filter": {"must": [{"key": "source", "match": {"value": "test"}}]},
            },
            {
                "id": "semantic-get-1",
                "display_name": "Semantic get",
                "type": "query:semantic:get",
                "collection_name": "${{semantic-upsert-1.collection_name}}",
                "point_ids": "${{semantic-delete-1.point_ids}}",
            }
        ],
        "edges": [
            {"source_id": "trigger-cron-1", "target_id": "semantic-ensure-1"},
            {"source_id": "semantic-ensure-1", "target_id": "semantic-upsert-1"},
            {"source_id": "semantic-upsert-1", "target_id": "semantic-status-1"},
            {"source_id": "semantic-upsert-1", "target_id": "semantic-search-1"},
            {"source_id": "semantic-status-1", "target_id": "semantic-delete-1"},
            {"source_id": "semantic-delete-1", "target_id": "semantic-get-1"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["semantic-ensure-1"]["collection_name"] == {"type": "string"}
    assert output_schemas["semantic-upsert-1"]["payload"] == {"type": "object"}
    assert output_schemas["semantic-delete-1"]["point_ids"] == {"type": "array"}
    assert output_schemas["semantic-status-1"]["reachable"] == {"type": "boolean"}
    assert output_schemas["semantic-search-1"]["points"] == {"type": "array"}
    assert output_schemas["semantic-get-1"]["missing_ids"] == {"type": "array"}
    assert diagnostics == []


def test_issue_list_outputs_are_bindable() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-cron-1",
                "display_name": "Cron",
                "type": "trigger:cron",
                "expression": "*/15 * * * *",
            }
        ],
        "queries": [
            {
                "id": "query-issues-1",
                "display_name": "Issue list",
                "type": "query:issue:list",
                "directory_path": "root/team-a",
                "direct_only": True,
                "states": ["Open", "In Progress"],
                "exclude_states": ["Done"],
                "source": "local_gui",
                "source_type": "local_manual",
                "tags_any": ["triage"],
                "tags_all": ["team-a"],
                "metadata_equals": {"priority": "high"},
                "metadata_exists": ["owner"],
                "metadata_prefix": {"fields.title": "Fix"},
                "limit": 10,
            }
        ],
        "effects": [
            {
                "id": "prompt-agent",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "session_id": "session-1",
                "prompt": "Found ${{query-issues-1.count}} issues in ${{query-issues-1.issues}}",
            }
        ],
        "edges": [
            {"source_id": "trigger-cron-1", "target_id": "query-issues-1"},
            {"source_id": "query-issues-1", "target_id": "prompt-agent"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["query-issues-1"]["issues"]["type"] == "array"
    assert output_schemas["query-issues-1"]["count"] == {"type": "integer"}
    assert diagnostics == []


def test_schedule_event_outputs_are_bindable() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-cron-1",
                "display_name": "Cron",
                "type": "trigger:cron",
                "expression": "*/15 * * * *",
            }
        ],
        "effects": [
            {
                "id": "schedule-event-1",
                "display_name": "Schedule event",
                "type": "effect:event:schedule",
                "event_type": "custom.operator_event",
                "payload": {"source": "workflow"},
                "delay_seconds": 60,
            },
            {
                "id": "prompt-agent",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "session_id": "session-1",
                "prompt": (
                    "Scheduled ${{schedule-event-1.schedule_id}} for "
                    "${{schedule-event-1.run_at}} with status ${{schedule-event-1.status}}"
                ),
            },
        ],
        "queries": [],
        "edges": [
            {"source_id": "trigger-cron-1", "target_id": "schedule-event-1"},
            {"source_id": "schedule-event-1", "target_id": "prompt-agent"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["schedule-event-1"] == {
        "success": {"type": "boolean"},
        "created": {"type": "boolean"},
        "schedule_id": {"type": "string"},
        "event_type": {"type": "string"},
        "correlation_key": {"type": ["string", "null"]},
        "run_at": {"type": "string"},
        "status": {"type": "string"},
        "payload": {"type": "object"},
    }
    assert diagnostics == []


def test_http_request_outputs_are_bindable() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-cron-1",
                "display_name": "Cron",
                "type": "trigger:cron",
                "expression": "*/15 * * * *",
            }
        ],
        "effects": [
            {
                "id": "effect-http-1",
                "display_name": "HTTP request",
                "type": "effect:http:request",
                "method": "GET",
                "url": "https://api.example.test/items/42",
            },
            {
                "id": "effect-prompt-1",
                "display_name": "Prompt",
                "type": "effect:agent:prompt",
                "prompt": (
                    "HTTP returned ${{effect-http-1.status_code}} and item "
                    "${{effect-http-1.body_json.id}}"
                ),
            },
        ],
        "edges": [
            {"source_id": "trigger-cron-1", "target_id": "effect-http-1"},
            {"source_id": "effect-http-1", "target_id": "effect-prompt-1"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["effect-http-1"]["status_code"] == {"type": "integer"}
    assert output_schemas["effect-http-1"]["body_json"] == {"type": "object"}
    assert diagnostics == []


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


def test_ci_run_completed_outputs_are_bindable() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-ci-1",
                "display_name": "CI run completed",
                "type": "trigger:ci:run:completed",
                "provider": "github_actions",
                "repo": "owner/repo",
                "workflow": "build.yml",
                "branches": ["main"],
            }
        ],
        "effects": [
            {
                "id": "prompt-agent",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "session_id": "session-1",
                "prompt": "CI ${{trigger-ci-1.repo}} ran as ${{trigger-ci-1.workflow_name}}",
            }
        ],
        "edges": [
            {"source_id": "trigger-ci-1", "target_id": "prompt-agent"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["trigger-ci-1"]["provider"] == {"type": "string"}
    assert output_schemas["trigger-ci-1"]["repo"] == {"type": "string"}
    assert output_schemas["trigger-ci-1"]["workflow_id"] == {"type": ["string", "null"]}
    assert output_schemas["trigger-ci-1"]["pipeline_id"] == {"type": ["integer", "null"]}
    assert output_schemas["trigger-ci-1"]["pull_requests"] == {
        "type": "array",
        "items": {"type": "object"},
    }
    assert output_schemas["trigger-ci-1"]["metadata"] == {"type": "object"}
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


def test_output_schema_derivation_infers_deserialize_outputs_from_literal_yaml() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-event-main",
                "display_name": "Event",
                "type": "trigger:event",
                "event_type": "payload",
            }
        ],
        "gates": [
            {
                "id": "gate-deserialize-1",
                "display_name": "Deserialize",
                "type": "gate:deserialize",
                "content": "title: Fix relay\nmetadata:\n  priority: P1\ncount: 2\nactive: true\n",
                "format": "yaml",
            }
        ],
        "effects": [
            {
                "id": "prompt-agent",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "session_id": "session-1",
                "prompt": (
                    "Review ${{gate-deserialize-1.title}} "
                    "${{gate-deserialize-1.metadata.priority}}"
                ),
            }
        ],
        "edges": [
            {"source_id": "trigger-event-main", "target_id": "gate-deserialize-1"},
            {"source_id": "gate-deserialize-1", "target_id": "prompt-agent"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["gate-deserialize-1"] == {
        "title": {"type": "string"},
        "metadata": {"type": "object"},
        "count": {"type": "integer"},
        "active": {"type": "boolean"},
    }
    assert diagnostics == []


def test_output_schema_derivation_respects_explicit_deserialize_json_format() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-event-main",
                "display_name": "Event",
                "type": "trigger:event",
                "event_type": "payload",
            }
        ],
        "gates": [
            {
                "id": "gate-deserialize-1",
                "display_name": "Deserialize",
                "type": "gate:deserialize",
                "content": "title: Fix relay\n",
                "format": "json",
            }
        ],
        "effects": [
            {
                "id": "prompt-agent",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "session_id": "session-1",
                "prompt": "Review ${{gate-deserialize-1.title}}",
            }
        ],
        "edges": [
            {"source_id": "trigger-event-main", "target_id": "gate-deserialize-1"},
            {"source_id": "gate-deserialize-1", "target_id": "prompt-agent"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["gate-deserialize-1"] == {}
    assert [diagnostic.model_dump() for diagnostic in diagnostics] == [
        {
            "code": "missing_binding_output",
            "message": "prompt references missing output gate-deserialize-1.title",
            "node_id": "prompt-agent",
            "node_type": "effect:agent:prompt",
            "field_path": "prompt",
            "reference": "gate-deserialize-1.title",
        }
    ]


def test_output_schema_derivation_uses_declared_deserialize_outputs_for_bound_content() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-event-main",
                "display_name": "Event",
                "type": "trigger:event",
                "event_type": "payload",
                "outputs": {"payload_text": {"type": "string"}},
            }
        ],
        "gates": [
            {
                "id": "gate-deserialize-1",
                "display_name": "Deserialize",
                "type": "gate:deserialize",
                "content": "${{trigger-event-main.payload_text}}",
                "outputs": {"title": {"type": "string"}, "metadata": {"type": "object"}},
            }
        ],
        "effects": [
            {
                "id": "prompt-agent",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "session_id": "session-1",
                "prompt": "Review ${{gate-deserialize-1.title}}",
            }
        ],
        "edges": [
            {"source_id": "trigger-event-main", "target_id": "gate-deserialize-1"},
            {"source_id": "gate-deserialize-1", "target_id": "prompt-agent"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["gate-deserialize-1"] == {
        "title": {"type": "string"},
        "metadata": {"type": "object"},
    }
    assert diagnostics == []


def test_output_schema_derivation_exposes_static_serialize_outputs() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-event-main",
                "display_name": "Event",
                "type": "trigger:event",
                "event_type": "payload",
                "outputs": {"ticket": {"type": "object"}},
            }
        ],
        "gates": [
            {
                "id": "gate-serialize-1",
                "display_name": "Serialize",
                "type": "gate:serialize",
                "value": "${{trigger-event-main.ticket}}",
                "format": "json",
            }
        ],
        "effects": [
            {
                "id": "prompt-agent",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "session_id": "session-1",
                "prompt": "Serialized ${{gate-serialize-1.content}}",
            }
        ],
        "edges": [
            {"source_id": "trigger-event-main", "target_id": "gate-serialize-1"},
            {"source_id": "gate-serialize-1", "target_id": "prompt-agent"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["gate-serialize-1"] == {
        "content": {"type": "string"},
        "format": {"type": "string"},
    }
    assert diagnostics == []


def test_output_schema_derivation_exposes_webhook_envelope_outputs() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-webhook-1",
                "display_name": "Webhook",
                "type": "trigger:http:webhook",
                "slug": "build-notify",
                "secret_env": "WEBHOOK_SECRET",
            }
        ],
        "effects": [
            {
                "id": "prompt-agent",
                "display_name": "Prompt agent",
                "type": "effect:agent:prompt",
                "session_id": "session-1",
                "prompt": "Webhook ${{trigger-webhook-1.slug}} via ${{trigger-webhook-1.signature_header}}",
            }
        ],
        "edges": [{"source_id": "trigger-webhook-1", "target_id": "prompt-agent"}],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["trigger-webhook-1"] == {
        "slug": {"type": "string"},
        "headers": {"type": "object"},
        "query": {"type": "object"},
        "body": {"type": "object"},
        "received_at": {"type": "string"},
        "remote_addr": {"type": "string"},
        "signature_header": {"type": "string"},
    }
    assert diagnostics == []


def test_pull_request_list_next_cursor_is_bindable() -> None:
    config = {
        "triggers": [
            {
                "id": "trigger-cron-1",
                "display_name": "Cron",
                "type": "trigger:cron",
                "expression": "*/5 * * * *",
            }
        ],
        "queries": [
            {
                "id": "query-prs-1",
                "display_name": "List PRs",
                "type": "query:pull-request:list",
                "provider": "ado",
                "repo": "repo-1",
                "status": "active",
                "top": 50,
            },
            {
                "id": "query-prs-next",
                "display_name": "List next PRs",
                "type": "query:pull-request:list",
                "provider": "ado",
                "repo": "repo-1",
                "status": "active",
                "cursor": "${{query-prs-1.next_cursor}}",
                "top": 50,
            },
        ],
        "edges": [
            {"source_id": "trigger-cron-1", "target_id": "query-prs-1"},
            {"source_id": "query-prs-1", "target_id": "query-prs-next"},
        ],
    }

    output_schemas = derive_workflow_output_schemas(config)
    diagnostics = validate_workflow_binding_diagnostics(config)

    assert output_schemas["query-prs-1"]["next_cursor"] == {"type": ["string", "null"]}
    assert diagnostics == []
