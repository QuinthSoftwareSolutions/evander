from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import httpx

from evander.addons import addon_storage_path

from .client import BokioApiError, BokioConfigError
from .execution_ownership import process_is_alive
from .state import ADDON_ID


class BokioOperationError(RuntimeError):
    """Raised when a durable Bokio operation cannot safely continue."""


class BokioOperationStore:
    """Durable idempotency ledger for direct Bokio write tools."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.database_path = addon_storage_path(runtime_dir, ADDON_ID, "operations.db")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def begin(
        self,
        *,
        session_id: str,
        operation_kind: str,
        operation_key: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        identity = _identity(session_id, operation_kind, operation_key)
        normalized_payload = _json_object(payload)
        payload_json = json.dumps(normalized_payload, sort_keys=True, separators=(",", ":"))
        payload_digest = sha256(payload_json.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM operations
                WHERE session_id = ? AND operation_kind = ? AND operation_key = ?
                """,
                identity,
            ).fetchone()
            if row is not None:
                operation = _operation_from_row(row)
                if operation["payload_digest"] != payload_digest:
                    raise BokioOperationError(
                        "operation_key is already associated with a different Bokio request "
                        "for this session"
                    )
                return operation
            now = _now()
            connection.execute(
                """
                INSERT INTO operations (
                    session_id, operation_kind, operation_key, payload_json, payload_digest,
                    status, attempt_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*identity, payload_json, payload_digest, "pending", 0, now, now),
            )
            return self._get_with_connection(connection, identity)

    def claim(
        self,
        *,
        session_id: str,
        operation_kind: str,
        operation_key: str,
        retry_if_failed: bool = False,
        confirm_not_created: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        identity = _identity(session_id, operation_kind, operation_key)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            operation = self._get_with_connection(connection, identity)
            status = str(operation["status"])
            if status == "succeeded":
                return operation, False
            allowed_status: str | None = None
            if status == "pending":
                allowed_status = "pending"
            elif status == "failed":
                if not retry_if_failed:
                    raise BokioOperationError(
                        "Bokio operation previously failed; set retry_if_failed to make a new attempt"
                    )
                allowed_status = "failed"
            elif status == "needs_attention":
                if not confirm_not_created:
                    raise BokioOperationError(
                        "Bokio operation outcome is unknown; inspect Bokio and set "
                        "confirm_not_created only after confirming the supplier invoice is absent"
                    )
                allowed_status = "needs_attention"
            elif status == "executing":
                if not confirm_not_created:
                    raise BokioOperationError(
                        "Bokio operation is already executing or its outcome is unknown; inspect "
                        "Bokio and set confirm_not_created only after confirming the supplier "
                        "invoice is absent"
                    )
                owner_alive = process_is_alive(operation.get("execution_owner_pid"))
                if owner_alive is None:
                    raise BokioOperationError(
                        "Bokio operation cannot be safely recovered because its execution owner "
                        "is unknown; automatic recovery is unavailable"
                    )
                if owner_alive:
                    raise BokioOperationError(
                        "Bokio operation is still executing in a live process; wait for that "
                        "attempt to finish before reconciling it"
                    )
                allowed_status = "executing"
            if allowed_status is None:
                raise BokioOperationError(f"Unsupported Bokio operation status: {status}")
            now = _now()
            claimed = connection.execute(
                """
                UPDATE operations
                SET status = ?, attempt_count = attempt_count + 1, error_json = NULL,
                    result_json = NULL, completed_at = NULL, started_at = ?, updated_at = ?,
                    execution_owner_pid = ?
                WHERE session_id = ? AND operation_kind = ? AND operation_key = ? AND status = ?
                """,
                ("executing", now, now, os.getpid(), *identity, allowed_status),
            ).rowcount
            current = self._get_with_connection(connection, identity)
            if claimed != 1:
                if current["status"] == "succeeded":
                    return current, False
                raise BokioOperationError(
                    "Bokio operation was claimed concurrently; inspect its current state"
                )
            return current, True

    def mark_succeeded(
        self,
        *,
        session_id: str,
        operation_kind: str,
        operation_key: str,
        result: Any,
    ) -> dict[str, Any]:
        return self._finish(
            identity=_identity(session_id, operation_kind, operation_key),
            status="succeeded",
            result=result,
            error=None,
        )

    def mark_failed(
        self,
        *,
        session_id: str,
        operation_kind: str,
        operation_key: str,
        error: Any,
        uncertain: bool,
    ) -> dict[str, Any]:
        return self._finish(
            identity=_identity(session_id, operation_kind, operation_key),
            status="needs_attention" if uncertain else "failed",
            result=None,
            error=error,
        )

    def list(
        self,
        *,
        status: str | None = None,
        operation_kind: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if str(status or "").strip():
            clauses.append("status = ?")
            parameters.append(str(status).strip())
        if str(operation_kind or "").strip():
            clauses.append("operation_kind = ?")
            parameters.append(str(operation_kind).strip())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        bounded_limit = min(500, max(1, int(limit or 100)))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM operations{where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
                (*parameters, bounded_limit),
            ).fetchall()
            return [_operation_from_row(row) for row in rows]

    def _finish(
        self,
        *,
        identity: tuple[str, str, str],
        status: str,
        result: Any,
        error: Any,
    ) -> dict[str, Any]:
        now = _now()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE operations
                SET status = ?, result_json = ?, error_json = ?, completed_at = ?, updated_at = ?,
                    execution_owner_pid = NULL
                WHERE session_id = ? AND operation_kind = ? AND operation_key = ?
                    AND status = ?
                """,
                (
                    status,
                    _json_dump(result),
                    _json_dump(error),
                    now,
                    now,
                    *identity,
                    "executing",
                ),
            ).rowcount
            if updated != 1:
                raise BokioOperationError("Bokio operation is not owned by this execution attempt")
            return self._get_with_connection(connection, identity)

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS operations (
                    session_id TEXT NOT NULL,
                    operation_kind TEXT NOT NULL,
                    operation_key TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    result_json TEXT,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    execution_owner_pid INTEGER,
                    PRIMARY KEY (session_id, operation_kind, operation_key)
                )
                """
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(operations)")}
            if "execution_owner_pid" not in columns:
                connection.execute("ALTER TABLE operations ADD COLUMN execution_owner_pid INTEGER")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _get_with_connection(
        self,
        connection: sqlite3.Connection,
        identity: tuple[str, str, str],
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT * FROM operations
            WHERE session_id = ? AND operation_kind = ? AND operation_key = ?
            """,
            identity,
        ).fetchone()
        if row is None:
            raise BokioOperationError("Unknown Bokio operation")
        return _operation_from_row(row)


