from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from urllib.parse import quote

import httpx

from agent_sandbox.admin.operations_helpers import (
    _journalctl_tail,
    _run_systemd_control,
    _systemctl_show,
)
from agent_sandbox.admin.payloads import (
    EffectConfigMutationPayload,
    TriggerConfigMutationPayload,
    WorkflowEditorDocumentPayload,
    WorkflowEditorSavePayload,
    WorkflowEditorValidationPayload,
    WorkflowRunDetailPayload,
    WorkflowRunSearchPayload,
    WorkflowRunSnapshotPayload,
)
from agent_sandbox.admin.services import session_helpers, vm_helpers
from agent_sandbox.admin.services.ports import DirectoryOwnershipPort, WorkflowConfigPort
from agent_sandbox.admin.services.vm_agent.common_helpers import (
    _resolve_session_approval_notification_target_ids,
)
from agent_sandbox.admin.worker_config import (
    _WORKER_DEFINITIONS,
    _worker_definition,
)
from agent_sandbox.workflow.catalog import WorkflowEditorCatalogPayload
from agent_sandbox.workflow.events import WorkflowEventJournal
from agent_sandbox.directories import DirectoryService
from agent_sandbox.config import GatewayConfig
from agent_sandbox.models import (
    McpToolName,
    SessionActionResult,
    SessionSummary,
    ToolName,
    VmSummary,
    WakeScheduledRequest,
    WakeScheduleDefinition,
    WorkerActionResult,
    WorkerSummary,
    normalize_tool_name,
    tool_name_value,
)
from agent_sandbox.messaging.teams_auth import (
    DeviceCodeLoginSession,
    JsonTeamsIdentityCredentialStore,
    MicrosoftDeviceCodeAuthConfig,
    TeamsAuthCredentials,
    TeamsDeviceCodeTokenProvider,
)


@dataclass(frozen=True)
class TeamsAdminDependencies:
    runtime: Any
    normalize_directory_reference: Any


@dataclass(frozen=True)
class SessionAdminDependencies:
    runtime: Any
    config: Any
    directory_ownership: DirectoryOwnershipPort


@dataclass(frozen=True)
class WorkerAdminDependencies:
    runtime: Any
    config: Any
    agent_control_store: Any
    directory_ownership: DirectoryOwnershipPort
    workflow_config: WorkflowConfigPort


