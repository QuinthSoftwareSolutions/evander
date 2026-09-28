from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from evander.repositories.sqlite_runtime import storage_lock
from evander.repositories.operational_payloads import (
    discard_record_field_references, load_record, prepare_record, store_prepared_record,
)
from evander.repositories.execution_history import prepare_execution_history_payloads
from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    connect_orchestration_db_read_only,
    datetime_to_storage,
    ensure_orchestration_schema,
)
from evander.events.models import TriggerEvent
from evander.workflow.events import SqliteWorkflowEventJournal

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
    config_revision: str | None = None
    directory_path: str | None = None


@dataclass(frozen=True)
class TriggerQueuePublishResult:
    record: TriggerQueueRecord
    created: bool


def _normalize_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _normalize_trace_id(value: str | None) -> str:
    if value is None:
        return str(uuid4())
    normalized = value.strip()
    return normalized or str(uuid4())


class SqliteTriggerEventQueue:
    """Durable trigger event queue backed by the orchestration SQLite database."""

    supports_atomic_publication = True

    def __init__(
        self,
        path: Path | str,
        *,
        journal: SqliteWorkflowEventJournal | None = None,
        retention_days: int | None = None,
        compact_history_retention_days: int | None = None,
    ) -> None:
        if retention_days is not None and compact_history_retention_days is None:
            raise ValueError("compact_history_retention_days is required when retention_days is configured")
        self.path = Path(path)
        ensure_orchestration_schema(self.path)
        self._journal = journal or SqliteWorkflowEventJournal(
            self.path,
            retention_days=retention_days,
            compact_history_retention_days=compact_history_retention_days,
        )
        self._lock = RLock()

    def publish(
        self,
        event: TriggerEvent,
        *,
        idempotency_key: str | None = None,
        config_revision: str | None = None,
        directory_path: str | None = None,
    ) -> TriggerQueueRecord:
        return self.publish_result(
            event,
            idempotency_key=idempotency_key,
            config_revision=config_revision,
            directory_path=directory_path,
        ).record

    def publish_result(
        self,
        event: TriggerEvent,
        *,
        idempotency_key: str | None = None,
        config_revision: str | None = None,
        directory_path: str | None = None,
    ) -> TriggerQueuePublishResult:
        normalized_key = _normalize_idempotency_key(idempotency_key)
        trace_id = _normalize_trace_id(event.trace_id)
        created = TriggerQueueRecord(
            event_id=event.event_id, trigger_id=event.trigger_id, trigger_type=event.trigger_type,
            trace_id=trace_id, occurred_at=event.occurred_at, payload=event.payload,
            delivery_state="pending", delivered_at=None, idempotency_key=normalized_key,
            config_revision=str(config_revision or "").strip() or None,
            directory_path=str(directory_path or "").strip() or None,
        )
        prepared = self._prepare_record(created)
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if normalized_key is not None:
                existing = self._fetch_record_by_idempotency_key(connection, normalized_key)
                if existing is not None:
                    return TriggerQueuePublishResult(record=existing, created=False)
            existing = self._fetch_record(connection, event.event_id)
            if existing is not None:
                return TriggerQueuePublishResult(record=existing, created=False)
            self._insert_record(connection, created, prepared=prepared)
            self._append_journal(connection, "workflow.trigger_event.published", event.event_id,
                                 payload={"trigger_id": event.trigger_id, "trigger_type": event.trigger_type})
        return TriggerQueuePublishResult(record=created, created=True)

    def _append_journal(self, connection: Any, event_type: str, event_id: str | None,
                        *, payload: dict[str, Any] | None = None) -> None:
        from evander.repositories.orchestration import SqliteExecutionRepository

        SqliteExecutionRepository._append_journal_row(
            connection, event_id=str(uuid4()), event_type=event_type, source="trigger_queue",
            subject_id=event_id, occurred_at=datetime.now(UTC), payload=payload or {},
        )

    def contains(self, event_id: str) -> bool:
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            return False
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
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
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(sql, params).fetchall()
            return [self._record_from_row(row, connection) for row in rows]

    def list_active_records(self) -> list[TriggerQueueRecord]:
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                """
                SELECT payload_json
                FROM trigger_events
                WHERE delivery_state != 'delivered'
                ORDER BY occurred_at ASC, event_id ASC
                """
            ).fetchall()
            return [self._record_from_row(row, connection) for row in rows]

    def list_active_summaries(self) -> list[dict[str, Any]]:
        """Read queue display metadata without materializing event payloads."""
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                """
                SELECT event_id, trigger_id, trigger_type, trace_id, delivery_state,
                       occurred_at, inflight_at, delivered_at, error
                FROM trigger_events
                WHERE delivery_state != 'delivered'
                ORDER BY occurred_at ASC, event_id ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_activity_summaries_between(
        self, *, start_at: datetime, end_at: datetime
    ) -> list[dict[str, Any]]:
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                """
                SELECT event_id, trigger_id, trigger_type, trace_id, delivery_state,
                       occurred_at, inflight_at, delivered_at, error,
                       json_extract(payload_json, '$.started_at') AS started_at,
                       json_extract(payload_json, '$.finished_at') AS finished_at,
                       json_extract(payload_json, '$.dismissed_at') AS dismissed_at
                FROM trigger_events
                WHERE COALESCE(delivered_at, inflight_at, occurred_at) BETWEEN ? AND ?
                ORDER BY COALESCE(delivered_at, inflight_at, occurred_at) ASC, event_id ASC
                """,
                (datetime_to_storage(start_at), datetime_to_storage(end_at)),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_activity_between(
        self, *, start_at: datetime, end_at: datetime
    ) -> list[TriggerQueueRecord]:
        start_text = datetime_to_storage(start_at)
        end_text = datetime_to_storage(end_at)
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                """
                SELECT payload_json
                FROM trigger_events
                WHERE COALESCE(delivered_at, inflight_at, occurred_at) BETWEEN ? AND ?
                ORDER BY COALESCE(delivered_at, inflight_at, occurred_at) ASC, event_id ASC
                """,
                (start_text, end_text),
            ).fetchall()
            return [self._record_from_row(row, connection) for row in rows]

    def _update_delivery(self, connection: Any, event_id: str, *, state: str, at: datetime,
                         clear_reason: str | None = None, error: str | None = None) -> bool:
        row = connection.execute("SELECT delivery_state FROM trigger_events WHERE event_id=?", (event_id,)).fetchone()
        if row is None or row["delivery_state"] == "delivered":
            return row is not None and state == "delivered"
        timestamp = datetime_to_storage(at)
        inflight_at = timestamp if state == "inflight" else None
        delivered_at = timestamp if state == "delivered" else None
        # json_set preserves reference nodes and legacy inline payloads without
        # decoding, hashing or compressing operational inputs on a state change.
        fields = {"delivery_state": state, "inflight_at": inflight_at, "delivered_at": delivered_at}
        if clear_reason is not None:
            fields["clear_reason"] = clear_reason
        if error is not None:
            fields["error"] = error
        json_params = [value for key, value in fields.items() for value in (f"$.{key}", value)]
        connection.execute("UPDATE trigger_events SET " + ",".join(f"{key}=?" for key in fields)
            + ",payload_json=json_set(payload_json," + ",".join("?,?" for _ in fields) + ") WHERE event_id=?",
            [*fields.values(), *json_params, event_id])
        discard_record_field_references(
            connection, kind="trigger", record_id=event_id, fields=fields,
        )
        connection.execute("""UPDATE workflow_node_execution_history SET status=?,
            finished_at=CASE WHEN ?='pending' THEN started_at ELSE ? END,updated_at=?,
            error=COALESCE(?,error) WHERE execution_key=?""",
            (state,state,timestamp,timestamp,error,f"trigger:{event_id}"))
        return True

    def mark_delivered(self, event_id: str, *, delivered_at: datetime | None = None) -> bool:
        event_id = event_id.strip()
        if not event_id:
            return False
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            delivered = self._update_delivery(connection, event_id, state="delivered", at=delivered_at or datetime.now(UTC))
            if delivered:
                self._append_journal(connection, "workflow.trigger_event.delivered", event_id)
        return delivered

    def mark_inflight(self, event_id: str, *, inflight_at: datetime | None = None) -> bool:
        event_id = event_id.strip()
        if not event_id:
            return False
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            marked = self._update_delivery(connection, event_id, state="inflight", at=inflight_at or datetime.now(UTC))
            if marked:
                self._append_journal(connection, "workflow.trigger_event.inflight", event_id)
        return marked

    def reset_inflight(self) -> int:
        count, after = 0, ""
        with storage_lock(self._lock):
            while True:
                with connect_orchestration_db(self.path) as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    rows = connection.execute(
                        "SELECT event_id FROM trigger_events WHERE delivery_state='inflight' "
                        "AND event_id>? ORDER BY event_id LIMIT 25", (after,),
                    ).fetchall()
                    if not rows:
                        return count
                    now = datetime.now(UTC)
                    for row in rows:
                        self._update_delivery(connection, row["event_id"], state="pending", at=now)
                    count += len(rows)
                    after = rows[-1]["event_id"]
                    self._append_journal(connection, "workflow.trigger_event.reset_inflight", None,
                                         payload={"reset_count": len(rows)})

    def mark_event_cleared(
        self, event_id: str, *, cleared_at: datetime, clear_reason: str, error: str,
    ) -> int:
        event_id = str(event_id or "").strip()
        if not event_id:
            return 0
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT delivery_state FROM trigger_events WHERE event_id=?", (event_id,)).fetchone()
            if row is None or row["delivery_state"] == "delivered":
                return 0
            self._update_delivery(connection, event_id, state="delivered", at=cleared_at,
                                  clear_reason=clear_reason, error=error)
            self._append_journal(connection, "workflow.trigger_event.delivered", event_id)
            return 1

    def read_all(self) -> dict[str, TriggerQueueRecord]:
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                "SELECT payload_json FROM trigger_events ORDER BY occurred_at ASC, event_id ASC"
            ).fetchall()
            return {record.event_id: record for record in (self._record_from_row(row, connection) for row in rows)}

    def update_all(self, updater):
        with storage_lock(self._lock):
            records = self.read_all()
            result = updater(records)
            prepared_records = [(validated, self._prepare_record(validated))
                                for record in records.values()
                                for validated in [TriggerQueueRecord.model_validate(record)]]
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
                for record, prepared in prepared_records:
                    self._upsert_record(connection, record, prepared=prepared)
            return result

    @staticmethod
    def _record_from_row(row: Any, connection: Any) -> TriggerQueueRecord:
        return TriggerQueueRecord.model_validate(load_record(connection, row["payload_json"]))

    @staticmethod
    def _prepare_record(record: TriggerQueueRecord) -> tuple[Any, ...]:
        payload = record.model_dump(mode="json")
        history = (prepare_execution_history_payloads(kind="trigger", record=payload)
                   if record.trigger_type.startswith("trigger:") else None)
        return payload, prepare_record(kind="trigger", record=payload), history

    @classmethod
    def _record_columns(cls, connection: Any, record: TriggerQueueRecord, prepared: tuple[Any, ...]) -> tuple[Any, ...]:
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
            store_prepared_record(connection, prepared[1]),
        )

    @classmethod
    def _insert_record(cls, connection: Any, record: TriggerQueueRecord, *, prepared: tuple[Any, ...] | None = None) -> None:
        prepared = prepared or cls._prepare_record(record)
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
            cls._record_columns(connection, record, prepared),
        )
        cls._archive_record(connection, record, prepared=prepared)

    @classmethod
    def _upsert_record(cls, connection: Any, record: TriggerQueueRecord, *, prepared: tuple[Any, ...] | None = None) -> None:
        prepared = prepared or cls._prepare_record(record)
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
            cls._record_columns(connection, record, prepared),
        )
        cls._archive_record(connection, record, prepared=prepared)

    @staticmethod
    def _archive_record(connection: Any, record: TriggerQueueRecord, *, prepared: tuple[Any, ...] | None = None) -> None:
        from evander.repositories.execution_history import archive_execution_record

        archive_execution_record(
            connection,
            kind="trigger",
            record=prepared[0] if prepared else record.model_dump(mode="json"),
            prepared_payloads=prepared[2] if prepared else None,
        )

    @classmethod
    def _fetch_record(cls, connection: Any, event_id: str) -> TriggerQueueRecord | None:
        row = connection.execute(
            "SELECT payload_json FROM trigger_events WHERE event_id = ?",
            (event_id,),
        ).fetchone()
        return cls._record_from_row(row, connection) if row is not None else None

    @classmethod
    def _fetch_record_by_idempotency_key(
        cls, connection: Any, idempotency_key: str
    ) -> TriggerQueueRecord | None:
        row = connection.execute(
            "SELECT payload_json FROM trigger_events WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return cls._record_from_row(row, connection) if row is not None else None
