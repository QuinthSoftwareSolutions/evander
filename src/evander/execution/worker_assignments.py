from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field


class ContainerWorkerAssignment(BaseModel):
    session_id: str
    directory_path: str = "root"
    worker_url: str
    worker_id: str | None = None
    connection: dict[str, object]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ContainerWorkerAssignmentRepository:
    """Host-owned, write-once session assignments, retained after session cleanup."""

    def __init__(self, runtime_dir: Path) -> None:
        self.path = Path(runtime_dir).resolve() / "container-worker-assignments.db"

    def list_assignments(self) -> list[ContainerWorkerAssignment]:
        if not self.path.exists():
            return []
        with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as connection:
            if not self._has_table(connection):
                return []
            rows = connection.execute(
                "SELECT payload_json FROM container_worker_assignments ORDER BY session_id"
            ).fetchall()
        return [ContainerWorkerAssignment.model_validate_json(row[0]) for row in rows]

    def get(self, session_id: str) -> ContainerWorkerAssignment | None:
        if not self.path.exists():
            return None
        with closing(sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)) as connection:
            if not self._has_table(connection):
                return None
            row = connection.execute(
                "SELECT payload_json FROM container_worker_assignments WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return ContainerWorkerAssignment.model_validate_json(row[0]) if row else None

    def pin(self, assignment: ContainerWorkerAssignment) -> ContainerWorkerAssignment:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS container_worker_assignments ("
                "session_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO container_worker_assignments VALUES (?, ?)",
                (assignment.session_id, assignment.model_dump_json()),
            )
            row = connection.execute(
                "SELECT payload_json FROM container_worker_assignments WHERE session_id = ?",
                (assignment.session_id,),
            ).fetchone()
        return ContainerWorkerAssignment.model_validate(json.loads(row[0]))

    @staticmethod
    def _has_table(connection: sqlite3.Connection) -> bool:
        return (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='container_worker_assignments'"
            ).fetchone()
            is not None
        )
