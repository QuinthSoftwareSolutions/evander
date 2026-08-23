from __future__ import annotations

from collections import Counter
from ipaddress import ip_address, ip_network
from pathlib import Path
import re
from typing import Any, Literal
from urllib.parse import urlsplit
import unicodedata

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    IPvAnyNetwork,
    TypeAdapter,
    ValidationInfo,
    ValidationError,
    field_validator,
    model_validator,
)

from evander.approval_notification_targets import (
    normalize_approval_notification_target_ids,
)
from evander.approval_diagnostics import DEFAULT_REDACTED_PAYLOAD_KEYS
from evander.azure_devops import (
    ado_organization_from_legacy_url,
    azure_devops_organization_url,
    normalize_ado_organization,
)
from evander.addons import AddonLoaderConfig
from evander.models import (
    DirectoryPath,
    McpToolPolicyRule,
    McpToolName,
    ToolArgumentPolicyRule,
    normalize_mcp_tool_policies,
    normalize_tool_argument_policies,
    normalize_tool_names,
    validate_tool_argument_policy_map,
)
from evander.provider_profiles import DEFAULT_PROVIDER_PROFILE_NAME, resolve_provider_profile
from evander.container_worker_images import normalize_container_worker_images
from evander.events.scheduled import (
    scheduled_internal_event_store_path as runtime_scheduled_internal_event_store_path,
)
from evander.events.models import (
    EdgeDefinition,
    EffectDefinitionDiscriminated,
    GateDefinitionDiscriminated,
    QueryDefinitionDiscriminated,
    materialize_node_definition,
    TriggerDefinitionDiscriminated,
)
from evander.workflow.graph_metadata import resolve_workflow_graph_metadata
from evander.workflow.packs import WorkflowPackGraph
from evander.schema_bootstrap import bootstrap_default_output_schemas
from evander.workflow.validation import validate_workflow_graph

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


def _host_is_loopback(host: str) -> bool:
    normalized = host.strip().casefold()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _host_is_unspecified(host: str) -> bool:
    normalized = host.strip().casefold()
    try:
        return ip_address(normalized).is_unspecified
    except ValueError:
        return normalized == ""


def _http_url_host_and_port(value: str) -> tuple[str, int]:
    parsed = urlsplit(value)
    if parsed.hostname is None:
        return "", 443 if parsed.scheme == "https" else 80
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.hostname.casefold(), port


def _local_openai_upstream_conflicts_with_relay_listener(
    *, upstream_url: str, relay_host: str, relay_port: int
) -> bool:
    upstream_host, upstream_port = _http_url_host_and_port(upstream_url)
    if upstream_port != relay_port:
        return False

    relay_bind_host = relay_host.strip().casefold()
    if upstream_host == relay_bind_host:
        return True
    if _host_is_unspecified(relay_bind_host):
        return _host_is_loopback(upstream_host) or _host_is_unspecified(upstream_host)
    if _host_is_loopback(relay_bind_host):
        return _host_is_loopback(upstream_host) or _host_is_unspecified(upstream_host)
    return False


