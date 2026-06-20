from __future__ import annotations

import base64
import json
import logging
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from starlette.applications import Starlette

from agent_sandbox.capacity_health import (
    HealthState,
    SessionStorageSample,
    TelemetryError,
    TelemetryStatus,
    collect_disk_usage,
    collect_recursive_directory_size,
    top_session_storage,
)
from agent_sandbox.managed_sections import merge_managed_section
from agent_sandbox.session_artifacts import resolve_artifact_file

from .docker_runtime import DockerContainerRuntime
from .models import (
    DEFAULT_WORKER_ROOT,
    WorkerFileWriteRequest,
    WorkerFileWriteRecord,
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerDiskTelemetry,
    WorkerJobTelemetry,
    WorkerMssqlDatabaseDeleteRecord,
    WorkerMssqlDatabaseListRecord,
    WorkerMssqlDatabaseRecord,
    WorkerMssqlDatabaseRequest,
    WorkerMssqlSessionDeleteRecord,
    WorkerRepoCloneRecord,
    WorkerRepoCloneRequest,
    SAFE_ID_PATTERN,
    WorkerSessionPrepareRequest,
    WorkerSessionPaths,
    WorkerSessionRecord,
    WorkerSessionStorageTelemetry,
    WorkerTelemetryPayload,
    now_utc,
)
from .mssql import WorkerMssqlProvisioner, WorkerMssqlStore
from .runtime import ContainerRuntime
from .sandbox_profile import WorkerSandboxProfile
from .security import safe_subprocess_error, sanitize_metadata, sanitize_text
from .store import WorkerJobStore

logger = logging.getLogger(__name__)
_CODEX_SESSION_ID_LOG_PATTERN = re.compile(
    r"^session id:\s*(?P<thread_id>\S+)\s*$", re.IGNORECASE | re.MULTILINE
)
_CODEX_TOOL_SUMMARY_LIMIT = 240
_SESSION_MESSAGE_ARTIFACTS = {
    "no response requested",
    "no response requested.",
}


