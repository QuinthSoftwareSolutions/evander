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
from .durable_operations import SQLiteDurableOperationLedger
from .execution_ownership import process_is_alive
from .state import ADDON_ID


class BokioOperationError(RuntimeError):
    """Raised when a durable Bokio operation cannot safely continue."""


class BokioOperationStore:
    """Durable idempotency ledger for direct Bokio write tools."""

    def __init__(self, runtime_dir: str | Path) -> None:
        self.database_path = addon_storage_path(runtime_dir, ADDON_ID, "operations.db")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._ledger = SQLiteDurableOperationLedger(
            self.database_path,
            table="operations",
            identity_columns=("session_id", "operation_kind", "operation_key"),
        )
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
            self._ledger.begin_immediate(connection)
            row = self._ledger.fetch_row(connection, identity)
            if row is not None:
                operation = _operation_from_row(row)
                if operation["payload_digest"] != payload_digest:
                    raise BokioOperationError(
                        "operation_key is already associated with a different Bokio request "
                        "for this session"
                    )
                return operation
            now = _now()
            self._ledger.insert(
                connection,
                {
                    "session_id": identity[0],
                    "operation_kind": identity[1],
                    "operation_key": identity[2],
                    "payload_json": payload_json,
                    "payload_digest": payload_digest,
                    "status": "pending",
                    "attempt_count": 0,
                    "created_at": now,
                    "updated_at": now,
                },
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
            self._ledger.begin_immediate(connection)
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
            claimed = self._ledger.transition(
                connection,
                identity,
                expected_status=allowed_status,
                new_status="executing",
                values={
                    "error_json": None,
                    "result_json": None,
                    "completed_at": None,
                    "started_at": now,
                    "updated_at": now,
                    "execution_owner_pid": os.getpid(),
                },
                increment_columns=("attempt_count",),
            )
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
            updated = self._ledger.transition(
                connection,
                identity,
                expected_status="executing",
                new_status=status,
                values={
                    "result_json": _json_dump(result),
                    "error_json": _json_dump(error),
                    "completed_at": now,
                    "updated_at": now,
                    "execution_owner_pid": None,
                },
            )
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
        return self._ledger.connect()

    def _get_with_connection(
        self,
        connection: sqlite3.Connection,
        identity: tuple[str, str, str],
    ) -> dict[str, Any]:
        row = self._ledger.fetch_row(connection, identity)
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


def _optional_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


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
        payload = {**exc.to_payload(), "error_class": exc.__class__.__name__}
        payload["operator_message"] = bokio_operator_error_message(payload)
        return payload
    return {"error": str(exc), "error_class": exc.__class__.__name__}


def bokio_operator_error_message(error: Mapping[str, Any]) -> str:
    """Turn a structured Bokio failure into concise operator-facing guidance."""

    upstream_payload = error.get("payload")
    upstream = upstream_payload if isinstance(upstream_payload, Mapping) else {}
    validation_errors = upstream.get("errors")
    details: list[str] = []
    if isinstance(validation_errors, list):
        for item in validation_errors:
            if not isinstance(item, Mapping):
                continue
            field = _optional_text(item.get("field"))
            message = _optional_text(item.get("message"))
            if field and message:
                details.append(f"{field} - {message}")
            elif field or message:
                details.append(field or message or "")

    upstream_message = _optional_text(error.get("error")) or "Bokio request failed"
    status_code = error.get("status_code")
    status_text = f" (HTTP {status_code})" if isinstance(status_code, int) else ""
    if details:
        message = f"Bokio validation failed{status_text}: {'; '.join(details)}."
    else:
        message = f"{upstream_message}{status_text}."

    evidence_operation = error.get("evidence_operation")
    operation = evidence_operation if isinstance(evidence_operation, Mapping) else {}
    journal_entry_id = _optional_text(operation.get("journal_entry_id"))
    if not journal_entry_id and not bool(error.get("journal_entry_created")):
        return message

    result = operation.get("result")
    result_mapping = result if isinstance(result, Mapping) else {}
    entry_number = _optional_text(result_mapping.get("journalEntryNumber"))
    identifier = (
        f"journal entry {entry_number} ({journal_entry_id})"
        if entry_number
        else f"journal entry {journal_entry_id}"
        if journal_entry_id
        else "the journal entry"
    )
    associations = operation.get("associations")
    association_rows = associations if isinstance(associations, list) else []
    attached_count = sum(
        1
        for item in association_rows
        if isinstance(item, Mapping) and item.get("status") == "attached"
    )
    failed_count = sum(
        1
        for item in association_rows
        if isinstance(item, Mapping) and item.get("status") == "failed"
    )
    attachment_status = ""
    if attached_count or failed_count:
        attachment_status = (
            f" {attached_count} evidence file{' was' if attached_count == 1 else 's were'} "
            f"attached and {failed_count} failed."
        )
    return (
        f"{message} {identifier[:1].upper() + identifier[1:]} was created; only evidence attachment failed."
        f"{attachment_status} A retry resumes evidence attachment and will not create another "
        "journal entry."
    )
