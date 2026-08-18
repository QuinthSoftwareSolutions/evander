from __future__ import annotations

import asyncio
import ctypes
import fcntl
import json
import os
import select
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Literal, TypeVar
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
from evander.storage import JsonStore

T = TypeVar("T", bound=BaseModel)
_ORCHESTRATION_JSON_FINALIZATION_VERSION = 1
_ORCHESTRATION_JSON_FINALIZATION_MARKER = (
    "orchestration-json-store-finalized-v1.json"
)
_ORCHESTRATION_JSON_FINALIZATION_STATE_SUFFIX = (
    ".orchestration-json-store-finalized-v1.json"
)
_ORCHESTRATION_JSON_FINALIZATION_LOCK_SUFFIX = ".orchestration-json-store-finalization.lock"


class WorkflowEventCursor(int):
    """Integer-compatible cursor tagged with the journal backend that produced it."""

    backend: Literal["json", "sqlite"]

    def __new__(
        cls, value: int, *, backend: Literal["json", "sqlite"]
    ) -> WorkflowEventCursor:
        cursor = int.__new__(cls, max(0, int(value)))
        cursor.backend = backend
        return cursor


@dataclass(frozen=True)
class _FinalizedWorkflowEventJournal:
    journal: SqliteWorkflowEventJournal
    cutover_cursor: int


def workflow_event_finalization_state_path(journal_path: Path | str) -> Path:
    path = Path(journal_path)
    return path.with_name(f".{path.name}{_ORCHESTRATION_JSON_FINALIZATION_STATE_SUFFIX}")


def workflow_event_finalization_lock_path(journal_path: Path | str) -> Path:
    path = Path(journal_path)
    return path.with_name(f".{path.name}{_ORCHESTRATION_JSON_FINALIZATION_LOCK_SUFFIX}")