def _identity(session_id: str, operation_kind: str, operation_key: str) -> tuple[str, str, str]:
    key = _required_text(operation_key, "operation_key")
    if len(key) > 256:
        raise BokioOperationError("operation_key must be at most 256 characters")
    return (
        _required_text(session_id, "session_id"),
        _required_text(operation_kind, "operation_kind"),
        key,
    )


def _required_text(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise BokioOperationError(f"{field_name} is required")
    return normalized


def _json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise BokioOperationError("payload must be a JSON object")
    try:
        encoded = json.dumps(dict(value), sort_keys=True, separators=(",", ":"))
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise BokioOperationError("payload must contain only JSON-compatible values") from exc
    return decoded


def _json_dump(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, sort_keys=True, default=str)


def _operation_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "session_id": str(row["session_id"]),
        "operation_kind": str(row["operation_kind"]),
        "operation_key": str(row["operation_key"]),
        "payload": json.loads(str(row["payload_json"])),
        "payload_digest": str(row["payload_digest"]),
        "status": str(row["status"]),
        "attempt_count": int(row["attempt_count"]),
        "result": json.loads(str(row["result_json"])) if row["result_json"] else None,
        "error": json.loads(str(row["error_json"])) if row["error_json"] else None,
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "started_at": str(row["started_at"]) if row["started_at"] else None,
        "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
        "execution_owner_pid": (
            int(row["execution_owner_pid"]) if row["execution_owner_pid"] is not None else None
        ),
    }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def bokio_write_failure_is_uncertain(exc: Exception) -> bool:
    """Return whether a failed write may still have reached Bokio."""

    if isinstance(exc, BokioConfigError | ValueError | TypeError):
        return False
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout):
        return False
    if isinstance(exc, BokioApiError):
        return exc.status_code is None or 200 <= exc.status_code < 300 or exc.status_code >= 500
    return True


def bokio_error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, BokioApiError):
        return {**exc.to_payload(), "error_class": exc.__class__.__name__}
    return {"error": str(exc), "error_class": exc.__class__.__name__}
