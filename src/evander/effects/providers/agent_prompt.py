from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from evander.agent_context_retry import (
    CONTEXT_EXHAUSTION_FAILURE_KIND,
    CONTEXT_RETRY_MODE_REDUCED,
    build_reduced_context_retry_prompt,
    context_exhaustion_reason,
    context_retry_enabled,
    context_retry_max_attempts,
    context_retry_target_chars,
)
from evander.config import GatewayConfig
from evander.codex_auth import CodexSubscriptionDeferral
from evander.events.models import NODE_OUTPUTS_CONTEXT_KEY, TriggerEvent
from evander.events.provider_models import AgentPromptEffectDefinition
from evander.execution import (
    ExecutionBackendKind,
    ExecutionSpec,
    ExecutionStatusKind,
    build_execution_backend,
)
from evander.execution.container_worker_client import (
    CodexSubscriptionDeferredClientError,
)
from evander.local_inference_capacity import (
    LOCAL_INFERENCE_QUEUE_REASON,
    LocalInferenceCapacityAdmission,
    LocalInferenceCapacityLease,
    local_inference_capacity_store,
    local_inference_execution_is_active,
    local_inference_vm_lease_is_active,
    maintain_local_inference_launch,
)
from evander.messaging.agent_prompt_enqueue import (
    enqueue_agent_prompt,
    workflow_context_from_sources,
    workflow_for_execution_ids_from_sources,
)
from evander.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
    queued_agent_prompt_dispatch_id,
)
from evander.model_usage.runtime import (
    clear_model_usage_correlation_context,
    register_model_usage_correlation_context,
    resolve_runtime_dir,
)
from evander.orchestration.effect_extensions import list_vm_summaries_for_agent_reconciliation
from evander.session_auth import session_mcp_auth_token
from evander.triggers.queue import TriggerQueueRecord
from evander.worker.security import sanitize_text
from evander.workflow.lifecycle_ports import (
    AGENT_ENQUEUE_PROMPT_EFFECT_TYPE,
    AGENT_PROMPT_COMPLETED_PORT,
    AGENT_PROMPT_ERROR_PORT,
    AGENT_PROMPT_STARTED_PORT,
)
from evander.workflow.validation import NodeCapability

from .agent_session import (
    container_backend,
    container_codex_turn_spec,
    container_prompt_command,
    container_thread_id_from_logs,
    container_worker_guest_gateway_url,
    container_worker_image_for_session,
    container_worker_resource_limits_for_session,
    default_agent_model_for_provider,
    default_agent_provider_for_config,
    effective_config_for_session,
    gateway_url_with_session_context,
    require_container_session_id,
    resolve_active_session_if_available,
    safe_effect_execution_id,
    selected_execution_backend_for_target,
)
from .base import EffectProviderBase, EffectProviderExecutionError
from .resume_thread_metadata import (
    normalize_resume_thread_metadata_collection,
    resume_thread_id_from_metadata,
    resume_thread_metadata_patch,
)
from .resume_thread_metadata import (
    resume_thread_metadata_key as normalize_resume_thread_metadata_key,
)

logger = logging.getLogger(__name__)

_AWAIT_AGENT_STALE_GRACE_SECONDS = 120
_DEFAULT_CONTAINER_WORKER_JOB_TIMEOUT_SECONDS = 1800.0
_DEFAULT_CONTAINER_WORKER_ACTIVE_TOOL_TIMEOUT_SECONDS = 300.0
_BRANCH_PORT_PAYLOAD_KEY = "__branch_port"
_FOR_EXECUTION_IDS_PAYLOAD_KEY = "__for_execution_ids"
CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY = "interrupt_in_progress"
_CONTAINER_CODEX_NO_FINAL_MESSAGE_REASON = "codex_no_final_message"
_CONTAINER_CODEX_NO_FINAL_MESSAGE = "Codex ended without a final agent message."
_CONTAINER_COMPLETED_WITHOUT_AGENT_RESPONSE = (
    "container worker job completed without an agent response"
)
_QUEUED_PROMPT_CONTEXT_METADATA_KEYS = (
    "reduced_context_prompt",
    "context_retry_prompt",
    "agent_context_retry_enabled",
    "agent_context_retry_max_attempts",
    "agent_context_retry_target_chars",
)
_QUEUED_PROMPT_METADATA_KEYS = (
    "workflow_run_id",
    "mcp_tool_approval_lease_id",
    *_QUEUED_PROMPT_CONTEXT_METADATA_KEYS,
)
_SUBSCRIPTION_DEFERRAL_METADATA_KEYS = (
    *_QUEUED_PROMPT_METADATA_KEYS,
    "operator_steering",
    "operator_steering_note_id",
    "wake_dispatch_id",
    "request_id",
    "source",
)
_SUBSCRIPTION_REPLAY_ATTEMPT_METADATA_KEY = "subscription_replay_attempt"


def _prompt_error_outputs(
    message: str,
    *,
    error_class: str,
    failed_phase: str,
    dispatch_id: str | None = None,
    session_id: str | None = None,
    vm_name: str | None = None,
    queue_key: str | None = None,
    queued: bool | None = None,
    coalesced_count: int | None = None,
    retry_count: int | None = None,
    max_retries: int | None = None,
    execution_backend: str | None = None,
) -> dict[str, Any]:
    outputs: dict[str, Any] = {
        "success": False,
        "error_message": sanitize_text(str(message or error_class)),
        "error_class": sanitize_text(str(error_class or "RuntimeError")),
        "failed_phase": sanitize_text(str(failed_phase or "dispatch")),
    }
    for key, value in (
        ("dispatch_id", dispatch_id),
        ("session_id", session_id),
        ("vm_name", vm_name),
        ("queue_key", queue_key),
        ("execution_backend", execution_backend),
    ):
        text = str(value or "").strip()
        if text:
            outputs[key] = sanitize_text(text)
    if queued is not None:
        outputs["queued"] = bool(queued)
    if coalesced_count is not None:
        outputs["coalesced_count"] = int(coalesced_count)
    if retry_count is not None:
        outputs["retry_count"] = int(retry_count)
    if max_retries is not None:
        outputs["max_retries"] = int(max_retries)
    return outputs


def _waiting_execution_records(worker: Any, effect_type: str) -> list[Any]:
    execution_store = getattr(worker, "execution_store", None)
    list_waiting = getattr(execution_store, "list_waiting_records", None)
    if callable(list_waiting):
        return list(list_waiting(effect_type))
    read_all = getattr(execution_store, "read_all", None)
    if not callable(read_all):
        return []
    return [
        record
        for record in read_all().values()
        if str(getattr(record, "effect_type", "") or "").strip() == effect_type
        and str(getattr(record, "status", "") or "").strip() == "waiting"
    ]


