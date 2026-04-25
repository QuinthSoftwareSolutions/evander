from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..models import ToolName


class OperationsAdminService:
    """Communications, sessions, directories, and host worker admin operations."""

    def __init__(
        self,
        *,
        project_dir: Path,
        get_privileged_helper: Callable[[], Any | None],
        get_use_privileged_helper: Callable[[], bool],
        set_privileged_helper: Callable[[Any | None], None],
        teams_admin: Any,
        session_admin: Any,
        worker_admin: Any,
        directory_admin: Any,
        privileged_helper_factory: Any,
    ) -> None:
        self._project_dir = project_dir
        self._get_privileged_helper = get_privileged_helper
        self._get_use_privileged_helper = get_use_privileged_helper
        self._set_privileged_helper = set_privileged_helper
        self._teams_admin = teams_admin
        self._session_admin = session_admin
        self._worker_admin = worker_admin
        self._directory_admin = directory_admin
        self._privileged_helper_factory = privileged_helper_factory

    def privileged_helper_required(self) -> bool:
        return bool(self._get_use_privileged_helper())

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
        helper = self._get_privileged_helper()
        if helper is None:
            helper = self._privileged_helper_factory(
                python_executable=sys.executable,
                project_dir=self._project_dir,
            )
            self._set_privileged_helper(helper)
        helper.start()
        return "Privileged operations unlocked."

    def lock_privileged_operations(self):
        helper = self._get_privileged_helper()
        if helper is not None:
            helper.close()
            self._set_privileged_helper(None)

    def privileged_operations_unlocked(self):
        helper = self._get_privileged_helper()
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

    def list_wake_schedules(
        self,
        *,
        session_id: str | None = None,
        include_disabled: bool = True,
    ):
        return self._session_admin.list_wake_schedules(
            session_id=session_id,
            include_disabled=include_disabled,
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
        return self._session_admin.create_wake_schedule(
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
        return self._session_admin.edit_wake_schedule(
            request_id,
            resume_at=resume_at,
            cron=cron,
            prompt=prompt,
            resume_mode=resume_mode,
            enabled=enabled,
        )

    def delete_wake_schedule(self, request_id: str):
        return self._session_admin.delete_wake_schedule(request_id)

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

    def set_session_model(self, target: str, model: str):
        return self._session_admin.set_session_model(target, model)

    def list_background_workers(self):
        return self._worker_admin.list_background_workers()

    def get_background_worker_log_snapshot(self, worker_id: str, *, lines: int = 200):
        return self._worker_admin.get_background_worker_log_snapshot(worker_id, lines=lines)

    def start_background_worker(self, worker_id: str):
        return self._worker_admin.start_background_worker(worker_id)

    def stop_background_worker(self, worker_id: str):
        return self._worker_admin.stop_background_worker(worker_id)

    def list_directory_tree(self):
        return self._directory_admin.tree("root")

    def get_directory(self, reference: str):
        return self._directory_admin.get(self._normalize_directory_path(reference))

    def create_directory_child(self, parent: str, name: str):
        return self._directory_admin.create_child(parent=self._normalize_directory_path(parent), name=name)

    def read_directory_domain_config(self, directory_path: str, domain: str):
        return self._directory_admin.read_domain_config(self._normalize_directory_path(directory_path), domain=domain)

    def write_directory_domain_config(self, directory_path: str, domain: str, payload: dict[str, object]):
        return self._directory_admin.write_domain_config(
            self._normalize_directory_path(directory_path),
            domain=domain,
            payload=payload,
        )

    def compute_directory_effective_config(self, directory_path: str, domain: str):
        return self._directory_admin.compute_effective_config(
            self._normalize_directory_path(directory_path),
            domain=domain,
        )
