import pytest

from agent_sandbox.config import (
    AdoWorkerGlobalConfig,
    DiscordIngestWorkerGlobalConfig,
    DiscordWakeWorkerGlobalConfig,
    GatewayConfig,
    ScheduledWakeWorkerGlobalConfig,
    TeamsIngestWorkerGlobalConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from agent_sandbox.events.models import materialize_node_definition
from agent_sandbox.models import SessionManifest, ToolName
from agent_sandbox.policy_config import EffectivePolicyConfig
from agent_sandbox.effects.providers.agent_session import (
    container_worker_guest_http_target_relay_base_url,
    container_worker_guest_npm_registry_url,
    container_worker_guest_pypi_index_url,
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


def test_approval_notification_target_ids_are_normalized_and_deduplicated() -> None:
    payload = {
        "approval_notification_target_ids": [
            " Ops-Primary ",
            "ops-primary",
            "",
            "OPS-Secondary",
            "ops-secondary",
        ]
    }

    gateway_config = GatewayConfig.model_validate(payload)
    policy_config = EffectivePolicyConfig.model_validate(payload)

    assert gateway_config.approval_notification_target_ids == [
        "ops-primary",
        "ops-secondary",
    ]
    assert policy_config.approval_notification_target_ids == [
        "ops-primary",
        "ops-secondary",
    ]


def test_tool_argument_policies_round_trip_through_config_models() -> None:
    payload = {
        "tool_argument_policies": {
            "create_ado_backlog_item": [
                {
                    "id": "ado-item-policy",
                    "name": "ADO backlog item guardrails",
                    "constraints": [
                        {
                            "id": "project",
                            "path": "project",
                            "operator": "fixed",
                            "value": "Example Project",
                        },
                        {
                            "id": "type",
                            "path": "type",
                            "operator": "enum",
                            "values": ["Task", "Product Backlog Item"],
                        },
                        {
                            "id": "title",
                            "path": "title",
                            "operator": "required",
                        },
                        {
                            "id": "iteration",
                            "path": "iteration",
                            "operator": "required",
                        },
                        {
                            "id": "tags",
                            "path": "tags[]",
                            "operator": "denylist",
                            "values": ["blocked-tag"],
                        },
                    ],
                }
            ]
        }
    }

    gateway_config = GatewayConfig.model_validate(payload)
    policy_config = EffectivePolicyConfig.model_validate(payload)
    session_manifest = SessionManifest.model_validate(
        {
            "session_id": "session-1",
            "allowed_tools": [ToolName.CREATE_ADO_BACKLOG_ITEM.value],
            "expires_at": "2030-01-01T00:00:00Z",
            **payload,
        }
    )

    assert gateway_config.model_dump(
        mode="json", include={"tool_argument_policies"}, exclude_defaults=True
    ) == payload
    assert policy_config.model_dump(
        mode="json", include={"tool_argument_policies"}, exclude_defaults=True
    ) == payload
    assert session_manifest.model_dump(
        mode="json", include={"tool_argument_policies"}, exclude_defaults=True
    ) == payload


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "tool_argument_policies": {
                    "not_a_real_tool": [
                        {
                            "id": "rule",
                            "constraints": [
                                {"id": "path", "path": "x", "operator": "required"}
                            ],
                        }
                    ]
                }
            },
            "unknown tool name",
        ),
        (
            {
                "tool_argument_policies": {
                    "create_ado_backlog_item": [
                        {
                            "id": "rule",
                            "constraints": [
                                {"id": "path", "path": "x", "operator": "bogus"}
                            ],
                        }
                    ]
                }
            },
            "operator",
        ),
        (
            {
                "tool_argument_policies": {
                    "create_ado_backlog_item": [
                        {
                            "id": "rule",
                            "constraints": [
                                {"id": "path", "path": "x", "operator": "pattern", "pattern": "("}
                            ],
                        }
                    ]
                }
            },
            "pattern is not a valid regular expression",
        ),
        (
            {
                "tool_argument_policies": {
                    "create_ado_backlog_item": [
                        {
                            "id": "rule",
                            "constraints": [
                                {"id": "path", "path": "x", "operator": "required"},
                                {"id": "path", "path": "y", "operator": "required"},
                            ],
                        }
                    ]
                }
            },
            "duplicate tool argument constraint ids",
        ),
        (
            {
                "tool_argument_policies": {
                    "create_ado_backlog_item": [
                        {
                            "id": "rule",
                            "constraints": [
                                {"id": "path", "path": "x", "operator": "required"}
                            ],
                        },
                        {
                            "id": "rule",
                            "constraints": [
                                {"id": "other", "path": "y", "operator": "required"}
                            ],
                        },
                    ]
                }
            },
            "duplicate tool argument policy rule ids",
        ),
    ],
)
def test_tool_argument_policies_reject_invalid_configs(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        GatewayConfig.model_validate(payload)
    with pytest.raises(ValueError, match=message):
        EffectivePolicyConfig.model_validate(payload)
    with pytest.raises(ValueError, match=message):
        SessionManifest.model_validate(
            {
                "session_id": "session-1",
                "allowed_tools": [ToolName.CREATE_ADO_BACKLOG_ITEM.value],
                "expires_at": "2030-01-01T00:00:00Z",
                **payload,
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


def test_teams_graph_access_token_env_defaults_to_compatibility_name() -> None:
    config = GatewayConfig.model_validate({})

    assert config.teams_graph_access_token_env == "GRAPH_TEAMS_ACCESS_TOKEN"


def test_read_reaction_emoji_defaults_to_eyes_for_discord_and_teams() -> None:
    config = GatewayConfig.model_validate({})

    assert config.discord_read_reaction_emoji == "👀"
    assert config.teams_read_reaction_emoji == "👀"


def test_read_reaction_emoji_fields_normalize_unicode_and_trim_whitespace() -> None:
    config = GatewayConfig.model_validate(
        {
            "discord_read_reaction_emoji": " e\u0301 ",
            "teams_read_reaction_emoji": " \u212b ",
        }
    )

    assert config.discord_read_reaction_emoji == "é"
    assert config.teams_read_reaction_emoji == "Å"


@pytest.mark.parametrize("field_name", ["discord_read_reaction_emoji", "teams_read_reaction_emoji"])
def test_read_reaction_emoji_fields_reject_empty_values_with_field_name(field_name: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        GatewayConfig.model_validate({field_name: "   "})


def test_teams_delegated_scopes_default_includes_channel_and_chat_access() -> None:
    config = GatewayConfig.model_validate({})

    assert config.teams_delegated_scopes == [
        "offline_access",
        "https://graph.microsoft.com/ChannelMessage.Read.All",
        "https://graph.microsoft.com/ChannelMessage.Send",
        "https://graph.microsoft.com/Chat.ReadWrite",
    ]


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("gateway_url", " https://example.test/mcp/ "),
        ("discord_api_base_url", " https://discord.com/api/v10/ "),
        ("teams_graph_api_base_url", " https://graph.microsoft.com/v1.0/ "),
        ("container_worker_guest_openai_base_url", " http://worker-relay.test:11434/v1/ "),
        ("container_worker_guest_gateway_url", " http://worker-gateway.test:8000/mcp/ "),
        (
            "container_worker_guest_http_target_relay_base_url",
            " http://worker-http-target.test:11777/ ",
        ),
        ("container_worker_guest_nuget_relay_base_url", " http://worker-nuget.test:11666/ "),
        (
            "container_worker_guest_pypi_index_url",
            " http://nexus.test:8081/repository/pypi-all/simple/ ",
        ),
        (
            "container_worker_guest_npm_registry_url",
            " http://nexus.test:8081/repository/npm-all/ ",
        ),
        ("openai_api_base_url", " https://api.openai.com/v1/ "),
        ("qdrant_base_url", " http://127.0.0.1:6333/ "),
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
        ("container_worker_guest_openai_base_url", "worker-relay.test:11434/v1"),
        ("container_worker_guest_gateway_url", "ftp://worker-gateway.test/mcp"),
        ("container_worker_guest_http_target_relay_base_url", "worker-http-target.test:11777"),
        ("container_worker_guest_nuget_relay_base_url", ""),
        ("container_worker_guest_pypi_index_url", "nexus.test:8081/repository/pypi-all/simple"),
        ("container_worker_guest_npm_registry_url", "ftp://nexus.test/repository/npm-all/"),
        ("openai_api_base_url", "not-a-url"),
        ("qdrant_base_url", "qdrant.local:6333"),
        ("nuget_public_feed_base_url", "://missing-scheme"),
        ("nuget_azure_feed_base_url", ""),
    ],
)
def test_http_url_fields_reject_invalid_values_with_field_name(field_name: str, value: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        GatewayConfig.model_validate({field_name: value})


def test_bind_network_cidr_trims_whitespace_and_normalizes() -> None:
    config = GatewayConfig.model_validate({"bind_network_cidr": " 192.168.250.0/24 "})
    assert str(config.bind_network_cidr) == "192.168.250.0/24"


def test_container_worker_package_cache_urls_derive_from_gateway_host() -> None:
    config = GatewayConfig.model_validate(
        {
            "gateway_url": "http://10.20.30.40:8000/mcp",
            "openai_relay_port": 12000,
            "nuget_relay_port": 13000,
            "http_target_relay_port": 14000,
        }
    )

    assert (
        container_worker_guest_http_target_relay_base_url(config)
        == "http://10.20.30.40:14000"
    )
    assert (
        container_worker_guest_pypi_index_url(config)
        == "http://10.20.30.40:8081/repository/pypi-all/simple"
    )
    assert (
        container_worker_guest_npm_registry_url(config)
        == "http://10.20.30.40:8081/repository/npm-all/"
    )


def test_container_worker_package_cache_url_derivation_does_not_change_relays() -> None:
    from agent_sandbox.effects.providers.agent_session import (
        container_worker_guest_gateway_url,
        container_worker_guest_nuget_relay_base_url,
        container_worker_guest_openai_base_url,
    )

    config = GatewayConfig.model_validate({"gateway_url": "http://10.20.30.40:8000/mcp"})

    assert container_worker_guest_gateway_url(config) == "http://10.20.30.40:8000/mcp"
    assert container_worker_guest_openai_base_url(config) == "http://10.20.30.40:11434/v1"
    assert container_worker_guest_nuget_relay_base_url(config) == "http://10.20.30.40:11666"
    assert container_worker_guest_http_target_relay_base_url(config) == "http://10.20.30.40:11777"


def test_container_worker_mssql_config_fields_are_validated_and_redacted_by_default() -> None:
    config = GatewayConfig.model_validate(
        {
            "container_worker_mssql_enabled": True,
            "container_worker_mssql_host": " 172.17.0.1 ",
            "container_worker_mssql_port": 1433,
        }
    )

    assert config.container_worker_mssql_enabled is True
    assert config.container_worker_mssql_host == "172.17.0.1"
    assert config.container_worker_mssql_port == 1433
    assert "password" in config.redacted_keys
    assert "connection_string" in config.redacted_keys


def test_semantic_store_config_defaults_and_normalization() -> None:
    config = GatewayConfig.model_validate(
        {
            "qdrant_base_url": " http://127.0.0.1:6333/ ",
            "qdrant_api_key_env": " ",
            "semantic_vector_distance": "cosine",
        }
    )

    assert config.semantic_store_provider == "qdrant"
    assert config.qdrant_base_url == "http://127.0.0.1:6333"
    assert config.qdrant_api_key_env is None
    assert config.semantic_embedding_provider == "openai"
    assert config.semantic_embedding_model == "text-embedding-3-small"
    assert config.semantic_embedding_dimensions == 1536
    assert config.semantic_vector_distance == "Cosine"
    assert "api_key" in config.redacted_keys


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


def test_wake_session_cooldown_path_follows_runtime_dir_default() -> None:
    config = GatewayConfig.model_validate({"runtime_dir": "/tmp/agent-sandbox-runtime"})
    assert (
        str(config.wake_session_cooldown_path)
        == "/tmp/agent-sandbox-runtime/wake-session-cooldown.json"
    )


def test_wake_dispatch_state_path_follows_runtime_dir_default() -> None:
    config = GatewayConfig.model_validate({"runtime_dir": "/tmp/agent-sandbox-runtime"})
    assert (
        str(config.wake_dispatch_state_path)
        == "/tmp/agent-sandbox-runtime/wake-dispatch-state.json"
    )


def test_host_agent_control_state_path_follows_runtime_dir_default() -> None:
    config = GatewayConfig.model_validate({"runtime_dir": "/tmp/agent-sandbox-runtime"})
    assert (
        str(config.host_agent_control_state_path)
        == "/tmp/agent-sandbox-runtime/host-agent-control-state.json"
    )


def test_wake_poll_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="wake_poll_interval_seconds"):
        GatewayConfig.model_validate({"wake_poll_interval_seconds": 0})


def test_discord_wake_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = DiscordWakeWorkerGlobalConfig.model_validate(
        {"runtime_dir": "/tmp/agent-sandbox-runtime"}
    )
    assert str(config.lock_path) == "/tmp/agent-sandbox-runtime/discord-wake.lock"
    assert (
        str(config.session_cooldown_path)
        == "/tmp/agent-sandbox-runtime/discord-wake-session-cooldown.json"
    )
    assert str(config.wake_state_path) == "/tmp/agent-sandbox-runtime/discord-wake-state.json"
    assert config.directories == []
    assert config.backoff_seconds == 60.0


def test_discord_wake_worker_global_config_directory_paths_are_normalized() -> None:
    config = DiscordWakeWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/agent-sandbox-runtime",
            "directories": [" root/team-a ", "root/team-a/subdir", "  "],
        }
    )
    assert config.directories == ["root/team-a", "root/team-a/subdir"]


def test_scheduled_wake_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = ScheduledWakeWorkerGlobalConfig.model_validate(
        {"runtime_dir": "/tmp/agent-sandbox-runtime"}
    )
    assert str(config.lock_path) == "/tmp/agent-sandbox-runtime/scheduled-wake.lock"
    assert config.backoff_seconds == 60.0
    assert (
        str(config.session_cooldown_path)
        == "/tmp/agent-sandbox-runtime/scheduled-wake-session-cooldown.json"
    )
    assert str(config.wake_state_path) == "/tmp/agent-sandbox-runtime/scheduled-wake-state.json"
    assert config.directories == []
    assert config.backoff_seconds == 60.0


def test_scheduled_wake_worker_global_config_directory_paths_are_normalized() -> None:
    config = ScheduledWakeWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/agent-sandbox-runtime",
            "directories": [" root/team-a ", "root/team-a/subdir", "  "],
        }
    )
    assert config.directories == ["root/team-a", "root/team-a/subdir"]


def test_discord_ingest_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = DiscordIngestWorkerGlobalConfig.model_validate(
        {"runtime_dir": "/tmp/agent-sandbox-runtime"}
    )
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


def test_teams_ingest_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = TeamsIngestWorkerGlobalConfig.model_validate(
        {"runtime_dir": "/tmp/agent-sandbox-runtime"}
    )
    assert str(config.lock_path) == "/tmp/agent-sandbox-runtime/teams-ingest.lock"
    assert config.directories == []


