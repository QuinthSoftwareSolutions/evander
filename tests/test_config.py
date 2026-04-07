import pytest

from agent_sandbox.config import (
    AdoWorkerGlobalConfig,
    DiscordIngestWorkerGlobalConfig,
    GatewayConfig,
    HostHeartbeatWorkerGlobalConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)


def test_folder_allowlist_defaults_populate_only_when_mapping_is_empty() -> None:
    config = GatewayConfig.model_validate(
        {
            "email_mailboxes": [
                {"mailbox_id": "primary", "folder_allowlist_defaults": ["inbox", "drafts"]},
                {"mailbox_id": "secondary", "folder_allowlist_defaults": ["inbox"]},
            ],
            "email_folder_allowlist_by_mailbox": {"primary": ["inbox"]},
        }
    )

    assert config.email_folder_allowlist_by_mailbox == {"primary": ["inbox"]}


def test_folder_allowlist_defaults_populate_all_mailboxes_when_mapping_left_empty() -> None:
    config = GatewayConfig.model_validate(
        {
            "email_mailboxes": [
                {"mailbox_id": "primary", "folder_allowlist_defaults": ["inbox", "drafts"]},
                {"mailbox_id": "secondary", "folder_allowlist_defaults": ["inbox"]},
            ],
        }
    )

    assert config.email_folder_allowlist_by_mailbox == {
        "primary": ["inbox", "drafts"],
        "secondary": ["inbox"],
    }


def test_duplicate_email_mailbox_ids_fail_validation() -> None:
    with pytest.raises(ValueError, match="Duplicate email mailbox IDs are not allowed: primary"):
        GatewayConfig.model_validate(
            {
                "email_mailboxes": [
                    {"mailbox_id": "primary"},
                    {"mailbox_id": "primary"},
                ]
            }
        )


def test_unique_email_mailbox_ids_pass_validation_with_alias_fields() -> None:
    config = GatewayConfig.model_validate(
        {
            "email_mailboxes": [
                {
                    "mailbox_id": "primary",
                    "account": "primary@example.com",
                    "auth_env_var_names": {"access_token": "PRIMARY_TOKEN"},
                },
                {
                    "mailbox_id": "secondary",
                    "address": "secondary@example.com",
                    "auth_env_var_names": {"access_token": "SECONDARY_TOKEN"},
                },
            ]
        }
    )

    assert [mailbox.mailbox_id for mailbox in config.email_mailboxes] == ["primary", "secondary"]
    assert [mailbox.account_identifier for mailbox in config.email_mailboxes] == [
        "primary@example.com",
        "secondary@example.com",
    ]
    assert [mailbox.auth_secret_refs for mailbox in config.email_mailboxes] == [
        {"access_token": "PRIMARY_TOKEN"},
        {"access_token": "SECONDARY_TOKEN"},
    ]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("gateway_url", " https://example.test/mcp/ "),
        ("discord_api_base_url", " https://discord.com/api/v10/ "),
        ("teams_graph_api_base_url", " https://graph.microsoft.com/v1.0/ "),
        ("openai_api_base_url", " https://api.openai.com/v1/ "),
        ("nuget_public_feed_base_url", " https://api.nuget.org/ "),
        (
            "nuget_azure_feed_base_url",
            " https://pkgs.dev.azure.com/example/example/_packaging/example/nuget/ ",
        ),
    ],
)
def test_http_url_fields_are_trimmed_and_trailing_slash_is_removed(
    field_name: str, value: str
) -> None:
    config = GatewayConfig.model_validate({field_name: value})
    assert getattr(config, field_name) == value.strip().rstrip("/")


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("gateway_url", "ftp://example.test/mcp"),
        ("discord_api_base_url", "discord.com/api/v10"),
        ("teams_graph_api_base_url", "graph.microsoft.com/v1.0"),
        ("openai_api_base_url", "not-a-url"),
        ("nuget_public_feed_base_url", "://missing-scheme"),
        ("nuget_azure_feed_base_url", ""),
    ],
)
def test_http_url_fields_reject_invalid_values_with_field_name(
    field_name: str, value: str
) -> None:
    with pytest.raises(ValueError, match=field_name):
        GatewayConfig.model_validate({field_name: value})


def test_bind_network_cidr_trims_whitespace_and_normalizes() -> None:
    config = GatewayConfig.model_validate({"bind_network_cidr": " 192.168.250.0/24 "})
    assert str(config.bind_network_cidr) == "192.168.250.0/24"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-cidr",
        "192.168.250.1/24",
    ],
)
def test_bind_network_cidr_rejects_invalid_values_with_clear_error(value: str) -> None:
    with pytest.raises(ValueError, match="bind_network_cidr"):
        GatewayConfig.model_validate({"bind_network_cidr": value})


def test_host_heartbeat_lock_path_follows_runtime_dir_default() -> None:
    config = GatewayConfig.model_validate({"runtime_dir": "/tmp/agent-sandbox-runtime"})
    assert str(config.host_heartbeat_lock_path) == "/tmp/agent-sandbox-runtime/host-heartbeat.lock"


def test_host_heartbeat_wake_state_path_follows_runtime_dir_default() -> None:
    config = GatewayConfig.model_validate({"runtime_dir": "/tmp/agent-sandbox-runtime"})
    assert str(config.host_heartbeat_wake_state_path) == "/tmp/agent-sandbox-runtime/host-heartbeat-wake-state.json"


def test_host_agent_control_state_path_follows_runtime_dir_default() -> None:
    config = GatewayConfig.model_validate({"runtime_dir": "/tmp/agent-sandbox-runtime"})
    assert str(config.host_agent_control_state_path) == "/tmp/agent-sandbox-runtime/host-agent-control-state.json"


def test_host_heartbeat_poll_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="host_heartbeat_poll_interval_seconds"):
        GatewayConfig.model_validate({"host_heartbeat_poll_interval_seconds": 0})


def test_host_heartbeat_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = HostHeartbeatWorkerGlobalConfig.model_validate({"runtime_dir": "/tmp/agent-sandbox-runtime"})
    assert str(config.host_heartbeat_lock_path) == "/tmp/agent-sandbox-runtime/host-heartbeat.lock"
    assert str(config.host_heartbeat_session_cooldown_path) == "/tmp/agent-sandbox-runtime/host-heartbeat-session-cooldown.json"
    assert str(config.host_heartbeat_wake_state_path) == "/tmp/agent-sandbox-runtime/host-heartbeat-wake-state.json"
    assert config.host_heartbeat_directory_paths == []
    assert config.host_heartbeat_schedule_max_active_per_session is None
    assert config.host_heartbeat_schedule_max_active_total is None
    assert config.host_heartbeat_schedule_min_interval_seconds is None


def test_host_heartbeat_worker_global_config_directory_paths_are_normalized() -> None:
    config = HostHeartbeatWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/agent-sandbox-runtime",
            "host_heartbeat_directory_paths": [" root/team-a ", "root/team-a/subdir", "  "],
        }
    )
    assert config.host_heartbeat_directory_paths == ["root/team-a", "root/team-a/subdir"]


def test_discord_ingest_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = DiscordIngestWorkerGlobalConfig.model_validate({"runtime_dir": "/tmp/agent-sandbox-runtime"})
    assert str(config.lock_path) == "/tmp/agent-sandbox-runtime/discord-ingest.lock"
    assert config.directories == []


def test_discord_ingest_worker_global_config_directory_paths_are_normalized() -> None:
    config = DiscordIngestWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/agent-sandbox-runtime",
            "directories": [" root/team-a ", "root/team-a/subdir", "  "],
        }
    )
    assert config.directories == ["root/team-a", "root/team-a/subdir"]


def test_ado_worker_global_config_defaults_include_dedicated_pat_env_name() -> None:
    config = AdoWorkerGlobalConfig.model_validate({})
    assert config.ado_work_items_azure_devops_pat_env == "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    assert config.directories == []


def test_directory_scoped_config_flag_defaults_to_disabled() -> None:
    config = GatewayConfig.model_validate({})
    assert config.enable_directory_scoped_config is False


def test_trigger_orchestrator_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {"runtime_dir": "/tmp/agent-sandbox-runtime"}
    )
    assert str(config.lock_path) == "/tmp/agent-sandbox-runtime/trigger-orchestrator.lock"
    assert (
        str(config.event_queue_store_path)
        == "/tmp/agent-sandbox-runtime/trigger-orchestrator-events.json"
    )
    assert (
        str(config.event_queue_cursor_store_path)
        == "/tmp/agent-sandbox-runtime/trigger-orchestrator-cursors.json"
    )
    assert (
        str(config.gate_execution_store_path)
        == "/tmp/agent-sandbox-runtime/gate-orchestrator-executions.json"
    )
    assert (
        str(config.gate_cursor_store_path)
        == "/tmp/agent-sandbox-runtime/gate-orchestrator-cursors.json"
    )
    assert (
        str(config.effect_execution_store_path)
        == "/tmp/agent-sandbox-runtime/effect-orchestrator-executions.json"
    )
    assert (
        str(config.effect_cursor_store_path)
        == "/tmp/agent-sandbox-runtime/effect-orchestrator-cursors.json"
    )
    assert str(config.effect_lock_path) == "/tmp/agent-sandbox-runtime/effect-orchestrator.lock"
    assert config.triggers == []


def test_trigger_orchestrator_filter_gate_accepts_when_predicates() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/agent-sandbox-runtime",
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                }
            ],
            "gates": [
                {
                    "id": "gate-filter",
                    "display_name": "VM create succeeded",
                    "type": "gate:filter",
                    "outputs": {
                        "guest_repo_name": {"type": "string"},
                        "session_id": {"type": "string"},
                        "labels": {"type": "object"},
                        "remote_url": {"type": "string"},
                        "success": {"type": "boolean"},
                        "target_dir": {"type": "string"},
                        "vm_name": {"type": "string"},
                    },
                    "when": {
                        "all": [
                            {"left": "${{effect-1.success}}", "equals": True},
                            {"left": "${{effect-1.session_id}}", "is_not_null": True},
                        ]
                    },
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Create VM",
                    "type": "effect:vm:create",
                    "vm_name": "vm-1",
                }
            ],
            "edges": [
                {"source_id": "trigger-1", "target_id": "effect-1"},
                {"source_id": "effect-1", "target_id": "gate-filter"},
            ],
        }
    )

    assert config.gates[0].when == {
        "all": [
            {"left": "${{effect-1.success}}", "equals": True},
            {"left": "${{effect-1.session_id}}", "is_not_null": True},
        ]
    }


def test_trigger_orchestrator_filter_gate_rejects_match_and_when_together() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {
                "runtime_dir": "/tmp/agent-sandbox-runtime",
                "triggers": [
                    {
                        "id": "trigger-1",
                        "display_name": "Tick",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    }
                ],
                "gates": [
                    {
                        "id": "gate-filter",
                        "display_name": "Invalid filter",
                        "type": "gate:filter",
                        "outputs": {},
                        "match": {"success": True},
                        "when": {"all": [{"left": "${{effect-1.success}}", "equals": True}]},
                    }
                ],
                "effects": [],
                "edges": [],
            }
        )


def test_trigger_orchestrator_filter_gate_accepts_string_operators() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/agent-sandbox-runtime",
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Git push",
                    "type": "trigger:git:push",
                    "path_glob": "/tmp/*.git",
                }
            ],
            "gates": [
                {
                    "id": "gate-filter",
                    "display_name": "Agent branch only",
                    "type": "gate:filter",
                    "outputs": {
                        "branch_name": {"type": "string"},
                        "commit_hash": {"type": "string"},
                        "old_commit_hash": {"type": "string"},
                        "ref_name": {"type": "string"},
                        "repo_path": {"type": "string"},
                        "repo_name": {"type": "string"},
                        "session_id": {"type": "string"},
                        "review_branch": {"type": "string"},
                        "trusted_repo_path": {"type": "string"},
                        "base_branch": {"type": "string"},
                        "canonical_origin_url": {"type": "string"},
                    },
                    "when": {
                        "all": [
                            {"left": "${{trigger-1.branch_name}}", "starts_with": "agents/"},
                            {"left": "${{trigger-1.repo_path}}", "ends_with": ".git"},
                            {"left": "${{trigger-1.branch_name}}", "contains": "/"},
                        ]
                    },
                }
            ],
            "effects": [
                {
                    "id": "effect-1",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "continue",
                }
            ],
            "edges": [
                {"source_id": "trigger-1", "target_id": "gate-filter"},
                {"source_id": "gate-filter", "target_id": "effect-1"},
            ],
        }
    )

    assert config.gates[0].when == {
        "all": [
            {"left": "${{trigger-1.branch_name}}", "starts_with": "agents/"},
            {"left": "${{trigger-1.repo_path}}", "ends_with": ".git"},
            {"left": "${{trigger-1.branch_name}}", "contains": "/"},
        ]
    }


def test_trigger_orchestrator_await_agent_gate_accepts_completion_trigger_reference() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/agent-sandbox-runtime",
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                },
                {
                    "id": "agent-complete",
                    "display_name": "Agent completed",
                    "type": "trigger:agent:run:completed",
                },
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "completion_trigger_id": "agent-complete",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-prompt",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "continue",
                }
            ],
            "edges": [
                {"source_id": "trigger-1", "target_id": "effect-prompt"},
                {"source_id": "effect-prompt", "target_id": "await-agent-1"},
            ],
        }
    )

    assert config.gates[0].type == "gate:await-agent"
    assert config.gates[0].outputs["dispatch_id"] == {"type": "string"}


def test_trigger_orchestrator_await_agent_gate_accepts_linear_flow_without_completion_trigger() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/agent-sandbox-runtime",
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                },
            ],
            "gates": [
                {
                    "id": "await-agent-1",
                    "display_name": "Await agent",
                    "type": "gate:await-agent",
                    "outputs": {},
                }
            ],
            "effects": [
                {
                    "id": "effect-prompt",
                    "display_name": "Prompt",
                    "type": "effect:agent:prompt",
                    "session_id": "session-1",
                    "prompt": "continue",
                }
            ],
            "edges": [
                {"source_id": "trigger-1", "target_id": "effect-prompt"},
                {"source_id": "effect-prompt", "target_id": "await-agent-1"},
            ],
        }
    )

    assert config.gates[0].type == "gate:await-agent"
    assert config.gates[0].outputs["dispatch_id"] == {"type": "string"}


def test_trigger_orchestrator_worker_global_config_accepts_discriminated_triggers() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "triggers": [
                {
                    "id": "cron-15m",
                    "display_name": "Every 15 minutes",
                    "type": "trigger:cron",
                    "expression": "*/15 * * * *",
                }
            ]
        }
    )

    assert config.triggers[0].type == "trigger:cron"




def test_trigger_orchestrator_worker_global_config_accepts_effects() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "triggers": [
                {
                    "id": "cron-15m",
                    "display_name": "Every 15 minutes",
                    "type": "trigger:cron",
                    "expression": "*/15 * * * *",
                }
            ],
            "effects": [
                {
                    "id": "prompt-agent",
                    "display_name": "Prompt agent",
                    "type": "effect:agent:prompt",
                    "prompt": "Summarize ${{trigger.branch}}",
                }
            ],
            "edges": [{"source_id": "cron-15m", "target_id": "prompt-agent"}],
        }
    )

    assert config.effects[0].type == "effect:agent:prompt"


def test_trigger_orchestrator_worker_global_config_accepts_queries() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "triggers": [
                {
                    "id": "cron-15m",
                    "display_name": "Every 15 minutes",
                    "type": "trigger:cron",
                    "expression": "*/15 * * * *",
                }
            ],
            "queries": [
                {
                    "id": "query-state",
                    "display_name": "VM run state",
                    "type": "query:vm:run-state",
                    "session_id": "session-1",
                    "outputs": {"state_code": {"type": "string"}},
                }
            ],
            "edges": [{"source_id": "cron-15m", "target_id": "query-state"}],
        }
    )

    assert config.queries[0].type == "query:vm:run-state"


