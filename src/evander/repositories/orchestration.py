from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
import time
from typing import Any, Generic, Protocol, TypeVar
from uuid import uuid4

from pydantic import BaseModel

from evander.repositories.sqlite_runtime import storage_lock
from evander.repositories.operational_payloads import (
    FORMAT_KEY, discard_record_field_references, load_lifecycle_field,
    load_record, load_record_projection, prepare_record, store_prepared_record,
)
from evander.repositories.execution_history import prepare_execution_history_payloads
from evander.repositories.orchestration_db import (
    connect_orchestration_db,
    connect_orchestration_db_read_only,
    datetime_to_storage,
    ensure_orchestration_schema,
    json_dumps,
    json_loads_object,
)
from evander.storage import write_json_atomically

logger = logging.getLogger(__name__)

TRecord = TypeVar("TRecord", bound=BaseModel)
R = TypeVar("R")

_PROMPT_RECEIPT_FIELDS = {
    **dict.fromkeys(("execution_id", "effect_type", "status", "started_at", "finished_at", "outputs")),
    "resolved_config": ("session_id", "resume_thread_id"),
    "input_payload": ("session_id", "resume_thread_id"),
}


@dataclass(frozen=True)
class PromptActivityRecord:
    execution_id: str
    idempotency_key: str
    effect_type: str
    status: str
    started_at: datetime
    completion_pending_status: str | None
    outputs: dict[str, Any]
    resolved_config: dict[str, Any]
    input_payload: dict[str, Any]

    @classmethod
    def read(cls, connection, payload):
        target_fields = ("session_id", "vm_name", "target")
        data = load_record_projection(connection, payload, {
            **dict.fromkeys(("execution_id", "idempotency_key", "effect_type", "status",
                             "started_at", "completion_pending_status")),
            **dict.fromkeys(("outputs", "resolved_config", "input_payload"), target_fields),
        })
        data["started_at"] = datetime.fromisoformat(data["started_at"])
        data.setdefault("completion_pending_status", None)
        for field in ("outputs", "resolved_config", "input_payload"):
            data.setdefault(field, {})
        return cls(**data)


@dataclass(frozen=True)
class PromptLifecycleReceipt:
    execution_id: str
    effect_type: str
    status: str
    started_at: datetime
    finished_at: datetime
    outputs: dict[str, Any]
    resolved_config: dict[str, Any]
    input_payload: dict[str, Any]

    @classmethod
    def read(cls, connection, payload):
        data = load_record_projection(connection, payload, _PROMPT_RECEIPT_FIELDS)
        for field in ("started_at", "finished_at"):
            data[field] = datetime.fromisoformat(data[field])
        for field in ("outputs", "resolved_config", "input_payload"):
            data.setdefault(field, {})
        return cls(**data)


def read_latest_agent_prompt_execution(
    path: Path, *, session_id: str, dispatch_id: str
) -> dict[str, Any] | None:
    return _read_latest_prompt_execution(path, session_id=session_id, dispatch_id=dispatch_id,
                                         lifecycle_only=False)


def read_latest_agent_prompt_lifecycle_receipt(
    path: Path, *, session_id: str, dispatch_id: str, include_direct_prompts: bool = False,
) -> dict[str, Any] | None:
    return _read_latest_prompt_execution(
        path, session_id=session_id, dispatch_id=dispatch_id, lifecycle_only=True,
        include_direct_prompts=include_direct_prompts,
    )


