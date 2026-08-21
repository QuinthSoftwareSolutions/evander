from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    datetime_to_storage,
    ensure_orchestration_schema,
    json_dumps,
    json_loads_object,
    maybe_run_orchestration_retention,
)


class WorkflowChangeEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    event_type: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = Field(min_length=1)
    subject_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SqliteWorkflowEventJournal:
    def __init__(
        self,
        path: Path | str,
        *,
        retention_days: int | None = None,
        compact_history_retention_days: int | None = None,
    ) -> None:
        self.path = Path(path)
        ensure_orchestration_schema(self.path)
        maybe_run_orchestration_retention(
            self.path,
            retention_days=retention_days,
            compact_history_retention_days=compact_history_retention_days,
        )

    def append(
        self,
        event_type: str,
        *,
        source: str,
        subject_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> WorkflowChangeEvent:
        event = WorkflowChangeEvent(
            event_type=event_type,
            source=source,
            subject_id=subject_id,
            payload=payload or {},
        )
        self.put(event)
        return event

    def put(self, event: WorkflowChangeEvent) -> None:
        payload = event.model_dump(mode="json")
        with connect_orchestration_db(self.path) as connection:
            connection.execute(
                """
                INSERT INTO workflow_events(
                    event_id,
                    event_type,
                    source,
                    subject_id,
                    occurred_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    event_type = excluded.event_type,
                    source = excluded.source,
                    subject_id = excluded.subject_id,
                    occurred_at = excluded.occurred_at,
                    payload_json = excluded.payload_json
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.source,
                    event.subject_id,
                    datetime_to_storage(event.occurred_at),
                    json_dumps(payload),
                ),
            )

    def cursor(self) -> int:
        ensure_orchestration_schema(self.path)
        with connect_orchestration_db(self.path) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(row_id), 0) AS cursor FROM workflow_events"
            ).fetchone()
        return int(row["cursor"] if row is not None else 0)

    def read_after(self, cursor: int) -> tuple[list[WorkflowChangeEvent], int]:
        ensure_orchestration_schema(self.path)
        with connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                """
                SELECT row_id, payload_json
                FROM workflow_events
                WHERE row_id > ?
                ORDER BY row_id ASC
                """,
                (max(0, int(cursor)),),
            ).fetchall()
        events = [
            WorkflowChangeEvent.model_validate(json_loads_object(row["payload_json"]))
            for row in rows
        ]
        next_cursor = int(rows[-1]["row_id"]) if rows else max(0, int(cursor))
        return events, next_cursor

    async def wait_after(
        self, cursor: int, *, timeout_seconds: float
    ) -> tuple[list[WorkflowChangeEvent], int]:
        events, next_cursor = self.read_after(cursor)
        if events or timeout_seconds <= 0:
            return events, next_cursor

        deadline = time.monotonic() + timeout_seconds
        poll_seconds = 0.25
        while time.monotonic() < deadline:
            await asyncio.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
            events, next_cursor = self.read_after(cursor)
            if events:
                return events, next_cursor
        return self.read_after(cursor)
