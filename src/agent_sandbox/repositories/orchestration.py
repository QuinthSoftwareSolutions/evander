from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

from agent_sandbox.storage import JsonStore
from agent_sandbox.workflow.events import WorkflowEventJournal

logger = logging.getLogger(__name__)

TRecord = TypeVar("TRecord", bound=BaseModel)
R = TypeVar("R")


class EffectExecutionRepository(Protocol):
    def get_execution(self, execution_id: str) -> Any | None: ...

    def list_executions(self) -> list[Any]: ...

    def record_execution(self, record: Any) -> None: ...

    def find_execution_by_idempotency_key(self, idempotency_key: str) -> Any | None: ...

    def read_all(self) -> dict[str, Any]: ...

    def put(self, record: Any) -> None: ...

    def find_by_idempotency_key(self, idempotency_key: str) -> Any | None: ...

    def update_all(self, updater: Callable[[dict[str, Any]], R]) -> R: ...


class GateExecutionRepository(EffectExecutionRepository, Protocol):
    def find_latest_execution_for_gate(self, gate_id: str) -> Any | None: ...

    def find_latest_by_gate_id(self, gate_id: str) -> Any | None: ...


class GateCursorRepository(Protocol):
    def get_trigger_state(self, trigger_id: str) -> dict[str, Any] | None: ...

    def record_trigger_poll(
        self,
        *,
        trigger_id: str,
        trigger_type: str,
        discovered: int,
        event_ids: list[str],
        polled_at: datetime | None = None,
    ) -> None: ...

    def load(self) -> dict[str, Any]: ...

    def save(self, payload: dict[str, Any]) -> None: ...


class EffectCursorRepository(Protocol):
    def get_processing_checkpoint(self) -> dict[str, Any]: ...

    def record_processed_event(
        self,
        *,
        event_id: str,
        source_node_id: str,
        processed_at: datetime | None = None,
    ) -> None: ...

    def load(self) -> dict[str, Any]: ...

    def save(self, payload: dict[str, Any]) -> None: ...


class JsonCursorRepository:
    def __init__(self, path: Path, *, warning_context: str) -> None:
        self.path = path
        self.warning_context = warning_context
        self._lock = RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            return self._read_payload()

    def save(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._write_payload(payload)

    def update(self, updater: Any) -> dict[str, Any]:
        with self._lock:
            payload = self._read_payload()
            updater(payload)
            self._write_payload(payload)
            return payload

    def _read_payload(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning(
                "Unable to read %s cursor state path=%s",
                self.warning_context,
                self.path,
                exc_info=True,
            )
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    def _write_payload(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)


class JsonGateCursorRepository(JsonCursorRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path, warning_context="trigger orchestrator")

    def get_trigger_state(self, trigger_id: str) -> dict[str, Any] | None:
        payload = self.load()
        triggers_payload = payload.get("triggers", {})
        if not isinstance(triggers_payload, dict):
            return None
        state = triggers_payload.get(trigger_id)
        return dict(state) if isinstance(state, dict) else None

    def record_trigger_poll(
        self,
        *,
        trigger_id: str,
        trigger_type: str,
        discovered: int,
        event_ids: list[str],
        polled_at: datetime | None = None,
    ) -> None:
        timestamp = (polled_at or datetime.now(UTC)).isoformat().replace("+00:00", "Z")

        def _update(payload: dict[str, Any]) -> None:
            triggers_payload = payload.setdefault("triggers", {})
            if not isinstance(triggers_payload, dict):
                payload["triggers"] = {}
                triggers_payload = payload["triggers"]
            trigger_payload: dict[str, Any] = {
                "trigger_type": trigger_type,
                "last_polled_at": timestamp,
                "last_discovered_count": discovered,
                "last_event_ids": event_ids[-20:],
            }
            if event_ids:
                trigger_payload["last_event_id"] = event_ids[-1]
            triggers_payload[trigger_id] = trigger_payload

        self.update(_update)


class JsonEffectCursorRepository(JsonCursorRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path, warning_context="effect orchestrator")

    def get_processing_checkpoint(self) -> dict[str, Any]:
        return self.load()

    def record_processed_event(
        self,
        *,
        event_id: str,
        source_node_id: str,
        processed_at: datetime | None = None,
    ) -> None:
        timestamp = (processed_at or datetime.now(UTC)).isoformat().replace("+00:00", "Z")

        def _update(payload: dict[str, Any]) -> None:
            payload["last_processed_event_id"] = event_id
            payload["last_processed_source_node_id"] = source_node_id
            payload["last_processed_at"] = timestamp

        self.update(_update)


class JsonExecutionRepository(Generic[TRecord]):
    def __init__(
        self,
        path: Path,
        model: type[TRecord],
        *,
        journal_path: Path | None = None,
        source: str | None = None,
    ) -> None:
        self.path = path
        self.model = model
        self._store = JsonStore(path, model)
        self._journal = WorkflowEventJournal(journal_path) if journal_path is not None else None
        self._journal_source = source
        self._lock = RLock()

    def read_all(self) -> dict[str, TRecord]:
        with self._lock:
            return self._store.read_all()

    def get_execution(self, execution_id: str) -> TRecord | None:
        return self.read_all().get(execution_id)

    def list_executions(self) -> list[TRecord]:
        return list(self.read_all().values())

    def record_execution(self, record: TRecord) -> None:
        self.put(record)

    def put(self, record: TRecord) -> None:
        def _put_record(payload: dict[str, TRecord]) -> None:
            payload[getattr(record, "execution_id")] = record

        with self._lock:
            self._store.update_all(_put_record)
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
            for record in self._store.read_all().values():
                if getattr(record, "idempotency_key") == idempotency_key:
                    return record
        return None

    def find_execution_by_idempotency_key(self, idempotency_key: str) -> TRecord | None:
        return self.find_by_idempotency_key(idempotency_key)

    def update_all(self, updater: Callable[[dict[str, TRecord]], R]) -> R:
        with self._lock:
            return self._store.update_all(updater)