def _read_latest_prompt_execution(
    path: Path, *, session_id: str, dispatch_id: str, lifecycle_only: bool,
    include_direct_prompts: bool = False,
) -> dict[str, Any] | None:
    """Read a dispatch receipt using indexed metadata, with legacy inline fallback."""
    if not path.is_file():
        return None
    types = (
        ("effect:agent:enqueue-prompt", "effect:agent:prompt")
        if include_direct_prompts else ("effect:agent:enqueue-prompt",)
    )
    placeholders = ",".join("?" for _ in types)
    with connect_orchestration_db_read_only(path) as connection:
        connection.execute("BEGIN")
        row = connection.execute(
            f"""SELECT payload_json FROM (
                SELECT e.payload_json, e.execution_id,
                       COALESCE(p.native_thread_observed_at,e.finished_at,e.started_at) AS observed_at
                FROM workflow_execution_projections p INDEXED BY idx_execution_projection_dispatch
                JOIN effect_executions e ON e.execution_id=p.record_id
                WHERE p.dispatch_id=? AND p.session_id=? AND p.record_kind='effect' AND p.queued!=1
                  AND e.effect_type IN ({placeholders})
                  AND e.status IN ('running','waiting','completion_pending','succeeded','failed')
                UNION ALL
                SELECT e.payload_json, e.execution_id,
                       COALESCE(json_extract(e.payload_json,'$.outputs.native_thread_observed_at'),
                                e.finished_at,e.started_at) AS observed_at
                FROM effect_executions e
                WHERE e.effect_type IN ({placeholders})
                  AND json_extract(e.payload_json,'$.outputs.dispatch_id')=?
                  AND e.status IN ('running','waiting','completion_pending','succeeded','failed')
                  AND COALESCE(NULLIF(json_extract(e.payload_json,'$.outputs.session_id'),''),
                       NULLIF(json_extract(e.payload_json,'$.outputs.scope.session_id'),''),
                       json_extract(e.payload_json,'$.resolved_config.session_id'))=?
                  AND COALESCE(json_extract(e.payload_json,'$.outputs.queued'),0)!=1
                  AND NOT EXISTS(SELECT 1 FROM workflow_execution_projections p
                                 WHERE p.record_kind='effect' AND p.record_id=e.execution_id)
            ) ORDER BY observed_at DESC,execution_id DESC LIMIT 1""",
            (dispatch_id, session_id, *types, *types, dispatch_id, session_id),
        ).fetchone()
        if row is None:
            return None
        return (load_record_projection(connection, row["payload_json"], _PROMPT_RECEIPT_FIELDS)
                if lifecycle_only else load_record(connection, row["payload_json"]))


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


@dataclass(frozen=True)
class TriggerPollCheckpoint:
    trigger_id: str
    trigger_type: str
    discovered: int
    event_ids: list[str]
    polled_at: datetime


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

    def record_trigger_polls(self, polls: list[TriggerPollCheckpoint]) -> None: ...

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
        with storage_lock(self._lock):
            return self._read_payload()

    def save(self, payload: dict[str, Any]) -> None:
        with storage_lock(self._lock):
            self._write_payload(payload)

    def update(self, updater: Any) -> dict[str, Any]:
        with storage_lock(self._lock):
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
        write_json_atomically(self.path, payload)


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
        self.record_trigger_polls([
            TriggerPollCheckpoint(
                trigger_id=trigger_id,
                trigger_type=trigger_type,
                discovered=discovered,
                event_ids=event_ids[-20:],
                polled_at=polled_at or datetime.now(UTC),
            )
        ])

    def record_trigger_polls(self, polls: list[TriggerPollCheckpoint]) -> None:
        if not polls:
            return

        def _update(payload: dict[str, Any]) -> None:
            triggers_payload = payload.setdefault("triggers", {})
            if not isinstance(triggers_payload, dict):
                payload["triggers"] = {}
                triggers_payload = payload["triggers"]
            for poll in polls:
                trigger_payload: dict[str, Any] = {
                    "trigger_type": poll.trigger_type,
                    "last_polled_at": poll.polled_at.isoformat().replace("+00:00", "Z"),
                    "last_discovered_count": poll.discovered,
                    "last_event_ids": poll.event_ids[-20:],
                }
                if poll.event_ids:
                    trigger_payload["last_event_id"] = poll.event_ids[-1]
                triggers_payload[poll.trigger_id] = trigger_payload

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


