from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .config import GatewayConfig, load_config
from .directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from .directories import DirectoryService, DirectoryStore
from .models import SessionManifest, ToolName
from .storage import JsonStore


class EffectivePolicyConfig(BaseModel):
    default_session_ttl_seconds: int = 28800
    discord_operator_channel_allowlist: list[str] = Field(default_factory=list)
    discord_operator_user_allowlist: list[str] = Field(default_factory=list)
    discord_poll_operator_dms: bool = False
    email_draft_domain_allowlist: list[str] = Field(default_factory=list)
    email_draft_recipient_allowlist: list[str] = Field(default_factory=list)
    email_folder_allowlist_by_mailbox: dict[str, list[str]] = Field(default_factory=dict)
    email_mailbox_allowlist: list[str] = Field(default_factory=list)
    email_send_enabled: bool = False
    pr_reply_require_approval: bool = False
    redacted_keys: set[str] = Field(
        default_factory=lambda: {"body", "body_text", "authorization", "token"}
    )


class PolicyConfigResolver:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        directory_store: DirectoryStore | None = None,
        session_store: JsonStore[SessionManifest] | None = None,
    ) -> None:
        self.config = config
        self.directory_store = directory_store or DirectoryStore(config.directories_store_path)
        self.directory_store.ensure_root()
        self.directory_service = DirectoryService(self.directory_store)
        self.config_store = DirectoryConfigStore(self.directory_store, runtime_dir=config.runtime_dir)
        self.session_store = session_store or JsonStore(config.session_store_path, SessionManifest)
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
        session = self.session_store.get(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        return self.resolve_for_session_manifest(session)

    def resolve_for_session_manifest(self, session: SessionManifest) -> EffectivePolicyConfig:
        return self.resolve_for_directory(session.directory_path)

    def active_session_policies(self) -> list[EffectivePolicyConfig]:
        now = datetime.now(UTC)
        directories: set[str] = {"root"}
        for session in self.session_store.read_all().values():
            if session.expires_at <= now:
                continue
            directories.add(self._normalize_directory_path(session.directory_path))
        return [self.resolve_for_directory(path) for path in sorted(directories)]

    def allowed_discord_channel_ids_for_active_sessions(self) -> list[str]:
        now = datetime.now(UTC)
        channel_ids: set[str] = set()
        for policy in self.active_session_policies():
            channel_ids.update(policy.discord_operator_channel_allowlist)
        for session in self.session_store.read_all().values():
            if session.expires_at <= now:
                continue
            if ToolName.DISCORD_GET_COMMANDS not in session.allowed_tools:
                continue
            channel_ids.update(session.discord_channel_allowlist)
        return sorted(channel_id.strip() for channel_id in channel_ids if channel_id.strip())

    def operator_user_ids_for_active_sessions(self) -> list[str]:
        user_ids: set[str] = set()
        for policy in self.active_session_policies():
            user_ids.update(policy.discord_operator_user_allowlist)
        return sorted(user_id.strip() for user_id in user_ids if user_id.strip())

    def include_operator_dms_for_active_sessions(self) -> bool:
        return any(policy.discord_poll_operator_dms for policy in self.active_session_policies())

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        try:
            return self.directory_service.show(directory_path).directory.path
        except ValueError:
            return "root"
