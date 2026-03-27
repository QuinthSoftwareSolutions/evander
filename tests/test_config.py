import pytest

from agent_sandbox.config import GatewayConfig


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


def test_directory_scoped_config_flag_defaults_to_disabled() -> None:
    config = GatewayConfig.model_validate({})
    assert config.enable_directory_scoped_config is False
