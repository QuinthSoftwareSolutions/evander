from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agent_sandbox.models import utc_now
from agent_sandbox.storage import JsonStore


class AgentPromptRetryLimitExceeded(RuntimeError):
    def __init__(
        self,
        *,
        queue_key: str,
        retry_key: str,
        retry_count: int,
        max_retries: int,
    ) -> None:
        self.queue_key = queue_key
        self.retry_key = retry_key
        self.retry_count = retry_count
        self.max_retries = max_retries
        super().__init__(
            "queued agent prompt retry limit exceeded "
            f"(queue_key={queue_key}, retry_count={retry_count}, max_retries={max_retries})"
        )


def _prompt_digest(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _retry_key(*, effect_id: str, queue_key: str, prompt: str) -> str:
    return hashlib.sha256(
        "\0".join((effect_id, queue_key, _prompt_digest(prompt))).encode("utf-8")
    ).hexdigest()


class AgentPromptRetryLedgerRecord(BaseModel):
    retry_key: str = Field(min_length=1)
    queue_key: str = Field(min_length=1)
    effect_id: str = Field(min_length=1)
    prompt_digest: str = Field(min_length=1)
    target: str = Field(min_length=1)
    vm_name: str = Field(min_length=1)
    session_id: str | None = None
    retry_count: int = Field(default=0, ge=0)
    max_retries: int = Field(default=3, ge=1)
    first_attempted_at: datetime = Field(default_factory=utc_now)
    last_attempted_at: datetime = Field(default_factory=utc_now)
    exhausted_at: datetime | None = None


class QueuedAgentPromptRecord(BaseModel):
    queue_key: str = Field(min_length=1)
    target: str = Field(min_length=1)
    vm_name: str = Field(min_length=1)
    session_id: str | None = None
    prompt: str = Field(min_length=1)
    model: str | None = None
    vm_user: str = Field(min_length=1, default="agent")
    resume_thread_id: str | None = None
    resume_mode: str | None = None
    resume_thread_metadata_key: str | None = None
    resume_thread_metadata_collection: str | None = None
    effect_id: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)
    workflow_context: dict[str, dict[str, Any]] | None = None
    workflow_for_execution_ids: dict[str, str] | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    coalesced_count: int = Field(default=1, ge=1)
    retry_key: str | None = None
    retry_count: int = Field(default=1, ge=1)
    max_retries: int | None = Field(default=None, ge=1)

    @field_validator("resume_mode")
    @classmethod
    def validate_resume_mode(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized:
            return None
        if normalized not in {"same_context", "new_context"}:
            raise ValueError("resume_mode must be one of: same_context, new_context")
        return normalized

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

    @field_validator("workflow_for_execution_ids", mode="before")
    @classmethod
    def normalize_workflow_for_execution_ids(cls, value: Any) -> dict[str, str] | None:
        if not isinstance(value, Mapping):
            return None
        normalized: dict[str, str] = {}
        for gate_id, execution_id in value.items():
            if not isinstance(gate_id, str):
                continue
            gate_text = gate_id.strip()
            execution_text = str(execution_id).strip() if execution_id is not None else ""
            if gate_text and execution_text:
                normalized[gate_text] = execution_text
        return normalized or None


class AgentPromptRetryLedgerStore:
    def __init__(self, path: Path) -> None:
        self.store = JsonStore(path, AgentPromptRetryLedgerRecord)

    def get(self, retry_key: str) -> AgentPromptRetryLedgerRecord | None:
        return self.store.get(retry_key)

    def claim_retry(
        self,
        *,
        record: QueuedAgentPromptRecord,
        max_retries: int,
    ) -> QueuedAgentPromptRecord:
        normalized_max_retries = max(1, int(max_retries))
        retry_key = _retry_key(
            effect_id=record.effect_id,
            queue_key=record.queue_key,
            prompt=record.prompt,
        )
        prompt_digest = _prompt_digest(record.prompt)
        now = utc_now()
        exceeded: AgentPromptRetryLimitExceeded | None = None

        def updater(
            records: dict[str, AgentPromptRetryLedgerRecord],
        ) -> AgentPromptRetryLedgerRecord:
            nonlocal exceeded
            existing = records.get(retry_key)
            retry_count = (existing.retry_count if existing is not None else 0) + 1
            first_attempted_at = (
                existing.first_attempted_at if existing is not None else now
            )
            exhausted_at = existing.exhausted_at if existing is not None else None
            if retry_count > normalized_max_retries:
                exhausted_at = now
                exceeded = AgentPromptRetryLimitExceeded(
                    queue_key=record.queue_key,
                    retry_key=retry_key,
                    retry_count=retry_count,
                    max_retries=normalized_max_retries,
                )
            ledger_record = AgentPromptRetryLedgerRecord(
                retry_key=retry_key,
                queue_key=record.queue_key,
                effect_id=record.effect_id,
                prompt_digest=prompt_digest,
                target=record.target,
                vm_name=record.vm_name,
                session_id=record.session_id,
                retry_count=retry_count,
                max_retries=normalized_max_retries,
                first_attempted_at=first_attempted_at,
                last_attempted_at=now,
                exhausted_at=exhausted_at,
            )
            records[retry_key] = ledger_record
            return ledger_record

        ledger_record = self.store.update_all(updater)
        if exceeded is not None:
            raise exceeded

        record.retry_key = ledger_record.retry_key
        record.retry_count = ledger_record.retry_count
        record.max_retries = ledger_record.max_retries
        return record


class AgentPromptQueueStore:
    def __init__(self, path: Path) -> None:
        self.store = JsonStore(path, QueuedAgentPromptRecord)
        self.retry_ledger = AgentPromptRetryLedgerStore(
            path.with_name("agent-prompt-retry-ledger.json")
        )

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

    def put(
        self,
        record: QueuedAgentPromptRecord,
        *,
        max_retries: int = 3,
    ) -> QueuedAgentPromptRecord:
        record = self.retry_ledger.claim_retry(record=record, max_retries=max_retries)
        existing = self.get(record.queue_key)
        if existing is not None:
            record.created_at = existing.created_at
            record.coalesced_count = existing.coalesced_count + 1
        self.store.put(record.queue_key, record)
        return record