@contextmanager
def workflow_event_finalization_lock(journal_path: Path | str) -> Iterator[None]:
    lock_path = workflow_event_finalization_lock_path(journal_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class WorkflowChangeEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    event_type: str = Field(min_length=1)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: str = Field(min_length=1)
    subject_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class WorkflowEventJournal:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._sqlite_delegate: SqliteWorkflowEventJournal | None = None
        self._sqlite_cutover_cursor = 0
        self._started_on_json = False
        with workflow_event_finalization_lock(self.path):
            finalized = _completed_finalization_sqlite_journal(self.path)
            if finalized is None:
                self._started_on_json = True
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.touch(exist_ok=True)
            else:
                self._activate_sqlite(finalized)

    def _active_journal(self) -> SqliteWorkflowEventJournal | None:
        if self._sqlite_delegate is not None:
            return self._sqlite_delegate
        with workflow_event_finalization_lock(self.path):
            finalized = _completed_finalization_sqlite_journal(self.path)
            if finalized is not None:
                self._activate_sqlite(finalized)
        return self._sqlite_delegate

    def _activate_sqlite(self, finalized: _FinalizedWorkflowEventJournal) -> None:
        self._sqlite_delegate = finalized.journal
        self._sqlite_cutover_cursor = finalized.cutover_cursor

    def _sqlite_cursor(self, cursor: int) -> int:
        if (
            self._started_on_json
            and getattr(cursor, "backend", None) != "sqlite"
        ):
            return self._sqlite_cutover_cursor
        return max(0, int(cursor))

    def append(
        self,
        event_type: str,
        *,
        source: str,
        subject_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> WorkflowChangeEvent:
        if self._sqlite_delegate is not None:
            return self._sqlite_delegate.append(
                event_type,
                source=source,
                subject_id=subject_id,
                payload=payload,
            )
        with workflow_event_finalization_lock(self.path):
            finalized = _completed_finalization_sqlite_journal(self.path)
            if finalized is not None:
                self._activate_sqlite(finalized)
                journal = finalized.journal
            else:
                journal = None
            if journal is None:
                event = WorkflowChangeEvent(
                    event_type=event_type,
                    source=source,
                    subject_id=subject_id,
                    payload=payload or {},
                )
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(
                        json.dumps(event.model_dump(mode="json"), sort_keys=True) + "\n"
                    )
                return event
        if journal is not None:
            return journal.append(
                event_type,
                source=source,
                subject_id=subject_id,
                payload=payload,
            )
        raise AssertionError("workflow event journal backend was not selected")

    def cursor(self) -> int:
        if journal := self._active_journal():
            return WorkflowEventCursor(journal.cursor(), backend="sqlite")
        with workflow_event_finalization_lock(self.path):
            finalized = _completed_finalization_sqlite_journal(self.path)
            if finalized is not None:
                self._activate_sqlite(finalized)
                return WorkflowEventCursor(finalized.journal.cursor(), backend="sqlite")
            try:
                value = self.path.stat().st_size
            except FileNotFoundError:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.touch(exist_ok=True)
                value = 0
            return WorkflowEventCursor(value, backend="json")

    def read_after(self, cursor: int) -> tuple[list[WorkflowChangeEvent], int]:
        if journal := self._active_journal():
            events, next_cursor = journal.read_after(self._sqlite_cursor(cursor))
            return events, WorkflowEventCursor(next_cursor, backend="sqlite")
        with workflow_event_finalization_lock(self.path):
            finalized = _completed_finalization_sqlite_journal(self.path)
            if finalized is not None:
                self._activate_sqlite(finalized)
                events, next_cursor = finalized.journal.read_after(
                    self._sqlite_cursor(cursor)
                )
                return events, WorkflowEventCursor(next_cursor, backend="sqlite")
            self.path.touch(exist_ok=True)
            with self.path.open("r", encoding="utf-8") as handle:
                handle.seek(max(0, int(cursor)))
                events = []
                for line in handle:
                    text = line.strip()
                    if not text:
                        continue
                    events.append(WorkflowChangeEvent.model_validate(json.loads(text)))
                return events, WorkflowEventCursor(handle.tell(), backend="json")

    async def wait_after(
        self, cursor: int, *, timeout_seconds: float
    ) -> tuple[list[WorkflowChangeEvent], int]:
        if journal := self._active_journal():
            events, next_cursor = await journal.wait_after(
                self._sqlite_cursor(cursor), timeout_seconds=timeout_seconds
            )
            return events, WorkflowEventCursor(next_cursor, backend="sqlite")
        events, next_cursor = self.read_after(cursor)
        if events:
            return events, next_cursor
        await asyncio.to_thread(self._wait_for_write, timeout_seconds)
        return self.read_after(cursor)

    def _wait_for_write(self, timeout_seconds: float) -> None:
        if timeout_seconds <= 0:
            return
        if sys.platform.startswith("linux"):
            _wait_for_linux_directory_write(self.path.parent, timeout_seconds)
            return
        select.select([], [], [], timeout_seconds)


class NotifyingJsonStore(JsonStore[T]):
    def __init__(self, path: Path, model: type[T], *, journal_path: Path, source: str) -> None:
        super().__init__(path=path, model=model)
        self._journal = WorkflowEventJournal(journal_path)
        self._source = source

    def put(self, key: str, value: T) -> None:
        super().put(key, value)
        self._journal.append("workflow.store.updated", source=self._source, subject_id=key)

    def delete(self, key: str) -> None:
        super().delete(key)
        self._journal.append("workflow.store.deleted", source=self._source, subject_id=key)


def workflow_event_journal_path(runtime_dir: Path | str) -> Path:
    return Path(runtime_dir) / "workflow-events.jsonl"


def _wait_for_linux_directory_write(directory: Path, timeout_seconds: float) -> None:
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    inotify_init1 = libc.inotify_init1
    inotify_init1.argtypes = [ctypes.c_int]
    inotify_init1.restype = ctypes.c_int
    inotify_add_watch = libc.inotify_add_watch
    inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    inotify_add_watch.restype = ctypes.c_int

    inotify_close_write = 0x00000008
    inotify_modify = 0x00000002
    inotify_create = 0x00000100
    inotify_moved_to = 0x00000080
    in_cloexec = 0o2000000

    fd = inotify_init1(in_cloexec)
    if fd < 0:
        select.select([], [], [], timeout_seconds)
        return
    try:
        mask = inotify_close_write | inotify_modify | inotify_create | inotify_moved_to
        watch = inotify_add_watch(fd, os.fsencode(directory), mask)
        if watch < 0:
            select.select([], [], [], timeout_seconds)
            return
        ready, _, _ = select.select([fd], [], [], timeout_seconds)
        if ready:
            os.read(fd, 4096)
    finally:
        os.close(fd)


class SqliteWorkflowEventJournal:
    def __init__(
        self,
        path: Path | str,
        *,
        retention_days: int | None = None,
    ) -> None:
        self.path = Path(path)
        ensure_orchestration_schema(self.path)
        maybe_run_orchestration_retention(self.path, retention_days=retention_days)

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


def _completed_finalization_sqlite_journal(
    journal_path: Path,
) -> _FinalizedWorkflowEventJournal | None:
    state_path = workflow_event_finalization_state_path(journal_path)
    if _finalization_document_exists(state_path):
        state = _read_finalization_document(state_path, kind="cutover state")
        marker_value = state.get("marker_path")
        if not isinstance(marker_value, str) or not marker_value.strip():
            raise RuntimeError(
                f"workflow event finalization cutover state is missing marker_path: {state_path}"
            )
        marker_path = _absolute_finalization_path(
            marker_value, document_path=state_path, field="marker_path"
        )
        marker = _read_finalization_document(marker_path, kind="marker")
        _validate_finalization_documents(
            state,
            state_path,
            marker,
            marker_path,
            journal_path=journal_path,
        )
        document = state
    else:
        if journal_path.name != "workflow-events.jsonl":
            return None
        marker_path = (
            journal_path.parent
            / "migrations"
            / _ORCHESTRATION_JSON_FINALIZATION_MARKER
        )
        if not _finalization_document_exists(marker_path):
            return None
        marker = _read_finalization_document(marker_path, kind="marker")
        document = marker

    database_value = document.get("database_path")
    if not isinstance(database_value, str) or not database_value.strip():
        raise RuntimeError(
            f"workflow event finalization document is missing database_path: {marker_path}"
        )
    database_path = _absolute_finalization_path(
        database_value, document_path=marker_path, field="database_path"
    )
    if not database_path.is_file():
        raise RuntimeError(
            "completed orchestration JSON finalization marker references a missing "
            f"database: {database_path}"
        )
    retention_value = document.get("retention_days")
    try:
        retention_days = int(retention_value) if retention_value is not None else None
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"workflow event finalization document has invalid retention_days: {marker_path}"
        ) from exc
    if retention_days is not None and retention_days < 0:
        raise RuntimeError(
            f"workflow event finalization document has invalid retention_days: {marker_path}"
        )
    journal = SqliteWorkflowEventJournal(database_path, retention_days=retention_days)
    cursor_value = document.get("workflow_event_cursor")
    try:
        cutover_cursor = 0 if cursor_value is None else max(0, int(cursor_value))
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"workflow event finalization document has invalid workflow_event_cursor: {marker_path}"
        ) from exc
    return _FinalizedWorkflowEventJournal(journal=journal, cutover_cursor=cutover_cursor)


