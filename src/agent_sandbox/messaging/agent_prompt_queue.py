from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agent_sandbox.models import utc_now
from agent_sandbox.storage import JsonStore


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
    metadata: dict[str, str] = Field(default_factory=dict)
    workflow_context: dict[str, dict[str, Any]] | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    coalesced_count: int = Field(default=1, ge=1)

    @field_validator("workflow_context", mode="before")
    @classmethod
    def normalize_workflow_context(cls, value: Any) -> dict[str, dict[str, Any]] | None:
        if not isinstance(value, Mapping):
            return None
        context: dict[str, dict[str, Any]] = {}
        for node_id, node_payload in value.items():
            if not isinstance(node_id, str) or not isinstance(node_payload, Mapping):
                continue
            if not all(isinstance(output_name, str) for output_name in node_payload):
                continue
            context[node_id] = dict(node_payload)
        return context or None


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
        return {key: QueuedAgentPromptRecord.model_validate(value) for key, value in raw.items()}

    def delete(self, queue_key: str) -> None:
        self.store.delete(queue_key)

    def put(self, record: QueuedAgentPromptRecord) -> QueuedAgentPromptRecord:
        existing = self.get(record.queue_key)
        if existing is not None:
            record.created_at = existing.created_at
            record.coalesced_count = existing.coalesced_count + 1
        self.store.put(record.queue_key, record)
        return record
