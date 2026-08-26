from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    datetime_to_storage,
    ensure_orchestration_schema,
    json_dumps,
    json_loads_object,
    maybe_run_orchestration_retention,
)

_EVENT_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
EXTERNAL_WORKFLOW_EVENT_PREFIX = "external."


class InternalEventValidationError(ValueError):
    """Raised when an operator-defined internal event is invalid."""


class WakeNotifier(Protocol):
    def notify(self) -> None: ...


class InternalEventSource(BaseModel):
    source_type: str | None = None
    source_name: str | None = None
    user_id: str | None = None
    user_login: str | None = None
    workflow_run_id: str | None = None
    workflow_event_id: str | None = None
    workflow_trace_id: str | None = None
    node_id: str | None = None
    node_type: str | None = None


class InternalEvent(BaseModel):
    event_id: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_key: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source: InternalEventSource | None = None
    idempotency_key: str | None = None

    @field_validator("event_type")
    @classmethod
    def validate_event_type(cls, value: str) -> str:
        return validate_internal_event_type(value)

    @field_validator("correlation_key", "idempotency_key", mode="before")
    @classmethod
    def normalize_optional_string(cls, value: object) -> str | None:
        return _normalize_optional_string(value)


@dataclass(frozen=True)
class InternalEventSubscriptionSpec:
    trigger_id: str
    trigger_type: str
    event_type: str
    filter_payload: Mapping[str, Any]

    @property
    def filter_hash(self) -> str:
        material = json_dumps(dict(self.filter_payload))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def validate_internal_event_type(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise InternalEventValidationError("event_type must be a non-empty string")
    if not _EVENT_TYPE_RE.fullmatch(text):
        raise InternalEventValidationError(
            "event_type must start with an alphanumeric character and contain only "
            "letters, numbers, '.', '_', ':', or '-'"
        )
    return text


class InternalEventBus:
    """SQLite-backed durable bus for operator-defined internal events."""

    def __init__(
        self,
        database_path: Path | str,
        *,
        retention_days: int | None = None,
        compact_history_retention_days: int = 365,
        wake_notifier: WakeNotifier | None = None,
    ) -> None:
        self.path = Path(database_path)
        self._wake_notifier = wake_notifier
        ensure_orchestration_schema(self.path)
        maybe_run_orchestration_retention(
            self.path,
            retention_days=retention_days,
            compact_history_retention_days=compact_history_retention_days,
        )

    def publish(
        self,
        *,
        event_type: object,
        payload: dict[str, Any] | None = None,
        correlation_key: object | None = None,
        source: InternalEventSource | dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        created_at: datetime | None = None,
    ) -> InternalEvent:
        normalized_type = validate_internal_event_type(event_type)
        normalized_correlation = _normalize_optional_string(correlation_key)
        normalized_idempotency = _normalize_optional_string(idempotency_key)
        normalized_payload = dict(payload or {})
        normalized_source = (
            source
            if isinstance(source, InternalEventSource) or source is None
            else InternalEventSource.model_validate(source)
        )
        event = InternalEvent(
            event_id=_event_id(
                event_type=normalized_type,
                payload=normalized_payload,
                correlation_key=normalized_correlation,
                idempotency_key=normalized_idempotency,
            ),
            event_type=normalized_type,
            payload=normalized_payload,
            correlation_key=normalized_correlation,
            created_at=created_at or datetime.now(UTC),
            source=normalized_source,
            idempotency_key=normalized_idempotency,
        )
        with connect_orchestration_db(self.path) as connection:
            event, created = self._publish_with_connection(connection, event)
        if created and self._wake_notifier is not None:
            self._wake_notifier.notify()
        return event

    def publish_scheduled(self, schedule_id: str) -> tuple[InternalEvent, bool]:
        """Atomically publish and complete one due scheduled event."""

        normalized_schedule_id = str(schedule_id).strip()
        if not normalized_schedule_id:
            raise ValueError("schedule_id must be a non-empty string")
        with connect_orchestration_db(self.path) as connection:
            scheduled = connection.execute(
                "SELECT * FROM scheduled_internal_events WHERE schedule_id = ?",
                (normalized_schedule_id,),
            ).fetchone()
            if scheduled is None:
                raise KeyError(normalized_schedule_id)
            if str(scheduled["status"]) == "published":
                published = connection.execute(
                    "SELECT * FROM internal_events WHERE event_id = ?",
                    (scheduled["published_event_id"],),
                ).fetchone()
                if published is None:
                    raise RuntimeError(
                        f"scheduled event {normalized_schedule_id} references a missing event"
                    )
                return _event_from_row(published), False
            run_at = _datetime_from_storage(scheduled["run_at"])
            if run_at > datetime.now(UTC):
                raise ValueError(f"scheduled event {normalized_schedule_id} is not due")
            source_payload = json_loads_object(scheduled["source_json"])
            source = (
                InternalEventSource.model_validate(source_payload)
                if source_payload
                else InternalEventSource()
            ).model_copy(update={"source_type": "scheduled_internal_event"})
            event = InternalEvent(
                event_id=_event_id(
                    event_type=str(scheduled["event_type"]),
                    payload=json_loads_object(scheduled["payload_json"]),
                    correlation_key=_normalize_optional_string(scheduled["correlation_key"]),
                    idempotency_key=f"scheduled-event:{normalized_schedule_id}",
                ),
                event_type=str(scheduled["event_type"]),
                payload=json_loads_object(scheduled["payload_json"]),
                correlation_key=_normalize_optional_string(scheduled["correlation_key"]),
                source=source,
                idempotency_key=f"scheduled-event:{normalized_schedule_id}",
            )
            event, _created = self._publish_with_connection(connection, event)
            published_at = datetime_to_storage(datetime.now(UTC))
            result = connection.execute(
                """
                UPDATE scheduled_internal_events
                SET status = 'published', published_at = ?, published_event_id = ?
                WHERE schedule_id = ? AND status = 'pending'
                """,
                (published_at, event.event_id, normalized_schedule_id),
            )
            if result.rowcount != 1:
                raise RuntimeError(
                    f"scheduled event {normalized_schedule_id} changed during publication"
                )
        if self._wake_notifier is not None:
            self._wake_notifier.notify()
        return event, True

    def _publish_with_connection(
        self,
        connection: sqlite3.Connection,
        event: InternalEvent,
    ) -> tuple[InternalEvent, bool]:
        if event.idempotency_key is not None:
            row = connection.execute(
                "SELECT * FROM internal_events WHERE idempotency_key = ?",
                (event.idempotency_key,),
            ).fetchone()
            if row is not None:
                return _event_from_row(row), False
        try:
            connection.execute(
                """
                INSERT INTO internal_events(
                    event_id, event_type, correlation_key, created_at,
                    source_json, idempotency_key, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.correlation_key,
                    datetime_to_storage(event.created_at),
                    json_dumps(event.source.model_dump(mode="json"))
                    if event.source is not None
                    else None,
                    event.idempotency_key,
                    json_dumps(event.payload),
                ),
            )
        except sqlite3.IntegrityError:
            row = connection.execute(
                "SELECT * FROM internal_events WHERE event_id = ? OR idempotency_key = ?",
                (event.event_id, event.idempotency_key),
            ).fetchone()
            if row is None:
                raise
            return _event_from_row(row), False
        workflow_event_id = str(uuid4())
        occurred_at = datetime.now(UTC)
        workflow_payload = {
            "event_id": workflow_event_id,
            "event_type": "workflow.internal_event.published",
            "occurred_at": datetime_to_storage(occurred_at),
            "source": "internal_event_bus",
            "subject_id": event.event_id,
            "payload": {
                "event_type": event.event_type,
                "correlation_key": event.correlation_key,
                "idempotency_key": event.idempotency_key,
            },
        }
        connection.execute(
            """
            INSERT INTO workflow_events(
                event_id, event_type, source, subject_id, occurred_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                workflow_event_id,
                workflow_payload["event_type"],
                workflow_payload["source"],
                workflow_payload["subject_id"],
                workflow_payload["occurred_at"],
                json_dumps(workflow_payload),
            ),
        )
        return event, True

    def list_events(
        self,
        *,
        event_type: str | None = None,
        after_row_id: int = 0,
        limit: int | None = None,
    ) -> list[InternalEvent]:
        clauses = ["row_id > ?"]
        parameters: list[Any] = [max(0, int(after_row_id))]
        if event_type is not None:
            clauses.append("event_type = ?")
            parameters.append(validate_internal_event_type(event_type))
        sql = f"SELECT * FROM internal_events WHERE {' AND '.join(clauses)} ORDER BY row_id"
        if limit is not None:
            sql += " LIMIT ?"
            parameters.append(max(0, int(limit)))
        with connect_orchestration_db(self.path) as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [_event_from_row(row) for row in rows]

    def import_event(self, event: InternalEvent) -> None:
        """Import one legacy row without producing a new workflow notification."""

        with connect_orchestration_db(self.path) as connection:
            existing = connection.execute(
                "SELECT * FROM internal_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                if _event_from_row(existing) != event:
                    raise ValueError(f"conflicting internal event {event.event_id}")
                return
            connection.execute(
                """
                INSERT INTO internal_events(
                    event_id, event_type, correlation_key, created_at,
                    source_json, idempotency_key, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.event_type,
                    event.correlation_key,
                    datetime_to_storage(event.created_at),
                    json_dumps(event.source.model_dump(mode="json"))
                    if event.source is not None
                    else None,
                    event.idempotency_key,
                    json_dumps(event.payload),
                ),
            )

    def seed_legacy_subscription(
        self,
        spec: InternalEventSubscriptionSpec,
        *,
        config_revision: str | None,
    ) -> None:
        self.reconcile_subscription(
            spec,
            config_revision=config_revision,
            legacy_start=True,
        )
        with connect_orchestration_db(self.path) as connection:
            connection.execute(
                """
                UPDATE internal_event_subscriptions
                SET scan_cursor_row_id = 0, active = 1, config_revision = ?
                WHERE trigger_id = ?
                """,
                (config_revision, spec.trigger_id),
            )

    def tail_row_id(self) -> int:
        with connect_orchestration_db(self.path) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(row_id), 0) AS row_id FROM internal_events"
            ).fetchone()
        return int(row["row_id"] if row is not None else 0)

    def reconcile_subscription(
        self,
        spec: InternalEventSubscriptionSpec,
        *,
        config_revision: str | None,
        legacy_start: bool = False,
    ) -> None:
        now = datetime_to_storage(datetime.now(UTC))
        assert now is not None
        with connect_orchestration_db(self.path) as connection:
            existing = connection.execute(
                "SELECT filter_hash FROM internal_event_subscriptions WHERE trigger_id = ?",
                (spec.trigger_id,),
            ).fetchone()
            if existing is None:
                cursor = 0
                if not legacy_start:
                    tail = connection.execute(
                        "SELECT COALESCE(MAX(row_id), 0) AS row_id FROM internal_events"
                    ).fetchone()
                    cursor = int(tail["row_id"] if tail is not None else 0)
                connection.execute(
                    """
                    INSERT INTO internal_event_subscriptions(
                        trigger_id, trigger_type, event_type, filter_hash, filter_json,
                        scan_cursor_row_id, active, config_revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        spec.trigger_id,
                        spec.trigger_type,
                        spec.event_type,
                        spec.filter_hash,
                        json_dumps(dict(spec.filter_payload)),
                        cursor,
                        config_revision,
                        now,
                        now,
                    ),
                )
                return
            if str(existing["filter_hash"]) != spec.filter_hash:
                tail = connection.execute(
                    "SELECT COALESCE(MAX(row_id), 0) AS row_id FROM internal_events"
                ).fetchone()
                connection.execute(
                    "DELETE FROM internal_event_deliveries WHERE trigger_id = ?",
                    (spec.trigger_id,),
                )
                connection.execute(
                    """
                    UPDATE internal_event_subscriptions
                    SET trigger_type = ?, event_type = ?, filter_hash = ?, filter_json = ?,
                        scan_cursor_row_id = ?, active = 1, config_revision = ?, updated_at = ?
                    WHERE trigger_id = ?
                    """,
                    (
                        spec.trigger_type,
                        spec.event_type,
                        spec.filter_hash,
                        json_dumps(dict(spec.filter_payload)),
                        int(tail["row_id"] if tail is not None else 0),
                        config_revision,
                        now,
                        spec.trigger_id,
                    ),
                )
                return
            connection.execute(
                """
                UPDATE internal_event_subscriptions
                SET active = 1, config_revision = ?, updated_at = ?
                WHERE trigger_id = ?
                """,
                (config_revision, now, spec.trigger_id),
            )

    def deactivate_missing_subscriptions(
        self, active_trigger_ids: set[str], *, config_revision: str | None
    ) -> None:
        now = datetime_to_storage(datetime.now(UTC))
        assert now is not None
        with connect_orchestration_db(self.path) as connection:
            rows = connection.execute(
                "SELECT trigger_id FROM internal_event_subscriptions WHERE active = 1"
            ).fetchall()
            missing = [
                str(row["trigger_id"])
                for row in rows
                if str(row["trigger_id"]) not in active_trigger_ids
            ]
            for trigger_id in missing:
                connection.execute(
                    "DELETE FROM internal_event_deliveries "
                    "WHERE trigger_id = ? AND state = 'pending'",
                    (trigger_id,),
                )
                connection.execute(
                    """
                    UPDATE internal_event_subscriptions
                    SET active = 0, config_revision = ?, updated_at = ?
                    WHERE trigger_id = ?
                    """,
                    (config_revision, now, trigger_id),
                )

    def poll_subscription(
        self,
        spec: InternalEventSubscriptionSpec,
        *,
        matcher: Callable[[InternalEvent], bool],
        limit: int = 200,
        scan_batch_size: int = 1000,
    ) -> list[InternalEvent]:
        normalized_limit = max(1, int(limit))
        with connect_orchestration_db(self.path) as connection:
            pending = _pending_rows(connection, spec.trigger_id, normalized_limit)
            if pending:
                return [_event_from_row(row) for row in pending]
            subscription = connection.execute(
                """
                SELECT scan_cursor_row_id, active, filter_hash
                FROM internal_event_subscriptions
                WHERE trigger_id = ?
                """,
                (spec.trigger_id,),
            ).fetchone()
            if subscription is None or not bool(subscription["active"]):
                return []
            if str(subscription["filter_hash"]) != spec.filter_hash:
                return []
            cursor = int(subscription["scan_cursor_row_id"])
            now = datetime_to_storage(datetime.now(UTC))
            assert now is not None
            batch_size = max(normalized_limit, int(scan_batch_size))
            while True:
                rows = connection.execute(
                    """
                    SELECT * FROM internal_events
                    WHERE event_type = ? AND row_id > ?
                    ORDER BY row_id
                    LIMIT ?
                    """,
                    (spec.event_type, cursor, batch_size),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    event = _event_from_row(row)
                    if matcher(event):
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO internal_event_deliveries(
                                trigger_id, internal_event_row_id, state, created_at, delivered_at
                            ) VALUES (?, ?, 'pending', ?, NULL)
                            """,
                            (spec.trigger_id, int(row["row_id"]), now),
                        )
                cursor = int(rows[-1]["row_id"])
                connection.execute(
                    """
                    UPDATE internal_event_subscriptions
                    SET scan_cursor_row_id = ?, updated_at = ?
                    WHERE trigger_id = ?
                    """,
                    (cursor, now, spec.trigger_id),
                )
                pending = _pending_rows(connection, spec.trigger_id, normalized_limit)
                if pending or len(rows) < batch_size:
                    return [_event_from_row(row) for row in pending]
            return []

    def acknowledge_delivery(self, trigger_id: str, internal_event_id: str) -> None:
        delivered_at = datetime_to_storage(datetime.now(UTC))
        with connect_orchestration_db(self.path) as connection:
            connection.execute(
                """
                UPDATE internal_event_deliveries
                SET state = 'delivered', delivered_at = ?
                WHERE trigger_id = ?
                  AND internal_event_row_id = (
                      SELECT row_id FROM internal_events WHERE event_id = ?
                  )
                """,
                (delivered_at, trigger_id, internal_event_id),
            )

    def seed_delivered_event(self, trigger_id: str, internal_event_id: str) -> None:
        now = datetime_to_storage(datetime.now(UTC))
        with connect_orchestration_db(self.path) as connection:
            row = connection.execute(
                "SELECT row_id FROM internal_events WHERE event_id = ?",
                (internal_event_id,),
            ).fetchone()
            if row is None:
                raise KeyError(internal_event_id)
            connection.execute(
                """
                INSERT OR IGNORE INTO internal_event_deliveries(
                    trigger_id, internal_event_row_id, state, created_at, delivered_at
                ) VALUES (?, ?, 'delivered', ?, ?)
                """,
                (trigger_id, int(row["row_id"]), now, now),
            )


def _pending_rows(connection: sqlite3.Connection, trigger_id: str, limit: int) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT event.*
        FROM internal_event_deliveries AS delivery
        JOIN internal_events AS event ON event.row_id = delivery.internal_event_row_id
        WHERE delivery.trigger_id = ? AND delivery.state = 'pending'
        ORDER BY event.row_id
        LIMIT ?
        """,
        (trigger_id, limit),
    ).fetchall()


def _event_from_row(row: sqlite3.Row) -> InternalEvent:
    source_payload = json_loads_object(row["source_json"])
    return InternalEvent(
        event_id=str(row["event_id"]),
        event_type=str(row["event_type"]),
        payload=json_loads_object(row["payload_json"]),
        correlation_key=_normalize_optional_string(row["correlation_key"]),
        created_at=_datetime_from_storage(row["created_at"]),
        source=InternalEventSource.model_validate(source_payload) if source_payload else None,
        idempotency_key=_normalize_optional_string(row["idempotency_key"]),
    )


def _event_id(
    *,
    event_type: str,
    payload: dict[str, Any],
    correlation_key: str | None,
    idempotency_key: str | None,
) -> str:
    if idempotency_key is None:
        return f"evt-{uuid4()}"
    material = json.dumps(
        {
            "event_type": event_type,
            "payload": payload,
            "correlation_key": correlation_key,
            "idempotency_key": idempotency_key,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"evt-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:32]}"


def _datetime_from_storage(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _normalize_optional_string(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
