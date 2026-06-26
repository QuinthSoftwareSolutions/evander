from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from agent_sandbox.messaging.agent_prompt_queue import (
    AgentPromptQueueStore,
    AgentPromptRetryLimitExceeded,
    QueuedAgentPromptRecord,
)

logger = logging.getLogger(__name__)


def enqueue_agent_prompt(
    *,
    runtime_dir: Path,
    target: str,
    vm_name: str,
    session_id: str | None,
    prompt: str,
    model: str | None,
    vm_user: str,
    dispatch_id: str | None,
    resume_thread_id: str | None,
    effect_id: str,
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
    queue_store = AgentPromptQueueStore(runtime_dir / "queued-agent-prompts.json")
    try:
        record = queue_store.put(
            QueuedAgentPromptRecord(
                queue_key=queue_key or vm_name,
                target=target,
                vm_name=vm_name,
                session_id=session_id,
                prompt=prompt,
                model=model,
                vm_user=vm_user,
                dispatch_id=dispatch_id,
                resume_thread_id=resume_thread_id,
                resume_mode=resume_mode,
                resume_thread_metadata_key=resume_thread_metadata_key,
                resume_thread_metadata_collection=resume_thread_metadata_collection,
                effect_id=effect_id,
                metadata=dict(metadata or {}),
                workflow_context=workflow_context,
                workflow_for_execution_ids=workflow_for_execution_ids,
            ),
            max_retries=max_retries,
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
