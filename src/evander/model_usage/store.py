from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any

from evander.model_usage.models import (
    ModelInferenceUsageEvent,
    ModelUsageCorrelationContext,
)

SQLITE_BUSY_TIMEOUT_MS = 30_000
_SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}


def model_usage_store_db_path(path: Path) -> Path:
    candidate = Path(path)
    if candidate.suffix.lower() in _SQLITE_SUFFIXES:
        return candidate
    return candidate.with_suffix(".db")


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _storage_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _storage_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _ensure_columns(
    connection: sqlite3.Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    existing = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table_name})")
    }
    for column_name, column_type in columns.items():
        if column_name not in existing:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
            )


class ModelUsageStore:
    def __init__(self, path: Path) -> None:
        self.path = model_usage_store_db_path(Path(path))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS model_usage_events (
                    event_id TEXT PRIMARY KEY,
                    occurred_at TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    provider_endpoint_identifier TEXT NOT NULL,
                    model TEXT,
                    deployment TEXT,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    total_tokens INTEGER,
                    request_status TEXT NOT NULL,
                    duration_ms INTEGER,
                    normalized_usage_status TEXT NOT NULL,
                    raw_usage_metadata_json TEXT NOT NULL,
                    request_body_bytes INTEGER,
                    request_body_sha256 TEXT,
                    response_body_bytes INTEGER,
                    response_body_sha256 TEXT,
                    session_id TEXT,
                    workflow_run_id TEXT,
                    effect_id TEXT,
                    node_id TEXT,
                    dispatch_id TEXT,
                    agent_harness TEXT,
                    execution_backend TEXT,
                    directory_path TEXT,
                    trace_id TEXT,
                    request_id TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_model_usage_events_occurred_at
                    ON model_usage_events(occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_session_id
                    ON model_usage_events(session_id, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_workflow_run_id
                    ON model_usage_events(workflow_run_id, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_effect_id
                    ON model_usage_events(effect_id, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_node_id
                    ON model_usage_events(node_id, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_dispatch_id
                    ON model_usage_events(dispatch_id, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_directory_path
                    ON model_usage_events(directory_path, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_provider
                    ON model_usage_events(provider, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_model
                    ON model_usage_events(model, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_agent_harness
                    ON model_usage_events(agent_harness, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_execution_backend
                    ON model_usage_events(execution_backend, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_request_status
                    ON model_usage_events(request_status, occurred_at, event_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_events_usage_status
                    ON model_usage_events(normalized_usage_status, occurred_at, event_id);

                CREATE TABLE IF NOT EXISTS model_usage_correlation_contexts (
                    dispatch_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    workflow_run_id TEXT,
                    effect_id TEXT,
                    node_id TEXT,
                    agent_harness TEXT NOT NULL,
                    execution_backend TEXT,
                    directory_path TEXT,
                    model TEXT,
                    team_id TEXT,
                    project_id TEXT,
                    agent_id TEXT,
                    trace_id TEXT,
                    started_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_session_harness
                    ON model_usage_correlation_contexts(session_id, agent_harness, started_at, dispatch_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_expires_at
                    ON model_usage_correlation_contexts(expires_at, dispatch_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_workflow_run_id
                    ON model_usage_correlation_contexts(workflow_run_id, started_at, dispatch_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_effect_id
                    ON model_usage_correlation_contexts(effect_id, started_at, dispatch_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_execution_backend
                    ON model_usage_correlation_contexts(execution_backend, started_at, dispatch_id);
                CREATE INDEX IF NOT EXISTS idx_model_usage_contexts_directory_path
                    ON model_usage_correlation_contexts(directory_path, started_at, dispatch_id);
                """
            )
            _ensure_columns(
                connection,
                "model_usage_correlation_contexts",
                {
                    "team_id": "TEXT",
                    "project_id": "TEXT",
                    "agent_id": "TEXT",
                },
            )

    def upsert_event(self, event: ModelInferenceUsageEvent) -> None:
        validated = ModelInferenceUsageEvent.model_validate(event)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO model_usage_events(
                    event_id,
                    occurred_at,
                    provider,
                    provider_endpoint_identifier,
                    model,
                    deployment,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    request_status,
                    duration_ms,
                    normalized_usage_status,
                    raw_usage_metadata_json,
                    request_body_bytes,
                    request_body_sha256,
                    response_body_bytes,
                    response_body_sha256,
                    session_id,
                    workflow_run_id,
                    effect_id,
                    node_id,
                    dispatch_id,
                    agent_harness,
                    execution_backend,
                    directory_path,
                    trace_id,
                    request_id,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    occurred_at = excluded.occurred_at,
                    provider = excluded.provider,
                    provider_endpoint_identifier = excluded.provider_endpoint_identifier,
                    model = excluded.model,
                    deployment = excluded.deployment,
                    input_tokens = excluded.input_tokens,
                    output_tokens = excluded.output_tokens,
                    total_tokens = excluded.total_tokens,
                    request_status = excluded.request_status,
                    duration_ms = excluded.duration_ms,
                    normalized_usage_status = excluded.normalized_usage_status,
                    raw_usage_metadata_json = excluded.raw_usage_metadata_json,
                    request_body_bytes = excluded.request_body_bytes,
                    request_body_sha256 = excluded.request_body_sha256,
                    response_body_bytes = excluded.response_body_bytes,
                    response_body_sha256 = excluded.response_body_sha256,
                    session_id = excluded.session_id,
                    workflow_run_id = excluded.workflow_run_id,
                    effect_id = excluded.effect_id,
                    node_id = excluded.node_id,
                    dispatch_id = excluded.dispatch_id,
                    agent_harness = excluded.agent_harness,
                    execution_backend = excluded.execution_backend,
                    directory_path = excluded.directory_path,
                    trace_id = excluded.trace_id,
                    request_id = excluded.request_id,
                    payload_json = excluded.payload_json
                """,
                self._event_row(validated),
            )

    def get_event(self, event_id: object) -> ModelInferenceUsageEvent | None:
        normalized_event_id = _storage_optional_text(event_id)
        if normalized_event_id is None:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM model_usage_events
                WHERE event_id = ?
                """,
                (normalized_event_id,),
            ).fetchone()
        if row is None:
            return None
        return ModelInferenceUsageEvent.model_validate_json(row["payload_json"])

    def list_events(
        self,
        *,
        session_id: object | None = None,
        workflow_run_id: object | None = None,
        effect_id: object | None = None,
        node_id: object | None = None,
        dispatch_id: object | None = None,
        directory_path: object | None = None,
        include_directory_descendants: bool = False,
        provider: object | None = None,
        model: object | None = None,
        agent_harness: object | None = None,
        execution_backend: object | None = None,
        normalized_usage_status: object | None = None,
        request_status: object | None = None,
        occurred_after: datetime | None = None,
        occurred_before: datetime | None = None,
        limit: int | None = None,
    ) -> list[ModelInferenceUsageEvent]:
        sql = ["SELECT payload_json FROM model_usage_events WHERE 1=1"]
        params: list[Any] = []

        def add_condition(condition: str, *values: Any) -> None:
            sql.append(f" AND {condition}")
            params.extend(values)

        if (normalized := _storage_optional_text(session_id)) is not None:
            add_condition("session_id = ?", normalized)
        if (normalized := _storage_optional_text(workflow_run_id)) is not None:
            add_condition("workflow_run_id = ?", normalized)
        if (normalized := _storage_optional_text(effect_id)) is not None:
            add_condition("effect_id = ?", normalized)
        if (normalized := _storage_optional_text(node_id)) is not None:
            add_condition("node_id = ?", normalized)
        if (normalized := _storage_optional_text(dispatch_id)) is not None:
            add_condition("dispatch_id = ?", normalized)
        if (normalized := _storage_optional_text(provider)) is not None:
            add_condition("provider = ?", normalized)
        if (normalized := _storage_optional_text(model)) is not None:
            add_condition("model = ?", normalized)
        if (normalized := _storage_optional_text(agent_harness)) is not None:
            add_condition("agent_harness = ?", normalized)
        if (normalized := _storage_optional_text(execution_backend)) is not None:
            add_condition("execution_backend = ?", normalized)
        if (normalized := _storage_optional_text(normalized_usage_status)) is not None:
            add_condition("normalized_usage_status = ?", normalized)
        if (normalized := _storage_optional_text(request_status)) is not None:
            add_condition("request_status = ?", normalized)
        if (normalized := _storage_optional_text(directory_path)) is not None:
            if include_directory_descendants:
                escaped = _escape_like(normalized)
                add_condition(
                    "(directory_path = ? OR directory_path LIKE ? ESCAPE '\\')",
                    normalized,
                    f"{escaped}/%",
                )
            else:
                add_condition("directory_path = ?", normalized)
        if occurred_after is not None:
            add_condition("occurred_at >= ?", _storage_datetime(occurred_after))
        if occurred_before is not None:
            add_condition("occurred_at <= ?", _storage_datetime(occurred_before))

        sql.append(" ORDER BY occurred_at ASC, event_id ASC")
        if limit is not None:
            sql.append(" LIMIT ?")
            params.append(int(limit))

        query = "".join(sql)
        with self._lock, self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [ModelInferenceUsageEvent.model_validate_json(row["payload_json"]) for row in rows]

    def upsert_correlation_context(self, context: ModelUsageCorrelationContext) -> None:
        validated = ModelUsageCorrelationContext.model_validate(context)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO model_usage_correlation_contexts(
                    dispatch_id,
                    session_id,
                    workflow_run_id,
                    effect_id,
                    node_id,
                    agent_harness,
                    execution_backend,
                    directory_path,
                    model,
                    team_id,
                    project_id,
                    agent_id,
                    trace_id,
                    started_at,
                    expires_at,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dispatch_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    workflow_run_id = excluded.workflow_run_id,
                    effect_id = excluded.effect_id,
                    node_id = excluded.node_id,
                    agent_harness = excluded.agent_harness,
                    execution_backend = excluded.execution_backend,
                    directory_path = excluded.directory_path,
                    model = excluded.model,
                    team_id = excluded.team_id,
                    project_id = excluded.project_id,
                    agent_id = excluded.agent_id,
                    trace_id = excluded.trace_id,
                    started_at = excluded.started_at,
                    expires_at = excluded.expires_at,
                    payload_json = excluded.payload_json
                """,
                self._context_row(validated),
            )

    def get_active_correlation_context(
        self,
        *,
        session_id: object,
        agent_harness: object | None = None,
        execution_backend: object | None = None,
        dispatch_id: object | None = None,
        now_utc: datetime | None = None,
    ) -> ModelUsageCorrelationContext | None:
        normalized_session_id = _storage_optional_text(session_id)
        if normalized_session_id is None:
            return None
        normalized_agent_harness = _storage_optional_text(agent_harness)
        normalized_execution_backend = _storage_optional_text(execution_backend)
        normalized_dispatch_id = _storage_optional_text(dispatch_id)
        now = now_utc or datetime.now(UTC)
        now_value = _storage_datetime(now)

        sql = [
            "SELECT payload_json FROM model_usage_correlation_contexts WHERE session_id = ?",
        ]
        params: list[Any] = [normalized_session_id]
        if normalized_dispatch_id is not None:
            sql.append(" AND dispatch_id = ?")
            params.append(normalized_dispatch_id)
        if normalized_agent_harness is not None:
            sql.append(" AND agent_harness = ?")
            params.append(normalized_agent_harness)
        if normalized_execution_backend is not None:
            sql.append(" AND execution_backend = ?")
            params.append(normalized_execution_backend)
        sql.append(" AND expires_at > ?")
        params.append(now_value)
        sql.append(" ORDER BY started_at DESC, dispatch_id DESC LIMIT 1")

        query = "".join(sql)
        with self._lock, self._connect() as connection:
            row = connection.execute(query, params).fetchone()
            if row is not None:
                return ModelUsageCorrelationContext.model_validate_json(row["payload_json"])
        return None

    def get_correlation_context(
        self,
        dispatch_id: object,
    ) -> ModelUsageCorrelationContext | None:
        normalized_dispatch_id = _storage_optional_text(dispatch_id)
        if normalized_dispatch_id is None:
            return None
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM model_usage_correlation_contexts
                WHERE dispatch_id = ?
                """,
                (normalized_dispatch_id,),
            ).fetchone()
        if row is None:
            return None
        return ModelUsageCorrelationContext.model_validate_json(row["payload_json"])

    def clear_correlation_context(
        self,
        *,
        dispatch_id: object | None = None,
        session_id: object | None = None,
        agent_harness: object | None = None,
        execution_backend: object | None = None,
    ) -> int:
        clauses = []
        params: list[Any] = []
        if (normalized := _storage_optional_text(dispatch_id)) is not None:
            clauses.append("dispatch_id = ?")
            params.append(normalized)
        if (normalized := _storage_optional_text(session_id)) is not None:
            clauses.append("session_id = ?")
            params.append(normalized)
        if (normalized := _storage_optional_text(agent_harness)) is not None:
            clauses.append("agent_harness = ?")
            params.append(normalized)
        if (normalized := _storage_optional_text(execution_backend)) is not None:
            clauses.append("execution_backend = ?")
            params.append(normalized)
        if not clauses:
            return 0

        statement = (
            "DELETE FROM model_usage_correlation_contexts WHERE " + " AND ".join(clauses)
        )
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(statement, params)
            return int(cursor.rowcount or 0)

    def prune_expired_correlation_contexts(self, *, now_utc: datetime | None = None) -> int:
        now_value = _storage_datetime(now_utc or datetime.now(UTC))
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                DELETE FROM model_usage_correlation_contexts
                WHERE expires_at <= ?
                """,
                (now_value,),
            )
            return int(cursor.rowcount or 0)

    def _event_row(self, event: ModelInferenceUsageEvent) -> tuple[Any, ...]:
        payload_json = _json_dumps(event.model_dump(mode="json"))
        return (
            event.event_id,
            _storage_datetime(event.occurred_at),
            event.provider,
            event.provider_endpoint_identifier,
            event.model,
            event.deployment,
            event.input_tokens,
            event.output_tokens,
            event.total_tokens,
            event.request_status,
            event.duration_ms,
            event.normalized_usage_status,
            _json_dumps(event.raw_usage_metadata),
            event.request_body_bytes,
            event.request_body_sha256,
            event.response_body_bytes,
            event.response_body_sha256,
            event.session_id,
            event.workflow_run_id,
            event.effect_id,
            event.node_id,
            event.dispatch_id,
            event.agent_harness,
            event.execution_backend,
            event.directory_path,
            event.trace_id,
            event.request_id,
            payload_json,
        )

    def _context_row(self, context: ModelUsageCorrelationContext) -> tuple[Any, ...]:
        payload_json = _json_dumps(context.model_dump(mode="json"))
        return (
            context.dispatch_id,
            context.session_id,
            context.workflow_run_id,
            context.effect_id,
            context.node_id,
            context.agent_harness,
            context.execution_backend,
            context.directory_path,
            context.model,
            context.team_id,
            context.project_id,
            context.agent_id,
            context.trace_id,
            _storage_datetime(context.started_at),
            _storage_datetime(context.expires_at),
            payload_json,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection
