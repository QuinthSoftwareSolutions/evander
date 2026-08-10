from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Generic, Protocol, TypeVar

from pydantic import BaseModel

from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    datetime_to_storage,
    ensure_orchestration_schema,
    json_dumps,
    json_loads_object,
    maybe_run_orchestration_retention,
)
from evander.storage import JsonObjectStore, JsonStore
from evander.workflow.events import WorkflowEventJournal

logger = logging.getLogger(__name__)

TRecord = TypeVar("TRecord", bound=BaseModel)
R = TypeVar("R")


class EffectExecutionRepository(Protocol):
    def get_execution(self, execution_id: str) -> Any | None: ...

    def list_executions(self) -> list[Any]: ...

    def record_execution(self, record: Any) -> None: ...

    def find_execution_by_idempotency_key(self, idempotency_key: str) -> Any | None: ...

    def read_all(self) -> dict[str, Any]: ...

    def list_waiting_records(self, node_type: str | None = None) -> list[Any]: ...

    def list_records_by_node_type_and_statuses(
        self, *, node_type: str, statuses: set[str]
    ) -> list[Any]: ...

    def list_completion_pending_records(self) -> list[Any]: ...

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


class WorkflowConfigSnapshotRepository(Protocol):
    def get_snapshot(self, revision: str) -> dict[str, Any] | None: ...

    def list_snapshots(self) -> list[dict[str, Any]]: ...

    def put_snapshot(self, snapshot: dict[str, Any]) -> None: ...


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


class JsonWorkflowConfigSnapshotRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._store = JsonObjectStore(path, {})
        self._lock = RLock()

    def read_all(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            payload = self._store.read_object()
        if not isinstance(payload, dict):
            return {}
        normalized: dict[str, dict[str, Any]] = {}
        for revision, snapshot in payload.items():
            revision_key = str(revision or "").strip()
            if not revision_key or not isinstance(snapshot, dict):
                continue
            normalized[revision_key] = dict(snapshot)
        return normalized

    def get_snapshot(self, revision: str) -> dict[str, Any] | None:
        normalized_revision = str(revision or "").strip()
        if not normalized_revision:
            return None
        return self.read_all().get(normalized_revision)

    def list_snapshots(self) -> list[dict[str, Any]]:
        snapshots = list(self.read_all().values())
        snapshots.sort(
            key=lambda snapshot: (
                str(snapshot.get("captured_at") or ""),
                str(snapshot.get("revision") or ""),
            ),
            reverse=True,
        )
        return snapshots

    def put_snapshot(self, snapshot: dict[str, Any]) -> None:
        revision = str(snapshot.get("revision") or "").strip()
        captured_at = str(snapshot.get("captured_at") or "").strip()
        if not revision:
            raise ValueError("workflow config snapshot requires revision")
        if not captured_at:
            raise ValueError("workflow config snapshot requires captured_at")

        def _update(payload: dict[str, Any]) -> None:
            payload[revision] = dict(snapshot)

        with self._lock:
            self._store.update_object(_update)


class SqliteWorkflowConfigSnapshotRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        ensure_orchestration_schema(self.path)

    def get_snapshot(self, revision: str) -> dict[str, Any] | None:
        normalized_revision = str(revision or "").strip()
        if not normalized_revision:
            return None
        with self._lock, connect_orchestration_db(self.path) as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM workflow_config_snapshots
                WHERE revision = ?
                """,
                (normalized_revision,),
            ).fetchone()
        return json_loads_object(row["payload_json"]) if row is not None else None

    def list_snapshots(self) -> list[dict[str, Any]]:
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM workflow_config_snapshots
                ORDER BY captured_at DESC, revision DESC
                """
            ).fetchall()
        return [json_loads_object(row["payload_json"]) for row in rows]

    def put_snapshot(self, snapshot: dict[str, Any]) -> None:
        revision = str(snapshot.get("revision") or "").strip()
        captured_at = str(snapshot.get("captured_at") or "").strip()
        if not revision:
            raise ValueError("workflow config snapshot requires revision")
        if not captured_at:
            raise ValueError("workflow config snapshot requires captured_at")
        with self._lock, connect_orchestration_db(self.path) as connection:
            connection.execute(
                """
                INSERT INTO workflow_config_snapshots(
                    revision,
                    captured_at,
                    payload_json
                )
                VALUES (?, ?, ?)
                ON CONFLICT(revision) DO UPDATE SET
                    captured_at = excluded.captured_at,
                    payload_json = excluded.payload_json
                """,
                (revision, captured_at, json_dumps(snapshot)),
            )


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

    def list_records_by_node_type_and_statuses(
        self, *, node_type: str, statuses: set[str]
    ) -> list[TRecord]:
        normalized_type = str(node_type or "").strip()
        normalized_statuses = {
            str(status or "").strip() for status in statuses if str(status or "").strip()
        }
        if not normalized_type or not normalized_statuses:
            return []
        return [
            record
            for record in self.read_all().values()
            if str(getattr(record, "status", "") or "").strip() in normalized_statuses
            and normalized_type
            in {
                str(getattr(record, "effect_type", "") or "").strip(),
                str(getattr(record, "gate_type", "") or "").strip(),
            }
        ]

    def list_waiting_records(self, node_type: str | None = None) -> list[TRecord]:
        normalized_type = str(node_type or "").strip()
        records = []
        for record in self.read_all().values():
            if str(getattr(record, "status", "") or "").strip() != "waiting":
                continue
            if normalized_type and normalized_type not in {
                str(getattr(record, "effect_type", "") or "").strip(),
                str(getattr(record, "gate_type", "") or "").strip(),
            }:
                continue
            records.append(record)
        return records

    def list_completion_pending_records(self) -> list[TRecord]:
        return [
            record
            for record in self.read_all().values()
            if str(getattr(record, "status", "") or "").strip() == "completion_pending"
        ]

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


