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

    def _records(self) -> list[WorkerJobRecord]:
        with self._store._lock:
            with self._store._file_lock():
                return list(self._store._read_all_unlocked().values())

    @staticmethod
    def _sort_key(record: WorkerJobRecord) -> tuple[bool, object, str]:
        return (record.started_at is None, record.started_at, record.job_id)

    def get(self, job_id: str) -> WorkerJobRecord | None:
        return self._store.get(job_id)

    def put(self, record: WorkerJobRecord) -> None:
        self._store.put(record.job_id, record)

    def delete(self, job_id: str) -> None:
        self._store.delete(job_id)

    def read_all(self) -> dict[str, WorkerJobRecord]:
        return self._store.read_all()

    def list_by_session(self, session_id: str) -> list[WorkerJobRecord]:
        return sorted(
            (record for record in self._records() if record.session_id == session_id),
            key=self._sort_key,
        )

    def list_active_by_session(self, session_id: str) -> list[WorkerJobRecord]:
        active_statuses = {"created", "starting", "running"}
        return sorted(
            (
                record
                for record in self._records()
                if record.session_id == session_id and record.status in active_statuses
            ),
            key=self._sort_key,
        )

    def list_all_ordered(self) -> list[WorkerJobRecord]:
        return sorted(self._records(), key=self._sort_key)
