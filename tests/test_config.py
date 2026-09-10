from config_helpers import canonical_worker_config

import pytest

from evander.config import (
    AdoWorkerGlobalConfig,
    DiscordIngestWorkerGlobalConfig,
    DiscordWakeWorkerGlobalConfig,
    GatewayConfig,
    ScheduledWakeWorkerGlobalConfig,
    TeamsIngestWorkerGlobalConfig,
)
from evander.container_worker_images import (
    normalize_container_worker_images,
    resolve_container_worker_image_selector,
)
from evander.host_config import HostConfig, build_runtime_gateway_config
from evander.events.models import materialize_node_definition
from evander.models import (
    ApprovalRequest,
    SessionManifest,
    SessionToolApprovalLease,
    ToolName,
)
from evander.policy_config import EffectivePolicyConfig
from evander.effects.providers.agent_session import (
    container_worker_guest_anthropic_base_url,
    container_worker_guest_http_target_browser_proxy_url,
    container_worker_guest_http_target_relay_base_url,
    container_worker_guest_npm_registry_url,
    container_worker_guest_pypi_index_url,
)
from config_helpers import canonical_workflow_payload


def _validate_trigger_config(payload):
    return canonical_worker_config(canonical_workflow_payload(payload))


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


def test_approval_notification_target_ids_use_consistent_normalization_across_models() -> None:
    session = SessionManifest.model_validate(
        {
            "session_id": "session-1",
            "directory_path": "root",
            "allowed_tools": ["approval_submit"],
            "approval_notification_target_ids": [" Ops-Primary ", "", "OPS-Primary", "ops-backup"],
            "expires_at": "2030-01-01T00:00:00Z",
        }
    )
    lease = SessionToolApprovalLease.model_validate(
        {
            "lease_id": "lease-1",
            "session_id": "session-1",
            "workflow_run_id": "run-1",
            "tools": ["approval_submit"],
            "approval_notification_target_ids": [
                " Ops-Primary ",
                "ops-primary",
                "ops-backup",
            ],
            "expires_at": "2030-01-01T00:00:00Z",
        }
    )
    request = ApprovalRequest.model_validate(
        {
            "request_id": "approval-1",
            "session_id": "session-1",
            "action_type": "tool-call",
            "payload_digest": "digest",
            "human_summary": "summary",
            "notification_target_ids": [
                " Ops-Primary ",
                "ops-primary",
                "ops-backup",
            ],
            "expires_at": "2030-01-01T00:00:00Z",
        }
    )

    assert session.approval_notification_target_ids == ["ops-primary", "ops-backup"]
    assert lease.approval_notification_target_ids == ["ops-primary", "ops-backup"]
    assert request.notification_target_ids == ["ops-primary", "ops-backup"]


