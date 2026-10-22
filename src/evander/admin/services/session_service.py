from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from evander.admin.services import git_helpers, session_helpers
from evander.admin.services.ports import DirectoryOwnershipPort
from evander.admin.services.vm_agent.common_helpers import (
    _admin_agent_communication_allowlists,
    _resolve_session_approval_notification_target_ids,
    _revoke_managed_git_access_for_session,
)
from evander.codex_auth import (
    CodexAuthMode,
    normalize_codex_auth_mode,
)
from evander.connectors.grants import resolve_and_validate_connector_grants
from evander.email.grants import (
    email_mailbox_grant_policy_update,
    validate_email_mailbox_grants,
)
from evander.fast_mode import normalize_fast_mode, validate_session_fast_mode
from evander.cyber_access import normalize_cyber_access_program, session_cyber_access_program
from evander.host_directories.grants import (
    host_directory_tool_names_for_grants,
    resolve_and_validate_host_directory_grants,
)
from evander.models import (
    McpToolName,
    SessionActionResult,
    SessionSummary,
    ToolName,
    normalize_tool_name,
    tool_name_value,
)
from evander.reasoning_effort import normalize_reasoning_effort, validate_session_reasoning_effort
from evander.subscription_profile_bindings import save_session_with_subscription_profile


@dataclass(frozen=True)
class SessionAdminDependencies:
    runtime: Any
    config: Any
    directory_ownership: DirectoryOwnershipPort
    managed_git_admin: Any | None = None