def _finalization_document_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError(
            f"cannot inspect workflow event finalization document {path}: {exc}"
        ) from exc
    return True


def _read_finalization_document(path: Path, *, kind: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read workflow event finalization {kind} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"workflow event finalization {kind} is not an object: {path}")
    if payload.get("version") != _ORCHESTRATION_JSON_FINALIZATION_VERSION:
        raise RuntimeError(f"workflow event finalization {kind} has unsupported version: {path}")
    if payload.get("state") != "complete":
        raise RuntimeError(
            f"workflow event finalization {kind} is not complete: {path}"
        )
    return payload


def _absolute_finalization_path(value: str, *, document_path: Path, field: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise RuntimeError(
            f"workflow event finalization {field} must be absolute in {document_path}"
        )
    return path


def _validate_finalization_documents(
    state: dict[str, Any],
    state_path: Path,
    marker: dict[str, Any],
    marker_path: Path,
    *,
    journal_path: Path,
) -> None:
    state_database = state.get("database_path")
    marker_database = marker.get("database_path")
    if state_database != marker_database:
        raise RuntimeError(
            "workflow event finalization cutover state database_path does not match "
            f"marker {marker_path}: {state_path}"
        )
    workflow_events_value = state.get("workflow_events_path")
    if not isinstance(workflow_events_value, str) or not workflow_events_value.strip():
        raise RuntimeError(
            f"workflow event finalization cutover state is missing workflow_events_path: {state_path}"
        )
    configured_path = _absolute_finalization_path(
        workflow_events_value,
        document_path=state_path,
        field="workflow_events_path",
    )
    if configured_path != journal_path.expanduser().resolve():
        raise RuntimeError(
            "workflow event finalization cutover state belongs to a different journal: "
            f"{state_path}"
        )
    for field in ("workflow_event_cursor", "retention_days"):
        if field in marker and state.get(field) != marker.get(field):
            raise RuntimeError(
                f"workflow event finalization cutover state {field} does not match "
                f"marker {marker_path}: {state_path}"
            )
    marker_workflow_events = marker.get("workflow_events_path")
    if marker_workflow_events is not None and marker_workflow_events != workflow_events_value:
        raise RuntimeError(
            "workflow event finalization cutover state workflow_events_path does not match "
            f"marker {marker_path}: {state_path}"
        )
