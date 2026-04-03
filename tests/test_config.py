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
    assert config.triggers == []


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