class SessionAdminService:
    def __init__(self, dependencies: SessionAdminDependencies) -> None:
        self._deps = dependencies

    def _session_repository(self) -> Any:
        return self._deps.runtime.session_repository

    def _sessions_by_id(self) -> dict[str, Any]:
        repository = self._session_repository()
        return {session.session_id: session for session in repository.list_sessions()}

    def _save_subscription_session(self, manifest: Any) -> None:
        save_session_with_subscription_profile(
            config=self._deps.config, repository=self._session_repository(), session=manifest,
            provider_config=self._deps.runtime.provider_config,
        )

    def list_sessions(
        self,
        *,
        include_expired: bool = False,
        expired_only: bool = False,
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[SessionSummary]:
        if include_expired and expired_only:
            raise ValueError("include_expired and expired_only cannot both be true")

        now = session_helpers.utc_now()
        normalized_directory_path = self._deps.directory_ownership.normalize_directory_path(
            directory_path
        )
        owned_session_ids = (
            self._deps.directory_ownership.direct_owned_entity_ids(
                normalized_directory_path, kind="session"
            )
            if direct_only
            else set()
        )
        summaries: list[SessionSummary] = []
        for session_id, manifest in self._sessions_by_id().items():
            if direct_only:
                if session_id not in owned_session_ids:
                    continue
            elif (
                directory_path is not None and manifest.directory_path != normalized_directory_path
            ):
                continue
            is_expired = manifest.expires_at <= now
            if expired_only:
                if not is_expired:
                    continue
            elif not include_expired and is_expired:
                continue
            summaries.append(
                SessionSummary(
                    **manifest.model_dump(),
                    issue_number=session_helpers.extract_issue_number(manifest.vm_id)
                    or session_helpers.extract_issue_number(session_id),
                    is_expired=is_expired,
                )
            )
        summaries.sort(key=lambda item: (item.created_at, item.expires_at, item.session_id))
        return summaries

    def get_session_summary(self, session_id: str) -> SessionSummary:
        manifest = self._session_repository().get_session(session_id)
        if manifest is None:
            raise ValueError(f"Unknown session: {session_id}")
        return SessionSummary(
            **manifest.model_dump(),
            issue_number=session_helpers.extract_issue_number(manifest.vm_id)
            or session_helpers.extract_issue_number(session_id),
            is_expired=manifest.expires_at <= session_helpers.utc_now(),
        )

    def renew_session(self, target: str, *, extension: str = "1d") -> SessionActionResult:
        session_id, manifest = session_helpers.resolve_session_target(
            target, self._sessions_by_id()
        )
        previous_expires_at = manifest.expires_at
        manifest = manifest.model_copy(
            update={"expires_at": session_helpers.utc_now() + session_helpers.parse_session_extension(extension)}
        )
        self._save_subscription_session(manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="renew_session",
            message=(
                f"Renewed session {session_id} "
                f"(previous_expires_at={previous_expires_at.isoformat()} "
                f"new_expires_at={manifest.expires_at.isoformat()})"
            ),
        )

    def revoke_session(self, target: str) -> SessionActionResult:
        session_id, manifest = session_helpers.resolve_session_target(
            target, self._sessions_by_id()
        )
        previous_expires_at = manifest.expires_at
        git_access_revoked = _revoke_managed_git_access_for_session(manifest)
        managed_git = self._deps.managed_git_admin
        if managed_git is not None:
            managed_git.unbind_all("authorized_sessions", session_id)
            managed_git.unbind_all("sessions", session_id)
        manifest.expires_at = session_helpers.utc_now()
        self._session_repository().upsert_session(manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="revoke_session",
            message=(
                f"Revoked session {session_id} "
                f"(previous_expires_at={previous_expires_at.isoformat()} "
                f"new_expires_at={manifest.expires_at.isoformat()} "
                f"git_access_revoked={int(git_access_revoked)})"
            ),
        )

    def delete_session(self, target: str) -> SessionActionResult:
        session_id, manifest = session_helpers.resolve_session_target(
            target, self._sessions_by_id()
        )
        from evander.execution.preservation import require_session_data_disposable
        require_session_data_disposable(self._deps.config.runtime_dir, session_id)
        from evander.session_dependencies import SessionDependencies

        SessionDependencies(self._deps.config.runtime_dir).release(session_id)
        git_access_revoked = _revoke_managed_git_access_for_session(manifest)
        managed_git = self._deps.managed_git_admin
        if managed_git is not None:
            managed_git.unbind_all("authorized_sessions", session_id)
            managed_git.unbind_all("sessions", session_id)
        self._session_repository().delete_session(session_id)
        removed_pr_map_entries, removed_streams = git_helpers.prune_pr_map(
            self._deps.config.pr_map_path,
            session_ids={session_id},
        )
        removed_cursors, removed_feedback = git_helpers.prune_pr_feedback_store(
            self._deps.config.runtime_dir / "pr-feedback-store.json",
            streams=removed_streams,
        )
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="delete_session",
            message=(
                f"Deleted session {session_id} "
                f"git_access_revoked={int(git_access_revoked)} "
                f"removed_pr_map_entries={removed_pr_map_entries} "
                f"removed_feedback_cursors={removed_cursors} "
                f"removed_feedback_items={removed_feedback}"
            ),
        )

    def set_session_allowed_tools(
        self, target: str, allowed_tools: list[str | ToolName]
    ) -> SessionActionResult:
        session_id, manifest = session_helpers.resolve_session_target(
            target, self._sessions_by_id()
        )
        previous_manifest = manifest.model_copy(deep=True)
        normalized_tools: list[McpToolName] = []
        seen: set[str] = set()
        for tool in allowed_tools:
            normalized_tool = normalize_tool_name(tool, field_name="allowed_tools")
            tool_value = tool_name_value(normalized_tool)
            if tool_value in seen:
                continue
            seen.add(tool_value)
            normalized_tools.append(normalized_tool)
        manifest.allowed_tools = normalized_tools
        allowed_values = {tool_name_value(tool) for tool in normalized_tools}
        manifest.approval_required_tools = [
            tool
            for tool in manifest.approval_required_tools
            if tool_name_value(tool) in allowed_values
        ]
        manifest.approval_tool_settings = {
            tool_name: settings
            for tool_name, settings in manifest.approval_tool_settings.items()
            if tool_name in allowed_values
        }
        self._session_repository().upsert_session(manifest)
        session_helpers.sync_added_discord_session_cursors(
            self._deps.runtime,
            previous_session=previous_manifest,
            updated_session=manifest,
        )
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="set_session_allowed_tools",
            message=f"Updated allowed tools for session {session_id}",
        )

    def set_session_allowed_script_names(
        self, target: str, allowed_script_names: list[str]
    ) -> SessionActionResult:
        session_id, manifest = session_helpers.resolve_session_target(
            target, self._sessions_by_id()
        )
        manifest.allowed_script_names = session_helpers.normalize_allowed_script_names(
            allowed_script_names
        )
        self._session_repository().upsert_session(manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="set_session_allowed_script_names",
            message=f"Updated allowed script names for session {session_id}",
        )

    def update_session_policy(self, target: str, payload: dict[str, Any]) -> SessionActionResult:
        session_id, manifest = session_helpers.resolve_session_target(
            target, self._sessions_by_id()
        )
        contacts_config = None
        if "allowed_contact_ids" in payload:
            contacts_config = self._deps.runtime.provider_config.resolve_contacts_for_session_id(
                manifest.session_id
            )
        update = session_helpers.session_policy_update_from_payload(
            manifest,
            payload,
            contacts_config=contacts_config,
        )
        if "communication_target_ids" in payload:
            communication_allowlists = _admin_agent_communication_allowlists(
                payload,
                config=self._deps.config,
                directory_path=manifest.directory_path,
            )
            update["communication_target_ids"] = communication_allowlists[
                "communication_target_ids"
            ]
            if communication_allowlists["communication_target_ids"] is not None:
                update.update(
                    {
                        "discord_channel_allowlist": communication_allowlists["discord_channels"]
                        or [],
                        "teams_channel_allowlist": communication_allowlists[
                            "teams_channel_allowlist"
                        ]
                        or [],
                        "teams_chat_allowlist": communication_allowlists["teams_chat_allowlist"]
                        or [],
                        "teams_user_allowlist": communication_allowlists["teams_user_allowlist"]
                        or [],
                    }
                )
        if "email_mailbox_grants" in payload:
            config = self._deps.runtime.provider_config.resolve_for_directory(
                manifest.directory_path
            )
            policy = self._deps.runtime.policy_config.resolve_for_directory(manifest.directory_path)
            grants = validate_email_mailbox_grants(
                update["email_mailbox_grants"],
                mailboxes=config.email_mailboxes,
                allowed_mailbox_ids=policy.email_mailbox_allowlist,
                folder_allowlist_by_mailbox=policy.email_folder_allowlist_by_mailbox,
                email_send_enabled=policy.email_send_enabled,
                directory_path=manifest.directory_path,
            )
            update.update(email_mailbox_grant_policy_update(grants))
        if "connector_grants" in update:
            update["connector_grants"] = resolve_and_validate_connector_grants(
                self._deps.runtime.provider_config,
                directory_path=manifest.directory_path,
                session_id=manifest.session_id,
                value=update["connector_grants"],
            )
        if "host_directory_grants" in update:
            update["host_directory_grants"] = resolve_and_validate_host_directory_grants(
                provider_config=self._deps.runtime.provider_config,
                directory_path=manifest.directory_path,
                session_id=manifest.session_id,
                value=update["host_directory_grants"],
            )
            update["allowed_tools"] = session_helpers.normalize_allowed_tools(
                [
                    *manifest.allowed_tools,
                    *host_directory_tool_names_for_grants(update["host_directory_grants"]),
                ]
            )
        if "approval_notification_target_ids" in update:
            update["approval_notification_target_ids"] = (
                _resolve_session_approval_notification_target_ids(
                    self._deps.runtime,
                    self._deps.config,
                    manifest,
                    update["approval_notification_target_ids"],
                )
            )
        if update:
            previous_manifest = manifest
            manifest = manifest.model_copy(update=update)
            session_cyber_access_program(
                manifest,
                self._deps.runtime.provider_config.resolve_for_directory(manifest.directory_path),
            )
            validate_session_fast_mode(
                manifest,
                self._deps.runtime.provider_config.resolve_for_directory(manifest.directory_path),
            )
            self._session_repository().upsert_session(manifest)
            session_helpers.sync_added_discord_session_cursors(
                self._deps.runtime,
                previous_session=previous_manifest,
                updated_session=manifest,
            )
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="update_session_policy",
            message=f"Updated policy for session {session_id}",
        )

    def update_session_model_settings(
        self, target: str, settings: Mapping[str, Any]
    ) -> SessionActionResult:
        session_id, manifest = session_helpers.resolve_session_target(
            target, self._sessions_by_id()
        )
        if not settings or set(settings) - {"model", "reasoning_effort", "fast_mode", "cyber_access_program"}:
            raise ValueError("Provide model, reasoning_effort, fast_mode, and/or cyber_access_program")
        update: dict[str, Any] = {}
        if "model" in settings:
            model = settings["model"]
            if model is not None and (not isinstance(model, str) or not model.strip()):
                raise ValueError("model must be non-empty")
            update["model"] = model.strip() if model is not None else None
        if "reasoning_effort" in settings:
            update["reasoning_effort"] = normalize_reasoning_effort(settings["reasoning_effort"])
        if "fast_mode" in settings:
            update["fast_mode"] = normalize_fast_mode(settings["fast_mode"])
        if "cyber_access_program" in settings:
            update["cyber_access_program"] = normalize_cyber_access_program(settings["cyber_access_program"])
        updated = manifest.model_copy(update=update)
        resolver = getattr(self._deps.runtime, "provider_config", None)
        config = (
            resolver.resolve_for_directory(updated.directory_path)
            if resolver is not None
            else self._deps.config
        )
        validate_session_reasoning_effort(updated, config)
        validate_session_fast_mode(updated, config)
        session_cyber_access_program(updated, config)
        self._session_repository().upsert_session(updated)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="set_session_model",
            message=f"Updated model settings for session {session_id}",
        )

    def set_session_model(self, target: str, model: str) -> SessionActionResult:
        if not str(model).strip():
            raise ValueError("model must be non-empty")
        return self.update_session_model_settings(target, {"model": model})

    def set_session_codex_auth_mode(
        self,
        target: str,
        mode: str | CodexAuthMode | None,
    ) -> SessionActionResult:
        session_id, manifest = session_helpers.resolve_session_target(
            target, self._sessions_by_id()
        )
        if str(manifest.vm_provider or "").strip() != "container_worker_vm":
            raise ValueError("codex_auth_mode is supported only for container worker sessions")
        normalized = normalize_codex_auth_mode(mode)
        metadata = manifest.metadata if isinstance(manifest.metadata, dict) else {}
        agent_provider = str(metadata.get("agent_provider") or "codex").strip().casefold()
        if normalized is CodexAuthMode.CHATGPT_SUBSCRIPTION and agent_provider != "codex":
            raise ValueError("chatgpt_subscription is supported only for Codex agents")
        updated = manifest.model_copy(
            update={
                "codex_auth_mode": normalized,
                "subscription_profile_id": (
                    manifest.subscription_profile_id
                    if normalized is CodexAuthMode.CHATGPT_SUBSCRIPTION
                    else None
                ),
            }
        )
        self._save_subscription_session(updated)
        from evander.messaging.agent_prompt_queue import AgentPromptQueueStore

        AgentPromptQueueStore(
            self._deps.config.runtime_dir / "queued-agent-prompts.json"
        ).reroute_session(
            updated.session_id,
            subscription_profile_id=updated.subscription_profile_id,
        )
        effective = normalized.value if normalized is not None else "directory default"
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="set_session_codex_auth_mode",
            message=f"Updated Codex auth mode for session {session_id} to {effective}",
        )