class SqliteWorkflowConfigSnapshotRepository:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        ensure_orchestration_schema(self.path)

    def get_snapshot(self, revision: str) -> dict[str, Any] | None:
        normalized_revision = str(revision or "").strip()
        if not normalized_revision:
            return None
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
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
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
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
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
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
        compact_history_retention_days: int | None = None,
    ) -> None:
        if retention_days is not None and compact_history_retention_days is None:
            raise ValueError("compact_history_retention_days is required when retention_days is configured")
        self.path = Path(path)
        self.model = model
        self.table_name = table_name
        self.node_id_field = node_id_field
        self.node_type_field = node_type_field
        self._journal = journal
        self._journal_source = source
        self._lock = RLock()
        ensure_orchestration_schema(self.path)

    def read_all(self) -> dict[str, TRecord]:
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                f"SELECT payload_json FROM {self.table_name} ORDER BY started_at ASC, execution_id ASC"
            ).fetchall()
            records = [self._record_from_row(row, connection) for row in rows]
        return {str(getattr(record, "execution_id")): record for record in records}

    def get_execution(self, execution_id: str) -> TRecord | None:
        normalized_execution_id = str(execution_id or "").strip()
        if not normalized_execution_id:
            return None
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                f"SELECT payload_json FROM {self.table_name} WHERE execution_id = ?",
                (normalized_execution_id,),
            ).fetchone()
            return self._record_from_row(row, connection) if row is not None else None

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
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
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
            return [self._record_from_row(row, connection) for row in rows]

    def list_prompt_records_for_dispatches(self, dispatch_ids: set[str]) -> list[TRecord]:
        return self._prompt_records_for_dispatches(dispatch_ids, lifecycle_only=False)

    def list_prompt_lifecycle_receipts(self, dispatch_ids: set[str]) -> list[PromptLifecycleReceipt]:
        """Internal receipt projection; no prompt/configuration payload hydration."""
        return self._prompt_records_for_dispatches(dispatch_ids, lifecycle_only=True)

    def list_agent_prompt_activity_records(self) -> list[PromptActivityRecord]:
        """Read operational prompt owners without history or shared writer locks."""
        if self.table_name != "effect_executions":
            return []
        with connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                """SELECT payload_json FROM effect_executions
                   INDEXED BY idx_effect_executions_status_type
                   WHERE status IN ('running', 'waiting', 'completion_pending')
                     AND effect_type='effect:agent:enqueue-prompt'
                     AND dismissed_at IS NULL"""
            ).fetchall()
            return [PromptActivityRecord.read(connection, row["payload_json"]) for row in rows]

    def _prompt_records_for_dispatches(
        self, dispatch_ids: set[str], *, lifecycle_only: bool,
    ) -> list[Any]:
        """Read all receipts for a bounded set of immutable dispatches.

        Do not LIMIT receipts: a newer missing-thread receipt must supersede an
        older success even when history/recovery is split across pages.
        """
        ids = sorted({str(value).strip() for value in dispatch_ids if str(value).strip()})
        if not ids:
            return []
        if len(ids) > 50:
            raise ValueError("prompt receipt lookup is limited to 50 dispatch IDs")
        if self.table_name != "effect_executions":
            return []
        placeholders = ",".join("?" for _ in ids)
        # Maintenance runs concurrently with foreground dispatch. A separate
        # SQLite snapshot must not hold the execution repository's writer lock.
        with connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                f"""SELECT payload_json FROM (
                    SELECT e.payload_json,e.execution_id,COALESCE(e.finished_at,e.started_at) AS observed_at
                    FROM workflow_execution_projections p INDEXED BY idx_execution_projection_dispatch
                    JOIN effect_executions e ON e.execution_id=p.record_id
                    WHERE p.dispatch_id IN ({placeholders}) AND p.record_kind='effect'
                      AND e.effect_type='effect:agent:enqueue-prompt'
                      AND e.status IN ('waiting','succeeded','failed')
                    UNION ALL
                    SELECT e.payload_json,e.execution_id,COALESCE(e.finished_at,e.started_at) AS observed_at
                    FROM effect_executions e
                    WHERE e.effect_type='effect:agent:enqueue-prompt'
                      AND json_extract(e.payload_json,'$.outputs.dispatch_id') IN ({placeholders})
                      AND e.status IN ('waiting','succeeded','failed')
                      AND NOT EXISTS(SELECT 1 FROM workflow_execution_projections p
                                     WHERE p.record_kind='effect' AND p.record_id=e.execution_id)
                    ) ORDER BY observed_at,execution_id""",
                [*ids, *ids],
            ).fetchall()
            if not lifecycle_only:
                return [self._record_from_row(row, connection) for row in rows]
            # Outputs carry durable receipts and complete missing-thread error
            # evidence. Prompt inputs/config need only these two scalar fields.
            return [PromptLifecycleReceipt.read(connection, row["payload_json"]) for row in rows]

    def list_waiting_records_page(
        self, *, node_type: str, after: str = "", limit: int = 50
    ) -> list[TRecord]:
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                f"""SELECT payload_json FROM {self.table_name}
                    WHERE status = 'waiting' AND {self.node_type_field} = ? AND execution_id > ?
                    ORDER BY execution_id LIMIT ?""",
                (node_type, after, max(1, min(50, limit))),
            ).fetchall()
            return [self._record_from_row(row, connection) for row in rows]

    def list_waiting_records(self, node_type: str | None = None) -> list[TRecord]:
        params: list[Any] = []
        where = ["status = 'waiting'"]
        normalized_type = str(node_type or "").strip()
        if normalized_type:
            where.append(f"{self.node_type_field} = ?")
            params.append(normalized_type)
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE {" AND ".join(where)}
                ORDER BY COALESCE(finished_at, started_at) DESC, execution_id DESC
                """,
                params,
            ).fetchall()
            return [self._record_from_row(row, connection) for row in rows]

    def list_completion_pending_records(
        self, *, limit: int = 32, after: tuple[str, str] | None = None
    ) -> list[TRecord]:
        parameters: list[Any] = []
        cursor_clause = ""
        if after is not None:
            cursor_clause = "AND (finished_at, execution_id) > (?, ?)"
            parameters.extend(after)
        parameters.append(max(1, min(100, limit)))
        # finished_at is NOT NULL. Match the status index's ordering so even
        # analyzed databases seek a bounded page instead of scanning history.
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                f"""SELECT payload_json FROM {self.table_name}
                    INDEXED BY idx_{self.table_name}_status
                    WHERE status = 'completion_pending' {cursor_clause}
                    ORDER BY finished_at, execution_id LIMIT ?""",
                parameters,
            ).fetchall()
            return [self._record_from_row(row, connection) for row in rows]

    def pending_completion_ids(self, execution_ids: list[str]) -> set[str]:
        """Check a bounded set of retry owners without hydrating their payloads."""
        if not execution_ids:
            return set()
        if len(execution_ids) > 100:
            raise ValueError("At most 100 completion IDs can be checked at once")
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            return {row[0] for row in connection.execute(
                f"SELECT execution_id FROM {self.table_name} WHERE status='completion_pending' "
                "AND execution_id IN (" + ",".join("?" for _ in execution_ids) + ")",
                execution_ids,
            )}

    def record_execution(self, record: TRecord) -> None:
        self.put(record)

    def update_lifecycle(self, execution_id: str, **fields: Any) -> bool:
        """Update small lifecycle fields without hydrating immutable payloads."""
        allowed = {"status", "finished_at", "cleared_at", "dismissed_at", "error",
                   "published_event_id", "dismiss_reason", "clear_reason", "completion_status"}
        if set(fields) - allowed:
            raise ValueError("update_lifecycle only accepts lifecycle metadata")
        values = {key: datetime_to_storage(value) if isinstance(value, datetime) else value
                  for key, value in fields.items()}
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self._update_lifecycle_fields(connection, execution_id, values)

    def _update_lifecycle_fields(self, connection: Any, execution_id: str, values: dict[str, Any]) -> bool:
        if not values:
            return connection.execute(f"SELECT 1 FROM {self.table_name} WHERE execution_id=?",
                                      (execution_id,)).fetchone() is not None
        columns = {key: value for key, value in values.items()
                   if key in {"status", "finished_at", "cleared_at", "dismissed_at"}}
        json_params = [value for key, value in values.items() for value in (f"$.{key}", value)]
        assignments = [*(f"{key}=?" for key in columns),
                       "payload_json=json_set(payload_json," + ",".join("?,?" for _ in values) + ")"]
        cursor = connection.execute(f"UPDATE {self.table_name} SET " + ",".join(assignments)
            + " WHERE execution_id=?", [*columns.values(), *json_params, execution_id])
        if not cursor.rowcount:
            return False
        kind = "gate" if self.table_name == "gate_executions" else "effect"
        discard_record_field_references(
            connection, kind=kind, record_id=execution_id, fields=values,
        )
        history_fields = {key: value for key, value in values.items()
                          if key in {"status", "finished_at", "error", "published_event_id"}}
        if history_fields:
            history_fields["updated_at"] = datetime_to_storage(datetime.now(UTC))
            connection.execute("UPDATE workflow_node_execution_history SET "
                + ",".join(f"{key}=?" for key in history_fields) + " WHERE execution_key=?",
                [*history_fields.values(), f"{kind}:{execution_id}"])
        return True

    def put(self, record: TRecord) -> None:
        started = time.perf_counter()
        prepared = self._prepare_record(record)
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
            phases = self._upsert_record(connection, record, prepared=prepared)
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
        self._log_write_duration(record, "record", started, phases)

    def _log_write_duration(
        self,
        record: TRecord,
        operation: str,
        started: float,
        phases: dict[str, float] | None,
    ) -> None:
        if not logger.isEnabledFor(logging.DEBUG):
            return
        duration_ms = (time.perf_counter() - started) * 1000
        logger.debug(
            "workflow_execution_storage operation=%s execution_id=%s trace_id=%s "
            "node_id=%s status=%s duration_ms=%.3f phases_ms=%s",
            operation,
            getattr(record, "execution_id", ""),
            getattr(record, "trace_id", ""),
            getattr(record, self.node_id_field, ""),
            getattr(record, "status", ""),
            duration_ms,
            {key: round(value, 3) for key, value in (phases or {}).items()},
        )

    def find_by_idempotency_key(self, idempotency_key: str) -> TRecord | None:
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return None
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            row = connection.execute(
                f"SELECT payload_json FROM {self.table_name} WHERE idempotency_key = ?",
                (normalized_key,),
            ).fetchone()
            return self._record_from_row(row, connection) if row is not None else None

    def find_execution_by_idempotency_key(self, idempotency_key: str) -> TRecord | None:
        return self.find_by_idempotency_key(idempotency_key)

    def has_running_execution_for_event(self, event_id: str) -> bool:
        normalized_event_id = str(event_id or "").strip()
        if not normalized_event_id:
            return False
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
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
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE status IN (
                    'running', 'waiting', 'failed', 'completion_pending', 'error_routed'
                )
                  AND dismissed_at IS NULL
                ORDER BY COALESCE(finished_at, started_at) DESC, execution_id DESC
                """
            ).fetchall()
            return [self._record_from_row(row, connection) for row in rows]

    def list_active_summaries(self) -> list[dict[str, Any]]:
        """Read display metadata without hydrating execution inputs and outputs."""
        return self._list_run_summaries(
            where="status IN ('running', 'waiting', 'failed', 'completion_pending', 'error_routed')"
            " AND dismissed_at IS NULL",
            parameters=(),
            newest_first=True,
        )

    def list_activity_summaries_between(
        self, *, start_at: datetime, end_at: datetime
    ) -> list[dict[str, Any]]:
        return self._list_run_summaries(
            where="COALESCE(finished_at, started_at) BETWEEN ? AND ?",
            parameters=(datetime_to_storage(start_at), datetime_to_storage(end_at)),
            newest_first=False,
        )

    def _list_run_summaries(
        self, *, where: str, parameters: tuple[Any, ...], newest_first: bool
    ) -> list[dict[str, Any]]:
        # These projections are only for display. Control operations must still
        # read full records, including the actual input required to resume a run.
        # Missing legacy input_payload fields default to {} in the record models.
        # Keep extra activity timestamps too: the renderer considers all of them.
        order = "DESC" if newest_first else "ASC"
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                f"""
                SELECT execution_id, event_id, trace_id, source_node_id, source_trigger_type,
                       {self.node_id_field}, status, started_at, finished_at, dismissed_at,
                       json_extract(payload_json, '$.error') AS error,
                       json_extract(payload_json, '$.{FORMAT_KEY}.fields.error') AS encoded_error,
                       COALESCE(json_type(payload_json, '$.input_payload'), 'object') = 'object'
                           AS has_input_payload,
                       json_extract(payload_json, '$.occurred_at') AS occurred_at,
                       json_extract(payload_json, '$.inflight_at') AS inflight_at,
                       json_extract(payload_json, '$.delivered_at') AS delivered_at
                FROM {self.table_name}
                WHERE {where}
                ORDER BY COALESCE(finished_at, started_at) {order}, execution_id {order}
                """,
                parameters,
            ).fetchall()
            summaries = [dict(row) for row in rows]
            for summary in summaries:
                summary["error"] = load_lifecycle_field(
                    connection, summary["error"], summary.pop("encoded_error"),
                )
            return summaries

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
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE {" AND ".join(where)}
                ORDER BY COALESCE(finished_at, started_at) DESC, execution_id DESC
                """,
                params,
            ).fetchall()
            return [self._record_from_row(row, connection) for row in rows]

    def list_activity_between(self, *, start_at: datetime, end_at: datetime) -> list[TRecord]:
        start_text = datetime_to_storage(start_at)
        end_text = datetime_to_storage(end_at)
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
            rows = connection.execute(
                f"""
                SELECT payload_json
                FROM {self.table_name}
                WHERE COALESCE(finished_at, started_at) BETWEEN ? AND ?
                ORDER BY COALESCE(finished_at, started_at) ASC, execution_id ASC
                """,
                (start_text, end_text),
            ).fetchall()
            return [self._record_from_row(row, connection) for row in rows]

    def clear_event_records(
        self, event_id: str, *, cleared_at: datetime, clear_reason: str, clear_error: str,
    ) -> int:
        event_id = str(event_id or "").strip()
        if not event_id:
            return 0
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(f"SELECT execution_id,status FROM {self.table_name} WHERE event_id=? "
                "AND status IN ('failed','running','waiting','completion_pending')", (event_id,)).fetchall()
            timestamp = datetime_to_storage(cleared_at)
            for row in rows:
                fields = {"dismissed_at": timestamp, "dismiss_reason": "manual_dismiss_errored_workflow_run"}
                if row["status"] != "failed":
                    fields.update(status="failed", finished_at=timestamp, cleared_at=timestamp,
                                  clear_reason=clear_reason, error=clear_error,
                                  dismiss_reason="manual_clear_stale_workflow_runs")
                self._update_lifecycle_fields(connection, row["execution_id"], fields)
            return len(rows)

    def dismiss_failed_records_for_node(
        self, node_id: str, *, dismissed_at: datetime, dismiss_reason: str,
    ) -> int:
        node_id = str(node_id or "").strip()
        if not node_id:
            return 0
        with storage_lock(self._lock), connect_orchestration_db(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(f"SELECT execution_id FROM {self.table_name} "
                f"WHERE {self.node_id_field}=? AND status='failed' AND dismissed_at IS NULL", (node_id,)).fetchall()
            for row in rows:
                self._update_lifecycle_fields(connection, row["execution_id"],
                    {"dismissed_at": datetime_to_storage(dismissed_at), "dismiss_reason": dismiss_reason})
            return len(rows)

    def update_all(self, updater: Callable[[dict[str, TRecord]], R]) -> R:
        with storage_lock(self._lock):
            records = self.read_all()
            result = updater(records)
            prepared_records = [(validated, self._prepare_record(validated))
                                for record in records.values()
                                for validated in [self.model.model_validate(record)]]
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
                for record, prepared in prepared_records:
                    self._upsert_record(connection, record, prepared=prepared)
        return result

    def _record_from_row(self, row: Any, connection: Any) -> TRecord:
        return self.model.model_validate(load_record(connection, row["payload_json"]))

    def _prepare_record(self, record: TRecord) -> tuple[Any, ...]:
        payload = record.model_dump(mode="json")
        kind = "gate" if self.table_name == "gate_executions" else "effect"
        return (payload, prepare_record(kind=kind, record=payload),
                prepare_execution_history_payloads(kind=kind, record=payload))

    def _record_columns(self, connection: Any, record: TRecord, prepared: tuple[Any, ...]) -> tuple[Any, ...]:
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
            store_prepared_record(connection, prepared[1]),
        )

    def _upsert_record(self, connection: Any, record: TRecord, *, prepared: tuple[Any, ...] | None = None) -> dict[str, float]:
        prepared = prepared or self._prepare_record(record)
        started = time.perf_counter()
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
            self._record_columns(connection, record, prepared),
        )
        record_written = time.perf_counter()
        from evander.repositories.execution_history import archive_execution_record

        archive_execution_record(
            connection,
            kind="gate" if self.table_name == "gate_executions" else "effect",
            record=prepared[0],
            prepared_payloads=prepared[2],
        )
        return {
            "record_write": (record_written - started) * 1000,
            "history": (time.perf_counter() - record_written) * 1000,
        }

    def record_completion_and_publish(
        self,
        *,
        record: TRecord,
        event_queue: Any,
        event: Any,
        idempotency_key: str,
    ) -> str:
        """Commit a terminal execution and its durable successor in one transaction."""

        from evander.triggers.queue import TriggerQueueRecord

        queue_path = Path(getattr(event_queue, "path", ""))
        if queue_path != self.path:
            raise ValueError("execution and trigger event stores must share one database")
        queue_lock = getattr(event_queue, "_lock", None)
        fetch_by_key = getattr(event_queue, "_fetch_record_by_idempotency_key", None)
        fetch_by_id = getattr(event_queue, "_fetch_record", None)
        insert_record = getattr(event_queue, "_insert_record", None)
        if queue_lock is None or not all(
            callable(item) for item in (fetch_by_key, fetch_by_id, insert_record)
        ):
            raise TypeError("trigger event queue does not support atomic completion publication")

        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            raise ValueError("completion idempotency_key is required")
        event_id = str(getattr(event, "event_id", "") or "").strip()
        if not event_id:
            raise ValueError("completion event_id is required")

        started = time.perf_counter()
        created_event = False
        queued = TriggerQueueRecord(
            event_id=event_id,
            trigger_id=str(event.trigger_id),
            trigger_type=str(event.trigger_type),
            trace_id=str(event.trace_id),
            occurred_at=event.occurred_at,
            payload=dict(event.payload),
            delivery_state="pending",
            idempotency_key=normalized_key,
        )
        prepared_event = event_queue._prepare_record(queued)
        prepared_execution = self._prepare_record(record)
        with storage_lock(self._lock), storage_lock(queue_lock), connect_orchestration_db(self.path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = fetch_by_key(connection, normalized_key)
            if existing is None:
                existing = fetch_by_id(connection, event_id)
            if existing is None:
                insert_record(connection, queued, prepared=prepared_event)
                created_event = True
            elif existing.event_id != event_id:
                raise ValueError("completion idempotency key belongs to another event")

            phases = self._upsert_record(connection, record, prepared=prepared_execution)
            now = datetime.now(UTC)
            if created_event and getattr(event_queue, "_journal", None) is not None:
                self._append_journal_row(
                    connection,
                    event_id=str(uuid4()),
                    event_type="workflow.trigger_event.published",
                    source="trigger_queue",
                    subject_id=event_id,
                    occurred_at=now,
                    payload={
                        "trigger_id": str(event.trigger_id),
                        "trigger_type": str(event.trigger_type),
                    },
                )
            if self._journal is not None and self._journal_source is not None:
                self._append_journal_row(
                    connection,
                    event_id=f"execution-completion:{self.table_name}:{getattr(record, 'execution_id')}:{event_id}",
                    event_type="workflow.execution.updated",
                    source=self._journal_source,
                    subject_id=str(getattr(record, "execution_id")),
                    occurred_at=now,
                    payload={
                        "event_id": str(getattr(record, "event_id", "")),
                        "status": str(getattr(record, "status", "")),
                    },
                )
        self._log_write_duration(record, "completion", started, phases)
        return event_id

    @staticmethod
    def _append_journal_row(
        connection: Any,
        *,
        event_id: str,
        event_type: str,
        source: str,
        subject_id: str | None,
        occurred_at: datetime,
        payload: dict[str, Any],
    ) -> None:
        journal_payload = {
            "event_id": event_id,
            "event_type": event_type,
            "occurred_at": occurred_at.isoformat(),
            "source": source,
            "subject_id": subject_id,
            "payload": payload,
        }
        connection.execute(
            """INSERT OR IGNORE INTO workflow_events(
                   event_id, event_type, source, subject_id, occurred_at, payload_json
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                event_type,
                source,
                subject_id,
                datetime_to_storage(occurred_at),
                json_dumps(journal_payload),
            ),
        )



class SqliteGateExecutionRepository(SqliteExecutionRepository[TRecord]):
    def find_latest_by_gate_id(self, gate_id: str) -> TRecord | None:
        return self.find_latest_execution_for_gate(gate_id)

    def find_latest_execution_for_gate(self, gate_id: str) -> TRecord | None:
        normalized_gate_id = str(gate_id or "").strip()
        if not normalized_gate_id:
            return None
        with storage_lock(self._lock), connect_orchestration_db_read_only(self.path) as connection:
            connection.execute("BEGIN")
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
            return self._record_from_row(row, connection) if row is not None else None
