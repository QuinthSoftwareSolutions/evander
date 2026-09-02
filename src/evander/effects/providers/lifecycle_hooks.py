from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from evander.effects.providers.agent_session import (
    container_codex_turn_spec,
    container_prompt_command,
    container_worker_guest_gateway_url,
    container_worker_image_for_session,
    container_worker_resource_limits_for_session,
    default_agent_model_for_provider,
    gateway_url_with_session_context,
    safe_effect_execution_id,
)
from evander.effects.providers.base import EffectLifecycleHookBase
from evander.effects.providers.resume_thread_metadata import (
    normalize_resume_thread_metadata_collection,
    resume_thread_id_from_metadata,
    resume_thread_metadata_patch,
)
from evander.effects.providers.resume_thread_metadata import (
    resume_thread_metadata_key as normalize_resume_thread_metadata_key,
)
from evander.execution import ExecutionBackendKind, ExecutionSpec, build_execution_backend
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
from evander.messaging.agent_prompt_lock import prompt_lock_metadata
from evander.messaging.agent_prompt_queue import (
    agent_prompt_replay_queue_key,
    queued_agent_prompt_dispatch_id,
)
from evander.messaging.scheduled_wake_queue import (
    SCHEDULED_WAKE_QUEUE_PREFIX,
    scheduled_wake_queue_should_be_discarded,
    scheduled_wake_request_id_from_queue_key,
)
from evander.model_usage.runtime import (
    register_model_usage_correlation_context,
    resolve_runtime_dir,
)
from evander.orchestration.effect_extensions import (
    dispatch_agent_prompt,
    list_vm_summaries_for_agent_reconciliation,
    resolve_agent_prompt_dispatch_port,
)
from evander.session_auth import session_mcp_auth_token
from evander.session_expiry import (
    SessionExpiredError,
    ensure_session_not_expired,
    session_expired_message,
)
from evander.workflow.contracts import (
    FOR_EXECUTION_IDS_CONTEXT_KEY,
    NODE_OUTPUTS_CONTEXT_KEY,
)
from evander.workflow.lifecycle_ports import AGENT_PROMPT_STARTED_PORT

logger = logging.getLogger(__name__)

_ADMIN_AGENT_PROMPT_EFFECT_ID_PREFIX = "admin-agent-prompt:"
_AGENT_ENQUEUE_PROMPT_EFFECT_TYPE = "effect:agent:enqueue-prompt"
_ACTIVE_ENQUEUE_PROMPT_STATUSES = {"running", "waiting"}
_RECONCILED_QUEUE_EXECUTION_STATUSES = {
    "waiting",
    "completion_pending",
    "succeeded",
    "failed",
    "error_routed",
    "skipped",
}
_QUEUED_PROMPT_RESTORED_METADATA_KEYS = (
    "workflow_run_id",
    "mcp_tool_approval_lease_id",
    "reduced_context_prompt",
    "context_retry_prompt",
    "agent_context_retry_enabled",
    "agent_context_retry_max_attempts",
    "agent_context_retry_target_chars",
)


class _DeniedPromptLockClaim:
    claimed = False


def has_active_enqueue_prompt_for_target(
    execution_store: Any,
    *,
    target: str,
    vm_name: str,
    session_id: str | None = None,
    current_idempotency_key: str | None = None,
    owner_id: str | None = None,
) -> bool:
    normalized_current_key = str(current_idempotency_key or "").strip()
    normalized_owner_id = str(owner_id or "").strip()
    try:
        records = _list_enqueue_prompt_activity_records(execution_store)
    except Exception:
        return False
    current_order_key = _current_enqueue_prompt_order_key(
        records,
        current_idempotency_key=normalized_current_key,
    )
    for execution in records:
        execution_owner_id = str(getattr(execution, "idempotency_key", "") or "").strip()
        if normalized_owner_id and execution_owner_id != normalized_owner_id:
            continue
        if normalized_current_key and execution_owner_id == normalized_current_key:
            continue
        if current_order_key is not None:
            execution_order_key = _enqueue_prompt_order_key(execution)
            if execution_order_key is not None and execution_order_key > current_order_key:
                continue
        if _enqueue_prompt_execution_matches_target(
            execution,
            session_id=session_id,
            vm_name=vm_name,
            target=target,
        ):
            return True
    return False


def _list_enqueue_prompt_activity_records(execution_store: Any) -> list[Any]:
    list_active = getattr(execution_store, "list_active_records", None)
    list_executions = getattr(execution_store, "list_executions", None)
    list_waiting = getattr(execution_store, "list_waiting_records", None)
    read_all = getattr(execution_store, "read_all", None)
    if callable(list_active):
        candidates = list_active()
    elif callable(list_executions):
        candidates = list_executions()
    elif callable(list_waiting):
        candidates = list_waiting(_AGENT_ENQUEUE_PROMPT_EFFECT_TYPE)
    elif callable(read_all):
        candidates = list(read_all().values())
    else:
        return []
    return [record for record in candidates if _enqueue_prompt_execution_is_active(record)]


def _current_enqueue_prompt_order_key(
    records: list[Any],
    *,
    current_idempotency_key: str,
) -> tuple[float, str] | None:
    if not current_idempotency_key:
        return None
    for record in records:
        if str(getattr(record, "idempotency_key", "") or "").strip() != current_idempotency_key:
            continue
        return _enqueue_prompt_order_key(record)
    return None


def _enqueue_prompt_order_key(execution: Any) -> tuple[float, str] | None:
    started_at = getattr(execution, "started_at", None)
    timestamp = started_at.timestamp() if isinstance(started_at, datetime) else None
    if timestamp is None:
        return None
    return (timestamp, str(getattr(execution, "execution_id", "") or ""))


def _enqueue_prompt_execution_is_active(execution: Any) -> bool:
    if (
        str(getattr(execution, "effect_type", "") or "").strip()
        != _AGENT_ENQUEUE_PROMPT_EFFECT_TYPE
    ):
        return False
    status = str(getattr(execution, "status", "") or "").strip()
    if status in _ACTIVE_ENQUEUE_PROMPT_STATUSES:
        return True
    if status != "completion_pending":
        return False
    pending_status = str(getattr(execution, "completion_pending_status", "") or "").strip()
    return pending_status in _ACTIVE_ENQUEUE_PROMPT_STATUSES


def _enqueue_prompt_execution_matches_target(
    execution: Any,
    *,
    session_id: str | None,
    vm_name: str,
    target: str,
) -> bool:
    normalized_session_id = str(session_id or "").strip()
    normalized_vm_name = str(vm_name or "").strip()
    normalized_target = str(target or "").strip()
    outputs = getattr(execution, "outputs", None)
    if not isinstance(outputs, dict):
        outputs = {}
    resolved_config = getattr(execution, "resolved_config", None)
    if not isinstance(resolved_config, dict):
        resolved_config = {}
    input_payload = getattr(execution, "input_payload", None)
    if not isinstance(input_payload, dict):
        input_payload = {}
    active_session_id = str(
        outputs.get("session_id")
        or resolved_config.get("session_id")
        or input_payload.get("session_id")
        or ""
    ).strip()
    active_vm_name = str(
        outputs.get("vm_name")
        or resolved_config.get("vm_name")
        or input_payload.get("vm_name")
        or ""
    ).strip()
    active_target = str(
        resolved_config.get("target") or input_payload.get("target") or outputs.get("target") or ""
    ).strip()
    if normalized_session_id and active_session_id == normalized_session_id:
        return True
    if normalized_vm_name and active_vm_name == normalized_vm_name:
        return True
    return bool(normalized_target and active_target == normalized_target)


class ProviderCompletionLifecycleHook(EffectLifecycleHookBase):
    def __init__(self, provider_class: type[Any]) -> None:
        self._provider_class = provider_class

    def on_waiting_reconciliation(self, *, worker: Any) -> int:
        return int(self._provider_class.reconcile_waiting(worker=worker) or 0)

    def on_completion_fan_out(self, *, worker: Any, queue_record: Any, graph: Any) -> int:
        del graph
        return int(
            self._provider_class.on_completion_event(worker=worker, queue_record=queue_record) or 0
        )