def test_container_worker_images_are_normalized_and_round_trip_through_config_model() -> None:
    payload = {
        "container_worker_default_image": "runner-base:latest",
        "container_worker_images": {
            " default ": " runner-base:latest ",
            "dotnet-playwright": " runner-dotnet-playwright:2026-06-05 ",
        },
    }

    config = GatewayConfig.model_validate(payload)

    assert config.container_worker_images == {
        "default": "runner-base:latest",
        "dotnet-playwright": "runner-dotnet-playwright:2026-06-05",
    }
    assert config.model_dump(mode="json")["container_worker_images"] == {
        "default": "runner-base:latest",
        "dotnet-playwright": "runner-dotnet-playwright:2026-06-05",
    }
    assert GatewayConfig.model_validate(config.model_dump(mode="json")).container_worker_images == {
        "default": "runner-base:latest",
        "dotnet-playwright": "runner-dotnet-playwright:2026-06-05",
    }

    assert resolve_container_worker_image_selector(
        None,
        default_image=config.container_worker_default_image,
        images=config.container_worker_images,
    ) == {"image": "runner-base:latest", "image_alias": None}
    assert resolve_container_worker_image_selector(
        "dotnet-playwright",
        default_image=config.container_worker_default_image,
        images=config.container_worker_images,
    ) == {
        "image": "runner-dotnet-playwright:2026-06-05",
        "image_alias": "dotnet-playwright",
    }
    assert resolve_container_worker_image_selector(
        "runner-base:latest",
        default_image=config.container_worker_default_image,
        images=config.container_worker_images,
    ) == {"image": "runner-base:latest", "image_alias": None}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"container_worker_images": {"": "runner:latest"}},
            "aliases cannot be empty",
        ),
        (
            {"container_worker_images": {"bad alias": "runner:latest"}},
            "must match",
        ),
        (
            {"container_worker_images": {"default": ""}},
            "cannot be empty",
        ),
        (
            {"container_worker_images": {"default": 123}},
            "must be strings",
        ),
        (
            {
                "container_worker_images": {
                    "default": "runner:latest",
                    " default ": "runner-alt:latest",
                }
            },
            "duplicate container_worker_images alias",
        ),
    ],
)
def test_container_worker_images_reject_invalid_catalog_entries(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        GatewayConfig.model_validate(payload)


def test_container_worker_image_selector_rejects_unknown_selector() -> None:
    with pytest.raises(ValueError, match="unknown container worker image selector"):
        resolve_container_worker_image_selector(
            "unknown",
            default_image="runner-base:latest",
            images=normalize_container_worker_images(
                {"default": "runner-base:latest", "dotnet": "runner-dotnet:latest"}
            ),
        )


def test_container_worker_git_allowed_from_is_normalized() -> None:
    config = GatewayConfig(container_worker_git_allowed_from=" 10.2.1.4/32 ")

    assert config.container_worker_git_allowed_from == "10.2.1.4/32"


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

    assert (
        gateway_config.model_dump(
            mode="json", include={"tool_argument_policies"}, exclude_defaults=True
        )
        == payload
    )
    assert (
        policy_config.model_dump(
            mode="json", include={"tool_argument_policies"}, exclude_defaults=True
        )
        == payload
    )
    assert (
        session_manifest.model_dump(
            mode="json", include={"tool_argument_policies"}, exclude_defaults=True
        )
        == payload
    )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "tool_argument_policies": {
                    "not a real tool": [
                        {
                            "id": "rule",
                            "constraints": [{"id": "path", "path": "x", "operator": "required"}],
                        }
                    ]
                }
            },
            "valid MCP tool name",
        ),
        (
            {
                "tool_argument_policies": {
                    "create_ado_backlog_item": [
                        {
                            "id": "rule",
                            "constraints": [{"id": "path", "path": "x", "operator": "bogus"}],
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
                            "constraints": [{"id": "path", "path": "x", "operator": "required"}],
                        },
                        {
                            "id": "rule",
                            "constraints": [{"id": "other", "path": "y", "operator": "required"}],
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


def test_discord_persona_webhook_cache_limit_is_bounded() -> None:
    assert GatewayConfig.model_validate({}).discord_persona_webhook_cache_limit == 10
    assert (
        GatewayConfig.model_validate(
            {"discord_persona_webhook_cache_limit": 3}
        ).discord_persona_webhook_cache_limit
        == 3
    )
    with pytest.raises(ValueError, match="discord_persona_webhook_cache_limit"):
        GatewayConfig.model_validate({"discord_persona_webhook_cache_limit": 11})


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


def test_communication_artifact_link_defaults_are_control_plane_scoped() -> None:
    config = GatewayConfig.model_validate({})

    assert config.communication_artifact_public_base_url is None
    assert (
        config.communication_artifact_link_secret_env
        == "EVANDER_COMMUNICATION_ARTIFACT_LINK_SECRET"
    )
    assert config.communication_artifact_link_ttl_seconds == 7 * 24 * 60 * 60


def test_openai_provider_defaults_remain_public_mode() -> None:
    config = GatewayConfig.model_validate({})

    assert config.model_provider_mode == "openai_public"
    assert config.openai_provider_mode == "openai_public"
    assert config.azure_openai_endpoint is None
    assert config.azure_openai_auth_mode == "api_key"
    assert config.azure_openai_api_key_env is None
    assert config.azure_openai_api_version == "preview"
    assert config.azure_openai_model_deployments == {}
    assert config.local_openai_api_base_url == "http://127.0.0.1:11434/v1"
    assert config.local_openai_api_key == "ollama"
    assert config.local_openai_provider_name == "local_openai"
    assert config.anthropic_api_base_url == "https://api.anthropic.com"
    assert config.anthropic_api_key_env == "ANTHROPIC_API_KEY"
    assert config.anthropic_api_version == "2023-06-01"
    assert config.openrouter_api_base_url == "https://openrouter.ai/api/v1"
    assert config.openrouter_api_key_env == "OPENROUTER_API_KEY"
    assert config.openrouter_model_routes == {}


def test_openrouter_provider_config_normalizes_exact_pinned_routes() -> None:
    config = GatewayConfig.model_validate(
        {
            "model_provider_mode": " openrouter ",
            "openrouter_api_base_url": " https://openrouter.example/api/v1/ ",
            "openrouter_api_key_env": " OPENROUTER_TEST_KEY ",
            "openrouter_model_routes": {
                " openai/gpt-5.4 ": " openai ",
                "anthropic/claude-sonnet-4.6": "anthropic",
            },
        }
    )

    assert config.model_provider_mode == "openrouter"
    assert config.openrouter_api_base_url == "https://openrouter.example/api/v1"
    assert config.openrouter_api_key_env == "OPENROUTER_TEST_KEY"
    assert config.openrouter_model_routes == {
        "openai/gpt-5.4": "openai",
        "anthropic/claude-sonnet-4.6": "anthropic",
    }


@pytest.mark.parametrize(
    "routes",
    [
        [],
        {"": "openai"},
        {"openai/*": "openai"},
        {"~openai/gpt-5.4": "openai"},
        {"openrouter/auto": "openrouter"},
        {"openrouter/free": "openrouter"},
        {"openai/gpt-5.4:online": "openai"},
        {"openai/gpt-5.4@preset/test": "openai"},
        {"openai/gpt-5.4": ["openai", "azure"]},
        {"openai/gpt-5.4": {"provider": "openai"}},
        {"openai/gpt-5.4": ""},
    ],
)
def test_openrouter_provider_config_rejects_unpinned_routes(routes: object) -> None:
    with pytest.raises(ValueError, match="openrouter_model_routes"):
        GatewayConfig.model_validate({"openrouter_model_routes": routes})


def test_gateway_config_scheduled_wake_prefix_defaults_to_empty_and_uses_canonical_key() -> None:
    config = GatewayConfig.model_validate({})
    dumped = config.model_dump(mode="json")

    assert config.scheduled_wake_prefix_text == ""
    assert config.wake_scheduled_prefix_text == ""
    assert dumped["scheduled_wake_prefix_text"] == ""
    assert "wake_scheduled_prefix_text" not in dumped

    legacy_config = GatewayConfig.model_validate(
        {"wake_scheduled_prefix_text": "Legacy scheduled wake prefix."}
    )
    legacy_dumped = legacy_config.model_dump(mode="json")

    assert legacy_config.scheduled_wake_prefix_text == "Legacy scheduled wake prefix."
    assert legacy_config.wake_scheduled_prefix_text == "Legacy scheduled wake prefix."
    assert legacy_dumped["scheduled_wake_prefix_text"] == "Legacy scheduled wake prefix."
    assert "wake_scheduled_prefix_text" not in legacy_dumped

    canonical_config = GatewayConfig.model_validate(
        {"scheduled_wake_prefix_text": "Canonical scheduled wake prefix."}
    )
    canonical_dumped = canonical_config.model_dump(mode="json")

    assert canonical_config.scheduled_wake_prefix_text == "Canonical scheduled wake prefix."
    assert canonical_config.wake_scheduled_prefix_text == "Canonical scheduled wake prefix."
    assert canonical_dumped["scheduled_wake_prefix_text"] == "Canonical scheduled wake prefix."
    assert "wake_scheduled_prefix_text" not in canonical_dumped


def test_model_provider_mode_accepts_legacy_openai_provider_mode_alias() -> None:
    config = GatewayConfig.model_validate({"openai_provider_mode": " anthropic "})
    dumped = config.model_dump(mode="json")

    assert config.model_provider_mode == "anthropic"
    assert config.openai_provider_mode == "anthropic"
    assert dumped["model_provider_mode"] == "anthropic"
    assert "openai_provider_mode" not in dumped


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("gateway_url", " https://example.test/mcp/ "),
        ("discord_api_base_url", " https://discord.com/api/v10/ "),
        ("teams_graph_api_base_url", " https://graph.microsoft.com/v1.0/ "),
        ("container_worker_guest_openai_base_url", " http://worker-relay.test:11434/v1/ "),
        ("container_worker_guest_anthropic_base_url", " http://worker-relay.test:11434/ "),
        ("container_worker_guest_gateway_url", " http://worker-gateway.test:8000/mcp/ "),
        (
            "container_worker_guest_http_target_relay_base_url",
            " http://worker-http-target.test:11777/ ",
        ),
        (
            "container_worker_guest_http_target_browser_proxy_url",
            " http://worker-http-target.test:11778/ ",
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
        ("azure_openai_endpoint", " https://example-resource.openai.azure.com/v1/ "),
        ("openai_api_base_url", " https://api.openai.com/v1/ "),
        ("local_openai_api_base_url", " http://127.0.0.1:11434/v1/ "),
        ("anthropic_api_base_url", " https://api.anthropic.com/ "),
        ("qdrant_base_url", " http://127.0.0.1:6333/ "),
        ("nuget_public_feed_base_url", " https://api.nuget.org/ "),
        (
            "nuget_azure_feed_base_url",
            " https://pkgs.dev.azure.com/example/example/_packaging/example/nuget/ ",
        ),
        (
            "npm_audit_facade_audit_registry_url",
            " https://registry.npmjs.org/ ",
        ),
        (
            "npm_audit_facade_package_registry_url",
            " http://nexus.test:8081/repository/npm-all/ ",
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
        ("container_worker_guest_anthropic_base_url", "worker-relay.test:11434"),
        ("container_worker_guest_gateway_url", "ftp://worker-gateway.test/mcp"),
        ("container_worker_guest_http_target_relay_base_url", "worker-http-target.test:11777"),
        (
            "container_worker_guest_http_target_browser_proxy_url",
            "worker-http-target.test:11778",
        ),
        ("container_worker_guest_nuget_relay_base_url", ""),
        ("container_worker_guest_pypi_index_url", "nexus.test:8081/repository/pypi-all/simple"),
        ("container_worker_guest_npm_registry_url", "ftp://nexus.test/repository/npm-all/"),
        ("container_worker_guest_npm_registry_url", "http://facade.test\nfetch-retries=0"),
        ("azure_openai_endpoint", "not-a-url"),
        ("openai_api_base_url", "not-a-url"),
        ("local_openai_api_base_url", "ollama.internal:11434/v1"),
        ("anthropic_api_base_url", "not-a-url"),
        ("qdrant_base_url", "qdrant.local:6333"),
        ("nuget_public_feed_base_url", "://missing-scheme"),
        ("nuget_azure_feed_base_url", ""),
        ("npm_audit_facade_audit_registry_url", "ftp://registry.npmjs.org"),
        ("npm_audit_facade_package_registry_url", ""),
    ],
)
def test_http_url_fields_reject_invalid_values_with_field_name(field_name: str, value: str) -> None:
    with pytest.raises(ValueError, match=field_name):
        GatewayConfig.model_validate({field_name: value})


def test_runtime_gateway_config_carries_npm_audit_facade_bind_settings() -> None:
    host_config = HostConfig(
        runtime_dir="/tmp/evander-runtime",
        npm_audit_facade_host="127.0.0.1",
        npm_audit_facade_port=18181,
    )

    config = build_runtime_gateway_config(host_config)

    assert config.npm_audit_facade_host == "127.0.0.1"
    assert config.npm_audit_facade_port == 18181


def test_runtime_gateway_config_carries_browser_proxy_header_timeout() -> None:
    host_config = HostConfig(
        runtime_dir="/tmp/evander-runtime",
        http_target_browser_proxy_header_timeout_seconds=3.5,
    )

    config = build_runtime_gateway_config(host_config)

    assert config.http_target_browser_proxy_header_timeout_seconds == 3.5
    assert (
        HostConfig.from_gateway_config(config).http_target_browser_proxy_header_timeout_seconds
        == 3.5
    )


def test_azure_openai_provider_config_validates_and_normalizes_full_configuration() -> None:
    config = GatewayConfig.model_validate(
        {
            "model_provider_mode": "azure_openai",
            "azure_openai_endpoint": " https://example-resource.openai.azure.com/ ",
            "azure_openai_api_key_env": " AZURE_OPENAI_API_KEY ",
            "azure_openai_api_version": " 2024-10-21-preview ",
            "azure_openai_model_deployments": {
                " gpt-5.4 ": " deployment-gpt-5-4 ",
                " gpt-4.1-mini ": " deployment-gpt-4-1-mini ",
            },
        }
    )

    assert config.model_provider_mode == "azure_openai"
    assert config.azure_openai_endpoint == "https://example-resource.openai.azure.com"
    assert config.azure_openai_api_key_env == "AZURE_OPENAI_API_KEY"
    assert config.azure_openai_api_version == "2024-10-21-preview"
    assert config.azure_openai_model_deployments == {
        "gpt-5.4": "deployment-gpt-5-4",
        "gpt-4.1-mini": "deployment-gpt-4-1-mini",
    }


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {
                "model_provider_mode": "azure_openai",
                "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
                "azure_openai_model_deployments": {"gpt-5.4": "deployment-gpt-5-4"},
            },
            "azure_openai_endpoint",
        ),
        (
            {
                "model_provider_mode": "azure_openai",
                "azure_openai_endpoint": "https://example-resource.openai.azure.com",
                "azure_openai_model_deployments": {"gpt-5.4": "deployment-gpt-5-4"},
            },
            "azure_openai_api_key_env",
        ),
        (
            {
                "model_provider_mode": "azure_openai",
                "azure_openai_endpoint": "https://example-resource.openai.azure.com",
                "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
            },
            "azure_openai_model_deployments",
        ),
        (
            {
                "model_provider_mode": "azure_openai",
                "azure_openai_endpoint": "https://example-resource.openai.azure.com",
                "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
                "azure_openai_model_deployments": {"gpt-5.4": "deployment-gpt-5-4"},
                "azure_openai_auth_mode": "managed_identity",
            },
            "azure_openai_auth_mode currently supports only 'api_key'",
        ),
    ],
)
def test_azure_openai_provider_config_rejects_missing_fields_and_unsupported_auth_modes(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        GatewayConfig.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"azure_openai_model_deployments": []},
        {"azure_openai_model_deployments": None},
        {"model_provider_mode": "azure_openai", "azure_openai_model_deployments": {"": "x"}},
        {
            "model_provider_mode": "azure_openai",
            "azure_openai_endpoint": "https://example-resource.openai.azure.com",
            "azure_openai_api_key_env": "AZURE_OPENAI_API_KEY",
            "azure_openai_model_deployments": {"gpt-5.4": "   "},
        },
    ],
)
def test_azure_openai_model_deployments_reject_invalid_payloads(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="azure_openai_model_deployments"):
        GatewayConfig.model_validate(payload)


