from __future__ import annotations

import logging
from datetime import UTC, datetime

from pydantic import BaseModel, Field, field_validator

from evander.approval_notification_targets import (
    normalize_approval_notification_target_ids,
)
from evander.config import GatewayConfig
from evander.directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    DirectoryScopeResolver,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from evander.directories import DirectoryService, DirectoryStore
from evander.messaging.targets import (
    CommunicationTargetCapability,
    CommunicationTargetDefinition,
    load_communication_targets_config,
)
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.models import (
    McpToolPolicyRule,
    McpToolName,
    SessionManifest,
    ToolArgumentPolicyRule,
    ToolName,
    normalize_mcp_tool_policies,
    normalize_tool_name,
    normalize_tool_argument_policies,
    normalize_tool_names,
    tool_name_value,
    validate_tool_argument_policy_map,
)
from evander.repositories.session_manifests import (
    JsonSessionManifestRepository,
    SessionManifestRepository,
    as_session_manifest_repository,
)
from evander.services import (
    normalize_teams_target,
    normalize_teams_user_target,
    parse_teams_target,
)

logger = logging.getLogger(__name__)


def _apply_inherited_mcp_tool_denies(
    effective_config: dict[str, object], overlays: list[dict[str, object]]
) -> dict[str, object]:
    denied_tools: set[str] = set()
    for overlay in overlays:
        policies = overlay.get("mcp_tool_policies")
        if not isinstance(policies, dict):
            continue
        for raw_tool_name, raw_policy in policies.items():
            if not isinstance(raw_policy, dict):
                continue
            denied = raw_policy.get("denied", raw_policy.get("deny", False))
            if not bool(denied):
                continue
            try:
                denied_tools.add(
                    tool_name_value(
                        normalize_tool_name(raw_tool_name, field_name="mcp_tool_policies tool name")
                    )
                )
            except ValueError:
                continue
    if not denied_tools:
        return effective_config
    patched = dict(effective_config)
    policies = dict(patched.get("mcp_tool_policies") or {})
    for tool_name in sorted(denied_tools):
        policy = dict(policies.get(tool_name) or {})
        policy["denied"] = True
        policies[tool_name] = policy
    patched["mcp_tool_policies"] = policies
    return patched