def test_teams_ingest_worker_global_config_directory_paths_are_normalized() -> None:
    config = TeamsIngestWorkerGlobalConfig.model_validate(
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


def test_teams_delegated_credential_store_path_follows_runtime_dir_default() -> None:
    config = GatewayConfig.model_validate({"runtime_dir": "/tmp/agent-sandbox-runtime"})
    assert str(config.teams_delegated_credential_store_path) == (
        "/tmp/agent-sandbox-runtime/teams-delegated-credentials.json"
    )


def test_teams_backend_accepts_delegated_graph_api_when_identity_is_bound() -> None:
    config = GatewayConfig.model_validate(
        {
            "teams_backend": "delegated_graph_api",
            "teams_delegated_identity_id": " team-a ",
            "teams_delegated_client_id": " client-123 ",
        }
    )

    assert config.teams_backend == "delegated_graph_api"
    assert config.teams_delegated_identity_id == "team-a"
    assert config.teams_delegated_client_id == "client-123"


def test_trigger_orchestrator_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {"runtime_dir": "/tmp/agent-sandbox-runtime"}
    )
    assert str(config.lock_path) == "/tmp/agent-sandbox-runtime/trigger-orchestrator.lock"
    assert (
        str(config.event_queue_store_path)
        == "/tmp/agent-sandbox-runtime/trigger-orchestrator-events.json"
    )
    assert config.orchestration_store_backend == "sqlite"
    assert str(config.orchestration_store_path) == "/tmp/agent-sandbox-runtime/orchestration.db"
    assert config.orchestration_retention_days == 30
    assert (
        str(config.event_queue_cursor_store_path)
        == "/tmp/agent-sandbox-runtime/trigger-orchestrator-cursors.json"
    )
    assert (
        str(config.workflow_event_journal_path)
        == "/tmp/agent-sandbox-runtime/workflow-events.jsonl"
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
    assert (
        str(config.workflow_config_snapshot_store_path)
        == "/tmp/agent-sandbox-runtime/workflow-config-snapshots.json"
    )
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
                        "session_id": {"type": "string"},
                        "labels": {"type": "object"},
                        "success": {"type": "boolean"},
                        "message": {"type": "string"},
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


def test_trigger_orchestrator_switch_gate_accepts_cases_and_duplicate_ports() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/agent-sandbox-runtime",
            "gates": [
                {
                    "id": "gate-switch",
                    "display_name": "Route by state",
                    "type": "gate:switch",
                    "default_port": "fallback",
                    "cases": [
                        {
                            "port": "ready",
                            "label": "Ready path",
                            "when": {
                                "all": [
                                    {"left": "${{trigger-1.state}}", "equals": "ready"}
                                ]
                            },
                        },
                        {
                            "port": "ready",
                            "match": {"state": "blocked"},
                        },
                    ],
                }
            ],
        }
    )

    gate = config.gates[0]
    assert gate.type == "gate:switch"
    assert gate.default_port == "fallback"
    assert [case["port"] for case in gate.config["cases"]] == ["ready", "ready"]

    materialized = materialize_node_definition(gate)
    assert [case.port for case in materialized.cases] == ["ready", "ready"]
    assert materialized.cases[0].label == "Ready path"
    assert materialized.cases[0].when == {
        "all": [{"left": "${{trigger-1.state}}", "equals": "ready"}]
    }
    assert materialized.cases[1].match == {"state": "blocked"}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "runtime_dir": "/tmp/agent-sandbox-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "cases": [],
                    }
                ],
            },
            "gate:switch requires at least one case",
        ),
        (
            {
                "runtime_dir": "/tmp/agent-sandbox-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "cases": [
                            {
                                "port": "   ",
                                "when": {
                                    "all": [
                                        {"left": "${{trigger-1.state}}", "equals": "ready"}
                                    ]
                                },
                            }
                        ],
                    }
                ],
            },
            "gate:switch case port must be a non-empty string",
        ),
        (
            {
                "runtime_dir": "/tmp/agent-sandbox-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "default_port": "   ",
                        "cases": [
                            {
                                "port": "ready",
                                "when": {
                                    "all": [
                                        {"left": "${{trigger-1.state}}", "equals": "ready"}
                                    ]
                                },
                            }
                        ],
                    }
                ],
            },
            "gate:switch default_port must be a non-empty string",
        ),
        (
            {
                "runtime_dir": "/tmp/agent-sandbox-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "cases": [
                            {
                                "port": "ready",
                                "when": {
                                    "all": [
                                        {"left": "${{trigger-1.state}}", "equals": "ready"}
                                    ]
                                },
                                "match": {"state": "ready"},
                            }
                        ],
                    }
                ],
            },
            "mutually exclusive",
        ),
        (
            {
                "runtime_dir": "/tmp/agent-sandbox-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "cases": [{"port": "ready"}],
                    }
                ],
            },
            "requires exactly one of when or match",
        ),
    ],
)
def test_trigger_orchestrator_switch_gate_rejects_invalid_cases(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        TriggerOrchestratorWorkerGlobalConfig.model_validate(payload)


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
                    "session_id": "session-1",
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


def test_trigger_orchestrator_await_agent_gate_accepts_linear_flow_without_completion_trigger() -> (
    None
):
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
                    "session_id": "session-1",
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


def test_trigger_orchestrator_for_gate_accepts_body_done_ports_and_cycle_return() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/agent-sandbox-runtime",
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"steps": {"type": "array"}},
                }
            ],
            "gates": [
                {
                    "id": "for-1",
                    "display_name": "Iterate steps",
                    "type": "gate:for",
                    "items": "${{trigger-1.steps}}",
                }
            ],
            "effects": [
                {
                    "id": "effect-body",
                    "display_name": "Body",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "echo body",
                },
                {
                    "id": "effect-done",
                    "display_name": "Done",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "echo done",
                },
            ],
            "edges": [
                {"source_id": "trigger-1", "target_id": "for-1"},
                {"source_id": "for-1", "source_port": "body", "target_id": "effect-body"},
                {"source_id": "for-1", "source_port": "done", "target_id": "effect-done"},
                {"source_id": "effect-body", "target_id": "for-1", "allow_cycle": True},
            ],
        }
    )

    assert config.gates[0].type == "gate:for"