def test_local_openai_provider_config_validates_and_normalizes_full_configuration() -> None:
    config = GatewayConfig.model_validate(
        {
            "model_provider_mode": " local_openai ",
            "openai_relay_host": "192.168.250.1",
            "local_openai_api_base_url": " http://127.0.0.1:11434/v1/ ",
            "local_openai_api_key": " ollama-local ",
            "local_openai_provider_name": " local_truman ",
        }
    )

    assert config.model_provider_mode == "local_openai"
    assert config.openai_provider_mode == "local_openai"
    assert config.local_openai_api_base_url == "http://127.0.0.1:11434/v1"
    assert config.local_openai_api_key == "ollama-local"
    assert config.local_openai_provider_name == "local_truman"


def test_local_openai_provider_config_rejects_default_upstream_when_relay_binds_wildcard() -> None:
    with pytest.raises(ValueError, match="local_openai_api_base_url"):
        GatewayConfig.model_validate({"model_provider_mode": "local_openai"})


def test_local_openai_provider_config_allows_non_conflicting_local_upstream() -> None:
    config = GatewayConfig.model_validate(
        {
            "model_provider_mode": "local_openai",
            "local_openai_api_base_url": "http://127.0.0.1:11435/v1",
        }
    )

    assert config.local_openai_api_base_url == "http://127.0.0.1:11435/v1"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"local_openai_api_key": "   "}, "local_openai_api_key"),
        ({"local_openai_provider_name": "   "}, "local_openai_provider_name"),
    ],
)
def test_local_openai_provider_config_rejects_empty_required_fields(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        GatewayConfig.model_validate(payload)


def test_anthropic_provider_config_validates_and_normalizes_full_configuration() -> None:
    config = GatewayConfig.model_validate(
        {
            "model_provider_mode": "anthropic",
            "anthropic_api_base_url": " https://api.anthropic.example/ ",
            "anthropic_api_key_env": " ANTHROPIC_TEST_KEY ",
            "anthropic_api_version": " 2023-06-01 ",
            "anthropic_relay_timeout_seconds": 120,
        }
    )

    assert config.model_provider_mode == "anthropic"
    assert config.anthropic_api_base_url == "https://api.anthropic.example"
    assert config.anthropic_api_key_env == "ANTHROPIC_TEST_KEY"
    assert config.anthropic_api_version == "2023-06-01"
    assert config.anthropic_relay_timeout_seconds == 120


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"anthropic_api_key_env": "   "}, "anthropic_api_key_env"),
        ({"anthropic_api_version": "   "}, "anthropic_api_version"),
    ],
)
def test_anthropic_provider_config_rejects_empty_required_fields(
    payload: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        GatewayConfig.model_validate(payload)


def test_gateway_config_conversion_preserves_npm_audit_facade_bind_settings() -> None:
    config = GatewayConfig.model_validate(
        {
            "runtime_dir": "/tmp/evander-runtime",
            "npm_audit_facade_host": "127.0.0.1",
            "npm_audit_facade_port": 18181,
        }
    )

    host_config = HostConfig.from_gateway_config(config)

    assert host_config.npm_audit_facade_host == "127.0.0.1"
    assert host_config.npm_audit_facade_port == 18181


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

    assert container_worker_guest_http_target_relay_base_url(config) == "http://10.20.30.40:14000"
    assert (
        container_worker_guest_pypi_index_url(config)
        == "http://10.20.30.40:8081/repository/pypi-all/simple"
    )
    assert (
        container_worker_guest_npm_registry_url(config)
        == "http://10.20.30.40:8081/repository/npm-all/"
    )


def test_container_worker_package_cache_url_derivation_does_not_change_relays() -> None:
    from evander.effects.providers.agent_session import (
        container_worker_guest_gateway_url,
        container_worker_guest_nuget_relay_base_url,
        container_worker_guest_openai_base_url,
    )

    config = GatewayConfig.model_validate({"gateway_url": "http://10.20.30.40:8000/mcp"})

    assert container_worker_guest_gateway_url(config) == "http://10.20.30.40:8000/mcp"
    assert container_worker_guest_openai_base_url(config) == "http://10.20.30.40:11434/v1"
    assert container_worker_guest_anthropic_base_url(config) == "http://10.20.30.40:11434"
    assert container_worker_guest_nuget_relay_base_url(config) == "http://10.20.30.40:11666"
    assert container_worker_guest_http_target_relay_base_url(config) == "http://10.20.30.40:11777"
    assert (
        container_worker_guest_http_target_browser_proxy_url(config)
        == "http://10.20.30.40:11778"
    )

    configured_relay = config.model_copy(
        update={
            "container_worker_guest_http_target_relay_base_url": (
                "http://worker-private.test:11777"
            )
        }
    )
    assert (
        container_worker_guest_http_target_browser_proxy_url(configured_relay)
        == "http://worker-private.test:11778"
    )


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
    config = GatewayConfig.model_validate({"runtime_dir": "/tmp/evander-runtime"})
    assert (
        str(config.wake_session_cooldown_path) == "/tmp/evander-runtime/wake-session-cooldown.json"
    )


def test_wake_dispatch_state_path_follows_runtime_dir_default() -> None:
    config = GatewayConfig.model_validate({"runtime_dir": "/tmp/evander-runtime"})
    assert str(config.wake_dispatch_state_path) == "/tmp/evander-runtime/wake-dispatch-state.json"


def test_host_agent_control_state_path_follows_runtime_dir_default() -> None:
    config = GatewayConfig.model_validate({"runtime_dir": "/tmp/evander-runtime"})
    assert (
        str(config.host_agent_control_state_path)
        == "/tmp/evander-runtime/host-agent-control-state.json"
    )


def test_wake_poll_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="wake_poll_interval_seconds"):
        GatewayConfig.model_validate({"wake_poll_interval_seconds": 0})


