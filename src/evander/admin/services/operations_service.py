from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from evander.admin.common import (
    _coerce_optional_datetime,
    _read_json_object,
    _utc_now,
)
from evander.admin.payloads import (
    DeploymentStatusPayload,
    ReleaseChangelogEntryPayload,
    ReleaseChangelogPayload,
    ReleaseInfoPayload,
    ReleaseNewsBlockPayload,
    ReleaseNewsPayload,
)
from evander.admin.release_metadata import read_release_metadata
from evander.admin.services.ports import PrivilegedHelperFactoryPort, PrivilegedHelperPort
from evander.config import AssistantSettingsConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.events.internal import InternalEventSource
from evander.orchestration.host_polling import notify_wake_socket_for_lock
from evander.triggers.queue import SqliteTriggerEventQueue
from evander.events.models import TriggerEvent

from evander.models import ToolName

_SYSTEM_CHAT_ASSISTANT_TRIGGER_ID = "system.chat_assistant"


class OperationsAdminService:
    """Communications, sessions, directories, and host worker admin operations."""

    def __init__(
        self,
        *,
        config: Any | None = None,
        project_dir: Path,
        privileged_helper: PrivilegedHelperPort,
        teams_admin: Any,
        session_admin: Any,
        wake_schedule_admin: Any,
        worker_control_admin: Any,
        directory_admin: Any,
        workflow_config_admin: Any | None = None,
        trigger_repository: Any | None = None,
        privileged_helper_factory: PrivilegedHelperFactoryPort,
        admin_config_mutation_lock: Callable[[], AbstractContextManager[None]] | None = None,
    ) -> None:
        self._config = config
        self._project_dir = project_dir
        self._privileged_helper = privileged_helper
        self._teams_admin = teams_admin
        self._session_admin = session_admin
        self._wake_schedule_admin = wake_schedule_admin
        self._worker_control_admin = worker_control_admin
        self._directory_admin = directory_admin
        self._workflow_config_admin = workflow_config_admin
        self._trigger_repository = trigger_repository
        self._privileged_helper_factory = privileged_helper_factory
        self._admin_config_mutation_lock = admin_config_mutation_lock or nullcontext

    def admin_config_mutation_lock(self) -> AbstractContextManager[None]:
        return self._admin_config_mutation_lock()

    def privileged_helper_required(self) -> bool:
        return bool(self._privileged_helper.get_use_privileged_helper())

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        result = self._directory_admin.get(directory_path)
        if not result.ok or result.data is None:
            return "root"
        return result.data.details.directory.path

    def teams_auth_login(self, *, directory: str = "root"):
        return self._teams_admin.login(directory=directory)

    def teams_auth_complete_login(
        self,
        login_session,
        *,
        directory: str = "root",
        timeout_seconds: float | None = None,
    ):
        return self._teams_admin.complete_login(
            login_session,
            directory=directory,
            timeout_seconds=timeout_seconds,
        )

    def teams_auth_status(self, *, directory: str = "root"):
        return self._teams_admin.status(directory=directory)

    def teams_auth_logout(self, *, directory: str = "root"):
        return self._teams_admin.logout(directory=directory)

    def teams_auth_list(self):
        return self._teams_admin.list()

    def teams_auth_list_channels(
        self,
        *,
        directory: str = "root",
        team_id: str,
        channel_name: str | None = None,
        include_all: bool = False,
    ):
        return self._teams_admin.list_channels(
            directory=directory,
            team_id=team_id,
            channel_name=channel_name,
            include_all=include_all,
        )

    def unlock_privileged_operations(self):
        if not self.privileged_helper_required():
            return "Privileged operations unlocked."
        helper = self._privileged_helper.get_privileged_helper()
        if helper is None:
            helper = self._privileged_helper_factory.create_privileged_helper(
                python_executable=sys.executable,
                project_dir=self._project_dir,
            )
            self._privileged_helper.set_privileged_helper(helper)
        helper.start()
        return "Privileged operations unlocked."

    def lock_privileged_operations(self):
        helper = self._privileged_helper.get_privileged_helper()
        if helper is not None:
            helper.close()
            self._privileged_helper.set_privileged_helper(None)

    def privileged_operations_unlocked(self):
        helper = self._privileged_helper.get_privileged_helper()
        return helper is not None and helper.is_running()

    def list_sessions(
        self,
        *,
        include_expired: bool = False,
        expired_only: bool = False,
        directory_path: str | None = None,
        direct_only: bool = False,
    ):
        return self._session_admin.list_sessions(
            include_expired=include_expired,
            expired_only=expired_only,
            directory_path=directory_path,
            direct_only=direct_only,
        )

    def get_session_summary(self, session_id: str):
        return self._session_admin.get_session_summary(session_id)

    def list_wake_schedules(
        self,
        *,
        session_id: str | None = None,
        include_disabled: bool = True,
    ):
        return self._wake_schedule_admin.list_wake_schedules(
            session_id=session_id,
            include_disabled=include_disabled,
        )

    def list_scheduled_wake_ledger(
        self,
        *,
        statuses: list[str] | None = None,
        session_id: str | None = None,
        limit: int = 100,
        include_completed: bool = True,
    ):
        return self._wake_schedule_admin.list_scheduled_wake_ledger(
            statuses=statuses,
            session_id=session_id,
            limit=limit,
            include_completed=include_completed,
        )

    def retry_scheduled_wake(
        self,
        request_id: str,
        *,
        resume_at: datetime | None = None,
        run_now: bool = True,
    ):
        return self._wake_schedule_admin.retry_scheduled_wake(
            request_id,
            resume_at=resume_at,
            run_now=run_now,
        )

    def create_wake_schedule(
        self,
        *,
        session_id: str,
        prompt: str,
        resume_at: datetime | None = None,
        cron: str | None = None,
        resume_mode: str = "same_context",
        enabled: bool = True,
        request_id: str | None = None,
        source: str | None = "admin_cli",
        metadata: dict[str, Any] | None = None,
    ):
        return self._wake_schedule_admin.create_wake_schedule(
            session_id=session_id,
            prompt=prompt,
            resume_at=resume_at,
            cron=cron,
            resume_mode=resume_mode,
            enabled=enabled,
            request_id=request_id,
            source=source,
            metadata=metadata,
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
    ):
        return self._wake_schedule_admin.edit_wake_schedule(
            request_id,
            resume_at=resume_at,
            cron=cron,
            prompt=prompt,
            resume_mode=resume_mode,
            enabled=enabled,
        )

    def delete_wake_schedule(self, request_id: str):
        return self._wake_schedule_admin.delete_wake_schedule(request_id)

    def renew_session(self, target: str, *, extension: str = "1d"):
        return self._session_admin.renew_session(target, extension=extension)

    def revoke_session(self, target: str):
        return self._session_admin.revoke_session(target)

    def delete_session(self, target: str):
        return self._session_admin.delete_session(target)

    def set_session_allowed_tools(self, target: str, allowed_tools: list[str | ToolName]):
        return self._session_admin.set_session_allowed_tools(target, allowed_tools)

    def set_session_allowed_script_names(self, target: str, allowed_script_names: list[str]):
        return self._session_admin.set_session_allowed_script_names(target, allowed_script_names)

    def reconcile_session_policy(self, target, policy, *, directory_path):
        return self._session_admin.reconcile_session_policy(
            target, policy, directory_path=directory_path
        )

    def update_session_policy(self, target: str, payload: dict[str, object]):
        return self._session_admin.update_session_policy(target, payload)

    def update_session_model_settings(self, target: str, settings: Mapping[str, Any]):
        return self._session_admin.update_session_model_settings(target, settings)

    def set_session_model(self, target: str, model: str):
        return self._session_admin.set_session_model(target, model)

    def set_session_codex_auth_mode(self, target: str, mode: str | None):
        return self._session_admin.set_session_codex_auth_mode(target, mode)

    def list_background_workers(self):
        return self._worker_control_admin.list_background_workers()

    def get_background_worker_log_snapshot(self, worker_id: str, *, lines: int = 200):
        return self._worker_control_admin.get_background_worker_log_snapshot(worker_id, lines=lines)

    def start_background_worker(self, worker_id: str):
        return self._worker_control_admin.start_background_worker(worker_id)

    def stop_background_worker(self, worker_id: str):
        return self._worker_control_admin.stop_background_worker(worker_id)

    def list_directory_tree(self):
        return self._directory_admin.tree("root")

    def get_directory(self, reference: str):
        return self._directory_admin.get(self._normalize_directory_path(reference))

    def create_directory_child(self, parent: str, name: str):
        return self._directory_admin.create_child(
            parent=self._normalize_directory_path(parent), name=name
        )

    def read_directory_domain_config(self, directory_path: str, domain: str):
        return self._directory_admin.read_domain_config(
            self._normalize_directory_path(directory_path), domain=domain
        )

    def write_directory_domain_config(
        self, directory_path: str, domain: str, payload: dict[str, object], *, strict: bool = False
    ):
        # Scoped mutations must fail when their destination disappears rather than
        # inheriting the operator-facing fallback to root.
        return self._directory_admin.write_domain_config(
            directory_path if strict else self._normalize_directory_path(directory_path),
            domain=domain,
            payload=payload,
        )

    def compute_directory_effective_config(self, directory_path: str, domain: str):
        return self._directory_admin.compute_effective_config(
            self._normalize_directory_path(directory_path),
            domain=domain,
        )

    def read_deployment_status(self):
        payload_path = self._config.runtime_dir / "deployment-status.json"
        raw = _read_json_object(payload_path)
        if not raw:
            return DeploymentStatusPayload()
        try:
            status = DeploymentStatusPayload.model_validate(raw)
        except ValidationError:
            return DeploymentStatusPayload()
        if not status.active:
            return status
        started_at = _coerce_optional_datetime(status.started_at)
        updated_at = _coerce_optional_datetime(status.updated_at)
        status = status.model_copy(update={"started_at": started_at, "updated_at": updated_at})
        checked_at = updated_at or started_at
        is_stale = False
        if checked_at is not None:
            is_stale = (_utc_now() - checked_at).total_seconds() > status.ttl_seconds
        if is_stale:
            return status.model_copy(update={"active": False, "stale": True})
        if not status.message:
            return status.model_copy(update={"message": "Control plane update in progress."})
        return status

    def read_release_info(self, *, news_index: int = 0):
        release_metadata = read_release_metadata(self._project_dir, news_index=news_index)
        return ReleaseInfoPayload(
            version=release_metadata.version,
            version_source=release_metadata.version_source,
            build=release_metadata.build,
            build_source=release_metadata.build_source,
            changelog=(
                None
                if release_metadata.changelog is None
                else ReleaseChangelogPayload(
                    source=release_metadata.changelog.source,
                    path=release_metadata.changelog.path,
                    heading=release_metadata.changelog.heading,
                    entries=[
                        ReleaseChangelogEntryPayload(
                            text=entry.text,
                            kind=entry.kind,
                        )
                        for entry in release_metadata.changelog.entries
                    ],
                )
            ),
            news=(
                None
                if release_metadata.news is None
                else ReleaseNewsPayload(
                    source=release_metadata.news.source,
                    path=release_metadata.news.path,
                    index=release_metadata.news.index,
                    heading=release_metadata.news.heading,
                    blocks=[
                        ReleaseNewsBlockPayload(
                            kind=block.kind,
                            text=block.text,
                            level=block.level,
                            items=list(block.items),
                            src=block.src,
                            dark_src=block.dark_src,
                            alt=block.alt,
                            title=block.title,
                        )
                        for block in release_metadata.news.blocks
                    ],
                    has_previous=release_metadata.news.has_previous,
                    has_next=release_metadata.news.has_next,
                    previous_index=release_metadata.news.previous_index,
                    next_index=release_metadata.news.next_index,
                )
            ),
            missing_reasons=release_metadata.missing_reasons,
        )

    def release_changelog_file(self) -> Path:
        changelog_path = self._project_dir / "CHANGELOG.md"
        if not changelog_path.is_file():
            raise FileNotFoundError(f"{changelog_path.name} is missing.")
        return changelog_path

    def assistant_settings_path(self) -> Path:
        return self._config.runtime_dir / "assistant-settings.json"

    def read_assistant_settings(self) -> dict[str, object]:
        path = self.assistant_settings_path()
        if not path.exists():
            return self._config.assistant_settings.model_dump(mode="json", exclude_none=True)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"assistant settings file {path} must contain a JSON object")
        return AssistantSettingsConfig.model_validate(payload).model_dump(
            mode="json", exclude_none=True
        )

    def write_assistant_settings(self, payload: dict[str, object]) -> dict[str, object]:
        config = AssistantSettingsConfig.model_validate(payload).model_dump(
            mode="json", exclude_none=True
        )
        path = self.assistant_settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, indent=2, sort_keys=True), encoding="utf-8")
        return config

    def ensure_chat_assistant_workflow(self):
        return self._trigger_repository.read_document()

    def publish_chat_assistant_message(
        self,
        *,
        user_id: str,
        thread_id: str,
        user_message_id: str,
        content: str,
        backend: str | None = None,
        assistant_capability_mode: str | None = None,
    ) -> dict[str, object]:
        document = self.ensure_chat_assistant_workflow()
        worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(document.config)
        queue = SqliteTriggerEventQueue(
            worker_config.orchestration_store_path,
            retention_days=worker_config.orchestration_retention_days,
            compact_history_retention_days=(
                worker_config.orchestration_compact_history_retention_days
            ),
        )
        normalized_backend = str(backend or self._config.execution_backend).strip() or "libvirt_vm"
        normalized_capability_mode = str(assistant_capability_mode or "").strip() or "read-only"
        if normalized_capability_mode != "full-access":
            normalized_capability_mode = "read-only"
        event_id = f"chat:{user_message_id}"
        event = TriggerEvent(
            event_id=event_id,
            trigger_id=_SYSTEM_CHAT_ASSISTANT_TRIGGER_ID,
            trigger_type="trigger:system:chat-assistant",
            occurred_at=_utc_now(),
            payload={
                "user_id": user_id,
                "thread_id": thread_id,
                "user_message_id": user_message_id,
                "content": content,
                "backend": normalized_backend,
                "assistant_capability_mode": normalized_capability_mode,
            },
        )
        publish_result = queue.publish_result(
            event,
            idempotency_key=event_id,
            directory_path="root",
        )
        record = publish_result.record
        if publish_result.created and record.event_id == event_id:
            notify_wake_socket_for_lock(worker_config.effect_lock_path)
        return record.model_dump(mode="json")

    def publish_external_workflow_event(
        self,
        *,
        event_type: object,
        payload: dict[str, Any],
        correlation_key: object | None = None,
        idempotency_key: str | None = None,
        source: InternalEventSource | dict[str, Any] | None = None,
    ) -> dict[str, object]:
        return self._workflow_config_admin.publish_external_workflow_event(
            event_type=event_type,
            payload=payload,
            correlation_key=correlation_key,
            idempotency_key=idempotency_key,
            source=source,
        )
