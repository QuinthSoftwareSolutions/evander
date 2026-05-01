from __future__ import annotations

from pathlib import Path

from agent_sandbox.messaging.agent_prompt_queue import AgentPromptQueueStore, QueuedAgentPromptRecord


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
    queue_store = AgentPromptQueueStore(runtime_dir / "queued-agent-prompts.json")
    return queue_store.put(
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
