from __future__ import annotations

from collections import Counter
from ipaddress import ip_network
from pathlib import Path
from typing import Literal

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

from .models import DirectoryPath

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


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
    folder_mapping: dict[str, str] = Field(default_factory=lambda: {"inbox": "inbox", "drafts": "drafts"})
    folder_allowlist_defaults: list[str] = Field(default_factory=lambda: ["inbox", "drafts"])


class GatewayConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    approval_ttl_seconds: int = 3600
    runtime_dir: Path = Path("/var/lib/agent-sandbox")
    audit_log_path: Path = Path("/var/log/agent-sandbox/audit.jsonl")
    session_store_path: Path = Path("/var/lib/agent-sandbox/sessions.json")
    approval_store_path: Path = Path("/var/lib/agent-sandbox/approvals.json")
    draft_store_path: Path = Path("/var/lib/agent-sandbox/drafts.json")
    email_store_path: Path = Path("/var/lib/agent-sandbox/emails.json")
    email_cursor_store_path: Path = Path("/var/lib/agent-sandbox/email_cursors.json")
    discord_command_store_path: Path = Path("/var/lib/agent-sandbox/discord_commands.json")
    directories_store_path: Path = Path("/var/lib/agent-sandbox/directories.json")
    enable_directory_scoped_config: bool = False
    discord_backend: Literal["stub", "bot_api"] = "stub"
    discord_bot_token_env: str = Field(default="DISCORD_BOT_TOKEN", min_length=1)
    discord_api_base_url: str = "https://discord.com/api/v10"
    discord_poll_limit: int = Field(default=50, ge=1, le=100)
    discord_timeout_seconds: float = Field(default=15.0, gt=0)
    discord_retry_count: int = Field(default=3, ge=0, le=10)
    discord_retry_base_backoff_seconds: float = Field(default=0.5, gt=0)
    discord_retry_max_backoff_seconds: float = Field(default=5.0, gt=0)
    discord_operator_user_allowlist: list[str] = Field(default_factory=list)
    discord_operator_channel_allowlist: list[str] = Field(default_factory=list)
    discord_poll_operator_dms: bool = False
    discord_inbox_routes: dict[str, list[str]] = Field(default_factory=dict)
    bind_network_cidr: IPvAnyNetwork = "192.168.250.0/24"
    gateway_url: str = "http://192.168.250.1:8000/mcp"
    openai_relay_host: str = "0.0.0.0"
    openai_relay_port: int = 11434
    openai_api_base_url: str = "https://api.openai.com/v1"
    openai_api_key_env: str = "OPENAI_API_KEY"
    openai_relay_timeout_seconds: float = 300.0
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
    pr_reply_require_approval: bool = False
    github_token_env: str = "GITHUB_TOKEN"
    azure_devops_pat_env: str = "AZURE_DEVOPS_PAT"
    azure_devops_pr_pat_env: str = "AZURE_DEVOPS_PR_PAT"
    azure_devops_organization_url: str = "https://dev.azure.com"
    azure_devops_project: str = "default"
    ado_area_filters: list[str] = Field(default_factory=list)
    ado_team_filters: list[str] = Field(default_factory=list)
    ado_project_or_solution: str = ""
    ado_prewarm_target: str | None = None
    ado_bootstrap_dotnet: bool = True
    ado_bootstrap_nuget: bool = True
    ado_bootstrap_mssql: bool = True
    ado_mssql_init_root: str | None = None
    ado_bootstrap_playwright: bool = False
    ado_bootstrap_frontend_deps: bool = False
    ado_frontend_host_checkout: str | None = None
    ado_frontend_install_roots: list[str] = Field(default_factory=list)
    ado_mssql_sa_password_env: str | None = None
    email_provider_timeout_seconds: float = Field(default=30.0, gt=0)
    host_heartbeat_poll_interval_seconds: float = Field(default=30.0, gt=0)
    host_heartbeat_jitter_seconds: float = Field(default=5.0, ge=0)
    host_heartbeat_cooldown_seconds: float = Field(default=60.0, ge=0)
    host_heartbeat_http_timeout_seconds: float = Field(default=30.0, gt=0)
    host_heartbeat_lock_path: Path = Path("/var/lib/agent-sandbox/host-heartbeat.lock")
    host_heartbeat_session_cooldown_path: Path = Path(
        "/var/lib/agent-sandbox/host-heartbeat-session-cooldown.json"
    )
    host_agent_control_state_path: Path = Path("/var/lib/agent-sandbox/host-agent-control-state.json")
    host_busy_check_script_path: Path = Path("scripts/check_session_vm_busy.sh")
    host_resume_script_path: Path = Path("scripts/resume_issue_agent.sh")
    host_heartbeat_wake_state_path: Path = Path("/var/lib/agent-sandbox/host-heartbeat-wake-state.json")
    host_heartbeat_pr_resume_prompt: str = (
        "You have unread PR comments. Please fetch them with list_unread_feedback."
    )
    host_heartbeat_discord_resume_prompt: str = (
        "You have unread Discord messages. Please fetch them with "
        "list_unread_discord_messages and respond with discord_post_status if needed."
    )
    host_heartbeat_vm_user: str = "agent"
    host_heartbeat_resume_timeout_seconds: float = Field(default=1200.0, gt=0)
    host_heartbeat_stream_conflict_policy: Literal["fanout", "reject"] = "fanout"
    pr_map_path: Path = Path("/var/lib/agent-sandbox/pr-map.json")
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
    redacted_keys: set[str] = Field(
        default_factory=lambda: {"body", "body_text", "authorization", "token"}
    )

    @field_validator(
        "gateway_url",
        "discord_api_base_url",
        "openai_api_base_url",
        "nuget_public_feed_base_url",
        "nuget_azure_feed_base_url",
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

    @field_validator(
        "gateway_url",
        "discord_api_base_url",
        "openai_api_base_url",
        "nuget_public_feed_base_url",
        "nuget_azure_feed_base_url",
    )
    @classmethod
    def validate_http_url_fields(cls, value: str, info) -> str:
        try:
            _HTTP_URL_ADAPTER.validate_python(value)
        except ValidationError as exc:
            raise ValueError(
                f"{info.field_name} must be a valid http(s) URL, got {value!r}"
            ) from exc
        return value

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
            "host_heartbeat_lock_path": default_runtime_dir / "host-heartbeat.lock",
            "host_heartbeat_session_cooldown_path": default_runtime_dir
            / "host-heartbeat-session-cooldown.json",
            "host_agent_control_state_path": default_runtime_dir / "host-agent-control-state.json",
            "host_heartbeat_wake_state_path": default_runtime_dir / "host-heartbeat-wake-state.json",
            "session_store_path": default_runtime_dir / "sessions.json",
            "approval_store_path": default_runtime_dir / "approvals.json",
            "draft_store_path": default_runtime_dir / "drafts.json",
            "email_store_path": default_runtime_dir / "emails.json",
            "email_cursor_store_path": default_runtime_dir / "email_cursors.json",
            "discord_command_store_path": default_runtime_dir / "discord_commands.json",
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
        duplicates = sorted(mailbox_id for mailbox_id, count in Counter(mailbox_ids).items() if count > 1)
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

        return self


class AdoWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    poll_interval_seconds: float = Field(default=30.0, gt=0)
    directories: list[str] = Field(default_factory=list)
    yaml_output_dir: Path | None = None
    setup_log_dir: Path | None = None
    processed_store_path: Path | None = None
    setup_script_path: Path = Path("scripts/setup_issue_vm.sh")
    ado_work_items_azure_devops_pat_env: str = "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    setup_timeout_seconds: float = Field(default=1800.0, gt=0)
    setup_concurrency_limit: int = Field(default=1, ge=1)
    setup_lock_path: Path | None = None
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
        if self.setup_log_dir is None:
            self.setup_log_dir = self.runtime_dir / "setup-logs"
        if self.processed_store_path is None:
            self.processed_store_path = self.runtime_dir / "ado-processed-work-items.json"
        if self.setup_lock_path is None:
            self.setup_lock_path = self.runtime_dir / "setup-issue-vm.lock"
        return self


class HostHeartbeatWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    host_heartbeat_directory_paths: list[str] = Field(default_factory=list)
    host_heartbeat_poll_interval_seconds: float = Field(default=30.0, gt=0)
    host_heartbeat_jitter_seconds: float = Field(default=5.0, ge=0)
    host_heartbeat_cooldown_seconds: float = Field(default=60.0, ge=0)
    host_heartbeat_http_timeout_seconds: float = Field(default=30.0, gt=0)
    host_heartbeat_lock_path: Path | None = None
    host_heartbeat_session_cooldown_path: Path | None = None
    host_busy_check_script_path: Path = Path("scripts/check_session_vm_busy.sh")
    host_resume_script_path: Path = Path("scripts/resume_issue_agent.sh")
    host_heartbeat_wake_state_path: Path | None = None
    host_heartbeat_vm_user: str = "agent"
    host_heartbeat_resume_timeout_seconds: float = Field(default=1200.0, gt=0)
    host_heartbeat_stream_conflict_policy: Literal["fanout", "reject"] = "fanout"

    @field_validator("host_heartbeat_directory_paths", mode="before")
    @classmethod
    def normalize_host_heartbeat_directory_paths(cls, value: object) -> object:
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
    def apply_runtime_path_defaults(self) -> HostHeartbeatWorkerGlobalConfig:
        if self.host_heartbeat_lock_path is None:
            self.host_heartbeat_lock_path = self.runtime_dir / "host-heartbeat.lock"
        if self.host_heartbeat_session_cooldown_path is None:
            self.host_heartbeat_session_cooldown_path = (
                self.runtime_dir / "host-heartbeat-session-cooldown.json"
            )
        if self.host_heartbeat_wake_state_path is None:
            self.host_heartbeat_wake_state_path = self.runtime_dir / "host-heartbeat-wake-state.json"
        return self


class IssueOrchestratorWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    directories: list[str] = Field(default_factory=list)
    poll_interval_seconds: float = Field(default=30.0, gt=0)
    jitter_seconds: float = Field(default=5.0, ge=0)
    cooldown_seconds: float = Field(default=60.0, ge=0)
    lock_path: Path | None = None
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
    def apply_runtime_path_defaults(self) -> IssueOrchestratorWorkerGlobalConfig:
        if self.lock_path is None:
            self.lock_path = self.runtime_dir / "issue-orchestrator.lock"
        if self.wake_state_path is None:
            self.wake_state_path = self.runtime_dir / "issue-orchestrator-wake-state.json"
        return self


class DiscordIngestWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    directories: list[str] = Field(default_factory=list)
    poll_interval_seconds: float = Field(default=30.0, gt=0)
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


def load_config(path: str | Path) -> GatewayConfig:
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return GatewayConfig.model_validate(data)
