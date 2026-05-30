from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agent_sandbox.admin.service import (
    resolve_agent_model_for_manifest,
    resolve_effective_issue_setup_config,
)
from agent_sandbox.agent_control import AgentControlStore
from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.agent_session import (
    container_prompt_command,
    container_worker_guest_gateway_url,
    gateway_url_with_session_context,
    normalize_optional_string,
    safe_effect_execution_id,
)
from agent_sandbox.execution import (
    ExecutionBackendKind,
    ExecutionSpec,
    ExecutionStatusKind,
    build_execution_backend,
)
from agent_sandbox.messaging.agent_prompt_enqueue import enqueue_agent_prompt
from agent_sandbox.models import WakeDispatchRecord, WakeResumePromptPayload
from agent_sandbox.orchestration.host_polling import single_instance_lock
from agent_sandbox.runtime import RuntimeContext
from agent_sandbox.session_expiry import SessionExpiredError, ensure_session_not_expired
from agent_sandbox.worker_directory_context import WorkerDirectoryContextResolver

logger = logging.getLogger(__name__)

__all__ = ["single_instance_lock"]

_ACTIVE_CONTAINER_STATUSES = {
    ExecutionStatusKind.PREPARING.value,
    ExecutionStatusKind.STARTING.value,
    ExecutionStatusKind.RUNNING.value,
    "created",
    "starting",
    "running",
}


@dataclass(frozen=True)
class SessionWakeBinding:
    session_id: str
    vm_id: str | None
    source: str
    directory_path: str | None = None