class AgentEnqueuePromptQueueDispatchHook(EffectLifecycleHookBase):
    def on_post_completion_dispatch(self, *, worker: Any) -> int:
        queue_store = getattr(worker, "queued_agent_prompt_store", None)
        dispatch_port = resolve_agent_prompt_dispatch_port(worker)
        if queue_store is None or dispatch_port is None:
            return 0

        queued_records = queue_store.read_all()
        if not queued_records:
            return 0

        graph = self._build_current_execution_graph(worker=worker)
        if graph is not None:
            self._discard_non_dispatchable_queue_records(
                queue_store=queue_store,
                records=queued_records,
                graph=graph,
            )
            queued_records = queue_store.read_all()
            if not queued_records:
                return 0

        dispatched = 0
        container_queue_keys = self._container_queue_keys(worker=worker, records=queued_records)
        for queue_key in self._fifo_queue_keys(queued_records, container_queue_keys):
            current_records = queue_store.read_all()
            record = current_records.get(queue_key)
            if record is None:
                continue
            if not self._retry_not_before_ready(record):
                continue
            if self._has_earlier_subscription_profile_record(
                records=current_records,
                queue_key=queue_key,
            ):
                continue
            if self._has_earlier_local_capacity_record(
                records=current_records,
                queue_key=queue_key,
                candidates=set(current_records),
            ):
                continue
            if not self._scheduled_wake_queue_record_is_current(
                worker=worker,
                queue_store=queue_store,
                queue_key=queue_key,
                record=record,
            ):
                continue
            if self._discard_queue_record_for_exact_execution(
                worker=worker,
                queue_store=queue_store,
                queue_key=queue_key,
                record=record,
            ):
                continue
            if self._has_earlier_claimed_replay_record(
                records=current_records,
                queue_key=queue_key,
                record=record,
            ):
                continue
            if self._has_waiting_enqueue_prompt_for_record(worker=worker, record=record):
                continue
            if self._dispatch_container_record_if_idle(
                worker=worker,
                queue_store=queue_store,
                queue_key=queue_key,
                record=record,
            ):
                dispatched += 1

        all_queued_records = queue_store.read_all()
        current_container_queue_keys = self._container_queue_keys(
            worker=worker,
            records=all_queued_records,
        )
        queued_records = {
            queue_key: record
            for queue_key, record in all_queued_records.items()
            if queue_key not in current_container_queue_keys
        }
        if not queued_records:
            return dispatched

        try:
            summaries = {
                str(getattr(item, "vm_name", "") or "").strip(): item
                for item in list_vm_summaries_for_agent_reconciliation(worker)
            }
        except Exception:
            logger.exception(
                "AgentEnqueuePromptQueueDispatchHook inventory reconciliation skipped "
                "phase=post_completion_dispatch queued_non_container_records=%d",
                len(queued_records),
            )
            return dispatched

        for queue_key in self._fifo_queue_keys(queued_records):
            current_records = queue_store.read_all()
            record = current_records.get(queue_key)
            if record is None:
                continue
            if self._has_earlier_local_capacity_record(
                records=current_records,
                queue_key=queue_key,
                candidates=set(current_records),
            ):
                continue
            if not self._scheduled_wake_queue_record_is_current(
                worker=worker,
                queue_store=queue_store,
                queue_key=queue_key,
                record=record,
            ):
                continue
            if self._discard_queue_record_for_exact_execution(
                worker=worker,
                queue_store=queue_store,
                queue_key=queue_key,
                record=record,
            ):
                continue
            if self._has_earlier_claimed_replay_record(
                records=current_records,
                queue_key=queue_key,
                record=record,
            ):
                continue
            if graph is None:
                continue
            effect = graph.effects_by_id.get(record.effect_id)
            if not self._is_dispatchable_libvirt_record(effect=effect, record=record):
                continue
            if self._has_waiting_enqueue_prompt_for_record(worker=worker, record=record):
                continue
            vm_name = str(record.vm_name or "").strip()
            summary = summaries.get(vm_name)
            if (
                summary is not None
                and str(getattr(summary, "agent_state", "") or "").strip() == "running"
            ):
                continue

            dispatch_id = self._dispatch_id_for_queue_record(record)
            claimed_queue_key = agent_prompt_replay_queue_key(record)
            _session_id, session = self._session_context_for_libvirt_record(
                worker=worker,
                record=record,
            )
            if session is not None and self._queued_prompt_session_is_expired(
                queue_key=queue_key,
                session=session,
            ):
                self._delete_queue_record_if_current(
                    queue_store=queue_store,
                    queue_key=queue_key,
                    dispatch_id=dispatch_id,
                )
                continue
            effective_resume_thread_id = self._effective_record_resume_thread_id(
                record=record,
                session=session,
            )
            prompt_lock_claim = self._claim_enqueue_prompt_for_record(
                worker=worker,
                record=record,
                queue_key=claimed_queue_key,
                dispatch_id=dispatch_id,
                execution_backend=ExecutionBackendKind.LIBVIRT_VM.value,
            )
            if prompt_lock_claim is not None and not bool(
                getattr(prompt_lock_claim, "claimed", False)
            ):
                continue
            capacity_admission = self._claim_local_inference_capacity_for_record(
                worker=worker,
                record=record,
                config=self._config_for_libvirt_record(
                    worker=worker,
                    session_id=_session_id or "",
                    session=session,
                ),
                queue_key=queue_key,
                owner_queue_key=claimed_queue_key,
                dispatch_id=dispatch_id,
                execution_backend=ExecutionBackendKind.LIBVIRT_VM.value,
            )
            if not capacity_admission.admitted:
                self._release_enqueue_prompt_claim(worker=worker, claim=prompt_lock_claim)
                continue
            claimed = self._claim_queue_record_for_replay(
                queue_store=queue_store,
                queue_key=queue_key,
                record=record,
            )
            if claimed is None:
                self._release_local_inference_capacity(
                    worker=worker,
                    admission=capacity_admission,
                )
                self._release_enqueue_prompt_claim(worker=worker, claim=prompt_lock_claim)
                continue
            queue_key, record, dispatch_id = claimed
            with maintain_local_inference_launch(
                resolve_runtime_dir(worker),
                capacity_admission.slot_id,
            ):
                result = dispatch_agent_prompt(
                    worker,
                    target=record.target,
                    prompt=record.prompt,
                    model=record.model,
                    vm_user=record.vm_user,
                    dispatch_id=dispatch_id,
                    resume_thread_id=effective_resume_thread_id,
                    resume_mode=str(getattr(record, "resume_mode", None) or "new_context"),
                    persist_thread_id=self._resume_thread_metadata_key(record) is None,
                    mcp_gateway_url=self._mcp_gateway_url_for_libvirt_record(
                        worker=worker,
                        record=record,
                    ),
                )
            if result is not None and bool(getattr(result, "success", False)):
                self._mark_local_inference_running(
                    worker=worker,
                    admission=capacity_admission,
                    target_name=str(record.vm_name or record.target).strip(),
                    execution_id=str(record.vm_name or record.target).strip(),
                )
                self._persist_chat_assistant_thread_id(
                    worker=worker,
                    record=record,
                    result=result,
                    queue_key=queue_key,
                )
                self._persist_resume_thread_metadata_key(
                    worker=worker,
                    record=record,
                    thread_id=str(getattr(result, "thread_id", "") or "").strip(),
                )
                recorded = self._record_libvirt_waiting_execution(
                    worker=worker,
                    record=record,
                    queue_key=queue_key,
                    dispatch_id=dispatch_id,
                    result=result,
                    effective_resume_thread_id=effective_resume_thread_id,
                    prompt_lock_metadata=self._prompt_lock_metadata_from_claim(prompt_lock_claim),
                    capacity_metadata=capacity_admission.output_metadata(),
                )
                if not recorded:
                    continue
                self._delete_queue_record_if_current(
                    queue_store=queue_store,
                    queue_key=queue_key,
                    dispatch_id=dispatch_id,
                )
                dispatched += 1
            else:
                self._release_local_inference_capacity(
                    worker=worker,
                    admission=capacity_admission,
                )
                self._release_enqueue_prompt_claim(worker=worker, claim=prompt_lock_claim)

        return dispatched

    @staticmethod
    def _fifo_queue_keys(
        records: dict[str, Any],
        candidates: set[str] | None = None,
    ) -> list[str]:
        selected = candidates if candidates is not None else set(records)
        return sorted(
            (key for key in selected if key in records),
            key=lambda key: (
                getattr(records[key], "created_at", datetime.min.replace(tzinfo=UTC)),
                key,
            ),
        )

    @classmethod
    def _has_earlier_local_capacity_record(
        cls,
        *,
        records: dict[str, Any],
        queue_key: str,
        candidates: set[str],
    ) -> bool:
        current = records.get(queue_key)
        if (
            current is None
            or getattr(current, "queue_reason", None) != LOCAL_INFERENCE_QUEUE_REASON
        ):
            return False
        ordered = cls._fifo_queue_keys(records, candidates | {queue_key})
        return any(
            key != queue_key
            and getattr(records[key], "queue_reason", None) == LOCAL_INFERENCE_QUEUE_REASON
            for key in ordered[: ordered.index(queue_key)]
        )

    @classmethod
    def _has_earlier_subscription_profile_record(
        cls,
        *,
        records: dict[str, Any],
        queue_key: str,
    ) -> bool:
        current = records.get(queue_key)
        profile_id = str(getattr(current, "subscription_profile_id", "") or "").strip()
        if not profile_id:
            return False
        ordered = cls._fifo_queue_keys(records)
        try:
            current_index = ordered.index(queue_key)
        except ValueError:
            return False
        return any(
            str(getattr(records[key], "subscription_profile_id", "") or "").strip() == profile_id
            for key in ordered[:current_index]
        )

    @staticmethod
    def _retry_not_before_ready(record: Any) -> bool:
        retry_at = getattr(record, "retry_not_before", None)
        if not isinstance(retry_at, datetime):
            return True
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return retry_at.astimezone(UTC) <= datetime.now(UTC)

    @classmethod
    def _has_earlier_claimed_replay_record(
        cls,
        *,
        records: dict[str, Any],
        queue_key: str,
        record: Any,
    ) -> bool:
        ordered = cls._fifo_queue_keys(records)
        try:
            current_index = ordered.index(queue_key)
        except ValueError:
            return False
        for earlier_key in ordered[:current_index]:
            if ":replay:" not in earlier_key:
                continue
            earlier = records[earlier_key]
            if cls._queued_records_share_target(earlier, record):
                return True
        return False

    @staticmethod
    def _queued_records_share_target(first: Any, second: Any) -> bool:
        first_session = str(getattr(first, "session_id", "") or "").strip()
        second_session = str(getattr(second, "session_id", "") or "").strip()
        if first_session and first_session == second_session:
            return True
        first_vm = str(getattr(first, "vm_name", "") or "").strip()
        second_vm = str(getattr(second, "vm_name", "") or "").strip()
        if first_vm and first_vm == second_vm:
            return True
        first_target = str(getattr(first, "target", "") or "").strip()
        second_target = str(getattr(second, "target", "") or "").strip()
        return bool(first_target and first_target == second_target)

    @staticmethod
    def _is_dispatchable_libvirt_record(*, effect: Any | None, record: Any) -> bool:
        if effect is not None and getattr(effect, "type", "") in {
            "effect:agent:enqueue-prompt",
            "effect:agent:prompt",
        }:
            return True
        effect_id = str(getattr(record, "effect_id", "") or "").strip()
        queue_key = str(getattr(record, "queue_key", "") or "").strip()
        if effect_id.startswith(
            (SCHEDULED_WAKE_QUEUE_PREFIX, "wake-prompt:")
        ) and queue_key.startswith((SCHEDULED_WAKE_QUEUE_PREFIX, "wake-prompt:")):
            return True
        return effect_id.startswith(_ADMIN_AGENT_PROMPT_EFFECT_ID_PREFIX) and queue_key.startswith(
            _ADMIN_AGENT_PROMPT_EFFECT_ID_PREFIX
        )

    @staticmethod
    def _build_current_execution_graph(*, worker: Any) -> Any | None:
        build_graph = getattr(worker, "_build_execution_graph", None)
        if not callable(build_graph):
            return None
        try:
            return build_graph()
        except Exception:
            logger.exception(
                "AgentEnqueuePromptQueueDispatchHook could not build the current graph "
                "before queued-prompt reconciliation"
            )
            return None

    def _discard_non_dispatchable_queue_records(
        self,
        *,
        queue_store: Any,
        records: dict[str, Any],
        graph: Any,
    ) -> int:
        """Remove obsolete workflow rows before they participate in global FIFO admission."""

        effects_by_id = getattr(graph, "effects_by_id", {})
        if not isinstance(effects_by_id, Mapping):
            return 0
        discarded = 0
        for queue_key in self._fifo_queue_keys(records):
            record = records[queue_key]
            if getattr(record, "queue_reason", None) != LOCAL_INFERENCE_QUEUE_REASON:
                continue
            effect = effects_by_id.get(str(getattr(record, "effect_id", "") or "").strip())
            if self._is_dispatchable_libvirt_record(effect=effect, record=record):
                continue
            dispatch_id = self._dispatch_id_for_queue_record(record)
            if not self._delete_queue_record_if_current(
                queue_store=queue_store,
                queue_key=queue_key,
                dispatch_id=dispatch_id,
            ):
                continue
            discarded += 1
            logger.warning(
                "AgentEnqueuePromptQueueDispatchHook discarded non-dispatchable queued "
                "prompt before FIFO admission queue_key=%s dispatch_id=%s effect_id=%s",
                queue_key,
                dispatch_id,
                getattr(record, "effect_id", None),
            )
        return discarded

    def _persist_chat_assistant_thread_id(
        self,
        *,
        worker: Any,
        record: Any,
        result: Any,
        queue_key: str,
    ) -> None:
        if not str(queue_key or "").startswith("chat:"):
            return
        thread_id = str(getattr(result, "thread_id", "") or "").strip()
        session_id = str(getattr(record, "session_id", "") or "").strip()
        if not thread_id or not session_id:
            return
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        vm_agent_admin = getattr(admin_service, "vm_agent_admin", None)
        persist = getattr(vm_agent_admin, "persist_chat_assistant_agent_thread_id", None)
        if not callable(persist):
            return
        try:
            persist(
                session_id=session_id,
                agent_thread_id=thread_id,
                backend=ExecutionBackendKind.LIBVIRT_VM.value,
                assistant_scope="admin_chat",
                queue_key=queue_key,
            )
        except Exception:
            pass

    def _container_queue_keys(self, *, worker: Any, records: dict[str, Any]) -> set[str]:
        keys: set[str] = set()
        for queue_key, record in records.items():
            if self._container_record_session_context(worker=worker, record=record) is not None:
                keys.add(queue_key)
        return keys

    def _claim_enqueue_prompt_for_record(
        self,
        *,
        worker: Any,
        record: Any,
        queue_key: str,
        dispatch_id: str,
        execution_backend: str,
    ) -> Any | None:
        activity = self._agent_prompt_activity(worker)
        claim = getattr(activity, "claim_enqueue_prompt", None)
        if not callable(claim):
            return None
        try:
            return claim(
                target=str(getattr(record, "target", "") or "").strip(),
                vm_name=str(getattr(record, "vm_name", "") or "").strip(),
                session_id=str(getattr(record, "session_id", "") or "").strip() or None,
                owner_id=self._queued_prompt_lock_owner_id(
                    record=record,
                    queue_key=queue_key,
                    dispatch_id=dispatch_id,
                ),
                dispatch_id=dispatch_id,
                effect_id=str(getattr(record, "effect_id", "") or "").strip() or None,
                execution_backend=execution_backend,
            )
        except Exception:
            logger.warning(
                "AgentEnqueuePromptQueueDispatchHook could not claim prompt lock "
                "queue_key=%s dispatch_id=%s",
                queue_key,
                dispatch_id,
                exc_info=True,
            )
            return _DeniedPromptLockClaim()

    def _release_enqueue_prompt_claim(self, *, worker: Any, claim: Any | None) -> None:
        if claim is None or not bool(getattr(claim, "claimed", False)):
            return
        lock_key = str(getattr(claim, "lock_key", "") or "").strip()
        owner_id = str(getattr(claim, "owner_id", "") or "").strip()
        if not lock_key or not owner_id:
            return
        activity = self._agent_prompt_activity(worker)
        release = getattr(activity, "release_enqueue_prompt", None)
        if not callable(release):
            return
        try:
            release(lock_key=lock_key, owner_id=owner_id)
        except Exception:
            logger.warning(
                "AgentEnqueuePromptQueueDispatchHook could not release prompt lock "
                "lock_key=%s owner_id=%s",
                lock_key,
                owner_id,
                exc_info=True,
            )

    def _claim_local_inference_capacity_for_record(
        self,
        *,
        worker: Any,
        record: Any,
        config: Any,
        queue_key: str,
        dispatch_id: str,
        execution_backend: str,
        owner_queue_key: str | None = None,
    ) -> LocalInferenceCapacityAdmission:
        provider_mode = str(getattr(config, "model_provider_mode", None) or "").strip()
        if provider_mode != "local_openai":
            return LocalInferenceCapacityAdmission(enabled=False, admitted=True)
        runtime_dir = resolve_runtime_dir(worker)
        if runtime_dir is None:
            return LocalInferenceCapacityAdmission(enabled=False, admitted=True)
        capacity_store = local_inference_capacity_store(runtime_dir)
        self._reconcile_local_inference_capacity(worker=worker, capacity_store=capacity_store)
        resolved_owner_queue_key = str(owner_queue_key or queue_key).strip()
        return capacity_store.claim(
            max_concurrency=getattr(config, "local_openai_max_concurrency", None),
            owner_id=f"queued-agent-prompt:{resolved_owner_queue_key}:{dispatch_id}",
            model=str(getattr(record, "model", None) or "unknown-local-model"),
            session_id=str(getattr(record, "session_id", None) or "").strip() or None,
            dispatch_id=dispatch_id,
            effect_id=str(getattr(record, "effect_id", None) or "").strip() or None,
            execution_backend=execution_backend,
            target_name=str(getattr(record, "vm_name", None) or "").strip() or None,
            queue_key=queue_key,
        )

    def _reconcile_local_inference_capacity(self, *, worker: Any, capacity_store: Any) -> None:
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
            try:
                summaries = list(list_vm_summaries_for_agent_reconciliation(worker))
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
            if not session_id or not execution_id:
                return None
            repository = self._session_repository_for_worker(worker)
            get_session = getattr(repository, "get_session", None)
            if not callable(get_session):
                return None
            session = get_session(session_id)
            if session is None:
                return False
            config = self._config_for_container_record(
                worker=worker,
                session=session,
                session_id=session_id,
            )
            if config is None:
                return None
            admin_service = getattr(
                getattr(worker, "provider_factory", None), "admin_service", None
            )
            backend = build_execution_backend(
                config.model_copy(
                    update={"execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value}
                ),
                admin_service=admin_service,
            )
            return local_inference_execution_is_active(backend.inspect(execution_id))

        capacity_store.reconcile_running(is_active)

    @staticmethod
    def _mark_local_inference_running(
        *,
        worker: Any,
        admission: LocalInferenceCapacityAdmission,
        target_name: str,
        execution_id: str,
    ) -> None:
        if admission.slot_id is None:
            return
        runtime_dir = resolve_runtime_dir(worker)
        if runtime_dir is not None:
            local_inference_capacity_store(runtime_dir).mark_running(
                admission.slot_id,
                target_name=target_name,
                execution_id=execution_id,
            )

    @staticmethod
    def _release_local_inference_capacity(
        *, worker: Any, admission: LocalInferenceCapacityAdmission
    ) -> None:
        if admission.slot_id is None:
            return
        runtime_dir = resolve_runtime_dir(worker)
        if runtime_dir is not None:
            local_inference_capacity_store(runtime_dir).release(admission.slot_id)

    @staticmethod
    def _prompt_lock_metadata_from_claim(claim: Any | None) -> dict[str, Any]:
        if claim is None or not bool(getattr(claim, "claimed", False)):
            return {}
        return prompt_lock_metadata(getattr(claim, "record", None))

    @staticmethod
    def _queued_prompt_lock_owner_id(
        *,
        record: Any,
        queue_key: str,
        dispatch_id: str,
    ) -> str:
        event_id = f"queued-agent-prompt:{queue_key}:{dispatch_id}"
        return f"{event_id}::{getattr(record, 'effect_id', '')}"

    @staticmethod
    def _agent_prompt_activity(worker: Any) -> Any | None:
        activity = getattr(worker, "_agent_prompt_activity", None)
        if activity is not None:
            return activity
        return getattr(getattr(worker, "provider_factory", None), "agent_prompt_activity", None)

    def _dispatch_container_record_if_idle(
        self,
        *,
        worker: Any,
        queue_store: Any,
        queue_key: str,
        record: Any,
    ) -> bool:
        context = self._container_record_session_context(worker=worker, record=record)
        if context is None:
            return False
        session_id, session, config = context

        if not str(
            getattr(record, "subscription_profile_id", "") or ""
        ).strip() and self._queued_prompt_session_is_expired(queue_key=queue_key, session=session):
            self._delete_queue_record_if_current(
                queue_store=queue_store,
                queue_key=queue_key,
                dispatch_id=self._dispatch_id_for_queue_record(record),
            )
            return False

        dispatch_id = self._dispatch_id_for_queue_record(record)
        claimed_queue_key = agent_prompt_replay_queue_key(record)

        from evander.effects.providers.agent_enqueue_prompt import (
            AgentEnqueuePromptEffectProvider,
        )

        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        try:
            backend = build_execution_backend(
                config.model_copy(
                    update={"execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value}
                ),
                admin_service=admin_service,
            )
        except Exception:
            return False

        if AgentEnqueuePromptEffectProvider.container_session_is_busy_for_backend(
            backend=backend,
            session_id=session_id,
        ):
            return False

        prompt_lock_claim = self._claim_enqueue_prompt_for_record(
            worker=worker,
            record=record,
            queue_key=claimed_queue_key,
            dispatch_id=dispatch_id,
            execution_backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
        )
        if prompt_lock_claim is not None and not bool(getattr(prompt_lock_claim, "claimed", False)):
            return False
        capacity_admission = self._claim_local_inference_capacity_for_record(
            worker=worker,
            record=record,
            config=config,
            queue_key=queue_key,
            owner_queue_key=claimed_queue_key,
            dispatch_id=dispatch_id,
            execution_backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
        )
        if not capacity_admission.admitted:
            self._release_enqueue_prompt_claim(worker=worker, claim=prompt_lock_claim)
            return False
        claimed = self._claim_queue_record_for_replay(
            queue_store=queue_store,
            queue_key=queue_key,
            record=record,
        )
        if claimed is None:
            self._release_local_inference_capacity(
                worker=worker,
                admission=capacity_admission,
            )
            self._release_enqueue_prompt_claim(worker=worker, claim=prompt_lock_claim)
            return False
        queue_key, record, dispatch_id = claimed
        execution_id = safe_effect_execution_id("agent", session_id)
        record_metadata = getattr(record, "metadata", {})
        workflow_run_id = (
            str(record_metadata.get("workflow_run_id") or "").strip()
            if isinstance(record_metadata, dict)
            else ""
        )
        mcp_tool_approval_lease_id = (
            str(record_metadata.get("mcp_tool_approval_lease_id") or "").strip()
            if isinstance(record_metadata, dict)
            else ""
        )
        mcp_gateway_url = gateway_url_with_session_context(
            container_worker_guest_gateway_url(config),
            session_id=session_id,
            workflow_run_id=workflow_run_id or None,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id or None,
            mcp_session_token=session_mcp_auth_token(session),
        )
        effective_resume_thread_id = self._effective_record_resume_thread_id(
            record=record,
            session=session,
        )
        from evander.effects.providers.agent_prompt import AgentPromptEffectProvider

        agent_harness = AgentPromptEffectProvider._agent_harness_from_session(
            session,
            config=config,
        )
        model = str(
            record.model
            or getattr(session, "model", None)
            or default_agent_model_for_provider(agent_harness, config)
        ).strip()
        codex_turn = container_codex_turn_spec(
            prompt=record.prompt,
            model=model,
            resume_thread_id=effective_resume_thread_id,
            session=session,
            config=config,
            agent_provider=agent_harness,
            resume_mode=getattr(record, "resume_mode", None),
            mcp_gateway_url=mcp_gateway_url,
            web_search_enabled=bool(getattr(session, "web_search_enabled", False)),
        )
        try:
            with maintain_local_inference_launch(
                resolve_runtime_dir(worker),
                capacity_admission.slot_id,
            ):
                handle = backend.start(
                    backend.prepare(
                        ExecutionSpec(
                            backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                            target_name=execution_id,
                            session_id=session_id,
                            directory_path=getattr(session, "directory_path", None) or "root",
                            image=container_worker_image_for_session(session, config),
                            resource_limits=container_worker_resource_limits_for_session(session),
                            command=container_prompt_command(
                                prompt=record.prompt,
                                model=model,
                                resume_thread_id=effective_resume_thread_id,
                                session=session,
                                agent_provider=agent_harness,
                                resume_mode=getattr(record, "resume_mode", None),
                                mcp_gateway_url=mcp_gateway_url,
                                web_search_enabled=bool(
                                    getattr(session, "web_search_enabled", False)
                                ),
                            ),
                            codex_turn=codex_turn,
                            metadata={
                                "effect_type": "effect:agent:enqueue-prompt",
                                "dispatch_id": dispatch_id,
                                "agent_provider": agent_harness,
                                "model": model,
                                "model_provider_mode": config.model_provider_mode,
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
                                "resume_thread_id": effective_resume_thread_id,
                                "queued": True,
                            },
                        )
                    )
                )
        except Exception as exc:
            self._release_local_inference_capacity(
                worker=worker,
                admission=capacity_admission,
            )
            self._release_enqueue_prompt_claim(worker=worker, claim=prompt_lock_claim)
            if isinstance(exc, CodexSubscriptionDeferredClientError):
                try:
                    queue_store.defer_capacity(
                        record,
                        queue_reason=exc.deferral.queue_reason,
                        retry_at=exc.deferral.retry_at,
                    )
                except Exception:
                    logger.warning(
                        "Could not persist subscription capacity deferral queue_key=%s",
                        queue_key,
                        exc_info=True,
                    )
            return False
        self._mark_local_inference_running(
            worker=worker,
            admission=capacity_admission,
            target_name=str(record.vm_name or session_id).strip(),
            execution_id=handle.execution_id,
        )
        if not self._record_container_waiting_execution(
            worker=worker,
            record=record,
            queue_key=queue_key,
            session=session,
            session_id=session_id,
            dispatch_id=dispatch_id,
            execution_id=handle.execution_id,
            model=model,
            agent_harness=agent_harness,
            effective_resume_thread_id=effective_resume_thread_id,
            prompt_lock_metadata=self._prompt_lock_metadata_from_claim(prompt_lock_claim),
            capacity_metadata=capacity_admission.output_metadata(),
        ):
            destroyed = False
            try:
                backend.destroy(handle.execution_id)
                destroyed = True
            except Exception:
                pass
            if destroyed:
                self._release_local_inference_capacity(
                    worker=worker,
                    admission=capacity_admission,
                )
                self._release_enqueue_prompt_claim(worker=worker, claim=prompt_lock_claim)
            return False
        self._delete_queue_record_if_current(
            queue_store=queue_store,
            queue_key=queue_key,
            dispatch_id=dispatch_id,
        )
        return True

    def _scheduled_wake_queue_record_is_current(
        self,
        *,
        worker: Any,
        queue_store: Any,
        queue_key: str,
        record: Any,
    ) -> bool:
        request_id = self._scheduled_wake_request_id_for_record(
            queue_key=queue_key,
            record=record,
        )
        if request_id is None:
            return True

        wake_store = self._wake_store_for_worker(worker)
        get_request = getattr(wake_store, "get_request", None)
        if wake_store is None or not callable(get_request):
            return True

        request = get_request(request_id)
        if request is None or scheduled_wake_queue_should_be_discarded(request):
            self._delete_queue_record_if_current(
                queue_store=queue_store,
                queue_key=queue_key,
                dispatch_id=self._dispatch_id_for_queue_record(record),
            )
            return False

        if str(getattr(request, "status", "") or "").strip().casefold() != "queued":
            self._delete_queue_record_if_current(
                queue_store=queue_store,
                queue_key=queue_key,
                dispatch_id=self._dispatch_id_for_queue_record(record),
            )
            return False

        record_metadata = getattr(record, "metadata", {})
        queue_dispatch_id = (
            str(record_metadata.get("wake_dispatch_id") or "").strip()
            if isinstance(record_metadata, dict)
            else ""
        )
        request_dispatch_id = str(getattr(request, "last_dispatch_id", "") or "").strip()
        if queue_dispatch_id and request_dispatch_id and queue_dispatch_id != request_dispatch_id:
            self._delete_queue_record_if_current(
                queue_store=queue_store,
                queue_key=queue_key,
                dispatch_id=self._dispatch_id_for_queue_record(record),
            )
            return False

        return True

    @staticmethod
    def _dispatch_id_for_queue_record(record: Any) -> str:
        return queued_agent_prompt_dispatch_id(record)

    @staticmethod
    def _claim_queue_record_for_replay(
        *,
        queue_store: Any,
        queue_key: str,
        record: Any,
    ) -> tuple[str, Any, str] | None:
        dispatch_id = queued_agent_prompt_dispatch_id(record)
        claimed_queue_key = agent_prompt_replay_queue_key(record)
        claim = getattr(queue_store, "claim_for_replay", None)
        if not callable(claim):
            return None
        try:
            claimed_record = claim(
                queue_key,
                dispatch_id,
                claimed_queue_key=claimed_queue_key,
            )
        except Exception:
            logger.warning(
                "AgentEnqueuePromptQueueDispatchHook could not atomically claim queued "
                "prompt queue_key=%s dispatch_id=%s",
                queue_key,
                dispatch_id,
                exc_info=True,
            )
            return None
        if claimed_record is None:
            return None
        return claimed_queue_key, claimed_record, dispatch_id

    @staticmethod
    def _delete_queue_record_if_current(
        *,
        queue_store: Any,
        queue_key: str,
        dispatch_id: str,
    ) -> bool:
        delete_if_dispatch_id = getattr(queue_store, "delete_if_dispatch_id", None)
        if not callable(delete_if_dispatch_id):
            return False
        return bool(delete_if_dispatch_id(queue_key, dispatch_id))

    @staticmethod
    def _scheduled_wake_request_id_for_record(
        *,
        queue_key: str,
        record: Any,
    ) -> str | None:
        if not str(queue_key or "").strip().startswith(SCHEDULED_WAKE_QUEUE_PREFIX):
            return None
        metadata = getattr(record, "metadata", {})
        if isinstance(metadata, dict):
            request_id = str(metadata.get("request_id") or "").strip()
            if request_id:
                return request_id
        return scheduled_wake_request_id_from_queue_key(queue_key)

    @staticmethod
    def _wake_store_for_worker(worker: Any) -> Any | None:
        runtime = getattr(worker, "runtime", None)
        wake_store = getattr(runtime, "wake_store", None)
        if wake_store is not None:
            return wake_store
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        runtime = getattr(admin_service, "runtime", None)
        return getattr(runtime, "wake_store", None)

    def _record_container_waiting_execution(
        self,
        *,
        worker: Any,
        record: Any,
        queue_key: str,
        session: Any,
        session_id: str,
        dispatch_id: str,
        execution_id: str,
        model: str,
        agent_harness: str,
        effective_resume_thread_id: str | None,
        prompt_lock_metadata: dict[str, Any] | None = None,
        capacity_metadata: dict[str, Any] | None = None,
    ) -> bool:
        execution_store = getattr(worker, "execution_store", None)
        record_execution = getattr(execution_store, "record_execution", None)
        if not callable(record_execution):
            return False

        from evander.daemons.effect_orchestrator import EffectExecutionRecord

        event_id = f"queued-agent-prompt:{queue_key}:{dispatch_id}"
        idempotency_key = f"{event_id}::{record.effect_id}"
        find_by_idempotency_key = getattr(
            execution_store,
            "find_execution_by_idempotency_key",
            None,
        )
        if (
            callable(find_by_idempotency_key)
            and find_by_idempotency_key(idempotency_key) is not None
        ):
            return True

        now = datetime.now(UTC)
        directory_path = str(getattr(session, "directory_path", None) or "root")
        vm_user = str(getattr(record, "vm_user", None) or "agent")
        wake_dispatch_id = ""
        if isinstance(getattr(record, "metadata", {}), dict):
            wake_dispatch_id = str(record.metadata.get("wake_dispatch_id") or "").strip()
        restored_metadata = self._queued_prompt_restored_metadata(record)
        lock_metadata = dict(prompt_lock_metadata or {})
        local_capacity_metadata = dict(capacity_metadata or {})
        resolved_config = {
            "target": record.target,
            "vm_name": record.vm_name,
            "session_id": session_id,
            "prompt": record.prompt,
            "model": model,
            "vm_user": vm_user,
            "resume_thread_id": effective_resume_thread_id,
            "resume_mode": getattr(record, "resume_mode", None),
            "resume_thread_metadata_key": getattr(record, "resume_thread_metadata_key", None),
            "resume_thread_metadata_collection": self._resume_thread_metadata_collection(record),
            "directory_path": directory_path,
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            "agent_provider": agent_harness,
            "queue_key": queue_key,
            "wake_dispatch_id": wake_dispatch_id,
            **restored_metadata,
            **lock_metadata,
            **local_capacity_metadata,
        }
        outputs = {
            "dispatch_id": dispatch_id,
            "thread_id": effective_resume_thread_id or dispatch_id,
            "success": True,
            "queued": False,
            "dispatched_from_queue": True,
            "output_mode": "on_completion",
            "vm_name": record.vm_name,
            "session_id": session_id,
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            "execution_id": execution_id,
            "queue_key": queue_key,
            "wake_dispatch_id": wake_dispatch_id,
            **restored_metadata,
            **lock_metadata,
            **local_capacity_metadata,
        }
        input_payload = {
            "target": record.target,
            "vm_name": record.vm_name,
            "session_id": session_id,
            "prompt": record.prompt,
            "model": model,
            "vm_user": vm_user,
            "resume_thread_id": effective_resume_thread_id,
            "resume_mode": getattr(record, "resume_mode", None),
            "resume_thread_metadata_key": getattr(record, "resume_thread_metadata_key", None),
            "resume_thread_metadata_collection": self._resume_thread_metadata_collection(record),
            "queue_key": queue_key,
            **restored_metadata,
            **lock_metadata,
            **local_capacity_metadata,
        }
        self._restore_workflow_context(input_payload=input_payload, record=record)
        execution_record = EffectExecutionRecord(
            execution_id=(f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}"),
            idempotency_key=idempotency_key,
            event_id=event_id,
            trace_id=f"queued-agent-prompt:{queue_key}",
            source_node_id=record.effect_id,
            source_trigger_type="effect:agent:enqueue-prompt",
            effect_id=record.effect_id,
            effect_type="effect:agent:enqueue-prompt",
            status="waiting",
            started_at=now,
            finished_at=now,
            resolved_config_hash=self._json_sha256(resolved_config),
            resolved_config=resolved_config,
            input_payload=input_payload,
            outputs=outputs,
            config_revision=getattr(worker, "_last_graph_revision", None),
        )
        try:
            record_execution(execution_record)
        except Exception:
            return False
        runtime_dir = resolve_runtime_dir(worker)
        if runtime_dir is not None:
            record_metadata = getattr(record, "metadata", {})
            register_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                session_id=session_id,
                dispatch_id=dispatch_id,
                workflow_run_id=(
                    str(record_metadata.get("workflow_run_id") or "").strip()
                    if isinstance(record_metadata, dict)
                    else None
                ),
                effect_id=record.effect_id,
                execution_backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                directory_path=directory_path,
                model=model,
                trace_id=execution_record.trace_id,
                agent_harness=agent_harness,
            )
        self._publish_started_event_for_waiting_record(
            worker=worker,
            execution_record=execution_record,
        )
        return True

    @staticmethod
    def _queued_prompt_restored_metadata(record: Any) -> dict[str, str]:
        metadata = getattr(record, "metadata", {})
        if not isinstance(metadata, dict):
            return {}
        restored: dict[str, str] = {}
        for key in _QUEUED_PROMPT_RESTORED_METADATA_KEYS:
            value = metadata.get(key)
            text = str(value).strip() if value is not None else ""
            if text:
                restored[key] = text
        return restored

    def _record_libvirt_waiting_execution(
        self,
        *,
        worker: Any,
        record: Any,
        queue_key: str,
        dispatch_id: str,
        result: Any,
        effective_resume_thread_id: str | None,
        prompt_lock_metadata: dict[str, Any] | None = None,
        capacity_metadata: dict[str, Any] | None = None,
    ) -> bool:
        execution_store = getattr(worker, "execution_store", None)
        record_execution = getattr(execution_store, "record_execution", None)
        if not callable(record_execution):
            return False

        from evander.daemons.effect_orchestrator import EffectExecutionRecord

        event_id = f"queued-agent-prompt:{queue_key}:{dispatch_id}"
        idempotency_key = f"{event_id}::{record.effect_id}"
        find_by_idempotency_key = getattr(
            execution_store,
            "find_execution_by_idempotency_key",
            None,
        )
        if (
            callable(find_by_idempotency_key)
            and find_by_idempotency_key(idempotency_key) is not None
        ):
            return True

        now = datetime.now(UTC)
        session_id, session = self._session_context_for_libvirt_record(
            worker=worker,
            record=record,
        )
        vm_user = str(getattr(record, "vm_user", None) or "agent")
        thread_id = str(getattr(result, "thread_id", "") or "").strip()
        effective_thread_id = thread_id or str(effective_resume_thread_id or "").strip()
        model = str(record.model or getattr(session, "model", None) or "gpt-5.4").strip()
        record_metadata = getattr(record, "metadata", {})
        wake_dispatch_id = (
            str(record_metadata.get("wake_dispatch_id") or "").strip()
            if isinstance(record_metadata, dict)
            else ""
        )
        restored_metadata = self._queued_prompt_restored_metadata(record)
        lock_metadata = dict(prompt_lock_metadata or {})
        local_capacity_metadata = dict(capacity_metadata or {})
        directory_path = (
            str(getattr(session, "directory_path", None) or "").strip()
            or (
                str(record_metadata.get("directory_path") or "").strip()
                if isinstance(record_metadata, dict)
                else ""
            )
            or "root"
        )
        resolved_config = {
            "target": record.target,
            "vm_name": record.vm_name,
            "session_id": session_id,
            "prompt": record.prompt,
            "model": record.model,
            "vm_user": vm_user,
            "resume_thread_id": effective_resume_thread_id,
            "resume_mode": getattr(record, "resume_mode", None),
            "resume_thread_metadata_key": getattr(record, "resume_thread_metadata_key", None),
            "resume_thread_metadata_collection": self._resume_thread_metadata_collection(record),
            "execution_backend": ExecutionBackendKind.LIBVIRT_VM.value,
            "queue_key": queue_key,
            "wake_dispatch_id": wake_dispatch_id,
            **restored_metadata,
            **lock_metadata,
            **local_capacity_metadata,
        }
        outputs = {
            "dispatch_id": dispatch_id,
            "success": True,
            "queued": False,
            "dispatched_from_queue": True,
            "vm_name": record.vm_name,
            "session_id": session_id,
            "execution_backend": ExecutionBackendKind.LIBVIRT_VM.value,
            "queue_key": queue_key,
            "wake_dispatch_id": wake_dispatch_id,
            **restored_metadata,
            **lock_metadata,
            **local_capacity_metadata,
        }
        if effective_thread_id:
            outputs["thread_id"] = effective_thread_id
        input_payload = {
            "target": record.target,
            "vm_name": record.vm_name,
            "session_id": session_id,
            "prompt": record.prompt,
            "model": record.model,
            "vm_user": vm_user,
            "resume_thread_id": effective_resume_thread_id,
            "resume_mode": getattr(record, "resume_mode", None),
            "resume_thread_metadata_key": getattr(record, "resume_thread_metadata_key", None),
            "resume_thread_metadata_collection": self._resume_thread_metadata_collection(record),
            "queue_key": queue_key,
            "wake_dispatch_id": wake_dispatch_id,
            **restored_metadata,
            **lock_metadata,
            **local_capacity_metadata,
        }
        self._restore_workflow_context(input_payload=input_payload, record=record)
        execution_record = EffectExecutionRecord(
            execution_id=(f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}"),
            idempotency_key=idempotency_key,
            event_id=event_id,
            trace_id=f"queued-agent-prompt:{queue_key}",
            source_node_id=record.effect_id,
            source_trigger_type="effect:agent:enqueue-prompt",
            effect_id=record.effect_id,
            effect_type="effect:agent:enqueue-prompt",
            status="waiting",
            started_at=now,
            finished_at=now,
            resolved_config_hash=self._json_sha256(resolved_config),
            resolved_config=resolved_config,
            input_payload=input_payload,
            outputs=outputs,
            config_revision=getattr(worker, "_last_graph_revision", None),
        )
        try:
            record_execution(execution_record)
        except Exception:
            return False
        runtime_dir = resolve_runtime_dir(worker)
        if runtime_dir is not None:
            register_model_usage_correlation_context(
                runtime_dir=runtime_dir,
                session_id=session_id,
                dispatch_id=dispatch_id,
                workflow_run_id=(
                    str(record_metadata.get("workflow_run_id") or "").strip()
                    if isinstance(record_metadata, dict)
                    else None
                ),
                effect_id=record.effect_id,
                execution_backend=ExecutionBackendKind.LIBVIRT_VM.value,
                directory_path=directory_path,
                model=model,
                trace_id=execution_record.trace_id,
            )
        self._publish_started_event_for_waiting_record(
            worker=worker,
            execution_record=execution_record,
        )
        return True

    def _publish_started_event_for_waiting_record(
        self,
        *,
        worker: Any,
        execution_record: Any,
    ) -> None:
        publish_or_mark_pending = getattr(
            worker, "_publish_effect_completion_event_or_mark_pending", None
        )
        if callable(publish_or_mark_pending):
            publish_or_mark_pending(
                record=execution_record,
                outputs=dict(execution_record.outputs),
                branch_port=AGENT_PROMPT_STARTED_PORT,
                pending_status="waiting",
            )
            return
        publish = getattr(worker, "_publish_completion_event_from_record", None)
        if not callable(publish):
            return
        try:
            publish(
                record=execution_record,
                outputs=dict(execution_record.outputs),
                branch_port=AGENT_PROMPT_STARTED_PORT,
            )
        except Exception:
            return

    @staticmethod
    def _restore_workflow_context(*, input_payload: dict[str, Any], record: Any) -> None:
        workflow_context = AgentEnqueuePromptQueueDispatchHook._workflow_context_from_record(record)
        if workflow_context:
            input_payload[NODE_OUTPUTS_CONTEXT_KEY] = workflow_context
        workflow_for_execution_ids = (
            AgentEnqueuePromptQueueDispatchHook._workflow_for_execution_ids_from_record(record)
        )
        if workflow_for_execution_ids:
            input_payload[FOR_EXECUTION_IDS_CONTEXT_KEY] = workflow_for_execution_ids

    def _has_waiting_enqueue_prompt_for_record(self, *, worker: Any, record: Any) -> bool:
        execution_store = getattr(worker, "execution_store", None)
        if execution_store is None:
            return False
        return has_active_enqueue_prompt_for_target(
            execution_store,
            session_id=str(getattr(record, "session_id", "") or "").strip() or None,
            vm_name=str(getattr(record, "vm_name", "") or "").strip(),
            target=str(getattr(record, "target", "") or "").strip(),
        )

    def _discard_queue_record_for_exact_execution(
        self,
        *,
        worker: Any,
        queue_store: Any,
        queue_key: str,
        record: Any,
    ) -> bool:
        dispatch_id = self._dispatch_id_for_queue_record(record)
        effect_id = str(getattr(record, "effect_id", "") or "").strip()
        if not dispatch_id or not effect_id:
            return False
        execution_store = getattr(worker, "execution_store", None)
        find_execution = getattr(execution_store, "find_execution_by_idempotency_key", None)
        if not callable(find_execution):
            return False
        event_id = f"queued-agent-prompt:{queue_key}:{dispatch_id}"
        existing = find_execution(f"{event_id}::{effect_id}")
        if existing is None:
            return False
        if (
            str(getattr(existing, "effect_type", "") or "").strip()
            != _AGENT_ENQUEUE_PROMPT_EFFECT_TYPE
        ):
            return False
        status = str(getattr(existing, "status", "") or "").strip()
        if status not in _RECONCILED_QUEUE_EXECUTION_STATUSES:
            return False
        outputs = getattr(existing, "outputs", {})
        if not isinstance(outputs, dict):
            return False
        if str(outputs.get("queue_key") or "").strip() != queue_key:
            return False
        if str(outputs.get("dispatch_id") or "").strip() != dispatch_id:
            return False
        if not self._publish_started_event_for_reconciled_execution(
            worker=worker,
            execution_record=existing,
        ):
            return True
        self._delete_queue_record_if_current(
            queue_store=queue_store,
            queue_key=queue_key,
            dispatch_id=dispatch_id,
        )
        logger.info(
            "AgentEnqueuePromptQueueDispatchHook reconciled exact queued execution "
            "queue_key=%s dispatch_id=%s status=%s",
            queue_key,
            dispatch_id,
            status,
        )
        return True

    @staticmethod
    def _publish_started_event_for_reconciled_execution(
        *,
        worker: Any,
        execution_record: Any,
    ) -> bool:
        publish = getattr(worker, "_publish_completion_event_from_record", None)
        if not callable(publish):
            return False
        try:
            publish(
                record=execution_record,
                outputs=dict(execution_record.outputs),
                branch_port=AGENT_PROMPT_STARTED_PORT,
            )
        except Exception:
            logger.exception(
                "AgentEnqueuePromptQueueDispatchHook could not republish the started "
                "event for reconciled execution queue_event_id=%s dispatch_id=%s",
                getattr(execution_record, "event_id", None),
                getattr(execution_record, "outputs", {}).get("dispatch_id"),
            )
            return False
        return True

    def _effective_record_resume_thread_id(
        self,
        *,
        record: Any,
        session: Any | None,
    ) -> str | None:
        explicit = self._usable_resume_thread_id(getattr(record, "resume_thread_id", None))
        if explicit:
            return explicit
        key = self._resume_thread_metadata_key(record)
        if not key or session is None:
            return None
        metadata = getattr(session, "metadata", {})
        if not isinstance(metadata, dict):
            return None
        return self._usable_resume_thread_id(
            resume_thread_id_from_metadata(
                metadata,
                collection=self._resume_thread_metadata_collection(record),
                key=key,
            )
        )

    def _persist_resume_thread_metadata_key(
        self,
        *,
        worker: Any,
        record: Any,
        thread_id: str | None,
    ) -> None:
        key = self._resume_thread_metadata_key(record)
        value = self._usable_resume_thread_id(thread_id)
        session_id = str(getattr(record, "session_id", "") or "").strip()
        target = str(getattr(record, "target", "") or "").strip()
        if not key or not value or not (session_id or target):
            return
        metadata_patch = resume_thread_metadata_patch(
            collection=self._resume_thread_metadata_collection(record),
            key=key,
            thread_id=value,
        )
        if not metadata_patch:
            return
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        update_fn = getattr(admin_service, "session_update_metadata", None)
        if callable(update_fn):
            try:
                update_fn(target=session_id or target, metadata=metadata_patch)
                return
            except Exception:
                pass
        repository = self._session_repository_for_worker(worker)
        get_session = getattr(repository, "get_session", None)
        upsert_session = getattr(repository, "upsert_session", None)
        if not callable(get_session) or not callable(upsert_session):
            return
        try:
            resolved_session_id = session_id
            if not resolved_session_id:
                resolved_session_id, _session = self._session_context_for_libvirt_record(
                    worker=worker,
                    record=record,
                )
            if not resolved_session_id:
                return
            session = get_session(resolved_session_id)
            if session is None:
                return
            metadata = getattr(session, "metadata", {})
            if not isinstance(metadata, dict):
                metadata = {}
            merged_metadata = self._deep_merge_metadata(metadata, metadata_patch)
            upsert_session(session.model_copy(update={"metadata": merged_metadata}))
        except Exception:
            return

    @staticmethod
    def _resume_thread_metadata_key(record: Any) -> str | None:
        return normalize_resume_thread_metadata_key(
            getattr(record, "resume_thread_metadata_key", None)
        )

    @staticmethod
    def _resume_thread_metadata_collection(record: Any) -> str:
        return normalize_resume_thread_metadata_collection(
            getattr(record, "resume_thread_metadata_collection", None)
        )

    @classmethod
    def _deep_merge_metadata(cls, base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        merged = dict(base)
        for key, value in update.items():
            existing = merged.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                merged[key] = cls._deep_merge_metadata(existing, value)
                continue
            merged[key] = value
        return merged

    @staticmethod
    def _usable_resume_thread_id(value: Any) -> str | None:
        text = str(value or "").strip()
        if not text:
            return None
        if text.startswith("dispatch-") or text.startswith("chat-thread-"):
            return None
        return text

    @staticmethod
    def _workflow_context_from_record(record: Any) -> dict[str, dict[str, Any]] | None:
        raw_context = getattr(record, "workflow_context", None)
        if not isinstance(raw_context, dict):
            return None
        workflow_context: dict[str, dict[str, Any]] = {}
        for node_id, node_payload in raw_context.items():
            if isinstance(node_id, str) and isinstance(node_payload, dict):
                workflow_context[node_id] = dict(node_payload)
        return workflow_context or None

    @staticmethod
    def _workflow_for_execution_ids_from_record(record: Any) -> dict[str, str] | None:
        raw_value = getattr(record, "workflow_for_execution_ids", None)
        if not isinstance(raw_value, dict):
            return None
        workflow_for_execution_ids: dict[str, str] = {}
        for gate_id, execution_id in raw_value.items():
            if not isinstance(gate_id, str):
                continue
            gate_text = gate_id.strip()
            execution_text = str(execution_id).strip() if execution_id is not None else ""
            if gate_text and execution_text:
                workflow_for_execution_ids[gate_text] = execution_text
        return workflow_for_execution_ids or None

    @staticmethod
    def _json_sha256(value: Any) -> str:
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _container_record_session_context(
        self, *, worker: Any, record: Any
    ) -> tuple[str, Any, Any] | None:
        session_id = str(getattr(record, "session_id", "") or "").strip()
        if not session_id:
            return None
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        runtime = getattr(admin_service, "runtime", None)
        repository = getattr(runtime, "session_repository", None)
        get_session = getattr(repository, "get_session", None)
        if not callable(get_session):
            return None
        session = get_session(session_id)
        if session is None:
            return None
        metadata = getattr(session, "metadata", {})
        provider = str(getattr(session, "vm_provider", "") or "").strip()
        if provider != ExecutionBackendKind.CONTAINER_WORKER_VM.value and not (
            isinstance(metadata, dict)
            and str(metadata.get("execution_backend") or "").strip()
            == ExecutionBackendKind.CONTAINER_WORKER_VM.value
        ):
            return None
        config = self._config_for_container_record(
            worker=worker,
            session=session,
            session_id=session_id,
        )
        if config is None:
            return None
        return session_id, session, config

    @staticmethod
    def _queued_prompt_session_is_expired(*, queue_key: str, session: Any) -> bool:
        try:
            ensure_session_not_expired(session)
        except SessionExpiredError:
            logger.warning(
                "AgentEnqueuePromptQueueDispatchHook discarded expired queued prompt "
                "queue_key=%s %s",
                queue_key,
                session_expired_message(session),
            )
            return True
        return False

    def _config_for_container_record(
        self,
        *,
        worker: Any,
        session: Any,
        session_id: str,
    ) -> Any | None:
        resolver = getattr(worker, "provider_config_resolver", None)
        for method_name, argument in (
            ("resolve_for_session_id", session_id),
            ("resolve_for_directory", getattr(session, "directory_path", None)),
        ):
            resolver_method = getattr(resolver, method_name, None)
            if not callable(resolver_method):
                continue
            try:
                return resolver_method(argument)
            except Exception:
                continue
        return getattr(worker, "config", None)

    def _mcp_gateway_url_for_libvirt_record(
        self,
        *,
        worker: Any,
        record: Any,
    ) -> str | None:
        record_metadata = getattr(record, "metadata", {})
        if not isinstance(record_metadata, dict):
            return None
        mcp_tool_approval_lease_id = str(
            record_metadata.get("mcp_tool_approval_lease_id") or ""
        ).strip()
        if not mcp_tool_approval_lease_id:
            return None

        workflow_run_id = str(record_metadata.get("workflow_run_id") or "").strip() or None
        session_id, session = self._session_context_for_libvirt_record(
            worker=worker,
            record=record,
        )
        if not session_id:
            return None
        config = self._config_for_libvirt_record(
            worker=worker,
            session_id=session_id,
            session=session,
        )
        base_gateway_url = str(getattr(config, "gateway_url", "") or "").strip()
        if not base_gateway_url:
            return None
        return gateway_url_with_session_context(
            base_gateway_url,
            session_id=session_id,
            workflow_run_id=workflow_run_id,
            mcp_tool_approval_lease_id=mcp_tool_approval_lease_id,
            mcp_session_token=session_mcp_auth_token(session),
        )

    def _session_context_for_libvirt_record(
        self,
        *,
        worker: Any,
        record: Any,
    ) -> tuple[str | None, Any | None]:
        repository = self._session_repository_for_worker(worker)
        session_id = str(getattr(record, "session_id", "") or "").strip()
        if repository is not None and session_id:
            get_session = getattr(repository, "get_session", None)
            if callable(get_session):
                try:
                    return session_id, get_session(session_id)
                except Exception:
                    return session_id, None
        if repository is not None:
            resolve_session_target = getattr(repository, "resolve_session_target", None)
            if callable(resolve_session_target):
                try:
                    session = resolve_session_target(str(getattr(record, "target", "") or ""))
                except Exception:
                    session = None
                resolved_session_id = str(getattr(session, "session_id", "") or "").strip()
                if resolved_session_id:
                    return resolved_session_id, session
        return session_id or None, None

    @staticmethod
    def _session_repository_for_worker(worker: Any) -> Any | None:
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        runtime = getattr(admin_service, "runtime", None)
        return getattr(runtime, "session_repository", None)

    def _config_for_libvirt_record(
        self,
        *,
        worker: Any,
        session_id: str,
        session: Any | None,
    ) -> Any | None:
        for resolver in self._provider_config_resolvers_for_worker(worker):
            resolve_for_session_id = getattr(resolver, "resolve_for_session_id", None)
            if callable(resolve_for_session_id):
                try:
                    return resolve_for_session_id(session_id)
                except Exception:
                    pass
            directory_path = getattr(session, "directory_path", None)
            resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
            if directory_path and callable(resolve_for_directory):
                try:
                    return resolve_for_directory(directory_path)
                except Exception:
                    pass
        return getattr(worker, "config", None)

    @staticmethod
    def _provider_config_resolvers_for_worker(worker: Any) -> tuple[Any, ...]:
        resolvers: list[Any] = []
        direct_resolver = getattr(worker, "provider_config_resolver", None)
        if direct_resolver is not None:
            resolvers.append(direct_resolver)
        admin_service = getattr(getattr(worker, "provider_factory", None), "admin_service", None)
        runtime_resolver = getattr(getattr(admin_service, "runtime", None), "provider_config", None)
        if runtime_resolver is not None and runtime_resolver is not direct_resolver:
            resolvers.append(runtime_resolver)
        return tuple(resolvers)


def build_effect_lifecycle_hooks(*, provider_factory: Any) -> tuple[EffectLifecycleHookBase, ...]:
    hooks: list[EffectLifecycleHookBase] = []

    for effect_type in provider_factory.registry.registered_types():
        provider_class = provider_factory.registry.resolve(effect_type)
        hooks.append(ProviderCompletionLifecycleHook(provider_class))
        for hook_class in provider_class.lifecycle_hooks():
            hooks.append(hook_class())
    return tuple(hooks)
