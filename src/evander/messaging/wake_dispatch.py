from __future__ import annotations

import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.admin.services.issue_setup_helpers import (
    resolve_agent_model_for_manifest,
    resolve_effective_issue_setup_config,
)
from evander.agent_control import AgentControlStore
from evander.agent_threads import usable_agent_thread_id
from evander.codex_auth import CodexSubscriptionDeferral, legacy_worker_model_capacity_deferral
from evander.config import GatewayConfig
from evander.effects.providers.agent_session import (
    container_codex_turn_spec,
    container_prompt_command,
    container_worker_guest_gateway_url,
    container_worker_image_for_session,
    container_worker_resource_limits_for_session,
    default_agent_model_for_provider,
    default_agent_provider_for_config,
    gateway_url_with_session_context,
    normalize_optional_string,
    safe_effect_execution_id,
)
from evander.execution import (
    ExecutionBackendKind,
    ExecutionSpec,
    ExecutionStatusKind,
    build_execution_backend,
)
from evander.execution.container_worker_client import (
    CodexSubscriptionDeferredClientError,
)
from evander.fast_mode import FastModeValue, resolve_fast_mode
from evander.cyber_access import session_cyber_access_program
from evander.local_inference_capacity import (
    LOCAL_INFERENCE_QUEUE_REASON,
    LocalInferenceCapacityAdmission,
    LocalInferenceCapacityLease,
    local_inference_capacity_store,
    local_inference_execution_is_active,
    maintain_local_inference_launch,
)
from evander.messaging.agent_prompt_enqueue import enqueue_agent_prompt
from evander.messaging.scheduled_wake_queue import scheduled_wake_queue_key
from evander.models import WakeDispatchRecord, WakeResumePromptPayload
from evander.orchestration.host_polling import single_instance_lock
from evander.provider_config import EffectProviderConfig
from evander.reasoning_effort import resolve_reasoning_effort
from evander.runtime import RuntimeContext
from evander.session_auth import session_mcp_auth_token
from evander.session_expiry import SessionExpiredError, ensure_session_not_expired

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

_MODEL_UNAVAILABLE_PATTERNS = (
    re.compile(r"\bmodel[_\s-]?not[_\s-]?found\b", re.IGNORECASE),
    re.compile(r"\binvalid[_\s-]?model\b", re.IGNORECASE),
    re.compile(r"\bunknown model\b", re.IGNORECASE),
    re.compile(r"\bmodel\b.*\bunavailable\b", re.IGNORECASE),
    re.compile(r"\bmodel\b.*\bdoes not exist\b", re.IGNORECASE),
    re.compile(r"\bdoes not have access to model\b", re.IGNORECASE),
)


def _sanitize_operator_text(value: object, *, limit: int = 240) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = " ".join(text.split())
    text = re.sub(r"(?i)\bbearer\s+[^,\s;]+", "Bearer [redacted]", text)
    text = re.sub(
        r"(?i)\b(secret|token|private|password|api[_-]?key|authorization)(\s*[=:]\s*)[^,\s;]+",
        lambda match: f"{match.group(1)}{match.group(2)}[redacted]",
        text,
    )
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _normalize_failure_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _is_model_unavailable_failure(
    *,
    stdout: object = None,
    stderr: object = None,
    exception: object = None,
    reason: object = None,
) -> bool:
    for value in (stdout, stderr, exception, reason):
        text = _normalize_failure_text(value)
        if not text:
            continue
        if any(pattern.search(text) for pattern in _MODEL_UNAVAILABLE_PATTERNS):
            return True
    return False


def _model_unavailable_failure_summary(
    *,
    agent_provider: str | None = None,
    agent_model: str | None = None,
) -> str:
    provider = _sanitize_operator_text(agent_provider, limit=80)
    model = _sanitize_operator_text(agent_model, limit=120)
    if provider and model:
        return (
            f"model unavailable: configured {provider} model '{model}' "
            "is unavailable or not permitted"
        )
    if provider:
        return f"model unavailable: configured {provider} model is unavailable or not permitted"
    if model:
        return f"model unavailable: configured model '{model}' is unavailable or not permitted"
    return "model unavailable: configured model is unavailable or not permitted"