class HostWakeDispatcher:
    def __init__(
        self,
        *,
        config: GatewayConfig,
        runtime: RuntimeContext,
        agent_control_store: AgentControlStore | None = None,
    ) -> None:
        self.config = config
        self.runtime = runtime
        self.agent_control_store = agent_control_store
        if getattr(config, "enable_directory_scoped_config", False):
            try:
                self.directory_context_resolver = WorkerDirectoryContextResolver(config)
            except Exception:
                logger.debug(
                    "Directory shadow resolver unavailable for wake dispatcher", exc_info=True
                )
                self.directory_context_resolver = None
        else:
            self.directory_context_resolver = None

    def resolve_scoped_config(
        self,
        *,
        session_id: str | None,
        directory_path: str | None,
        source: str,
    ) -> GatewayConfig:
        if self.directory_context_resolver is None:
            return self.config
        try:
            resolution = self.directory_context_resolver.resolve_effective_domain_for_session(
                session_id,
                domain="wake",
                fallback_directory_path=directory_path,
            )
        except Exception:
            logger.warning(
                "Failed to resolve directory-scoped wake config source=%s session_id=%s directory_path=%s",
                source,
                session_id,
                directory_path or "root",
                exc_info=True,
            )
            return self.config
        if resolution.validation_errors:
            logger.warning(
                "Invalid directory-scoped wake config source=%s session_id=%s directory_path=%s errors=%s",
                source,
                session_id,
                resolution.selected_directory.path,
                resolution.validation_errors,
            )
            return self.config
        payload = self.config.model_dump(mode="json")
        payload.update(resolution.effective_config)
        return GatewayConfig.model_validate(payload)

    def is_host_disabled(self, binding: SessionWakeBinding) -> bool:
        if self.agent_control_store is None or not binding.vm_id:
            return False
        return self.agent_control_store.is_disabled(binding.vm_id)

    def resume_binding(
        self,
        binding: SessionWakeBinding,
        *,
        worker_config: GatewayConfig,
        prompt: str,
        resume_mode: str = "same_context",
        dispatch_source: str,
        request_id: str | None = None,
        scheduled_for: datetime | None = None,
        wake_metadata: dict[str, Any] | None = None,
    ) -> WakeDispatchRecord | None:
        if not binding.vm_id:
            return None
        wake_store = getattr(self.runtime, "wake_store", None)
        session = self.runtime.session_repository.get_session(binding.session_id)
        if session is not None:
            try:
                ensure_session_not_expired(session)
            except SessionExpiredError as exc:
                dispatch_record = WakeDispatchRecord(
                    dispatch_id=f"wake-{int(time.time() * 1_000_000)}-{binding.session_id}",
                    request_id=request_id,
                    session_id=binding.session_id,
                    status="failed",
                    resume_prompt=WakeResumePromptPayload(prompt=prompt),
                    resume_mode=resume_mode,
                    scheduled_for=scheduled_for,
                    source=dispatch_source,
                    metadata={
                        "vm_id": binding.vm_id,
                        "source": binding.source,
                        **(wake_metadata or {}),
                    },
                    error=str(exc),
                )
                if wake_store is not None:
                    wake_store.record_dispatch(dispatch_record)
                return dispatch_record
        script_path = worker_config.host_resume_script_path
        if not script_path.exists():
            logger.warning(
                "Resume script missing path=%s session_id=%s vm_id=%s",
                script_path,
                binding.session_id,
                binding.vm_id,
            )
            return None
        cmd = [
            str(script_path),
            "--prompt",
            prompt,
            "--vm-name",
            binding.vm_id,
            "--session-id",
            binding.session_id,
            "--vm-user",
            worker_config.wake_vm_user,
            "--resume-mode",
            resume_mode,
            "--worker-mode",
            "--host-state-file",
            str(worker_config.wake_dispatch_state_path),
        ]
        host_control_state_path = getattr(self.config, "host_agent_control_state_path", None)
        if host_control_state_path is not None:
            cmd.extend(["--host-control-state-path", str(host_control_state_path)])
        run_kwargs: dict[str, Any] = {
            "check": False,
            "capture_output": True,
            "text": True,
            "timeout": worker_config.wake_resume_timeout_seconds,
        }
        agent_model = self._resolve_agent_model_for_binding(binding)
        agent_env = {"AGENT_WEB_SEARCH_MODE": self._web_search_mode_for_session(binding.session_id)}
        if agent_model:
            agent_env["AGENT_MODEL"] = agent_model
        run_kwargs["env"] = os.environ | agent_env
        try:
            completed = subprocess.run(cmd, **run_kwargs)
        except OSError:
            logger.warning(
                "Resume script invocation failed path=%s session_id=%s vm_id=%s",
                script_path,
                binding.session_id,
                binding.vm_id,
                exc_info=True,
            )
            dispatch_record = WakeDispatchRecord(
                dispatch_id=f"wake-{int(time.time() * 1_000_000)}-{binding.session_id}",
                request_id=request_id,
                session_id=binding.session_id,
                status="failed",
                resume_prompt=WakeResumePromptPayload(prompt=prompt),
                resume_mode=resume_mode,
                scheduled_for=scheduled_for,
                source=dispatch_source,
                metadata={
                    "vm_id": binding.vm_id,
                    "source": binding.source,
                    **(wake_metadata or {}),
                },
                error="resume script invocation failed",
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record
        except subprocess.TimeoutExpired:
            logger.warning(
                "Resume script timed out path=%s session_id=%s vm_id=%s timeout_seconds=%.3f",
                script_path,
                binding.session_id,
                binding.vm_id,
                worker_config.wake_resume_timeout_seconds,
            )
            dispatch_record = WakeDispatchRecord(
                dispatch_id=f"wake-{int(time.time() * 1_000_000)}-{binding.session_id}",
                request_id=request_id,
                session_id=binding.session_id,
                status="timed_out",
                resume_prompt=WakeResumePromptPayload(prompt=prompt),
                resume_mode=resume_mode,
                scheduled_for=scheduled_for,
                source=dispatch_source,
                metadata={
                    "vm_id": binding.vm_id,
                    "source": binding.source,
                    "timeout_seconds": worker_config.wake_resume_timeout_seconds,
                    **(wake_metadata or {}),
                },
                error="resume script timed out",
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record

        if completed.returncode == 0:
            dispatch_record = WakeDispatchRecord(
                dispatch_id=f"wake-{int(time.time() * 1_000_000)}-{binding.session_id}",
                request_id=request_id,
                session_id=binding.session_id,
                status="dispatched",
                resume_prompt=WakeResumePromptPayload(prompt=prompt),
                resume_mode=resume_mode,
                scheduled_for=scheduled_for,
                source=dispatch_source,
                metadata={
                    "vm_id": binding.vm_id,
                    "source": binding.source,
                    **(wake_metadata or {}),
                },
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record

        dispatch_record = WakeDispatchRecord(
            dispatch_id=f"wake-{int(time.time() * 1_000_000)}-{binding.session_id}",
            request_id=request_id,
            session_id=binding.session_id,
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt=prompt),
            resume_mode=resume_mode,
            scheduled_for=scheduled_for,
            source=dispatch_source,
            metadata={
                "vm_id": binding.vm_id,
                "source": binding.source,
                "exit_code": completed.returncode,
                "stdout": (completed.stdout or "").strip(),
                "stderr": (completed.stderr or "").strip(),
                **(wake_metadata or {}),
            },
            error=(completed.stderr or "").strip() or f"exit_code={completed.returncode}",
        )
        if wake_store is not None:
            wake_store.record_dispatch(dispatch_record)
        return dispatch_record

    def resume_container_binding(
        self,
        session: Any,
        *,
        worker_config: GatewayConfig,
        prompt: str,
        resume_mode: str | None = "same_context",
        dispatch_source: str,
        request_id: str | None = None,
        scheduled_for: datetime | None = None,
        wake_metadata: dict[str, Any] | None = None,
    ) -> WakeDispatchRecord | None:
        wake_store = getattr(self.runtime, "wake_store", None)
        session_id = normalize_optional_string(getattr(session, "session_id", None))
        if not session_id:
            raise ValueError("container wake dispatch requires a session_id")
        directory_path = normalize_optional_string(getattr(session, "directory_path", None)) or "root"
        target_name = self._container_target_name(session=session, session_id=session_id)
        effective_resume_mode = normalize_optional_string(resume_mode) or "same_context"
        dispatch_id = f"wake-{int(time.time() * 1_000_000)}-{session_id}"
        try:
            ensure_session_not_expired(session)
        except SessionExpiredError as exc:
            dispatch_record = self._build_container_dispatch_record(
                dispatch_id=dispatch_id,
                session_id=session_id,
                request_id=request_id,
                prompt=prompt,
                resume_mode=effective_resume_mode,
                scheduled_for=scheduled_for,
                dispatch_source=dispatch_source,
                wake_metadata=wake_metadata,
                status="failed",
                target_name=target_name,
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                error=str(exc),
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record
        base_config = worker_config.model_copy(
            update={"execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value}
        )
        agent_model = self._resolve_agent_model_for_binding(
            SessionWakeBinding(
                session_id=session_id,
                vm_id=None,
                source=dispatch_source,
                directory_path=directory_path,
            )
        )
        try:
            backend = build_execution_backend(
                base_config,
                admin_service=getattr(self.runtime, "admin_service", None),
            )
        except Exception as exc:
            dispatch_record = self._build_container_dispatch_record(
                dispatch_id=dispatch_id,
                session_id=session_id,
                request_id=request_id,
                prompt=prompt,
                resume_mode=effective_resume_mode,
                scheduled_for=scheduled_for,
                dispatch_source=dispatch_source,
                wake_metadata=wake_metadata,
                status="failed",
                target_name=target_name,
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                error=str(exc) or type(exc).__name__,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record

        if self._container_session_is_busy_for_backend(backend=backend, session_id=session_id):
            queue_key = f"scheduled-wake:{request_id or session_id}"
            queue_metadata: dict[str, str] = {
                "source": dispatch_source,
                "resume_mode": effective_resume_mode,
                "backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                "target_name": target_name,
            }
            if request_id:
                queue_metadata["request_id"] = request_id
            try:
                queued_record = enqueue_agent_prompt(
                    runtime_dir=worker_config.runtime_dir,
                    target=session_id,
                    vm_name=target_name,
                    session_id=session_id,
                    prompt=prompt,
                    model=agent_model,
                    vm_user=worker_config.wake_vm_user,
                    resume_thread_id=None,
                    effect_id=queue_key,
                    resume_mode=effective_resume_mode,
                    queue_key=queue_key,
                    metadata=queue_metadata,
                )
            except Exception as exc:
                dispatch_record = self._build_container_dispatch_record(
                    dispatch_id=dispatch_id,
                    session_id=session_id,
                    request_id=request_id,
                    prompt=prompt,
                    resume_mode=effective_resume_mode,
                    scheduled_for=scheduled_for,
                    dispatch_source=dispatch_source,
                    wake_metadata=wake_metadata,
                    status="failed",
                    target_name=target_name,
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                    error=str(exc) or type(exc).__name__,
                )
                if wake_store is not None:
                    wake_store.record_dispatch(dispatch_record)
                return dispatch_record
            dispatch_record = self._build_container_dispatch_record(
                dispatch_id=dispatch_id,
                session_id=session_id,
                request_id=request_id,
                prompt=prompt,
                resume_mode=effective_resume_mode,
                scheduled_for=scheduled_for,
                dispatch_source=dispatch_source,
                wake_metadata={
                    **(wake_metadata or {}),
                    "backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                    "target_name": target_name,
                    "queue_key": queued_record.queue_key,
                },
                status="queued",
                target_name=target_name,
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record

        execution_id = safe_effect_execution_id("agent", session_id)
        command = container_prompt_command(
            prompt=prompt,
            model=agent_model or str(getattr(session, "model", "") or "").strip() or "gpt-5.4",
            resume_thread_id=None,
            resume_mode=effective_resume_mode,
            mcp_gateway_url=gateway_url_with_session_context(
                container_worker_guest_gateway_url(base_config),
                session_id=session_id,
            ),
            web_search_enabled=bool(getattr(session, "web_search_enabled", False)),
        )
        try:
            handle = backend.start(
                backend.prepare(
                    ExecutionSpec(
                        backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                        target_name=execution_id,
                        session_id=session_id,
                        directory_path=directory_path,
                        command=command,
                        metadata={
                            "source": dispatch_source,
                            "dispatch_id": dispatch_id,
                            "resume_mode": effective_resume_mode,
                        },
                    )
                )
            )
        except Exception as exc:
            dispatch_record = self._build_container_dispatch_record(
                dispatch_id=dispatch_id,
                session_id=session_id,
                request_id=request_id,
                prompt=prompt,
                resume_mode=effective_resume_mode,
                scheduled_for=scheduled_for,
                dispatch_source=dispatch_source,
                wake_metadata=wake_metadata,
                status="failed",
                target_name=execution_id,
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                error=str(exc) or type(exc).__name__,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record

        dispatch_record = self._build_container_dispatch_record(
            dispatch_id=dispatch_id,
            session_id=session_id,
            request_id=request_id,
            prompt=prompt,
            resume_mode=effective_resume_mode,
            scheduled_for=scheduled_for,
            dispatch_source=dispatch_source,
            wake_metadata={
                **(wake_metadata or {}),
                "backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                "target_name": execution_id,
                "execution_id": getattr(handle, "execution_id", execution_id),
            },
            status="dispatched",
            target_name=execution_id,
            backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
        )
        if wake_store is not None:
            wake_store.record_dispatch(dispatch_record)
        return dispatch_record

    def _resolve_agent_model_for_binding(self, binding: SessionWakeBinding) -> str | None:
        session = self.runtime.session_repository.get_session(binding.session_id)
        if session is None:
            return os.environ.get("AGENT_MODEL") or None
        if not hasattr(self.runtime, "directories"):
            session_model = str(getattr(session, "model", "") or "").strip()
            if session_model:
                return session_model
            return os.environ.get("AGENT_MODEL") or None
        try:
            issue_setup = resolve_effective_issue_setup_config(
                config=self.config,
                runtime=self.runtime,
                directory_path=session.directory_path,
                root_dir=Path(__file__).resolve().parents[2],
            )
            return resolve_agent_model_for_manifest(session, issue_setup=issue_setup)
        except ValueError:
            session_model = str(getattr(session, "model", "") or "").strip()
            if session_model:
                return session_model
            return os.environ.get("AGENT_MODEL") or None

    def _web_search_mode_for_session(self, session_id: str) -> str:
        session = self.runtime.session_repository.get_session(session_id)
        if session is None:
            return "disabled"
        return "live" if bool(getattr(session, "web_search_enabled", False)) else "disabled"

    def _build_container_dispatch_record(
        self,
        *,
        dispatch_id: str,
        session_id: str,
        request_id: str | None,
        prompt: str,
        resume_mode: str,
        scheduled_for: datetime | None,
        dispatch_source: str,
        wake_metadata: dict[str, Any] | None,
        status: str,
        target_name: str,
        backend: str,
        error: str | None = None,
    ) -> WakeDispatchRecord:
        payload_metadata: dict[str, Any] = {
            "backend": backend,
            "target_name": target_name,
            "source": dispatch_source,
            **(wake_metadata or {}),
        }
        return WakeDispatchRecord(
            dispatch_id=dispatch_id,
            request_id=request_id,
            session_id=session_id,
            status=status,
            resume_prompt=WakeResumePromptPayload(
                prompt=prompt,
                metadata=dict(wake_metadata or {}),
            ),
            resume_mode=resume_mode,
            scheduled_for=scheduled_for,
            source=dispatch_source,
            metadata=payload_metadata,
            error=error,
        )

    @staticmethod
    def _container_target_name(*, session: Any | None, session_id: str) -> str:
        metadata = getattr(session, "metadata", {}) if session is not None else {}
        if isinstance(metadata, Mapping):
            target_name = normalize_optional_string(metadata.get("target_name"))
            if target_name:
                return target_name
        provider_id = normalize_optional_string(getattr(session, "vm_provider_id", None))
        return provider_id or session_id

    @staticmethod
    def _container_session_is_busy_for_backend(*, backend: Any, session_id: str) -> bool:
        normalized_session_id = normalize_optional_string(session_id)
        if not normalized_session_id:
            return False
        repository = getattr(backend, "repository", None)
        list_active_by_session = getattr(repository, "list_active_by_session", None)
        if callable(list_active_by_session):
            records = list_active_by_session(normalized_session_id, _ACTIVE_CONTAINER_STATUSES)
        else:
            read_all = getattr(repository, "read_all", None)
            if not callable(read_all):
                return False
            records = [
                record
                for record in read_all().values()
                if normalize_optional_string(getattr(record, "session_id", None))
                == normalized_session_id
                and (normalize_optional_string(getattr(record, "last_status", None)) or "").casefold()
                in _ACTIVE_CONTAINER_STATUSES
            ]

        for record in records:
            status = normalize_optional_string(getattr(record, "last_status", None)) or ""
            try:
                inspected = backend.inspect(getattr(record, "execution_id"))
                inspected_status = getattr(inspected, "status", None)
                status = normalize_optional_string(getattr(inspected_status, "value", inspected_status))
            except Exception:
                pass
            if (status or "").casefold() in _ACTIVE_CONTAINER_STATUSES:
                return True
        return False

def probe_agent_dispatch_state(*, session_id: str, vm_id: str, script_path: Path) -> str:
    if not script_path.exists():
        logger.warning(
            "Busy check script missing path=%s session_id=%s vm_id=%s",
            script_path,
            session_id,
            vm_id,
        )
        return "unknown"
    try:
        completed = subprocess.run(
            [str(script_path), session_id, vm_id],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        logger.warning(
            "Busy check script invocation failed path=%s session_id=%s vm_id=%s",
            script_path,
            session_id,
            vm_id,
            exc_info=True,
        )
        return "unknown"
    output = (completed.stdout or "").strip()
    if completed.returncode == 0:
        return "busy"
    if completed.returncode == 1:
        return "idle"
    if completed.returncode == 3:
        logger.info(
            "Busy check status=unavailable session_id=%s vm_id=%s output=%s",
            session_id,
            vm_id,
            output,
        )
        return "unavailable"
    logger.warning(
        "Busy check status=unknown session_id=%s vm_id=%s exit_code=%s output=%s",
        session_id,
        vm_id,
        completed.returncode,
        output,
    )
    return "unknown"


def is_session_in_cooldown(*, path: Path, session_id: str) -> bool:
    state = _load_cooldown_state(path)
    now = time.time()
    expires_at = float(state.get(session_id, 0.0))
    if expires_at <= now:
        if session_id in state:
            state.pop(session_id, None)
            _save_cooldown_state(path, state)
        return False
    return True


def set_session_cooldown(*, path: Path, session_id: str, cooldown_seconds: float) -> None:
    state = _load_cooldown_state(path)
    state[session_id] = max(0.0, time.time() + cooldown_seconds)
    _save_cooldown_state(path, state)


def compute_next_sleep_seconds(
    *,
    poll_interval_seconds: float,
    jitter_seconds: float,
    cooldown_seconds: float,
    activity_detected: int,
) -> float:
    delay = poll_interval_seconds
    if jitter_seconds > 0:
        delay += random.uniform(0.0, jitter_seconds)
    if activity_detected > 0:
        delay = max(delay, cooldown_seconds)
    return max(0.0, delay)


def directory_is_within_scope(directory_path: str | None, scope_directory_path: str) -> bool:
    candidate = (directory_path or "root").strip("/").split("/")
    scope = scope_directory_path.strip("/").split("/")
    return candidate[: len(scope)] == scope


def _load_cooldown_state(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Unable to read cooldown state path=%s", path, exc_info=True)
        return {}
    if not isinstance(payload, dict):
        return {}
    state: dict[str, float] = {}
    for session_id, raw_expires_at in payload.items():
        try:
            state[str(session_id)] = float(raw_expires_at)
        except (TypeError, ValueError):
            continue
    return state


def _save_cooldown_state(path: Path, state: dict[str, float]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)
