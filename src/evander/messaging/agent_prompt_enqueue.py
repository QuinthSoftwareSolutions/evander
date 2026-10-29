from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from evander.fast_mode import FastMode, FastModeSource
from evander.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
    AgentPromptRetryLimitExceeded,
    QueuedAgentPromptRecord,
)
from evander.reasoning_effort import ReasoningEffort, ReasoningEffortSource

logger = logging.getLogger(__name__)


def workflow_context_from_sources(
    *,
    execution_context: Mapping[str, Any] | None,
    input_payload: Mapping[str, Any] | None,
    context_key: str,
) -> dict[str, dict[str, Any]] | None:
    for source in (execution_context, input_payload):
        if not isinstance(source, Mapping):
            continue
        workflow_context = _copy_workflow_context(source.get(context_key))
        if workflow_context:
            return workflow_context
        nested_input = source.get("input_payload")
        if isinstance(nested_input, Mapping):
            workflow_context = _copy_workflow_context(nested_input.get(context_key))
            if workflow_context:
                return workflow_context
    return None


def workflow_for_execution_ids_from_sources(
    *,
    execution_context: Mapping[str, Any] | None,
    input_payload: Mapping[str, Any] | None,
    context_key: str,
) -> dict[str, str] | None:
    for source in (execution_context, input_payload):
        if not isinstance(source, Mapping):
            continue
        execution_ids = _copy_workflow_for_execution_ids(source.get(context_key))
        if execution_ids:
            return execution_ids
        nested_input = source.get("input_payload")
        if isinstance(nested_input, Mapping):
            execution_ids = _copy_workflow_for_execution_ids(nested_input.get(context_key))
            if execution_ids:
                return execution_ids
    return None


def _copy_workflow_for_execution_ids(raw_value: Any) -> dict[str, str] | None:
    if not isinstance(raw_value, Mapping):
        return None
    execution_ids = {
        gate_id.strip(): str(execution_id).strip()
        for gate_id, execution_id in raw_value.items()
        if isinstance(gate_id, str)
        and gate_id.strip()
        and execution_id is not None
        and str(execution_id).strip()
    }
    return execution_ids or None


def _copy_workflow_context(raw_context: Any) -> dict[str, dict[str, Any]] | None:
    if not isinstance(raw_context, Mapping):
        return None
    workflow_context: dict[str, dict[str, Any]] = {}
    for node_id, node_payload in raw_context.items():
        if not isinstance(node_id, str) or not isinstance(node_payload, Mapping):
            continue
        if not all(isinstance(key, str) for key in node_payload):
            continue
        try:
            copied_payload = json.loads(json.dumps(dict(node_payload)))
        except (TypeError, ValueError):
            continue
        if isinstance(copied_payload, dict):
            workflow_context[node_id] = copied_payload
    return workflow_context or None


def enqueue_agent_prompt(
    *,
    runtime_dir: Path,
    target: str,
    vm_name: str,
    session_id: str | None,
    prompt: str,
    model: str | None,
    reasoning_effort: ReasoningEffort | None = None,
    fast_mode: FastMode | None = None,
    reasoning_effort_source: ReasoningEffortSource | None = None,
    fast_mode_source: FastModeSource | None = None,
    vm_user: str,
    dispatch_id: str | None,
    resume_thread_id: str | None,
    effect_id: str,
    model_provider_mode: str | None = None,
    queue_reason: str | None = None,
    subscription_profile_id: str | None = None,
    retry_not_before: datetime | None = None,
    capacity_deferral: bool = False,
    resume_mode: str | None = None,
    resume_thread_metadata_key: str | None = None,
    resume_thread_metadata_collection: str | None = None,
    queue_key: str | None = None,
    metadata: dict[str, str] | None = None,
    workflow_context: dict[str, dict[str, Any]] | None = None,
    workflow_for_execution_ids: dict[str, str] | None = None,
    max_retries: int = 3,
) -> QueuedAgentPromptRecord:
    start = time.monotonic()
    persisted_dispatch_id = str(dispatch_id or "").strip() or f"dispatch-queued-{uuid4().hex}"
    queue_store = AgentPromptQueueStore(runtime_dir / "queued-agent-prompts.json")
    try:
        pending_record = QueuedAgentPromptRecord(
            queue_key=queue_key or vm_name,
            target=target,
            vm_name=vm_name,
            session_id=session_id,
            prompt=prompt,
            model=model,
            reasoning_effort=reasoning_effort,
            fast_mode=fast_mode,
            reasoning_effort_source=reasoning_effort_source,
            fast_mode_source=fast_mode_source,
            model_provider_mode=model_provider_mode,
            queue_reason=queue_reason,
            subscription_profile_id=subscription_profile_id,
            retry_not_before=retry_not_before,
            capacity_deferral_count=0,
            vm_user=vm_user,
            dispatch_id=persisted_dispatch_id,
            resume_thread_id=resume_thread_id,
            resume_mode=resume_mode,
            resume_thread_metadata_key=resume_thread_metadata_key,
            resume_thread_metadata_collection=resume_thread_metadata_collection,
            effect_id=effect_id,
            metadata=dict(metadata or {}),
            workflow_context=workflow_context,
            workflow_for_execution_ids=workflow_for_execution_ids,
        )
        record = (
            queue_store.defer_capacity(
                pending_record,
                queue_reason=queue_reason or "subscription_unavailable",
                retry_at=retry_not_before,
            )
            if capacity_deferral
            else queue_store.put(
                pending_record,
                max_retries=max_retries,
                count_retry=not (metadata or {}).get("delivery_mode") == "steer_if_running",
            )
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "metric_prompt_enqueue_success queue_key=%s session_id=%s vm_name=%s latency_ms=%s coalesced_count=%s retry_count=%s max_retries=%s",
            record.queue_key,
            session_id or "",
            vm_name,
            latency_ms,
            record.coalesced_count,
            record.retry_count,
            record.max_retries or "",
        )
        return record
    except AgentPromptRetryLimitExceeded as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.warning(
            "metric_prompt_enqueue_retry_limit_exceeded queue_key=%s session_id=%s vm_name=%s latency_ms=%s retry_count=%s max_retries=%s",
            exc.queue_key,
            session_id or "",
            vm_name,
            latency_ms,
            exc.retry_count,
            exc.max_retries,
        )
        raise
    except Exception as exc:
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.warning(
            "metric_prompt_enqueue_failed queue_key=%s session_id=%s vm_name=%s latency_ms=%s reason=%s",
            queue_key or vm_name,
            session_id or "",
            vm_name,
            latency_ms,
            type(exc).__name__,
        )
        raise