def test_wake_completion_confirmation_retry_policy_defaults_are_conservative() -> None:
    config = GatewayConfig.model_validate({})

    assert config.wake_completion_confirmation_retry_max_attempts == 3
    assert config.wake_completion_confirmation_retry_window_seconds == 24 * 60 * 60
    assert config.wake_completion_confirmation_retry_base_backoff_seconds == 60.0
    assert config.wake_completion_confirmation_retry_max_backoff_seconds == 600.0


def test_discord_wake_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = DiscordWakeWorkerGlobalConfig.model_validate({"runtime_dir": "/tmp/evander-runtime"})
    assert str(config.lock_path) == "/tmp/evander-runtime/discord-wake.lock"
    assert (
        str(config.session_cooldown_path)
        == "/tmp/evander-runtime/discord-wake-session-cooldown.json"
    )
    assert str(config.wake_state_path) == "/tmp/evander-runtime/discord-wake-state.json"
    assert config.directories == []
    assert config.backoff_seconds == 60.0


def test_discord_wake_worker_global_config_directory_paths_are_normalized() -> None:
    config = DiscordWakeWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/evander-runtime",
            "directories": [" root/team-a ", "root/team-a/subdir", "  "],
        }
    )
    assert config.directories == ["root/team-a", "root/team-a/subdir"]