class EffectivePolicyConfig(BaseModel):
    approval_ttl_seconds: int = 3600
    approval_notification_target_ids: list[str] = Field(default_factory=list)
    default_session_ttl_seconds: int = 28800
    discord_operator_channel_allowlist: list[str] = Field(default_factory=list)
    discord_operator_user_allowlist: list[str] = Field(default_factory=list)
    discord_poll_operator_dms: bool = False
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
    approval_required_tools: list[McpToolName] = Field(default_factory=list)
    mcp_tool_policies: dict[str, McpToolPolicyRule] = Field(default_factory=dict)
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
        return normalize_approval_notification_target_ids(
            value, field_name="approval_notification_target_ids"
        )

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
        self.directory_scope_resolver = DirectoryScopeResolver(self.directory_service)
        self.resolver = DirectoryConfigResolver(
            self.directory_service,
            base_gateway_config=config,
            config_store=self.config_store,
        )

    def resolve_for_directory(self, directory_path: str | None) -> EffectivePolicyConfig:
        normalized_path = self.directory_scope_resolver.resolve_directory_path(directory_path)
        resolution = self.resolver.resolve_domain(
            normalized_path,
            domain="policy",
            base_config=get_domain_base_config(self.config, "policy"),
            validator=lambda candidate: validate_effective_domain_payload("policy", candidate),
        )
        effective_config = _apply_inherited_mcp_tool_denies(
            resolution.effective_config,
            [layer.overlay for layer in resolution.applied_layers],
        )
        return EffectivePolicyConfig.model_validate(effective_config)

    def resolve_for_session_id(self, session_id: str) -> EffectivePolicyConfig:
        session = self.session_store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        return self.resolve_for_session_manifest(session)

    def resolve_for_session_manifest(self, session: SessionManifest) -> EffectivePolicyConfig:
        normalized_path = self.directory_scope_resolver.resolve_session_directory_path(
            context="policy config",
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
        return self.resolve_for_directory(normalized_path)

    def active_session_policies(self) -> list[EffectivePolicyConfig]:
        directories = self._active_session_directory_paths()
        return [self.resolve_for_directory(path) for path in sorted(directories)]

    def resolve_communication_target_reference_for_directory(
        self,
        directory_path: str | None,
        reference: str,
        *,
        provider: str | None = None,
        capability: CommunicationTargetCapability | None = None,
    ) -> CommunicationTargetDefinition | None:
        normalized_reference = str(reference or "").strip().lower()
        if not normalized_reference:
            return None
        normalized_path = self.directory_scope_resolver.resolve_directory_path(directory_path)
        target = self._communication_targets_by_id_for_directory(normalized_path).get(
            normalized_reference
        )
        if target is None:
            return None
        if provider is not None and target.provider != provider:
            raise ValueError(
                f"communication target {target.id!r} uses provider {target.provider!r}, "
                f"not {provider!r}"
            )
        if capability is not None and capability not in set(target.policy.capabilities):
            raise ValueError(
                f"communication target {target.id!r} does not allow {capability} capability"
            )
        return target

    def resolve_communication_target_reference_for_session(
        self,
        session: SessionManifest,
        reference: str,
        *,
        provider: str | None = None,
        capability: CommunicationTargetCapability | None = None,
    ) -> CommunicationTargetDefinition | None:
        directory_path = self.directory_scope_resolver.resolve_session_directory_path(
            context="communication target",
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
        return self.resolve_communication_target_reference_for_directory(
            directory_path,
            reference,
            provider=provider,
            capability=capability,
        )

    def resolved_discord_channel_ids_for_session(
        self,
        session: SessionManifest,
        *,
        capability: CommunicationTargetCapability,
    ) -> list[str]:
        directory_path = self.directory_scope_resolver.resolve_session_directory_path(
            context="communication target",
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
        refs = (
            session.communication_target_ids
            if session.communication_target_ids is not None
            else session.discord_channel_allowlist
        )
        channel_ids = self._discord_channel_ids_from_refs(
            directory_path,
            refs,
            capability=capability,
            strict_target_capability=False,
            allow_unresolved_raw_reference=session.communication_target_ids is None,
        )
        return self._expand_discord_thread_scopes(
            directory_path,
            refs,
            channel_ids=channel_ids,
            capability=capability,
        )

    def resolved_discord_webhook_channel_ids_for_session(
        self,
        session: SessionManifest,
    ) -> list[str]:
        directory_path = self.directory_scope_resolver.resolve_session_directory_path(
            context="communication target",
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
        refs = (
            session.communication_target_ids
            if session.communication_target_ids is not None
            else session.discord_channel_allowlist
        )
        parent_channel_ids: set[str] = set()
        for reference in refs:
            normalized = str(reference or "").strip()
            if not normalized:
                continue
            try:
                target = self._target_for_ref(directory_path, normalized, provider="discord")
            except ValueError:
                continue
            if (
                target is None
                or target.policy.sender_identity != "resolved"
                or "reply" not in set(target.policy.capabilities)
                or target.policy.discord_conversation_scope
                not in {"public_threads", "channel_and_public_threads"}
            ):
                continue
            parent_channel_ids.add(_discord_channel_id_from_address(target.address))
        return sorted(parent_channel_ids)

    def resolved_teams_channel_targets_for_session(
        self,
        session: SessionManifest,
        *,
        capability: CommunicationTargetCapability,
    ) -> list[str]:
        directory_path = self.directory_scope_resolver.resolve_session_directory_path(
            context="communication target",
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
        refs = (
            list(session.communication_target_ids)
            if session.communication_target_ids is not None
            else list(session.teams_channel_allowlist or [])
            + list(session.teams_chat_allowlist or [])
        )
        return self._teams_channel_targets_from_refs(
            directory_path,
            refs,
            capability=capability,
            strict_target_capability=False,
            allow_unresolved_raw_reference=session.communication_target_ids is None,
        )

    def resolved_teams_user_targets_for_session(
        self,
        session: SessionManifest,
        *,
        capability: CommunicationTargetCapability,
    ) -> list[str]:
        directory_path = self.directory_scope_resolver.resolve_session_directory_path(
            context="communication target",
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
        refs = (
            session.communication_target_ids
            if session.communication_target_ids is not None
            else session.teams_user_allowlist
        )
        return self._teams_user_targets_from_refs(
            directory_path,
            refs,
            capability=capability,
            strict_target_capability=False,
            allow_unresolved_raw_reference=session.communication_target_ids is None,
        )

    def allowed_discord_channel_ids_for_active_sessions(self) -> list[str]:
        resolved_sessions = self._active_sessions_with_resolved_directory_paths()
        directories = {"root"}
        for _, directory_path in resolved_sessions:
            directories.add(directory_path)

        channel_ids: set[str] = set()
        for directory_path in sorted(directories):
            policy = self.resolve_for_directory(directory_path)
            channel_ids.update(
                self._discord_channel_ids_from_refs(
                    directory_path,
                    policy.discord_operator_channel_allowlist,
                    capability="read",
                    strict_target_capability=False,
                )
            )
        for session, _directory_path in resolved_sessions:
            if ToolName.DISCORD_GET_COMMANDS not in session.allowed_tools:
                continue
            channel_ids.update(
                self.resolved_discord_channel_ids_for_session(session, capability="read")
            )
        return sorted(channel_id.strip() for channel_id in channel_ids if channel_id.strip())

    def allowed_discord_channel_ids_for_session_id(
        self,
        session_id: str,
        *,
        capability: CommunicationTargetCapability = "read",
    ) -> list[str]:
        session = self.session_store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        policy = self.resolve_for_session_manifest(session)
        directory_path = self.directory_scope_resolver.resolve_session_directory_path(
            context="policy config",
            session_id=session.session_id,
            directory_path=session.directory_path,
        )
        channel_ids = set(
            self._discord_channel_ids_from_refs(
                directory_path,
                policy.discord_operator_channel_allowlist,
                capability=capability,
                strict_target_capability=False,
            )
        )
        if capability != "read" or ToolName.DISCORD_GET_COMMANDS in session.allowed_tools:
            channel_ids.update(
                self.resolved_discord_channel_ids_for_session(session, capability=capability)
            )
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

    def _active_session_directory_paths(self) -> list[str]:
        directories = {"root"}
        for _, directory_path in self._active_sessions_with_resolved_directory_paths():
            directories.add(directory_path)
        return sorted(directories)

    def _active_sessions_with_resolved_directory_paths(
        self,
    ) -> list[tuple[SessionManifest, str]]:
        now = datetime.now(UTC)
        resolved_sessions: list[tuple[SessionManifest, str]] = []
        for session in self.session_store.list_active_sessions(now=now):
            try:
                normalized_path = self.directory_scope_resolver.resolve_session_directory_path(
                    context="policy config",
                    session_id=session.session_id,
                    directory_path=session.directory_path,
                )
            except ValueError as exc:
                logger.warning(
                    "policy config skipped stale active session %s directory_path %r: %s",
                    session.session_id,
                    session.directory_path,
                    exc,
                )
                continue
            resolved_sessions.append((session, normalized_path))
        return resolved_sessions

    def _communication_targets_by_id_for_directory(
        self, directory_path: str
    ) -> dict[str, CommunicationTargetDefinition]:
        return load_communication_targets_config(
            runtime_dir=self.config.runtime_dir,
            directory_store=self.directory_store,
            directory_path=directory_path,
        ).by_id()

    def _target_for_ref(
        self,
        directory_path: str,
        reference: str,
        *,
        provider: str,
    ) -> CommunicationTargetDefinition | None:
        target = self._communication_targets_by_id_for_directory(directory_path).get(
            str(reference or "").strip().lower()
        )
        if target is None:
            return None
        if target.provider != provider:
            raise ValueError(
                f"communication target {target.id!r} uses provider {target.provider!r}, "
                f"not {provider!r}"
            )
        return target

    @staticmethod
    def _target_has_capability(
        target: CommunicationTargetDefinition,
        capability: CommunicationTargetCapability,
    ) -> bool:
        return capability in set(target.policy.capabilities)

    def _discord_channel_id_from_ref(
        self,
        directory_path: str,
        reference: str,
        *,
        capability: CommunicationTargetCapability,
        strict_target_capability: bool,
        allow_unresolved_raw_reference: bool,
    ) -> str:
        normalized = str(reference or "").strip()
        if not normalized:
            return ""
        try:
            target = self._target_for_ref(directory_path, normalized, provider="discord")
        except ValueError:
            if strict_target_capability:
                raise
            return ""
        if target is None:
            return normalized if allow_unresolved_raw_reference else ""
        if not self._target_has_capability(target, capability):
            if strict_target_capability:
                raise ValueError(
                    f"communication target {target.id!r} does not allow {capability} capability"
                )
            return ""
        return _discord_channel_id_from_address(target.address)

    def _discord_channel_ids_from_refs(
        self,
        directory_path: str,
        refs: list[str],
        *,
        capability: CommunicationTargetCapability,
        strict_target_capability: bool,
        allow_unresolved_raw_reference: bool = True,
    ) -> list[str]:
        channel_ids = {
            self._discord_channel_id_from_ref(
                directory_path,
                ref,
                capability=capability,
                strict_target_capability=strict_target_capability,
                allow_unresolved_raw_reference=allow_unresolved_raw_reference,
            )
            for ref in refs
            if str(ref or "").strip()
        }
        return sorted(channel_id for channel_id in channel_ids if channel_id)

    def _expand_discord_thread_scopes(
        self,
        directory_path: str,
        refs: list[str],
        *,
        channel_ids: list[str],
        capability: CommunicationTargetCapability,
    ) -> list[str]:
        expanded = set(channel_ids)
        store: DiscordInboxStore | None = None
        for reference in refs:
            normalized = str(reference or "").strip()
            if not normalized:
                continue
            try:
                target = self._target_for_ref(directory_path, normalized, provider="discord")
            except ValueError:
                continue
            if target is None or not self._target_has_capability(target, capability):
                continue
            scope = target.policy.discord_conversation_scope
            if scope == "channel":
                continue
            parent_channel_id = _discord_channel_id_from_address(target.address)
            if scope == "public_threads":
                expanded.discard(parent_channel_id)
            if store is None:
                store = DiscordInboxStore(self.config.runtime_dir / "discord-inbox-store.json")
            include_archived = capability == "read"
            expanded.update(
                binding.thread_id
                for binding in store.list_thread_bindings(
                    parent_channel_ids=[parent_channel_id],
                    include_archived=include_archived,
                )
                if include_archived or (not binding.archived and not binding.locked)
            )
        return sorted(value for value in expanded if value)

    def _teams_channel_target_from_ref(
        self,
        directory_path: str,
        reference: str,
        *,
        capability: CommunicationTargetCapability,
        strict_target_capability: bool,
        allow_unresolved_raw_reference: bool,
    ) -> str:
        normalized = str(reference or "").strip()
        if not normalized:
            return ""
        try:
            target = self._target_for_ref(directory_path, normalized, provider="teams")
        except ValueError:
            if strict_target_capability:
                raise
            return ""
        if target is not None:
            if not self._target_has_capability(target, capability):
                if strict_target_capability:
                    raise ValueError(
                        f"communication target {target.id!r} does not allow {capability} capability"
                    )
                return ""
            target_address = normalize_teams_target(target.address)
            if parse_teams_target(target_address).kind == "user":
                return ""
            return target_address
        if not allow_unresolved_raw_reference:
            return ""
        try:
            target_address = normalize_teams_target(normalized)
        except ValueError:
            return ""
        if parse_teams_target(target_address).kind == "user":
            return ""
        return target_address

    def _teams_channel_targets_from_refs(
        self,
        directory_path: str,
        refs: list[str],
        *,
        capability: CommunicationTargetCapability,
        strict_target_capability: bool,
        allow_unresolved_raw_reference: bool = True,
    ) -> list[str]:
        targets = {
            self._teams_channel_target_from_ref(
                directory_path,
                ref,
                capability=capability,
                strict_target_capability=strict_target_capability,
                allow_unresolved_raw_reference=allow_unresolved_raw_reference,
            )
            for ref in refs
            if str(ref or "").strip()
        }
        return sorted(target for target in targets if target)

    def _teams_user_target_from_ref(
        self,
        directory_path: str,
        reference: str,
        *,
        capability: CommunicationTargetCapability,
        strict_target_capability: bool,
        allow_unresolved_raw_reference: bool,
    ) -> str:
        normalized = str(reference or "").strip()
        if not normalized:
            return ""
        try:
            target = self._target_for_ref(directory_path, normalized, provider="teams")
        except ValueError:
            if strict_target_capability:
                raise
            return ""
        if target is not None:
            if not self._target_has_capability(target, capability):
                if strict_target_capability:
                    raise ValueError(
                        f"communication target {target.id!r} does not allow {capability} capability"
                    )
                return ""
            target_address = normalize_teams_target(target.address)
            return target_address if parse_teams_target(target_address).kind == "user" else ""
        if not allow_unresolved_raw_reference:
            return ""
        try:
            return normalize_teams_user_target(normalized)
        except ValueError:
            return ""

    def _teams_user_targets_from_refs(
        self,
        directory_path: str,
        refs: list[str],
        *,
        capability: CommunicationTargetCapability,
        strict_target_capability: bool,
        allow_unresolved_raw_reference: bool = True,
    ) -> list[str]:
        targets = {
            self._teams_user_target_from_ref(
                directory_path,
                ref,
                capability=capability,
                strict_target_capability=strict_target_capability,
                allow_unresolved_raw_reference=allow_unresolved_raw_reference,
            )
            for ref in refs
            if str(ref or "").strip()
        }
        return sorted(target for target in targets if target)


def _discord_channel_id_from_address(address: str) -> str:
    return str(address or "").strip().split("/")[-1].strip()
