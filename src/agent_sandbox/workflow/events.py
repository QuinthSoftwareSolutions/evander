from __future__ import annotations

import asyncio
import ctypes
import json
import os
import select
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Generic, TypeVar
from uuid import uuid4

from pydantic import BaseModel, Field

from agent_sandbox.storage import JsonStore

T = TypeVar("T", bound=BaseModel)


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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

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
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.model_dump(mode="json"), sort_keys=True) + "\n")
        return event

    def cursor(self) -> int:
        try:
            return self.path.stat().st_size
        except FileNotFoundError:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.touch(exist_ok=True)
            return 0

    def read_after(self, cursor: int) -> tuple[list[WorkflowChangeEvent], int]:
        self.path.touch(exist_ok=True)
        with self.path.open("r", encoding="utf-8") as handle:
            handle.seek(max(0, cursor))
            events: list[WorkflowChangeEvent] = []
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                events.append(WorkflowChangeEvent.model_validate(json.loads(text)))
            return events, handle.tell()

    async def wait_after(
        self, cursor: int, *, timeout_seconds: float
    ) -> tuple[list[WorkflowChangeEvent], int]:
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