def test_scheduled_wake_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = ScheduledWakeWorkerGlobalConfig.model_validate({"runtime_dir": "/tmp/evander-runtime"})
    assert str(config.lock_path) == "/tmp/evander-runtime/scheduled-wake.lock"
    assert config.backoff_seconds == 60.0
    assert (
        str(config.session_cooldown_path)
        == "/tmp/evander-runtime/scheduled-wake-session-cooldown.json"
    )
    assert str(config.wake_state_path) == "/tmp/evander-runtime/scheduled-wake-state.json"
    assert config.directories == []
    assert config.backoff_seconds == 60.0
    assert config.late_threshold_seconds == 300.0
    assert config.completion_confirmation_retry_max_attempts == 3
    assert config.completion_confirmation_retry_window_seconds == 24 * 60 * 60
    assert config.completion_confirmation_retry_base_backoff_seconds == 60.0
    assert config.completion_confirmation_retry_max_backoff_seconds == 600.0
    assert config.scheduled_wake_prefix_text == ""
    assert config.wake_scheduled_prefix_text == ""


def test_scheduled_wake_worker_global_config_scheduled_wake_prefix_uses_canonical_key() -> None:
    config = ScheduledWakeWorkerGlobalConfig.model_validate({})
    dumped = config.model_dump(mode="json")

    assert config.scheduled_wake_prefix_text == ""
    assert config.wake_scheduled_prefix_text == ""
    assert dumped["scheduled_wake_prefix_text"] == ""
    assert "wake_scheduled_prefix_text" not in dumped

    legacy_config = ScheduledWakeWorkerGlobalConfig.model_validate(
        {"wake_scheduled_prefix_text": "Legacy scheduled wake prefix."}
    )
    legacy_dumped = legacy_config.model_dump(mode="json")

    assert legacy_config.scheduled_wake_prefix_text == "Legacy scheduled wake prefix."
    assert legacy_config.wake_scheduled_prefix_text == "Legacy scheduled wake prefix."
    assert legacy_dumped["scheduled_wake_prefix_text"] == "Legacy scheduled wake prefix."
    assert "wake_scheduled_prefix_text" not in legacy_dumped

    canonical_config = ScheduledWakeWorkerGlobalConfig.model_validate(
        {"scheduled_wake_prefix_text": "Canonical scheduled wake prefix."}
    )
    canonical_dumped = canonical_config.model_dump(mode="json")

    assert canonical_config.scheduled_wake_prefix_text == "Canonical scheduled wake prefix."
    assert canonical_config.wake_scheduled_prefix_text == "Canonical scheduled wake prefix."
    assert canonical_dumped["scheduled_wake_prefix_text"] == "Canonical scheduled wake prefix."
    assert "wake_scheduled_prefix_text" not in canonical_dumped


def test_scheduled_wake_worker_global_config_directory_paths_are_normalized() -> None:
    config = ScheduledWakeWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/evander-runtime",
            "directories": [" root/team-a ", "root/team-a/subdir", "  "],
        }
    )
    assert config.directories == ["root/team-a", "root/team-a/subdir"]


def test_scheduled_wake_worker_global_config_accepts_retry_policy_overrides() -> None:
    config = ScheduledWakeWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/evander-runtime",
            "completion_confirmation_retry_max_attempts": 5,
            "completion_confirmation_retry_window_seconds": 7200,
            "completion_confirmation_retry_base_backoff_seconds": 90,
            "completion_confirmation_retry_max_backoff_seconds": 900,
        }
    )

    assert config.completion_confirmation_retry_max_attempts == 5
    assert config.completion_confirmation_retry_window_seconds == 7200.0
    assert config.completion_confirmation_retry_base_backoff_seconds == 90.0
    assert config.completion_confirmation_retry_max_backoff_seconds == 900.0


def test_discord_ingest_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = DiscordIngestWorkerGlobalConfig.model_validate({"runtime_dir": "/tmp/evander-runtime"})
    assert str(config.lock_path) == "/tmp/evander-runtime/discord-ingest.lock"
    assert config.directories == []


def test_discord_ingest_worker_global_config_directory_paths_are_normalized() -> None:
    config = DiscordIngestWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/evander-runtime",
            "directories": [" root/team-a ", "root/team-a/subdir", "  "],
        }
    )
    assert config.directories == ["root/team-a", "root/team-a/subdir"]


def test_teams_ingest_worker_global_config_defaults_follow_runtime_dir() -> None:
    config = TeamsIngestWorkerGlobalConfig.model_validate({"runtime_dir": "/tmp/evander-runtime"})
    assert str(config.lock_path) == "/tmp/evander-runtime/teams-ingest.lock"
    assert config.directories == []
    assert config.max_concurrent_target_polls == 1
    assert config.publish_trigger_events_directly is True
    assert config.target_transient_backoff_initial_seconds == 5.0
    assert config.target_transient_backoff_max_seconds == 30.0
    assert config.change_notifications_enabled is False
    assert config.change_notification_public_base_url is None
    assert config.change_notification_listener_host == "127.0.0.1"
    assert config.change_notification_listener_port == 11882
    assert config.change_notification_subscription_lifetime_hours == 48.0
    assert config.change_notification_renewal_margin_hours == 24.0
    assert config.change_notification_reconciliation_poll_interval_seconds == 60.0


def test_teams_change_notifications_require_https_public_callback() -> None:
    with pytest.raises(ValueError, match="change_notification_public_base_url is required"):
        TeamsIngestWorkerGlobalConfig.model_validate({"change_notifications_enabled": True})
    with pytest.raises(ValueError, match="must use https"):
        TeamsIngestWorkerGlobalConfig.model_validate(
            {
                "change_notifications_enabled": True,
                "change_notification_public_base_url": "http://notifications.example.test",
            }
        )


def test_teams_change_notification_listener_must_be_loopback() -> None:
    with pytest.raises(ValueError, match="must be a loopback address"):
        TeamsIngestWorkerGlobalConfig.model_validate(
            {"change_notification_listener_host": "0.0.0.0"}
        )

    config = TeamsIngestWorkerGlobalConfig.model_validate(
        {"change_notification_listener_host": " ::1 "}
    )

    assert config.change_notification_listener_host == "::1"