def _build_dispatch_failure_details(
    *,
    backend: str,
    session_id: str,
    vm_id: str | None = None,
    target_name: str | None = None,
    timeout_seconds: float | None = None,
    exit_code: int | None = None,
    stdout: str | None = None,
    stderr: str | None = None,
    exception: BaseException | None = None,
    reason: str | None = None,
    agent_provider: str | None = None,
    agent_model: str | None = None,
) -> tuple[str, dict[str, Any]]:
    metadata: dict[str, Any] = {"backend": backend, "session_id": session_id}
    context_parts = [f"backend={backend}", f"session_id={session_id}"]
    if vm_id:
        metadata["vm_id"] = vm_id
        context_parts.append(f"vm_id={vm_id}")
    if target_name:
        metadata["target_name"] = target_name
        context_parts.append(f"target_name={target_name}")
    if agent_provider is not None:
        metadata["agent_provider"] = agent_provider
    if agent_model is not None:
        metadata["agent_model"] = agent_model
    if timeout_seconds is not None:
        metadata["timeout_seconds"] = timeout_seconds
        context_parts.append(f"timeout_seconds={timeout_seconds:.3f}")
    if exit_code is not None:
        metadata["exit_code"] = exit_code
        context_parts.append(f"exit_code={exit_code}")
    sanitized_stdout = _sanitize_operator_text(stdout, limit=240)
    sanitized_stderr = _sanitize_operator_text(stderr, limit=240)
    model_unavailable = _is_model_unavailable_failure(
        stdout=stdout,
        stderr=stderr,
        exception=exception,
        reason=reason,
    )
    if model_unavailable:
        metadata["failure_kind"] = "model_unavailable"
        summary = _model_unavailable_failure_summary(
            agent_provider=agent_provider,
            agent_model=agent_model,
        )
    elif exception is not None:
        exception_type = type(exception).__name__
        metadata["exception_type"] = exception_type
        message = _sanitize_operator_text(exception, limit=180) or exception_type
        summary = f"{exception_type}: {message}"
    elif reason is not None:
        summary = _sanitize_operator_text(reason, limit=180) or "dispatch failed"
    elif exit_code is not None:
        summary = f"exit_code={exit_code}"
    else:
        summary = "dispatch failed"
    if sanitized_stdout is not None:
        metadata["stdout"] = sanitized_stdout
        context_parts.append(f"stdout={sanitized_stdout}")
    if sanitized_stderr is not None:
        metadata["stderr"] = sanitized_stderr
        context_parts.append(f"stderr={sanitized_stderr}")
    summary = f"{summary} {' '.join(context_parts)}".strip()
    return _sanitize_operator_text(summary, limit=320) or summary, metadata


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

    def resolve_scoped_config(
        self,
        *,
        session_id: str | None,
        directory_path: str | None,
        source: str,
    ) -> GatewayConfig:
        return self._resolve_prompt_provider_config(
            self.config,
            session_id=session_id,
            directory_path=directory_path,
        )

    def _resolve_prompt_provider_config(
        self,
        config: GatewayConfig,
        *,
        session_id: str | None,
        directory_path: str | None,
    ) -> GatewayConfig:
        resolver = getattr(self.runtime, "provider_config", None)
        resolved: Any | None = None
        if resolver is not None and session_id:
            resolve_for_session_id = getattr(resolver, "resolve_for_session_id", None)
            if callable(resolve_for_session_id):
                try:
                    resolved = resolve_for_session_id(session_id)
                except ValueError:
                    pass
        if resolver is not None and resolved is None:
            resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
            if callable(resolve_for_directory):
                try:
                    resolved = resolve_for_directory(directory_path)
                except ValueError:
                    pass
        if resolved is None:
            return config
        resolved_payload = resolved.model_dump(mode="json")
        payload = config.model_dump(mode="json")
        for field_name in EffectProviderConfig.model_fields:
            if field_name in resolved_payload:
                payload[field_name] = resolved_payload[field_name]
        return GatewayConfig.model_validate(payload)

    def _claim_local_inference_capacity(
        self,
        *,
        config: GatewayConfig,
        dispatch_id: str,
        session_id: str,
        model: str,
        execution_backend: str,
        target_name: str,
    ) -> LocalInferenceCapacityAdmission:
        if config.model_provider_mode != "local_openai":
            return LocalInferenceCapacityAdmission(enabled=False, admitted=True)
        capacity_store = local_inference_capacity_store(config.runtime_dir)
        self._reconcile_local_inference_capacity(capacity_store=capacity_store)
        return capacity_store.claim(
            max_concurrency=config.local_openai_max_concurrency,
            owner_id=f"wake:{dispatch_id}",
            model=model,
            session_id=session_id,
            dispatch_id=dispatch_id,
            effect_id=f"wake-prompt:{dispatch_id}",
            execution_backend=execution_backend,
            target_name=target_name,
        )

    def _reconcile_local_inference_capacity(self, *, capacity_store: Any) -> None:
        def is_active(lease: LocalInferenceCapacityLease) -> bool | None:
            if lease.execution_backend != ExecutionBackendKind.CONTAINER_WORKER_VM.value:
                return None
            session_id = str(lease.session_id or "").strip()
            execution_id = str(lease.execution_id or "").strip()
            if not execution_id and session_id:
                execution_id = safe_effect_execution_id("agent", session_id)
            if not session_id or not execution_id:
                return None
            session = self.runtime.session_repository.get_session(session_id)
            if session is None:
                return False
            config = self._resolve_prompt_provider_config(
                self.config,
                session_id=session_id,
                directory_path=getattr(session, "directory_path", None),
            )
            backend = build_execution_backend(
                config.model_copy(
                    update={"execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value}
                ),
                admin_service=getattr(self.runtime, "admin_service", None),
            )
            return local_inference_execution_is_active(backend.inspect(execution_id))

        capacity_store.reconcile_running(is_active)

    @staticmethod
    def _mark_local_inference_running(
        config: GatewayConfig,
        admission: LocalInferenceCapacityAdmission,
        *,
        target_name: str,
        execution_id: str,
    ) -> None:
        if admission.slot_id is not None:
            local_inference_capacity_store(config.runtime_dir).mark_running(
                admission.slot_id,
                target_name=target_name,
                execution_id=execution_id,
            )

    @staticmethod
    def _release_local_inference_capacity(
        config: GatewayConfig,
        admission: LocalInferenceCapacityAdmission,
    ) -> None:
        if admission.slot_id is not None:
            local_inference_capacity_store(config.runtime_dir).release(admission.slot_id)

    @staticmethod
    def _wake_prompt_queue_key(*, request_id: str | None, dispatch_id: str) -> str:
        if request_id:
            return scheduled_wake_queue_key(request_id)
        return f"wake-prompt:{dispatch_id}"

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
        dispatch_id = f"wake-{int(time.time() * 1_000_000)}-{binding.session_id}"
        session = self.runtime.session_repository.get_session(binding.session_id)
        worker_config = self._resolve_prompt_provider_config(
            worker_config,
            session_id=binding.session_id,
            directory_path=binding.directory_path,
        )
        if session is not None:
            try:
                ensure_session_not_expired(session)
            except SessionExpiredError as exc:
                error, failure_metadata = _build_dispatch_failure_details(
                    backend=self.config.execution_backend,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    exception=exc,
                )
                dispatch_record = self._build_dispatch_record(
                    dispatch_id=dispatch_id,
                    request_id=request_id,
                    session_id=binding.session_id,
                    status="failed",
                    resume_prompt=WakeResumePromptPayload(prompt=prompt),
                    resume_mode=resume_mode,
                    scheduled_for=scheduled_for,
                    source=dispatch_source,
                    dispatch_source=dispatch_source,
                    wake_metadata={**(wake_metadata or {}), **failure_metadata},
                    backend=self.config.execution_backend,
                    vm_id=binding.vm_id,
                    error=error,
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
            error, failure_metadata = _build_dispatch_failure_details(
                backend=self.config.execution_backend,
                session_id=binding.session_id,
                vm_id=binding.vm_id,
                reason=f"resume script missing path={script_path}",
            )
            dispatch_record = self._build_dispatch_record(
                dispatch_id=dispatch_id,
                request_id=request_id,
                session_id=binding.session_id,
                status="failed",
                resume_prompt=WakeResumePromptPayload(prompt=prompt),
                resume_mode=resume_mode,
                scheduled_for=scheduled_for,
                source=dispatch_source,
                dispatch_source=dispatch_source,
                wake_metadata={**(wake_metadata or {}), **failure_metadata},
                backend=self.config.execution_backend,
                vm_id=binding.vm_id,
                error=error,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record
        cmd = [
            str(script_path),
            "--prompt",
            prompt,
            "--vm-name",
            binding.vm_id,
            "--session-id",
            binding.session_id,
            "--dispatch-id",
            dispatch_id,
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
        agent_provider = self._resolve_agent_provider_for_binding(
            binding,
            scoped_config=worker_config,
        )
        agent_model = self._resolve_agent_model_for_binding(
            binding,
            scoped_config=worker_config,
            agent_provider=agent_provider,
        )
        resolved_model = agent_model or default_agent_model_for_provider(
            agent_provider,
            worker_config,
        )
        reasoning = resolve_reasoning_effort(
            {},
            session=session,
            harness=agent_provider or default_agent_provider_for_config(worker_config),
            model=resolved_model,
            route=worker_config.model_provider_mode,
        )
        speed = resolve_fast_mode(
            {},
            session=session,
            harness=agent_provider or default_agent_provider_for_config(worker_config),
            route=worker_config.model_provider_mode,
        )
        wake_metadata = {**(wake_metadata or {}), **reasoning.fields(), **speed.fields()}
        capacity_admission = self._claim_local_inference_capacity(
            config=worker_config,
            dispatch_id=dispatch_id,
            session_id=binding.session_id,
            model=resolved_model,
            execution_backend=ExecutionBackendKind.LIBVIRT_VM.value,
            target_name=binding.vm_id,
        )
        if not capacity_admission.admitted:
            queue_key = self._wake_prompt_queue_key(
                request_id=request_id,
                dispatch_id=dispatch_id,
            )
            queue_metadata: dict[str, str] = {
                "source": dispatch_source,
                "resume_mode": resume_mode,
                "backend": ExecutionBackendKind.LIBVIRT_VM.value,
                "target_name": binding.vm_id,
                "wake_dispatch_id": dispatch_id,
                "directory_path": binding.directory_path or "root",
                "queue_reason": LOCAL_INFERENCE_QUEUE_REASON,
            }
            if request_id:
                queue_metadata["request_id"] = request_id
            try:
                queued_record = enqueue_agent_prompt(
                    runtime_dir=worker_config.runtime_dir,
                    target=binding.session_id,
                    vm_name=binding.vm_id,
                    session_id=binding.session_id,
                    prompt=prompt,
                    model=resolved_model,
                    **reasoning.fields(),
                    **speed.fields(),
                    model_provider_mode=worker_config.model_provider_mode,
                    queue_reason=LOCAL_INFERENCE_QUEUE_REASON,
                    vm_user=worker_config.wake_vm_user,
                    dispatch_id=dispatch_id,
                    resume_thread_id=None,
                    effect_id=queue_key,
                    resume_mode=resume_mode,
                    queue_key=queue_key,
                    metadata=queue_metadata,
                    max_retries=worker_config.agent_prompt_queue_max_retries,
                )
            except Exception as exc:
                error, failure_metadata = _build_dispatch_failure_details(
                    backend=ExecutionBackendKind.LIBVIRT_VM.value,
                    session_id=binding.session_id,
                    vm_id=binding.vm_id,
                    exception=exc,
                    agent_provider=agent_provider,
                    agent_model=resolved_model,
                )
                dispatch_record = self._build_dispatch_record(
                    dispatch_id=dispatch_id,
                    request_id=request_id,
                    session_id=binding.session_id,
                    status="failed",
                    resume_prompt=WakeResumePromptPayload(prompt=prompt),
                    resume_mode=resume_mode,
                    scheduled_for=scheduled_for,
                    source=dispatch_source,
                    dispatch_source=dispatch_source,
                    wake_metadata={**queue_metadata, **(wake_metadata or {}), **failure_metadata},
                    backend=ExecutionBackendKind.LIBVIRT_VM.value,
                    vm_id=binding.vm_id,
                    error=error,
                )
                if wake_store is not None:
                    wake_store.record_dispatch(dispatch_record)
                return dispatch_record
            dispatch_record = self._build_dispatch_record(
                dispatch_id=dispatch_id,
                request_id=request_id,
                session_id=binding.session_id,
                status="queued",
                resume_prompt=WakeResumePromptPayload(prompt=prompt),
                resume_mode=resume_mode,
                scheduled_for=scheduled_for,
                source=dispatch_source,
                dispatch_source=dispatch_source,
                wake_metadata={
                    **queue_metadata,
                    **(wake_metadata or {}),
                    "queue_key": queued_record.queue_key,
                    **capacity_admission.output_metadata(),
                },
                backend=ExecutionBackendKind.LIBVIRT_VM.value,
                vm_id=binding.vm_id,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record
        agent_env = {
            "AGENT_WEB_SEARCH_MODE": self._web_search_mode_for_session(binding.session_id),
            "AGENT_REASONING_EFFORT": reasoning.value or "",
            "AGENT_REASONING_EFFORT_SOURCE": reasoning.source,
            "AGENT_FAST_MODE": speed.env_value,
            "AGENT_CYBER_ACCESS_PROGRAM": session_cyber_access_program(session, worker_config) or "",
        }
        if agent_provider:
            agent_env["AGENT_PROVIDER"] = agent_provider
        if agent_model:
            agent_env["AGENT_MODEL"] = agent_model
        run_kwargs["env"] = os.environ | agent_env
        try:
            with maintain_local_inference_launch(
                worker_config.runtime_dir,
                capacity_admission.slot_id,
            ):
                completed = subprocess.run(cmd, **run_kwargs)
        except OSError as exc:
            self._release_local_inference_capacity(worker_config, capacity_admission)
            logger.warning(
                "Resume script invocation failed path=%s session_id=%s vm_id=%s",
                script_path,
                binding.session_id,
                binding.vm_id,
                exc_info=True,
            )
            error, failure_metadata = _build_dispatch_failure_details(
                backend=self.config.execution_backend,
                session_id=binding.session_id,
                vm_id=binding.vm_id,
                exception=exc,
                agent_provider=agent_provider,
                agent_model=agent_model,
            )
            dispatch_record = self._build_dispatch_record(
                dispatch_id=dispatch_id,
                request_id=request_id,
                session_id=binding.session_id,
                status="failed",
                resume_prompt=WakeResumePromptPayload(prompt=prompt),
                resume_mode=resume_mode,
                scheduled_for=scheduled_for,
                source=dispatch_source,
                dispatch_source=dispatch_source,
                wake_metadata={**(wake_metadata or {}), **failure_metadata},
                backend=self.config.execution_backend,
                vm_id=binding.vm_id,
                error=error,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record
        except subprocess.TimeoutExpired:
            self._release_local_inference_capacity(worker_config, capacity_admission)
            logger.warning(
                "Resume script timed out path=%s session_id=%s vm_id=%s timeout_seconds=%.3f",
                script_path,
                binding.session_id,
                binding.vm_id,
                worker_config.wake_resume_timeout_seconds,
            )
            error, failure_metadata = _build_dispatch_failure_details(
                backend=self.config.execution_backend,
                session_id=binding.session_id,
                vm_id=binding.vm_id,
                timeout_seconds=worker_config.wake_resume_timeout_seconds,
                reason="resume script timed out",
                agent_provider=agent_provider,
                agent_model=agent_model,
            )
            dispatch_record = self._build_dispatch_record(
                dispatch_id=dispatch_id,
                request_id=request_id,
                session_id=binding.session_id,
                status="timed_out",
                resume_prompt=WakeResumePromptPayload(prompt=prompt),
                resume_mode=resume_mode,
                scheduled_for=scheduled_for,
                source=dispatch_source,
                dispatch_source=dispatch_source,
                wake_metadata={**(wake_metadata or {}), **failure_metadata},
                backend=self.config.execution_backend,
                vm_id=binding.vm_id,
                error=error,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record

        if completed.returncode == 0:
            self._mark_local_inference_running(
                worker_config,
                capacity_admission,
                target_name=binding.vm_id,
                execution_id=binding.vm_id,
            )
            dispatch_record = self._build_dispatch_record(
                dispatch_id=dispatch_id,
                request_id=request_id,
                session_id=binding.session_id,
                status="dispatched",
                resume_prompt=WakeResumePromptPayload(prompt=prompt),
                resume_mode=resume_mode,
                scheduled_for=scheduled_for,
                source=dispatch_source,
                dispatch_source=dispatch_source,
                wake_metadata={
                    **(wake_metadata or {}),
                    **capacity_admission.output_metadata(),
                },
                backend=self.config.execution_backend,
                vm_id=binding.vm_id,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            watcher_kwargs: dict[str, Any] = {
                "vm_name": binding.vm_id,
                "dispatch_id": dispatch_id,
                "vm_user": worker_config.wake_vm_user,
                "session_id": binding.session_id,
                "directory_path": binding.directory_path,
            }
            if capacity_admission.slot_id is not None:
                watcher_kwargs["local_inference_slot_id"] = capacity_admission.slot_id
            self._spawn_agent_completion_watcher(
                **watcher_kwargs,
            )
            return dispatch_record

        self._release_local_inference_capacity(worker_config, capacity_admission)
        error, failure_metadata = _build_dispatch_failure_details(
            backend=self.config.execution_backend,
            session_id=binding.session_id,
            vm_id=binding.vm_id,
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            agent_provider=agent_provider,
            agent_model=agent_model,
        )
        dispatch_record = self._build_dispatch_record(
            dispatch_id=dispatch_id,
            request_id=request_id,
            session_id=binding.session_id,
            status="failed",
            resume_prompt=WakeResumePromptPayload(prompt=prompt),
            resume_mode=resume_mode,
            scheduled_for=scheduled_for,
            source=dispatch_source,
            dispatch_source=dispatch_source,
            wake_metadata={**(wake_metadata or {}), **failure_metadata},
            backend=self.config.execution_backend,
            vm_id=binding.vm_id,
            error=error,
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
        directory_path = (
            normalize_optional_string(getattr(session, "directory_path", None)) or "root"
        )
        target_name = self._container_target_name(session=session, session_id=session_id)
        effective_resume_mode = normalize_optional_string(resume_mode) or "same_context"
        dispatch_id = f"wake-{int(time.time() * 1_000_000)}-{session_id}"
        agent_provider: str | None = None
        agent_model: str | None = None
        try:
            ensure_session_not_expired(session)
        except SessionExpiredError as exc:
            error, failure_metadata = _build_dispatch_failure_details(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                session_id=session_id,
                target_name=target_name,
                exception=exc,
                agent_provider=agent_provider,
                agent_model=agent_model,
            )
            dispatch_record = self._build_dispatch_record(
                dispatch_id=dispatch_id,
                session_id=session_id,
                request_id=request_id,
                prompt=prompt,
                resume_mode=effective_resume_mode,
                scheduled_for=scheduled_for,
                dispatch_source=dispatch_source,
                wake_metadata={**(wake_metadata or {}), **failure_metadata},
                status="failed",
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                target_name=target_name,
                error=error,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record
        base_config = self._resolve_prompt_provider_config(
            worker_config,
            session_id=session_id,
            directory_path=directory_path,
        ).model_copy(update={"execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value})
        binding_for_resolution = SessionWakeBinding(
            session_id=session_id,
            vm_id=None,
            source=dispatch_source,
            directory_path=directory_path,
        )
        agent_provider = self._resolve_agent_provider_for_binding(
            binding_for_resolution,
            scoped_config=base_config,
        )
        agent_model = self._resolve_agent_model_for_binding(
            binding_for_resolution,
            scoped_config=base_config,
            agent_provider=agent_provider,
        )
        resolved_model = agent_model or str(getattr(session, "model", "") or "").strip()
        if not resolved_model:
            resolved_model = default_agent_model_for_provider(agent_provider, base_config)
        try:
            reasoning = resolve_reasoning_effort(
                {},
                session=session,
                harness=agent_provider or default_agent_provider_for_config(base_config),
                model=resolved_model,
                route=base_config.model_provider_mode,
            )
            speed = resolve_fast_mode(
                {},
                session=session,
                harness=agent_provider or default_agent_provider_for_config(base_config),
                route=base_config.model_provider_mode,
            )
            wake_metadata = {**(wake_metadata or {}), **reasoning.fields(), **speed.fields()}
            backend = build_execution_backend(
                base_config,
                admin_service=getattr(self.runtime, "admin_service", None),
            )
        except Exception as exc:
            error, failure_metadata = _build_dispatch_failure_details(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                session_id=session_id,
                target_name=target_name,
                exception=exc,
                agent_provider=agent_provider,
                agent_model=resolved_model,
            )
            dispatch_record = self._build_dispatch_record(
                dispatch_id=dispatch_id,
                session_id=session_id,
                request_id=request_id,
                prompt=prompt,
                resume_mode=effective_resume_mode,
                scheduled_for=scheduled_for,
                dispatch_source=dispatch_source,
                wake_metadata={**(wake_metadata or {}), **failure_metadata},
                status="failed",
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                target_name=target_name,
                error=error,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record

        session_is_busy = self._container_session_is_busy_for_backend(
            backend=backend,
            session_id=session_id,
        )
        capacity_admission = LocalInferenceCapacityAdmission(enabled=False, admitted=True)
        if not session_is_busy:
            capacity_admission = self._claim_local_inference_capacity(
                config=base_config,
                dispatch_id=dispatch_id,
                session_id=session_id,
                model=resolved_model,
                execution_backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                target_name=target_name,
            )
        if session_is_busy or not capacity_admission.admitted:
            queue_key = self._wake_prompt_queue_key(
                request_id=request_id,
                dispatch_id=dispatch_id,
            )
            queue_reason = LOCAL_INFERENCE_QUEUE_REASON if not capacity_admission.admitted else None
            queue_metadata: dict[str, str] = {
                "source": dispatch_source,
                "resume_mode": effective_resume_mode,
                "backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                "target_name": target_name,
                "wake_dispatch_id": dispatch_id,
                "directory_path": directory_path,
            }
            if agent_provider:
                queue_metadata["agent_provider"] = agent_provider
            if request_id:
                queue_metadata["request_id"] = request_id
            if queue_reason:
                queue_metadata["queue_reason"] = queue_reason
            try:
                queued_record = enqueue_agent_prompt(
                    runtime_dir=base_config.runtime_dir,
                    target=session_id,
                    vm_name=target_name,
                    session_id=session_id,
                    prompt=prompt,
                    model=resolved_model,
                    **reasoning.fields(),
                    **speed.fields(),
                    model_provider_mode=base_config.model_provider_mode,
                    queue_reason=queue_reason,
                    vm_user=base_config.wake_vm_user,
                    dispatch_id=dispatch_id,
                    resume_thread_id=None,
                    effect_id=queue_key,
                    resume_mode=effective_resume_mode,
                    queue_key=queue_key,
                    metadata=queue_metadata,
                    max_retries=int(getattr(base_config, "agent_prompt_queue_max_retries", 3) or 3),
                )
            except Exception as exc:
                error, failure_metadata = _build_dispatch_failure_details(
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                    session_id=session_id,
                    target_name=target_name,
                    exception=exc,
                    agent_provider=agent_provider,
                    agent_model=resolved_model,
                )
                dispatch_record = self._build_dispatch_record(
                    dispatch_id=dispatch_id,
                    session_id=session_id,
                    request_id=request_id,
                    prompt=prompt,
                    resume_mode=effective_resume_mode,
                    scheduled_for=scheduled_for,
                    dispatch_source=dispatch_source,
                    wake_metadata={**queue_metadata, **(wake_metadata or {}), **failure_metadata},
                    status="failed",
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                    target_name=target_name,
                    error=error,
                )
                if wake_store is not None:
                    wake_store.record_dispatch(dispatch_record)
                return dispatch_record
            dispatch_record = self._build_dispatch_record(
                dispatch_id=dispatch_id,
                session_id=session_id,
                request_id=request_id,
                prompt=prompt,
                resume_mode=effective_resume_mode,
                scheduled_for=scheduled_for,
                dispatch_source=dispatch_source,
                wake_metadata={
                    **(wake_metadata or {}),
                    **queue_metadata,
                    "queue_key": queued_record.queue_key,
                    **capacity_admission.output_metadata(),
                },
                status="queued",
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                target_name=target_name,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record

        execution_id = safe_effect_execution_id("agent", session_id)
        mcp_gateway_url = gateway_url_with_session_context(
            container_worker_guest_gateway_url(base_config),
            session_id=session_id,
            mcp_session_token=session_mcp_auth_token(session),
        )
        command = container_prompt_command(
            prompt=prompt,
            model=resolved_model,
            **reasoning.fields(),
            **speed.fields(),
            resume_thread_id=None,
            session=session,
            agent_provider=agent_provider,
            resume_mode=effective_resume_mode,
            mcp_gateway_url=mcp_gateway_url,
            web_search_enabled=bool(getattr(session, "web_search_enabled", False)),
        )
        codex_turn = container_codex_turn_spec(
            prompt=prompt,
            model=resolved_model,
            **reasoning.fields(),
            **speed.fields(),
            resume_thread_id=None,
            session=session,
            config=base_config,
            agent_provider=agent_provider,
            resume_mode=effective_resume_mode,
            mcp_gateway_url=mcp_gateway_url,
            web_search_enabled=bool(getattr(session, "web_search_enabled", False)),
        )
        try:
            with maintain_local_inference_launch(
                base_config.runtime_dir,
                capacity_admission.slot_id,
            ):
                handle = backend.start(
                    backend.prepare(
                        ExecutionSpec(
                            backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                            target_name=execution_id,
                            session_id=session_id,
                            directory_path=directory_path,
                            image=container_worker_image_for_session(session, base_config),
                            resource_limits=container_worker_resource_limits_for_session(session),
                            command=command,
                            codex_turn=codex_turn,
                            metadata={
                                "source": dispatch_source,
                                "dispatch_id": dispatch_id,
                                "resume_mode": effective_resume_mode,
                                "agent_provider": agent_provider,
                                "model": resolved_model,
                                **reasoning.fields(),
                                **speed.fields(),
                                "model_provider_mode": base_config.model_provider_mode,
                                **(
                                    {"codex_auth_mode": codex_turn.auth_mode.value}
                                    if codex_turn is not None
                                    else {}
                                ),
                                **(
                                    {
                                        "subscription_profile_id": (
                                            codex_turn.subscription_profile_id
                                        )
                                    }
                                    if codex_turn is not None
                                    and codex_turn.subscription_profile_id is not None
                                    else {}
                                ),
                            },
                        )
                    )
                )
        except Exception as exc:
            self._release_local_inference_capacity(base_config, capacity_admission)
            if isinstance(exc, CodexSubscriptionDeferredClientError):
                queue_key = self._wake_prompt_queue_key(
                    request_id=request_id,
                    dispatch_id=dispatch_id,
                )
                queue_metadata = {
                    "source": dispatch_source,
                    "resume_mode": effective_resume_mode,
                    "backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                    "target_name": target_name,
                    "wake_dispatch_id": dispatch_id,
                    "directory_path": directory_path,
                    "capacity_deferral": "true",
                }
                if request_id:
                    queue_metadata["request_id"] = request_id
                queued_record = enqueue_agent_prompt(
                    runtime_dir=base_config.runtime_dir,
                    target=session_id,
                    vm_name=target_name,
                    session_id=session_id,
                    prompt=prompt,
                    model=resolved_model,
                    **reasoning.fields(),
                    **speed.fields(),
                    model_provider_mode=base_config.model_provider_mode,
                    queue_reason=exc.deferral.queue_reason,
                    subscription_profile_id=exc.deferral.profile_id,
                    retry_not_before=exc.deferral.retry_at,
                    capacity_deferral=True,
                    vm_user=base_config.wake_vm_user,
                    dispatch_id=dispatch_id,
                    resume_thread_id=None,
                    effect_id=queue_key,
                    resume_mode=effective_resume_mode,
                    queue_key=queue_key,
                    metadata=queue_metadata,
                )
                dispatch_record = self._build_dispatch_record(
                    dispatch_id=dispatch_id,
                    session_id=session_id,
                    request_id=request_id,
                    prompt=prompt,
                    resume_mode=effective_resume_mode,
                    scheduled_for=scheduled_for,
                    dispatch_source=dispatch_source,
                    wake_metadata={
                        **(wake_metadata or {}),
                        **queue_metadata,
                        "queue_key": queued_record.queue_key,
                        "queue_reason": queued_record.queue_reason,
                        "subscription_profile_id": queued_record.subscription_profile_id,
                        "retry_not_before": (
                            queued_record.retry_not_before.isoformat()
                            if queued_record.retry_not_before is not None
                            else None
                        ),
                    },
                    status="queued",
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                    target_name=target_name,
                )
                if wake_store is not None:
                    wake_store.record_dispatch(dispatch_record)
                return dispatch_record
            error, failure_metadata = _build_dispatch_failure_details(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                session_id=session_id,
                target_name=execution_id,
                exception=exc,
                agent_provider=agent_provider,
                agent_model=resolved_model,
            )
            dispatch_record = self._build_dispatch_record(
                dispatch_id=dispatch_id,
                session_id=session_id,
                request_id=request_id,
                prompt=prompt,
                resume_mode=effective_resume_mode,
                scheduled_for=scheduled_for,
                dispatch_source=dispatch_source,
                wake_metadata={**(wake_metadata or {}), **failure_metadata},
                status="failed",
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                target_name=execution_id,
                error=error,
            )
            if wake_store is not None:
                wake_store.record_dispatch(dispatch_record)
            return dispatch_record

        self._mark_local_inference_running(
            base_config,
            capacity_admission,
            target_name=target_name,
            execution_id=handle.execution_id,
        )
        dispatch_record = self._build_dispatch_record(
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
                **capacity_admission.output_metadata(),
            },
            status="dispatched",
            backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            target_name=execution_id,
        )
        if wake_store is not None:
            wake_store.record_dispatch(dispatch_record)
        monitor_kwargs: dict[str, Any] = {
            "backend": backend,
            "execution_id": getattr(handle, "execution_id", execution_id),
            "dispatch_id": dispatch_id,
            "session_id": session_id,
            "directory_path": directory_path,
            "vm_user": base_config.wake_vm_user,
            "prompt": prompt,
            "model": resolved_model,
            **reasoning.fields(),
            **speed.fields(),
            "model_provider_mode": base_config.model_provider_mode,
            "request_id": request_id,
            "resume_mode": effective_resume_mode,
            "target_name": target_name,
            "dispatch_source": dispatch_source,
            "scheduled_for": scheduled_for,
            "wake_metadata": wake_metadata,
            "runtime_dir": base_config.runtime_dir,
        }
        if capacity_admission.slot_id is not None:
            monitor_kwargs["local_inference_slot_id"] = capacity_admission.slot_id
        self._spawn_container_completion_monitor(**monitor_kwargs)
        return dispatch_record

    def _spawn_agent_completion_watcher(
        self,
        *,
        vm_name: str,
        dispatch_id: str,
        vm_user: str,
        session_id: str | None,
        directory_path: str | None,
        local_inference_slot_id: str | None = None,
    ) -> None:
        runtime_dir = self.config.runtime_dir
        watcher_cmd = [
            sys.executable,
            "-m",
            "evander.daemons.agent_completion_watcher",
            "--runtime-dir",
            str(runtime_dir),
            "--vm-name",
            vm_name,
            "--dispatch-id",
            dispatch_id,
            "--vm-user",
            vm_user,
            "--poll-interval-seconds",
            "1.0",
            "--timeout-seconds",
            "172800",
            "--effect-lock-path",
            str(runtime_dir / "effect-orchestrator.lock"),
            "--orchestration-store-path",
            str(runtime_dir / "orchestration.db"),
        ]
        if session_id:
            watcher_cmd.extend(["--session-id", session_id])
        if directory_path:
            watcher_cmd.extend(["--directory-path", directory_path])
        if local_inference_slot_id:
            watcher_cmd.extend(["--local-inference-slot-id", local_inference_slot_id])
        try:
            subprocess.Popen(
                watcher_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(runtime_dir.parent),
                env=os.environ.copy(),
                text=True,
            )
        except Exception:
            logger.warning(
                "Failed to spawn wake completion watcher vm_name=%s dispatch_id=%s",
                vm_name,
                dispatch_id,
                exc_info=True,
            )

    def _spawn_container_completion_monitor(
        self,
        *,
        backend: Any,
        execution_id: str,
        dispatch_id: str,
        session_id: str,
        directory_path: str,
        vm_user: str,
        prompt: str = "",
        model: str | None = None,
        reasoning_effort: str | None = None,
        fast_mode: FastModeValue | None = None,
        reasoning_effort_source: str | None = None,
        fast_mode_source: str | None = None,
        model_provider_mode: str | None = None,
        request_id: str | None = None,
        resume_mode: str = "same_context",
        target_name: str | None = None,
        dispatch_source: str = "wake",
        scheduled_for: datetime | None = None,
        wake_metadata: dict[str, Any] | None = None,
        runtime_dir: Path | None = None,
        local_inference_slot_id: str | None = None,
    ) -> None:
        thread = threading.Thread(
            target=self._monitor_container_execution_completion,
            kwargs={
                "backend": backend,
                "execution_id": execution_id,
                "dispatch_id": dispatch_id,
                "session_id": session_id,
                "directory_path": directory_path,
                "vm_user": vm_user,
                "prompt": prompt,
                "model": model,
                "reasoning_effort": reasoning_effort,
                "fast_mode": fast_mode,
                "reasoning_effort_source": reasoning_effort_source,
                "fast_mode_source": fast_mode_source,
                "model_provider_mode": model_provider_mode,
                "request_id": request_id,
                "resume_mode": resume_mode,
                "target_name": target_name,
                "dispatch_source": dispatch_source,
                "scheduled_for": scheduled_for,
                "wake_metadata": wake_metadata,
                "runtime_dir": runtime_dir,
                "local_inference_slot_id": local_inference_slot_id,
            },
            daemon=True,
            name=f"wake-container-monitor:{session_id}:{dispatch_id}",
        )
        thread.start()

    def _monitor_container_execution_completion(
        self,
        *,
        backend: Any,
        execution_id: str,
        dispatch_id: str,
        session_id: str,
        directory_path: str,
        vm_user: str,
        prompt: str = "",
        model: str | None = None,
        reasoning_effort: str | None = None,
        fast_mode: FastModeValue | None = None,
        reasoning_effort_source: str | None = None,
        fast_mode_source: str | None = None,
        model_provider_mode: str | None = None,
        request_id: str | None = None,
        resume_mode: str = "same_context",
        target_name: str | None = None,
        dispatch_source: str = "wake",
        scheduled_for: datetime | None = None,
        wake_metadata: dict[str, Any] | None = None,
        runtime_dir: Path | None = None,
        local_inference_slot_id: str | None = None,
    ) -> None:
        effective_runtime_dir = runtime_dir or self.config.runtime_dir
        effective_target_name = target_name or execution_id
        wake_store = getattr(self.runtime, "wake_store", None)
        mark_completed = getattr(wake_store, "mark_completed_by_dispatch_id", None)
        timeout_seconds = float(
            getattr(self.config, "container_worker_job_timeout_seconds", 1800.0)
        )
        poll_interval_seconds = 1.0
        started_at = time.monotonic()
        while time.monotonic() - started_at < timeout_seconds:
            try:
                status = backend.inspect(execution_id)
            except Exception:
                return
            status_value = getattr(status, "status", None)
            normalized_status = str(getattr(status_value, "value", status_value) or "").strip()
            if normalized_status.casefold() in _ACTIVE_CONTAINER_STATUSES:
                time.sleep(poll_interval_seconds)
                continue
            if normalized_status not in {
                ExecutionStatusKind.STOPPED.value,
                ExecutionStatusKind.FAILED.value,
                ExecutionStatusKind.DESTROYED.value,
            }:
                return
            metadata = getattr(status, "metadata", None)
            raw_deferral = (
                metadata.get("codex_subscription_deferral")
                if isinstance(metadata, Mapping)
                else None
            )
            if (
                raw_deferral is None and isinstance(metadata, Mapping)
                and metadata.get("codex_auth_mode") == "chatgpt_subscription"
                and metadata.get("codex_subscription_exit_code") in (1, "1")
            ):
                try:
                    logs = backend.get_log_snapshot(execution_id, lines=40, mode="raw")
                except Exception:
                    logs = ""
                legacy_deferral = legacy_worker_model_capacity_deferral(metadata, logs)
                if legacy_deferral is not None:
                    raw_deferral = legacy_deferral.model_dump()
            if isinstance(raw_deferral, Mapping) and prompt:
                try:
                    deferral = CodexSubscriptionDeferral.model_validate(raw_deferral)
                except ValueError:
                    deferral = None
                if deferral is not None:
                    queue_key = self._wake_prompt_queue_key(
                        request_id=request_id,
                        dispatch_id=dispatch_id,
                    )
                    queue_metadata = {
                        "source": dispatch_source,
                        "resume_mode": resume_mode,
                        "backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                        "target_name": effective_target_name,
                        "wake_dispatch_id": dispatch_id,
                        "directory_path": directory_path,
                        "capacity_deferral": "true",
                    }
                    if request_id:
                        queue_metadata["request_id"] = request_id
                    resume_thread_id = None
                    retry_resume_mode = resume_mode
                    if deferral.reason == "model_capacity":
                        queue_metadata["model_capacity_failed_execution_id"] = execution_id
                        resume_thread_id = usable_agent_thread_id(
                            metadata.get("codex_subscription_thread_id"), execution_id, dispatch_id
                        )
                        if resume_thread_id:
                            retry_resume_mode = "same_context"
                    queued_record = enqueue_agent_prompt(
                        runtime_dir=effective_runtime_dir,
                        target=session_id,
                        vm_name=effective_target_name,
                        session_id=session_id,
                        prompt=prompt,
                        model=model,
                        reasoning_effort=reasoning_effort,
                        fast_mode=fast_mode,
                        reasoning_effort_source=reasoning_effort_source,
                        fast_mode_source=fast_mode_source,
                        model_provider_mode=(
                            model_provider_mode or self.config.model_provider_mode
                        ),
                        queue_reason=deferral.queue_reason,
                        subscription_profile_id=deferral.profile_id,
                        retry_not_before=deferral.retry_at,
                        capacity_deferral=True,
                        vm_user=vm_user,
                        dispatch_id=dispatch_id,
                        resume_thread_id=resume_thread_id,
                        effect_id=queue_key,
                        resume_mode=retry_resume_mode,
                        queue_key=queue_key,
                        metadata=queue_metadata,
                    )
                    queued_dispatch = self._build_dispatch_record(
                        dispatch_id=dispatch_id,
                        session_id=session_id,
                        request_id=request_id,
                        prompt=prompt,
                        resume_mode=resume_mode,
                        scheduled_for=scheduled_for,
                        dispatch_source=dispatch_source,
                        wake_metadata={
                            **(wake_metadata or {}),
                            **queue_metadata,
                            "queue_key": queued_record.queue_key,
                            "queue_reason": queued_record.queue_reason,
                            "subscription_profile_id": (queued_record.subscription_profile_id),
                            "retry_not_before": (
                                queued_record.retry_not_before.isoformat()
                                if queued_record.retry_not_before is not None
                                else None
                            ),
                        },
                        status="queued",
                        backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                        target_name=effective_target_name,
                    )
                    record_dispatch = getattr(wake_store, "record_dispatch", None)
                    if callable(record_dispatch):
                        record_dispatch(queued_dispatch)
                    if normalized_status != ExecutionStatusKind.DESTROYED.value:
                        try:
                            backend.destroy(execution_id)
                        except Exception:
                            pass
                    if local_inference_slot_id:
                        local_inference_capacity_store(effective_runtime_dir).release(
                            local_inference_slot_id
                        )
                    return
            if normalized_status != ExecutionStatusKind.DESTROYED.value:
                try:
                    backend.destroy(execution_id)
                except Exception:
                    pass
            completed_at = datetime.now(UTC)
            evidence = {
                "trigger_event_id": f"container:{execution_id}:{completed_at.isoformat()}",
                "trigger_type": "trigger:agent:run:completed",
                "dispatch_id": dispatch_id,
                "session_id": session_id,
                "directory_path": directory_path,
                "vm_user": vm_user,
                "agent_state": "idle",
                "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
            }
            if callable(mark_completed):
                try:
                    mark_completed(
                        dispatch_id,
                        completed_at=completed_at,
                        evidence=evidence,
                    )
                except Exception:
                    logger.warning(
                        "Failed to mark container wake completed dispatch_id=%s execution_id=%s",
                        dispatch_id,
                        execution_id,
                        exc_info=True,
                    )
            if local_inference_slot_id:
                local_inference_capacity_store(effective_runtime_dir).release(
                    local_inference_slot_id
                )
            return
        logger.warning(
            "Timed out waiting for container wake completion dispatch_id=%s execution_id=%s",
            dispatch_id,
            execution_id,
        )

    def _resolve_agent_provider_for_binding(
        self,
        binding: SessionWakeBinding,
        *,
        scoped_config: GatewayConfig | None = None,
    ) -> str:
        session = self.runtime.session_repository.get_session(binding.session_id)
        if session is None:
            return default_agent_provider_for_config(scoped_config)
        metadata = getattr(session, "metadata", {})
        if isinstance(metadata, Mapping):
            candidate = normalize_optional_string(
                metadata.get("agent_provider") or metadata.get("agent_harness")
            )
            if candidate:
                return candidate
        return default_agent_provider_for_config(scoped_config)

    def _resolve_agent_model_for_binding(
        self,
        binding: SessionWakeBinding,
        *,
        scoped_config: GatewayConfig | None = None,
        agent_provider: str | None = None,
    ) -> str | None:
        session = self.runtime.session_repository.get_session(binding.session_id)
        provider = normalize_optional_string(
            agent_provider
        ) or self._resolve_agent_provider_for_binding(
            binding,
            scoped_config=scoped_config,
        )
        if session is None:
            return os.environ.get("AGENT_MODEL") or default_agent_model_for_provider(
                provider,
                scoped_config,
            )
        session_model = str(getattr(session, "model", "") or "").strip()
        if session_model:
            return session_model
        if not hasattr(self.runtime, "directories"):
            return default_agent_model_for_provider(provider, scoped_config)
        try:
            issue_setup = resolve_effective_issue_setup_config(
                config=self.config,
                runtime=self.runtime,
                directory_path=session.directory_path,
                root_dir=Path(__file__).resolve().parents[2],
            )
            return resolve_agent_model_for_manifest(session, issue_setup=issue_setup)
        except ValueError:
            return default_agent_model_for_provider(provider, scoped_config)

    def _web_search_mode_for_session(self, session_id: str) -> str:
        session = self.runtime.session_repository.get_session(session_id)
        if session is None:
            return "disabled"
        return "live" if bool(getattr(session, "web_search_enabled", False)) else "disabled"

    def _build_dispatch_record(
        self,
        *,
        dispatch_id: str,
        session_id: str,
        request_id: str | None,
        resume_prompt: WakeResumePromptPayload | None = None,
        prompt: str | None = None,
        resume_mode: str,
        scheduled_for: datetime | None,
        dispatch_source: str,
        wake_metadata: dict[str, Any] | None,
        status: str,
        backend: str,
        source: str | None = None,
        vm_id: str | None = None,
        target_name: str | None = None,
        error: str | None = None,
    ) -> WakeDispatchRecord:
        effective_prompt = prompt or ""
        effective_resume_prompt = resume_prompt or WakeResumePromptPayload(prompt=effective_prompt)
        if wake_metadata:
            effective_resume_prompt = WakeResumePromptPayload(
                prompt=effective_resume_prompt.prompt,
                metadata={**effective_resume_prompt.metadata, **wake_metadata},
            )
        payload_metadata: dict[str, Any] = {
            "backend": backend,
            "source": source or dispatch_source,
            **(wake_metadata or {}),
        }
        if vm_id is not None:
            payload_metadata["vm_id"] = vm_id
        if target_name is not None:
            payload_metadata["target_name"] = target_name
        return WakeDispatchRecord(
            dispatch_id=dispatch_id,
            request_id=request_id,
            session_id=session_id,
            status=status,
            resume_prompt=effective_resume_prompt,
            resume_mode=resume_mode,
            scheduled_for=scheduled_for,
            source=source or dispatch_source,
            metadata=payload_metadata,
            error=error,
        )

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
        return self._build_dispatch_record(
            dispatch_id=dispatch_id,
            session_id=session_id,
            request_id=request_id,
            resume_prompt=WakeResumePromptPayload(prompt=prompt),
            prompt=prompt,
            resume_mode=resume_mode,
            scheduled_for=scheduled_for,
            dispatch_source=dispatch_source,
            wake_metadata=wake_metadata,
            status=status,
            backend=backend,
            target_name=target_name,
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
                and (
                    normalize_optional_string(getattr(record, "last_status", None)) or ""
                ).casefold()
                in _ACTIVE_CONTAINER_STATUSES
            ]

        for record in records:
            status = normalize_optional_string(getattr(record, "last_status", None)) or ""
            try:
                inspected = backend.inspect(getattr(record, "execution_id"))
                inspected_status = getattr(inspected, "status", None)
                status = normalize_optional_string(
                    getattr(inspected_status, "value", inspected_status)
                )
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
