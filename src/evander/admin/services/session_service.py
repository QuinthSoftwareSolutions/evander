from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evander.admin.services import session_helpers
from evander.admin.services.ports import DirectoryOwnershipPort
from evander.admin.services.vm_agent.common_helpers import (
    _admin_agent_communication_allowlists,
    _admin_agent_email_mailbox_grants,
    _revoke_managed_git_access_for_session,
    _resolve_session_approval_notification_target_ids,
)
from evander.models import (
    McpToolName,
    SessionActionResult,
    SessionSummary,
    ToolName,
    normalize_tool_name,
    tool_name_value,
)


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
        for session_id, manifest in sorted(
            self._sessions_by_id().items(), key=session_helpers.session_sort_key
        ):
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
        return summaries

    def renew_session(self, target: str, *, extension: str = "1d") -> SessionActionResult:
        session_id, manifest = session_helpers.resolve_session_target(
            target, self._sessions_by_id()
        )
        previous_expires_at = manifest.expires_at
        manifest.expires_at = session_helpers.utc_now() + session_helpers.parse_session_extension(
            extension
        )
        self._session_repository().upsert_session(manifest)
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
        git_access_revoked = _revoke_managed_git_access_for_session(manifest)
        managed_git = self._deps.managed_git_admin
        if managed_git is not None:
            managed_git.unbind_all("authorized_sessions", session_id)
            managed_git.unbind_all("sessions", session_id)
        self._session_repository().delete_session(session_id)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="delete_session",
            message=(f"Deleted session {session_id} git_access_revoked={int(git_access_revoked)}"),
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
            grants = _admin_agent_email_mailbox_grants(
                payload,
                runtime=self._deps.runtime,
                directory_path=manifest.directory_path,
            )
            update["email_mailbox_grants"] = grants
            if grants is not None:
                update["email_mailbox_allowlist"] = [grant.mailbox_id for grant in grants]
                update["email_folder_allowlist_by_mailbox"] = {
                    grant.mailbox_id: list(grant.folders) for grant in grants
                }
                update["email_folder_allowlist"] = sorted(
                    {folder for grant in grants for folder in grant.folders}
                )
        if "connector_grants" in update:
            connector_config = self._deps.runtime.provider_config.resolve_connectors_for_directory(
                manifest.directory_path
            )
            for connector_id, operation_names in update["connector_grants"].items():
                connector = connector_config.connectors.get(connector_id)
                if connector is None or connector.disabled:
                    raise ValueError(f"unknown or disabled connector: {connector_id}")
                for operation_name in operation_names:
                    operation = connector.operations.get(operation_name)
                    if operation is None or operation.disabled:
                        raise ValueError(
                            "unknown or disabled connector operation: "
                            f"{connector_id}.{operation_name}"
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

    def set_session_model(self, target: str, model: str) -> SessionActionResult:
        session_id, manifest = session_helpers.resolve_session_target(
            target, self._sessions_by_id()
        )
        normalized_model = str(model).strip()
        if not normalized_model:
            raise ValueError("model must be non-empty")
        manifest.model = normalized_model
        self._session_repository().upsert_session(manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="set_session_model",
            message=f"Updated model for session {session_id}",
        )
