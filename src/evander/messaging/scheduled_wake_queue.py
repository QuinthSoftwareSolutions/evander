from __future__ import annotations

from pathlib import Path
from typing import Any

from evander.messaging.agent_prompt_queue import AgentPromptQueueStore

SCHEDULED_WAKE_QUEUE_PREFIX = "scheduled-wake:"

_TERMINAL_WAKE_QUEUE_STATUSES = {
    "cancelled",
    "completed",
    "completed_or_acknowledged",
    "failed",
    "missed_or_late",
}


def scheduled_wake_queue_key(request_id: str) -> str:
    return f"{SCHEDULED_WAKE_QUEUE_PREFIX}{str(request_id or '').strip()}"


def scheduled_wake_request_id_from_queue_key(queue_key: str) -> str | None:
    normalized = str(queue_key or "").strip()
    if not normalized.startswith(SCHEDULED_WAKE_QUEUE_PREFIX):
        return None
    request_id = normalized[len(SCHEDULED_WAKE_QUEUE_PREFIX) :].strip()
    return request_id or None


def scheduled_wake_queue_should_be_discarded(request: Any) -> bool:
    status = str(getattr(request, "status", "") or "").strip().casefold()
    if status in _TERMINAL_WAKE_QUEUE_STATUSES:
        return True
    if bool(getattr(request, "enabled", False)) is False:
        return True
    if getattr(request, "completed_at", None) is not None:
        return True
    if getattr(request, "cancelled_at", None) is not None:
        return True
    return False


def discard_scheduled_wake_queue_record(runtime_dir: Path, request_id: str) -> None:
    normalized_request_id = str(request_id or "").strip()
    if not normalized_request_id:
        return
    queue_path = Path(runtime_dir) / "queued-agent-prompts.json"
    if not queue_path.exists():
        return
    AgentPromptQueueStore(queue_path).delete(scheduled_wake_queue_key(normalized_request_id))
