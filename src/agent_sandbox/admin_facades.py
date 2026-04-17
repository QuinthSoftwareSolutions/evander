from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import admin_service as admin_service_module
from .admin_service import (
    _WORKER_DEFINITIONS,
    _worker_definition,
    EffectConfigMutationPayload,
    TriggerConfigMutationPayload,
    WorkflowEditorCatalogPayload,
    WorkflowEditorDocumentPayload,
    WorkflowEditorSavePayload,
    WorkflowEditorValidationPayload,
    WorkflowRunSnapshotPayload,
    WorkerActionResult,
    WorkerSummary,
)
from .directories import DirectoryService
from .models import SessionActionResult, SessionSummary, ToolName, VmSummary, WakeScheduledRequest, WakeScheduleDefinition
from .teams_auth import DeviceCodeLoginSession

if TYPE_CHECKING:
    from .admin_service import AdminService


class TeamsAdminFacade:
    def __init__(self, service: "AdminService") -> None:
        self._service = service

    def login(self, *, directory: str = "root") -> DeviceCodeLoginSession:
        normalized_directory, config, identity_id, provider = self._service._teams_auth_provider_for_directory(directory)
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
        normalized_directory, config, identity_id, provider = self._service._teams_auth_provider_for_directory(directory)
        credentials = provider.complete_device_code_login(login_session, timeout_seconds=timeout_seconds)
        return self._service._teams_auth_payload(
            directory_path=normalized_directory,
            config=config,
            identity_id=identity_id,
            credentials=credentials,
        )

    def status(self, *, directory: str = "root") -> dict[str, object]:
        normalized_directory, config, identity_id, provider = self._service._teams_auth_provider_for_directory(directory)
        return self._service._teams_auth_payload(
            directory_path=normalized_directory,
            config=config,
            identity_id=identity_id,
            credentials=provider.load_credentials(),
        )

    def logout(self, *, directory: str = "root") -> dict[str, object]:
        normalized_directory, config, identity_id, provider = self._service._teams_auth_provider_for_directory(directory)
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
        directory_service = DirectoryService(self._service.runtime.directories)
        directory_paths = ["root", *[item.path for item in directory_service.list_descendants("root")]]
        entries: list[dict[str, object]] = []
        for directory_path in directory_paths:
            resolved_config = self._service.runtime.provider_config.resolve_for_directory(directory_path)
            if resolved_config.teams_backend != "delegated_graph_api":
                continue
            _, config, identity_id, provider = self._service._teams_auth_provider_for_directory(directory_path)
            entries.append(
                self._service._teams_auth_payload(
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
        normalized_directory, config, identity_id, provider = self._service._teams_auth_provider_for_directory(directory)
        normalized_team_id = str(team_id or "").strip()
        if not normalized_team_id:
            raise ValueError("team_id is required")
        normalized_channel_name = str(channel_name or "").strip()
        if not normalized_channel_name and not include_all:
            raise ValueError("channel_name is required unless include_all=True")

        channels = self._service._teams_graph_list_channels(config, provider, team_id=normalized_team_id)
        matches: list[dict[str, object]] = []
        for channel in channels:
            display_name = str(channel.get("displayName") or "").strip()
            if normalized_channel_name and display_name.casefold() != normalized_channel_name.casefold():
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
    def __init__(self, service: "AdminService") -> None:
        self._service = service

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

        now = admin_service_module._utc_now()
        normalized_directory_path = self._service._normalize_directory_path(directory_path)
        owned_session_ids = (
            self._service._direct_owned_entity_ids(normalized_directory_path, kind="session") if direct_only else set()
        )
        summaries: list[SessionSummary] = []
        for session_id, manifest in sorted(self._service.runtime.sessions.read_all().items(), key=admin_service_module._session_sort_key):
            if direct_only:
                if session_id not in owned_session_ids:
                    continue
            elif directory_path is not None and manifest.directory_path != normalized_directory_path:
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
                    issue_number=admin_service_module._extract_issue_number(manifest.vm_id) or admin_service_module._extract_issue_number(session_id),
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
        payload = self._service.runtime.wake_store.read_raw()
        raw_requests = payload.get("requests")
        raw_dispatches = payload.get("dispatches")
        if not isinstance(raw_requests, dict):
            return []
        latest_dispatch_by_request_id: dict[str, dict[str, Any]] = {}
        if isinstance(raw_dispatches, dict):
            for raw_value in raw_dispatches.values():
                if not isinstance(raw_value, dict):
                    continue
                dispatch_request_id = str(raw_value.get("request_id") or "").strip()
                if not dispatch_request_id:
                    continue
                dispatched_at = str(raw_value.get("dispatched_at") or "")
                current = latest_dispatch_by_request_id.get(dispatch_request_id)
                if current is None or dispatched_at > str(current.get("dispatched_at") or ""):
                    latest_dispatch_by_request_id[dispatch_request_id] = raw_value

        schedules: list[dict[str, Any]] = []
        for raw_value in raw_requests.values():
            if not isinstance(raw_value, dict):
                continue
            request = WakeScheduledRequest.model_validate(raw_value)
            if session_id and request.session_id != session_id:
                continue
            if not include_disabled and not request.enabled:
                continue
            dispatch = latest_dispatch_by_request_id.get(request.request_id)
            schedules.append(
                {
                    "request_id": request.request_id,
                    "session_id": request.session_id,
                    "resume_at": request.schedule.resume_at,
                    "cron": request.schedule.cron,
                    "prompt": request.resume_prompt.prompt,
                    "resume_mode": request.resume_mode,
                    "enabled": request.enabled,
                    "source": request.source,
                    "created_at": request.created_at,
                    "updated_at": request.updated_at,
                    "last_dispatched_at": request.last_dispatched_at,
                    "next_run_at": request.next_run_at,
                    "last_dispatch": (
                        {
                            "dispatch_id": dispatch.get("dispatch_id"),
                            "status": dispatch.get("status"),
                            "error": dispatch.get("error"),
                            "dispatched_at": dispatch.get("dispatched_at"),
                        }
                        if dispatch is not None
                        else None
                    ),
                }
            )
        schedules.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return schedules

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
        normalized_resume_mode = admin_service_module._normalize_resume_mode(resume_mode)
        normalized_source = str(source or "admin_cli").strip() or "admin_cli"
        if normalized_source.startswith("mcp") and normalized_resume_mode != "same_context":
            raise ValueError("MCP scheduling only supports resume_mode='same_context'.")
        normalized_cron = None
        if cron is not None and str(cron).strip():
            normalized_cron = admin_service_module._require_valid_cron(str(cron))
        schedule = WakeScheduleDefinition(resume_at=resume_at, cron=normalized_cron)
        normalized_session_id = session_id.strip()
        self._enforce_wake_schedule_guardrails(
            session_id=normalized_session_id,
            schedule=schedule,
            enabled=bool(enabled),
        )
        return self._service.runtime.wake_store.schedule_resume_request(
            session_id=normalized_session_id,
            prompt=normalized_prompt,
            schedule=schedule,
            request_id=request_id.strip() if isinstance(request_id, str) and request_id.strip() else None,
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
        existing = self._service.runtime.wake_store.get_request(request_id.strip())
        if existing is None:
            raise ValueError(f"Wake schedule not found: {request_id}")
        updated_prompt = existing.resume_prompt.prompt if prompt is None else str(prompt).strip()
        if not updated_prompt:
            raise ValueError("prompt cannot be empty")
        updated_mode = existing.resume_mode if resume_mode is None else admin_service_module._normalize_resume_mode(resume_mode)
        if str(existing.source or "").strip().startswith("mcp") and updated_mode != "same_context":
            raise ValueError("MCP scheduling only supports resume_mode='same_context'.")
        resume_changed = resume_at is not None
        cron_changed = cron is not None
        if resume_changed and cron_changed:
            raise ValueError("Provide only one of resume_at or cron when editing a schedule.")
        if resume_changed:
            updated_schedule = WakeScheduleDefinition(resume_at=resume_at, cron=None)
        elif cron_changed:
            normalized_cron = admin_service_module._require_valid_cron(str(cron)) if str(cron).strip() else None
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
        existing.updated_at = admin_service_module._utc_now()
        if existing.schedule.resume_at is not None:
            existing.next_run_at = existing.schedule.resume_at
        elif cron_changed:
            existing.next_run_at = admin_service_module._utc_now()
        self._service.runtime.wake_store.upsert_request(existing)
        return existing

    def delete_wake_schedule(self, request_id: str) -> bool:
        normalized = request_id.strip()
        if not normalized:
            raise ValueError("request_id cannot be empty")
        return self._service.runtime.wake_store.delete_request(normalized)

    def renew_session(self, target: str, *, extension: str = "1d") -> SessionActionResult:
        session_id, manifest = admin_service_module._resolve_session_target(target, self._service.runtime.sessions.read_all())
        previous_expires_at = manifest.expires_at
        manifest.expires_at = admin_service_module._utc_now() + admin_service_module._parse_session_extension(extension)
        self._service.runtime.sessions.put(session_id, manifest)
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
        session_id, manifest = admin_service_module._resolve_session_target(target, self._service.runtime.sessions.read_all())
        previous_expires_at = manifest.expires_at
        manifest.expires_at = admin_service_module._utc_now()
        self._service.runtime.sessions.put(session_id, manifest)
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
        session_id, _ = admin_service_module._resolve_session_target(target, self._service.runtime.sessions.read_all())
        self._service.runtime.sessions.delete(session_id)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="delete_session",
            message=f"Deleted session {session_id}",
        )

    def set_session_allowed_tools(self, target: str, allowed_tools: list[str | ToolName]) -> SessionActionResult:
        session_id, manifest = admin_service_module._resolve_session_target(target, self._service.runtime.sessions.read_all())
        normalized_tools: list[ToolName] = []
        seen: set[ToolName] = set()
        for tool in allowed_tools:
            normalized_tool = tool if isinstance(tool, ToolName) else ToolName(str(tool).strip())
            if normalized_tool in seen:
                continue
            seen.add(normalized_tool)
            normalized_tools.append(normalized_tool)
        manifest.allowed_tools = normalized_tools
        self._service.runtime.sessions.put(session_id, manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="set_session_allowed_tools",
            message=f"Updated allowed tools for session {session_id}",
        )

    def set_session_allowed_script_names(self, target: str, allowed_script_names: list[str]) -> SessionActionResult:
        session_id, manifest = admin_service_module._resolve_session_target(target, self._service.runtime.sessions.read_all())
        manifest.allowed_script_names = admin_service_module._normalize_allowed_script_names(allowed_script_names)
        self._service.runtime.sessions.put(session_id, manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="set_session_allowed_script_names",
            message=f"Updated allowed script names for session {session_id}",
        )

    def set_session_model(self, target: str, model: str) -> SessionActionResult:
        session_id, manifest = admin_service_module._resolve_session_target(target, self._service.runtime.sessions.read_all())
        normalized_model = str(model).strip()
        if not normalized_model:
            raise ValueError("model must be non-empty")
        manifest.model = normalized_model
        self._service.runtime.sessions.put(session_id, manifest)
        return SessionActionResult(
            success=True,
            session_id=session_id,
            action="set_session_model",
            message=f"Updated model for session {session_id}",
        )

    def _active_wake_requests(self, *, exclude_request_id: str | None = None) -> list[WakeScheduledRequest]:
        payload = self._service.runtime.wake_store.read_raw()
        raw_requests = payload.get("requests", {})
        if not isinstance(raw_requests, dict):
            return []
        active: list[WakeScheduledRequest] = []
        for raw_value in raw_requests.values():
            if not isinstance(raw_value, dict):
                continue
            request = WakeScheduledRequest.model_validate(raw_value)
            if exclude_request_id and request.request_id == exclude_request_id:
                continue
            if request.enabled:
                active.append(request)
        return active

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
        max_active_per_session = self._service.config.host_heartbeat_schedule_max_active_per_session
        if max_active_per_session is not None:
            active_for_session = sum(1 for request in active_requests if request.session_id == session_id)
            if active_for_session >= max_active_per_session:
                raise ValueError(
                    "Wake schedule limit reached for session "
                    f"{session_id}: max_active_per_session={max_active_per_session}"
                )
        max_active_total = self._service.config.host_heartbeat_schedule_max_active_total
        if max_active_total is not None and len(active_requests) >= max_active_total:
            raise ValueError(f"Wake schedule limit reached: max_active_total={max_active_total}")
        min_interval_seconds = self._service.config.host_heartbeat_schedule_min_interval_seconds
        cron = str(schedule.cron or "").strip()
        if min_interval_seconds is not None and cron:
            actual_interval_seconds = admin_service_module._cron_min_interval_seconds(cron)
            if actual_interval_seconds is None:
                raise ValueError(f"Unable to determine cron interval for schedule: {cron}")
            if actual_interval_seconds < min_interval_seconds:
                raise ValueError(
                    "Recurring wake schedule interval is too short: "
                    f"minimum={min_interval_seconds:g}s actual={actual_interval_seconds:g}s"
                )


class WorkerAdminFacade:
    def __init__(self, service: "AdminService") -> None:
        self._service = service

    def list_vms(
        self,
        *,
        include_inactive: bool = True,
        omit_state: bool = False,
        vm_user: str = "agent",
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[VmSummary]:
        vm_names = admin_service_module._virsh_lines(["list", "--all" if include_inactive else "--state-running", "--name"])
        session_ids_by_vm = self._service._session_ids_by_vm()
        labels_by_vm = self._service._labels_by_vm()
        directory_paths_by_vm = self._service._directory_paths_by_vm()
        normalized_directory_path = self._service._normalize_directory_path(directory_path)
        owned_vm_ids = self._service._direct_owned_entity_ids(normalized_directory_path, kind="vm") if direct_only else set()
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
            libvirt_state = admin_service_module._dominfo_field(vm_name, "State") or "unknown"
            host_control_status = self._service.agent_control_store.status_for_vm(vm_name)
            ip_address = admin_service_module._resolve_vm_ip(vm_name) if libvirt_state == "running" else None
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
                agent_state, repo_names, updated_at, dispatch_id, agent_pid = admin_service_module._probe_agent_state(
                    vm_name, ip_address, vm_user
                )
            summaries.append(
                VmSummary(
                    vm_name=vm_name,
                    issue_number=admin_service_module._extract_issue_number(vm_name),
                    session_ids=session_ids_by_vm.get(vm_name, []),
                    labels=labels_by_vm.get(vm_name, {}),
                    directory_paths=vm_directory_paths,
                    libvirt_state=libvirt_state,
                    ip_address=ip_address,
                    agent_state=agent_state,
                    agent_pid=agent_pid if libvirt_state == "running" and not omit_state and ip_address else None,
                    agent_repo_names=repo_names,
                    last_state_update_at=updated_at,
                    dispatch_id=dispatch_id if libvirt_state == "running" and not omit_state and ip_address else None,
                )
            )
        return summaries

    def list_background_workers(self) -> list[WorkerSummary]:
        summaries: list[WorkerSummary] = []
        for worker_id, name, unit in _WORKER_DEFINITIONS:
            try:
                payload = admin_service_module._systemctl_show(unit)
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
            main_pid = int(main_pid_raw) if main_pid_raw.isdigit() and int(main_pid_raw) > 0 else None
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
        text = admin_service_module._journalctl_tail(unit, lines=lines)
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
        completed = admin_service_module._run_systemd_control("start", unit)
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
        completed = admin_service_module._run_systemd_control("stop", unit)
        message = (completed.stdout or completed.stderr or "").strip() or f"Stopped {name}"
        return WorkerActionResult(
            success=completed.returncode == 0,
            worker_id=resolved_worker_id,
            unit=unit,
            action="stop",
            message=message,
        )

    def read_worker_global_config(self, worker_id: str):
        return self._service._read_worker_global_config(worker_id)

    def write_worker_global_config(self, worker_id: str, payload: dict[str, object]):
        return self._service._write_worker_global_config(worker_id, payload)

    def read_workflow_editor_document(self) -> WorkflowEditorDocumentPayload:
        return self._service._read_workflow_editor_document()

    def read_workflow_editor_catalog(self) -> WorkflowEditorCatalogPayload:
        return self._service._read_workflow_editor_catalog()

    def validate_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
    ) -> WorkflowEditorValidationPayload:
        return self._service._validate_workflow_editor_document(config=config, layout=layout)

    def save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
    ) -> WorkflowEditorSavePayload:
        return self._service._save_workflow_editor_document(config=config, layout=layout)

    def read_workflow_run_snapshot(self) -> WorkflowRunSnapshotPayload:
        return self._service._read_workflow_run_snapshot()

    def upsert_trigger_config(self, trigger_id: str, payload: dict[str, object]) -> TriggerConfigMutationPayload:
        return self._service._upsert_trigger_config(trigger_id, payload)

    def delete_trigger_config(self, trigger_id: str) -> TriggerConfigMutationPayload:
        return self._service._delete_trigger_config(trigger_id)

    def upsert_effect_config(self, effect_id: str, payload: dict[str, object]) -> EffectConfigMutationPayload:
        return self._service._upsert_effect_config(effect_id, payload)

    def delete_effect_config(self, effect_id: str) -> EffectConfigMutationPayload:
        return self._service._delete_effect_config(effect_id)


__all__ = ["SessionAdminFacade", "TeamsAdminFacade", "WorkerAdminFacade"]
