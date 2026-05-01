from __future__ import annotations

import logging
import time
from pathlib import Path

from agent_sandbox.messaging.agent_prompt_queue import AgentPromptQueueStore, QueuedAgentPromptRecord

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
    resume_thread_id: str | None,
    effect_id: str,
    queue_key: str | None = None,
    metadata: dict[str, str] | None = None,
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
                resume_thread_id=resume_thread_id,
                effect_id=effect_id,
                metadata=dict(metadata or {}),
            )
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "metric_prompt_enqueue_success queue_key=%s session_id=%s vm_name=%s latency_ms=%s coalesced_count=%s",
            record.queue_key,
            session_id or "",
            vm_name,
            latency_ms,
            record.coalesced_count,
        )
        return record
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
