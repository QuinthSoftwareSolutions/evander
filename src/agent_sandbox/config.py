from __future__ import annotations

from collections import Counter
from ipaddress import ip_network
from pathlib import Path
from typing import Any, Literal
import unicodedata

import yaml
from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BaseModel,
    Field,
    IPvAnyNetwork,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from agent_sandbox.models import (
    DirectoryPath,
    ToolArgumentPolicyRule,
    ToolName,
    normalize_tool_argument_policies,
    normalize_tool_names,
    validate_tool_argument_policy_map,
)
from agent_sandbox.provider_profiles import DEFAULT_PROVIDER_PROFILE_NAME, resolve_provider_profile
from agent_sandbox.events.models import (
    EdgeDefinition,
    EffectDefinitionDiscriminated,
    GateDefinitionDiscriminated,
    QueryDefinitionDiscriminated,
    materialize_node_definition,
    TriggerDefinitionDiscriminated,
)
from agent_sandbox.schema_bootstrap import bootstrap_default_node_schemas
from agent_sandbox.workflow.validation import validate_workflow_graph

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)
bootstrap_default_node_schemas()


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


AssistantCapabilityProfileName = Literal[
    "", "read-only", "operator-limited", "full-admin"
]


class AssistantSettingsConfig(BaseModel):
    enabled: bool = False
    show_launcher_when_disabled: bool = False
    default_model: str | None = None
    workflow_generation_model: str | None = None
    default_profile: AssistantCapabilityProfileName | None = None
    read_only: bool = True
    custom_system_instructions: str | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_write_tool_profile(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        payload = dict(data)
        legacy_profile = str(payload.pop("allowed_mcp_tool_profile", "") or "").strip()
        current_profile = str(payload.get("default_profile") or "").strip()
        if legacy_profile and not current_profile:
            payload["default_profile"] = legacy_profile
        return payload

    @field_validator(
        "default_model", "workflow_generation_model", "default_profile", "custom_system_instructions"
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized or None


class GatewayConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    approval_ttl_seconds: int = 3600
    approval_notification_target_ids: list[str] = Field(default_factory=list)
    runtime_dir: Path = Path("/var/lib/agent-sandbox")
    audit_log_path: Path = Path("/var/log/agent-sandbox/audit.jsonl")
    session_store_path: Path = Path("/var/lib/agent-sandbox/sessions.json")
    approval_store_path: Path = Path("/var/lib/agent-sandbox/approvals.json")
    draft_store_path: Path = Path("/var/lib/agent-sandbox/drafts.json")
    email_store_path: Path = Path("/var/lib/agent-sandbox/emails.json")
    email_cursor_store_path: Path = Path("/var/lib/agent-sandbox/email_cursors.json")
    discord_command_store_path: Path = Path("/var/lib/agent-sandbox/discord_commands.json")
    teams_message_store_path: Path = Path("/var/lib/agent-sandbox/teams_messages.json")
    directories_store_path: Path = Path("/var/lib/agent-sandbox/directories.json")
    enable_directory_scoped_config: bool = False
    execution_backend: Literal["libvirt_vm", "container_worker_vm"] = "libvirt_vm"
    container_worker_base_url: str | None = None
    container_worker_token_env: str = Field(default="AGENT_SANDBOX_WORKER_TOKEN", min_length=1)
    container_worker_default_image: str = Field(default="agent-sandbox-runner:latest", min_length=1)
    container_worker_job_timeout_seconds: float = Field(default=1800.0, gt=0)
    container_worker_request_timeout_seconds: float = Field(default=30.0, gt=0)
    container_worker_workspace_persistence: str = Field(default="session", min_length=1)
    container_worker_guest_openai_base_url: str | None = None
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
    teams_meeting_store_path: Path = Path("/var/lib/agent-sandbox/teams-meetings.json")
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
        "/var/lib/agent-sandbox/teams-delegated-credentials.json"
    )
    retell_api_base_url: str = "https://api.retellai.com"
    retell_api_key_env: str = Field(default="RETELL_API_KEY", min_length=1)
    retell_from_number: str | None = None
    retell_default_agent_id: str | None = None
    retell_request_timeout_seconds: float = Field(default=30.0, gt=0)
    discord_inbox_routes: dict[str, list[str]] = Field(default_factory=dict)
    bind_network_cidr: IPvAnyNetwork = "192.168.250.0/24"
    gateway_url: str = "http://192.168.250.1:8000/mcp"
    openai_relay_host: str = "0.0.0.0"
    openai_relay_port: int = 11434
    openai_api_base_url: str = "https://api.openai.com/v1"
    openai_api_key_env: str = "OPENAI_API_KEY"
    openai_relay_timeout_seconds: float = 300.0
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
    pr_reply_require_approval: bool = False
    approval_required_tools: list[ToolName] = Field(default_factory=list)
    tool_argument_policies: dict[str, list[ToolArgumentPolicyRule]] = Field(default_factory=dict)
    github_token_env: str = "GITHUB_TOKEN"
    agent_prompt_token_secret_env: str = "AGENT_PROMPT_TOKEN_SECRET"
    agent_prompt_token_ttl_seconds: int = Field(default=3600, ge=1)
    azure_devops_pat_env: str = "AZURE_DEVOPS_PAT"
    azure_devops_pr_pat_env: str = "AZURE_DEVOPS_PR_PAT"
    azure_devops_organization_url: str = "https://dev.azure.com"
    azure_devops_project: str = "default"
    ado_area_filters: list[str] = Field(default_factory=list)
    ado_team_filters: list[str] = Field(default_factory=list)
    email_provider_timeout_seconds: float = Field(default=30.0, gt=0)
    wake_poll_interval_seconds: float = Field(default=30.0, gt=0)
    wake_jitter_seconds: float = Field(default=5.0, ge=0)
    wake_cooldown_seconds: float = Field(default=60.0, ge=0)
    wake_session_cooldown_path: Path = Path(
        "/var/lib/agent-sandbox/wake-session-cooldown.json"
    )
    host_agent_control_state_path: Path = Path(
        "/var/lib/agent-sandbox/host-agent-control-state.json"
    )
    host_busy_check_script_path: Path = Path("scripts/check_session_vm_busy.sh")
    host_resume_script_path: Path = Path("scripts/resume_issue_agent.sh")
    wake_dispatch_state_path: Path = Path(
        "/var/lib/agent-sandbox/wake-dispatch-state.json"
    )
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
    wake_scheduled_prefix_text: str = (
        "You have unread PR comments. Please fetch them with list_unread_feedback."
    )
    wake_schedule_max_active_per_session: int | None = Field(default=None, ge=1)
    wake_schedule_max_active_total: int | None = Field(default=None, ge=1)
    wake_schedule_min_interval_seconds: float | None = Field(default=None, gt=0)
    pr_map_path: Path = Path("/var/lib/agent-sandbox/pr-map.json")
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
    redacted_keys: set[str] = Field(
        default_factory=lambda: {
            "body",
            "body_text",
            "prompt",
            "authorization",
            "token",
            "api_key",
            "response_token",
            "parameters",
            "password",
            "connection_string",
            "connectionString",
        }
    )

    @field_validator(
        "gateway_url",
        "discord_api_base_url",
        "teams_graph_api_base_url",
        "teams_meeting_media_bridge_url",
        "teams_delegated_authority_base_url",
        "container_worker_guest_openai_base_url",
        "container_worker_guest_gateway_url",
        "container_worker_guest_http_target_relay_base_url",
        "container_worker_guest_nuget_relay_base_url",
        "container_worker_guest_pypi_index_url",
        "container_worker_guest_npm_registry_url",
        "openai_api_base_url",
        "qdrant_base_url",
        "nuget_public_feed_base_url",
        "nuget_azure_feed_base_url",
        "retell_api_base_url",
        mode="before",
    )
    @classmethod
    def normalize_http_url_fields(cls, value: object, info) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError(f"{info.field_name} cannot be empty")
        return normalized

    @field_validator("approval_required_tools", mode="before")
    @classmethod
    def normalize_approval_required_tools(cls, value: object) -> list[ToolName]:
        return normalize_tool_names(value, field_name="approval_required_tools")

    @field_validator("tool_argument_policies", mode="before")
    @classmethod
    def normalize_tool_argument_policies_field(
        cls, value: object
    ) -> dict[str, list[object]]:
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
        "container_worker_guest_gateway_url",
        "container_worker_guest_http_target_relay_base_url",
        "container_worker_guest_nuget_relay_base_url",
        "container_worker_guest_pypi_index_url",
        "container_worker_guest_npm_registry_url",
        "openai_api_base_url",
        "qdrant_base_url",
        "nuget_public_feed_base_url",
        "nuget_azure_feed_base_url",
        "retell_api_base_url",
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
        from agent_sandbox.semantic.settings import normalize_vector_distance

        return normalize_vector_distance(value)

    @field_validator("approval_notification_target_ids", mode="before")
    @classmethod
    def normalize_approval_notification_target_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError("approval_notification_target_ids must be a JSON array of strings")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            target_id = str(item or "").strip().lower()
            if not target_id or target_id in seen:
                continue
            seen.add(target_id)
            normalized.append(target_id)
        return normalized

    @field_validator("qdrant_api_key_env", mode="before")
    @classmethod
    def normalize_qdrant_api_key_env(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("retell_from_number", "retell_default_agent_id", mode="before")
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
        default_runtime_dir = Path("/var/lib/agent-sandbox")
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
            "teams_message_store_path": default_runtime_dir / "teams_messages.json",
            "teams_meeting_store_path": default_runtime_dir / "teams-meetings.json",
            "teams_delegated_credential_store_path": default_runtime_dir
            / "teams-delegated-credentials.json",
            "directories_store_path": default_runtime_dir / "directories.json",
            "pr_map_path": default_runtime_dir / "pr-map.json",
        }
        for field_name, default_path in default_paths.items():
            if getattr(self, field_name) == default_path:
                setattr(self, field_name, self.runtime_dir / default_path.name)
        if self.audit_log_path == Path("/var/log/agent-sandbox/audit.jsonl"):
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


class AdoWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    poll_interval_seconds: float = Field(default=5.0, gt=0)
    directories: list[str] = Field(default_factory=list)
    yaml_output_dir: Path | None = None
    processed_store_path: Path | None = None
    ado_work_items_azure_devops_pat_env: str = "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    fetch_retry_count: int = Field(default=2, ge=0)
    fetch_retry_backoff_seconds: float = Field(default=2.0, ge=0.0)
    include_comments: bool = True
    comments_max_count: int = Field(default=20, ge=1)
    comment_text_max_chars: int = Field(default=4000, ge=1)

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
    def apply_runtime_path_defaults(self) -> AdoWorkerGlobalConfig:
        if self.yaml_output_dir is None:
            self.yaml_output_dir = self.runtime_dir / "ado-work-items"
        if self.processed_store_path is None:
            self.processed_store_path = self.runtime_dir / "ado-processed-work-items.json"
        return self


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
    lock_path: Path | None = None
    session_cooldown_path: Path | None = None
    busy_check_script_path: Path = Path("scripts/check_session_vm_busy.sh")
    resume_script_path: Path = Path("scripts/resume_issue_agent.sh")
    wake_state_path: Path | None = None
    vm_user: str = "agent"
    resume_timeout_seconds: float = Field(default=1200.0, gt=0)
    scheduled_wake_prefix_text: str = (
        "You have unread PR comments. Please fetch them with list_unread_feedback."
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


class TeamsIngestWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    directories: list[str] = Field(default_factory=list)
    poll_interval_seconds: float = Field(default=2.0, gt=0)
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
    orchestration_store_backend: Literal["sqlite", "json"] = "sqlite"
    orchestration_store_path: Path | None = None
    orchestration_retention_days: int = Field(default=30, ge=0)
    lock_path: Path | None = None
    event_queue_store_path: Path | None = None
    event_queue_cursor_store_path: Path | None = None
    workflow_event_journal_path: Path | None = None
    internal_event_store_path: Path | None = None
    gate_execution_store_path: Path | None = None
    gate_cursor_store_path: Path | None = None
    effect_execution_store_path: Path | None = None
    effect_cursor_store_path: Path | None = None
    effect_lock_path: Path | None = None
    workflow_config_snapshot_store_path: Path | None = None
    workflow_packs: list[str] = Field(default_factory=list)
    workflow_ir: dict[str, Any] | None = None
    workflow_transforms: list[str] = Field(default_factory=list)
    triggers: list[TriggerDefinitionDiscriminated] = Field(default_factory=list)
    gates: list[GateDefinitionDiscriminated] = Field(default_factory=list)
    edges: list[EdgeDefinition] = Field(default_factory=list)
    effects: list[EffectDefinitionDiscriminated] = Field(default_factory=list)
    queries: list[QueryDefinitionDiscriminated] = Field(default_factory=list)
    max_graph_depth: int = Field(default=16, ge=1)
    max_events_per_cycle: int = Field(default=200, ge=1)

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
    def apply_runtime_path_defaults(self) -> OrchestrationCoreConfig:
        if self.lock_path is None:
            self.lock_path = self.runtime_dir / "trigger-orchestrator.lock"
        if self.event_queue_store_path is None:
            self.event_queue_store_path = self.runtime_dir / "trigger-orchestrator-events.json"
        if self.orchestration_store_path is None:
            self.orchestration_store_path = self.runtime_dir / "orchestration.db"
        if self.event_queue_cursor_store_path is None:
            self.event_queue_cursor_store_path = (
                self.runtime_dir / "trigger-orchestrator-cursors.json"
            )
        if self.workflow_event_journal_path is None:
            self.workflow_event_journal_path = self.runtime_dir / "workflow-events.jsonl"
        if self.internal_event_store_path is None:
            self.internal_event_store_path = self.runtime_dir / "internal-events.json"
        if self.gate_execution_store_path is None:
            self.gate_execution_store_path = self.runtime_dir / "gate-orchestrator-executions.json"
        if self.gate_cursor_store_path is None:
            self.gate_cursor_store_path = self.runtime_dir / "gate-orchestrator-cursors.json"
        if self.effect_execution_store_path is None:
            self.effect_execution_store_path = (
                self.runtime_dir / "effect-orchestrator-executions.json"
            )
        if self.effect_cursor_store_path is None:
            self.effect_cursor_store_path = self.runtime_dir / "effect-orchestrator-cursors.json"
        if self.effect_lock_path is None:
            self.effect_lock_path = self.runtime_dir / "effect-orchestrator.lock"
        if self.workflow_config_snapshot_store_path is None:
            self.workflow_config_snapshot_store_path = (
                self.runtime_dir / "workflow-config-snapshots.json"
            )
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


def load_config(path: str | Path) -> GatewayConfig:
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return GatewayConfig.model_validate(data)