class TeamsAdminFacade:
    def __init__(self, dependencies: TeamsAdminDependencies) -> None:
        self._deps = dependencies

    def normalize_directory_reference(self, reference: str | None) -> str:
        return self._deps.normalize_directory_reference(reference)

    def _teams_auth_provider_for_directory(
        self,
        directory_path: str | None,
    ) -> tuple[str, GatewayConfig, str, TeamsDeviceCodeTokenProvider]:
        normalized_directory = self._deps.normalize_directory_reference(directory_path)
        resolved_config = self._deps.runtime.provider_config.resolve_for_directory(
            normalized_directory
        )
        if resolved_config.teams_backend != "delegated_graph_api":
            raise ValueError(
                f"Directory '{normalized_directory}' is not configured for delegated Teams auth "
                f"(teams_backend={resolved_config.teams_backend!r})."
            )
        identity_id = str(resolved_config.teams_delegated_identity_id or "").strip()
        if not identity_id:
            raise ValueError(
                f"Directory '{normalized_directory}' is missing teams_delegated_identity_id."
            )
        client_id = str(resolved_config.teams_delegated_client_id or "").strip()
        if not client_id:
            raise ValueError(
                f"Directory '{normalized_directory}' is missing teams_delegated_client_id."
            )
        provider = TeamsDeviceCodeTokenProvider(
            MicrosoftDeviceCodeAuthConfig(
                client_id=client_id,
                tenant_id=resolved_config.teams_delegated_tenant_id,
                scopes=resolved_config.teams_delegated_scopes,
                authority_base_url=resolved_config.teams_delegated_authority_base_url,
                timeout_seconds=resolved_config.teams_timeout_seconds,
            ),
            JsonTeamsIdentityCredentialStore(
                resolved_config.teams_delegated_credential_store_path,
                identity_id,
            ),
        )
        return normalized_directory, resolved_config, identity_id, provider

    def _teams_auth_payload(
        self,
        *,
        directory_path: str,
        config: GatewayConfig,
        identity_id: str,
        credentials: TeamsAuthCredentials | None,
    ) -> dict[str, object]:
        return {
            "directory_path": directory_path,
            "identity_id": identity_id,
            "backend": config.teams_backend,
            "authenticated": credentials is not None,
            "status": "authenticated" if credentials is not None else "reauth-required",
            "account_identifier": identity_id,
            "tenant_id": config.teams_delegated_tenant_id,
            "scopes": list(config.teams_delegated_scopes),
            "expires_at": credentials.expires_at if credentials is not None else None,
            "issued_at": credentials.issued_at if credentials is not None else None,
            "store_path": str(config.teams_delegated_credential_store_path),
        }

    def _teams_graph_list_channels(
        self,
        config: GatewayConfig,
        provider: TeamsDeviceCodeTokenProvider,
        *,
        team_id: str,
    ) -> list[dict[str, Any]]:
        encoded_team_id = quote(team_id, safe="")
        next_url = f"{config.teams_graph_api_base_url}/teams/{encoded_team_id}/channels"
        channels: list[dict[str, Any]] = []
        with httpx.Client(timeout=config.teams_timeout_seconds) as client:
            while next_url:
                response = client.get(
                    next_url,
                    headers={
                        "Authorization": f"Bearer {provider.get_bearer_token()}",
                        "Content-Type": "application/json",
                    },
                )
                response.raise_for_status()
                payload = response.json()
                for item in payload.get("value", []):
                    if isinstance(item, dict):
                        channels.append(item)
                raw_next = payload.get("@odata.nextLink")
                next_url = str(raw_next).strip() if raw_next else ""
        return channels

    def login(self, *, directory: str = "root") -> DeviceCodeLoginSession:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory)
        )
        session = provider.begin_device_code_login()
        metadata = dict(session.metadata)
        metadata.update(
            {
                "directory_path": normalized_directory,
                "identity_id": identity_id,
                "store_path": str(config.teams_delegated_credential_store_path),
            }
        )
        return session.model_copy(update={"metadata": metadata})

    def complete_login(
        self,
        login_session: DeviceCodeLoginSession,
        *,
        directory: str = "root",
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory)
        )
        credentials = provider.complete_device_code_login(
            login_session, timeout_seconds=timeout_seconds
        )
        return self._teams_auth_payload(
            directory_path=normalized_directory,
            config=config,
            identity_id=identity_id,
            credentials=credentials,
        )

    def status(self, *, directory: str = "root") -> dict[str, object]:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory)
        )
        return self._teams_auth_payload(
            directory_path=normalized_directory,
            config=config,
            identity_id=identity_id,
            credentials=provider.load_credentials(),
        )

    def logout(self, *, directory: str = "root") -> dict[str, object]:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory)
        )
        provider.clear_credentials()
        return {
            "directory_path": normalized_directory,
            "identity_id": identity_id,
            "backend": config.teams_backend,
            "status": "logged_out",
            "authenticated": False,
            "store_path": str(config.teams_delegated_credential_store_path),
        }

    def list(self) -> list[dict[str, object]]:
        directory_service = DirectoryService(self._deps.runtime.directories)
        directory_paths = [
            "root",
            *[item.path for item in directory_service.list_descendants("root")],
        ]
        entries: list[dict[str, object]] = []
        for directory_path in directory_paths:
            resolved_config = self._deps.runtime.provider_config.resolve_for_directory(
                directory_path
            )
            if resolved_config.teams_backend != "delegated_graph_api":
                continue
            _, config, identity_id, provider = self._teams_auth_provider_for_directory(
                directory_path
            )
            entries.append(
                self._teams_auth_payload(
                    directory_path=directory_path,
                    config=config,
                    identity_id=identity_id,
                    credentials=provider.load_credentials(),
                )
            )
        return entries

    def list_channels(
        self,
        *,
        directory: str = "root",
        team_id: str,
        channel_name: str | None = None,
        include_all: bool = False,
    ) -> dict[str, object]:
        normalized_directory, config, identity_id, provider = (
            self._teams_auth_provider_for_directory(directory)
        )
        normalized_team_id = str(team_id or "").strip()
        if not normalized_team_id:
            raise ValueError("team_id is required")
        normalized_channel_name = str(channel_name or "").strip()
        if not normalized_channel_name and not include_all:
            raise ValueError("channel_name is required unless include_all=True")

        channels = self._teams_graph_list_channels(config, provider, team_id=normalized_team_id)
        matches: list[dict[str, object]] = []
        for channel in channels:
            display_name = str(channel.get("displayName") or "").strip()
            if (
                normalized_channel_name
                and display_name.casefold() != normalized_channel_name.casefold()
            ):
                continue
            channel_id = str(channel.get("id") or "").strip()
            matches.append(
                {
                    "team_id": normalized_team_id,
                    "channel_id": channel_id,
                    "display_name": display_name,
                    "membership_type": str(channel.get("membershipType") or "").strip() or None,
                    "web_url": str(channel.get("webUrl") or "").strip() or None,
                    "allowlist_entry": f"{normalized_team_id}/{channel_id}" if channel_id else None,
                }
            )

        return {
            "directory_path": normalized_directory,
            "identity_id": identity_id,
            "backend": config.teams_backend,
            "team_id": normalized_team_id,
            "channel_name": normalized_channel_name or None,
            "match_count": len(matches),
            "channels": matches if normalized_channel_name or include_all else [],
        }


