from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Callable, TypeVar
from uuid import uuid4

from evander.addons import addon_storage_path
from evander.models import utc_now
from evander.repositories.orchestration_db import SQLITE_BUSY_TIMEOUT_MS

from .models import (
    SCHEMA_VERSION,
    TeamDispatchActivity,
    TeamIdempotencyRecord,
    TeamOutboxRecord,
    TeamOutboxStatus,
    TeamState,
)

ADDON_ID = "team-addon"
STATE_DB_FILE = "state.db"
LEGACY_STATE_FILE = "state.json"
SQLITE_SCHEMA_VERSION = 42
_SQLITE_WAL_ENABLE_TIMEOUT_SECONDS = 5.0

T = TypeVar("T")


class TeamIdempotencyConflictError(RuntimeError):
    def __init__(
        self,
        *,
        tool_name: str,
        caller_identity: str,
        scope_kind: str,
        scope_id: str,
        request_id: str,
        existing_digest: str,
        attempted_digest: str,
    ) -> None:
        super().__init__(
            "idempotency key reused with a different normalized payload digest: "
            f"tool={tool_name!r}, caller={caller_identity!r}, "
            f"scope={scope_kind!r}:{scope_id!r}, request_id={request_id!r}"
        )
        self.tool_name = tool_name
        self.caller_identity = caller_identity
        self.scope_kind = scope_kind
        self.scope_id = scope_id
        self.request_id = request_id
        self.existing_digest = existing_digest
        self.attempted_digest = attempted_digest


def normalized_payload_digest(payload: Mapping[str, Any]) -> str:
    normalized = _json_normalize(dict(payload))
    return hashlib.sha256(_json_dumps(normalized).encode("utf-8")).hexdigest()


