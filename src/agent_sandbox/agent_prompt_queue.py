from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from .models import utc_now
from .storage import JsonStore


class QueuedAgentPromptRecord(BaseModel):
    queue_key: str = Field(min_length=1)
    target: str = Field(min_length=1)
    vm_name: str = Field(min_length=1)
    session_id: str | None = None
    prompt: str = Field(min_length=1)
    model: str | None = None
    vm_user: str = Field(min_length=1, default="agent")
    resume_thread_id: str | None = None
    effect_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    coalesced_count: int = Field(default=1, ge=1)


class AgentPromptQueueStore:
    def __init__(self, path: Path) -> None:
        self.store = JsonStore(path, QueuedAgentPromptRecord)

    def get(self, queue_key: str) -> QueuedAgentPromptRecord | None:
        return self.store.get(queue_key)

    def read_all(self) -> dict[str, QueuedAgentPromptRecord]:
        raw_text = self.store.path.read_text(encoding="utf-8").strip()
        if not raw_text:
            return {}
        try:
            raw = json.loads(raw_text)
        except json.JSONDecodeError:
            return {}
        if not isinstance(raw, dict):
            return {}
        return {
            key: QueuedAgentPromptRecord.model_validate(value)
            for key, value in raw.items()
        }

    def delete(self, queue_key: str) -> None:
        self.store.delete(queue_key)

    def put(self, record: QueuedAgentPromptRecord) -> QueuedAgentPromptRecord:
        existing = self.get(record.queue_key)
        if existing is not None:
            record.created_at = existing.created_at
            record.coalesced_count = existing.coalesced_count + 1
        self.store.put(record.queue_key, record)
        return record