def test_trigger_orchestrator_worker_global_config_accepts_multiple_effect_edges_from_one_source() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Every 15 minutes",
                    "type": "trigger:cron",
                    "expression": "*/15 * * * *",
                }
            ],
            "effects": [
                {
                    "id": "effect-a",
                    "display_name": "First downstream node",
                    "type": "effect:agent:prompt",
                    "prompt": "First ${{trigger.ticket_id}}",
                },
                {
                    "id": "effect-b",
                    "display_name": "Second downstream node",
                    "type": "effect:agent:prompt",
                    "prompt": "Second ${{trigger.ticket_id}}",
                },
            ],
            "edges": [
                {"source_id": "trigger-1", "target_id": "effect-a"},
                {"source_id": "trigger-1", "target_id": "effect-b"},
            ],
        }
    )

    assert [effect.id for effect in config.effects] == ["effect-a", "effect-b"]
    assert [(edge.source_id, edge.target_id) for edge in config.edges] == [
        ("trigger-1", "effect-a"),
        ("trigger-1", "effect-b"),
    ]


def test_trigger_orchestrator_worker_global_config_rejects_duplicate_ids_across_graph() -> None:
    with pytest.raises(ValueError, match="Node IDs must be globally unique across triggers, gates, effects, and queries: node-1"):
        TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {
                "triggers": [
                    {
                        "id": "node-1",
                        "display_name": "Every 15 minutes",
                        "type": "trigger:cron",
                        "expression": "*/15 * * * *",
                    },
                    {
                        "id": "trigger-2",
                        "display_name": "Every 30 minutes",
                        "type": "trigger:cron",
                        "expression": "*/30 * * * *",
                    },
                ],
                "effects": [
                    {
                        "id": "node-1",
                        "display_name": "Prompt agent",
                        "type": "effect:agent:prompt",
                        "prompt": "Summarize",
                    }
                ],
                "edges": [{"source_id": "trigger-2", "target_id": "node-1"}],
            }
        )


def test_trigger_orchestrator_worker_global_config_rejects_duplicate_trigger_ids() -> None:
    with pytest.raises(ValueError, match="Node IDs must be globally unique across triggers, gates, effects, and queries: cron-15m"):
        TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {
                "triggers": [
                    {
                        "id": "cron-15m",
                        "display_name": "Every 15 minutes",
                        "type": "trigger:cron",
                        "expression": "*/15 * * * *",
                    },
                    {
                        "id": "cron-15m",
                        "display_name": "Every 30 minutes",
                        "type": "trigger:cron",
                        "expression": "*/30 * * * *",
                    },
                ]
            }
        )


def test_trigger_orchestrator_worker_global_config_rejects_duplicate_effect_ids() -> None:
    with pytest.raises(ValueError, match="Node IDs must be globally unique across triggers, gates, effects, and queries: effect-dup"):
        TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {
                "triggers": [
                    {
                        "id": "trigger-1",
                        "display_name": "Every 15 minutes",
                        "type": "trigger:cron",
                        "expression": "*/15 * * * *",
                    }
                ],
                "effects": [
                    {
                        "id": "effect-dup",
                        "display_name": "Prompt agent",
                        "type": "effect:agent:prompt",
                        "prompt": "Summarize",
                    },
                    {
                        "id": "effect-dup",
                        "display_name": "Prompt agent again",
                        "type": "effect:agent:prompt",
                        "prompt": "Summarize",
                    },
                ],
                "edges": [{"source_id": "trigger-1", "target_id": "effect-dup"}],
            }
        )


def test_trigger_orchestrator_worker_global_config_rejects_unknown_effect_edge_source() -> None:
    with pytest.raises(ValueError, match="Edge endpoints must reference existing node IDs: missing-id"):
        TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {
                "triggers": [
                    {
                        "id": "trigger-1",
                        "display_name": "Every 15 minutes",
                        "type": "trigger:cron",
                        "expression": "*/15 * * * *",
                    }
                ],
                "effects": [
                    {
                        "id": "effect-1",
                        "display_name": "Prompt agent",
                        "type": "effect:agent:prompt",
                        "prompt": "Summarize",
                    }
                ],
                "edges": [{"source_id": "missing-id", "target_id": "effect-1"}],
            }
        )