class WorkerDaemon:
    def __init__(
        self,
        *,
        runtime_dir: Path,
        worker_root: Path = DEFAULT_WORKER_ROOT,
        container_runtime: ContainerRuntime | None = None,
        docker_network: str | None = "none",
        sandbox_profile: WorkerSandboxProfile | None = None,
        read_only_mounts: Sequence[str] | None = None,
        allowed_git_source_prefixes: Sequence[str] | None = None,
        git_ssh_source_dir: Path | None = None,
        token: str | None = None,
        mssql_enabled: bool = False,
        mssql_admin_user: str = "sa",
        mssql_admin_password: str | None = None,
        mssql_admin_password_env: str = "AGENT_SANDBOX_WORKER_MSSQL_ADMIN_PASSWORD",
        mssql_admin_host: str = "127.0.0.1",
        mssql_port: int = 1433,
        mssql_container_host: str = "172.17.0.1",
        mssql_sqlcmd_path: str = "sqlcmd",
        mssql_provisioner: WorkerMssqlProvisioner | None = None,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.worker_root = worker_root
        self.store = WorkerJobStore(runtime_dir / "worker-jobs.json")
        self.allowed_git_source_prefixes = tuple(
            prefix.strip()
            for prefix in (allowed_git_source_prefixes or ())
            if str(prefix).strip()
        )
        self.git_ssh_source_dir = Path(git_ssh_source_dir) if git_ssh_source_dir else None
        self.container_runtime = container_runtime or DockerContainerRuntime(
            docker_network=docker_network,
            sandbox_profile=sandbox_profile,
            read_only_mounts=read_only_mounts,
        )
        self.token = token if token is not None else os.environ.get("AGENT_SANDBOX_WORKER_TOKEN")
        self.mssql = mssql_provisioner or WorkerMssqlProvisioner(
            enabled=mssql_enabled,
            store=WorkerMssqlStore(runtime_dir / "mssql-state.json"),
            admin_user=mssql_admin_user,
            admin_password=(
                mssql_admin_password
                if mssql_admin_password is not None
                else os.environ.get(mssql_admin_password_env)
            ),
            admin_host=mssql_admin_host,
            port=mssql_port,
            container_host=mssql_container_host,
            sqlcmd_path=mssql_sqlcmd_path,
        )

    def create_app(self) -> Starlette:

        from .api import build_worker_app

        return build_worker_app(self)

    def start_job(self, request: WorkerJobRequest) -> WorkerJobRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, request.session_id)
        if request.repo_source:
            self._ensure_allowed_git_source(request.repo_source)
        self._sync_git_ssh_into_agent_home(
            paths, required=_repo_source_requires_git_ssh(request.repo_source)
        )
        self._clone_repo_if_requested(request, paths)
        job_id = request.job_id or uuid.uuid4().hex
        container_name = f"agent-worker-{job_id}"
        record = WorkerJobRecord(
            job_id=job_id,
            session_id=request.session_id,
            image=request.image,
            command=request.command,
            container_name=container_name,
            status="starting",
            started_at=now_utc(),
            agent_home_path=str(paths.agent_home),
            repo_root_path=str(paths.repo_root),
            artifacts_path=str(paths.artifacts),
            logs_path=str(paths.logs),
            metadata=sanitize_metadata(request.metadata),
        )
        self._log_path(record).touch(exist_ok=True)
        self.store.put(record)
        try:
            container_id = self.container_runtime.start_job(request, record, paths)
        except Exception as exc:
            record.status = "failed"
            record.finished_at = now_utc()
            record.error = sanitize_text(str(exc))
            self.store.put(record)
            logger.exception(
                "worker_job_start_failed job_id=%s session_id=%s", job_id, request.session_id
            )
            raise
        record.container_id = container_id
        record.status = "running"
        self.store.put(record)
        logger.info(
            "worker_job_started job_id=%s session_id=%s container_id=%s",
            record.job_id,
            record.session_id,
            record.container_id,
        )
        return record

    def inspect_job(self, record: WorkerJobRecord) -> WorkerJobRecord:
        try:
            info = self.container_runtime.inspect_job(record)
        except subprocess.CalledProcessError as exc:
            record.error = safe_subprocess_error(exc)
            self.store.put(record)
            return record
        status = str(info.get("status") or record.status)
        record.status = status
        if status in {"exited", "stopped", "failed", "removed"} and record.finished_at is None:
            record.finished_at = now_utc()
        self.store.put(record)
        return record

    def stop_job(self, record: WorkerJobRecord) -> WorkerJobRecord:
        self.container_runtime.stop_job(record)
        record.status = "stopped"
        record.finished_at = now_utc()
        self.store.put(record)
        logger.info("worker_job_stopped job_id=%s session_id=%s", record.job_id, record.session_id)
        return record

    def remove_job(self, record: WorkerJobRecord) -> None:
        if record.status == "removed":
            logger.info(
                "worker_job_remove_noop job_id=%s session_id=%s",
                record.job_id,
                record.session_id,
            )
            return
        attempted_at = now_utc()
        try:
            self.container_runtime.remove_job(record)
        except subprocess.CalledProcessError as exc:
            self._mark_job_cleanup_failure(
                record,
                error=safe_subprocess_error(exc),
                attempted_at=attempted_at,
            )
            raise
        self._mark_job_cleanup_success(record, attempted_at=attempted_at)
        logger.info("worker_job_removed job_id=%s session_id=%s", record.job_id, record.session_id)

    def get_logs(
        self,
        record: WorkerJobRecord,
        *,
        mode: str = "augmented",
        tail_lines: int | None = None,
    ) -> str:
        selected_mode = str(mode or "augmented").strip().casefold()
        if selected_mode not in {"augmented", "execution", "raw", "session"}:
            raise ValueError("log mode must be one of: augmented, execution, raw, session")
        text = self._raw_logs(record, tail_lines=tail_lines)
        if selected_mode == "raw":
            return text
        use_claude_first = _worker_job_uses_claude(record)
        if selected_mode == "execution":
            if use_claude_first:
                execution_log = self._claude_execution_log(record, text, skip_existing=True)
                if not execution_log:
                    execution_log = self._codex_execution_log(record, text, skip_existing=True)
            else:
                execution_log = self._codex_execution_log(record, text, skip_existing=True)
                if not execution_log:
                    execution_log = self._claude_execution_log(record, text, skip_existing=True)
            return self._append_codex_session_log(text, execution_log)
        if use_claude_first:
            session_log = self._claude_session_log(
                record,
                text,
                skip_existing=selected_mode == "augmented",
            )
            if not session_log:
                session_log = self._codex_session_log(
                    record,
                    text,
                    skip_existing=selected_mode == "augmented",
                )
        else:
            session_log = self._codex_session_log(
                record,
                text,
                skip_existing=selected_mode == "augmented",
            )
            if not session_log:
                session_log = self._claude_session_log(
                    record,
                    text,
                    skip_existing=selected_mode == "augmented",
                )
        if selected_mode == "session":
            return session_log
        return self._append_codex_session_log(text, session_log)

    def get_log_page(
        self,
        record: WorkerJobRecord,
        *,
        mode: str = "augmented",
        limit: int = 200,
        offset: int = 0,
        query: str | None = None,
    ) -> dict[str, object]:
        normalized_limit = max(1, min(int(limit), 5000))
        normalized_offset = max(0, int(offset))
        normalized_query = str(query or "").strip()
        tail_lines = (
            normalized_limit + normalized_offset
            if not normalized_query and normalized_limit > 0
            else None
        )
        text = self.get_logs(record, mode=mode, tail_lines=tail_lines)
        page = _log_page_from_end(
            text,
            limit=normalized_limit,
            offset=normalized_offset,
            query=normalized_query or None,
        )
        if tail_lines is not None and page["returned_lines"] >= normalized_limit:
            page["has_more_before"] = True
            page["total_lines"] = None
            page["start_line"] = None
            page["end_line"] = None
        return page

    def _raw_logs(self, record: WorkerJobRecord, *, tail_lines: int | None = None) -> str:
        log_path = self._log_path(record)
        try:
            try:
                text = self.container_runtime.get_logs(record, tail_lines=tail_lines)
            except TypeError:
                text = self.container_runtime.get_logs(record)
        except subprocess.CalledProcessError:
            text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
            reconstructed = self._reconstruct_codex_job_log(record, text)
            if reconstructed is not None:
                text = reconstructed
        if tail_lines is None:
            log_path.write_text(text, encoding="utf-8")
        elif text:
            _merge_tail_log_snapshot(log_path, text)
        return text

    def _codex_session_log(
        self,
        record: WorkerJobRecord,
        text: str,
        *,
        skip_existing: bool,
    ) -> str:
        agent_home = Path(record.agent_home_path)
        thread_ids = list(dict.fromkeys(_CODEX_SESSION_ID_LOG_PATTERN.findall(text)))
        if not thread_ids:
            thread_ids = _recent_codex_session_ids(agent_home)
        if not thread_ids:
            return ""
        messages: list[str] = []
        seen: set[str] = set()
        for thread_id in thread_ids:
            for message in self._codex_session_messages(
                agent_home,
                thread_id,
                include_running_context=record.status == "running",
            ):
                if message in seen or (skip_existing and message in text):
                    continue
                seen.add(message)
                messages.append(message)
        if not messages:
            return ""
        return "\n".join(messages) + "\n"

    def _codex_execution_log(
        self,
        record: WorkerJobRecord,
        text: str,
        *,
        skip_existing: bool,
    ) -> str:
        if record.started_at is None and record.finished_at is None:
            return ""
        agent_home = Path(record.agent_home_path)
        thread_ids = list(dict.fromkeys(_CODEX_SESSION_ID_LOG_PATTERN.findall(text)))
        if not thread_ids:
            thread_ids = _recent_codex_session_ids(agent_home)
        if not thread_ids:
            return ""
        messages: list[str] = []
        seen: set[str] = set()
        for thread_id in thread_ids:
            for message in self._codex_session_messages(
                agent_home,
                thread_id,
                started_at=record.started_at,
                finished_at=record.finished_at,
                include_running_context=record.status == "running",
            ):
                if message in seen or (skip_existing and message in text):
                    continue
                seen.add(message)
                messages.append(message)
        if not messages:
            return ""
        return "\n".join(messages) + "\n"

    def _claude_session_log(
        self,
        record: WorkerJobRecord,
        text: str,
        *,
        skip_existing: bool,
    ) -> str:
        agent_home = Path(record.agent_home_path)
        thread_ids = list(dict.fromkeys(_CODEX_SESSION_ID_LOG_PATTERN.findall(text)))
        if not thread_ids:
            thread_ids = _recent_claude_session_ids(agent_home)
        if not thread_ids:
            return ""
        messages: list[str] = []
        seen: set[str] = set()
        for thread_id in thread_ids:
            for message in self._claude_session_messages(agent_home, thread_id):
                if message in seen or (skip_existing and message in text):
                    continue
                seen.add(message)
                messages.append(message)
        if not messages:
            return ""
        return "\n".join(messages) + "\n"

    def _claude_execution_log(
        self,
        record: WorkerJobRecord,
        text: str,
        *,
        skip_existing: bool,
    ) -> str:
        if record.started_at is None and record.finished_at is None:
            return ""
        agent_home = Path(record.agent_home_path)
        thread_ids = list(dict.fromkeys(_CODEX_SESSION_ID_LOG_PATTERN.findall(text)))
        if not thread_ids:
            thread_ids = _recent_claude_session_ids(agent_home)
        if not thread_ids:
            return ""
        messages: list[str] = []
        seen: set[str] = set()
        for thread_id in thread_ids:
            for message in self._claude_session_messages(
                agent_home,
                thread_id,
                started_at=record.started_at,
                finished_at=record.finished_at,
            ):
                if message in seen or (skip_existing and message in text):
                    continue
                seen.add(message)
                messages.append(message)
        if not messages:
            return ""
        return "\n".join(messages) + "\n"

    @staticmethod
    def _append_codex_session_log(text: str, session_log: str) -> str:
        if not session_log:
            return text
        prefix = text
        if prefix and not prefix.endswith("\n"):
            prefix += "\n"
        return prefix + session_log

    def _reconstruct_codex_job_log(self, record: WorkerJobRecord, text: str) -> str | None:
        thread_ids = list(dict.fromkeys(_CODEX_SESSION_ID_LOG_PATTERN.findall(text)))
        if not thread_ids or record.started_at is None or record.finished_at is None:
            return None
        agent_home = Path(record.agent_home_path)
        lines: list[str] = []
        for thread_id in thread_ids:
            if not lines:
                lines.append(f"session id: {thread_id}")
            for message in self._codex_session_messages(
                agent_home,
                thread_id,
                started_at=record.started_at,
                finished_at=record.finished_at,
            ):
                lines.append(message)
        if not lines:
            return None
        return "\n".join(lines) + "\n"

    @staticmethod
    def _codex_session_messages(
        agent_home: Path,
        thread_id: str,
        *,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        include_running_context: bool = False,
    ) -> list[str]:
        messages: list[str] = []
        seen: set[str] = set()
        active_tool_calls: dict[str, str] = {}
        latest_event_timestamp: str | None = None
        for path in _codex_session_jsonl_paths(agent_home, thread_id):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for raw_line in lines:
                try:
                    event = json.loads(raw_line)
                except ValueError:
                    continue
                if not _codex_event_is_in_window(
                    event, started_at=started_at, finished_at=finished_at
                ):
                    continue
                latest_event_timestamp = _latest_codex_timestamp(
                    latest_event_timestamp,
                    event.get("timestamp"),
                )
                for message in _codex_messages_from_session_event(event):
                    if not message:
                        continue
                    if _is_session_message_artifact(message):
                        continue
                    if message in seen:
                        continue
                    seen.add(message)
                    messages.append(message)
                if include_running_context:
                    _update_codex_active_tool_calls(event, active_tool_calls)
        if include_running_context:
            for message in _codex_running_context_messages(
                latest_event_timestamp=latest_event_timestamp,
                active_tool_calls=active_tool_calls,
            ):
                if not message or message in seen:
                    continue
                seen.add(message)
                messages.append(message)
        return messages

    @staticmethod
    def _claude_session_messages(
        agent_home: Path,
        thread_id: str,
        *,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
    ) -> list[str]:
        messages: list[str] = []
        seen: set[str] = set()
        for path in _claude_session_jsonl_paths(agent_home, thread_id):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for raw_line in lines:
                try:
                    event = json.loads(raw_line)
                except ValueError:
                    continue
                if not _codex_event_is_in_window(
                    event, started_at=started_at, finished_at=finished_at
                ):
                    continue
                for message in _claude_messages_from_session_event(event):
                    if (
                        not message
                        or _is_session_message_artifact(message)
                        or message in seen
                    ):
                        continue
                    seen.add(message)
                    messages.append(message)
        return messages

    def prepare_session(self, request: WorkerSessionPrepareRequest) -> WorkerSessionRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, request.session_id)
        self._sync_git_ssh_into_agent_home(paths, required=False)
        metadata = sanitize_metadata(request.metadata)
        if metadata:
            current = self._read_session_metadata(paths)
            current["metadata"] = {**dict(current.get("metadata") or {}), **metadata}
            self._write_session_metadata(paths, current)
        return self._session_record(paths, metadata=metadata)

    def clone_repo(
        self,
        session_id: str,
        request: WorkerRepoCloneRequest,
    ) -> WorkerRepoCloneRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, session_id)
        self._ensure_allowed_git_source(request.repo_source)
        self._sync_git_ssh_into_agent_home(
            paths, required=_repo_source_requires_git_ssh(request.repo_source)
        )
        repo_name = request.repo_name or self._repo_name(request.repo_source)
        _target_dir, cloned = self._clone_repo_into_repo_root(
            paths=paths,
            repo_source=request.repo_source,
            clone_branch=request.clone_branch,
            repo_name=repo_name,
        )
        return WorkerRepoCloneRecord(
            session_id=session_id,
            target_dir=f"/home/agent/repos/{repo_name}",
            guest_repo_name=repo_name,
            remote_url=request.repo_source,
            cloned=cloned,
        )

    def write_files(
        self,
        session_id: str,
        request: WorkerFileWriteRequest,
    ) -> WorkerFileWriteRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, session_id)
        copied_paths: list[str] = []
        for entry in request.files:
            root, relative_path, container_path = self._resolve_container_destination(
                paths=paths,
                destination_path=entry.destination_path,
            )
            destination = root / relative_path
            self._ensure_resolved_destination_stays_under_root(
                root=root,
                destination=destination,
                container_path=container_path,
            )
            if destination.exists() and not entry.overwrite:
                raise ValueError(f"destination already exists: {container_path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._ensure_resolved_destination_stays_under_root(
                root=root,
                destination=destination,
                container_path=container_path,
            )
            if entry.content_base64 is not None:
                desired_content = base64.b64decode(entry.content_base64)
            else:
                content_text = entry.content or ""
                if entry.merge_strategy == "managed_section":
                    existing_text = (
                        destination.read_text(encoding="utf-8") if destination.is_file() else ""
                    )
                    content_text = merge_managed_section(
                        existing_text,
                        section_id=str(entry.section_id or ""),
                        content=content_text,
                    )
                desired_content = content_text.encode("utf-8")
            desired_mode = (
                int(self._normalize_mode(entry.mode), 8) if entry.mode is not None else None
            )
            if destination.is_file() and entry.overwrite:
                content_matches = destination.read_bytes() == desired_content
                mode_matches = (
                    desired_mode is None
                    or stat.S_IMODE(destination.stat().st_mode) == desired_mode
                )
                if content_matches and mode_matches:
                    continue
                if content_matches and desired_mode is not None:
                    destination.chmod(desired_mode)
                    copied_paths.append(container_path)
                    continue
            if entry.content_base64 is not None:
                destination.write_bytes(desired_content)
            else:
                destination.write_text(desired_content.decode("utf-8"), encoding="utf-8")
            if desired_mode is not None:
                destination.chmod(desired_mode)
            copied_paths.append(container_path)
        return WorkerFileWriteRecord(
            session_id=session_id,
            copied_count=len(copied_paths),
            copied_paths=copied_paths,
            roots={"agent_home": "/home/agent", "repo_root": "/home/agent/repos"},
        )

    def read_session_artifact_text(self, session_id: str, relative_path: str) -> str:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        if not SAFE_ID_PATTERN.fullmatch(normalized_session_id):
            raise ValueError(
                "session_id must contain only letters, numbers, dots, underscores, or dashes"
            )
        root = self.worker_root / "sessions" / normalized_session_id / "artifacts"
        artifact_path = resolve_artifact_file(root, relative_path)
        try:
            return artifact_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("artifact file is not valid UTF-8 text") from exc

    def provision_mssql_database(
        self,
        session_id: str,
        request: WorkerMssqlDatabaseRequest,
    ) -> WorkerMssqlDatabaseRecord:
        return self.mssql.provision_database(session_id, request)

    def list_mssql_databases(self, session_id: str) -> WorkerMssqlDatabaseListRecord:
        return self.mssql.list_databases(session_id)

    def drop_mssql_database(
        self,
        session_id: str,
        logical_name: str,
    ) -> WorkerMssqlDatabaseDeleteRecord:
        return self.mssql.drop_database(session_id, logical_name)

    def drop_mssql_session(self, session_id: str) -> WorkerMssqlSessionDeleteRecord:
        return self.mssql.drop_session(session_id)

    def delete_session(self, session_id: str) -> bool:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        if not SAFE_ID_PATTERN.fullmatch(normalized_session_id):
            raise ValueError(
                "session_id must contain only letters, numbers, dots, underscores, or dashes"
            )

        mssql_cleanup = self.drop_mssql_session(normalized_session_id)
        removed_any = False
        session_records = self.store.list_by_session(normalized_session_id)
        cleanup_started_at = now_utc()
        for record in session_records:
            if record.status == "removed":
                self._mark_job_cleanup_success(
                    record,
                    attempted_at=cleanup_started_at,
                    completed_at=cleanup_started_at,
                )
                removed_any = True
                continue
            try:
                self.remove_job(record)
            except subprocess.CalledProcessError as exc:
                error = safe_subprocess_error(exc)
                self._mark_job_cleanup_failure(
                    record,
                    error=error,
                    attempted_at=cleanup_started_at,
                    completed_at=now_utc(),
                )
                raise RuntimeError(
                    f"worker session cleanup failed for {normalized_session_id}: {error}"
                ) from exc
            removed_any = True

        session_root = self.worker_root / "sessions" / normalized_session_id
        try:
            if session_root.is_symlink() or session_root.is_file():
                session_root.unlink()
                removed_any = True
            elif session_root.exists():
                shutil.rmtree(session_root)
                removed_any = True
        except OSError as exc:
            error = sanitize_text(str(exc))
            self._mark_session_cleanup_failure(
                session_records,
                error=error,
                attempted_at=cleanup_started_at,
                completed_at=now_utc(),
            )
            raise RuntimeError(
                f"worker session cleanup failed for {normalized_session_id}: {error}"
            ) from exc
        logger.info(
            "worker_session_deleted session_id=%s removed=%s mssql_databases=%s",
            normalized_session_id,
            removed_any,
            len(mssql_cleanup.deleted_databases),
        )
        return removed_any

    def read_telemetry(self) -> WorkerTelemetryPayload:
        worker_root_disk = collect_disk_usage(self.worker_root, scope="worker_root")
        sessions_root = self.worker_root / "sessions"
        session_storage_total = collect_recursive_directory_size(
            sessions_root,
            scope="worker_sessions",
        )
        session_samples = self._session_storage_samples(sessions_root)
        return WorkerTelemetryPayload(
            disk=WorkerDiskTelemetry(worker_root=worker_root_disk),
            session_storage=WorkerSessionStorageTelemetry(
                total=session_storage_total,
                session_count=len(session_samples),
                largest_sessions=top_session_storage(session_samples, limit=5, order="largest"),
                oldest_sessions=top_session_storage(session_samples, limit=5, order="oldest"),
            ),
            jobs=self._job_telemetry(),
        )

    def _log_path(self, record: WorkerJobRecord) -> Path:
        return Path(record.logs_path) / f"{record.job_id}.log"

    def _session_storage_samples(self, sessions_root: Path) -> list[SessionStorageSample]:
        try:
            entries = list(os.scandir(sessions_root))
        except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
            return []

        samples: list[SessionStorageSample] = []
        for entry in entries:
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            session_id = str(entry.name or "").strip()
            if not session_id or not SAFE_ID_PATTERN.fullmatch(session_id):
                continue
            sample = self._session_storage_sample(Path(entry.path), session_id=session_id)
            if sample is not None:
                samples.append(sample)
        return samples

    def _session_storage_sample(
        self,
        session_root: Path,
        *,
        session_id: str,
    ) -> SessionStorageSample | None:
        telemetry = collect_recursive_directory_size(
            session_root,
            scope=f"session_storage:{session_id}",
        )
        if telemetry.status is not TelemetryStatus.AVAILABLE:
            return None
        created_at, updated_at = self._session_storage_timestamps(session_root)
        return SessionStorageSample(
            session_id=session_id,
            directory_path=f"sessions/{session_id}",
            backend="container_worker_vm",
            size_bytes=telemetry.size_bytes,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _session_storage_timestamps(
        self,
        session_root: Path,
    ) -> tuple[datetime | None, datetime | None]:
        metadata_path = session_root / "metadata.json"
        created_at = self._read_datetime_value(self._read_json_object(metadata_path), "created_at")
        try:
            root_stat = session_root.stat()
        except OSError:
            return created_at, None
        updated_at = datetime.fromtimestamp(root_stat.st_mtime, tz=UTC)
        if created_at is None:
            created_at = updated_at
        try:
            metadata_stat = metadata_path.stat()
        except OSError:
            return created_at, updated_at
        metadata_updated_at = datetime.fromtimestamp(metadata_stat.st_mtime, tz=UTC)
        if metadata_updated_at > updated_at:
            updated_at = metadata_updated_at
        return created_at, updated_at

    def _read_json_object(self, path: Path) -> dict[str, object]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _read_datetime_value(payload: dict[str, object], key: str) -> datetime | None:
        value = payload.get(key)
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _job_telemetry(self) -> WorkerJobTelemetry:
        try:
            records = self.store.list_all_ordered()
        except FileNotFoundError:
            return self._job_telemetry_failure(
                TelemetryStatus.MISSING,
                "job store is missing",
            )
        except PermissionError:
            return self._job_telemetry_failure(
                TelemetryStatus.PERMISSION_DENIED,
                "permission denied",
            )
        except (sqlite3.Error, OSError, ValueError):
            return self._job_telemetry_failure(
                TelemetryStatus.UNSUPPORTED,
                "job store is unavailable",
            )

        counts_by_status: dict[str, int] = {}
        running_job_count = 0
        failed_start_count = 0
        cleanup_failure_count = 0
        for record in records:
            counts_by_status[record.status] = counts_by_status.get(record.status, 0) + 1
            if record.status == "running":
                running_job_count += 1
            if record.status == "failed":
                failed_start_count += 1
            if record.status == "cleanup_failed" or record.cleanup_status == "cleanup_failed":
                cleanup_failure_count += 1

        if cleanup_failure_count:
            cleanup_failure_message = (
                f"{cleanup_failure_count} cleanup failure"
                f"{'s' if cleanup_failure_count != 1 else ''} recorded"
            )
            cleanup_failure_status = TelemetryStatus.AVAILABLE
            telemetry_health = HealthState.WARNING
            telemetry_error_count = cleanup_failure_count
            telemetry_errors = [
                TelemetryError(status=TelemetryStatus.AVAILABLE, message=cleanup_failure_message)
            ]
        else:
            cleanup_failure_message = "cleanup failures are not currently persisted"
            cleanup_failure_status = TelemetryStatus.UNSUPPORTED
            telemetry_health = HealthState.OK
            telemetry_error_count = 0
            telemetry_errors = []

        return WorkerJobTelemetry(
            health=telemetry_health,
            total_jobs=len(records),
            counts_by_status=counts_by_status,
            running_job_count=running_job_count,
            failed_start_count=failed_start_count,
            cleanup_failure_status=cleanup_failure_status,
            cleanup_failure_message=cleanup_failure_message,
            cleanup_failure_count=cleanup_failure_count,
            error_count=telemetry_error_count,
            errors=telemetry_errors,
        )

    @staticmethod
    def _job_telemetry_failure(
        status: TelemetryStatus,
        message: str,
    ) -> WorkerJobTelemetry:
        return WorkerJobTelemetry(
            status=status,
            health=HealthState.UNKNOWN,
            error_count=1,
            errors=[TelemetryError(status=status, message=message)],
        )

    def _mark_job_cleanup_success(
        self,
        record: WorkerJobRecord,
        *,
        attempted_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> WorkerJobRecord:
        completed = completed_at or now_utc()
        record.status = "removed"
        record.finished_at = record.finished_at or completed
        record.cleanup_status = "cleanup_succeeded"
        record.cleanup_attempted_at = attempted_at or record.cleanup_attempted_at or completed
        record.cleanup_completed_at = completed
        record.cleanup_error = None
        self.store.put(record)
        return record

    def _mark_job_cleanup_failure(
        self,
        record: WorkerJobRecord,
        *,
        error: str,
        attempted_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> WorkerJobRecord:
        completed = completed_at or now_utc()
        record.status = "cleanup_failed"
        record.finished_at = completed
        record.cleanup_status = "cleanup_failed"
        record.cleanup_attempted_at = attempted_at or record.cleanup_attempted_at or completed
        record.cleanup_completed_at = completed
        record.cleanup_error = sanitize_text(error)
        self.store.put(record)
        return record

    def _mark_session_cleanup_failure(
        self,
        records: list[WorkerJobRecord],
        *,
        error: str,
        attempted_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        completed = completed_at or now_utc()
        sanitized_error = sanitize_text(error)
        for record in records:
            record.cleanup_status = "cleanup_failed"
            record.cleanup_attempted_at = attempted_at or record.cleanup_attempted_at or completed
            record.cleanup_completed_at = completed
            record.cleanup_error = sanitized_error
            if record.status != "removed":
                record.status = "cleanup_failed"
                record.finished_at = completed
            self.store.put(record)

    def _clone_repo_if_requested(
        self,
        request: WorkerJobRequest,
        paths: WorkerSessionPaths,
    ) -> None:
        if not request.repo_source:
            return
        self._clone_repo_into_repo_root(
            paths=paths,
            repo_source=request.repo_source,
            clone_branch=request.clone_branch,
            repo_name=self._repo_name(request.repo_source),
        )

    def _clone_repo_into_repo_root(
        self,
        *,
        paths: WorkerSessionPaths,
        repo_source: str,
        clone_branch: str | None,
        repo_name: str,
    ) -> tuple[Path, bool]:
        self._ensure_allowed_git_source(repo_source)
        target_dir = paths.repo_root / repo_name
        if target_dir.is_symlink():
            raise ValueError(
                f"repo target already exists and is not a directory: "
                f"/home/agent/repos/{repo_name}"
            )
        if target_dir.exists() and not target_dir.is_dir():
            raise ValueError(
                f"repo target already exists and is not a directory: "
                f"/home/agent/repos/{repo_name}"
            )
        if target_dir.exists() and any(target_dir.iterdir()):
            if self._is_git_checkout_for_source(target_dir, repo_source):
                return target_dir, False
            raise ValueError(
                f"repo target already exists and is not a checkout for requested source: "
                f"/home/agent/repos/{repo_name}"
            )
        target_dir.mkdir(parents=True, exist_ok=True)
        args = ["git", "clone"]
        if clone_branch:
            args.extend(["--branch", clone_branch])
        args.extend([repo_source, str(target_dir)])
        subprocess.run(args, cwd=paths.repo_root, check=True, capture_output=True, text=True)
        return target_dir, True

    def _ensure_allowed_git_source(self, repo_source: str) -> None:
        if not self.allowed_git_source_prefixes:
            raise ValueError(
                "worker Git source prefixes are not configured; refusing repo_source"
            )
        if not any(repo_source.startswith(prefix) for prefix in self.allowed_git_source_prefixes):
            allowed = ", ".join(self.allowed_git_source_prefixes)
            raise ValueError(
                f"repo_source is not allowed for this worker: {repo_source!r}; "
                f"allowed prefixes: {allowed}"
            )

    def _sync_git_ssh_into_agent_home(
        self,
        paths: WorkerSessionPaths,
        *,
        required: bool,
    ) -> None:
        if self.git_ssh_source_dir is None:
            if required:
                raise ValueError("worker Git SSH source directory is not configured")
            return
        source_dir = self.git_ssh_source_dir
        required_files = ("agent-sandbox-git", "config")
        missing = [name for name in required_files if not (source_dir / name).is_file()]
        if missing:
            if required:
                raise ValueError(
                    "worker Git SSH source directory is missing required files: "
                    + ", ".join(missing)
                )
            return
        target_dir = paths.agent_home / ".ssh"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_dir.chmod(0o700)
        for name in ("agent-sandbox-git", "agent-sandbox-git.pub", "config", "known_hosts"):
            source = source_dir / name
            if not source.is_file():
                continue
            destination = target_dir / name
            shutil.copy2(source, destination)
            if name.endswith(".pub"):
                destination.chmod(0o644)
            else:
                destination.chmod(0o600)

    def _session_record(
        self,
        paths: WorkerSessionPaths,
        *,
        metadata: dict[str, object] | None = None,
    ) -> WorkerSessionRecord:
        return WorkerSessionRecord(
            session_id=paths.session_id,
            root_path=str(paths.root),
            agent_home_path=str(paths.agent_home),
            repo_root_path=str(paths.repo_root),
            artifacts_path=str(paths.artifacts),
            logs_path=str(paths.logs),
            metadata_path=str(paths.metadata_path),
            metadata=dict(metadata or {}),
        )

    def _read_session_metadata(self, paths: WorkerSessionPaths) -> dict[str, object]:
        try:
            import json

            payload = json.loads(paths.metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_session_metadata(
        self,
        paths: WorkerSessionPaths,
        payload: dict[str, object],
    ) -> None:
        import json

        paths.metadata_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _resolve_container_destination(
        self,
        *,
        paths: WorkerSessionPaths,
        destination_path: str,
    ) -> tuple[Path, Path, str]:
        normalized = destination_path.strip()
        for container_root, host_root in (
            ("/home/agent", paths.agent_home),
        ):
            if normalized == container_root:
                raise ValueError(f"destination_path must name a file below {container_root}")
            prefix = f"{container_root}/"
            if not normalized.startswith(prefix):
                continue
            relative_text = normalized[len(prefix) :]
            relative = Path(relative_text)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"destination_path must stay below {container_root}")
            return host_root, relative, f"{container_root}/{relative.as_posix()}"
        raise ValueError("destination_path must be under /home/agent")

    @staticmethod
    def _ensure_resolved_destination_stays_under_root(
        *,
        root: Path,
        destination: Path,
        container_path: str,
    ) -> None:
        resolved_root = root.resolve()
        resolved_destination = destination.resolve(strict=False)
        try:
            resolved_destination.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("destination_path must stay below /home/agent") from exc

    @staticmethod
    def _normalize_mode(mode: str) -> str:
        normalized = mode.strip()
        if not normalized or any(char not in "01234567" for char in normalized):
            raise ValueError(f"mode must be octal digits: {mode!r}")
        return normalized

    @staticmethod
    def _repo_name(repo_source: str) -> str:
        parsed = urlparse(repo_source)
        raw_path = parsed.path if parsed.scheme else repo_source
        candidate = unquote(raw_path.rstrip("/").rsplit("/", 1)[-1]).strip() or "repo"
        if candidate.endswith(".git"):
            candidate = candidate[:-4]
        candidate = re.sub(r"[^A-Za-z0-9_.-]+", "-", candidate).strip(".-")
        if not candidate or candidate in {".", ".."}:
            return "repo"
        return candidate

    @staticmethod
    def _is_git_checkout_for_source(target_dir: Path, repo_source: str) -> bool:
        if not (target_dir / ".git").exists():
            return False
        try:
            completed = subprocess.run(
                ["git", "-C", str(target_dir), "remote", "get-url", "origin"],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError:
            return False
        return completed.stdout.strip() == repo_source


def _repo_source_requires_git_ssh(repo_source: str | None) -> bool:
    if not repo_source:
        return False
    normalized = repo_source.strip()
    return normalized.startswith("ssh://") or bool(
        re.match(r"^[^/@:\s]+@[^:\s]+:.+", normalized)
    )


def _worker_job_uses_claude(record: WorkerJobRecord) -> bool:
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    for key in ("agent_provider", "provider", "model_provider"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip().casefold() in {"claude", "anthropic"}:
            return True
    image = str(getattr(record, "image", "") or "").casefold()
    if "claude" in image or "anthropic" in image:
        return True
    command = getattr(record, "command", None)
    command_text = (
        " ".join(command)
        if isinstance(command, list) and all(isinstance(item, str) for item in command)
        else str(command or "")
    ).casefold()
    return "agent_sandbox_claude_json_to_log" in command_text or re.search(
        r"(^|\s)claude(\s|$)",
        command_text,
    ) is not None


def _codex_session_jsonl_paths(agent_home: Path, thread_id: str) -> list[Path]:
    roots = [
        agent_home / ".config" / "agent-sandbox" / "codex" / "sessions",
        agent_home / ".codex" / "sessions",
    ]
    paths: dict[Path, None] = {}
    for root in roots:
        if not root.is_dir():
            continue
        try:
            matches = root.rglob(f"*{thread_id}*.jsonl")
            for path in matches:
                if path.is_file():
                    paths[path] = None
        except OSError:
            continue
    return sorted(paths, key=lambda path: (path.stat().st_mtime, str(path)))


def _recent_codex_session_ids(agent_home: Path, *, limit: int = 5) -> list[str]:
    roots = [
        agent_home / ".config" / "agent-sandbox" / "codex" / "sessions",
        agent_home / ".codex" / "sessions",
    ]
    paths: dict[Path, None] = {}
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for path in root.rglob("*.jsonl"):
                if path.is_file():
                    paths[path] = None
        except OSError:
            continue
    session_ids: list[str] = []
    for path in sorted(paths, key=lambda item: (item.stat().st_mtime, str(item)), reverse=True):
        session_id = _session_id_from_codex_jsonl(path)
        if not session_id or session_id in session_ids:
            continue
        session_ids.append(session_id)
        if len(session_ids) >= limit:
            break
    return session_ids


def _claude_session_jsonl_paths(agent_home: Path, thread_id: str) -> list[Path]:
    roots = [
        agent_home / ".claude" / "projects",
        agent_home / ".claude" / "sessions",
    ]
    paths: dict[Path, None] = {}
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for pattern in (f"{thread_id}.jsonl", f"*{thread_id}*.jsonl"):
                for path in root.rglob(pattern):
                    if path.is_file():
                        paths[path] = None
        except OSError:
            continue
    return sorted(paths, key=lambda path: (path.stat().st_mtime, str(path)))


def _recent_claude_session_ids(agent_home: Path, *, limit: int = 5) -> list[str]:
    roots = [
        agent_home / ".claude" / "projects",
        agent_home / ".claude" / "sessions",
    ]
    paths: dict[Path, None] = {}
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for path in root.rglob("*.jsonl"):
                if path.is_file():
                    paths[path] = None
        except OSError:
            continue
    session_ids: list[str] = []
    for path in sorted(paths, key=lambda item: (item.stat().st_mtime, str(item)), reverse=True):
        session_id = _session_id_from_claude_jsonl(path)
        if not session_id or session_id in session_ids:
            continue
        session_ids.append(session_id)
        if len(session_ids) >= limit:
            break
    return session_ids


def _session_id_from_claude_jsonl(path: Path) -> str | None:
    match = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        path.name,
    )
    if match is not None:
        return match.group(0)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for _index, raw_line in zip(range(20), handle):
                try:
                    event = json.loads(raw_line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                session_id = _codex_first_text(
                    event.get("sessionId"),
                    event.get("session_id"),
                    event.get("conversation_id"),
                    event.get("conversationId"),
                )
                if session_id:
                    return session_id.strip()
    except OSError:
        return None
    return None


def _session_id_from_codex_jsonl(path: Path) -> str | None:
    match = re.search(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
        path.name,
    )
    if match is not None:
        return match.group(0)
    try:
        with path.open("r", encoding="utf-8") as handle:
            for _index, raw_line in zip(range(20), handle):
                try:
                    event = json.loads(raw_line)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                if str(event.get("type") or "") != "session_meta":
                    continue
                payload = event.get("payload")
                if not isinstance(payload, dict):
                    continue
                session_id = _codex_first_text(payload.get("id"))
                if session_id:
                    return session_id.strip()
    except OSError:
        return None
    return None


def _merge_tail_log_snapshot(log_path: Path, tail_text: str) -> None:
    if not tail_text:
        return
    if not log_path.exists():
        log_path.write_text(tail_text, encoding="utf-8")
        return
    try:
        existing = log_path.read_text(encoding="utf-8")
    except OSError:
        existing = ""
    if not existing or tail_text.startswith(existing):
        log_path.write_text(tail_text, encoding="utf-8")
        return
    overlap = min(len(existing), len(tail_text))
    for size in range(overlap, 0, -1):
        if existing.endswith(tail_text[:size]):
            log_path.write_text(existing + tail_text[size:], encoding="utf-8")
            return
    log_path.write_text(existing.rstrip("\n") + "\n" + tail_text, encoding="utf-8")


def _log_page_from_end(
    text: str,
    *,
    limit: int,
    offset: int,
    query: str | None = None,
) -> dict[str, object]:
    lines = text.splitlines()
    normalized_query = str(query or "").strip()
    if normalized_query:
        needle = normalized_query.casefold()
        lines = [line for line in lines if needle in line.casefold()]
    total_lines = len(lines)
    end_index = max(0, total_lines - max(0, offset))
    start_index = max(0, end_index - max(1, limit))
    page_lines = lines[start_index:end_index]
    return {
        "text": "\n".join(page_lines) + ("\n" if page_lines else ""),
        "limit": max(1, limit),
        "offset": max(0, offset),
        "returned_lines": len(page_lines),
        "total_lines": total_lines,
        "start_line": start_index + 1 if page_lines else 0,
        "end_line": end_index if page_lines else 0,
        "has_more_before": start_index > 0,
        "query": normalized_query or None,
    }


def _codex_event_is_in_window(
    event: object,
    *,
    started_at: datetime | None,
    finished_at: datetime | None,
) -> bool:
    if started_at is None and finished_at is None:
        return True
    if not isinstance(event, dict):
        return False
    timestamp = _parse_codex_timestamp(event.get("timestamp"))
    if timestamp is None:
        return False
    if started_at is not None and timestamp < _normalize_datetime(started_at):
        return False
    if finished_at is not None and timestamp > _normalize_datetime(finished_at):
        return False
    return True


def _parse_codex_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return _normalize_datetime(datetime.fromisoformat(text))
    except ValueError:
        return None


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _latest_codex_timestamp(current: str | None, candidate: object) -> str | None:
    parsed_candidate = _parse_codex_timestamp(candidate)
    if parsed_candidate is None:
        return current
    if current is None:
        return (
            str(candidate).strip()
            if isinstance(candidate, str)
            else parsed_candidate.isoformat()
        )
    parsed_current = _parse_codex_timestamp(current)
    if parsed_current is None or parsed_candidate > parsed_current:
        return (
            str(candidate).strip()
            if isinstance(candidate, str)
            else parsed_candidate.isoformat()
        )
    return current


def _codex_messages_from_session_event(event: object) -> list[str]:
    if not isinstance(event, dict):
        return []
    event_type = str(event.get("type") or "")
    payload = event.get("payload")
    if event_type == "event_msg" and isinstance(payload, dict):
        payload_type = str(payload.get("type") or "")
        if payload_type == "agent_message":
            return [_codex_first_text(payload.get("message"))]
        if payload_type == "task_complete":
            return [_codex_first_text(payload.get("last_agent_message"))]
        return []
    if event_type == "response_item" and isinstance(payload, dict):
        if str(payload.get("type") or "") != "message":
            return []
        if str(payload.get("role") or "") != "assistant":
            return []
        return [
            _codex_first_text(
                payload.get("text"),
                payload.get("message"),
                _codex_content_text(payload.get("content")),
                _codex_content_text(payload.get("formatted")),
            )
        ]
    return []


def _is_session_message_artifact(message: str) -> bool:
    normalized = " ".join(message.strip().split()).casefold()
    return normalized in _SESSION_MESSAGE_ARTIFACTS


def _claude_messages_from_session_event(event: object) -> list[str]:
    if not isinstance(event, dict):
        return []
    event_type = str(event.get("type") or "")
    if event_type not in {"assistant", "assistant_message", "message"}:
        return []
    message = event.get("message")
    if isinstance(message, dict):
        if str(message.get("role") or "assistant") != "assistant":
            return []
        text = _claude_content_text(message.get("content"))
    else:
        text = _claude_content_text(event.get("content"))
    return [text] if text else []


def _update_codex_active_tool_calls(event: object, active_tool_calls: dict[str, str]) -> None:
    if not isinstance(event, dict):
        return
    event_type = str(event.get("type") or "")
    payload = event.get("payload")
    if event_type == "response_item" and isinstance(payload, dict):
        payload_type = str(payload.get("type") or "")
        call_id = _codex_first_text(payload.get("call_id"), payload.get("id"))
        if payload_type == "function_call":
            if call_id:
                active_tool_calls[call_id] = _codex_function_call_summary(payload)
            return
        if payload_type in {"function_call_output", "tool_call_output"} and call_id:
            active_tool_calls.pop(call_id, None)
            return
    if event_type == "event_msg" and isinstance(payload, dict):
        payload_type = str(payload.get("type") or "")
        call_id = _codex_first_text(payload.get("call_id"), payload.get("id"))
        if call_id and payload_type.endswith("_end"):
            active_tool_calls.pop(call_id, None)


def _codex_running_context_messages(
    *,
    latest_event_timestamp: str | None,
    active_tool_calls: dict[str, str],
) -> list[str]:
    messages: list[str] = []
    if not active_tool_calls:
        return messages
    if latest_event_timestamp:
        messages.append(f"latest codex event: {latest_event_timestamp}")
    for summary in active_tool_calls.values():
        if summary:
            messages.append(f"active tool call: {summary}")
    return messages


def _codex_function_call_summary(payload: dict[str, object]) -> str:
    name = _codex_first_text(
        payload.get("name"),
        payload.get("tool_name"),
        payload.get("function_name"),
    ) or "tool"
    arguments = _json_object_from_string(payload.get("arguments"))
    command = _codex_command_from_arguments(arguments)
    cwd = _codex_first_text(
        arguments.get("workdir"),
        arguments.get("cwd"),
        payload.get("cwd"),
    )
    parts = [name]
    if command:
        parts.append(_truncate_log_fragment(command, limit=_CODEX_TOOL_SUMMARY_LIMIT))
    if cwd:
        parts.append(f"cwd={cwd}")
    return " ".join(parts)


def _json_object_from_string(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {"arguments": value}
    return parsed if isinstance(parsed, dict) else {}


def _codex_command_from_arguments(arguments: dict[str, object]) -> str:
    command = arguments.get("cmd") or arguments.get("command")
    if isinstance(command, list) and all(isinstance(item, str) for item in command):
        return shlex.join(command)
    if isinstance(command, str) and command.strip():
        return command.strip()
    raw_arguments = arguments.get("arguments")
    return raw_arguments.strip() if isinstance(raw_arguments, str) else ""


def _truncate_log_fragment(value: str, *, limit: int) -> str:
    text = " ".join(value.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _codex_content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, str):
                if part:
                    parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type and part_type not in {"text", "output_text", "assistant_message"}:
                continue
            text = _codex_first_text(part.get("text"), part.get("content"), part.get("message"))
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        return _codex_first_text(value.get("text"), value.get("content"), value.get("message"))
    return ""


def _claude_content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for part in value:
            if isinstance(part, str):
                if part:
                    parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type and part_type not in {"text", "output_text", "assistant_message"}:
                continue
            text = _codex_first_text(part.get("text"), part.get("content"), part.get("message"))
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        return _codex_first_text(value.get("text"), value.get("content"), value.get("message"))
    return ""


def _codex_first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""