@pytest.mark.parametrize("value", [0, 33])
def test_teams_ingest_worker_global_config_bounds_concurrent_target_polls(value: int) -> None:
    with pytest.raises(ValueError, match="max_concurrent_target_polls"):
        TeamsIngestWorkerGlobalConfig.model_validate({"max_concurrent_target_polls": value})


def test_teams_ingest_worker_global_config_rejects_inverted_transient_backoff() -> None:
    with pytest.raises(ValueError, match="target_transient_backoff_max_seconds"):
        TeamsIngestWorkerGlobalConfig.model_validate(
            {
                "target_transient_backoff_initial_seconds": 30,
                "target_transient_backoff_max_seconds": 5,
            }
        )


def test_teams_ingest_worker_global_config_directory_paths_are_normalized() -> None:
    config = TeamsIngestWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": "/tmp/evander-runtime",
            "directories": [" root/team-a ", "root/team-a/subdir", "  "],
        }
    )
    assert config.directories == ["root/team-a", "root/team-a/subdir"]


def test_ado_worker_global_config_is_canonical_and_rejects_retired_fields() -> None:
    config = AdoWorkerGlobalConfig.model_validate({})
    assert config.directories == []
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        AdoWorkerGlobalConfig.model_validate({"include_comments": True})


def test_directory_scoped_config_flag_defaults_to_disabled() -> None:
    config = GatewayConfig.model_validate({})
    assert config.enable_directory_scoped_config is False


def test_teams_delegated_credential_store_path_follows_runtime_dir_default() -> None:
    config = GatewayConfig.model_validate({"runtime_dir": "/tmp/evander-runtime"})
    assert str(config.teams_delegated_credential_store_path) == (
        "/tmp/evander-runtime/teams-delegated-credentials.json"
    )


def test_teams_graph_history_retry_defaults_are_deadline_aware() -> None:
    config = GatewayConfig.model_validate({})

    assert config.teams_history_request_timeout_seconds == 5.0
    assert config.teams_history_deadline_seconds == 15.0
    assert config.teams_retry_count == 2


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
    config = _validate_trigger_config({"runtime_dir": "/tmp/evander-runtime"})
    assert str(config.lock_path) == "/tmp/evander-runtime/trigger-orchestrator.lock"
    assert str(config.orchestration_store_path) == "/tmp/evander-runtime/orchestration.db"
    assert config.orchestration_retention_days == 30
    assert config.orchestration_compact_history_retention_days == 365
    assert config.orchestration_wal_checkpoint_mode == "truncate"
    assert config.orchestration_wal_checkpoint_min_bytes == 64 * 1024 * 1024
    assert config.orchestration_wal_checkpoint_interval_seconds == 5 * 60
    assert config.orchestration_wal_checkpoint_busy_timeout_ms == 1_000
    assert (
        str(config.event_queue_cursor_store_path)
        == "/tmp/evander-runtime/trigger-orchestrator-cursors.json"
    )
    assert (
        str(config.gate_cursor_store_path) == "/tmp/evander-runtime/gate-orchestrator-cursors.json"
    )
    assert (
        str(config.effect_cursor_store_path)
        == "/tmp/evander-runtime/effect-orchestrator-cursors.json"
    )
    assert str(config.effect_lock_path) == "/tmp/evander-runtime/effect-orchestrator.lock"
    assert config.triggers == []


@pytest.mark.parametrize(
    "legacy_key",
    ["internal_event_store_path", "scheduled_internal_event_store_path"],
)
def test_trigger_orchestrator_worker_global_config_rejects_legacy_event_store_paths(
    legacy_key: str,
) -> None:
    with pytest.raises(ValueError, match=legacy_key):
        _validate_trigger_config(
            {
                "runtime_dir": "/tmp/evander-runtime",
                legacy_key: "/tmp/legacy-events.json",
            }
        )


def test_trigger_orchestrator_worker_config_accepts_stale_explicit_sqlite_backend() -> None:
    config = _validate_trigger_config({"orchestration_store_backend": "sqlite"})

    assert "orchestration_store_backend" not in type(config).model_fields


def test_trigger_orchestrator_worker_config_rejects_removed_json_backend() -> None:
    with pytest.raises(ValueError, match="no longer supported"):
        _validate_trigger_config({"orchestration_store_backend": "json"})