def test_trigger_orchestrator_for_gate_allows_missing_done_edge_in_core_validation() -> None:
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
                    "id": "for-1",
                    "display_name": "Iterate steps",
                    "type": "gate:for",
                    "items": ["a", "b"],
                }
            ],
            "effects": [
                {
                    "id": "effect-body",
                    "display_name": "Body",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "echo body",
                }
            ],
            "edges": [
                {"source_id": "trigger-1", "target_id": "for-1"},
                {"source_id": "for-1", "source_port": "body", "target_id": "effect-body"},
            ],
        }
    )

    assert config.gates[0].type == "gate:for"


def test_trigger_orchestrator_allows_orphaned_draft_subgraphs() -> None:
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
                    "id": "for-1",
                    "display_name": "Draft loop",
                    "type": "gate:for",
                    "items": ["a", "b"],
                }
            ],
            "effects": [
                {
                    "id": "effect-body",
                    "display_name": "Body",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "echo body",
                }
            ],
            "edges": [],
        }
    )

    assert config.gates[0].type == "gate:for"


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


def test_trigger_orchestrator_worker_global_config_accepts_multiple_effect_edges_from_one_source() -> (
    None
):
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
    with pytest.raises(
        ValueError,
        match="Node IDs must be globally unique across triggers, gates, effects, and queries: node-1",
    ):
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
    with pytest.raises(
        ValueError,
        match="Node IDs must be globally unique across triggers, gates, effects, and queries: cron-15m",
    ):
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
    with pytest.raises(
        ValueError,
        match="Node IDs must be globally unique across triggers, gates, effects, and queries: effect-dup",
    ):
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


def test_trigger_orchestrator_worker_global_config_defaults_provider_profile_to_agent_default() -> (
    None
):
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate({})

    assert config.provider_profile == "profiles/agent-default"
    assert config.provider_include_builtin is True


def test_trigger_orchestrator_worker_global_config_supports_core_minimal_profile() -> None:
    config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {"provider_loader": {"provider_profile": "profiles/core-minimal"}}
    )

    assert config.provider_profile == "profiles/core-minimal"
    assert config.provider_include_builtin is False


def test_trigger_orchestrator_worker_global_config_rejects_unknown_provider_profile() -> None:
    with pytest.raises(ValueError, match="Unknown provider loader profile"):
        TriggerOrchestratorWorkerGlobalConfig.model_validate(
            {"provider_loader": {"provider_profile": "profiles/unknown"}}
        )


def test_trigger_orchestrator_worker_global_config_rejects_unknown_effect_edge_source() -> None:
    with pytest.raises(
        ValueError, match="Edge endpoints must reference existing node IDs: missing-id"
    ):
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