class SessionAdminFacade:
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
        normalized_directory_path = (
            self._deps.directory_ownership.normalize_directory_path(directory_path)
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

    def list_wake_schedules(
        self,
        *,
        session_id: str | None = None,
        include_disabled: bool = True,
    ) -> list[dict[str, Any]]:
        requests = self._deps.runtime.wake_store.list_requests(
            session_id=session_id,
            include_disabled=include_disabled,
        )
        latest_dispatch_by_request_id = (
            self._deps.runtime.wake_store.latest_dispatch_by_request_id()
        )

        schedules: list[dict[str, Any]] = []
        for request in requests:
            dispatch = latest_dispatch_by_request_id.get(request.request_id)
            schedules.append(
                {
                    "request_id": request.request_id,
                    "session_id": request.session_id,
                    "resume_at": request.schedule.resume_at,
                    "cron": request.schedule.cron,
                    "prompt": request.resume_prompt.prompt,
                    "status": request.status,
                    "status_reason": request.status_reason,
                    "status_updated_at": request.status_updated_at,
                    "last_dispatch_id": request.last_dispatch_id,
                    "last_dispatch_status": request.last_dispatch_status,
                    "last_error": request.last_error,
                    "last_error_at": request.last_error_at,
                    "completed_at": request.completed_at,
                    "completion_evidence": dict(request.completion_evidence or {}),
                    "resume_mode": request.resume_mode,
                    "enabled": request.enabled,
                    "source": request.source,
                    "created_at": request.created_at,
                    "updated_at": request.updated_at,
                    "last_dispatched_at": request.last_dispatched_at,
                    "next_run_at": request.next_run_at,
                    "cancelled_at": request.cancelled_at,
                    "cancelled_by_session_id": request.cancelled_by_session_id,
                    "cancel_reason": request.cancel_reason,
                    "cancel_metadata": request.cancel_metadata,
                    "last_dispatch": (
                        {
                            "dispatch_id": dispatch.dispatch_id,
                            "status": dispatch.status,
                            "error": dispatch.error,
                            "dispatched_at": dispatch.dispatched_at,
                        }
                        if dispatch is not None
                        else None
                    ),
                }
            )
        schedules.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return schedules

    @staticmethod
    def _normalize_wake_ledger_statuses(statuses: list[str] | None) -> list[str] | None:
        if statuses is None:
            return None
        allowed_statuses = {
            "pending",
            "due",
            "dispatching",
            "queued",
            "dispatched",
            "failed",
            "missed_or_late",
            "completed_or_acknowledged",
            "cancelled",
        }
        normalized_statuses: list[str] = []
        seen: set[str] = set()
        for status in statuses:
            normalized = str(status or "").strip().casefold()
            if not normalized:
                continue
            if normalized not in allowed_statuses:
                raise ValueError(f"Invalid wake ledger status: {status}")
            if normalized in seen:
                continue
            seen.add(normalized)
            normalized_statuses.append(normalized)
        return normalized_statuses or None

    def _wake_ledger_entry_for_request(self, request: WakeScheduledRequest) -> dict[str, Any]:
        latest_dispatch = self._deps.runtime.wake_store.latest_dispatch_by_request_id().get(
            request.request_id
        )
        status = self._deps.runtime.wake_store._ledger_status_for_request(
            request,
            latest_dispatch=latest_dispatch,
        )
        return self._deps.runtime.wake_store._ledger_entry_for_request(
            request,
            latest_dispatch=latest_dispatch,
            status=status,
        )

    def list_scheduled_wake_ledger(
        self,
        *,
        statuses: list[str] | None = None,
        session_id: str | None = None,
        limit: int = 100,
        include_completed: bool = True,
    ) -> list[dict[str, Any]]:
        normalized_statuses = self._normalize_wake_ledger_statuses(statuses)
        return self._deps.runtime.wake_store.list_ledger_entries(
            session_id=session_id,
            statuses=normalized_statuses,
            include_completed=include_completed,
            limit=limit,
        )

    def retry_scheduled_wake(
        self,
        request_id: str,
        *,
        resume_at: datetime | None = None,
        run_now: bool = True,
    ) -> dict[str, Any]:
        normalized_request_id = str(request_id or "").strip()
        if not normalized_request_id:
            raise ValueError("request_id cannot be empty")

        request = self._deps.runtime.wake_store.get_request(normalized_request_id)
        if request is None:
            raise ValueError(f"Wake schedule not found: {normalized_request_id}")

        current_entry = self._wake_ledger_entry_for_request(request)
        current_status = str(current_entry.get("status") or "").strip().casefold()
        if current_status not in {"failed", "missed_or_late", "due"}:
            raise ValueError(
                f"Wake schedule {normalized_request_id} is not retryable in status {current_status}."
            )

        if resume_at is not None and run_now:
            raise ValueError("Provide either resume_at or run_now=true, not both.")
        if resume_at is None and not run_now:
            raise ValueError("Either resume_at or run_now=true is required.")

        now = session_helpers.utc_now()
        validated_resume_at = None
        updated_schedule = request.schedule
        if resume_at is not None:
            updated_schedule = WakeScheduleDefinition(resume_at=resume_at)
            validated_resume_at = updated_schedule.resume_at
            next_run_at = validated_resume_at or now
        else:
            next_run_at = now
        self._enforce_wake_schedule_guardrails(
            session_id=request.session_id,
            schedule=updated_schedule,
            enabled=True,
            exclude_request_id=request.request_id,
        )

        metadata = dict(request.resume_prompt.metadata or {})
        existing_history = metadata.get("retry_history")
        retry_history = list(existing_history) if isinstance(existing_history, list) else []
        retry_history.append(
            {
                "retried_at": now,
                "previous_status": current_entry.get("status"),
                "previous_status_reason": current_entry.get("status_reason"),
                "previous_failure_reason": current_entry.get("failure_reason"),
                "previous_last_dispatch_id": current_entry.get("latest_dispatch_id"),
                "previous_last_dispatch_status": current_entry.get("latest_dispatch_status"),
                "previous_auto_dispatch_attempts": request.auto_dispatch_attempts,
                "previous_auto_retry_started_at": request.auto_retry_started_at,
                "previous_auto_retry_last_backoff_seconds": request.auto_retry_last_backoff_seconds,
                "previous_auto_retry_exhausted_at": request.auto_retry_exhausted_at,
                "requested_run_now": bool(run_now and resume_at is None),
                "requested_resume_at": validated_resume_at,
            }
        )
        metadata["retry_history"] = retry_history[-10:]
        request.resume_prompt.metadata = metadata
        request.schedule = updated_schedule
        request.enabled = True
        request.next_run_at = next_run_at
        request.status = "due" if next_run_at <= now else "pending"
        request.status_reason = None
        request.status_updated_at = now
        request.last_error = None
        request.last_error_at = None
        request.auto_dispatch_attempts = 0
        request.auto_retry_started_at = None
        request.auto_retry_last_backoff_seconds = None
        request.auto_retry_exhausted_at = None
        request.updated_at = now
        self._deps.runtime.wake_store.upsert_request(request)
        return self._wake_ledger_entry_for_request(request)

    def create_wake_schedule(
        self,
        *,
        session_id: str,
        prompt: str,
        resume_at: Any | None = None,
        cron: str | None = None,
        resume_mode: str = "same_context",
        enabled: bool = True,
        request_id: str | None = None,
        source: str | None = "admin_cli",
        metadata: dict[str, Any] | None = None,
    ) -> WakeScheduledRequest:
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            raise ValueError("prompt cannot be empty")
        normalized_resume_mode = session_helpers.normalize_resume_mode(resume_mode)
        normalized_source = str(source or "admin_cli").strip() or "admin_cli"
        if normalized_source.startswith("mcp") and normalized_resume_mode != "same_context":
            raise ValueError("MCP scheduling only supports resume_mode='same_context'.")
        normalized_cron = None
        if cron is not None and str(cron).strip():
            normalized_cron = session_helpers.require_valid_cron(str(cron))
        schedule = WakeScheduleDefinition(resume_at=resume_at, cron=normalized_cron)
        normalized_session_id = session_id.strip()
        self._enforce_wake_schedule_guardrails(
            session_id=normalized_session_id,
            schedule=schedule,
            enabled=bool(enabled),
        )
        return self._deps.runtime.wake_store.schedule_resume_request(
            session_id=normalized_session_id,
            prompt=normalized_prompt,
            schedule=schedule,
            request_id=request_id.strip()
            if isinstance(request_id, str) and request_id.strip()
            else None,
            source=normalized_source,
            resume_mode=normalized_resume_mode,
            enabled=bool(enabled),
            metadata=metadata or {},
        )

    def edit_wake_schedule(
        self,
        request_id: str,
        *,
        resume_at: Any | None = None,
        cron: str | None = None,
        prompt: str | None = None,
        resume_mode: str | None = None,
        enabled: bool | None = None,
    ) -> WakeScheduledRequest:
        existing = self._deps.runtime.wake_store.get_request(request_id.strip())
        if existing is None:
            raise ValueError(f"Wake schedule not found: {request_id}")
        if existing.status == "cancelled":
            raise ValueError(
                f"Wake schedule {existing.request_id} is cancelled and cannot be edited."
            )
        updated_prompt = existing.resume_prompt.prompt if prompt is None else str(prompt).strip()
        if not updated_prompt:
            raise ValueError("prompt cannot be empty")
        updated_mode = (
            existing.resume_mode
            if resume_mode is None
            else session_helpers.normalize_resume_mode(resume_mode)
        )
        if str(existing.source or "").strip().startswith("mcp") and updated_mode != "same_context":
            raise ValueError("MCP scheduling only supports resume_mode='same_context'.")
        resume_changed = resume_at is not None
        cron_changed = cron is not None
        if resume_changed and cron_changed:
            raise ValueError("Provide only one of resume_at or cron when editing a schedule.")
        if resume_changed:
            updated_schedule = WakeScheduleDefinition(resume_at=resume_at, cron=None)
        elif cron_changed:
            normalized_cron = (
                session_helpers.require_valid_cron(str(cron)) if str(cron).strip() else None
            )
            updated_schedule = WakeScheduleDefinition(resume_at=None, cron=normalized_cron)
        else:
            updated_schedule = existing.schedule

        updated_enabled = existing.enabled if enabled is None else bool(enabled)
        self._enforce_wake_schedule_guardrails(
            session_id=existing.session_id,
            schedule=updated_schedule,
            enabled=updated_enabled,
            exclude_request_id=existing.request_id,
        )
        existing.schedule = updated_schedule
        existing.resume_prompt.prompt = updated_prompt
        existing.resume_mode = updated_mode
        if enabled is not None:
            existing.enabled = bool(enabled)
        existing.updated_at = session_helpers.utc_now()
        if existing.schedule.resume_at is not None:
            existing.next_run_at = existing.schedule.resume_at
        elif cron_changed:
            existing.next_run_at = session_helpers.utc_now()
        self._deps.runtime.wake_store.upsert_request(existing)
        return existing

    def delete_wake_schedule(self, request_id: str) -> bool:
        normalized = request_id.strip()
        if not normalized:
            raise ValueError("request_id cannot be empty")
        return self._deps.runtime.wake_store.delete_request(normalized)

    def renew_session(self, target: str, *, extension: str = "1d") -> SessionActionResult:
        session_id, manifest = session_helpers.resolve_session_target(
            target, self._sessions_by_id()
        )
        previous_expires_at = manifest.expires_at
        manifest.expires_at = (
            session_helpers.utc_now()
            + session_helpers.parse_session_extension(extension)
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
        manifest.expires_at = session_helpers.utc_now()
        self._session_repository().upsert_session(manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="revoke_session",
            message=(
                f"Revoked session {session_id} "
                f"(previous_expires_at={previous_expires_at.isoformat()} "
                f"new_expires_at={manifest.expires_at.isoformat()})"
            ),
        )

    def delete_session(self, target: str) -> SessionActionResult:
        session_id, _ = session_helpers.resolve_session_target(target, self._sessions_by_id())
        self._session_repository().delete_session(session_id)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="delete_session",
            message=f"Deleted session {session_id}",
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

    def update_session_policy(
        self, target: str, payload: dict[str, Any]
    ) -> SessionActionResult:
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

    def _active_wake_requests(
        self, *, exclude_request_id: str | None = None
    ) -> list[WakeScheduledRequest]:
        requests = self._deps.runtime.wake_store.list_requests(include_disabled=False)
        if not exclude_request_id:
            return requests
        return [request for request in requests if request.request_id != exclude_request_id]

    def _enforce_wake_schedule_guardrails(
        self,
        *,
        session_id: str,
        schedule: WakeScheduleDefinition,
        enabled: bool,
        exclude_request_id: str | None = None,
    ) -> None:
        if not enabled:
            return
        active_requests = self._active_wake_requests(exclude_request_id=exclude_request_id)
        max_active_per_session = self._deps.config.wake_schedule_max_active_per_session
        if max_active_per_session is not None:
            active_for_session = sum(
                1 for request in active_requests if request.session_id == session_id
            )
            if active_for_session >= max_active_per_session:
                raise ValueError(
                    "Wake schedule limit reached for session "
                    f"{session_id}: max_active_per_session={max_active_per_session}"
                )
        max_active_total = self._deps.config.wake_schedule_max_active_total
        if max_active_total is not None and len(active_requests) >= max_active_total:
            raise ValueError(f"Wake schedule limit reached: max_active_total={max_active_total}")
        min_interval_seconds = self._deps.config.wake_schedule_min_interval_seconds
        cron = str(schedule.cron or "").strip()
        if min_interval_seconds is not None and cron:
            actual_interval_seconds = session_helpers.cron_min_interval_seconds_for_schedule(cron)
            if actual_interval_seconds is None:
                raise ValueError(f"Unable to determine cron interval for schedule: {cron}")
            if actual_interval_seconds < min_interval_seconds:
                raise ValueError(
                    "Recurring wake schedule interval is too short: "
                    f"minimum={min_interval_seconds:g}s actual={actual_interval_seconds:g}s"
                )


class WorkerAdminFacade:
    def __init__(self, dependencies: WorkerAdminDependencies) -> None:
        self._deps = dependencies

    def _session_ids_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for session_id, session in {
            session.session_id: session
            for session in self._deps.runtime.session_repository.list_sessions()
        }.items():
            if session.vm_id:
                mapping.setdefault(session.vm_id, []).append(session_id)
        for session_ids in mapping.values():
            session_ids.sort()
        return mapping

    def _directory_paths_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, set[str]] = {}
        for session in self._deps.runtime.session_repository.list_sessions():
            if session.vm_id:
                mapping.setdefault(session.vm_id, set()).add(session.directory_path)
        return {vm_id: sorted(paths) for vm_id, paths in mapping.items()}

    def _labels_by_vm(self) -> dict[str, dict[str, str]]:
        mapping: dict[str, dict[str, str] | None] = {}
        sessions = {
            session.session_id: session
            for session in self._deps.runtime.session_repository.list_sessions()
        }
        for session_id in sorted(sessions):
            session = sessions[session_id]
            if not session.vm_id:
                continue
            labels = session_helpers.normalize_session_labels(session.metadata.get("labels"))
            current = mapping.get(session.vm_id)
            if current is None:
                mapping[session.vm_id] = dict(labels)
                continue
            mapping[session.vm_id] = {
                key: value for key, value in current.items() if labels.get(key) == value
            }
        return {vm_id: labels for vm_id, labels in mapping.items() if labels}

    def list_vms(
        self,
        *,
        include_inactive: bool = True,
        omit_state: bool = False,
        vm_user: str = "agent",
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[VmSummary]:
        vm_names = vm_helpers.virsh_lines(
            ["list", "--all" if include_inactive else "--state-running", "--name"]
        )
        session_ids_by_vm = self._session_ids_by_vm()
        labels_by_vm = self._labels_by_vm()
        directory_paths_by_vm = self._directory_paths_by_vm()
        normalized_directory_path = (
            self._deps.directory_ownership.normalize_directory_path(directory_path)
        )
        owned_vm_ids = (
            self._deps.directory_ownership.direct_owned_entity_ids(
                normalized_directory_path, kind="vm"
            )
            if direct_only
            else set()
        )
        summaries: list[VmSummary] = []
        for vm_name in vm_names:
            vm_directory_paths = directory_paths_by_vm.get(vm_name, [])
            if direct_only:
                if vm_name not in owned_vm_ids and not (
                    normalized_directory_path == "root" and not vm_directory_paths
                ):
                    continue
            elif directory_path is not None and normalized_directory_path not in vm_directory_paths:
                continue
            libvirt_state = vm_helpers.dominfo_field(vm_name, "State") or "unknown"
            host_control_status = self._deps.agent_control_store.status_for_vm(vm_name)
            ip_address = (
                vm_helpers.resolve_vm_ip(vm_name) if libvirt_state == "running" else None
            )
            if libvirt_state != "running":
                agent_state = "disabled" if host_control_status == "disabled" else "inactive_vm"
                agent_pid = None
                repo_names: list[str] = []
                updated_at = None
                dispatch_id = None
            elif omit_state or not ip_address:
                if host_control_status == "disabled":
                    agent_state = "disabled"
                else:
                    agent_state = "unknown" if not ip_address else "omitted"
                agent_pid = None
                repo_names = []
                updated_at = None
                dispatch_id = None
            else:
                agent_state, repo_names, updated_at, dispatch_id, agent_pid = (
                    vm_helpers.probe_agent_state(vm_name, ip_address, vm_user)
                )
            summaries.append(
                VmSummary(
                    vm_name=vm_name,
                    issue_number=session_helpers.extract_issue_number(vm_name),
                    session_ids=session_ids_by_vm.get(vm_name, []),
                    labels=labels_by_vm.get(vm_name, {}),
                    directory_paths=vm_directory_paths,
                    libvirt_state=libvirt_state,
                    ip_address=ip_address,
                    agent_state=agent_state,
                    agent_pid=agent_pid
                    if libvirt_state == "running" and not omit_state and ip_address
                    else None,
                    agent_repo_names=repo_names,
                    last_state_update_at=updated_at,
                    dispatch_id=dispatch_id
                    if libvirt_state == "running" and not omit_state and ip_address
                    else None,
                )
            )
        return summaries

    def list_background_workers(self) -> list[WorkerSummary]:
        summaries: list[WorkerSummary] = []
        for worker_id, name, unit in _WORKER_DEFINITIONS:
            try:
                payload = _systemctl_show(unit)
            except RuntimeError as exc:
                summaries.append(
                    WorkerSummary(
                        worker_id=worker_id,
                        name=name,
                        unit=unit,
                        active_state="unavailable",
                        running=False,
                        failed=False,
                        detail=str(exc),
                    )
                )
                continue

            active_state = payload.get("ActiveState", "") or "unknown"
            sub_state = payload.get("SubState", "") or None
            result = payload.get("Result", "") or None
            unit_file_state = payload.get("UnitFileState", "") or None
            description = payload.get("Description", "") or None
            main_pid_raw = payload.get("MainPID", "").strip()
            main_pid = (
                int(main_pid_raw) if main_pid_raw.isdigit() and int(main_pid_raw) > 0 else None
            )
            detail_parts = []
            if payload.get("LoadState", "").strip():
                detail_parts.append(f"load={payload['LoadState'].strip()}")
            exec_code = payload.get("ExecMainCode", "").strip()
            exec_status = payload.get("ExecMainStatus", "").strip()
            if exec_code or exec_status:
                detail_parts.append(f"exec={exec_code or '?'}:{exec_status or '?'}")

            summaries.append(
                WorkerSummary(
                    worker_id=worker_id,
                    name=name,
                    unit=unit,
                    active_state=active_state,
                    sub_state=sub_state,
                    result=result,
                    unit_file_state=unit_file_state,
                    description=description,
                    main_pid=main_pid,
                    running=active_state == "active",
                    failed=active_state == "failed" or result == "failed",
                    detail=", ".join(detail_parts) or None,
                )
            )

        return summaries

    def get_background_worker_log_snapshot(self, worker_id: str, *, lines: int = 200) -> str:
        worker = _worker_definition(worker_id)
        if worker is None:
            return f"Worker log unavailable: unknown worker '{worker_id}'."
        _, name, unit = worker
        text = _journalctl_tail(unit, lines=lines)
        return f"Worker: {name}\nUnit: {unit}\n\n{text}".strip()

    def start_background_worker(self, worker_id: str) -> WorkerActionResult:
        worker = _worker_definition(worker_id)
        if worker is None:
            return WorkerActionResult(
                success=False,
                worker_id=worker_id,
                unit="",
                action="start",
                message=f"Unknown worker: {worker_id}",
            )
        resolved_worker_id, name, unit = worker
        completed = _run_systemd_control("start", unit)
        message = (completed.stdout or completed.stderr or "").strip() or f"Started {name}"
        return WorkerActionResult(
            success=completed.returncode == 0,
            worker_id=resolved_worker_id,
            unit=unit,
            action="start",
            message=message,
        )

    def stop_background_worker(self, worker_id: str) -> WorkerActionResult:
        worker = _worker_definition(worker_id)
        if worker is None:
            return WorkerActionResult(
                success=False,
                worker_id=worker_id,
                unit="",
                action="stop",
                message=f"Unknown worker: {worker_id}",
            )
        resolved_worker_id, name, unit = worker
        completed = _run_systemd_control("stop", unit)
        message = (completed.stdout or completed.stderr or "").strip() or f"Stopped {name}"
        return WorkerActionResult(
            success=completed.returncode == 0,
            worker_id=resolved_worker_id,
            unit=unit,
            action="stop",
            message=message,
        )

    def read_worker_global_config(self, worker_id: str):
        return self._deps.workflow_config.read_worker_global_config(worker_id)

    def write_worker_global_config(
        self,
        worker_id: str,
        payload: dict[str, object],
        *,
        expected_revision: str | None = None,
    ):
        return self._deps.workflow_config.write_worker_global_config(
            worker_id, payload, expected_revision=expected_revision
        )

    def read_workflow_editor_document(
        self, workflow_pack: str | None = None
    ) -> WorkflowEditorDocumentPayload:
        if workflow_pack is None:
            return self._deps.workflow_config.read_workflow_editor_document()
        return self._deps.workflow_config.read_workflow_editor_document(workflow_pack=workflow_pack)

    def read_composed_workflow_editor_document(self) -> WorkflowEditorDocumentPayload:
        return self._deps.workflow_config.read_composed_workflow_editor_document()

    def read_workflow_editor_catalog(
        self, directory_path: str | None = None
    ) -> WorkflowEditorCatalogPayload:
        return self._deps.workflow_config.read_workflow_editor_catalog(directory_path=directory_path)

    def validate_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        workflow_pack: str | None = None,
    ) -> WorkflowEditorValidationPayload:
        if workflow_pack is None:
            return self._deps.workflow_config.validate_workflow_editor_document(
                config=config, layout=layout, graphs=graphs
            )
        return self._deps.workflow_config.validate_workflow_editor_document(
            config=config, layout=layout, graphs=graphs, workflow_pack=workflow_pack
        )

    def save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        expected_revision: str | None = None,
        workflow_pack: str | None = None,
    ) -> WorkflowEditorSavePayload:
        if workflow_pack is None:
            return self._deps.workflow_config.save_workflow_editor_document(
                config=config,
                layout=layout,
                graphs=graphs,
                expected_revision=expected_revision,
            )
        return self._deps.workflow_config.save_workflow_editor_document(
            config=config,
            layout=layout,
            graphs=graphs,
            expected_revision=expected_revision,
            workflow_pack=workflow_pack,
        )

    def read_workflow_run_snapshot(self) -> WorkflowRunSnapshotPayload:
        return self._deps.workflow_config.read_workflow_run_snapshot()

    def read_workflow_run_detail(
        self,
        *,
        run_id: str | None = None,
        event_id: str | None = None,
        trigger_event_id: str | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        artifact_id: str | None = None,
    ) -> WorkflowRunDetailPayload:
        return self._deps.workflow_config.read_workflow_run_detail(
            run_id=run_id,
            event_id=event_id,
            trigger_event_id=trigger_event_id,
            trace_id=trace_id,
            session_id=session_id,
            artifact_id=artifact_id,
        )

    def read_workflow_run_search(
        self,
        *,
        node_id: str | None = None,
        graph_id: str | None = None,
        without_session: bool = False,
        include_completed: bool = True,
        limit: int | None = None,
    ) -> WorkflowRunSearchPayload:
        return self._deps.workflow_config.read_workflow_run_search(
            node_id=node_id,
            graph_id=graph_id,
            without_session=without_session,
            include_completed=include_completed,
            limit=limit,
        )

    def clear_workflow_run(self, event_id: str):
        return self._deps.workflow_config.clear_workflow_run(event_id)

    def dismiss_workflow_node_failures(self, node_id: str):
        return self._deps.workflow_config.dismiss_workflow_node_failures(node_id)

    def resume_workflow_run(self, event_id: str):
        return self._deps.workflow_config.resume_workflow_run(event_id)

    def workflow_event_journal(self) -> WorkflowEventJournal:
        return self._deps.workflow_config.workflow_event_journal()

    def upsert_trigger_config(
        self, trigger_id: str, payload: dict[str, object]
    ) -> TriggerConfigMutationPayload:
        return self._deps.workflow_config.upsert_trigger_config(trigger_id, payload)

    def delete_trigger_config(self, trigger_id: str) -> TriggerConfigMutationPayload:
        return self._deps.workflow_config.delete_trigger_config(trigger_id)

    def upsert_effect_config(
        self, effect_id: str, payload: dict[str, object]
    ) -> EffectConfigMutationPayload:
        return self._deps.workflow_config.upsert_effect_config(effect_id, payload)

    def delete_effect_config(self, effect_id: str) -> EffectConfigMutationPayload:
        return self._deps.workflow_config.delete_effect_config(effect_id)


__all__ = [
    "SessionAdminDependencies",
    "SessionAdminFacade",
    "TeamsAdminDependencies",
    "TeamsAdminFacade",
    "WorkerAdminDependencies",
    "WorkerAdminFacade",
]