class SqliteExecutionRepository(Generic[TRecord]):
    def __init__(
        self,
        path: Path,
        model: type[TRecord],
        *,
        table_name: str,
        node_id_field: str,
        node_type_field: str,
        journal: Any | None = None,
        source: str | None = None,
        retention_days: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.model = model
        self.table_name = table_name
        self.node_id_field = node_id_field
        self.node_type_field = node_type_field
        self._journal = journal
        self._journal_source = source
        self._lock = RLock()
        ensure_orchestration_schema(self.path)
        maybe_run_orchestration_retention(self.path, retention_days=retention_days)

    def read_all(self) -> dict[str, TRecord]:
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                f"SELECT payload_json FROM {self.table_name} ORDER BY started_at ASC, execution_id ASC"
            ).fetchall()
        records = [self._record_from_row(row) for row in rows]
        return {str(getattr(record, "execution_id")): record for record in records}

    def get_execution(self, execution_id: str) -> TRecord | None:
        normalized_execution_id = str(execution_id or "").strip()
        if not normalized_execution_id:
            return None
        with self._lock, connect_orchestration_db(self.path) as connection:
            row = connection.execute(
                f"SELECT payload_json FROM {self.table_name} WHERE execution_id = ?",
                (normalized_execution_id,),
            ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def list_executions(self) -> list[TRecord]:
        return list(self.read_all().values())

    def list_records_by_node_type_and_statuses(
        self, *, node_type: str, statuses: set[str]
    ) -> list[TRecord]:
        normalized_type = str(node_type or "").strip()
        normalized_statuses = sorted(
            {str(status or "").strip() for status in statuses if str(status or "").strip()}
        )
        if not normalized_type or not normalized_statuses:
            return []
        placeholders = ", ".join("?" for _ in normalized_statuses)
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE {self.node_type_field} = ?
                  AND status IN ({placeholders})
                ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                """,
                [normalized_type, *normalized_statuses],
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_waiting_records(self, node_type: str | None = None) -> list[TRecord]:
        params: list[Any] = []
        where = ["status = 'waiting'"]
        normalized_type = str(node_type or "").strip()
        if normalized_type:
            where.append(f"{self.node_type_field} = ?")
            params.append(normalized_type)
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE {" AND ".join(where)}
                ORDER BY COALESCE(finished_at, started_at) DESC, execution_id DESC
                """,
                params,
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_completion_pending_records(self) -> list[TRecord]:
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE status = 'completion_pending'
                ORDER BY COALESCE(finished_at, started_at) DESC, execution_id DESC
                """
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def record_execution(self, record: TRecord) -> None:
        self.put(record)

    def put(self, record: TRecord) -> None:
        with self._lock, connect_orchestration_db(self.path) as connection:
            self._upsert_record(connection, record)
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
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return None
        with self._lock, connect_orchestration_db(self.path) as connection:
            row = connection.execute(
                f"SELECT payload_json FROM {self.table_name} WHERE idempotency_key = ?",
                (normalized_key,),
            ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def find_execution_by_idempotency_key(self, idempotency_key: str) -> TRecord | None:
        return self.find_by_idempotency_key(idempotency_key)

    def has_running_execution_for_event(self, event_id: str) -> bool:
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            return False
        with self._lock, connect_orchestration_db(self.path) as connection:
            row = connection.execute(
                f"""
                SELECT 1
                FROM {self.table_name}
                WHERE event_id = ? AND status IN ('running', 'completion_pending')
                LIMIT 1
                """,
                (normalized_event_id,),
            ).fetchone()
        return row is not None

    def list_active_records(self) -> list[TRecord]:
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE status IN ('running', 'waiting', 'failed', 'completion_pending')
                  AND dismissed_at IS NULL
                ORDER BY COALESCE(finished_at, started_at) DESC, execution_id DESC
                """
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_records_for_event(
        self, event_id: str, *, statuses: set[str] | None = None
    ) -> list[TRecord]:
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            return []
        params: list[Any] = [normalized_event_id]
        where = ["event_id = ?"]
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            where.append(f"status IN ({placeholders})")
            params.extend(sorted(statuses))
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE {" AND ".join(where)}
                ORDER BY COALESCE(finished_at, started_at) DESC, execution_id DESC
                """,
                params,
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_activity_between(self, *, start_at: datetime, end_at: datetime) -> list[TRecord]:
        start_text = datetime_to_storage(start_at)
        end_text = datetime_to_storage(end_at)
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE COALESCE(finished_at, started_at) BETWEEN ? AND ?
                ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                """,
                (start_text, end_text),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def clear_event_records(
        self,
        event_id: str,
        *,
        cleared_at: datetime,
        clear_reason: str,
        clear_error: str,
    ) -> int:
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            return 0
        updated = 0
        with self._lock:
            records = self.list_records_for_event(normalized_event_id)
            with connect_orchestration_db(self.path) as connection:
                for record in records:
                    status = str(getattr(record, "status", "") or "").strip()
                    if status == "failed":
                        setattr(record, "dismissed_at", cleared_at)
                        setattr(record, "dismiss_reason", "manual_dismiss_errored_workflow_run")
                        self._upsert_record(connection, record)
                        updated += 1
                        continue
                    if status not in {"running", "waiting", "completion_pending"}:
                        continue
                    setattr(record, "status", "failed")
                    setattr(record, "finished_at", cleared_at)
                    setattr(record, "cleared_at", cleared_at)
                    setattr(record, "clear_reason", clear_reason)
                    setattr(record, "error", clear_error)
                    setattr(record, "dismissed_at", cleared_at)
                    setattr(record, "dismiss_reason", "manual_clear_stale_workflow_runs")
                    self._upsert_record(connection, record)
                    updated += 1
        return updated

    def dismiss_failed_records_for_node(
        self,
        node_id: str,
        *,
        dismissed_at: datetime,
        dismiss_reason: str,
    ) -> int:
        normalized_node_id = str(node_id or "").strip()
        if not normalized_node_id:
            return 0
        updated = 0
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE {self.node_id_field} = ?
                  AND status = 'failed'
                  AND dismissed_at IS NULL
                """,
                (normalized_node_id,),
            ).fetchall()
            for row in rows:
                record = self._record_from_row(row)
                setattr(record, "dismissed_at", dismissed_at)
                setattr(record, "dismiss_reason", dismiss_reason)
                self._upsert_record(connection, record)
                updated += 1
        return updated

    def update_all(self, updater: Callable[[dict[str, TRecord]], R]) -> R:
        with self._lock:
            records = self.read_all()
            result = updater(records)
            with connect_orchestration_db(self.path) as connection:
                existing_ids = set(
                    row["execution_id"]
                    for row in connection.execute(f"SELECT execution_id FROM {self.table_name}")
                )
                next_ids = set(records)
                for execution_id in existing_ids - next_ids:
                    connection.execute(
                        f"DELETE FROM {self.table_name} WHERE execution_id = ?",
                        (execution_id,),
                    )
                for record in records.values():
                    self._upsert_record(connection, self.model.model_validate(record))
        return result

    def _record_from_row(self, row: Any) -> TRecord:
        return self.model.model_validate(json_loads_object(row["payload_json"]))

    def _record_columns(self, record: TRecord) -> tuple[Any, ...]:
        payload = record.model_dump(mode="json")
        return (
            str(getattr(record, "execution_id")),
            str(getattr(record, "idempotency_key")),
            str(getattr(record, "event_id")),
            str(getattr(record, "trace_id")),
            str(getattr(record, "source_node_id")),
            str(getattr(record, "source_trigger_type")),
            str(getattr(record, "status")),
            str(getattr(record, self.node_id_field)),
            str(getattr(record, self.node_type_field)),
            datetime_to_storage(getattr(record, "started_at")),
            datetime_to_storage(getattr(record, "finished_at")),
            datetime_to_storage(getattr(record, "cleared_at", None)),
            datetime_to_storage(getattr(record, "dismissed_at", None)),
            json_dumps(payload),
        )

    def _upsert_record(self, connection: Any, record: TRecord) -> None:
        node_id_column = self.node_id_field
        node_type_column = self.node_type_field
        connection.execute(
            f"""
            INSERT INTO {self.table_name}(
                execution_id,
                idempotency_key,
                event_id,
                trace_id,
                source_node_id,
                source_trigger_type,
                status,
                {node_id_column},
                {node_type_column},
                started_at,
                finished_at,
                cleared_at,
                dismissed_at,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(execution_id) DO UPDATE SET
                idempotency_key = excluded.idempotency_key,
                event_id = excluded.event_id,
                trace_id = excluded.trace_id,
                source_node_id = excluded.source_node_id,
                source_trigger_type = excluded.source_trigger_type,
                status = excluded.status,
                {node_id_column} = excluded.{node_id_column},
                {node_type_column} = excluded.{node_type_column},
                started_at = excluded.started_at,
                finished_at = excluded.finished_at,
                cleared_at = excluded.cleared_at,
                dismissed_at = excluded.dismissed_at,
                payload_json = excluded.payload_json
            """,
            self._record_columns(record),
        )


class SqliteGateExecutionRepository(SqliteExecutionRepository[TRecord]):
    def find_latest_by_gate_id(self, gate_id: str) -> TRecord | None:
        return self.find_latest_execution_for_gate(gate_id)

    def find_latest_execution_for_gate(self, gate_id: str) -> TRecord | None:
        normalized_gate_id = str(gate_id or "").strip()
        if not normalized_gate_id:
            return None
        with self._lock, connect_orchestration_db(self.path) as connection:
            row = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE gate_id = ?
                ORDER BY finished_at DESC, started_at DESC, execution_id DESC
                LIMIT 1
                """,
                (normalized_gate_id,),
            ).fetchone()
        return self._record_from_row(row) if row is not None else None
