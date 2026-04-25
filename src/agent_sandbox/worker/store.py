from __future__ import annotations

from pathlib import Path

from agent_sandbox.storage import JsonStore

from .models import WorkerJobRecord


class WorkerJobStore:
    def __init__(self, path: Path) -> None:
        self._store = JsonStore(path, WorkerJobRecord)

    @property
    def path(self) -> Path:
        return self._store.path

    def get(self, job_id: str) -> WorkerJobRecord | None:
        return self._store.get(job_id)

    def put(self, record: WorkerJobRecord) -> None:
        self._store.put(record.job_id, record)

    def delete(self, job_id: str) -> None:
        self._store.delete(job_id)

    def read_all(self) -> dict[str, WorkerJobRecord]:
        return self._store.read_all()
