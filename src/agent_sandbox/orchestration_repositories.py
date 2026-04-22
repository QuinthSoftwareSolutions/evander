from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import RLock
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

from .workflow_events import WorkflowEventJournal

logger = logging.getLogger(__name__)

TRecord = TypeVar("TRecord", bound=BaseModel)


class EffectExecutionRepository(Protocol):
    def read_all(self) -> dict[str, Any]: ...

    def put(self, record: Any) -> None: ...

    def find_by_idempotency_key(self, idempotency_key: str) -> Any | None: ...


class GateExecutionRepository(EffectExecutionRepository, Protocol):
    def find_latest_by_gate_id(self, gate_id: str) -> Any | None: ...


class GateCursorRepository(Protocol):
    def load(self) -> dict[str, Any]: ...

    def save(self, payload: dict[str, Any]) -> None: ...


class EffectCursorRepository(Protocol):
    def load(self) -> dict[str, Any]: ...

    def save(self, payload: dict[str, Any]) -> None: ...


class JsonCursorRepository:
    def __init__(self, path: Path, *, warning_context: str) -> None:
        self.path = path
        self.warning_context = warning_context
        self._lock = RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {}
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                logger.warning("Unable to read %s cursor state path=%s", self.warning_context, self.path, exc_info=True)
                return {}
            if not isinstance(payload, dict):
                return {}
            return payload

    def save(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
            tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            tmp_path.replace(self.path)


class JsonGateCursorRepository(JsonCursorRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path, warning_context="trigger orchestrator")


class JsonEffectCursorRepository(JsonCursorRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path, warning_context="effect orchestrator")


class JsonExecutionRepository(Generic[TRecord]):
    def __init__(self, path: Path, model: type[TRecord], *, journal_path: Path | None = None, source: str | None = None) -> None:
        self.path = path
        self.model = model
        self._journal = WorkflowEventJournal(journal_path) if journal_path is not None else None
        self._journal_source = source
        self._lock = RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def read_all(self) -> dict[str, TRecord]:
        with self._lock:
            payload = self._read_payload()
            return {key: self.model.model_validate(value) for key, value in payload.items()}

    def put(self, record: TRecord) -> None:
        with self._lock:
            payload = self._read_payload()
            payload[getattr(record, "execution_id")] = record.model_dump(mode="json")
            self.path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        if self._journal is not None and self._journal_source is not None:
            self._journal.append(
                "workflow.execution.updated",
                source=self._journal_source,
                subject_id=str(getattr(record, "execution_id")),
                payload={
                    "event_id": str(getattr(record, "event_id", "")),
                    "status": str(getattr(record, "status", "")),
                },
            )

    def find_by_idempotency_key(self, idempotency_key: str) -> TRecord | None:
        with self._lock:
            for value in self._read_payload().values():
                record = self.model.model_validate(value)
                if getattr(record, "idempotency_key") == idempotency_key:
                    return record
        return None

    def _read_payload(self) -> dict[str, Any]:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        return payload