class TeamStateSqliteStore:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.path = addon_storage_path(self.runtime_dir, ADDON_ID, STATE_DB_FILE)
        self.legacy_json_path = addon_storage_path(self.runtime_dir, ADDON_ID, LEGACY_STATE_FILE)
        self._lock = RLock()
        self.ensure_schema()
        self._import_legacy_json_or_initialize()

    def read_object(self) -> dict[str, Any]:
        with self._lock:
            with self._connect() as connection:
                return self._read_payload(connection)

    def write_object(self, payload: dict[str, Any]) -> None:
        state = TeamState.model_validate(payload)
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._write_state(connection, state)

    def update_object(
        self,
        updater: Callable[[dict[str, Any]], T | None],
    ) -> T | None:
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                payload = self._read_payload(connection)
                result = updater(payload)
                state = TeamState.model_validate(payload)
                self._write_state(connection, state)
                return result

    def update_object_with_command_transaction(
        self,
        updater: Callable[[dict[str, Any], TeamStateSqliteCommandTransaction], T],
    ) -> T:
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                payload = self._read_payload(connection)
                result = updater(
                    payload,
                    TeamStateSqliteCommandTransaction(self, connection),
                )
                state = TeamState.model_validate(payload)
                self._write_state(connection, state)
                return result

    def append_dispatch_activity(
        self,
        activity: TeamDispatchActivity | Mapping[str, Any],
    ) -> TeamDispatchActivity:
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                return self._insert_dispatch_activity(connection, activity)

    def list_dispatch_activity(
        self,
        *,
        dispatch_id: str | None = None,
        caller_session_id: str | None = None,
        caller_identity: str | None = None,
        agent_id: str | None = None,
        governance_group_id: str | None = None,
        assignment_id: str | None = None,
        tool_name: str | None = None,
        request_id: str | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[TeamDispatchActivity]:
        with self._lock:
            with self._connect() as connection:
                return self._list_dispatch_activity(
                    connection,
                    dispatch_id=dispatch_id,
                    caller_session_id=caller_session_id,
                    caller_identity=caller_identity,
                    agent_id=agent_id,
                    governance_group_id=governance_group_id,
                    assignment_id=assignment_id,
                    tool_name=tool_name,
                    request_id=request_id,
                    limit=limit,
                    newest_first=newest_first,
                )

    @classmethod
    def _list_dispatch_activity(
        cls,
        connection: sqlite3.Connection,
        *,
        dispatch_id: str | None = None,
        caller_session_id: str | None = None,
        caller_identity: str | None = None,
        agent_id: str | None = None,
        governance_group_id: str | None = None,
        assignment_id: str | None = None,
        tool_name: str | None = None,
        request_id: str | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[TeamDispatchActivity]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("dispatch_id", dispatch_id),
            ("caller_session_id", caller_session_id),
            ("caller_identity", caller_identity),
            ("agent_id", agent_id),
            ("governance_group_id", governance_group_id),
            ("assignment_id", assignment_id),
            ("tool_name", tool_name),
            ("request_id", request_id),
        ):
            if value is None:
                continue
            clauses.append(f"{column} = ?")
            params.append(_required_text(value, column))
        query = "SELECT * FROM team_dispatch_activity"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += (
            " ORDER BY created_at DESC, id DESC" if newest_first else " ORDER BY created_at, id"
        )
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(0, int(limit)))
        return [cls._dispatch_activity_from_row(row) for row in connection.execute(query, params)]

    def check_idempotency_replay(
        self,
        *,
        tool_name: str,
        caller_identity: str,
        scope_kind: str,
        scope_id: str,
        request_id: str,
        normalized_payload_digest: str,
    ) -> TeamIdempotencyRecord | None:
        key = _idempotency_key_values(
            tool_name=tool_name,
            caller_identity=caller_identity,
            scope_kind=scope_kind,
            scope_id=scope_id,
            request_id=request_id,
        )
        digest = _required_text(
            normalized_payload_digest,
            "normalized_payload_digest",
        )
        with self._lock:
            with self._connect() as connection:
                record = self._lookup_idempotency_record(connection, **key)
        if record is None:
            return None
        if record.normalized_payload_digest != digest:
            _raise_idempotency_conflict(
                **key,
                existing_digest=record.normalized_payload_digest,
                attempted_digest=digest,
            )
        return record

    def store_idempotency_record(
        self,
        record: TeamIdempotencyRecord | Mapping[str, Any],
    ) -> TeamIdempotencyRecord:
        stored = TeamIdempotencyRecord.model_validate(record)
        key = _idempotency_key_values(
            tool_name=stored.tool_name,
            caller_identity=stored.caller_identity,
            scope_kind=stored.scope_kind,
            scope_id=stored.scope_id,
            request_id=stored.request_id,
        )
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                return self._insert_idempotency_record(connection, stored, key=key)

    def get_idempotency_record(
        self,
        *,
        tool_name: str,
        caller_identity: str,
        scope_kind: str,
        scope_id: str,
        request_id: str,
    ) -> TeamIdempotencyRecord | None:
        key = _idempotency_key_values(
            tool_name=tool_name,
            caller_identity=caller_identity,
            scope_kind=scope_kind,
            scope_id=scope_id,
            request_id=request_id,
        )
        with self._lock:
            with self._connect() as connection:
                return self._lookup_idempotency_record(connection, **key)

    def enqueue_outbox_record(
        self,
        record: TeamOutboxRecord | Mapping[str, Any],
    ) -> TeamOutboxRecord:
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                return self._insert_outbox_record(connection, record)

    def list_outbox_records(
        self,
        *,
        status: TeamOutboxStatus | str | None = None,
        intent_type: str | None = None,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        limit: int | None = None,
    ) -> list[TeamOutboxRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(_outbox_status_value(status))
        for column, value in (
            ("intent_type", intent_type),
            ("aggregate_type", aggregate_type),
            ("aggregate_id", aggregate_id),
        ):
            if value is None:
                continue
            clauses.append(f"{column} = ?")
            params.append(_required_text(value, column))
        query = "SELECT * FROM team_outbox"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY available_at, created_at, id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(max(0, int(limit)))
        with self._lock:
            with self._connect() as connection:
                return [
                    self._outbox_record_from_row(row) for row in connection.execute(query, params)
                ]

    def claim_outbox_records(
        self,
        *,
        worker_id: str,
        limit: int = 1,
        intent_types: Sequence[str] | None = None,
        claim_token: str | None = None,
        claim_ttl_seconds: int = 300,
        now: datetime | None = None,
    ) -> list[TeamOutboxRecord]:
        if limit <= 0:
            return []
        normalized_worker_id = _required_text(worker_id, "worker_id")
        normalized_claim_token = _optional_text(claim_token) or f"claim-{uuid4().hex}"
        effective_now = now or utc_now()
        now_text = _datetime_text(effective_now)
        expires_at = effective_now + timedelta(seconds=max(1, int(claim_ttl_seconds)))
        expires_at_text = _datetime_text(expires_at)
        normalized_intent_types = [
            _required_text(intent_type, "intent_type") for intent_type in (intent_types or [])
        ]

        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if claim_token is not None:
                    existing = self._claimed_outbox_by_token(
                        connection,
                        claim_token=normalized_claim_token,
                        limit=limit,
                    )
                    if existing:
                        return existing

                clauses = [
                    "available_at <= ?",
                    """
                    (
                        status = ?
                        OR (
                            status = ?
                            AND claim_expires_at IS NOT NULL
                            AND claim_expires_at <= ?
                        )
                    )
                    """,
                ]
                params: list[Any] = [
                    now_text,
                    TeamOutboxStatus.PENDING.value,
                    TeamOutboxStatus.CLAIMED.value,
                    now_text,
                ]
                if normalized_intent_types:
                    placeholders = ", ".join("?" for _ in normalized_intent_types)
                    clauses.append(f"intent_type IN ({placeholders})")
                    params.extend(normalized_intent_types)
                ids = [
                    str(row["id"])
                    for row in connection.execute(
                        f"""
                        SELECT id
                        FROM team_outbox
                        WHERE {" AND ".join(clauses)}
                        ORDER BY available_at, created_at, id
                        LIMIT ?
                        """,
                        [*params, limit],
                    )
                ]
                claimed: list[TeamOutboxRecord] = []
                for record_id in ids:
                    connection.execute(
                        """
                        UPDATE team_outbox
                        SET status = ?,
                            claimed_at = ?,
                            claimed_by = ?,
                            claim_token = ?,
                            claim_expires_at = ?,
                            attempt_count = attempt_count + 1,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            TeamOutboxStatus.CLAIMED.value,
                            now_text,
                            normalized_worker_id,
                            normalized_claim_token,
                            expires_at_text,
                            now_text,
                            record_id,
                        ),
                    )
                    row = connection.execute(
                        "SELECT * FROM team_outbox WHERE id = ?",
                        (record_id,),
                    ).fetchone()
                    if row is not None:
                        claimed.append(self._outbox_record_from_row(row))
                return claimed

    def complete_outbox_record(
        self,
        *,
        record_id: str,
        claim_token: str | None = None,
        now: datetime | None = None,
    ) -> TeamOutboxRecord:
        normalized_record_id = _required_text(record_id, "record_id")
        normalized_claim_token = _optional_text(claim_token)
        now_text = _datetime_text(now or utc_now())
        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                clauses = ["id = ?"]
                params: list[Any] = [normalized_record_id]
                if normalized_claim_token is not None:
                    clauses.append("claim_token = ?")
                    params.append(normalized_claim_token)
                cursor = connection.execute(
                    f"""
                    UPDATE team_outbox
                    SET status = ?,
                        completed_at = ?,
                        updated_at = ?
                    WHERE {" AND ".join(clauses)}
                    """,
                    [TeamOutboxStatus.COMPLETED.value, now_text, now_text, *params],
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"outbox record {normalized_record_id} was not claimed")
                return self._require_outbox_record(connection, normalized_record_id)

    def fail_outbox_record(
        self,
        *,
        record_id: str,
        error_code: str | None = None,
        error_message: str | None = None,
        claim_token: str | None = None,
        retry_after_seconds: int | None = None,
        now: datetime | None = None,
    ) -> TeamOutboxRecord:
        normalized_record_id = _required_text(record_id, "record_id")
        normalized_claim_token = _optional_text(claim_token)
        now_value = now or utc_now()
        now_text = _datetime_text(now_value)
        next_status = TeamOutboxStatus.FAILED
        available_at: str | None = None
        if retry_after_seconds is not None:
            next_status = TeamOutboxStatus.PENDING
            available_at = _datetime_text(
                now_value + timedelta(seconds=max(0, int(retry_after_seconds)))
            )

        with self._lock:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                clauses = ["id = ?"]
                params: list[Any] = [normalized_record_id]
                if normalized_claim_token is not None:
                    clauses.append("claim_token = ?")
                    params.append(normalized_claim_token)
                cursor = connection.execute(
                    f"""
                    UPDATE team_outbox
                    SET status = ?,
                        available_at = COALESCE(?, available_at),
                        claimed_at = NULL,
                        claimed_by = NULL,
                        claim_token = NULL,
                        claim_expires_at = NULL,
                        completed_at = NULL,
                        last_error_code = ?,
                        last_error_message = ?,
                        updated_at = ?
                    WHERE {" AND ".join(clauses)}
                    """,
                    [
                        next_status.value,
                        available_at,
                        _optional_text(error_code),
                        _optional_text(error_message),
                        now_text,
                        *params,
                    ],
                )
                if cursor.rowcount == 0:
                    raise ValueError(f"outbox record {normalized_record_id} was not claimed")
                return self._require_outbox_record(connection, normalized_record_id)

    @staticmethod
    def _insert_dispatch_activity(
        connection: sqlite3.Connection,
        activity: TeamDispatchActivity | Mapping[str, Any],
    ) -> TeamDispatchActivity:
        record = TeamDispatchActivity.model_validate(activity)
        payload = record.model_dump(mode="json")
        retention_cutoff = utc_now() - timedelta(days=30)
        connection.execute(
            "DELETE FROM team_dispatch_activity WHERE created_at < ?",
            (_datetime_text(retention_cutoff),),
        )
        connection.execute(
            """
            INSERT INTO team_dispatch_activity(
                id, tool_name, caller_session_id, caller_identity,
                agent_id, governance_group_id, assignment_id, dispatch_id,
                request_id, correlation_id, causation_id, status,
                progress_classification, action_record_id, audit_record_id,
                error_code, error_message, correction_context_summary,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                payload["tool_name"],
                payload["caller_session_id"],
                payload["caller_identity"],
                payload.get("agent_id"),
                payload.get("governance_group_id"),
                payload.get("assignment_id"),
                payload.get("dispatch_id"),
                payload.get("request_id"),
                payload.get("correlation_id"),
                payload.get("causation_id"),
                payload["status"],
                payload["progress_classification"],
                payload.get("action_record_id"),
                payload.get("audit_record_id"),
                payload.get("error_code"),
                payload.get("error_message"),
                payload.get("correction_context_summary"),
                payload["created_at"],
                payload["updated_at"],
            ),
        )
        return record

    @classmethod
    def _insert_idempotency_record(
        cls,
        connection: sqlite3.Connection,
        record: TeamIdempotencyRecord | Mapping[str, Any],
        *,
        key: dict[str, str] | None = None,
    ) -> TeamIdempotencyRecord:
        stored = TeamIdempotencyRecord.model_validate(record)
        effective_key = key or _idempotency_key_values(
            tool_name=stored.tool_name,
            caller_identity=stored.caller_identity,
            scope_kind=stored.scope_kind,
            scope_id=stored.scope_id,
            request_id=stored.request_id,
        )
        payload = stored.model_dump(mode="json")
        existing = cls._lookup_idempotency_record(connection, **effective_key)
        if existing is not None:
            if existing.normalized_payload_digest != stored.normalized_payload_digest:
                _raise_idempotency_conflict(
                    **effective_key,
                    existing_digest=existing.normalized_payload_digest,
                    attempted_digest=stored.normalized_payload_digest,
                )
            return existing
        connection.execute(
            """
            INSERT INTO team_idempotency_records(
                id, tool_name, caller_identity, scope_kind, scope_id,
                request_id, normalized_payload_digest, response_payload_json,
                created_domain_ids_json, created_event_ids_json, status,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                payload["tool_name"],
                payload["caller_identity"],
                payload["scope_kind"],
                payload["scope_id"],
                payload["request_id"],
                payload["normalized_payload_digest"],
                _json_dumps(payload["response_payload"]),
                _json_dumps(payload["created_domain_ids"]),
                _json_dumps(payload["created_event_ids"]),
                payload["status"],
                payload["created_at"],
                payload["updated_at"],
            ),
        )
        return stored

    @classmethod
    def _insert_outbox_record(
        cls,
        connection: sqlite3.Connection,
        record: TeamOutboxRecord | Mapping[str, Any],
    ) -> TeamOutboxRecord:
        stored = TeamOutboxRecord.model_validate(record)
        payload = stored.model_dump(mode="json")
        if stored.idempotency_key is not None:
            existing = cls._lookup_outbox_by_idempotency_key(
                connection,
                stored.idempotency_key,
            )
            if existing is not None:
                return existing
        connection.execute(
            """
            INSERT INTO team_outbox(
                id, intent_type, aggregate_type, aggregate_id,
                idempotency_key, payload_json, status, available_at,
                attempt_count, claimed_at, claimed_by, claim_token,
                claim_expires_at, completed_at, last_error_code,
                last_error_message, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["id"],
                payload["intent_type"],
                payload.get("aggregate_type"),
                payload.get("aggregate_id"),
                payload.get("idempotency_key"),
                _json_dumps(payload["payload"]),
                payload["status"],
                payload["available_at"],
                payload["attempt_count"],
                payload.get("claimed_at"),
                payload.get("claimed_by"),
                payload.get("claim_token"),
                payload.get("claim_expires_at"),
                payload.get("completed_at"),
                payload.get("last_error_code"),
                payload.get("last_error_message"),
                payload["created_at"],
                payload["updated_at"],
            ),
        )
        return stored

    @staticmethod
    def _dispatch_activity_from_row(row: sqlite3.Row) -> TeamDispatchActivity:
        return TeamDispatchActivity.model_validate(
            {
                "id": row["id"],
                "tool_name": row["tool_name"],
                "caller_session_id": row["caller_session_id"],
                "caller_identity": row["caller_identity"],
                "agent_id": row["agent_id"],
                "governance_group_id": row["governance_group_id"],
                "assignment_id": row["assignment_id"],
                "dispatch_id": row["dispatch_id"],
                "request_id": row["request_id"],
                "correlation_id": row["correlation_id"],
                "causation_id": row["causation_id"],
                "status": row["status"],
                "progress_classification": row["progress_classification"],
                "action_record_id": row["action_record_id"],
                "audit_record_id": row["audit_record_id"],
                "error_code": row["error_code"],
                "error_message": row["error_message"],
                "correction_context_summary": row["correction_context_summary"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    @staticmethod
    def _idempotency_record_from_row(row: sqlite3.Row) -> TeamIdempotencyRecord:
        return TeamIdempotencyRecord.model_validate(
            {
                "id": row["id"],
                "tool_name": row["tool_name"],
                "caller_identity": row["caller_identity"],
                "scope_kind": row["scope_kind"],
                "scope_id": row["scope_id"],
                "request_id": row["request_id"],
                "normalized_payload_digest": row["normalized_payload_digest"],
                "response_payload": _json_loads(row["response_payload_json"]),
                "created_domain_ids": _json_loads(row["created_domain_ids_json"]),
                "created_event_ids": _json_loads(row["created_event_ids_json"]),
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    @classmethod
    def _outbox_record_from_row(cls, row: sqlite3.Row) -> TeamOutboxRecord:
        return TeamOutboxRecord.model_validate(
            {
                "id": row["id"],
                "intent_type": row["intent_type"],
                "aggregate_type": row["aggregate_type"],
                "aggregate_id": row["aggregate_id"],
                "idempotency_key": row["idempotency_key"],
                "payload": _json_loads(row["payload_json"]),
                "status": row["status"],
                "available_at": row["available_at"],
                "attempt_count": row["attempt_count"],
                "claimed_at": row["claimed_at"],
                "claimed_by": row["claimed_by"],
                "claim_token": row["claim_token"],
                "claim_expires_at": row["claim_expires_at"],
                "completed_at": row["completed_at"],
                "last_error_code": row["last_error_code"],
                "last_error_message": row["last_error_message"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    @classmethod
    def _lookup_idempotency_record(
        cls,
        connection: sqlite3.Connection,
        *,
        tool_name: str,
        caller_identity: str,
        scope_kind: str,
        scope_id: str,
        request_id: str,
    ) -> TeamIdempotencyRecord | None:
        row = connection.execute(
            """
            SELECT *
            FROM team_idempotency_records
            WHERE tool_name = ?
              AND caller_identity = ?
              AND scope_kind = ?
              AND scope_id = ?
              AND request_id = ?
            """,
            (tool_name, caller_identity, scope_kind, scope_id, request_id),
        ).fetchone()
        if row is None:
            return None
        return cls._idempotency_record_from_row(row)

    @classmethod
    def _lookup_outbox_by_idempotency_key(
        cls,
        connection: sqlite3.Connection,
        idempotency_key: str,
    ) -> TeamOutboxRecord | None:
        row = connection.execute(
            "SELECT * FROM team_outbox WHERE idempotency_key = ?",
            (_required_text(idempotency_key, "idempotency_key"),),
        ).fetchone()
        if row is None:
            return None
        return cls._outbox_record_from_row(row)

    @classmethod
    def _claimed_outbox_by_token(
        cls,
        connection: sqlite3.Connection,
        *,
        claim_token: str,
        limit: int,
    ) -> list[TeamOutboxRecord]:
        return [
            cls._outbox_record_from_row(row)
            for row in connection.execute(
                """
                SELECT *
                FROM team_outbox
                WHERE status = ?
                  AND claim_token = ?
                ORDER BY claimed_at, id
                LIMIT ?
                """,
                (TeamOutboxStatus.CLAIMED.value, claim_token, limit),
            )
        ]

    @classmethod
    def _require_outbox_record(
        cls,
        connection: sqlite3.Connection,
        record_id: str,
    ) -> TeamOutboxRecord:
        row = connection.execute(
            "SELECT * FROM team_outbox WHERE id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"outbox record {record_id} does not exist")
        return cls._outbox_record_from_row(row)

    def ensure_schema(self) -> None:
        with self._lock:
            with self._connect() as connection:
                connection.executescript(_SCHEMA_SQL)
                schema_version_row = connection.execute(
                    """
                    SELECT value
                    FROM team_state_metadata
                    WHERE key = 'sqlite_schema_version'
                    """
                ).fetchone()
                previous_schema_version = (
                    int(schema_version_row["value"]) if schema_version_row is not None else 0
                )
                state_schema_version_row = connection.execute(
                    """
                    SELECT value
                    FROM team_state_metadata
                    WHERE key = 'state_schema_version'
                    """
                ).fetchone()
                previous_state_schema_version = (
                    int(state_schema_version_row["value"])
                    if state_schema_version_row is not None
                    else 0
                )
                _ensure_column(
                    connection,
                    table_name="team_governance_group_goals",
                    column_name="goal_name",
                    definition="TEXT NOT NULL DEFAULT ''",
                )
                _ensure_column(
                    connection,
                    table_name="team_action_records",
                    column_name="feedback_delivered_at",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_conversation_messages",
                    column_name="attachment_ids_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                _ensure_column(
                    connection,
                    table_name="team_action_records",
                    column_name="feedback_delivered_by_dispatch_id",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_inbox",
                    column_name="response_requested",
                    definition="INTEGER NOT NULL DEFAULT 1",
                )
                _ensure_column(
                    connection,
                    table_name="team_dispatches",
                    column_name="review_routing_authorities_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                _ensure_column(
                    connection,
                    table_name="team_inbox",
                    column_name="priority",
                    definition="TEXT NOT NULL DEFAULT 'normal'",
                )
                _ensure_column(
                    connection,
                    table_name="team_teams",
                    column_name="escalate_to_governance",
                    definition="INTEGER NOT NULL DEFAULT 0",
                )
                _ensure_column(
                    connection,
                    table_name="team_agent_runtimes",
                    column_name="low_priority_batch_started_at",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_agent_runtimes",
                    column_name="low_priority_batch_cycle_count",
                    definition="INTEGER NOT NULL DEFAULT 0",
                )
                _ensure_column(
                    connection,
                    table_name="team_agent_runtimes",
                    column_name="low_priority_batch_due_after_cycles",
                    definition="INTEGER",
                )
                _ensure_column(
                    connection,
                    table_name="team_agent_runtimes",
                    column_name="context_clearing_policy",
                    definition="TEXT NOT NULL DEFAULT 'off'",
                )
                _ensure_column(
                    connection,
                    table_name="team_agent_runtimes",
                    column_name="context_handoff_preparation_pending",
                    definition="INTEGER NOT NULL DEFAULT 0",
                )
                _ensure_column(
                    connection,
                    table_name="team_agent_runtimes",
                    column_name="context_handoff_preparation_status",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_agent_runtimes",
                    column_name="last_agent_handoff_summary",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_agent_runtimes",
                    column_name="last_agent_handoff_at",
                    definition="TEXT",
                )
                connection.execute(
                    """
                    UPDATE team_agent_runtimes
                    SET context_clearing_policy = CASE
                        WHEN auto_context_clearing_enabled != 0
                            AND (
                                context_clearing_policy IS NULL
                                OR context_clearing_policy = ''
                                OR context_clearing_policy = 'off'
                            )
                            THEN 'idle'
                        ELSE COALESCE(NULLIF(context_clearing_policy, ''), 'off')
                    END
                    """
                )
                _ensure_column(
                    connection,
                    table_name="team_agents",
                    column_name="avatar_json",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_agents",
                    column_name="voice_profile_json",
                    definition="TEXT",
                )
                _ensure_team_agents_nullable_membership(connection)
                _ensure_project_assignments_from_legacy(connection)
                _ensure_team_conversations_operator_scope(connection)
                _ensure_column(
                    connection,
                    table_name="team_governance_runtimes",
                    column_name="last_cost_signal_fingerprints_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                _ensure_column(
                    connection,
                    table_name="team_governance_runtimes",
                    column_name="last_blocked_signal_fingerprints_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                _ensure_column(
                    connection,
                    table_name="team_tasks",
                    column_name="state_entered_at",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_tasks",
                    column_name="case_id",
                    definition="TEXT",
                )
                for column_name, definition in (
                    ("task_kind", "TEXT NOT NULL DEFAULT 'support'"),
                    ("outcome_contract_json", "TEXT"),
                    ("integration_boundary", "INTEGER NOT NULL DEFAULT 0"),
                    ("deliverable_contract_json", "TEXT"),
                    ("completion_json", "TEXT"),
                    ("originating_task_id", "TEXT"),
                    ("cancellation_json", "TEXT"),
                ):
                    _ensure_column(
                        connection,
                        table_name="team_tasks",
                        column_name=column_name,
                        definition=definition,
                    )
                _ensure_column(
                    connection,
                    table_name="team_deliverables",
                    column_name="branch_context_id",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_deliverables",
                    column_name="supersedes_deliverable_id",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_deliverables",
                    column_name="superseded_by_deliverable_id",
                    definition="TEXT",
                )
                for column_name, definition in (
                    ("supersession_reason", "TEXT"),
                    ("superseded_at", "TEXT"),
                    ("superseded_by_actor", "TEXT"),
                    ("review_cohort_id", "TEXT"),
                    ("review_cohort_sealed_at", "TEXT"),
                    ("rework_requested_at", "TEXT"),
                    ("rework_requested_by_agent_id", "TEXT"),
                    ("rework_reason", "TEXT"),
                ):
                    _ensure_column(
                        connection,
                        table_name="team_deliverables",
                        column_name=column_name,
                        definition=definition,
                    )
                _ensure_column(
                    connection,
                    table_name="team_review_requests",
                    column_name="review_cohort_id",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_deliverables",
                    column_name="verification_json",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_deliverables",
                    column_name="outcomes_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                for column_name in (
                    "submitted_commit_sha",
                    "reviewed_commit_sha",
                    "feature_commit_sha",
                    "release_commit_sha",
                ):
                    _ensure_column(
                        connection,
                        table_name="team_deliverables",
                        column_name=column_name,
                        definition="TEXT",
                    )
                _ensure_column(
                    connection,
                    table_name="team_deliverables",
                    column_name="carried_findings_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                _ensure_column(
                    connection,
                    table_name="team_deliverables",
                    column_name="review_manifest_json",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_deliverables",
                    column_name="acceptance_recipient_agent_id",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_deliverables",
                    column_name="acceptance_decision_json",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_deliverables",
                    column_name="completion_json",
                    definition="TEXT",
                )
                for column_name, definition in (
                    ("reason_code", "TEXT NOT NULL DEFAULT 'unspecified'"),
                    ("reporter_actor", "TEXT"),
                ):
                    _ensure_column(
                        connection,
                        table_name="team_blockers",
                        column_name=column_name,
                        definition=definition,
                    )
                _ensure_column(
                    connection,
                    table_name="team_review_requests",
                    column_name="reviewed_commit_sha",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_review_profiles",
                    column_name="required_outcome_kinds_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                _ensure_column(
                    connection,
                    table_name="team_review_requests",
                    column_name="release_package_id",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_review_requests",
                    column_name="candidate_commit_sha",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_review_requests",
                    column_name="criteria_snapshot_json",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_review_requests",
                    column_name="required_outcome_kind_definitions_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                for column_name in (
                    "requested_by_agent_id",
                    "requested_by_actor",
                    "supersedes_review_request_id",
                    "superseded_by_review_request_id",
                    "retry_reason",
                    "retry_requested_by",
                    "block_kind",
                    "retired_at",
                    "retired_by",
                    "retirement_reason",
                ):
                    _ensure_column(
                        connection,
                        table_name="team_review_requests",
                        column_name=column_name,
                        definition="TEXT",
                    )
                _ensure_column(
                    connection,
                    table_name="team_projects",
                    column_name="release_policy_json",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_release_packages",
                    column_name="summary",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_release_packages",
                    column_name="owner_agent_id",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_release_packages",
                    column_name="policy_version",
                    definition="INTEGER NOT NULL DEFAULT 1",
                )
                _ensure_column(
                    connection,
                    table_name="team_release_packages",
                    column_name="verification_waivers_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                _ensure_column(
                    connection,
                    table_name="team_release_packages",
                    column_name="scope_validation_json",
                    definition="TEXT",
                )
                for column_name in (
                    "base_commit_sha",
                    "candidate_commit_sha",
                    "candidate_tree_hash",
                    "approved_commit_sha",
                    "review_correction_task_id",
                ):
                    _ensure_column(
                        connection,
                        table_name="team_release_packages",
                        column_name=column_name,
                        definition="TEXT",
                    )
                _ensure_column(
                    connection,
                    table_name="team_release_packages",
                    column_name="verification_run_ids_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                _ensure_column(
                    connection,
                    table_name="team_release_packages",
                    column_name="review_request_ids_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                _ensure_column(
                    connection,
                    table_name="team_release_packages",
                    column_name="manifest_json",
                    definition="TEXT NOT NULL DEFAULT '{}'",
                )
                _ensure_column(
                    connection,
                    table_name="team_release_packages",
                    column_name="gate_failure_json",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_release_packages",
                    column_name="supersedes_release_package_id",
                    definition="TEXT",
                )
                _ensure_column(
                    connection,
                    table_name="team_human_review_findings",
                    column_name="resolved_by_human_review_request_id",
                    definition="TEXT",
                )
                _migrate_human_review_relationships(connection)
                _ensure_column(
                    connection,
                    table_name="team_worktree_cleanup_candidates",
                    column_name="worktree_path",
                    definition="TEXT",
                )
                connection.execute("DROP INDEX IF EXISTS idx_team_worktree_cleanup_identity")
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_team_worktree_cleanup_identity_v2
                    ON team_worktree_cleanup_candidates(
                        task_id, managed_repo_id, session_id, worktree_path
                    )
                    """
                )
                _ensure_column(
                    connection,
                    table_name="team_git_promotions",
                    column_name="sources_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                _ensure_column(
                    connection,
                    table_name="team_git_work_contexts",
                    column_name="dependency_git_sources_json",
                    definition="TEXT NOT NULL DEFAULT '[]'",
                )
                connection.execute(
                    """
                    UPDATE team_tasks
                    SET state_entered_at = COALESCE(NULLIF(state_entered_at, ''), updated_at, created_at)
                    WHERE state_entered_at IS NULL OR state_entered_at = ''
                    """
                )
                connection.execute(
                    """
                    UPDATE team_governance_group_goals
                    SET goal_name = goal_id
                    WHERE goal_name IS NULL OR goal_name = ''
                    """
                )
                connection.execute(
                    """
                    UPDATE team_release_packages
                    SET state = 'rejected',
                        error = 'legacy_unpinned_release: recreate this nonterminal release under the current immutable candidate policy',
                        updated_at = COALESCE(updated_at, created_at)
                    WHERE policy_version < 2
                      AND state NOT IN ('accepted', 'rejected')
                    """
                )
                if previous_schema_version < 31:
                    _migrate_legacy_outcome_kind_definitions(connection)
                connection.execute(
                    """
                    INSERT INTO team_state_metadata(key, value)
                    VALUES ('sqlite_schema_version', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(SQLITE_SCHEMA_VERSION),),
                )
                if previous_state_schema_version < SCHEMA_VERSION:
                    connection.execute(
                        """
                        INSERT INTO team_state_metadata(key, value)
                        VALUES ('state_schema_version', ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (str(SCHEMA_VERSION),),
                    )

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        self._enable_wal(connection)
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _enable_wal(connection: sqlite3.Connection) -> None:
        deadline = time.monotonic() + _SQLITE_WAL_ENABLE_TIMEOUT_SECONDS
        while True:
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).casefold() or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def _import_legacy_json_or_initialize(self) -> None:
        with self._lock:
            with self._connect() as connection:
                if self._has_state(connection):
                    return
                connection.execute("BEGIN IMMEDIATE")
                if self.legacy_json_path.is_file():
                    text = self.legacy_json_path.read_text(encoding="utf-8")
                    payload = json.loads(text)
                    _migrate_legacy_json_outcome_kind_records(payload)
                    state = TeamState.model_validate(payload)
                    self._write_state(connection, state)
                    now = utc_now().isoformat()
                    connection.executemany(
                        """
                        INSERT INTO team_state_metadata(key, value)
                        VALUES (?, ?)
                        ON CONFLICT(key) DO UPDATE SET value = excluded.value
                        """,
                        (
                            ("legacy_json_imported_at", now),
                            ("legacy_json_path", str(self.legacy_json_path)),
                            (
                                "legacy_json_sha256",
                                hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            ),
                        ),
                    )
                    return
                self._write_state(connection, TeamState.empty())

    @staticmethod
    def _has_state(connection: sqlite3.Connection) -> bool:
        row = connection.execute(
            "SELECT 1 FROM team_state_metadata WHERE key = 'revision'"
        ).fetchone()
        return row is not None

    def _read_payload(self, connection: sqlite3.Connection) -> dict[str, Any]:
        metadata = {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM team_state_metadata")
        }
        if "revision" not in metadata:
            return TeamState.empty().model_dump(mode="json")

        payload: dict[str, Any] = {
            "schema_version": int(metadata.get("state_schema_version", SCHEMA_VERSION)),
            "revision": int(metadata.get("revision", "0")),
            "updated_at": metadata.get("updated_at") or utc_now().isoformat(),
            "teams": {},
            "governance_groups": {},
            "governance_agenda": {},
            "governance_runtimes": {},
            "projects": {},
            "roles": {},
            "agents": {},
            "project_assignments": {},
            "agent_runtimes": {},
            "cases": {},
            "tasks": {},
            "deliverables": {},
            "review_requests": {},
            "outcome_kind_definitions": {},
            "review_profiles": {},
            "inbox": {},
            "blockers": {},
            "blocker_events": {},
            "action_records": {},
            "memories": {},
            "conversations": {},
            "conversation_messages": {},
            "external_chat_ingress": {},
            "external_chat_deliveries": {},
            "chat_attachments": {},
            "chat_attachment_policies": {},
            "release_packages": {},
            "release_verification_runs": {},
            "human_review_requests": {},
            "human_review_decisions": {},
            "human_review_findings": {},
            "dispatches": {},
            "worktree_cleanup_candidates": {},
            "ephemeral_assignments": {},
            "git_promotions": {},
            "git_work_contexts": {},
            "stand_downs": {},
        }
        self._read_teams(connection, payload)
        self._read_governance_groups(connection, payload)
        self._read_governance_agenda(connection, payload)
        self._read_governance_runtimes(connection, payload)
        self._read_roles(connection, payload)
        self._read_projects(connection, payload)
        self._read_agents(connection, payload)
        self._read_project_assignments(connection, payload)
        self._read_stand_downs(connection, payload)
        self._read_agent_runtimes(connection, payload)
        self._read_cases(connection, payload)
        self._read_tasks(connection, payload)
        self._read_deliverables(connection, payload)
        self._read_outcome_kind_definitions(connection, payload)
        self._read_review_profiles(connection, payload)
        self._read_review_requests(connection, payload)
        self._read_inbox(connection, payload)
        self._read_blockers(connection, payload)
        self._read_blocker_events(connection, payload)
        self._read_action_records(connection, payload)
        self._read_memories(connection, payload)
        self._read_conversations(connection, payload)
        self._read_conversation_messages(connection, payload)
        self._read_external_chat_ingress(connection, payload)
        self._read_external_chat_deliveries(connection, payload)
        self._read_chat_attachments(connection, payload)
        self._read_chat_attachment_policies(connection, payload)
        self._read_release_packages(connection, payload)
        self._read_release_verification_runs(connection, payload)
        self._read_human_reviews(connection, payload)
        self._read_dispatches(connection, payload)
        self._read_worktree_cleanup_candidates(connection, payload)
        self._read_ephemeral_assignments(connection, payload)
        self._read_git_promotions(connection, payload)
        self._read_git_work_contexts(connection, payload)
        return TeamState.model_validate(payload).model_dump(mode="json")

    def _write_state(self, connection: sqlite3.Connection, state: TeamState) -> None:
        payload = state.model_dump(mode="json")
        payload["schema_version"] = max(
            int(payload.get("schema_version", SCHEMA_VERSION)),
            SCHEMA_VERSION,
        )
        self._clear_state_tables(connection)
        self._write_teams(connection, payload["teams"])
        self._write_governance_groups(connection, payload["governance_groups"])
        self._write_governance_agenda(connection, payload["governance_agenda"])
        self._write_governance_runtimes(connection, payload["governance_runtimes"])
        self._write_roles(connection, payload["roles"])
        self._write_projects(connection, payload["projects"])
        self._write_agents(connection, payload["agents"])
        self._write_project_assignments(connection, payload["project_assignments"])
        self._write_stand_downs(connection, payload["stand_downs"])
        self._write_agent_runtimes(connection, payload["agent_runtimes"])
        self._write_cases(connection, payload["cases"])
        self._write_tasks(connection, payload["tasks"])
        self._write_deliverables(connection, payload["deliverables"])
        self._write_outcome_kind_definitions(
            connection,
            payload["outcome_kind_definitions"],
        )
        self._write_review_profiles(connection, payload["review_profiles"])
        self._write_review_requests(connection, payload["review_requests"])
        self._write_inbox(connection, payload["inbox"])
        self._write_blockers(connection, payload["blockers"])
        self._write_blocker_events(connection, payload["blocker_events"])
        self._write_action_records(connection, payload["action_records"])
        self._write_memories(connection, payload["memories"])
        self._write_conversations(connection, payload["conversations"])
        self._write_conversation_messages(connection, payload["conversation_messages"])
        self._write_external_chat_ingress(connection, payload["external_chat_ingress"])
        self._write_external_chat_deliveries(connection, payload["external_chat_deliveries"])
        self._write_chat_attachments(connection, payload["chat_attachments"])
        self._write_chat_attachment_policies(connection, payload["chat_attachment_policies"])
        self._write_release_packages(connection, payload["release_packages"])
        self._write_release_verification_runs(connection, payload["release_verification_runs"])
        self._write_human_reviews(
            connection,
            requests=payload["human_review_requests"],
            decisions=payload["human_review_decisions"],
            findings=payload["human_review_findings"],
        )
        self._write_dispatches(connection, payload["dispatches"])
        self._write_worktree_cleanup_candidates(
            connection,
            payload["worktree_cleanup_candidates"],
        )
        self._write_ephemeral_assignments(connection, payload["ephemeral_assignments"])
        self._write_git_promotions(connection, payload["git_promotions"])
        self._write_git_work_contexts(connection, payload["git_work_contexts"])
        connection.executemany(
            """
            INSERT INTO team_state_metadata(key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (
                ("sqlite_schema_version", str(SQLITE_SCHEMA_VERSION)),
                ("state_schema_version", str(payload["schema_version"])),
                ("revision", str(payload["revision"])),
                ("updated_at", str(payload["updated_at"])),
            ),
        )

    @staticmethod
    def _clear_state_tables(connection: sqlite3.Connection) -> None:
        for table in _STATE_TABLES:
            connection.execute(f"DELETE FROM {table}")

    @staticmethod
    def _read_teams(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        project_ids = _grouped_values(
            connection,
            "SELECT team_id AS owner_id, project_id AS value FROM team_team_projects ORDER BY team_id, position",
        )
        for row in connection.execute("SELECT * FROM team_teams ORDER BY id"):
            payload["teams"][row["id"]] = {
                "id": row["id"],
                "name": row["name"],
                "manager_agent_id": row["manager_agent_id"],
                "operator_communication_target_id": row["operator_communication_target_id"],
                "max_cost_usd": row["max_cost_usd"],
                "escalate_to_governance": _bool(row["escalate_to_governance"]),
                "project_ids": project_ids.get(row["id"], []),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "archived": _bool(row["archived"]),
            }

    @staticmethod
    def _write_teams(connection: sqlite3.Connection, teams: dict[str, Any]) -> None:
        for team in teams.values():
            connection.execute(
                """
                INSERT INTO team_teams(
                    id, name, manager_agent_id, operator_communication_target_id,
                    max_cost_usd, escalate_to_governance, created_at, updated_at, archived
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    team["id"],
                    team["name"],
                    team["manager_agent_id"],
                    team.get("operator_communication_target_id"),
                    _optional_text(team.get("max_cost_usd")),
                    _int_bool(team.get("escalate_to_governance")),
                    team["created_at"],
                    team["updated_at"],
                    _int_bool(team.get("archived")),
                ),
            )
            _insert_position_values(
                connection,
                "team_team_projects",
                "team_id",
                team["id"],
                "project_id",
                team.get("project_ids", []),
            )

    @staticmethod
    def _read_governance_groups(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        member_team_ids = _grouped_values(
            connection,
            """
            SELECT governance_group_id AS owner_id, team_id AS value
            FROM team_governance_group_members
            ORDER BY governance_group_id, position
            """,
        )
        goals = _grouped_rows(
            connection,
            """
            SELECT governance_group_id AS owner_id, goal_id, goal_name, description, state
            FROM team_governance_group_goals
            ORDER BY governance_group_id, position
            """,
            lambda row: {
                "id": row["goal_id"],
                "name": row["goal_name"] or row["goal_id"],
                "description": row["description"],
                "state": row["state"],
            },
        )
        for row in connection.execute("SELECT * FROM team_governance_groups ORDER BY id"):
            payload["governance_groups"][row["id"]] = {
                "id": row["id"],
                "name": row["name"],
                "manager_agent_id": row["manager_agent_id"],
                "member_team_ids": member_team_ids.get(row["id"], []),
                "goals": goals.get(row["id"], []),
                "max_cost_usd": row["max_cost_usd"],
                "archived": _bool(row["archived"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_governance_groups(
        connection: sqlite3.Connection,
        groups: dict[str, Any],
    ) -> None:
        for group in groups.values():
            connection.execute(
                """
                INSERT INTO team_governance_groups(
                    id, name, manager_agent_id, max_cost_usd, archived,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group["id"],
                    group["name"],
                    group["manager_agent_id"],
                    _optional_text(group.get("max_cost_usd")),
                    _int_bool(group.get("archived")),
                    group["created_at"],
                    group["updated_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_governance_group_members",
                "governance_group_id",
                group["id"],
                "team_id",
                group.get("member_team_ids", []),
            )
            for position, goal in enumerate(group.get("goals", [])):
                connection.execute(
                    """
                    INSERT INTO team_governance_group_goals(
                        governance_group_id, position, goal_id, goal_name, description, state
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        group["id"],
                        position,
                        goal["id"],
                        goal["name"],
                        goal["description"],
                        goal.get("state", "pending"),
                    ),
                )

    @staticmethod
    def _read_governance_agenda(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute("SELECT * FROM team_governance_agenda ORDER BY id"):
            payload["governance_agenda"][row["id"]] = {
                "id": row["id"],
                "governance_group_id": row["governance_group_id"],
                "item_type": row["item_type"],
                "title": row["title"],
                "summary": row["summary"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "agent_id": row["agent_id"],
                "payload": _json_loads(row["payload_json"]) if row["payload_json"] else {},
                "state": row["state"],
                "priority": row["priority"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_governance_agenda(
        connection: sqlite3.Connection,
        agenda: dict[str, Any],
    ) -> None:
        for item in agenda.values():
            connection.execute(
                """
                INSERT INTO team_governance_agenda(
                    id, governance_group_id, item_type, title, summary,
                    team_id, project_id, agent_id, payload_json, state, priority,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["governance_group_id"],
                    item["item_type"],
                    item["title"],
                    item["summary"],
                    item.get("team_id"),
                    item.get("project_id"),
                    item.get("agent_id"),
                    _json_dumps(item.get("payload") or {}),
                    item.get("state", "pending"),
                    item.get("priority", "normal"),
                    item["created_at"],
                    item["updated_at"],
                ),
            )

    @staticmethod
    def _read_governance_runtimes(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute(
            "SELECT * FROM team_governance_runtimes ORDER BY governance_group_id"
        ):
            payload["governance_runtimes"][row["governance_group_id"]] = {
                "governance_group_id": row["governance_group_id"],
                "wake_leased_until": row["wake_leased_until"],
                "active_dispatch_id": row["active_dispatch_id"],
                "wake_reason": row["wake_reason"],
                "last_wake_at": row["last_wake_at"],
                "next_wake_at": row["next_wake_at"],
                "last_cost_signal_fingerprints": (
                    _json_loads(row["last_cost_signal_fingerprints_json"])
                    if row["last_cost_signal_fingerprints_json"]
                    else []
                ),
                "last_blocked_signal_fingerprints": (
                    _json_loads(row["last_blocked_signal_fingerprints_json"])
                    if row["last_blocked_signal_fingerprints_json"]
                    else []
                ),
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_governance_runtimes(
        connection: sqlite3.Connection,
        runtimes: dict[str, Any],
    ) -> None:
        for runtime in runtimes.values():
            connection.execute(
                """
                INSERT INTO team_governance_runtimes(
                    governance_group_id, wake_leased_until, active_dispatch_id,
                    wake_reason, last_wake_at, next_wake_at,
                    last_cost_signal_fingerprints_json,
                    last_blocked_signal_fingerprints_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    runtime["governance_group_id"],
                    runtime.get("wake_leased_until"),
                    runtime.get("active_dispatch_id"),
                    runtime.get("wake_reason"),
                    runtime.get("last_wake_at"),
                    runtime.get("next_wake_at"),
                    _json_dumps(runtime.get("last_cost_signal_fingerprints") or []),
                    _json_dumps(runtime.get("last_blocked_signal_fingerprints") or []),
                    runtime["updated_at"],
                ),
            )

    @staticmethod
    def _read_roles(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        permissions = _grouped_values(
            connection,
            "SELECT role_id AS owner_id, permission AS value FROM team_role_permissions ORDER BY role_id, position",
        )
        for row in connection.execute("SELECT * FROM team_roles ORDER BY id"):
            payload["roles"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "name": row["name"],
                "description": row["description"],
                "permissions": permissions.get(row["id"], []),
                "ephemeral_policy": {"max_concurrent": row["max_ephemeral_concurrent"]},
                "archived": _bool(row["archived"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_roles(connection: sqlite3.Connection, roles: dict[str, Any]) -> None:
        for role in roles.values():
            connection.execute(
                """
                INSERT INTO team_roles(
                    id, team_id, project_id, name, description, max_ephemeral_concurrent,
                    archived, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    role["id"],
                    role["team_id"],
                    role.get("project_id"),
                    role["name"],
                    role.get("description", ""),
                    int(role.get("ephemeral_policy", {}).get("max_concurrent", 0)),
                    _int_bool(role.get("archived")),
                    role["created_at"],
                    role["updated_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_role_permissions",
                "role_id",
                role["id"],
                "permission",
                role.get("permissions", []),
            )

    @staticmethod
    def _read_projects(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        goals = _grouped_rows(
            connection,
            """
            SELECT project_id AS owner_id, goal_id, description, state
            FROM team_project_goals
            ORDER BY project_id, position
            """,
            lambda row: {
                "id": row["goal_id"],
                "description": row["description"],
                "state": row["state"],
            },
        )
        for row in connection.execute("SELECT * FROM team_projects ORDER BY id"):
            payload["projects"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "name": row["name"],
                "lead_agent_id": row["lead_agent_id"],
                "goals": goals.get(row["id"], []),
                "git": _json_loads(row["git_json"]) if row["git_json"] is not None else None,
                "release_policy": (
                    _json_loads(row["release_policy_json"])
                    if row["release_policy_json"] is not None
                    else None
                ),
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_projects(connection: sqlite3.Connection, projects: dict[str, Any]) -> None:
        for project in projects.values():
            connection.execute(
                """
                INSERT INTO team_projects(
                    id, team_id, name, lead_agent_id, git_json, release_policy_json,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project["id"],
                    project["team_id"],
                    project["name"],
                    project["lead_agent_id"],
                    _json_dumps(project.get("git")) if project.get("git") is not None else None,
                    (
                        _json_dumps(project.get("release_policy"))
                        if project.get("release_policy") is not None
                        else None
                    ),
                    project.get("status", "active"),
                    project["created_at"],
                    project["updated_at"],
                ),
            )
            for position, goal in enumerate(project.get("goals", [])):
                connection.execute(
                    """
                    INSERT INTO team_project_goals(project_id, position, goal_id, description, state)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        project["id"],
                        position,
                        goal["id"],
                        goal["description"],
                        goal.get("state", "pending"),
                    ),
                )

    @staticmethod
    def _read_agents(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        subordinates = _grouped_values(
            connection,
            "SELECT agent_id AS owner_id, subordinate_agent_id AS value FROM team_agent_subordinates ORDER BY agent_id, position",
        )
        for row in connection.execute("SELECT * FROM team_agents ORDER BY id"):
            payload["agents"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "display_name": row["display_name"],
                "avatar": (
                    _json_loads(row["avatar_json"]) if row["avatar_json"] is not None else None
                ),
                "voice_profile": (
                    _json_loads(row["voice_profile_json"])
                    if row["voice_profile_json"] is not None
                    else None
                ),
                "role_id": row["role_id"],
                "session_id": row["session_id"],
                "reports_to_agent_id": row["reports_to_agent_id"],
                "subordinate_agent_ids": subordinates.get(row["id"], []),
                "ephemeral_policy": {"max_concurrent": row["max_ephemeral_concurrent"]},
                "status": row["status"],
            }

    @staticmethod
    def _write_agents(connection: sqlite3.Connection, agents: dict[str, Any]) -> None:
        for agent in agents.values():
            connection.execute(
                """
                INSERT INTO team_agents(
                    id, team_id, display_name, avatar_json, voice_profile_json,
                    role_id, session_id, reports_to_agent_id, max_ephemeral_concurrent, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent["id"],
                    agent["team_id"],
                    agent.get("display_name"),
                    _json_dumps(agent.get("avatar")) if agent.get("avatar") is not None else None,
                    (
                        _json_dumps(agent.get("voice_profile"))
                        if agent.get("voice_profile") is not None
                        else None
                    ),
                    agent["role_id"],
                    agent["session_id"],
                    agent.get("reports_to_agent_id"),
                    int(agent.get("ephemeral_policy", {}).get("max_concurrent", 0)),
                    agent.get("status", "active"),
                ),
            )
            _insert_position_values(
                connection,
                "team_agent_subordinates",
                "agent_id",
                agent["id"],
                "subordinate_agent_id",
                agent.get("subordinate_agent_ids", []),
            )

    @staticmethod
    def _read_project_assignments(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_project_assignments ORDER BY id"):
            payload["project_assignments"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "agent_id": row["agent_id"],
                "role_id": row["role_id"],
                "kind": row["kind"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_project_assignments(
        connection: sqlite3.Connection,
        assignments: dict[str, Any],
    ) -> None:
        for assignment in assignments.values():
            connection.execute(
                """
                INSERT INTO team_project_assignments(
                    id, team_id, project_id, agent_id, role_id, kind, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment["id"],
                    assignment["team_id"],
                    assignment["project_id"],
                    assignment["agent_id"],
                    assignment.get("role_id"),
                    assignment.get("kind", "project_member"),
                    assignment["created_at"],
                    assignment["updated_at"],
                ),
            )

    @staticmethod
    def _read_stand_downs(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute("SELECT * FROM team_agent_stand_downs ORDER BY id"):
            payload["stand_downs"][row["id"]] = {
                "id": row["id"],
                "agent_id": row["agent_id"],
                "scope": row["scope"],
                "reason": row["reason"],
                "source": row["source"],
                "set_by": row["set_by"],
                "set_at": row["set_at"],
                "cleared_by": row["cleared_by"],
                "cleared_at": row["cleared_at"],
            }

    @staticmethod
    def _write_stand_downs(
        connection: sqlite3.Connection,
        stand_downs: dict[str, Any],
    ) -> None:
        for stand_down in stand_downs.values():
            connection.execute(
                """
                INSERT INTO team_agent_stand_downs(
                    id, agent_id, scope, reason, source, set_by, set_at,
                    cleared_by, cleared_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stand_down["id"],
                    stand_down["agent_id"],
                    stand_down.get("scope", "subtree"),
                    stand_down["reason"],
                    stand_down.get("source", "operator"),
                    stand_down["set_by"],
                    stand_down["set_at"],
                    stand_down.get("cleared_by"),
                    stand_down.get("cleared_at"),
                ),
            )

    @staticmethod
    def _read_agent_runtimes(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_agent_runtimes ORDER BY agent_id"):
            payload["agent_runtimes"][row["agent_id"]] = {
                "agent_id": row["agent_id"],
                "wake_leased_until": row["wake_leased_until"],
                "active_dispatch_id": row["active_dispatch_id"],
                "wake_reason": row["wake_reason"],
                "last_wake_at": row["last_wake_at"],
                "next_wake_at": row["next_wake_at"],
                "no_progress_count": row["no_progress_count"],
                "last_progress_at": row["last_progress_at"],
                "low_priority_batch_started_at": row["low_priority_batch_started_at"],
                "low_priority_batch_cycle_count": row["low_priority_batch_cycle_count"],
                "low_priority_batch_due_after_cycles": row["low_priority_batch_due_after_cycles"],
                "context_clearing_policy": row["context_clearing_policy"],
                "auto_context_clearing_enabled": _bool(row["auto_context_clearing_enabled"]),
                "context_rotation_pending": _bool(row["context_rotation_pending"]),
                "context_rotation_requested_by": row["context_rotation_requested_by"],
                "context_rotation_requested_at": row["context_rotation_requested_at"],
                "context_rotation_reason": row["context_rotation_reason"],
                "context_rotation_request_note": row["context_rotation_request_note"],
                "last_handoff_summary": row["last_handoff_summary"],
                "context_handoff_preparation_pending": _bool(row["context_handoff_preparation_pending"]),
                "context_handoff_preparation_status": row["context_handoff_preparation_status"],
                "last_agent_handoff_summary": row["last_agent_handoff_summary"],
                "last_agent_handoff_at": row["last_agent_handoff_at"],
                "last_context_rotated_at": row["last_context_rotated_at"],
                "last_thread_id": row["last_thread_id"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_agent_runtimes(connection: sqlite3.Connection, runtimes: dict[str, Any]) -> None:
        for runtime in runtimes.values():
            connection.execute(
                """
                INSERT INTO team_agent_runtimes(
                    agent_id, wake_leased_until, active_dispatch_id, wake_reason,
                    last_wake_at, next_wake_at, no_progress_count, last_progress_at,
                    low_priority_batch_started_at, low_priority_batch_cycle_count,
                    low_priority_batch_due_after_cycles,
                    context_clearing_policy,
                    auto_context_clearing_enabled, context_rotation_pending,
                    context_rotation_requested_by, context_rotation_requested_at,
                    context_rotation_reason, context_rotation_request_note,
                    last_handoff_summary, context_handoff_preparation_pending,
                    context_handoff_preparation_status, last_agent_handoff_summary,
                    last_agent_handoff_at, last_context_rotated_at, last_thread_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    runtime["agent_id"],
                    runtime.get("wake_leased_until"),
                    runtime.get("active_dispatch_id"),
                    runtime.get("wake_reason"),
                    runtime.get("last_wake_at"),
                    runtime.get("next_wake_at"),
                    int(runtime.get("no_progress_count", 0)),
                    runtime.get("last_progress_at"),
                    runtime.get("low_priority_batch_started_at"),
                    int(runtime.get("low_priority_batch_cycle_count", 0)),
                    runtime.get("low_priority_batch_due_after_cycles"),
                    runtime.get("context_clearing_policy", "off"),
                    _int_bool(runtime.get("auto_context_clearing_enabled")),
                    _int_bool(runtime.get("context_rotation_pending")),
                    runtime.get("context_rotation_requested_by"),
                    runtime.get("context_rotation_requested_at"),
                    runtime.get("context_rotation_reason"),
                    runtime.get("context_rotation_request_note"),
                    runtime.get("last_handoff_summary"),
                    _int_bool(runtime.get("context_handoff_preparation_pending")),
                    runtime.get("context_handoff_preparation_status"),
                    runtime.get("last_agent_handoff_summary"),
                    runtime.get("last_agent_handoff_at"),
                    runtime.get("last_context_rotated_at"),
                    runtime.get("last_thread_id"),
                    runtime["updated_at"],
                ),
            )

    @staticmethod
    def _read_cases(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_cases ORDER BY id"):
            payload["cases"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "title": row["title"],
                "description": row["description"],
                "status": row["status"],
                "priority": row["priority"],
                "owner_agent_id": row["owner_agent_id"],
                "source": row["source"],
                "source_ref": row["source_ref"],
                "tags": _json_loads(row["tags_json"]),
                "metadata": _json_loads(row["metadata_json"]),
                "task_ids": _json_loads(row["task_ids_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "closed_at": row["closed_at"],
            }

    @staticmethod
    def _write_cases(connection: sqlite3.Connection, cases: dict[str, Any]) -> None:
        for case in cases.values():
            connection.execute(
                """
                INSERT INTO team_cases(
                    id, team_id, project_id, title, description, status, priority,
                    owner_agent_id, source, source_ref, tags_json, metadata_json,
                    task_ids_json, created_at, updated_at, closed_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case["id"],
                    case["team_id"],
                    case.get("project_id"),
                    case["title"],
                    case.get("description", ""),
                    case.get("status", "open"),
                    case.get("priority", "normal"),
                    case.get("owner_agent_id"),
                    case.get("source"),
                    case.get("source_ref"),
                    _json_dumps(case.get("tags", [])),
                    _json_dumps(case.get("metadata", {})),
                    _json_dumps(case.get("task_ids", [])),
                    case["created_at"],
                    case["updated_at"],
                    case.get("closed_at"),
                ),
            )

    @staticmethod
    def _read_tasks(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        dependencies = _grouped_values(
            connection,
            "SELECT task_id AS owner_id, dependency_task_id AS value FROM team_task_dependencies ORDER BY task_id, position",
        )
        deliverables = _grouped_values(
            connection,
            "SELECT task_id AS owner_id, deliverable_id AS value FROM team_task_deliverables ORDER BY task_id, position",
        )
        for row in connection.execute("SELECT * FROM team_tasks ORDER BY id"):
            payload["tasks"][row["id"]] = {
                "id": row["id"],
                "owner_agent_id": row["owner_agent_id"],
                "project_id": row["project_id"],
                "case_id": row["case_id"],
                "priority": row["priority"],
                "state": row["state"],
                "task_kind": row["task_kind"],
                "outcome_contract": (
                    _json_loads(row["outcome_contract_json"])
                    if row["outcome_contract_json"] is not None
                    else None
                ),
                "integration_boundary": _bool(row["integration_boundary"]),
                "deliverable_contract": (
                    _json_loads(row["deliverable_contract_json"])
                    if row["deliverable_contract_json"] is not None
                    else None
                ),
                "completion": (
                    _json_loads(row["completion_json"])
                    if row["completion_json"] is not None
                    else None
                ),
                "state_entered_at": row["state_entered_at"]
                or row["updated_at"]
                or row["created_at"],
                "title": row["title"],
                "description": row["description"],
                "created_by_agent_id": row["created_by_agent_id"],
                "created_by_actor": row["created_by_actor"],
                "parent_task_id": row["parent_task_id"],
                "originating_task_id": row["originating_task_id"],
                "cancellation": (
                    _json_loads(row["cancellation_json"])
                    if row["cancellation_json"] is not None
                    else None
                ),
                "dependency_task_ids": dependencies.get(row["id"], []),
                "deliverable_ids": deliverables.get(row["id"], []),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_tasks(connection: sqlite3.Connection, tasks: dict[str, Any]) -> None:
        for task in tasks.values():
            connection.execute(
                """
                INSERT INTO team_tasks(
                    id, owner_agent_id, project_id, case_id, priority, state, title, description,
                    state_entered_at, created_by_agent_id, created_by_actor, parent_task_id,
                    originating_task_id, cancellation_json, task_kind,
                    outcome_contract_json, integration_boundary,
                    deliverable_contract_json, completion_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task["id"],
                    task["owner_agent_id"],
                    task["project_id"],
                    task.get("case_id"),
                    int(task["priority"]),
                    task.get("state", "pending"),
                    task["title"],
                    task["description"],
                    task.get("state_entered_at") or task.get("updated_at") or task["created_at"],
                    task.get("created_by_agent_id"),
                    task.get("created_by_actor"),
                    task.get("parent_task_id"),
                    task.get("originating_task_id"),
                    (
                        _json_dumps(task.get("cancellation"))
                        if task.get("cancellation") is not None
                        else None
                    ),
                    task.get("task_kind", "support"),
                    (
                        _json_dumps(task.get("outcome_contract"))
                        if task.get("outcome_contract") is not None
                        else None
                    ),
                    _int_bool(task.get("integration_boundary")),
                    (
                        _json_dumps(task.get("deliverable_contract"))
                        if task.get("deliverable_contract") is not None
                        else None
                    ),
                    (
                        _json_dumps(task.get("completion"))
                        if task.get("completion") is not None
                        else None
                    ),
                    task["created_at"],
                    task["updated_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_task_dependencies",
                "task_id",
                task["id"],
                "dependency_task_id",
                task.get("dependency_task_ids", []),
            )
            _insert_position_values(
                connection,
                "team_task_deliverables",
                "task_id",
                task["id"],
                "deliverable_id",
                task.get("deliverable_ids", []),
            )

    @staticmethod
    def _read_deliverables(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        artifacts = _grouped_values(
            connection,
            "SELECT deliverable_id AS owner_id, artifact_ref AS value FROM team_deliverable_artifacts ORDER BY deliverable_id, position",
        )
        reviews = _grouped_values(
            connection,
            "SELECT deliverable_id AS owner_id, review_request_id AS value FROM team_deliverable_reviews ORDER BY deliverable_id, position",
        )
        promotions = _grouped_values(
            connection,
            "SELECT deliverable_id AS owner_id, promotion_record_id AS value FROM team_deliverable_promotion_records ORDER BY deliverable_id, position",
        )
        sources = _grouped_values(
            connection,
            "SELECT deliverable_id AS owner_id, source_deliverable_id AS value "
            "FROM team_deliverable_sources ORDER BY deliverable_id, position",
        )
        for row in connection.execute("SELECT * FROM team_deliverables ORDER BY id"):
            payload["deliverables"][row["id"]] = {
                "id": row["id"],
                "task_id": row["task_id"],
                "producer_agent_id": row["producer_agent_id"],
                "artifact_refs": artifacts.get(row["id"], []),
                "summary": row["summary"],
                "state": row["state"],
                "review_request_ids": reviews.get(row["id"], []),
                "git_refs": {
                    "branch_context_id": row["branch_context_id"],
                    "agent_branch": row["agent_branch"],
                    "review_branch": row["review_branch"],
                    "feature_branch": row["feature_branch"],
                    "release_branch": row["release_branch"],
                    "commit_sha": row["commit_sha"],
                    "submitted_commit_sha": row["submitted_commit_sha"],
                    "reviewed_commit_sha": row["reviewed_commit_sha"],
                    "feature_commit_sha": row["feature_commit_sha"],
                    "release_commit_sha": row["release_commit_sha"],
                    "promotion_record_ids": promotions.get(row["id"], []),
                },
                "verification": (
                    _json_loads(row["verification_json"])
                    if row["verification_json"] is not None
                    else None
                ),
                "outcomes": _json_loads(row["outcomes_json"]),
                "source_deliverable_ids": sources.get(row["id"], []),
                "completion": (
                    _json_loads(row["completion_json"])
                    if row["completion_json"] is not None
                    else None
                ),
                "carried_findings": _json_loads(row["carried_findings_json"]),
                "review_manifest": (
                    _json_loads(row["review_manifest_json"])
                    if row["review_manifest_json"] is not None
                    else None
                ),
                "acceptance_recipient_agent_id": row["acceptance_recipient_agent_id"],
                "acceptance_decision": (
                    _json_loads(row["acceptance_decision_json"])
                    if row["acceptance_decision_json"] is not None
                    else None
                ),
                "supersedes_deliverable_id": row["supersedes_deliverable_id"],
                "superseded_by_deliverable_id": row["superseded_by_deliverable_id"],
                "supersession_reason": row["supersession_reason"],
                "superseded_at": row["superseded_at"],
                "superseded_by_actor": row["superseded_by_actor"],
                "review_cohort_id": row["review_cohort_id"],
                "review_cohort_sealed_at": row["review_cohort_sealed_at"],
                "rework_requested_at": row["rework_requested_at"],
                "rework_requested_by_agent_id": row[
                    "rework_requested_by_agent_id"
                ],
                "rework_reason": row["rework_reason"],
                "created_at": row["created_at"],
            }

    @staticmethod
    def _write_deliverables(connection: sqlite3.Connection, deliverables: dict[str, Any]) -> None:
        for deliverable in deliverables.values():
            git_refs = deliverable.get("git_refs", {})
            connection.execute(
                """
                INSERT INTO team_deliverables(
                    id, task_id, producer_agent_id, summary, state, branch_context_id,
                    agent_branch, review_branch, feature_branch, release_branch, commit_sha,
                    submitted_commit_sha, reviewed_commit_sha, feature_commit_sha,
                    release_commit_sha, verification_json, supersedes_deliverable_id,
                    superseded_by_deliverable_id, carried_findings_json,
                    review_manifest_json, acceptance_recipient_agent_id,
                    acceptance_decision_json, supersession_reason, superseded_at,
                    superseded_by_actor, review_cohort_id,
                    review_cohort_sealed_at, rework_requested_at,
                    rework_requested_by_agent_id, rework_reason, outcomes_json,
                    completion_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    deliverable["id"],
                    deliverable["task_id"],
                    deliverable["producer_agent_id"],
                    deliverable["summary"],
                    deliverable.get("state", "pending"),
                    git_refs.get("branch_context_id"),
                    git_refs.get("agent_branch"),
                    git_refs.get("review_branch"),
                    git_refs.get("feature_branch"),
                    git_refs.get("release_branch"),
                    git_refs.get("commit_sha"),
                    git_refs.get("submitted_commit_sha"),
                    git_refs.get("reviewed_commit_sha"),
                    git_refs.get("feature_commit_sha"),
                    git_refs.get("release_commit_sha"),
                    (
                        _json_dumps(deliverable.get("verification"))
                        if deliverable.get("verification") is not None
                        else None
                    ),
                    deliverable.get("supersedes_deliverable_id"),
                    deliverable.get("superseded_by_deliverable_id"),
                    _json_dumps(deliverable.get("carried_findings", [])),
                    (
                        _json_dumps(deliverable.get("review_manifest"))
                        if deliverable.get("review_manifest") is not None
                        else None
                    ),
                    deliverable.get("acceptance_recipient_agent_id"),
                    (
                        _json_dumps(deliverable.get("acceptance_decision"))
                        if deliverable.get("acceptance_decision") is not None
                        else None
                    ),
                    deliverable.get("supersession_reason"),
                    deliverable.get("superseded_at"),
                    deliverable.get("superseded_by_actor"),
                    deliverable.get("review_cohort_id"),
                    deliverable.get("review_cohort_sealed_at"),
                    deliverable.get("rework_requested_at"),
                    deliverable.get("rework_requested_by_agent_id"),
                    deliverable.get("rework_reason"),
                    _json_dumps(deliverable.get("outcomes", [])),
                    (
                        _json_dumps(deliverable.get("completion"))
                        if deliverable.get("completion") is not None
                        else None
                    ),
                    deliverable["created_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_deliverable_artifacts",
                "deliverable_id",
                deliverable["id"],
                "artifact_ref",
                deliverable.get("artifact_refs", []),
            )
            _insert_position_values(
                connection,
                "team_deliverable_reviews",
                "deliverable_id",
                deliverable["id"],
                "review_request_id",
                deliverable.get("review_request_ids", []),
            )
            _insert_position_values(
                connection,
                "team_deliverable_promotion_records",
                "deliverable_id",
                deliverable["id"],
                "promotion_record_id",
                git_refs.get("promotion_record_ids", []),
            )
            _insert_position_values(
                connection,
                "team_deliverable_sources",
                "deliverable_id",
                deliverable["id"],
                "source_deliverable_id",
                deliverable.get("source_deliverable_ids", []),
            )

    @staticmethod
    def _read_review_profiles(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        roles = _grouped_values(
            connection,
            "SELECT profile_id AS owner_id, role_id AS value FROM team_review_profile_roles ORDER BY profile_id, position",
        )
        criteria = _grouped_rows(
            connection,
            """
            SELECT profile_id AS owner_id, criterion_id, label, description, required
            FROM team_review_profile_criteria
            ORDER BY profile_id, position
            """,
            lambda row: {
                "id": row["criterion_id"],
                "label": row["label"],
                "description": row["description"],
                "required": _bool(row["required"]),
            },
        )
        for row in connection.execute("SELECT * FROM team_review_profiles ORDER BY id"):
            payload["review_profiles"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "name": row["name"],
                "description": row["description"],
                "applicable_role_ids": roles.get(row["id"], []),
                "criteria": criteria.get(row["id"], []),
                "required_outcome_kinds": _json_loads(row["required_outcome_kinds_json"]),
                "archived": _bool(row["archived"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _read_outcome_kind_definitions(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute("SELECT * FROM team_outcome_kind_definitions ORDER BY id"):
            payload["outcome_kind_definitions"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "kind": row["kind"],
                "name": row["name"],
                "description": row["description"],
                "reference_requirements": row["reference_requirements"],
                "evidence_requirements": row["evidence_requirements"],
                "archived": _bool(row["archived"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_outcome_kind_definitions(
        connection: sqlite3.Connection,
        definitions: dict[str, Any],
    ) -> None:
        for definition in definitions.values():
            connection.execute(
                """
                INSERT INTO team_outcome_kind_definitions(
                    id, team_id, project_id, kind, name, description,
                    reference_requirements, evidence_requirements, archived,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    definition["id"],
                    definition["team_id"],
                    definition.get("project_id"),
                    definition["kind"],
                    definition["name"],
                    definition.get("description", ""),
                    definition.get("reference_requirements", ""),
                    definition.get("evidence_requirements", ""),
                    _int_bool(definition.get("archived")),
                    definition["created_at"],
                    definition["updated_at"],
                ),
            )

    @staticmethod
    def _write_review_profiles(connection: sqlite3.Connection, profiles: dict[str, Any]) -> None:
        for profile in profiles.values():
            connection.execute(
                """
                INSERT INTO team_review_profiles(
                    id, team_id, project_id, name, description, archived,
                    required_outcome_kinds_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    profile["id"],
                    profile["team_id"],
                    profile.get("project_id"),
                    profile["name"],
                    profile.get("description", ""),
                    _int_bool(profile.get("archived")),
                    _json_dumps(profile.get("required_outcome_kinds", [])),
                    profile["created_at"],
                    profile["updated_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_review_profile_roles",
                "profile_id",
                profile["id"],
                "role_id",
                profile.get("applicable_role_ids", []),
            )
            for position, criterion in enumerate(profile.get("criteria", [])):
                connection.execute(
                    """
                    INSERT INTO team_review_profile_criteria(
                        profile_id, position, criterion_id, label, description, required
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        profile["id"],
                        position,
                        criterion["id"],
                        criterion["label"],
                        criterion.get("description", ""),
                        _int_bool(criterion.get("required", True)),
                    ),
                )

    @staticmethod
    def _read_review_requests(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        findings = _grouped_values(
            connection,
            "SELECT review_request_id AS owner_id, finding AS value FROM team_review_request_findings ORDER BY review_request_id, position",
        )
        criteria = _grouped_rows(
            connection,
            """
            SELECT review_request_id AS owner_id, criterion_id, state, finding
            FROM team_review_request_criteria
            ORDER BY review_request_id, position
            """,
            lambda row: {
                "criterion_id": row["criterion_id"],
                "state": row["state"],
                "finding": row["finding"],
            },
        )
        for row in connection.execute("SELECT * FROM team_review_requests ORDER BY id"):
            payload["review_requests"][row["id"]] = {
                "id": row["id"],
                "deliverable_id": row["deliverable_id"] or None,
                "release_package_id": row["release_package_id"],
                "candidate_commit_sha": row["candidate_commit_sha"],
                "reviewer_agent_id": row["reviewer_agent_id"],
                "requested_by_agent_id": row["requested_by_agent_id"],
                "requested_by_actor": row["requested_by_actor"],
                "state": row["state"],
                "findings": findings.get(row["id"], []),
                "review_profile_id": row["review_profile_id"],
                "criteria_snapshot": (
                    _json_loads(row["criteria_snapshot_json"])
                    if row["criteria_snapshot_json"] is not None
                    else None
                ),
                "required_outcome_kind_definitions": _json_loads(
                    row["required_outcome_kind_definitions_json"]
                ),
                "instructions": row["instructions"],
                "criteria": criteria.get(row["id"], []),
                "reviewed_commit_sha": row["reviewed_commit_sha"],
                "review_cohort_id": row["review_cohort_id"],
                "supersedes_review_request_id": row["supersedes_review_request_id"],
                "superseded_by_review_request_id": row["superseded_by_review_request_id"],
                "retry_reason": row["retry_reason"],
                "retry_requested_by": row["retry_requested_by"],
                "block_kind": row["block_kind"],
                "retired_at": row["retired_at"],
                "retired_by": row["retired_by"],
                "retirement_reason": row["retirement_reason"],
                "created_at": row["created_at"],
                "decision_at": row["decision_at"],
            }

    @staticmethod
    def _write_review_requests(connection: sqlite3.Connection, requests: dict[str, Any]) -> None:
        for request in requests.values():
            connection.execute(
                """
                INSERT INTO team_review_requests(
                    id, deliverable_id, release_package_id, candidate_commit_sha,
                    reviewer_agent_id, requested_by_agent_id, requested_by_actor,
                    state, review_profile_id, criteria_snapshot_json,
                    required_outcome_kind_definitions_json, instructions,
                    reviewed_commit_sha, review_cohort_id,
                    supersedes_review_request_id,
                    superseded_by_review_request_id, retry_reason,
                    retry_requested_by, block_kind, retired_at, retired_by,
                    retirement_reason, created_at, decision_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request["id"],
                    request.get("deliverable_id") or "",
                    request.get("release_package_id"),
                    request.get("candidate_commit_sha"),
                    request["reviewer_agent_id"],
                    request.get("requested_by_agent_id"),
                    request.get("requested_by_actor"),
                    request.get("state", "pending"),
                    request.get("review_profile_id"),
                    (
                        _json_dumps(request.get("criteria_snapshot"))
                        if request.get("criteria_snapshot") is not None
                        else None
                    ),
                    _json_dumps(request.get("required_outcome_kind_definitions", [])),
                    request.get("instructions"),
                    request.get("reviewed_commit_sha"),
                    request.get("review_cohort_id"),
                    request.get("supersedes_review_request_id"),
                    request.get("superseded_by_review_request_id"),
                    request.get("retry_reason"),
                    request.get("retry_requested_by"),
                    request.get("block_kind"),
                    request.get("retired_at"),
                    request.get("retired_by"),
                    request.get("retirement_reason"),
                    request.get("created_at"),
                    request.get("decision_at"),
                ),
            )
            _insert_position_values(
                connection,
                "team_review_request_findings",
                "review_request_id",
                request["id"],
                "finding",
                request.get("findings", []),
            )
            for position, criterion in enumerate(request.get("criteria", [])):
                connection.execute(
                    """
                    INSERT INTO team_review_request_criteria(
                        review_request_id, position, criterion_id, state, finding
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        request["id"],
                        position,
                        criterion["criterion_id"],
                        criterion["state"],
                        criterion.get("finding", ""),
                    ),
                )

    @staticmethod
    def _read_inbox(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_inbox ORDER BY id"):
            payload["inbox"][row["id"]] = {
                "id": row["id"],
                "recipient_agent_id": row["recipient_agent_id"],
                "item_type": row["item_type"],
                "sender_agent_id": row["sender_agent_id"],
                "task_id": row["task_id"],
                "response_requested": bool(row["response_requested"])
                if row["response_requested"] is not None
                else True,
                "priority": row["priority"] or "normal",
                "payload": _json_loads(row["payload_json"]),
                "state": row["state"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_inbox(connection: sqlite3.Connection, inbox: dict[str, Any]) -> None:
        for item in inbox.values():
            connection.execute(
                """
                INSERT INTO team_inbox(
                    id, recipient_agent_id, item_type, sender_agent_id, task_id,
                    response_requested, priority, payload_json, state, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["id"],
                    item["recipient_agent_id"],
                    item["item_type"],
                    item.get("sender_agent_id"),
                    item.get("task_id"),
                    1 if item.get("response_requested", True) else 0,
                    item.get("priority", "normal"),
                    _json_dumps(item.get("payload", {})),
                    item.get("state", "pending"),
                    item["created_at"],
                    item["updated_at"],
                ),
            )

    @staticmethod
    def _read_blockers(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        affected_tasks = _grouped_values(
            connection,
            "SELECT blocker_id AS owner_id, task_id AS value "
            "FROM team_blocker_affected_tasks ORDER BY blocker_id, position",
        )
        for row in connection.execute("SELECT * FROM team_blockers ORDER BY id"):
            payload["blockers"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "task_id": row["task_id"],
                "source_kind": row["source_kind"],
                "source_id": row["source_id"],
                "reporter_agent_id": row["reporter_agent_id"],
                "reporter_actor": row["reporter_actor"],
                "reason_code": row["reason_code"],
                "affected_task_ids": affected_tasks.get(row["id"], []),
                "owner_agent_id": row["owner_agent_id"],
                "delegate_agent_id": row["delegate_agent_id"],
                "state": row["state"],
                "route": row["route"],
                "summary": row["summary"],
                "details": row["details"],
                "priority": row["priority"],
                "escalation_depth": row["escalation_depth"],
                "active_dispatch_id": row["active_dispatch_id"],
                "follow_up_at": row["follow_up_at"],
                "last_progress_at": row["last_progress_at"],
                "terminal_reference_ids": _json_loads(row["terminal_reference_ids_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "resolved_at": row["resolved_at"],
            }

    @staticmethod
    def _write_blockers(connection: sqlite3.Connection, blockers: dict[str, Any]) -> None:
        for blocker in blockers.values():
            connection.execute(
                """
                INSERT INTO team_blockers(
                    id, team_id, project_id, task_id, source_kind, source_id,
                    reporter_agent_id, reporter_actor, reason_code,
                    owner_agent_id, delegate_agent_id, state, route,
                    summary, details, priority, escalation_depth, active_dispatch_id,
                    follow_up_at, last_progress_at, terminal_reference_ids_json,
                    created_at, updated_at, resolved_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    blocker["id"],
                    blocker["team_id"],
                    blocker.get("project_id"),
                    blocker.get("task_id"),
                    blocker["source_kind"],
                    blocker["source_id"],
                    blocker.get("reporter_agent_id"),
                    blocker.get("reporter_actor"),
                    blocker.get("reason_code", "unspecified"),
                    blocker.get("owner_agent_id"),
                    blocker.get("delegate_agent_id"),
                    blocker.get("state", "open"),
                    blocker.get("route", "owned"),
                    blocker["summary"],
                    blocker.get("details", ""),
                    blocker.get("priority", "normal"),
                    blocker.get("escalation_depth", 0),
                    blocker.get("active_dispatch_id"),
                    blocker.get("follow_up_at"),
                    blocker.get("last_progress_at"),
                    _json_dumps(blocker.get("terminal_reference_ids", [])),
                    blocker["created_at"],
                    blocker["updated_at"],
                    blocker.get("resolved_at"),
                ),
            )
            _insert_position_values(
                connection,
                "team_blocker_affected_tasks",
                "blocker_id",
                blocker["id"],
                "task_id",
                blocker.get("affected_task_ids", []),
            )

    @staticmethod
    def _read_blocker_events(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_blocker_events ORDER BY created_at, id"):
            payload["blocker_events"][row["id"]] = {
                "id": row["id"],
                "blocker_id": row["blocker_id"],
                "event_type": row["event_type"],
                "actor_agent_id": row["actor_agent_id"],
                "from_agent_id": row["from_agent_id"],
                "to_agent_id": row["to_agent_id"],
                "dispatch_id": row["dispatch_id"],
                "note": row["note"],
                "reference_ids": _json_loads(row["reference_ids_json"]),
                "metadata": _json_loads(row["metadata_json"]),
                "created_at": row["created_at"],
            }

    @staticmethod
    def _write_blocker_events(connection: sqlite3.Connection, events: dict[str, Any]) -> None:
        for event in events.values():
            connection.execute(
                """
                INSERT INTO team_blocker_events(
                    id, blocker_id, event_type, actor_agent_id, from_agent_id,
                    to_agent_id, dispatch_id, note, reference_ids_json,
                    metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event["id"],
                    event["blocker_id"],
                    event["event_type"],
                    event.get("actor_agent_id"),
                    event.get("from_agent_id"),
                    event.get("to_agent_id"),
                    event.get("dispatch_id"),
                    event.get("note", ""),
                    _json_dumps(event.get("reference_ids", [])),
                    _json_dumps(event.get("metadata", {})),
                    event["created_at"],
                ),
            )

    @staticmethod
    def _read_action_records(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_action_records ORDER BY id"):
            payload["action_records"][row["id"]] = {
                "id": row["id"],
                "agent_id": row["agent_id"],
                "dispatch_id": row["dispatch_id"],
                "action": _json_loads(row["action_json"]),
                "success": _bool(row["success"]),
                "error": row["error"],
                "created_at": row["created_at"],
                "dismissed_at": row["dismissed_at"],
                "dismissed_by": row["dismissed_by"],
                "dismissed_reason": row["dismissed_reason"],
                "feedback_delivered_at": row["feedback_delivered_at"],
                "feedback_delivered_by_dispatch_id": row["feedback_delivered_by_dispatch_id"],
            }

    @staticmethod
    def _write_action_records(connection: sqlite3.Connection, records: dict[str, Any]) -> None:
        for record in records.values():
            connection.execute(
                """
                INSERT INTO team_action_records(
                    id, agent_id, dispatch_id, action_json, success, error, created_at,
                    dismissed_at, dismissed_by, dismissed_reason,
                    feedback_delivered_at, feedback_delivered_by_dispatch_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["agent_id"],
                    record["dispatch_id"],
                    _json_dumps(record.get("action", {})),
                    _int_bool(record.get("success", True)),
                    record.get("error"),
                    record["created_at"],
                    record.get("dismissed_at"),
                    record.get("dismissed_by"),
                    record.get("dismissed_reason"),
                    record.get("feedback_delivered_at"),
                    record.get("feedback_delivered_by_dispatch_id"),
                ),
            )

    @staticmethod
    def _read_memories(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        tags = _grouped_values(
            connection,
            "SELECT memory_id AS owner_id, tag AS value FROM team_memory_tags ORDER BY memory_id, position",
        )
        source_refs = _grouped_values(
            connection,
            "SELECT memory_id AS owner_id, source_ref AS value FROM team_memory_source_refs ORDER BY memory_id, position",
        )
        artifact_refs = _grouped_values(
            connection,
            "SELECT memory_id AS owner_id, artifact_ref AS value FROM team_memory_artifact_refs ORDER BY memory_id, position",
        )
        for row in connection.execute("SELECT * FROM team_memories ORDER BY id"):
            payload["memories"][row["id"]] = {
                "id": row["id"],
                "team_id": row["team_id"],
                "project_id": row["project_id"],
                "codebase_id": row["codebase_id"],
                "agent_id": row["agent_id"],
                "scope": row["scope"],
                "kind": row["kind"],
                "title": row["title"],
                "summary": row["summary"],
                "body": row["body"],
                "tags": tags.get(row["id"], []),
                "source_refs": source_refs.get(row["id"], []),
                "artifact_refs": artifact_refs.get(row["id"], []),
                "status": row["status"],
                "confidence": row["confidence"],
                "supersedes_memory_id": row["supersedes_memory_id"],
                "superseded_by_memory_id": row["superseded_by_memory_id"],
                "created_by_agent_id": row["created_by_agent_id"],
                "created_by": row["created_by"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_memories(connection: sqlite3.Connection, memories: dict[str, Any]) -> None:
        for memory in memories.values():
            connection.execute(
                """
                INSERT INTO team_memories(
                    id, team_id, project_id, codebase_id, agent_id, scope, kind,
                    title, summary, body, status, confidence, supersedes_memory_id,
                    superseded_by_memory_id, created_by_agent_id, created_by,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory["id"],
                    memory["team_id"],
                    memory.get("project_id"),
                    memory.get("codebase_id"),
                    memory.get("agent_id"),
                    memory.get("scope", "team_global"),
                    memory.get("kind", "other"),
                    memory["title"],
                    memory["summary"],
                    memory.get("body"),
                    memory.get("status", "active"),
                    memory.get("confidence"),
                    memory.get("supersedes_memory_id"),
                    memory.get("superseded_by_memory_id"),
                    memory.get("created_by_agent_id"),
                    memory.get("created_by"),
                    memory["created_at"],
                    memory["updated_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_memory_tags",
                "memory_id",
                memory["id"],
                "tag",
                memory.get("tags", []),
            )
            _insert_position_values(
                connection,
                "team_memory_source_refs",
                "memory_id",
                memory["id"],
                "source_ref",
                memory.get("source_refs", []),
            )
            _insert_position_values(
                connection,
                "team_memory_artifact_refs",
                "memory_id",
                memory["id"],
                "artifact_ref",
                memory.get("artifact_refs", []),
            )

    @staticmethod
    def _read_conversations(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute("SELECT * FROM team_conversations ORDER BY id"):
            payload["conversations"][row["id"]] = {
                "id": row["id"],
                "agent_id": row["agent_id"],
                "operator_user_id": row["operator_user_id"],
                "operator_display_name": row["operator_display_name"],
                "created_by": row["created_by"],
                "last_message_preview": row["last_message_preview"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "last_interaction_at": row["last_interaction_at"],
            }

    @staticmethod
    def _write_conversations(
        connection: sqlite3.Connection,
        conversations: dict[str, Any],
    ) -> None:
        for conversation in conversations.values():
            connection.execute(
                """
                INSERT INTO team_conversations(
                    id, agent_id, operator_user_id, operator_display_name,
                    created_by, last_message_preview,
                    created_at, updated_at, last_interaction_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation["id"],
                    conversation["agent_id"],
                    conversation.get("operator_user_id") or "admin",
                    conversation.get("operator_display_name"),
                    conversation.get("created_by"),
                    conversation.get("last_message_preview"),
                    conversation["created_at"],
                    conversation["updated_at"],
                    conversation["last_interaction_at"],
                ),
            )

    @staticmethod
    def _read_conversation_messages(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute(
            "SELECT * FROM team_conversation_messages ORDER BY created_at, id"
        ):
            payload["conversation_messages"][row["id"]] = {
                "id": row["id"],
                "conversation_id": row["conversation_id"],
                "sender": row["sender"],
                "message_type": row["message_type"],
                "body": row["body"],
                "status": row["status"],
                "dispatch_id": row["dispatch_id"],
                "voice_session_id": row["voice_session_id"],
                "source_message_id": row["source_message_id"],
                "error": row["error"],
                "metadata": _json_loads(row["metadata_json"]),
                "attachment_ids": _json_loads(row["attachment_ids_json"] or "[]"),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_conversation_messages(
        connection: sqlite3.Connection,
        messages: dict[str, Any],
    ) -> None:
        for message in messages.values():
            connection.execute(
                """
                INSERT INTO team_conversation_messages(
                    id, conversation_id, sender, message_type, body, status,
                    dispatch_id, voice_session_id, source_message_id, error,
                    metadata_json, created_at, updated_at
                    , attachment_ids_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message["id"],
                    message["conversation_id"],
                    message["sender"],
                    message.get("message_type", "text"),
                    message.get("body"),
                    message.get("status", "sent"),
                    message.get("dispatch_id"),
                    message.get("voice_session_id"),
                    message.get("source_message_id"),
                    message.get("error"),
                    _json_dumps(message.get("metadata", {})),
                    message["created_at"],
                    message["updated_at"],
                    _json_dumps(message.get("attachment_ids", [])),
                ),
            )

    @staticmethod
    def _read_external_chat_ingress(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute(
            "SELECT * FROM team_external_chat_ingress ORDER BY created_at, id"
        ):
            payload["external_chat_ingress"][row["id"]] = {
                "id": row["id"],
                "provider": row["provider"],
                "communication_target_id": row["communication_target_id"],
                "source_message_id": row["source_message_id"],
                "source_thread_id": row["source_thread_id"],
                "source_author_id": row["source_author_id"],
                "source_author_name": row["source_author_name"],
                "operator_user_id": row["operator_user_id"],
                "conversation_id": row["conversation_id"],
                "message_id": row["message_id"],
                "dispatch_id": row["dispatch_id"],
                "allow_team_mutations": bool(row["allow_team_mutations"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_external_chat_ingress(
        connection: sqlite3.Connection,
        records: dict[str, Any],
    ) -> None:
        for record in records.values():
            connection.execute(
                """
                INSERT INTO team_external_chat_ingress(
                    id, provider, communication_target_id, source_message_id,
                    source_thread_id, source_author_id, source_author_name,
                    operator_user_id, conversation_id, message_id, dispatch_id,
                    allow_team_mutations, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["provider"],
                    record["communication_target_id"],
                    record["source_message_id"],
                    record.get("source_thread_id"),
                    record["source_author_id"],
                    record.get("source_author_name"),
                    record["operator_user_id"],
                    record["conversation_id"],
                    record["message_id"],
                    record["dispatch_id"],
                    int(bool(record.get("allow_team_mutations"))),
                    record["created_at"],
                    record["updated_at"],
                ),
            )

    @staticmethod
    def _read_external_chat_deliveries(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute(
            "SELECT * FROM team_external_chat_deliveries ORDER BY created_at, id"
        ):
            payload["external_chat_deliveries"][row["id"]] = {
                "id": row["id"],
                "agent_reply_message_id": row["agent_reply_message_id"],
                "operator_message_id": row["operator_message_id"],
                "provider": row["provider"],
                "communication_target_id": row["communication_target_id"],
                "source_thread_id": row["source_thread_id"],
                "status": row["status"],
                "provider_message_id": row["provider_message_id"],
                "attempt_count": row["attempt_count"],
                "last_error": row["last_error"],
                "attempted_at": row["attempted_at"],
                "delivered_at": row["delivered_at"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_external_chat_deliveries(
        connection: sqlite3.Connection,
        records: dict[str, Any],
    ) -> None:
        for record in records.values():
            connection.execute(
                """
                INSERT INTO team_external_chat_deliveries(
                    id, agent_reply_message_id, operator_message_id, provider,
                    communication_target_id, source_thread_id, status,
                    provider_message_id, attempt_count, last_error, attempted_at,
                    delivered_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["agent_reply_message_id"],
                    record["operator_message_id"],
                    record["provider"],
                    record["communication_target_id"],
                    record.get("source_thread_id"),
                    record.get("status", "pending"),
                    record.get("provider_message_id"),
                    record.get("attempt_count", 0),
                    record.get("last_error"),
                    record.get("attempted_at"),
                    record.get("delivered_at"),
                    record["created_at"],
                    record["updated_at"],
                ),
            )

    @staticmethod
    def _read_chat_attachments(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute(
            "SELECT * FROM team_chat_attachments ORDER BY created_at, id"
        ):
            payload["chat_attachments"][row["id"]] = {
                "id": row["id"],
                "message_id": row["message_id"],
                "conversation_id": row["conversation_id"],
                "filename": row["filename"],
                "mime_type": row["mime_type"],
                "byte_count": row["byte_count"],
                "sha256": row["sha256"],
                "sender": row["sender"],
                "created_by_session_id": row["created_by_session_id"],
                "created_at": row["created_at"],
            }

    @staticmethod
    def _write_chat_attachments(
        connection: sqlite3.Connection, attachments: dict[str, Any]
    ) -> None:
        for attachment in attachments.values():
            connection.execute(
                """
                INSERT INTO team_chat_attachments(
                    id, message_id, conversation_id, filename, mime_type, byte_count,
                    sha256, sender, created_by_session_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment["id"],
                    attachment["message_id"],
                    attachment["conversation_id"],
                    attachment["filename"],
                    attachment.get("mime_type"),
                    attachment["byte_count"],
                    attachment["sha256"],
                    attachment["sender"],
                    attachment.get("created_by_session_id"),
                    attachment["created_at"],
                ),
            )

    @staticmethod
    def _read_chat_attachment_policies(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        for row in connection.execute(
            "SELECT * FROM team_chat_attachment_policies ORDER BY directory_path"
        ):
            payload["chat_attachment_policies"][row["directory_path"]] = {
                "id": row["directory_path"],
                "directory_path": row["directory_path"],
                "operator_to_agent_enabled": _optional_db_bool(row["operator_to_agent_enabled"]),
                "agent_to_operator_enabled": _optional_db_bool(row["agent_to_operator_enabled"]),
                "agent_send_requires_approval": _optional_db_bool(
                    row["agent_send_requires_approval"]
                ),
                "max_attachment_bytes": row["max_attachment_bytes"],
                "max_attachments_per_message": row["max_attachments_per_message"],
                "allowed_mime_types": _json_loads(row["allowed_mime_types_json"])
                if row["allowed_mime_types_json"] is not None
                else None,
                "allowed_extensions": _json_loads(row["allowed_extensions_json"])
                if row["allowed_extensions_json"] is not None
                else None,
                "blocked_extensions": _json_loads(row["blocked_extensions_json"])
                if row["blocked_extensions_json"] is not None
                else None,
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_chat_attachment_policies(
        connection: sqlite3.Connection, policies: dict[str, Any]
    ) -> None:
        for policy in policies.values():
            connection.execute(
                """
                INSERT INTO team_chat_attachment_policies(
                    directory_path, operator_to_agent_enabled, agent_to_operator_enabled,
                    agent_send_requires_approval, max_attachment_bytes,
                    max_attachments_per_message, allowed_mime_types_json,
                    allowed_extensions_json, blocked_extensions_json, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    policy["directory_path"],
                    _optional_bool_db(policy.get("operator_to_agent_enabled")),
                    _optional_bool_db(policy.get("agent_to_operator_enabled")),
                    _optional_bool_db(policy.get("agent_send_requires_approval")),
                    policy.get("max_attachment_bytes"),
                    policy.get("max_attachments_per_message"),
                    _json_dumps(policy["allowed_mime_types"])
                    if policy.get("allowed_mime_types") is not None
                    else None,
                    _json_dumps(policy["allowed_extensions"])
                    if policy.get("allowed_extensions") is not None
                    else None,
                    _json_dumps(policy["blocked_extensions"])
                    if policy.get("blocked_extensions") is not None
                    else None,
                    policy.get("updated_by"),
                    policy["updated_at"],
                ),
            )

    @staticmethod
    def _read_release_packages(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        deliverables = _grouped_values(
            connection,
            "SELECT package_id AS owner_id, deliverable_id AS value FROM team_release_deliverables ORDER BY package_id, position",
        )
        artifacts = _grouped_values(
            connection,
            "SELECT package_id AS owner_id, artifact_ref AS value FROM team_release_artifacts ORDER BY package_id, position",
        )
        branches = _grouped_values(
            connection,
            "SELECT package_id AS owner_id, feature_branch AS value FROM team_release_feature_branches ORDER BY package_id, position",
        )
        promotions = _grouped_values(
            connection,
            "SELECT package_id AS owner_id, promotion_record_id AS value FROM team_release_git_promotions ORDER BY package_id, position",
        )
        for row in connection.execute("SELECT * FROM team_release_packages ORDER BY id"):
            payload["release_packages"][row["id"]] = {
                "id": row["id"],
                "project_id": row["project_id"],
                "owner_agent_id": row["owner_agent_id"],
                "summary": row["summary"],
                "supersedes_release_package_id": row["supersedes_release_package_id"],
                "deliverable_ids": deliverables.get(row["id"], []),
                "artifact_refs": artifacts.get(row["id"], []),
                "feature_branches": branches.get(row["id"], []),
                "release_branch": row["release_branch"],
                "canonical_branch": row["canonical_branch"],
                "published_commit_sha": row["published_commit_sha"],
                "base_commit_sha": row["base_commit_sha"],
                "candidate_commit_sha": row["candidate_commit_sha"],
                "candidate_tree_hash": row["candidate_tree_hash"],
                "approved_commit_sha": row["approved_commit_sha"],
                "review_correction_task_id": row["review_correction_task_id"],
                "verification_run_ids": _json_loads(row["verification_run_ids_json"]),
                "review_request_ids": _json_loads(row["review_request_ids_json"]),
                "manifest": _json_loads(row["manifest_json"]),
                "gate_failure": (
                    _json_loads(row["gate_failure_json"])
                    if row["gate_failure_json"] is not None
                    else None
                ),
                "git_promotion_record_ids": promotions.get(row["id"], []),
                "state": row["state"],
                "published_at": row["published_at"],
                "error": row["error"],
                "policy_version": row["policy_version"],
                "verification_waivers": _json_loads(row["verification_waivers_json"]),
                "scope_validation": (
                    _json_loads(row["scope_validation_json"])
                    if row["scope_validation_json"] is not None
                    else None
                ),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_release_packages(connection: sqlite3.Connection, packages: dict[str, Any]) -> None:
        for package in packages.values():
            connection.execute(
                """
                INSERT INTO team_release_packages(
                    id, project_id, owner_agent_id, summary, supersedes_release_package_id,
                    release_branch, canonical_branch, published_commit_sha,
                    base_commit_sha, candidate_commit_sha, candidate_tree_hash,
                    approved_commit_sha, review_correction_task_id,
                    verification_run_ids_json, review_request_ids_json,
                    manifest_json, gate_failure_json,
                    state, published_at, error, policy_version,
                    verification_waivers_json, scope_validation_json, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    package["id"],
                    package["project_id"],
                    package.get("owner_agent_id"),
                    package.get("summary"),
                    package.get("supersedes_release_package_id"),
                    package.get("release_branch"),
                    package.get("canonical_branch"),
                    package.get("published_commit_sha"),
                    package.get("base_commit_sha"),
                    package.get("candidate_commit_sha"),
                    package.get("candidate_tree_hash"),
                    package.get("approved_commit_sha"),
                    package.get("review_correction_task_id"),
                    _json_dumps(package.get("verification_run_ids", [])),
                    _json_dumps(package.get("review_request_ids", [])),
                    _json_dumps(package.get("manifest") or {}),
                    (
                        _json_dumps(package.get("gate_failure"))
                        if package.get("gate_failure") is not None
                        else None
                    ),
                    package.get("state", "pending"),
                    package.get("published_at"),
                    package.get("error"),
                    package.get("policy_version", 1),
                    _json_dumps(package.get("verification_waivers", [])),
                    (
                        _json_dumps(package.get("scope_validation"))
                        if package.get("scope_validation") is not None
                        else None
                    ),
                    package["created_at"],
                    package.get("updated_at"),
                ),
            )
            _insert_position_values(
                connection,
                "team_release_deliverables",
                "package_id",
                package["id"],
                "deliverable_id",
                package.get("deliverable_ids", []),
            )
            _insert_position_values(
                connection,
                "team_release_artifacts",
                "package_id",
                package["id"],
                "artifact_ref",
                package.get("artifact_refs", []),
            )
            _insert_position_values(
                connection,
                "team_release_feature_branches",
                "package_id",
                package["id"],
                "feature_branch",
                package.get("feature_branches", []),
            )
            _insert_position_values(
                connection,
                "team_release_git_promotions",
                "package_id",
                package["id"],
                "promotion_record_id",
                package.get("git_promotion_record_ids", []),
            )

    @staticmethod
    def _read_release_verification_runs(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        for row in connection.execute(
            "SELECT id, payload_json FROM team_release_verification_runs ORDER BY id"
        ):
            payload["release_verification_runs"][row["id"]] = _json_loads(row["payload_json"])

    @staticmethod
    def _write_release_verification_runs(
        connection: sqlite3.Connection, runs: dict[str, Any]
    ) -> None:
        for run in runs.values():
            connection.execute(
                "INSERT INTO team_release_verification_runs(id, payload_json) VALUES (?, ?)",
                (run["id"], _json_dumps(run)),
            )

    @staticmethod
    def _read_human_reviews(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        request_configured_groups = _grouped_values(
            connection,
            (
                "SELECT human_review_request_id AS owner_id, group_id AS value "
                "FROM team_human_review_request_configured_groups "
                "ORDER BY human_review_request_id, position"
            ),
        )
        request_initial_users = _grouped_values(
            connection,
            (
                "SELECT human_review_request_id AS owner_id, user_id AS value "
                "FROM team_human_review_request_initial_users "
                "ORDER BY human_review_request_id, position"
            ),
        )
        request_current_users = _grouped_values(
            connection,
            (
                "SELECT human_review_request_id AS owner_id, user_id AS value "
                "FROM team_human_review_request_current_users "
                "ORDER BY human_review_request_id, position"
            ),
        )
        request_explicit_users = _grouped_values(
            connection,
            (
                "SELECT human_review_request_id AS owner_id, user_id AS value "
                "FROM team_human_review_request_explicit_users "
                "ORDER BY human_review_request_id, position"
            ),
        )
        for row in connection.execute("SELECT * FROM team_human_review_requests ORDER BY id"):
            request = _json_loads(row["payload_json"])
            requirement = dict(request.get("requirement_snapshot") or {})
            requirement["configured_group_ids"] = request_configured_groups.get(row["id"], [])
            requirement["initially_eligible_user_ids"] = request_initial_users.get(row["id"], [])
            request["requirement_snapshot"] = requirement
            assignment = dict(request.get("current_assignment") or {})
            assignment["eligible_user_ids"] = request_current_users.get(row["id"], [])
            assignment["explicitly_assigned_user_ids"] = request_explicit_users.get(row["id"], [])
            request["current_assignment"] = assignment
            request.update(
                {
                    "release_package_id": row["release_package_id"],
                    "state": row["state"],
                    "candidate_commit_sha": row["candidate_commit_sha"],
                    "candidate_tree_hash": row["candidate_tree_hash"],
                    "assignment_generation": row["assignment_generation"],
                    "preparation_generation": row["preparation_generation"],
                }
            )
            payload["human_review_requests"][row["id"]] = request

        decision_confirmed_findings = _grouped_values(
            connection,
            (
                "SELECT human_review_decision_id AS owner_id, finding_id AS value "
                "FROM team_human_review_decision_confirmed_findings "
                "ORDER BY human_review_decision_id, position"
            ),
        )
        for row in connection.execute("SELECT * FROM team_human_review_decisions ORDER BY id"):
            decision = _json_loads(row["payload_json"])
            decision.update(
                {
                    "human_review_request_id": row["human_review_request_id"],
                    "reviewer_user_id": row["reviewer_user_id"],
                    "decision": row["decision"],
                    "request_id": row["request_id"],
                    "decided_at": row["decided_at"],
                    "confirmed_finding_ids": decision_confirmed_findings.get(row["id"], []),
                }
            )
            payload["human_review_decisions"][row["id"]] = decision

        finding_targets = _grouped_values(
            connection,
            (
                "SELECT finding_id AS owner_id, deliverable_id AS value "
                "FROM team_human_review_finding_targets "
                "ORDER BY finding_id, position"
            ),
        )
        finding_proposals = _grouped_values(
            connection,
            (
                "SELECT finding_id AS owner_id, deliverable_id AS value "
                "FROM team_human_review_finding_proposals "
                "ORDER BY finding_id, position"
            ),
        )
        for row in connection.execute("SELECT * FROM team_human_review_findings ORDER BY id"):
            finding = _json_loads(row["payload_json"])
            finding.update(
                {
                    "source_human_review_request_id": row["source_human_review_request_id"],
                    "correction_task_id": row["correction_task_id"],
                    "state": row["state"],
                    "resolved_by_human_review_request_id": row[
                        "resolved_by_human_review_request_id"
                    ],
                }
            )
            finding["target_deliverable_ids"] = finding_targets.get(row["id"], [])
            finding["proposed_by_deliverable_ids"] = finding_proposals.get(
                row["id"],
                [],
            )
            payload["human_review_findings"][row["id"]] = finding

    @staticmethod
    def _write_human_reviews(
        connection: sqlite3.Connection,
        *,
        requests: dict[str, Any],
        decisions: dict[str, Any],
        findings: dict[str, Any],
    ) -> None:
        for request in requests.values():
            connection.execute(
                """
                INSERT INTO team_human_review_requests(
                    id, release_package_id, state, candidate_commit_sha,
                    candidate_tree_hash, assignment_generation,
                    preparation_generation, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request["id"],
                    request["release_package_id"],
                    request["state"],
                    request["candidate_commit_sha"],
                    request["candidate_tree_hash"],
                    request.get("assignment_generation", 1),
                    request.get("preparation_generation", 1),
                    _json_dumps(request),
                ),
            )
            requirement = request["requirement_snapshot"]
            assignment = request.get("current_assignment") or {}
            for table_name, value_column, values in (
                (
                    "team_human_review_request_configured_groups",
                    "group_id",
                    requirement.get("configured_group_ids", []),
                ),
                (
                    "team_human_review_request_initial_users",
                    "user_id",
                    requirement.get("initially_eligible_user_ids", []),
                ),
                (
                    "team_human_review_request_current_users",
                    "user_id",
                    assignment.get("eligible_user_ids", []),
                ),
                (
                    "team_human_review_request_explicit_users",
                    "user_id",
                    assignment.get("explicitly_assigned_user_ids", []),
                ),
            ):
                _insert_position_values(
                    connection,
                    table_name,
                    "human_review_request_id",
                    request["id"],
                    value_column,
                    values,
                )
        for decision in decisions.values():
            connection.execute(
                """
                INSERT INTO team_human_review_decisions(
                    id, human_review_request_id, reviewer_user_id,
                    decision, request_id, decided_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision["id"],
                    decision["human_review_request_id"],
                    decision["reviewer_user_id"],
                    decision["decision"],
                    decision["request_id"],
                    decision["decided_at"],
                    _json_dumps(decision),
                ),
            )
            _insert_position_values(
                connection,
                "team_human_review_decision_confirmed_findings",
                "human_review_decision_id",
                decision["id"],
                "finding_id",
                decision.get("confirmed_finding_ids", []),
            )
        for finding in findings.values():
            connection.execute(
                """
                INSERT INTO team_human_review_findings(
                    id, source_human_review_request_id, correction_task_id,
                    state, resolved_by_human_review_request_id, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    finding["id"],
                    finding["source_human_review_request_id"],
                    finding["correction_task_id"],
                    finding["state"],
                    finding.get("resolved_by_human_review_request_id"),
                    _json_dumps(finding),
                ),
            )
            _insert_position_values(
                connection,
                "team_human_review_finding_targets",
                "finding_id",
                finding["id"],
                "deliverable_id",
                finding.get("target_deliverable_ids", []),
            )
            _insert_position_values(
                connection,
                "team_human_review_finding_proposals",
                "finding_id",
                finding["id"],
                "deliverable_id",
                finding.get("proposed_by_deliverable_ids", []),
            )

    @staticmethod
    def _read_dispatches(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        scope_values: dict[str, dict[str, list[str]]] = {}
        for field_name, table_name, value_column in (
            ("task_ids", "team_dispatch_tasks", "task_id"),
            ("blocker_ids", "team_dispatch_blockers", "blocker_id"),
            ("review_request_ids", "team_dispatch_reviews", "review_request_id"),
            ("inbox_item_ids", "team_dispatch_inbox", "inbox_item_id"),
            ("project_ids", "team_dispatch_projects", "project_id"),
            ("managed_repo_ids", "team_dispatch_repositories", "managed_repo_id"),
        ):
            scope_values[field_name] = _grouped_values(
                connection,
                (
                    f"SELECT dispatch_id AS owner_id, {value_column} AS value "
                    f"FROM {table_name} ORDER BY dispatch_id, position"
                ),
            )
        for row in connection.execute("SELECT * FROM team_dispatches ORDER BY id"):
            dispatch_id = row["id"]
            payload["dispatches"][dispatch_id] = {
                "id": dispatch_id,
                "agent_id": row["agent_id"],
                "session_id": row["session_id"],
                "cause_kind": row["cause_kind"],
                "cause_id": row["cause_id"],
                "task_ids": scope_values["task_ids"].get(dispatch_id, []),
                "blocker_ids": scope_values["blocker_ids"].get(dispatch_id, []),
                "review_request_ids": scope_values["review_request_ids"].get(dispatch_id, []),
                "inbox_item_ids": scope_values["inbox_item_ids"].get(dispatch_id, []),
                "project_ids": scope_values["project_ids"].get(dispatch_id, []),
                "managed_repo_ids": scope_values["managed_repo_ids"].get(dispatch_id, []),
                "review_routing_authorities": _json_loads(
                    row["review_routing_authorities_json"] or "[]"
                ),
                "status": row["status"],
                "created_at": row["created_at"],
                "prepared_at": row["prepared_at"],
                "leased_at": row["leased_at"],
                "lease_expires_at": row["lease_expires_at"],
                "completed_at": row["completed_at"],
                "error": row["error"],
            }

    @staticmethod
    def _write_dispatches(
        connection: sqlite3.Connection,
        dispatches: dict[str, Any],
    ) -> None:
        for dispatch in dispatches.values():
            connection.execute(
                """
                INSERT INTO team_dispatches(
                    id, agent_id, session_id, cause_kind, cause_id,
                    review_routing_authorities_json, status,
                    created_at, prepared_at, leased_at, lease_expires_at,
                    completed_at, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dispatch["id"],
                    dispatch["agent_id"],
                    dispatch["session_id"],
                    dispatch["cause_kind"],
                    dispatch.get("cause_id"),
                    _json_dumps(
                        dispatch.get("review_routing_authorities", [])
                    ),
                    dispatch["status"],
                    dispatch["created_at"],
                    dispatch.get("prepared_at"),
                    dispatch.get("leased_at"),
                    dispatch.get("lease_expires_at"),
                    dispatch.get("completed_at"),
                    dispatch.get("error"),
                ),
            )
            for field_name, table_name, value_column in (
                ("task_ids", "team_dispatch_tasks", "task_id"),
                ("blocker_ids", "team_dispatch_blockers", "blocker_id"),
                ("review_request_ids", "team_dispatch_reviews", "review_request_id"),
                ("inbox_item_ids", "team_dispatch_inbox", "inbox_item_id"),
                ("project_ids", "team_dispatch_projects", "project_id"),
                (
                    "managed_repo_ids",
                    "team_dispatch_repositories",
                    "managed_repo_id",
                ),
            ):
                _insert_position_values(
                    connection,
                    table_name,
                    "dispatch_id",
                    dispatch["id"],
                    value_column,
                    dispatch.get(field_name, []),
                )

    @staticmethod
    def _read_worktree_cleanup_candidates(
        connection: sqlite3.Connection,
        payload: dict[str, Any],
    ) -> None:
        for row in connection.execute(
            "SELECT id, payload_json FROM team_worktree_cleanup_candidates ORDER BY id"
        ):
            payload["worktree_cleanup_candidates"][row["id"]] = _json_loads(row["payload_json"])

    @staticmethod
    def _write_worktree_cleanup_candidates(
        connection: sqlite3.Connection,
        candidates: dict[str, Any],
    ) -> None:
        for candidate in candidates.values():
            connection.execute(
                """
                INSERT INTO team_worktree_cleanup_candidates(
                    id, agent_id, session_id, task_id, project_id,
                    managed_repo_id, worktree_path, state, eligible_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate["id"],
                    candidate["agent_id"],
                    candidate["session_id"],
                    candidate["task_id"],
                    candidate["project_id"],
                    candidate["managed_repo_id"],
                    candidate["worktree_path"],
                    candidate["state"],
                    candidate["eligible_at"],
                    _json_dumps(candidate),
                ),
            )

    @staticmethod
    def _read_ephemeral_assignments(
        connection: sqlite3.Connection, payload: dict[str, Any]
    ) -> None:
        artifacts = _grouped_values(
            connection,
            "SELECT assignment_id AS owner_id, artifact_ref AS value FROM team_ephemeral_assignment_artifacts ORDER BY assignment_id, position",
        )
        tools = _grouped_values(
            connection,
            "SELECT assignment_id AS owner_id, tool AS value FROM team_ephemeral_assignment_allowed_tools ORDER BY assignment_id, position",
        )
        for row in connection.execute("SELECT * FROM team_ephemeral_assignments ORDER BY id"):
            payload["ephemeral_assignments"][row["id"]] = {
                "id": row["id"],
                "parent_agent_id": row["parent_agent_id"],
                "project_id": row["project_id"],
                "task_id": row["task_id"],
                "session_id": row["session_id"],
                "title": row["title"],
                "prompt": row["prompt"],
                "state": row["state"],
                "artifact_refs": artifacts.get(row["id"], []),
                "agent_branch": row["agent_branch"],
                "commit_sha": row["commit_sha"],
                "summary": row["summary"],
                "error": row["error"],
                "model": row["model"],
                "agent_provider": row["agent_provider"],
                "execution_backend": row["execution_backend"],
                "container_image": row["container_image"],
                "allowed_tools": tools.get(row["id"], []),
                "ttl_hours": row["ttl_hours"],
                "directory_path": row["directory_path"],
                "metadata": _json_loads(row["metadata_json"]),
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_ephemeral_assignments(
        connection: sqlite3.Connection, assignments: dict[str, Any]
    ) -> None:
        for assignment in assignments.values():
            connection.execute(
                """
                INSERT INTO team_ephemeral_assignments(
                    id, parent_agent_id, project_id, task_id, session_id, title, prompt,
                    state, agent_branch, commit_sha, summary, error, model, agent_provider,
                    execution_backend, container_image, ttl_hours, directory_path, metadata_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assignment["id"],
                    assignment["parent_agent_id"],
                    assignment["project_id"],
                    assignment["task_id"],
                    assignment["session_id"],
                    assignment["title"],
                    assignment["prompt"],
                    assignment.get("state", "requested"),
                    assignment.get("agent_branch"),
                    assignment.get("commit_sha"),
                    assignment.get("summary"),
                    assignment.get("error"),
                    assignment.get("model"),
                    assignment.get("agent_provider"),
                    assignment.get("execution_backend"),
                    assignment.get("container_image"),
                    assignment.get("ttl_hours"),
                    assignment.get("directory_path"),
                    _json_dumps(assignment.get("metadata", {})),
                    assignment["created_at"],
                    assignment["updated_at"],
                ),
            )
            _insert_position_values(
                connection,
                "team_ephemeral_assignment_artifacts",
                "assignment_id",
                assignment["id"],
                "artifact_ref",
                assignment.get("artifact_refs", []),
            )
            _insert_position_values(
                connection,
                "team_ephemeral_assignment_allowed_tools",
                "assignment_id",
                assignment["id"],
                "tool",
                assignment.get("allowed_tools", []),
            )

    @staticmethod
    def _read_git_promotions(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_git_promotions ORDER BY id"):
            payload["git_promotions"][row["id"]] = {
                "id": row["id"],
                "project_id": row["project_id"],
                "actor_agent_id": row["actor_agent_id"],
                "source_branch": row["source_branch"],
                "sources": _json_loads(row["sources_json"]),
                "target_branch": row["target_branch"],
                "promotion_kind": row["promotion_kind"],
                "task_id": row["task_id"],
                "deliverable_id": row["deliverable_id"],
                "release_package_id": row["release_package_id"],
                "state": row["state"],
                "commit_sha": row["commit_sha"],
                "outputs": _json_loads(row["outputs_json"]),
                "error": row["error"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_git_promotions(connection: sqlite3.Connection, promotions: dict[str, Any]) -> None:
        for promotion in promotions.values():
            connection.execute(
                """
                INSERT INTO team_git_promotions(
                    id, project_id, actor_agent_id, source_branch, sources_json, target_branch,
                    promotion_kind, task_id, deliverable_id, release_package_id, state,
                    commit_sha, outputs_json, error, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    promotion["id"],
                    promotion["project_id"],
                    promotion["actor_agent_id"],
                    promotion["source_branch"],
                    _json_dumps(promotion.get("sources", [])),
                    promotion["target_branch"],
                    promotion["promotion_kind"],
                    promotion.get("task_id"),
                    promotion.get("deliverable_id"),
                    promotion.get("release_package_id"),
                    promotion.get("state", "pending"),
                    promotion.get("commit_sha"),
                    _json_dumps(promotion.get("outputs", {})),
                    promotion.get("error"),
                    promotion["created_at"],
                    promotion["updated_at"],
                ),
            )

    @staticmethod
    def _read_git_work_contexts(connection: sqlite3.Connection, payload: dict[str, Any]) -> None:
        for row in connection.execute("SELECT * FROM team_git_work_contexts ORDER BY id"):
            payload["git_work_contexts"][row["id"]] = {
                "id": row["id"],
                "agent_id": row["agent_id"],
                "task_id": row["task_id"],
                "dispatch_id": row["dispatch_id"],
                "worktree_path": row["worktree_path"],
                "base_branch": row["base_branch"],
                "base_commit": row["base_commit"],
                "agent_branch": row["agent_branch"],
                "commit_sha": row["commit_sha"],
                "dependency_git_sources": _json_loads(
                    row["dependency_git_sources_json"]
                ),
                "state": row["state"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    @staticmethod
    def _write_git_work_contexts(connection: sqlite3.Connection, contexts: dict[str, Any]) -> None:
        for context in contexts.values():
            connection.execute(
                """
                INSERT INTO team_git_work_contexts(
                    id, agent_id, task_id, dispatch_id, worktree_path, base_branch,
                    base_commit, agent_branch, commit_sha,
                    dependency_git_sources_json, state, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    context["id"],
                    context["agent_id"],
                    context["task_id"],
                    context.get("dispatch_id"),
                    context.get("worktree_path"),
                    context.get("base_branch"),
                    context.get("base_commit"),
                    context["agent_branch"],
                    context.get("commit_sha"),
                    _json_dumps(context.get("dependency_git_sources", [])),
                    context.get("state", "active"),
                    context["created_at"],
                    context["updated_at"],
                ),
            )


class TeamStateSqliteCommandTransaction:
    def __init__(
        self,
        store: TeamStateSqliteStore,
        connection: sqlite3.Connection,
    ) -> None:
        self._store = store
        self._connection = connection

    def get_idempotency_record(
        self,
        *,
        tool_name: str,
        caller_identity: str,
        scope_kind: str,
        scope_id: str,
        request_id: str,
    ) -> TeamIdempotencyRecord | None:
        key = _idempotency_key_values(
            tool_name=tool_name,
            caller_identity=caller_identity,
            scope_kind=scope_kind,
            scope_id=scope_id,
            request_id=request_id,
        )
        return self._store._lookup_idempotency_record(self._connection, **key)

    def store_idempotency_record(
        self,
        record: TeamIdempotencyRecord | Mapping[str, Any],
    ) -> TeamIdempotencyRecord:
        return self._store._insert_idempotency_record(self._connection, record)

    def append_dispatch_activity(
        self,
        activity: TeamDispatchActivity | Mapping[str, Any],
    ) -> TeamDispatchActivity:
        return self._store._insert_dispatch_activity(self._connection, activity)

    def list_dispatch_activity(
        self,
        *,
        dispatch_id: str | None = None,
        caller_session_id: str | None = None,
        caller_identity: str | None = None,
        agent_id: str | None = None,
        governance_group_id: str | None = None,
        assignment_id: str | None = None,
        tool_name: str | None = None,
        request_id: str | None = None,
        limit: int | None = None,
        newest_first: bool = False,
    ) -> list[TeamDispatchActivity]:
        return self._store._list_dispatch_activity(
            self._connection,
            dispatch_id=dispatch_id,
            caller_session_id=caller_session_id,
            caller_identity=caller_identity,
            agent_id=agent_id,
            governance_group_id=governance_group_id,
            assignment_id=assignment_id,
            tool_name=tool_name,
            request_id=request_id,
            limit=limit,
            newest_first=newest_first,
        )

    def enqueue_outbox_record(
        self,
        record: TeamOutboxRecord | Mapping[str, Any],
    ) -> TeamOutboxRecord:
        return self._store._insert_outbox_record(self._connection, record)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str | None) -> Any:
    if value is None or value == "":
        return {}
    return json.loads(value)


def _json_normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_normalize(item) for key, item in sorted(value.items())}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_normalize(item) for item in value]
    if hasattr(value, "model_dump"):
        return _json_normalize(value.model_dump(mode="json"))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _bool(value: Any) -> bool:
    return bool(int(value or 0))


def _int_bool(value: Any) -> int:
    return 1 if bool(value) else 0


def _optional_db_bool(value: Any) -> bool | None:
    return None if value is None else bool(int(value))


def _optional_bool_db(value: Any) -> int | None:
    return None if value is None else (1 if bool(value) else 0)


def _required_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be a non-empty string")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _datetime_text(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _idempotency_key_values(
    *,
    tool_name: str,
    caller_identity: str,
    scope_kind: str,
    scope_id: str,
    request_id: str,
) -> dict[str, str]:
    return {
        "tool_name": _required_text(tool_name, "tool_name"),
        "caller_identity": _required_text(caller_identity, "caller_identity"),
        "scope_kind": _required_text(scope_kind, "scope_kind"),
        "scope_id": _required_text(scope_id, "scope_id"),
        "request_id": _required_text(request_id, "request_id"),
    }


def _raise_idempotency_conflict(
    *,
    tool_name: str,
    caller_identity: str,
    scope_kind: str,
    scope_id: str,
    request_id: str,
    existing_digest: str,
    attempted_digest: str,
) -> None:
    raise TeamIdempotencyConflictError(
        tool_name=tool_name,
        caller_identity=caller_identity,
        scope_kind=scope_kind,
        scope_id=scope_id,
        request_id=request_id,
        existing_digest=existing_digest,
        attempted_digest=attempted_digest,
    )


def _outbox_status_value(status: TeamOutboxStatus | str) -> str:
    if isinstance(status, TeamOutboxStatus):
        return status.value
    return TeamOutboxStatus(_required_text(status, "status")).value


def _ensure_team_agents_nullable_membership(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"]): row for row in connection.execute("PRAGMA table_info(team_agents)")
    }
    if not columns:
        return
    team_column = columns.get("team_id")
    role_column = columns.get("role_id")
    if (
        team_column is not None
        and role_column is not None
        and not int(team_column["notnull"] or 0)
        and not int(role_column["notnull"] or 0)
    ):
        return
    connection.execute("ALTER TABLE team_agents RENAME TO team_agents_notnull_legacy")
    connection.execute(
        """
        CREATE TABLE team_agents (
            id TEXT PRIMARY KEY,
            team_id TEXT,
            project_id TEXT,
            display_name TEXT,
            avatar_json TEXT,
            voice_profile_json TEXT,
            role_id TEXT,
            session_id TEXT NOT NULL,
            reports_to_agent_id TEXT,
            max_ephemeral_concurrent INTEGER NOT NULL,
            status TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO team_agents(
            id, team_id, project_id, display_name, avatar_json, voice_profile_json,
            role_id, session_id, reports_to_agent_id, max_ephemeral_concurrent, status
        )
        SELECT
            id, team_id, project_id, display_name, avatar_json, voice_profile_json,
            role_id, session_id, reports_to_agent_id, max_ephemeral_concurrent, status
        FROM team_agents_notnull_legacy
        """
    )
    connection.execute("DROP TABLE team_agents_notnull_legacy")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_agents_status_scope "
        "ON team_agents(status, team_id, id)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_agents_session ON team_agents(session_id)"
    )


def _ensure_project_assignments_from_legacy(connection: sqlite3.Connection) -> None:
    project_assignment_count = connection.execute(
        "SELECT COUNT(*) AS count FROM team_project_assignments"
    ).fetchone()["count"]
    if int(project_assignment_count or 0) > 0:
        return
    now = utc_now().isoformat()
    agent_columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(team_agents)")
    }
    has_agent_project_id = "project_id" in agent_columns
    for row in connection.execute(
        """
        SELECT p.id AS project_id, p.team_id, p.lead_agent_id, a.role_id
        FROM team_projects p
        LEFT JOIN team_agents a ON a.id = p.lead_agent_id
        ORDER BY p.id
        """
    ):
        assignment_id = f"{row['project_id']}__{row['lead_agent_id']}__project_lead"
        connection.execute(
            """
            INSERT OR IGNORE INTO team_project_assignments(
                id, team_id, project_id, agent_id, role_id, kind, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'project_lead', ?, ?)
            """,
            (
                assignment_id,
                row["team_id"],
                row["project_id"],
                row["lead_agent_id"],
                row["role_id"],
                now,
                now,
            ),
        )
    if has_agent_project_id:
        for row in connection.execute(
            """
            SELECT id, team_id, project_id, role_id
            FROM team_agents
            WHERE project_id IS NOT NULL AND trim(project_id) <> ''
            ORDER BY id
            """
        ):
            assignment_id = f"{row['project_id']}__{row['id']}__project_member"
            connection.execute(
                """
                INSERT OR IGNORE INTO team_project_assignments(
                    id, team_id, project_id, agent_id, role_id, kind, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'project_member', ?, ?)
                """,
                (
                    assignment_id,
                    row["team_id"],
                    row["project_id"],
                    row["id"],
                    row["role_id"],
                    now,
                    now,
                ),
            )


def _ensure_team_conversations_operator_scope(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"]): row for row in connection.execute("PRAGMA table_info(team_conversations)")
    }
    if not columns:
        return
    has_operator_columns = "operator_user_id" in columns and "operator_display_name" in columns
    needs_rebuild = not has_operator_columns or _team_conversations_has_agent_unique_index(
        connection
    )
    if not needs_rebuild:
        connection.execute(
            """
            UPDATE team_conversations
            SET operator_user_id = lower(trim(
                COALESCE(NULLIF(trim(operator_user_id), ''), 'admin')
            ))
            WHERE operator_user_id IS NULL
               OR trim(operator_user_id) = ''
               OR operator_user_id <> lower(trim(operator_user_id))
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_team_conversations_agent_operator
            ON team_conversations(agent_id, operator_user_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_team_conversations_operator_recent
            ON team_conversations(operator_user_id, last_interaction_at DESC, id)
            """
        )
        return

    connection.execute("ALTER TABLE team_conversations RENAME TO team_conversations_legacy")
    connection.execute(
        """
        CREATE TABLE team_conversations (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            operator_user_id TEXT NOT NULL,
            operator_display_name TEXT,
            created_by TEXT,
            last_message_preview TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_interaction_at TEXT NOT NULL
        )
        """
    )
    legacy_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(team_conversations_legacy)")
    }
    legacy_operator_user_expr = (
        "COALESCE(NULLIF(trim(operator_user_id), ''), "
        "CASE "
        "WHEN trim(created_by) LIKE 'operator:%' THEN substr(trim(created_by), 10) "
        "WHEN created_by IS NOT NULL AND trim(created_by) <> '' THEN trim(created_by) "
        "ELSE 'admin' END)"
        if "operator_user_id" in legacy_columns
        else (
            "CASE "
            "WHEN trim(created_by) LIKE 'operator:%' THEN substr(trim(created_by), 10) "
            "WHEN created_by IS NOT NULL AND trim(created_by) <> '' THEN trim(created_by) "
            "ELSE 'admin' END"
        )
    )
    operator_user_expr = f"lower(trim({legacy_operator_user_expr}))"
    operator_display_expr = (
        "operator_display_name" if "operator_display_name" in legacy_columns else "NULL"
    )
    connection.execute(
        f"""
        INSERT OR IGNORE INTO team_conversations(
            id, agent_id, operator_user_id, operator_display_name, created_by,
            last_message_preview, created_at, updated_at, last_interaction_at
        )
        SELECT
            id,
            agent_id,
            {operator_user_expr},
            {operator_display_expr},
            created_by,
            last_message_preview,
            created_at,
            updated_at,
            last_interaction_at
        FROM team_conversations_legacy
        """
    )
    connection.execute("DROP TABLE team_conversations_legacy")
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_team_conversations_recent
        ON team_conversations(last_interaction_at DESC, id)
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_team_conversations_agent_operator
        ON team_conversations(agent_id, operator_user_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_team_conversations_operator_recent
        ON team_conversations(operator_user_id, last_interaction_at DESC, id)
        """
    )


def _team_conversations_has_agent_unique_index(connection: sqlite3.Connection) -> bool:
    for index in connection.execute("PRAGMA index_list(team_conversations)"):
        if not int(index["unique"] or 0):
            continue
        index_name = str(index["name"])
        columns = [
            str(row["name"]) for row in connection.execute(f"PRAGMA index_info({index_name})")
        ]
        if columns == ["agent_id"]:
            return True
    return False


def _migrate_human_review_relationships(connection: sqlite3.Connection) -> None:
    """Backfill normalized human-review relationships from the v33 payload rows."""

    for row in connection.execute(
        "SELECT id, payload_json FROM team_human_review_requests ORDER BY id"
    ):
        request = _json_loads(row["payload_json"])
        requirement = request.get("requirement_snapshot") or {}
        assignment = request.get("current_assignment") or {}
        for table_name, value_column, values in (
            (
                "team_human_review_request_configured_groups",
                "group_id",
                requirement.get("configured_group_ids", []),
            ),
            (
                "team_human_review_request_initial_users",
                "user_id",
                requirement.get("initially_eligible_user_ids", []),
            ),
            (
                "team_human_review_request_current_users",
                "user_id",
                assignment.get("eligible_user_ids", []),
            ),
            (
                "team_human_review_request_explicit_users",
                "user_id",
                assignment.get("explicitly_assigned_user_ids", []),
            ),
        ):
            _backfill_position_values(
                connection,
                table_name=table_name,
                owner_column="human_review_request_id",
                owner_id=row["id"],
                value_column=value_column,
                values=values,
            )

    for row in connection.execute(
        "SELECT id, payload_json FROM team_human_review_decisions ORDER BY id"
    ):
        decision = _json_loads(row["payload_json"])
        _backfill_position_values(
            connection,
            table_name="team_human_review_decision_confirmed_findings",
            owner_column="human_review_decision_id",
            owner_id=row["id"],
            value_column="finding_id",
            values=decision.get("confirmed_finding_ids", []),
        )

    for row in connection.execute(
        """
        SELECT id, resolved_by_human_review_request_id, payload_json
        FROM team_human_review_findings
        ORDER BY id
        """
    ):
        finding = _json_loads(row["payload_json"])
        if row["resolved_by_human_review_request_id"] is None:
            resolved_by_request_id = finding.get("resolved_by_human_review_request_id")
            if resolved_by_request_id:
                connection.execute(
                    """
                    UPDATE team_human_review_findings
                    SET resolved_by_human_review_request_id = ?
                    WHERE id = ?
                    """,
                    (resolved_by_request_id, row["id"]),
                )
        for table_name, values in (
            (
                "team_human_review_finding_targets",
                finding.get("target_deliverable_ids", []),
            ),
            (
                "team_human_review_finding_proposals",
                finding.get("proposed_by_deliverable_ids", []),
            ),
        ):
            _backfill_position_values(
                connection,
                table_name=table_name,
                owner_column="finding_id",
                owner_id=row["id"],
                value_column="deliverable_id",
                values=values,
            )


def _backfill_position_values(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    owner_column: str,
    owner_id: str,
    value_column: str,
    values: Sequence[object],
) -> None:
    existing = connection.execute(
        f"SELECT 1 FROM {table_name} WHERE {owner_column} = ? LIMIT 1",
        (owner_id,),
    ).fetchone()
    if existing is not None:
        return
    for position, value in enumerate(values):
        text = str(value).strip()
        if text:
            connection.execute(
                (
                    f"INSERT INTO {table_name}("
                    f"{owner_column}, position, {value_column}"
                    ") VALUES (?, ?, ?)"
                ),
                (owner_id, position, text),
            )


_KNOWN_OUTCOME_KIND_MIGRATIONS: dict[str, dict[str, str]] = {
    "evidence_match": {
        "name": "Evidence Match",
        "description": (
            "A durable source-to-Bokio match or an explicit unmatched or blocked disposition."
        ),
        "reference_requirements": (
            "Reference the source record identifiers and matching Bokio "
            "transaction or evidence identifiers. Unmatched work must reference "
            "its durable case or blocker."
        ),
        "evidence_requirements": (
            "Cite the amount, date, currency, counterparty, and account match "
            "basis together with durable Bokio duplicate-check evidence."
        ),
    },
    "bokio_prepared_action": {
        "name": "Bokio Prepared Action",
        "description": (
            "A prepared-only Bokio bookkeeping action ready for independent "
            "review and human approval; it does not assert that the action was "
            "approved or posted."
        ),
        "reference_requirements": (
            "Reference the prepared Bokio supplier invoice, journal entry, or "
            "bank payment identifier."
        ),
        "evidence_requirements": (
            "Cite supporting documents, Bokio account and counterparty mapping, "
            "tax treatment, and the applicable human approval boundary."
        ),
    },
}


def _migrate_legacy_json_outcome_kind_records(payload: object) -> None:
    if not isinstance(payload, dict):
        return
    raw_definitions = payload.get("outcome_kind_definitions")
    if raw_definitions is None:
        definitions: dict[str, Any] = {}
        payload["outcome_kind_definitions"] = definitions
    elif isinstance(raw_definitions, dict):
        definitions = raw_definitions
    else:
        return

    def records(name: str) -> dict[str, Any]:
        value = payload.get(name)
        return value if isinstance(value, dict) else {}

    projects = records("projects")
    tasks = records("tasks")
    deliverables = records("deliverables")
    profiles = records("review_profiles")
    reviews = records("review_requests")
    releases = records("release_packages")

    def text(value: object) -> str:
        return str(value or "").strip()

    def optional_text(value: object) -> str | None:
        normalized = text(value)
        return normalized or None

    def kind_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        result: list[str] = []
        for item in value:
            kind = text(item)
            if kind and kind not in result:
                result.append(kind)
        return result

    def project_team_id(project_id: str | None) -> str | None:
        project = projects.get(project_id) if project_id is not None else None
        return (
            optional_text(project.get("team_id"))
            if isinstance(project, Mapping)
            else None
        )

    def definition_for(
        *,
        team_id: str,
        project_id: str | None,
        kind: str,
        exact_scope: bool = False,
    ) -> Mapping[str, Any] | None:
        inherited: Mapping[str, Any] | None = None
        for definition in definitions.values():
            if not isinstance(definition, Mapping):
                continue
            if (
                optional_text(definition.get("team_id")) != team_id
                or text(definition.get("kind")) != kind
            ):
                continue
            definition_project_id = optional_text(definition.get("project_id"))
            if definition_project_id == project_id:
                return definition
            if not exact_scope and definition_project_id is None:
                inherited = definition
        return inherited

    def add_definition(
        *,
        team_id: str | None,
        project_id: str | None,
        kind: str,
        source: Mapping[str, Any],
        profile_guidance: str | None = None,
        exact_scope: bool = False,
    ) -> Mapping[str, Any] | None:
        if team_id is None or not kind:
            return None
        existing = definition_for(
            team_id=team_id,
            project_id=project_id,
            kind=kind,
            exact_scope=exact_scope,
        )
        if existing is not None:
            return existing
        known = _KNOWN_OUTCOME_KIND_MIGRATIONS.get(kind)
        name = (
            known["name"]
            if known is not None
            else kind.replace("_", " ").replace("-", " ").title()
        )
        description = (
            known["description"]
            if known is not None
            else text(source.get("description") or source.get("name"))
            or f"Migrated definition for the {name} outcome kind."
        )
        reference_requirements = (
            known["reference_requirements"]
            if known is not None
            else f"Provide durable reference IDs for {name}."
        )
        evidence_requirements = (
            known["evidence_requirements"]
            if known is not None
            else profile_guidance
            or "Provide evidence sufficient to verify the claimed outcome."
        )
        digest = hashlib.sha256(
            f"{team_id}:{project_id or ''}:{kind}".encode("utf-8")
        ).hexdigest()[:16]
        base_id = f"outcome-kind-{digest}"
        definition_id = base_id
        suffix = 2
        while definition_id in definitions:
            definition_id = f"{base_id}-{suffix}"
            suffix += 1
        definition: dict[str, Any] = {
            "id": definition_id,
            "team_id": team_id,
            "project_id": project_id,
            "kind": kind,
            "name": name,
            "description": description,
            "reference_requirements": reference_requirements,
            "evidence_requirements": evidence_requirements,
            "archived": False,
        }
        for timestamp_field in ("created_at", "updated_at"):
            if source.get(timestamp_field) is not None:
                definition[timestamp_field] = source[timestamp_field]
        definitions[definition_id] = definition
        return definition

    def snapshot(
        definition: Mapping[str, Any] | None,
    ) -> dict[str, str] | None:
        if definition is None:
            return None
        kind = text(definition.get("kind"))
        name = text(definition.get("name"))
        if not kind or not name:
            return None
        return {
            "kind": kind,
            "name": name,
            "description": text(definition.get("description")),
            "reference_requirements": text(
                definition.get("reference_requirements")
            ),
            "evidence_requirements": text(
                definition.get("evidence_requirements")
            ),
        }

    for profile in profiles.values():
        if not isinstance(profile, Mapping):
            continue
        team_id = optional_text(profile.get("team_id"))
        project_id = optional_text(profile.get("project_id"))
        criteria = profile.get("criteria")
        guidance = "; ".join(
            text(criterion.get("description") or criterion.get("label"))
            for criterion in criteria
            if isinstance(criterion, Mapping)
            and text(criterion.get("description") or criterion.get("label"))
        ) if isinstance(criteria, list) else ""
        for kind in kind_list(profile.get("required_outcome_kinds")):
            add_definition(
                team_id=team_id,
                project_id=project_id,
                kind=kind,
                source=profile,
                profile_guidance=guidance,
                exact_scope=True,
            )

    for task in tasks.values():
        if not isinstance(task, dict):
            continue
        contract = task.get("outcome_contract")
        if not isinstance(contract, dict):
            continue
        legacy_forbidden = kind_list(contract.pop("forbidden_effect_kinds", []))
        forbidden = kind_list(contract.get("forbidden_outcome_kinds"))
        contract["forbidden_outcome_kinds"] = [
            *forbidden,
            *(kind for kind in legacy_forbidden if kind not in forbidden),
        ]
        project_id = optional_text(task.get("project_id"))
        team_id = project_team_id(project_id)
        kinds = [
            *kind_list(contract.get("required_outcome_kinds")),
            *contract["forbidden_outcome_kinds"],
        ]
        for kind in kinds:
            add_definition(
                team_id=team_id,
                project_id=project_id,
                kind=kind,
                source=task,
            )

    for deliverable in deliverables.values():
        if not isinstance(deliverable, Mapping):
            continue
        task = tasks.get(optional_text(deliverable.get("task_id")))
        project_id = (
            optional_text(task.get("project_id"))
            if isinstance(task, Mapping)
            else None
        )
        team_id = project_team_id(project_id)
        outcomes = deliverable.get("outcomes")
        if not isinstance(outcomes, list):
            continue
        for outcome in outcomes:
            if not isinstance(outcome, Mapping):
                continue
            add_definition(
                team_id=team_id,
                project_id=project_id,
                kind=text(outcome.get("kind")),
                source=deliverable,
            )

    for task in tasks.values():
        if not isinstance(task, Mapping):
            continue
        contract = task.get("outcome_contract")
        if not isinstance(contract, dict):
            continue
        project_id = optional_text(task.get("project_id"))
        team_id = project_team_id(project_id)
        kinds = [
            *kind_list(contract.get("required_outcome_kinds")),
            *kind_list(contract.get("forbidden_outcome_kinds")),
        ]
        existing_snapshots = {
            text(item.get("kind")): dict(item)
            for item in contract.get("outcome_kind_definitions", [])
            if isinstance(item, Mapping) and text(item.get("kind"))
        }
        contract["outcome_kind_definitions"] = [
            resolved
            for kind in kinds
            if (
                resolved := existing_snapshots.get(kind)
                or snapshot(
                    definition_for(
                        team_id=team_id or "",
                        project_id=project_id,
                        kind=kind,
                    )
                )
            )
            is not None
        ]

    for deliverable in deliverables.values():
        if not isinstance(deliverable, Mapping):
            continue
        task = tasks.get(optional_text(deliverable.get("task_id")))
        project_id = (
            optional_text(task.get("project_id"))
            if isinstance(task, Mapping)
            else None
        )
        team_id = project_team_id(project_id)
        outcomes = deliverable.get("outcomes")
        if not isinstance(outcomes, list):
            continue
        for outcome in outcomes:
            if not isinstance(outcome, dict) or isinstance(
                outcome.get("definition_snapshot"),
                Mapping,
            ):
                continue
            outcome["definition_snapshot"] = snapshot(
                definition_for(
                    team_id=team_id or "",
                    project_id=project_id,
                    kind=text(outcome.get("kind")),
                )
            )

    for review in reviews.values():
        if not isinstance(review, dict):
            continue
        profile = profiles.get(optional_text(review.get("review_profile_id")))
        if not isinstance(profile, Mapping):
            continue
        team_id = optional_text(profile.get("team_id"))
        deliverable = deliverables.get(
            optional_text(review.get("deliverable_id"))
        )
        task = (
            tasks.get(optional_text(deliverable.get("task_id")))
            if isinstance(deliverable, Mapping)
            else None
        )
        release = releases.get(optional_text(review.get("release_package_id")))
        project_id = (
            optional_text(task.get("project_id"))
            if isinstance(task, Mapping)
            else optional_text(release.get("project_id"))
            if isinstance(release, Mapping)
            else optional_text(profile.get("project_id"))
        )
        existing_snapshots = {
            text(item.get("kind")): dict(item)
            for item in review.get("required_outcome_kind_definitions", [])
            if isinstance(item, Mapping) and text(item.get("kind"))
        }
        review["required_outcome_kind_definitions"] = [
            resolved
            for kind in kind_list(profile.get("required_outcome_kinds"))
            if (
                resolved := existing_snapshots.get(kind)
                or snapshot(
                    definition_for(
                        team_id=team_id or "",
                        project_id=project_id,
                        kind=kind,
                    )
                )
            )
            is not None
        ]


def _migrate_legacy_outcome_kind_definitions(
    connection: sqlite3.Connection,
) -> None:
    profiles = connection.execute(
        """
        SELECT id, team_id, project_id, name, description,
               required_outcome_kinds_json, created_at, updated_at
        FROM team_review_profiles
        ORDER BY id
        """
    ).fetchall()
    for profile in profiles:
        kinds = _json_loads(profile["required_outcome_kinds_json"])
        criteria_rows = connection.execute(
            """
            SELECT label, description
            FROM team_review_profile_criteria
            WHERE profile_id = ?
            ORDER BY position
            """,
            (profile["id"],),
        ).fetchall()
        criteria_guidance = "; ".join(
            str(row["description"] or row["label"])
            for row in criteria_rows
            if row["description"] or row["label"]
        )
        for raw_kind in kinds:
            kind = str(raw_kind).strip()
            if not kind:
                continue
            existing = connection.execute(
                """
                SELECT id
                FROM team_outcome_kind_definitions
                WHERE team_id = ?
                  AND COALESCE(project_id, '') = COALESCE(?, '')
                  AND kind = ?
                """,
                (profile["team_id"], profile["project_id"], kind),
            ).fetchone()
            if existing is not None:
                continue
            known = _KNOWN_OUTCOME_KIND_MIGRATIONS.get(kind)
            name = (
                known["name"]
                if known is not None
                else kind.replace("_", " ").replace("-", " ").title()
            )
            description = (
                known["description"]
                if known is not None
                else str(profile["description"] or profile["name"])
            )
            reference_requirements = (
                known["reference_requirements"]
                if known is not None
                else f"Provide durable reference IDs for {name}."
            )
            evidence_requirements = (
                known["evidence_requirements"] if known is not None else criteria_guidance
            )
            digest = hashlib.sha256(
                (f"{profile['team_id']}:{profile['project_id'] or ''}:{kind}").encode("utf-8")
            ).hexdigest()[:16]
            connection.execute(
                """
                INSERT INTO team_outcome_kind_definitions(
                    id, team_id, project_id, kind, name, description,
                    reference_requirements, evidence_requirements, archived,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    f"outcome-kind-{digest}",
                    profile["team_id"],
                    profile["project_id"],
                    kind,
                    name,
                    description,
                    reference_requirements,
                    evidence_requirements,
                    profile["created_at"],
                    profile["updated_at"],
                ),
            )

    def ensure_generic_definition(
        *,
        team_id: str,
        project_id: str | None,
        kind: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        existing = connection.execute(
            """
            SELECT id
            FROM team_outcome_kind_definitions
            WHERE team_id = ?
              AND kind = ?
              AND (project_id = ? OR project_id IS NULL)
            ORDER BY CASE WHEN project_id = ? THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            (team_id, kind, project_id, project_id),
        ).fetchone()
        if existing is not None:
            return
        known = _KNOWN_OUTCOME_KIND_MIGRATIONS.get(kind)
        name = (
            known["name"] if known is not None else kind.replace("_", " ").replace("-", " ").title()
        )
        description = (
            known["description"]
            if known is not None
            else f"Migrated definition for the {name} outcome kind."
        )
        reference_requirements = (
            known["reference_requirements"]
            if known is not None
            else f"Provide durable reference IDs for {name}."
        )
        evidence_requirements = (
            known["evidence_requirements"]
            if known is not None
            else "Provide evidence sufficient to verify the claimed outcome."
        )
        digest = hashlib.sha256(f"{team_id}:{project_id or ''}:{kind}".encode("utf-8")).hexdigest()[
            :16
        ]
        connection.execute(
            """
            INSERT INTO team_outcome_kind_definitions(
                id, team_id, project_id, kind, name, description,
                reference_requirements, evidence_requirements, archived,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                f"outcome-kind-{digest}",
                team_id,
                project_id,
                kind,
                name,
                description,
                reference_requirements,
                evidence_requirements,
                created_at,
                updated_at,
            ),
        )

    task_source_rows = connection.execute(
        """
        SELECT task.id, task.project_id, task.outcome_contract_json,
               task.created_at, task.updated_at, project.team_id
        FROM team_tasks AS task
        JOIN team_projects AS project ON project.id = task.project_id
        WHERE task.outcome_contract_json IS NOT NULL
        """
    ).fetchall()
    for row in task_source_rows:
        contract = _json_loads(row["outcome_contract_json"])
        kinds = list(contract.get("required_outcome_kinds", [])) + list(
            contract.get(
                "forbidden_outcome_kinds",
                contract.get("forbidden_effect_kinds", []),
            )
        )
        for raw_kind in kinds:
            kind = str(raw_kind).strip()
            if kind:
                ensure_generic_definition(
                    team_id=row["team_id"],
                    project_id=row["project_id"],
                    kind=kind,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

    deliverable_source_rows = connection.execute(
        """
        SELECT deliverable.id, deliverable.outcomes_json,
               deliverable.created_at, task.project_id, task.updated_at,
               project.team_id
        FROM team_deliverables AS deliverable
        JOIN team_tasks AS task ON task.id = deliverable.task_id
        JOIN team_projects AS project ON project.id = task.project_id
        WHERE deliverable.outcomes_json <> '[]'
        """
    ).fetchall()
    for row in deliverable_source_rows:
        for outcome in _json_loads(row["outcomes_json"]):
            if not isinstance(outcome, dict):
                continue
            kind = str(outcome.get("kind") or "").strip()
            if kind:
                ensure_generic_definition(
                    team_id=row["team_id"],
                    project_id=row["project_id"],
                    kind=kind,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )

    def snapshot_for(
        *,
        team_id: str,
        project_id: str | None,
        kind: str,
    ) -> dict[str, str] | None:
        row = connection.execute(
            """
            SELECT kind, name, description, reference_requirements,
                   evidence_requirements
            FROM team_outcome_kind_definitions
            WHERE team_id = ?
              AND kind = ?
              AND (project_id = ? OR project_id IS NULL)
            ORDER BY CASE WHEN project_id = ? THEN 0 ELSE 1 END, id
            LIMIT 1
            """,
            (team_id, kind, project_id, project_id),
        ).fetchone()
        return (
            {
                "kind": row["kind"],
                "name": row["name"],
                "description": row["description"],
                "reference_requirements": row["reference_requirements"],
                "evidence_requirements": row["evidence_requirements"],
            }
            if row is not None
            else None
        )

    for row in task_source_rows:
        contract = _json_loads(row["outcome_contract_json"])
        if "forbidden_effect_kinds" in contract:
            contract["forbidden_outcome_kinds"] = contract.pop("forbidden_effect_kinds")
        kinds = list(contract.get("required_outcome_kinds", [])) + list(
            contract.get("forbidden_outcome_kinds", [])
        )
        contract["outcome_kind_definitions"] = [
            snapshot
            for kind in kinds
            if (
                snapshot := snapshot_for(
                    team_id=row["team_id"],
                    project_id=row["project_id"],
                    kind=str(kind),
                )
            )
            is not None
        ]
        connection.execute(
            "UPDATE team_tasks SET outcome_contract_json = ? WHERE id = ?",
            (_json_dumps(contract), row["id"]),
        )

    for row in deliverable_source_rows:
        outcomes = _json_loads(row["outcomes_json"])
        for outcome in outcomes:
            if not isinstance(outcome, dict):
                continue
            kind = str(outcome.get("kind") or "").strip()
            if not kind:
                continue
            outcome["definition_snapshot"] = snapshot_for(
                team_id=row["team_id"],
                project_id=row["project_id"],
                kind=kind,
            )
        connection.execute(
            "UPDATE team_deliverables SET outcomes_json = ? WHERE id = ?",
            (_json_dumps(outcomes), row["id"]),
        )

    review_rows = connection.execute(
        """
        SELECT review.id, profile.team_id,
               COALESCE(
                   task.project_id,
                   release.project_id,
                   profile.project_id
               ) AS reviewed_project_id,
               profile.required_outcome_kinds_json
        FROM team_review_requests AS review
        JOIN team_review_profiles AS profile
          ON profile.id = review.review_profile_id
        LEFT JOIN team_deliverables AS deliverable
          ON deliverable.id = review.deliverable_id
        LEFT JOIN team_tasks AS task
          ON task.id = deliverable.task_id
        LEFT JOIN team_release_packages AS release
          ON release.id = review.release_package_id
        """
    ).fetchall()
    for row in review_rows:
        snapshots = [
            snapshot
            for kind in _json_loads(row["required_outcome_kinds_json"])
            if (
                snapshot := snapshot_for(
                    team_id=row["team_id"],
                    project_id=row["reviewed_project_id"],
                    kind=str(kind),
                )
            )
            is not None
        ]
        connection.execute(
            """
            UPDATE team_review_requests
            SET required_outcome_kind_definitions_json = ?
            WHERE id = ?
            """,
            (_json_dumps(snapshots), row["id"]),
        )


def _ensure_column(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    existing_columns = {
        str(row["name"]) for row in connection.execute(f"PRAGMA table_info({table_name})")
    }
    if column_name in existing_columns:
        return
    connection.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _insert_position_values(
    connection: sqlite3.Connection,
    table: str,
    owner_column: str,
    owner_id: str,
    value_column: str,
    values: list[Any],
) -> None:
    for position, value in enumerate(values):
        connection.execute(
            f"""
            INSERT INTO {table}({owner_column}, position, {value_column})
            VALUES (?, ?, ?)
            """,
            (owner_id, position, value),
        )


def _grouped_values(connection: sqlite3.Connection, query: str) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for row in connection.execute(query):
        grouped.setdefault(row["owner_id"], []).append(row["value"])
    return grouped


def _grouped_rows(
    connection: sqlite3.Connection,
    query: str,
    mapper: Callable[[sqlite3.Row], dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in connection.execute(query):
        grouped.setdefault(row["owner_id"], []).append(mapper(row))
    return grouped


_STATE_TABLES = (
    "team_governance_group_members",
    "team_governance_group_goals",
    "team_governance_agenda",
    "team_governance_runtimes",
    "team_team_projects",
    "team_role_permissions",
    "team_project_goals",
    "team_agent_subordinates",
    "team_project_assignments",
    "team_task_dependencies",
    "team_task_deliverables",
    "team_deliverable_artifacts",
    "team_deliverable_reviews",
    "team_deliverable_promotion_records",
    "team_deliverable_sources",
    "team_review_profile_roles",
    "team_review_profile_criteria",
    "team_review_request_findings",
    "team_review_request_criteria",
    "team_release_deliverables",
    "team_release_artifacts",
    "team_release_feature_branches",
    "team_release_git_promotions",
    "team_human_review_request_configured_groups",
    "team_human_review_request_initial_users",
    "team_human_review_request_current_users",
    "team_human_review_request_explicit_users",
    "team_human_review_decision_confirmed_findings",
    "team_human_review_finding_targets",
    "team_human_review_finding_proposals",
    "team_dispatch_tasks",
    "team_dispatch_blockers",
    "team_dispatch_reviews",
    "team_dispatch_inbox",
    "team_dispatch_projects",
    "team_dispatch_repositories",
    "team_ephemeral_assignment_artifacts",
    "team_ephemeral_assignment_allowed_tools",
    "team_memory_tags",
    "team_memory_source_refs",
    "team_memory_artifact_refs",
    "team_git_work_contexts",
    "team_git_promotions",
    "team_worktree_cleanup_candidates",
    "team_dispatches",
    "team_human_review_decisions",
    "team_human_review_findings",
    "team_human_review_requests",
    "team_ephemeral_assignments",
    "team_release_verification_runs",
    "team_release_packages",
    "team_external_chat_deliveries",
    "team_external_chat_ingress",
    "team_chat_attachments",
    "team_chat_attachment_policies",
    "team_conversation_messages",
    "team_conversations",
    "team_memories",
    "team_action_records",
    "team_blocker_events",
    "team_blocker_affected_tasks",
    "team_blockers",
    "team_inbox",
    "team_review_requests",
    "team_review_profiles",
    "team_outcome_kind_definitions",
    "team_deliverables",
    "team_cases",
    "team_tasks",
    "team_agent_stand_downs",
    "team_agent_runtimes",
    "team_agents",
    "team_projects",
    "team_roles",
    "team_governance_groups",
    "team_teams",
)


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS team_state_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_teams (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    manager_agent_id TEXT NOT NULL,
    operator_communication_target_id TEXT,
    max_cost_usd TEXT,
    escalate_to_governance INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_teams_archived ON team_teams(archived, id);

CREATE TABLE IF NOT EXISTS team_team_projects (
    team_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    PRIMARY KEY(team_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_team_projects_project ON team_team_projects(project_id);

CREATE TABLE IF NOT EXISTS team_governance_groups (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    manager_agent_id TEXT NOT NULL,
    max_cost_usd TEXT,
    archived INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_governance_groups_archived
    ON team_governance_groups(archived, id);

CREATE TABLE IF NOT EXISTS team_governance_group_members (
    governance_group_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    team_id TEXT NOT NULL,
    PRIMARY KEY(governance_group_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_governance_group_members_team
    ON team_governance_group_members(team_id);

CREATE TABLE IF NOT EXISTS team_governance_group_goals (
    governance_group_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    goal_id TEXT NOT NULL,
    goal_name TEXT NOT NULL,
    description TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY(governance_group_id, position)
);

CREATE TABLE IF NOT EXISTS team_governance_agenda (
    id TEXT PRIMARY KEY,
    governance_group_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    team_id TEXT,
    project_id TEXT,
    agent_id TEXT,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL,
    priority TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_governance_agenda_group_state
    ON team_governance_agenda(governance_group_id, state, priority, id);
CREATE INDEX IF NOT EXISTS idx_team_governance_agenda_team
    ON team_governance_agenda(team_id, state, id);

CREATE TABLE IF NOT EXISTS team_governance_runtimes (
    governance_group_id TEXT PRIMARY KEY,
    wake_leased_until TEXT,
    active_dispatch_id TEXT,
    wake_reason TEXT,
    last_wake_at TEXT,
    next_wake_at TEXT,
    last_cost_signal_fingerprints_json TEXT NOT NULL DEFAULT '[]',
    last_blocked_signal_fingerprints_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_roles (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    max_ephemeral_concurrent INTEGER NOT NULL,
    archived INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_roles_team_project ON team_roles(team_id, project_id, archived, id);

CREATE TABLE IF NOT EXISTS team_role_permissions (
    role_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    permission TEXT NOT NULL,
    PRIMARY KEY(role_id, position)
);

CREATE TABLE IF NOT EXISTS team_projects (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    name TEXT NOT NULL,
    lead_agent_id TEXT NOT NULL,
    git_json TEXT,
    release_policy_json TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_projects_team_status ON team_projects(team_id, status, id);

CREATE TABLE IF NOT EXISTS team_project_goals (
    project_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    goal_id TEXT NOT NULL,
    description TEXT NOT NULL,
    state TEXT NOT NULL,
    PRIMARY KEY(project_id, position)
);

CREATE TABLE IF NOT EXISTS team_agents (
    id TEXT PRIMARY KEY,
    team_id TEXT,
    display_name TEXT,
    avatar_json TEXT,
    voice_profile_json TEXT,
    role_id TEXT,
    session_id TEXT NOT NULL,
    reports_to_agent_id TEXT,
    max_ephemeral_concurrent INTEGER NOT NULL,
    status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_agents_status_scope ON team_agents(status, team_id, id);
CREATE INDEX IF NOT EXISTS idx_team_agents_session ON team_agents(session_id);

CREATE TABLE IF NOT EXISTS team_project_assignments (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role_id TEXT,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_project_assignments_unique_kind
    ON team_project_assignments(project_id, agent_id, kind);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_project_assignments_one_lead
    ON team_project_assignments(project_id, kind) WHERE kind = 'project_lead';
CREATE INDEX IF NOT EXISTS idx_team_project_assignments_agent
    ON team_project_assignments(agent_id, project_id, kind);

CREATE TABLE IF NOT EXISTS team_agent_subordinates (
    agent_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    subordinate_agent_id TEXT NOT NULL,
    PRIMARY KEY(agent_id, position)
);

CREATE TABLE IF NOT EXISTS team_agent_stand_downs (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    reason TEXT NOT NULL,
    source TEXT NOT NULL,
    set_by TEXT NOT NULL,
    set_at TEXT NOT NULL,
    cleared_by TEXT,
    cleared_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_agent_stand_downs_agent_active
    ON team_agent_stand_downs(agent_id, cleared_at, source, id);

CREATE TABLE IF NOT EXISTS team_agent_runtimes (
    agent_id TEXT PRIMARY KEY,
    wake_leased_until TEXT,
    active_dispatch_id TEXT,
    wake_reason TEXT,
    last_wake_at TEXT,
    next_wake_at TEXT,
    no_progress_count INTEGER NOT NULL,
    last_progress_at TEXT,
    low_priority_batch_started_at TEXT,
    low_priority_batch_cycle_count INTEGER NOT NULL DEFAULT 0,
    low_priority_batch_due_after_cycles INTEGER,
    context_clearing_policy TEXT NOT NULL DEFAULT 'off',
    auto_context_clearing_enabled INTEGER NOT NULL,
    context_rotation_pending INTEGER NOT NULL,
    context_rotation_requested_by TEXT,
    context_rotation_requested_at TEXT,
    context_rotation_reason TEXT,
    context_rotation_request_note TEXT,
    last_handoff_summary TEXT,
    context_handoff_preparation_pending INTEGER NOT NULL DEFAULT 0,
    context_handoff_preparation_status TEXT,
    last_agent_handoff_summary TEXT,
    last_agent_handoff_at TEXT,
    last_context_rotated_at TEXT,
    last_thread_id TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_agent_runtimes_wake
    ON team_agent_runtimes(wake_leased_until, next_wake_at, active_dispatch_id);

CREATE TABLE IF NOT EXISTS team_cases (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    owner_agent_id TEXT,
    source TEXT,
    source_ref TEXT,
    tags_json TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    task_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_cases_team_status
    ON team_cases(team_id, status, priority, id);
CREATE INDEX IF NOT EXISTS idx_team_cases_project_status
    ON team_cases(project_id, status, priority, id);
CREATE INDEX IF NOT EXISTS idx_team_cases_owner_status
    ON team_cases(owner_agent_id, status, priority, id);
CREATE INDEX IF NOT EXISTS idx_team_cases_source ON team_cases(source, source_ref, id);

CREATE TABLE IF NOT EXISTS team_tasks (
    id TEXT PRIMARY KEY,
    owner_agent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    case_id TEXT,
    priority INTEGER NOT NULL,
    state TEXT NOT NULL,
    state_entered_at TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    created_by_agent_id TEXT,
    created_by_actor TEXT,
    parent_task_id TEXT,
    originating_task_id TEXT,
    cancellation_json TEXT,
    task_kind TEXT NOT NULL DEFAULT 'support',
    outcome_contract_json TEXT,
    integration_boundary INTEGER NOT NULL DEFAULT 0,
    deliverable_contract_json TEXT,
    completion_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_tasks_owner_state_priority
    ON team_tasks(owner_agent_id, state, priority, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_tasks_project_state
    ON team_tasks(project_id, state, priority, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_tasks_parent ON team_tasks(parent_task_id, id);

CREATE TABLE IF NOT EXISTS team_task_dependencies (
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    dependency_task_id TEXT NOT NULL,
    PRIMARY KEY(task_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_task_dependencies_dependency
    ON team_task_dependencies(dependency_task_id);

CREATE TABLE IF NOT EXISTS team_task_deliverables (
    task_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    deliverable_id TEXT NOT NULL,
    PRIMARY KEY(task_id, position)
);

CREATE TABLE IF NOT EXISTS team_deliverables (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    producer_agent_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    state TEXT NOT NULL,
    branch_context_id TEXT,
    agent_branch TEXT,
    review_branch TEXT,
    feature_branch TEXT,
    release_branch TEXT,
    commit_sha TEXT,
    submitted_commit_sha TEXT,
    reviewed_commit_sha TEXT,
    feature_commit_sha TEXT,
    release_commit_sha TEXT,
    verification_json TEXT,
    outcomes_json TEXT NOT NULL DEFAULT '[]',
    carried_findings_json TEXT NOT NULL DEFAULT '[]',
    review_manifest_json TEXT,
    acceptance_recipient_agent_id TEXT,
    acceptance_decision_json TEXT,
    supersedes_deliverable_id TEXT,
    superseded_by_deliverable_id TEXT,
    supersession_reason TEXT,
    superseded_at TEXT,
    superseded_by_actor TEXT,
    review_cohort_id TEXT,
    review_cohort_sealed_at TEXT,
    rework_requested_at TEXT,
    rework_requested_by_agent_id TEXT,
    rework_reason TEXT,
    completion_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_deliverables_task_state ON team_deliverables(task_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_deliverables_producer ON team_deliverables(producer_agent_id, created_at, id);

CREATE TABLE IF NOT EXISTS team_deliverable_artifacts (
    deliverable_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artifact_ref TEXT NOT NULL,
    PRIMARY KEY(deliverable_id, position)
);

CREATE TABLE IF NOT EXISTS team_deliverable_reviews (
    deliverable_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    review_request_id TEXT NOT NULL,
    PRIMARY KEY(deliverable_id, position)
);

CREATE TABLE IF NOT EXISTS team_deliverable_promotion_records (
    deliverable_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    promotion_record_id TEXT NOT NULL,
    PRIMARY KEY(deliverable_id, position)
);

CREATE TABLE IF NOT EXISTS team_deliverable_sources (
    deliverable_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    source_deliverable_id TEXT NOT NULL,
    PRIMARY KEY(deliverable_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_deliverable_sources_source
    ON team_deliverable_sources(source_deliverable_id, deliverable_id);

CREATE TABLE IF NOT EXISTS team_review_profiles (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    archived INTEGER NOT NULL,
    required_outcome_kinds_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_review_profiles_scope
    ON team_review_profiles(team_id, project_id, archived, id);

CREATE TABLE IF NOT EXISTS team_outcome_kind_definitions (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    reference_requirements TEXT NOT NULL,
    evidence_requirements TEXT NOT NULL,
    archived INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_outcome_kind_definitions_scope_kind
    ON team_outcome_kind_definitions(
        team_id,
        COALESCE(project_id, ''),
        kind
    );
CREATE INDEX IF NOT EXISTS idx_team_outcome_kind_definitions_scope
    ON team_outcome_kind_definitions(team_id, project_id, archived, kind, id);

CREATE TABLE IF NOT EXISTS team_review_profile_roles (
    profile_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    role_id TEXT NOT NULL,
    PRIMARY KEY(profile_id, position)
);

CREATE TABLE IF NOT EXISTS team_review_profile_criteria (
    profile_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    criterion_id TEXT NOT NULL,
    label TEXT NOT NULL,
    description TEXT NOT NULL,
    required INTEGER NOT NULL,
    PRIMARY KEY(profile_id, position)
);

CREATE TABLE IF NOT EXISTS team_review_requests (
    id TEXT PRIMARY KEY,
    deliverable_id TEXT NOT NULL,
    release_package_id TEXT,
    candidate_commit_sha TEXT,
    reviewer_agent_id TEXT NOT NULL,
    requested_by_agent_id TEXT,
    requested_by_actor TEXT,
    state TEXT NOT NULL,
    review_profile_id TEXT,
    criteria_snapshot_json TEXT,
    required_outcome_kind_definitions_json TEXT NOT NULL DEFAULT '[]',
    instructions TEXT,
    reviewed_commit_sha TEXT,
    review_cohort_id TEXT,
    supersedes_review_request_id TEXT,
    superseded_by_review_request_id TEXT,
    retry_reason TEXT,
    retry_requested_by TEXT,
    block_kind TEXT,
    retired_at TEXT,
    retired_by TEXT,
    retirement_reason TEXT,
    created_at TEXT,
    decision_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_review_requests_deliverable_state
    ON team_review_requests(deliverable_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_review_requests_reviewer_state
    ON team_review_requests(reviewer_agent_id, state, id);

CREATE TABLE IF NOT EXISTS team_review_request_findings (
    review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    finding TEXT NOT NULL,
    PRIMARY KEY(review_request_id, position)
);

CREATE TABLE IF NOT EXISTS team_review_request_criteria (
    review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    criterion_id TEXT NOT NULL,
    state TEXT NOT NULL,
    finding TEXT NOT NULL,
    PRIMARY KEY(review_request_id, position)
);

CREATE TABLE IF NOT EXISTS team_inbox (
    id TEXT PRIMARY KEY,
    recipient_agent_id TEXT NOT NULL,
    item_type TEXT NOT NULL,
    sender_agent_id TEXT,
    task_id TEXT,
    response_requested INTEGER NOT NULL DEFAULT 1,
    priority TEXT NOT NULL DEFAULT 'normal',
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_inbox_recipient_state_created
    ON team_inbox(recipient_agent_id, state, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_inbox_task_state ON team_inbox(task_id, state, id);

CREATE TABLE IF NOT EXISTS team_blockers (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    task_id TEXT,
    source_kind TEXT NOT NULL,
    source_id TEXT NOT NULL,
    reporter_agent_id TEXT,
    reporter_actor TEXT,
    reason_code TEXT NOT NULL DEFAULT 'unspecified',
    owner_agent_id TEXT,
    delegate_agent_id TEXT,
    state TEXT NOT NULL,
    route TEXT NOT NULL,
    summary TEXT NOT NULL,
    details TEXT NOT NULL,
    priority TEXT NOT NULL,
    escalation_depth INTEGER NOT NULL DEFAULT 0,
    active_dispatch_id TEXT,
    follow_up_at TEXT,
    last_progress_at TEXT,
    terminal_reference_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_blockers_open_source
    ON team_blockers(team_id, source_kind, source_id) WHERE state = 'open';
CREATE INDEX IF NOT EXISTS idx_team_blockers_owner_state
    ON team_blockers(owner_agent_id, state, updated_at, id);
CREATE INDEX IF NOT EXISTS idx_team_blockers_delegate_state
    ON team_blockers(delegate_agent_id, state, updated_at, id);
CREATE INDEX IF NOT EXISTS idx_team_blockers_dispatch_state
    ON team_blockers(active_dispatch_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_blockers_team_project_state
    ON team_blockers(team_id, project_id, state, updated_at, id);

CREATE TABLE IF NOT EXISTS team_blocker_affected_tasks (
    blocker_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    PRIMARY KEY(blocker_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_blocker_affected_tasks_task
    ON team_blocker_affected_tasks(task_id, blocker_id);

CREATE TABLE IF NOT EXISTS team_blocker_events (
    id TEXT PRIMARY KEY,
    blocker_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor_agent_id TEXT,
    from_agent_id TEXT,
    to_agent_id TEXT,
    dispatch_id TEXT,
    note TEXT NOT NULL,
    reference_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_blocker_events_blocker
    ON team_blocker_events(blocker_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_blocker_events_dispatch
    ON team_blocker_events(dispatch_id, created_at, id);

CREATE TABLE IF NOT EXISTS team_action_records (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    dispatch_id TEXT NOT NULL,
    action_json TEXT NOT NULL,
    success INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    dismissed_at TEXT,
    dismissed_by TEXT,
    dismissed_reason TEXT,
    feedback_delivered_at TEXT,
    feedback_delivered_by_dispatch_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_action_records_agent_dispatch
    ON team_action_records(agent_id, dispatch_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_action_records_attention
    ON team_action_records(success, dismissed_at, created_at, id);

CREATE TABLE IF NOT EXISTS team_memories (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL,
    project_id TEXT,
    codebase_id TEXT,
    agent_id TEXT,
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    body TEXT,
    status TEXT NOT NULL,
    confidence REAL,
    supersedes_memory_id TEXT,
    superseded_by_memory_id TEXT,
    created_by_agent_id TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_memories_team_status_updated
    ON team_memories(team_id, status, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_team_memories_project_status
    ON team_memories(project_id, status, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_team_memories_agent_status
    ON team_memories(agent_id, status, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_team_memories_codebase_status
    ON team_memories(codebase_id, status, updated_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_team_memories_kind_status
    ON team_memories(kind, status, updated_at DESC, id);

CREATE TABLE IF NOT EXISTS team_memory_tags (
    memory_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    tag TEXT NOT NULL,
    PRIMARY KEY(memory_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_memory_tags_tag ON team_memory_tags(tag, memory_id);

CREATE TABLE IF NOT EXISTS team_memory_source_refs (
    memory_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    source_ref TEXT NOT NULL,
    PRIMARY KEY(memory_id, position)
);

CREATE TABLE IF NOT EXISTS team_memory_artifact_refs (
    memory_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artifact_ref TEXT NOT NULL,
    PRIMARY KEY(memory_id, position)
);

CREATE TABLE IF NOT EXISTS team_conversations (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    operator_user_id TEXT NOT NULL,
    operator_display_name TEXT,
    created_by TEXT,
    last_message_preview TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_interaction_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_conversations_recent
    ON team_conversations(last_interaction_at DESC, id);

CREATE TABLE IF NOT EXISTS team_conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    message_type TEXT NOT NULL,
    body TEXT,
    status TEXT NOT NULL,
    dispatch_id TEXT,
    voice_session_id TEXT,
    source_message_id TEXT,
    error TEXT,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_conversation_messages_conversation
    ON team_conversation_messages(conversation_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_conversation_messages_dispatch
    ON team_conversation_messages(dispatch_id, id);

CREATE TABLE IF NOT EXISTS team_external_chat_ingress (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    communication_target_id TEXT NOT NULL,
    source_message_id TEXT NOT NULL,
    source_thread_id TEXT,
    source_author_id TEXT NOT NULL,
    source_author_name TEXT,
    operator_user_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    dispatch_id TEXT NOT NULL,
    allow_team_mutations INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(provider, communication_target_id, source_message_id)
);
CREATE INDEX IF NOT EXISTS idx_team_external_chat_ingress_message
    ON team_external_chat_ingress(message_id, id);

CREATE TABLE IF NOT EXISTS team_external_chat_deliveries (
    id TEXT PRIMARY KEY,
    agent_reply_message_id TEXT NOT NULL,
    operator_message_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    communication_target_id TEXT NOT NULL,
    source_thread_id TEXT,
    status TEXT NOT NULL,
    provider_message_id TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    attempted_at TEXT,
    delivered_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(agent_reply_message_id, communication_target_id)
);
CREATE INDEX IF NOT EXISTS idx_team_external_chat_deliveries_reply
    ON team_external_chat_deliveries(agent_reply_message_id, id);

CREATE TABLE IF NOT EXISTS team_chat_attachments (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT,
    byte_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    sender TEXT NOT NULL,
    created_by_session_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_chat_attachments_message
    ON team_chat_attachments(message_id, created_at, id);

CREATE TABLE IF NOT EXISTS team_chat_attachment_policies (
    directory_path TEXT PRIMARY KEY,
    operator_to_agent_enabled INTEGER,
    agent_to_operator_enabled INTEGER,
    agent_send_requires_approval INTEGER,
    max_attachment_bytes INTEGER,
    max_attachments_per_message INTEGER,
    allowed_mime_types_json TEXT,
    allowed_extensions_json TEXT,
    blocked_extensions_json TEXT,
    updated_by TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_release_packages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    owner_agent_id TEXT,
    summary TEXT,
    supersedes_release_package_id TEXT,
    release_branch TEXT,
    canonical_branch TEXT,
    published_commit_sha TEXT,
    base_commit_sha TEXT,
    candidate_commit_sha TEXT,
    candidate_tree_hash TEXT,
    approved_commit_sha TEXT,
    review_correction_task_id TEXT,
    verification_run_ids_json TEXT NOT NULL DEFAULT '[]',
    review_request_ids_json TEXT NOT NULL DEFAULT '[]',
    manifest_json TEXT NOT NULL DEFAULT '{}',
    gate_failure_json TEXT,
    state TEXT NOT NULL,
    published_at TEXT,
    error TEXT,
    policy_version INTEGER NOT NULL DEFAULT 1,
    verification_waivers_json TEXT NOT NULL DEFAULT '[]',
    scope_validation_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_release_packages_project_state
    ON team_release_packages(project_id, state, created_at, id);

CREATE TABLE IF NOT EXISTS team_release_verification_runs (
    id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_release_deliverables (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    deliverable_id TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_release_artifacts (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artifact_ref TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_release_feature_branches (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    feature_branch TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_release_git_promotions (
    package_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    promotion_record_id TEXT NOT NULL,
    PRIMARY KEY(package_id, position)
);

CREATE TABLE IF NOT EXISTS team_human_review_requests (
    id TEXT PRIMARY KEY,
    release_package_id TEXT NOT NULL,
    state TEXT NOT NULL,
    candidate_commit_sha TEXT NOT NULL,
    candidate_tree_hash TEXT NOT NULL,
    assignment_generation INTEGER NOT NULL,
    preparation_generation INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_human_review_requests_release
    ON team_human_review_requests(release_package_id);
CREATE INDEX IF NOT EXISTS idx_team_human_review_requests_state
    ON team_human_review_requests(state, id);

CREATE TABLE IF NOT EXISTS team_human_review_request_configured_groups (
    human_review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    group_id TEXT NOT NULL,
    PRIMARY KEY(human_review_request_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_request_groups_group
    ON team_human_review_request_configured_groups(
        group_id, human_review_request_id
    );

CREATE TABLE IF NOT EXISTS team_human_review_request_initial_users (
    human_review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY(human_review_request_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_request_initial_users_user
    ON team_human_review_request_initial_users(
        user_id, human_review_request_id
    );

CREATE TABLE IF NOT EXISTS team_human_review_request_current_users (
    human_review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY(human_review_request_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_request_current_users_user
    ON team_human_review_request_current_users(
        user_id, human_review_request_id
    );

CREATE TABLE IF NOT EXISTS team_human_review_request_explicit_users (
    human_review_request_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    PRIMARY KEY(human_review_request_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_request_explicit_users_user
    ON team_human_review_request_explicit_users(
        user_id, human_review_request_id
    );

CREATE TABLE IF NOT EXISTS team_human_review_decisions (
    id TEXT PRIMARY KEY,
    human_review_request_id TEXT NOT NULL,
    reviewer_user_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    request_id TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    UNIQUE(human_review_request_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_decisions_request
    ON team_human_review_decisions(human_review_request_id, decided_at, id);

CREATE TABLE IF NOT EXISTS team_human_review_decision_confirmed_findings (
    human_review_decision_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    finding_id TEXT NOT NULL,
    PRIMARY KEY(human_review_decision_id, position)
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_decision_findings_finding
    ON team_human_review_decision_confirmed_findings(
        finding_id, human_review_decision_id
    );

CREATE TABLE IF NOT EXISTS team_human_review_findings (
    id TEXT PRIMARY KEY,
    source_human_review_request_id TEXT NOT NULL,
    correction_task_id TEXT NOT NULL,
    state TEXT NOT NULL,
    resolved_by_human_review_request_id TEXT,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_human_review_findings_request_state
    ON team_human_review_findings(source_human_review_request_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_human_review_findings_task
    ON team_human_review_findings(correction_task_id, state, id);

CREATE TABLE IF NOT EXISTS team_human_review_finding_targets (
    finding_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    deliverable_id TEXT NOT NULL,
    PRIMARY KEY(finding_id, position)
);

CREATE TABLE IF NOT EXISTS team_human_review_finding_proposals (
    finding_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    deliverable_id TEXT NOT NULL,
    PRIMARY KEY(finding_id, position)
);

CREATE TABLE IF NOT EXISTS team_dispatches (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    cause_kind TEXT NOT NULL,
    cause_id TEXT,
    review_routing_authorities_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    prepared_at TEXT,
    leased_at TEXT,
    lease_expires_at TEXT,
    completed_at TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_team_dispatches_agent_status
    ON team_dispatches(agent_id, status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_dispatches_session_status
    ON team_dispatches(session_id, status, created_at, id);

CREATE TABLE IF NOT EXISTS team_dispatch_tasks (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    PRIMARY KEY(dispatch_id, position)
);
CREATE TABLE IF NOT EXISTS team_dispatch_blockers (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    blocker_id TEXT NOT NULL,
    PRIMARY KEY(dispatch_id, position)
);
CREATE TABLE IF NOT EXISTS team_dispatch_reviews (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    review_request_id TEXT NOT NULL,
    PRIMARY KEY(dispatch_id, position)
);
CREATE TABLE IF NOT EXISTS team_dispatch_inbox (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    inbox_item_id TEXT NOT NULL,
    PRIMARY KEY(dispatch_id, position)
);
CREATE TABLE IF NOT EXISTS team_dispatch_projects (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    project_id TEXT NOT NULL,
    PRIMARY KEY(dispatch_id, position)
);
CREATE TABLE IF NOT EXISTS team_dispatch_repositories (
    dispatch_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    managed_repo_id TEXT NOT NULL,
    PRIMARY KEY(dispatch_id, position)
);

CREATE TABLE IF NOT EXISTS team_worktree_cleanup_candidates (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    managed_repo_id TEXT NOT NULL,
    worktree_path TEXT,
    state TEXT NOT NULL,
    eligible_at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_worktree_cleanup_identity_v2
    ON team_worktree_cleanup_candidates(
        task_id, managed_repo_id, session_id, worktree_path
    );
CREATE INDEX IF NOT EXISTS idx_team_worktree_cleanup_state_eligible
    ON team_worktree_cleanup_candidates(state, eligible_at, id);

CREATE TABLE IF NOT EXISTS team_ephemeral_assignments (
    id TEXT PRIMARY KEY,
    parent_agent_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    state TEXT NOT NULL,
    agent_branch TEXT,
    commit_sha TEXT,
    summary TEXT,
    error TEXT,
    model TEXT,
    agent_provider TEXT,
    execution_backend TEXT,
    container_image TEXT,
    ttl_hours INTEGER,
    directory_path TEXT,
    metadata_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_ephemeral_assignments_parent_state
    ON team_ephemeral_assignments(parent_agent_id, state, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_ephemeral_assignments_session
    ON team_ephemeral_assignments(session_id);
CREATE INDEX IF NOT EXISTS idx_team_ephemeral_assignments_project_task
    ON team_ephemeral_assignments(project_id, task_id, state, id);

CREATE TABLE IF NOT EXISTS team_ephemeral_assignment_artifacts (
    assignment_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    artifact_ref TEXT NOT NULL,
    PRIMARY KEY(assignment_id, position)
);

CREATE TABLE IF NOT EXISTS team_ephemeral_assignment_allowed_tools (
    assignment_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    tool TEXT NOT NULL,
    PRIMARY KEY(assignment_id, position)
);

CREATE TABLE IF NOT EXISTS team_git_promotions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    actor_agent_id TEXT NOT NULL,
    source_branch TEXT NOT NULL,
    sources_json TEXT NOT NULL DEFAULT '[]',
    target_branch TEXT NOT NULL,
    promotion_kind TEXT NOT NULL,
    task_id TEXT,
    deliverable_id TEXT,
    release_package_id TEXT,
    state TEXT NOT NULL,
    commit_sha TEXT,
    outputs_json TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_git_promotions_project_state
    ON team_git_promotions(project_id, state, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_git_promotions_deliverable
    ON team_git_promotions(deliverable_id, promotion_kind, state, id);
CREATE INDEX IF NOT EXISTS idx_team_git_promotions_release
    ON team_git_promotions(release_package_id, promotion_kind, state, id);

CREATE TABLE IF NOT EXISTS team_git_work_contexts (
    id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    dispatch_id TEXT,
    worktree_path TEXT,
    base_branch TEXT,
    base_commit TEXT,
    agent_branch TEXT NOT NULL,
    commit_sha TEXT,
    dependency_git_sources_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_git_work_contexts_agent_task
    ON team_git_work_contexts(agent_id, task_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_git_work_contexts_dispatch
    ON team_git_work_contexts(dispatch_id, state, id);
CREATE INDEX IF NOT EXISTS idx_team_git_work_contexts_branch
    ON team_git_work_contexts(agent_branch, state, id);

CREATE TABLE IF NOT EXISTS team_dispatch_activity (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    caller_session_id TEXT NOT NULL,
    caller_identity TEXT NOT NULL,
    agent_id TEXT,
    governance_group_id TEXT,
    assignment_id TEXT,
    dispatch_id TEXT,
    request_id TEXT,
    correlation_id TEXT,
    causation_id TEXT,
    status TEXT NOT NULL,
    progress_classification TEXT NOT NULL,
    action_record_id TEXT,
    audit_record_id TEXT,
    error_code TEXT,
    error_message TEXT,
    correction_context_summary TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_dispatch_activity_dispatch
    ON team_dispatch_activity(dispatch_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_dispatch_activity_caller
    ON team_dispatch_activity(caller_identity, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_dispatch_activity_session
    ON team_dispatch_activity(caller_session_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_dispatch_activity_request
    ON team_dispatch_activity(tool_name, caller_identity, dispatch_id, request_id);

CREATE TABLE IF NOT EXISTS team_idempotency_records (
    id TEXT PRIMARY KEY,
    tool_name TEXT NOT NULL,
    caller_identity TEXT NOT NULL,
    scope_kind TEXT NOT NULL,
    scope_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    normalized_payload_digest TEXT NOT NULL,
    response_payload_json TEXT NOT NULL,
    created_domain_ids_json TEXT NOT NULL,
    created_event_ids_json TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(tool_name, caller_identity, scope_kind, scope_id, request_id)
);
CREATE INDEX IF NOT EXISTS idx_team_idempotency_records_scope
    ON team_idempotency_records(tool_name, caller_identity, scope_kind, scope_id, request_id);

CREATE TABLE IF NOT EXISTS team_outbox (
    id TEXT PRIMARY KEY,
    intent_type TEXT NOT NULL,
    aggregate_type TEXT,
    aggregate_id TEXT,
    idempotency_key TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL,
    available_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    claimed_at TEXT,
    claimed_by TEXT,
    claim_token TEXT,
    claim_expires_at TEXT,
    completed_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_team_outbox_idempotency_key
    ON team_outbox(idempotency_key)
    WHERE idempotency_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_team_outbox_claimable
    ON team_outbox(status, available_at, created_at, id);
CREATE INDEX IF NOT EXISTS idx_team_outbox_claim_token
    ON team_outbox(claim_token, status, id);
CREATE INDEX IF NOT EXISTS idx_team_outbox_aggregate
    ON team_outbox(aggregate_type, aggregate_id, id);
"""


__all__ = [
    "LEGACY_STATE_FILE",
    "STATE_DB_FILE",
    "TeamIdempotencyConflictError",
    "TeamStateSqliteStore",
    "normalized_payload_digest",
]
