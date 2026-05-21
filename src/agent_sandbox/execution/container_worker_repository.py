from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agent_sandbox.storage import JsonStore
from agent_sandbox.worker.models import now_utc
from agent_sandbox.worker.security import sanitize_metadata


class ContainerWorkerExecutionRecord(BaseModel):
    execution_id: str
    session_id: str
    worker_job_id: str
    worker_url: str
    target_name: str
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_status: str = "unknown"
    cleanup_status: str = "unknown"
    cleanup_attempted_at: datetime | None = None
    cleanup_completed_at: datetime | None = None
    cleanup_error: str | None = None
    directory_path: str = "root"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContainerWorkerExecutionRepository:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, ContainerWorkerExecutionRecord)

    @property
    def path(self) -> Path:
        return self._store.path

    def get(self, execution_id: str) -> ContainerWorkerExecutionRecord | None:
        return self._store.get(execution_id)

    def put(self, record: ContainerWorkerExecutionRecord) -> None:
        record.metadata = sanitize_metadata(record.metadata)
        record.updated_at = now_utc()
        self._store.put(record.execution_id, record)

    def delete(self, execution_id: str) -> None:
        self._store.delete(execution_id)

    def read_all(self) -> dict[str, ContainerWorkerExecutionRecord]:
        return self._store.read_all()

    def resolve(self, target: str) -> ContainerWorkerExecutionRecord:
        normalized = str(target or "").strip()
        if not normalized:
            raise ValueError("container worker execution target is required")
        records = list(self.read_all().values())
        for record in records:
            if normalized in {record.execution_id, record.worker_job_id, record.target_name}:
                return record
        session_matches = [record for record in records if record.session_id == normalized]
        if len(session_matches) == 1:
            return session_matches[0]
        if len(session_matches) > 1:
            matches = ", ".join(sorted(record.execution_id for record in session_matches))
            raise ValueError(f"Ambiguous container worker session target {normalized}: {matches}")
        raise ValueError(f"Unknown container worker execution target: {normalized}")
