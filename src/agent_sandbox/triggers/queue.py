from __future__ import annotations

from uuid import uuid4
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agent_sandbox.repositories.orchestration_db import (
    connect_orchestration_db,
    datetime_to_storage,
    ensure_orchestration_schema,
    json_dumps,
    json_loads_object,
    maybe_run_orchestration_retention,
)
from agent_sandbox.storage import JsonStore
from agent_sandbox.events.models import TriggerEvent
from agent_sandbox.workflow.events import (
    SqliteWorkflowEventJournal,
    WorkflowEventJournal,
    workflow_event_journal_path,
)

DeliveryState = Literal["pending", "inflight", "delivered"]


class TriggerQueueRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1)
    trigger_id: str = Field(min_length=1)
    trigger_type: str
    trace_id: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    occurred_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)
    delivery_state: DeliveryState = "pending"
    inflight_at: datetime | None = None
    delivered_at: datetime | None = None
    idempotency_key: str | None = None
    clear_reason: str | None = None
    error: str | None = None


class TriggerEventQueue:
    """Durable trigger event queue backed by the shared JSON store conventions."""

    def __init__(
        self,
        path: Path | str = Path("runtime") / "trigger-event-queue.json",
        *,
        journal_path: Path | str | None = None,
    ) -> None:
        self.path = Path(path)
        self._store = JsonStore(path=self.path, model=TriggerQueueRecord)
        self._journal = WorkflowEventJournal(
            journal_path or workflow_event_journal_path(self.path.parent)
        )
        self._lock = RLock()

    def publish(
        self, event: TriggerEvent, *, idempotency_key: str | None = None
    ) -> TriggerQueueRecord:
        normalized_key = self._normalize_idempotency_key(idempotency_key)
        trace_id = self._normalize_trace_id(event.trace_id)
        published = False
        with self._lock:
            def update(items: dict[str, TriggerQueueRecord]) -> TriggerQueueRecord:
                nonlocal published
                if normalized_key is not None:
                    for record in items.values():
                        if record.idempotency_key == normalized_key:
                            return record
                existing = items.get(event.event_id)
                if existing is not None:
                    return existing

                created = TriggerQueueRecord(
                    event_id=event.event_id,
                    trigger_id=event.trigger_id,
                    trigger_type=event.trigger_type,
                    trace_id=trace_id,
                    occurred_at=event.occurred_at,
                    payload=event.payload,
                    delivery_state="pending",
                    delivered_at=None,
                    idempotency_key=normalized_key,
                )
                items[event.event_id] = created
                published = True
                return created

            record = self._store.update_all(update)
            if not published:
                return record
            self._journal.append(
                "workflow.trigger_event.published",
                source="trigger_queue",
                subject_id=event.event_id,
                payload={"trigger_id": event.trigger_id, "trigger_type": event.trigger_type},
            )
            return record

    def contains(self, event_id: str) -> bool:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            return False

        with self._lock:
            return normalized_event_id in self._store.read_all()

    def list_pending(
        self,
        *,
        trigger_id: str | None = None,
        trigger_type: str | None = None,
        limit: int | None = None,
    ) -> list[TriggerQueueRecord]:
        if limit is not None and limit <= 0:
            return []

        with self._lock:
            pending = [
                record
                for record in self._store.read_all().values()
                if record.delivery_state == "pending"
                and (trigger_id is None or record.trigger_id == trigger_id)
                and (trigger_type is None or record.trigger_type == trigger_type)
            ]

        pending.sort(key=lambda record: (record.occurred_at, record.event_id))
        if limit is not None:
            return pending[:limit]
        return pending

    def list_active_records(self) -> list[TriggerQueueRecord]:
        with self._lock:
            active = [
                record
                for record in self._store.read_all().values()
                if record.delivery_state != "delivered"
            ]
        active.sort(key=lambda record: (record.occurred_at, record.event_id))
        return active

    def mark_delivered(self, event_id: str, *, delivered_at: datetime | None = None) -> bool:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            return False

        delivered = False
        with self._lock:
            def update(items: dict[str, TriggerQueueRecord]) -> bool:
                nonlocal delivered
                record = items.get(normalized_event_id)
                if record is None:
                    return False
                if record.delivery_state == "delivered":
                    return True

                record.delivery_state = "delivered"
                record.inflight_at = None
                record.delivered_at = delivered_at or datetime.now(UTC)
                items[normalized_event_id] = record
                delivered = True
                return True

            found = self._store.update_all(update)
            if not found:
                return False
            if not delivered:
                return True
            self._journal.append(
                "workflow.trigger_event.delivered",
                source="trigger_queue",
                subject_id=normalized_event_id,
            )
            return True

    def mark_inflight(self, event_id: str, *, inflight_at: datetime | None = None) -> bool:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            return False

        marked = False
        with self._lock:
            def update(items: dict[str, TriggerQueueRecord]) -> bool:
                nonlocal marked
                record = items.get(normalized_event_id)
                if record is None:
                    return False
                if record.delivery_state == "delivered":
                    return False

                record.delivery_state = "inflight"
                record.inflight_at = inflight_at or datetime.now(UTC)
                record.delivered_at = None
                items[normalized_event_id] = record
                marked = True
                return True

            if not self._store.update_all(update):
                return False
            if not marked:
                return False
            self._journal.append(
                "workflow.trigger_event.inflight",
                source="trigger_queue",
                subject_id=normalized_event_id,
            )
            return True

    def reset_inflight(self) -> int:
        with self._lock:
            def update(items: dict[str, TriggerQueueRecord]) -> int:
                reset_count = 0
                for event_id, record in items.items():
                    if record.delivery_state != "inflight":
                        continue
                    record.delivery_state = "pending"
                    record.inflight_at = None
                    record.delivered_at = None
                    items[event_id] = record
                    reset_count += 1
                return reset_count

            reset_count = self._store.update_all(update)
            if reset_count > 0:
                self._journal.append(
                    "workflow.trigger_event.reset_inflight",
                    source="trigger_queue",
                    payload={"reset_count": reset_count},
                )
        return reset_count

    @staticmethod
    def _normalize_idempotency_key(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _normalize_trace_id(value: str | None) -> str:
        if value is None:
            return str(uuid4())
        normalized = value.strip()
        return normalized or str(uuid4())


class SqliteTriggerEventQueue:
    """Durable trigger event queue backed by the orchestration SQLite database."""

    def __init__(
        self,
        path: Path | str,
        *,
        journal: SqliteWorkflowEventJournal | None = None,
        retention_days: int | None = None,
    ) -> None:
        self.path = Path(path)
        ensure_orchestration_schema(self.path)
        maybe_run_orchestration_retention(self.path, retention_days=retention_days)
        self._journal = journal or SqliteWorkflowEventJournal(
            self.path, retention_days=retention_days
        )
        self._lock = RLock()

    def publish(
        self, event: TriggerEvent, *, idempotency_key: str | None = None
    ) -> TriggerQueueRecord:
        normalized_key = TriggerEventQueue._normalize_idempotency_key(idempotency_key)
        trace_id = TriggerEventQueue._normalize_trace_id(event.trace_id)
        published = False
        with self._lock:
            with connect_orchestration_db(self.path) as connection:
                if normalized_key is not None:
                    existing = self._fetch_record_by_idempotency_key(
                        connection, normalized_key
                    )
                    if existing is not None:
                        return existing
                existing = self._fetch_record(connection, event.event_id)
                if existing is not None:
                    return existing

                created = TriggerQueueRecord(
                    event_id=event.event_id,
                    trigger_id=event.trigger_id,
                    trigger_type=event.trigger_type,
                    trace_id=trace_id,
                    occurred_at=event.occurred_at,
                    payload=event.payload,
                    delivery_state="pending",
                    delivered_at=None,
                    idempotency_key=normalized_key,
                )
                try:
                    self._insert_record(connection, created)
                    published = True
                except Exception:
                    existing = self._fetch_record(connection, event.event_id)
                    if existing is None and normalized_key is not None:
                        existing = self._fetch_record_by_idempotency_key(
                            connection, normalized_key
                        )
                    if existing is not None:
                        return existing
                    raise

        if published:
            self._journal.append(
                "workflow.trigger_event.published",
                source="trigger_queue",
                subject_id=event.event_id,
                payload={"trigger_id": event.trigger_id, "trigger_type": event.trigger_type},
            )
        return created

    def contains(self, event_id: str) -> bool:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            return False
        with self._lock, connect_orchestration_db(self.path) as connection:
            row = connection.execute(
                "SELECT 1 FROM trigger_events WHERE event_id = ?",
                (normalized_event_id,),
            ).fetchone()
        return row is not None

    def list_pending(
        self,
        *,
        trigger_id: str | None = None,
        trigger_type: str | None = None,
        limit: int | None = None,
    ) -> list[TriggerQueueRecord]:
        if limit is not None and limit <= 0:
            return []
        where = ["delivery_state = 'pending'"]
        params: list[Any] = []
        if trigger_id is not None:
            where.append("trigger_id = ?")
            params.append(trigger_id)
        if trigger_type is not None:
            where.append("trigger_type = ?")
            params.append(trigger_type)
        sql = (
            "SELECT payload_json FROM trigger_events WHERE "
            + " AND ".join(where)
            + " ORDER BY occurred_at ASC, event_id ASC"
        )
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_active_records(self) -> list[TriggerQueueRecord]:
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM trigger_events
                WHERE delivery_state != 'delivered'
                ORDER BY occurred_at ASC, event_id ASC
                """
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def list_activity_between(
        self, *, start_at: datetime, end_at: datetime
    ) -> list[TriggerQueueRecord]:
        start_text = datetime_to_storage(start_at)
        end_text = datetime_to_storage(end_at)
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM trigger_events
                WHERE COALESCE(delivered_at, inflight_at, occurred_at) BETWEEN ? AND ?
                ORDER BY COALESCE(delivered_at, inflight_at, occurred_at) ASC, event_id ASC
                """,
                (start_text, end_text),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def mark_delivered(self, event_id: str, *, delivered_at: datetime | None = None) -> bool:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            return False

        delivered = False
        with self._lock:
            with connect_orchestration_db(self.path) as connection:
                record = self._fetch_record(connection, normalized_event_id)
                if record is None:
                    return False
                if record.delivery_state == "delivered":
                    return True
                record.delivery_state = "delivered"
                record.inflight_at = None
                record.delivered_at = delivered_at or datetime.now(UTC)
                self._upsert_record(connection, record)
                delivered = True
        if delivered:
            self._journal.append(
                "workflow.trigger_event.delivered",
                source="trigger_queue",
                subject_id=normalized_event_id,
            )
        return True

    def mark_inflight(self, event_id: str, *, inflight_at: datetime | None = None) -> bool:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            return False

        marked = False
        with self._lock:
            with connect_orchestration_db(self.path) as connection:
                record = self._fetch_record(connection, normalized_event_id)
                if record is None or record.delivery_state == "delivered":
                    return False
                record.delivery_state = "inflight"
                record.inflight_at = inflight_at or datetime.now(UTC)
                record.delivered_at = None
                self._upsert_record(connection, record)
                marked = True
        if marked:
            self._journal.append(
                "workflow.trigger_event.inflight",
                source="trigger_queue",
                subject_id=normalized_event_id,
            )
        return marked

    def reset_inflight(self) -> int:
        with self._lock:
            with connect_orchestration_db(self.path) as connection:
                rows = connection.execute(
                    """
                    SELECT payload_json
                    FROM trigger_events
                    WHERE delivery_state = 'inflight'
                    """
                ).fetchall()
                records = [self._record_from_row(row) for row in rows]
                for record in records:
                    record.delivery_state = "pending"
                    record.inflight_at = None
                    record.delivered_at = None
                    self._upsert_record(connection, record)
        reset_count = len(records)
        if reset_count > 0:
            self._journal.append(
                "workflow.trigger_event.reset_inflight",
                source="trigger_queue",
                payload={"reset_count": reset_count},
            )
        return reset_count

    def mark_event_cleared(
        self,
        event_id: str,
        *,
        cleared_at: datetime,
        clear_reason: str,
        error: str,
    ) -> int:
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            return 0
        updated = 0
        with self._lock:
            with connect_orchestration_db(self.path) as connection:
                record = self._fetch_record(connection, normalized_event_id)
                if record is None or record.delivery_state == "delivered":
                    return 0
                record.delivery_state = "delivered"
                record.inflight_at = None
                record.delivered_at = cleared_at
                record.clear_reason = clear_reason
                record.error = error
                self._upsert_record(connection, record)
                updated = 1
        if updated:
            self._journal.append(
                "workflow.trigger_event.delivered",
                source="trigger_queue",
                subject_id=normalized_event_id,
            )
        return updated

    def read_all(self) -> dict[str, TriggerQueueRecord]:
        with self._lock, connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM trigger_events ORDER BY occurred_at ASC, event_id ASC"
            ).fetchall()
        return {record.event_id: record for record in (self._record_from_row(row) for row in rows)}

    def update_all(self, updater):
        with self._lock:
            records = self.read_all()
            result = updater(records)
            with connect_orchestration_db(self.path) as connection:
                existing_ids = set(
                    row["event_id"]
                    for row in connection.execute("SELECT event_id FROM trigger_events")
                )
                next_ids = set(records)
                for event_id in existing_ids - next_ids:
                    connection.execute(
                        "DELETE FROM trigger_events WHERE event_id = ?",
                        (event_id,),
                    )
                for record in records.values():
                    self._upsert_record(connection, TriggerQueueRecord.model_validate(record))
            return result

    @staticmethod
    def _record_from_row(row: Any) -> TriggerQueueRecord:
        return TriggerQueueRecord.model_validate(json_loads_object(row["payload_json"]))

    @classmethod
    def _record_columns(cls, record: TriggerQueueRecord) -> tuple[Any, ...]:
        payload = record.model_dump(mode="json")
        return (
            record.event_id,
            record.idempotency_key,
            record.delivery_state,
            record.trigger_id,
            record.trigger_type,
            record.trace_id,
            datetime_to_storage(record.occurred_at),
            datetime_to_storage(record.inflight_at),
            datetime_to_storage(record.delivered_at),
            record.clear_reason,
            record.error,
            json_dumps(payload),
        )

    @classmethod
    def _insert_record(cls, connection: Any, record: TriggerQueueRecord) -> None:
        connection.execute(
            """
            INSERT INTO trigger_events(
                event_id,
                idempotency_key,
                delivery_state,
                trigger_id,
                trigger_type,
                trace_id,
                occurred_at,
                inflight_at,
                delivered_at,
                clear_reason,
                error,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            cls._record_columns(record),
        )

    @classmethod
    def _upsert_record(cls, connection: Any, record: TriggerQueueRecord) -> None:
        connection.execute(
            """
            INSERT INTO trigger_events(
                event_id,
                idempotency_key,
                delivery_state,
                trigger_id,
                trigger_type,
                trace_id,
                occurred_at,
                inflight_at,
                delivered_at,
                clear_reason,
                error,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                idempotency_key = excluded.idempotency_key,
                delivery_state = excluded.delivery_state,
                trigger_id = excluded.trigger_id,
                trigger_type = excluded.trigger_type,
                trace_id = excluded.trace_id,
                occurred_at = excluded.occurred_at,
                inflight_at = excluded.inflight_at,
                delivered_at = excluded.delivered_at,
                clear_reason = excluded.clear_reason,
                error = excluded.error,
                payload_json = excluded.payload_json
            """,
            cls._record_columns(record),
        )

    @classmethod
    def _fetch_record(cls, connection: Any, event_id: str) -> TriggerQueueRecord | None:
        row = connection.execute(
            "SELECT payload_json FROM trigger_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return cls._record_from_row(row) if row is not None else None

    @classmethod
    def _fetch_record_by_idempotency_key(
        cls, connection: Any, idempotency_key: str
    ) -> TriggerQueueRecord | None:
        row = connection.execute(
            "SELECT payload_json FROM trigger_events WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return cls._record_from_row(row) if row is not None else None
