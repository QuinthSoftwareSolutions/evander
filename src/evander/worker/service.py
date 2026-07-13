from __future__ import annotations

import base64
import errno
import json
import logging
import os
import re
import shlex
import shutil
import sqlite3
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from starlette.applications import Starlette

from evander.capacity_health import (
    DirectorySizeTelemetry,
    HealthState,
    SessionStorageSample,
    TelemetryError,
    TelemetryStatus,
    collect_disk_usage,
    top_session_storage,
)
from evander.managed_sections import merge_managed_section
from evander.session_artifacts import (
    open_relative_regular_file_no_follow,
    read_artifact_text_no_follow,
)
from evander.git_ssh_authorized_keys import public_key_body_from_line

from .docker_runtime import DockerContainerRuntime
from .models import (
    DEFAULT_WORKER_ROOT,
    WorkerFileWriteRequest,
    WorkerFileWriteRecord,
    WorkerJobRecord,
    WorkerJobRequest,
    WorkerGitWriteKeyRecord,
    WorkerGitWriteKeyRequest,
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
    WorkerTaskWorktreeRecord,
    WorkerTaskWorktreeRequest,
    WorkerTelemetryPayload,
    ensure_resolved_path_within,
    now_utc,
    validate_worker_component_id,
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
_CODEX_OUTCOME_METADATA_KEY = "codex_outcome"
_CODEX_OUTCOME_STATUS_FAILED = "failed"
_CODEX_OUTCOME_REASON_CONTEXT_LIMIT_SUSPECTED = "codex_context_limit_suspected"
_CODEX_OUTCOME_REASON_NO_FINAL_MESSAGE = "codex_no_final_message"
_CODEX_OUTCOME_REASON_TURN_FAILED = "codex_turn_failed"
_CODEX_OUTCOME_REASON_UNSUPPORTED_TOOL_CALL = "codex_unsupported_tool_call"
_CODEX_LOCAL_MODEL_PROVIDER_MODES = {"local_openai"}
_CODEX_CONTEXT_LIMIT_WARNING_TOTAL_TOKENS = 60_000
_CODEX_UNSUPPORTED_TOOL_CALL_PATTERN = re.compile(
    r"codex_core::tools::router:\s+error=unsupported call:\s*(?P<tool>[^\r\n]+)",
    re.IGNORECASE,
)
_SESSION_STORAGE_TELEMETRY_TTL_SECONDS = 60.0
_SESSION_STORAGE_WARMING_MESSAGE = (
    "worker session storage scan is warming; use refresh=1 to wait for a full scan"
)
_DESTINATION_NOFOLLOW_ERROR = (
    "destination_path must stay below /home/agent and must not contain symlinks "
    "or non-directory segments"
)


@dataclass(frozen=True)
class _SessionStorageTelemetryCacheEntry:
    telemetry: WorkerSessionStorageTelemetry
    collected_at_monotonic: float
    refresh_generation: int


@dataclass(frozen=True)
class _ContainerFileSnapshot:
    content: bytes
    mode: int


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
        mssql_admin_password_env: str = "EVANDER_WORKER_MSSQL_ADMIN_PASSWORD",
        mssql_admin_host: str = "127.0.0.1",
        mssql_port: int = 1433,
        mssql_container_host: str = "172.17.0.1",
        mssql_sqlcmd_path: str = "sqlcmd",
        mssql_provisioner: WorkerMssqlProvisioner | None = None,
        storage_telemetry_ttl_seconds: float = _SESSION_STORAGE_TELEMETRY_TTL_SECONDS,
    ) -> None:
        self.runtime_dir = runtime_dir
        self.worker_root = worker_root
        self.store = WorkerJobStore(runtime_dir / "worker-jobs.json")
        self.allowed_git_source_prefixes = tuple(
            prefix.strip() for prefix in (allowed_git_source_prefixes or ()) if str(prefix).strip()
        )
        self.git_ssh_source_dir = Path(git_ssh_source_dir) if git_ssh_source_dir else None
        self.container_runtime = container_runtime or DockerContainerRuntime(
            docker_network=docker_network,
            sandbox_profile=sandbox_profile,
            read_only_mounts=read_only_mounts,
        )
        self.token = token if token is not None else os.environ.get("EVANDER_WORKER_TOKEN")
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
        self._session_storage_telemetry_ttl_seconds = max(0.0, float(storage_telemetry_ttl_seconds))
        self._session_storage_telemetry_lock = threading.Lock()
        self._session_storage_telemetry_cache: _SessionStorageTelemetryCacheEntry | None = None
        self._session_storage_telemetry_refreshing = False
        self._session_storage_telemetry_background_generation: int | None = None
        self._session_storage_telemetry_next_generation = 0
        self._session_storage_telemetry_applied_generation = 0

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
            working_directory=request.working_directory,
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
        if status in {"exited", "stopped", "failed", "removed"}:
            log_text: str | None = None
            if _worker_job_uses_codex(record):
                try:
                    log_text = self._raw_logs(record)
                except OSError as exc:
                    logger.warning(
                        "worker_job_terminal_log_refresh_failed job_id=%s exception_class=%s exception_message=%s",
                        record.job_id,
                        exc.__class__.__name__,
                        str(exc),
                    )
            self._refresh_codex_job_outcome(record, log_text=log_text)
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
                metadata=record.metadata,
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
                metadata=record.metadata,
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
                metadata=record.metadata,
            ):
                lines.append(message)
        if not lines:
            return None
        return "\n".join(lines) + "\n"

    def _refresh_codex_job_outcome(
        self,
        record: WorkerJobRecord,
        *,
        log_text: str | None = None,
    ) -> None:
        if not _worker_job_uses_codex(record):
            return
        if record.started_at is None and record.finished_at is None:
            return
        text = log_text
        if text is None:
            text = ""
            log_path = self._log_path(record)
            if log_path.exists():
                try:
                    text = log_path.read_text(encoding="utf-8")
                except OSError:
                    text = ""
        thread_ids = list(dict.fromkeys(_CODEX_SESSION_ID_LOG_PATTERN.findall(text)))
        if not thread_ids:
            thread_ids = _recent_codex_session_ids(Path(record.agent_home_path), limit=1)
        if not thread_ids:
            return
        for thread_id in thread_ids:
            outcome = _codex_session_outcome(
                Path(record.agent_home_path),
                thread_id,
                started_at=record.started_at,
                finished_at=record.finished_at,
                metadata=record.metadata,
                log_text=text,
            )
            if outcome is None:
                continue
            metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
            metadata[_CODEX_OUTCOME_METADATA_KEY] = outcome
            record.metadata = sanitize_metadata(metadata)
            if outcome.get("status") == _CODEX_OUTCOME_STATUS_FAILED and not record.error:
                record.error = sanitize_text(str(outcome.get("message") or "Codex failed"))
            return

    @staticmethod
    def _codex_session_messages(
        agent_home: Path,
        thread_id: str,
        *,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        metadata: dict[str, object] | None = None,
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
        outcome = _failed_codex_outcome_from_metadata(metadata)
        if outcome is None:
            outcome = _codex_session_outcome(
                agent_home,
                thread_id,
                started_at=started_at,
                finished_at=finished_at,
                metadata=metadata,
            )
        if outcome is not None:
            message = _codex_outcome_log_line(outcome)
            if message and message not in seen:
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
                    if not message or _is_session_message_artifact(message) or message in seen:
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
            reconcile_existing_remote=request.reconcile_existing_remote,
        )
        return WorkerRepoCloneRecord(
            session_id=session_id,
            target_dir=f"/home/agent/repos/{repo_name}",
            guest_repo_name=repo_name,
            remote_url=request.repo_source,
            cloned=cloned,
            commit_sha=self._git_commit_sha(_target_dir, paths.agent_home),
        )

    def prepare_task_worktree(
        self,
        session_id: str,
        request: WorkerTaskWorktreeRequest,
    ) -> WorkerTaskWorktreeRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, session_id)
        repo_dir = paths.repo_root / request.repo_name
        ensure_resolved_path_within(
            base=paths.repo_root,
            candidate=repo_dir,
            description="task worktree repository",
        )
        if not (repo_dir / ".git").exists():
            raise ValueError(f"session repository does not exist: {request.repo_name}")
        worktrees_root = paths.agent_home / "worktrees" / request.repo_name
        target_dir = worktrees_root / request.worktree_name
        ensure_resolved_path_within(
            base=paths.agent_home,
            candidate=target_dir,
            description="task worktree target",
        )
        env = _git_subprocess_env(paths.agent_home)
        self._validate_git_branch(repo_dir, request.branch, env=env)
        created = False
        if target_dir.exists():
            current_branch = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if current_branch != request.branch:
                raise ValueError(
                    f"task worktree {request.worktree_name} uses branch "
                    f"{current_branch!r}, expected {request.branch!r}"
                )
        else:
            worktrees_root.mkdir(parents=True, exist_ok=True)
            local_branch = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{request.branch}"],
                cwd=repo_dir,
                env=env,
                check=False,
            )
            args = ["git", "worktree", "add"]
            if local_branch.returncode == 0:
                args.extend([str(target_dir), request.branch])
            else:
                args.extend(["-b", request.branch, str(target_dir), request.base_ref])
            subprocess.run(
                args,
                cwd=repo_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
            created = True
        subprocess.run(
            ["git", "config", "extensions.worktreeConfig", "true"],
            cwd=repo_dir,
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        for key, value in (
            ("evander.workBranch", request.branch),
            ("evander.baseBranch", request.base_ref),
        ):
            subprocess.run(
                ["git", "config", "--worktree", key, value],
                cwd=target_dir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )
        helper_source = repo_dir / ".evander"
        helper_target = target_dir / ".evander"
        if helper_source.is_dir() and not helper_target.exists():
            shutil.copytree(helper_source, helper_target)
        return WorkerTaskWorktreeRecord(
            session_id=session_id,
            repo_name=request.repo_name,
            target_dir=(
                f"/home/agent/worktrees/{request.repo_name}/{request.worktree_name}"
            ),
            branch=request.branch,
            commit_sha=self._git_commit_sha(target_dir, paths.agent_home),
            created=created,
        )

    def provision_git_write_key(
        self,
        session_id: str,
        request: WorkerGitWriteKeyRequest,
    ) -> WorkerGitWriteKeyRecord:
        paths = WorkerSessionPaths.resolve(self.worker_root, session_id)
        ssh_dir = paths.agent_home / ".ssh"
        self._ensure_managed_ssh_dir(ssh_dir)

        key_path = ssh_dir / request.key_name
        if not self._managed_regular_file_exists_no_follow(key_path, label="Git write private key"):
            self._generate_git_write_key_pair(
                ssh_dir=ssh_dir,
                key_path=key_path,
                session_id=paths.session_id,
            )
        self._chmod_regular_file_no_follow(
            key_path,
            0o600,
            label="Git write private key",
        )
        public_key_path = key_path.with_suffix(key_path.suffix + ".pub")
        public_key = self._read_managed_text_file_no_follow(
            public_key_path,
            label="Git write public key",
        ).strip()
        self._chmod_regular_file_no_follow(
            public_key_path,
            0o644,
            label="Git write public key",
        )

        config_path = ssh_dir / "config"
        write_git_ssh_alias = _git_write_ssh_alias(request.git_ssh_alias)
        self._write_git_write_ssh_config(
            config_path=config_path,
            read_git_ssh_alias=request.git_ssh_alias,
            write_git_ssh_alias=write_git_ssh_alias,
            git_host=request.git_host,
            git_user=request.git_user,
            identity_file=f"~/.ssh/{request.key_name}",
        )
        metadata = self._read_session_metadata(paths)
        metadata["git_write_key"] = {
            "public_key_body": public_key_body_from_line(public_key),
            "key_path": str(key_path),
            "config_path": str(config_path),
            "git_ssh_alias": write_git_ssh_alias,
            "updated_at": now_utc().isoformat(),
        }
        self._write_session_metadata(paths, metadata)
        return WorkerGitWriteKeyRecord(
            session_id=paths.session_id,
            public_key=public_key,
            public_key_body=public_key_body_from_line(public_key),
            key_path=str(key_path),
            config_path=str(config_path),
            git_ssh_alias=write_git_ssh_alias,
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
            existing_snapshot = (
                self._read_container_file_snapshot(
                    root=root,
                    relative_path=relative_path,
                )
                if entry.overwrite
                else None
            )
            if entry.content_base64 is not None:
                desired_content = base64.b64decode(entry.content_base64)
            else:
                content_text = entry.content or ""
                if entry.merge_strategy == "managed_section":
                    existing_text = (
                        existing_snapshot.content.decode("utf-8")
                        if existing_snapshot is not None
                        else ""
                    )
                    content_text = merge_managed_section(
                        existing_text,
                        section_id=str(entry.section_id or ""),
                        content=content_text,
                        remove_section_ids=entry.remove_section_ids,
                    )
                desired_content = content_text.encode("utf-8")
            desired_mode = (
                int(self._normalize_mode(entry.mode), 8) if entry.mode is not None else None
            )
            if existing_snapshot is not None and entry.overwrite:
                content_matches = existing_snapshot.content == desired_content
                mode_matches = (
                    desired_mode is None or stat.S_IMODE(existing_snapshot.mode) == desired_mode
                )
                if content_matches and mode_matches:
                    continue
                if content_matches and desired_mode is not None:
                    self._chmod_container_file(
                        root=root,
                        relative_path=relative_path,
                        mode=desired_mode,
                        container_path=container_path,
                    )
                    copied_paths.append(container_path)
                    continue
            self._write_container_file(
                root=root,
                relative_path=relative_path,
                content=desired_content,
                overwrite=entry.overwrite,
                mode=desired_mode,
                container_path=container_path,
            )
            copied_paths.append(container_path)
        return WorkerFileWriteRecord(
            session_id=session_id,
            copied_count=len(copied_paths),
            copied_paths=copied_paths,
            roots={"agent_home": "/home/agent", "repo_root": "/home/agent/repos"},
        )

    def read_session_artifact_text(self, session_id: str, relative_path: str) -> str:
        _normalized_session_id, _sessions_root, session_root = (
            WorkerSessionPaths.resolve_session_root(self.worker_root, session_id)
        )
        root = session_root / "artifacts"
        return read_artifact_text_no_follow(root, relative_path)

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
        normalized_session_id, _sessions_root, session_root = (
            WorkerSessionPaths.resolve_session_root(self.worker_root, session_id)
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

    def read_telemetry(self, *, refresh: bool = False) -> WorkerTelemetryPayload:
        worker_root_disk = collect_disk_usage(self.worker_root, scope="worker_root")
        return WorkerTelemetryPayload(
            disk=WorkerDiskTelemetry(worker_root=worker_root_disk),
            session_storage=self._session_storage_telemetry(refresh=refresh),
            jobs=self._job_telemetry(),
        )

    def _log_path(self, record: WorkerJobRecord) -> Path:
        return Path(record.logs_path) / f"{record.job_id}.log"

    def _session_storage_telemetry(self, *, refresh: bool) -> WorkerSessionStorageTelemetry:
        if refresh:
            return self._refresh_session_storage_telemetry()

        now = time.monotonic()
        with self._session_storage_telemetry_lock:
            cached = self._session_storage_telemetry_cache
            if cached is not None:
                if self._session_storage_telemetry_is_stale(cached, now=now):
                    self._start_session_storage_telemetry_refresh_locked()
                return cached.telemetry

            self._start_session_storage_telemetry_refresh_locked()
        return self._warming_session_storage_telemetry()

    def _session_storage_telemetry_is_stale(
        self,
        cached: _SessionStorageTelemetryCacheEntry,
        *,
        now: float,
    ) -> bool:
        return now - cached.collected_at_monotonic >= self._session_storage_telemetry_ttl_seconds

    def _start_session_storage_telemetry_refresh_locked(self) -> None:
        if self._session_storage_telemetry_refreshing:
            return
        generation = self._next_session_storage_telemetry_generation_locked()
        self._session_storage_telemetry_refreshing = True
        self._session_storage_telemetry_background_generation = generation
        thread = threading.Thread(
            target=self._refresh_session_storage_telemetry_async,
            args=(generation,),
            name="evander-worker-session-storage-telemetry",
            daemon=True,
        )
        thread.start()

    def _next_session_storage_telemetry_generation_locked(self) -> int:
        self._session_storage_telemetry_next_generation += 1
        return self._session_storage_telemetry_next_generation

    def _refresh_session_storage_telemetry_async(self, generation: int) -> None:
        try:
            self._refresh_session_storage_telemetry(generation=generation)
        except Exception:
            logger.exception("worker_session_storage_telemetry_refresh_failed")
            with self._session_storage_telemetry_lock:
                if self._session_storage_telemetry_background_generation == generation:
                    self._session_storage_telemetry_background_generation = None
                    self._session_storage_telemetry_refreshing = False

    def _refresh_session_storage_telemetry(
        self,
        *,
        generation: int | None = None,
    ) -> WorkerSessionStorageTelemetry:
        if generation is None:
            with self._session_storage_telemetry_lock:
                generation = self._next_session_storage_telemetry_generation_locked()
        telemetry = self._collect_session_storage_telemetry()
        with self._session_storage_telemetry_lock:
            if generation >= self._session_storage_telemetry_applied_generation:
                self._session_storage_telemetry_cache = _SessionStorageTelemetryCacheEntry(
                    telemetry=telemetry,
                    collected_at_monotonic=time.monotonic(),
                    refresh_generation=generation,
                )
                self._session_storage_telemetry_applied_generation = generation
            if self._session_storage_telemetry_background_generation == generation:
                self._session_storage_telemetry_background_generation = None
                self._session_storage_telemetry_refreshing = False
        return telemetry

    def _collect_session_storage_telemetry(self) -> WorkerSessionStorageTelemetry:
        session_storage_total, session_samples = self._scan_session_storage(
            self.worker_root / "sessions"
        )
        return WorkerSessionStorageTelemetry(
            total=session_storage_total,
            session_count=len(session_samples),
            largest_sessions=top_session_storage(session_samples, limit=5, order="largest"),
            oldest_sessions=top_session_storage(session_samples, limit=5, order="oldest"),
        )

    def _scan_session_storage(
        self,
        sessions_root: Path,
    ) -> tuple[DirectorySizeTelemetry, list[SessionStorageSample]]:
        if sessions_root.is_symlink():
            return (
                self._session_storage_total_failure(
                    status=TelemetryStatus.UNSUPPORTED,
                    message="symlink roots are not followed",
                ),
                [],
            )
        try:
            root_stat = sessions_root.stat()
        except FileNotFoundError:
            return (
                self._session_storage_total_failure(
                    status=TelemetryStatus.MISSING,
                    message="path is missing",
                ),
                [],
            )
        except PermissionError:
            return (
                self._session_storage_total_failure(
                    status=TelemetryStatus.PERMISSION_DENIED,
                    message="permission denied",
                ),
                [],
            )
        except OSError:
            return (
                self._session_storage_total_failure(
                    status=TelemetryStatus.UNSUPPORTED,
                    message="directory size is unavailable",
                ),
                [],
            )

        if stat.S_ISREG(root_stat.st_mode):
            return (
                DirectorySizeTelemetry(
                    scope="worker_sessions",
                    status=TelemetryStatus.AVAILABLE,
                    health=HealthState.OK,
                    size_bytes=max(0, int(root_stat.st_size)),
                    file_count=1,
                ),
                [],
            )
        if not stat.S_ISDIR(root_stat.st_mode):
            return (
                self._session_storage_total_failure(
                    status=TelemetryStatus.UNSUPPORTED,
                    message="path is not a regular file or directory",
                ),
                [],
            )

        totals = {
            "size_bytes": 0,
            "file_count": 0,
            "directory_count": 0,
            "symlink_count": 0,
        }
        session_sizes: dict[str, int] = {}
        errors: list[TelemetryError] = []
        self._accumulate_session_storage(
            sessions_root,
            totals=totals,
            session_sizes=session_sizes,
            errors=errors,
            allow_session_root=True,
            session_id=None,
        )
        health = HealthState.OK if not errors else HealthState.WARNING
        session_storage_total = DirectorySizeTelemetry(
            scope="worker_sessions",
            status=TelemetryStatus.AVAILABLE,
            health=health,
            size_bytes=totals["size_bytes"],
            file_count=totals["file_count"],
            directory_count=totals["directory_count"],
            symlink_count=totals["symlink_count"],
            error_count=len(errors),
            errors=errors,
        )
        session_samples = [
            self._session_storage_sample_from_size(
                sessions_root / session_id,
                session_id=session_id,
                size_bytes=size_bytes,
            )
            for session_id, size_bytes in session_sizes.items()
        ]
        return session_storage_total, session_samples

    def _accumulate_session_storage(
        self,
        path: Path,
        *,
        totals: dict[str, int],
        session_sizes: dict[str, int],
        errors: list[TelemetryError],
        allow_session_root: bool,
        session_id: str | None,
    ) -> None:
        try:
            entries = list(os.scandir(path))
        except FileNotFoundError:
            errors.append(
                TelemetryError(
                    status=TelemetryStatus.MISSING,
                    message="directory entry is missing",
                )
            )
            return
        except PermissionError:
            errors.append(
                TelemetryError(
                    status=TelemetryStatus.PERMISSION_DENIED,
                    message="permission denied while scanning directory",
                )
            )
            return
        except NotADirectoryError:
            errors.append(
                TelemetryError(
                    status=TelemetryStatus.UNSUPPORTED,
                    message="path is not a directory",
                )
            )
            return
        except OSError:
            errors.append(
                TelemetryError(
                    status=TelemetryStatus.UNSUPPORTED,
                    message="directory size is unavailable",
                )
            )
            return

        for entry in entries:
            try:
                if entry.is_symlink():
                    totals["symlink_count"] += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    totals["directory_count"] += 1
                    child_session_id = session_id
                    if allow_session_root and child_session_id is None:
                        candidate = str(entry.name or "").strip()
                        if candidate and SAFE_ID_PATTERN.fullmatch(candidate):
                            child_session_id = candidate
                            session_sizes.setdefault(candidate, 0)
                    self._accumulate_session_storage(
                        Path(entry.path),
                        totals=totals,
                        session_sizes=session_sizes,
                        errors=errors,
                        allow_session_root=False,
                        session_id=child_session_id,
                    )
                    continue

                entry_stat = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(entry_stat.st_mode):
                    size_bytes = max(0, int(entry_stat.st_size))
                    totals["file_count"] += 1
                    totals["size_bytes"] += size_bytes
                    if session_id is not None:
                        session_sizes[session_id] = session_sizes.get(session_id, 0) + size_bytes
                    continue

                errors.append(
                    TelemetryError(
                        status=TelemetryStatus.UNSUPPORTED,
                        message="unsupported filesystem entry",
                    )
                )
            except FileNotFoundError:
                errors.append(
                    TelemetryError(
                        status=TelemetryStatus.MISSING,
                        message="directory entry is missing",
                    )
                )
            except PermissionError:
                errors.append(
                    TelemetryError(
                        status=TelemetryStatus.PERMISSION_DENIED,
                        message="permission denied while scanning directory",
                    )
                )
            except OSError:
                errors.append(
                    TelemetryError(
                        status=TelemetryStatus.UNSUPPORTED,
                        message="directory entry is unavailable",
                    )
                )

    def _session_storage_sample_from_size(
        self,
        session_root: Path,
        *,
        session_id: str,
        size_bytes: int,
    ) -> SessionStorageSample:
        created_at, updated_at = self._session_storage_timestamps(session_root)
        return SessionStorageSample(
            session_id=session_id,
            directory_path=f"sessions/{session_id}",
            backend="container_worker_vm",
            size_bytes=size_bytes,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _warming_session_storage_telemetry(self) -> WorkerSessionStorageTelemetry:
        return WorkerSessionStorageTelemetry(
            total=DirectorySizeTelemetry(
                scope="worker_sessions",
                status=TelemetryStatus.UNSUPPORTED,
                health=HealthState.UNKNOWN,
                error_count=1,
                errors=[
                    TelemetryError(
                        status=TelemetryStatus.UNSUPPORTED,
                        message=_SESSION_STORAGE_WARMING_MESSAGE,
                    )
                ],
            )
        )

    def _session_storage_total_failure(
        self,
        *,
        status: TelemetryStatus,
        message: str,
    ) -> DirectorySizeTelemetry:
        return DirectorySizeTelemetry(
            scope="worker_sessions",
            status=status,
            health=HealthState.UNKNOWN,
            error_count=1,
            errors=[TelemetryError(status=status, message=message)],
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
        self._refresh_codex_job_outcome(record)
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
        self._refresh_codex_job_outcome(record)
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
            reconcile_existing_remote=False,
        )

    def _clone_repo_into_repo_root(
        self,
        *,
        paths: WorkerSessionPaths,
        repo_source: str,
        clone_branch: str | None,
        repo_name: str,
        reconcile_existing_remote: bool = False,
    ) -> tuple[Path, bool]:
        self._ensure_allowed_git_source(repo_source)
        repo_name = str(validate_worker_component_id(repo_name, field_name="repo_name"))
        target_dir = paths.repo_root / repo_name
        ensure_resolved_path_within(
            base=paths.repo_root,
            candidate=target_dir,
            description="repo target",
        )
        if target_dir.is_symlink():
            raise ValueError(
                f"repo target already exists and is not a directory: /home/agent/repos/{repo_name}"
            )
        if target_dir.exists() and not target_dir.is_dir():
            raise ValueError(
                f"repo target already exists and is not a directory: /home/agent/repos/{repo_name}"
            )
        if target_dir.exists() and any(target_dir.iterdir()):
            if self._is_git_checkout_for_source(
                target_dir,
                repo_source,
                agent_home=paths.agent_home,
            ):
                return target_dir, False
            if reconcile_existing_remote and (target_dir / ".git").exists():
                git_env = _git_subprocess_env(paths.agent_home)
                has_origin = subprocess.run(
                    ["git", "-C", str(target_dir), "remote", "get-url", "origin"],
                    env=git_env,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                remote_action = "set-url" if has_origin.returncode == 0 else "add"
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(target_dir),
                        "remote",
                        remote_action,
                        "origin",
                        repo_source,
                    ],
                    env=git_env,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return target_dir, False
            raise ValueError(
                f"repo target already exists and is not a checkout for requested source: "
                f"/home/agent/repos/{repo_name}"
            )
        target_dir.mkdir(parents=True, exist_ok=True)
        ensure_resolved_path_within(
            base=paths.repo_root,
            candidate=target_dir,
            description="repo target",
        )
        args = ["git", "clone"]
        if clone_branch:
            args.extend(["--branch", clone_branch])
        args.extend([repo_source, str(target_dir)])
        subprocess.run(
            args,
            cwd=paths.repo_root,
            env=_git_subprocess_env(paths.agent_home),
            check=True,
            capture_output=True,
            text=True,
        )
        return target_dir, True

    def _ensure_allowed_git_source(self, repo_source: str) -> None:
        if not self.allowed_git_source_prefixes:
            raise ValueError("worker Git source prefixes are not configured; refusing repo_source")
        if not any(repo_source.startswith(prefix) for prefix in self.allowed_git_source_prefixes):
            allowed = ", ".join(self.allowed_git_source_prefixes)
            raise ValueError(
                f"repo_source is not allowed for this worker: {repo_source!r}; "
                f"allowed prefixes: {allowed}"
            )

    @staticmethod
    def _git_commit_sha(repo_dir: Path, agent_home: Path) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD^{commit}"],
            cwd=repo_dir,
            env=_git_subprocess_env(agent_home),
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    @staticmethod
    def _validate_git_branch(
        repo_dir: Path,
        branch: str,
        *,
        env: dict[str, str],
    ) -> None:
        result = subprocess.run(
            ["git", "check-ref-format", "--branch", branch],
            cwd=repo_dir,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError(f"invalid task branch: {branch!r}")

    def _sync_git_ssh_into_agent_home(
        self,
        paths: WorkerSessionPaths,
        *,
        required: bool,
    ) -> None:
        target_dir = paths.agent_home / ".ssh"
        if required and self._managed_regular_file_exists_no_follow(
            target_dir / "config",
            label="SSH config",
        ) and any(
            self._managed_regular_file_exists_no_follow(target_dir / key_name, label="Git key")
            for key_name in ("evander-git-write", "evander-git")
        ):
            return
        if self.git_ssh_source_dir is None:
            if required:
                raise ValueError("worker Git SSH source directory is not configured")
            return
        source_dir = self.git_ssh_source_dir
        required_files = ("evander-git", "config")
        missing = [name for name in required_files if not (source_dir / name).is_file()]
        if missing:
            if required:
                raise ValueError(
                    "worker Git SSH source directory is missing required files: "
                    + ", ".join(missing)
                )
            return
        self._ensure_managed_ssh_dir(target_dir)
        for name in ("evander-git", "evander-git.pub", "config", "known_hosts"):
            if name == "config" and self._managed_regular_file_exists_no_follow(
                target_dir / "evander-git-write",
                label="Git write private key",
            ):
                continue
            source = source_dir / name
            if not source.is_file():
                continue
            destination = target_dir / name
            self._write_managed_bytes_file_no_follow(
                destination,
                source.read_bytes(),
                mode=0o644 if name.endswith(".pub") else 0o600,
                label=f"SSH {name}",
            )

    @staticmethod
    def _managed_regular_file_exists_no_follow(
        path: Path,
        *,
        label: str,
    ) -> bool:
        try:
            stat_result = path.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(stat_result.st_mode):
            raise ValueError(f"{label} must not be a symlink")
        if not stat.S_ISREG(stat_result.st_mode):
            raise ValueError(f"{label} must be a regular file")
        return True

    @classmethod
    def _ensure_managed_ssh_dir(cls, ssh_dir: Path) -> None:
        try:
            ssh_dir.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            pass
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            fd = os.open(ssh_dir, flags)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError("Managed SSH directory must not be a symlink") from exc
            if exc.errno == errno.ENOTDIR:
                raise ValueError("Managed SSH path must be a directory") from exc
            raise
        try:
            stat_result = os.fstat(fd)
            if not stat.S_ISDIR(stat_result.st_mode):
                raise ValueError("Managed SSH path must be a directory")
            os.fchmod(fd, 0o700)
        finally:
            os.close(fd)

    @classmethod
    def _generate_git_write_key_pair(
        cls,
        *,
        ssh_dir: Path,
        key_path: Path,
        session_id: str,
    ) -> None:
        private_temp = ssh_dir / f".{key_path.name}-{uuid.uuid4().hex}"
        public_temp = private_temp.with_name(f"{private_temp.name}.pub")
        try:
            cls._validate_managed_file_target_no_follow(
                key_path,
                label="Git write private key",
            )
            cls._validate_managed_file_target_no_follow(
                key_path.with_suffix(key_path.suffix + ".pub"),
                label="Git write public key",
            )
            subprocess.run(
                [
                    "ssh-keygen",
                    "-q",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-C",
                    f"evander-git-write@{session_id}",
                    "-f",
                    str(private_temp),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            cls._replace_with_managed_temp_file_no_follow(
                key_path,
                private_temp,
                mode=0o600,
                label="Git write private key",
            )
            cls._replace_with_managed_temp_file_no_follow(
                key_path.with_suffix(key_path.suffix + ".pub"),
                public_temp,
                mode=0o644,
                label="Git write public key",
            )
        finally:
            for temp_path in (private_temp, public_temp):
                try:
                    temp_path.unlink()
                except FileNotFoundError:
                    pass

    @classmethod
    def _open_regular_file_no_follow(cls, path: Path, *, label: str, flags: int) -> int:
        try:
            fd = os.open(
                path,
                flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError(f"{label} must not be a symlink") from exc
            if exc.errno in {errno.EISDIR, errno.ENOTDIR}:
                raise ValueError(f"{label} must be a regular file") from exc
            raise
        try:
            stat_result = os.fstat(fd)
            if not stat.S_ISREG(stat_result.st_mode):
                raise ValueError(f"{label} must be a regular file")
        except Exception:
            os.close(fd)
            raise
        return fd

    @classmethod
    def _read_managed_text_file_no_follow(cls, path: Path, *, label: str) -> str:
        fd = cls._open_regular_file_no_follow(path, label=label, flags=os.O_RDONLY)
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            return handle.read()

    @classmethod
    def _chmod_regular_file_no_follow(cls, path: Path, mode: int, *, label: str) -> None:
        fd = cls._open_regular_file_no_follow(path, label=label, flags=os.O_RDONLY)
        try:
            os.fchmod(fd, mode)
        finally:
            os.close(fd)

    @classmethod
    def _validate_managed_file_target_no_follow(cls, path: Path, *, label: str) -> None:
        cls._managed_regular_file_exists_no_follow(path, label=label)

    @classmethod
    def _write_managed_bytes_file_no_follow(
        cls,
        path: Path,
        content: bytes,
        *,
        mode: int,
        label: str,
    ) -> None:
        cls._validate_managed_file_target_no_follow(path, label=label)
        temp_path = path.with_name(f".{path.name}-{uuid.uuid4().hex}")
        fd = os.open(
            temp_path,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            mode,
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            cls._replace_with_managed_temp_file_no_follow(
                path,
                temp_path,
                mode=mode,
                label=label,
            )
        finally:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass

    @classmethod
    def _write_managed_text_file_no_follow(
        cls,
        path: Path,
        content: str,
        *,
        mode: int,
        label: str,
    ) -> None:
        cls._write_managed_bytes_file_no_follow(
            path,
            content.encode("utf-8"),
            mode=mode,
            label=label,
        )

    @classmethod
    def _replace_with_managed_temp_file_no_follow(
        cls,
        path: Path,
        temp_path: Path,
        *,
        mode: int,
        label: str,
    ) -> None:
        cls._validate_managed_file_target_no_follow(path, label=label)
        cls._chmod_regular_file_no_follow(temp_path, mode, label=f"temporary {label}")
        os.replace(temp_path, path)

    @classmethod
    def _write_git_write_ssh_config(
        cls,
        *,
        config_path: Path,
        read_git_ssh_alias: str,
        write_git_ssh_alias: str,
        git_host: str,
        git_user: str,
        identity_file: str,
    ) -> None:
        cls._ensure_managed_ssh_dir(config_path.parent)
        text = (
            cls._read_managed_text_file_no_follow(config_path, label="SSH config")
            if cls._managed_regular_file_exists_no_follow(
                config_path,
                label="SSH config",
            )
            else ""
        )
        for marker_begin, marker_end in (
            ("# BEGIN evander git host", "# END evander git host"),
            ("# BEGIN evander git write host", "# END evander git write host"),
        ):
            while marker_begin in text and marker_end in text:
                start = text.index(marker_begin)
                stop = text.index(marker_end, start) + len(marker_end)
                suffix = "\n" if stop < len(text) and text[stop : stop + 1] == "\n" else ""
                text = text[:start].rstrip() + "\n" + text[stop + len(suffix) :].lstrip("\n")
        block = "\n".join(
            [
                "# BEGIN evander git host",
                f"Host {read_git_ssh_alias}",
                f"  HostName {git_host}",
                f"  User {git_user}",
                "  IdentityFile ~/.ssh/evander-git",
                "  IdentitiesOnly yes",
                "  StrictHostKeyChecking accept-new",
                "# END evander git host",
                "",
                "# BEGIN evander git write host",
                f"Host {write_git_ssh_alias}",
                f"  HostName {git_host}",
                f"  User {git_user}",
                f"  IdentityFile {identity_file}",
                "  IdentitiesOnly yes",
                "  StrictHostKeyChecking accept-new",
                "# END evander git write host",
                "",
            ]
        )
        text = block if not text.strip() else block + "\n" + text.strip() + "\n"
        cls._write_managed_text_file_no_follow(
            config_path,
            text,
            mode=0o600,
            label="SSH config",
        )

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
        for container_root, host_root in (("/home/agent", paths.agent_home),):
            if normalized == container_root:
                raise ValueError(f"destination_path must name a file below {container_root}")
            prefix = f"{container_root}/"
            if not normalized.startswith(prefix):
                continue
            relative_text = normalized[len(prefix) :]
            if any(part in {"", ".", ".."} for part in relative_text.split("/")):
                raise ValueError(
                    f"destination_path must not contain empty, dot, or parent "
                    f"segments below {container_root}"
                )
            relative = Path(relative_text)
            if relative.is_absolute():
                raise ValueError(f"destination_path must stay below {container_root}")
            return host_root, relative, f"{container_root}/{relative.as_posix()}"
        raise ValueError("destination_path must be under /home/agent")

    @staticmethod
    def _read_container_file_snapshot(
        *,
        root: Path,
        relative_path: Path,
    ) -> _ContainerFileSnapshot | None:
        fd = open_relative_regular_file_no_follow(
            root,
            relative_path.as_posix(),
            flags=os.O_RDONLY,
            missing_ok=True,
            path_label="destination_path",
            unsafe_path_message=_DESTINATION_NOFOLLOW_ERROR,
        )
        if fd is None:
            return None
        with os.fdopen(fd, "rb") as handle:
            file_stat = os.fstat(handle.fileno())
            return _ContainerFileSnapshot(
                content=handle.read(),
                mode=file_stat.st_mode,
            )

    @staticmethod
    def _chmod_container_file(
        *,
        root: Path,
        relative_path: Path,
        mode: int,
        container_path: str,
    ) -> None:
        fd = open_relative_regular_file_no_follow(
            root,
            relative_path.as_posix(),
            flags=os.O_RDONLY,
            path_label="destination_path",
            unsafe_path_message=_DESTINATION_NOFOLLOW_ERROR,
        )
        if fd is None:
            raise FileNotFoundError(f"destination_path is not a regular file: {container_path}")
        try:
            os.fchmod(fd, mode)
        finally:
            os.close(fd)

    @staticmethod
    def _write_container_file(
        *,
        root: Path,
        relative_path: Path,
        content: bytes,
        overwrite: bool,
        mode: int | None,
        container_path: str,
    ) -> None:
        flags = os.O_WRONLY | os.O_CREAT
        if not overwrite:
            flags |= os.O_EXCL
        else:
            flags = os.O_RDWR | os.O_CREAT
        fd = open_relative_regular_file_no_follow(
            root,
            relative_path.as_posix(),
            flags=flags,
            mode=mode if mode is not None else 0o666,
            create_parents=True,
            path_label="destination_path",
            already_exists_message=f"destination already exists: {container_path}",
            unsafe_path_message=_DESTINATION_NOFOLLOW_ERROR,
        )
        if fd is None:
            raise FileNotFoundError(f"destination_path is not a regular file: {container_path}")
        try:
            os.ftruncate(fd, 0)
            remaining = memoryview(content)
            while remaining:
                written = os.write(fd, remaining)
                if written == 0:
                    raise OSError("failed to write destination file")
                remaining = remaining[written:]
            if mode is not None:
                os.fchmod(fd, mode)
        finally:
            os.close(fd)

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
    def _is_git_checkout_for_source(
        target_dir: Path,
        repo_source: str,
        *,
        agent_home: Path,
    ) -> bool:
        if not (target_dir / ".git").exists():
            return False
        try:
            completed = subprocess.run(
                ["git", "-C", str(target_dir), "remote", "get-url", "origin"],
                env=_git_subprocess_env(agent_home),
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
    return normalized.startswith("ssh://") or bool(re.match(r"^[^/@:\s]+@[^:\s]+:.+", normalized))


def _git_write_ssh_alias(read_git_ssh_alias: str) -> str:
    """Return the dedicated host alias for a session's restricted write key."""

    return f"{read_git_ssh_alias}-write"


def _git_subprocess_env(agent_home: Path) -> dict[str, str]:
    return os.environ | {
        "HOME": str(agent_home),
        "GIT_SSH_COMMAND": f"ssh -F {agent_home / '.ssh' / 'config'}",
    }


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
    return (
        "evander_claude_json_to_log" in command_text
        or re.search(
            r"(^|\s)claude(\s|$)",
            command_text,
        )
        is not None
    )


def _worker_job_uses_codex(record: WorkerJobRecord) -> bool:
    if _worker_job_uses_claude(record):
        return False
    metadata = record.metadata if isinstance(record.metadata, dict) else {}
    for key in ("agent_provider", "provider", "model_provider"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip().casefold() == "codex":
            return True
    command = getattr(record, "command", None)
    command_text = (
        " ".join(command)
        if isinstance(command, list) and all(isinstance(item, str) for item in command)
        else str(command or "")
    ).casefold()
    return (
        "evander_codex_json_to_log" in command_text
        or re.search(
            r"(^|\s)codex(\s|$)",
            command_text,
        )
        is not None
    )


def _codex_session_outcome(
    agent_home: Path,
    thread_id: str,
    *,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
    metadata: dict[str, object] | None = None,
    log_text: str | None = None,
) -> dict[str, object] | None:
    task_complete_without_final_message = False
    explicit_failure_message: str | None = None
    explicit_failure_event_type: str | None = None
    last_token_usage: dict[str, int] | None = None
    model_context_window: int | None = None
    assistant_text_seen = False
    saw_event = False

    for event in _iter_codex_session_events(
        agent_home,
        thread_id,
        started_at=started_at,
        finished_at=finished_at,
    ):
        saw_event = True
        event_type = str(event.get("type") or "")
        if _codex_event_is_token_count(event):
            token_usage, context_window = _codex_token_count_details(event)
            if token_usage:
                last_token_usage = token_usage
            if context_window is not None:
                model_context_window = context_window
            continue
        if any(message.strip() for message in _codex_messages_from_session_event(event)):
            assistant_text_seen = True
        if event_type in {"error", "turn.failed"}:
            message = _codex_event_error_message(event)
            if message:
                explicit_failure_message = message
                explicit_failure_event_type = event_type
            continue
        if event_type != "event_msg":
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if str(payload.get("type") or "") != "task_complete":
            continue
        if not _codex_first_text(payload.get("last_agent_message")).strip():
            task_complete_without_final_message = True

    if not saw_event:
        return None

    provider_kind = _codex_provider_kind(metadata)
    unsupported_tool_calls = _codex_unsupported_tool_calls(log_text)
    outcome: dict[str, object] | None = None
    if explicit_failure_message:
        outcome = {
            "status": _CODEX_OUTCOME_STATUS_FAILED,
            "reason": _CODEX_OUTCOME_REASON_TURN_FAILED,
            "message": f"Codex reported {explicit_failure_event_type}: {explicit_failure_message}",
        }
    elif (
        task_complete_without_final_message or finished_at is not None
    ) and not assistant_text_seen:
        if unsupported_tool_calls:
            reason = _CODEX_OUTCOME_REASON_UNSUPPORTED_TOOL_CALL
            message = (
                "Codex ended without a final agent message after emitting unsupported tool "
                f"calls: {', '.join(unsupported_tool_calls)}."
            )
        else:
            context_limit_suspected = _codex_context_limit_suspected(
                provider_kind=provider_kind,
                last_token_usage=last_token_usage,
                model_context_window=model_context_window,
            )
            reason = (
                _CODEX_OUTCOME_REASON_CONTEXT_LIMIT_SUSPECTED
                if context_limit_suspected
                else _CODEX_OUTCOME_REASON_NO_FINAL_MESSAGE
            )
            message = "Codex ended without a final agent message."
            if context_limit_suspected:
                total_tokens = last_token_usage.get("total_tokens") if last_token_usage else None
                if total_tokens is not None:
                    message = (
                        "Codex ended without a final agent message after local model token usage "
                        f"reached {total_tokens} tokens."
                    )
                else:
                    message = (
                        "Codex ended without a final agent message after local model token usage "
                        "reached the configured warning threshold."
                    )
        outcome = {
            "status": _CODEX_OUTCOME_STATUS_FAILED,
            "reason": reason,
            "message": message,
        }

    if outcome is None:
        return None
    outcome["thread_ids"] = [thread_id]
    outcome["provider_kind"] = provider_kind
    if last_token_usage:
        outcome["last_token_usage"] = last_token_usage
    if model_context_window is not None:
        outcome["model_context_window"] = model_context_window
    if unsupported_tool_calls:
        outcome["unsupported_tool_calls"] = unsupported_tool_calls
    return sanitize_metadata(outcome)


def _failed_codex_outcome_from_metadata(
    metadata: dict[str, object] | None,
) -> dict[str, object] | None:
    if not isinstance(metadata, dict):
        return None
    outcome = metadata.get(_CODEX_OUTCOME_METADATA_KEY)
    if not isinstance(outcome, dict):
        return None
    if str(outcome.get("status") or "").strip().casefold() != _CODEX_OUTCOME_STATUS_FAILED:
        return None
    return outcome


def _codex_unsupported_tool_calls(log_text: str | None) -> list[str]:
    if not isinstance(log_text, str) or not log_text:
        return []
    calls: list[str] = []
    seen: set[str] = set()
    for match in _CODEX_UNSUPPORTED_TOOL_CALL_PATTERN.finditer(log_text):
        tool = sanitize_text(match.group("tool")).strip()
        if not tool or tool in seen:
            continue
        seen.add(tool)
        calls.append(tool)
    return calls


def _iter_codex_session_events(
    agent_home: Path,
    thread_id: str,
    *,
    started_at: datetime | None = None,
    finished_at: datetime | None = None,
):
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
            if not isinstance(event, dict):
                continue
            if not _codex_event_is_in_window(
                event,
                started_at=started_at,
                finished_at=finished_at,
            ):
                continue
            yield event


def _codex_event_is_token_count(event: dict[str, object]) -> bool:
    if str(event.get("type") or "") == "token_count":
        return True
    payload = event.get("payload")
    return isinstance(payload, dict) and str(payload.get("type") or "") == "token_count"


def _codex_token_count_details(
    event: dict[str, object],
) -> tuple[dict[str, int] | None, int | None]:
    candidates: list[dict[str, object]] = []
    info = event.get("info")
    if isinstance(info, dict):
        candidates.append(info)
    payload = event.get("payload")
    if isinstance(payload, dict):
        candidates.append(payload)
        payload_info = payload.get("info")
        if isinstance(payload_info, dict):
            candidates.append(payload_info)

    usage: dict[str, int] = {}
    model_context_window: int | None = None
    for candidate in candidates:
        raw_usage = candidate.get("last_token_usage") or candidate.get("usage")
        if not isinstance(raw_usage, dict):
            continue
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = _codex_int(raw_usage.get(key))
            if value is not None:
                usage[key] = value
        if model_context_window is None:
            model_context_window = _codex_int(
                candidate.get("model_context_window")
                or candidate.get("context_window")
                or candidate.get("context_limit")
            )
    return (usage or None), model_context_window


def _codex_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _codex_event_error_message(event: dict[str, object]) -> str:
    payload = event.get("payload")
    payload_message = ""
    if isinstance(payload, dict):
        payload_message = _codex_first_text(
            payload.get("message"),
            payload.get("error"),
            payload.get("detail"),
        )
    return _codex_first_text(
        event.get("message"),
        event.get("error"),
        event.get("detail"),
        payload_message,
    ).strip()


def _codex_provider_kind(metadata: dict[str, object] | None) -> str:
    if not isinstance(metadata, dict):
        return "unknown"
    mode = (
        _codex_first_text(
            metadata.get("model_provider_mode"),
            metadata.get("openai_provider_mode"),
        )
        .strip()
        .casefold()
    )
    if mode in _CODEX_LOCAL_MODEL_PROVIDER_MODES:
        return "local_model"
    if mode in {"openai_public", "azure_openai", "anthropic"}:
        return "cloud_or_unknown"
    return "unknown"


def _codex_context_limit_suspected(
    *,
    provider_kind: str,
    last_token_usage: dict[str, int] | None,
    model_context_window: int | None,
) -> bool:
    if provider_kind != "local_model" or not last_token_usage:
        return False
    total_tokens = last_token_usage.get("total_tokens")
    if total_tokens is None:
        input_tokens = last_token_usage.get("input_tokens")
        output_tokens = last_token_usage.get("output_tokens")
        if input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens
    if total_tokens is None:
        return False
    if total_tokens >= _CODEX_CONTEXT_LIMIT_WARNING_TOTAL_TOKENS:
        return True
    if model_context_window is not None and model_context_window > 0:
        return total_tokens >= int(model_context_window * 0.98)
    return False


def _codex_outcome_log_line(outcome: dict[str, object]) -> str:
    reason = str(outcome.get("reason") or "").strip()
    message = str(outcome.get("message") or "").strip()
    if reason == _CODEX_OUTCOME_REASON_TURN_FAILED:
        return f"codex failed: {message or 'Codex reported a turn failure.'}"
    if reason == _CODEX_OUTCOME_REASON_UNSUPPORTED_TOOL_CALL:
        return f"codex failed: {message or 'Codex emitted an unsupported tool call.'}"
    usage = outcome.get("last_token_usage")
    total_tokens = None
    if isinstance(usage, dict):
        total_tokens = usage.get("total_tokens")
    suffix = f" (last_total_tokens={total_tokens})" if total_tokens is not None else ""
    if reason == _CODEX_OUTCOME_REASON_CONTEXT_LIMIT_SUSPECTED:
        return (
            "codex failed: task_complete did not include a final agent message; "
            f"context limit suspected{suffix}"
        )
    return f"codex failed: task_complete did not include a final agent message{suffix}"


def _codex_session_jsonl_paths(agent_home: Path, thread_id: str) -> list[Path]:
    roots = [
        agent_home / ".config" / "evander" / "codex" / "sessions",
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
        agent_home / ".config" / "evander" / "codex" / "sessions",
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
            str(candidate).strip() if isinstance(candidate, str) else parsed_candidate.isoformat()
        )
    parsed_current = _parse_codex_timestamp(current)
    if parsed_current is None or parsed_candidate > parsed_current:
        return (
            str(candidate).strip() if isinstance(candidate, str) else parsed_candidate.isoformat()
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
    name = (
        _codex_first_text(
            payload.get("name"),
            payload.get("tool_name"),
            payload.get("function_name"),
        )
        or "tool"
    )
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
