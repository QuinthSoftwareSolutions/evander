from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from evander.addons import addon_storage_path

from .catalog import ADDON_ID, CATALOG_BY_NAME, STATE_DB, SUBMISSION_API_NAMES


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class SkatteverketStateError(ValueError):
    pass


class SkatteverketStateConflictError(SkatteverketStateError):
    pass


@dataclass(frozen=True)
class DraftSnapshot:
    draft_id: str
    api_name: str
    status: str
    revision: int
    period: str | None
    subject_id: str | None
    payload: dict[str, Any]
    source_session_id: str | None

    def approval_payload(self) -> dict[str, Any]:
        return {
            "addon_id": ADDON_ID,
            "operation": "submit",
            "draft_id": self.draft_id,
            "draft_revision": self.revision,
            "api_name": self.api_name,
        }


class SkatteverketRepository:
    def __init__(self, runtime_dir: str | Path) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.path = addon_storage_path(self.runtime_dir, ADDON_ID, STATE_DB)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS connections (
                    connection_id TEXT PRIMARY KEY,
                    environment TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    base_url TEXT,
                    token_url TEXT,
                    client_id_env TEXT,
                    client_secret_env TEXT,
                    apigw_client_id_env TEXT,
                    apigw_client_secret_env TEXT,
                    api_key_env TEXT,
                    certificate_subject TEXT,
                    redirect_uri TEXT,
                    submission_endpoint TEXT,
                    enabled_api_names_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS drafts (
                    draft_id TEXT PRIMARY KEY,
                    api_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    period TEXT,
                    subject_id TEXT,
                    payload_json TEXT NOT NULL,
                    validation_json TEXT,
                    source_session_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    submitted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS submissions (
                    submission_id TEXT PRIMARY KEY,
                    draft_id TEXT NOT NULL,
                    api_name TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    request_summary_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    approval_request_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_skatteverket_submissions_idempotency
                    ON submissions(idempotency_key);
                CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    subject_id TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )
            _ensure_columns(
                conn,
                "connections",
                {
                    "apigw_client_id_env": "TEXT",
                    "apigw_client_secret_env": "TEXT",
                },
            )

    def state_payload(self) -> dict[str, Any]:
        return {
            "addon_id": ADDON_ID,
            "connections": self.list_connections(),
            "drafts": self.list_drafts(),
            "recent_submissions": self.list_submissions(limit=20),
        }

    def upsert_connection(self, payload: dict[str, Any]) -> dict[str, Any]:
        now = utc_now_iso()
        connection_id = _text(payload.get("connection_id") or payload.get("id")) or "default"
        environment = _text(payload.get("environment")) or "sandbox"
        display_name = _text(payload.get("display_name")) or connection_id
        enabled_api_names = _string_list(payload.get("enabled_api_names"))
        if not enabled_api_names:
            enabled_api_names = sorted(CATALOG_BY_NAME)
        unknown = sorted(name for name in enabled_api_names if name not in CATALOG_BY_NAME)
        if unknown:
            raise SkatteverketStateError(f"unknown Skatteverket API name(s): {', '.join(unknown)}")
        enabled = _bool(payload.get("enabled"), default=True)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT created_at FROM connections WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO connections (
                    connection_id, environment, display_name, base_url, token_url,
                    client_id_env, client_secret_env, apigw_client_id_env,
                    apigw_client_secret_env, api_key_env, certificate_subject, redirect_uri,
                    submission_endpoint, enabled_api_names_json, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(connection_id) DO UPDATE SET
                    environment = excluded.environment,
                    display_name = excluded.display_name,
                    base_url = excluded.base_url,
                    token_url = excluded.token_url,
                    client_id_env = excluded.client_id_env,
                    client_secret_env = excluded.client_secret_env,
                    apigw_client_id_env = excluded.apigw_client_id_env,
                    apigw_client_secret_env = excluded.apigw_client_secret_env,
                    api_key_env = excluded.api_key_env,
                    certificate_subject = excluded.certificate_subject,
                    redirect_uri = excluded.redirect_uri,
                    submission_endpoint = excluded.submission_endpoint,
                    enabled_api_names_json = excluded.enabled_api_names_json,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    connection_id,
                    environment,
                    display_name,
                    _text(payload.get("base_url")),
                    _text(payload.get("token_url")),
                    _text(payload.get("client_id_env")),
                    _text(payload.get("client_secret_env")),
                    _text(payload.get("apigw_client_id_env")),
                    _text(payload.get("apigw_client_secret_env")),
                    _text(payload.get("api_key_env")),
                    _text(payload.get("certificate_subject")),
                    _text(payload.get("redirect_uri")),
                    _text(payload.get("submission_endpoint")),
                    json.dumps(enabled_api_names, sort_keys=True),
                    1 if enabled else 0,
                    existing["created_at"] if existing else now,
                    now,
                ),
            )
            self._record_audit(
                conn,
                "connection_upserted",
                connection_id,
                {
                    "connection_id": connection_id,
                    "environment": environment,
                    "enabled_api_names": enabled_api_names,
                    "enabled": enabled,
                },
            )
        return self.get_connection(connection_id) or {}

    def list_connections(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM connections ORDER BY connection_id"
            ).fetchall()
        return [_connection_payload(row) for row in rows]

    def get_connection(self, connection_id: str = "default") -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM connections WHERE connection_id = ?",
                (connection_id,),
            ).fetchone()
        return _connection_payload(row) if row is not None else None

    def default_connection(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM connections
                WHERE enabled = 1
                ORDER BY CASE WHEN connection_id = 'default' THEN 0 ELSE 1 END, connection_id
                LIMIT 1
                """
            ).fetchone()
        return _connection_payload(row) if row is not None else None

    def create_draft(
        self,
        *,
        api_name: str,
        payload: dict[str, Any],
        period: str | None = None,
        subject_id: str | None = None,
        source_session_id: str | None = None,
    ) -> dict[str, Any]:
        api_name = _require_api_name(api_name)
        if not isinstance(payload, dict):
            raise SkatteverketStateError("payload must be a JSON object")
        now = utc_now_iso()
        draft_id = f"skv-draft-{uuid4().hex[:16]}"
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO drafts (
                    draft_id, api_name, status, revision, period, subject_id, payload_json,
                    validation_json, source_session_id, created_at, updated_at
                ) VALUES (?, ?, 'draft', 1, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    draft_id,
                    api_name,
                    _text(period),
                    _text(subject_id),
                    json.dumps(payload, sort_keys=True),
                    _text(source_session_id),
                    now,
                    now,
                ),
            )
            self._record_audit(
                conn,
                "draft_created",
                draft_id,
                {"draft_id": draft_id, "api_name": api_name, "period": period},
            )
        return self.get_draft_payload(draft_id)

    def get_draft(self, draft_id: str) -> DraftSnapshot:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM drafts WHERE draft_id = ?",
                (_required_text(draft_id, "draft_id"),),
            ).fetchone()
        if row is None:
            raise SkatteverketStateError(f"unknown draft_id: {draft_id}")
        return _draft_snapshot(row)

    def get_draft_payload(self, draft_id: str) -> dict[str, Any]:
        return _draft_payload(self.get_draft(draft_id))

    def list_drafts(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM drafts ORDER BY updated_at DESC, draft_id DESC"
            ).fetchall()
        return [_draft_payload(_draft_snapshot(row)) for row in rows]

    def validate_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        errors: list[str] = []
        entry = CATALOG_BY_NAME.get(draft.api_name)
        if entry is None:
            errors.append(f"unknown Skatteverket API: {draft.api_name}")
        elif "validate" not in entry.implemented_operations:
            errors.append(f"validation adapter is not implemented for {draft.api_name}")
        if not isinstance(draft.payload, dict) or not draft.payload:
            errors.append("payload must be a non-empty JSON object")
        if draft.api_name in SUBMISSION_API_NAMES and not draft.period:
            errors.append("period is required for submission drafts")
        validation = {
            "valid": not errors,
            "errors": errors,
            "validated_at": utc_now_iso(),
        }
        status = "validated" if validation["valid"] else "validation_failed"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE drafts
                SET status = ?, revision = revision + 1, validation_json = ?, updated_at = ?
                WHERE draft_id = ?
                """,
                (status, json.dumps(validation, sort_keys=True), utc_now_iso(), draft.draft_id),
            )
            self._record_audit(
                conn,
                "draft_validated",
                draft.draft_id,
                {"draft_id": draft.draft_id, "valid": validation["valid"], "errors": errors},
            )
        return self.get_draft_payload(draft.draft_id)

    def record_submission(
        self,
        *,
        draft_id: str,
        approval_request_id: str,
        response: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        draft = self.get_draft(draft_id)
        if draft.status != "validated":
            raise SkatteverketStateConflictError(
                f"draft {draft_id} must be validated before submission; current status is {draft.status}"
            )
        now = utc_now_iso()
        submission_id = f"skv-submission-{uuid4().hex[:16]}"
        idempotency_key = idempotency_key or f"{draft.draft_id}:{draft.revision}"
        request_summary = {
            "draft_id": draft.draft_id,
            "api_name": draft.api_name,
            "draft_revision": draft.revision,
        }
        status = str(response.get("status") or "submitted").strip() or "submitted"
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO submissions (
                        submission_id, draft_id, api_name, idempotency_key, status,
                        request_summary_json, response_json, approval_request_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        submission_id,
                        draft.draft_id,
                        draft.api_name,
                        idempotency_key,
                        status,
                        json.dumps(request_summary, sort_keys=True),
                        json.dumps(response, sort_keys=True),
                        approval_request_id,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise SkatteverketStateConflictError(
                    f"submission idempotency key has already been used: {idempotency_key}"
                ) from exc
            conn.execute(
                """
                UPDATE drafts
                SET status = ?, revision = revision + 1, submitted_at = ?, updated_at = ?
                WHERE draft_id = ?
                """,
                (status, now, now, draft.draft_id),
            )
            self._record_audit(
                conn,
                "draft_submitted",
                draft.draft_id,
                {
                    "draft_id": draft.draft_id,
                    "submission_id": submission_id,
                    "status": status,
                    "approval_request_id": approval_request_id,
                },
            )
        return self.get_submission(submission_id)

    def get_submission(self, submission_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM submissions WHERE submission_id = ?",
                (_required_text(submission_id, "submission_id"),),
            ).fetchone()
        if row is None:
            raise SkatteverketStateError(f"unknown submission_id: {submission_id}")
        return _submission_payload(row)

    def list_submissions(self, *, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM submissions
                ORDER BY created_at DESC, submission_id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [_submission_payload(row) for row in rows]

    def _record_audit(
        self, conn: sqlite3.Connection, event_type: str, subject_id: str | None, payload: dict[str, Any]
    ) -> None:
        conn.execute(
            """
            INSERT INTO audit_events (event_id, event_type, subject_id, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                f"skv-event-{uuid4().hex[:16]}",
                event_type,
                subject_id,
                json.dumps(payload, sort_keys=True),
                utc_now_iso(),
            ),
        )


def _connection_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "connection_id": row["connection_id"],
        "environment": row["environment"],
        "display_name": row["display_name"],
        "base_url": row["base_url"],
        "token_url": row["token_url"],
        "client_id_env": row["client_id_env"],
        "client_secret_env": row["client_secret_env"],
        "apigw_client_id_env": row["apigw_client_id_env"],
        "apigw_client_secret_env": row["apigw_client_secret_env"],
        "api_key_env": row["api_key_env"],
        "certificate_subject": row["certificate_subject"],
        "redirect_uri": row["redirect_uri"],
        "submission_endpoint": row["submission_endpoint"],
        "enabled_api_names": _loads_list(row["enabled_api_names_json"]),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _ensure_columns(conn: sqlite3.Connection, table_name: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}
    for column_name, definition in columns.items():
        if column_name not in existing:
            conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def _draft_snapshot(row: sqlite3.Row) -> DraftSnapshot:
    return DraftSnapshot(
        draft_id=row["draft_id"],
        api_name=row["api_name"],
        status=row["status"],
        revision=int(row["revision"]),
        period=row["period"],
        subject_id=row["subject_id"],
        payload=_loads_dict(row["payload_json"]),
        source_session_id=row["source_session_id"],
    )


def _draft_payload(draft: DraftSnapshot) -> dict[str, Any]:
    return {
        "draft_id": draft.draft_id,
        "api_name": draft.api_name,
        "status": draft.status,
        "revision": draft.revision,
        "period": draft.period,
        "subject_id": draft.subject_id,
        "payload": draft.payload,
        "source_session_id": draft.source_session_id,
    }


def _submission_payload(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "submission_id": row["submission_id"],
        "draft_id": row["draft_id"],
        "api_name": row["api_name"],
        "idempotency_key": row["idempotency_key"],
        "status": row["status"],
        "request_summary": _loads_dict(row["request_summary_json"]),
        "response": _loads_dict(row["response_json"]),
        "approval_request_id": row["approval_request_id"],
        "created_at": row["created_at"],
    }


def _require_api_name(value: str) -> str:
    api_name = _required_text(value, "api_name")
    if api_name not in CATALOG_BY_NAME:
        raise SkatteverketStateError(f"unknown Skatteverket API: {api_name}")
    return api_name


def _required_text(value: Any, field_name: str) -> str:
    text = _text(value)
    if not text:
        raise SkatteverketStateError(f"{field_name} is required")
    return text


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise SkatteverketStateError("enabled must be a boolean")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise SkatteverketStateError("enabled_api_names must be a JSON array")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _loads_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    loaded = json.loads(value)
    return loaded if isinstance(loaded, dict) else {}


def _loads_list(value: str | None) -> list[str]:
    if not value:
        return []
    loaded = json.loads(value)
    return [str(item) for item in loaded] if isinstance(loaded, list) else []