class AgentPromptEffectProvider(EffectProviderBase):
    capability = NodeCapability(
        min_inbound=1,
        max_inbound=1,
        allowed_source_ports=frozenset(
            {
                AGENT_PROMPT_ERROR_PORT,
                AGENT_PROMPT_STARTED_PORT,
                AGENT_PROMPT_COMPLETED_PORT,
            }
        ),
    )

    def execute(
        self,
        *,
        resolved_config: Mapping[str, Any] | None = None,
        execution_context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()

        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "prompt": effect.prompt,
            "model": effect.model,
            "vm_user": effect.vm_user,
            "resume_thread_id": effect.resume_thread_id,
            "resume_thread_metadata_key": effect.resume_thread_metadata_key,
            "resume_thread_metadata_collection": effect.resume_thread_metadata_collection,
            "mcp_tool_approval_lease_id": effect.mcp_tool_approval_lease_id,
            "reduced_context_prompt": effect.reduced_context_prompt,
            "context_retry_prompt": effect.context_retry_prompt,
            "agent_context_retry_enabled": effect.agent_context_retry_enabled,
            "agent_context_retry_max_attempts": effect.agent_context_retry_max_attempts,
            "agent_context_retry_target_chars": effect.agent_context_retry_target_chars,
        }
        if resolved_config:
            payload.update(dict(resolved_config))
        workflow_context = workflow_context_from_sources(
            execution_context=execution_context,
            input_payload=payload,
            context_key=NODE_OUTPUTS_CONTEXT_KEY,
        )
        workflow_for_execution_ids = workflow_for_execution_ids_from_sources(
            execution_context=execution_context,
            input_payload=payload,
            context_key=_FOR_EXECUTION_IDS_PAYLOAD_KEY,
        )

        prompt = self._require_string(payload.get("prompt"), field_name="prompt")
        target = self._resolve_target(payload)
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"
        model = self._normalize_optional_string(payload.get("model"))

        dispatch_id = self._make_dispatch_id(effect_id=effect.id)
        resume_thread_id = self._normalize_optional_string(payload.get("resume_thread_id"))
        resume_thread_metadata_key = self._normalize_optional_string(
            payload.get("resume_thread_metadata_key")
        )
        resume_thread_metadata_collection = normalize_resume_thread_metadata_collection(
            payload.get("resume_thread_metadata_collection")
        )
        mcp_tool_approval_lease_id = self._resolve_mcp_tool_approval_lease_id(
            payload=payload,
            execution_context=execution_context,
        )
        workflow_run_id = self._workflow_run_id(execution_context)
        queue_metadata = self._queued_prompt_metadata(
            payload=payload,
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
        )

        execution_backend, config = self._resolve_execution_backend(payload, target=target)
        if execution_backend == ExecutionBackendKind.CONTAINER_WORKER_VM.value:
            return self._execute_container_prompt(
                payload=payload,
                prompt=prompt,
                model=model,
                dispatch_id=dispatch_id,
                resume_thread_id=resume_thread_id,
                resume_thread_metadata_key=resume_thread_metadata_key,
                resume_thread_metadata_collection=resume_thread_metadata_collection,
                config=config,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                execution_context=execution_context,
                workflow_context=workflow_context,
                workflow_for_execution_ids=workflow_for_execution_ids,
                queue_metadata=queue_metadata,
            )
        if execution_backend != ExecutionBackendKind.LIBVIRT_VM.value:
            raise ValueError(f"Unsupported execution_backend: {execution_backend}")

        active_session_id, active_session = resolve_active_session_if_available(
            context,
            target,
            config=config,
        )
        session_id = active_session_id or self._resolve_session_id_if_available(target)
        effective_config = (
            effective_config_for_session(
                context,
                session_id,
                fallback_directory_path=getattr(active_session, "directory_path", None),
            )
            if session_id
            else config
        )
        agent_harness = self._agent_harness_from_session(
            active_session,
            config=effective_config,
        )
        resolved_model = (
            model
            or getattr(active_session, "model", None)
            or default_agent_model_for_provider(agent_harness, effective_config)
        )
        if context.agent_resumer is None:
            message = "effect:agent:prompt agent_resumer is not available"
            raise EffectProviderExecutionError(
                message,
                outputs=_prompt_error_outputs(
                    message,
                    error_class="RuntimeError",
                    failed_phase="runtime_services",
                    dispatch_id=dispatch_id,
                    session_id=session_id,
                    vm_name=self._normalize_optional_string(payload.get("vm_name")),
                ),
            )
        effective_resume_thread_id = self._effective_resume_thread_id(
            resume_thread_id=resume_thread_id,
            resume_thread_metadata_key=resume_thread_metadata_key,
            resume_thread_metadata_collection=resume_thread_metadata_collection,
            target=target,
            session_id=session_id,
            session=active_session,
        )
        capacity_admission = LocalInferenceCapacityAdmission(enabled=False, admitted=True)
        runtime_dir = resolve_runtime_dir(context)
        vm_name = (
            self._normalize_optional_string(payload.get("vm_name"))
            or self._normalize_optional_string(getattr(active_session, "vm_id", None))
            or target
        )
        if runtime_dir is not None and effective_config.model_provider_mode == "local_openai":
            capacity_store = local_inference_capacity_store(runtime_dir)
            self._reconcile_local_inference_capacity(
                capacity_store=capacity_store,
                config=effective_config,
            )
            capacity_admission = capacity_store.claim(
                max_concurrency=effective_config.local_openai_max_concurrency,
                owner_id=self._current_idempotency_key(execution_context) or dispatch_id,
                model=resolved_model,
                session_id=session_id,
                dispatch_id=dispatch_id,
                effect_id=effect.id,
                execution_backend=ExecutionBackendKind.LIBVIRT_VM.value,
                target_name=vm_name,
            )
        if not capacity_admission.admitted:
            record = enqueue_agent_prompt(
                runtime_dir=runtime_dir,
                target=target,
                vm_name=vm_name,
                session_id=session_id,
                prompt=prompt,
                model=resolved_model,
                model_provider_mode=effective_config.model_provider_mode,
                queue_reason=LOCAL_INFERENCE_QUEUE_REASON,
                vm_user=vm_user,
                dispatch_id=dispatch_id,
                resume_thread_id=resume_thread_id,
                resume_thread_metadata_key=resume_thread_metadata_key,
                resume_thread_metadata_collection=resume_thread_metadata_collection,
                effect_id=effect.id,
                queue_key=f"{effect.id}:{dispatch_id}",
                metadata=queue_metadata,
                workflow_context=workflow_context,
                workflow_for_execution_ids=workflow_for_execution_ids,
                max_retries=effective_config.agent_prompt_queue_max_retries,
            )
            return {
                "dispatch_id": dispatch_id,
                "success": True,
                "queued": True,
                "queue_key": record.queue_key,
                "queue_reason": LOCAL_INFERENCE_QUEUE_REASON,
                "session_id": session_id,
                "model": resolved_model,
                "model_provider_mode": effective_config.model_provider_mode,
                **capacity_admission.output_metadata(),
            }
        with maintain_local_inference_launch(runtime_dir, capacity_admission.slot_id):
            result = context.agent_resumer.resume_agent(
                target=target,
                prompt=prompt,
                model=resolved_model,
                vm_user=vm_user,
                dispatch_id=dispatch_id,
                resume_thread_id=effective_resume_thread_id,
                persist_thread_id=resume_thread_metadata_key is None,
                mcp_gateway_url=self._mcp_gateway_url(
                    config=effective_config,
                    target=target,
                    workflow_run_id=workflow_run_id,
                    mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
                ),
            )
        if not bool(getattr(result, "success", False)):
            if runtime_dir is not None:
                local_inference_capacity_store(runtime_dir).release(capacity_admission.slot_id)
            result_message = (
                self._normalize_optional_string(getattr(result, "message", None)) or "<empty>"
            )
            message = (
                "effect:agent:prompt resume_agent failed "
                f"(target={target}, dispatch_id={dispatch_id}, message={result_message})"
            )
            raise EffectProviderExecutionError(
                message,
                outputs=_prompt_error_outputs(
                    message,
                    error_class="RuntimeError",
                    failed_phase="dispatch",
                    dispatch_id=dispatch_id,
                    session_id=session_id,
                    vm_name=self._normalize_optional_string(payload.get("vm_name")),
                ),
            )
        if bool(getattr(result, "queued", False)):
            if runtime_dir is not None:
                local_inference_capacity_store(runtime_dir).release(capacity_admission.slot_id)
            return {
                "dispatch_id": getattr(result, "dispatch_id", None) or dispatch_id,
                "success": True,
                "queued": True,
                "queue_key": getattr(result, "queue_key", None),
                "queue_reason": getattr(result, "queue_reason", None),
                "session_id": getattr(result, "session_id", None) or session_id,
                "model": getattr(result, "model", None) or resolved_model,
                "model_provider_mode": getattr(result, "model_provider_mode", None),
                "local_inference_capacity_enabled": getattr(
                    result, "local_inference_capacity_enabled", False
                ),
                "local_inference_active_count": getattr(result, "local_inference_active_count", 0),
                "local_inference_max_concurrency": getattr(
                    result, "local_inference_max_concurrency", None
                ),
            }
        thread_id = self._normalize_optional_string(getattr(result, "thread_id", None))
        if not thread_id:
            if runtime_dir is not None and capacity_admission.slot_id is not None:
                local_inference_capacity_store(runtime_dir).mark_running(
                    capacity_admission.slot_id,
                    target_name=vm_name,
                    execution_id=vm_name,
                )
            message = (
                "effect:agent:prompt did not receive a thread_id from resume_agent "
                "(success=True, message=<empty-or-missing-thread-id>)"
            )
            raise EffectProviderExecutionError(
                message,
                outputs=_prompt_error_outputs(
                    message,
                    error_class="RuntimeError",
                    failed_phase="response_validation",
                    dispatch_id=dispatch_id,
                    session_id=session_id,
                    vm_name=self._normalize_optional_string(payload.get("vm_name")),
                ),
            )
        session = active_session or self._session_for_metadata_lookup(
            target=target, session_id=session_id
        )
        directory_path = self._normalize_optional_string(getattr(session, "directory_path", None))
        if directory_path is None:
            directory_path = str(self._context().metadata.get("directory_path") or "root")
        if runtime_dir is not None:
            register_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                session_id=session_id,
                dispatch_id=dispatch_id,
                workflow_run_id=workflow_run_id,
                effect_id=effect.id,
                execution_backend=ExecutionBackendKind.LIBVIRT_VM.value,
                directory_path=directory_path,
                model=resolved_model,
                trace_id=self._trace_id(execution_context),
                agent_harness=agent_harness,
            )
            if capacity_admission.slot_id is not None:
                local_inference_capacity_store(runtime_dir).mark_running(
                    capacity_admission.slot_id,
                    target_name=vm_name,
                    execution_id=vm_name,
                )
        self._persist_resume_thread_metadata_key(
            target=target,
            session_id=session_id,
            resume_thread_metadata_key=resume_thread_metadata_key,
            resume_thread_metadata_collection=resume_thread_metadata_collection,
            thread_id=thread_id,
        )
        return {
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "success": bool(result.success),
            "queued": False,
            "session_id": getattr(result, "session_id", None) or session_id,
            "model": getattr(result, "model", None) or resolved_model,
            "model_provider_mode": (
                getattr(result, "model_provider_mode", None) or effective_config.model_provider_mode
            ),
            "local_inference_capacity_enabled": getattr(
                result, "local_inference_capacity_enabled", False
            )
            or capacity_admission.enabled,
            "local_inference_active_count": getattr(result, "local_inference_active_count", 0)
            or capacity_admission.active_count,
            "local_inference_max_concurrency": getattr(
                result, "local_inference_max_concurrency", None
            )
            or capacity_admission.max_concurrency,
            "local_inference_slot_id": (
                getattr(result, "local_inference_slot_id", None) or capacity_admission.slot_id
            ),
        }

    def _reconcile_local_inference_capacity(self, *, capacity_store: Any, config: Any) -> None:
        context = self._context()
        running_leases = [
            lease for lease in capacity_store.read_all().values() if lease.status == "running"
        ]
        if not running_leases:
            return

        summaries: list[Any] | None = None
        if any(
            lease.execution_backend != ExecutionBackendKind.CONTAINER_WORKER_VM.value
            for lease in running_leases
        ):
            list_vms = getattr(context.vm_inventory, "list_vms", None)
            if callable(list_vms):
                try:
                    summaries = list(list_vms(include_inactive=True, vm_user="agent"))
                except Exception:
                    summaries = None

        def is_active(lease: LocalInferenceCapacityLease) -> bool | None:
            if lease.execution_backend != ExecutionBackendKind.CONTAINER_WORKER_VM.value:
                if summaries is None:
                    return None
                return local_inference_vm_lease_is_active(lease, summaries)

            session_id = str(lease.session_id or "").strip()
            execution_id = str(lease.execution_id or "").strip()
            if not execution_id and session_id:
                execution_id = safe_effect_execution_id("agent", session_id)
            if not execution_id:
                return None
            effective_config = (
                effective_config_for_session(context, session_id) if session_id else config
            )
            backend = container_backend(context, effective_config)
            return local_inference_execution_is_active(backend.inspect(execution_id))

        capacity_store.reconcile_running(is_active)

    @staticmethod
    def _current_idempotency_key(
        execution_context: Mapping[str, Any] | None,
    ) -> str | None:
        if not isinstance(execution_context, Mapping):
            return None
        value = str(execution_context.get("idempotency_key") or "").strip()
        return value or None

    def _execute_container_prompt(
        self,
        *,
        payload: dict[str, Any],
        prompt: str,
        model: str | None,
        dispatch_id: str,
        resume_thread_id: str | None,
        resume_thread_metadata_key: str | None,
        resume_thread_metadata_collection: str,
        config: GatewayConfig,
        workflow_run_id: str | None,
        mcp_tool_approval_lease_id: str | None,
        execution_context: Mapping[str, Any] | None,
        workflow_context: dict[str, dict[str, Any]] | None,
        workflow_for_execution_ids: dict[str, str] | None,
        queue_metadata: dict[str, str],
    ) -> dict[str, Any]:
        context = self._context()
        session_id, session = require_container_session_id(
            context,
            payload,
            effect_type="effect:agent:prompt",
            config=config,
        )
        effective_config = effective_config_for_session(
            context,
            session_id,
            fallback_directory_path=session.directory_path if session is not None else None,
        )
        agent_harness = self._agent_harness_from_session(session, config=effective_config)
        resolved_model = (
            model
            or getattr(session, "model", None)
            or default_agent_model_for_provider(agent_harness, effective_config)
        )
        effective_resume_thread_id = self._effective_resume_thread_id(
            resume_thread_id=resume_thread_id,
            resume_thread_metadata_key=resume_thread_metadata_key,
            resume_thread_metadata_collection=resume_thread_metadata_collection,
            target=self._normalize_optional_string(payload.get("target")) or session_id,
            session_id=session_id,
            session=session,
        )
        capacity_admission = LocalInferenceCapacityAdmission(enabled=False, admitted=True)
        runtime_dir = resolve_runtime_dir(context)
        if runtime_dir is not None and effective_config.model_provider_mode == "local_openai":
            capacity_store = local_inference_capacity_store(runtime_dir)
            self._reconcile_local_inference_capacity(
                capacity_store=capacity_store,
                config=effective_config,
            )
            capacity_admission = capacity_store.claim(
                max_concurrency=effective_config.local_openai_max_concurrency,
                owner_id=self._current_idempotency_key(execution_context) or dispatch_id,
                model=resolved_model,
                session_id=session_id,
                dispatch_id=dispatch_id,
                effect_id=self._effect().id,
                execution_backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                target_name=safe_effect_execution_id("agent", session_id),
            )
        if not capacity_admission.admitted:
            record = enqueue_agent_prompt(
                runtime_dir=runtime_dir,
                target=self._normalize_optional_string(payload.get("target")) or session_id,
                vm_name=session_id,
                session_id=session_id,
                prompt=prompt,
                model=resolved_model,
                model_provider_mode=effective_config.model_provider_mode,
                queue_reason=LOCAL_INFERENCE_QUEUE_REASON,
                vm_user="agent",
                dispatch_id=dispatch_id,
                resume_thread_id=resume_thread_id,
                resume_thread_metadata_key=resume_thread_metadata_key,
                resume_thread_metadata_collection=resume_thread_metadata_collection,
                effect_id=self._effect().id,
                queue_key=f"{self._effect().id}:{dispatch_id}",
                metadata=queue_metadata,
                workflow_context=workflow_context,
                workflow_for_execution_ids=workflow_for_execution_ids,
                max_retries=effective_config.agent_prompt_queue_max_retries,
            )
            return {
                "dispatch_id": dispatch_id,
                "success": True,
                "queued": True,
                "queue_key": record.queue_key,
                "queue_reason": LOCAL_INFERENCE_QUEUE_REASON,
                "session_id": session_id,
                "model": resolved_model,
                "model_provider_mode": effective_config.model_provider_mode,
                **capacity_admission.output_metadata(),
            }
        backend = container_backend(context, effective_config)
        execution_id = safe_effect_execution_id("agent", session_id)
        mcp_gateway_url = gateway_url_with_session_context(
            container_worker_guest_gateway_url(effective_config),
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
            mcp_session_token=session_mcp_auth_token(session),
        )
        codex_turn = container_codex_turn_spec(
            prompt=prompt,
            model=resolved_model,
            resume_thread_id=effective_resume_thread_id,
            session=session,
            config=effective_config,
            agent_provider=agent_harness,
            resume_mode="new_context",
            mcp_gateway_url=mcp_gateway_url,
            web_search_enabled=bool(getattr(session, "web_search_enabled", False)),
        )
        command = self._container_prompt_command(
            prompt=prompt,
            model=resolved_model,
            resume_thread_id=effective_resume_thread_id,
            session=session,
            agent_provider=agent_harness,
            mcp_gateway_url=mcp_gateway_url,
            web_search_enabled=bool(getattr(session, "web_search_enabled", False)),
        )
        try:
            with maintain_local_inference_launch(runtime_dir, capacity_admission.slot_id):
                handle = backend.start(
                    backend.prepare(
                        ExecutionSpec(
                            backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                            target_name=execution_id,
                            session_id=session_id,
                            directory_path=(
                                session.directory_path if session is not None else "root"
                            ),
                            image=container_worker_image_for_session(session, effective_config),
                            resource_limits=container_worker_resource_limits_for_session(session),
                            command=command,
                            codex_turn=codex_turn,
                            metadata={
                                "effect_type": self.node_type(),
                                "dispatch_id": dispatch_id,
                                "resume_thread_id": effective_resume_thread_id,
                                "agent_provider": agent_harness,
                                "model": resolved_model,
                                "model_provider_mode": effective_config.model_provider_mode,
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
            if runtime_dir is not None:
                local_inference_capacity_store(runtime_dir).release(capacity_admission.slot_id)
            if isinstance(exc, CodexSubscriptionDeferredClientError):
                deferral = exc.deferral
                record = enqueue_agent_prompt(
                    runtime_dir=runtime_dir,
                    target=self._normalize_optional_string(payload.get("target")) or session_id,
                    vm_name=session_id,
                    session_id=session_id,
                    prompt=prompt,
                    model=resolved_model,
                    model_provider_mode=effective_config.model_provider_mode,
                    queue_reason=deferral.queue_reason,
                    subscription_profile_id=deferral.profile_id,
                    retry_not_before=deferral.retry_at,
                    capacity_deferral=True,
                    vm_user="agent",
                    dispatch_id=dispatch_id,
                    resume_thread_id=resume_thread_id,
                    resume_thread_metadata_key=resume_thread_metadata_key,
                    resume_thread_metadata_collection=resume_thread_metadata_collection,
                    effect_id=self._effect().id,
                    queue_key=f"{self._effect().id}:{dispatch_id}",
                    metadata=queue_metadata,
                    workflow_context=workflow_context,
                    workflow_for_execution_ids=workflow_for_execution_ids,
                )
                return {
                    "dispatch_id": dispatch_id,
                    "success": True,
                    "queued": True,
                    "queue_key": record.queue_key,
                    "queue_reason": record.queue_reason,
                    "retry_not_before": record.retry_not_before,
                    "subscription_profile_id": record.subscription_profile_id,
                    "session_id": session_id,
                    "model": resolved_model,
                    "model_provider_mode": effective_config.model_provider_mode,
                }
            detail = self._normalize_optional_string(exc) or type(exc).__name__
            message = (
                "effect:agent:prompt container dispatch failed "
                f"(session_id={session_id}, dispatch_id={dispatch_id}, message={detail})"
            )
            raise EffectProviderExecutionError(
                message,
                outputs=_prompt_error_outputs(
                    message,
                    error_class=type(exc).__name__,
                    failed_phase="container_dispatch",
                    dispatch_id=dispatch_id,
                    session_id=session_id,
                    execution_backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                ),
            ) from exc
        if runtime_dir is not None:
            register_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                session_id=session_id,
                dispatch_id=dispatch_id,
                workflow_run_id=workflow_run_id,
                effect_id=self._effect().id,
                execution_backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                directory_path=str(getattr(session, "directory_path", None) or "root"),
                model=resolved_model,
                trace_id=self._trace_id(execution_context),
                agent_harness=agent_harness,
            )
            if capacity_admission.slot_id is not None:
                local_inference_capacity_store(runtime_dir).mark_running(
                    capacity_admission.slot_id,
                    target_name=str(getattr(handle, "target_name", None) or handle.execution_id),
                    execution_id=handle.execution_id,
                )
        return {
            "dispatch_id": dispatch_id,
            "thread_id": effective_resume_thread_id or dispatch_id,
            "success": True,
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            "execution_id": handle.execution_id,
            "session_id": session_id,
            "queued": False,
            "model": resolved_model,
            "model_provider_mode": effective_config.model_provider_mode,
            **capacity_admission.output_metadata(),
        }

    @classmethod
    def on_execution_started(cls, **kwargs: Any) -> bool:
        record = kwargs.get("record")
        outputs = kwargs.get("outputs")
        if record is None or not isinstance(outputs, Mapping):
            return False
        if outputs.get("success") is False or outputs.get("queued") is True:
            return False
        return bool(str(outputs.get("dispatch_id") or "").strip())

    @classmethod
    def on_execution_waiting(cls, **kwargs: Any) -> None:
        worker = kwargs.get("worker")
        record = kwargs.get("record")
        if worker is None or record is None:
            return
        publish_or_mark_pending = getattr(
            worker, "_publish_effect_completion_event_or_mark_pending", None
        )
        if callable(publish_or_mark_pending):
            publish_or_mark_pending(
                record=record,
                outputs=dict(record.outputs),
                branch_port=AGENT_PROMPT_STARTED_PORT,
                pending_status="waiting",
            )
            return
        worker._publish_completion_event_from_record(
            record=record,
            outputs=dict(record.outputs),
            branch_port=AGENT_PROMPT_STARTED_PORT,
        )

    @classmethod
    def on_completion_event(cls, **kwargs: Any) -> int:
        worker = kwargs.get("worker")
        queue_record = kwargs.get("queue_record")
        if worker is None or not isinstance(queue_record, TriggerQueueRecord):
            return 0
        dispatch_id = str(queue_record.payload.get("dispatch_id") or "").strip()
        if not dispatch_id:
            return 0

        finalized = 0
        for record in _waiting_execution_records(worker, cls.node_type()):
            record_dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
            if record_dispatch_id != dispatch_id:
                continue
            cls._finalize_waiting_record(worker=worker, record=record, queue_record=queue_record)
            finalized += 1
        return finalized

    @classmethod
    def reconcile_waiting(cls, **kwargs: Any) -> int:
        worker = kwargs.get("worker")
        if worker is None:
            return 0
        waiting_records = _waiting_execution_records(worker, cls.node_type())
        if not waiting_records:
            return 0

        completed = 0
        vm_records: list[Any] = []
        for record in waiting_records:
            if cls._is_container_waiting_record(record):
                completed += cls._reconcile_container_waiting_record(worker=worker, record=record)
                continue
            vm_records.append(record)
        if not vm_records:
            return completed
        try:
            summaries = list_vm_summaries_for_agent_reconciliation(worker)
        except Exception:
            logger.exception(
                "AgentPromptEffectProvider inventory reconciliation skipped "
                "phase=waiting_reconciliation waiting_vm_records=%d",
                len(vm_records),
            )
            return completed
        for record in vm_records:
            queue_record = cls._build_completion_queue_record_for_waiting_record(
                record=record, summaries=summaries
            )
            if queue_record is None:
                continue
            cls._finalize_waiting_record(worker=worker, record=record, queue_record=queue_record)
            completed += 1
        return completed

    @classmethod
    def _is_container_waiting_record(cls, record: Any) -> bool:
        return (
            str(record.outputs.get("execution_backend") or "").strip()
            == ExecutionBackendKind.CONTAINER_WORKER_VM.value
        )

    @classmethod
    def _reconcile_container_waiting_record(cls, *, worker: Any, record: Any) -> int:
        if bool(record.outputs.get(CONTAINER_INTERRUPT_IN_PROGRESS_OUTPUT_KEY)):
            return 0
        execution_id = str(record.outputs.get("execution_id") or "").strip()
        session_id = str(record.outputs.get("session_id") or "").strip()
        if not execution_id or not session_id:
            return 0
        backend = cls._container_backend_for_worker(worker=worker, session_id=session_id)
        if backend is None:
            return 0
        try:
            status = backend.inspect(execution_id)
        except Exception:
            return 0
        if status.status not in {
            ExecutionStatusKind.STOPPED,
            ExecutionStatusKind.FAILED,
            ExecutionStatusKind.DESTROYED,
        }:
            cls._interrupt_stalled_container_active_tool_if_expired(
                backend=backend,
                status=status,
                execution_id=execution_id,
            )
            timeout_result = cls._timeout_container_waiting_record_if_expired(
                worker=worker,
                record=record,
                backend=backend,
                status=status,
                session_id=session_id,
                execution_id=execution_id,
            )
            if timeout_result:
                return timeout_result
            cls._capture_container_thread_id_from_running_logs(
                worker=worker,
                record=record,
                backend=backend,
                session_id=session_id,
                execution_id=execution_id,
            )
            return 0
        if cls._defer_subscription_waiting_record(
            worker=worker,
            record=record,
            backend=backend,
            status=status,
            session_id=session_id,
            execution_id=execution_id,
        ):
            return 1
        logs = ""
        try:
            logs = backend.get_log_snapshot(execution_id, lines=400)
        except Exception:
            logs = ""
        thread_id = cls._thread_id_from_logs(logs)
        failed_codex_outcome = cls._failed_codex_outcome_from_status_metadata(
            getattr(status, "metadata", None)
        )
        no_agent_response = cls._container_logs_indicate_no_agent_response(logs)
        if thread_id and failed_codex_outcome is None and not no_agent_response:
            cls._store_container_thread_id(
                worker=worker,
                record=record,
                session_id=session_id,
                thread_id=thread_id,
            )
        if status.status != ExecutionStatusKind.DESTROYED:
            try:
                backend.destroy(execution_id)
            except Exception:
                pass
        queue_record = cls._build_container_completion_queue_record(
            record=record,
            session_id=session_id,
            execution_id=execution_id,
            worker_status=status.status.value,
            success=status.status == ExecutionStatusKind.STOPPED,
            message=status.message,
            logs=logs,
            status_metadata=getattr(status, "metadata", None),
        )
        cls._finalize_waiting_record(worker=worker, record=record, queue_record=queue_record)
        return 1

    @classmethod
    def _defer_subscription_waiting_record(
        cls,
        *,
        worker: Any,
        record: Any,
        backend: Any,
        status: Any,
        session_id: str,
        execution_id: str,
    ) -> bool:
        metadata = getattr(status, "metadata", None)
        raw_deferral = (
            metadata.get("codex_subscription_deferral") if isinstance(metadata, Mapping) else None
        )
        if not isinstance(raw_deferral, Mapping):
            return False
        try:
            deferral = CodexSubscriptionDeferral.model_validate(raw_deferral)
        except ValueError:
            return False
        runtime_dir = resolve_runtime_dir(worker)
        prompt = cls._record_text_value(record, "prompt")
        if runtime_dir is None or not prompt:
            return False
        dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
        target = cls._record_text_value(record, "target") or session_id
        vm_name = cls._record_text_value(record, "vm_name") or session_id
        queue_key = (
            cls._record_text_value(record, "queue_key")
            or f"{record.effect_id}:{dispatch_id}:subscription"
        )
        input_payload = getattr(record, "input_payload", None)
        workflow_context = workflow_context_from_sources(
            execution_context=input_payload,
            input_payload=input_payload,
            context_key=NODE_OUTPUTS_CONTEXT_KEY,
        )
        workflow_for_execution_ids = workflow_for_execution_ids_from_sources(
            execution_context=input_payload,
            input_payload=input_payload,
            context_key=_FOR_EXECUTION_IDS_PAYLOAD_KEY,
        )
        deferral_metadata = cls._subscription_deferral_metadata(record)
        queued = enqueue_agent_prompt(
            runtime_dir=runtime_dir,
            target=target,
            vm_name=vm_name,
            session_id=session_id,
            prompt=prompt,
            model=cls._record_text_value(record, "model"),
            model_provider_mode=cls._record_text_value(record, "model_provider_mode"),
            queue_reason=deferral.queue_reason,
            subscription_profile_id=deferral.profile_id,
            retry_not_before=deferral.retry_at,
            capacity_deferral=True,
            vm_user=cls._record_text_value(record, "vm_user") or "agent",
            dispatch_id=dispatch_id or None,
            resume_thread_id=cls._record_text_value(record, "resume_thread_id"),
            resume_mode=cls._record_text_value(record, "resume_mode"),
            resume_thread_metadata_key=cls._record_text_value(record, "resume_thread_metadata_key"),
            resume_thread_metadata_collection=cls._record_text_value(
                record, "resume_thread_metadata_collection"
            ),
            effect_id=str(record.effect_id),
            queue_key=queue_key,
            metadata=deferral_metadata,
            workflow_context=workflow_context,
            workflow_for_execution_ids=workflow_for_execution_ids,
        )
        note_id = str(deferral_metadata.get("operator_steering_note_id") or "").strip()
        if note_id:
            try:
                from evander.operator_steering import operator_steering_note_store

                note = operator_steering_note_store(runtime_dir).mark_queued_replacement_deferred(
                    note_id
                )
            except Exception:
                AgentPromptQueueStore(
                    runtime_dir / "queued-agent-prompts.json"
                ).delete_if_dispatch_id(
                    queued.queue_key,
                    queued_agent_prompt_dispatch_id(queued),
                )
                raise
            if note is not None and note.status != "pending":
                AgentPromptQueueStore(
                    runtime_dir / "queued-agent-prompts.json"
                ).delete_if_dispatch_id(
                    queued.queue_key,
                    queued_agent_prompt_dispatch_id(queued),
                )
                if note.status == "cancelled":
                    return False
                raise RuntimeError(
                    "Queued operator steering note could not return to pending after "
                    f"subscription deferral: {note.note_id} status={note.status}"
                )
        if status.status != ExecutionStatusKind.DESTROYED:
            try:
                backend.destroy(execution_id)
            except Exception:
                pass
        finished_at = datetime.now(UTC)
        record.status = "succeeded"
        record.outputs = {
            **record.outputs,
            "success": True,
            "queued": True,
            "queue_key": queued.queue_key,
            "queue_reason": queued.queue_reason,
            "subscription_profile_id": queued.subscription_profile_id,
            "retry_not_before": queued.retry_not_before,
        }
        record.error = None
        record.finished_at = finished_at
        worker.execution_store.put(record)
        worker._update_event_delivery_state(record.event_id)
        if dispatch_id:
            clear_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                dispatch_id=dispatch_id,
            )
        cls._release_enqueue_prompt_lock(worker=worker, record=record, outputs=record.outputs)
        cls._release_local_inference_capacity(worker=worker, record=record, outputs=record.outputs)
        return True

    @classmethod
    def _subscription_deferral_metadata(cls, record: Any) -> dict[str, str]:
        metadata: dict[str, str] = {}
        for key in _SUBSCRIPTION_DEFERRAL_METADATA_KEYS:
            value = cls._record_text_value(record, key)
            if value:
                metadata[key] = value
        if "workflow_run_id" not in metadata:
            trace_id = str(getattr(record, "trace_id", "") or "").strip()
            if trace_id:
                metadata["workflow_run_id"] = trace_id
        raw_attempt = cls._record_raw_value(record, _SUBSCRIPTION_REPLAY_ATTEMPT_METADATA_KEY)
        try:
            attempt = max(0, int(raw_attempt or 0)) + 1
        except (TypeError, ValueError):
            attempt = 1
        metadata[_SUBSCRIPTION_REPLAY_ATTEMPT_METADATA_KEY] = str(attempt)
        return metadata

    @classmethod
    def _interrupt_stalled_container_active_tool_if_expired(
        cls,
        *,
        backend: Any,
        status: Any,
        execution_id: str,
    ) -> None:
        metadata = getattr(status, "metadata", None)
        active_tool = metadata.get("active_tool") if isinstance(metadata, Mapping) else None
        started_at = (
            cls._container_active_tool_started_at(active_tool)
            if isinstance(active_tool, Mapping)
            else None
        )
        elapsed_seconds = cls._elapsed_seconds_since(started_at)
        timeout_seconds = cls._container_active_tool_timeout_seconds(backend)
        if elapsed_seconds is None or elapsed_seconds <= timeout_seconds:
            return
        interrupt = getattr(backend, "interrupt_active_tool", None)
        if not callable(interrupt):
            return
        try:
            interrupt(
                execution_id,
                max_elapsed_seconds=timeout_seconds,
                reason="active_tool_timeout",
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "container_active_tool_timeout_interrupt_failed execution_id=%s",
                execution_id,
                exc_info=True,
            )

    @staticmethod
    def _container_active_tool_timeout_seconds(backend: Any) -> float:
        config = getattr(backend, "config", None)
        value = getattr(config, "container_worker_active_tool_timeout_seconds", None)
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            timeout = _DEFAULT_CONTAINER_WORKER_ACTIVE_TOOL_TIMEOUT_SECONDS
        return timeout if timeout > 0 else _DEFAULT_CONTAINER_WORKER_ACTIVE_TOOL_TIMEOUT_SECONDS

    @staticmethod
    def _container_active_tool_started_at(active_tool: Mapping[str, Any]) -> datetime | None:
        value = active_tool.get("started_at")
        if not isinstance(value, str) or not value.strip():
            return None
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            started_at = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return (
            started_at.replace(tzinfo=UTC)
            if started_at.tzinfo is None
            else started_at.astimezone(UTC)
        )

    @classmethod
    def _timeout_container_waiting_record_if_expired(
        cls,
        *,
        worker: Any,
        record: Any,
        backend: Any,
        status: Any,
        session_id: str,
        execution_id: str,
    ) -> int:
        timeout_seconds = cls._container_job_timeout_seconds(backend)
        started_at = (
            cls._container_status_started_at(status) or record.finished_at or record.started_at
        )
        elapsed_seconds = cls._elapsed_seconds_since(started_at)
        if elapsed_seconds is None or elapsed_seconds <= timeout_seconds:
            return 0

        logs = ""
        try:
            logs = backend.get_log_snapshot(execution_id, lines=400)
        except Exception:
            logs = ""
        thread_id = cls._thread_id_from_logs(logs)
        if thread_id and not cls._container_logs_indicate_no_agent_response(logs):
            cls._store_container_thread_id(
                worker=worker,
                record=record,
                session_id=session_id,
                thread_id=thread_id,
            )

        stop_error = cls._stop_and_destroy_container_job(backend=backend, execution_id=execution_id)
        message = cls._container_timeout_message(
            execution_id=execution_id,
            elapsed_seconds=elapsed_seconds,
            timeout_seconds=timeout_seconds,
            logs=logs,
            stop_error=stop_error,
        )
        cls._fail_waiting_record_without_completion_event(
            worker=worker,
            record=record,
            outputs={
                "dispatch_id": str(record.outputs.get("dispatch_id") or "").strip(),
                "thread_id": thread_id or str(record.outputs.get("thread_id") or ""),
                "session_id": session_id,
                "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                "worker_execution_id": execution_id,
                "worker_status": "timeout",
                "success": False,
                "message": message,
                "logs": logs,
                "agent_state": "failed",
                "scope": {
                    "directory_path": str(record.resolved_config.get("directory_path") or "root"),
                    "session_id": session_id,
                    "vm_name": None,
                    "vm_user": str(record.resolved_config.get("vm_user") or "agent"),
                },
            },
            error=message,
        )
        return 1

    @staticmethod
    def _container_job_timeout_seconds(backend: Any) -> float:
        config = getattr(backend, "config", None)
        value = getattr(config, "container_worker_job_timeout_seconds", None)
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            timeout = _DEFAULT_CONTAINER_WORKER_JOB_TIMEOUT_SECONDS
        return timeout if timeout > 0 else _DEFAULT_CONTAINER_WORKER_JOB_TIMEOUT_SECONDS

    @staticmethod
    def _container_status_started_at(status: Any) -> datetime | None:
        value = getattr(status, "started_at", None)
        return value if isinstance(value, datetime) else None

    @staticmethod
    def _elapsed_seconds_since(started_at: datetime | None) -> float | None:
        if started_at is None:
            return None
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        else:
            started_at = started_at.astimezone(UTC)
        return max(0.0, (datetime.now(UTC) - started_at).total_seconds())

    @staticmethod
    def _stop_and_destroy_container_job(*, backend: Any, execution_id: str) -> str | None:
        stop_error: str | None = None
        try:
            backend.stop(execution_id, timeout_seconds=30)
        except Exception as exc:  # noqa: BLE001
            stop_error = str(exc)
        try:
            backend.destroy(execution_id)
        except Exception as exc:  # noqa: BLE001
            destroy_error = str(exc)
            stop_error = (
                f"{stop_error}; destroy failed: {destroy_error}" if stop_error else destroy_error
            )
        return stop_error

    @classmethod
    def _container_timeout_message(
        cls,
        *,
        execution_id: str,
        elapsed_seconds: float,
        timeout_seconds: float,
        logs: str,
        stop_error: str | None,
    ) -> str:
        lines = [
            "container worker prompt job timed out "
            f"(execution_id={execution_id}, elapsed_seconds={elapsed_seconds:.1f}, "
            f"timeout_seconds={timeout_seconds:.1f})"
        ]
        if stop_error:
            lines.append(f"stop/remove error: {stop_error}")
        last_lines = cls._last_useful_log_lines(logs, max_lines=20)
        if last_lines:
            lines.append("last log lines:")
            lines.append(last_lines)
        else:
            lines.append("last log lines: <none>")
        return "\n".join(lines)

    @staticmethod
    def _last_useful_log_lines(logs: str, *, max_lines: int) -> str:
        useful = [line.rstrip() for line in logs.splitlines() if line.strip()]
        return "\n".join(useful[-max(1, max_lines) :])

    @classmethod
    def _fail_waiting_record_without_completion_event(
        cls,
        *,
        worker: Any,
        record: Any,
        outputs: dict[str, Any],
        error: str,
    ) -> None:
        finished_at = datetime.now(UTC)
        record.status = "failed"
        record.outputs = {**record.outputs, **outputs}
        record.error = error
        record.published_event_id = None
        record.completion_event_enqueued_at = None
        record.finished_at = finished_at
        worker.execution_store.put(record)
        worker._update_event_delivery_state(record.event_id)
        cls._release_enqueue_prompt_lock(
            worker=worker,
            record=record,
            outputs=record.outputs,
        )
        cls._release_local_inference_capacity(
            worker=worker,
            record=record,
            outputs=record.outputs,
        )

    @classmethod
    def _capture_container_thread_id_from_running_logs(
        cls,
        *,
        worker: Any,
        record: Any,
        backend: Any,
        session_id: str,
        execution_id: str,
    ) -> None:
        current_thread_id = str(record.outputs.get("thread_id") or "").strip()
        if current_thread_id and not current_thread_id.startswith("dispatch-"):
            return
        try:
            logs = backend.get_log_snapshot(execution_id, lines=80)
        except Exception:
            return
        thread_id = cls._thread_id_from_logs(logs)
        if not thread_id or cls._container_logs_indicate_no_agent_response(logs):
            return
        cls._store_container_thread_id(
            worker=worker,
            record=record,
            session_id=session_id,
            thread_id=thread_id,
        )

    @classmethod
    def _store_container_thread_id(
        cls,
        *,
        worker: Any,
        record: Any,
        session_id: str,
        thread_id: str,
    ) -> None:
        normalized_thread_id = str(thread_id or "").strip()
        if not normalized_thread_id or normalized_thread_id.startswith("dispatch-"):
            return
        current_thread_id = str(record.outputs.get("thread_id") or "").strip()
        if current_thread_id != normalized_thread_id:
            record.outputs = {**record.outputs, "thread_id": normalized_thread_id}
            store = getattr(worker, "execution_store", None)
            put = getattr(store, "put", None)
            record_execution = getattr(store, "record_execution", None)
            try:
                if callable(put):
                    put(record)
                elif callable(record_execution):
                    record_execution(record)
            except Exception:
                pass
        cls._persist_container_session_thread_id(
            worker=worker,
            record=record,
            session_id=session_id,
            thread_id=normalized_thread_id,
        )

    @classmethod
    def _persist_container_session_thread_id(
        cls,
        *,
        worker: Any,
        record: Any,
        session_id: str,
        thread_id: str,
    ) -> None:
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        cls._persist_chat_assistant_mapping_thread_id(
            admin_service=admin_service,
            session_id=session_id,
            thread_id=thread_id,
        )
        resume_thread_metadata_key = cls._resume_thread_metadata_key_from_record(record)
        resume_thread_metadata_collection = cls._resume_thread_metadata_collection_from_record(
            record
        )
        metadata_patch = resume_thread_metadata_patch(
            collection=resume_thread_metadata_collection,
            key=resume_thread_metadata_key,
            thread_id=thread_id,
        )
        if not metadata_patch:
            return
        update_fn = getattr(admin_service, "session_update_metadata", None)
        if callable(update_fn):
            try:
                update_fn(target=session_id, metadata=metadata_patch)
                return
            except Exception:
                pass

        repository = getattr(getattr(admin_service, "runtime", None), "session_repository", None)
        get_session = getattr(repository, "get_session", None)
        upsert_session = getattr(repository, "upsert_session", None)
        if not callable(get_session) or not callable(upsert_session):
            return
        try:
            manifest = get_session(session_id)
            if manifest is None:
                return
            metadata = getattr(manifest, "metadata", {})
            if not isinstance(metadata, Mapping):
                metadata = {}
            merged_metadata = cls._deep_merge_metadata(dict(metadata), metadata_patch)
            updated = manifest.model_copy(update={"metadata": merged_metadata})
            upsert_session(updated)
        except Exception:
            pass

    @staticmethod
    def _resume_thread_metadata_key_from_record(record: Any) -> str | None:
        for source_name in ("resolved_config", "input_payload", "outputs"):
            source = getattr(record, source_name, None)
            if not isinstance(source, Mapping):
                continue
            text = str(source.get("resume_thread_metadata_key") or "").strip()
            if text:
                return text
        return None

    @staticmethod
    def _resume_thread_metadata_collection_from_record(record: Any) -> str:
        for source_name in ("resolved_config", "input_payload", "outputs"):
            source = getattr(record, source_name, None)
            if not isinstance(source, Mapping):
                continue
            text = str(source.get("resume_thread_metadata_collection") or "").strip()
            if text:
                return text
        return normalize_resume_thread_metadata_collection(None)

    @classmethod
    def _deep_merge_metadata(
        cls, base: Mapping[str, Any], update: Mapping[str, Any]
    ) -> dict[str, Any]:
        merged = dict(base)
        for key, value in update.items():
            existing = merged.get(key)
            if isinstance(existing, Mapping) and isinstance(value, Mapping):
                merged[key] = cls._deep_merge_metadata(existing, value)
                continue
            merged[key] = value
        return merged

    @classmethod
    def _persist_chat_assistant_mapping_thread_id(
        cls,
        *,
        admin_service: Any,
        session_id: str,
        thread_id: str,
    ) -> None:
        vm_agent_admin = getattr(admin_service, "vm_agent_admin", None)
        persist = getattr(vm_agent_admin, "persist_chat_assistant_agent_thread_id", None)
        if not callable(persist):
            return
        try:
            persist(
                session_id=session_id,
                agent_thread_id=thread_id,
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                assistant_scope="admin_chat",
            )
        except Exception:
            pass

    @classmethod
    def _container_backend_for_worker(cls, *, worker: Any, session_id: str) -> Any | None:
        config: GatewayConfig | None = None
        resolver = getattr(worker, "provider_config_resolver", None)
        resolve_for_session_id = getattr(resolver, "resolve_for_session_id", None)
        if callable(resolve_for_session_id):
            try:
                config = resolve_for_session_id(session_id)
            except Exception:
                config = None
        if config is None:
            config = getattr(worker, "config", None)
        if config is None:
            return None
        try:
            admin_service = getattr(
                getattr(worker, "provider_factory", None), "admin_service", None
            )
            return build_execution_backend(
                config.model_copy(
                    update={"execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value}
                ),
                admin_service=admin_service,
            )
        except Exception:
            return None

    @classmethod
    def _build_container_completion_queue_record(
        cls,
        *,
        record: Any,
        session_id: str,
        execution_id: str,
        worker_status: str,
        success: bool,
        message: str | None,
        logs: str,
        status_metadata: Mapping[str, Any] | None = None,
    ) -> TriggerQueueRecord:
        finished_at = datetime.now(UTC)
        dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
        wake_dispatch_id = str(record.outputs.get("wake_dispatch_id") or "").strip()
        thread_id = cls._thread_id_from_logs(logs) or str(record.outputs.get("thread_id") or "")
        failed_codex_outcome = cls._failed_codex_outcome_from_status_metadata(status_metadata)
        no_agent_response = (
            failed_codex_outcome is None
            and success
            and cls._container_logs_indicate_no_agent_response(logs)
        )
        if failed_codex_outcome is not None:
            success = False
            message = message or str(
                failed_codex_outcome.get("message") or _CONTAINER_CODEX_NO_FINAL_MESSAGE
            )
        elif no_agent_response:
            success = False
            message = message or _CONTAINER_COMPLETED_WITHOUT_AGENT_RESPONSE
        payload: dict[str, object] = {
            "dispatch_id": dispatch_id,
            "thread_id": thread_id,
            "session_id": session_id,
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            "worker_execution_id": execution_id,
            "worker_status": worker_status,
            "success": success,
            "message": message or f"container worker job status={worker_status}",
            "agent_state": "idle" if success else "failed",
            "scope": {
                "directory_path": str(record.resolved_config.get("directory_path") or "root"),
                "session_id": session_id,
                "vm_name": None,
                "vm_user": str(record.resolved_config.get("vm_user") or "agent"),
            },
        }
        if isinstance(status_metadata, Mapping):
            codex_outcome = status_metadata.get("codex_outcome")
            if isinstance(codex_outcome, Mapping):
                payload["codex_outcome"] = dict(codex_outcome)
            failure_reason = context_exhaustion_reason(status_metadata)
            if failure_reason is not None:
                payload["failure_kind"] = CONTEXT_EXHAUSTION_FAILURE_KIND
                payload["failure_reason"] = failure_reason
        if no_agent_response and "codex_outcome" not in payload:
            payload["codex_outcome"] = {
                "status": "failed",
                "reason": _CONTAINER_CODEX_NO_FINAL_MESSAGE_REASON,
                "message": _CONTAINER_CODEX_NO_FINAL_MESSAGE,
            }
        if wake_dispatch_id:
            payload["wake_dispatch_id"] = wake_dispatch_id
        return TriggerQueueRecord(
            event_id=f"__await-agent-completion__:{dispatch_id}:{finished_at.isoformat()}",
            trigger_id="__await-agent-completion__",
            trigger_type="trigger:agent:run:completed",
            trace_id=record.trace_id,
            occurred_at=finished_at,
            payload=payload,
            delivery_state="delivered",
            delivered_at=finished_at,
            idempotency_key=f"__await-agent-completion__:{dispatch_id}",
        )

    @classmethod
    def node_type(cls) -> str:
        return "effect:agent:prompt"

    @classmethod
    def _finalize_waiting_record(
        cls, *, worker: Any, record: Any, queue_record: TriggerQueueRecord
    ) -> None:
        finished_at = datetime.now(UTC)
        merged_outputs = {
            **record.outputs,
            **{
                key: value
                for key, value in queue_record.payload.items()
                if key
                not in {
                    NODE_OUTPUTS_CONTEXT_KEY,
                    _BRANCH_PORT_PAYLOAD_KEY,
                    _FOR_EXECUTION_IDS_PAYLOAD_KEY,
                }
            },
        }
        if cls._maybe_start_reduced_context_retry(
            worker=worker,
            record=record,
            queue_record=queue_record,
            merged_outputs=merged_outputs,
        ):
            return
        if "context_retry_in_progress" in merged_outputs:
            merged_outputs["context_retry_in_progress"] = False
        # Reconciliation may construct a completion without a wake monitor ever
        # publishing it. Persist it before finalizing the waiting execution so
        # configured completion triggers also receive it, including after a crash.
        # A monitor event already in the queue is deduplicated by dispatch ID.
        worker.event_queue.publish(
            TriggerEvent(
                event_id=queue_record.event_id,
                trigger_id=queue_record.trigger_id,
                trigger_type=queue_record.trigger_type,
                occurred_at=queue_record.occurred_at,
                trace_id=queue_record.trace_id,
                payload=queue_record.payload,
            ),
            idempotency_key=queue_record.idempotency_key,
            config_revision=queue_record.config_revision,
            directory_path=queue_record.directory_path,
        )
        branch_port = (
            AGENT_PROMPT_COMPLETED_PORT
            if record.effect_type in {cls.node_type(), AGENT_ENQUEUE_PROMPT_EFFECT_TYPE}
            else None
        )
        published_event_id = worker._publish_completion_event_from_record(
            record=record,
            outputs=merged_outputs,
            branch_port=branch_port,
        )
        record.status = "succeeded"
        record.outputs = merged_outputs
        record.published_event_id = published_event_id
        record.completion_event_enqueued_at = finished_at
        record.finished_at = finished_at
        worker.execution_store.put(record)
        worker._update_event_delivery_state(record.event_id)
        dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
        runtime_dir = getattr(getattr(worker, "config", None), "runtime_dir", None)
        if runtime_dir is not None and dispatch_id:
            clear_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                dispatch_id=dispatch_id,
            )
        cls._release_enqueue_prompt_lock(
            worker=worker,
            record=record,
            outputs=merged_outputs,
        )
        cls._release_local_inference_capacity(
            worker=worker,
            record=record,
            outputs=merged_outputs,
        )
        wake_dispatch_id = str(queue_record.payload.get("wake_dispatch_id") or "").strip()
        if wake_dispatch_id:
            wake_store = getattr(getattr(worker, "runtime", None), "wake_store", None)
            mark_completed = getattr(wake_store, "mark_completed_by_dispatch_id", None)
            if callable(mark_completed):
                scope = queue_record.payload.get("scope")
                if not isinstance(scope, Mapping):
                    scope = {}
                completed_at = finished_at
                evidence = {
                    "trigger_event_id": queue_record.event_id,
                    "trigger_type": queue_record.trigger_type,
                    "dispatch_id": wake_dispatch_id,
                    "session_id": queue_record.payload.get("session_id") or scope.get("session_id"),
                    "directory_path": scope.get("directory_path"),
                    "vm_user": scope.get("vm_user"),
                    "agent_state": queue_record.payload.get("agent_state"),
                    "completed_at": completed_at.isoformat().replace("+00:00", "Z"),
                }
                try:
                    mark_completed(
                        wake_dispatch_id,
                        completed_at=completed_at,
                        evidence={
                            key: value for key, value in evidence.items() if value is not None
                        },
                    )
                except Exception:
                    logger.warning(
                        "Failed to mark queued container wake completed dispatch_id=%s event_id=%s",
                        wake_dispatch_id,
                        queue_record.event_id,
                        exc_info=True,
                    )

    @classmethod
    def _release_enqueue_prompt_lock(
        cls,
        *,
        worker: Any,
        record: Any,
        outputs: Mapping[str, Any] | None = None,
    ) -> None:
        if (
            str(getattr(record, "effect_type", "") or "").strip()
            != AGENT_ENQUEUE_PROMPT_EFFECT_TYPE
        ):
            return
        lock_key = cls._record_text_value_from_sources(
            record=record,
            outputs=outputs,
            key="prompt_lock_key",
        )
        owner_id = cls._record_text_value_from_sources(
            record=record,
            outputs=outputs,
            key="prompt_lock_owner_id",
        )
        if not lock_key or not owner_id:
            return
        activity = getattr(worker, "_agent_prompt_activity", None)
        if activity is None:
            activity = getattr(
                getattr(worker, "provider_factory", None), "agent_prompt_activity", None
            )
        release = getattr(activity, "release_enqueue_prompt", None)
        if not callable(release):
            return
        try:
            release(lock_key=lock_key, owner_id=owner_id)
        except Exception:
            logger.warning(
                "Failed to release enqueue prompt lock lock_key=%s owner_id=%s",
                lock_key,
                owner_id,
                exc_info=True,
            )

    @classmethod
    def _release_local_inference_capacity(
        cls,
        *,
        worker: Any,
        record: Any,
        outputs: Mapping[str, Any] | None = None,
    ) -> None:
        slot_id = cls._record_text_value_from_sources(
            record=record,
            outputs=outputs,
            key="local_inference_slot_id",
        )
        if not slot_id:
            return
        runtime_dir = resolve_runtime_dir(worker)
        if runtime_dir is None:
            return
        try:
            local_inference_capacity_store(runtime_dir).release(slot_id)
        except Exception:
            logger.warning(
                "Failed to release local inference capacity slot slot_id=%s",
                slot_id,
                exc_info=True,
            )

    @staticmethod
    def _record_text_value_from_sources(
        *,
        record: Any,
        outputs: Mapping[str, Any] | None,
        key: str,
    ) -> str | None:
        for source in (
            outputs,
            getattr(record, "outputs", None),
            getattr(record, "resolved_config", None),
            getattr(record, "input_payload", None),
        ):
            if not isinstance(source, Mapping) or key not in source:
                continue
            text = str(source.get(key) or "").strip()
            if text:
                return text
        return None

    @classmethod
    def _maybe_start_reduced_context_retry(
        cls,
        *,
        worker: Any,
        record: Any,
        queue_record: TriggerQueueRecord,
        merged_outputs: dict[str, Any],
    ) -> bool:
        reason = context_exhaustion_reason(merged_outputs) or context_exhaustion_reason(
            queue_record.payload
        )
        if reason is None:
            return False
        if (
            str(merged_outputs.get("execution_backend") or "").strip()
            != ExecutionBackendKind.CONTAINER_WORKER_VM.value
        ):
            merged_outputs["context_retry_skipped_reason"] = "unsupported_execution_backend"
            return False

        session_id = str(merged_outputs.get("session_id") or "").strip()
        previous_execution_id = str(
            merged_outputs.get("worker_execution_id") or merged_outputs.get("execution_id") or ""
        ).strip()
        if not session_id or not previous_execution_id:
            merged_outputs["context_retry_skipped_reason"] = "missing_session_or_execution_id"
            return False

        backend = cls._container_backend_for_worker(worker=worker, session_id=session_id)
        if backend is None:
            merged_outputs["context_retry_skipped_reason"] = "container_backend_unavailable"
            return False
        config = getattr(backend, "config", None) or getattr(worker, "config", None)

        if not cls._context_retry_enabled_for_record(record=record, config=config):
            merged_outputs["context_retry_skipped_reason"] = "disabled"
            return False
        max_attempts = cls._context_retry_max_attempts_for_record(record=record, config=config)
        attempt = cls._context_retry_attempt_from_record(record)
        if attempt >= max_attempts:
            merged_outputs["context_retry_skipped_reason"] = "attempts_exhausted"
            merged_outputs["context_retry_attempt"] = attempt
            merged_outputs["context_retry_max_attempts"] = max_attempts
            return False

        original_prompt = cls._record_text_value(record, "prompt")
        if not original_prompt:
            merged_outputs["context_retry_skipped_reason"] = "missing_original_prompt"
            return False

        session = cls._session_for_context_retry(worker=worker, session_id=session_id)
        agent_harness = cls._agent_harness_from_session(session, config=config)
        if str(agent_harness or "").strip().casefold() != "codex":
            merged_outputs["context_retry_skipped_reason"] = "non_codex_agent_provider"
            return False

        model = (
            cls._record_text_value(record, "model")
            or str(getattr(session, "model", None) or "").strip()
            or default_agent_model_for_provider(agent_harness, config)
        )
        target_chars = cls._context_retry_target_chars_for_record(record=record, config=config)
        retry_prompt = build_reduced_context_retry_prompt(
            original_prompt=original_prompt,
            reduced_prompt=(
                cls._record_text_value(record, "reduced_context_prompt")
                or cls._record_text_value(record, "context_retry_prompt")
            ),
            failure_message=str(merged_outputs.get("message") or "").strip() or None,
            failure_reason=reason,
            target_chars=target_chars,
        )
        retry_execution_id = safe_effect_execution_id("agent", session_id)
        dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
        directory_path = (
            str(getattr(session, "directory_path", None) or "").strip()
            or str(getattr(record, "resolved_config", {}).get("directory_path") or "").strip()
            or "root"
        )
        retry_mcp_gateway_url = cls._context_retry_mcp_gateway_url(
            record=record,
            config=config,
            session_id=session_id,
            session=session,
        )
        codex_turn = container_codex_turn_spec(
            prompt=retry_prompt,
            model=model,
            resume_thread_id=None,
            session=session,
            config=config,
            agent_provider=agent_harness,
            resume_mode="new_context",
            mcp_gateway_url=retry_mcp_gateway_url,
            web_search_enabled=bool(getattr(session, "web_search_enabled", False)),
        )
        if codex_turn is None:
            merged_outputs["context_retry_skipped_reason"] = "non_codex_agent_provider"
            return False
        try:
            handle = backend.start(
                backend.prepare(
                    ExecutionSpec(
                        backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                        target_name=retry_execution_id,
                        session_id=session_id,
                        directory_path=directory_path,
                        image=container_worker_image_for_session(session, config),
                        resource_limits=container_worker_resource_limits_for_session(session),
                        command=cls._container_prompt_command(
                            prompt=retry_prompt,
                            model=model,
                            resume_thread_id=None,
                            session=session,
                            agent_provider=agent_harness,
                            resume_mode="new_context",
                            mcp_gateway_url=retry_mcp_gateway_url,
                            web_search_enabled=bool(getattr(session, "web_search_enabled", False)),
                        ),
                        codex_turn=codex_turn,
                        metadata={
                            "effect_type": str(getattr(record, "effect_type", "") or ""),
                            "dispatch_id": dispatch_id,
                            "resume_thread_id": None,
                            "agent_provider": agent_harness,
                            "model": model,
                            "model_provider_mode": getattr(config, "model_provider_mode", None),
                            "codex_auth_mode": codex_turn.auth_mode.value,
                            **(
                                {"subscription_profile_id": (codex_turn.subscription_profile_id)}
                                if codex_turn.subscription_profile_id is not None
                                else {}
                            ),
                            "context_retry_mode": CONTEXT_RETRY_MODE_REDUCED,
                            "context_retry_attempt": attempt + 1,
                            "context_retry_max_attempts": max_attempts,
                            "context_retry_of_execution_id": previous_execution_id,
                            "context_retry_reason": reason,
                        },
                    )
                )
            )
        except CodexSubscriptionDeferredClientError as exc:
            try:
                return cls._queue_deferred_context_retry(
                    worker=worker,
                    record=record,
                    session=session,
                    session_id=session_id,
                    dispatch_id=dispatch_id,
                    directory_path=directory_path,
                    prompt=retry_prompt,
                    model=model,
                    model_provider_mode=str(
                        getattr(config, "model_provider_mode", None) or ""
                    ).strip()
                    or None,
                    deferral=exc.deferral,
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                    previous_execution_id=previous_execution_id,
                    reason=reason,
                    target_chars=target_chars,
                )
            except Exception as queue_exc:  # noqa: BLE001
                merged_outputs["context_retry_skipped_reason"] = "retry_dispatch_failed"
                merged_outputs["context_retry_dispatch_error"] = sanitize_text(str(queue_exc))
                return False
        except Exception as exc:  # noqa: BLE001
            merged_outputs["context_retry_skipped_reason"] = "retry_dispatch_failed"
            merged_outputs["context_retry_dispatch_error"] = sanitize_text(str(exc))
            return False

        retry_history = cls._context_retry_history(record)
        retry_history.append(
            {
                "attempt": attempt + 1,
                "mode": CONTEXT_RETRY_MODE_REDUCED,
                "reason": reason,
                "previous_execution_id": previous_execution_id,
                "retry_execution_id": handle.execution_id,
                "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            }
        )
        retry_outputs = dict(record.outputs)
        retry_outputs.update(
            {
                "success": True,
                "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                "execution_id": handle.execution_id,
                "worker_execution_id": handle.execution_id,
                "session_id": session_id,
                "thread_id": dispatch_id,
                "context_retry_enqueued": True,
                "context_retry_in_progress": True,
                "context_retry_attempt": attempt + 1,
                "context_retry_max_attempts": max_attempts,
                "context_retry_mode": CONTEXT_RETRY_MODE_REDUCED,
                "context_retry_reason": reason,
                "context_retry_of_execution_id": previous_execution_id,
                "context_retry_latest_execution_id": handle.execution_id,
                "context_retry_target_chars": target_chars,
                "context_retry_history": retry_history[-10:],
                "message": (
                    "Context exhausted; started reduced-context retry "
                    f"{attempt + 1}/{max_attempts} for {previous_execution_id}."
                ),
            }
        )
        record.status = "waiting"
        record.outputs = retry_outputs
        record.finished_at = datetime.now(UTC)
        cls._put_execution_record(worker=worker, record=record)
        update_event_delivery_state = getattr(worker, "_update_event_delivery_state", None)
        if callable(update_event_delivery_state):
            update_event_delivery_state(record.event_id)
        cls._register_context_retry_model_usage(
            worker=worker,
            record=record,
            session_id=session_id,
            dispatch_id=dispatch_id,
            directory_path=directory_path,
            model=model,
            agent_harness=agent_harness,
        )
        return True

    @classmethod
    def _queue_deferred_context_retry(
        cls,
        *,
        worker: Any,
        record: Any,
        session: Any,
        session_id: str,
        dispatch_id: str,
        directory_path: str,
        prompt: str,
        model: str,
        model_provider_mode: str | None,
        deferral: CodexSubscriptionDeferral,
        attempt: int,
        max_attempts: int,
        previous_execution_id: str,
        reason: str,
        target_chars: int,
    ) -> bool:
        runtime_dir = resolve_runtime_dir(worker)
        if runtime_dir is None:
            raise RuntimeError("runtime directory is unavailable for context retry deferral")
        target = cls._record_text_value(record, "target") or session_id
        vm_name = (
            cls._record_text_value(record, "vm_name")
            or str(getattr(session, "vm_provider_id", None) or "").strip()
            or session_id
        )
        queue_key = (
            f"{record.effect_id}:{dispatch_id or previous_execution_id}:"
            f"context-retry-{attempt}:subscription"
        )
        input_payload = getattr(record, "input_payload", None)
        workflow_context = workflow_context_from_sources(
            execution_context=input_payload,
            input_payload=input_payload,
            context_key=NODE_OUTPUTS_CONTEXT_KEY,
        )
        workflow_for_execution_ids = workflow_for_execution_ids_from_sources(
            execution_context=input_payload,
            input_payload=input_payload,
            context_key=_FOR_EXECUTION_IDS_PAYLOAD_KEY,
        )
        metadata = cls._subscription_deferral_metadata(record)
        metadata.update(
            {
                "context_retry_mode": CONTEXT_RETRY_MODE_REDUCED,
                "context_retry_attempt": str(attempt),
                "context_retry_max_attempts": str(max_attempts),
                "context_retry_of_execution_id": previous_execution_id,
                "context_retry_reason": reason,
                "context_retry_target_chars": str(target_chars),
            }
        )
        queued = enqueue_agent_prompt(
            runtime_dir=runtime_dir,
            target=target,
            vm_name=vm_name,
            session_id=session_id,
            prompt=prompt,
            model=model,
            model_provider_mode=model_provider_mode,
            queue_reason=deferral.queue_reason,
            subscription_profile_id=deferral.profile_id,
            retry_not_before=deferral.retry_at,
            capacity_deferral=True,
            vm_user=cls._record_text_value(record, "vm_user") or "agent",
            dispatch_id=dispatch_id or None,
            resume_thread_id=None,
            resume_mode="new_context",
            resume_thread_metadata_key=cls._record_text_value(record, "resume_thread_metadata_key"),
            resume_thread_metadata_collection=cls._record_text_value(
                record, "resume_thread_metadata_collection"
            ),
            effect_id=str(record.effect_id),
            queue_key=queue_key,
            metadata=metadata,
            workflow_context=workflow_context,
            workflow_for_execution_ids=workflow_for_execution_ids,
        )
        now = datetime.now(UTC)
        retry_history = cls._context_retry_history(record)
        retry_history.append(
            {
                "attempt": attempt,
                "mode": CONTEXT_RETRY_MODE_REDUCED,
                "reason": reason,
                "previous_execution_id": previous_execution_id,
                "queue_key": queued.queue_key,
                "queued_at": now.isoformat().replace("+00:00", "Z"),
            }
        )
        record.status = "succeeded"
        record.outputs = {
            **record.outputs,
            "success": True,
            "queued": True,
            "queue_key": queued.queue_key,
            "queue_reason": queued.queue_reason,
            "subscription_profile_id": queued.subscription_profile_id,
            "retry_not_before": queued.retry_not_before,
            "context_retry_enqueued": True,
            "context_retry_in_progress": True,
            "context_retry_attempt": attempt,
            "context_retry_max_attempts": max_attempts,
            "context_retry_mode": CONTEXT_RETRY_MODE_REDUCED,
            "context_retry_reason": reason,
            "context_retry_of_execution_id": previous_execution_id,
            "context_retry_target_chars": target_chars,
            "context_retry_history": retry_history[-10:],
            "message": (
                "Context exhausted; queued reduced-context retry "
                f"{attempt}/{max_attempts} for subscription profile "
                f"{deferral.profile_id!r}."
            ),
        }
        record.error = None
        record.finished_at = now
        cls._put_execution_record(worker=worker, record=record)
        update_event_delivery_state = getattr(worker, "_update_event_delivery_state", None)
        if callable(update_event_delivery_state):
            update_event_delivery_state(record.event_id)
        if dispatch_id:
            clear_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                dispatch_id=dispatch_id,
            )
        cls._release_enqueue_prompt_lock(worker=worker, record=record, outputs=record.outputs)
        cls._release_local_inference_capacity(worker=worker, record=record, outputs=record.outputs)
        return True

    @staticmethod
    def _context_retry_history(record: Any) -> list[dict[str, Any]]:
        value = getattr(record, "outputs", {}).get("context_retry_history")
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, Mapping)]

    @staticmethod
    def _context_retry_attempt_from_record(record: Any) -> int:
        value = getattr(record, "outputs", {}).get("context_retry_attempt")
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _context_retry_enabled_for_record(cls, *, record: Any, config: Any) -> bool:
        value = cls._record_raw_value(record, "agent_context_retry_enabled")
        if value is not None:
            return context_retry_enabled(value)
        return context_retry_enabled(getattr(config, "agent_context_retry_enabled", True))

    @classmethod
    def _context_retry_max_attempts_for_record(cls, *, record: Any, config: Any) -> int:
        value = cls._record_raw_value(record, "agent_context_retry_max_attempts")
        if value is None:
            value = getattr(config, "agent_context_retry_max_attempts", None)
        return context_retry_max_attempts(value)

    @classmethod
    def _context_retry_target_chars_for_record(cls, *, record: Any, config: Any) -> int:
        value = cls._record_raw_value(record, "agent_context_retry_target_chars")
        if value is None:
            value = getattr(config, "agent_context_retry_target_chars", None)
        return context_retry_target_chars(value)

    @classmethod
    def _record_text_value(cls, record: Any, key: str) -> str | None:
        value = cls._record_raw_value(record, key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _record_raw_value(record: Any, key: str) -> Any | None:
        for source_name in ("resolved_config", "input_payload", "outputs"):
            source = getattr(record, source_name, None)
            if isinstance(source, Mapping) and key in source:
                return source.get(key)
        return None

    @staticmethod
    def _session_for_context_retry(*, worker: Any, session_id: str) -> Any | None:
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        repository = getattr(getattr(admin_service, "runtime", None), "session_repository", None)
        get_session = getattr(repository, "get_session", None)
        if not callable(get_session):
            return None
        try:
            return get_session(session_id)
        except Exception:
            return None

    @classmethod
    def _context_retry_mcp_gateway_url(
        cls,
        *,
        record: Any,
        config: Any,
        session_id: str,
        session: Any | None = None,
    ) -> str | None:
        lease_id = cls._record_text_value(record, "mcp_tool_approval_lease_id")
        workflow_run_id = (
            cls._record_text_value(record, "workflow_run_id")
            or str(getattr(record, "trace_id", "") or "").strip()
            or None
        )
        try:
            return gateway_url_with_session_context(
                container_worker_guest_gateway_url(config),
                session_id=session_id,
                workflow_run_id=workflow_run_id,
                mcp_tool_approval_lease_id=lease_id,
                mcp_session_token=session_mcp_auth_token(session),
            )
        except Exception:
            return None

    @staticmethod
    def _put_execution_record(*, worker: Any, record: Any) -> None:
        execution_store = getattr(worker, "execution_store", None)
        put = getattr(execution_store, "put", None)
        if callable(put):
            put(record)
            return
        record_execution = getattr(execution_store, "record_execution", None)
        if callable(record_execution):
            record_execution(record)

    @staticmethod
    def _register_context_retry_model_usage(
        *,
        worker: Any,
        record: Any,
        session_id: str,
        dispatch_id: str,
        directory_path: str,
        model: str,
        agent_harness: str,
    ) -> None:
        runtime_dir = getattr(getattr(worker, "config", None), "runtime_dir", None)
        if runtime_dir is None or not dispatch_id:
            return
        try:
            register_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                session_id=session_id,
                dispatch_id=dispatch_id,
                workflow_run_id=str(getattr(record, "trace_id", "") or "").strip() or None,
                effect_id=str(getattr(record, "effect_id", "") or "").strip() or None,
                execution_backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                directory_path=directory_path,
                model=model,
                trace_id=str(getattr(record, "trace_id", "") or "").strip() or None,
                agent_harness=agent_harness,
            )
        except Exception:
            pass

    @classmethod
    def _build_completion_queue_record_for_waiting_record(
        cls,
        *,
        record: Any,
        summaries: list[Any],
    ) -> TriggerQueueRecord | None:
        dispatch_id = str(record.outputs.get("dispatch_id") or "").strip()
        if not dispatch_id:
            return None
        session_id, vm_name = cls._waiting_target(record)
        summary = cls._match_reconciliation_summary(
            summaries=summaries,
            dispatch_id=dispatch_id,
            session_id=session_id,
            vm_name=vm_name,
            record=record,
        )
        if summary is None:
            return None
        if str(getattr(summary, "agent_state", "") or "").strip() == "running":
            return None

        from evander.daemons.agent_completion_watcher import build_agent_run_completed_event

        completion_event = build_agent_run_completed_event(
            trigger_id="__await-agent-completion__",
            dispatch_id=dispatch_id,
            vm_user=str(record.resolved_config.get("vm_user") or "agent"),
            summary=summary,
            session_id=session_id,
            directory_path=(getattr(summary, "directory_paths", None) or [None])[0],
            wake_dispatch_id=str(record.outputs.get("wake_dispatch_id") or "").strip() or None,
        )
        return TriggerQueueRecord(
            event_id=completion_event.event_id,
            trigger_id=completion_event.trigger_id,
            trigger_type=completion_event.trigger_type,
            trace_id=completion_event.trace_id or record.trace_id,
            occurred_at=completion_event.occurred_at,
            payload=completion_event.payload,
            delivery_state="delivered",
            delivered_at=datetime.now(UTC),
            idempotency_key=f"__await-agent-completion__:{dispatch_id}",
        )

    @staticmethod
    def _waiting_target(record: Any) -> tuple[str | None, str | None]:
        session_id = str(record.resolved_config.get("session_id") or "").strip() or None
        vm_name = str(record.resolved_config.get("vm_name") or "").strip() or None
        target = str(record.resolved_config.get("target") or "").strip() or None
        if target and vm_name is None and target.startswith("vm-"):
            vm_name = target
        if target and session_id is None and not target.startswith("vm-"):
            session_id = target
        return session_id, vm_name

    @staticmethod
    def _match_reconciliation_summary(
        *,
        summaries: list[Any],
        dispatch_id: str,
        session_id: str | None,
        vm_name: str | None,
        record: Any,
    ) -> Any | None:
        exact_match = next(
            (
                summary
                for summary in summaries
                if str(getattr(summary, "dispatch_id", "") or "").strip() == dispatch_id
                and (
                    vm_name is None or str(getattr(summary, "vm_name", "") or "").strip() == vm_name
                )
                and (
                    session_id is None
                    or session_id
                    in {
                        str(item or "").strip()
                        for item in (getattr(summary, "session_ids", []) or [])
                    }
                )
            ),
            None,
        )
        if exact_match is not None:
            return exact_match

        if (
            datetime.now(UTC) - record.finished_at
        ).total_seconds() < _AWAIT_AGENT_STALE_GRACE_SECONDS:
            return None

        if vm_name is not None:
            vm_match = next(
                (
                    summary
                    for summary in summaries
                    if str(getattr(summary, "vm_name", "") or "").strip() == vm_name
                    and not str(getattr(summary, "dispatch_id", "") or "").strip()
                    and str(getattr(summary, "agent_state", "") or "").strip() != "running"
                ),
                None,
            )
            if vm_match is not None:
                return vm_match

        if session_id is None:
            return None

        return next(
            (
                summary
                for summary in summaries
                if session_id
                in {str(item or "").strip() for item in (getattr(summary, "session_ids", []) or [])}
                and not str(getattr(summary, "dispatch_id", "") or "").strip()
                and str(getattr(summary, "agent_state", "") or "").strip() != "running"
            ),
            None,
        )

    def _resolve_target(self, payload: dict[str, Any]) -> str:
        explicit_target = self._normalize_optional_string(payload.get("target"))
        if explicit_target:
            return explicit_target
        session_id = self._normalize_optional_string(payload.get("session_id"))
        if session_id:
            return session_id
        vm_name = self._normalize_optional_string(payload.get("vm_name"))
        if vm_name:
            return vm_name
        raise ValueError("effect:agent:prompt requires one of target, session_id, or vm_name")

    def _resolve_execution_backend(
        self,
        payload: dict[str, Any],
        *,
        target: str,
    ) -> tuple[str, GatewayConfig]:
        context = self._context()
        payload_for_resolution = dict(payload)
        payload_for_resolution.setdefault("target", target)
        return selected_execution_backend_for_target(
            context,
            payload_for_resolution,
            directory_path=str(context.metadata.get("directory_path") or "root"),
        )

    def _mcp_gateway_url(
        self,
        *,
        config: GatewayConfig,
        target: str,
        workflow_run_id: str | None,
        mcp_tool_approval_lease_id: str | None,
        base_gateway_url: str | None = None,
    ) -> str | None:
        if not mcp_tool_approval_lease_id:
            return None
        session_id = self._session_id_from_target(target)
        return gateway_url_with_session_context(
            base_gateway_url or config.gateway_url,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
            mcp_session_token=session_mcp_auth_token(
                self._read_all_sessions_safe().get(session_id)
            ),
        )

    def _session_id_from_target(self, target: str) -> str:
        sessions = self._read_all_sessions_safe()
        if target in sessions:
            return target
        matches = [
            session_id
            for session_id, session in sessions.items()
            if self._normalize_optional_string(getattr(session, "vm_id", None)) == target
        ]
        if len(matches) == 1:
            return matches[0]
        return target

    def _resolve_mcp_tool_approval_lease_id(
        self,
        *,
        payload: Mapping[str, Any],
        execution_context: Mapping[str, Any] | None,
    ) -> str | None:
        text = self._normalize_optional_string(payload.get("mcp_tool_approval_lease_id"))
        if text:
            return text
        input_payload = (
            execution_context.get("input_payload")
            if isinstance(execution_context, Mapping)
            else None
        )
        if isinstance(input_payload, Mapping):
            for key in ("mcp_tool_approval_lease_id", "lease_id"):
                text = self._normalize_optional_string(input_payload.get(key))
                if text:
                    return text
        return None

    @classmethod
    def _queued_prompt_metadata(
        cls,
        *,
        payload: Mapping[str, Any],
        workflow_run_id: str | None,
        mcp_tool_approval_lease_id: str | None,
    ) -> dict[str, str]:
        metadata = {
            key: value
            for key, value in {
                "workflow_run_id": cls._normalize_optional_string(workflow_run_id),
                "mcp_tool_approval_lease_id": cls._normalize_optional_string(
                    mcp_tool_approval_lease_id
                ),
            }.items()
            if value
        }
        for key in _QUEUED_PROMPT_CONTEXT_METADATA_KEYS:
            value = cls._normalize_optional_string(payload.get(key))
            if value is not None:
                metadata[key] = value
        return metadata

    def _workflow_run_id(self, execution_context: Mapping[str, Any] | None) -> str | None:
        if not isinstance(execution_context, Mapping):
            return None
        for key in ("workflow_run_id", "workflow_trace_id", "trace_id"):
            text = self._normalize_optional_string(execution_context.get(key))
            if text:
                return text
        return None

    def _trace_id(self, execution_context: Mapping[str, Any] | None) -> str | None:
        if not isinstance(execution_context, Mapping):
            return None
        for key in ("trace_id", "workflow_trace_id"):
            text = self._normalize_optional_string(execution_context.get(key))
            if text:
                return text
        return None

    def _read_all_sessions_safe(self) -> Mapping[str, Any]:
        context = self._context()
        if context.session_lookup is None:
            return {}
        try:
            return context.session_lookup.read_all_sessions()
        except Exception:
            return {}

    def _resolve_session_id_if_available(self, target: str) -> str | None:
        sessions = self._read_all_sessions_safe()
        if target in sessions:
            return target
        try:
            from evander.admin.services.session_helpers import resolve_session_target

            session_id, _session = resolve_session_target(target, sessions)
        except Exception:
            return None
        return session_id

    def _session_for_metadata_lookup(self, *, target: str, session_id: str | None) -> Any | None:
        sessions = self._read_all_sessions_safe()
        if session_id:
            session = sessions.get(session_id)
            if session is not None:
                return session
        try:
            from evander.admin.services.session_helpers import resolve_session_target

            _resolved_session_id, session = resolve_session_target(target, sessions)
        except Exception:
            return None
        return session

    def _effective_resume_thread_id(
        self,
        *,
        resume_thread_id: str | None,
        resume_thread_metadata_key: str | None,
        resume_thread_metadata_collection: str,
        target: str,
        session_id: str | None,
        session: Any | None,
    ) -> str | None:
        explicit = self._usable_resume_thread_id(resume_thread_id)
        if explicit:
            return explicit
        key = normalize_resume_thread_metadata_key(resume_thread_metadata_key)
        if not key:
            return None
        if session is None:
            session = self._session_for_metadata_lookup(target=target, session_id=session_id)
        if session is None:
            return None
        metadata = getattr(session, "metadata", {})
        return self._usable_resume_thread_id(
            resume_thread_id_from_metadata(
                metadata,
                collection=resume_thread_metadata_collection,
                key=key,
            )
        )

    def _persist_resume_thread_metadata_key(
        self,
        *,
        target: str,
        session_id: str | None,
        resume_thread_metadata_key: str | None,
        resume_thread_metadata_collection: str,
        thread_id: str | None,
    ) -> None:
        key = normalize_resume_thread_metadata_key(resume_thread_metadata_key)
        value = self._usable_resume_thread_id(thread_id)
        if not key or not value:
            return
        context = self._context()
        update_fn = getattr(context.session_metadata_updater, "session_update_metadata", None)
        if not callable(update_fn):
            return
        metadata = resume_thread_metadata_patch(
            collection=resume_thread_metadata_collection,
            key=key,
            thread_id=value,
        )
        if not metadata:
            return
        try:
            update_fn(target=session_id or target, metadata=metadata)
        except Exception:
            pass

    def _effect(self) -> AgentPromptEffectDefinition:
        if not isinstance(self.effect_definition, AgentPromptEffectDefinition):
            raise RuntimeError(
                "AgentPromptEffectProvider initialized with a non-agent-prompt effect definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AgentPromptEffectProvider has no runtime context")
        return self.runtime_context

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _usable_resume_thread_id(cls, value: Any) -> str | None:
        text = cls._normalize_optional_string(value)
        if not text:
            return None
        if text.startswith("dispatch-") or text.startswith("chat-thread-"):
            return None
        return text

    @classmethod
    def _require_string(cls, value: Any, *, field_name: str) -> str:
        text = cls._normalize_optional_string(value)
        if not text:
            raise ValueError(f"{field_name} is required")
        return text

    @staticmethod
    def _make_dispatch_id(*, effect_id: str) -> str:
        return f"dispatch-{effect_id}-{uuid4().hex[:12]}"

    @staticmethod
    def _container_prompt_command(
        *,
        prompt: str,
        model: str,
        resume_thread_id: str | None,
        session: object | None = None,
        agent_provider: str | None = None,
        resume_mode: str | None = None,
        mcp_gateway_url: str | None = None,
        web_search_enabled: bool = False,
    ) -> str:
        return container_prompt_command(
            prompt=prompt,
            model=model,
            resume_thread_id=resume_thread_id,
            session=session,
            agent_provider=agent_provider,
            resume_mode=resume_mode,
            mcp_gateway_url=mcp_gateway_url,
            web_search_enabled=web_search_enabled,
        )

    @staticmethod
    def _agent_harness_from_session(session: Any, *, config: GatewayConfig | None = None) -> str:
        metadata = getattr(session, "metadata", {}) if session is not None else {}
        if isinstance(metadata, Mapping):
            candidate = str(
                metadata.get("agent_provider") or metadata.get("agent_harness") or ""
            ).strip()
            if candidate:
                return candidate
        return default_agent_provider_for_config(config)

    @classmethod
    def _thread_id_from_logs(cls, logs: str) -> str | None:
        return container_thread_id_from_logs(logs)

    @classmethod
    def _container_logs_indicate_no_agent_response(cls, logs: str) -> bool:
        if not cls._thread_id_from_logs(logs):
            return False
        useful_lines = [
            line.strip()
            for line in str(logs or "").splitlines()
            if line.strip()
            and not cls._is_container_session_id_log_line(line)
            and not cls._is_codex_failure_diagnostic_log_line(line)
        ]
        return not useful_lines

    @staticmethod
    def _is_container_session_id_log_line(line: str) -> bool:
        normalized = str(line or "").strip().casefold()
        return normalized.startswith("session id:") and len(normalized.split()) == 3

    @staticmethod
    def _is_codex_failure_diagnostic_log_line(line: str) -> bool:
        return str(line or "").strip().casefold().startswith("codex failed:")

    @staticmethod
    def _failed_codex_outcome_from_status_metadata(
        status_metadata: Mapping[str, Any] | None,
    ) -> Mapping[str, Any] | None:
        if not isinstance(status_metadata, Mapping):
            return None
        codex_outcome = status_metadata.get("codex_outcome")
        if not isinstance(codex_outcome, Mapping):
            return None
        status = str(codex_outcome.get("status") or "").strip().casefold()
        if status != "failed":
            return None
        return codex_outcome
