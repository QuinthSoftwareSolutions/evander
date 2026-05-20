from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

from agent_sandbox.config import GatewayConfig, load_config
from agent_sandbox.directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.models import (
    SessionManifest,
    ToolArgumentPolicyRule,
    ToolName,
    normalize_tool_argument_policies,
    normalize_tool_names,
    validate_tool_argument_policy_map,
)
from agent_sandbox.repositories.session_manifests import (
    JsonSessionManifestRepository,
    SessionManifestRepository,
    as_session_manifest_repository,
)


class EffectivePolicyConfig(BaseModel):
    approval_ttl_seconds: int = 3600
    approval_notification_target_ids: list[str] = Field(default_factory=list)
    default_session_ttl_seconds: int = 28800
    discord_operator_channel_allowlist: list[str] = Field(default_factory=list)
    discord_operator_user_allowlist: list[str] = Field(default_factory=list)
    discord_poll_operator_dms: bool = False
    discord_inbox_routes: dict[str, list[str]] = Field(default_factory=dict)
    email_draft_domain_allowlist: list[str] = Field(default_factory=list)
    email_draft_recipient_allowlist: list[str] = Field(default_factory=list)
    email_folder_allowlist_by_mailbox: dict[str, list[str]] = Field(default_factory=dict)
    email_mailbox_allowlist: list[str] = Field(default_factory=list)
    email_send_enabled: bool = False
    communication_file_receive_enabled: bool = False
    communication_file_send_enabled: bool = False
    communication_file_max_bytes: int | None = 10 * 1024 * 1024
    communication_file_allowed_mime_types: list[str] = Field(default_factory=list)
    communication_file_allowed_extensions: list[str] = Field(default_factory=list)
    communication_file_blocked_extensions: list[str] = Field(default_factory=list)
    communication_file_require_approval_for_send: bool = False
    pr_reply_require_approval: bool = False
    approval_required_tools: list[ToolName] = Field(default_factory=list)
    tool_argument_policies: dict[str, list[ToolArgumentPolicyRule]] = Field(default_factory=dict)
    redacted_keys: set[str] = Field(
        default_factory=lambda: {
            "body",
            "body_text",
            "authorization",
            "token",
            "response_token",
            "password",
            "connection_string",
            "connectionString",
        }
    )

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


class PolicyConfigResolver:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        directory_store: DirectoryStore | None = None,
        session_store: SessionManifestRepository | None = None,
    ) -> None:
        self.config = config
        self.directory_store = directory_store or DirectoryStore(config.directories_store_path)
        self.directory_store.ensure_root()
        self.directory_service = DirectoryService(self.directory_store)
        self.config_store = DirectoryConfigStore(
            self.directory_store, runtime_dir=config.runtime_dir
        )
        raw_session_store = session_store or JsonSessionManifestRepository(
            config.session_store_path
        )
        self.session_store = as_session_manifest_repository(raw_session_store)
        self.resolver = DirectoryConfigResolver(
            self.directory_service,
            base_gateway_config=config,
            config_store=self.config_store,
        )

    @classmethod
    def from_config_path(cls, path: str | Path) -> "PolicyConfigResolver":
        return cls(load_config(path))

    def resolve_for_directory(self, directory_path: str | None) -> EffectivePolicyConfig:
        normalized_path = self._normalize_directory_path(directory_path)
        resolution = self.resolver.resolve_domain(
            normalized_path,
            domain="policy",
            base_config=get_domain_base_config(self.config, "policy"),
            validator=lambda candidate: validate_effective_domain_payload("policy", candidate),
        )
        return EffectivePolicyConfig.model_validate(resolution.effective_config)

    def resolve_for_session_id(self, session_id: str) -> EffectivePolicyConfig:
        session = self.session_store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        return self.resolve_for_session_manifest(session)

    def resolve_for_session_manifest(self, session: SessionManifest) -> EffectivePolicyConfig:
        return self.resolve_for_directory(session.directory_path)

    def active_session_policies(self) -> list[EffectivePolicyConfig]:
        now = datetime.now(UTC)
        directories: set[str] = {"root"}
        for session in self.session_store.list_active_sessions(now=now):
            directories.add(self._normalize_directory_path(session.directory_path))
        return [self.resolve_for_directory(path) for path in sorted(directories)]

    def allowed_discord_channel_ids_for_active_sessions(self) -> list[str]:
        now = datetime.now(UTC)
        channel_ids: set[str] = set()
        for policy in self.active_session_policies():
            channel_ids.update(policy.discord_operator_channel_allowlist)
        for session in self.session_store.list_active_sessions(now=now):
            if ToolName.DISCORD_GET_COMMANDS not in session.allowed_tools:
                continue
            channel_ids.update(session.discord_channel_allowlist)
        return sorted(channel_id.strip() for channel_id in channel_ids if channel_id.strip())

    def allowed_discord_channel_ids_for_session_id(self, session_id: str) -> list[str]:
        session = self.session_store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        policy = self.resolve_for_session_manifest(session)
        channel_ids = set(policy.discord_operator_channel_allowlist)
        if ToolName.DISCORD_GET_COMMANDS in session.allowed_tools:
            channel_ids.update(session.discord_channel_allowlist)
        return sorted(channel_id.strip() for channel_id in channel_ids if channel_id.strip())

    def operator_user_ids_for_active_sessions(self) -> list[str]:
        user_ids: set[str] = set()
        for policy in self.active_session_policies():
            user_ids.update(policy.discord_operator_user_allowlist)
        return sorted(user_id.strip() for user_id in user_ids if user_id.strip())

    def operator_user_ids_for_session_id(self, session_id: str) -> list[str]:
        policy = self.resolve_for_session_id(session_id)
        return sorted(
            user_id.strip() for user_id in policy.discord_operator_user_allowlist if user_id.strip()
        )

    def include_operator_dms_for_active_sessions(self) -> bool:
        return any(policy.discord_poll_operator_dms for policy in self.active_session_policies())

    def include_operator_dms_for_session_id(self, session_id: str) -> bool:
        return self.resolve_for_session_id(session_id).discord_poll_operator_dms

    def discord_inbox_routes_for_directory(
        self, directory_path: str | None
    ) -> dict[str, list[str]]:
        policy = self.resolve_for_directory(directory_path)
        return {
            str(channel_id).strip(): [
                str(session_id).strip() for session_id in session_ids if str(session_id).strip()
            ]
            for channel_id, session_ids in policy.discord_inbox_routes.items()
            if str(channel_id).strip()
        }

    def discord_inbox_channel_ids_for_session_id(self, session_id: str) -> list[str]:
        routes = self.resolve_for_session_id(session_id).discord_inbox_routes
        return sorted(
            channel_id.strip()
            for channel_id, target_session_ids in routes.items()
            if channel_id.strip()
            and session_id in {str(candidate).strip() for candidate in target_session_ids}
        )

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        try:
            return self.directory_service.show(directory_path).directory.path
        except ValueError:
            return "root"