def _contains_url_control_character(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_required_text(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _normalize_directory_path(value: object, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return DirectoryPath(segments=text).as_posix()


def _normalize_phone_number(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = "".join(character for character in text if character.isdigit())
    if not digits:
        raise ValueError(f"{field_name} must contain at least one digit")
    return f"+{digits}" if "+" in text else digits


def _normalize_phone_number_list(value: object, *, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a JSON array of phone numbers")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        phone_number = _normalize_phone_number(item, field_name=field_name)
        if phone_number is None:
            raise ValueError(f"{field_name} cannot contain empty values")
        if phone_number in seen:
            continue
        seen.add(phone_number)
        normalized.append(phone_number)
    return normalized


bootstrap_default_output_schemas()


class MailboxConfig(BaseModel):
    mailbox_id: str
    provider: Literal["json_store", "gmail", "graph"] = "json_store"
    account_identifier: str = Field(
        default="default",
        validation_alias=AliasChoices("account_identifier", "account", "address"),
    )
    auth_secret_refs: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("auth_secret_refs", "auth_env_var_names"),
    )
    folder_mapping: dict[str, str] = Field(
        default_factory=lambda: {"inbox": "inbox", "drafts": "drafts"}
    )
    folder_allowlist_defaults: list[str] = Field(default_factory=lambda: ["inbox", "drafts"])


AssistantCapabilityProfileName = Literal["", "read-only", "operator-limited", "full-admin"]


class AssistantSettingsConfig(BaseModel):
    enabled: bool = False
    show_launcher_when_disabled: bool = False
    default_model: str | None = None
    workflow_generation_model: str | None = None
    default_profile: AssistantCapabilityProfileName | None = None
    read_only: bool = True
    custom_system_instructions: str | None = None

    @field_validator(
        "default_model",
        "workflow_generation_model",
        "default_profile",
        "custom_system_instructions",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)


class AgentConsultationProfile(BaseModel):
    """Bounded execution settings for one request-scoped consultation worker."""

    execution_backend: Literal["container_worker_vm"] = "container_worker_vm"
    agent_provider: str = Field(default="codex", min_length=1)
    model: str = Field(min_length=1)
    model_provider_mode: (
        Literal["openai_public", "azure_openai", "anthropic", "local_openai"] | None
    ) = None
    container_image: str | None = None
    timeout_seconds: int = Field(default=180, ge=15, le=3600)
    max_concurrency: int = Field(default=1, ge=1, le=32)

    @field_validator(
        "agent_provider", "model", "model_provider_mode", "container_image", mode="before"
    )
    @classmethod
    def normalize_profile_text(cls, value: object, info) -> object:
        if value is None and info.field_name in {"model_provider_mode", "container_image"}:
            return None
        text = str(value or "").strip()
        if not text and info.field_name in {"agent_provider", "model"}:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return text or None


class InboundPhoneRoute(BaseModel):
    id: str = Field(min_length=1)
    did: str | None = None
    called_number: str | None = None
    directory_path: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    allowed_callers: list[str] = Field(default_factory=list)
    blocked_callers: list[str] = Field(default_factory=list)
    route_prompt: str = Field(min_length=1)
    startup_text: str | None = None

    @field_validator("id", mode="before")
    @classmethod
    def normalize_id(cls, value: object) -> str:
        return _normalize_required_text(value, field_name="id")

    @field_validator("did", "called_number", mode="before")
    @classmethod
    def normalize_phone_match_fields(cls, value: object, info) -> object:
        return _normalize_phone_number(value, field_name=info.field_name)

    @field_validator("directory_path", mode="before")
    @classmethod
    def normalize_directory_path_field(cls, value: object, info) -> object:
        return _normalize_directory_path(value, field_name=info.field_name)

    @field_validator("startup_text", mode="before")
    @classmethod
    def normalize_optional_route_text(cls, value: object) -> object:
        return _normalize_optional_text(value)

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session_id(cls, value: object) -> str:
        return _normalize_required_text(value, field_name="session_id")

    @field_validator("route_prompt", mode="before")
    @classmethod
    def normalize_route_prompt(cls, value: object) -> str:
        return _normalize_required_text(value, field_name="route_prompt")

    @field_validator("allowed_callers", "blocked_callers", mode="before")
    @classmethod
    def normalize_phone_number_lists(cls, value: object, info) -> list[str]:
        return _normalize_phone_number_list(value, field_name=info.field_name)

    @model_validator(mode="after")
    def validate_route(self) -> "InboundPhoneRoute":
        if not self.did and not self.called_number:
            raise ValueError("inbound_phone_routes entries must define did or called_number")
        return self


class GatewayConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    approval_ttl_seconds: int = 3600
    approval_notification_target_ids: list[str] = Field(default_factory=list)
    runtime_dir: Path = Path("/var/lib/evander")
    audit_log_path: Path = Path("/var/log/evander/audit.jsonl")
    session_store_path: Path = Path("/var/lib/evander/sessions.json")
    approval_store_path: Path = Path("/var/lib/evander/approvals.json")
    draft_store_path: Path = Path("/var/lib/evander/drafts.json")
    email_store_path: Path = Path("/var/lib/evander/emails.json")
    email_cursor_store_path: Path = Path("/var/lib/evander/email_cursors.json")
    discord_command_store_path: Path = Path("/var/lib/evander/discord_commands.json")
    discord_webhook_store_path: Path = Path("/var/lib/evander/discord-webhooks.json")
    teams_message_store_path: Path = Path("/var/lib/evander/teams_messages.db")
    directories_store_path: Path = Path("/var/lib/evander/directories.json")
    addon_loader: AddonLoaderConfig = Field(default_factory=AddonLoaderConfig)
    enable_directory_scoped_config: bool = False
    execution_backend: Literal["libvirt_vm", "container_worker_vm"] = "libvirt_vm"
    container_worker_base_url: str | None = None
    container_worker_token_env: str = Field(default="EVANDER_WORKER_TOKEN", min_length=1)
    container_worker_default_image: str = Field(default="evander-runner:latest", min_length=1)
    container_worker_images: dict[str, str] = Field(default_factory=dict)
    container_worker_active_tool_timeout_seconds: float = Field(default=300.0, gt=0)
    container_worker_job_timeout_seconds: float = Field(default=1800.0, gt=0)
    container_worker_request_timeout_seconds: float = Field(default=30.0, gt=0)
    container_worker_workspace_persistence: str = Field(default="session", min_length=1)
    container_worker_codex_auth_mode: Literal["metered_api", "chatgpt_subscription"] = "metered_api"
    container_worker_git_allowed_from: str | None = None
    managed_git_commit_name: str | None = None
    managed_git_commit_email: str | None = None
    managed_git_operation_timeout_seconds: float = Field(default=300.0, gt=0)
    managed_git_worktree_stale_seconds: float = Field(default=3600.0, ge=0)
    container_worker_guest_openai_base_url: str | None = None
    container_worker_guest_anthropic_base_url: str | None = None
    container_worker_guest_gateway_url: str | None = None
    container_worker_guest_http_target_relay_base_url: str | None = None
    container_worker_guest_nuget_relay_base_url: str | None = None
    container_worker_guest_pypi_index_url: str | None = None
    container_worker_guest_npm_registry_url: str | None = None
    container_worker_mssql_enabled: bool = False
    container_worker_mssql_host: str | None = None
    container_worker_mssql_port: int = Field(default=1433, gt=0, le=65535)
    discord_backend: Literal["stub", "bot_api"] = "stub"
    discord_bot_token_env: str = Field(default="DISCORD_BOT_TOKEN", min_length=1)
    discord_api_base_url: str = "https://discord.com/api/v10"
    discord_poll_limit: int = Field(default=50, ge=1, le=100)
    discord_timeout_seconds: float = Field(default=15.0, gt=0)
    discord_retry_count: int = Field(default=3, ge=0, le=10)
    discord_retry_base_backoff_seconds: float = Field(default=0.5, gt=0)
    discord_retry_max_backoff_seconds: float = Field(default=5.0, gt=0)
    discord_persona_webhook_cache_limit: int = Field(default=10, ge=1, le=10)
    discord_read_reaction_emoji: str = "👀"
    discord_operator_user_allowlist: list[str] = Field(default_factory=list)
    discord_operator_channel_allowlist: list[str] = Field(default_factory=list)
    discord_poll_operator_dms: bool = False
    teams_backend: Literal["stub", "graph_api", "delegated_graph_api"] = "stub"
    teams_graph_access_token_env: str = Field(default="GRAPH_TEAMS_ACCESS_TOKEN", min_length=1)
    teams_graph_api_base_url: str = "https://graph.microsoft.com/v1.0"
    teams_poll_limit: int = Field(default=50, ge=1, le=100)
    teams_history_page_cap: int = Field(default=1, ge=1, le=100)
    teams_history_deadline_seconds: float = Field(default=5.0, gt=0)
    teams_timeout_seconds: float = Field(default=15.0, gt=0)
    teams_retry_count: int = Field(default=3, ge=0, le=10)
    teams_retry_base_backoff_seconds: float = Field(default=0.5, gt=0)
    teams_retry_max_backoff_seconds: float = Field(default=5.0, gt=0)
    teams_read_reaction_emoji: str = "👀"
    teams_meeting_media_bridge_url: str | None = None
    teams_meeting_media_bridge_token_env: str = Field(
        default="TEAMS_MEETING_MEDIA_BRIDGE_TOKEN", min_length=1
    )
    teams_meeting_default_voice: str = Field(default="en-US-JennyNeural", min_length=1)
    teams_meeting_poll_interval_seconds: float = Field(default=5.0, gt=0)
    teams_meeting_request_timeout_seconds: float = Field(default=15.0, gt=0)
    teams_meeting_max_active_meetings: int = Field(default=1, ge=1)
    teams_meeting_store_path: Path = Path("/var/lib/evander/teams-meetings.json")
    teams_delegated_identity_id: str | None = None
    teams_delegated_client_id: str | None = None
    teams_delegated_tenant_id: str = "organizations"
    teams_delegated_scopes: list[str] = Field(
        default_factory=lambda: [
            "offline_access",
            "https://graph.microsoft.com/ChannelMessage.Read.All",
            "https://graph.microsoft.com/ChannelMessage.Send",
            "https://graph.microsoft.com/Chat.ReadWrite",
        ]
    )
    teams_delegated_authority_base_url: str = "https://login.microsoftonline.com"
    teams_delegated_credential_store_path: Path = Path(
        "/var/lib/evander/teams-delegated-credentials.json"
    )
    retell_api_base_url: str = "https://api.retellai.com"
    retell_api_key_env: str = Field(default="RETELL_API_KEY", min_length=1)
    retell_from_number: str | None = None
    retell_default_agent_id: str | None = None
    retell_request_timeout_seconds: float = Field(default=30.0, gt=0)
    phone_call_backend: Literal["retell", "openai_realtime_didww"] = "retell"
    openai_realtime_model: str = Field(default="gpt-realtime-2", min_length=1)
    openai_realtime_api_key_env: str = Field(default="OPENAI_API_KEY", min_length=1)
    openai_realtime_api_base_url: str = "https://api.openai.com/v1"
    voice_media_controller_url: str | None = None
    voice_media_controller_token_env: str = Field(
        default="VOICE_MEDIA_CONTROLLER_TOKEN", min_length=1
    )
    voice_media_controller_request_timeout_seconds: float = Field(default=30.0, gt=0)
    didww_sip_bridge_url: str | None = None
    didww_sip_bridge_token_env: str = Field(default="DIDWW_SIP_BRIDGE_TOKEN", min_length=1)
    didww_from_number: str | None = None
    inbound_phone_routes: list[InboundPhoneRoute] = Field(default_factory=list)
    bind_network_cidr: IPvAnyNetwork = "192.168.250.0/24"
    gateway_url: str = "http://192.168.250.1:8000/mcp"
    openai_relay_host: str = "0.0.0.0"
    openai_relay_port: int = 11434
    model_provider_mode: Literal["openai_public", "azure_openai", "anthropic", "local_openai"] = (
        Field(
            default="openai_public",
            validation_alias=AliasChoices("model_provider_mode", "openai_provider_mode"),
        )
    )
    openai_api_base_url: str = "https://api.openai.com/v1"
    openai_api_key_env: str = "OPENAI_API_KEY"
    local_openai_api_base_url: str = "http://127.0.0.1:11434/v1"
    local_openai_api_key: str = "ollama"
    local_openai_provider_name: str = "local_openai"
    local_openai_max_concurrency: int | None = Field(default=None, ge=1)
    azure_openai_endpoint: str | None = None
    azure_openai_auth_mode: Literal["api_key"] = "api_key"
    azure_openai_api_key_env: str | None = None
    azure_openai_api_version: str = "preview"
    azure_openai_model_deployments: dict[str, str] = Field(default_factory=dict)
    openai_relay_timeout_seconds: float = 300.0
    anthropic_api_base_url: str = "https://api.anthropic.com"
    anthropic_api_key_env: str = "ANTHROPIC_API_KEY"
    anthropic_api_version: str = "2023-06-01"
    anthropic_relay_timeout_seconds: float = 300.0
    semantic_store_provider: Literal["qdrant"] = "qdrant"
    qdrant_base_url: str = "http://127.0.0.1:6333"
    qdrant_api_key_env: str | None = "QDRANT_API_KEY"
    semantic_embedding_provider: Literal["openai"] = "openai"
    semantic_embedding_model: str = Field(default="text-embedding-3-small", min_length=1)
    semantic_embedding_dimensions: int = Field(default=1536, ge=1)
    semantic_vector_distance: Literal["Cosine", "Dot", "Euclid", "Manhattan"] = "Cosine"
    http_target_relay_host: str = "0.0.0.0"
    http_target_relay_port: int = 11777
    http_target_relay_timeout_seconds: float = 300.0
    nuget_relay_host: str = "0.0.0.0"
    nuget_relay_port: int = 11666
    nuget_public_feed_base_url: str = "https://api.nuget.org"
    nuget_azure_feed_base_url: str = (
        "https://pkgs.dev.azure.com/example/example/_packaging/example/nuget"
    )
    nuget_azure_pat_env: str = "AZURE_ARTIFACTS_PAT"
    nuget_relay_timeout_seconds: float = 300.0
    npm_audit_facade_host: str = "0.0.0.0"
    npm_audit_facade_port: int = 11881
    npm_audit_facade_timeout_seconds: float = 300.0
    npm_audit_facade_audit_registry_url: str = "https://registry.npmjs.org"
    npm_audit_facade_package_registry_url: str | None = None
    default_session_ttl_seconds: int = 28800
    email_mailbox_allowlist: list[str] = Field(default_factory=list)
    email_folder_allowlist_by_mailbox: dict[str, list[str]] = Field(default_factory=dict)
    email_draft_recipient_allowlist: list[str] = Field(default_factory=list)
    email_draft_domain_allowlist: list[str] = Field(default_factory=list)
    email_send_enabled: bool = False
    communication_file_receive_enabled: bool = False
    communication_file_send_enabled: bool = False
    communication_file_max_bytes: int | None = Field(default=10 * 1024 * 1024, ge=0)
    communication_file_allowed_mime_types: list[str] = Field(default_factory=list)
    communication_file_allowed_extensions: list[str] = Field(default_factory=list)
    communication_file_blocked_extensions: list[str] = Field(default_factory=list)
    communication_file_require_approval_for_send: bool = False
    communication_artifact_public_base_url: str | None = None
    communication_artifact_link_secret_env: str = Field(
        default="EVANDER_COMMUNICATION_ARTIFACT_LINK_SECRET",
        min_length=1,
    )
    communication_artifact_link_ttl_seconds: int = Field(
        default=7 * 24 * 60 * 60,
        ge=1,
    )
    pr_reply_require_approval: bool = False
    approval_required_tools: list[McpToolName] = Field(default_factory=list)
    mcp_tool_policies: dict[str, McpToolPolicyRule] = Field(default_factory=dict)
    tool_argument_policies: dict[str, list[ToolArgumentPolicyRule]] = Field(default_factory=dict)
    github_token_env: str = "GITHUB_TOKEN"
    agent_prompt_token_secret_env: str = "AGENT_PROMPT_TOKEN_SECRET"
    agent_prompt_token_ttl_seconds: int = Field(default=3600, ge=1)
    agent_prompt_queue_max_retries: int = Field(default=3, ge=1)
    agent_consultation_profiles: dict[str, AgentConsultationProfile] = Field(default_factory=dict)
    agent_consultation_default_profile: str | None = None
    agent_consultation_max_wait_seconds: int = Field(default=60, ge=0, le=300)
    agent_consultation_retention_days: int = Field(default=30, ge=1, le=3650)
    agent_consultation_token_secret_env: str = Field(
        default="AGENT_PROMPT_TOKEN_SECRET", min_length=1
    )
    agent_context_retry_enabled: bool = True
    agent_context_retry_max_attempts: int = Field(default=1, ge=0)
    agent_context_retry_target_chars: int = Field(default=16_000, ge=4_000)
    azure_devops_pat_env: str = "AZURE_DEVOPS_PAT"
    azure_devops_pr_pat_env: str = "AZURE_DEVOPS_PR_PAT"
    ado_organization: str | None = None
    azure_devops_organization_url: str = "https://dev.azure.com"
    azure_devops_project: str = "default"
    ado_area_filters: list[str] = Field(default_factory=list)
    ado_team_filters: list[str] = Field(default_factory=list)
    email_provider_timeout_seconds: float = Field(default=30.0, gt=0)
    wake_poll_interval_seconds: float = Field(default=30.0, gt=0)
    wake_jitter_seconds: float = Field(default=5.0, ge=0)
    wake_cooldown_seconds: float = Field(default=60.0, ge=0)
    wake_schedule_late_threshold_seconds: float = Field(default=300.0, ge=0)
    wake_completion_confirmation_timeout_seconds: float = Field(default=300.0, ge=0)
    wake_completion_confirmation_retry_max_attempts: int = Field(default=3, ge=1)
    wake_completion_confirmation_retry_window_seconds: float = Field(
        default=24 * 60 * 60,
        ge=0,
    )
    wake_completion_confirmation_retry_base_backoff_seconds: float = Field(
        default=60.0,
        gt=0,
    )
    wake_completion_confirmation_retry_max_backoff_seconds: float = Field(
        default=600.0,
        gt=0,
    )
    wake_session_cooldown_path: Path = Path("/var/lib/evander/wake-session-cooldown.json")
    host_agent_control_state_path: Path = Path("/var/lib/evander/host-agent-control-state.json")
    host_busy_check_script_path: Path = Path("scripts/check_session_vm_busy.sh")
    host_resume_script_path: Path = Path("scripts/resume_issue_agent.sh")
    wake_dispatch_state_path: Path = Path("/var/lib/evander/wake-dispatch-state.json")
    pr_feedback_resume_prompt: str = (
        "You have unread PR comments. Please fetch them with list_unread_feedback."
    )
    discord_resume_prompt: str = Field(
        default=(
            "You have unread Discord messages. Please fetch them with "
            "list_unread_discord_messages and respond with discord_post_status if needed."
        ),
    )
    wake_vm_user: str = "agent"
    wake_resume_timeout_seconds: float = Field(default=1200.0, gt=0)
    scheduled_wake_prefix_text: str = Field(
        default="",
        validation_alias=AliasChoices(
            "scheduled_wake_prefix_text",
            "wake_scheduled_prefix_text",
        ),
    )
    wake_schedule_max_active_per_session: int | None = Field(default=None, ge=1)
    wake_schedule_max_active_total: int | None = Field(default=None, ge=1)
    wake_schedule_min_interval_seconds: float | None = Field(default=None, gt=0)
    pr_map_path: Path = Path("/var/lib/evander/pr-map.json")
    provider_state: dict[str, Any] = Field(default_factory=dict)
    email_mailboxes: list[MailboxConfig] = Field(
        default_factory=lambda: [
            MailboxConfig(
                mailbox_id="default",
                provider="json_store",
                account_identifier="default",
                folder_mapping={"inbox": "inbox", "drafts": "drafts"},
                folder_allowlist_defaults=["inbox", "drafts"],
            )
        ]
    )
    assistant_settings: AssistantSettingsConfig = Field(default_factory=AssistantSettingsConfig)
    redacted_keys: set[str] = Field(default_factory=lambda: set(DEFAULT_REDACTED_PAYLOAD_KEYS))

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_azure_devops_organization(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        migrated_organization = None
        if "azure_devops_organization_url" in payload:
            migrated_organization = ado_organization_from_legacy_url(
                payload.get("azure_devops_organization_url")
            )
        explicit_organization = normalize_ado_organization(payload.get("ado_organization"))
        if explicit_organization is not None:
            payload["ado_organization"] = explicit_organization
            payload["azure_devops_organization_url"] = azure_devops_organization_url(
                explicit_organization
            )
            return payload

        if migrated_organization is not None:
            payload["ado_organization"] = migrated_organization
            payload["azure_devops_organization_url"] = azure_devops_organization_url(
                migrated_organization
            )
        return payload

    @field_validator(
        "gateway_url",
        "discord_api_base_url",
        "teams_graph_api_base_url",
        "teams_meeting_media_bridge_url",
        "teams_delegated_authority_base_url",
        "container_worker_guest_openai_base_url",
        "container_worker_guest_anthropic_base_url",
        "container_worker_guest_gateway_url",
        "container_worker_guest_http_target_relay_base_url",
        "container_worker_guest_nuget_relay_base_url",
        "container_worker_guest_pypi_index_url",
        "container_worker_guest_npm_registry_url",
        "azure_openai_endpoint",
        "openai_api_base_url",
        "local_openai_api_base_url",
        "anthropic_api_base_url",
        "qdrant_base_url",
        "nuget_public_feed_base_url",
        "nuget_azure_feed_base_url",
        "npm_audit_facade_audit_registry_url",
        "npm_audit_facade_package_registry_url",
        "retell_api_base_url",
        "openai_realtime_api_base_url",
        "voice_media_controller_url",
        "didww_sip_bridge_url",
        mode="before",
    )
    @classmethod
    def normalize_http_url_fields(cls, value: object, info) -> object:
        if not isinstance(value, str):
            return value
        if _contains_url_control_character(value):
            raise ValueError(f"{info.field_name} must be a valid http(s) URL, got {value!r}")
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError(f"{info.field_name} cannot be empty")
        return normalized

    @field_validator("approval_required_tools", mode="before")
    @classmethod
    def normalize_approval_required_tools(cls, value: object) -> list[McpToolName]:
        return normalize_tool_names(value, field_name="approval_required_tools")

    @field_validator("mcp_tool_policies", mode="before")
    @classmethod
    def normalize_mcp_tool_policies_field(cls, value: object) -> dict[str, object]:
        return normalize_mcp_tool_policies(value, field_name="mcp_tool_policies")

    @field_validator("tool_argument_policies", mode="before")
    @classmethod
    def normalize_tool_argument_policies_field(cls, value: object) -> dict[str, list[object]]:
        return normalize_tool_argument_policies(value, field_name="tool_argument_policies")

    @field_validator("tool_argument_policies")
    @classmethod
    def validate_tool_argument_policies_field(
        cls, value: dict[str, list[ToolArgumentPolicyRule]]
    ) -> dict[str, list[ToolArgumentPolicyRule]]:
        return validate_tool_argument_policy_map(value, field_name="tool_argument_policies")

    @field_validator(
        "gateway_url",
        "discord_api_base_url",
        "teams_graph_api_base_url",
        "teams_delegated_authority_base_url",
        "container_worker_guest_openai_base_url",
        "container_worker_guest_anthropic_base_url",
        "container_worker_guest_gateway_url",
        "container_worker_guest_http_target_relay_base_url",
        "container_worker_guest_nuget_relay_base_url",
        "container_worker_guest_pypi_index_url",
        "container_worker_guest_npm_registry_url",
        "azure_openai_endpoint",
        "openai_api_base_url",
        "local_openai_api_base_url",
        "anthropic_api_base_url",
        "qdrant_base_url",
        "nuget_public_feed_base_url",
        "nuget_azure_feed_base_url",
        "npm_audit_facade_audit_registry_url",
        "npm_audit_facade_package_registry_url",
        "retell_api_base_url",
        "openai_realtime_api_base_url",
        "voice_media_controller_url",
        "didww_sip_bridge_url",
    )
    @classmethod
    def validate_http_url_fields(cls, value: str | None, info) -> str | None:
        if value is None:
            return None
        try:
            _HTTP_URL_ADAPTER.validate_python(value)
        except ValidationError as exc:
            raise ValueError(
                f"{info.field_name} must be a valid http(s) URL, got {value!r}"
            ) from exc
        return value

    @field_validator("semantic_vector_distance", mode="before")
    @classmethod
    def normalize_semantic_vector_distance(cls, value: object) -> object:
        from evander.semantic.settings import normalize_vector_distance

        return normalize_vector_distance(value)

    @field_validator("approval_notification_target_ids", mode="before")
    @classmethod
    def normalize_approval_notification_target_ids(cls, value: object) -> list[str]:
        return normalize_approval_notification_target_ids(
            value, field_name="approval_notification_target_ids"
        )

    @field_validator("qdrant_api_key_env", mode="before")
    @classmethod
    def normalize_qdrant_api_key_env(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("model_provider_mode", mode="before")
    @classmethod
    def normalize_model_provider_mode(cls, value: object) -> object:
        if value is None:
            return value
        text = str(value).strip()
        return text or None

    @field_validator("container_worker_codex_auth_mode", mode="before")
    @classmethod
    def normalize_container_worker_codex_auth_mode(cls, value: object) -> object:
        from evander.codex_auth import normalize_codex_auth_mode

        normalized = normalize_codex_auth_mode(value)
        return normalized.value if normalized is not None else "metered_api"

    @field_validator("local_openai_api_key", "local_openai_provider_name", mode="before")
    @classmethod
    def normalize_local_openai_required_strings(cls, value: object, info) -> object:
        if value is None:
            return value
        text = str(value).strip()
        if not text:
            raise ValueError(f"{info.field_name} cannot be empty")
        return text

    @field_validator("azure_openai_auth_mode", mode="before")
    @classmethod
    def validate_azure_openai_auth_mode(cls, value: object) -> object:
        if value is None:
            return value
        text = str(value).strip()
        if text != "api_key":
            raise ValueError("azure_openai_auth_mode currently supports only 'api_key'")
        return text

    @field_validator("azure_openai_api_key_env", mode="before")
    @classmethod
    def normalize_azure_openai_api_key_env(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("anthropic_api_key_env", mode="before")
    @classmethod
    def normalize_anthropic_api_key_env(cls, value: object) -> object:
        if value is None:
            return value
        text = str(value).strip()
        if not text:
            raise ValueError("anthropic_api_key_env cannot be empty")
        return text

    @field_validator("anthropic_api_version", mode="before")
    @classmethod
    def normalize_anthropic_api_version(cls, value: object) -> object:
        if value is None:
            return value
        text = str(value).strip()
        if not text:
            raise ValueError("anthropic_api_version cannot be empty")
        return text

    @field_validator("azure_openai_api_version", mode="before")
    @classmethod
    def normalize_azure_openai_api_version(cls, value: object) -> object:
        if value is None:
            return value
        text = str(value).strip()
        if not text:
            raise ValueError("azure_openai_api_version cannot be empty")
        return text

    @field_validator("azure_openai_model_deployments", mode="before")
    @classmethod
    def normalize_azure_openai_model_deployments(cls, value: object) -> dict[str, str]:
        if value is None:
            raise ValueError("azure_openai_model_deployments must be a JSON object")
        if not isinstance(value, dict):
            raise ValueError("azure_openai_model_deployments must be a JSON object")
        normalized: dict[str, str] = {}
        for raw_model_name, raw_deployment_name in value.items():
            model_name = str(raw_model_name or "").strip()
            deployment_name = str(raw_deployment_name or "").strip()
            if not model_name:
                raise ValueError("azure_openai_model_deployments keys cannot be empty")
            if not deployment_name:
                raise ValueError(f"azure_openai_model_deployments[{model_name!r}] cannot be empty")
            if model_name in normalized:
                raise ValueError(f"duplicate azure_openai_model_deployments key: {model_name!r}")
            normalized[model_name] = deployment_name
        return normalized

    @field_validator("container_worker_images", mode="before")
    @classmethod
    def normalize_container_worker_images_field(cls, value: object) -> dict[str, str]:
        return normalize_container_worker_images(value)

    @field_validator("agent_consultation_profiles", mode="before")
    @classmethod
    def normalize_agent_consultation_profiles(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("agent_consultation_profiles must be a JSON object")
        normalized: dict[str, object] = {}
        for raw_name, profile in value.items():
            name = str(raw_name or "").strip()
            if not name or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name):
                raise ValueError(
                    "agent consultation profile names must use letters, numbers, '.', '_', or '-'"
                )
            normalized[name] = profile
        return normalized

    @field_validator(
        "agent_consultation_default_profile",
        "agent_consultation_token_secret_env",
        mode="before",
    )
    @classmethod
    def normalize_agent_consultation_text(cls, value: object, info) -> object:
        if value is None and info.field_name == "agent_consultation_default_profile":
            return None
        text = str(value or "").strip()
        if not text and info.field_name == "agent_consultation_token_secret_env":
            raise ValueError("agent_consultation_token_secret_env must be non-empty")
        return text or None

    @model_validator(mode="after")
    def validate_agent_consultation_default_profile(self, info: ValidationInfo) -> "GatewayConfig":
        if (info.context or {}).get("validation_scope") == "overlay":
            return self
        default_profile = self.agent_consultation_default_profile
        if default_profile and default_profile not in self.agent_consultation_profiles:
            raise ValueError(
                "agent_consultation_default_profile must name an agent_consultation_profiles entry"
            )
        return self

    @field_validator(
        "retell_from_number",
        "retell_default_agent_id",
        "didww_from_number",
        mode="before",
    )
    @classmethod
    def normalize_retell_optional_strings(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("container_worker_mssql_host", mode="before")
    @classmethod
    def normalize_container_worker_mssql_host(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator(
        "container_worker_git_allowed_from",
        "managed_git_commit_name",
        "managed_git_commit_email",
        mode="before",
    )
    @classmethod
    def normalize_managed_git_optional_text(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("teams_delegated_identity_id", mode="before")
    @classmethod
    def normalize_teams_delegated_identity_id(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("discord_read_reaction_emoji", "teams_read_reaction_emoji", mode="before")
    @classmethod
    def normalize_read_reaction_emoji_fields(cls, value: object, info) -> object:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        normalized = unicodedata.normalize("NFC", text)
        if not normalized:
            raise ValueError(f"{info.field_name} must be a non-empty string")
        return normalized

    @field_validator("teams_delegated_client_id", "teams_delegated_tenant_id", mode="before")
    @classmethod
    def normalize_teams_delegated_strings(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("communication_artifact_public_base_url", mode="before")
    @classmethod
    def normalize_communication_artifact_public_base_url(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip().rstrip("/")
        return text or None

    @field_validator("teams_delegated_scopes", mode="before")
    @classmethod
    def normalize_teams_delegated_scopes(cls, value: object) -> object:
        if value is None:
            return value
        if isinstance(value, str):
            return [scope for scope in value.split(" ") if scope.strip()]
        return value

    @field_validator("teams_delegated_scopes")
    @classmethod
    def validate_teams_delegated_scopes(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for scope in value:
            candidate = scope.strip()
            if not candidate or candidate in seen:
                continue
            seen.add(candidate)
            normalized.append(candidate)
        if not normalized:
            raise ValueError("teams_delegated_scopes must contain at least one scope")
        return normalized

    @field_validator("bind_network_cidr", mode="before")
    @classmethod
    def normalize_bind_network_cidr(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("bind_network_cidr cannot be empty")
        try:
            network = ip_network(normalized, strict=True)
        except ValueError as exc:
            raise ValueError(
                "bind_network_cidr must be a valid CIDR network (for example: 192.168.250.0/24)"
            ) from exc
        return str(network)

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> GatewayConfig:
        default_runtime_dir = Path("/var/lib/evander")
        default_paths = {
            "wake_session_cooldown_path": default_runtime_dir / "wake-session-cooldown.json",
            "host_agent_control_state_path": default_runtime_dir / "host-agent-control-state.json",
            "wake_dispatch_state_path": default_runtime_dir / "wake-dispatch-state.json",
            "session_store_path": default_runtime_dir / "sessions.json",
            "approval_store_path": default_runtime_dir / "approvals.json",
            "draft_store_path": default_runtime_dir / "drafts.json",
            "email_store_path": default_runtime_dir / "emails.json",
            "email_cursor_store_path": default_runtime_dir / "email_cursors.json",
            "discord_command_store_path": default_runtime_dir / "discord_commands.json",
            "discord_webhook_store_path": default_runtime_dir / "discord-webhooks.json",
            "teams_message_store_path": default_runtime_dir / "teams_messages.db",
            "teams_meeting_store_path": default_runtime_dir / "teams-meetings.json",
            "teams_delegated_credential_store_path": default_runtime_dir
            / "teams-delegated-credentials.json",
            "directories_store_path": default_runtime_dir / "directories.json",
            "pr_map_path": default_runtime_dir / "pr-map.json",
        }
        for field_name, default_path in default_paths.items():
            if getattr(self, field_name) == default_path:
                setattr(self, field_name, self.runtime_dir / default_path.name)
        if self.audit_log_path == Path("/var/log/evander/audit.jsonl"):
            self.audit_log_path = self.runtime_dir / "audit.jsonl"
        return self

    @model_validator(mode="after")
    def apply_email_mailbox_defaults(self) -> GatewayConfig:
        mailbox_ids = [mailbox.mailbox_id for mailbox in self.email_mailboxes]
        duplicates = sorted(
            mailbox_id for mailbox_id, count in Counter(mailbox_ids).items() if count > 1
        )
        if duplicates:
            duplicate_list = ", ".join(duplicates)
            raise ValueError(f"Duplicate email mailbox IDs are not allowed: {duplicate_list}")

        if not self.email_mailbox_allowlist:
            self.email_mailbox_allowlist = mailbox_ids

        if not self.email_folder_allowlist_by_mailbox:
            self.email_folder_allowlist_by_mailbox = {
                mailbox.mailbox_id: mailbox.folder_allowlist_defaults
                for mailbox in self.email_mailboxes
            }

        if self.teams_backend == "delegated_graph_api" and not self.teams_delegated_identity_id:
            raise ValueError(
                "teams_backend 'delegated_graph_api' requires teams_delegated_identity_id"
            )
        if self.teams_backend == "delegated_graph_api" and not self.teams_delegated_client_id:
            raise ValueError(
                "teams_backend 'delegated_graph_api' requires teams_delegated_client_id"
            )

        return self

    @model_validator(mode="after")
    def validate_azure_openai_provider_config(self, info) -> GatewayConfig:
        context = getattr(info, "context", None) or {}
        if (
            context.get("directory_config_domain") == "providers"
            and context.get("validation_scope") == "overlay"
        ):
            return self
        if self.model_provider_mode != "azure_openai":
            return self

        missing_fields: list[str] = []
        if not self.azure_openai_endpoint:
            missing_fields.append("azure_openai_endpoint")
        if not self.azure_openai_api_key_env:
            missing_fields.append("azure_openai_api_key_env")
        if not self.azure_openai_model_deployments:
            missing_fields.append("azure_openai_model_deployments")

        if missing_fields:
            raise ValueError(
                "model_provider_mode='azure_openai' requires "
                + ", ".join(missing_fields)
                + " to be configured"
            )
        return self

    @model_validator(mode="after")
    def validate_local_openai_provider_config(self, info) -> GatewayConfig:
        context = getattr(info, "context", None) or {}
        if (
            context.get("directory_config_domain") == "providers"
            and context.get("validation_scope") == "overlay"
        ):
            return self
        if self.model_provider_mode != "local_openai":
            return self
        if _local_openai_upstream_conflicts_with_relay_listener(
            upstream_url=self.local_openai_api_base_url,
            relay_host=self.openai_relay_host,
            relay_port=self.openai_relay_port,
        ):
            raise ValueError(
                "model_provider_mode='local_openai' requires local_openai_api_base_url "
                "to point at an endpoint other than the OpenAI relay listener; set "
                "openai_relay_host to a guest-facing address or set "
                "local_openai_api_base_url to a non-conflicting host/port"
            )
        return self

    @model_validator(mode="after")
    def validate_inbound_phone_routes(self) -> GatewayConfig:
        route_ids = [route.id for route in self.inbound_phone_routes]
        duplicates = sorted(route_id for route_id, count in Counter(route_ids).items() if count > 1)
        if duplicates:
            duplicate_list = ", ".join(duplicates)
            raise ValueError(
                f"Duplicate inbound_phone_routes ids are not allowed: {duplicate_list}"
            )
        return self

    @property
    def wake_scheduled_prefix_text(self) -> str:
        return self.scheduled_wake_prefix_text

    @property
    def openai_provider_mode(
        self,
    ) -> Literal["openai_public", "azure_openai", "anthropic", "local_openai"]:
        return self.model_provider_mode


class AdoWorkerGlobalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_dir: Path = Path("runtime")
    poll_interval_seconds: float = Field(default=5.0, gt=0)
    directories: list[str] = Field(default_factory=list)

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            normalized.append(DirectoryPath(segments=text).as_posix())
        return normalized


class ProxyIssueSyncWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    poll_interval_seconds: float = Field(default=300.0, gt=0)
    freshness_window_seconds: float = Field(default=300.0, ge=0)


class DiscordIngestWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    directories: list[str] = Field(default_factory=list)
    poll_interval_seconds: float = Field(default=5.0, gt=0)
    jitter_seconds: float = Field(default=5.0, ge=0)
    backoff_seconds: float = Field(default=60.0, ge=0)
    lock_path: Path | None = None

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            normalized.append(DirectoryPath(segments=text).as_posix())
        return normalized

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> DiscordIngestWorkerGlobalConfig:
        if self.lock_path is None:
            self.lock_path = self.runtime_dir / "discord-ingest.lock"
        return self


class DiscordWakeWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    directories: list[str] = Field(default_factory=list)
    poll_interval_seconds: float = Field(default=30.0, gt=0)
    jitter_seconds: float = Field(default=5.0, ge=0)
    backoff_seconds: float = Field(default=60.0, ge=0)
    cooldown_seconds: float = Field(default=60.0, ge=0)
    lock_path: Path | None = None
    session_cooldown_path: Path | None = None
    busy_check_script_path: Path = Path("scripts/check_session_vm_busy.sh")
    resume_script_path: Path = Path("scripts/resume_issue_agent.sh")
    wake_state_path: Path | None = None
    vm_user: str = "agent"
    resume_timeout_seconds: float = Field(default=1200.0, gt=0)

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            normalized.append(DirectoryPath(segments=text).as_posix())
        return normalized

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> DiscordWakeWorkerGlobalConfig:
        if self.lock_path is None:
            self.lock_path = self.runtime_dir / "discord-wake.lock"
        if self.session_cooldown_path is None:
            self.session_cooldown_path = self.runtime_dir / "discord-wake-session-cooldown.json"
        if self.wake_state_path is None:
            self.wake_state_path = self.runtime_dir / "discord-wake-state.json"
        return self


class ScheduledWakeWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    directories: list[str] = Field(default_factory=list)
    poll_interval_seconds: float = Field(default=30.0, gt=0)
    jitter_seconds: float = Field(default=5.0, ge=0)
    backoff_seconds: float = Field(default=60.0, ge=0)
    cooldown_seconds: float = Field(default=60.0, ge=0)
    late_threshold_seconds: float = Field(default=300.0, ge=0)
    lock_path: Path | None = None
    session_cooldown_path: Path | None = None
    busy_check_script_path: Path = Path("scripts/check_session_vm_busy.sh")
    resume_script_path: Path = Path("scripts/resume_issue_agent.sh")
    wake_state_path: Path | None = None
    vm_user: str = "agent"
    resume_timeout_seconds: float = Field(default=1200.0, gt=0)
    completion_confirmation_timeout_seconds: float = Field(default=300.0, ge=0)
    completion_confirmation_retry_max_attempts: int = Field(default=3, ge=1)
    completion_confirmation_retry_window_seconds: float = Field(
        default=24 * 60 * 60,
        ge=0,
    )
    completion_confirmation_retry_base_backoff_seconds: float = Field(
        default=60.0,
        gt=0,
    )
    completion_confirmation_retry_max_backoff_seconds: float = Field(
        default=600.0,
        gt=0,
    )
    scheduled_wake_prefix_text: str = Field(
        default="",
        validation_alias=AliasChoices(
            "scheduled_wake_prefix_text",
            "wake_scheduled_prefix_text",
        ),
    )

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            normalized.append(DirectoryPath(segments=text).as_posix())
        return normalized

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> ScheduledWakeWorkerGlobalConfig:
        if self.lock_path is None:
            self.lock_path = self.runtime_dir / "scheduled-wake.lock"
        if self.session_cooldown_path is None:
            self.session_cooldown_path = self.runtime_dir / "scheduled-wake-session-cooldown.json"
        if self.wake_state_path is None:
            self.wake_state_path = self.runtime_dir / "scheduled-wake-state.json"
        return self

    @property
    def wake_scheduled_prefix_text(self) -> str:
        return self.scheduled_wake_prefix_text


class TeamsIngestWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    directories: list[str] = Field(default_factory=list)
    publish_trigger_events_directly: bool = True
    poll_interval_seconds: float = Field(default=2.0, gt=0)
    jitter_seconds: float = Field(default=5.0, ge=0)
    backoff_seconds: float = Field(default=60.0, ge=0)
    max_concurrent_target_polls: int = Field(default=1, ge=1, le=32)
    target_backoff_initial_seconds: float = Field(default=60.0, ge=0)
    target_backoff_max_seconds: float = Field(default=900.0, ge=0)
    lock_path: Path | None = None

    @model_validator(mode="after")
    def validate_target_backoff(self) -> TeamsIngestWorkerGlobalConfig:
        if self.target_backoff_max_seconds < self.target_backoff_initial_seconds:
            raise ValueError(
                "target_backoff_max_seconds must be greater than or equal to "
                "target_backoff_initial_seconds"
            )
        return self

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            normalized.append(DirectoryPath(segments=text).as_posix())
        return normalized

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> TeamsIngestWorkerGlobalConfig:
        if self.lock_path is None:
            self.lock_path = self.runtime_dir / "teams-ingest.lock"
        return self


class TeamsMeetingIngestWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    directories: list[str] = Field(default_factory=list)
    poll_interval_seconds: float = Field(default=5.0, gt=0)
    jitter_seconds: float = Field(default=5.0, ge=0)
    backoff_seconds: float = Field(default=60.0, ge=0)
    lock_path: Path | None = None

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            normalized.append(DirectoryPath(segments=text).as_posix())
        return normalized

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> TeamsMeetingIngestWorkerGlobalConfig:
        if self.lock_path is None:
            self.lock_path = self.runtime_dir / "teams-meeting-ingest.lock"
        return self


class OrchestrationCoreConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    poll_interval_seconds: float = Field(default=5.0, gt=0)
    jitter_seconds: float = Field(default=5.0, ge=0)
    orchestration_store_path: Path | None = None
    orchestration_retention_days: int = Field(default=30, ge=0)
    orchestration_compact_history_retention_days: int = Field(default=365, ge=0)
    orchestration_wal_checkpoint_mode: Literal[
        "disabled", "passive", "full", "restart", "truncate"
    ] = "truncate"
    orchestration_wal_checkpoint_min_bytes: int = Field(default=64 * 1024 * 1024, ge=0)
    orchestration_wal_checkpoint_interval_seconds: int = Field(default=5 * 60, ge=0)
    orchestration_wal_checkpoint_busy_timeout_ms: int = Field(default=1_000, ge=0)
    lock_path: Path | None = None
    event_queue_cursor_store_path: Path | None = None
    internal_event_store_path: Path | None = None
    scheduled_internal_event_store_path: Path | None = None
    gate_cursor_store_path: Path | None = None
    effect_cursor_store_path: Path | None = None
    effect_lock_path: Path | None = None
    workflow_packs: list[str] = Field(default_factory=list)
    workflow_graphs: list[WorkflowPackGraph] = Field(default_factory=list)
    workflow_ir: dict[str, Any] | None = None
    workflow_transforms: list[str] = Field(default_factory=list)
    triggers: list[TriggerDefinitionDiscriminated] = Field(default_factory=list)
    gates: list[GateDefinitionDiscriminated] = Field(default_factory=list)
    edges: list[EdgeDefinition] = Field(default_factory=list)
    effects: list[EffectDefinitionDiscriminated] = Field(default_factory=list)
    queries: list[QueryDefinitionDiscriminated] = Field(default_factory=list)
    max_graph_depth: int = Field(default=16, ge=1)
    max_events_per_cycle: int = Field(default=200, ge=1)

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_json_orchestration_backend(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        backend = value.get("orchestration_store_backend")
        if backend is not None and str(backend).strip().casefold() != "sqlite":
            raise ValueError(
                "orchestration_store_backend='json' is no longer supported; "
                "orchestration state is always stored in SQLite"
            )
        return value

    @model_validator(mode="after")
    def validate_unique_graph_node_ids(self) -> OrchestrationCoreConfig:
        node_ids = (
            [node.id for node in self.triggers]
            + [node.id for node in self.gates]
            + [node.id for node in self.effects]
            + [node.id for node in self.queries]
        )
        duplicates = sorted(node_id for node_id, count in Counter(node_ids).items() if count > 1)
        if duplicates:
            duplicate_list = ", ".join(duplicates)
            raise ValueError(
                f"Node IDs must be globally unique across triggers, gates, effects, and queries: {duplicate_list}"
            )
        return self

    @model_validator(mode="after")
    def validate_unique_webhook_trigger_slugs(self) -> OrchestrationCoreConfig:
        slugs = [
            str(getattr(materialize_node_definition(trigger), "slug")).strip()
            for trigger in self.triggers
            if str(trigger.type).strip() == "trigger:http:webhook"
        ]
        duplicates = sorted(slug for slug, count in Counter(slugs).items() if count > 1)
        if duplicates:
            duplicate_list = ", ".join(duplicates)
            raise ValueError(
                f"Webhook trigger slugs must be globally unique across active config: {duplicate_list}"
            )
        return self

    @model_validator(mode="after")
    def validate_graph_edges(self) -> OrchestrationCoreConfig:
        errors = validate_workflow_graph(
            nodes=(*self.triggers, *self.gates, *self.effects, *self.queries),
            edges=self.edges,
            trigger_node_ids=(trigger.id for trigger in self.triggers),
            node_capabilities=None,
        )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @model_validator(mode="after")
    def validate_state_graph_metadata(self) -> OrchestrationCoreConfig:
        resolve_workflow_graph_metadata(
            nodes_by_id={
                node.id: node
                for node in (*self.triggers, *self.gates, *self.effects, *self.queries)
            },
            edges=self.edges,
            workflow_graphs=self.workflow_graphs,
        )
        return self

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> OrchestrationCoreConfig:
        if self.lock_path is None:
            self.lock_path = self.runtime_dir / "trigger-orchestrator.lock"
        if self.orchestration_store_path is None:
            self.orchestration_store_path = self.runtime_dir / "orchestration.db"
        if self.event_queue_cursor_store_path is None:
            self.event_queue_cursor_store_path = (
                self.runtime_dir / "trigger-orchestrator-cursors.json"
            )
        if self.internal_event_store_path is None:
            self.internal_event_store_path = self.runtime_dir / "internal-events.json"
        if self.scheduled_internal_event_store_path is None:
            self.scheduled_internal_event_store_path = runtime_scheduled_internal_event_store_path(
                self.runtime_dir
            )
        if self.gate_cursor_store_path is None:
            self.gate_cursor_store_path = self.runtime_dir / "gate-orchestrator-cursors.json"
        if self.effect_cursor_store_path is None:
            self.effect_cursor_store_path = self.runtime_dir / "effect-orchestrator-cursors.json"
        if self.effect_lock_path is None:
            self.effect_lock_path = self.runtime_dir / "effect-orchestrator.lock"
        return self


class OrchestrationProviderLoaderConfig(BaseModel):
    provider_profile: str = DEFAULT_PROVIDER_PROFILE_NAME
    include_builtin: bool | None = None
    provider_modules: list[str] = Field(default_factory=list)
    provider_entry_points_enabled: bool = True

    @field_validator("provider_profile")
    @classmethod
    def validate_provider_profile(cls, value: str) -> str:
        return resolve_provider_profile(value).name


class TriggerOrchestratorWorkerGlobalConfig(OrchestrationCoreConfig):
    provider_loader: OrchestrationProviderLoaderConfig = Field(
        default_factory=OrchestrationProviderLoaderConfig
    )
    addon_loader: AddonLoaderConfig = Field(default_factory=AddonLoaderConfig)

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_provider_loader_settings(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        legacy_keys = [
            key for key in ("provider_modules", "provider_entry_points_enabled") if key in value
        ]
        if legacy_keys:
            key_list = ", ".join(legacy_keys)
            raise ValueError(
                f"Legacy trigger-orchestrator config keys are no longer supported: {key_list}. "
                "Move these keys under provider_loader."
            )
        return value

    @property
    def provider_modules(self) -> list[str]:
        return list(self.provider_loader.provider_modules)

    @property
    def provider_profile(self) -> str:
        return self.provider_loader.provider_profile

    @property
    def provider_include_builtin(self) -> bool:
        override = self.provider_loader.include_builtin
        if override is not None:
            return override
        profile = resolve_provider_profile(self.provider_loader.provider_profile)
        return profile.include_builtin

    @property
    def provider_entry_points_enabled(self) -> bool:
        return self.provider_loader.provider_entry_points_enabled
