"""Team journal."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from evander.models import utc_now

from .models import TeamDispatchActivity, TeamIdempotencyRecord, TeamOutboxRecord, TeamOutboxStatus
from .sqlite_codec import (
    _datetime_text,
    _idempotency_key_values,
    _json_dumps,
    _json_loads,
    _optional_text,
    _outbox_status_value,
    _raise_idempotency_conflict,
    _required_text,
)


class CommandJournalStoreMixin:
    """Command journal, idempotency, and outbox operations."""

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
        record_ids: Sequence[str] | None = None,
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
        normalized_record_ids = list(
            dict.fromkeys(
                _required_text(record_id, "record_id") for record_id in (record_ids or [])
            )
        )
        if record_ids is not None and not normalized_record_ids:
            return []

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
                if normalized_record_ids:
                    placeholders = ", ".join("?" for _ in normalized_record_ids)
                    clauses.append(f"id IN ({placeholders})")
                    params.extend(normalized_record_ids)
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
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_team_dispatch_activity_retention'"
        ).fetchone() is not None:
            connection.execute(
                "DELETE FROM team_dispatch_activity WHERE id IN ("
                "SELECT id FROM team_dispatch_activity WHERE created_at < ? "
                "ORDER BY created_at, id LIMIT 100)",
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