def test_trigger_orchestrator_filter_gate_accepts_when_predicates() -> None:
    config = _validate_trigger_config(
        {
            "runtime_dir": "/tmp/evander-runtime",
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
        _validate_trigger_config(
            {
                "runtime_dir": "/tmp/evander-runtime",
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
    config = _validate_trigger_config(
        {
            "runtime_dir": "/tmp/evander-runtime",
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Git push",
                    "type": "trigger:git:push",
                    "managed_repo_id_glob": "mr_*",
                }
            ],
            "gates": [
                {
                    "id": "gate-filter",
                    "display_name": "Agent branch only",
                    "type": "gate:filter",
                    "outputs": {
                        "branch_name": {"type": "string"},
                        "managed_repo_id": {"type": "string"},
                        "commit_hash": {"type": "string"},
                        "new_commit_hash": {"type": "string"},
                        "old_commit_hash": {"type": "string"},
                        "ref_name": {"type": "string"},
                        "session_id": {"type": "string"},
                        "review_branch": {"type": "string"},
                        "notes_ref": {"type": "string"},
                        "default_branch": {"type": "string"},
                    },
                    "when": {
                        "all": [
                            {"left": "${{trigger-1.branch_name}}", "starts_with": "agents/"},
                            {"left": "${{trigger-1.managed_repo_id}}", "starts_with": "mr_"},
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
            {"left": "${{trigger-1.managed_repo_id}}", "starts_with": "mr_"},
            {"left": "${{trigger-1.branch_name}}", "contains": "/"},
        ]
    }


def test_trigger_orchestrator_validate_gate_accepts_named_checks() -> None:
    config = _validate_trigger_config(
        {
            "runtime_dir": "/tmp/evander-runtime",
            "gates": [
                {
                    "id": "gate-validate",
                    "display_name": "Validate candidate",
                    "type": "gate:validate",
                    "checks": [
                        {
                            "code": "description.prefix",
                            "message": "Description must use the required prefix.",
                            "when": {
                                "left": "${{trigger-1.description}}",
                                "starts_with": "---\n\n",
                            },
                        }
                    ],
                }
            ],
        }
    )

    gate = materialize_node_definition(config.gates[0])
    assert gate.type == "gate:validate"
    assert gate.checks[0].code == "description.prefix"
    assert gate.checks[0].message == "Description must use the required prefix."
    assert gate.checks[0].when == {
        "left": "${{trigger-1.description}}",
        "starts_with": "---\n\n",
    }


@pytest.mark.parametrize(
    ("checks", "message"),
    [
        ([], "gate:validate requires at least one check"),
        (
            [
                {
                    "code": "description.prefix",
                    "message": "First",
                    "when": {"left": "value", "equals": "value"},
                },
                {
                    "code": "description.prefix",
                    "message": "Second",
                    "when": {"left": "value", "equals": "value"},
                },
            ],
            "gate:validate check codes must be unique: description.prefix",
        ),
        (
            [
                {
                    "code": "invalid code",
                    "message": "Invalid code",
                    "when": {"left": "value", "equals": "value"},
                }
            ],
            "String should match pattern",
        ),
        (
            [
                {
                    "code": "description.prefix",
                    "message": "Invalid predicate",
                    "when": {"left": "value"},
                }
            ],
            "gate:validate check when must contain a logical operator or one of",
        ),
    ],
)
def test_trigger_orchestrator_validate_gate_rejects_invalid_checks(
    checks: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_trigger_config(
            {
                "runtime_dir": "/tmp/evander-runtime",
                "gates": [
                    {
                        "id": "gate-validate",
                        "display_name": "Validate candidate",
                        "type": "gate:validate",
                        "checks": checks,
                    }
                ],
            }
        )


def test_trigger_orchestrator_switch_gate_accepts_cases_and_duplicate_ports() -> None:
    config = _validate_trigger_config(
        {
            "runtime_dir": "/tmp/evander-runtime",
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
                            "when": {"all": [{"left": "${{trigger-1.state}}", "equals": "ready"}]},
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
                "runtime_dir": "/tmp/evander-runtime",
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
                "runtime_dir": "/tmp/evander-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "cases": [
                            {
                                "port": "   ",
                                "when": {
                                    "all": [{"left": "${{trigger-1.state}}", "equals": "ready"}]
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
                "runtime_dir": "/tmp/evander-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "cases": [
                            {
                                "port": "error",
                                "when": {
                                    "all": [{"left": "${{trigger-1.state}}", "equals": "ready"}]
                                },
                            }
                        ],
                    }
                ],
            },
            "gate:switch case port 'error' is reserved for lifecycle failures",
        ),
        (
            {
                "runtime_dir": "/tmp/evander-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "cases": [
                            {
                                "port": None,
                                "when": {
                                    "all": [{"left": "${{trigger-1.state}}", "equals": "ready"}]
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
                "runtime_dir": "/tmp/evander-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "cases": [
                            {
                                "port": 123,
                                "when": {
                                    "all": [{"left": "${{trigger-1.state}}", "equals": "ready"}]
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
                "runtime_dir": "/tmp/evander-runtime",
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
                                    "all": [{"left": "${{trigger-1.state}}", "equals": "ready"}]
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
                "runtime_dir": "/tmp/evander-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "default_port": "error",
                        "cases": [
                            {
                                "port": "ready",
                                "when": {
                                    "all": [{"left": "${{trigger-1.state}}", "equals": "ready"}]
                                },
                            }
                        ],
                    }
                ],
            },
            "gate:switch default_port 'error' is reserved for lifecycle failures",
        ),
        (
            {
                "runtime_dir": "/tmp/evander-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "default_port": 123,
                        "cases": [
                            {
                                "port": "ready",
                                "when": {
                                    "all": [{"left": "${{trigger-1.state}}", "equals": "ready"}]
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
                "runtime_dir": "/tmp/evander-runtime",
                "gates": [
                    {
                        "id": "gate-switch",
                        "display_name": "Route by state",
                        "type": "gate:switch",
                        "cases": [
                            {
                                "port": "ready",
                                "when": {
                                    "all": [{"left": "${{trigger-1.state}}", "equals": "ready"}]
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
                "runtime_dir": "/tmp/evander-runtime",
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
        _validate_trigger_config(payload)


def test_trigger_orchestrator_await_agent_gate_accepts_linear_flow_without_completion_trigger() -> (
    None
):
    config = _validate_trigger_config(
        {
            "runtime_dir": "/tmp/evander-runtime",
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


def test_personal_assistant_workflow_accepts_batched_reference_repository_preparation() -> None:
    config = _validate_trigger_config(
        {
            "runtime_dir": "/tmp/evander-runtime",
            "triggers": [
                {
                    "id": "teams-dm",
                    "display_name": "Teams DM",
                    "type": "trigger:teams:direct-message",
                    "targets": [{"target": "chat:dm-chat"}],
                    "sender_user_ids": ["user-1"],
                }
            ],
            "gates": [
                {
                    "id": "debounce",
                    "display_name": "Debounce",
                    "type": "gate:debounce",
                    "key": "user-1",
                    "quiet_period": "PT2S",
                    "max_delay": "PT10S",
                }
            ],
            "effects": [
                {
                    "id": "prepare-repositories",
                    "display_name": "Prepare repositories",
                    "type": "effect:agent:prepare-reference-repositories",
                    "session_id": "personal-assistant-user-1",
                    "directory_path": "root/personal",
                    "repositories": [
                        {"managed_repo_id": "mr-one"},
                        {
                            "managed_repo_id": "mr-two",
                            "branch": "trunk",
                        },
                    ],
                }
            ],
            "edges": [
                {"source_id": "teams-dm", "target_id": "debounce"},
                {"source_id": "debounce", "target_id": "prepare-repositories"},
            ],
        }
    )

    effect = materialize_node_definition(config.effects[0])
    assert effect.type == "effect:agent:prepare-reference-repositories"
    assert effect.refresh_upstream is False

    refreshed = effect.model_copy(update={"refresh_upstream": True})
    assert refreshed.refresh_upstream is True


def test_prepare_reference_repositories_rejects_legacy_repo_source_inputs() -> None:
    with pytest.raises(ValueError, match="repo_source is not supported; select a managed_repo_id"):
        _validate_trigger_config(
            {
                "runtime_dir": "/tmp/evander-runtime",
                "triggers": [],
                "gates": [],
                "effects": [
                    {
                        "id": "prepare-repositories",
                        "display_name": "Prepare repositories",
                        "type": "effect:agent:prepare-reference-repositories",
                        "session_id": "personal-assistant-user-1",
                        "directory_path": "root/personal",
                        "repositories": [{"repo_source": "https://example.invalid/legacy.git"}],
                    }
                ],
                "edges": [],
            }
        )


def test_trigger_orchestrator_for_gate_accepts_body_done_ports_and_cycle_return() -> None:
    config = _validate_trigger_config(
        {
            "runtime_dir": "/tmp/evander-runtime",
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
    config = _validate_trigger_config(
        {
            "runtime_dir": "/tmp/evander-runtime",
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


def test_trigger_orchestrator_while_gate_accepts_body_done_exhausted_ports_and_cycle_return() -> (
    None
):
    config = _validate_trigger_config(
        {
            "runtime_dir": "/tmp/evander-runtime",
            "triggers": [
                {
                    "id": "trigger-1",
                    "display_name": "Tick",
                    "type": "trigger:cron",
                    "expression": "* * * * *",
                    "outputs": {"failure_count": {"type": "integer"}},
                }
            ],
            "gates": [
                {
                    "id": "while-1",
                    "display_name": "Retry while failing",
                    "type": "gate:while",
                    "when": {"left": "${{trigger-1.failure_count}}", "greater_than": 0},
                    "max_iterations": 3,
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
                {
                    "id": "effect-exhausted",
                    "display_name": "Exhausted",
                    "type": "effect:vm:exec",
                    "target": "session-1",
                    "command": "echo exhausted",
                },
            ],
            "edges": [
                {"source_id": "trigger-1", "target_id": "while-1"},
                {"source_id": "while-1", "source_port": "body", "target_id": "effect-body"},
                {"source_id": "while-1", "source_port": "done", "target_id": "effect-done"},
                {
                    "source_id": "while-1",
                    "source_port": "exhausted",
                    "target_id": "effect-exhausted",
                },
                {"source_id": "effect-body", "target_id": "while-1", "allow_cycle": True},
            ],
        }
    )

    assert config.gates[0].type == "gate:while"


@pytest.mark.parametrize("max_iterations", [0, -1, 1.5, [], {}])
def test_trigger_orchestrator_while_gate_rejects_invalid_literal_max_iterations(
    max_iterations,
) -> None:
    with pytest.raises(ValueError, match="gate:while max_iterations must be a positive integer"):
        _validate_trigger_config(
            {
                "runtime_dir": "/tmp/evander-runtime",
                "gates": [
                    {
                        "id": "while-1",
                        "display_name": "Retry while failing",
                        "type": "gate:while",
                        "when": {"left": 1, "equals": 1},
                        "max_iterations": max_iterations,
                    }
                ],
            }
        )


def test_trigger_orchestrator_allows_orphaned_draft_subgraphs() -> None:
    config = _validate_trigger_config(
        {
            "runtime_dir": "/tmp/evander-runtime",
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
    config = _validate_trigger_config(
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
    config = _validate_trigger_config(
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
    config = _validate_trigger_config(
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
    config = _validate_trigger_config(
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
        _validate_trigger_config(
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
        _validate_trigger_config(
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


def test_trigger_orchestrator_worker_global_config_rejects_duplicate_webhook_slugs() -> None:
    with pytest.raises(
        ValueError,
        match="Webhook trigger slugs must be globally unique across active config: build-notify",
    ):
        _validate_trigger_config(
            {
                "triggers": [
                    {
                        "id": "webhook-1",
                        "display_name": "Webhook 1",
                        "type": "trigger:http:webhook",
                        "slug": "build-notify",
                        "secret_env": "WEBHOOK_SECRET",
                    },
                    {
                        "id": "webhook-2",
                        "display_name": "Webhook 2",
                        "type": "trigger:http:webhook",
                        "slug": "build-notify",
                        "secret_env": "WEBHOOK_SECRET_2",
                    },
                ]
            }
        )


def test_trigger_orchestrator_worker_global_config_rejects_duplicate_effect_ids() -> None:
    with pytest.raises(
        ValueError,
        match="Node IDs must be globally unique across triggers, gates, effects, and queries: effect-dup",
    ):
        _validate_trigger_config(
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


@pytest.mark.parametrize(
    ("state_effect", "state_query", "message"),
    [
        (
            {
                "id": "effect-state-update",
                "display_name": "Update state",
                "type": "effect:state:update",
                "namespace": "workflow",
                "key": "cursor",
                "operation": "replace",
                "value": {"counter": 1},
            },
            {
                "id": "query-state-get",
                "display_name": "Read state",
                "type": "query:state:get",
                "namespace": "workflow",
                "key": "cursor",
            },
            "effect:state:update operation must be set, patch, or delete",
        ),
        (
            {
                "id": "effect-state-update",
                "display_name": "Update state",
                "type": "effect:state:update",
                "namespace": "workflow",
                "key": "cursor",
                "operation": "set",
                "value": {"counter": 1},
            },
            {
                "id": "query-state-get",
                "display_name": "Read state",
                "type": "query:state:get",
                "namespace": "workflow",
            },
            "key",
        ),
    ],
)
def test_trigger_orchestrator_worker_global_config_validates_state_node_schemas(
    state_effect: dict[str, object],
    state_query: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_trigger_config(
            {
                "triggers": [
                    {
                        "id": "trigger-state",
                        "display_name": "State trigger",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    }
                ],
                "effects": [state_effect],
                "queries": [state_query],
                "edges": [
                    {"source_id": "trigger-state", "target_id": "effect-state-update"},
                    {"source_id": "effect-state-update", "target_id": "query-state-get"},
                ],
                "workflow_graphs": [
                    {
                        "id": "graph-state",
                        "name": "State graph",
                        "directory_path": "root/team-a",
                        "node_ids": [
                            "trigger-state",
                            "effect-state-update",
                            "query-state-get",
                        ],
                    }
                ],
            }
        )


def test_trigger_orchestrator_worker_global_config_defaults_provider_profile_to_agent_default() -> (
    None
):
    config = _validate_trigger_config({})

    assert config.provider_profile == "profiles/agent-default"
    assert config.provider_include_builtin is True


def test_trigger_orchestrator_worker_global_config_supports_core_minimal_profile() -> None:
    config = _validate_trigger_config(
        {"provider_loader": {"provider_profile": "profiles/core-minimal"}}
    )

    assert config.provider_profile == "profiles/core-minimal"
    assert config.provider_include_builtin is False


def test_trigger_orchestrator_worker_global_config_rejects_unknown_provider_profile() -> None:
    with pytest.raises(ValueError, match="Unknown provider loader profile"):
        _validate_trigger_config({"provider_loader": {"provider_profile": "profiles/unknown"}})


def test_trigger_orchestrator_worker_global_config_rejects_unknown_effect_edge_source() -> None:
    with pytest.raises(
        ValueError, match="Edge endpoints must reference existing node IDs: missing-id"
    ):
        _validate_trigger_config(
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
